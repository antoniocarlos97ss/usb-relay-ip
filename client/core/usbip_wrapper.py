import logging
import re
import subprocess
import sys
from typing import Optional

from shared.constants import USBIPD_EXE
from shared.models import AttachedDevice, CommandResult

logger = logging.getLogger(__name__)


def _run_command(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return -1, "", f"Executable not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", "Command timed out"
    except Exception as exc:
        return -3, "", str(exc)


def is_available() -> bool:
    _, stdout, _ = _run_command([USBIPD_EXE, "--version"])
    return bool(stdout.strip()) and "usbipd" in stdout.lower()


def attach_device(host_ip: str, busid: str) -> CommandResult:
    returncode, stdout, stderr = _run_command(
        [USBIPD_EXE, "attach", "--remote", host_ip, "--busid", busid]
    )
    success = returncode == 0
    if success:
        message = f"Device {busid} attached from {host_ip}."
    else:
        message = f"Failed to attach device {busid} from {host_ip}."
    return CommandResult(success=success, message=message, stdout=stdout, stderr=stderr)


def detach_device(port: int) -> CommandResult:
    returncode, stdout, stderr = _run_command(
        [USBIPD_EXE, "detach", "--port", str(port)]
    )
    success = returncode == 0
    if success:
        message = f"Device on port {port} detached."
    else:
        message = f"Failed to detach device on port {port}."
    return CommandResult(success=success, message=message, stdout=stdout, stderr=stderr)


def list_attached() -> list[AttachedDevice]:
    returncode, stdout, _ = _run_command([USBIPD_EXE, "list"])
    if returncode != 0 or not stdout.strip():
        return []

    attached: list[AttachedDevice] = []
    lines = stdout.strip().splitlines()
    for line in lines:
        stripped = line.strip()
        match = re.match(
            r"(\d+-\d+)\s+(\S+)(?:\s+\S+)*\s+(.*?)\s+(Attached|Shared|Not shared)\s*$",
            stripped,
        )
        if match:
            busid, vid_pid, description, state = match.groups()
            if state == "Attached":
                vid, pid = "0000", "0000"
                vid_pid_match = re.match(r"([0-9a-fA-F]{4}):([0-9a-fA-F]{4})", vid_pid)
                if vid_pid_match:
                    vid = vid_pid_match.group(1).lower()
                    pid = vid_pid_match.group(2).lower()

                port_match = re.search(r"\(?port\s*(\d+)\)?", stripped, re.IGNORECASE)
                port = 0
                if port_match:
                    port = int(port_match.group(1))

                attached.append(AttachedDevice(
                    port=port,
                    busid=busid,
                    vid=vid,
                    pid=pid,
                ))
    return attached


def find_port_for_busid(busid: str) -> Optional[int]:
    attached = list_attached()
    for dev in attached:
        if dev.busid == busid:
            return dev.port
    return None
