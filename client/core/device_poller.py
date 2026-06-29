import logging
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

from client.api.host_client import HostApiClient

logger = logging.getLogger(__name__)


class DevicePoller(QThread):
    devices_fetched = pyqtSignal(list)
    connection_changed = pyqtSignal(bool, str)
    service_status_changed = pyqtSignal(bool)  # True = service healthy, False = service down

    def __init__(self, api_client: HostApiClient, poll_interval: int = 10, parent=None):
        super().__init__(parent)
        self._client = api_client
        self._poll_interval = poll_interval
        self._running = False
        self._refresh_now = False
        self._lock = threading.Lock()
        self._last_service_ok = False  # Start unknown; first successful poll sends True

    def set_poll_interval(self, seconds: int):
        self._poll_interval = max(1, seconds)

    def refresh_now(self):
        self._refresh_now = True

    def _fetch(self):
        if not self._lock.acquire(blocking=False):
            return
        try:
            # Two HTTP calls per cycle: get_devices(), then get_health()
            # to refresh the host service status (usbipd_listening).
            devices = self._client.get_devices()
            connected = self._client.is_connected()

            # get_health() refreshes the host client's cached _usbipd_listening.
            service_ok = False
            if connected:
                health = self._client.get_health()
                if health:
                    service_ok = health.usbipd_listening

            if service_ok != self._last_service_ok:
                self.service_status_changed.emit(service_ok)
                self._last_service_ok = service_ok

            self.devices_fetched.emit(devices)
            self.connection_changed.emit(connected, self._client.host_ip)
        except Exception as exc:
            logger.error(f"Poll error: {exc}")
            self.connection_changed.emit(False, "")
        finally:
            self._lock.release()

    def run(self):
        self._running = True
        while self._running:
            self._fetch()

            for _ in range(self._poll_interval):
                if not self._running:
                    break
                if self._refresh_now:
                    self._refresh_now = False
                    break
                time.sleep(1)

    def stop(self):
        self._running = False
        self.wait(3000)
