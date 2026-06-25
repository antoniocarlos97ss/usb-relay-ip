"""Client package root.

Expose the main subpackages so unittest.mock.patch can resolve dotted names
like `client.core.device_poller` reliably in tests.
"""

from . import api, core, gui

__all__ = ["api", "core", "gui"]
