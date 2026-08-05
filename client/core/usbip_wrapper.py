import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Optional

from client.core import operation_coordinator
from shared.constants import USBIP_EXE
from shared.models import AttachedDevice, CommandResult

logger = logging.getLogger(__name__)

_subprocesses: list[subprocess.Popen] = []
_subprocess_owners: dict[subprocess.Popen, int] = {}
_subprocess_lock = threading.RLock()
PNP_CORRELATION_TIMEOUT_SECONDS = 12
USBIP_PORT_MIN = 1
USBIP_PORT_MAX = 255


@dataclass(frozen=True)
class AttachedDevicesQuery:
    success: bool
    devices: tuple[AttachedDevice, ...]
    error: str = ""


def _register_proc(proc: subprocess.Popen):
    with _subprocess_lock:
        _subprocesses.append(proc)
        _subprocess_owners[proc] = threading.get_ident()


def _unregister_proc(proc: subprocess.Popen):
    with _subprocess_lock:
        try:
            _subprocesses.remove(proc)
        except ValueError:
            pass
        _subprocess_owners.pop(proc, None)


def kill_all_subprocesses(owner_thread_ids: set[int] | None = None):
    with _subprocess_lock:
        if owner_thread_ids is None:
            processes = list(_subprocesses)
        else:
            processes = [
                proc for proc in _subprocesses
                if _subprocess_owners.get(proc) in owner_thread_ids
            ]
        for proc in processes:
            try:
                _subprocesses.remove(proc)
            except ValueError:
                pass
            _subprocess_owners.pop(proc, None)
    for proc in processes:
        try:
            proc.kill()
        except Exception:
            pass


def _find_usbip() -> Optional[str]:
    if getattr(sys, "frozen", False):
        bundled = os.path.join(sys._MEIPASS, "usbipd-install", "USBip", f"{USBIP_EXE}.exe")
    else:
        bundled = os.path.join(os.path.dirname(__file__), "..", "..", "usbipd-install", "USBip", f"{USBIP_EXE}.exe")
    if os.path.exists(bundled):
        return bundled

    found = shutil.which(USBIP_EXE)
    if found:
        return found

    paths = [
        os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "USBip", f"{USBIP_EXE}.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "USBip", f"{USBIP_EXE}.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "USBip", f"{USBIP_EXE}.exe"),
    ]

    for p in paths:
        if os.path.exists(p):
            return p

    return None


def _run_command(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    exe_path = _find_usbip()
    if not exe_path:
        return -1, "", f"Executable not found: {USBIP_EXE}"

    full_args = [exe_path] + args

    try:
        # Keep cleanup from observing the gap between Popen and registration.
        with _subprocess_lock:
            proc = subprocess.Popen(
                full_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=os.path.dirname(exe_path),
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            _register_proc(proc)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return proc.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                stdout, stderr = proc.communicate(timeout=2)
            except Exception:
                stdout, stderr = "", "Command timed out and kill failed"
            return -2, stdout or "", stderr or "Command timed out"
        finally:
            _unregister_proc(proc)
    except FileNotFoundError:
        return -1, "", f"Executable not found: {exe_path}"
    except Exception as exc:
        return -3, "", str(exc)


def is_available() -> bool:
    return _find_usbip() is not None


def list_remote_devices(host_ip: str) -> list[dict]:
    returncode, stdout, stderr = _run_command(["list", "-r", host_ip])
    if returncode != 0:
        logger.warning(f"usbip list failed: {stderr}")
        return []

    devices: list[dict] = []
    for line in stdout.strip().splitlines():
        stripped = line.strip()
        match = re.match(
            r"^\s*([^:]+):\s*(.*)$", stripped,
        )
        if match:
            line_content = match.group(2).strip()
            dev_match = re.match(
                r"busid\s*=\s*(\S+).*?([0-9a-fA-F]{4})\s*:\s*([0-9a-fA-F]{4})",
                line_content,
            )
            if dev_match:
                devices.append({
                    "busid": dev_match.group(1),
                    "vid": dev_match.group(2).lower(),
                    "pid": dev_match.group(3).lower(),
                })

    return devices


def parse_attach_port(stdout: str) -> int | None:
    match = re.search(r"attached\s+to\s+port\s+(\d+)", stdout, re.IGNORECASE)
    if not match:
        return None
    port = int(match.group(1))
    return port if USBIP_PORT_MIN <= port <= USBIP_PORT_MAX else None


def _attach_device_locked(
    host_ip: str,
    busid: str,
    timeout: int = 30,
    vid: str = "",
    pid: str = "",
) -> CommandResult:
    before_snapshot = None
    if vid and pid and sys.platform == "win32":
        try:
            from client.core import windows_pnp
            windows_pnp.remove_session_correlation(busid)
            before_snapshot = windows_pnp.snapshot_usb_devices(timeout=min(2, max(1, timeout)))
            if before_snapshot is None and timeout >= 3:
                before_snapshot = windows_pnp.snapshot_usb_devices(timeout=3)
            if before_snapshot is None:
                logger.warning("PnP correlation disabled for %s: pre-attach snapshot timed out", busid)
        except Exception:
            logger.exception("Failed to invalidate/capture PnP state before attach for %s", busid)
            return CommandResult(
                success=False,
                message=f"Failed to prepare safe PnP correlation for {busid}.",
            )

    returncode, stdout, stderr = _run_command(
        ["attach", "-r", host_ip, "-b", busid],
        timeout=timeout,
    )
    success = returncode == 0
    assigned_port = parse_attach_port(stdout) if success else None
    if success:
        message = f"Device {busid} attached from {host_ip}."
    else:
        detail = stderr.strip() or stdout.strip()
        message = f"Failed to attach {busid}: {detail}" if detail else f"Failed to attach device {busid} from {host_ip}."
    if success and vid and pid and sys.platform == "win32":
        try:
            from client.core import windows_pnp
            if assigned_port is not None:
                windows_pnp.record_session_port(busid, vid, pid, assigned_port)
            if before_snapshot is not None:
                mapped, reason = windows_pnp.register_attached_session(
                    busid,
                    vid,
                    pid,
                    before_snapshot,
                    poll_timeout=PNP_CORRELATION_TIMEOUT_SECONDS,
                    local_port=assigned_port,
                )
                if not mapped:
                    logger.warning("Attached %s but PnP correlation was not recorded safely: %s", busid, reason)
        except Exception:
            logger.exception("Failed to record PnP correlation for %s", busid)
    return CommandResult(success=success, message=message, stdout=stdout, stderr=stderr)


def attach_device(
    host_ip: str,
    busid: str,
    timeout: int = 30,
    vid: str = "",
    pid: str = "",
) -> CommandResult:
    if not operation_coordinator.acquire_named(
        "usbip-transport",
        "global",
        timeout=max(1, timeout),
    ):
        return CommandResult(
            success=False,
            message=f"Timed out waiting to attach {busid}; another USB/IP mutation is running.",
        )
    try:
        return _attach_device_locked(host_ip, busid, timeout, vid, pid)
    finally:
        operation_coordinator.release_named("usbip-transport", "global")


def _detach_device_by_port(port: int, timeout: int = 30) -> CommandResult:
    returncode, stdout, stderr = _run_command(
        ["detach", "-p", str(port)],
        timeout=timeout,
    )
    success = returncode == 0
    if success:
        message = f"Device on port {port} detached."
    else:
        message = f"Failed to detach device on port {port}."
    return CommandResult(success=success, message=message, stdout=stdout, stderr=stderr)


def detach_busid(
    busid: str,
    timeout: int = 30,
    port_hint: int | None = None,
    expected_vid: str = "",
    expected_pid: str = "",
) -> CommandResult:
    if not expected_vid or not expected_pid:
        return CommandResult(
            success=False,
            message=f"Expected VID/PID identity is required to detach {busid}",
        )
    if not operation_coordinator.acquire_named(
        "usbip-transport",
        "global",
        timeout=max(1, timeout),
    ):
        return CommandResult(
            success=False,
            message=f"Timed out waiting to detach {busid}; another USB/IP mutation is running.",
        )

    try:
        first, error = _resolve_live_attachment(
            busid,
            timeout,
            expected_vid,
            expected_pid,
        )
        if first is None:
            return CommandResult(success=False, message=error)

        second, error = _resolve_live_attachment(
            busid,
            timeout,
            expected_vid,
            expected_pid,
        )
        if second is None:
            return CommandResult(success=False, message=error)
        if second.port != first.port:
            return CommandResult(
                success=False,
                message=f"USB/IP port changed during detach validation for {busid}",
            )

        if port_hint is not None and port_hint != second.port:
            logger.info(
                "Ignoring stale port hint %s for %s; current validated port is %s",
                port_hint,
                busid,
                second.port,
            )

        result = _detach_device_by_port(second.port, timeout=timeout)
        if result.success:
            try:
                from client.core import windows_pnp
                windows_pnp.remove_session_correlation(busid)
            except Exception:
                logger.exception("Detached %s but failed to clear its persisted session", busid)
        return result
    finally:
        operation_coordinator.release_named("usbip-transport", "global")


def _parse_attached_output(stdout: str) -> list[AttachedDevice]:
    attached: list[AttachedDevice] = []
    current: dict[str, object] | None = None

    def flush_current():
        nonlocal current
        if current is None:
            return
        port = int(current["port"])
        if USBIP_PORT_MIN <= port <= USBIP_PORT_MAX:
            attached.append(AttachedDevice(
                port=port,
                busid=str(current["busid"]),
                vid=str(current["vid"]),
                pid=str(current["pid"]),
            ))
        current = None

    for line in stdout.splitlines():
        stripped = line.strip()
        header_match = re.match(r"^port\s+(\d+)\s*:", stripped, re.IGNORECASE)
        if header_match:
            flush_current()
            current = {
                "port": int(header_match.group(1)),
                "busid": "",
                "vid": "0000",
                "pid": "0000",
            }
            continue

        if current is not None:
            uri_match = re.search(r"usbip://[^\s/]+/([^\s]+)", stripped, re.IGNORECASE)
            if uri_match:
                current["busid"] = uri_match.group(1)
            vid_match = re.search(r"([0-9a-fA-F]{4})\s*:\s*([0-9a-fA-F]{4})", stripped)
            if vid_match:
                current["vid"] = vid_match.group(1).lower()
                current["pid"] = vid_match.group(2).lower()
            continue

        # Compatibility with the compact format accepted by previous versions.
        port_match = re.search(r"port\s*[=:]\s*(\d+)", stripped, re.IGNORECASE)
        if not port_match:
            continue
        port = int(port_match.group(1))
        if not USBIP_PORT_MIN <= port <= USBIP_PORT_MAX:
            continue
        busid_match = re.search(r"busid\s*[=:]\s*(\S+)", stripped, re.IGNORECASE)
        vid_match = re.search(r"([0-9a-fA-F]{4})\s*:\s*([0-9a-fA-F]{4})", stripped)
        attached.append(AttachedDevice(
            port=port,
            busid=busid_match.group(1) if busid_match else "",
            vid=vid_match.group(1).lower() if vid_match else "0000",
            pid=vid_match.group(2).lower() if vid_match else "0000",
        ))

    flush_current()
    return attached


def query_attached_devices(timeout: int = 30) -> AttachedDevicesQuery:
    returncode, stdout, stderr = _run_command(["port"], timeout=timeout)
    if returncode != 0:
        detail = stderr.strip() or stdout.strip() or f"usbip port exited with code {returncode}"
        return AttachedDevicesQuery(False, (), detail)
    return AttachedDevicesQuery(True, tuple(_parse_attached_output(stdout)))


def _resolve_live_attachment(
    busid: str,
    timeout: int,
    expected_vid: str = "",
    expected_pid: str = "",
) -> tuple[AttachedDevice | None, str]:
    query = query_attached_devices(timeout=timeout)
    if not query.success:
        return None, f"Local USB/IP state is unknown: {query.error}"

    matches = [device for device in query.devices if device.busid == busid]
    if len(matches) != 1:
        if len(matches) > 1:
            return None, f"Multiple live ports claim busid {busid}"
        return None, f"No unambiguous live port found for {busid}"

    device = matches[0]
    if expected_vid and expected_pid:
        expected = (expected_vid.lower(), expected_pid.lower())
        observed = (device.vid.lower(), device.pid.lower())
        if observed != expected:
            return None, (
                f"USB/IP identity changed for {busid}: expected "
                f"{expected[0]}:{expected[1]}, observed {observed[0]}:{observed[1]}"
            )
    return device, ""


def list_attached(timeout: int = 30) -> list[AttachedDevice]:
    return list(query_attached_devices(timeout=timeout).devices)


def find_port_for_busid(
    busid: str,
    timeout: int = 30,
    port_hint: int | None = None,
) -> Optional[int]:
    query = query_attached_devices(timeout=timeout)
    if not query.success:
        logger.error("Cannot resolve port for %s: %s", busid, query.error)
        return None
    attached = query.devices
    matches = [dev.port for dev in attached if dev.busid == busid]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.error("Refusing ambiguous detach for %s: live ports=%s", busid, matches)
    elif port_hint is not None:
        logger.warning(
            "Ignoring unverified persisted port hint %s for %s; live usbip output has no exact busid",
            port_hint,
            busid,
        )
    return None
