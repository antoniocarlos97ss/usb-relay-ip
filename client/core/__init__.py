"""Client core package.

Lazy-load submodules to avoid requiring PyQt6 at import time.
"""

__all__ = [
    "autostart_manager",
    "config_manager",
    "device_poller",
    "scheduled_reconnect",
    "usbip_worker",
    "usbip_wrapper",
]


def __getattr__(name: str):
    if name in __all__:
        import importlib
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
