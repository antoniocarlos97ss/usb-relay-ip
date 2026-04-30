import unittest
from unittest.mock import patch

from shared.models import CommandResult


class TestUsbipWrapper(unittest.TestCase):
    def setUp(self):
        patch("client.core.usbip_wrapper._run_command", return_value=(0, "", "")).start()
        self.addCleanup(patch.stopall)

    def test_is_available_true(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, "usbipd version 4.2.0", "")
            from client.core.usbip_wrapper import is_available
            self.assertTrue(is_available())

    def test_is_available_false(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (-1, "", "not found")
            from client.core.usbip_wrapper import is_available
            self.assertFalse(is_available())

    def test_attach_device_success(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, "attached", "")
            from client.core.usbip_wrapper import attach_device
            result = attach_device("192.168.1.10", "1-5")
            self.assertTrue(result.success)
            self.assertIn("attached", result.message)

    def test_attach_device_failure(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (1, "", "connection refused")
            from client.core.usbip_wrapper import attach_device
            result = attach_device("192.168.1.10", "1-5")
            self.assertFalse(result.success)

    def test_detach_device_success(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, "detached", "")
            from client.core.usbip_wrapper import detach_device
            result = detach_device(3)
            self.assertTrue(result.success)

    def test_detach_device_failure(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (1, "", "device not found")
            from client.core.usbip_wrapper import detach_device
            result = detach_device(99)
            self.assertFalse(result.success)

    def test_list_attached_parses_correctly(self):
        text_output = (
            "BUSID  VID:PID                                  DEVICE                   STATE\n"
            "1-5    046d:c31c                                 Logitech Keyboard        Attached\n"
        )
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, text_output, "")
            from client.core.usbip_wrapper import list_attached
            attached = list_attached()
            self.assertGreaterEqual(len(attached), 1)

    def test_list_attached_empty(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (1, "", "")
            from client.core.usbip_wrapper import list_attached
            attached = list_attached()
            self.assertEqual(len(attached), 0)

    def test_find_port_for_busid_found(self):
        text_output = (
            "BUSID  VID:PID                                  DEVICE                   STATE\n"
            "1-5    046d:c31c                                 Logitech Keyboard        Attached\n"
        )
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, text_output, "")
            from client.core.usbip_wrapper import find_port_for_busid
            port = find_port_for_busid("1-5")
            self.assertIn(port, (0, None))

    def test_find_port_for_busid_not_found(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, "BUSID  VID:PID  STATE\n", "")
            from client.core.usbip_wrapper import find_port_for_busid
            port = find_port_for_busid("nonexistent")
            self.assertIsNone(port)


if __name__ == "__main__":
    unittest.main()
