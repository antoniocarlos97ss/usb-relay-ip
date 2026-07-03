import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from typing import Optional

from shared.constants import USBIPD_EXE, USBIPD_MIN_VERSION
from shared.models import CommandResult, UsbDevice

logger = logging.getLogger(__name__)


def _register_proc(proc):
    return None


def _unregister_proc(proc):
    return None


def kill_all_subprocesses():
    return None


def _run_sc(args: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["sc"] + args,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return -1, "", "sc not found"
    except subprocess.TimeoutExpired:
        return -1, "", "sc command timed out"


def _sc_output_contains_not_installed(output: str) -> bool:
    lowered = output.lower()
    return "failed 1060" in lowered or "specified service does not exist" in lowered


def _parse_sc_state(output: str) -> str:
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("STATE"):
            parts = line.split()
            if len(parts) >= 3:
                return parts[-1].upper()
    return "UNKNOWN"


def get_service_state() -> str:
    """Check the current state of usbipd.

    Since the app manages usbipd as a child process rather than a Windows service,
    this reports the real health by checking port 3240 first, then falling back
    to the Windows service status as supplementary info.

    Returns one of: 'RUNNING', 'STOPPED', 'NOT_INSTALLED', or 'UNKNOWN'.
    """
    if check_port_listening(3240):
        return "RUNNING"
    try:
        returncode, stdout, stderr = _run_sc(["query", "usbipd"])
        output = (stdout or "") + (stderr or "")
        if returncode != 0 and _sc_output_contains_not_installed(output):
            return "NOT_INSTALLED"
        if _sc_output_contains_not_installed(output):
            return "NOT_INSTALLED"
        state = _parse_sc_state(output)
        return state
    except Exception as exc:
        logger.warning(f"Failed to query usbipd service state: {exc}")
        return "UNKNOWN"


def _find_usbipd() -> Optional[str]:
    # Check bundled path first (PyInstaller — usbipd.exe bundled alongside the app)
    if getattr(sys, "frozen", False):
        bundled = os.path.join(sys._MEIPASS, "usbipd-win", f"{USBIPD_EXE}.exe")
        if os.path.exists(bundled):
            return bundled

    found = shutil.which(USBIPD_EXE)
    if found:
        return found

    paths = [
        os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "usbipd-win", f"{USBIPD_EXE}.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "usbipd-win", f"{USBIPD_EXE}.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "usbipd-win", f"{USBIPD_EXE}.exe"),
        os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", f"{USBIPD_EXE}.exe"),
    ]

    for p in paths:
        if os.path.exists(p):
            return p

    return None


def _run_command(args: list[str], timeout: int = 15) -> tuple[int, str, str]:
    exe_path = _find_usbipd()
    if not exe_path:
        return -1, "", f"Executable not found: {USBIPD_EXE}"

    full_args = [exe_path] + args[1:] if args[0] == USBIPD_EXE else args

    try:
        proc = subprocess.Popen(
            full_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
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


def _start_service() -> tuple[bool, str]:
    returncode, stdout, stderr = _run_sc(["start", "usbipd"])
    output = (stdout or "") + (stderr or "")
    return returncode == 0, output.strip() or "sc start usbipd failed"


def _stop_service() -> tuple[bool, str]:
    returncode, stdout, stderr = _run_sc(["stop", "usbipd"])
    output = (stdout or "") + (stderr or "")
    # sc stop returns 0 even when service is already stopping/stopped in some cases.
    return returncode == 0, output.strip() or "sc stop usbipd failed"


def _wait_for_port(timeout_seconds: float = 15.0, interval_seconds: float = 0.5) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if check_port_listening(3240):
            return True
        time.sleep(interval_seconds)
    return False


def _parse_version(version_text: str) -> tuple[int, int]:
    match = re.search(r"(\d+)\.(\d+)", version_text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 0, 0


def get_version() -> tuple[int, int]:
    """Return (major, minor) version of installed usbipd."""
    returncode, stdout, _ = _run_command([USBIPD_EXE, "--version"])
    if returncode == 0 and stdout.strip():
        return _parse_version(stdout)
    return 0, 0


def is_available() -> bool:
    """Check if usbipd is available."""
    exe = _find_usbipd()
    if not exe:
        return False
    major, minor = get_version()
    if major == 0 and minor == 0:
        return False
    return (major, minor) >= USBIPD_MIN_VERSION


def _parse_list_text(stdout: str) -> list[UsbDevice]:
    devices: list[UsbDevice] = []
    lines = stdout.strip().splitlines()
    if len(lines) < 2:
        return devices

    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue

        match = re.match(
            r"^(\S+)\s+(\S+)\s{2,}(.+?)\s{2,}(Not shared|Shared|Attached)\s*$",
            line,
        )
        if not match:
            match = re.match(
                r"^(\S+)\s+(\S+)\s+(.*?)\s+(Not shared|Shared|Attached)\s*$",
                line,
            )
        if not match:
            continue

        busid = match.group(1)
        vid_pid = match.group(2)
        description = match.group(3).strip()
        state = match.group(4)

        vid, pid = "0000", "0000"
        vid_pid_match = re.match(r"([0-9a-fA-F]{4}):([0-9a-fA-F]{4})", vid_pid)
        if vid_pid_match:
            vid = vid_pid_match.group(1).lower()
            pid = vid_pid_match.group(2).lower()

        devices.append(UsbDevice(
            busid=busid,
            vid=vid,
            pid=pid,
            description=description,
            state=state,
        ))

    return devices


def _parse_list_json(stdout: str) -> Optional[list[UsbDevice]]:
    try:
        data = json.loads(stdout)
        raw_devices = data.get("Devices") or data.get("devices") or []
        devices: list[UsbDevice] = []
        for dev in raw_devices:
            busid = dev.get("BusId") or dev.get("busId") or dev.get("busid", "")
            vid = dev.get("VendorId") or dev.get("vendorId") or ""
            pid = dev.get("ProductId") or dev.get("productId") or ""
            description = dev.get("Description") or dev.get("description") or dev.get("ServiceDescription", "")

            is_attached = dev.get("IsAttached") or dev.get("isAttached", False)
            is_bound = dev.get("IsBound") or dev.get("isBound", False)

            state = "Not shared"
            if is_attached:
                state = "Attached"
            elif is_bound:
                state = "Shared"

            vid_str = vid.replace("VID_", "").replace("0x", "").lower()
            pid_str = pid.replace("PID_", "").replace("0x", "").lower()

            if vid_str == "" or pid_str == "":
                instance_id = dev.get("InstanceId") or dev.get("instanceId", "")
                hw_match = re.search(r"VID[_\s]*([0-9a-fA-F]{4})", instance_id)
                pid_match = re.search(r"PID[_\s]*([0-9a-fA-F]{4})", instance_id)
                if hw_match:
                    vid_str = hw_match.group(1).lower()
                if pid_match:
                    pid_str = pid_match.group(1).lower()

            devices.append(UsbDevice(
                busid=str(busid),
                vid=vid_str or "0000",
                pid=pid_str or "0000",
                description=str(description),
                state=state,
            ))

        return devices
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def list_devices() -> list[UsbDevice]:
    returncode, stdout, stderr = _run_command([USBIPD_EXE, "list", "--json"])
    if returncode == 0:
        devices = _parse_list_json(stdout)
        if devices is not None:
            return devices

    returncode, stdout, stderr = _run_command([USBIPD_EXE, "list"])
    if returncode == 0 and stdout.strip():
        return _parse_list_text(stdout)

    return []


def bind_device(busid: str) -> CommandResult:
    returncode, stdout, stderr = _run_command([USBIPD_EXE, "bind", "--busid", busid])
    success = returncode == 0
    message = f"Device {busid} bound successfully." if success else f"Failed to bind device {busid}."
    return CommandResult(success=success, message=message, stdout=stdout, stderr=stderr)


def unbind_device(busid: str) -> CommandResult:
    returncode, stdout, stderr = _run_command([USBIPD_EXE, "unbind", "--busid", busid])
    success = returncode == 0
    message = f"Device {busid} unbound successfully." if success else f"Failed to unbind device {busid}."
    return CommandResult(success=success, message=message, stdout=stdout, stderr=stderr)


def get_device_state(busid: str) -> str:
    devices = list_devices()
    for device in devices:
        if device.busid == busid:
            return device.state
    return "Not shared"


def check_port_listening(port: int = 3240) -> bool:
    """Check if the given TCP port is listening on localhost."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0) as s:
            return True
    except Exception:
        return False


def ensure_service_running() -> tuple[bool, str]:
    """Ensure that the usbipd Windows service is running and listening on port 3240."""
    if check_port_listening(3240):
        return True, 'Server is listening on port 3240.'

    try:
        state = get_service_state()
        if state == "NOT_INSTALLED":
            return False, f'{USBIPD_EXE} service not installed. Execute o instalador primeiro.'

        if state == "START_PENDING":
            logger.info("usbipd service is START_PENDING. Waiting for port 3240...")
            if _wait_for_port():
                return True, 'usbipd service is starting and is listening on port 3240.'
            return False, 'usbipd service is starting but port 3240 is not responding.'

        if state in ("STOPPED", "STOP_PENDING", "PAUSED", "UNKNOWN"):
            logger.info("usbipd service is not running. Attempting to start it via SCM...")
            started, msg = _start_service()
            if not started:
                return False, f'Could not start usbipd service: {msg}'
            if _wait_for_port():
                return True, 'usbipd service started and is listening on port 3240.'
            return False, 'usbipd service was started but port 3240 is not responding.'

        if state == "RUNNING":
            logger.warning("usbipd service reports RUNNING but port 3240 is not listening. Restarting via SCM...")
            stopped, stop_msg = _stop_service()
            if not stopped:
                logger.warning(f'Could not stop usbipd service during recovery: {stop_msg}')
            time.sleep(1)
            started, start_msg = _start_service()
            if not started:
                return False, f'Could not start usbipd service: {start_msg}'
            if _wait_for_port():
                return True, 'usbipd service restarted and is listening on port 3240.'
            return False, 'usbipd service was restarted but port 3240 is not responding.'

        started, msg = _start_service()
        if started and _wait_for_port():
            return True, 'usbipd service started and is listening on port 3240.'
        return False, f'Could not start usbipd service: {msg}'
    except Exception as exc:
        return False, f'Falha ao iniciar usbipd service: {exc}'
