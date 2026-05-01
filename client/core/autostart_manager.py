import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

TASK_NAME = "USBRelayClient"


def _run_schtasks(args: list[str]) -> tuple[bool, str, str]:
    try:
        proc = subprocess.run(
            ["schtasks"] + args,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return proc.returncode == 0, proc.stdout, proc.stderr
    except FileNotFoundError:
        return False, "", "schtasks not found"
    except subprocess.TimeoutExpired:
        return False, "", "schtasks command timed out"


def register_startup(exe_path: str) -> bool:
    success, stdout, stderr = _run_schtasks([
        "/Create",
        "/TN", TASK_NAME,
        "/TR", f'"{exe_path}"',
        "/SC", "ONSTART",
        "/RU", "SYSTEM",
        "/RL", "HIGHEST",
        "/F",
    ])
    if success:
        logger.info(f"USBRelay Client startup task created: {TASK_NAME}")
    else:
        logger.error(f"Failed to create startup task: {stderr}")
    return success


def unregister_startup() -> bool:
    success, stdout, stderr = _run_schtasks([
        "/Delete",
        "/TN", TASK_NAME,
        "/F",
    ])
    if success:
        logger.info(f"USBRelay Client startup task deleted: {TASK_NAME}")
    else:
        logger.warning(f"Failed to delete startup task: {stderr}")
    return success


def is_registered() -> bool:
    success, stdout, _ = _run_schtasks(["/Query", "/TN", TASK_NAME])
    return success
