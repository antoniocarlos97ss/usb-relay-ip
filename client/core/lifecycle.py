"""Pure lifecycle identity helpers shared by GUI shutdown paths and tests."""

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class LiveShutdownSession:
    """A live local USB/IP session with an unambiguous device identity."""

    port: int
    busid: str
    vid: str
    pid: str


def _identity(device) -> tuple[str, str, str] | None:
    busid = str(getattr(device, "busid", "") or "").strip()
    vid = str(getattr(device, "vid", "") or "").strip().lower()
    pid = str(getattr(device, "pid", "") or "").strip().lower()
    if not busid or not vid or not pid:
        return None
    return busid, vid, pid


def _matching_identity(devices: Iterable, local_key: tuple[str, str, str]):
    return [device for device in devices if _identity(device) == local_key]


def resolve_live_shutdown_sessions(
    live_devices: Iterable,
    *,
    host_devices: Iterable,
    cached_devices: Iterable,
    host_reachable: bool,
) -> tuple[list[LiveShutdownSession], list[str]]:
    """Resolve live local sessions before issuing shutdown detach commands.

    The host is authoritative while reachable.  When it is unreachable, a
    unique cache identity is acceptable as a bounded fallback; ambiguity or a
    missing identity is rejected.  No caller should mutate a session that this
    function does not return.
    """
    host_devices = list(host_devices or [])
    cached_devices = list(cached_devices or [])
    sessions: list[LiveShutdownSession] = []
    rejected: list[str] = []
    live_devices = list(live_devices or [])
    busid_counts: dict[str, int] = {}
    for local in live_devices:
        busid = str(getattr(local, "busid", "") or "").strip()
        if busid:
            busid_counts[busid] = busid_counts.get(busid, 0) + 1

    rejected_duplicate_busids: set[str] = set()
    for local in live_devices:
        local_key = _identity(local)
        busid = str(getattr(local, "busid", "") or "").strip()
        if local_key is None:
            rejected.append(f"{busid or '<unknown>'}: local identity unavailable")
            continue
        _, vid, pid = local_key
        if busid_counts.get(busid, 0) != 1:
            if busid not in rejected_duplicate_busids:
                rejected.append(f"{busid}: ambiguous duplicate live session")
                rejected_duplicate_busids.add(busid)
            continue

        if host_reachable:
            host_matches = [
                device for device in host_devices
                if str(getattr(device, "busid", "") or "").strip() == busid
            ]
            if len(host_matches) != 1:
                rejected.append(
                    f"{busid}: identity changed or disappeared on reachable host"
                )
                continue
            host_key = _identity(host_matches[0])
            if host_key != local_key:
                rejected.append(f"{busid}: identity changed on reachable host")
                continue
        else:
            cache_matches = _matching_identity(cached_devices, local_key)
            if len(cache_matches) != 1:
                if len(cache_matches) > 1:
                    reason = "ambiguous cached identity"
                else:
                    reason = "host unreachable and cached identity unavailable"
                rejected.append(f"{busid}: {reason}")
                continue

        try:
            port = int(getattr(local, "port"))
        except (TypeError, ValueError):
            rejected.append(f"{busid}: local port unavailable")
            continue
        if port < 1 or port > 255:
            rejected.append(f"{busid}: local port invalid")
            continue
        sessions.append(LiveShutdownSession(port=port, busid=busid, vid=vid, pid=pid))

    return sessions, rejected
