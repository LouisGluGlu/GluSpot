"""Allowlisted login timings. Never serialize requests, accounts or exceptions."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import logging
import threading
import time


_log = logging.getLogger("GluSpot.login")
_sources = {"netease", "qq", "kugou", "qishui"}
_operations = {"accounts", "sources", "prepare", "prepare_login", "account", "account_status",
               "begin", "begin_login", "poll", "poll_login", "cancel", "cancel_login", "logout", "open"}
_stages = {"task_queue", "auth_queue", "runtime_start", "module_load", "saved_account_validation",
           "qr_acquisition", "scan_detection", "confirmed_account_validation", "library_visible", "request"}
_errors = {"none", "network", "timeout", "runtime", "unavailable", "expired", "auth", "auth_required",
           "account_changed", "credential", "response", "invalid_response", "cancelled", "closed", "request_failed"}


@dataclass(frozen=True)
class LoginTrace:
    source: str
    operation: str
    generation: int
    deadline: float


_trace: ContextVar[LoginTrace | None] = ContextVar("gluspot_login_trace", default=None)
_request_guards: ContextVar[tuple[threading.Event, ...]] = ContextVar("gluspot_login_request_guards", default=())


def current_login_trace():
    return _trace.get()


def login_request_cancelled():
    """Read ownership without taking the service lock from a transport lock."""
    return any(event.is_set() for event in _request_guards.get())


@contextmanager
def login_request_guard(*events):
    """Carry the exact service generation across local I/O and transport entry."""
    token = _request_guards.set(_request_guards.get() + tuple(events))
    try:
        yield
    finally:
        _request_guards.reset(token)


def record_login_phase(source, operation, stage, elapsed_seconds, generation=0, error="none"):
    """Only fixed enums and numbers enter the log, even with a hostile caller."""
    if source not in _sources or operation not in _operations or stage not in _stages:
        return
    error = error if error in _errors else "request_failed"
    elapsed = max(0., min(3600., float(elapsed_seconds)))
    generation = generation if isinstance(generation, int) and not isinstance(generation, bool) else 0
    _log.info("platform=%s operation=%s stage=%s elapsed_ms=%.3f generation=%d error=%s",
              source, operation, stage, elapsed * 1000., generation, error)


@contextmanager
def login_trace(source, operation, generation=0, *, deadline=None, timeout_seconds=18.):
    value = LoginTrace(source, operation, generation,
                       deadline if deadline is not None else time.monotonic() + timeout_seconds)
    token = _trace.set(value)
    try:
        yield value
    finally:
        _trace.reset(token)


@contextmanager
def login_phase(stage):
    trace = _trace.get()
    start = time.monotonic()
    error = "none"
    try:
        yield
    except Exception as exc:
        error = getattr(exc, "code", "request_failed")
        raise
    finally:
        if trace is not None:
            record_login_phase(trace.source, trace.operation, stage, time.monotonic() - start,
                               trace.generation, error)
