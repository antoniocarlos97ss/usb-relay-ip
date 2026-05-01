import logging
import traceback

from PyQt6.QtCore import QThread, pyqtSignal

from client.core import usbip_wrapper

logger = logging.getLogger(__name__)


class AttachWorker(QThread):
    finished = pyqtSignal(bool, str, str)

    def __init__(self, host_ip: str, busid: str, parent=None):
        super().__init__(parent)
        self._host_ip = host_ip
        self._busid = busid

    def run(self):
        try:
            result = usbip_wrapper.attach_device(self._host_ip, self._busid)
            self.finished.emit(result.success, result.message, self._busid)
        except Exception as exc:
            logger.error(f"AttachWorker crashed: {exc}\n{traceback.format_exc()}")
            self.finished.emit(False, str(exc), self._busid)


class DetachWorker(QThread):
    finished = pyqtSignal(bool, str, str)

    def __init__(self, busid: str, parent=None):
        super().__init__(parent)
        self._busid = busid

    def run(self):
        try:
            port = usbip_wrapper.find_port_for_busid(self._busid)
            if port is None:
                self.finished.emit(False, f"No port found for {self._busid}", self._busid)
                return
            result = usbip_wrapper.detach_device(port)
            self.finished.emit(result.success, result.message, self._busid)
        except Exception as exc:
            logger.error(f"DetachWorker crashed: {exc}\n{traceback.format_exc()}")
            self.finished.emit(False, str(exc), self._busid)
