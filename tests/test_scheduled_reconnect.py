import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, call, patch

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
_fake_qtcore.pyqtSignal = lambda *args, **kwargs: _FakeSignal()
sys.modules.setdefault("PyQt6", _fake_qt)
sys.modules["PyQt6.QtCore"] = _fake_qtcore

from client.core.scheduled_reconnect import _run_reconnect_cycle  # noqa: E402


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


class TestScheduledReconnectCycle(unittest.TestCase):
    @patch("client.core.scheduled_reconnect.time.sleep")
    @patch("client.core.scheduled_reconnect.config_manager")
    @patch("client.core.scheduled_reconnect.usbip_wrapper")
    def test_reconnect_cycle_detach_unbind_bind_attach(self, mock_usbip, mock_cfg, mock_sleep):
        mock_sleep.return_value = None
        mock_cfg.load_config.return_value = Mock(
            permanent_devices=[
                Mock(
                    vid="046d",
                    pid="c31c",
                    last_scheduled_reconnect_at="",
                )
            ]
        )
        mock_cfg.save_config.return_value = None
        mock_usbip.find_port_for_busid.return_value = 3
        mock_usbip.detach_device.return_value = Mock(success=True, message="detached")
        mock_usbip.attach_device.return_value = Mock(success=True, message="attached")

        api_client = Mock()
        api_client.host_ip = "192.168.1.10"
        api_client.get_devices.return_value = [_make_device(state="Attached")]
        api_client.unbind_device.return_value = True
        api_client.bind_device.return_value = True

        success, message = _run_reconnect_cycle(api_client, _make_device(state="Attached"))

        self.assertTrue(success)
        self.assertEqual(message, "1-5")
        mock_usbip.detach_device.assert_called_once_with(3)
        api_client.unbind_device.assert_called_once_with("1-5")
        api_client.bind_device.assert_called_once_with("1-5")
        mock_usbip.attach_device.assert_called_once_with("192.168.1.10", "1-5", vid="046d", pid="c31c")
        self.assertEqual(mock_sleep.call_args_list, [call(2), call(2)])
        mock_cfg.save_config.assert_called_once()

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
        mock_usbip.detach_device.assert_not_called()
        api_client.unbind_device.assert_not_called()


if __name__ == "__main__":
    unittest.main()
