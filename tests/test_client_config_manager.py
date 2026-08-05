import json
import multiprocessing
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from client.core import config_manager


def _add_client_device_in_process(root: str, vid: str, pid: str, start_event) -> None:
    from unittest.mock import patch as child_patch
    from client.core import config_manager as child_config

    with child_patch.object(child_config, "_config_dir", return_value=root), \
         child_patch.object(child_config, "_shared_config_dir", return_value=root):
        start_event.wait(10)
        child_config.add_permanent_device(vid, pid, f"Device {vid}:{pid}")


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

    def test_default_shared_config_does_not_hide_nondefault_user_mirror(self):
        user_dir = os.path.join(self._tmpdir, "user")
        shared_dir = os.path.join(self._tmpdir, "shared")
        os.makedirs(user_dir, exist_ok=True)
        os.makedirs(shared_dir, exist_ok=True)
        with open(os.path.join(user_dir, "client_config.json"), "w", encoding="utf-8") as stream:
            json.dump({
                "host_ip": "10.0.0.55",
                "permanent_devices": [{"vid": "1234", "pid": "abcd", "description": "Token"}],
            }, stream)
        with open(os.path.join(shared_dir, "client_config.json"), "w", encoding="utf-8") as stream:
            json.dump(config_manager.ClientConfig().model_dump(), stream, default=str)

        with patch("client.core.config_manager._config_dir", return_value=user_dir), \
             patch("client.core.config_manager._shared_config_dir", return_value=shared_dir):
            config = config_manager.load_config()

        self.assertEqual("10.0.0.55", config.host_ip)
        self.assertEqual([("1234", "abcd")], [(item.vid, item.pid) for item in config.permanent_devices])

    def test_newer_nondefault_user_config_wins_and_is_promoted_to_shared(self):
        user_dir = os.path.join(self._tmpdir, "conflict-user")
        shared_dir = os.path.join(self._tmpdir, "conflict-shared")
        os.makedirs(user_dir, exist_ok=True)
        os.makedirs(shared_dir, exist_ok=True)
        user_path = os.path.join(user_dir, "client_config.json")
        shared_path = os.path.join(shared_dir, "client_config.json")
        with open(shared_path, "w", encoding="utf-8") as stream:
            json.dump({"host_ip": "10.0.0.10"}, stream)
        with open(user_path, "w", encoding="utf-8") as stream:
            json.dump({"host_ip": "10.0.0.20"}, stream)
        os.utime(shared_path, (1000, 1000))
        os.utime(user_path, (2000, 2000))

        with patch("client.core.config_manager._config_dir", return_value=user_dir), \
             patch("client.core.config_manager._shared_config_dir", return_value=shared_dir):
            config = config_manager.load_config()
            with open(shared_path, "r", encoding="utf-8") as stream:
                promoted = json.load(stream)

        self.assertEqual("10.0.0.20", config.host_ip)
        self.assertEqual("10.0.0.20", promoted["host_ip"])

    def test_corrupt_shared_config_falls_back_to_valid_user_mirror(self):
        user_dir = os.path.join(self._tmpdir, "fallback-user")
        shared_dir = os.path.join(self._tmpdir, "fallback-shared")
        os.makedirs(user_dir, exist_ok=True)
        os.makedirs(shared_dir, exist_ok=True)
        with open(os.path.join(user_dir, "client_config.json"), "w", encoding="utf-8") as stream:
            json.dump({"host_ip": "10.0.0.77"}, stream)
        with open(os.path.join(shared_dir, "client_config.json"), "w", encoding="utf-8") as stream:
            stream.write("{invalid")

        with patch("client.core.config_manager._config_dir", return_value=user_dir), \
             patch("client.core.config_manager._shared_config_dir", return_value=shared_dir):
            config = config_manager.load_config()

        self.assertEqual("10.0.0.77", config.host_ip)
        self.assertTrue(os.path.exists(os.path.join(shared_dir, "client_config.json.bak")))

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

    def test_concurrent_process_updates_preserve_all_devices(self):
        ctx = multiprocessing.get_context("spawn")
        start_event = ctx.Event()
        identities = [(f"{index:04x}", f"{index + 100:04x}") for index in range(1, 5)]
        workers = [
            ctx.Process(
                target=_add_client_device_in_process,
                args=(self._tmpdir, vid, pid, start_event),
            )
            for vid, pid in identities
        ]
        for worker in workers:
            worker.start()
        start_event.set()
        for worker in workers:
            worker.join(15)
            self.assertEqual(0, worker.exitcode)

        config = config_manager.load_config()
        observed = {(item.vid, item.pid) for item in config.permanent_devices}
        self.assertEqual(set(identities), observed)

    def test_missing_shared_root_falls_back_to_user_storage_without_creating_it(self):
        user_dir = os.path.join(self._tmpdir, "fallback-user")
        shared_dir = os.path.join(self._tmpdir, "missing-shared")
        os.makedirs(user_dir, exist_ok=True)

        with patch("client.core.config_manager._config_dir", return_value=user_dir), \
             patch("client.core.config_manager._shared_config_dir", return_value=shared_dir):
            config = config_manager.load_config()

        self.assertEqual("", config.api_key)
        self.assertFalse(os.path.exists(shared_dir))
        self.assertTrue(os.path.exists(os.path.join(user_dir, "client_config.json")))

    def test_storage_lock_error_returns_controlled_default(self):
        with patch.object(config_manager, "_config_storage_lock", side_effect=PermissionError("denied")):
            config = config_manager.load_config()

        self.assertEqual(config_manager.ClientConfig(), config)

    def test_read_oserror_returns_controlled_default(self):
        with patch.object(config_manager, "_read_config_file", side_effect=OSError("unreadable")):
            config = config_manager.load_config()

        self.assertEqual(config_manager.ClientConfig(), config)

    def test_shared_api_key_is_preserved_when_newer_user_mirror_is_promoted(self):
        user_dir = os.path.join(self._tmpdir, "api-key-user")
        shared_dir = os.path.join(self._tmpdir, "api-key-shared")
        os.makedirs(user_dir, exist_ok=True)
        os.makedirs(shared_dir, exist_ok=True)
        user_path = os.path.join(user_dir, "client_config.json")
        shared_path = os.path.join(shared_dir, "client_config.json")
        with open(shared_path, "w", encoding="utf-8") as stream:
            json.dump({"host_ip": "10.0.0.10", "api_key": "keep-me"}, stream)
        with open(user_path, "w", encoding="utf-8") as stream:
            json.dump({"host_ip": "10.0.0.20"}, stream)
        os.utime(shared_path, (1000, 1000))
        os.utime(user_path, (2000, 2000))

        with patch("client.core.config_manager._config_dir", return_value=user_dir), \
             patch("client.core.config_manager._shared_config_dir", return_value=shared_dir):
            config = config_manager.load_config()

        self.assertEqual("10.0.0.20", config.host_ip)
        self.assertEqual("keep-me", config.api_key)

    def test_atomic_config_write_flushes_and_fsyncs(self):
        path = os.path.join(self._tmpdir, "atomic", "client_config.json")
        with patch("client.core.config_manager.os.fsync") as fsync:
            config_manager._write_config_file(path, config_manager.ClientConfig().model_dump())

        fsync.assert_called_once()
        self.assertTrue(os.path.exists(path))
        self.assertEqual([], [name for name in os.listdir(os.path.dirname(path)) if name.endswith(".tmp")])
