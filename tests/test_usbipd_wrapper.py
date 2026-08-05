import json
import unittest
from unittest.mock import Mock, patch, MagicMock

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
        with patch("host.core.usbipd_wrapper._find_usbipd", return_value=r"C:\usbipd.exe"), \
             patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, "usbipd version 4.2.0", "")
            from host.core.usbipd_wrapper import is_available
            self.assertTrue(is_available())

    def test_is_available_false_if_version_too_low(self):
        with patch("host.core.usbipd_wrapper._find_usbipd", return_value=r"C:\usbipd.exe"), \
             patch("host.core.usbipd_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, "usbipd version 3.0.0", "")
            from host.core.usbipd_wrapper import is_available
            self.assertFalse(is_available())

    def test_is_available_false_if_not_found(self):
        with patch("host.core.usbipd_wrapper._find_usbipd", return_value=None), \
             patch("host.core.usbipd_wrapper._run_command") as mock_run:
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


class TestGetServiceState(unittest.TestCase):
    """Tests for get_service_state() parsing of sc query output."""

    @patch("host.core.usbipd_wrapper.check_port_listening", return_value=False)
    @patch("host.core.usbipd_wrapper.subprocess.run")
    def test_running_state(self, mock_run, mock_port):
        mock_run.return_value = Mock(
            stdout="SERVICE_NAME: usbipd\n"
                   "        TYPE               : 10  WIN32_OWN_POINT\n"
                   "        STATE              : 4  RUNNING\n"
                   "                              (STOPPABLE, ACCEPTS_SHUTDOWN)\n",
                    stderr="",
        )
        from host.core.usbipd_wrapper import get_service_state
        self.assertEqual(get_service_state(), "RUNNING")

    @patch("host.core.usbipd_wrapper.check_port_listening", return_value=False)
    @patch("host.core.usbipd_wrapper.subprocess.run")
    def test_stopped_state(self, mock_run, mock_port):
        mock_run.return_value = Mock(
            stdout="SERVICE_NAME: usbipd\n"
                   "        STATE              : 1  STOPPED\n",
            stderr="",
        )
        from host.core.usbipd_wrapper import get_service_state
        self.assertEqual(get_service_state(), "STOPPED")

    @patch("host.core.usbipd_wrapper.check_port_listening", return_value=False)
    @patch("host.core.usbipd_wrapper.subprocess.run")
    def test_stop_pending_state(self, mock_run, mock_port):
        mock_run.return_value = Mock(
            stdout="SERVICE_NAME: usbipd\n"
                   "        STATE              : 3  STOP_PENDING\n",
            stderr="",
        )
        from host.core.usbipd_wrapper import get_service_state
        self.assertEqual(get_service_state(), "STOP_PENDING")

    @patch("host.core.usbipd_wrapper.check_port_listening", return_value=False)
    @patch("host.core.usbipd_wrapper.subprocess.run")
    def test_start_pending_state(self, mock_run, mock_port):
        mock_run.return_value = Mock(
            stdout="SERVICE_NAME: usbipd\n"
                   "        STATE              : 2  START_PENDING\n",
            stderr="",
        )
        from host.core.usbipd_wrapper import get_service_state
        self.assertEqual(get_service_state(), "START_PENDING")

    @patch("host.core.usbipd_wrapper.check_port_listening", return_value=False)
    @patch("host.core.usbipd_wrapper.subprocess.run")
    def test_not_installed(self, mock_run, mock_port):
        mock_run.return_value = Mock(
            stdout="",
            stderr="[SC] EnumQueryServicesStatus:OpenService FAILED 1060:\n"
                   "The specified service does not exist as an installed service.\n",
        )
        from host.core.usbipd_wrapper import get_service_state
        self.assertEqual(get_service_state(), "NOT_INSTALLED")

    @patch("host.core.usbipd_wrapper.check_port_listening", return_value=False)
    @patch("host.core.usbipd_wrapper.subprocess.run")
    def test_unknown_exception(self, mock_run, mock_port):
        mock_run.side_effect = Exception("subprocess failed")
        from host.core.usbipd_wrapper import get_service_state
        self.assertEqual(get_service_state(), "UNKNOWN")

    @patch("host.core.usbipd_wrapper.check_port_listening", return_value=False)
    @patch("host.core.usbipd_wrapper.subprocess.run")
    def test_no_state_line(self, mock_run, mock_port):
        mock_run.return_value = Mock(
            stdout="SERVICE_NAME: usbipd\n"
                   "        TYPE               : 10  WIN32_OWN_POINT\n",
            stderr="",
        )
        from host.core.usbipd_wrapper import get_service_state
        self.assertEqual(get_service_state(), "UNKNOWN")


class TestEnsureServiceRunning(unittest.TestCase):
    """Tests for the refactored ensure_service_running()."""

    _STOPPED_RESPONSE = Mock(
        stdout="SERVICE_NAME: usbipd\n"
               "        STATE              : 1  STOPPED\n",
        stderr="",
    )
    _RUNNING_RESPONSE = Mock(
        stdout="SERVICE_NAME: usbipd\n"
               "        STATE              : 4  RUNNING\n",
        stderr="",
    )

    @patch("host.core.usbipd_wrapper.check_port_listening")
    def test_already_listening(self, mock_port):
        mock_port.return_value = True
        from host.core.usbipd_wrapper import ensure_service_running
        ok, msg = ensure_service_running()
        self.assertTrue(ok)
        self.assertIn("listening", msg.lower())

    @patch("host.core.usbipd_wrapper.time")
    @patch("host.core.usbipd_wrapper.subprocess.run")
    @patch("host.core.usbipd_wrapper.check_port_listening")
    @patch("host.core.usbipd_wrapper._wait_for_port")
    def test_service_stopped_starts_ok(self, mock_wait, mock_port, mock_sc, mock_time):
        mock_time.sleep = Mock()
        mock_port.side_effect = [False, False]
        mock_wait.return_value = True
        sc_start_result = Mock(stdout="", stderr="", returncode=0)
        # sc calls: get_service_state (STOPPED), sc start
        mock_sc.side_effect = [self._STOPPED_RESPONSE, sc_start_result]

        from host.core.usbipd_wrapper import ensure_service_running
        ok, msg = ensure_service_running()
        self.assertTrue(ok)
        self.assertIn("listening", msg.lower())

    @patch("host.core.usbipd_wrapper.time")
    @patch("host.core.usbipd_wrapper.subprocess.run")
    @patch("host.core.usbipd_wrapper.check_port_listening")
    @patch("host.core.usbipd_wrapper._wait_for_port")
    def test_running_service_restarts_when_port_missing(self, mock_wait, mock_port, mock_sc, mock_time):
        mock_time.sleep = Mock()
        mock_port.side_effect = [False, False]
        mock_wait.return_value = True
        mock_sc.side_effect = [
            self._RUNNING_RESPONSE,
            Mock(stdout="", stderr="", returncode=0),
            Mock(stdout="", stderr="", returncode=0),
        ]

        from host.core.usbipd_wrapper import ensure_service_running
        ok, msg = ensure_service_running()
        self.assertTrue(ok)
        self.assertIn("restarted", msg.lower())

    @patch("host.core.usbipd_wrapper.time")
    @patch("host.core.usbipd_wrapper.subprocess.run")
    @patch("host.core.usbipd_wrapper.check_port_listening")
    def test_service_not_installed(self, mock_port, mock_sc, mock_time):
        mock_time.sleep = Mock()
        mock_port.return_value = False
        sc_query_result = Mock(
            stdout="",
            stderr="[SC] OpenService FAILED 1060",
        )
        mock_sc.side_effect = [sc_query_result]

        from host.core.usbipd_wrapper import ensure_service_running
        ok, msg = ensure_service_running()
        self.assertFalse(ok)
        self.assertIn("not installed", msg.lower())

    @patch("host.core.usbipd_wrapper.time")
    @patch("host.core.usbipd_wrapper.subprocess.run")
    @patch("host.core.usbipd_wrapper.check_port_listening")
    def test_start_fails_returns_false(self, mock_port, mock_sc, mock_time):
        mock_time.sleep = Mock()
        mock_port.return_value = False

        sc_start_result = Mock(stdout="", stderr="access denied", returncode=1)
        mock_sc.side_effect = [
            self._STOPPED_RESPONSE,
            sc_start_result,
        ]

        from host.core.usbipd_wrapper import ensure_service_running
        ok, msg = ensure_service_running()
        self.assertFalse(ok)
        self.assertIn("Could not start", msg)
