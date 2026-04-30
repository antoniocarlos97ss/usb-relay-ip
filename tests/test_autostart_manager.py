import unittest
from unittest.mock import Mock, patch


class TestAutostartManagerHost(unittest.TestCase):

    @patch("host.core.autostart_manager._get_nssm_path")
    @patch("host.core.autostart_manager.subprocess.run")
    def test_install_service_success(self, mock_run, mock_nssm_path):
        mock_nssm_path.return_value = "C:\\nssm.exe"
        mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")

        from host.core.autostart_manager import install_service
        result = install_service("C:\\USBRelay\\USBRelayHost.exe")
        self.assertTrue(result)
        self.assertGreaterEqual(mock_run.call_count, 1)

    @patch("host.core.autostart_manager._get_nssm_path")
    @patch("host.core.autostart_manager.subprocess.run")
    def test_install_service_nssm_not_found(self, mock_run, mock_nssm_path):
        mock_nssm_path.return_value = None

        from host.core.autostart_manager import install_service
        result = install_service("C:\\USBRelay\\USBRelayHost.exe")
        self.assertFalse(result)

    @patch("host.core.autostart_manager._get_nssm_path")
    @patch("host.core.autostart_manager.subprocess.run")
    def test_uninstall_service(self, mock_run, mock_nssm_path):
        mock_nssm_path.return_value = "C:\\nssm.exe"
        mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")

        from host.core.autostart_manager import uninstall_service
        result = uninstall_service()
        self.assertTrue(result)

    @patch("host.core.autostart_manager._get_nssm_path")
    @patch("host.core.autostart_manager.subprocess.run")
    def test_is_service_installed(self, mock_run, mock_nssm_path):
        mock_nssm_path.return_value = "C:\\nssm.exe"
        mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")

        from host.core.autostart_manager import is_service_installed
        result = is_service_installed()
        self.assertTrue(result)

    @patch("host.core.autostart_manager._get_nssm_path")
    @patch("host.core.autostart_manager.subprocess.run")
    def test_start_service(self, mock_run, mock_nssm_path):
        mock_nssm_path.return_value = "C:\\nssm.exe"
        mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")

        from host.core.autostart_manager import start_service
        result = start_service()
        self.assertTrue(result)

    @patch("host.core.autostart_manager._get_nssm_path")
    @patch("host.core.autostart_manager.subprocess.run")
    def test_stop_service(self, mock_run, mock_nssm_path):
        mock_nssm_path.return_value = "C:\\nssm.exe"
        mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")

        from host.core.autostart_manager import stop_service
        result = stop_service()
        self.assertTrue(result)

    @patch("host.core.autostart_manager._get_nssm_path")
    def test_is_nssm_available_true(self, mock_nssm_path):
        mock_nssm_path.return_value = "C:\\nssm.exe"
        from host.core.autostart_manager import is_nssm_available
        self.assertTrue(is_nssm_available())

    @patch("host.core.autostart_manager._get_nssm_path")
    def test_is_nssm_available_false(self, mock_nssm_path):
        mock_nssm_path.return_value = None
        from host.core.autostart_manager import is_nssm_available
        self.assertFalse(is_nssm_available())


class TestAutostartManagerClient(unittest.TestCase):

    @patch("client.core.autostart_manager.subprocess.run")
    def test_register_startup_success(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")

        from client.core.autostart_manager import register_startup
        result = register_startup("C:\\USBRelay\\USBRelayClient.exe")
        self.assertTrue(result)

    @patch("client.core.autostart_manager.subprocess.run")
    def test_register_startup_failure(self, mock_run):
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="access denied")

        from client.core.autostart_manager import register_startup
        result = register_startup("C:\\USBRelay\\USBRelayClient.exe")
        self.assertFalse(result)

    @patch("client.core.autostart_manager.subprocess.run")
    def test_unregister_startup_success(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")

        from client.core.autostart_manager import unregister_startup
        result = unregister_startup()
        self.assertTrue(result)

    @patch("client.core.autostart_manager.subprocess.run")
    def test_is_registered_true(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")

        from client.core.autostart_manager import is_registered
        result = is_registered()
        self.assertTrue(result)

    @patch("client.core.autostart_manager.subprocess.run")
    def test_is_registered_false(self, mock_run):
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="not found")

        from client.core.autostart_manager import is_registered
        result = is_registered()
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
