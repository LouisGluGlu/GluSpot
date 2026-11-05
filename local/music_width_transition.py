"""Width gesture coordinates and non-interruptible song-page handoffs."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class WidthMarks:
    minimum: float
    breakpoint: float
    maximum: float
    height: float

    @property
    def split_unreachable(self):
        return self.breakpoint >= self.maximum

    def mark(self, width):
        if width <= self.breakpoint:
            return 10. + 15. * (width - self.minimum) / max(1., self.breakpoint - self.minimum)
        return 25. + 25. * (width - self.breakpoint) / max(1., self.maximum - self.breakpoint)


# Narrow↔wide handoff from the layout-G study: 220 ms snap, 320 ms brisk, 420 ms settle.
HANDOFF_FAST_SECONDS = 0.22
HANDOFF_BRISK_SECONDS = 0.32
HANDOFF_SETTLE_SECONDS = 0.42


def handoff_seconds(fast_jump, drag_speed):
    """Pick one of the three study durations. The curve is cubic-bezier(0.28, 0, 0.72, 1)."""
    if fast_jump:
        return HANDOFF_FAST_SECONDS
    if float(drag_speed) >= 60.0:
        return HANDOFF_BRISK_SECONDS
    return HANDOFF_SETTLE_SECONDS


class WidthTransition:
    """Intent queues while the current split/merge finishes at its chosen speed."""
    def __init__(self, narrow=True):
        self.state = "NARROW" if narrow else "WIDE"
        self.want_narrow = bool(narrow)
        self.duration = 1.
        self.drag_speed = 0.
        self._sample = None
        self._fast_jump = False

    @property
    def active(self):
        return self.state in {"SPLITTING", "MERGING"}

    @property
    def narrow(self):
        return self.state in {"NARROW", "MERGING"}

    def sample(self, mark, now):
        if self._sample is not None:
            previous, then = self._sample
            delta = abs(mark - previous)
            elapsed = max(1e-6, now - then)
            weight = 1. - math.exp(-elapsed / .12)
            self.drag_speed += weight * (delta / elapsed - self.drag_speed)
            self._fast_jump = delta > 20.
        self._sample = (float(mark), float(now))

    def request_mark(self, mark, now, *, unreachable=False):
        self.sample(mark, now)
        if unreachable:
            return self.request(True)
        if mark >= 25.:
            return self.request(False)
        if mark < 24.:
            return self.request(True)
        return False

    def request(self, narrow):
        self.want_narrow = bool(narrow)
        if self.active or self.narrow == self.want_narrow:
            return False
        self.state = "MERGING" if narrow else "SPLITTING"
        self.duration = handoff_seconds(self._fast_jump, self.drag_speed)
        self._fast_jump = False
        return True

    def complete(self):
        if not self.active:
            return False
        self.state = "NARROW" if self.narrow else "WIDE"
        return self.request(self.want_narrow)

    def settle(self, narrow):
        self.state = "NARROW" if narrow else "WIDE"
        self.want_narrow = bool(narrow)
