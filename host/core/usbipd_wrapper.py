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

_subprocesses: list[subprocess.Popen] = []


def _register_proc(proc: subprocess.Popen):
    _subprocesses.append(proc)


def _unregister_proc(proc: subprocess.Popen):
    try:
        _subprocesses.remove(proc)
    except ValueError:
        pass


def kill_all_subprocesses():
    for proc in list(_subprocesses):
        try:
            proc.kill()
        except Exception:
            pass
    _subprocesses.clear()
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", f"{USBIPD_EXE}.exe"],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass


def _find_usbipd() -> Optional[str]:
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


def get_service_state() -> str:
    """Query the Windows Service status of usbipd.

    Returns one of: 'RUNNING', 'STOPPED', 'START_PENDING', 'STOP_PENDING',
    'PAUSED', 'NOT_INSTALLED', or 'UNKNOWN'.
    """
    try:
        proc = subprocess.run(
            ["sc", "query", "usbipd"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        output = (proc.stdout or "") + (proc.stderr or "")

        if "FAILED 1060" in output or "specified service does not exist" in output.lower():
            return "NOT_INSTALLED"

        for line in output.splitlines():
            line = line.strip()
            if line.startswith("STATE"):
                # e.g. "STATE              : 4  RUNNING"
                parts = line.split()
                if len(parts) >= 3:
                    return parts[-1].upper()
        return "UNKNOWN"
    except Exception as exc:
        logger.warning(f"Failed to query usbipd service state: {exc}")
        return "UNKNOWN"


def _wait_for_port(port: int, total_seconds: float = 10.0, interval: float = 0.5) -> bool:
    """Poll a TCP port until it's listening or timeout expires."""
    elapsed = 0.0
    while elapsed < total_seconds:
        time.sleep(interval)
        elapsed += interval
        if check_port_listening(port):
            return True
    return False


def ensure_service_running() -> tuple[bool, str]:
    """Ensure that the usbipd service is running and listening on port 3240.

    Robust against Windows Server edge cases:
    - Detects STOP_PENDING and waits for it to settle before starting
    - Tries forced restart (stop+start) if a plain start fails
    - Allows up to 10s for the port to bind (Windows Server boots slower)
    """
    if check_port_listening(3240):
        return True, "Service is listening on port 3240."

    logger.info("usbipd service is not listening on port 3240. Attempting recovery...")

    try:
        state = get_service_state()
        logger.info(f"usbipd service state: {state}")

        if state == "NOT_INSTALLED":
            return False, "usbipd Windows Service is not installed."

        # If STOP_PENDING, wait for the service to fully stop before starting
        if state == "STOP_PENDING":
            logger.info("Service is STOP_PENDING — waiting for it to settle...")
            for _ in range(20):  # up to 10s
                time.sleep(0.5)
                state = get_service_state()
                if state == "STOPPED":
                    break
                if state == "RUNNING" and check_port_listening(3240):
                    return True, "Service recovered (transitioned to RUNNING)."
            if state not in ("STOPPED", "RUNNING"):
                detail = f"Service stuck in {state} after 10s wait."
                logger.warning(detail)
                # Fall through to forced restart attempt below

        # If still not listening, attempt to start the service
        if not check_port_listening(3240):
            start_proc = subprocess.run(
                ["sc", "start", "usbipd"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            logger.info(f"sc start usbipd: rc={start_proc.returncode} stdout={start_proc.stdout.strip()!r} stderr={start_proc.stderr.strip()!r}")

            if _wait_for_port(3240, total_seconds=10.0):
                return True, "Service started and is listening on port 3240."

            # Plain start didn't work — refresh state and try forced restart (stop + start)
            # Include STOPPED: if sc start failed on a stopped service, a stop+start
            # cycle can clear a stuck state (common on Windows Server).
            state = get_service_state()
            if state in ("RUNNING", "START_PENDING", "STOP_PENDING", "STOPPED"):
                logger.warning("Plain start failed — attempting forced restart (stop + start)...")
                subprocess.run(
                    ["sc", "stop", "usbipd"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                # Wait for it to fully stop
                for _ in range(20):
                    time.sleep(0.5)
                    s = get_service_state()
                    if s == "STOPPED":
                        break

                subprocess.run(
                    ["sc", "start", "usbipd"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )

                if _wait_for_port(3240, total_seconds=10.0):
                    return True, "Service force-restarted and is listening on port 3240."

            detail = f"sc start stdout: {start_proc.stdout.strip()} stderr: {start_proc.stderr.strip()}"
            current_state = get_service_state()
            return False, (
                f"Could not start usbipd service. Current state: {current_state}. {detail}"
            )

        return True, "Service is now listening on port 3240."

    except subprocess.TimeoutExpired:
        return False, "Timed out waiting for usbipd service to respond."
    except Exception as exc:
        return False, f"Failed to manage usbipd service: {exc}"
