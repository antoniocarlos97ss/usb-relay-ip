import logging
import re
import traceback

from PyQt6.QtCore import QThread, pyqtSignal

from client.core import usbip_wrapper

logger = logging.getLogger(__name__)

_shutting_down = False


def set_shutting_down():
    global _shutting_down
    _shutting_down = True


class AttachWorker(QThread):
    finished = pyqtSignal(bool, str, str, int)

    def __init__(self, host_ip: str, busid: str, parent=None):
        super().__init__(parent)
        self._host_ip = host_ip
        self._busid = busid

    def run(self):
        if _shutting_down:
            self.finished.emit(False, "Shutting down", self._busid, 0)
            return
        try:
            result = usbip_wrapper.attach_device(self._host_ip, self._busid)
            port = 0
            if result.success:
                m = re.search(r"port\s+(\d+)", result.stdout)
                if m:
                    port = int(m.group(1))
            self.finished.emit(result.success, result.message, self._busid, port)
        except Exception as exc:
            logger.error(f"AttachWorker crashed: {exc}\n{traceback.format_exc()}")
            self.finished.emit(False, str(exc), self._busid, 0)


class DetachWorker(QThread):
    finished = pyqtSignal(bool, str, str)

    def __init__(self, busid: str, port: int | None = None, parent=None):
        super().__init__(parent)
        self._busid = busid
        self._port = port

    def run(self):
        if _shutting_down:
            self.finished.emit(False, "Shutting down", self._busid)
            return
        try:
            port = self._port
            if port is None:
                port = usbip_wrapper.find_port_for_busid(self._busid)
            if port is None:
                self.finished.emit(False, f"No port found for {self._busid}", self._busid)
                return
            result = usbip_wrapper.detach_device(port)
            self.finished.emit(result.success, result.message, self._busid)
        except Exception as exc:
            logger.error(f"DetachWorker crashed: {exc}\n{traceback.format_exc()}")
            self.finished.emit(False, str(exc), self._busid)
