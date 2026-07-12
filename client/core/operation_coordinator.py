import threading


_lock = threading.Lock()
_active: set[tuple[str, ...]] = set()


def device_key(vid: str, pid: str, busid: str = "") -> tuple[str, ...]:
    if busid:
        return "busid", busid.lower()
    return "vidpid", vid.lower(), pid.lower()


def try_acquire(vid: str, pid: str, busid: str = "") -> bool:
    key = device_key(vid, pid, busid)
    with _lock:
        if key in _active:
            return False
        _active.add(key)
        return True


def release(vid: str, pid: str, busid: str = "") -> None:
    with _lock:
        _active.discard(device_key(vid, pid, busid))


def is_active(vid: str, pid: str, busid: str = "") -> bool:
    with _lock:
        return device_key(vid, pid, busid) in _active


def reset() -> None:
    with _lock:
        _active.clear()
