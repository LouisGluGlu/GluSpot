"""A click is never queued behind prefetches on the one-at-a-time bridge."""
import threading
import time
import unittest

from api.online_music_bridge import NodeOnlineMusicBackend, background_requests, in_background, request_is_background


def _backend():
    backend = NodeOnlineMusicBackend.__new__(NodeOnlineMusicBackend)
    backend._lock = threading.RLock()
    backend._foreground_waiting = 0
    backend._priority_lock = threading.Lock()
    return backend


class BridgePriorityTests(unittest.TestCase):
    def test_background_marking_is_per_thread_and_scoped(self):
        self.assertFalse(request_is_background())
        with background_requests():
            self.assertTrue(request_is_background())
        self.assertFalse(request_is_background())
        self.assertTrue(in_background(request_is_background)())
        self.assertFalse(request_is_background())

    def test_foreground_goes_first_when_both_wait(self):
        backend = _backend()
        order = []
        backend._lock.acquire()                       # a request is running
        def worker(name, background):
            if backend._acquire_bridge(time.monotonic() + 3, background=background):
                order.append(name)
                time.sleep(.02)
                backend._lock.release()
        prefetches = [threading.Thread(target=worker, args=(f"prefetch{i}", True)) for i in range(3)]
        for thread in prefetches:
            thread.start()
        time.sleep(.05)
        click = threading.Thread(target=worker, args=("click", False))
        click.start()
        time.sleep(.05)
        backend._lock.release()                       # the running request ends
        for thread in prefetches + [click]:
            thread.join(3)
        self.assertEqual(order[0], "click")
        self.assertEqual(sorted(order[1:]), ["prefetch0", "prefetch1", "prefetch2"])

    def test_background_alone_is_not_delayed_and_respects_its_deadline(self):
        backend = _backend()
        began = time.monotonic()
        self.assertTrue(backend._acquire_bridge(time.monotonic() + 1, background=True))
        self.assertLess(time.monotonic() - began, .2)
        backend._lock.release()
        backend._foreground_waiting = 1               # a foreground request never leaves
        self.assertFalse(backend._acquire_bridge(time.monotonic() + .1, background=True))


if __name__ == "__main__":
    unittest.main()
