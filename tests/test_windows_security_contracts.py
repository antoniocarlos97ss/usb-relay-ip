import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
BUILD = ROOT / "build"


class WindowsInstallerSecurityContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = (BUILD / "installer_client.nsi").read_text(encoding="utf-8")
        cls.acl_script = (BUILD / "set_shared_acl.ps1").read_text(encoding="utf-8")

    def test_client_payload_and_system_task_use_protected_install_root(self):
        match = re.search(r'!define INSTALL_DIR "([^"]+)"', self.installer)
        self.assertIsNotNone(match)
        install_dir = match.group(1)

        self.assertIn("$PROGRAMFILES", install_dir.upper())
        self.assertNotIn("$LOCALAPPDATA", install_dir.upper())
        self.assertIn("schtasks.exe /Delete /TN", self.installer)

    def test_shared_state_is_acl_protected_before_legacy_migration(self):
        acl_call = self.installer.index("set_shared_acl.ps1")
        self.assertIn("-LegacyRoot", self.installer)
        migration_marker = self.installer.index("-LegacyRoot")

        self.assertLess(acl_call, migration_marker)
        self.assertNotIn("CopyFiles /SILENT", self.installer)

    def test_acl_walk_rejects_reparse_points_without_recursive_follow(self):
        self.assertIn("Set-StrictMode", self.acl_script)
        self.assertIn("ReparsePoint", self.acl_script)
        self.assertIn("EnumerateFileSystemInfos", self.acl_script)
        self.assertIn("throw", self.acl_script)
        self.assertNotRegex(self.acl_script, r"Get-ChildItem[^\n]*-Recurse")
        self.assertIn("[switch]$RemoveTarget", self.acl_script)

    def test_acl_allowlist_has_only_system_admin_and_installer_sid(self):
        self.assertIn("S-1-5-18", self.acl_script)
        self.assertIn("S-1-5-32-544", self.acl_script)
        self.assertIn("WindowsIdentity]::GetCurrent().User", self.acl_script)
        self.assertNotIn("S-1-5-32-545", self.installer + self.acl_script)
        self.assertNotRegex(self.acl_script, r"Builtin\\Users|BUILTIN\\Users")

    def test_shared_state_validator_is_bound_to_real_programdata_and_appdata(self):
        self.assertIn("GetFolderPath", self.acl_script)
        self.assertIn("CommonApplicationData", self.acl_script)
        self.assertIn("ApplicationData", self.acl_script)
        self.assertIn("OrdinalIgnoreCase", self.acl_script)
        self.assertIn("StartsWith", self.acl_script)
        self.assertIn("function Remove-Win32ExtendedPathPrefix", self.acl_script)

    def test_installer_helper_provisions_the_protected_operation_lock_directory(self):
        self.assertIn("'operation-locks'", self.acl_script)
        self.assertIn("CreateDirectory($operationLocks.FullName)", self.acl_script)

    def test_acl_operations_recheck_nodes_before_set_or_remove(self):
        self.assertIn("Get-Item -LiteralPath", self.acl_script)
        self.assertIn("Assert-NoReparseAncestors", self.acl_script)
        self.assertIn("Set-Acl -LiteralPath", self.acl_script)
        self.assertIn("Remove-Item -LiteralPath", self.acl_script)

    def test_legacy_appdata_root_has_a_separate_safe_validator(self):
        self.assertIn("function Get-LegacyStateDirectory", self.acl_script)
        self.assertIn("$legacy = Get-LegacyStateDirectory", self.acl_script)
        self.assertNotIn(
            "$legacy = Get-SharedStateDirectory -Path $LegacyRoot",
            self.acl_script,
        )

    def test_uninstall_prompts_for_api_key_state_and_silent_mode_is_documented(self):
        uninstall = self.installer[self.installer.index('Section "Uninstall"'):]

        self.assertIn("MB_YESNO", uninstall)
        self.assertIn("IfSilent", uninstall)
        self.assertIn("-RemoveTarget", uninstall)
        self.assertIn("$SharedStateRoot", uninstall)
        self.assertNotIn("$COMMONAPPDATA", self.installer)
        self.assertNotIn('RMDir /r "$SharedStateRoot"', uninstall)

    def test_install_abort_cleanup_is_scoped_and_reparse_safe(self):
        self.assertIn("Function .onInstFailed", self.installer)
        self.assertIn("!define MUI_CUSTOMFUNCTION_ABORT CleanupPartialInstall", self.installer)
        self.assertNotIn("Function .onUserAbort", self.installer)
        self.assertIn("CleanupPartialInstall", self.installer)
        self.assertIn("$SharedStateCreated", self.installer)
        self.assertIn("-RemoveTarget", self.installer)
        self.assertNotIn('RMDir /r "$SharedStateRoot"', self.installer)

    def test_programdata_uses_supported_nsis_shell_context(self):
        self.assertIn("Var SharedStateRoot", self.installer)
        self.assertIn("SetShellVarContext all", self.installer)
        self.assertIn('StrCpy $SharedStateRoot "$APPDATA\\USBRelay"', self.installer)
        self.assertNotIn("$COMMONAPPDATA", self.installer)


if __name__ == "__main__":
    unittest.main()
