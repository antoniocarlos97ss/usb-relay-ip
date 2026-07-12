import sys
import types
import unittest
import threading
import time
from unittest.mock import Mock, patch


qtcore = types.ModuleType("PyQt6.QtCore")
qtcore.QThread = type(
    "QThread",
    (),
    {
        "__init__": lambda self, *args: None,
        "wait": lambda self, *args: True,
    },
)
qtcore.pyqtSignal = lambda *args: Mock()
pyqt6 = types.ModuleType("PyQt6")
pyqt6.QtCore = qtcore
sys.modules.setdefault("PyQt6", pyqt6)
sys.modules.setdefault("PyQt6.QtCore", qtcore)

from client.core import operation_coordinator
from client.core.pnp_recovery import PnpRecoveryMonitor, _matching_attached, _wait_pnp_healthy, recover_device
from client.core.windows_pnp import PnpDeviceStatus
from shared.models import AttachedDevice, CommandResult, UsbDevice


class PnpRecoveryTests(unittest.TestCase):
    def setUp(self):
        operation_coordinator._active.clear()
        self.device = UsbDevice(
            busid="1-2",
            vid="1234",
            pid="abcd",
            description="Token",
            state="Attached",
        )

    @patch("client.core.pnp_recovery.usbip_wrapper.list_attached")
    def test_exact_busid_disambiguates_identical_devices(self, list_attached):
        expected = AttachedDevice(port=2, busid="1-2", vid="1234", pid="abcd")
        list_attached.return_value = [
            AttachedDevice(port=1, busid="1-1", vid="1234", pid="abcd"),
            expected,
        ]

        self.assertEqual(expected, _matching_attached(self.device))

    @patch("client.core.pnp_recovery._wait_pnp_healthy", return_value=True)
    @patch("client.core.pnp_recovery._wait_host_shared", return_value=True)
    @patch("client.core.pnp_recovery.usbip_wrapper.attach_device")
    @patch("client.core.pnp_recovery.usbip_wrapper.detach_device")
    @patch("client.core.pnp_recovery._matching_attached")
    def test_recovery_is_client_only(self, matching, detach, attach, wait_shared, wait_healthy):
        matching.return_value = AttachedDevice(port=3, busid="1-2", vid="1234", pid="abcd")
        detach.return_value = CommandResult(success=True, message="detached")
        attach.return_value = CommandResult(success=True, message="attached")
        api = Mock(host_ip="10.0.0.1")

        success, _ = recover_device(api, self.device)

        self.assertTrue(success)
        detach.assert_called_once_with(3, timeout=5)
        attach.assert_called_once_with("10.0.0.1", "1-2", timeout=8, vid="1234", pid="abcd")
        api.bind_device.assert_not_called()
        api.unbind_device.assert_not_called()

    @patch("client.core.pnp_recovery._matching_attached", return_value=None)
    def test_ambiguous_session_is_not_recovered(self, matching):
        success, message = recover_device(Mock(), self.device)

        self.assertFalse(success)
        self.assertIn("unambiguously", message)

    @patch("client.core.pnp_recovery.time.sleep")
    @patch("client.core.pnp_recovery._wait_pnp_healthy", side_effect=[False, True])
    @patch("client.core.pnp_recovery._wait_host_shared", return_value=True)
    @patch("client.core.pnp_recovery.usbip_wrapper.attach_device")
    @patch("client.core.pnp_recovery.usbip_wrapper.detach_device")
    @patch("client.core.pnp_recovery._matching_attached")
    def test_recovery_retries_once_after_failed_validation(
        self, matching, detach, attach, wait_shared, wait_healthy, sleep
    ):
        matching.return_value = AttachedDevice(port=3, busid="1-2", vid="1234", pid="abcd")
        detach.return_value = CommandResult(success=True, message="detached")
        attach.return_value = CommandResult(success=True, message="attached")

        success, _ = recover_device(Mock(host_ip="10.0.0.1"), self.device)

        self.assertTrue(success)
        self.assertEqual(2, detach.call_count)
        self.assertEqual(2, attach.call_count)

    @patch("client.core.pnp_recovery.time.sleep")
    @patch("client.core.pnp_recovery.time.monotonic", side_effect=[0.0, 0.1, 1.0])
    @patch("client.core.pnp_recovery.usbip_wrapper.list_attached")
    @patch("client.core.pnp_recovery.windows_pnp.get_correlated_statuses")
    @patch("client.core.pnp_recovery.windows_pnp.list_usb_devices")
    def test_validation_requires_healthy_correlated_instance(
        self, list_usb_devices, correlated, list_attached, monotonic, sleep
    ):
        list_attached.return_value = [AttachedDevice(port=3, busid="1-2", vid="1234", pid="abcd")]
        list_usb_devices.return_value = [
            PnpDeviceStatus(
                instance_id=r"USB\VID_1234&PID_ABCD\OTHER",
                name="Other token",
                problem_code=0,
                status="OK",
                vid="1234",
                pid="abcd",
            )
        ]
        correlated.return_value = []

        self.assertFalse(_wait_pnp_healthy(self.device, 0.5))

    @patch("client.core.pnp_recovery.recover_device", return_value=(True, "ok"))
    @patch("client.core.pnp_recovery.usbip_wrapper.list_attached")
    @patch("client.core.pnp_recovery.windows_pnp.find_session_code43")
    @patch("client.core.pnp_recovery.windows_pnp.get_busid_for_instance_id", return_value="1-2")
    @patch("client.core.pnp_recovery.windows_pnp.find_unknown_code43")
    @patch("client.core.pnp_recovery.windows_pnp.list_usb_devices")
    def test_monitor_recovers_mapped_unknown_code43(
        self,
        list_usb_devices,
        find_unknown,
        get_busid,
        find_session_code43,
        list_attached,
        recover,
    ):
        status = PnpDeviceStatus(instance_id=r"USB\UNKNOWN\1", name="", problem_code=43, status="Error")
        list_usb_devices.return_value = [status]
        find_unknown.return_value = [status]
        find_session_code43.return_value = [status]
        list_attached.return_value = [AttachedDevice(port=2, busid="1-2", vid="1234", pid="abcd")]

        monitor = PnpRecoveryMonitor(Mock(host_ip="10.0.0.1", host_port=5757, api_key=""))
        monitor._running = True
        monitor._devices = [self.device]
        monitor.recovery_succeeded = Mock()

        monitor._check_once()

        recover.assert_not_called()
        completed = threading.Event()
        recover.side_effect = lambda *args: (completed.set() or True, "ok")
        monitor._check_once()
        self.assertTrue(completed.wait(1))
        recover.assert_called_once()
        monitor.recovery_succeeded.emit.assert_called_once_with("1-2", "ok")

    @patch("client.core.pnp_recovery.usbip_wrapper.kill_all_subprocesses")
    @patch("client.core.pnp_recovery.windows_pnp.kill_all_queries")
    def test_monitor_stop_is_bounded(self, kill_queries, kill_usbip):
        monitor = PnpRecoveryMonitor(Mock(host_ip="10.0.0.1", host_port=5757, api_key=""))

        started = time.monotonic()
        monitor.stop()

        self.assertLess(time.monotonic() - started, 5)
        kill_queries.assert_called_once()
        kill_usbip.assert_called_once()

    @patch("client.core.pnp_recovery.recover_device", side_effect=RuntimeError("boom"))
    def test_recovery_exception_clears_active_worker(self, recover):
        monitor = PnpRecoveryMonitor(Mock(host_ip="10.0.0.1", host_port=5757, api_key=""))
        monitor._running = True
        monitor._recovery_threads[self.device.busid] = threading.current_thread()
        monitor.recovery_failed = Mock()

        monitor._recover_in_background(self.device)

        self.assertNotIn(self.device.busid, monitor._recovery_threads)
        monitor.recovery_failed.emit.assert_called_once()

    @patch("client.core.pnp_recovery.recover_device")
    @patch("client.core.pnp_recovery.usbip_wrapper.list_attached")
    @patch("client.core.pnp_recovery.windows_pnp.find_session_code43", return_value=[Mock()])
    @patch("client.core.pnp_recovery.windows_pnp.find_unknown_code43", return_value=[])
    @patch("client.core.pnp_recovery.windows_pnp.list_usb_devices", return_value=[Mock()])
    def test_monitor_recovers_multiple_devices_in_parallel(
        self, list_usb, find_unknown, find_session, list_attached, recover
    ):
        second = self.device.model_copy(update={"busid": "1-3", "vid": "9999", "pid": "0001"})
        list_attached.return_value = [
            AttachedDevice(port=2, busid="1-2", vid="1234", pid="abcd"),
            AttachedDevice(port=3, busid="1-3", vid="9999", pid="0001"),
        ]
        release = threading.Event()
        both_started = threading.Event()
        calls = []

        def blocking_recovery(api, device, cancel_event):
            calls.append(device.busid)
            if len(calls) == 2:
                both_started.set()
            release.wait(1)
            return True, "ok"

        recover.side_effect = blocking_recovery
        monitor = PnpRecoveryMonitor(Mock(host_ip="10.0.0.1", host_port=5757, api_key=""))
        monitor._devices = [self.device, second]

        monitor._check_once()
        monitor._check_once()

        self.assertTrue(both_started.wait(1))
        release.set()
        self.assertEqual({"1-2", "1-3"}, set(calls))


if __name__ == "__main__":
    unittest.main()
