"""Sequential, identity-fenced seeks with independent physical readback."""
from __future__ import annotations

import asyncio
import logging
import math
import sys
import time
from dataclasses import dataclass
from enum import Enum

from api.capture.contracts import SeekBackend, TimelineSample


_log = logging.getLogger('GluSpot.capture.seek')
_log.setLevel(logging.INFO)
# launch.bat redirects stderr into the normal GluSpot-*.log. Other capture
# callers have no logging setup; a dedicated handler keeps decisions visible
# without enabling unrelated library logs. StreamHandler flushes every record.
if not _log.handlers and sys.stderr is not None:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter('%(asctime)s %(name)s %(message)s'))
    _log.addHandler(_handler)
    _log.propagate = False


def record_seek_decision(stage, reason, *, target=None, backend='', port=None, error_seconds=None):
    """Record decisions and physical error, never player metadata or payloads."""
    if stage not in {'source', 'transport', 'restore', 'provider', 'provider-guard', 'runtime', 'runtime-result', 'cef', 'ui'}:
        return
    if reason not in {
        'owned-active', 'system-disabled', 'system-selected', 'relaunch-port-bound', 'existing-port-bound',
        'previous-request-uncertain', 'busy', 'uia-worker-busy', 'legacy-route',
        'invalid-target', 'unavailable', 'expected-track-mismatch', 'owning-thread',
        'loop-unavailable', 'capture-disabled', 'track-changed', 'source-changed',
        'session-unavailable', 'session-source-mismatch', 'metadata-mismatch', 'queued',
        'wait-timeout', 'request-error', 'succeeded', 'failed', 'binding-unavailable',
        'track-mismatch', 'cef-seek-disabled', 'no-current-observation', 'executing',
        'identity-changed', 'verified', 'unverified', 'fake-ack', 'request-timeout', 'cancelled',
        'pending', 'no-connection', 'no-progress', 'native-acknowledged', 'native-rejected',
        'invalid-index', 'provider-unavailable', 'dispatch', 'legacy-provider',
        'gesture-track-mismatch', 'gesture-source-mismatch', 'gesture-target-mismatch',
        'completion-track-mismatch', 'completion-source-mismatch', 'closing',
    }:
        return
    backend = backend if backend in {'owned', 'cef', 'smtc', 'uia'} else ''
    fields = ['stage=%s' % stage, 'reason=%s' % reason]
    if backend:
        fields.append('backend=%s' % backend)
    for key, value in (('target_seconds', target), ('port', port), ('error_seconds', error_seconds)):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            fields.append('%s=%s' % (key, value))
    _log.info(' '.join(fields))


class SeekState(str, Enum):
    IDLE = 'idle'
    SMTC_REQUEST = 'smtc-request'
    VERIFY_SMTC = 'verify-smtc'
    UIA_REQUEST = 'uia-request'
    VERIFY_UIA = 'verify-uia'
    CEF_REQUEST = 'cef-request'
    VERIFY_CEF = 'verify-cef'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    UNCERTAIN = 'uncertain'
    CANCELLED = 'cancelled'


@dataclass(frozen=True)
class SeekPolicy:
    """Per-player tolerance and read schedule (total 300–800 ms by default)."""

    tolerance_seconds: float = 1.5
    read_delays: tuple[float, ...] = (0.3, 0.15, 0.15)
    request_timeout: float = 0.8
    read_timeout: float = 0.25


def seek_policy(session_id: str) -> SeekPolicy:
    """Conservative schedules for known SMTC publishers; no process/path I/O."""
    hint = session_id.casefold()
    if any(name in hint for name in ('cloudmusic', 'netease', 'orpheus', 'qqmusic')):
        return SeekPolicy(read_delays=(0.3, 0.2, 0.3))
    return SeekPolicy()


@dataclass(frozen=True)
class SeekResult:
    succeeded: bool
    state: SeekState
    reason: str
    backend: str = ''
    smtc_acknowledged: bool = False
    error_seconds: float | None = None


class SeekExecutor:
    """At most one in-flight request and one mutation per backend/request ID.

    A timeout of a mutation is ambiguous: cancellation cannot prove the native
    player stopped processing it, so no other backend is allowed afterward.
    Read timeouts may fall back only after the mutation itself has completed.
    Identity is checked before every mutation and after each awaited read.
    """

    def __init__(self, *, clock=time.monotonic, sleep=asyncio.sleep):
        self.clock = clock
        self.sleep = sleep
        self.state = SeekState.IDLE
        self._busy = False
        self._uncertain = False
        self._mutation_pending = False
        self._results: dict[str, SeekResult] = {}
        self.last_result: SeekResult | None = None

    @property
    def busy(self):
        return self._busy

    @property
    def uncertain(self):
        """Whether a native mutation may still be executing in the player."""
        return self._uncertain

    async def execute(self, target: float, *, identity: tuple[str, str], request_id: str,
                      smtc: SeekBackend, fallback: SeekBackend | None, guard,
                      policy: SeekPolicy = SeekPolicy(), optimistic=None,
                      primary_name: str = 'smtc', fallback_name: str = 'uia') -> SeekResult:
        """Run one fenced request; repeated IDs return their recorded result."""
        if request_id in self._results:
            return self._results[request_id]
        if self._uncertain:
            return SeekResult(False, SeekState.UNCERTAIN, 'previous-request-uncertain')
        if self._busy:
            return SeekResult(False, SeekState.FAILED, 'busy')
        if not math.isfinite(target) or target < 0:
            return SeekResult(False, SeekState.FAILED, 'invalid-target')
        self._busy = True
        acknowledged = False
        error = None
        try:
            if not await guard():
                return self._finish(request_id, False, SeekState.CANCELLED, 'identity-changed')
            before = await self._read(smtc, policy)
            if before is None or before.identity != identity or not await guard():
                return self._finish(request_id, False, SeekState.CANCELLED, 'no-current-observation')
            bounds = [v for v in (before.end_seconds,) if v > before.start_seconds]
            target = max(before.start_seconds, min(bounds[0], target) if bounds else target)
            if optimistic is not None:
                optimistic(target)
            for name, backend in ((primary_name, smtc), (fallback_name, fallback)):
                if backend is None:
                    break
                if not await guard():
                    return self._finish(request_id, False, SeekState.CANCELLED, 'identity-changed')
                started = self.clock()
                self.state = {'smtc': SeekState.SMTC_REQUEST, 'cef': SeekState.CEF_REQUEST}.get(name, SeekState.UIA_REQUEST)
                try:
                    self._mutation_pending = True
                    ack = await asyncio.wait_for(backend.seek(target, request_id=request_id), policy.request_timeout)
                except asyncio.TimeoutError:
                    return self._finish(request_id, False, SeekState.UNCERTAIN, 'request-timeout', name, acknowledged)
                except Exception:
                    # A native write can raise after reaching the player.
                    return self._finish(request_id, False, SeekState.UNCERTAIN, 'request-error', name, acknowledged)
                self._mutation_pending = False
                if name == 'smtc':
                    acknowledged = bool(ack)
                self.state = {'smtc': SeekState.VERIFY_SMTC, 'cef': SeekState.VERIFY_CEF}.get(name, SeekState.VERIFY_UIA)
                for delay in policy.read_delays:
                    await self.sleep(delay)
                    if not await guard():
                        return self._finish(request_id, False, SeekState.CANCELLED, 'identity-changed', name, acknowledged)
                    sample = await self._read(backend, policy)
                    if sample is None:
                        continue
                    if sample.identity != identity or not await guard():
                        return self._finish(request_id, False, SeekState.CANCELLED, 'identity-changed', name, acknowledged)
                    # Use raw independent player evidence. In particular, an
                    # unchanged SMTC anchor never becomes proof by interpolation.
                    expected = target + (max(0.0, sample.observed_at - started) * sample.rate if sample.is_playing else 0.0)
                    if sample.end_seconds > sample.start_seconds:
                        expected = min(sample.end_seconds, expected)
                    error = sample.position_seconds - expected
                    at_target_before = abs(before.position_seconds - target) <= policy.tolerance_seconds
                    moved = abs(sample.position_seconds - before.position_seconds) > 0.05
                    if abs(error) <= policy.tolerance_seconds and (at_target_before or moved):
                        return self._finish(request_id, True, SeekState.SUCCEEDED, 'verified', name, acknowledged, error)
                # The first mutation is complete before any fallback starts.
                # No retries of SMTC or UIA mutations, even on a false ack.
            reason = 'fake-ack' if acknowledged else 'unverified'
            return self._finish(request_id, False, SeekState.FAILED, reason, '', acknowledged, error)
        except asyncio.CancelledError:
            self._uncertain = self._uncertain or self._mutation_pending
            self._finish(request_id, False, SeekState.CANCELLED, 'cancelled')
            raise
        finally:
            self._busy = False

    async def _read(self, backend, policy) -> TimelineSample | None:
        try:
            sample = await asyncio.wait_for(backend.read(), policy.read_timeout)
            if sample is not None and math.isfinite(sample.position_seconds):
                return sample
        except Exception:
            pass
        return None

    def _finish(self, request_id, succeeded, state, reason, backend='', acknowledged=False, error=None):
        self.state = state
        self._uncertain = self._uncertain or state == SeekState.UNCERTAIN
        result = SeekResult(succeeded, state, reason, backend, acknowledged, error)
        self.last_result = result
        self._results[request_id] = result
        if len(self._results) > 256:
            del self._results[next(iter(self._results))]
        return result
