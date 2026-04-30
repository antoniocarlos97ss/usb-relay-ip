import unittest
from unittest.mock import Mock, patch

from shared.models import UsbDevice


class TestDeviceMonitor(unittest.TestCase):

    def _make_device(self, busid, vid, pid, desc, state="Not shared", permanent=False):
        return UsbDevice(
            busid=busid,
            vid=vid,
            pid=pid,
            description=desc,
            state=state,
            is_permanent=permanent,
        )

    @patch("host.core.device_monitor.QThread", Mock)
    @patch("host.core.device_monitor.pyqtSignal", Mock)
    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_detect_device_list_change(self, mock_usbipd, mock_config):
        mock_config.is_permanent.return_value = False
        mock_config.load_config.return_value = Mock(permanent_devices=[])

        from host.core.device_monitor import DeviceMonitor

        monitor = DeviceMonitor(poll_interval=5)
        monitor._running = False

        monitor._previous_devices = []

        current = [self._make_device("1-5", "046d", "c31c", "Keyboard")]
        self.assertTrue(monitor._device_list_changed(current))

    @patch("host.core.device_monitor.QThread", Mock)
    @patch("host.core.device_monitor.pyqtSignal", Mock)
    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_no_change_when_same_devices(self, mock_usbipd, mock_config):
        mock_config.is_permanent.return_value = False
        mock_config.load_config.return_value = Mock(permanent_devices=[])

        from host.core.device_monitor import DeviceMonitor

        monitor = DeviceMonitor(poll_interval=5)
        monitor._running = False

        dev = self._make_device("1-5", "046d", "c31c", "Keyboard")
        monitor._previous_devices = [dev]

        current = [dev]
        self.assertFalse(monitor._device_list_changed(current))

    @patch("host.core.device_monitor.QThread", Mock)
    @patch("host.core.device_monitor.pyqtSignal", Mock)
    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_handle_new_device_auto_binds_permanent(self, mock_usbipd, mock_config):
        mock_usbipd.bind_device.return_value = Mock(success=True, message="")
        mock_config.is_permanent.return_value = True

        from host.core.device_monitor import DeviceMonitor

        monitor = DeviceMonitor(poll_interval=5)
        monitor._running = False
        monitor._previous_devices = []

        new_device = self._make_device("1-5", "046d", "c31c", "Keyboard", permanent=True)
        monitor._handle_new_devices([new_device])

        mock_usbipd.bind_device.assert_called_once_with("1-5")

    @patch("host.core.device_monitor.QThread", Mock)
    @patch("host.core.device_monitor.pyqtSignal", Mock)
    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_handle_new_device_skips_already_shared(self, mock_usbipd, mock_config):
        mock_config.is_permanent.return_value = True

        from host.core.device_monitor import DeviceMonitor

        monitor = DeviceMonitor(poll_interval=5)
        monitor._running = False
        monitor._previous_devices = []

        shared_device = self._make_device("1-5", "046d", "c31c", "Keyboard", state="Shared", permanent=True)
        monitor._handle_new_devices([shared_device])

        mock_usbipd.bind_device.assert_not_called()

    @patch("host.core.device_monitor.QThread", Mock)
    @patch("host.core.device_monitor.pyqtSignal", Mock)
    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_auto_bind_on_startup_binds_unshared(self, mock_usbipd, mock_config):
        from shared.models import PermanentDevice

        perm_dev = PermanentDevice(vid="046d", pid="c31c", description="Keyboard")
        mock_config.load_config.return_value = Mock(permanent_devices=[perm_dev])
        mock_usbipd.list_devices.return_value = [
            self._make_device("1-5", "046d", "c31c", "Keyboard", state="Not shared")
        ]
        mock_usbipd.bind_device.return_value = Mock(success=True, message="")
        mock_config.save_config = Mock()

        from host.core.device_monitor import DeviceMonitor

        monitor = DeviceMonitor(poll_interval=5)
        monitor._running = False
        monitor._auto_bind_permanent_on_startup()

        mock_usbipd.bind_device.assert_called_once_with("1-5")

    @patch("host.core.device_monitor.QThread", Mock)
    @patch("host.core.device_monitor.pyqtSignal", Mock)
    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_auto_bind_on_startup_skips_already_shared(self, mock_usbipd, mock_config):
        from shared.models import PermanentDevice

        perm_dev = PermanentDevice(vid="046d", pid="c31c", description="Keyboard")
        mock_config.load_config.return_value = Mock(permanent_devices=[perm_dev])
        mock_usbipd.list_devices.return_value = [
            self._make_device("1-5", "046d", "c31c", "Keyboard", state="Shared")
        ]

        from host.core.device_monitor import DeviceMonitor

        monitor = DeviceMonitor(poll_interval=5)
        monitor._running = False
        monitor._auto_bind_permanent_on_startup()

        mock_usbipd.bind_device.assert_not_called()

    @patch("host.core.device_monitor.QThread", Mock)
    @patch("host.core.device_monitor.pyqtSignal", Mock)
    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_auto_bind_on_startup_no_permanent_devices(self, mock_usbipd, mock_config):
        mock_config.load_config.return_value = Mock(permanent_devices=[])

        from host.core.device_monitor import DeviceMonitor

        monitor = DeviceMonitor(poll_interval=5)
        monitor._running = False
        monitor._auto_bind_permanent_on_startup()

        mock_usbipd.list_devices.assert_not_called()
        mock_usbipd.bind_device.assert_not_called()

    @patch("host.core.device_monitor.QThread", Mock)
    @patch("host.core.device_monitor.pyqtSignal", Mock)
    @patch("host.core.device_monitor.config_manager")
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_mark_permanent_status(self, mock_usbipd, mock_config):
        mock_config.is_permanent.side_effect = lambda vid, pid: vid == "046d"

        from host.core.device_monitor import DeviceMonitor

        monitor = DeviceMonitor(poll_interval=5)
        monitor._running = False

        devices = [
            self._make_device("1-5", "046d", "c31c", "Keyboard"),
            self._make_device("2-1", "0951", "1666", "USB Drive"),
        ]
        monitor._mark_permanent_status(devices)

        self.assertTrue(devices[0].is_permanent)
        self.assertFalse(devices[1].is_permanent)


if __name__ == "__main__":
    unittest.main()
