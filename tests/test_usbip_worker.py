import sys
import types
import unittest
from unittest.mock import patch


class _FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _SignalDescriptor:
    def __set_name__(self, owner, name):
        self._name = f"_{name}_signal"

    def __get__(self, instance, owner):
        if instance is None:
            return self
        signal = instance.__dict__.get(self._name)
        if signal is None:
            signal = _FakeSignal()
            instance.__dict__[self._name] = signal
        return signal


class _FakeQThread:
    def __init__(self, parent=None):
        self.parent = parent

    def wait(self, *_args, **_kwargs):
        return True


def _pyqt_signal(*_args, **_kwargs):
    return _SignalDescriptor()


if "PyQt6.QtCore" not in sys.modules:
    pyqt6 = types.ModuleType("PyQt6")
    qtcore = types.ModuleType("PyQt6.QtCore")
    qtcore.QThread = _FakeQThread
    qtcore.pyqtSignal = _pyqt_signal
    pyqt6.QtCore = qtcore
    sys.modules["PyQt6"] = pyqt6
    sys.modules["PyQt6.QtCore"] = qtcore

from client.core.usbip_worker import DetachWorker


class DetachWorkerTests(unittest.TestCase):
    def test_port_hint_is_validated_before_detach(self):
        emitted = []
        worker = DetachWorker("1-11", port=7, expected_vid="1234", expected_pid="abcd")
        worker.finished.connect(lambda success, message, busid: emitted.append((success, message, busid)))

        with patch("client.core.usbip_worker.usbip_wrapper.detach_busid") as detach:
            detach.return_value = types.SimpleNamespace(success=False, message="No port found for 1-11")
            worker.run()

        detach.assert_called_once_with(
            "1-11",
            port_hint=7,
            expected_vid="1234",
            expected_pid="abcd",
        )
        self.assertEqual([(False, "No port found for 1-11", "1-11")], emitted)

    def test_successful_detach_uses_busid_aware_wrapper(self):
        worker = DetachWorker("1-11", port=7, expected_vid="1234", expected_pid="abcd")
        results = []
        worker.finished.connect(lambda *args: results.append(args))

        with patch("client.core.usbip_worker.usbip_wrapper.detach_busid") as detach:
            detach.return_value = types.SimpleNamespace(success=True, message="detached")
            worker.run()

        self.assertEqual([(True, "detached", "1-11")], results)
        detach.assert_called_once_with(
            "1-11",
            port_hint=7,
            expected_vid="1234",
            expected_pid="abcd",
        )


if __name__ == "__main__":
    unittest.main()
