import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass


logger = logging.getLogger(__name__)

_VID_PID_RE = re.compile(r"VID_([0-9A-F]{4}).*PID_([0-9A-F]{4})", re.IGNORECASE)
_QUERY_TEMPLATE = r"""
$hasPnpProps = __INCLUDE_PROPS__ -and ($null -ne (Get-Command Get-PnpDeviceProperty -ErrorAction SilentlyContinue))
$devices = Get-CimInstance -ClassName Win32_PnPEntity | Where-Object {
    $_.PNPDeviceID -like 'USB\*' -or $_.PNPDeviceID -like 'ROOT\USBIP*'
}
$items = foreach ($device in $devices) {
    $parent = $null
    $containerId = $null
    $locationPaths = @()
    $locationInfo = $null
    if ($hasPnpProps) {
        try { $parent = (Get-PnpDeviceProperty -InstanceId $device.PNPDeviceID -KeyName 'DEVPKEY_Device_Parent' -ErrorAction Stop).Data } catch {}
        try { $containerId = (Get-PnpDeviceProperty -InstanceId $device.PNPDeviceID -KeyName 'DEVPKEY_Device_ContainerId' -ErrorAction Stop).Data } catch {}
        try { $locationPaths = @((Get-PnpDeviceProperty -InstanceId $device.PNPDeviceID -KeyName 'DEVPKEY_Device_LocationPaths' -ErrorAction Stop).Data) } catch {}
        try { $locationInfo = (Get-PnpDeviceProperty -InstanceId $device.PNPDeviceID -KeyName 'DEVPKEY_Device_LocationInfo' -ErrorAction Stop).Data } catch {}
    }

    [pscustomobject]@{
        PNPDeviceID = $device.PNPDeviceID
        Name = $device.Name
        ConfigManagerErrorCode = $device.ConfigManagerErrorCode
        Status = $device.Status
        ClassGuid = $device.ClassGuid
        Manufacturer = $device.Manufacturer
        Service = $device.Service
        Parent = $parent
        ContainerId = $containerId
        LocationPaths = @($locationPaths)
        LocationInfo = $locationInfo
    }
}
@($items) | ConvertTo-Json -Compress -Depth 4
""".strip()
_QUERY_FULL = _QUERY_TEMPLATE.replace("__INCLUDE_PROPS__", "$true")
_QUERY_FAST = _QUERY_TEMPLATE.replace("__INCLUDE_PROPS__", "$false")

_USB_UNKNOWN_PREFIX = "USB\\UNKNOWN"
_USBIP_ROOT_PREFIX = "ROOT\\USBIP"
# Windows re-enumerates a device whose descriptor read failed (Code 43,
# "Device Descriptor Request Failed") as USB\VID_0000&PID_0002 or with a
# DEVICE_DESCRIPTOR_FAILURE hardware id, replacing the original devnode.
_DESCRIPTOR_FAILURE_PREFIX = "USB\\VID_0000&PID_0002"
_DESCRIPTOR_FAILURE_MARKER = "DEVICE_DESCRIPTOR_FAILURE"
_SESSION_LOCK = threading.Lock()
_SESSION_CORRELATIONS: dict[str, "SessionCorrelation"] = {}
_SESSION_CORRELATIONS_LOADED = False
_SESSION_FILE = "pnp_sessions.json"
_QUERY_LOCK = threading.Lock()
_QUERY_PROCESSES: set[subprocess.Popen] = set()
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class PnpDeviceStatus:
    instance_id: str
    name: str
    problem_code: int
    status: str
    vid: str = ""
    pid: str = ""
    parent_instance_id: str = ""
    container_id: str = ""
    location_paths: tuple[str, ...] = ()
    location_info: str = ""
    service: str = ""
    class_guid: str = ""
    manufacturer: str = ""


@dataclass(frozen=True)
class PnpSnapshot:
    devices: tuple[PnpDeviceStatus, ...]
    observed_at: float


@dataclass(frozen=True)
class SessionCorrelation:
    busid: str
    vid: str
    pid: str
    instance_ids: tuple[str, ...]
    observed_at: float
    basis: str


def _normalize_instance_id(value: str) -> str:
    return value.strip().upper()


def _session_path() -> str:
    from shared.constants import CONFIG_DIR_NAME

    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    return os.path.join(base, CONFIG_DIR_NAME, _SESSION_FILE)


def _load_session_correlations_locked() -> None:
    global _SESSION_CORRELATIONS_LOADED
    if _SESSION_CORRELATIONS_LOADED:
        return
    try:
        with open(_session_path(), "r", encoding="utf-8") as stream:
            raw = json.load(stream)
        for item in raw:
            correlation = SessionCorrelation(
                busid=str(item["busid"]),
                vid=str(item["vid"]).lower(),
                pid=str(item["pid"]).lower(),
                instance_ids=tuple(_normalize_instance_id(value) for value in item["instance_ids"]),
                observed_at=float(item.get("observed_at", 0)),
                basis=str(item.get("basis", "persisted")),
            )
            if time.time() - correlation.observed_at <= SESSION_TTL_SECONDS:
                _SESSION_CORRELATIONS[correlation.busid] = correlation
        _SESSION_CORRELATIONS_LOADED = True
    except FileNotFoundError:
        _SESSION_CORRELATIONS_LOADED = True
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load PnP session correlations: %s", exc)
        try:
            os.replace(_session_path(), _session_path() + ".corrupt")
        except OSError:
            pass
        _SESSION_CORRELATIONS_LOADED = True


def _save_session_correlations_locked() -> None:
    path = _session_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        payload = [
            {
                "busid": item.busid,
                "vid": item.vid,
                "pid": item.pid,
                "instance_ids": list(item.instance_ids),
                "observed_at": item.observed_at,
                "basis": item.basis,
            }
            for item in _SESSION_CORRELATIONS.values()
        ]
        with open(tmp_path, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
        os.replace(tmp_path, path)
    except OSError as exc:
        logger.warning("Failed to save PnP session correlations: %s", exc)


def _normalize_text(value) -> str:
    return str(value or "").strip()


def _normalize_location_paths(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(_normalize_text(item) for item in value if _normalize_text(item))
    text = _normalize_text(value)
    return (text,) if text else ()


def _problem_code(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _is_unknown_device(status: PnpDeviceStatus) -> bool:
    return _normalize_instance_id(status.instance_id).startswith(_USB_UNKNOWN_PREFIX)


def _is_usbip_root_device(status: PnpDeviceStatus) -> bool:
    return _normalize_instance_id(status.instance_id).startswith(_USBIP_ROOT_PREFIX)


def _is_descriptor_failure_device(status: PnpDeviceStatus) -> bool:
    normalized = _normalize_instance_id(status.instance_id)
    return (
        normalized.startswith(_DESCRIPTOR_FAILURE_PREFIX)
        or _DESCRIPTOR_FAILURE_MARKER in normalized
    )


def _is_session_candidate(status: PnpDeviceStatus) -> bool:
    return bool(status.instance_id) and (
        bool(status.vid and status.pid)
        or _is_unknown_device(status)
        or _is_usbip_root_device(status)
    )


def _devices_related(left: PnpDeviceStatus, right: PnpDeviceStatus) -> bool:
    if left.instance_id == right.instance_id:
        return True
    if left.parent_instance_id and left.parent_instance_id == right.instance_id:
        return True
    if right.parent_instance_id and right.parent_instance_id == left.instance_id:
        return True
    if left.container_id and left.container_id == right.container_id:
        return True
    if set(left.location_paths).intersection(right.location_paths):
        return True
    return False


def _component_for(devices: list[PnpDeviceStatus], origin: PnpDeviceStatus) -> list[PnpDeviceStatus]:
    component: list[PnpDeviceStatus] = []
    pending = [origin]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        key = _normalize_instance_id(current.instance_id)
        if key in seen:
            continue
        seen.add(key)
        component.append(current)
        for candidate in devices:
            if _normalize_instance_id(candidate.instance_id) in seen:
                continue
            if _devices_related(current, candidate):
                pending.append(candidate)
    return component


def _build_components(devices: list[PnpDeviceStatus]) -> list[list[PnpDeviceStatus]]:
    components: list[list[PnpDeviceStatus]] = []
    seen: set[str] = set()
    for device in devices:
        key = _normalize_instance_id(device.instance_id)
        if key in seen:
            continue
        component = _component_for(devices, device)
        components.append(component)
        seen.update(_normalize_instance_id(item.instance_id) for item in component)
    return components


def _derive_correlation(
    busid: str,
    vid: str,
    pid: str,
    before: PnpSnapshot,
    after: PnpSnapshot,
) -> SessionCorrelation | None:
    before_ids = {_normalize_instance_id(item.instance_id) for item in before.devices}
    new_devices = [
        item for item in after.devices
        if _normalize_instance_id(item.instance_id) not in before_ids and _is_session_candidate(item)
    ]
    if not new_devices:
        return None

    components = _build_components(new_devices)
    key = (vid.lower(), pid.lower())
    exact_components = [
        component for component in components
        if any((item.vid, item.pid) == key for item in component)
    ]
    if len(exact_components) > 1:
        return None
    if len(exact_components) == 1:
        chosen = exact_components[0]
        basis = "vidpid-delta"
    else:
        unknown_components = [
            component for component in components
            if any(_is_unknown_device(item) or _is_descriptor_failure_device(item) for item in component)
        ]
        if len(components) != 1 or len(unknown_components) != 1:
            return None
        chosen = unknown_components[0]
        basis = "unknown-delta"

    instance_ids = tuple(sorted({_normalize_instance_id(item.instance_id) for item in chosen}))
    return SessionCorrelation(
        busid=busid,
        vid=vid.lower(),
        pid=pid.lower(),
        instance_ids=instance_ids,
        observed_at=time.time(),
        basis=basis,
    )


def _parse_statuses(payload: str) -> list[PnpDeviceStatus]:
    if not payload.strip():
        return []
    raw = json.loads(payload)
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]

    statuses: list[PnpDeviceStatus] = []
    for item in raw:
        instance_id = str(item.get("PNPDeviceID") or "")
        match = _VID_PID_RE.search(instance_id)
        statuses.append(PnpDeviceStatus(
            instance_id=instance_id,
            name=str(item.get("Name") or ""),
            problem_code=_problem_code(item.get("ConfigManagerErrorCode")),
            status=str(item.get("Status") or ""),
            vid=match.group(1).lower() if match else "",
            pid=match.group(2).lower() if match else "",
            parent_instance_id=_normalize_text(item.get("Parent")),
            container_id=_normalize_text(item.get("ContainerId")),
            location_paths=_normalize_location_paths(item.get("LocationPaths")),
            location_info=_normalize_text(item.get("LocationInfo")),
            service=_normalize_text(item.get("Service")),
            class_guid=_normalize_text(item.get("ClassGuid")),
            manufacturer=_normalize_text(item.get("Manufacturer")),
        ))
    return statuses


def list_usb_devices(timeout: int = 5, include_properties: bool = True) -> list[PnpDeviceStatus] | None:
    if sys.platform != "win32":
        return None
    try:
        process = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                _QUERY_FULL if include_properties else _QUERY_FAST,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        with _QUERY_LOCK:
            _QUERY_PROCESSES.add(process)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            logger.warning("PnP query timed out")
            return None
        finally:
            with _QUERY_LOCK:
                _QUERY_PROCESSES.discard(process)
    except OSError as exc:
        logger.warning("PnP query failed: %s", exc)
        return None

    if process.returncode != 0:
        logger.warning("PnP query failed: %s", stderr.strip())
        return None
    try:
        return _parse_statuses(stdout)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("Invalid PnP query response: %s", exc)
        return None


def kill_all_queries() -> None:
    with _QUERY_LOCK:
        processes = list(_QUERY_PROCESSES)
    for process in processes:
        try:
            process.kill()
        except OSError:
            pass


def snapshot_usb_devices(timeout: int = 5) -> PnpSnapshot | None:
    statuses = list_usb_devices(timeout=timeout)
    if statuses is None:
        return None
    return PnpSnapshot(devices=tuple(statuses), observed_at=time.time())


def find_code43(vid: str, pid: str, statuses: list[PnpDeviceStatus]) -> list[PnpDeviceStatus]:
    key = vid.lower(), pid.lower()
    return [item for item in statuses if (item.vid, item.pid) == key and item.problem_code == 43]


def find_unknown_code43(statuses: list[PnpDeviceStatus]) -> list[PnpDeviceStatus]:
    return [
        item for item in statuses
        if item.problem_code == 43 and (not item.vid or _is_descriptor_failure_device(item))
    ]


def find_descriptor_failure_code43(statuses: list[PnpDeviceStatus]) -> list[PnpDeviceStatus]:
    return [
        item for item in statuses
        if item.problem_code == 43 and _is_descriptor_failure_device(item)
    ]


def clear_session_correlations() -> None:
    global _SESSION_CORRELATIONS_LOADED
    with _SESSION_LOCK:
        _SESSION_CORRELATIONS.clear()
        _SESSION_CORRELATIONS_LOADED = True


def get_session_correlation(busid: str) -> SessionCorrelation | None:
    with _SESSION_LOCK:
        _load_session_correlations_locked()
        correlation = _SESSION_CORRELATIONS.get(busid)
        if correlation and time.time() - correlation.observed_at > SESSION_TTL_SECONDS:
            _SESSION_CORRELATIONS.pop(busid, None)
            _save_session_correlations_locked()
            return None
        return correlation


def remove_session_correlation(busid: str) -> None:
    with _SESSION_LOCK:
        _load_session_correlations_locked()
        if _SESSION_CORRELATIONS.pop(busid, None) is not None:
            _save_session_correlations_locked()


def get_busid_for_instance_id(instance_id: str) -> str | None:
    normalized = _normalize_instance_id(instance_id)
    with _SESSION_LOCK:
        _load_session_correlations_locked()
        for busid, correlation in _SESSION_CORRELATIONS.items():
            if normalized in correlation.instance_ids:
                return busid
    return None


def get_correlated_statuses(busid: str, statuses: list[PnpDeviceStatus]) -> list[PnpDeviceStatus]:
    correlation = get_session_correlation(busid)
    if correlation is None:
        return []
    instance_ids = set(correlation.instance_ids)
    return [item for item in statuses if _normalize_instance_id(item.instance_id) in instance_ids]


def register_attached_session(
    busid: str,
    vid: str,
    pid: str,
    before: PnpSnapshot | None,
    poll_timeout: int = 10,
) -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "PnP correlation is only available on Windows"
    if before is None:
        return False, "missing pre-attach PnP snapshot"

    deadline = time.monotonic() + max(1, poll_timeout)
    while time.monotonic() < deadline:
        remaining = max(1, int(deadline - time.monotonic()))
        after = snapshot_usb_devices(timeout=min(5, remaining))
        if after is None:
            return False, "post-attach PnP snapshot failed"
        correlation = _derive_correlation(busid, vid, pid, before, after)
        if correlation is not None:
            with _SESSION_LOCK:
                _load_session_correlations_locked()
                _SESSION_CORRELATIONS[busid] = correlation
                _save_session_correlations_locked()
            return True, correlation.basis
        time.sleep(0.5)
    return False, "attach did not produce an unambiguous PnP delta"


def _find_reenumerated_code43(
    busid: str,
    correlation: SessionCorrelation | None,
    statuses: list[PnpDeviceStatus],
    attached_devices,
) -> list[PnpDeviceStatus]:
    failures = [
        item for item in find_descriptor_failure_code43(statuses)
        if get_busid_for_instance_id(item.instance_id) in (None, busid)
    ]
    if not failures:
        return []

    if correlation is None:
        # Without a recorded correlation the failure node can only be
        # attributed safely when this is the sole attached session.
        attached = list(attached_devices)
        if len(attached) == 1 and attached[0].busid == busid:
            return failures
        return []

    present_ids = {_normalize_instance_id(item.instance_id) for item in statuses}

    def _correlated_nodes_vanished(session_busid: str) -> bool:
        session = get_session_correlation(session_busid)
        return session is not None and not set(session.instance_ids) & present_ids

    if not _correlated_nodes_vanished(busid):
        return []
    if any(item.busid != busid and _correlated_nodes_vanished(item.busid) for item in attached_devices):
        return []
    return failures


def find_session_code43(
    busid: str,
    vid: str,
    pid: str,
    statuses: list[PnpDeviceStatus],
    attached_devices,
) -> list[PnpDeviceStatus]:
    correlation = get_session_correlation(busid)
    if correlation and (correlation.vid, correlation.pid) != (vid.lower(), pid.lower()):
        remove_session_correlation(busid)
        correlation = None
    correlated = [item for item in get_correlated_statuses(busid, statuses) if item.problem_code == 43]
    if correlated:
        return correlated

    exact = find_code43(vid, pid, statuses)
    same_vid_pid = [
        item for item in attached_devices
        if (item.vid.lower(), item.pid.lower()) == (vid.lower(), pid.lower())
    ]
    if len(same_vid_pid) == 1 and same_vid_pid[0].busid == busid and len(exact) == 1:
        return exact

    return _find_reenumerated_code43(busid, correlation, statuses, attached_devices)
