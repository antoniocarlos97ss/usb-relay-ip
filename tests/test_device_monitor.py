"""Tests for DeviceMonitor auto-share behavior.

Mocks PyQt6.QtCore before importing the device_monitor module so tests run
without a real Qt installation.
"""
import sys
import types
import unittest
from unittest.mock import Mock, patch

# Create a fake PyQt6.QtCore module BEFORE any import of device_monitor
_fake_qtcore = types.ModuleType("PyQt6.QtCore")
_fake_qtcore.QThread = type("QThread", (object,), {"__init__": lambda self, *a, **kw: None, "wait": lambda self, *a: None})
_fake_qtcore.pyqtSignal = lambda *a: Mock()
sys.modules.setdefault("PyQt6", types.ModuleType("PyQt6"))
sys.modules["PyQt6.QtCore"] = _fake_qtcore

from host.core.device_monitor import DeviceMonitor  # noqa: E402
from shared.models import UsbDevice  # noqa: E402


def _make_device(busid="1-1", vid="1234", pid="5678", state="Not shared", desc="Test Device"):
    return UsbDevice(busid=busid, vid=vid, pid=pid, description=desc, state=state)


class TestAutoShare(unittest.TestCase):
    def _make_monitor(self):
        m = DeviceMonitor.__new__(DeviceMonitor)
        m._poll_interval = 5
        m._running = False
        m._previous_devices = []
        m._failed_auto_share_busids = set()
        m.devices_changed = Mock()
        m.device_bound = Mock()
        m.device_unplugged = Mock()
        m.device_auto_bound = Mock()
        m.device_auto_shared = Mock()
        return m

    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_auto_share_all_startup_binds_all(self, mock_usb, mock_cfg):
        mock_cfg.load_config.return_value = Mock(auto_share_all=True, auto_share_exclude=[])
        mock_cfg.is_permanent.return_value = False
        mock_cfg.is_auto_share_excluded.return_value = False
        mock_usb.list_devices.return_value = [
            _make_device("1-1", state="Not shared"),
            _make_device("1-2", state="Not shared"),
            _make_device("1-3", state="Not shared"),
        ]
        mock_usb.bind_device.return_value = Mock(success=True, message="ok")

        m = self._make_monitor()
        m._auto_share_all_on_startup()

        self.assertEqual(mock_usb.bind_device.call_count, 3)
        self.assertEqual(m.device_auto_shared.emit.call_count, 3)

    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_auto_share_excluded_device_skipped(self, mock_usb, mock_cfg):
        mock_cfg.load_config.return_value = Mock(auto_share_all=True, auto_share_exclude=["1234:5678"])
        mock_cfg.is_permanent.return_value = False
        mock_cfg.is_auto_share_excluded.side_effect = lambda v, p: f"{v}:{p}" == "1234:5678"
        mock_usb.list_devices.return_value = [
            _make_device("1-1", vid="1234", pid="5678", state="Not shared"),
            _make_device("1-2", vid="abcd", pid="ef01", state="Not shared"),
        ]
        mock_usb.bind_device.return_value = Mock(success=True, message="ok")

        m = self._make_monitor()
        m._auto_share_all_on_startup()

        self.assertEqual(mock_usb.bind_device.call_count, 1)
        mock_usb.bind_device.assert_called_with("1-2")

    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_auto_share_already_shared_skipped(self, mock_usb, mock_cfg):
        mock_cfg.load_config.return_value = Mock(auto_share_all=True, auto_share_exclude=[])
        mock_cfg.is_permanent.return_value = False
        mock_cfg.is_auto_share_excluded.return_value = False
        mock_usb.list_devices.return_value = [
            _make_device("1-1", state="Shared"),
            _make_device("1-2", state="Not shared"),
        ]
        mock_usb.bind_device.return_value = Mock(success=True, message="ok")

        m = self._make_monitor()
        m._auto_share_all_on_startup()

        self.assertEqual(mock_usb.bind_device.call_count, 1)
        mock_usb.bind_device.assert_called_with("1-2")

    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_auto_share_disabled_no_bind(self, mock_usb, mock_cfg):
        mock_cfg.load_config.return_value = Mock(auto_share_all=False, auto_share_exclude=[])
        mock_cfg.is_permanent.return_value = False

        m = self._make_monitor()
        m._handle_auto_share([_make_device("1-1", state="Not shared")])

        mock_usb.bind_device.assert_not_called()

    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_handle_auto_share_rebinds_on_poll(self, mock_usb, mock_cfg):
        mock_cfg.load_config.return_value = Mock(auto_share_all=True, auto_share_exclude=[])
        mock_cfg.is_permanent.return_value = False
        mock_cfg.is_auto_share_excluded.return_value = False
        mock_usb.bind_device.return_value = Mock(success=True, message="ok")

        m = self._make_monitor()
        devices = [_make_device("1-1", state="Not shared")]
        m._handle_auto_share(devices)

        self.assertEqual(mock_usb.bind_device.call_count, 1)
        m.device_auto_shared.emit.assert_called_once_with("1-1", "Test Device")

    @patch("host.core.device_monitor.config_manager")
    def test_device_list_changed_detects_state_change(self, mock_cfg):
        mock_cfg.load_config.return_value = Mock(auto_share_all=False, auto_share_exclude=[])
        mock_cfg.is_permanent.return_value = False

        m = self._make_monitor()
        m._previous_devices = [_make_device("1-1", state="Shared")]
        result = m._device_list_changed([_make_device("1-1", state="Not shared")])
        self.assertTrue(result)

    @patch("host.core.device_monitor.config_manager")
    def test_device_list_changed_same_state_false(self, mock_cfg):
        mock_cfg.load_config.return_value = Mock(auto_share_all=False, auto_share_exclude=[])
        mock_cfg.is_permanent.return_value = False

        m = self._make_monitor()
        m._previous_devices = [_make_device("1-1", state="Shared")]
        result = m._device_list_changed([_make_device("1-1", state="Shared")])
        self.assertFalse(result)


class TestHeadlessSync(unittest.TestCase):
    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_headless_sync_binds_permanent_devices(self, mock_usb, mock_cfg):
        mock_cfg.load_config.return_value = Mock(auto_share_all=False, auto_share_exclude=[])
        mock_cfg.is_permanent.side_effect = lambda vid, pid: (vid, pid) == ("1234", "5678")
        mock_usb.list_devices.return_value = [_make_device("1-1", state="Not shared")]
        mock_usb.bind_device.return_value = Mock(success=True, message="ok")

        from host.core.device_monitor import sync_headless_devices_once

        failed = sync_headless_devices_once(set())

        self.assertEqual(failed, set())
        mock_usb.bind_device.assert_called_once_with("1-1")

    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_headless_sync_respects_exclusions(self, mock_usb, mock_cfg):
        mock_cfg.load_config.return_value = Mock(auto_share_all=True, auto_share_exclude=["1234:5678"])
        mock_cfg.is_permanent.return_value = False
        mock_usb.list_devices.return_value = [
            _make_device("1-1", vid="1234", pid="5678", state="Not shared"),
            _make_device("1-2", vid="abcd", pid="ef01", state="Not shared"),
        ]
        mock_usb.bind_device.return_value = Mock(success=True, message="ok")

        from host.core.device_monitor import sync_headless_devices_once

        failed = sync_headless_devices_once(set())

        self.assertEqual(failed, set())
        mock_usb.bind_device.assert_called_once_with("1-2")
