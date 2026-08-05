import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


qtcore = types.ModuleType("PyQt6.QtCore")
qtcore.Qt = types.SimpleNamespace(WindowModality=types.SimpleNamespace(WindowModal=1))
qtcore.QTimer = type("QTimer", (), {})
qtcore.QThread = type(
    "QThread",
    (),
    {
        "__init__": lambda self, *args: None,
        "wait": lambda self, *args: True,
        "start": lambda self, *args: None,
    },
)
qtcore.QObject = type("QObject", (), {})
qtcore.pyqtSignal = lambda *args, **kwargs: Mock()
qtgui = types.ModuleType("PyQt6.QtGui")
qtgui.QIcon = type("QIcon", (), {})
qtwidgets = types.ModuleType("PyQt6.QtWidgets")
for name in ("QApplication", "QMessageBox", "QProgressDialog", "QSystemTrayIcon"):
    setattr(qtwidgets, name, type(name, (), {}))
pyqt6 = types.ModuleType("PyQt6")
pyqt6.QtCore = qtcore
pyqt6.QtGui = qtgui
pyqt6.QtWidgets = qtwidgets
sys.modules.setdefault("PyQt6", pyqt6)
sys.modules.setdefault("PyQt6.QtCore", qtcore)
sys.modules.setdefault("PyQt6.QtGui", qtgui)
sys.modules.setdefault("PyQt6.QtWidgets", qtwidgets)

from client import main


class ClientCleanupTests(unittest.TestCase):
    def test_headless_monitor_filters_nonpermanent_devices(self):
        from client.main import _filter_permanent_devices
        from shared.models import ClientConfig, ClientPermanentDevice, UsbDevice

        configured = UsbDevice(
            busid="1-11", vid="1234", pid="abcd", description="Configured", state="Attached"
        )
        unrelated = UsbDevice(
            busid="1-12", vid="9999", pid="0001", description="Other", state="Attached"
        )
        config = ClientConfig(permanent_devices=[
            ClientPermanentDevice(vid="1234", pid="abcd", description="Configured")
        ])

        self.assertEqual(
            [configured],
            _filter_permanent_devices([configured, unrelated], config),
        )

    def test_emergency_cleanup_only_kills_owned_usbip_children(self):
        with patch("client.core.usbip_wrapper.kill_all_subprocesses") as kill_owned, \
             patch("client.core.usbip_worker.active_killable_thread_ids", return_value={101}) as owners, \
             patch("subprocess.run") as global_taskkill:
            main._emergency_cleanup()

        owners.assert_called_once_with()
        kill_owned.assert_called_once_with({101})
        global_taskkill.assert_not_called()

    def test_gui_cleanup_does_not_force_release_active_operation_locks(self):
        source = (Path(__file__).parents[1] / "client" / "gui" / "main_window.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("operation_coordinator.reset()", source)

    def test_headless_keeps_monitoring_after_devices_are_attached(self):
        from shared.models import UsbDevice

        configured = Mock(vid="1234", pid="abcd")
        config = Mock(
            host_ip="10.0.0.10",
            host_port=5757,
            api_key="",
            permanent_devices=[configured],
        )
        device = UsbDevice(
            busid="1-11",
            vid="1234",
            pid="abcd",
            description="Token",
            state="Attached",
        )
        api_client = Mock()
        api_client.get_devices.return_value = [device]
        api_client.is_connected.return_value = True

        with patch("client.core.config_manager.load_config", return_value=config), \
             patch("client.api.host_client.HostApiClient", return_value=api_client), \
             patch("client.core.usbip_wrapper.query_attached_devices") as local_query, \
             patch("client.core.pnp_recovery._wait_pnp_healthy", return_value=True), \
             patch.object(main, "_monitor_headless", create=True) as monitor:
            from client.core.usbip_wrapper import AttachedDevicesQuery
            from shared.models import AttachedDevice

            local_query.return_value = AttachedDevicesQuery(
                True,
                (AttachedDevice(port=3, busid="1-11", vid="1234", pid="abcd"),),
            )
            main.run_headless()

        monitor.assert_called_once()
        self.assertEqual((api_client, [device]), monitor.call_args.args)
        self.assertIn("stop_event", monitor.call_args.kwargs)

    def test_headless_recovers_host_attached_without_local_session(self):
        from client.core.usbip_wrapper import AttachedDevicesQuery
        from shared.models import UsbDevice

        configured = Mock(vid="1234", pid="abcd")
        config = Mock(host_ip="10.0.0.10", host_port=5757, api_key="", permanent_devices=[configured])
        device = UsbDevice(
            busid="1-11", vid="1234", pid="abcd", description="Token", state="Attached"
        )
        api_client = Mock()
        api_client.get_devices.return_value = [device]
        api_client.is_connected.return_value = True

        with patch("client.core.config_manager.load_config", return_value=config), \
             patch("client.api.host_client.HostApiClient", return_value=api_client), \
             patch(
                 "client.core.usbip_wrapper.query_attached_devices",
                 return_value=AttachedDevicesQuery(True, ()),
             ), \
             patch(
                 "client.core.scheduled_reconnect._run_reconnect_cycle",
                 return_value=(True, "1-11"),
             ) as reconnect, \
             patch.object(main, "_monitor_headless", create=True):
            main.run_headless(max_attempts=1, retry_seconds=0)

        reconnect.assert_called_once_with(api_client, device, record_completion=False)

    def test_headless_default_loop_continues_until_stop_event(self):
        stop = __import__("threading").Event()
        configured = Mock(vid="1234", pid="abcd")
        config = Mock(host_ip="10.0.0.10", host_port=5757, api_key="", permanent_devices=[configured])
        api_client = Mock()
        calls = 0

        def unavailable():
            nonlocal calls
            calls += 1
            if calls == 2:
                stop.set()
            return []

        api_client.get_devices.side_effect = unavailable
        api_client.is_connected.return_value = False
        with patch("client.core.config_manager.load_config", return_value=config), \
             patch("client.api.host_client.HostApiClient", return_value=api_client):
            main.run_headless(retry_seconds=0, stop_event=stop)

        self.assertEqual(2, calls)

    def test_headless_attach_loop_retries_after_poll_exception(self):
        stop = __import__("threading").Event()
        configured = Mock(vid="1234", pid="abcd")
        config = Mock(
            host_ip="10.0.0.10",
            host_port=5757,
            api_key="",
            permanent_devices=[configured],
        )
        api_client = Mock()
        calls = 0

        def poll():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("temporary host poll failure")
            stop.set()
            return []

        api_client.get_devices.side_effect = poll
        api_client.is_connected.return_value = False
        with patch("client.core.config_manager.load_config", return_value=config), \
             patch("client.api.host_client.HostApiClient", return_value=api_client):
            main.run_headless(retry_seconds=0, stop_event=stop)

        self.assertEqual(2, calls)

    def test_headless_refuses_ambiguous_identical_devices(self):
        from shared.models import UsbDevice

        configured = Mock(vid="1234", pid="abcd")
        config = Mock(
            host_ip="10.0.0.10",
            host_port=5757,
            api_key="",
            permanent_devices=[configured],
        )
        devices = [
            UsbDevice(busid="1-11", vid="1234", pid="abcd", description="A", state="Shared"),
            UsbDevice(busid="1-12", vid="1234", pid="abcd", description="B", state="Shared"),
        ]
        api_client = Mock()
        api_client.get_devices.return_value = devices
        api_client.is_connected.return_value = True

        with patch("client.core.config_manager.load_config", return_value=config), \
             patch("client.api.host_client.HostApiClient", return_value=api_client), \
             patch("client.core.usbip_wrapper.attach_device") as attach, \
             patch("client.core.scheduled_reconnect._run_reconnect_cycle") as reconnect, \
             patch.object(main, "_monitor_headless", create=True) as monitor:
            main.run_headless(max_attempts=1, retry_seconds=0)

        attach.assert_not_called()
        reconnect.assert_not_called()
        monitor.assert_not_called()

    def test_headless_attach_failure_uses_verified_reconnect_cycle(self):
        from shared.models import CommandResult, UsbDevice

        configured = Mock(vid="1234", pid="abcd")
        config = Mock(
            host_ip="10.0.0.10",
            host_port=5757,
            api_key="",
            permanent_devices=[configured],
        )
        device = UsbDevice(
            busid="1-11",
            vid="1234",
            pid="abcd",
            description="Token",
            state="Shared",
        )
        api_client = Mock()
        api_client.get_devices.return_value = [device]
        api_client.is_connected.return_value = True

        with patch("client.core.config_manager.load_config", return_value=config), \
             patch("client.api.host_client.HostApiClient", return_value=api_client), \
             patch(
                 "client.core.usbip_wrapper.attach_device",
                 return_value=CommandResult(success=False, message="failed"),
             ), \
             patch("client.core.scheduled_reconnect._run_reconnect_cycle", return_value=(True, "1-11")) as reconnect, \
             patch.object(main, "_monitor_headless", create=True) as monitor:
            main.run_headless(max_attempts=1, retry_seconds=0)

        reconnect.assert_called_once_with(
            api_client,
            device,
            record_completion=False,
        )
        monitor.assert_called_once()
        self.assertEqual((api_client, [device]), monitor.call_args.args)
        self.assertIn("stop_event", monitor.call_args.kwargs)

    def test_headless_monitor_refreshes_devices_and_stops(self):
        initial = [Mock(busid="1-11")]
        refreshed = [Mock(busid="1-11")]
        api_client = Mock()
        api_client.get_devices.return_value = refreshed
        api_client.is_connected.return_value = True
        monitor = Mock()

        class _OneRefreshStop:
            calls = 0

            def wait(self, _timeout):
                self.calls += 1
                return self.calls > 1

        with patch("client.core.pnp_recovery.PnpRecoveryCore", return_value=monitor):
            main._monitor_headless(
                api_client,
                initial,
                stop_event=_OneRefreshStop(),
                refresh_seconds=1,
            )

        monitor.start.assert_called_once()
        self.assertEqual([initial, refreshed], [item.args[0] for item in monitor.update_devices.call_args_list])
        monitor.stop.assert_called_once()

    def test_headless_monitor_uses_plain_core_without_starting_qthread(self):
        initial = [Mock(busid="1-11")]
        api_client = Mock()

        class _StopAfterOneRefresh:
            def __init__(self):
                self.calls = 0

            def wait(self, _timeout):
                self.calls += 1
                return True

        plain_monitor = Mock()
        with patch("client.core.pnp_recovery.PnpRecoveryCore", return_value=plain_monitor) as core, \
             patch("client.core.pnp_recovery.PnpRecoveryMonitor", side_effect=AssertionError("QThread must not start")):
            main._monitor_headless(
                api_client,
                initial,
                stop_event=_StopAfterOneRefresh(),
                refresh_seconds=1,
            )

        core.assert_called_once()
        plain_monitor.start.assert_called_once()
        plain_monitor.poll_cycle.assert_called_once()
        plain_monitor.stop.assert_called_once()

    def test_headless_monitor_continues_after_refresh_exception(self):
        initial = [Mock(busid="1-11")]
        api_client = Mock()
        api_client.get_devices.side_effect = [RuntimeError("temporary poll failure"), []]
        api_client.is_connected.return_value = True

        class _StopAfterTwoRefreshes:
            def __init__(self):
                self.calls = 0

            def wait(self, _timeout):
                self.calls += 1
                return self.calls > 2

        plain_monitor = Mock()
        with patch("client.core.pnp_recovery.PnpRecoveryCore", return_value=plain_monitor), \
             patch("client.core.config_manager.load_config", side_effect=RuntimeError("config unavailable")):
            main._monitor_headless(
                api_client,
                initial,
                stop_event=_StopAfterTwoRefreshes(),
                refresh_seconds=1,
            )

        self.assertGreaterEqual(plain_monitor.poll_cycle.call_count, 1)
        plain_monitor.stop.assert_called_once()

    def test_headless_boot_task_is_continuous_and_single_instance(self):
        sys.modules.setdefault("winreg", types.ModuleType("winreg"))
        from client.core import autostart_manager

        self.assertIn(
            "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>",
            autostart_manager._BOOT_TASK_XML,
        )
        self.assertIn(
            "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",
            autostart_manager._BOOT_TASK_XML,
        )

    def test_gui_stale_retry_uses_verified_worker_without_fixed_host_sleeps(self):
        source = (Path(__file__).parents[1] / "client" / "gui" / "main_window.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("ScheduledReconnectWorker", source)
        self.assertIn("record_completion=False", source)
        self.assertNotIn("def _retry_do_unbind", source)
        self.assertNotIn("def _retry_do_bind", source)
        self.assertNotIn("def _retry_do_attach", source)
        self.assertIn("device identity unavailable", source)
        self.assertNotIn(".terminate()", source)

    def test_client_installer_grants_shared_state_access_by_sid(self):
        build_dir = Path(__file__).parents[1] / "build"
        installer = (build_dir / "installer_client.nsi").read_text(encoding="utf-8")
        acl_script = (build_dir / "set_shared_acl.ps1").read_text(encoding="utf-8")

        self.assertIn(r'CreateDirectory "$SharedStateRoot"', installer)
        self.assertIn(r'StrCpy $SharedStateRoot "$APPDATA\USBRelay"', installer)
        self.assertIn(r'-LegacyRoot "$APPDATA\USBRelay"', installer)
        self.assertIn(
            r'IfFileExists "$SharedStateRoot\." shared_state_exists shared_state_missing',
            installer,
        )
        self.assertIn("set_shared_acl.ps1", installer)
        self.assertIn("Abort", installer)
        self.assertIn("S-1-5-18", acl_script)
        self.assertIn("S-1-5-32-544", acl_script)
        self.assertIn("WindowsIdentity]::GetCurrent().User", acl_script)
        self.assertIn("SetAccessRuleProtection($true, $false)", acl_script)
        self.assertNotIn("S-1-5-32-545", installer + acl_script)
        self.assertIn('schtasks.exe /Delete /TN "USBRelayClientBoot" /F', installer)
        normalized_installer = installer.replace("\\\\", "\\")
        self.assertIn(
            'DeleteRegValue HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "USBRelayClient"',
            normalized_installer,
        )
        self.assertIn("EnumerateFileSystemInfos", acl_script)


if __name__ == "__main__":
    unittest.main()
