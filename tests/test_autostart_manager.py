import unittest
from unittest.mock import Mock, patch


class TestAutostartManagerHost(unittest.TestCase):

    @patch("host.core.autostart_manager.subprocess.run")
    def test_register_boot_task_success(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="created", stderr="")

        from host.core.autostart_manager import register_boot_task
        result = register_boot_task("C:\\USBRelay\\USBRelayHost.exe")
        self.assertTrue(result)
        self.assertGreaterEqual(mock_run.call_count, 1)

    @patch("host.core.autostart_manager.subprocess.run")
    def test_register_boot_task_failure(self, mock_run):
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="access denied")

        from host.core.autostart_manager import register_boot_task
        result = register_boot_task("C:\\USBRelay\\USBRelayHost.exe")
        self.assertFalse(result)

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

    @patch("client.core.autostart_manager.subprocess.run")
    def test_register_startup_success(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")

        from client.core.autostart_manager import register_startup
        result = register_startup("C:\\USBRelay\\USBRelayClient.exe")
        self.assertEqual(result, (True, True))

    @patch("client.core.autostart_manager.subprocess.run")
    def test_register_startup_failure(self, mock_run):
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="access denied")

        from client.core.autostart_manager import register_startup
        result = register_startup("C:\\USBRelay\\USBRelayClient.exe")
        self.assertEqual(result, (True, False))

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
