"""Timed I/O dispatch keeps bounded reserve capacity and rejects late results."""
import threading
import time
import unittest

from PySide6.QtWidgets import QApplication

from local.music_tasks import MusicTasks


class MusicTaskDeadlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tasks = MusicTasks(task_timeout_seconds=.08)
        self.results = []
        self.tasks.completed.connect(lambda *args: self.results.append(args))
        self.release = threading.Event()

    def tearDown(self):
        self.tasks.close()
        self.release.set()
        self.app.processEvents()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            self.release.wait(.002)
        self.assertTrue(predicate(), self.results)

    def test_two_hung_requests_expire_then_good_request_uses_reserve_without_late_publish(self):
        started = [threading.Event(), threading.Event()]
        def hung(index):
            started[index].set()
            self.release.wait(3)
            return "late"
        for i in range(2):
            self.assertTrue(self.tasks.submit(("artwork", 1, i), lambda i=i: hung(i)))
        self.wait_for(lambda: all(event.is_set() for event in started) and len(self.results) == 2)
        self.assertTrue(all(result is None and error == "timeout" for _, result, error in self.results))
        self.tasks.submit(("artwork", 2), lambda: "current original")
        self.wait_for(lambda: len(self.results) == 3)
        self.assertEqual(self.results[-1], (("artwork", 2), "current original", None))
        self.release.set()
        self.app.processEvents()
        self.assertEqual(len(self.results), 3)

    def test_hung_action_capacity_is_bounded_and_exhaustion_still_completes(self):
        starts = []
        def hung():
            starts.append(threading.get_ident())
            self.release.wait(3)
        for batch in range(2):
            for index in range(2):
                self.tasks.submit(("artwork", batch, index), hung)
            self.wait_for(lambda: len(self.results) == (batch + 1) * 2)
        self.assertEqual(len(starts), 4)
        self.tasks.submit(("artwork", "overflow"), lambda: starts.append("overflow"))
        self.wait_for(lambda: len(self.results) == 5)
        self.assertEqual(len(starts), 4)
        self.assertEqual(self.results[-1], (("artwork", "overflow"), None, "timeout"))

    def test_expired_queued_action_is_not_executed_and_later_work_recovers(self):
        self.tasks.close()
        self.tasks = MusicTasks(worker_count=1, task_timeout_seconds=.08)
        self.tasks.completed.connect(lambda *args: self.results.append(args))
        calls = []
        self.tasks.submit(("artwork", "blocked"), lambda: self.release.wait(3))
        self.tasks.submit(("artwork", "queued"), lambda: calls.append("expired"))
        self.wait_for(lambda: len(self.results) == 2)
        self.assertEqual(calls, [])
        self.tasks.submit(("artwork", "current"), lambda: "original")
        self.wait_for(lambda: len(self.results) == 3)
        self.assertEqual(self.results[-1], (("artwork", "current"), "original", None))
