import logging
import time

from PyQt6.QtCore import QThread, pyqtSignal

from host.core import config_manager, usbipd_wrapper
from shared.models import UsbDevice

logger = logging.getLogger(__name__)


def sync_headless_devices_once(failed_auto_share_busids: set[str] | None = None) -> set[str]:
    """Perform one headless sync pass for permanent bind and auto-share.

    Returns the updated set of busids that should be skipped on the next pass
    because auto-share failed for them in this pass.
    """
    failed = set(failed_auto_share_busids or set())
    config = config_manager.load_config()
    devices = usbipd_wrapper.list_devices()
    current_ids = {device.busid for device in devices}
    failed.intersection_update(current_ids)

    for device in devices:
        device.is_permanent = config_manager.is_permanent(device.vid, device.pid)

    attempted_busids: set[str] = set()

    for device in devices:
        if not device.is_permanent:
            continue
        if device.state in ("Shared", "Attached"):
            continue
        result = usbipd_wrapper.bind_device(device.busid)
        attempted_busids.add(device.busid)
        if result.success:
            device.state = "Shared"
            failed.discard(device.busid)
            logger.info(f"Headless auto-bound permanent device {device.busid}")
        else:
            logger.warning(f"Headless auto-bind failed for {device.busid}: {result.message}")

    if not config.auto_share_all:
        return failed

    exclude_set = set(config.auto_share_exclude)
    for device in devices:
        if device.busid in attempted_busids:
            continue
        if device.state != "Not shared":
            continue
        if device.busid in failed:
            continue
        if f"{device.vid.lower()}:{device.pid.lower()}" in exclude_set:
            continue
        result = usbipd_wrapper.bind_device(device.busid)
        if result.success:
            failed.discard(device.busid)
            logger.info(f"Headless auto-shared device {device.busid} ({device.description})")
        else:
            failed.add(device.busid)
            logger.warning(f"Headless auto-share failed for {device.busid}: {result.message}")

    return failed


class DeviceMonitor(QThread):
    devices_changed = pyqtSignal(list)
    device_bound = pyqtSignal(str, bool, str)
    device_unplugged = pyqtSignal(str)
    device_auto_bound = pyqtSignal(str, str)
    device_auto_shared = pyqtSignal(str, str)

    def __init__(self, poll_interval: int = 5, parent=None):
        super().__init__(parent)
        self._poll_interval = poll_interval
        self._running = False
        self._previous_devices: list[UsbDevice] = []
        self._failed_auto_share_busids: set[str] = set()

    def run(self):
        self._running = True
        logger.info("Device monitor started")

        self._auto_bind_permanent_on_startup()
        self._auto_share_all_on_startup()

        while self._running:
            try:
                current_devices = usbipd_wrapper.list_devices()
                self._mark_permanent_status(current_devices)

                if self._device_list_changed(current_devices):
                    self.devices_changed.emit(current_devices)
                    self._handle_new_devices(current_devices)

                self._handle_auto_share(current_devices)
                self._previous_devices = current_devices
            except Exception as exc:
                logger.error(f"Error in device monitor: {exc}")

            for _ in range(self._poll_interval):
                if not self._running:
                    break
                time.sleep(1)

        logger.info("Device monitor stopped")

    def stop(self):
        self._running = False
        self.wait(3000)

    def set_poll_interval(self, seconds: int):
        self._poll_interval = max(1, seconds)

    def _mark_permanent_status(self, devices: list[UsbDevice]):
        for device in devices:
            device.is_permanent = config_manager.is_permanent(device.vid, device.pid)

    def _device_list_changed(self, current_devices: list[UsbDevice]) -> bool:
        prev_ids = {d.busid for d in self._previous_devices}
        curr_ids = {d.busid for d in current_devices}
        if prev_ids != curr_ids:
            return True
        prev_states = {d.busid: d.state for d in self._previous_devices}
        return any(prev_states.get(d.busid) != d.state for d in current_devices)

    def _handle_new_devices(self, current_devices: list[UsbDevice]):
        # Remove from failed set only busids that are no longer present
        curr_ids = {d.busid for d in current_devices}
        self._failed_auto_share_busids.intersection_update(curr_ids)
        prev_ids = {d.busid for d in self._previous_devices}
        for device in current_devices:
            if device.busid not in prev_ids:
                logger.info(f"New device detected: {device.busid} ({device.description})")
                if device.is_permanent and device.state != "Shared":
                    result = usbipd_wrapper.bind_device(device.busid)
                    if result.success:
                        self.device_auto_bound.emit(device.busid, device.description)
                        logger.info(f"Auto-bound permanent device {device.busid}")
                    else:
                        logger.warning(f"Failed to auto-bind {device.busid}: {result.message}")

        for prev_device in self._previous_devices:
            curr_ids = {d.busid for d in current_devices}
            if prev_device.busid not in curr_ids:
                self.device_unplugged.emit(prev_device.busid)
                logger.info(f"Device removed: {prev_device.busid}")

    def _auto_bind_permanent_on_startup(self):
        config = config_manager.load_config()
        if not config.permanent_devices:
            return

        logger.info(f"Auto-binding {len(config.permanent_devices)} permanent devices")
        devices = usbipd_wrapper.list_devices()

        for perm_device in config.permanent_devices:
            matched = None
            for device in devices:
                if device.vid == perm_device.vid and device.pid == perm_device.pid:
                    matched = device
                    break

            if matched is None:
                logger.info(f"Permanent device {perm_device.vid}:{perm_device.pid} not currently connected")
                continue

            if matched.state in ("Shared", "Attached"):
                logger.info(f"Permanent device {matched.busid} already {matched.state}")
                continue

            result = usbipd_wrapper.bind_device(matched.busid)
            if result.success:
                self.device_auto_bound.emit(matched.busid, matched.description)
                logger.info(f"Startup auto-bound: {matched.busid}")
            else:
                logger.warning(f"Startup auto-bind failed for {matched.busid}: {result.message}")

    def _auto_share_all_on_startup(self):
        config = config_manager.load_config()
        if not config.auto_share_all:
            return

        devices = usbipd_wrapper.list_devices()
        exclude_set = set(config.auto_share_exclude)
        for device in devices:
            if device.state != "Not shared":
                continue
            if f"{device.vid.lower()}:{device.pid.lower()}" in exclude_set:
                continue
            result = usbipd_wrapper.bind_device(device.busid)
            if result.success:
                self.device_auto_shared.emit(device.busid, device.description)
                logger.info(f"Startup auto-share: {device.busid} ({device.description})")
            else:
                logger.warning(f"Startup auto-share failed for {device.busid}: {result.message}")

    def _handle_auto_share(self, current_devices: list[UsbDevice]):
        config = config_manager.load_config()
        if not config.auto_share_all:
            return
        exclude_set = set(config.auto_share_exclude)
        for device in current_devices:
            if device.state != "Not shared":
                continue
            if device.busid in self._failed_auto_share_busids:
                continue
            if f"{device.vid.lower()}:{device.pid.lower()}" in exclude_set:
                continue
            result = usbipd_wrapper.bind_device(device.busid)
            if result.success:
                self._failed_auto_share_busids.discard(device.busid)
                self.device_auto_shared.emit(device.busid, device.description)
                logger.info(f"Auto-share: bound {device.busid} ({device.description})")
            else:
                self._failed_auto_share_busids.add(device.busid)
                logger.warning(f"Auto-share bind failed for {device.busid}: {result.message}")
