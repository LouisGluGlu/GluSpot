"""INFO timings for the existing startup sequence.

Each line is `GluSpot.startup phase=<name> ms=<milliseconds>`. Callers keep
the same statements in the same order; this only records how long they took.
"""
from __future__ import annotations

from contextlib import contextmanager
import logging
import sys
import time


_log = logging.getLogger("GluSpot.startup")
_log.setLevel(logging.INFO)
if not _log.handlers and sys.stderr is not None:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    _log.addHandler(_handler)
    _log.propagate = False


def log_startup_phase(phase: str, started: float) -> None:
    elapsed_ms = int(round((time.perf_counter() - float(started)) * 1000.0))
    if elapsed_ms < 0:
        elapsed_ms = 0
    _log.info("phase=%s ms=%d", phase, elapsed_ms)


@contextmanager
def startup_phase(phase: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        log_startup_phase(phase, started)
