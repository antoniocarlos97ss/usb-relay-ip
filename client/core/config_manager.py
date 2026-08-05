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
    """Return the per-user configuration directory.

    APPDATA is user-owned storage and may be created by the application.  The
    shared ProgramData directory is deliberately handled separately: source
    runs must not manufacture a directory with installer-owned ACLs.
    """
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(appdata, CONFIG_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        logger.warning("Unable to create user config directory %s: %s", path, exc)
        fallback = os.path.join(os.path.expanduser("~"), CONFIG_DIR_NAME)
        if os.path.normcase(os.path.abspath(fallback)) != os.path.normcase(os.path.abspath(path)):
            try:
                os.makedirs(fallback, exist_ok=True)
                return fallback
            except OSError as fallback_exc:
                logger.warning("Unable to create fallback user config directory %s: %s", fallback, fallback_exc)
        # Return the intended path so the caller can fail in a controlled
        # manner without inventing another shared or world-writable location.
    return path


def _config_path() -> str:
    return os.path.join(_config_dir(), CLIENT_CONFIG_FILE)


def _shared_config_dir() -> str | None:
    """Return an installer-provisioned shared directory, without creating it.

    ``PROGRAMDATA`` is normally present for installed Windows processes.  If
    it is absent (for example, a Linux source run), there is no shared store;
    callers explicitly fall back to APPDATA instead of using ``/tmp``.
    """
    programdata = os.environ.get("PROGRAMDATA")
    if not programdata:
        logger.debug("PROGRAMDATA is unavailable; shared client storage is disabled")
        return None
    return os.path.join(programdata, CONFIG_DIR_NAME)


def _programdata_dir() -> str | None:
    """Backward-compatible alias used by older tests and callers."""
    return _shared_config_dir()


def _shared_config_path() -> str | None:
    shared_dir = _shared_config_dir()
    if shared_dir is None:
        return None
    return os.path.join(shared_dir, CLIENT_CONFIG_FILE)


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
def _config_file_lock(path: str, timeout: float, *, create_parent: bool):
    """Acquire an OS lock for ``path`` and yield its open stream."""
    parent = os.path.dirname(path) or "."
    if create_parent:
        os.makedirs(parent, exist_ok=True)
    deadline = time.monotonic() + max(0.1, timeout)
    stream = None
    try:
        stream = open(path, "a+b")
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
                        raise TimeoutError(f"Timed out locking {path}")
                    time.sleep(0.05)
            try:
                yield stream
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
                        raise TimeoutError(f"Timed out locking {path}")
                    time.sleep(0.05)
            try:
                yield stream
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        if stream is not None:
            try:
                stream.close()
            except OSError:
                logger.debug("Failed to close config lock %s", path, exc_info=True)


@contextmanager
def _config_storage_lock(timeout: float = 5.0):
    """Serialize config access, preferring shared storage and falling back safely.

    The context yields the config path protected by the lock.  Production
    callers must pass that path to the ``*_unlocked`` helpers; they must not
    acquire this lock again while it is held.
    """
    with _CONFIG_THREAD_LOCK:
        shared_path = _shared_config_path()
        if shared_path is not None:
            try:
                with _config_file_lock(
                    shared_path + ".lock", timeout, create_parent=False
                ):
                    yield shared_path
                    return
            except (PermissionError, OSError, TimeoutError) as exc:
                logger.warning(
                    "Shared client storage lock unavailable at %s; falling back to APPDATA: %s",
                    shared_path,
                    exc,
                )

        user_path = _config_path()
        try:
            with _config_file_lock(user_path + ".lock", timeout, create_parent=True):
                yield user_path
        except (PermissionError, OSError, TimeoutError) as exc:
            logger.error("User client storage lock unavailable at %s: %s", user_path, exc)
            raise


def _write_config_file(path: str, data: dict, *, create_parent: bool = True) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    if create_parent:
        os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            json.dump(data, stream, indent=2, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Failed to clean temporary config file %s", tmp_path, exc_info=True)


def _same_path(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _save_config_unlocked(config: ClientConfig, storage_path: str | None = None) -> bool:
    """Persist a snapshot without acquiring the storage lock again."""
    data = _normalize_config(config).model_dump()
    shared_path = _shared_config_path()
    try:
        user_path = _config_path()
    except OSError as exc:
        logger.warning("Unable to resolve user config path: %s", exc)
        user_path = None

    active_path = storage_path or shared_path or user_path
    if active_path is None:
        logger.error("No usable client configuration storage path is available")
        return False

    shared_ok = False
    if _same_path(active_path, shared_path):
        try:
            # The installer owns the shared directory and its ACL.  Never
            # create it here.
            _write_config_file(shared_path, data, create_parent=False)
            shared_ok = True
        except (OSError, TimeoutError) as exc:
            logger.error("Failed to save canonical config to %s: %s", shared_path, exc)
    else:
        try:
            _write_config_file(active_path, data, create_parent=True)
            return True
        except (OSError, TimeoutError) as exc:
            logger.error("Failed to save user config to %s: %s", active_path, exc)
            return False

    if user_path is None or _same_path(user_path, shared_path):
        return shared_ok
    try:
        _write_config_file(user_path, data, create_parent=True)
    except (OSError, TimeoutError) as exc:
        logger.warning("Could not mirror config to user profile: %s", exc)
        return shared_ok
    return shared_ok


def _read_config_file(path: str) -> ClientConfig:
    with open(path, "r", encoding="utf-8") as stream:
        return _normalize_config(ClientConfig(**json.load(stream)))


def _is_default_config(config: ClientConfig) -> bool:
    return config.model_dump() == _default_config().model_dump()


def _preserve_api_key(chosen: ClientConfig, other: ClientConfig) -> ClientConfig:
    if not chosen.api_key and other.api_key:
        chosen.api_key = other.api_key
    return chosen


def _try_read_config(path: str, *, label: str) -> ClientConfig | None:
    try:
        return _read_config_file(path)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        logger.error("Failed to load config from %s (%s): %s", path, label, exc)
        _backup_corrupted(path)
        return None


def _load_config_unlocked(storage_path: str | None = None) -> ClientConfig:
    """Load and, when possible, promote config without reacquiring the lock."""
    shared_path = _shared_config_path()
    user_path = _config_path()
    active_path = storage_path or shared_path or user_path
    use_shared = _same_path(active_path, shared_path)

    shared_config = _try_read_config(shared_path, label="shared") if use_shared and shared_path else None
    user_config = _try_read_config(user_path, label="user") if user_path and not _same_path(user_path, shared_path) else None

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

        # A user mirror may be newer while omitting the secret that SYSTEM
        # already knows.  Promotion must not erase that API key.
        other = shared_config if chosen is user_config else user_config
        _preserve_api_key(chosen, other)
        if use_shared:
            _save_config_unlocked(chosen, storage_path=active_path)
        return chosen

    if shared_config is not None:
        return shared_config

    if user_config is not None:
        if use_shared:
            _save_config_unlocked(user_config, storage_path=active_path)
        return user_config

    config = _default_config()
    _save_config_unlocked(config, storage_path=active_path)
    return config


def _load_user_config_without_lock() -> ClientConfig:
    try:
        user_path = _config_path()
        config = _try_read_config(user_path, label="fallback user")
        if config is None:
            config = _default_config()
        _save_config_unlocked(config, storage_path=user_path)
        return config
    except (OSError, TimeoutError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        logger.error("Unable to use fallback APPDATA client storage: %s", exc)
        return _default_config()


def load_config() -> ClientConfig:
    try:
        with _config_storage_lock() as storage_path:
            return _load_config_unlocked(storage_path)
    except (PermissionError, OSError, TimeoutError) as exc:
        logger.error("Client config storage unavailable; using controlled fallback: %s", exc)
        return _load_user_config_without_lock()


def save_config(config: ClientConfig) -> None:
    """Deprecated whole-document replacement; production uses locked mutators."""
    import warnings

    warnings.warn(
        "save_config() replaces a full snapshot and is deprecated; use update_config()",
        DeprecationWarning,
        stacklevel=2,
    )
    try:
        with _config_storage_lock() as storage_path:
            _save_config_unlocked(config, storage_path=storage_path)
    except (PermissionError, OSError, TimeoutError) as exc:
        logger.error("Client config save was not persisted: %s", exc)


def update_config(mutator: Callable[[ClientConfig], None]) -> ClientConfig:
    """Atomically load, mutate, and save the canonical shared configuration."""
    try:
        with _config_storage_lock() as storage_path:
            config = _load_config_unlocked(storage_path)
            mutator(config)
            _save_config_unlocked(config, storage_path=storage_path)
            return config
    except (PermissionError, OSError, TimeoutError) as exc:
        logger.error("Client config update was not persisted: %s", exc)
        return _load_user_config_without_lock()


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
