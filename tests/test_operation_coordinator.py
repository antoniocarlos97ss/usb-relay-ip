import multiprocessing
import os
import tempfile
import unittest
from unittest import mock

from client.core import operation_coordinator


def _try_operation_in_process(lock_path, result_queue, release_event, hold):
    from client.core import operation_coordinator

    with mock.patch.object(
        operation_coordinator,
        "_operation_lock_path",
        return_value=lock_path,
        create=True,
    ):
        operation_coordinator.reset()
        acquired = operation_coordinator.try_acquire("1234", "abcd", "1-11")
        result_queue.put(acquired)
        if acquired and hold:
            release_event.wait(10)
        if acquired:
            operation_coordinator.release("1234", "abcd", "1-11")


def _try_named_operation_in_process(lock_path, result_queue, release_event, hold):
    from client.core import operation_coordinator

    with mock.patch.object(
        operation_coordinator,
        "_operation_lock_path",
        return_value=lock_path,
        create=True,
    ):
        operation_coordinator.reset()
        acquired = operation_coordinator.try_acquire_named("usbip-transport", "global")
        result_queue.put(acquired)
        if acquired and hold:
            release_event.wait(10)
        if acquired:
            operation_coordinator.release_named("usbip-transport", "global")


class OperationCoordinatorTests(unittest.TestCase):
    def test_same_busid_is_exclusive_across_processes(self):
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        release_event = context.Event()
        with tempfile.TemporaryDirectory() as tempdir:
            lock_path = os.path.join(tempdir, "busid-1-11.lock")
            holder = context.Process(
                target=_try_operation_in_process,
                args=(lock_path, result_queue, release_event, True),
            )
            holder.start()
            self.assertTrue(result_queue.get(timeout=5))

            contender = context.Process(
                target=_try_operation_in_process,
                args=(lock_path, result_queue, release_event, False),
            )
            contender.start()
            contender.join(10)
            self.assertEqual(0, contender.exitcode)
            self.assertFalse(result_queue.get(timeout=5))

            release_event.set()
            holder.join(10)
            self.assertEqual(0, holder.exitcode)

            successor = context.Process(
                target=_try_operation_in_process,
                args=(lock_path, result_queue, release_event, False),
            )
            successor.start()
            successor.join(10)
            self.assertEqual(0, successor.exitcode)
            self.assertTrue(result_queue.get(timeout=5))

    def test_named_usbip_transport_lock_is_exclusive_across_processes(self):
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        release_event = context.Event()
        with tempfile.TemporaryDirectory() as tempdir:
            lock_path = os.path.join(tempdir, "usbip-transport-global.lock")
            holder = context.Process(
                target=_try_named_operation_in_process,
                args=(lock_path, result_queue, release_event, True),
            )
            holder.start()
            self.assertTrue(result_queue.get(timeout=5))

            contender = context.Process(
                target=_try_named_operation_in_process,
                args=(lock_path, result_queue, release_event, False),
            )
            contender.start()
            contender.join(10)
            self.assertEqual(0, contender.exitcode)
            self.assertFalse(result_queue.get(timeout=5))

            release_event.set()
            holder.join(10)
            self.assertEqual(0, holder.exitcode)

    def test_missing_lock_parent_fails_closed_without_runtime_root_creation(self):
        with tempfile.TemporaryDirectory() as tempdir:
            lock_path = os.path.join(tempdir, "missing", "usbip.lock")
            with mock.patch(
                "client.core.operation_coordinator._operation_lock_path",
                return_value=lock_path,
            ):
                self.assertFalse(operation_coordinator.try_acquire("1234", "abcd", "1-12"))

            self.assertFalse(os.path.exists(os.path.dirname(lock_path)))

    def test_lock_path_errors_fail_closed(self):
        with mock.patch(
            "client.core.operation_coordinator._operation_lock_path",
            side_effect=PermissionError("denied"),
        ):
            self.assertFalse(operation_coordinator.try_acquire("1234", "abcd", "1-11"))

    def test_reset_does_not_release_an_active_global_lock(self):
        with tempfile.TemporaryDirectory() as tempdir:
            lock_path = os.path.join(tempdir, "usbip.lock")
            with mock.patch(
                "client.core.operation_coordinator._operation_lock_path",
                return_value=lock_path,
            ):
                self.assertTrue(operation_coordinator.try_acquire("1234", "abcd", "1-11"))
                operation_coordinator.reset()
                self.assertTrue(operation_coordinator.is_active("1234", "abcd", "1-11"))
                operation_coordinator.release("1234", "abcd", "1-11")


if __name__ == "__main__":
    unittest.main()
