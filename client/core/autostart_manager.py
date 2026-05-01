import logging
import os
import subprocess
import sys
import tempfile
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


_BOOT_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>USBRelayClient - boot-time headless auto-attach</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <StartBoundary>2000-01-01T00:00:00</StartBoundary>
      <Delay>PT30S</Delay>
      <Enabled>true</Enabled>
    </BootTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowHardTerminate>true</AllowHardTerminate>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{exe_path}</Command>
    </Exec>
  </Actions>
</Task>"""


def register_boot_task(exe_path: str) -> bool:
    try:
        clean_path = exe_path.strip('"')
        xml = _BOOT_TASK_XML.replace("{exe_path}", clean_path)

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".xml")
        try:
            with open(tmp_fd, "wb") as f:
                f.write(xml.encode("utf-16-le"))
            success, stdout, stderr = _run_schtasks([
                "/Create",
                "/TN", BOOT_TASK_NAME,
                "/XML", tmp_path,
                "/F",
            ])
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        if success:
            logger.info(f"Boot task created via XML: {BOOT_TASK_NAME}")
        else:
            logger.warning(f"Boot task failed: {stderr}")
        return success

    except Exception as exc:
        logger.error(f"Failed to create boot task: {exc}")
        return False


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
        run_value = f'"{exe_path.strip(chr(34))}" --minimized'
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, run_value)
        winreg.CloseKey(key)
        logger.info(f"Logon Run key added: {RUN_VALUE}={run_value}")
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


def register_startup(exe_path: str) -> tuple[bool, bool]:
    logon_ok = register_logon_run(exe_path)
    boot_ok = register_boot_task(exe_path)
    if logon_ok:
        logger.info("Logon startup configured (HKCU Run)")
    if boot_ok:
        logger.info("Boot startup configured (schtasks ONSTART SYSTEM)")
    return logon_ok, boot_ok


def unregister_startup() -> tuple[bool, bool]:
    logon_ok = unregister_logon_run()
    boot_ok = unregister_boot_task()
    return logon_ok, boot_ok


def is_registered() -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE)
        winreg.QueryValueEx(key, RUN_VALUE)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False
