"""Background thread that continuously monitors the usbipd service health.

Periodically checks whether port 3240 is listening. If the service goes down,
it emits `service_down` and attempts auto-recovery via `ensure_service_running()`.
When recovery succeeds, it emits `service_recovered`.
`service_error` is emitted only ONCE per failure streak (debounced) to avoid
spamming the tray with notifications every poll cycle.
"""
import logging
import time

from PyQt6.QtCore import QThread, pyqtSignal

from host.core import usbipd_wrapper

logger = logging.getLogger(__name__)


class ServiceMonitor(QThread):
    """Monitors the usbipd service in a background thread.

    Signals:
        service_healthy:   Emitted when the service is confirmed listening.
        service_down:      Emitted when the service stops listening.
        service_recovered: Emitted after auto-recovery brings the service back.
        service_error:     Emitted when recovery fails (str message). Debounced:
                           only fires once per failure streak, not every poll.
    """
    service_healthy = pyqtSignal()
    service_down = pyqtSignal()
    service_recovered = pyqtSignal()
    service_error = pyqtSignal(str)

    def __init__(self, poll_interval: int = 10, parent=None):
        super().__init__(parent)
        self._poll_interval = max(3, poll_interval)
        self._running = False
        self._was_healthy = True  # Assume healthy at start; first check will correct
        self._error_reported = False  # Debounce: only emit service_error once per streak

    def set_poll_interval(self, seconds: int):
        self._poll_interval = max(3, seconds)

    def _poll_once(self):
        """Execute a single poll cycle. Emits signals as needed.

        This method is extracted so tests can exercise the real production
        logic without running the full QThread event loop.
        """
        try:
            listening = usbipd_wrapper.check_port_listening(3240)

            if listening:
                if not self._was_healthy:
                    logger.info("Service monitor: service is healthy again")
                    self.service_recovered.emit()
                self._was_healthy = True
                self._error_reported = False  # Reset debounce on recovery
                self.service_healthy.emit()
            else:
                if self._was_healthy:
                    logger.warning("Service monitor: usbipd service is DOWN (port 3240 not listening)")
                    self.service_down.emit()

                # Attempt auto-recovery
                logger.info("Service monitor: attempting auto-recovery...")
                ok, msg = usbipd_wrapper.ensure_service_running()
                if ok:
                    logger.info(f"Service monitor: auto-recovery succeeded: {msg}")
                    self._was_healthy = True
                    self._error_reported = False
                    self.service_recovered.emit()
                else:
                    # Debounce: only emit service_error once per failure streak
                    if not self._error_reported:
                        logger.error(f"Service monitor: auto-recovery failed: {msg}")
                        self.service_error.emit(msg)
                        self._error_reported = True
                    self._was_healthy = False

        except Exception as exc:
            logger.error(f"Service monitor error: {exc}")

    def run(self):
        self._running = True
        logger.info("Service monitor started")

        while self._running:
            self._poll_once()

            # Sleep in 1-second increments so we can exit quickly
            for _ in range(self._poll_interval):
                if not self._running:
                    break
                time.sleep(1)

        logger.info("Service monitor stopped")

    def stop(self):
        self._running = False
        # Wait up to 60s — ensure_service_running can block ~40s in worst case
        self.wait(60000)