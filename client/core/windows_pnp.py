import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
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
_SESSION_FILE_SIGNATURE: tuple[int, int] | None = None
_SESSION_FILE = "pnp_sessions.json"
_QUERY_LOCK = threading.Lock()
_QUERY_PROCESSES: set[subprocess.Popen] = set()
_QUERY_OWNERS: dict[subprocess.Popen, int] = {}
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
    local_port: int | None = None


def _normalize_instance_id(value: str) -> str:
    return value.strip().upper()


def _session_path() -> str:
    from shared.constants import CONFIG_DIR_NAME

    base = (
        os.environ.get("PROGRAMDATA")
        or os.environ.get("ALLUSERSPROFILE")
        or os.environ.get("APPDATA")
        or os.path.expanduser("~")
    )
    return os.path.join(base, CONFIG_DIR_NAME, _SESSION_FILE)


def _legacy_session_path() -> str:
    from shared.constants import CONFIG_DIR_NAME

    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, CONFIG_DIR_NAME, _SESSION_FILE)


def _session_file_signature(path: str) -> tuple[int, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _session_uses_installer_root(path: str) -> bool:
    """Return whether ``path`` is under an installer-owned shared root."""
    shared_base = os.environ.get("PROGRAMDATA") or os.environ.get("ALLUSERSPROFILE")
    if not shared_base:
        return False
    shared_root = os.path.abspath(os.path.join(shared_base, "USBRelay"))
    candidate = os.path.abspath(path)
    try:
        return os.path.commonpath((shared_root, candidate)) == shared_root
    except ValueError:
        return False


@contextmanager
def _session_file_lock(path: str, timeout: float, *, create_parent: bool):
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
                logger.debug("Failed to close PnP session lock %s", path, exc_info=True)


@contextmanager
def _session_storage_lock(timeout: float = 5.0):
    """Acquire the persistent correlation lock without creating ProgramData."""
    lock_path = _session_path() + ".lock"
    with _session_file_lock(
        lock_path,
        timeout,
        create_parent=not _session_uses_installer_root(lock_path),
    ):
        yield


def _valid_local_port(value) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 255 else None


def _decode_session_records(raw, source_path: str) -> dict[str, SessionCorrelation]:
    if not isinstance(raw, list):
        raise ValueError("session correlation payload must be a list")
    records: dict[str, SessionCorrelation] = {}
    for item in raw:
        try:
            correlation = SessionCorrelation(
                busid=str(item["busid"]),
                vid=str(item["vid"]).lower(),
                pid=str(item["pid"]).lower(),
                instance_ids=tuple(_normalize_instance_id(value) for value in item["instance_ids"]),
                observed_at=float(item.get("observed_at", 0)),
                basis=str(item.get("basis", "persisted")),
                local_port=_valid_local_port(item.get("local_port")),
            )
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("Skipping invalid PnP session record from %s: %s", source_path, exc)
            continue
        if time.time() - correlation.observed_at <= SESSION_TTL_SECONDS:
            records[correlation.busid] = correlation
    return records


def _storage_signature(shared_path: str, legacy_path: str):
    shared_signature = _session_file_signature(shared_path)
    if os.path.normcase(os.path.abspath(legacy_path)) == os.path.normcase(os.path.abspath(shared_path)):
        return shared_signature, None
    return shared_signature, _session_file_signature(legacy_path)


def _archive_file(path: str, suffix: str) -> None:
    try:
        os.replace(path, path + suffix)
    except OSError:
        logger.warning("Failed to archive PnP session file %s", path, exc_info=True)


def _read_session_file(path: str) -> dict[str, SessionCorrelation]:
    with open(path, "r", encoding="utf-8") as stream:
        return _decode_session_records(json.load(stream), path)


def _load_session_correlations_locked() -> None:
    global _SESSION_CORRELATIONS_LOADED, _SESSION_FILE_SIGNATURE

    shared_path = _session_path()
    legacy_path = _legacy_session_path()
    same_path = os.path.normcase(os.path.abspath(legacy_path)) == os.path.normcase(
        os.path.abspath(shared_path)
    )
    signature = _storage_signature(shared_path, legacy_path)
    if _SESSION_CORRELATIONS_LOADED and signature == _SESSION_FILE_SIGNATURE:
        return

    shared_records: dict[str, SessionCorrelation] = {}
    legacy_records: dict[str, SessionCorrelation] = {}
    shared_corrupt = False
    legacy_loaded = False

    try:
        shared_records = _read_session_file(shared_path)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load PnP session correlations from %s: %s", shared_path, exc)
        _archive_file(shared_path, ".corrupt")
        shared_corrupt = True

    if not same_path:
        try:
            legacy_records = _read_session_file(legacy_path)
            legacy_loaded = True
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load legacy PnP session correlations from %s: %s", legacy_path, exc)
            _archive_file(legacy_path, ".corrupt")

    merged = dict(shared_records)
    for busid, legacy in legacy_records.items():
        current = merged.get(busid)
        if current is None or legacy.observed_at > current.observed_at:
            merged[busid] = legacy

    _SESSION_CORRELATIONS.clear()
    _SESSION_CORRELATIONS.update(merged)
    _SESSION_CORRELATIONS_LOADED = True

    if legacy_loaded or shared_corrupt:
        if _save_session_correlations_locked() and legacy_loaded:
            _archive_file(legacy_path, ".migrated")

    _SESSION_FILE_SIGNATURE = _storage_signature(shared_path, legacy_path)


def _save_session_correlations_locked() -> bool:
    global _SESSION_FILE_SIGNATURE
    path = _session_path()
    directory = os.path.dirname(os.path.abspath(path)) or "."
    try:
        if _session_uses_installer_root(path):
            # ProgramData and its ACL are installed, not provisioned by a
            # runtime monitor.  A missing directory is an unavailable store.
            if not os.path.isdir(directory):
                raise FileNotFoundError(directory)
        else:
            os.makedirs(directory, exist_ok=True)

        payload = [
            {
                "busid": item.busid,
                "vid": item.vid,
                "pid": item.pid,
                "instance_ids": list(item.instance_ids),
                "observed_at": item.observed_at,
                "basis": item.basis,
                "local_port": item.local_port,
            }
            for item in _SESSION_CORRELATIONS.values()
        ]
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            dir=directory,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                fd = -1
                json.dump(payload, stream, indent=2)
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
                logger.warning("Failed to clean temporary PnP session file %s", tmp_path, exc_info=True)

        _SESSION_FILE_SIGNATURE = _storage_signature(path, _legacy_session_path())
        return True
    except (OSError, TimeoutError) as exc:
        logger.warning("Failed to save PnP session correlations: %s", exc)
        return False


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
    local_port: int | None = None,
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
        local_port=local_port,
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
        # Make process creation + ownership registration atomic to cleanup.
        with _QUERY_LOCK:
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
            _QUERY_PROCESSES.add(process)
            _QUERY_OWNERS[process] = threading.get_ident()
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
                _QUERY_OWNERS.pop(process, None)
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


def kill_all_queries(owner_thread_ids: set[int] | None = None) -> None:
    with _QUERY_LOCK:
        if owner_thread_ids is None:
            processes = list(_QUERY_PROCESSES)
        else:
            processes = [
                process for process in _QUERY_PROCESSES
                if _QUERY_OWNERS.get(process) in owner_thread_ids
            ]
        for process in processes:
            _QUERY_PROCESSES.discard(process)
            _QUERY_OWNERS.pop(process, None)
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


def _session_correlation_state(busid: str) -> tuple[SessionCorrelation | None, bool]:
    """Return (correlation, storage_available) for fail-closed callers."""
    with _SESSION_LOCK:
        try:
            with _session_storage_lock():
                _load_session_correlations_locked()
                correlation = _SESSION_CORRELATIONS.get(busid)
                if correlation and time.time() - correlation.observed_at > SESSION_TTL_SECONDS:
                    _SESSION_CORRELATIONS.pop(busid, None)
                    _save_session_correlations_locked()
                    correlation = None
                return correlation, True
        except (PermissionError, OSError, TimeoutError) as exc:
            logger.warning("PnP session storage unavailable while reading %s: %s", busid, exc)
            return None, False


def clear_session_correlations() -> bool:
    """Clear memory and persist the empty correlation set.

    A failed write is reported to the caller and the in-memory set remains
    empty so stale identity can never be used after a reset.
    """
    global _SESSION_CORRELATIONS_LOADED, _SESSION_FILE_SIGNATURE
    with _SESSION_LOCK:
        _SESSION_CORRELATIONS.clear()
        _SESSION_CORRELATIONS_LOADED = True
        _SESSION_FILE_SIGNATURE = None
        try:
            with _session_storage_lock():
                ok = _save_session_correlations_locked()
        except (PermissionError, OSError, TimeoutError) as exc:
            logger.warning("Failed to clear persisted PnP session correlations: %s", exc)
            _SESSION_CORRELATIONS_LOADED = False
            return False
        if not ok:
            _SESSION_CORRELATIONS_LOADED = False
        return ok


def get_session_correlation(busid: str) -> SessionCorrelation | None:
    correlation, _storage_available = _session_correlation_state(busid)
    return correlation


def record_session_port(busid: str, vid: str, pid: str, port: int) -> bool:
    global _SESSION_CORRELATIONS_LOADED
    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid USB/IP port {port}; expected 1..255") from exc
    if not 1 <= port <= 255:
        raise ValueError(f"Invalid USB/IP port {port}; expected 1..255")

    with _SESSION_LOCK:
        try:
            with _session_storage_lock():
                _SESSION_CORRELATIONS_LOADED = False
                _load_session_correlations_locked()
                existing = _SESSION_CORRELATIONS.get(busid)
                if (
                    existing is not None
                    and existing.instance_ids
                    and (existing.vid, existing.pid) == (vid.lower(), pid.lower())
                ):
                    # Preserve a real PnP identity; a port is only an
                    # additional hint and must not replace it.
                    correlation = SessionCorrelation(
                        busid=busid,
                        vid=existing.vid,
                        pid=existing.pid,
                        instance_ids=existing.instance_ids,
                        observed_at=time.time(),
                        basis=existing.basis,
                        local_port=port,
                    )
                else:
                    correlation = SessionCorrelation(
                        busid=busid,
                        vid=vid.lower(),
                        pid=pid.lower(),
                        instance_ids=(),
                        observed_at=time.time(),
                        basis="port-only",
                        local_port=port,
                    )
                _SESSION_CORRELATIONS[busid] = correlation
                if not _save_session_correlations_locked():
                    _SESSION_CORRELATIONS.clear()
                    _SESSION_CORRELATIONS_LOADED = False
                    return False
                return True
        except (PermissionError, OSError, TimeoutError) as exc:
            logger.warning("Failed to record PnP session port for %s: %s", busid, exc)
            _SESSION_CORRELATIONS.clear()
            _SESSION_CORRELATIONS_LOADED = False
            return False


def get_session_port(busid: str, vid: str = "", pid: str = "") -> int | None:
    correlation = get_session_correlation(busid)
    if correlation is None:
        return None
    if vid and pid and (correlation.vid, correlation.pid) != (vid.lower(), pid.lower()):
        return None
    return correlation.local_port


def remove_session_correlation(busid: str) -> bool:
    global _SESSION_CORRELATIONS_LOADED
    with _SESSION_LOCK:
        try:
            with _session_storage_lock():
                _SESSION_CORRELATIONS_LOADED = False
                _load_session_correlations_locked()
                if _SESSION_CORRELATIONS.pop(busid, None) is None:
                    return True
                if not _save_session_correlations_locked():
                    _SESSION_CORRELATIONS.clear()
                    _SESSION_CORRELATIONS_LOADED = False
                    return False
                return True
        except (PermissionError, OSError, TimeoutError) as exc:
            logger.warning("Failed to remove PnP session correlation for %s: %s", busid, exc)
            _SESSION_CORRELATIONS.clear()
            _SESSION_CORRELATIONS_LOADED = False
            return False


def get_busid_for_instance_id(instance_id: str) -> str | None:
    normalized = _normalize_instance_id(instance_id)
    with _SESSION_LOCK:
        try:
            with _session_storage_lock():
                _load_session_correlations_locked()
                owners = {
                    busid for busid, correlation in _SESSION_CORRELATIONS.items()
                    if normalized in correlation.instance_ids
                }
        except (PermissionError, OSError, TimeoutError) as exc:
            logger.warning("PnP session storage unavailable while resolving %s: %s", instance_id, exc)
            return None
    return next(iter(owners)) if len(owners) == 1 else None


def get_correlated_statuses(busid: str, statuses: list[PnpDeviceStatus]) -> list[PnpDeviceStatus]:
    correlation, storage_available = _session_correlation_state(busid)
    if not storage_available or correlation is None or not correlation.instance_ids:
        return []
    instance_ids = set(correlation.instance_ids)
    return [item for item in statuses if _normalize_instance_id(item.instance_id) in instance_ids]


def _store_session_correlation(correlation: SessionCorrelation) -> bool:
    global _SESSION_CORRELATIONS_LOADED
    with _SESSION_LOCK:
        try:
            with _session_storage_lock():
                _SESSION_CORRELATIONS_LOADED = False
                _load_session_correlations_locked()
                _SESSION_CORRELATIONS[correlation.busid] = correlation
                if not _save_session_correlations_locked():
                    _SESSION_CORRELATIONS.clear()
                    _SESSION_CORRELATIONS_LOADED = False
                    return False
                return True
        except (PermissionError, OSError, TimeoutError) as exc:
            logger.warning("Failed to persist PnP correlation for %s: %s", correlation.busid, exc)
            _SESSION_CORRELATIONS.clear()
            _SESSION_CORRELATIONS_LOADED = False
            return False


def register_attached_session(
    busid: str,
    vid: str,
    pid: str,
    before: PnpSnapshot | None,
    poll_timeout: int = 10,
    local_port: int | None = None,
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
        correlation = _derive_correlation(
            busid,
            vid,
            pid,
            before,
            after,
            local_port=local_port,
        )
        if correlation is not None:
            if not _store_session_correlation(correlation):
                return False, "PnP session storage unavailable"
            return True, correlation.basis
        time.sleep(0.5)
    return False, "attach did not produce an unambiguous PnP delta"


def _find_reenumerated_code43(
    busid: str,
    correlation: SessionCorrelation | None,
    statuses: list[PnpDeviceStatus],
    attached_devices,
    storage_available: bool = True,
) -> list[PnpDeviceStatus]:
    if not storage_available:
        return []
    failures = [
        item for item in find_descriptor_failure_code43(statuses)
        if get_busid_for_instance_id(item.instance_id) in (None, busid)
    ]
    # More than one VID_0000&PID_0002 failure is inherently ambiguous.  Do
    # not choose the first item, even if one happens to look newer.
    if len(failures) != 1:
        return []

    if correlation is None:
        # Without a recorded correlation the failure node can only be
        # attributed safely when this is the sole attached session.
        attached = list(attached_devices)
        if len(attached) == 1 and attached[0].busid == busid:
            return failures
        return []

    present_ids = {_normalize_instance_id(item.instance_id) for item in statuses}

    def _device_instance_ids(session: SessionCorrelation) -> set[str]:
        vid_pid_marker = f"VID_{session.vid.upper()}&PID_{session.pid.upper()}"
        exact_device_ids = {
            instance_id for instance_id in session.instance_ids
            if vid_pid_marker in instance_id
        }
        if exact_device_ids:
            return exact_device_ids
        non_anchor_ids = {
            instance_id for instance_id in session.instance_ids
            if not instance_id.startswith(_USBIP_ROOT_PREFIX)
        }
        return non_anchor_ids or set(session.instance_ids)

    def _correlated_nodes_vanished(session_busid: str) -> bool:
        session, available = _session_correlation_state(session_busid)
        if not available or session is None:
            return False
        device_ids = _device_instance_ids(session)
        return bool(device_ids) and not device_ids & present_ids

    if not _correlated_nodes_vanished(busid):
        return []
    for item in attached_devices:
        if item.busid == busid:
            continue
        other, available = _session_correlation_state(item.busid)
        if not available or other is None or not _device_instance_ids(other):
            return []
        if _correlated_nodes_vanished(item.busid):
            return []
    return failures


def find_session_code43(
    busid: str,
    vid: str,
    pid: str,
    statuses: list[PnpDeviceStatus],
    attached_devices,
) -> list[PnpDeviceStatus]:
    correlation, storage_available = _session_correlation_state(busid)
    if not storage_available:
        return []
    if correlation and (correlation.vid, correlation.pid) != (vid.lower(), pid.lower()):
        if not remove_session_correlation(busid):
            return []
        correlation = None
    # A port-only record is location data, not identity.  Treat it exactly as
    # absent for attribution so it cannot block single-session fallback or
    # authorize a multi-session match.
    if correlation is not None and not correlation.instance_ids:
        correlation = None

    if correlation is not None:
        instance_ids = set(correlation.instance_ids)
        correlated = [
            item for item in statuses
            if _normalize_instance_id(item.instance_id) in instance_ids and item.problem_code == 43
        ]
        if correlated:
            return correlated

    exact = find_code43(vid, pid, statuses)
    same_vid_pid = [
        item for item in attached_devices
        if (item.vid.lower(), item.pid.lower()) == (vid.lower(), pid.lower())
    ]
    if len(same_vid_pid) == 1 and same_vid_pid[0].busid == busid and len(exact) == 1:
        return exact

    return _find_reenumerated_code43(
        busid,
        correlation,
        statuses,
        attached_devices,
        storage_available=storage_available,
    )
