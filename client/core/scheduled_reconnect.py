import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from client.api.host_client import HostApiClient
from client.core import config_manager, operation_coordinator, usbip_wrapper
from client.core.pnp_recovery import _wait_pnp_healthy
from shared.models import UsbDevice

logger = logging.getLogger(__name__)

FAILURE_COOLDOWN = timedelta(minutes=15)
HOST_TRANSITION_TIMEOUT_SECONDS = 10


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_key(vid: str, pid: str) -> tuple[str, str]:
    return vid.lower(), pid.lower()


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def find_unique_identity_match(
    devices: list[UsbDevice], vid: str, pid: str
) -> UsbDevice | None:
    key = _normalize_key(vid, pid)
    matches = [
        device for device in devices
        if _normalize_key(device.vid, device.pid) == key
    ]
    return matches[0] if len(matches) == 1 else None


def _mark_last_run(vid: str, pid: str, when: datetime) -> None:
    config_manager.mark_scheduled_reconnect_completed(
        vid,
        pid,
        _format_timestamp(when),
    )


def _wait_host_state(
    api_client: HostApiClient,
    busid: str,
    expected_state: str,
    timeout: float = HOST_TRANSITION_TIMEOUT_SECONDS,
    cancel_event: threading.Event | None = None,
    expected_vid: str = "",
    expected_pid: str = "",
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not (cancel_event and cancel_event.is_set()):
        remaining = deadline - time.monotonic()
        matches = [
            item
            for item in api_client.get_devices(timeout=min(2.0, max(0.2, remaining)))
            if item.busid == busid
        ]
        if len(matches) > 1:
            return False
        for item in matches:
            if expected_vid and expected_pid and _normalize_key(item.vid, item.pid) != _normalize_key(
                expected_vid, expected_pid
            ):
                return False
            if item.state == expected_state:
                return True
        if cancel_event:
            if cancel_event.wait(0.5):
                return False
        else:
            time.sleep(0.5)
    return False


def _host_busid_identity_is_current(
    api_client: HostApiClient,
    expected: UsbDevice,
) -> bool:
    current = [
        item
        for item in api_client.get_devices(timeout=2)
        if item.busid == expected.busid
    ]
    return (
        len(current) == 1
        and _normalize_key(current[0].vid, current[0].pid)
        == _normalize_key(expected.vid, expected.pid)
    )


def _run_reconnect_cycle_unlocked(
    api_client: HostApiClient,
    device: UsbDevice,
    cancel_event: threading.Event | None = None,
    record_completion: bool = True,
) -> tuple[bool, str]:
    if cancel_event and cancel_event.is_set():
        return False, "reconnect interrupted during shutdown"
    host_devices = api_client.get_devices()
    busid_matches = [item for item in host_devices if item.busid == device.busid]
    if len(busid_matches) > 1:
        return False, f"Multiple host devices claim busid {device.busid}"
    matched = busid_matches[0] if busid_matches else None
    if matched is not None and _normalize_key(matched.vid, matched.pid) != _normalize_key(
        device.vid, device.pid
    ):
        return False, f"Device identity changed at busid {device.busid}"
    if matched is None:
        matched = find_unique_identity_match(host_devices, device.vid, device.pid)
    if not matched:
        return False, f"Device {device.vid}:{device.pid} is no longer available"

    if matched.state == "Attached":
        local_query = usbip_wrapper.query_attached_devices()
        if not local_query.success:
            return False, f"Local USB/IP state is unknown: {local_query.error}"
        local_matches = [item for item in local_query.devices if item.busid == matched.busid]
        if len(local_matches) > 1:
            return False, f"Multiple local ports claim busid {matched.busid}"
        if not local_matches:
            logger.warning(
                "No local session exists for %s; continuing with safe host cycle",
                matched.busid,
            )
        else:
            if cancel_event and cancel_event.is_set():
                return False, "reconnect interrupted during shutdown"
            detach_result = usbip_wrapper.detach_busid(
                matched.busid,
                port_hint=local_matches[0].port,
                expected_vid=matched.vid,
                expected_pid=matched.pid,
            )
            if not detach_result.success:
                return False, detach_result.message

    if cancel_event and cancel_event.is_set():
        return False, "reconnect interrupted during shutdown"
    if not _host_busid_identity_is_current(api_client, matched):
        return False, f"Device identity changed at busid {matched.busid} before unbind"
    if not api_client.unbind_device(matched.busid):
        return False, f"Failed to unbind {matched.busid} on host"

    if not _wait_host_state(
        api_client,
        matched.busid,
        "Not shared",
        cancel_event=cancel_event,
        expected_vid=matched.vid,
        expected_pid=matched.pid,
    ):
        if cancel_event and cancel_event.is_set():
            return False, "reconnect interrupted during shutdown"
        return False, f"Host did not report {matched.busid} as Not shared after unbind"

    if cancel_event and cancel_event.is_set():
        return False, "reconnect interrupted during shutdown"
    if not api_client.bind_device(matched.busid):
        return False, f"Failed to bind {matched.busid} on host"

    if not _wait_host_state(
        api_client,
        matched.busid,
        "Shared",
        cancel_event=cancel_event,
        expected_vid=matched.vid,
        expected_pid=matched.pid,
    ):
        if cancel_event and cancel_event.is_set():
            return False, "reconnect interrupted during shutdown"
        return False, f"Host did not report {matched.busid} as Shared after bind"

    if cancel_event and cancel_event.is_set():
        return False, "reconnect interrupted during shutdown"
    attach_result = usbip_wrapper.attach_device(
        api_client.host_ip,
        matched.busid,
        vid=matched.vid,
        pid=matched.pid,
    )
    if not attach_result.success:
        return False, attach_result.message

    validation_deadline = time.monotonic() + 15
    if not _wait_pnp_healthy(matched, validation_deadline, cancel_event):
        if cancel_event and cancel_event.is_set():
            return False, "reconnect interrupted during shutdown"
        return False, f"Windows PnP validation failed for {matched.busid}"

    if record_completion:
        _mark_last_run(matched.vid, matched.pid, _utc_now())
    return True, matched.busid


def _run_reconnect_cycle(
    api_client: HostApiClient,
    device: UsbDevice,
    cancel_event: threading.Event | None = None,
    record_completion: bool = True,
) -> tuple[bool, str]:
    if not operation_coordinator.try_acquire(device.vid, device.pid, device.busid):
        return False, "another reconnect operation is already running"
    try:
        return _run_reconnect_cycle_unlocked(
            api_client,
            device,
            cancel_event,
            record_completion,
        )
    finally:
        operation_coordinator.release(device.vid, device.pid, device.busid)


class ScheduledReconnectWorker(QThread):
    result = pyqtSignal(bool, str, str)

    def __init__(
        self,
        api_client: HostApiClient,
        device: UsbDevice,
        parent=None,
        record_completion: bool = True,
    ):
        super().__init__(parent)
        self._api_client = HostApiClient(
            host_ip=api_client.host_ip,
            host_port=api_client.host_port,
            api_key=api_client.api_key,
        )
        self._device = device
        self._cancel_event = threading.Event()
        self._record_completion = record_completion

    def request_cancel(self):
        self._cancel_event.set()

    def run(self):
        try:
            success, message = _run_reconnect_cycle(
                self._api_client,
                self._device,
                self._cancel_event,
                self._record_completion,
            )
        except Exception as exc:
            logger.error("Scheduled reconnect worker crashed: %s", exc, exc_info=True)
            success, message = False, str(exc)

        try:
            self.result.emit(success, self._device.busid, message)
        except Exception:
            logger.exception("Failed to emit scheduled reconnect result")


class ScheduledReconnectController(QObject):
    reconnect_failed = pyqtSignal(str, str)

    def __init__(
        self,
        api_client: HostApiClient,
        parent=None,
        tick_interval_seconds: int = 60,
        now_provider=_utc_now,
    ):
        super().__init__(parent)
        self._api_client = api_client
        self._now_provider = now_provider
        self._timer = QTimer(self)
        self._timer.setInterval(max(1, tick_interval_seconds) * 1000)
        self._timer.timeout.connect(self.tick)
        self._started_at: datetime | None = None
        self._devices: list[UsbDevice] = []
        self._host_connected = False
        self._service_ok = False
        self._workers: list[ScheduledReconnectWorker] = []
        self._running_keys: set[tuple[str, str]] = set()
        self._failed_until: dict[tuple[str, str], datetime] = {}
        self._host_ip = api_client.host_ip
        self._host_port = api_client.host_port
        self._api_key = api_client.api_key
        self._lock = threading.Lock()
        self._stopping = False

    def start(self):
        self._stopping = False
        self._started_at = self._now_provider()
        self._timer.start()

    def stop(self, wait_ms: int = 3000):
        self._stopping = True
        self._timer.stop()
        workers = list(self._workers)
        for worker in workers:
            worker.request_cancel()
        deadline = time.monotonic() + max(0, wait_ms) / 1000
        for worker in workers:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if remaining_ms <= 0:
                break
            worker.wait(remaining_ms)

    def update_devices(self, devices: list[UsbDevice]):
        self._devices = list(devices)

    def update_connection_state(self, connected: bool):
        self._host_connected = connected

    def update_service_state(self, service_ok: bool):
        self._service_ok = service_ok

    def update_host_config(self, host_ip: str, host_port: int, api_key: str):
        with self._lock:
            self._host_ip = host_ip
            self._host_port = host_port
            self._api_key = api_key

    def refresh(self):
        self.tick()

    def tick(self):
        if self._stopping:
            return
        if self._started_at is None:
            self._started_at = self._now_provider()

        if not self._host_connected:
            logger.warning("Skipping scheduled reconnect cycle: host is offline")
            return

        if not self._service_ok:
            logger.warning("Skipping scheduled reconnect cycle: usbipd service unavailable")
            return

        now = self._now_provider()
        config = config_manager.load_config()
        scheduled_devices = {
            _normalize_key(dev.vid, dev.pid): dev
            for dev in config.permanent_devices
            if dev.scheduled_reconnect_enabled
        }

        for device in self._devices:
            key = _normalize_key(device.vid, device.pid)
            scheduled = scheduled_devices.get(key)
            if not scheduled:
                continue

            baseline = self._started_at
            last_run = _parse_timestamp(scheduled.last_scheduled_reconnect_at)
            if last_run and last_run > baseline:
                baseline = last_run

            interval = timedelta(hours=max(1, scheduled.scheduled_reconnect_interval_hours))
            if now - baseline < interval:
                continue

            with self._lock:
                if key in self._running_keys:
                    logger.debug("Scheduled reconnect already running for %s:%s", device.vid, device.pid)
                    continue
                if now < self._failed_until.get(key, datetime.min.replace(tzinfo=timezone.utc)):
                    logger.debug("Scheduled reconnect is cooling down for %s:%s", device.vid, device.pid)
                    continue
                self._running_keys.add(key)

            self._start_worker(device, key)

    def _start_worker(self, device: UsbDevice, key: tuple[str, str]):
        with self._lock:
            api_client = HostApiClient(
                host_ip=self._host_ip,
                host_port=self._host_port,
                api_key=self._api_key,
            )

        worker = ScheduledReconnectWorker(api_client, device, self)
        worker.result.connect(
            lambda success, busid, message, worker=worker, key=key: self._on_worker_result(
                success, busid, message, key, worker
            )
        )
        worker.finished.connect(worker.deleteLater)
        self._workers.append(worker)
        worker.start()

    def _on_worker_result(
        self,
        success: bool,
        busid: str,
        message: str,
        key: tuple[str, str],
        worker: ScheduledReconnectWorker,
    ):
        with self._lock:
            self._running_keys.discard(key)

        if worker in self._workers:
            self._workers.remove(worker)

        if success:
            logger.info("Scheduled reconnect completed for %s", busid)
            return

        if message == "another reconnect operation is already running":
            logger.info("Scheduled reconnect deferred for %s: %s", busid, message)
            return

        self._failed_until[key] = self._now_provider() + FAILURE_COOLDOWN
        logger.warning("Scheduled reconnect failed for %s: %s", busid, message)
        self.reconnect_failed.emit(busid, message)
