import sys
import types
import unittest
from unittest.mock import Mock, patch, MagicMock


def _make_mock_winreg():
    """Create a mock winreg module for Linux CI (winreg only exists on Windows)."""
    mock_winreg = types.ModuleType("winreg")
    mock_winreg.HKEY_CURRENT_USER = 0x80000001
    mock_winreg.KEY_SET_VALUE = 0x0002
    mock_winreg.KEY_QUERY_VALUE = 0x0001
    mock_winreg.REG_SZ = 1
    mock_winreg.OpenKey = Mock()
    mock_winreg.SetValueEx = Mock()
    mock_winreg.QueryValueEx = Mock(return_value=("mock_value", 1))
    mock_winreg.DeleteValue = Mock()
    mock_winreg.CloseKey = Mock()
    return mock_winreg


# Inject mock winreg into sys.modules BEFORE any module that imports winreg is loaded.
# This is needed for Linux CI where winreg doesn't exist.
if "winreg" not in sys.modules:
    sys.modules["winreg"] = _make_mock_winreg()


class TestAutostartManagerHost(unittest.TestCase):

    @patch("host.core.autostart_manager.register_boot_task", return_value=True)
    @patch("host.core.autostart_manager.register_logon_run", return_value=True)
    def test_register_startup_success(self, mock_logon, mock_boot):
        from host.core.autostart_manager import register_startup
        logon_ok, boot_ok = register_startup("C:\\USBRelay\\USBRelayHost.exe")
        self.assertTrue(logon_ok)
        self.assertTrue(boot_ok)

    @patch("host.core.autostart_manager.register_boot_task", return_value=False)
    @patch("host.core.autostart_manager.register_logon_run", return_value=False)
    def test_register_startup_failure(self, mock_logon, mock_boot):
        from host.core.autostart_manager import register_startup
        logon_ok, boot_ok = register_startup("C:\\USBRelay\\USBRelayHost.exe")
        self.assertFalse(logon_ok)
        self.assertFalse(boot_ok)

    @patch("host.core.autostart_manager.unregister_boot_task", return_value=True)
    @patch("host.core.autostart_manager.unregister_logon_run", return_value=True)
    def test_unregister_startup_success(self, mock_logon, mock_boot):
        from host.core.autostart_manager import unregister_startup
        logon_ok, boot_ok = unregister_startup()
        self.assertTrue(logon_ok)
        self.assertTrue(boot_ok)

    def test_is_registered_true(self):
        mock_winreg = sys.modules["winreg"]
        mock_winreg.OpenKey.side_effect = None
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value = mock_key
        mock_winreg.QueryValueEx.return_value = ("USBRelayHost", 1)

        from host.core.autostart_manager import is_registered
        result = is_registered()
        self.assertTrue(result)

    def test_is_registered_false(self):
        mock_winreg = sys.modules["winreg"]
        mock_winreg.OpenKey.side_effect = FileNotFoundError("key not found")
        mock_winreg.OpenKey.return_value = None

        from host.core.autostart_manager import is_registered
        result = is_registered()
        self.assertFalse(result)


class TestAutostartManagerClient(unittest.TestCase):

    def _patch_winreg(self):
        """Patch winreg for Linux CI compatibility."""
        mock_winreg = _make_mock_winreg()
        patcher = patch.dict(sys.modules, {"winreg": mock_winreg})
        patcher.start()
        self.addCleanup(patcher.stop)
        return mock_winreg

    @patch("client.core.autostart_manager.register_boot_task", return_value=True)
    @patch("client.core.autostart_manager.register_logon_run", return_value=True)
    def test_register_startup_success(self, mock_logon, mock_boot):
        from client.core.autostart_manager import register_startup
        logon_ok, boot_ok = register_startup("C:\\USBRelay\\USBRelayClient.exe")
        self.assertTrue(logon_ok)
        self.assertTrue(boot_ok)

    @patch("client.core.autostart_manager.register_boot_task", return_value=False)
    @patch("client.core.autostart_manager.register_logon_run", return_value=False)
    def test_register_startup_failure(self, mock_logon, mock_boot):
        from client.core.autostart_manager import register_startup
        logon_ok, boot_ok = register_startup("C:\\USBRelay\\USBRelayClient.exe")
        self.assertFalse(logon_ok)
        self.assertFalse(boot_ok)

    @patch("client.core.autostart_manager.unregister_boot_task", return_value=True)
    @patch("client.core.autostart_manager.unregister_logon_run", return_value=True)
    def test_unregister_startup_success(self, mock_logon, mock_boot):
        from client.core.autostart_manager import unregister_startup
        logon_ok, boot_ok = unregister_startup()
        self.assertTrue(logon_ok)
        self.assertTrue(boot_ok)

    def test_is_registered_true(self):
        mock_winreg = self._patch_winreg()
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value = mock_key
        mock_winreg.QueryValueEx.return_value = ("USBRelayClient", 1)

        from client.core.autostart_manager import is_registered
        result = is_registered()
        self.assertTrue(result)

    def test_is_registered_false(self):
        mock_winreg = self._patch_winreg()
        mock_winreg.OpenKey.side_effect = FileNotFoundError("key not found")

        from client.core.autostart_manager import is_registered
        result = is_registered()
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
