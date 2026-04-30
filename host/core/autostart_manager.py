import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

SERVICE_NAME = "USBRelayHost"
NSSM_EXE_NAME = "nssm.exe"


def _get_nssm_path() -> str | None:
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    nssm_path = os.path.join(base_dir, NSSM_EXE_NAME)
    if os.path.exists(nssm_path):
        return nssm_path

    try:
        result = subprocess.run(
            ["where", NSSM_EXE_NAME],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()[0]
    except Exception:
        pass

    return None


def _run_nssm(args: list[str]) -> tuple[bool, str, str]:
    nssm = _get_nssm_path()
    if not nssm:
        return False, "", "NSSM not found"
    try:
        proc = subprocess.run(
            [nssm] + args,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return proc.returncode == 0, proc.stdout, proc.stderr
    except FileNotFoundError:
        return False, "", "NSSM executable not found"
    except subprocess.TimeoutExpired:
        return False, "", "NSSM command timed out"


def is_nssm_available() -> bool:
    return _get_nssm_path() is not None


def install_service(exe_path: str) -> bool:
    success, stdout, stderr = _run_nssm(["install", SERVICE_NAME, exe_path])
    if not success:
        logger.error(f"Failed to install service: {stderr}")
        return False

    _run_nssm(["set", SERVICE_NAME, "Start", "SERVICE_AUTO_START"])
    _run_nssm(["set", SERVICE_NAME, "AppDirectory", os.path.dirname(exe_path)])
    _run_nssm(["set", SERVICE_NAME, "DisplayName", "USBRelay Host Service"])

    logger.info("USBRelay Host service installed")
    return True


def uninstall_service() -> bool:
    success, stdout, stderr = _run_nssm(["remove", SERVICE_NAME, "confirm"])
    if success:
        logger.info("USBRelay Host service uninstalled")
    else:
        logger.warning(f"Failed to uninstall service: {stderr}")
    return success


def is_service_installed() -> bool:
    success, stdout, _ = _run_nssm(["status", SERVICE_NAME])
    return success


def start_service() -> bool:
    success, stdout, stderr = _run_nssm(["start", SERVICE_NAME])
    if success:
        logger.info("USBRelay Host service started")
    else:
        logger.warning(f"Failed to start service: {stderr}")
    return success


def stop_service() -> bool:
    success, stdout, stderr = _run_nssm(["stop", SERVICE_NAME])
    if success:
        logger.info("USBRelay Host service stopped")
    else:
        logger.warning(f"Failed to stop service: {stderr}")
    return success
