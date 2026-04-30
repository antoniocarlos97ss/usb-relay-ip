import json
import logging
import os
import shutil

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


def _default_config() -> ClientConfig:
    return ClientConfig()


def _backup_corrupted(filepath: str) -> None:
    backup_path = filepath + ".bak"
    try:
        shutil.copy2(filepath, backup_path)
        logger.info(f"Corrupted config backed up to {backup_path}")
    except Exception:
        pass


def load_config() -> ClientConfig:
    path = _config_path()
    if not os.path.exists(path):
        config = _default_config()
        save_config(config)
        return config

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return ClientConfig(**data)
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


def add_permanent_device(vid: str, pid: str, description: str = "") -> None:
    config = load_config()
    vid_lower = vid.lower()
    pid_lower = pid.lower()

    for dev in config.permanent_devices:
        if dev.vid == vid_lower and dev.pid == pid_lower:
            dev.auto_attach = True
            dev.description = description or dev.description
            save_config(config)
            return

    config.permanent_devices.append(ClientPermanentDevice(
        vid=vid_lower,
        pid=pid_lower,
        description=description,
        auto_attach=True,
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


def get_permanent_devices() -> list[ClientPermanentDevice]:
    config = load_config()
    return config.permanent_devices


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


def update_autostart(enabled: bool) -> None:
    config = load_config()
    config.autostart_with_windows = enabled
    save_config(config)
