import os
import sys
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from shared.models import AttachedDevice

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _FakeSignal:
    def __init__(self, *args, **kwargs):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args, **kwargs):
        for callback in list(self._callbacks):
            callback(*args, **kwargs)


class _FakeQObject:
    def __init__(self, parent=None):
        self.parent = parent


class _FakeQt:
    class WindowModality:
        WindowModal = 1


class _FakeQThread:
    def __init__(self, parent=None):
        self.parent = parent
        self.finished = _FakeSignal()

    def start(self):
        self.run()
        self.finished.emit()

    def run(self):
        pass

    def deleteLater(self):
        pass

    def isRunning(self):
        return False

    def terminate(self):
        pass

    def wait(self, *_args, **_kwargs):
        pass


class _FakeQTimer:
    def __init__(self, parent=None):
        self.parent = parent
        self.timeout = _FakeSignal()
        self.interval_ms = 0
        self.started = False

    def setInterval(self, value):
        self.interval_ms = value

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


_fake_qt = types.ModuleType("PyQt6")
_fake_qtcore = types.ModuleType("PyQt6.QtCore")
_fake_qtcore.QObject = _FakeQObject
_fake_qtcore.QThread = _FakeQThread
_fake_qtcore.QTimer = _FakeQTimer
_fake_qtcore.Qt = _FakeQt
_fake_qtcore.pyqtSignal = lambda *args, **kwargs: _FakeSignal()
sys.modules.setdefault("PyQt6", _fake_qt)
sys.modules["PyQt6.QtCore"] = _fake_qtcore

from client.core.scheduled_reconnect import (  # noqa: E402
    _run_reconnect_cycle,
    _run_reconnect_cycle_unlocked,
    find_unique_identity_match,
)


def _make_device(busid="1-5", vid="046d", pid="c31c", state="Attached", desc="Keyboard"):
    from shared.models import UsbDevice

    return UsbDevice(
        busid=busid,
        vid=vid,
        pid=pid,
        description=desc,
        state=state,
    )


class TestScheduledReconnectController(unittest.TestCase):
    def _make_controller(self, now, devices, connected=True, service_ok=True):
        from client.api.host_client import HostApiClient
        from client.core.scheduled_reconnect import ScheduledReconnectController

        api_client = HostApiClient(host_ip="192.168.1.10")
        controller = ScheduledReconnectController(
            api_client,
            tick_interval_seconds=60,
            now_provider=lambda: now[0],
        )
        controller.update_devices(devices)
        controller.update_connection_state(connected)
        controller.update_service_state(service_ok)
        return controller

    def _mock_config(self, last_run="", interval_hours=1):
        from shared.models import ClientConfig, ClientPermanentDevice

        return ClientConfig(
            host_ip="192.168.1.10",
            host_port=5757,
            api_key="",
            permanent_devices=[
                ClientPermanentDevice(
                    vid="046d",
                    pid="c31c",
                    description="Keyboard",
                    scheduled_reconnect_enabled=True,
                    scheduled_reconnect_interval_hours=interval_hours,
                    last_scheduled_reconnect_at=last_run,
                )
            ],
        )

    @patch("client.core.scheduled_reconnect.config_manager")
    def test_does_not_run_before_interval(self, mock_cfg):
        now = [datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)]
        controller = self._make_controller(now, [_make_device()], connected=True, service_ok=True)
        mock_cfg.load_config.return_value = self._mock_config(interval_hours=1)
        controller._start_worker = Mock()

        controller.start()
        now[0] = now[0] + timedelta(minutes=30)
        controller.tick()

        controller._start_worker.assert_not_called()

    @patch("client.core.scheduled_reconnect.config_manager")
    def test_skips_when_host_offline_or_service_down(self, mock_cfg):
        now = [datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)]
        mock_cfg.load_config.return_value = self._mock_config(last_run="2026-07-07T10:00:00+00:00", interval_hours=1)

        offline = self._make_controller(now, [_make_device()], connected=False, service_ok=True)
        offline._start_worker = Mock()
        offline.start()
        offline.tick()
        offline._start_worker.assert_not_called()

        service_down = self._make_controller(now, [_make_device()], connected=True, service_ok=False)
        service_down._start_worker = Mock()
        service_down.start()
        service_down.tick()
        service_down._start_worker.assert_not_called()

    @patch("client.core.scheduled_reconnect.config_manager")
    def test_does_not_start_two_reconnections_for_same_vid_pid(self, mock_cfg):
        now = [datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc)]
        mock_cfg.load_config.return_value = self._mock_config(last_run="2026-07-07T10:00:00+00:00", interval_hours=1)
        controller = self._make_controller(now, [_make_device(), _make_device(busid="1-6")])
        controller._start_worker = Mock()

        controller.start()
        now[0] = now[0] + timedelta(hours=2)
        controller.tick()
        controller.tick()

        self.assertEqual(controller._start_worker.call_count, 1)

    @patch("client.core.scheduled_reconnect.config_manager")
    def test_failed_reconnect_uses_cooldown_before_retrying(self, mock_cfg):
        now = [datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc)]
        mock_cfg.load_config.return_value = self._mock_config(last_run="2026-07-07T08:00:00+00:00", interval_hours=1)
        controller = self._make_controller(now, [_make_device()])
        controller._start_worker = Mock()

        controller.start()
        now[0] = now[0] + timedelta(hours=2)
        controller.tick()
        controller._on_worker_result(False, "1-5", "failed", ("046d", "c31c"), Mock())
        controller.tick()

        self.assertEqual(controller._start_worker.call_count, 1)

        now[0] = now[0] + timedelta(minutes=16)
        controller.tick()

        self.assertEqual(controller._start_worker.call_count, 2)

    @patch("client.core.scheduled_reconnect.config_manager")
    def test_transport_lock_contention_does_not_apply_long_cooldown(self, mock_cfg):
        now = [datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc)]
        controller = self._make_controller(now, [_make_device()])
        key = ("046d", "c31c")

        controller._on_worker_result(
            False,
            "1-5",
            "Timed out waiting to attach 1-5; transport lock contention",
            key,
            Mock(),
        )

        self.assertNotIn(key, controller._failed_until)

    def test_wait_for_workers_tolerates_qthread_deleted_during_cleanup(self):
        now = [datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc)]
        controller = self._make_controller(now, [])
        deleted_worker = Mock()
        deleted_worker.wait.side_effect = RuntimeError("wrapped C/C++ object has been deleted")
        controller._workers = [deleted_worker]

        controller.wait_for_workers(10)

        deleted_worker.wait.assert_called_once()


class TestScheduledReconnectCycle(unittest.TestCase):
    def test_host_state_wait_rejects_duplicate_busid(self):
        from client.core.scheduled_reconnect import _wait_host_state

        device = _make_device(state="Shared")
        api = Mock()
        api.get_devices.return_value = [device, device.model_copy()]

        self.assertFalse(_wait_host_state(
            api,
            device.busid,
            "Shared",
            timeout=1,
            expected_vid=device.vid,
            expected_pid=device.pid,
        ))

    def test_identity_match_fails_closed_for_duplicate_vid_pid(self):
        devices = [
            _make_device(busid="1-5"),
            _make_device(busid="1-6"),
        ]

        self.assertIsNone(find_unique_identity_match(devices, "046d", "c31c"))

    @patch("client.core.scheduled_reconnect._wait_pnp_healthy", return_value=True, create=True)
    @patch("client.core.scheduled_reconnect._wait_host_state", return_value=True)
    @patch("client.core.scheduled_reconnect.config_manager")
    @patch("client.core.scheduled_reconnect.usbip_wrapper")
    def test_reconnect_cycle_detach_unbind_bind_attach(
        self, mock_usbip, mock_cfg, wait_host_state, wait_pnp
    ):
        mock_cfg.load_config.return_value = Mock(
            permanent_devices=[
                Mock(
                    vid="046d",
                    pid="c31c",
                    last_scheduled_reconnect_at="",
                )
            ]
        )
        mock_cfg.mark_scheduled_reconnect_completed.return_value = None
        mock_usbip.query_attached_devices.return_value = Mock(
            success=True,
            devices=(AttachedDevice(port=3, busid="1-5", vid="046d", pid="c31c"),),
            error="",
        )
        mock_usbip.detach_busid.return_value = Mock(success=True, message="detached")
        mock_usbip.attach_device.return_value = Mock(success=True, message="attached")

        api_client = Mock()
        api_client.host_ip = "192.168.1.10"
        api_client.get_devices.return_value = [_make_device(state="Attached")]
        api_client.unbind_device.return_value = True
        api_client.bind_device.return_value = True

        validation_started = time.monotonic()
        success, message = _run_reconnect_cycle(api_client, _make_device(state="Attached"))
        validation_finished = time.monotonic()

        self.assertTrue(success)
        self.assertEqual(message, "1-5")
        transport_timeout = 2
        self.assertLess(transport_timeout, 15)
        mock_usbip.query_attached_devices.assert_called_once_with(
            timeout=transport_timeout,
        )
        mock_usbip.detach_busid.assert_called_once_with(
            "1-5",
            timeout=transport_timeout,
            port_hint=3,
            expected_vid="046d",
            expected_pid="c31c",
        )
        api_client.unbind_device.assert_called_once_with("1-5")
        api_client.bind_device.assert_called_once_with("1-5")
        mock_usbip.attach_device.assert_called_once_with(
            "192.168.1.10",
            "1-5",
            timeout=transport_timeout,
            vid="046d",
            pid="c31c",
        )
        self.assertEqual(
            ["Not shared", "Shared"],
            [item.args[2] for item in wait_host_state.call_args_list],
        )
        mock_cfg.mark_scheduled_reconnect_completed.assert_called_once()
        wait_pnp.assert_called_once()
        validation_deadline = wait_pnp.call_args.args[1]
        self.assertGreaterEqual(validation_deadline, validation_started + 11.9)
        self.assertLessEqual(validation_deadline, validation_finished + 12.1)

    @patch("client.core.scheduled_reconnect._wait_pnp_healthy", return_value=True)
    @patch("client.core.scheduled_reconnect._wait_host_state", return_value=True)
    @patch("client.core.scheduled_reconnect.config_manager")
    @patch("client.core.scheduled_reconnect.usbip_wrapper")
    def test_non_scheduled_cycle_does_not_update_schedule_timestamp(
        self, mock_usbip, mock_cfg, wait_host_state, wait_pnp
    ):
        api = Mock()
        api.get_devices.return_value = [_make_device(state="Shared")]
        api.unbind_device.return_value = True
        api.bind_device.return_value = True
        mock_usbip.query_attached_devices.return_value = Mock(
            success=True, devices=(), error=""
        )
        mock_usbip.attach_device.return_value = Mock(success=True, message="attached")

        success, _ = _run_reconnect_cycle(
            api,
            _make_device(state="Shared"),
            record_completion=False,
            identity_confirmed=True,
        )

        self.assertTrue(success)
        mock_cfg.mark_scheduled_reconnect_completed.assert_not_called()

    @patch("client.core.scheduled_reconnect._wait_pnp_healthy", return_value=True, create=True)
    @patch("client.core.scheduled_reconnect._wait_host_state", return_value=True, create=True)
    @patch("client.core.scheduled_reconnect.config_manager")
    @patch("client.core.scheduled_reconnect.usbip_wrapper")
    def test_reconnect_cycle_without_local_port_fails_closed(
        self, mock_usbip, mock_cfg, wait_host_state, wait_pnp
    ):
        mock_cfg.load_config.return_value = Mock(
            permanent_devices=[
                Mock(
                    vid="046d",
                    pid="c31c",
                    last_scheduled_reconnect_at="",
                )
            ]
        )
        mock_usbip.query_attached_devices.return_value = Mock(success=True, devices=(), error="")
        mock_usbip.attach_device.return_value = Mock(success=True, message="attached")
        api_client = Mock(host_ip="192.168.1.10")
        api_client.get_devices.return_value = [_make_device(state="Attached")]
        api_client.unbind_device.return_value = True
        api_client.bind_device.return_value = True

        success, message = _run_reconnect_cycle(api_client, _make_device(state="Attached"))

        self.assertFalse(success)
        self.assertIn("local session", message.lower())
        mock_usbip.detach_busid.assert_not_called()
        api_client.unbind_device.assert_not_called()
        api_client.bind_device.assert_not_called()
        wait_host_state.assert_not_called()
        mock_usbip.attach_device.assert_not_called()

    @patch("client.core.scheduled_reconnect._wait_pnp_healthy", return_value=True, create=True)
    @patch("client.core.scheduled_reconnect._wait_host_state", return_value=True)
    @patch("client.core.scheduled_reconnect.config_manager")
    @patch("client.core.scheduled_reconnect.usbip_wrapper")
    def test_scheduled_reconnect_refuses_attached_host_without_local_session(
        self, mock_usbip, mock_cfg, wait_host_state, wait_pnp
    ):
        mock_usbip.query_attached_devices.return_value = Mock(success=True, devices=(), error="")
        api_client = Mock(host_ip="192.168.1.10")
        api_client.get_devices.return_value = [_make_device(state="Attached")]
        api_client.unbind_device.return_value = True
        api_client.bind_device.return_value = True

        success, message = _run_reconnect_cycle(api_client, _make_device(state="Attached"))

        self.assertFalse(success)
        self.assertIn("local session", message.lower())
        api_client.unbind_device.assert_not_called()
        api_client.bind_device.assert_not_called()
        mock_usbip.attach_device.assert_not_called()

    @patch("client.core.scheduled_reconnect._wait_pnp_healthy", return_value=False, create=True)
    @patch("client.core.scheduled_reconnect._wait_host_state", return_value=True)
    @patch("client.core.scheduled_reconnect.config_manager")
    @patch("client.core.scheduled_reconnect.usbip_wrapper")
    def test_unhealthy_pnp_does_not_mark_reconnect_complete(
        self, mock_usbip, mock_cfg, wait_host_state, wait_pnp
    ):
        mock_usbip.query_attached_devices.return_value = Mock(success=True, devices=(), error="")
        mock_usbip.attach_device.return_value = Mock(success=True, message="attached")
        api_client = Mock(host_ip="192.168.1.10")
        api_client.get_devices.return_value = [_make_device(state="Attached")]
        api_client.unbind_device.return_value = True
        api_client.bind_device.return_value = True

        success, message = _run_reconnect_cycle(
            api_client,
            _make_device(state="Attached"),
            identity_confirmed=True,
        )

        self.assertFalse(success)
        self.assertIn("PnP", message)
        mock_cfg.mark_scheduled_reconnect_completed.assert_not_called()

    @patch("client.core.scheduled_reconnect._wait_pnp_healthy", return_value=True, create=True)
    @patch("client.core.scheduled_reconnect._wait_host_state")
    @patch("client.core.scheduled_reconnect.config_manager")
    @patch("client.core.scheduled_reconnect.usbip_wrapper")
    def test_cancellation_after_shared_wait_blocks_attach(
        self, mock_usbip, mock_cfg, wait_host_state, wait_pnp
    ):
        cancelled = threading.Event()
        mock_usbip.query_attached_devices.return_value = Mock(success=True, devices=(), error="")

        def wait_state(*args, **kwargs):
            if args[2] == "Shared":
                cancelled.set()
            return True

        wait_host_state.side_effect = wait_state
        api_client = Mock(host_ip="192.168.1.10")
        api_client.get_devices.return_value = [_make_device(state="Attached")]
        api_client.unbind_device.return_value = True
        api_client.bind_device.return_value = True

        success, message = _run_reconnect_cycle(
            api_client,
            _make_device(state="Attached"),
            cancel_event=cancelled,
            identity_confirmed=True,
        )

        self.assertFalse(success)
        self.assertIn("interrupted", message)
        mock_usbip.attach_device.assert_not_called()

    @patch("client.core.scheduled_reconnect._wait_host_state")
    @patch("client.core.scheduled_reconnect.config_manager")
    @patch("client.core.scheduled_reconnect.usbip_wrapper")
    def test_cancel_after_unbind_rebinds_host_before_return(
        self, mock_usbip, mock_cfg, wait_host_state
    ):
        cancelled = threading.Event()
        mock_usbip.query_attached_devices.return_value = Mock(success=True, devices=(), error="")

        def wait_state(*args, **kwargs):
            if args[2] == "Not shared":
                cancelled.set()
                return False
            return True

        wait_host_state.side_effect = wait_state
        api_client = Mock(host_ip="192.168.1.10")
        api_client.get_devices.return_value = [_make_device(state="Attached")]
        api_client.unbind_device.return_value = True
        api_client.bind_device.return_value = True

        success, message = _run_reconnect_cycle(
            api_client,
            _make_device(state="Attached"),
            cancel_event=cancelled,
            identity_confirmed=True,
        )

        self.assertFalse(success)
        self.assertIn("interrupted", message)
        self.assertEqual(1, api_client.bind_device.call_count)
        self.assertGreaterEqual(
            sum(item.args[2] == "Shared" for item in wait_host_state.call_args_list),
            1,
        )

    @patch("client.core.scheduled_reconnect._wait_host_state", return_value=True)
    @patch("client.core.scheduled_reconnect.config_manager")
    @patch("client.core.scheduled_reconnect.usbip_wrapper")
    def test_bind_failure_attempts_compensatory_rebind(
        self, mock_usbip, mock_cfg, wait_host_state
    ):
        mock_usbip.query_attached_devices.return_value = Mock(success=True, devices=(), error="")
        api_client = Mock(host_ip="192.168.1.10")
        api_client.get_devices.return_value = [_make_device(state="Attached")]
        api_client.unbind_device.return_value = True
        api_client.bind_device.side_effect = [False, True]

        success, message = _run_reconnect_cycle(
            api_client,
            _make_device(state="Attached"),
            cancel_event=threading.Event(),
            identity_confirmed=True,
        )

        self.assertFalse(success)
        self.assertIn("bind", message.lower())
        self.assertEqual(2, api_client.bind_device.call_count)
        self.assertGreaterEqual(
            sum(item.args[2] == "Shared" for item in wait_host_state.call_args_list),
            1,
        )

    @patch("client.core.scheduled_reconnect._wait_host_state", side_effect=[True, False, True])
    @patch("client.core.scheduled_reconnect.config_manager")
    @patch("client.core.scheduled_reconnect.usbip_wrapper")
    def test_shared_timeout_attempts_compensatory_rebind(
        self, mock_usbip, mock_cfg, wait_host_state
    ):
        mock_usbip.query_attached_devices.return_value = Mock(success=True, devices=(), error="")
        api_client = Mock(host_ip="192.168.1.10")
        api_client.get_devices.return_value = [_make_device(state="Attached")]
        api_client.unbind_device.return_value = True
        api_client.bind_device.return_value = True

        success, message = _run_reconnect_cycle(
            api_client,
            _make_device(state="Attached"),
            identity_confirmed=True,
        )

        self.assertFalse(success)
        self.assertIn("Shared", message)
        self.assertEqual(2, api_client.bind_device.call_count)
        self.assertEqual(3, wait_host_state.call_count)

    @patch("client.core.scheduled_reconnect.time.sleep")
    def test_host_state_wait_rejects_replacement_at_same_busid(self, sleep):
        from client.core.scheduled_reconnect import _wait_host_state

        api_client = Mock()
        api_client.get_devices.return_value = [
            _make_device(vid="9999", pid="0001", state="Shared")
        ]

        self.assertFalse(
            _wait_host_state(
                api_client,
                "1-5",
                "Shared",
                timeout=0.01,
                expected_vid="046d",
                expected_pid="c31c",
            )
        )

    @patch("client.core.scheduled_reconnect.usbip_wrapper.query_attached_devices")
    def test_reconnect_revalidates_host_identity_immediately_before_unbind(self, local_query):
        from client.core.usbip_wrapper import AttachedDevicesQuery

        expected = _make_device(state="Attached")
        replacement = _make_device(vid="9999", pid="0001", state="Attached")
        api_client = Mock(host_ip="192.168.1.10")
        api_client.get_devices.side_effect = [[expected], [replacement]]
        local_query.return_value = AttachedDevicesQuery(True, ())

        success, message = _run_reconnect_cycle_unlocked(
            api_client,
            expected,
            identity_confirmed=True,
        )

        self.assertFalse(success)
        self.assertIn("identity changed", message.lower())
        api_client.unbind_device.assert_not_called()

    def test_cancel_during_final_identity_revalidation_stops_before_unbind(self):
        cancelled = threading.Event()
        expected = _make_device(state="Shared")
        api_client = Mock(host_ip="192.168.1.10")
        api_client.get_devices.return_value = [expected]

        def cancel_during_revalidation(*_args, **_kwargs):
            cancelled.set()
            return True, ""

        with patch(
            "client.core.scheduled_reconnect._host_busid_identity_status",
            side_effect=cancel_during_revalidation,
        ):
            success, message = _run_reconnect_cycle_unlocked(
                api_client,
                expected,
                cancel_event=cancelled,
                identity_confirmed=True,
            )

        self.assertFalse(success)
        self.assertIn("interrupted", message)
        api_client.unbind_device.assert_not_called()
        api_client.bind_device.assert_not_called()

    @patch("client.core.scheduled_reconnect.usbip_wrapper.query_attached_devices")
    def test_reconnect_reports_host_unreachable_before_mutation(self, local_query):
        from client.core.usbip_wrapper import AttachedDevicesQuery

        expected = _make_device(state="Attached")
        api_client = Mock(host_ip="192.168.1.10")
        api_client.get_devices.return_value = []
        api_client.is_connected.return_value = False
        local_query.return_value = AttachedDevicesQuery(True, ())

        success, message = _run_reconnect_cycle_unlocked(api_client, expected)

        self.assertFalse(success)
        self.assertIn("unreachable", message.lower())
        api_client.unbind_device.assert_not_called()

    @patch("client.core.scheduled_reconnect.config_manager")
    @patch("client.core.scheduled_reconnect.usbip_wrapper")
    def test_reconnect_cycle_fails_when_host_device_disappears(self, mock_usbip, mock_cfg):
        mock_cfg.load_config.return_value = Mock(permanent_devices=[])
        api_client = Mock()
        api_client.host_ip = "192.168.1.10"
        api_client.get_devices.return_value = []

        success, message = _run_reconnect_cycle(api_client, _make_device())

        self.assertFalse(success)
        self.assertIn("no longer available", message)
        mock_usbip.detach_busid.assert_not_called()
        api_client.unbind_device.assert_not_called()


if __name__ == "__main__":
    unittest.main()
