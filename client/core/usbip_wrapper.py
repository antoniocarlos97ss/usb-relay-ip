import logging
import os
import re
import shutil
import subprocess
import sys
from typing import Optional

from shared.constants import USBIP_EXE
from shared.models import AttachedDevice, CommandResult

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
            ["taskkill", "/F", "/IM", f"{USBIP_EXE}.exe"],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
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


def attach_device(host_ip: str, busid: str) -> CommandResult:
    returncode, stdout, stderr = _run_command(
        ["attach", "-r", host_ip, "-b", busid]
    )
    success = returncode == 0
    if success:
        message = f"Device {busid} attached from {host_ip}."
    else:
        detail = stderr.strip() or stdout.strip()
        message = f"Failed to attach {busid}: {detail}" if detail else f"Failed to attach device {busid} from {host_ip}."
    return CommandResult(success=success, message=message, stdout=stdout, stderr=stderr)


def detach_device(port: int) -> CommandResult:
    returncode, stdout, stderr = _run_command(
        ["detach", "-p", str(port)]
    )
    success = returncode == 0
    if success:
        message = f"Device on port {port} detached."
    else:
        message = f"Failed to detach device on port {port}."
    return CommandResult(success=success, message=message, stdout=stdout, stderr=stderr)


def list_attached() -> list[AttachedDevice]:
    returncode, stdout, _ = _run_command(["port"])
    if returncode != 0 or not stdout.strip():
        return []

    attached: list[AttachedDevice] = []
    for line in stdout.strip().splitlines():
        stripped = line.strip()
        busid_match = re.search(r"busid\s*[=:]\s*(\S+)", stripped, re.IGNORECASE)
        port_match = re.search(r"port\s*[=:]\s*(\d+)", stripped, re.IGNORECASE)
        vid_match = re.search(r"([0-9a-fA-F]{4})\s*:\s*([0-9a-fA-F]{4})", stripped)

        if port_match:
            port = int(port_match.group(1))
            busid = busid_match.group(1) if busid_match else ""
            vid, pid = "0000", "0000"
            if vid_match:
                vid = vid_match.group(1).lower()
                pid = vid_match.group(2).lower()

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
