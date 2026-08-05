import logging
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

    def __init__(self, host_ip: str, busid: str, vid: str = "", pid: str = "", parent=None):
        super().__init__(parent)
        self._host_ip = host_ip
        self._busid = busid
        self._vid = vid
        self._pid = pid

    def run(self):
        if _shutting_down:
            self.finished.emit(False, "Shutting down", self._busid, 0)
            return
        try:
            result = usbip_wrapper.attach_device(self._host_ip, self._busid, vid=self._vid, pid=self._pid)
            port = (usbip_wrapper.parse_attach_port(result.stdout) or 0) if result.success else 0
            self.finished.emit(result.success, result.message, self._busid, port)
        except Exception as exc:
            logger.error(f"AttachWorker crashed: {exc}\n{traceback.format_exc()}")
            self.finished.emit(False, str(exc), self._busid, 0)


class DetachWorker(QThread):
    finished = pyqtSignal(bool, str, str)

    def __init__(
        self,
        busid: str,
        port: int | None = None,
        expected_vid: str = "",
        expected_pid: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._busid = busid
        self._port = port
        self._expected_vid = expected_vid
        self._expected_pid = expected_pid

    def run(self):
        try:
            result = usbip_wrapper.detach_busid(
                self._busid,
                port_hint=self._port,
                expected_vid=self._expected_vid,
                expected_pid=self._expected_pid,
            )
            self.finished.emit(result.success, result.message, self._busid)
        except Exception as exc:
            logger.error(f"DetachWorker crashed: {exc}\n{traceback.format_exc()}")
            self.finished.emit(False, str(exc), self._busid)
