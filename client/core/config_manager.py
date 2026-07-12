import json
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

from shared.constants import CLIENT_CONFIG_FILE, CONFIG_DIR_NAME
from shared.models import ClientConfig, ClientPermanentDevice

logger = logging.getLogger(__name__)


def _config_dir() -> str:
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    path = os.path.join(appdata, CONFIG_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _config_path() -> str:
    return os.path.join(_config_dir(), CLIENT_CONFIG_FILE)


def _shared_config_dir() -> str:
    """%ProgramData%\\USBRelay — readable/writable by SYSTEM and all local users."""
    programdata = os.environ.get("PROGRAMDATA")
    if not programdata:
        programdata = r"C:\ProgramData" if sys.platform == "win32" else tempfile.gettempdir()
    path = os.path.join(programdata, CONFIG_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def _programdata_dir() -> str:
    """Backward-compatible alias used by older tests and callers."""
    return _shared_config_dir()


def _shared_config_path() -> str:
    return os.path.join(_shared_config_dir(), CLIENT_CONFIG_FILE)


def _default_config() -> ClientConfig:
    return ClientConfig()


def _normalize_vid_pid(vid: str, pid: str) -> tuple[str, str]:
    return vid.lower(), pid.lower()


def _get_or_create_permanent_device(
    config: ClientConfig,
    vid: str,
    pid: str,
    description: str = "",
    force_auto_attach: bool = False,
) -> ClientPermanentDevice:
    vid_lower, pid_lower = _normalize_vid_pid(vid, pid)

    for dev in config.permanent_devices:
        if dev.vid.lower() == vid_lower and dev.pid.lower() == pid_lower:
            dev.vid = vid_lower
            dev.pid = pid_lower
            if force_auto_attach:
                dev.auto_attach = True
            if description:
                dev.description = description
            return dev

    dev = ClientPermanentDevice(
        vid=vid_lower,
        pid=pid_lower,
        description=description,
        auto_attach=True,
    )
    config.permanent_devices.append(dev)
    return dev


def _find_permanent_device(config: ClientConfig, vid: str, pid: str) -> ClientPermanentDevice | None:
    vid_lower, pid_lower = _normalize_vid_pid(vid, pid)
    for dev in config.permanent_devices:
        if dev.vid.lower() == vid_lower and dev.pid.lower() == pid_lower:
            dev.vid = vid_lower
            dev.pid = pid_lower
            return dev
    return None


def _normalize_config(config: ClientConfig) -> ClientConfig:
    for dev in config.permanent_devices:
        dev.vid = dev.vid.lower()
        dev.pid = dev.pid.lower()
    return config


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _backup_corrupted(filepath: str) -> None:
    backup_path = filepath + ".bak"
    try:
        shutil.copy2(filepath, backup_path)
        logger.info(f"Corrupted config backed up to {backup_path}")
    except Exception:
        pass


def load_config() -> ClientConfig:
    # Boot task runs as SYSTEM: APPDATA points to the system profile, not the
    # user's profile.  Use the ProgramData mirror written by the GUI instead.
    if "--headless" in sys.argv:
        path = _shared_config_path()
    else:
        path = _config_path()

    if not os.path.exists(path):
        config = _default_config()
        save_config(config)
        return config

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _normalize_config(ClientConfig(**data))
    except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        logger.error(f"Failed to load config from {path}: {exc}")
        _backup_corrupted(path)
        config = _default_config()
        save_config(config)
        return config


def save_config(config: ClientConfig) -> None:
    path = _config_path()
    tmp_path = path + ".tmp"
    try:
        data = config.model_dump()
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, path)
    except Exception as exc:
        logger.error(f"Failed to save config to {path}: {exc}")
        raise

    # Mirror to ProgramData so the boot task (SYSTEM) can read it pre-login.
    try:
        shared_path = _shared_config_path()
        shared_tmp = shared_path + ".tmp"
        with open(shared_tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(shared_tmp, shared_path)
        logger.debug(f"Config mirrored to shared path: {shared_path}")
    except Exception as exc:
        logger.debug(f"Could not mirror config to ProgramData (non-fatal): {exc}")


def add_permanent_device(vid: str, pid: str, description: str = "") -> None:
    config = load_config()
    _get_or_create_permanent_device(config, vid, pid, description, force_auto_attach=True)
    save_config(config)


def remove_permanent_device(vid: str, pid: str) -> None:
    config = load_config()
    vid_lower, pid_lower = _normalize_vid_pid(vid, pid)
    config.permanent_devices = [
        dev for dev in config.permanent_devices
        if not (dev.vid.lower() == vid_lower and dev.pid.lower() == pid_lower)
    ]
    save_config(config)


def is_permanent(vid: str, pid: str) -> bool:
    config = load_config()
    vid_lower, pid_lower = _normalize_vid_pid(vid, pid)
    return any(dev.vid.lower() == vid_lower and dev.pid.lower() == pid_lower for dev in config.permanent_devices)


def get_permanent_devices() -> list[ClientPermanentDevice]:
    config = load_config()
    return config.permanent_devices


def enable_scheduled_reconnect(
    vid: str,
    pid: str,
    interval_hours: int = 24,
    description: str = "",
) -> None:
    config = load_config()
    dev = _get_or_create_permanent_device(config, vid, pid, description, force_auto_attach=True)
    dev.scheduled_reconnect_enabled = True
    dev.scheduled_reconnect_interval_hours = max(1, interval_hours)
    dev.last_scheduled_reconnect_at = _now_utc_iso()
    save_config(config)


def update_scheduled_reconnect(
    vid: str,
    pid: str,
    interval_hours: int,
    description: str = "",
) -> None:
    config = load_config()
    dev = _get_or_create_permanent_device(config, vid, pid, description)
    dev.scheduled_reconnect_enabled = True
    dev.scheduled_reconnect_interval_hours = max(1, interval_hours)
    dev.last_scheduled_reconnect_at = _now_utc_iso()
    save_config(config)


def disable_scheduled_reconnect(vid: str, pid: str) -> None:
    config = load_config()
    dev = _find_permanent_device(config, vid, pid)
    if dev is None:
        return
    dev.scheduled_reconnect_enabled = False
    save_config(config)


def update_host_ip(ip: str) -> None:
    config = load_config()
    config.host_ip = ip
    save_config(config)


def update_host_port(port: int) -> None:
    config = load_config()
    config.host_port = port
    save_config(config)


def update_api_key(key: str) -> None:
    config = load_config()
    config.api_key = key
    save_config(config)


def update_poll_interval(seconds: int) -> None:
    config = load_config()
    config.poll_interval_seconds = max(1, seconds)
    save_config(config)


def update_autostart(enabled: bool) -> tuple[bool, bool]:
    config = load_config()
    config.autostart_with_windows = enabled
    save_config(config)

    if enabled:
        from .autostart_manager import register_startup
        exe = sys.executable
        if getattr(sys, "frozen", False):
            exe = sys.executable
        else:
            import os
            exe = os.path.join(os.path.dirname(__file__), "..", "main.py")
            exe = f'"{sys.executable}" "{os.path.abspath(exe)}"'
        return register_startup(exe)
    else:
        from .autostart_manager import unregister_startup
        unregister_startup()
        return True, True
