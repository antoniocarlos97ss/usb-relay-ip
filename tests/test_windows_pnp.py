import json
import os
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
    register_attached_session,
)


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
