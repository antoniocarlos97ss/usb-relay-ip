"""Client core package.

Expose commonly-used submodules for consistent imports and easier test patching.
"""

from . import autostart_manager, config_manager, device_poller, usbip_worker, usbip_wrapper

__all__ = [
    "autostart_manager",
    "config_manager",
    "device_poller",
    "usbip_worker",
    "usbip_wrapper",
]
