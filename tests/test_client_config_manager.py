import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from client.core import config_manager


class TestClientScheduledReconnectConfig(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._config_patch = patch("client.core.config_manager._config_dir", return_value=self._tmpdir)
        self._shared_patch = patch("client.core.config_manager._shared_config_dir", return_value=self._tmpdir)
        self._programdata_patch = patch("client.core.config_manager._programdata_dir", return_value=self._tmpdir)
        self._config_patch.start()
        self._shared_patch.start()
        self._programdata_patch.start()
        config_path = os.path.join(self._tmpdir, "client_config.json")
        if os.path.exists(config_path):
            os.remove(config_path)

    def tearDown(self):
        self._config_patch.stop()
        self._shared_patch.stop()
        self._programdata_patch.stop()
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _config_path(self) -> str:
        return os.path.join(self._tmpdir, "client_config.json")

    def test_old_config_loads_with_new_defaults(self):
        with open(self._config_path(), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "host_ip": "10.0.0.2",
                    "permanent_devices": [
                        {
                            "vid": "046D",
                            "pid": "C31C",
                            "description": "Keyboard",
                        }
                    ],
                },
                f,
            )

        config = config_manager.load_config()

        self.assertEqual(config.host_ip, "10.0.0.2")
        self.assertEqual(len(config.permanent_devices), 1)
        device = config.permanent_devices[0]
        self.assertEqual(device.vid, "046d")
        self.assertEqual(device.pid, "c31c")
        self.assertFalse(device.scheduled_reconnect_enabled)
        self.assertEqual(device.scheduled_reconnect_interval_hours, 24)
        self.assertEqual(device.last_scheduled_reconnect_at, "")

    def test_enable_scheduled_reconnect_creates_permanent_device(self):
        config_manager.enable_scheduled_reconnect("046D", "C31C")

        config = config_manager.load_config()
        self.assertEqual(len(config.permanent_devices), 1)
        device = config.permanent_devices[0]
        self.assertEqual(device.vid, "046d")
        self.assertEqual(device.pid, "c31c")
        self.assertTrue(device.auto_attach)
        self.assertTrue(device.scheduled_reconnect_enabled)
        self.assertEqual(device.scheduled_reconnect_interval_hours, 24)
        self.assertNotEqual(device.last_scheduled_reconnect_at, "")
        datetime.fromisoformat(device.last_scheduled_reconnect_at)

    def test_update_scheduled_reconnect_keeps_device_and_updates_interval(self):
        config_manager.add_permanent_device("046D", "C31C", "Keyboard")
        config_manager.enable_scheduled_reconnect("046D", "C31C", interval_hours=12)

        config_manager.update_scheduled_reconnect("046d", "c31c", interval_hours=48)

        config = config_manager.load_config()
        self.assertEqual(len(config.permanent_devices), 1)
        device = config.permanent_devices[0]
        self.assertEqual(device.vid, "046d")
        self.assertEqual(device.pid, "c31c")
        self.assertEqual(device.description, "Keyboard")
        self.assertTrue(device.auto_attach)
        self.assertTrue(device.scheduled_reconnect_enabled)
        self.assertEqual(device.scheduled_reconnect_interval_hours, 48)
        self.assertNotEqual(device.last_scheduled_reconnect_at, "")
        datetime.fromisoformat(device.last_scheduled_reconnect_at)

    def test_update_scheduled_reconnect_preserves_existing_auto_attach_choice(self):
        config = config_manager.load_config()
        config.permanent_devices.append(
            config_manager.ClientPermanentDevice(
                vid="046D",
                pid="C31C",
                description="Token",
                auto_attach=False,
            )
        )
        config_manager.save_config(config)

        config_manager.update_scheduled_reconnect("046d", "c31c", interval_hours=8)

        config = config_manager.load_config()
        device = config.permanent_devices[0]
        self.assertFalse(device.auto_attach)
        self.assertTrue(device.scheduled_reconnect_enabled)
        self.assertEqual(device.scheduled_reconnect_interval_hours, 8)

    def test_disable_scheduled_reconnect_only_toggles_flag(self):
        config_manager.enable_scheduled_reconnect("046d", "c31c", interval_hours=6)

        config_manager.disable_scheduled_reconnect("046D", "C31C")

        config = config_manager.load_config()
        device = config.permanent_devices[0]
        self.assertFalse(device.scheduled_reconnect_enabled)
        self.assertEqual(device.scheduled_reconnect_interval_hours, 6)
        self.assertTrue(device.auto_attach)

    def test_helpers_normalize_vid_pid(self):
        config_manager.enable_scheduled_reconnect("046D", "C31C", interval_hours=2)
        config_manager.disable_scheduled_reconnect("046d", "c31c")

        config = config_manager.load_config()
        self.assertEqual(config.permanent_devices[0].vid, "046d")
        self.assertEqual(config.permanent_devices[0].pid, "c31c")
        self.assertTrue(config_manager.is_permanent("046D", "C31C"))
