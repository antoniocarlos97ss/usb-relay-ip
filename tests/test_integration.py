import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from shared.models import CommandResult, HealthStatus, UsbDevice


@patch("host.api.server.config_manager")
@patch("host.api.routes.health.usbipd_wrapper")
@patch("host.api.routes.devices.usbipd_wrapper")
@patch("host.api.routes.devices.config_manager")
@patch("host.api.routes.share.usbipd_wrapper")
@patch("host.api.routes.share.config_manager")
class TestHostAPIIntegration(unittest.TestCase):

    def test_health_endpoint(self, mock_share_config, mock_share_usbipd, mock_dev_config,
                             mock_dev_usbipd, mock_health_usbipd, mock_server_config):
        mock_server_config.load_config.return_value = Mock(api_key="")
        mock_health_usbipd.is_available.return_value = True
        mock_health_usbipd.get_version.return_value = (4, 2)
        mock_health_usbipd.list_devices.return_value = [
            UsbDevice(busid="1-5", vid="046d", pid="c31c", description="KB", state="Shared"),
        ]

        from host.api.server import app
        client = TestClient(app)
        response = client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["usbipd_version"], "4.2.0")
        self.assertEqual(data["shared_count"], 1)

    def test_devices_endpoint(self, mock_share_config, mock_share_usbipd, mock_dev_config,
                              mock_dev_usbipd, mock_health_usbipd, mock_server_config):
        mock_server_config.load_config.return_value = Mock(api_key="")
        mock_dev_usbipd.list_devices.return_value = [
            UsbDevice(busid="1-5", vid="046d", pid="c31c", description="Keyboard", state="Shared"),
            UsbDevice(busid="2-1", vid="0951", pid="1666", description="USB Drive", state="Not shared"),
        ]
        mock_dev_config.is_permanent.side_effect = lambda vid, pid: vid == "046d"

        from host.api.server import app
        client = TestClient(app)
        response = client.get("/api/v1/devices")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["devices"]), 2)
        self.assertTrue(data["devices"][0]["is_permanent"])
        self.assertFalse(data["devices"][1]["is_permanent"])

    def test_bind_device_success(self, mock_share_config, mock_share_usbipd, mock_dev_config,
                                 mock_dev_usbipd, mock_health_usbipd, mock_server_config):
        mock_server_config.load_config.return_value = Mock(api_key="")
        mock_share_usbipd.list_devices.return_value = [
            UsbDevice(busid="1-5", vid="046d", pid="c31c", description="KB", state="Not shared"),
        ]
        mock_share_usbipd.bind_device.return_value = CommandResult(
            success=True, message="Device 1-5 bound successfully."
        )

        from host.api.server import app
        client = TestClient(app)
        response = client.post("/api/v1/devices/1-5/bind")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

    def test_bind_device_already_shared(self, mock_share_config, mock_share_usbipd, mock_dev_config,
                                        mock_dev_usbipd, mock_health_usbipd, mock_server_config):
        mock_server_config.load_config.return_value = Mock(api_key="")
        mock_share_usbipd.list_devices.return_value = [
            UsbDevice(busid="1-5", vid="046d", pid="c31c", description="KB", state="Shared"),
        ]

        from host.api.server import app
        client = TestClient(app)
        response = client.post("/api/v1/devices/1-5/bind")
        self.assertFalse(response.json()["success"])

    def test_bind_device_not_found(self, mock_share_config, mock_share_usbipd, mock_dev_config,
                                   mock_dev_usbipd, mock_health_usbipd, mock_server_config):
        mock_server_config.load_config.return_value = Mock(api_key="")
        mock_share_usbipd.list_devices.return_value = []

        from host.api.server import app
        client = TestClient(app)
        response = client.post("/api/v1/devices/nonexistent/bind")
        self.assertFalse(response.json()["success"])

    def test_api_key_auth_required(self, mock_share_config, mock_share_usbipd, mock_dev_config,
                                   mock_dev_usbipd, mock_health_usbipd, mock_server_config):
        mock_server_config.load_config.return_value = Mock(api_key="secret-key")

        from host.api.server import app
        client = TestClient(app)
        response = client.get("/api/v1/devices")
        self.assertEqual(response.status_code, 401)

        response = client.get("/api/v1/devices", headers={"X-API-Key": "secret-key"})
        self.assertEqual(response.status_code, 200)

    def test_api_key_wrong(self, mock_share_config, mock_share_usbipd, mock_dev_config,
                           mock_dev_usbipd, mock_health_usbipd, mock_server_config):
        mock_server_config.load_config.return_value = Mock(api_key="correct-key")

        from host.api.server import app
        client = TestClient(app)
        response = client.get("/api/v1/devices", headers={"X-API-Key": "wrong-key"})
        self.assertEqual(response.status_code, 401)

    def test_config_endpoint(self, mock_share_config, mock_share_usbipd, mock_dev_config,
                             mock_dev_usbipd, mock_health_usbipd, mock_server_config):
        mock_server_config.load_config.return_value = Mock(api_key="")
        mock_share_config.load_config.return_value = Mock(
            api_port=5757,
            poll_interval_seconds=5,
            autostart_as_service=False,
            permanent_devices=[],
        )

        from host.api.server import app
        client = TestClient(app)
        response = client.get("/api/v1/config")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["api_port"], 5757)
        self.assertEqual(data["permanent_devices_count"], 0)


class TestConfigPersistenceIntegration(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        patch("host.core.config_manager._config_dir", return_value=self.tempdir).start()
        patch("host.core.config_manager._config_path",
              return_value=os.path.join(self.tempdir, "host_config.json")).start()
        patch("client.core.config_manager._config_dir", return_value=self.tempdir).start()
        patch("client.core.config_manager._config_path",
              return_value=os.path.join(self.tempdir, "client_config.json")).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_host_permanent_device_survives_reboot(self):
        from host.core.config_manager import add_permanent_device, load_config
        add_permanent_device("046d", "c31c", "Keyboard", "1-5")
        config = load_config()
        self.assertEqual(len(config.permanent_devices), 1)
        self.assertEqual(config.permanent_devices[0].vid, "046d")
        self.assertTrue(config.permanent_devices[0].auto_bind)

    def test_client_permanent_device_survives_reboot(self):
        from client.core.config_manager import add_permanent_device, load_config
        add_permanent_device("046d", "c31c", "Keyboard")
        config = load_config()
        self.assertEqual(len(config.permanent_devices), 1)
        self.assertEqual(config.permanent_devices[0].vid, "046d")

    def test_config_atomic_write(self):
        from host.core.config_manager import load_config, save_config
        config = load_config()
        config.api_port = 9999
        save_config(config)

        tmp_file = os.path.join(self.tempdir, "host_config.json.tmp")
        self.assertFalse(os.path.exists(tmp_file))

        loaded = load_config()
        self.assertEqual(loaded.api_port, 9999)

    def test_config_corruption_reset_host(self):
        config_path = os.path.join(self.tempdir, "host_config.json")
        with open(config_path, "w") as f:
            f.write("{invalid json content")

        from host.core.config_manager import load_config
        config = load_config()
        self.assertEqual(config.api_port, 5757)

    def test_config_corruption_reset_client(self):
        config_path = os.path.join(self.tempdir, "client_config.json")
        with open(config_path, "w") as f:
            f.write("not json")

        from client.core.config_manager import load_config
        config = load_config()
        self.assertEqual(config.host_port, 5757)


class TestErrorScenarios(unittest.TestCase):

    @patch("host.core.usbipd_wrapper._run_command")
    def test_bind_without_admin_fails(self, mock_run):
        mock_run.return_value = (1, "", "Access denied. Run as Administrator.")
        from host.core.usbipd_wrapper import bind_device
        result = bind_device("1-5")
        self.assertFalse(result.success)
        self.assertIn("Failed", result.message)

    @patch("client.core.usbip_wrapper._run_command")
    def test_attach_to_offline_host_fails(self, mock_run):
        mock_run.return_value = (1, "", "Connection refused")
        from client.core.usbip_wrapper import attach_device
        result = attach_device("192.168.1.99", "1-5")
        self.assertFalse(result.success)

    def test_client_host_unreachable_returns_empty_devices(self):
        with patch("client.api.host_client.httpx.Client") as mock_client_cls:
            mock_client = Mock()
            mock_client.request.side_effect = ConnectionError("Connection refused")
            mock_client_cls.return_value.__enter__.return_value = mock_client

            from client.api.host_client import HostApiClient
            api_client = HostApiClient(host_ip="10.0.0.99")
            devices = api_client.get_devices()
            self.assertEqual(len(devices), 0)
            self.assertFalse(api_client.is_connected())

    def test_client_wrong_port_returns_empty(self):
        with patch("client.api.host_client.httpx.Client") as mock_client_cls:
            mock_client = Mock()
            mock_client.request.side_effect = ConnectionError("Connection refused")
            mock_client_cls.return_value.__enter__.return_value = mock_client

            from client.api.host_client import HostApiClient
            api_client = HostApiClient(host_ip="192.168.1.10", host_port=9999)
            devices = api_client.get_devices()
            self.assertEqual(len(devices), 0)


class TestDeviceMonitorIntegration(unittest.TestCase):

    @patch("host.core.device_monitor.QThread", Mock)
    @patch("host.core.device_monitor.pyqtSignal", Mock)
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_device_unplug_detected(self, mock_usbipd):
        from host.core.device_monitor import DeviceMonitor
        from shared.models import UsbDevice

        monitor = DeviceMonitor(poll_interval=5)
        monitor._running = False

        prev_devices = [
            UsbDevice(busid="1-5", vid="046d", pid="c31c", description="KB", state="Shared"),
            UsbDevice(busid="2-1", vid="0951", pid="1666", description="USB", state="Not shared"),
        ]
        monitor._previous_devices = prev_devices

        current_devices = [
            UsbDevice(busid="2-1", vid="0951", pid="1666", description="USB", state="Not shared"),
        ]

        changed = monitor._device_list_changed(current_devices)
        self.assertTrue(changed)

        prev_ids = {d.busid for d in prev_devices}
        curr_ids = {d.busid for d in current_devices}
        removed = prev_ids - curr_ids
        self.assertEqual(removed, {"1-5"})

    @patch("host.core.device_monitor.QThread", Mock)
    @patch("host.core.device_monitor.pyqtSignal", Mock)
    @patch("host.core.device_monitor.usbipd_wrapper")
    def test_new_device_detected_triggers_change(self, mock_usbipd):
        from host.core.device_monitor import DeviceMonitor
        from shared.models import UsbDevice

        monitor = DeviceMonitor(poll_interval=5)
        monitor._running = False
        monitor._previous_devices = []

        current_devices = [
            UsbDevice(busid="1-5", vid="046d", pid="c31c", description="KB", state="Not shared"),
        ]

        self.assertTrue(monitor._device_list_changed(current_devices))


if __name__ == "__main__":
    unittest.main()
