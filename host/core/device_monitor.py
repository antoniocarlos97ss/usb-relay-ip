import logging
import time

from PyQt6.QtCore import QThread, pyqtSignal

from host.core import config_manager, usbipd_wrapper
from shared.models import UsbDevice

logger = logging.getLogger(__name__)


class DeviceMonitor(QThread):
    devices_changed = pyqtSignal(list)
    device_bound = pyqtSignal(str, bool, str)
    device_unplugged = pyqtSignal(str)
    device_auto_bound = pyqtSignal(str, str)

    def __init__(self, poll_interval: int = 5, parent=None):
        super().__init__(parent)
        self._poll_interval = poll_interval
        self._running = False
        self._previous_devices: list[UsbDevice] = []

    def run(self):
        self._running = True
        logger.info("Device monitor started")

        self._auto_bind_permanent_on_startup()

        while self._running:
            try:
                current_devices = usbipd_wrapper.list_devices()
                self._mark_permanent_status(current_devices)

                if self._device_list_changed(current_devices):
                    self.devices_changed.emit(current_devices)
                    self._handle_new_devices(current_devices)

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
        return prev_ids != curr_ids

    def _handle_new_devices(self, current_devices: list[UsbDevice]):
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
