"""Host package root.

Expose the main subpackages so unittest.mock.patch can resolve dotted names
like `host.core.usbipd_wrapper` reliably in tests.
"""

from . import api, core, gui

__all__ = ["api", "core", "gui"]
