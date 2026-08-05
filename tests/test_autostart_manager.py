import os
import sys
import types
import unittest
from unittest.mock import Mock, patch


if "winreg" not in sys.modules:
    try:
        import winreg  # noqa: F401
    except ImportError:
        winreg = types.ModuleType("winreg")
        winreg.HKEY_CURRENT_USER = object()
        winreg.KEY_SET_VALUE = 1
        winreg.KEY_QUERY_VALUE = 2
        winreg.REG_SZ = 1
        winreg.OpenKey = lambda *args, **kwargs: None
        winreg.SetValueEx = lambda *args, **kwargs: None
        winreg.QueryValueEx = lambda *args, **kwargs: None
        winreg.DeleteValue = lambda *args, **kwargs: None
        winreg.CloseKey = lambda *args, **kwargs: None
        sys.modules["winreg"] = winreg


class TestAutostartManagerHost(unittest.TestCase):

    @patch("host.core.autostart_manager.subprocess.run")
    def test_register_boot_task_success(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="created", stderr="")

        from host.core.autostart_manager import register_boot_task
        with patch.dict(os.environ, {"ProgramW6432": r"C:\Program Files"}, clear=False):
            result = register_boot_task(r"C:\Program Files\USBRelayHost\USBRelayHost.exe")
        self.assertTrue(result)
        self.assertGreaterEqual(mock_run.call_count, 1)

    @patch("host.core.autostart_manager.subprocess.run")
    def test_register_boot_task_failure(self, mock_run):
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="access denied")

        from host.core.autostart_manager import register_boot_task
        with patch.dict(os.environ, {"ProgramW6432": r"C:\Program Files"}, clear=False):
            result = register_boot_task(r"C:\Program Files\USBRelayHost\USBRelayHost.exe")
        self.assertFalse(result)

    @patch("host.core.autostart_manager._run_schtasks")
    def test_host_system_task_rejects_repo_executable_before_schtasks(self, mock_run):
        mock_run.return_value = (True, "created", "")

        from host.core.autostart_manager import register_boot_task

        with patch.dict(os.environ, {"ProgramW6432": r"C:\Program Files"}, clear=False):
            result = register_boot_task(r"C:\Users\Alice\src\host\main.py")

        self.assertFalse(result)
        mock_run.assert_not_called()

    @patch("host.core.autostart_manager.unregister_boot_task")
    @patch("host.core.autostart_manager.register_boot_task")
    @patch("host.core.autostart_manager.unregister_logon_run")
    @patch("host.core.autostart_manager.register_logon_run")
    def test_register_startup(self, mock_register_logon, mock_unregister_logon, mock_register_boot, mock_unregister_boot):
        mock_register_logon.return_value = True
        mock_register_boot.return_value = True

        from host.core.autostart_manager import register_startup
        result = register_startup("C:\\USBRelay\\USBRelayHost.exe")
        self.assertEqual(result, (True, True))
        mock_register_logon.assert_called_once()
        mock_register_boot.assert_called_once()

    @patch("host.core.autostart_manager.unregister_boot_task")
    @patch("host.core.autostart_manager.unregister_logon_run")
    def test_unregister_startup(self, mock_unregister_logon, mock_unregister_boot):
        mock_unregister_logon.return_value = True
        mock_unregister_boot.return_value = True

        from host.core.autostart_manager import unregister_startup
        result = unregister_startup()
        self.assertEqual(result, (True, True))
        mock_unregister_logon.assert_called_once()
        mock_unregister_boot.assert_called_once()

    @patch("host.core.autostart_manager.is_boot_task_registered")
    @patch("host.core.autostart_manager.is_logon_run_registered")
    def test_is_registered_true(self, mock_logon, mock_boot):
        mock_logon.return_value = False
        mock_boot.return_value = True

        from host.core.autostart_manager import is_registered
        self.assertTrue(is_registered())

    @patch("host.core.autostart_manager.is_boot_task_registered")
    @patch("host.core.autostart_manager.is_logon_run_registered")
    def test_is_registered_false(self, mock_logon, mock_boot):
        mock_logon.return_value = False
        mock_boot.return_value = False

        from host.core.autostart_manager import is_registered
        self.assertFalse(is_registered())


class TestAutostartManagerClient(unittest.TestCase):

    @patch("client.core.autostart_manager._run_schtasks")
    def test_register_boot_task_rejects_user_writable_path_before_schtasks(self, mock_schtasks):
        mock_schtasks.return_value = (True, "created", "")

        from client.core.autostart_manager import register_boot_task

        result = register_boot_task(r"C:\Users\Alice\src\USBRelayClient.exe")

        self.assertFalse(result)
        mock_schtasks.assert_not_called()

    def test_system_task_path_must_be_under_program_files(self):
        from client.core.autostart_manager import _is_protected_system_path

        with patch.dict(os.environ, {"ProgramW6432": r"C:\Program Files"}, clear=False):
            self.assertTrue(
                _is_protected_system_path(
                    r"C:\Program Files\USBRelayClient\USBRelayClient.exe"
                )
            )
            self.assertFalse(
                _is_protected_system_path(
                    r"C:\Program Files Evil\USBRelayClient.exe"
                )
            )

    def test_system_task_does_not_trust_user_overridden_program_files_environment(self):
        from client.core import autostart_manager

        with patch.object(
            autostart_manager,
            "_registry_program_files_roots",
            return_value=(r"C:\Program Files",),
        ), patch.dict(
            os.environ,
            {"ProgramW6432": r"C:\Users\Alice\Program Files"},
            clear=False,
        ):
            self.assertTrue(
                autostart_manager._is_protected_system_path(
                    r"C:\Program Files\USBRelayClient\USBRelayClient.exe"
                )
            )
            self.assertFalse(
                autostart_manager._is_protected_system_path(
                    r"C:\Users\Alice\Program Files\USBRelayClient.exe"
                )
            )

    @patch("client.core.autostart_manager._run_schtasks")
    def test_register_boot_task_rejects_user_writable_script_argument(self, mock_schtasks):
        mock_schtasks.return_value = (True, "created", "")

        from client.core.autostart_manager import register_boot_task

        result = register_boot_task(
            r"C:\Program Files\USBRelayClient\python.exe",
            [r"C:\Users\Alice\src\client\main.py"],
        )

        self.assertFalse(result)
        mock_schtasks.assert_not_called()

    @patch("client.core.autostart_manager._run_schtasks")
    def test_system_task_rejects_all_script_arguments_even_with_protected_exe(self, mock_schtasks):
        mock_schtasks.return_value = (True, "created", "")

        from client.core.autostart_manager import register_boot_task

        with patch.dict(os.environ, {"ProgramW6432": r"C:\Program Files"}, clear=False):
            result = register_boot_task(
                r"C:\Program Files\USBRelayClient\USBRelayClient.exe",
                [r"%LOCALAPPDATA%\USBRelay\main.py"],
            )

        self.assertFalse(result)
        mock_schtasks.assert_not_called()

    def test_boot_task_restarts_after_failure_without_overlapping_instances(self):
        from client.core.autostart_manager import _render_boot_task_xml

        xml = _render_boot_task_xml(
            r"C:\Program Files\USBRelayClient\USBRelayClient.exe"
        )

        self.assertIn("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", xml)
        self.assertIn("<RestartOnFailure>", xml)
        self.assertIn("<Interval>PT1M</Interval>", xml)
        self.assertIn("<Count>3</Count>", xml)

    @patch("client.core.autostart_manager._run_schtasks")
    @patch("client.core.autostart_manager.register_logon_run", return_value=True)
    def test_register_startup_keeps_hkcu_run_but_skips_unsafe_system_task(
        self, _register_logon, mock_schtasks
    ):
        mock_schtasks.return_value = (True, "created", "")

        from client.core.autostart_manager import register_startup

        result = register_startup(r"C:\Users\Alice\src\USBRelayClient.exe")

        self.assertEqual(result, (True, False))
        mock_schtasks.assert_not_called()

    def test_boot_task_xml_escapes_command_and_separates_python_script_arguments(self):
        from client.core.autostart_manager import _render_boot_task_xml

        xml = _render_boot_task_xml(
            r"C:\Program Files\A&B\python.exe",
            [r"C:\work dir\client<main>.py"],
        )

        self.assertIn(r"<Command>C:\Program Files\A&amp;B\python.exe</Command>", xml)
        self.assertIn("client&lt;main&gt;.py", xml)
        self.assertIn("--headless", xml)
        self.assertNotIn("python.exe</Command>\n      <Arguments>--headless", xml)

    @patch("client.core.autostart_manager.winreg.CloseKey")
    @patch("client.core.autostart_manager.winreg.SetValueEx")
    @patch("client.core.autostart_manager.winreg.OpenKey")
    @patch("client.core.autostart_manager.subprocess.run")
    def test_register_startup_success(self, mock_run, mock_open, mock_set, mock_close):
        mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")
        mock_open.return_value = Mock()

        from client.core.autostart_manager import register_startup
        with patch.dict(os.environ, {"ProgramW6432": r"C:\Program Files"}, clear=False):
            result = register_startup(r"C:\Program Files\USBRelayClient\USBRelayClient.exe")
        self.assertEqual(result, (True, True))
        mock_set.assert_called_once()
        mock_close.assert_called_once()

    @patch("client.core.autostart_manager.winreg.CloseKey")
    @patch("client.core.autostart_manager.winreg.SetValueEx")
    @patch("client.core.autostart_manager.winreg.OpenKey")
    @patch("client.core.autostart_manager.subprocess.run")
    def test_register_startup_failure(self, mock_run, mock_open, mock_set, mock_close):
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="access denied")
        mock_open.return_value = Mock()

        from client.core.autostart_manager import register_startup
        result = register_startup("C:\\USBRelay\\USBRelayClient.exe")
        self.assertEqual(result, (True, False))
        mock_set.assert_called_once()
        mock_close.assert_called_once()

    @patch("client.core.autostart_manager.winreg.CloseKey")
    @patch("client.core.autostart_manager.winreg.QueryValueEx")
    @patch("client.core.autostart_manager.winreg.OpenKey")
    def test_is_registered_true(self, mock_open, mock_query, mock_close):
        mock_open.return_value = Mock()
        mock_query.return_value = ("ok",)
        mock_close.return_value = None

        from client.core.autostart_manager import is_registered
        result = is_registered()
        self.assertTrue(result)

    @patch("client.core.autostart_manager.winreg.QueryValueEx", side_effect=FileNotFoundError())
    @patch("client.core.autostart_manager.winreg.OpenKey")
    def test_is_registered_false(self, mock_open, mock_query):
        mock_open.return_value = Mock()

        from client.core.autostart_manager import is_registered
        result = is_registered()
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
