import logging
import os
import re
import threading
import time
from typing import BinaryIO


logger = logging.getLogger(__name__)
_lock = threading.Lock()
_active: set[tuple[str, ...]] = set()
_process_locks: dict[tuple[str, ...], BinaryIO] = {}


def device_key(vid: str, pid: str, busid: str = "") -> tuple[str, ...]:
    if busid:
        return "busid", busid.lower()
    return "vidpid", vid.lower(), pid.lower()


def named_key(namespace: str, name: str) -> tuple[str, ...]:
    return "named", namespace.strip().lower(), name.strip().lower()


def _operation_lock_path(key: tuple[str, ...]) -> str:
    from shared.constants import CONFIG_DIR_NAME

    base = (
        os.environ.get("PROGRAMDATA")
        or os.environ.get("ALLUSERSPROFILE")
        or os.environ.get("APPDATA")
        or os.path.expanduser("~")
    )
    safe_key = "-".join(re.sub(r"[^a-zA-Z0-9_.-]", "_", part) for part in key)
    return os.path.join(base, CONFIG_DIR_NAME, "operation-locks", f"{safe_key}.lock")


def _try_lock_file(path: str) -> BinaryIO | None:
    """Acquire an inter-process lock; never manufacture a shared lock root."""
    stream: BinaryIO | None = None
    try:
        # ProgramData/operation-locks is provisioned by the installer.  A
        # missing or inaccessible parent must make the transport operation
        # fail closed instead of silently becoming thread-only.
        stream = open(path, "a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()

        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except (OSError, TimeoutError) as exc:
        logger.warning("Unable to acquire operation lock %s: %s", path, exc)
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        return None


def _unlock_file(stream: BinaryIO) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except OSError:
        logger.debug("Failed to unlock operation file", exc_info=True)
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _try_acquire_key(key: tuple[str, ...]) -> bool:
    with _lock:
        if key in _active:
            return False
        try:
            lock_path = _operation_lock_path(key)
            process_lock = _try_lock_file(lock_path)
        except (OSError, TimeoutError) as exc:
            logger.warning("Unable to resolve operation lock for %s: %s", key, exc)
            return False
        if process_lock is None:
            return False
        _process_locks[key] = process_lock
        _active.add(key)
        return True


def try_acquire(vid: str, pid: str, busid: str = "") -> bool:
    return _try_acquire_key(device_key(vid, pid, busid))


def try_acquire_named(namespace: str, name: str) -> bool:
    return _try_acquire_key(named_key(namespace, name))


def acquire_named(namespace: str, name: str, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if try_acquire_named(namespace, name):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


def _release_key(key: tuple[str, ...]) -> None:
    with _lock:
        _active.discard(key)
        process_lock = _process_locks.pop(key, None)
        if process_lock is not None:
            _unlock_file(process_lock)


def release(vid: str, pid: str, busid: str = "") -> None:
    _release_key(device_key(vid, pid, busid))


def release_named(namespace: str, name: str) -> None:
    _release_key(named_key(namespace, name))


def is_active(vid: str, pid: str, busid: str = "") -> bool:
    with _lock:
        return device_key(vid, pid, busid) in _active


def reset() -> None:
    """Deprecated compatibility hook; never releases another operation.

    Lock lifetime is owned by the matching release call.  A global reset can
    release a live transport lock while another thread/process is still
    mutating USB state, so it is intentionally a no-op.
    """
    logger.warning("operation_coordinator.reset() is deprecated and does not release active locks")
