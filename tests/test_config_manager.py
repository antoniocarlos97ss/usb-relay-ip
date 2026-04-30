import json
import os
import tempfile
import unittest
from unittest.mock import patch

from shared.models import ClientConfig, HostConfig


class TestHostConfigManager(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        patch("host.core.config_manager._config_dir", return_value=self.tempdir).start()
        patch("host.core.config_manager._config_path",
              return_value=os.path.join(self.tempdir, "host_config.json")).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_load_config_creates_default(self):
        from host.core.config_manager import load_config
        config = load_config()
        self.assertEqual(config.api_port, 5757)
        self.assertEqual(config.api_key, "")
        self.assertEqual(config.poll_interval_seconds, 5)
        self.assertFalse(config.autostart_as_service)
        self.assertEqual(len(config.permanent_devices), 0)

    def test_save_and_load_config(self):
        from host.core.config_manager import load_config, save_config
        config = load_config()
        config.api_port = 9999
        config.api_key = "secret"
        save_config(config)

        loaded = load_config()
        self.assertEqual(loaded.api_port, 9999)
        self.assertEqual(loaded.api_key, "secret")

    def test_add_permanent_device(self):
        from host.core.config_manager import add_permanent_device, load_config
        add_permanent_device("046d", "c31c", "Logitech Keyboard", "1-5")
        config = load_config()
        self.assertEqual(len(config.permanent_devices), 1)
        self.assertEqual(config.permanent_devices[0].vid, "046d")
        self.assertEqual(config.permanent_devices[0].pid, "c31c")
        self.assertTrue(config.permanent_devices[0].auto_bind)

    def test_add_permanent_device_duplicate(self):
        from host.core.config_manager import add_permanent_device, load_config
        add_permanent_device("046d", "c31c", "Keyboard")
        add_permanent_device("046d", "c31c", "Keyboard V2")
        config = load_config()
        self.assertEqual(len(config.permanent_devices), 1)
        self.assertEqual(config.permanent_devices[0].description, "Keyboard V2")

    def test_remove_permanent_device(self):
        from host.core.config_manager import add_permanent_device, remove_permanent_device, load_config
        add_permanent_device("046d", "c31c")
        add_permanent_device("0951", "1666")
        remove_permanent_device("046d", "c31c")
        config = load_config()
        self.assertEqual(len(config.permanent_devices), 1)
        self.assertEqual(config.permanent_devices[0].vid, "0951")

    def test_is_permanent(self):
        from host.core.config_manager import add_permanent_device, is_permanent
        add_permanent_device("046d", "c31c")
        self.assertTrue(is_permanent("046d", "c31c"))
        self.assertFalse(is_permanent("0000", "0000"))

    def test_is_permanent_case_insensitive(self):
        from host.core.config_manager import add_permanent_device, is_permanent
        add_permanent_device("046D", "C31C")
        self.assertTrue(is_permanent("046d", "c31c"))

    def test_corrupted_config_resets(self):
        from host.core.config_manager import load_config
        config_path = os.path.join(self.tempdir, "host_config.json")
        with open(config_path, "w") as f:
            f.write("{corrupted_json")

        config = load_config()
        self.assertEqual(config.api_port, 5757)

    def test_update_single_fields(self):
        from host.core.config_manager import (
            load_config,
            update_api_port,
            update_api_key,
            update_poll_interval,
            update_autostart,
        )
        update_api_port(9000)
        update_api_key("newkey")
        update_poll_interval(10)
        update_autostart(True)

        config = load_config()
        self.assertEqual(config.api_port, 9000)
        self.assertEqual(config.api_key, "newkey")
        self.assertEqual(config.poll_interval_seconds, 10)
        self.assertTrue(config.autostart_as_service)


class TestClientConfigManager(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        patch("client.core.config_manager._config_dir", return_value=self.tempdir).start()
        patch("client.core.config_manager._config_path",
              return_value=os.path.join(self.tempdir, "client_config.json")).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_load_config_creates_default(self):
        from client.core.config_manager import load_config
        config = load_config()
        self.assertEqual(config.host_ip, "")
        self.assertEqual(config.host_port, 5757)
        self.assertEqual(config.poll_interval_seconds, 10)

    def test_save_and_load_config(self):
        from client.core.config_manager import load_config, save_config
        config = load_config()
        config.host_ip = "192.168.1.10"
        config.host_port = 8888
        save_config(config)

        loaded = load_config()
        self.assertEqual(loaded.host_ip, "192.168.1.10")
        self.assertEqual(loaded.host_port, 8888)

    def test_add_permanent_device(self):
        from client.core.config_manager import add_permanent_device, load_config
        add_permanent_device("046d", "c31c", "Logitech Keyboard")
        config = load_config()
        self.assertEqual(len(config.permanent_devices), 1)
        self.assertTrue(config.permanent_devices[0].auto_attach)

    def test_remove_permanent_device(self):
        from client.core.config_manager import add_permanent_device, remove_permanent_device, load_config
        add_permanent_device("046d", "c31c")
        remove_permanent_device("046d", "c31c")
        config = load_config()
        self.assertEqual(len(config.permanent_devices), 0)

    def test_is_permanent(self):
        from client.core.config_manager import add_permanent_device, is_permanent
        add_permanent_device("046d", "c31c")
        self.assertTrue(is_permanent("046d", "c31c"))
        self.assertFalse(is_permanent("0000", "0000"))

    def test_corrupted_config_resets(self):
        from client.core.config_manager import load_config
        config_path = os.path.join(self.tempdir, "client_config.json")
        with open(config_path, "w") as f:
            f.write("not valid json")

        config = load_config()
        self.assertEqual(config.host_port, 5757)

    def test_update_single_fields(self):
        from client.core.config_manager import (
            load_config,
            update_host_ip,
            update_host_port,
            update_api_key,
            update_poll_interval,
            update_autostart,
        )
        update_host_ip("10.0.0.1")
        update_host_port(7777)
        update_api_key("secret123")
        update_poll_interval(15)
        update_autostart(True)

        config = load_config()
        self.assertEqual(config.host_ip, "10.0.0.1")
        self.assertEqual(config.host_port, 7777)
        self.assertEqual(config.api_key, "secret123")
        self.assertEqual(config.poll_interval_seconds, 15)
        self.assertTrue(config.autostart_with_windows)


if __name__ == "__main__":
    unittest.main()
