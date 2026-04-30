import json
import unittest
from unittest.mock import Mock, patch

from shared.models import CommandResult, UsbDevice


class TestUsbipdWrapper(unittest.TestCase):
    def setUp(self):
        patch("host.core.usbipd_wrapper._run_command", return_value=(0, "", "")).start()
        self.addCleanup(patch.stopall)

    def test_get_version_parses_correctly(self):
        with patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, "usbipd version 4.2.0", "")
            from host.core.usbipd_wrapper import get_version
            version = get_version()
            self.assertEqual(version, (4, 2))

    def test_get_version_missing(self):
        with patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.return_value = (-1, "", "not found")
            from host.core.usbipd_wrapper import get_version
            version = get_version()
            self.assertEqual(version, (0, 0))

    def test_is_available_true(self):
        with patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, "usbipd version 4.2.0", "")
            from host.core.usbipd_wrapper import is_available
            self.assertTrue(is_available())

    def test_is_available_false_if_version_too_low(self):
        with patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, "usbipd version 3.0.0", "")
            from host.core.usbipd_wrapper import is_available
            self.assertFalse(is_available())

    def test_is_available_false_if_not_found(self):
        with patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.return_value = (-1, "", "not found")
            from host.core.usbipd_wrapper import is_available
            self.assertFalse(is_available())

    def test_bind_device_success(self):
        with patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, "bound", "")
            from host.core.usbipd_wrapper import bind_device
            result = bind_device("1-5")
            self.assertTrue(result.success)
            self.assertIn("bound successfully", result.message)

    def test_bind_device_failure(self):
        with patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.return_value = (1, "", "access denied")
            from host.core.usbipd_wrapper import bind_device
            result = bind_device("1-5")
            self.assertFalse(result.success)
            self.assertIn("Failed", result.message)

    def test_unbind_device_success(self):
        with patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, "unbound", "")
            from host.core.usbipd_wrapper import unbind_device
            result = unbind_device("1-5")
            self.assertTrue(result.success)

    def test_get_device_state_found(self):
        mock_output = (
            "BUSID  VID:PID                                  DEVICE                   STATE\n"
            "1-5    046d:c31c                                 Logitech Keyboard        Shared\n"
            "2-1    0951:1666                                 Kingston DT              Not shared"
        )
        with patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, mock_output, "")
            from host.core.usbipd_wrapper import get_device_state
            self.assertEqual(get_device_state("1-5"), "Shared")
            self.assertEqual(get_device_state("2-1"), "Not shared")

    def test_get_device_state_not_found(self):
        mock_output = "BUSID  VID:PID                                  DEVICE                   STATE\n"
        with patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, mock_output, "")
            from host.core.usbipd_wrapper import get_device_state
            self.assertEqual(get_device_state("nonexistent"), "Not shared")

    def test_list_devices_json_format(self):
        json_output = json.dumps({
            "Devices": [
                {
                    "BusId": "1-5",
                    "VendorId": "VID_046D",
                    "ProductId": "PID_C31C",
                    "Description": "Logitech Keyboard",
                    "IsAttached": False,
                    "IsBound": True,
                },
                {
                    "BusId": "2-1",
                    "VendorId": "0951",
                    "ProductId": "1666",
                    "Description": "Kingston DT",
                    "IsAttached": False,
                    "IsBound": False,
                },
            ]
        })
        with patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, json_output, "")
            from host.core.usbipd_wrapper import _parse_list_json, list_devices
            devices = list_devices()
            self.assertEqual(len(devices), 2)
            self.assertEqual(devices[0].busid, "1-5")
            self.assertEqual(devices[0].vid, "046d")
            self.assertEqual(devices[0].pid, "c31c")
            self.assertEqual(devices[0].state, "Shared")
            self.assertEqual(devices[1].state, "Not shared")

    def test_list_devices_text_fallback(self):
        text_output = (
            "BUSID  VID:PID                                  DEVICE                   STATE\n"
            "1-5    046d:c31c                                 Logitech Keyboard        Shared\n"
            "2-1    0951:1666                                 Kingston DT              Not shared"
        )
        with patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.side_effect = [
                (1, "", ""),
                (0, text_output, ""),
            ]
            from host.core.usbipd_wrapper import list_devices
            devices = list_devices()
            self.assertEqual(len(devices), 2)
            self.assertEqual(devices[0].busid, "1-5")
            self.assertEqual(devices[0].vid, "046d")
            self.assertEqual(devices[0].pid, "c31c")
            self.assertEqual(devices[0].state, "Shared")
            self.assertEqual(devices[1].state, "Not shared")

    def test_list_devices_empty(self):
        with patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.side_effect = [
                (1, "", ""),
                (1, "", ""),
            ]
            from host.core.usbipd_wrapper import list_devices
            devices = list_devices()
            self.assertEqual(len(devices), 0)


if __name__ == "__main__":
    unittest.main()
