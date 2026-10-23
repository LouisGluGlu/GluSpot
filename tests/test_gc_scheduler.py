from __future__ import annotations

import unittest

from local.gc_scheduler import GcScheduler


class FakeGc:
    def __init__(self):
        self.count = [0, 0, 0]
        self.calls = []

    def get_threshold(self): return (700, 10, 10)
    def get_count(self): return tuple(self.count)
    def disable(self): self.calls.append("disable")
    def freeze(self): self.calls.append("freeze")

    def collect(self, generation=2):
        self.calls.append(("collect", generation))
        for index in range(generation + 1):
            self.count[index] = 0
        if generation < 2:
            self.count[generation + 1] += 1


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class GcSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.gc, self.clock, self.busy = FakeGc(), Clock(), [False]
        self.scheduler = GcScheduler(busy=lambda: self.busy[0], gc_module=self.gc, clock=self.clock,
                                     idle_full_interval=30.0, max_full_interval=120.0)

    def test_start_disables_automatic_collection(self):
        self.scheduler.start()
        self.assertEqual(self.gc.calls, ["disable"])

    def test_freeze_collects_before_freezing(self):
        self.scheduler.freeze()
        self.assertEqual(self.gc.calls, [("collect", 2), "freeze"])

    def test_tick_does_nothing_below_thresholds(self):
        self.gc.count = [699, 9, 0]
        self.assertIsNone(self.scheduler.tick())
        self.assertEqual(self.gc.calls, [])

    def test_young_and_middle_generations_follow_thresholds(self):
        self.gc.count = [700, 0, 0]
        self.assertEqual(self.scheduler.tick(), 0)
        self.gc.count = [700, 10, 0]
        self.assertEqual(self.scheduler.tick(), 1)

    def test_full_collection_waits_for_idle(self):
        self.busy[0] = True
        self.clock.now += 31
        self.assertIsNone(self.scheduler.tick())
        self.busy[0] = False
        self.assertEqual(self.scheduler.tick(), 2)
        self.assertIsNone(self.scheduler.tick())

    def test_full_collection_is_forced_after_max_interval_even_while_busy(self):
        self.busy[0] = True
        self.clock.now += 120
        self.assertEqual(self.scheduler.tick(), 2)
        self.clock.now += 119
        self.assertIsNone(self.scheduler.tick())


if __name__ == "__main__":
    unittest.main()
