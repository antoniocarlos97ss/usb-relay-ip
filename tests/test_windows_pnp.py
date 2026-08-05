import json
import multiprocessing
import os
import subprocess
import tempfile
import time
import unittest
import unittest.mock

from shared.models import AttachedDevice

from client.core.windows_pnp import (
    PnpSnapshot,
    _parse_statuses,
    clear_session_correlations,
    find_code43,
    find_descriptor_failure_code43,
    find_session_code43,
    find_unknown_code43,
    get_busid_for_instance_id,
    get_correlated_statuses,
    list_usb_devices,
    register_attached_session,
)


def _record_port_in_process(path: str, busid: str, port: int, start_event) -> None:
    import client.core.windows_pnp as windows_pnp

    with unittest.mock.patch.object(windows_pnp, "_session_path", return_value=path), \
         unittest.mock.patch.object(windows_pnp, "_legacy_session_path", return_value=path):
        windows_pnp._SESSION_CORRELATIONS.clear()
        windows_pnp._SESSION_CORRELATIONS_LOADED = False
        windows_pnp._SESSION_FILE_SIGNATURE = None
        start_event.wait(10)
        windows_pnp.record_session_port(busid, "1234", f"{port:04x}", port)


def _hold_session_lock_in_process(path: str, start_event, result_queue) -> None:
    import client.core.windows_pnp as windows_pnp

    with unittest.mock.patch.object(windows_pnp, "_session_path", return_value=path):
        start_event.wait(10)
        with windows_pnp._session_storage_lock(timeout=5):
            entered = time.monotonic()
            time.sleep(0.2)
            exited = time.monotonic()
        result_queue.put((entered, exited))


class WindowsPnpTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._path_patcher = unittest.mock.patch(
            "client.core.windows_pnp._session_path",
            return_value=os.path.join(self._tempdir.name, "sessions.json"),
        )
        self._path_patcher.start()
        clear_session_correlations()

    def tearDown(self):
        self._path_patcher.stop()
        self._tempdir.cleanup()

    def test_query_timeout_uses_bounded_wait_after_kill(self):
        class FakeProcess:
            returncode = -9

            def __init__(self):
                self.communicate_timeouts = []
                self.killed = False

            def communicate(self, timeout=None):
                self.communicate_timeouts.append(timeout)
                if len(self.communicate_timeouts) == 1:
                    raise subprocess.TimeoutExpired("powershell.exe", timeout)
                return "", ""

            def kill(self):
                self.killed = True

        process = FakeProcess()
        with (
            unittest.mock.patch("client.core.windows_pnp.sys.platform", "win32"),
            unittest.mock.patch("client.core.windows_pnp.subprocess.Popen", return_value=process),
            unittest.mock.patch.object(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
                create=True,
            ),
        ):
            self.assertIsNone(list_usb_devices(timeout=1, include_properties=False))

        self.assertTrue(process.killed)
        self.assertEqual([1, 2], process.communicate_timeouts)

    def test_query_timeout_after_kill_fails_closed_if_process_stays_alive(self):
        class HangingProcess:
            returncode = None

            def __init__(self):
                self.communicate_timeouts = []
                self.killed = False

            def communicate(self, timeout=None):
                self.communicate_timeouts.append(timeout)
                raise subprocess.TimeoutExpired("powershell.exe", timeout)

            def kill(self):
                self.killed = True

        process = HangingProcess()
        with (
            unittest.mock.patch("client.core.windows_pnp.sys.platform", "win32"),
            unittest.mock.patch("client.core.windows_pnp.subprocess.Popen", return_value=process),
            unittest.mock.patch.object(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
                create=True,
            ),
        ):
            result = list_usb_devices(timeout=1, include_properties=False)

        self.assertIsNone(result)
        self.assertTrue(process.killed)
        self.assertEqual([1, 2], process.communicate_timeouts)

    def test_parses_code43_and_vid_pid(self):
        payload = json.dumps([{
            "PNPDeviceID": r"USB\VID_1234&PID_ABCD\TOKEN",
            "Name": "License token",
            "ConfigManagerErrorCode": 43,
            "Status": "Error",
            "Parent": r"ROOT\USBIP\0001",
            "ContainerId": "{container}",
            "LocationPaths": ["PCIROOT(0)#PCI(1400)"] ,
        }])

        statuses = _parse_statuses(payload)

        self.assertEqual(1, len(statuses))
        self.assertEqual(("1234", "abcd"), (statuses[0].vid, statuses[0].pid))
        self.assertEqual(43, statuses[0].problem_code)
        self.assertEqual(r"ROOT\USBIP\0001", statuses[0].parent_instance_id)
        self.assertEqual(("PCIROOT(0)#PCI(1400)",), statuses[0].location_paths)
        self.assertEqual(statuses, find_code43("1234", "ABCD", statuses))

    def test_handles_single_object_and_unknown_device(self):
        payload = json.dumps({
            "PNPDeviceID": r"USB\UNKNOWN\1",
            "Name": "Device Descriptor Request Failed",
            "ConfigManagerErrorCode": 43,
            "Status": "Error",
        })

        status = _parse_statuses(payload)[0]

        self.assertEqual("", status.vid)
        self.assertEqual("", status.pid)
        self.assertEqual([], find_code43("1234", "abcd", [status]))
        self.assertEqual([status], find_unknown_code43([status]))

    def test_null_payload_is_empty(self):
        self.assertEqual([], _parse_statuses("null"))

    def test_invalid_problem_code_defaults_to_zero(self):
        status = _parse_statuses(json.dumps({
            "PNPDeviceID": r"USB\UNKNOWN\1",
            "ConfigManagerErrorCode": "invalid",
        }))[0]
        self.assertEqual(0, status.problem_code)

    def test_healthy_device_is_not_reported_as_code43(self):
        payload = json.dumps([{
            "PNPDeviceID": r"USB\VID_1234&PID_ABCD\TOKEN",
            "ConfigManagerErrorCode": 0,
        }])

        self.assertEqual([], find_code43("1234", "abcd", _parse_statuses(payload)))

    def test_register_attached_session_prefers_exact_vid_pid_component(self):
        before = PnpSnapshot(devices=tuple(_parse_statuses("[]")), observed_at=1.0)
        after_payload = json.dumps([
            {
                "PNPDeviceID": r"ROOT\USBIP\0001",
                "ConfigManagerErrorCode": 0,
                "ContainerId": "{c1}",
            },
            {
                "PNPDeviceID": r"USB\VID_1234&PID_ABCD\TOKEN",
                "ConfigManagerErrorCode": 0,
                "Parent": r"ROOT\USBIP\0001",
                "ContainerId": "{c1}",
            },
            {
                "PNPDeviceID": r"USB\VID_9999&PID_0001\OTHER",
                "ConfigManagerErrorCode": 0,
                "ContainerId": "{c2}",
            },
        ])

        with unittest.mock.patch("client.core.windows_pnp.sys.platform", "win32"), \
             unittest.mock.patch("client.core.windows_pnp.snapshot_usb_devices") as snapshot:
            snapshot.return_value = PnpSnapshot(devices=tuple(_parse_statuses(after_payload)), observed_at=2.0)
            ok, basis = register_attached_session("1-2", "1234", "abcd", before, poll_timeout=1)

        self.assertTrue(ok)
        self.assertEqual("vidpid-delta", basis)
        self.assertEqual("1-2", get_busid_for_instance_id(r"USB\VID_1234&PID_ABCD\TOKEN"))
        correlated = get_correlated_statuses("1-2", _parse_statuses(after_payload))
        self.assertEqual(
            {r"ROOT\USBIP\0001", r"USB\VID_1234&PID_ABCD\TOKEN"},
            {item.instance_id for item in correlated},
        )

    def test_register_attached_session_refuses_ambiguous_unknown_delta(self):
        before = PnpSnapshot(devices=tuple(_parse_statuses("[]")), observed_at=1.0)
        after_payload = json.dumps([
            {"PNPDeviceID": r"USB\UNKNOWN\A", "ConfigManagerErrorCode": 43, "ContainerId": "{c1}"},
            {"PNPDeviceID": r"USB\UNKNOWN\B", "ConfigManagerErrorCode": 43, "ContainerId": "{c2}"},
        ])

        with unittest.mock.patch("client.core.windows_pnp.sys.platform", "win32"), \
             unittest.mock.patch("client.core.windows_pnp.snapshot_usb_devices") as snapshot, \
             unittest.mock.patch("client.core.windows_pnp.time.sleep"):
            snapshot.return_value = PnpSnapshot(devices=tuple(_parse_statuses(after_payload)), observed_at=2.0)
            ok, reason = register_attached_session("1-2", "1234", "abcd", before, poll_timeout=1)

        self.assertFalse(ok)
        self.assertIn("unambiguous", reason)

    def test_find_session_code43_uses_mapping_for_unknown_device(self):
        before = PnpSnapshot(devices=tuple(_parse_statuses("[]")), observed_at=1.0)
        after_payload = json.dumps([
            {"PNPDeviceID": r"USB\UNKNOWN\1", "ConfigManagerErrorCode": 43},
        ])

        with unittest.mock.patch("client.core.windows_pnp.sys.platform", "win32"), \
             unittest.mock.patch("client.core.windows_pnp.snapshot_usb_devices") as snapshot:
            statuses = _parse_statuses(after_payload)
            snapshot.return_value = PnpSnapshot(devices=tuple(statuses), observed_at=2.0)
            ok, _ = register_attached_session("1-2", "1234", "abcd", before, poll_timeout=1)

        self.assertTrue(ok)
        matched = find_session_code43(
            "1-2",
            "1234",
            "abcd",
            statuses,
            [AttachedDevice(port=1, busid="1-2", vid="1234", pid="abcd")],
        )
        self.assertEqual([r"USB\UNKNOWN\1"], [item.instance_id for item in matched])

    def test_find_session_code43_refuses_identical_devices_without_mapping(self):
        statuses = _parse_statuses(json.dumps([
            {"PNPDeviceID": r"USB\VID_1234&PID_ABCD\A", "ConfigManagerErrorCode": 43},
        ]))

        matched = find_session_code43(
            "1-2",
            "1234",
            "abcd",
            statuses,
            [
                AttachedDevice(port=1, busid="1-1", vid="1234", pid="abcd"),
                AttachedDevice(port=2, busid="1-2", vid="1234", pid="abcd"),
            ],
        )

        self.assertEqual([], matched)

    def _register_session(self, busid: str, vid: str, pid: str, instance_id: str):
        before = PnpSnapshot(devices=tuple(), observed_at=1.0)
        payload = json.dumps([{"PNPDeviceID": instance_id, "ConfigManagerErrorCode": 0}])
        with unittest.mock.patch("client.core.windows_pnp.sys.platform", "win32"), \
             unittest.mock.patch("client.core.windows_pnp.snapshot_usb_devices") as snapshot:
            snapshot.return_value = PnpSnapshot(devices=tuple(_parse_statuses(payload)), observed_at=2.0)
            ok, _ = register_attached_session(busid, vid, pid, before, poll_timeout=1)
        self.assertTrue(ok)

    def test_find_unknown_code43_includes_descriptor_failure(self):
        statuses = _parse_statuses(json.dumps([{
            "PNPDeviceID": r"USB\VID_0000&PID_0002\5&104B56B8&0&2",
            "Name": "Unknown USB Device (Device Descriptor Request Failed)",
            "ConfigManagerErrorCode": 43,
            "Status": "Error",
        }]))

        self.assertEqual(statuses, find_unknown_code43(statuses))
        self.assertEqual(statuses, find_descriptor_failure_code43(statuses))
        self.assertEqual([], find_code43("1234", "abcd", statuses))

    def test_find_session_code43_attributes_reenumerated_descriptor_failure(self):
        self._register_session("1-2", "1234", "abcd", r"USB\VID_1234&PID_ABCD\TOKEN")
        statuses = _parse_statuses(json.dumps([{
            "PNPDeviceID": r"USB\VID_0000&PID_0002\5&104B56B8&0&2",
            "ConfigManagerErrorCode": 43,
        }]))

        matched = find_session_code43(
            "1-2",
            "1234",
            "abcd",
            statuses,
            [AttachedDevice(port=1, busid="1-2", vid="1234", pid="abcd")],
        )

        self.assertEqual(
            [r"USB\VID_0000&PID_0002\5&104B56B8&0&2"],
            [item.instance_id for item in matched],
        )

    def test_find_session_code43_ignores_descriptor_failure_while_session_node_present(self):
        self._register_session("1-2", "1234", "abcd", r"USB\VID_1234&PID_ABCD\TOKEN")
        statuses = _parse_statuses(json.dumps([
            {"PNPDeviceID": r"USB\VID_1234&PID_ABCD\TOKEN", "ConfigManagerErrorCode": 0},
            {"PNPDeviceID": r"USB\VID_0000&PID_0002\5&104B56B8&0&2", "ConfigManagerErrorCode": 43},
        ]))

        matched = find_session_code43(
            "1-2",
            "1234",
            "abcd",
            statuses,
            [AttachedDevice(port=1, busid="1-2", vid="1234", pid="abcd")],
        )

        self.assertEqual([], matched)

    def test_descriptor_failure_is_attributed_when_usbip_root_survives(self):
        before = PnpSnapshot(devices=tuple(), observed_at=1.0)
        attached_payload = json.dumps([
            {
                "PNPDeviceID": r"ROOT\USBIP\0001",
                "ConfigManagerErrorCode": 0,
                "ContainerId": "{c1}",
            },
            {
                "PNPDeviceID": r"USB\VID_1234&PID_ABCD\TOKEN",
                "ConfigManagerErrorCode": 0,
                "Parent": r"ROOT\USBIP\0001",
                "ContainerId": "{c1}",
            },
        ])
        with unittest.mock.patch("client.core.windows_pnp.sys.platform", "win32"), \
             unittest.mock.patch("client.core.windows_pnp.snapshot_usb_devices") as snapshot:
            snapshot.return_value = PnpSnapshot(
                devices=tuple(_parse_statuses(attached_payload)),
                observed_at=2.0,
            )
            ok, _ = register_attached_session("1-2", "1234", "abcd", before, poll_timeout=1)
        self.assertTrue(ok)

        failed_statuses = _parse_statuses(json.dumps([
            {"PNPDeviceID": r"ROOT\USBIP\0001", "ConfigManagerErrorCode": 0},
            {
                "PNPDeviceID": r"USB\VID_0000&PID_0002\5&104B56B8&0&2",
                "ConfigManagerErrorCode": 43,
            },
        ]))
        matched = find_session_code43(
            "1-2",
            "1234",
            "abcd",
            failed_statuses,
            [AttachedDevice(port=1, busid="1-2", vid="1234", pid="abcd")],
        )

        self.assertEqual(
            [r"USB\VID_0000&PID_0002\5&104B56B8&0&2"],
            [item.instance_id for item in matched],
        )

    def test_descriptor_failure_without_correlation_requires_single_session(self):
        statuses = _parse_statuses(json.dumps([{
            "PNPDeviceID": r"USB\VID_0000&PID_0002\5&104B56B8&0&2",
            "ConfigManagerErrorCode": 43,
        }]))
        single = [AttachedDevice(port=1, busid="1-2", vid="1234", pid="abcd")]
        multiple = single + [AttachedDevice(port=2, busid="1-3", vid="9999", pid="0001")]

        self.assertEqual(statuses, find_session_code43("1-2", "1234", "abcd", statuses, single))
        self.assertEqual([], find_session_code43("1-2", "1234", "abcd", statuses, multiple))

    def test_descriptor_failure_not_attributed_when_multiple_sessions_vanished(self):
        self._register_session("1-2", "1234", "abcd", r"USB\VID_1234&PID_ABCD\TOKEN")
        self._register_session("1-3", "9999", "0001", r"USB\VID_9999&PID_0001\OTHER")
        statuses = _parse_statuses(json.dumps([{
            "PNPDeviceID": r"USB\VID_0000&PID_0002\5&104B56B8&0&2",
            "ConfigManagerErrorCode": 43,
        }]))
        attached = [
            AttachedDevice(port=1, busid="1-2", vid="1234", pid="abcd"),
            AttachedDevice(port=2, busid="1-3", vid="9999", pid="0001"),
        ]

        self.assertEqual([], find_session_code43("1-2", "1234", "abcd", statuses, attached))
        self.assertEqual([], find_session_code43("1-3", "9999", "0001", statuses, attached))

    def test_port_only_record_does_not_confirm_identity_with_multiple_sessions(self):
        import client.core.windows_pnp as windows_pnp

        windows_pnp.record_session_port("1-2", "1234", "abcd", 7)
        statuses = _parse_statuses(json.dumps([{
            "PNPDeviceID": r"USB\VID_0000&PID_0002\FAILURE",
            "ConfigManagerErrorCode": 43,
        }]))
        attached = [
            AttachedDevice(port=7, busid="1-2", vid="1234", pid="abcd"),
            AttachedDevice(port=8, busid="1-3", vid="9999", pid="0001"),
        ]

        self.assertEqual([], find_session_code43("1-2", "1234", "abcd", statuses, attached))

    def test_recording_new_port_clears_stale_pnp_identity(self):
        import client.core.windows_pnp as windows_pnp

        self._register_session("1-2", "1234", "abcd", r"USB\VID_1234&PID_ABCD\OLD")
        windows_pnp.record_session_port("1-2", "1234", "abcd", 7)

        correlation = windows_pnp.get_session_correlation("1-2")
        self.assertIsNotNone(correlation)
        self.assertEqual((r"USB\VID_1234&PID_ABCD\OLD",), correlation.instance_ids)
        self.assertEqual(7, correlation.local_port)

    def test_port_only_record_allows_single_session_conservative_fallback(self):
        import client.core.windows_pnp as windows_pnp

        windows_pnp.record_session_port("1-2", "1234", "abcd", 7)
        statuses = _parse_statuses(json.dumps([{
            "PNPDeviceID": r"USB\VID_0000&PID_0002\FAILURE",
            "ConfigManagerErrorCode": 43,
        }]))
        attached = [AttachedDevice(port=7, busid="1-2", vid="1234", pid="abcd")]

        self.assertEqual(
            statuses,
            find_session_code43("1-2", "1234", "abcd", statuses, attached),
        )

    def test_session_storage_error_is_unknown_and_fails_closed(self):
        import client.core.windows_pnp as windows_pnp

        statuses = _parse_statuses(json.dumps([{
            "PNPDeviceID": r"USB\VID_0000&PID_0002\FAILURE",
            "ConfigManagerErrorCode": 43,
        }]))
        attached = [AttachedDevice(port=7, busid="1-2", vid="1234", pid="abcd")]
        with unittest.mock.patch.object(
            windows_pnp, "_session_storage_lock", side_effect=PermissionError("denied")
        ):
            self.assertIsNone(windows_pnp.get_session_correlation("1-2"))
            self.assertEqual([], find_session_code43("1-2", "1234", "abcd", statuses, attached))
            self.assertFalse(windows_pnp.record_session_port("1-2", "1234", "abcd", 7))

    def test_clear_session_correlations_persists_empty_store(self):
        import client.core.windows_pnp as windows_pnp

        windows_pnp.record_session_port("1-2", "1234", "abcd", 7)
        self.assertTrue(windows_pnp.clear_session_correlations())

        with open(os.path.join(self._tempdir.name, "sessions.json"), "r", encoding="utf-8") as stream:
            self.assertEqual([], json.load(stream))

    def test_atomic_session_write_flushes_and_fsyncs(self):
        import client.core.windows_pnp as windows_pnp

        with unittest.mock.patch.object(windows_pnp.os, "fsync") as fsync:
            windows_pnp.record_session_port("1-2", "1234", "abcd", 7)

        fsync.assert_called_once()

    def test_multiple_descriptor_failures_are_not_attributed(self):
        self._register_session("1-2", "1234", "abcd", r"USB\VID_1234&PID_ABCD\TOKEN")
        statuses = _parse_statuses(json.dumps([
            {"PNPDeviceID": r"USB\VID_0000&PID_0002\FAILURE-A", "ConfigManagerErrorCode": 43},
            {"PNPDeviceID": r"USB\VID_0000&PID_0002\FAILURE-B", "ConfigManagerErrorCode": 43},
        ]))
        attached = [AttachedDevice(port=7, busid="1-2", vid="1234", pid="abcd")]

        self.assertEqual([], find_session_code43("1-2", "1234", "abcd", statuses, attached))

    def test_duplicate_instance_ownership_is_ambiguous(self):
        instance_id = r"USB\VID_1234&PID_ABCD\TOKEN"
        self._register_session("1-2", "1234", "abcd", instance_id)
        self._register_session("1-3", "1234", "abcd", instance_id)

        self.assertIsNone(get_busid_for_instance_id(instance_id))

    def test_session_path_uses_programdata_for_cross_account_state(self):
        import client.core.windows_pnp as windows_pnp
        from shared.constants import CONFIG_DIR_NAME

        self._path_patcher.stop()
        try:
            with unittest.mock.patch.dict(
                os.environ,
                {"PROGRAMDATA": r"C:\ProgramData", "APPDATA": r"C:\Users\Operator\AppData\Roaming"},
                clear=False,
            ):
                path = windows_pnp._session_path()
        finally:
            self._path_patcher.start()

        self.assertEqual(
            os.path.join(r"C:\ProgramData", CONFIG_DIR_NAME, "pnp_sessions.json"),
            path,
        )

    def test_legacy_appdata_session_file_is_migrated_to_shared_storage(self):
        import client.core.windows_pnp as windows_pnp

        shared_path = os.path.join(self._tempdir.name, "shared", "pnp_sessions.json")
        legacy_path = os.path.join(self._tempdir.name, "user", "pnp_sessions.json")
        os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
        with open(legacy_path, "w", encoding="utf-8") as stream:
            json.dump([{
                "busid": "1-11",
                "vid": "1234",
                "pid": "abcd",
                "instance_ids": [r"USB\VID_1234&PID_ABCD\TOKEN"],
                "observed_at": time.time(),
                "basis": "legacy",
            }], stream)

        windows_pnp._SESSION_CORRELATIONS.clear()
        windows_pnp._SESSION_CORRELATIONS_LOADED = False
        with unittest.mock.patch.object(windows_pnp, "_session_path", return_value=shared_path), \
             unittest.mock.patch.object(
                 windows_pnp,
                 "_legacy_session_path",
                 return_value=legacy_path,
                 create=True,
             ):
            correlation = windows_pnp.get_session_correlation("1-11")

        self.assertIsNotNone(correlation)
        self.assertEqual("legacy", correlation.basis)
        self.assertTrue(os.path.exists(shared_path))

    def test_existing_shared_and_legacy_files_are_merged_by_newest_observation(self):
        import client.core.windows_pnp as windows_pnp

        shared_path = os.path.join(self._tempdir.name, "merge", "pnp_sessions.json")
        legacy_path = os.path.join(self._tempdir.name, "legacy", "pnp_sessions.json")
        os.makedirs(os.path.dirname(shared_path), exist_ok=True)
        os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
        now = time.time()
        with open(shared_path, "w", encoding="utf-8") as stream:
            json.dump([{
                "busid": "1-11", "vid": "1234", "pid": "abcd",
                "instance_ids": [r"USB\VID_1234&PID_ABCD\OLD"],
                "observed_at": now - 10, "basis": "shared-old",
            }], stream)
        with open(legacy_path, "w", encoding="utf-8") as stream:
            json.dump([
                {
                    "busid": "1-11", "vid": "1234", "pid": "abcd",
                    "instance_ids": [r"USB\VID_1234&PID_ABCD\NEW"],
                    "observed_at": now, "basis": "legacy-new",
                },
                {
                    "busid": "2-1", "vid": "5678", "pid": "ef01",
                    "instance_ids": [r"USB\VID_5678&PID_EF01\SECOND"],
                    "observed_at": now, "basis": "legacy-only",
                },
            ], stream)

        windows_pnp._SESSION_CORRELATIONS.clear()
        windows_pnp._SESSION_CORRELATIONS_LOADED = False
        with unittest.mock.patch.object(windows_pnp, "_session_path", return_value=shared_path), \
             unittest.mock.patch.object(windows_pnp, "_legacy_session_path", return_value=legacy_path):
            first = windows_pnp.get_session_correlation("1-11")
            second = windows_pnp.get_session_correlation("2-1")

        self.assertEqual("legacy-new", first.basis)
        self.assertEqual("legacy-only", second.basis)
        self.assertFalse(os.path.exists(legacy_path))
        self.assertTrue(os.path.exists(legacy_path + ".migrated"))

    def test_corrupt_shared_file_falls_back_to_valid_legacy_file(self):
        import client.core.windows_pnp as windows_pnp

        shared_path = os.path.join(self._tempdir.name, "corrupt", "pnp_sessions.json")
        legacy_path = os.path.join(self._tempdir.name, "corrupt-legacy", "pnp_sessions.json")
        os.makedirs(os.path.dirname(shared_path), exist_ok=True)
        os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
        with open(shared_path, "w", encoding="utf-8") as stream:
            stream.write("{not-json")
        with open(legacy_path, "w", encoding="utf-8") as stream:
            json.dump([{
                "busid": "1-11", "vid": "1234", "pid": "abcd",
                "instance_ids": [r"USB\VID_1234&PID_ABCD\TOKEN"],
                "observed_at": time.time(), "basis": "legacy-recovery",
            }], stream)

        windows_pnp._SESSION_CORRELATIONS.clear()
        windows_pnp._SESSION_CORRELATIONS_LOADED = False
        with unittest.mock.patch.object(windows_pnp, "_session_path", return_value=shared_path), \
             unittest.mock.patch.object(windows_pnp, "_legacy_session_path", return_value=legacy_path):
            correlation = windows_pnp.get_session_correlation("1-11")

        self.assertEqual("legacy-recovery", correlation.basis)
        self.assertTrue(os.path.exists(shared_path + ".corrupt"))
        self.assertTrue(os.path.exists(legacy_path + ".migrated"))

    def test_invalid_persisted_port_is_sanitized(self):
        import client.core.windows_pnp as windows_pnp

        path = os.path.join(self._tempdir.name, "invalid-port", "pnp_sessions.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            json.dump([{
                "busid": "1-11", "vid": "1234", "pid": "abcd",
                "instance_ids": [r"USB\VID_1234&PID_ABCD\TOKEN"],
                "observed_at": time.time(), "basis": "persisted", "local_port": 999,
            }], stream)

        windows_pnp._SESSION_CORRELATIONS.clear()
        windows_pnp._SESSION_CORRELATIONS_LOADED = False
        with unittest.mock.patch.object(windows_pnp, "_session_path", return_value=path), \
             unittest.mock.patch.object(windows_pnp, "_legacy_session_path", return_value=path):
            correlation = windows_pnp.get_session_correlation("1-11")

        self.assertIsNone(correlation.local_port)

    def test_session_cache_reloads_when_shared_file_changes(self):
        import client.core.windows_pnp as windows_pnp

        shared_path = os.path.join(self._tempdir.name, "shared-reload", "pnp_sessions.json")
        os.makedirs(os.path.dirname(shared_path), exist_ok=True)
        windows_pnp._SESSION_CORRELATIONS.clear()
        windows_pnp._SESSION_CORRELATIONS_LOADED = False

        with unittest.mock.patch.object(windows_pnp, "_session_path", return_value=shared_path), \
             unittest.mock.patch.object(windows_pnp, "_legacy_session_path", return_value=shared_path):
            self.assertIsNone(windows_pnp.get_session_correlation("1-11"))
            with open(shared_path, "w", encoding="utf-8") as stream:
                json.dump([{
                    "busid": "1-11",
                    "vid": "1234",
                    "pid": "abcd",
                    "instance_ids": [r"USB\VID_1234&PID_ABCD\TOKEN"],
                    "observed_at": time.time(),
                    "basis": "external-process",
                }], stream)
            correlation = windows_pnp.get_session_correlation("1-11")

        self.assertIsNotNone(correlation)
        self.assertEqual("external-process", correlation.basis)

    def test_local_port_hint_persists_across_process_memory(self):
        import client.core.windows_pnp as windows_pnp

        windows_pnp.record_session_port("1-11", "1234", "abcd", 7)
        windows_pnp._SESSION_CORRELATIONS.clear()
        windows_pnp._SESSION_CORRELATIONS_LOADED = False

        self.assertEqual(7, windows_pnp.get_session_port("1-11", "1234", "abcd"))

    def test_record_session_port_uses_interprocess_storage_lock(self):
        import client.core.windows_pnp as windows_pnp

        storage_lock = unittest.mock.MagicMock()
        with unittest.mock.patch.object(windows_pnp, "_session_storage_lock", storage_lock):
            windows_pnp.record_session_port("1-11", "1234", "abcd", 7)

        storage_lock.assert_called_once()
        self.assertTrue(storage_lock.return_value.__enter__.called)
        self.assertTrue(storage_lock.return_value.__exit__.called)

    def test_concurrent_process_writes_preserve_all_sessions(self):
        shared_path = os.path.join(self._tempdir.name, "multiprocess", "pnp_sessions.json")
        context = multiprocessing.get_context("spawn")
        start_event = context.Event()
        processes = [
            context.Process(
                target=_record_port_in_process,
                args=(shared_path, f"1-{index}", index, start_event),
            )
            for index in range(1, 9)
        ]
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(20)

        self.assertEqual([0] * len(processes), [process.exitcode for process in processes])
        with open(shared_path, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
        self.assertEqual(
            {f"1-{index}" for index in range(1, 9)},
            {item["busid"] for item in payload},
        )

    def test_session_storage_lock_serializes_processes(self):
        shared_path = os.path.join(self._tempdir.name, "lock", "pnp_sessions.json")
        context = multiprocessing.get_context("spawn")
        start_event = context.Event()
        result_queue = context.Queue()
        processes = [
            context.Process(
                target=_hold_session_lock_in_process,
                args=(shared_path, start_event, result_queue),
            )
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(10)

        self.assertEqual([0, 0], [process.exitcode for process in processes])
        first, second = sorted([result_queue.get(timeout=2), result_queue.get(timeout=2)])
        self.assertLessEqual(first[1], second[0])

    def test_session_correlation_persists_across_memory_reset(self):
        import client.core.windows_pnp as windows_pnp

        now = time.time()
        before = PnpSnapshot(devices=tuple(), observed_at=now - 1)
        statuses = _parse_statuses(json.dumps([{
            "PNPDeviceID": r"USB\VID_1234&PID_ABCD\TOKEN",
            "ConfigManagerErrorCode": 0,
        }]))
        with tempfile.TemporaryDirectory() as tmpdir, \
             unittest.mock.patch.object(windows_pnp, "_session_path", return_value=os.path.join(tmpdir, "sessions.json")), \
             unittest.mock.patch.object(windows_pnp.sys, "platform", "win32"), \
             unittest.mock.patch.object(windows_pnp, "snapshot_usb_devices", return_value=PnpSnapshot(tuple(statuses), now)):
            ok, _ = register_attached_session("1-2", "1234", "abcd", before, poll_timeout=1)
            self.assertTrue(ok)
            windows_pnp._SESSION_CORRELATIONS.clear()
            windows_pnp._SESSION_CORRELATIONS_LOADED = False
            self.assertEqual("1-2", get_busid_for_instance_id(statuses[0].instance_id))


if __name__ == "__main__":
    unittest.main()
