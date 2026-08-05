import unittest
from pathlib import Path

from shared.models import AttachedDevice, UsbDevice


class ClientLifecycleTests(unittest.TestCase):
    def test_shutdown_resolver_enumerates_live_session_beyond_port_cache(self):
        from client.core.lifecycle import resolve_live_shutdown_sessions

        live = [AttachedDevice(port=9, busid="1-9", vid="1234", pid="abcd")]
        host = [UsbDevice(busid="1-9", vid="1234", pid="abcd", description="Token", state="Attached")]

        sessions, rejected = resolve_live_shutdown_sessions(
            live,
            host_devices=host,
            cached_devices=[],
            host_reachable=True,
        )

        self.assertEqual([(9, "1-9", "1234", "abcd")], [
            (item.port, item.busid, item.vid, item.pid) for item in sessions
        ])
        self.assertEqual([], rejected)

    def test_shutdown_resolver_fails_closed_on_changed_or_ambiguous_identity(self):
        from client.core.lifecycle import resolve_live_shutdown_sessions

        live = [AttachedDevice(port=9, busid="1-9", vid="1234", pid="abcd")]
        changed = [UsbDevice(busid="1-9", vid="9999", pid="0001", description="Replacement")]
        sessions, rejected = resolve_live_shutdown_sessions(
            live,
            host_devices=changed,
            cached_devices=[],
            host_reachable=True,
        )
        self.assertEqual([], sessions)
        self.assertTrue(any("identity changed" in reason.lower() for reason in rejected))

        cache = [
            UsbDevice(busid="1-9", vid="1234", pid="abcd", description="A"),
            UsbDevice(busid="1-9", vid="1234", pid="abcd", description="B"),
        ]
        sessions, rejected = resolve_live_shutdown_sessions(
            live,
            host_devices=[],
            cached_devices=cache,
            host_reachable=False,
        )
        self.assertEqual([], sessions)
        self.assertTrue(any("ambiguous" in reason.lower() for reason in rejected))

    def test_shutdown_resolver_uses_unique_cache_when_host_is_unreachable(self):
        from client.core.lifecycle import resolve_live_shutdown_sessions

        live = [AttachedDevice(port=9, busid="1-9", vid="1234", pid="abcd")]
        cache = [UsbDevice(busid="1-9", vid="1234", pid="abcd", description="Token")]

        sessions, rejected = resolve_live_shutdown_sessions(
            live,
            host_devices=[],
            cached_devices=cache,
            host_reachable=False,
        )

        self.assertEqual([(9, "1-9")], [(item.port, item.busid) for item in sessions])
        self.assertEqual([], rejected)

    def test_shutdown_resolver_rejects_every_duplicate_live_busid(self):
        from client.core.lifecycle import resolve_live_shutdown_sessions

        live = [
            AttachedDevice(port=9, busid="1-9", vid="1234", pid="abcd"),
            AttachedDevice(port=10, busid="1-9", vid="1234", pid="abcd"),
        ]
        host = [
            UsbDevice(
                busid="1-9",
                vid="1234",
                pid="abcd",
                description="Token",
                state="Attached",
            )
        ]

        sessions, rejected = resolve_live_shutdown_sessions(
            live,
            host_devices=host,
            cached_devices=[],
            host_reachable=True,
        )

        self.assertEqual([], sessions)
        self.assertTrue(any("duplicate" in reason.lower() for reason in rejected))

    def test_shutdown_resolver_rejects_port_outside_usbip_range(self):
        from client.core.lifecycle import resolve_live_shutdown_sessions

        live = [AttachedDevice(port=0, busid="1-9", vid="1234", pid="abcd")]
        host = [
            UsbDevice(
                busid="1-9",
                vid="1234",
                pid="abcd",
                description="Token",
                state="Attached",
            )
        ]

        sessions, rejected = resolve_live_shutdown_sessions(
            live,
            host_devices=host,
            cached_devices=[],
            host_reachable=True,
        )

        self.assertEqual([], sessions)
        self.assertTrue(any("port invalid" in reason.lower() for reason in rejected))

    def test_main_separates_commit_data_request_from_explicit_quit(self):
        source = (Path(__file__).parents[1] / "client" / "main.py").read_text(encoding="utf-8")
        self.assertIn("window.commit_data_request", source)
        self.assertIn("window.quit_app_with_detach", source)
        self.assertNotIn("app.commitDataRequest.connect(lambda _manager: _quit())", source)

    def test_force_cleanup_does_not_globally_kill_detach_subprocesses(self):
        source = (Path(__file__).parents[1] / "client" / "gui" / "main_window.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("usbip_wrapper.kill_all_subprocesses()", source)
        self.assertIn("resolve_live_shutdown_sessions", source)


if __name__ == "__main__":
    unittest.main()
