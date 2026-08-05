import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CI_DIR = ROOT / "ci"


class WindowsCiWorkflowTests(unittest.TestCase):
    def test_hardware_pilot_serializes_pydantic_attached_devices(self):
        from ci.windows_hardware_pilot import _serialize_attached
        from shared.models import AttachedDevice

        device = AttachedDevice(port=3, busid="1-11", vid="1234", pid="abcd")

        self.assertEqual(
            {"port": 3, "busid": "1-11", "vid": "1234", "pid": "abcd"},
            _serialize_attached(device),
        )

    def test_hosted_windows_validation_runs_on_two_pinned_windows_versions(self):
        source = (WORKFLOWS / "windows-validation.yml").read_text(encoding="utf-8")

        self.assertIn("windows-2022", source)
        self.assertIn("windows-2025", source)
        self.assertIn("python -m unittest discover -s tests -v", source)
        self.assertIn("python -m compileall -q client host shared tests ci", source)
        self.assertIn("ci\\windows_integration.ps1", source)
        self.assertIn("actions/checkout@v7", source)
        self.assertIn("actions/setup-python@v7", source)
        self.assertIn("permissions:\n  contents: read", source)
        self.assertNotIn("continue-on-error: true", source)

    def test_windows_integration_exercises_acl_system_task_and_pnp_query(self):
        source = (CI_DIR / "windows_integration.ps1").read_text(encoding="utf-8")
        probe = (CI_DIR / "windows_system_probe.py").read_text(encoding="utf-8")
        renderer = (CI_DIR / "render_system_task.py").read_text(encoding="utf-8")

        self.assertIn("set_shared_acl.ps1", source)
        self.assertIn("S-1-5-18", source)
        self.assertIn("S-1-5-32-544", source)
        self.assertIn("S-1-5-32-545", source)
        self.assertIn("schtasks.exe /Create", source)
        self.assertIn("schtasks.exe /Run", source)
        self.assertIn("schtasks.exe /Delete", source)
        self.assertIn("finally", source)
        self.assertIn("list_usb_devices", source)
        self.assertIn("try_acquire_named", probe)
        self.assertIn("_render_boot_task_xml", renderer)

    def test_hardware_pilot_is_manual_approved_and_self_hosted_only(self):
        source = (WORKFLOWS / "windows-hardware-pilot.yml").read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", source)
        self.assertNotIn("pull_request:", source)
        self.assertNotIn("\n  push:", source)
        self.assertIn("- self-hosted", source)
        self.assertIn("- usbip-pilot", source)
        self.assertIn("environment: usbip-pilot", source)
        self.assertIn("USBIP-PILOT", source)
        self.assertIn("secrets.USB_RELAY_PILOT_API_KEY", source)
        self.assertIn("ci/windows_hardware_pilot.py", source)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", source)
        self.assertIn("actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97", source)
        self.assertIn("actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", source)
        self.assertNotIn("--host '${{ inputs.host_ip }}'", source)
        self.assertIn("--host $env:PILOT_HOST", source)

    def test_hardware_pilot_scopes_api_key_to_the_execution_step(self):
        source = (WORKFLOWS / "windows-hardware-pilot.yml").read_text(encoding="utf-8")
        job_start = source.index("  verified-reconnect-cycle:")
        job_env_start = source.index("    env:", job_start)
        steps_start = source.index("    steps:", job_start)
        job_env = source[job_env_start:steps_start]
        execute_start = source.index("      - name: Execute one identity-checked recovery cycle")
        execute_end = source.index("      - name: Upload hardware pilot report", execute_start)
        execute_step = source[execute_start:execute_end]

        self.assertNotIn("USB_RELAY_PILOT_API_KEY:", job_env)
        self.assertIn("USB_RELAY_PILOT_API_KEY: ${{ secrets.USB_RELAY_PILOT_API_KEY }}", execute_step)

    def test_hardware_pilot_requires_protected_ref_and_sha_pinned_actions(self):
        source = (WORKFLOWS / "windows-hardware-pilot.yml").read_text(encoding="utf-8")

        self.assertIn("github.ref_protected", source)
        action_refs = re.findall(r"uses:\s+([^\s]+)", source)
        self.assertGreaterEqual(len(action_refs), 3)
        self.assertTrue(all(re.search(r"@[0-9a-f]{40}$", ref) for ref in action_refs))
        self.assertNotRegex(source, r"actions/[A-Za-z0-9_.-]+@v\d+")

    def test_hardware_pilot_docs_require_environment_protected_branch_policy(self):
        docs = (ROOT / "docs" / "windows-testing.md").read_text(encoding="utf-8")

        self.assertRegex(docs, r"(?i)Environment.*usbip-pilot")
        self.assertRegex(docs, r"(?i)protected branch")
        self.assertRegex(docs, r"(?i)required reviewers")

    def test_installer_build_runs_for_current_fix_branches_on_pinned_windows(self):
        source = (WORKFLOWS / "build-installers.yml").read_text(encoding="utf-8")

        self.assertIn('"fix/**"', source)
        self.assertIn("pull_request:", source)
        self.assertIn("runs-on: windows-2022", source)
        self.assertNotIn("windows-latest", source)
        self.assertIn("if-no-files-found: error", source)
        self.assertIn("actions/checkout@v7", source)
        self.assertIn("actions/setup-python@v7", source)
        self.assertIn("actions/upload-artifact@v7", source)
        self.assertIn("permissions:\n  contents: read", source)


if __name__ == "__main__":
    unittest.main()
