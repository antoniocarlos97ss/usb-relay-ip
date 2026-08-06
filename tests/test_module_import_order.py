import subprocess
import sys
import unittest
from itertools import permutations
from pathlib import Path


ROOT = Path(__file__).parents[1]
QT_STUB_MODULES = (
    "tests.test_usbip_worker",
    "tests.test_pnp_recovery",
    "tests.test_device_monitor",
    "tests.test_scheduled_reconnect",
    "tests.test_client_scheduled_reconnect_ui",
    "tests.test_client_cleanup",
)
ORDER_PAIRS = tuple(permutations(QT_STUB_MODULES, 2))


class TestModuleImportOrder(unittest.TestCase):
    def test_pyqt_test_modules_import_in_all_ordered_pairs(self):
        for first, second in ORDER_PAIRS:
            with self.subTest(first=first, second=second):
                result = subprocess.run(
                    [sys.executable, "-c", f"import {first}; import {second}"],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                self.assertEqual(
                    0,
                    result.returncode,
                    msg=(
                        f"Import order failed: {first} -> {second}\n"
                        f"stdout:\n{result.stdout}\n"
                        f"stderr:\n{result.stderr}"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
