import sys
import types
import unittest
import threading
import time
from unittest.mock import Mock, call, patch


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
from client.core.pnp_recovery import (
    PnpRecoveryMonitor,
    _matching_attached,
    _wait_host_shared,
    _wait_pnp_healthy,
    recover_device,
)
from client.core.windows_pnp import PnpDeviceStatus
from shared.models import AttachedDevice, CommandResult, UsbDevice


def _bounded_clock(*values):
    iterator = iter(values)
    final = values[-1]
    return lambda: next(iterator, final)


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

    @patch("client.core.pnp_recovery.usbip_wrapper.query_attached_devices")
    def test_exact_busid_disambiguates_identical_devices(self, query):
        from client.core.usbip_wrapper import AttachedDevicesQuery

        expected = AttachedDevice(port=2, busid="1-2", vid="1234", pid="abcd")
        query.return_value = AttachedDevicesQuery(True, (
            AttachedDevice(port=1, busid="1-1", vid="1234", pid="abcd"),
            expected,
        ))

        self.assertEqual(expected, _matching_attached(self.device))

    @patch("client.core.pnp_recovery._wait_pnp_healthy", return_value=True)
    @patch("client.core.pnp_recovery._wait_host_shared", return_value=True)
    @patch("client.core.pnp_recovery._wait_host_unbound", return_value=True)
    @patch("client.core.pnp_recovery.usbip_wrapper.attach_device")
    @patch("client.core.pnp_recovery.usbip_wrapper.detach_busid")
    @patch("client.core.pnp_recovery._matching_attached")
    def test_recovery_cycles_host_binding_on_first_attempt(
        self, matching, detach, attach, wait_unbound, wait_shared, wait_healthy
    ):
        matching.return_value = AttachedDevice(port=3, busid="1-2", vid="1234", pid="abcd")
        detach.return_value = CommandResult(success=True, message="detached")
        attach.return_value = CommandResult(success=True, message="attached")
        api = Mock(host_ip="10.0.0.1")
        api.get_devices.return_value = [self.device]
        api.unbind_device.return_value = True
        api.bind_device.return_value = True

        success, _ = recover_device(api, self.device)

        self.assertTrue(success)
        detach.assert_called_once_with(
            "1-2",
            timeout=5,
            port_hint=3,
            expected_vid="1234",
            expected_pid="abcd",
        )
        api.unbind_device.assert_called_once_with("1-2")
        wait_unbound.assert_called_once()
        api.bind_device.assert_called_once_with("1-2")
        wait_shared.assert_called_once()
        attach.assert_called_once_with("10.0.0.1", "1-2", timeout=8, vid="1234", pid="abcd")

    @patch("client.core.pnp_recovery._wait_host_shared")
    @patch("client.core.pnp_recovery._wait_host_unbound", return_value=True)
    @patch("client.core.pnp_recovery.usbip_wrapper.attach_device")
    @patch("client.core.pnp_recovery._matching_attached", return_value=None)
    def test_cancellation_after_shared_wait_blocks_attach(
        self, matching, attach, wait_unbound, wait_shared
    ):
        cancelled = threading.Event()
        wait_shared.side_effect = lambda *args, **kwargs: (cancelled.set() or True)
        api = Mock(host_ip="10.0.0.1")
        api.get_devices.return_value = [self.device]
        api.unbind_device.return_value = True
        api.bind_device.return_value = True

        success, message = recover_device(
            api,
            self.device,
            cancel_event=cancelled,
            identity_confirmed=True,
        )

        self.assertFalse(success)
        self.assertIn("interrupted", message)
        attach.assert_not_called()

    @patch("client.core.pnp_recovery._wait_host_shared", return_value=True)
    @patch("client.core.pnp_recovery._wait_host_unbound")
    @patch("client.core.pnp_recovery._matching_attached", return_value=None)
    def test_cancel_after_unbind_rebinds_host_before_return(
        self, matching, wait_unbound, wait_shared
    ):
        cancelled = threading.Event()
        wait_unbound.side_effect = lambda *args, **kwargs: (cancelled.set() or False)
        api = Mock(host_ip="10.0.0.1")
        api.get_devices.return_value = [self.device]
        api.unbind_device.return_value = True
        api.bind_device.return_value = True

        success, message = recover_device(
            api,
            self.device,
            cancel_event=cancelled,
            identity_confirmed=True,
        )

        self.assertFalse(success)
        self.assertIn("interrupted", message)
        api.bind_device.assert_called_once_with("1-2")
        self.assertGreaterEqual(wait_shared.call_count, 1)

    @patch("client.core.pnp_recovery._wait_host_shared", return_value=True)
    @patch("client.core.pnp_recovery._wait_host_unbound", return_value=False)
    @patch("client.core.pnp_recovery._matching_attached", return_value=None)
    def test_unbound_timeout_rebinds_host_before_return(
        self, matching, wait_unbound, wait_shared
    ):
        api = Mock(host_ip="10.0.0.1")
        api.get_devices.return_value = [self.device]
        api.unbind_device.return_value = True
        api.bind_device.return_value = True

        success, message = recover_device(api, self.device, identity_confirmed=True)

        self.assertFalse(success)
        self.assertIn("Not shared", message)
        api.bind_device.assert_called_once_with("1-2")
        self.assertGreaterEqual(wait_shared.call_count, 1)

    @patch("client.core.pnp_recovery._wait_host_shared", return_value=True)
    @patch("client.core.pnp_recovery._wait_host_unbound", return_value=True)
    @patch("client.core.pnp_recovery._matching_attached", return_value=None)
    def test_bind_failure_attempts_bounded_compensatory_rebind(
        self, matching, wait_unbound, wait_shared
    ):
        api = Mock(host_ip="10.0.0.1")
        api.get_devices.return_value = [self.device]
        api.unbind_device.return_value = True
        api.bind_device.side_effect = [False, True]

        success, message = recover_device(api, self.device, identity_confirmed=True)

        self.assertFalse(success)
        self.assertIn("bind", message.lower())
        self.assertEqual([call("1-2"), call("1-2")], api.bind_device.call_args_list)
        self.assertGreaterEqual(wait_shared.call_count, 1)

    @patch("client.core.pnp_recovery._wait_host_shared", side_effect=[False, True])
    @patch("client.core.pnp_recovery._wait_host_unbound", return_value=True)
    @patch("client.core.pnp_recovery._matching_attached", return_value=None)
    def test_shared_timeout_attempts_bounded_compensatory_rebind(
        self, matching, wait_unbound, wait_shared
    ):
        api = Mock(host_ip="10.0.0.1")
        api.get_devices.return_value = [self.device]
        api.unbind_device.return_value = True
        api.bind_device.return_value = True

        success, message = recover_device(api, self.device, identity_confirmed=True)

        self.assertFalse(success)
        self.assertIn("Shared", message)
        self.assertEqual([call("1-2"), call("1-2")], api.bind_device.call_args_list)
        self.assertEqual(2, wait_shared.call_count)

    @patch("client.core.pnp_recovery._matching_attached", return_value=None)
    def test_ambiguous_session_is_not_recovered(self, matching):
        success, message = recover_device(Mock(), self.device)

        self.assertFalse(success)
        self.assertIn("unambiguously", message)

    @patch("client.core.pnp_recovery.usbip_wrapper.query_attached_devices")
    def test_unknown_local_enumeration_stops_before_host_mutation(self, query):
        from client.core.usbip_wrapper import AttachedDevicesQuery

        query.return_value = AttachedDevicesQuery(False, (), "driver error")
        api = Mock(host_ip="10.0.0.1")

        success, message = recover_device(api, self.device, identity_confirmed=True)

        self.assertFalse(success)
        self.assertIn("local USB/IP state is unknown", message)
        api.unbind_device.assert_not_called()
        api.bind_device.assert_not_called()

    @patch("client.core.pnp_recovery._wait_pnp_healthy", return_value=True)
    @patch("client.core.pnp_recovery._wait_host_shared", return_value=True)
    @patch("client.core.pnp_recovery._wait_host_unbound", return_value=True, create=True)
    @patch("client.core.pnp_recovery.usbip_wrapper.attach_device")
    @patch("client.core.pnp_recovery.usbip_wrapper.detach_busid")
    @patch("client.core.pnp_recovery._matching_attached", return_value=None)
    def test_confirmed_code43_without_local_port_cycles_host_binding(
        self,
        matching,
        detach,
        attach,
        wait_unbound,
        wait_shared,
        wait_healthy,
    ):
        attach.return_value = CommandResult(success=True, message="attached")
        api = Mock(host_ip="10.0.0.1")
        api.get_devices.return_value = [self.device]
        api.unbind_device.return_value = True
        api.bind_device.return_value = True

        success, _ = recover_device(api, self.device, identity_confirmed=True)

        self.assertTrue(success)
        detach.assert_not_called()
        api.unbind_device.assert_called_once_with("1-2")
        wait_unbound.assert_called_once()
        api.bind_device.assert_called_once_with("1-2")
        wait_shared.assert_called_once()
        attach.assert_called_once_with("10.0.0.1", "1-2", timeout=8, vid="1234", pid="abcd")

    @patch("client.core.pnp_recovery.time.sleep")
    @patch("client.core.pnp_recovery._wait_pnp_healthy", side_effect=[False, True])
    @patch("client.core.pnp_recovery._wait_host_shared", return_value=True)
    @patch("client.core.pnp_recovery._wait_host_unbound", return_value=True)
    @patch("client.core.pnp_recovery.usbip_wrapper.attach_device")
    @patch("client.core.pnp_recovery.usbip_wrapper.detach_busid")
    @patch("client.core.pnp_recovery._matching_attached")
    def test_recovery_retries_once_after_failed_validation(
        self, matching, detach, attach, wait_unbound, wait_shared, wait_healthy, sleep
    ):
        matching.return_value = AttachedDevice(port=3, busid="1-2", vid="1234", pid="abcd")
        detach.return_value = CommandResult(success=True, message="detached")
        attach.return_value = CommandResult(success=True, message="attached")

        api = Mock(host_ip="10.0.0.1")
        api.get_devices.return_value = [self.device]
        success, _ = recover_device(api, self.device)

        self.assertTrue(success)
        self.assertEqual(2, detach.call_count)
        self.assertEqual(2, attach.call_count)

    @patch("client.core.pnp_recovery.time.sleep")
    @patch(
        "client.core.pnp_recovery.time.monotonic",
        side_effect=_bounded_clock(0.0, 0.1, 0.2, 1.0),
    )
    @patch("client.core.pnp_recovery.usbip_wrapper.list_attached")
    @patch("client.core.pnp_recovery.windows_pnp.get_correlated_statuses")
    @patch("client.core.pnp_recovery.windows_pnp.list_usb_devices")
    def test_validation_stays_strict_while_correlated_instance_is_failing(
        self, list_usb_devices, correlated, list_attached, monotonic, sleep
    ):
        list_attached.return_value = [AttachedDevice(port=3, busid="1-2", vid="1234", pid="abcd")]
        broken = PnpDeviceStatus(
            instance_id=r"USB\VID_1234&PID_ABCD\TOKEN",
            name="Token",
            problem_code=43,
            status="Error",
            vid="1234",
            pid="abcd",
        )
        healthy_other = PnpDeviceStatus(
            instance_id=r"USB\VID_1234&PID_ABCD\OTHER",
            name="Other token",
            problem_code=0,
            status="OK",
            vid="1234",
            pid="abcd",
        )
        list_usb_devices.return_value = [broken, healthy_other]
        correlated.return_value = [broken]

        self.assertFalse(_wait_pnp_healthy(self.device, 0.5))

    @patch("client.core.pnp_recovery.time.sleep")
    @patch("client.core.pnp_recovery.usbip_wrapper.list_attached")
    @patch("client.core.pnp_recovery.windows_pnp.get_correlated_statuses", return_value=[])
    @patch("client.core.pnp_recovery.windows_pnp.list_usb_devices")
    def test_validation_falls_back_to_exact_vid_pid_without_correlation(
        self, list_usb_devices, correlated, list_attached, sleep
    ):
        list_attached.return_value = [AttachedDevice(port=3, busid="1-2", vid="1234", pid="abcd")]
        list_usb_devices.return_value = [
            PnpDeviceStatus(
                instance_id=r"USB\VID_1234&PID_ABCD\TOKEN",
                name="Token",
                problem_code=0,
                status="OK",
                vid="1234",
                pid="abcd",
            )
        ]

        self.assertTrue(_wait_pnp_healthy(self.device, time.monotonic() + 5))

    @patch("client.core.pnp_recovery.time.sleep")
    @patch(
        "client.core.pnp_recovery.time.monotonic",
        side_effect=_bounded_clock(0.0, 0.1, 0.2, 1.0),
    )
    @patch("client.core.pnp_recovery.usbip_wrapper.list_attached")
    @patch("client.core.pnp_recovery.windows_pnp.get_correlated_statuses", return_value=[])
    @patch("client.core.pnp_recovery.windows_pnp.list_usb_devices")
    def test_validation_fallback_rejects_failing_exact_identity(
        self, list_usb_devices, correlated, list_attached, monotonic, sleep
    ):
        list_attached.return_value = [AttachedDevice(port=3, busid="1-2", vid="1234", pid="abcd")]
        list_usb_devices.return_value = [
            PnpDeviceStatus(
                instance_id=r"USB\VID_1234&PID_ABCD\TOKEN",
                name="Token",
                problem_code=43,
                status="Error",
                vid="1234",
                pid="abcd",
            )
        ]

        self.assertFalse(_wait_pnp_healthy(self.device, 0.5))

    @patch("client.core.pnp_recovery.time.sleep")
    @patch(
        "client.core.pnp_recovery.time.monotonic",
        side_effect=_bounded_clock(0.0, 0.1, 0.2, 1.0),
    )
    @patch("client.core.pnp_recovery.usbip_wrapper.list_attached")
    @patch("client.core.pnp_recovery.windows_pnp.get_session_correlation")
    @patch("client.core.pnp_recovery.windows_pnp.get_correlated_statuses", return_value=[])
    @patch("client.core.pnp_recovery.windows_pnp.list_usb_devices")
    def test_validation_rejects_healthy_identical_sibling_when_target_correlation_exists(
        self, list_usb_devices, correlated, correlation, list_attached, monotonic, sleep
    ):
        correlation.return_value = Mock(instance_ids=(r"USB\VID_1234&PID_ABCD\TARGET",))
        list_attached.return_value = [
            AttachedDevice(port=3, busid="1-2", vid="1234", pid="abcd"),
            AttachedDevice(port=4, busid="1-3", vid="1234", pid="abcd"),
        ]
        list_usb_devices.return_value = [
            PnpDeviceStatus(
                instance_id=r"USB\VID_1234&PID_ABCD\SIBLING",
                name="Other token",
                problem_code=0,
                status="OK",
                vid="1234",
                pid="abcd",
            )
        ]

        self.assertFalse(_wait_pnp_healthy(self.device, 0.5))

    @patch("client.core.pnp_recovery.time.sleep")
    @patch(
        "client.core.pnp_recovery.time.monotonic",
        side_effect=_bounded_clock(0.0, 0.1, 0.2, 0.3, 1.0),
    )
    @patch("client.core.pnp_recovery.windows_pnp.get_session_correlation", return_value=None)
    @patch("client.core.pnp_recovery.windows_pnp.get_correlated_statuses", return_value=[])
    @patch("client.core.pnp_recovery.windows_pnp.list_usb_devices")
    @patch("client.core.pnp_recovery.usbip_wrapper.list_attached")
    def test_validation_refreshes_local_snapshot_before_accepting_healthy_sibling(
        self, list_attached, list_usb_devices, correlated, correlation, monotonic, sleep
    ):
        target = AttachedDevice(port=3, busid="1-2", vid="1234", pid="abcd")
        sibling = AttachedDevice(port=4, busid="1-3", vid="1234", pid="abcd")
        list_attached.side_effect = [[target], [sibling], [sibling]]
        list_usb_devices.side_effect = [
            [PnpDeviceStatus(
                instance_id=r"USB\VID_1234&PID_ABCD\TARGET",
                name="Target",
                problem_code=43,
                status="Error",
                vid="1234",
                pid="abcd",
            )],
            [PnpDeviceStatus(
                instance_id=r"USB\VID_1234&PID_ABCD\SIBLING",
                name="Sibling",
                problem_code=0,
                status="OK",
                vid="1234",
                pid="abcd",
            )],
        ]

        self.assertFalse(_wait_pnp_healthy(self.device, 0.5))
        self.assertGreaterEqual(list_attached.call_count, 2)

    @patch("client.core.pnp_recovery._matching_attached", return_value=None)
    def test_recovery_rejects_host_replacement_before_unbind(self, matching):
        replacement = self.device.model_copy(update={"vid": "9999", "pid": "0001"})
        api = Mock(host_ip="10.0.0.1")
        api.get_devices.return_value = [replacement]

        success, message = recover_device(api, self.device, identity_confirmed=True)

        self.assertFalse(success)
        self.assertIn("identity", message.lower())
        api.unbind_device.assert_not_called()

    @patch("client.core.pnp_recovery.time.sleep")
    def test_host_shared_wait_rejects_replacement_at_same_busid(self, sleep):
        replacement = self.device.model_copy(
            update={"vid": "9999", "pid": "0001", "state": "Shared"}
        )
        api = Mock()
        api.get_devices.return_value = [replacement]

        self.assertFalse(
            _wait_host_shared(
                api,
                "1-2",
                time.monotonic() + 0.01,
                expected_vid="1234",
                expected_pid="abcd",
            )
        )

    def test_host_shared_wait_rejects_duplicate_busid(self):
        duplicate = self.device.model_copy(update={"state": "Shared"})
        api = Mock()
        api.get_devices.return_value = [duplicate, duplicate.model_copy()]

        self.assertFalse(_wait_host_shared(
            api,
            self.device.busid,
            time.monotonic() + 1,
            expected_vid=self.device.vid,
            expected_pid=self.device.pid,
        ))

    @patch("client.core.pnp_recovery.time.sleep")
    @patch("client.core.pnp_recovery._wait_pnp_healthy", side_effect=[False, True])
    @patch("client.core.pnp_recovery._wait_host_shared", return_value=True)
    @patch("client.core.pnp_recovery._wait_host_unbound", return_value=True)
    @patch("client.core.pnp_recovery.usbip_wrapper.attach_device")
    @patch("client.core.pnp_recovery.usbip_wrapper.detach_busid")
    @patch("client.core.pnp_recovery._matching_attached")
    def test_second_attempt_cycles_host_binding(
        self, matching, detach, attach, wait_unbound, wait_shared, wait_healthy, sleep
    ):
        matching.return_value = AttachedDevice(port=3, busid="1-2", vid="1234", pid="abcd")
        detach.return_value = CommandResult(success=True, message="detached")
        attach.return_value = CommandResult(success=True, message="attached")
        api = Mock(host_ip="10.0.0.1")
        api.get_devices.return_value = [self.device]
        api.unbind_device.return_value = True
        api.bind_device.return_value = True

        success, _ = recover_device(api, self.device)

        self.assertTrue(success)
        self.assertEqual([call("1-2"), call("1-2")], api.unbind_device.call_args_list)
        self.assertEqual([call("1-2"), call("1-2")], api.bind_device.call_args_list)

    @patch("client.core.pnp_recovery.time.sleep")
    @patch("client.core.pnp_recovery._wait_pnp_healthy", return_value=True)
    @patch("client.core.pnp_recovery._wait_host_shared", return_value=True)
    @patch("client.core.pnp_recovery._wait_host_unbound", return_value=True)
    @patch("client.core.pnp_recovery.usbip_wrapper.attach_device")
    @patch("client.core.pnp_recovery.usbip_wrapper.detach_busid")
    @patch("client.core.pnp_recovery._matching_attached")
    def test_failed_attach_escalates_without_detach(
        self, matching, detach, attach, wait_unbound, wait_shared, wait_healthy, sleep
    ):
        matching.side_effect = [
            AttachedDevice(port=3, busid="1-2", vid="1234", pid="abcd"),
            None,
        ]
        detach.return_value = CommandResult(success=True, message="detached")
        attach.side_effect = [
            CommandResult(success=False, message="attach failed"),
            CommandResult(success=True, message="attached"),
        ]
        api = Mock(host_ip="10.0.0.1")
        api.get_devices.return_value = [self.device]
        api.unbind_device.return_value = True
        api.bind_device.return_value = True

        success, _ = recover_device(api, self.device)

        self.assertTrue(success)
        detach.assert_called_once()
        self.assertEqual(2, attach.call_count)
        self.assertEqual([call("1-2"), call("1-2")], api.unbind_device.call_args_list)
        self.assertEqual([call("1-2"), call("1-2")], api.bind_device.call_args_list)

    @patch("client.core.pnp_recovery.recover_device", return_value=(True, "ok"))
    @patch("client.core.pnp_recovery.usbip_wrapper.query_attached_devices")
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
        query_attached,
        recover,
    ):
        status = PnpDeviceStatus(instance_id=r"USB\UNKNOWN\1", name="", problem_code=43, status="Error")
        list_usb_devices.return_value = [status]
        find_unknown.return_value = [status]
        find_session_code43.return_value = [status]
        from client.core.usbip_wrapper import AttachedDevicesQuery

        query_attached.return_value = AttachedDevicesQuery(
            True,
            (AttachedDevice(port=2, busid="1-2", vid="1234", pid="abcd"),),
        )

        monitor = PnpRecoveryMonitor(Mock(host_ip="10.0.0.1", host_port=5757, api_key=""))
        monitor._running = True
        monitor._devices = [self.device]
        monitor.recovery_succeeded = Mock()

        monitor._check_once()

        recover.assert_not_called()
        completed = threading.Event()
        recover.side_effect = lambda *args, **kwargs: (completed.set() or True, "ok")
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
        kill_queries.assert_called_once_with(set())
        kill_usbip.assert_not_called()

    def test_monitor_run_does_not_clear_a_prior_stop_request(self):
        monitor = PnpRecoveryMonitor(Mock(host_ip="10.0.0.1", host_port=5757, api_key=""))
        monitor._check_once = Mock()
        monitor._running = False
        monitor._stop_event.set()

        monitor.run()

        monitor._check_once.assert_not_called()

    @patch("client.core.pnp_recovery.time.sleep")
    def test_monitor_continues_after_poll_exception(self, sleep):
        monitor = PnpRecoveryMonitor(Mock(host_ip="10.0.0.1", host_port=5757, api_key=""))
        monitor._running = True
        calls = []

        def check_once():
            calls.append("poll")
            if len(calls) == 1:
                raise OSError("ProgramData lock unavailable")
            monitor._stop_event.set()

        monitor._check_once = check_once
        monitor.run()

        self.assertEqual(["poll", "poll"], calls)

    @patch("client.core.pnp_recovery.recover_device", side_effect=RuntimeError("boom"))
    def test_recovery_exception_clears_active_worker(self, recover):
        monitor = PnpRecoveryMonitor(Mock(host_ip="10.0.0.1", host_port=5757, api_key=""))
        monitor._running = True
        monitor._recovery_threads[self.device.busid] = threading.current_thread()
        monitor.recovery_failed = Mock()

        monitor._recover_in_background(self.device)

        self.assertNotIn(self.device.busid, monitor._recovery_threads)
        monitor.recovery_failed.emit.assert_called_once()

    @patch("client.core.pnp_recovery.usbip_wrapper.query_attached_devices")
    @patch("client.core.pnp_recovery.windows_pnp.list_usb_devices")
    def test_monitor_does_not_recover_when_local_usbip_enumeration_failed(
        self, list_usb, local_query
    ):
        from client.core.usbip_wrapper import AttachedDevicesQuery

        list_usb.return_value = [
            PnpDeviceStatus(
                instance_id=r"USB\VID_0000&PID_0002\broken",
                name="",
                problem_code=43,
                status="Error",
            )
        ]
        local_query.return_value = AttachedDevicesQuery(False, (), "usbip port failed")
        monitor = PnpRecoveryMonitor(Mock(host_ip="10.0.0.1", host_port=5757, api_key=""))
        monitor._running = True
        monitor.update_devices([self.device])
        monitor._recover_in_background = Mock()

        monitor._check_once()

        monitor._recover_in_background.assert_not_called()

    @patch("client.core.pnp_recovery.recover_device")
    @patch("client.core.pnp_recovery.usbip_wrapper.query_attached_devices")
    @patch("client.core.pnp_recovery.windows_pnp.find_session_code43", return_value=[Mock()])
    @patch("client.core.pnp_recovery.windows_pnp.find_unknown_code43", return_value=[])
    @patch("client.core.pnp_recovery.windows_pnp.list_usb_devices", return_value=[Mock()])
    def test_monitor_recovers_multiple_devices_in_parallel(
        self, list_usb, find_unknown, find_session, query_attached, recover
    ):
        second = self.device.model_copy(update={"busid": "1-3", "vid": "9999", "pid": "0001"})
        from client.core.usbip_wrapper import AttachedDevicesQuery

        query_attached.return_value = AttachedDevicesQuery(True, (
            AttachedDevice(port=2, busid="1-2", vid="1234", pid="abcd"),
            AttachedDevice(port=3, busid="1-3", vid="9999", pid="0001"),
        ))
        release = threading.Event()
        both_started = threading.Event()
        calls = []

        def blocking_recovery(api, device, cancel_event, **kwargs):
            calls.append(device.busid)
            if len(calls) == 2:
                both_started.set()
            release.wait(1)
            return True, "ok"

        recover.side_effect = blocking_recovery
        monitor = PnpRecoveryMonitor(Mock(host_ip="10.0.0.1", host_port=5757, api_key=""))
        monitor._running = True
        monitor._devices = [self.device, second]

        monitor._check_once()
        monitor._check_once()

        self.assertTrue(both_started.wait(1))
        release.set()
        self.assertEqual({"1-2", "1-3"}, set(calls))


if __name__ == "__main__":
    unittest.main()
