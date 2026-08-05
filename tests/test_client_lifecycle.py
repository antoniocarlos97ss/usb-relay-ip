import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shared.models import AttachedDevice, UsbDevice


class _FakeWidget:
    def __init__(self, *args, **kwargs):
        pass


class _FakeQThread:
    def __init__(self, parent=None):
        self.parent = parent

    def wait(self, *_args, **_kwargs):
        return True


class _FakeSignalDescriptor:
    def __set_name__(self, owner, name):
        self._name = f"_{name}_signal"

    def __get__(self, instance, owner):
        if instance is None:
            return self
        signal = instance.__dict__.get(self._name)
        if signal is None:
            signal = SimpleNamespace(connect=lambda *_args, **_kwargs: None)
            instance.__dict__[self._name] = signal
        return signal


def _ensure_client_main_window_importable():
    """Load ClientMainWindow behind Qt/module fakes without creating a GUI."""
    pyqt6 = sys.modules.setdefault("PyQt6", types.ModuleType("PyQt6"))
    qtcore = sys.modules.setdefault("PyQt6.QtCore", types.ModuleType("PyQt6.QtCore"))
    qtgui = sys.modules.setdefault("PyQt6.QtGui", types.ModuleType("PyQt6.QtGui"))
    qtwidgets = sys.modules.setdefault("PyQt6.QtWidgets", types.ModuleType("PyQt6.QtWidgets"))
    pyqt6.QtCore = qtcore
    pyqt6.QtGui = qtgui
    pyqt6.QtWidgets = qtwidgets

    qtcore.QThread = _FakeQThread
    qtcore.QObject = _FakeWidget
    qtcore.QTimer = _FakeWidget
    qtcore.QCloseEvent = _FakeWidget
    qtcore.pyqtSignal = lambda *_args, **_kwargs: _FakeSignalDescriptor()
    qtgui.QCloseEvent = _FakeWidget

    for name in (
        "QHBoxLayout",
        "QInputDialog",
        "QLabel",
        "QMainWindow",
        "QPushButton",
        "QStatusBar",
        "QTabWidget",
        "QVBoxLayout",
        "QWidget",
    ):
        setattr(qtwidgets, name, _FakeWidget)

    gui_fakes = {
        "client.gui.device_table": {"ClientDeviceTable": _FakeWidget},
        "client.gui.log_viewer": {"LogViewer": _FakeWidget},
        "client.gui.settings_dialog": {"ClientSettingsDialog": _FakeWidget},
        "client.gui.tray": {"ClientTrayIcon": _FakeWidget},
    }
    for module_name, attributes in gui_fakes.items():
        module = sys.modules.setdefault(module_name, types.ModuleType(module_name))
        for name, value in attributes.items():
            setattr(module, name, value)

    from client.gui.main_window import ClientMainWindow

    return ClientMainWindow


class ClientLifecycleTests(unittest.TestCase):
    def _new_window(self):
        return object.__new__(_ensure_client_main_window_importable())

    def test_shutdown_resolver_enumerates_live_session_beyond_port_cache(self):
        from client.core.lifecycle import resolve_live_shutdown_sessions

        live = [AttachedDevice(port=9, busid="1-9", vid="1234", pid="abcd")]
        host = [UsbDevice(busid="1-9", vid="1234", pid="abcd", description="Token", state="Attached")]

        sessions, rejected = resolve_live_shutdown_sessions(
            live,
            host_devices=host,
            cached_devices=[],
            host_reachable=True,
        )

        self.assertEqual([(9, "1-9", "1234", "abcd")], [
            (item.port, item.busid, item.vid, item.pid) for item in sessions
        ])
        self.assertEqual([], rejected)

    def test_shutdown_resolver_fails_closed_on_changed_or_ambiguous_identity(self):
        from client.core.lifecycle import resolve_live_shutdown_sessions

        live = [AttachedDevice(port=9, busid="1-9", vid="1234", pid="abcd")]
        changed = [UsbDevice(busid="1-9", vid="9999", pid="0001", description="Replacement")]
        sessions, rejected = resolve_live_shutdown_sessions(
            live,
            host_devices=changed,
            cached_devices=[],
            host_reachable=True,
        )
        self.assertEqual([], sessions)
        self.assertTrue(any("identity changed" in reason.lower() for reason in rejected))

        cache = [
            UsbDevice(busid="1-9", vid="1234", pid="abcd", description="A"),
            UsbDevice(busid="1-9", vid="1234", pid="abcd", description="B"),
        ]
        sessions, rejected = resolve_live_shutdown_sessions(
            live,
            host_devices=[],
            cached_devices=cache,
            host_reachable=False,
        )
        self.assertEqual([], sessions)
        self.assertTrue(any("ambiguous" in reason.lower() for reason in rejected))

    def test_shutdown_resolver_uses_unique_cache_when_host_is_unreachable(self):
        from client.core.lifecycle import resolve_live_shutdown_sessions

        live = [AttachedDevice(port=9, busid="1-9", vid="1234", pid="abcd")]
        cache = [UsbDevice(busid="1-9", vid="1234", pid="abcd", description="Token")]

        sessions, rejected = resolve_live_shutdown_sessions(
            live,
            host_devices=[],
            cached_devices=cache,
            host_reachable=False,
        )

        self.assertEqual([(9, "1-9")], [(item.port, item.busid) for item in sessions])
        self.assertEqual([], rejected)

    def test_shutdown_resolver_rejects_every_duplicate_live_busid(self):
        from client.core.lifecycle import resolve_live_shutdown_sessions

        live = [
            AttachedDevice(port=9, busid="1-9", vid="1234", pid="abcd"),
            AttachedDevice(port=10, busid="1-9", vid="1234", pid="abcd"),
        ]
        host = [
            UsbDevice(
                busid="1-9",
                vid="1234",
                pid="abcd",
                description="Token",
                state="Attached",
            )
        ]

        sessions, rejected = resolve_live_shutdown_sessions(
            live,
            host_devices=host,
            cached_devices=[],
            host_reachable=True,
        )

        self.assertEqual([], sessions)
        self.assertTrue(any("duplicate" in reason.lower() for reason in rejected))

    def test_shutdown_resolver_rejects_port_outside_usbip_range(self):
        from client.core.lifecycle import resolve_live_shutdown_sessions

        live = [AttachedDevice(port=0, busid="1-9", vid="1234", pid="abcd")]
        host = [
            UsbDevice(
                busid="1-9",
                vid="1234",
                pid="abcd",
                description="Token",
                state="Attached",
            )
        ]

        sessions, rejected = resolve_live_shutdown_sessions(
            live,
            host_devices=host,
            cached_devices=[],
            host_reachable=True,
        )

        self.assertEqual([], sessions)
        self.assertTrue(any("port invalid" in reason.lower() for reason in rejected))

    def test_main_separates_commit_data_request_from_explicit_quit(self):
        source = (Path(__file__).parents[1] / "client" / "main.py").read_text(encoding="utf-8")
        self.assertIn("window.commit_data_request", source)
        self.assertIn("window.quit_app_with_detach", source)
        self.assertNotIn("app.commitDataRequest.connect(lambda _manager: _quit())", source)

    def test_headless_pnp_validation_uses_shared_twelve_second_window(self):
        source = (Path(__file__).parents[1] / "client" / "main.py").read_text(encoding="utf-8")

        self.assertNotIn("time.monotonic() + 15", source)
        self.assertGreaterEqual(source.count("time.monotonic() + VALIDATE_SECONDS"), 2)

    def test_detach_sets_shutdown_barrier_before_empty_resolution(self):
        from client.core import usbip_worker

        window = self._new_window()
        window._port_map = {"stale": 3}
        observed = []

        def resolve(**_kwargs):
            observed.append(usbip_worker._shutting_down)
            return []

        window._resolve_live_shutdown_sessions = resolve
        with patch.object(usbip_worker, "_shutting_down", False):
            window.detach_all_async()

        self.assertEqual([True], observed)
        self.assertEqual({}, window._port_map)

    def test_commit_data_request_uses_safe_local_timeout_and_short_host_timeout(self):
        window = self._new_window()
        window._shutting_down = False
        calls = []
        window.quit_app = lambda: calls.append(("quit",))
        window.detach_all_async = lambda **kwargs: calls.append(("detach", kwargs))
        window._wait_for_transaction_workers = lambda timeout_ms: calls.append(("wait", timeout_ms))

        window.commit_data_request()

        self.assertEqual(
            [
                ("quit",),
                ("detach", {"local_timeout": 3.0, "host_timeout": 0.35}),
                ("wait", 15000),
            ],
            calls,
        )

    def test_transaction_wait_uses_one_deadline_reduced_between_worker_pools(self):
        window = self._new_window()
        waits = []
        window._workers = []
        window._wait_for_workers = lambda timeout_ms: waits.append(("client", timeout_ms))
        window._scheduled_reconnect = SimpleNamespace(
            wait_for_workers=lambda timeout_ms: waits.append(("reconnect", timeout_ms)),
        )

        with patch("client.gui.main_window.time.monotonic", side_effect=[100.0, 103.0, 108.0]), \
             patch("client.gui.main_window.usbip_worker.active_killable_thread_ids", return_value=set()):
            window._wait_for_transaction_workers(15000)

        self.assertEqual([("client", 12000), ("reconnect", 7000)], waits)
        self.assertLess(waits[1][1], waits[0][1])

    def test_transaction_wait_kills_only_active_killable_ids(self):
        window = self._new_window()
        window._workers = []
        window._wait_for_workers = lambda _timeout_ms: None
        window._scheduled_reconnect = None

        with patch(
            "client.gui.main_window.usbip_worker.active_killable_thread_ids",
            return_value={101, 202},
        ) as active_ids, patch(
            "client.gui.main_window.usbip_wrapper.kill_all_subprocesses",
        ) as kill_subprocesses:
            window._wait_for_transaction_workers(15000)

        active_ids.assert_called_once_with()
        kill_subprocesses.assert_called_once_with({101, 202})


if __name__ == "__main__":
    unittest.main()
