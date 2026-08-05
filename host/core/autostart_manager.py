import logging
import ntpath
import os
import subprocess
import sys
import tempfile
import winreg
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

BOOT_TASK_NAME = "USBRelayHostBoot"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "USBRelayHost"


def _registry_program_files_roots() -> tuple[str, ...]:
    """Return machine-owned Program Files roots from the 64-bit registry view."""
    hklm = getattr(winreg, "HKEY_LOCAL_MACHINE", None)
    if hklm is None:
        return ()
    key = None
    roots: list[str] = []
    try:
        access = getattr(winreg, "KEY_READ", 0x20019)
        access |= getattr(winreg, "KEY_WOW64_64KEY", 0)
        key = winreg.OpenKey(
            hklm,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion",
            0,
            access,
        )
        for value_name in ("ProgramW6432Dir", "ProgramFilesDir", "ProgramFilesDir (x86)"):
            try:
                value, _ = winreg.QueryValueEx(key, value_name)
            except OSError:
                continue
            if isinstance(value, str) and value.strip():
                normalized = ntpath.normcase(ntpath.normpath(value.strip().strip('"')))
                if normalized not in roots:
                    roots.append(normalized)
    except (OSError, AttributeError, TypeError):
        return ()
    finally:
        if key is not None:
            try:
                winreg.CloseKey(key)
            except OSError:
                pass
    return tuple(roots)


def _program_files_roots() -> tuple[str, ...]:
    registry_roots = _registry_program_files_roots()
    if registry_roots:
        return registry_roots
    if os.name == "nt":
        return ()
    roots: list[str] = []
    for variable in (
        "ProgramW6432",
        "ProgramFiles",
        "PROGRAMFILES",
        "ProgramFiles(x86)",
        "PROGRAMFILES(X86)",
    ):
        value = os.environ.get(variable, "").strip().strip('"')
        if value:
            normalized = ntpath.normcase(ntpath.normpath(value))
            if normalized not in roots:
                roots.append(normalized)
    return tuple(roots)


def _is_protected_system_path(path: str) -> bool:
    candidate = str(path).strip().strip('"')
    if not ntpath.isabs(candidate) or ntpath.splitext(candidate)[1].lower() != ".exe":
        return False
    candidate = ntpath.normcase(ntpath.normpath(candidate))
    return any(
        candidate == root or candidate.startswith(root + "\\")
        for root in _program_files_roots()
    )


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
    <Description>USBRelayHost - boot-time headless API server and auto-bind</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <StartBoundary>2000-01-01T00:00:00</StartBoundary>
      <Delay>PT20S</Delay>
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
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{exe_path}</Command>
      <Arguments>--headless</Arguments>
    </Exec>
  </Actions>
</Task>"""


def register_boot_task(exe_path: str) -> bool:
    clean_path = exe_path.strip('"')
    if not _is_protected_system_path(clean_path):
        logger.error("Refusing SYSTEM boot task outside Program Files: %s", clean_path)
        return False
    try:
        xml = _BOOT_TASK_XML.replace("{exe_path}", escape(clean_path))

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".xml")
        try:
            with open(tmp_fd, "wb") as f:
                # utf-16 prepends BOM (\xFF\xFE) required by schtasks XML parser
                f.write(xml.encode("utf-16"))
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
            logger.info(f"Boot task created: {BOOT_TASK_NAME}")
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
        logger.info("Boot startup configured (schtasks BootTrigger SYSTEM)")
    return logon_ok, boot_ok


def unregister_startup() -> tuple[bool, bool]:
    logon_ok = unregister_logon_run()
    boot_ok = unregister_boot_task()
    return logon_ok, boot_ok


def is_logon_run_registered() -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE)
        winreg.QueryValueEx(key, RUN_VALUE)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False


def is_boot_task_registered() -> bool:
    success, stdout, stderr = _run_schtasks([
        "/Query",
        "/TN", BOOT_TASK_NAME,
    ])
    output = (stdout or "") + (stderr or "")
    if success:
        return True
    lowered = output.lower()
    if "cannot find the file specified" in lowered:
        return False
    if "the system cannot find the file specified" in lowered:
        return False
    if "error: the system cannot find the file specified" in lowered:
        return False
    return False


def is_registered() -> bool:
    return is_logon_run_registered() or is_boot_task_registered()
