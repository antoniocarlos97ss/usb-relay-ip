import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable

from shared.constants import CLIENT_CONFIG_FILE, CONFIG_DIR_NAME
from shared.models import ClientConfig, ClientPermanentDevice

logger = logging.getLogger(__name__)
_CONFIG_THREAD_LOCK = threading.RLock()


def _config_dir() -> str:
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    path = os.path.join(appdata, CONFIG_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _config_path() -> str:
    return os.path.join(_config_dir(), CLIENT_CONFIG_FILE)


def _shared_config_dir() -> str:
    """Return the ProgramData directory shared by SYSTEM and the interactive GUI."""
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


def _find_permanent_device(
    config: ClientConfig, vid: str, pid: str
) -> ClientPermanentDevice | None:
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
        logger.info("Corrupted config backed up to %s", backup_path)
    except Exception:
        pass


@contextmanager
def _config_storage_lock(timeout: float = 5.0):
    lock_path = _shared_config_path() + ".lock"
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    deadline = time.monotonic() + max(0.1, timeout)

    with _CONFIG_THREAD_LOCK, open(lock_path, "a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out locking {lock_path}")
                    time.sleep(0.05)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out locking {lock_path}")
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _write_config_file(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, default=str)
    os.replace(tmp_path, path)


def _save_config_unlocked(config: ClientConfig) -> None:
    data = _normalize_config(config).model_dump()
    shared_path = _shared_config_path()
    try:
        _write_config_file(shared_path, data)
    except Exception as exc:
        logger.error("Failed to save canonical config to %s: %s", shared_path, exc)
        raise

    user_path = _config_path()
    if os.path.normcase(os.path.abspath(user_path)) == os.path.normcase(
        os.path.abspath(shared_path)
    ):
        return
    try:
        _write_config_file(user_path, data)
    except Exception as exc:
        logger.warning("Could not mirror config to user profile: %s", exc)


def _read_config_file(path: str) -> ClientConfig:
    with open(path, "r", encoding="utf-8") as stream:
        return _normalize_config(ClientConfig(**json.load(stream)))


def _is_default_config(config: ClientConfig) -> bool:
    return config.model_dump() == _default_config().model_dump()


def _load_config_unlocked() -> ClientConfig:
    shared_path = _shared_config_path()
    user_path = _config_path()
    same_path = os.path.normcase(os.path.abspath(user_path)) == os.path.normcase(
        os.path.abspath(shared_path)
    )

    shared_config = None
    if os.path.exists(shared_path):
        try:
            shared_config = _read_config_file(shared_path)
        except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
            logger.error("Failed to load config from %s: %s", shared_path, exc)
            _backup_corrupted(shared_path)

    user_config = None
    if not same_path and os.path.exists(user_path):
        try:
            user_config = _read_config_file(user_path)
        except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
            logger.error("Failed to load config from %s: %s", user_path, exc)
            _backup_corrupted(user_path)

    if shared_config is not None and user_config is not None:
        shared_dump = shared_config.model_dump()
        user_dump = user_config.model_dump()
        if shared_dump == user_dump:
            return shared_config

        shared_default = _is_default_config(shared_config)
        user_default = _is_default_config(user_config)
        if shared_default != user_default:
            chosen = user_config if not user_default else shared_config
        else:
            try:
                shared_mtime = os.stat(shared_path).st_mtime_ns
            except OSError:
                shared_mtime = 0
            try:
                user_mtime = os.stat(user_path).st_mtime_ns
            except OSError:
                user_mtime = 0
            chosen = user_config if user_mtime > shared_mtime else shared_config

        _save_config_unlocked(chosen)
        return chosen

    if shared_config is not None:
        return shared_config

    if user_config is not None:
        _save_config_unlocked(user_config)
        return user_config

    config = _default_config()
    _save_config_unlocked(config)
    return config


def load_config() -> ClientConfig:
    with _config_storage_lock():
        return _load_config_unlocked()


def save_config(config: ClientConfig) -> None:
    """Deprecated whole-document replacement; production uses locked mutators."""
    import warnings

    warnings.warn(
        "save_config() replaces a full snapshot and is deprecated; use update_config()",
        DeprecationWarning,
        stacklevel=2,
    )
    with _config_storage_lock():
        _save_config_unlocked(config)


def update_config(mutator: Callable[[ClientConfig], None]) -> ClientConfig:
    """Atomically load, mutate, and save the canonical shared configuration."""
    with _config_storage_lock():
        config = _load_config_unlocked()
        mutator(config)
        _save_config_unlocked(config)
        return config


def add_permanent_device(vid: str, pid: str, description: str = "") -> None:
    update_config(
        lambda config: _get_or_create_permanent_device(
            config, vid, pid, description, force_auto_attach=True
        )
    )


def remove_permanent_device(vid: str, pid: str) -> None:
    vid_lower, pid_lower = _normalize_vid_pid(vid, pid)

    def mutate(config: ClientConfig) -> None:
        config.permanent_devices = [
            dev
            for dev in config.permanent_devices
            if not (dev.vid.lower() == vid_lower and dev.pid.lower() == pid_lower)
        ]

    update_config(mutate)


def is_permanent(vid: str, pid: str) -> bool:
    config = load_config()
    vid_lower, pid_lower = _normalize_vid_pid(vid, pid)
    return any(
        dev.vid.lower() == vid_lower and dev.pid.lower() == pid_lower
        for dev in config.permanent_devices
    )


def get_permanent_devices() -> list[ClientPermanentDevice]:
    return load_config().permanent_devices


def enable_scheduled_reconnect(
    vid: str,
    pid: str,
    interval_hours: int = 24,
    description: str = "",
) -> None:
    def mutate(config: ClientConfig) -> None:
        dev = _get_or_create_permanent_device(
            config, vid, pid, description, force_auto_attach=True
        )
        dev.scheduled_reconnect_enabled = True
        dev.scheduled_reconnect_interval_hours = max(1, interval_hours)
        dev.last_scheduled_reconnect_at = _now_utc_iso()

    update_config(mutate)


def update_scheduled_reconnect(
    vid: str,
    pid: str,
    interval_hours: int,
    description: str = "",
) -> None:
    def mutate(config: ClientConfig) -> None:
        dev = _get_or_create_permanent_device(config, vid, pid, description)
        dev.scheduled_reconnect_enabled = True
        dev.scheduled_reconnect_interval_hours = max(1, interval_hours)
        dev.last_scheduled_reconnect_at = _now_utc_iso()

    update_config(mutate)


def disable_scheduled_reconnect(vid: str, pid: str) -> None:
    def mutate(config: ClientConfig) -> None:
        dev = _find_permanent_device(config, vid, pid)
        if dev is not None:
            dev.scheduled_reconnect_enabled = False

    update_config(mutate)


def mark_scheduled_reconnect_completed(vid: str, pid: str, timestamp: str) -> None:
    def mutate(config: ClientConfig) -> None:
        dev = _find_permanent_device(config, vid, pid)
        if dev is not None:
            dev.last_scheduled_reconnect_at = timestamp

    update_config(mutate)


def update_host_ip(ip: str) -> None:
    update_config(lambda config: setattr(config, "host_ip", ip))


def update_host_port(port: int) -> None:
    update_config(lambda config: setattr(config, "host_port", port))


def update_api_key(key: str) -> None:
    update_config(lambda config: setattr(config, "api_key", key))


def update_poll_interval(seconds: int) -> None:
    update_config(
        lambda config: setattr(config, "poll_interval_seconds", max(1, seconds))
    )


def update_autostart(enabled: bool) -> tuple[bool, bool]:
    update_config(lambda config: setattr(config, "autostart_with_windows", enabled))

    if enabled:
        from .autostart_manager import register_startup

        exe = sys.executable
        arguments = None
        if not getattr(sys, "frozen", False):
            script = os.path.join(os.path.dirname(__file__), "..", "main.py")
            arguments = [os.path.abspath(script)]
        return register_startup(exe, arguments)

    from .autostart_manager import unregister_startup

    unregister_startup()
    return True, True
