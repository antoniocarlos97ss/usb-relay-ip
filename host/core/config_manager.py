import json
import logging
import os
import shutil
import sys
from typing import Optional

from shared.constants import CONFIG_DIR_NAME, HOST_CONFIG_FILE
from shared.models import HostConfig, PermanentDevice

logger = logging.getLogger(__name__)


def _config_dir() -> str:
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    path = os.path.join(appdata, CONFIG_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _programdata_dir() -> str:
    pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    path = os.path.join(pd, CONFIG_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _config_path() -> str:
    # When running as SYSTEM (headless boot task) %APPDATA% is the SYSTEM
    # profile, not the user's.  Read from ProgramData instead so the headless
    # process sees the same config as the GUI process.
    if "--headless" in sys.argv:
        return os.path.join(_programdata_dir(), HOST_CONFIG_FILE)
    return os.path.join(_config_dir(), HOST_CONFIG_FILE)


def _default_config() -> HostConfig:
    return HostConfig()


def _backup_corrupted(filepath: str) -> None:
    backup_path = filepath + ".bak"
    try:
        shutil.copy2(filepath, backup_path)
        logger.info(f"Corrupted config backed up to {backup_path}")
    except Exception:
        pass


def load_config() -> HostConfig:
    path = _config_path()
    if not os.path.exists(path):
        config = _default_config()
        save_config(config)
        return config

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return HostConfig(**data)
    except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        logger.error(f"Failed to load config from {path}: {exc}")
        _backup_corrupted(path)
        config = _default_config()
        save_config(config)
        return config


def save_config(config: HostConfig) -> None:
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

    # Mirror to ProgramData so the headless boot task (running as SYSTEM)
    # reads the same config as the GUI process.
    if not "--headless" in sys.argv:
        try:
            mirror_path = os.path.join(_programdata_dir(), HOST_CONFIG_FILE)
            mirror_tmp = mirror_path + ".tmp"
            with open(mirror_tmp, "w", encoding="utf-8") as f:
                json.dump(config.model_dump(), f, indent=2, default=str)
            os.replace(mirror_tmp, mirror_path)
        except Exception as exc:
            logger.warning(f"Failed to mirror config to ProgramData: {exc}")


def add_permanent_device(vid: str, pid: str, description: str = "", busid_hint: str = "") -> None:
    config = load_config()
    vid_lower = vid.lower()
    pid_lower = pid.lower()

    for dev in config.permanent_devices:
        if dev.vid == vid_lower and dev.pid == pid_lower:
            dev.auto_bind = True
            dev.description = description or dev.description
            if busid_hint:
                dev.busid_hint = busid_hint
            save_config(config)
            return

    config.permanent_devices.append(PermanentDevice(
        vid=vid_lower,
        pid=pid_lower,
        description=description,
        busid_hint=busid_hint,
        auto_bind=True,
    ))
    save_config(config)


def remove_permanent_device(vid: str, pid: str) -> None:
    config = load_config()
    vid_lower = vid.lower()
    pid_lower = pid.lower()
    config.permanent_devices = [
        dev for dev in config.permanent_devices
        if not (dev.vid == vid_lower and dev.pid == pid_lower)
    ]
    save_config(config)


def is_permanent(vid: str, pid: str) -> bool:
    config = load_config()
    vid_lower = vid.lower()
    pid_lower = pid.lower()
    return any(dev.vid == vid_lower and dev.pid == pid_lower for dev in config.permanent_devices)


def get_permanent_devices() -> list[PermanentDevice]:
    config = load_config()
    return config.permanent_devices


def update_api_port(port: int) -> None:
    config = load_config()
    config.api_port = port
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
    config.autostart_as_service = enabled
    save_config(config)

    if enabled:
        from .autostart_manager import register_startup
        exe = sys.executable
        if not getattr(sys, "frozen", False):
            exe = os.path.join(os.path.dirname(__file__), "..", "main.py")
            exe = f'"{sys.executable}" "{os.path.abspath(exe)}"'
        return register_startup(exe)
    else:
        from .autostart_manager import unregister_startup
        unregister_startup()
        return True, True
