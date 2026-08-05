import logging
import threading
import traceback

from PyQt6.QtCore import QThread, pyqtSignal

from client.core import usbip_wrapper

logger = logging.getLogger(__name__)

_shutting_down = False
_active_worker_runs: dict[int, tuple[int, bool]] = {}
_active_worker_runs_lock = threading.Lock()


def _mark_worker_running(worker, killable: bool):
    with _active_worker_runs_lock:
        _active_worker_runs[id(worker)] = (threading.get_ident(), killable)


def _clear_worker_running(worker):
    with _active_worker_runs_lock:
        _active_worker_runs.pop(id(worker), None)


def active_killable_thread_ids() -> set[int]:
    """Return only this process's active attach-worker owner thread IDs."""
    with _active_worker_runs_lock:
        return {
            thread_id
            for thread_id, killable in _active_worker_runs.values()
            if killable
        }


def set_shutting_down():
    global _shutting_down
    _shutting_down = True


class AttachWorker(QThread):
    finished = pyqtSignal(bool, str, str, int)
    shutdown_killable = True

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
        _mark_worker_running(self, killable=True)
        try:
            result = usbip_wrapper.attach_device(self._host_ip, self._busid, vid=self._vid, pid=self._pid)
            port = (usbip_wrapper.parse_attach_port(result.stdout) or 0) if result.success else 0
            self.finished.emit(result.success, result.message, self._busid, port)
        except Exception as exc:
            logger.error(f"AttachWorker crashed: {exc}\n{traceback.format_exc()}")
            self.finished.emit(False, str(exc), self._busid, 0)
        finally:
            _clear_worker_running(self)


class DetachWorker(QThread):
    finished = pyqtSignal(bool, str, str)
    shutdown_killable = False

    def __init__(
        self,
        busid: str,
        port: int | None = None,
        expected_vid: str = "",
        expected_pid: str = "",
        parent=None,
        timeout: int | None = None,
    ):
        super().__init__(parent)
        self._busid = busid
        self._port = port
        self._expected_vid = expected_vid
        self._expected_pid = expected_pid
        self._timeout = timeout

    def run(self):
        _mark_worker_running(self, killable=False)
        try:
            kwargs = {
                "port_hint": self._port,
                "expected_vid": self._expected_vid,
                "expected_pid": self._expected_pid,
            }
            if self._timeout is not None:
                kwargs["timeout"] = self._timeout
            result = usbip_wrapper.detach_busid(self._busid, **kwargs)
            self.finished.emit(result.success, result.message, self._busid)
        except Exception as exc:
            logger.error(f"DetachWorker crashed: {exc}\n{traceback.format_exc()}")
            self.finished.emit(False, str(exc), self._busid)
        finally:
            _clear_worker_running(self)
