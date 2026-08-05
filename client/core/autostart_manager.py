import logging
import ntpath
import os
import subprocess
import sys
import tempfile
import winreg
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

BOOT_TASK_NAME = "USBRelayClientBoot"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "USBRelayClient"


def _registry_program_files_roots() -> tuple[str, ...]:
    """Read machine-owned Program Files roots from the 64-bit registry view."""

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
    # Environment variables are convenient for non-Windows unit tests, but a
    # Windows user can override a process environment variable.  On Windows,
    # only machine-owned registry values are authoritative; failing closed is
    # safer than accepting a user-controlled root.
    registry_roots = tuple(
        ntpath.normcase(ntpath.normpath(root.strip().strip('"')))
        for root in _registry_program_files_roots()
        if isinstance(root, str) and root.strip()
    )
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
    """Return whether *path* is beneath an installed Program Files root.

    The SYSTEM task must never consume a command or script from a source tree,
    LocalAppData, TEMP, or another user-controlled location.  Program Files is
    intentionally the only accepted root; its default ACL prevents a normal
    user from replacing the installed files.
    """

    candidate = str(path).strip().strip('"')
    if not ntpath.isabs(candidate):
        return False
    candidate = ntpath.normcase(ntpath.normpath(candidate))
    return any(
        candidate == root or candidate.startswith(root + "\\")
        for root in _program_files_roots()
    )


def _system_task_arguments_are_safe(base_arguments: list[str] | None) -> bool:
    """Allow no arguments so SYSTEM can never be pointed at a script/path.

    The frozen client needs only ``--headless``, which the renderer appends.
    Rejecting every caller-supplied argument is intentionally conservative: it
    also rejects relative paths and environment-variable expansions that cannot
    be proven safe with a string-only check.
    """

    return not base_arguments


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
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowHardTerminate>true</AllowHardTerminate>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>"""


def _render_boot_task_xml(exe_path: str, base_arguments: list[str] | None = None) -> str:
    command = exe_path.strip('"')
    arguments = subprocess.list2cmdline([*(base_arguments or []), "--headless"])
    return (
        _BOOT_TASK_XML
        .replace("{command}", escape(command))
        .replace("{arguments}", escape(arguments))
    )


def register_boot_task(exe_path: str, base_arguments: list[str] | None = None) -> bool:
    clean_path = exe_path.strip('"')
    if (
        ntpath.splitext(clean_path)[1].lower() != ".exe"
        or not _is_protected_system_path(clean_path)
    ):
        logger.error("Refusing SYSTEM boot task outside Program Files: %s", exe_path)
        return False
    if not _system_task_arguments_are_safe(base_arguments):
        logger.error("Refusing SYSTEM boot task with an unprotected path argument")
        return False

    try:
        xml = _render_boot_task_xml(exe_path, base_arguments)

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".xml")
        try:
            with open(tmp_fd, "wb") as f:
                # utf-16 automatically prepends the BOM (\xFF\xFE) that
                # schtasks /Create /XML requires to parse the encoding
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


def register_logon_run(exe_path: str, base_arguments: list[str] | None = None) -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        run_value = subprocess.list2cmdline([
            exe_path.strip(chr(34)),
            *(base_arguments or []),
            "--minimized",
        ])
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


def register_startup(
    exe_path: str,
    base_arguments: list[str] | None = None,
) -> tuple[bool, bool]:
    logon_ok = register_logon_run(exe_path, base_arguments)
    boot_ok = register_boot_task(exe_path, base_arguments)
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
