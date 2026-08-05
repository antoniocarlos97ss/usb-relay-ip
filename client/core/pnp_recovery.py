import logging
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

from client.api.host_client import HostApiClient
from client.core import operation_coordinator, usbip_wrapper, windows_pnp
from shared.models import UsbDevice


logger = logging.getLogger(__name__)

POLL_SECONDS = 10
CONFIRM_SAMPLES = 2
RECOVERY_DEADLINE_SECONDS = 90
WAIT_SHARED_SECONDS = 6
WAIT_UNBOUND_SECONDS = 6
VALIDATE_SECONDS = 15
SUCCESS_COOLDOWN_SECONDS = 60
FAILURE_COOLDOWN_SECONDS = 15 * 60


class LocalUsbipStateUnknown(RuntimeError):
    pass


def _matching_attached(device: UsbDevice, timeout: int = 3):
    query = usbip_wrapper.query_attached_devices(timeout=timeout)
    if not query.success:
        raise LocalUsbipStateUnknown(query.error)
    busid_matches = [item for item in query.devices if item.busid == device.busid]
    if len(busid_matches) > 1:
        raise LocalUsbipStateUnknown(f"multiple local ports claim busid {device.busid}")
    if not busid_matches:
        return None
    matched = busid_matches[0]
    if (matched.vid.lower(), matched.pid.lower()) != (device.vid.lower(), device.pid.lower()):
        raise LocalUsbipStateUnknown(
            f"local identity changed at {device.busid}: observed {matched.vid}:{matched.pid}"
        )
    return matched


def _host_identity_is_current(api_client: HostApiClient, expected: UsbDevice) -> bool:
    try:
        matches = [
            device
            for device in api_client.get_devices(timeout=2)
            if device.busid == expected.busid
        ]
    except (TypeError, AttributeError):
        return False
    if len(matches) != 1:
        return False
    current = matches[0]
    return (
        (current.vid.lower(), current.pid.lower())
        == (expected.vid.lower(), expected.pid.lower())
        and current.state in {"Attached", "Shared"}
    )


def _wait_host_shared(
    api_client: HostApiClient,
    busid: str,
    deadline: float,
    cancel_event: threading.Event | None = None,
    expected_vid: str = "",
    expected_pid: str = "",
) -> bool:
    while time.monotonic() < deadline and not (cancel_event and cancel_event.is_set()):
        remaining = deadline - time.monotonic()
        matches = [
            device
            for device in api_client.get_devices(timeout=min(2.0, max(0.2, remaining)))
            if device.busid == busid
        ]
        if len(matches) > 1:
            return False
        if matches:
            device = matches[0]
            if expected_vid and expected_pid and (
                device.vid.lower(), device.pid.lower()
            ) != (expected_vid.lower(), expected_pid.lower()):
                return False
            if device.state == "Shared":
                return True
        time.sleep(0.5)
    return False


def _wait_host_unbound(
    api_client: HostApiClient,
    busid: str,
    deadline: float,
    cancel_event: threading.Event | None = None,
    expected_vid: str = "",
    expected_pid: str = "",
) -> bool:
    while time.monotonic() < deadline and not (cancel_event and cancel_event.is_set()):
        remaining = deadline - time.monotonic()
        matches = [
            device
            for device in api_client.get_devices(timeout=min(2.0, max(0.2, remaining)))
            if device.busid == busid
        ]
        if len(matches) > 1:
            return False
        if matches:
            device = matches[0]
            if expected_vid and expected_pid and (
                device.vid.lower(), device.pid.lower()
            ) != (expected_vid.lower(), expected_pid.lower()):
                return False
            if device.state == "Not shared":
                return True
        time.sleep(0.5)
    return False


def _wait_pnp_healthy(
    device: UsbDevice,
    deadline: float,
    cancel_event: threading.Event | None = None,
) -> bool:
    key = device.vid.lower(), device.pid.lower()
    while time.monotonic() < deadline and not (cancel_event and cancel_event.is_set()):
        attached_devices = usbip_wrapper.list_attached(timeout=2)
        target_matches = [item for item in attached_devices if item.busid == device.busid]
        if len(target_matches) != 1 or (
            target_matches[0].vid.lower(), target_matches[0].pid.lower()
        ) != key:
            time.sleep(0.5)
            continue
        remaining = max(1, int(deadline - time.monotonic()))
        statuses = windows_pnp.list_usb_devices(timeout=min(5, remaining), include_properties=False)
        if statuses is not None:
            correlated = windows_pnp.get_correlated_statuses(device.busid, statuses)
            correlation = windows_pnp.get_session_correlation(device.busid)
            if correlated:
                healthy_correlated = [
                    item for item in correlated
                    if item.problem_code == 0 and (item.vid, item.pid) == key
                ]
                if healthy_correlated:
                    return True
            elif correlation is None or not correlation.instance_ids:
                matching_local = [
                    item for item in attached_devices
                    if (item.vid.lower(), item.pid.lower()) == key
                ]
                exact = [item for item in statuses if (item.vid, item.pid) == key]
                if (
                    len(matching_local) == 1
                    and matching_local[0].busid == device.busid
                    and len(exact) == 1
                    and exact[0].problem_code == 0
                ):
                    return True
        time.sleep(1)
    return False


def recover_device(
    api_client: HostApiClient,
    device: UsbDevice,
    cancel_event: threading.Event | None = None,
    identity_confirmed: bool = False,
) -> tuple[bool, str]:
    started = time.monotonic()
    deadline = started + RECOVERY_DEADLINE_SECONDS
    if not operation_coordinator.try_acquire(device.vid, device.pid, device.busid):
        return False, "another reconnect operation is already running"

    try:
        for attempt in range(1, 3):
            if cancel_event and cancel_event.is_set():
                return False, "recovery interrupted during shutdown"
            try:
                attached = _matching_attached(device, timeout=3)
            except LocalUsbipStateUnknown as exc:
                return False, f"local USB/IP state is unknown: {exc}"
            if attached is None and attempt == 1:
                if not identity_confirmed:
                    return False, "local USB/IP session could not be identified unambiguously"

            if attached is not None:
                logger.warning(
                    "PnP Code 43 recovery attempt %s for %s (%s:%s), port %s",
                    attempt, device.busid, device.vid, device.pid, attached.port,
                )
                remaining = int(deadline - time.monotonic())
                if remaining < 5:
                    return False, "recovery deadline reached before detach"
                if cancel_event and cancel_event.is_set():
                    return False, "recovery interrupted during shutdown"
                detached = usbip_wrapper.detach_busid(
                    device.busid,
                    timeout=min(5, remaining),
                    port_hint=attached.port,
                    expected_vid=device.vid,
                    expected_pid=device.pid,
                )
                if not detached.success:
                    return False, detached.message
            else:
                logger.warning(
                    "PnP Code 43 recovery attempt %s for %s (%s:%s): session already detached",
                    attempt, device.busid, device.vid, device.pid,
                )

            logger.warning("Cycling host binding for PnP recovery of %s", device.busid)
            if cancel_event and cancel_event.is_set():
                return False, "recovery interrupted during shutdown"
            if not _host_identity_is_current(api_client, device):
                return False, "host identity changed before recovery unbind"
            if not api_client.unbind_device(device.busid):
                return False, "host unbind failed during recovery"
            unbound_deadline = min(deadline, time.monotonic() + WAIT_UNBOUND_SECONDS)
            if not _wait_host_unbound(
                api_client,
                device.busid,
                unbound_deadline,
                cancel_event,
                expected_vid=device.vid,
                expected_pid=device.pid,
            ):
                if cancel_event and cancel_event.is_set():
                    return False, "recovery interrupted during shutdown"
                return False, "host did not report the expected device as Not shared after unbind"
            if cancel_event and cancel_event.is_set():
                return False, "recovery interrupted during shutdown"
            if not api_client.bind_device(device.busid):
                return False, "host bind failed during recovery"

            shared_deadline = min(deadline, time.monotonic() + WAIT_SHARED_SECONDS)
            if not _wait_host_shared(
                api_client,
                device.busid,
                shared_deadline,
                cancel_event,
                expected_vid=device.vid,
                expected_pid=device.pid,
            ):
                if cancel_event and cancel_event.is_set():
                    return False, "recovery interrupted during shutdown"
                return False, "host did not report the expected device as Shared after bind"

            remaining = int(deadline - time.monotonic())
            if remaining < 5:
                return False, "recovery deadline reached before attach"
            if cancel_event and cancel_event.is_set():
                return False, "recovery interrupted during shutdown"
            attached_result = usbip_wrapper.attach_device(
                api_client.host_ip,
                device.busid,
                timeout=min(8, remaining),
                vid=device.vid,
                pid=device.pid,
            )
            if not attached_result.success:
                if attempt == 1 and time.monotonic() < deadline - 10:
                    if cancel_event:
                        if cancel_event.wait(1):
                            return False, "recovery interrupted during shutdown"
                    else:
                        time.sleep(1)
                    continue
                return False, attached_result.message

            validation_deadline = min(deadline, time.monotonic() + VALIDATE_SECONDS)
            if _wait_pnp_healthy(device, validation_deadline, cancel_event):
                elapsed = time.monotonic() - started
                return True, f"recovered in {elapsed:.1f}s"

            if attempt == 1 and time.monotonic() < deadline - 10:
                continue
            return False, "Windows still reports PnP Code 43 after reattach"

        return False, "recovery attempts exhausted"
    finally:
        operation_coordinator.release(device.vid, device.pid, device.busid)


class PnpRecoveryMonitor(QThread):
    recovery_failed = pyqtSignal(str, str)
    recovery_succeeded = pyqtSignal(str, str)

    def __init__(self, api_client: HostApiClient, parent=None, poll_seconds: int = POLL_SECONDS):
        super().__init__(parent)
        self._api_client = HostApiClient(
            host_ip=api_client.host_ip,
            host_port=api_client.host_port,
            api_key=api_client.api_key,
        )
        self._poll_seconds = max(1, poll_seconds)
        self._running = False
        self._devices: list[UsbDevice] = []
        self._devices_lock = threading.Lock()
        self._fail_samples: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}
        self._unknown_logged_at: dict[str, float] = {}
        self._stop_event = threading.Event()
        self._recovery_lock = threading.Lock()
        self._recovery_threads: dict[str, threading.Thread] = {}
        self._run_thread_id: int | None = None

    def update_devices(self, devices: list[UsbDevice]):
        with self._devices_lock:
            self._devices = list(devices)

    def update_host_config(self, host_ip: str, host_port: int, api_key: str):
        self._api_client.update_config(host_ip, host_port, api_key)

    def start(self, *args, **kwargs):
        self._stop_event.clear()
        self._running = True
        return super().start(*args, **kwargs)

    def run(self):
        if self._stop_event.is_set() or not self._running:
            return
        self._run_thread_id = threading.get_ident()
        try:
            while self._running and not self._stop_event.is_set():
                self._check_once()
                for _ in range(self._poll_seconds * 10):
                    if not self._running or self._stop_event.is_set():
                        return
                    time.sleep(0.1)
        finally:
            self._run_thread_id = None

    def _check_once(self):
        if self._stop_event.is_set() or not self._running:
            return
        statuses = windows_pnp.list_usb_devices(include_properties=False)
        if statuses is None:
            return
        local_query = usbip_wrapper.query_attached_devices(timeout=2)
        if not local_query.success:
            logger.warning(
                "Skipping PnP recovery poll: local USB/IP state is unknown: %s",
                local_query.error,
            )
            return
        attached_devices = list(local_query.devices)
        with self._devices_lock:
            devices = [device for device in self._devices if device.state == "Attached"]

        now = time.monotonic()
        attributed_ids: set[str] = set()

        for device in devices:
            key = device.busid
            failures = windows_pnp.find_session_code43(
                device.busid,
                device.vid,
                device.pid,
                statuses,
                attached_devices,
            )
            attributed_ids.update(str(item.instance_id).strip().upper() for item in failures)
            if now < self._cooldown_until.get(key, 0):
                continue
            if not failures:
                self._fail_samples.pop(key, None)
                continue
            self._fail_samples[key] = self._fail_samples.get(key, 0) + 1
            if self._fail_samples[key] < CONFIRM_SAMPLES:
                continue

            self._fail_samples[key] = 0
            with self._recovery_lock:
                if self._stop_event.is_set() or not self._running:
                    return
                if device.busid in self._recovery_threads:
                    continue
                worker = threading.Thread(
                    target=self._recover_in_background,
                    args=(device,),
                    name=f"pnp-recovery-{device.busid}",
                    daemon=True,
                )
                self._recovery_threads[device.busid] = worker
            worker.start()

        unknown = windows_pnp.find_unknown_code43(statuses)
        reportable = [
            item for item in unknown
            if str(item.instance_id).strip().upper() not in attributed_ids
            and windows_pnp.get_busid_for_instance_id(item.instance_id) is None
            and now - self._unknown_logged_at.get(item.instance_id, 0) >= 60
        ]
        if reportable:
            logger.error(
                "PnP Code 43 detected without VID/PID; automatic recovery skipped because "
                "the USB/IP session cannot be identified safely: %s",
                ", ".join(item.instance_id for item in reportable),
            )
            for item in reportable:
                self._unknown_logged_at[item.instance_id] = now

    def _recover_in_background(self, device: UsbDevice):
        try:
            success, message = recover_device(
                self._api_client,
                device,
                self._stop_event,
                identity_confirmed=True,
            )
        except Exception as exc:
            logger.exception("PnP recovery crashed for %s", device.busid)
            success, message = False, f"recovery crashed: {exc}"
        key = device.busid
        with self._recovery_lock:
            self._recovery_threads.pop(device.busid, None)
        if self._stop_event.is_set() or not self._running:
            return
        if success:
            self._cooldown_until[key] = time.monotonic() + SUCCESS_COOLDOWN_SECONDS
            if not self._stop_event.is_set() and self._running:
                try:
                    self.recovery_succeeded.emit(device.busid, message)
                except RuntimeError:
                    logger.debug("Recovery success signal discarded during shutdown")
        elif message == "another reconnect operation is already running":
            self._fail_samples[key] = CONFIRM_SAMPLES - 1
        else:
            self._cooldown_until[key] = time.monotonic() + FAILURE_COOLDOWN_SECONDS
            if not self._stop_event.is_set() and self._running:
                try:
                    self.recovery_failed.emit(device.busid, message)
                except RuntimeError:
                    logger.debug("Recovery failure signal discarded during shutdown")

    def stop(self):
        self._running = False
        self._stop_event.set()
        with self._recovery_lock:
            workers = list(self._recovery_threads.values())
        owner_thread_ids = {
            thread_id for thread_id in (
                self._run_thread_id,
                *(worker.ident for worker in workers),
            )
            if thread_id is not None
        }
        windows_pnp.kill_all_queries(owner_thread_ids)
        usbip_wrapper.kill_all_subprocesses(owner_thread_ids)
        self.wait(2000)
        join_deadline = time.monotonic() + 2.0
        for worker in workers:
            worker.join(timeout=max(0, join_deadline - time.monotonic()))
