import importlib
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
    received_parents = []

    def __init__(self, parent=None):
        type(self).received_parents.append(parent)

    def wait(self, *_args, **_kwargs):
        return True


def _pyqt_signal(*_args, **_kwargs):
    return _SignalDescriptor()


def _load_detach_worker_with_isolated_qt():
    pyqt6 = types.ModuleType("PyQt6")
    qtcore = types.ModuleType("PyQt6.QtCore")
    qtcore.QThread = _FakeQThread
    qtcore.pyqtSignal = _pyqt_signal
    pyqt6.QtCore = qtcore

    # PyQt6 is process-global, and other test modules install incompatible
    # stubs.  Import the worker with this test's stubs only, then restore the
    # caller's module state so either unittest module order remains valid.
    qt_modules = {
        "PyQt6": pyqt6,
        "PyQt6.QtCore": qtcore,
    }
    previous_qt_modules = {
        name: sys.modules.get(name)
        for name in qt_modules
    }
    missing = object()
    previous_worker = sys.modules.pop("client.core.usbip_worker", missing)
    core_package = importlib.import_module("client.core")
    previous_worker_attribute = core_package.__dict__.pop("usbip_worker", missing)
    sys.modules.update(qt_modules)
    try:
        module = importlib.import_module("client.core.usbip_worker")
    except Exception:
        if previous_worker is not missing:
            sys.modules["client.core.usbip_worker"] = previous_worker
        if previous_worker_attribute is not missing:
            core_package.usbip_worker = previous_worker_attribute
        raise
    finally:
        for name, previous in previous_qt_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return module.DetachWorker


DetachWorker = _load_detach_worker_with_isolated_qt()


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

    def test_positional_parent_argument_remains_backward_compatible(self):
        parent = object()
        _FakeQThread.received_parents.clear()

        DetachWorker("1-11", 7, "1234", "abcd", parent)

        self.assertEqual([parent], _FakeQThread.received_parents)

    def test_shutdown_detach_forwards_a_bounded_command_timeout(self):
        worker = DetachWorker(
            "1-11",
            port=7,
            expected_vid="1234",
            expected_pid="abcd",
            timeout=2,
        )

        with patch("client.core.usbip_worker.usbip_wrapper.detach_busid") as detach:
            detach.return_value = types.SimpleNamespace(success=True, message="detached")
            worker.run()

        detach.assert_called_once_with(
            "1-11",
            port_hint=7,
            expected_vid="1234",
            expected_pid="abcd",
            timeout=2,
        )


if __name__ == "__main__":
    unittest.main()
