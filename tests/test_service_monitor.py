"""Tests for the ServiceMonitor QThread.

Patches QThread/pyqtSignal so the monitor can be tested without a running Qt
event loop. Tests call `_poll_once()` directly — the real production method
extracted from `run()` — so they exercise the actual logic, not a re-implementation.
"""
import unittest
from unittest.mock import Mock, patch


@patch("host.core.service_monitor.QThread", Mock)
@patch("host.core.service_monitor.pyqtSignal", Mock)
class TestServiceMonitor(unittest.TestCase):
    """Test ServiceMonitor signal emission and debounce logic."""

    def _make_monitor(self):
        """Create a ServiceMonitor with Mock signals."""
        from host.core.service_monitor import ServiceMonitor
        monitor = ServiceMonitor(poll_interval=5)
        monitor.service_healthy = Mock()
        monitor.service_down = Mock()
        monitor.service_recovered = Mock()
        monitor.service_error = Mock()
        return monitor

    @patch("host.core.service_monitor.usbipd_wrapper")
    @patch("host.core.service_monitor.time")
    def test_service_healthy_emits_healthy(self, mock_time, mock_usbipd):
        """When port is listening, service_healthy is emitted."""
        mock_time.sleep = Mock()
        mock_usbipd.check_port_listening.return_value = True

        monitor = self._make_monitor()
        monitor._poll_once()

        monitor.service_healthy.emit.assert_called_once()
        monitor.service_recovered.emit.assert_not_called()
        self.assertTrue(monitor._was_healthy)

    @patch("host.core.service_monitor.usbipd_wrapper")
    @patch("host.core.service_monitor.time")
    def test_service_down_emits_down_and_error(self, mock_time, mock_usbipd):
        """When port is not listening and recovery fails, service_down then service_error emitted."""
        mock_time.sleep = Mock()
        mock_usbipd.check_port_listening.return_value = False
        mock_usbipd.ensure_service_running.return_value = (False, "Service stuck")

        monitor = self._make_monitor()
        monitor._was_healthy = True  # Was healthy, now goes down
        monitor._poll_once()

        monitor.service_down.emit.assert_called_once()
        monitor.service_error.emit.assert_called_once_with("Service stuck")
        monitor.service_recovered.emit.assert_not_called()
        self.assertFalse(monitor._was_healthy)
        self.assertTrue(monitor._error_reported)

    @patch("host.core.service_monitor.usbipd_wrapper")
    @patch("host.core.service_monitor.time")
    def test_service_recovered_after_failure(self, mock_time, mock_usbipd):
        """When port comes back after being down, service_recovered is emitted."""
        mock_time.sleep = Mock()
        mock_usbipd.check_port_listening.return_value = True

        monitor = self._make_monitor()
        monitor._was_healthy = False  # Was down, now recovers
        monitor._error_reported = True
        monitor._poll_once()

        monitor.service_recovered.emit.assert_called_once()
        monitor.service_healthy.emit.assert_called_once()
        self.assertTrue(monitor._was_healthy)
        self.assertFalse(monitor._error_reported)

    @patch("host.core.service_monitor.usbipd_wrapper")
    @patch("host.core.service_monitor.time")
    def test_service_error_debounced(self, mock_time, mock_usbipd):
        """service_error is only emitted ONCE per failure streak (debounce)."""
        mock_time.sleep = Mock()
        mock_usbipd.check_port_listening.return_value = False
        mock_usbipd.ensure_service_running.return_value = (False, "Still stuck")

        monitor = self._make_monitor()
        monitor._was_healthy = True
        monitor._error_reported = False

        # Simulate TWO poll cycles
        monitor._poll_once()
        monitor._poll_once()

        # service_down should be emitted once (only on first cycle when _was_healthy was True)
        monitor.service_down.emit.assert_called_once()
        # service_error should be emitted only ONCE (debounced on second cycle)
        monitor.service_error.emit.assert_called_once_with("Still stuck")

    @patch("host.core.service_monitor.usbipd_wrapper")
    @patch("host.core.service_monitor.time")
    def test_auto_recovery_success_emits_recovered(self, mock_time, mock_usbipd):
        """When port is down but recovery succeeds, service_recovered is emitted."""
        mock_time.sleep = Mock()
        mock_usbipd.check_port_listening.return_value = False
        mock_usbipd.ensure_service_running.return_value = (True, "Service started")

        monitor = self._make_monitor()
        monitor._was_healthy = True
        monitor._poll_once()

        monitor.service_down.emit.assert_called_once()
        monitor.service_recovered.emit.assert_called_once()
        monitor.service_error.emit.assert_not_called()
        self.assertTrue(monitor._was_healthy)
        self.assertFalse(monitor._error_reported)

    @patch("host.core.service_monitor.usbipd_wrapper")
    @patch("host.core.service_monitor.time")
    def test_error_reset_on_recovery(self, mock_time, mock_usbipd):
        """After recovery, _error_reported resets so next failure emits again."""
        mock_time.sleep = Mock()

        monitor = self._make_monitor()
        monitor._was_healthy = False
        monitor._error_reported = True
        mock_usbipd.check_port_listening.return_value = True
        monitor._poll_once()

        mock_usbipd.check_port_listening.return_value = False
        monitor._was_healthy = True
        monitor._error_reported = True

        # Simulate recovery
        mock_usbipd.check_port_listening.return_value = True
        monitor._poll_once()

        self.assertFalse(monitor._error_reported)
        self.assertTrue(monitor._was_healthy)

        # Now fail again — should emit error again (not debounced from before)
        mock_usbipd.check_port_listening.return_value = False
        mock_usbipd.ensure_service_running.return_value = (False, "Failed again")
        monitor.service_error.reset_mock()
        monitor._poll_once()

        monitor.service_error.emit.assert_called_once_with("Failed again")


if __name__ == "__main__":
    unittest.main()
