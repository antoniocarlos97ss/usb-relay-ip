import unittest
from unittest.mock import patch

from client.core.usbip_wrapper import AttachedDevicesQuery
from shared.models import AttachedDevice, CommandResult


class TestUsbipWrapper(unittest.TestCase):
    def setUp(self):
        patch("client.core.usbip_wrapper._run_command", return_value=(0, "", "")).start()
        self.addCleanup(patch.stopall)

    def test_is_available_true(self):
        with patch("client.core.usbip_wrapper._find_usbip") as mock_find:
            mock_find.return_value = "C:\\USBip\\usbip.exe"
            from client.core.usbip_wrapper import is_available
            self.assertTrue(is_available())

    def test_is_available_false(self):
        with patch("client.core.usbip_wrapper._find_usbip") as mock_find:
            mock_find.return_value = None
            from client.core.usbip_wrapper import is_available
            self.assertFalse(is_available())

    def test_attach_device_success(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, "attached", "")
            from client.core.usbip_wrapper import attach_device
            result = attach_device("192.168.1.10", "1-5")
            self.assertTrue(result.success)
            self.assertIn("attached", result.message)

    def test_attach_device_failure(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (1, "", "connection refused")
            from client.core.usbip_wrapper import attach_device
            result = attach_device("192.168.1.10", "1-5")
            self.assertFalse(result.success)

    def test_attach_device_records_windows_pnp_correlation_when_identity_known(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run, \
             patch("client.core.usbip_wrapper.sys.platform", "win32"), \
             patch("client.core.windows_pnp.snapshot_usb_devices") as snapshot, \
             patch("client.core.windows_pnp.register_attached_session") as register:
            mock_run.return_value = (0, "attached", "")
            snapshot.return_value = object()
            register.return_value = (True, "vidpid-delta")
            from client.core.usbip_wrapper import attach_device
            result = attach_device("192.168.1.10", "1-5", vid="1234", pid="abcd")

            self.assertTrue(result.success)
            snapshot.assert_called_once()
            register.assert_called_once()

    def test_attach_device_persists_assigned_port_for_other_windows_processes(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run, \
             patch("client.core.usbip_wrapper.sys.platform", "win32"), \
             patch("client.core.windows_pnp.snapshot_usb_devices") as snapshot, \
             patch("client.core.windows_pnp.record_session_port") as record_port, \
             patch("client.core.windows_pnp.register_attached_session") as register:
            mock_run.return_value = (0, "succesfully attached to port 7\r\n", "")
            snapshot.return_value = object()
            register.return_value = (True, "vidpid-delta")
            from client.core.usbip_wrapper import attach_device

            result = attach_device("192.168.1.10", "1-11", vid="1234", pid="abcd")

        self.assertTrue(result.success)
        record_port.assert_called_once_with("1-11", "1234", "abcd", 7)
        self.assertEqual(7, register.call_args.kwargs["local_port"])
        self.assertEqual(12, register.call_args.kwargs["poll_timeout"])

    def test_attach_invalidates_stale_correlation_even_without_snapshot_or_port(self):
        with patch("client.core.usbip_wrapper._run_command", return_value=(0, "attached", "")), \
             patch("client.core.usbip_wrapper.sys.platform", "win32"), \
             patch("client.core.windows_pnp.remove_session_correlation") as remove, \
             patch("client.core.windows_pnp.snapshot_usb_devices", return_value=None):
            from client.core.usbip_wrapper import attach_device

            result = attach_device("192.168.1.10", "1-11", vid="1234", pid="abcd")

        self.assertTrue(result.success)
        remove.assert_called_once_with("1-11")

    def test_attach_refuses_when_pnp_correlation_cannot_be_invalidated(self):
        with patch("client.core.usbip_wrapper._run_command") as run, \
             patch("client.core.usbip_wrapper.sys.platform", "win32"), \
             patch("client.core.windows_pnp.remove_session_correlation", return_value=False), \
             patch("client.core.windows_pnp.snapshot_usb_devices") as snapshot:
            snapshot.return_value = object()
            run.return_value = (0, "attached", "")
            from client.core.usbip_wrapper import attach_device

            result = attach_device("192.168.1.10", "1-11", vid="1234", pid="abcd")

        self.assertFalse(result.success)
        run.assert_not_called()

    @patch("client.core.usbip_wrapper.operation_coordinator.release_named", create=True)
    @patch("client.core.usbip_wrapper.operation_coordinator.acquire_named", return_value=True, create=True)
    @patch("client.core.usbip_wrapper._attach_device_locked")
    def test_attach_uses_cross_process_global_transport_lock(
        self, attach_locked, acquire_named, release_named
    ):
        from client.core.usbip_wrapper import attach_device

        attach_locked.return_value = CommandResult(success=True, message="attached")

        result = attach_device("10.0.0.10", "1-11", timeout=9, vid="1234", pid="abcd")

        self.assertTrue(result.success)
        acquire_named.assert_called_once_with("usbip-transport", "global", timeout=9)
        release_named.assert_called_once_with("usbip-transport", "global")

    def test_detach_device_success(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, "detached", "")
            from client.core.usbip_wrapper import _detach_device_by_port
            result = _detach_device_by_port(3)
            self.assertTrue(result.success)

    def test_detach_device_failure(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (1, "", "device not found")
            from client.core.usbip_wrapper import _detach_device_by_port
            result = _detach_device_by_port(99)
            self.assertFalse(result.success)

    def test_kill_all_subprocesses_only_kills_owned_processes(self):
        import client.core.usbip_wrapper as usbip_wrapper

        owned = unittest.mock.Mock()
        usbip_wrapper._subprocesses[:] = [owned]
        with patch("client.core.usbip_wrapper.sys.platform", "win32"), \
             patch("client.core.usbip_wrapper.subprocess.CREATE_NO_WINDOW", 0, create=True), \
             patch("client.core.usbip_wrapper.subprocess.run") as global_taskkill:
            usbip_wrapper.kill_all_subprocesses()

        owned.kill.assert_called_once()
        global_taskkill.assert_not_called()
        self.assertEqual([], usbip_wrapper._subprocesses)

    def test_subprocess_cleanup_filters_by_owner_thread(self):
        import client.core.usbip_wrapper as usbip_wrapper

        first = unittest.mock.Mock()
        second = unittest.mock.Mock()
        with patch("client.core.usbip_wrapper.threading.get_ident", side_effect=[101, 202]):
            usbip_wrapper._register_proc(first)
            usbip_wrapper._register_proc(second)

        usbip_wrapper.kill_all_subprocesses({101})

        first.kill.assert_called_once()
        second.kill.assert_not_called()
        usbip_wrapper.kill_all_subprocesses()

    def test_list_attached_parses_correctly(self):
        text_output = (
            "port=3 busid=1-5 046d:c31c\n"
        )
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, text_output, "")
            from client.core.usbip_wrapper import list_attached
            attached = list_attached()
            self.assertEqual(len(attached), 1)
            self.assertEqual(attached[0].busid, "1-5")
            self.assertEqual(attached[0].port, 3)

    def test_list_attached_parses_usbip_win2_0977_multiline_output(self):
        text_output = (
            "Imported USB devices\r\n"
            "====================\r\n"
            "Port 03: device in use at High Speed(480Mbps)\r\n"
            "         ACME : Token (1234:abcd)\r\n"
            "           -> usbip://10.0.0.10:3240/1-11\r\n"
            "           -> remote bus/dev 001/011\r\n"
            "Port 04: device in use at Full Speed(12Mbps)\r\n"
            "         Widget (5678:ef01)\r\n"
            "           -> usbip://server.example:3240/2-4.1\r\n"
            "           -> remote bus/dev 002/004\r\n"
        )
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, text_output, "")
            from client.core.usbip_wrapper import list_attached

            attached = list_attached()

        self.assertEqual(
            [(3, "1-11", "1234", "abcd"), (4, "2-4.1", "5678", "ef01")],
            [(item.port, item.busid, item.vid, item.pid) for item in attached],
        )

    def test_list_attached_empty(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (1, "", "")
            from client.core.usbip_wrapper import list_attached
            attached = list_attached()
            self.assertEqual(len(attached), 0)

    def test_attached_query_distinguishes_command_failure_from_empty_result(self):
        from client.core.usbip_wrapper import query_attached_devices

        with patch("client.core.usbip_wrapper._run_command", return_value=(1, "", "driver error")):
            failed = query_attached_devices()
        with patch("client.core.usbip_wrapper._run_command", return_value=(0, "Imported USB devices\n", "")):
            empty = query_attached_devices()

        self.assertFalse(failed.success)
        self.assertEqual((), failed.devices)
        self.assertIn("driver error", failed.error)
        self.assertTrue(empty.success)
        self.assertEqual((), empty.devices)

    def test_attached_query_accepts_real_upstream_blank_success(self):
        from client.core.usbip_wrapper import query_attached_devices

        with patch("client.core.usbip_wrapper._run_command", return_value=(0, "", "")):
            query = query_attached_devices()

        self.assertTrue(query.success)
        self.assertEqual((), query.devices)

    def test_detach_busid_removes_persisted_session_after_success(self):
        live = AttachedDevicesQuery(
            True,
            (AttachedDevice(port=3, busid="1-11", vid="1234", pid="abcd"),),
        )
        with patch("client.core.usbip_wrapper.query_attached_devices", return_value=live), \
             patch("client.core.usbip_wrapper._detach_device_by_port") as detach, \
             patch("client.core.windows_pnp.remove_session_correlation") as remove:
            detach.return_value = CommandResult(success=True, message="detached")
            from client.core.usbip_wrapper import detach_busid

            result = detach_busid(
                "1-11",
                port_hint=3,
                expected_vid="1234",
                expected_pid="abcd",
            )

        self.assertTrue(result.success)
        detach.assert_called_once_with(3, timeout=30)
        remove.assert_called_once_with("1-11")

    def test_detach_busid_rejects_live_identity_mismatch(self):
        replacement = AttachedDevicesQuery(
            True,
            (AttachedDevice(port=3, busid="1-11", vid="9999", pid="0001"),),
        )
        with patch("client.core.usbip_wrapper.query_attached_devices", return_value=replacement), \
             patch("client.core.usbip_wrapper._detach_device_by_port") as detach:
            from client.core.usbip_wrapper import detach_busid

            result = detach_busid("1-11", expected_vid="1234", expected_pid="abcd")

        self.assertFalse(result.success)
        detach.assert_not_called()

    @patch("client.core.usbip_wrapper.query_attached_devices")
    def test_detach_busid_requires_expected_identity(self, query):
        query.return_value = AttachedDevicesQuery(
            True,
            (AttachedDevice(port=3, busid="1-11", vid="1234", pid="abcd"),),
        )

        from client.core.usbip_wrapper import detach_busid

        result = detach_busid("1-11")

        self.assertFalse(result.success)
        self.assertIn("identity", result.message.lower())
        query.assert_not_called()

    def test_detach_busid_revalidates_port_and_identity_before_mutation(self):
        target = AttachedDevicesQuery(
            True,
            (AttachedDevice(port=3, busid="1-11", vid="1234", pid="abcd"),),
        )
        replacement = AttachedDevicesQuery(
            True,
            (AttachedDevice(port=3, busid="2-7", vid="9999", pid="0001"),),
        )
        with patch(
            "client.core.usbip_wrapper.query_attached_devices",
            side_effect=[target, replacement],
        ), patch("client.core.usbip_wrapper._detach_device_by_port") as detach:
            from client.core.usbip_wrapper import detach_busid

            result = detach_busid("1-11", expected_vid="1234", expected_pid="abcd")

        self.assertFalse(result.success)
        detach.assert_not_called()

    def test_find_port_for_busid_found(self):
        text_output = (
            "port=3 busid=1-5 046d:c31c\n"
        )
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, text_output, "")
            from client.core.usbip_wrapper import find_port_for_busid
            port = find_port_for_busid("1-5")
            self.assertEqual(port, 3)

    def test_find_port_rejects_hint_when_port_block_lost_busid(self):
        text_output = (
            "Imported USB devices\r\n"
            "====================\r\n"
            "Port 07: device in use at High Speed(480Mbps)\r\n"
            "         Unknown USB Device (0000:0002)\r\n"
        )
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, text_output, "")
            from client.core.usbip_wrapper import find_port_for_busid

            port = find_port_for_busid("1-11", port_hint=7)

        self.assertIsNone(port)

    def test_find_port_rejects_shared_hint_when_live_busid_is_unknown(self):
        text_output = (
            "Imported USB devices\r\n"
            "====================\r\n"
            "Port 07: device in use at High Speed(480Mbps)\r\n"
            "         Unknown USB Device (0000:0002)\r\n"
        )
        with patch("client.core.usbip_wrapper._run_command") as mock_run, \
             patch("client.core.usbip_wrapper.sys.platform", "win32"), \
             patch("client.core.windows_pnp.get_session_port", return_value=7):
            mock_run.return_value = (0, text_output, "")
            from client.core.usbip_wrapper import find_port_for_busid

            port = find_port_for_busid("1-11")

        self.assertIsNone(port)

    def test_find_port_rejects_duplicate_live_busid(self):
        text_output = (
            "port=3 busid=1-11 1234:abcd\n"
            "port=4 busid=1-11 1234:abcd\n"
        )
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, text_output, "")
            from client.core.usbip_wrapper import find_port_for_busid

            port = find_port_for_busid("1-11")

        self.assertIsNone(port)

    def test_attach_port_parser_rejects_out_of_range_values(self):
        from client.core.usbip_wrapper import parse_attach_port

        self.assertIsNone(parse_attach_port("attached to port 0"))
        self.assertIsNone(parse_attach_port("attached to port 256"))
        self.assertEqual(255, parse_attach_port("attached to port 255"))

    def test_list_attached_ignores_out_of_range_ports(self):
        text_output = (
            "port=0 busid=1-1 1234:abcd\n"
            "port=256 busid=1-2 1234:abcd\n"
            "port=255 busid=1-3 1234:abcd\n"
        )
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, text_output, "")
            from client.core.usbip_wrapper import list_attached

            attached = list_attached()

        self.assertEqual([(255, "1-3")], [(item.port, item.busid) for item in attached])

    def test_find_port_for_busid_not_found(self):
        with patch("client.core.usbip_wrapper._run_command") as mock_run:
            mock_run.return_value = (0, "BUSID  VID:PID  STATE\n", "")
            from client.core.usbip_wrapper import find_port_for_busid
            port = find_port_for_busid("nonexistent")
            self.assertIsNone(port)


if __name__ == "__main__":
    unittest.main()
