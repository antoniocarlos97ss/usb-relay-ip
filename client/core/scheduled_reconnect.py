import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from client.api.host_client import HostApiClient
from client.core import config_manager, usbip_wrapper
from shared.models import UsbDevice

logger = logging.getLogger(__name__)

FAILURE_COOLDOWN = timedelta(minutes=15)


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


def _find_matching_device(devices: list[UsbDevice], vid: str, pid: str) -> UsbDevice | None:
    key = _normalize_key(vid, pid)
    for device in devices:
        if _normalize_key(device.vid, device.pid) == key:
            return device
    return None


def _mark_last_run(vid: str, pid: str, when: datetime) -> None:
    config = config_manager.load_config()
    key = _normalize_key(vid, pid)
    for device in config.permanent_devices:
        if _normalize_key(device.vid, device.pid) == key:
            device.last_scheduled_reconnect_at = _format_timestamp(when)
            config_manager.save_config(config)
            return


def _run_reconnect_cycle(api_client: HostApiClient, device: UsbDevice) -> tuple[bool, str]:
    matched = _find_matching_device(api_client.get_devices(), device.vid, device.pid)
    if not matched:
        return False, f"Device {device.vid}:{device.pid} is no longer available"

    if matched.state == "Attached":
        port = usbip_wrapper.find_port_for_busid(matched.busid)
        if port is None:
            return False, f"Cannot find attached port for {matched.busid}"
        detach_result = usbip_wrapper.detach_device(port)
        if not detach_result.success:
            return False, detach_result.message

    if not api_client.unbind_device(matched.busid):
        return False, f"Failed to unbind {matched.busid} on host"

    time.sleep(2)

    if not api_client.bind_device(matched.busid):
        return False, f"Failed to bind {matched.busid} on host"

    time.sleep(2)

    attach_result = usbip_wrapper.attach_device(api_client.host_ip, matched.busid)
    if not attach_result.success:
        return False, attach_result.message

    _mark_last_run(matched.vid, matched.pid, _utc_now())
    return True, matched.busid


class ScheduledReconnectWorker(QThread):
    result = pyqtSignal(bool, str, str)

    def __init__(self, api_client: HostApiClient, device: UsbDevice, parent=None):
        super().__init__(parent)
        self._api_client = HostApiClient(
            host_ip=api_client.host_ip,
            host_port=api_client.host_port,
            api_key=api_client.api_key,
        )
        self._device = device

    def run(self):
        try:
            success, message = _run_reconnect_cycle(self._api_client, self._device)
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

    def stop(self):
        self._stopping = True
        self._timer.stop()

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

        self._failed_until[key] = self._now_provider() + FAILURE_COOLDOWN
        logger.warning("Scheduled reconnect failed for %s: %s", busid, message)
        self.reconnect_failed.emit(busid, message)
