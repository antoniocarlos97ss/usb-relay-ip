import json
import logging
import os
import re
import shutil
import subprocess
import sys
from typing import Optional

from shared.constants import USBIPD_EXE, USBIPD_MIN_VERSION
from shared.models import CommandResult, UsbDevice

logger = logging.getLogger(__name__)


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
        proc = subprocess.run(
            full_args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return -1, "", f"Executable not found: {exe_path}"
    except subprocess.TimeoutExpired:
        return -2, "", "Command timed out"
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
