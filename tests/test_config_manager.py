import os
import tempfile
import unittest
from unittest.mock import patch

from host.core import config_manager
from shared.models import HostConfig


class TestAutoShareConfig(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._patcher = patch("host.core.config_manager._config_dir", return_value=self._tmpdir)
        self._patcher_pd = patch("host.core.config_manager._programdata_dir", return_value=self._tmpdir)
        self._patcher.start()
        self._patcher_pd.start()
        # Remove any cached config so each test starts fresh
        config_path = os.path.join(self._tmpdir, "host_config.json")
        if os.path.exists(config_path):
            os.remove(config_path)

    def tearDown(self):
        self._patcher.stop()
        self._patcher_pd.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_auto_share_all_defaults_false(self):
        config = config_manager.load_config()
        self.assertFalse(config.auto_share_all)
        self.assertEqual(config.auto_share_exclude, [])

    def test_update_auto_share_all(self):
        config_manager.update_auto_share_all(True)
        config = config_manager.load_config()
        self.assertTrue(config.auto_share_all)

        config_manager.update_auto_share_all(False)
        config = config_manager.load_config()
        self.assertFalse(config.auto_share_all)

    def test_add_remove_exclusion(self):
        config_manager.add_auto_share_exclusion("046d", "c31c")
        config = config_manager.load_config()
        self.assertIn("046d:c31c", config.auto_share_exclude)

        config_manager.remove_auto_share_exclusion("046d", "c31c")
        config = config_manager.load_config()
        self.assertNotIn("046d:c31c", config.auto_share_exclude)

    def test_is_auto_share_excluded(self):
        config_manager.add_auto_share_exclusion("046d", "c31c")
        self.assertTrue(config_manager.is_auto_share_excluded("046d", "c31c"))
        self.assertTrue(config_manager.is_auto_share_excluded("046D", "C31C"))
        self.assertFalse(config_manager.is_auto_share_excluded("1234", "5678"))

    def test_exclusion_normalizes_uppercase(self):
        config_manager.add_auto_share_exclusion("046D", "C31C")
        config = config_manager.load_config()
        self.assertIn("046d:c31c", config.auto_share_exclude)

    def test_old_config_loads_with_new_defaults(self):
        config_path = os.path.join(self._tmpdir, "host_config.json")
        import json
        with open(config_path, "w") as f:
            json.dump({"api_port": 9999}, f)
        config = config_manager.load_config()
        self.assertEqual(config.api_port, 9999)
        self.assertFalse(config.auto_share_all)
        self.assertEqual(config.auto_share_exclude, [])

    def test_add_duplicate_exclusion_ignored(self):
        config_manager.add_auto_share_exclusion("046d", "c31c")
        config_manager.add_auto_share_exclusion("046d", "c31c")
        config = config_manager.load_config()
        self.assertEqual(config.auto_share_exclude.count("046d:c31c"), 1)
