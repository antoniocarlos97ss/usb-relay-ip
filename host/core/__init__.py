"""Host core package.

Expose commonly-patched submodules so tests and application code can import
`host.core.<module>` consistently.
"""

from . import autostart_manager, config_manager, device_monitor, service_monitor, usbipd_wrapper

__all__ = [
    "autostart_manager",
    "config_manager",
    "device_monitor",
    "service_monitor",
    "usbipd_wrapper",
]
