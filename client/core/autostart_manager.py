import logging
import subprocess
import sys
import winreg

logger = logging.getLogger(__name__)

BOOT_TASK_NAME = "USBRelayClientBoot"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "USBRelayClient"


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


def register_boot_task(exe_path: str) -> bool:
    success, stdout, stderr = _run_schtasks([
        "/Create",
        "/TN", BOOT_TASK_NAME,
        "/TR", f'"{exe_path}"',
        "/SC", "ONSTART",
        "/RU", "SYSTEM",
        "/RL", "HIGHEST",
        "/F",
    ])
    if success:
        logger.info(f"Boot task created: {BOOT_TASK_NAME}")
    else:
        logger.warning(f"Boot task failed (may need admin): {stderr}")
    return success


def unregister_boot_task() -> bool:
    success, stdout, stderr = _run_schtasks([
        "/Delete",
        "/TN", BOOT_TASK_NAME,
        "/F",
    ])
    if success:
        logger.info(f"Boot task deleted: {BOOT_TASK_NAME}")
    else:
        logger.warning(f"Failed to delete boot task: {stderr}")
    return success


def register_logon_run(exe_path: str) -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, exe_path)
        winreg.CloseKey(key)
        logger.info(f"Logon Run key added: {RUN_VALUE}={exe_path}")
        return True
    except Exception as exc:
        logger.error(f"Failed to set logon Run key: {exc}")
        return False


def unregister_logon_run() -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, RUN_VALUE)
        winreg.CloseKey(key)
        logger.info(f"Logon Run key removed: {RUN_VALUE}")
        return True
    except FileNotFoundError:
        return True
    except Exception as exc:
        logger.warning(f"Failed to remove logon Run key: {exc}")
        return False


def register_startup(exe_path: str) -> bool:
    logon_ok = register_logon_run(exe_path)
    boot_ok = register_boot_task(exe_path)
    if logon_ok:
        logger.info("Logon startup configured (HKCU Run)")
    if boot_ok:
        logger.info("Boot startup configured (schtasks ONSTART SYSTEM)")
    return logon_ok


def unregister_startup() -> bool:
    unregister_logon_run()
    unregister_boot_task()
    return True


def is_registered() -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE)
        winreg.QueryValueEx(key, RUN_VALUE)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False
