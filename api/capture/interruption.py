"""Opt-in, provider-owned media classification and passive SMTC observation.

The provider creates this observer only while interruption is enabled. No native
imports, process discovery or audio monitoring happen here. The reserved audio
session detector is intentionally inert; adding a detector does not grant VIDEO
or GAME permission to interrupt music.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from enum import Enum

# launch.bat redirects stderr into GluSpot-*.log. A dedicated handler keeps
# these decisions visible without turning on library logs. Records are emitted
# only while interruption is enabled; the OFF path never calls this.
_log = logging.getLogger('GluSpot.capture.interruption')
_log.setLevel(logging.INFO)
if not _log.handlers and sys.stderr is not None:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter('%(asctime)s %(name)s %(message)s'))
    _log.addHandler(_handler)
    _log.propagate = False

_LOG_PHASES = frozenset({
    'enable', 'disable', 'submit', 'observer', 'membership', 'session',
    'owned-start', 'pause-external', 'smtc-pause', 'cdp-fallback',
    'cdp-observe', 'cdp-pause', 'external-edge', 'yield', 'focus',
})
_LOG_DECISIONS = frozenset({
    'baseline', 'armed', 'skip', 'pause', 'yield', 'wait', 'owned-paused',
    'pause-external', 'pause-library',
})
_LOG_MATCHES = frozenset({'cloudmusic', 'orpheus'})
_LOG_REASONS = frozenset({
    'owned-already-playing', 'waiting-for-start', 'loop-not-running', 'loop-missing',
    'unsupported', 'unavailable', 'superseded', 'started', 'manager-replaced',
    'snapshot', 'ambiguous-aumid', 'identity-conflict',
    'baseline', 'start-edge', 'update', 'read-failed', 'stale-read', 'removed',
    'observer-missing', 'eligible', 'no-eligible-target', 'not-playing',
    'observed-after-start', 'kind-blocked', 'guard-revoked', 'revalidate-stale',
    'app-id-mismatch', 'not-explicit-reject', 'cdp-missing', 'cdp-not-ready',
    'no-sample', 'session-unbound', 'cef-unbound', 'native-track-mismatch',
    'generation-mismatch', 'cef-stale', 'no-capture-metadata', 'metadata-mismatch',
    'identity-mismatch', 'not-ready', 'guard-rejected', 'bad-flag', 'already-armed',
    'already-clear', 'armed', 'disarmed', 'evaluate-rejected', 'generation-changed',
    'observation-inactive', 'missing-request', 'duplicate-request', 'track-mismatch',
    'contract-unavailable', 'contract-track-mismatch', 'stale-before-send',
    'native-rejected', 'stale-after-send', 'readback-unconfirmed', 'paused',
    'error', 'absent', 'rejected', 'owned-not-playing', 'suspended', 'missing-start',
    'not-after-boundary', 'missing-nonplaying', 'nonplaying-before-boundary',
    'overlap', 'cooldown', 'owned-pause-failed',
    'uncertain-wait', 'settled', 'already-sent', 'library-earlier',
    'external-earlier', 'unknown-order',
})
_LOG_RESULTS = frozenset({
    'paused', 'rejected', 'uncertain', 'stale', 'started', 'unsupported',
    'unavailable', 'superseded', 'manager-replaced', 'armed', 'disarmed',
    'failed', 'cdp-paused', 'cdp-unverified', 'error', 'loop-missing',
    'loop-not-running', 'queued',
})
_LOG_KINDS = frozenset({'music', 'video', 'game', 'unknown'})
_LOG_LOOPS = frozenset({'missing', 'running', 'not-running'})
_LOG_OBSERVERS = frozenset({'present', 'missing'})
_LOG_ACCEPTED = frozenset({'true', 'false', 'timeout', 'error', 'none'})
_LOG_ORDER = (
    'app_id', 'match', 'kind', 'playing', 'decision', 'result', 'reason', 'status',
    'accepted', 'edge', 'targets', 'observer', 'owned_playing', 'loop',
    'seen', 'kept', 'ambiguous', 'count', 'port',
)


def _log_app_id(value) -> str | None:
    """AUMID or executable basename only. Never a path, title, or page string."""
    text = value if isinstance(value, str) else ''
    if not text:
        return 'empty'
    if text.casefold() == 'gluspot':
        return 'gluspot'
    if '\\' in text or '/' in text:
        text = text.replace('\\', '/').rsplit('/', 1)[-1]
    cleaned = []
    for char in text:
        if char.isascii() and (char.isalnum() or char in '._!-'):
            cleaned.append(char)
        else:
            cleaned.append('_')
        if len(cleaned) >= 80:
            break
    token = ''.join(cleaned).strip('_.')
    return token or 'redacted'


def _log_token(key, value):
    if key == 'app_id':
        return _log_app_id(value)
    if key == 'kind':
        return value if value in _LOG_KINDS else None
    if key == 'decision':
        return value if value in _LOG_DECISIONS else None
    if key == 'reason':
        return value if value in _LOG_REASONS else None
    if key == 'result':
        return value if value in _LOG_RESULTS else None
    if key == 'loop':
        return value if value in _LOG_LOOPS else None
    if key == 'observer':
        return value if value in _LOG_OBSERVERS else None
    if key == 'accepted':
        return value if value in _LOG_ACCEPTED else None
    if key == 'match':
        return value if value in _LOG_MATCHES else None
    if key in {'playing', 'edge', 'owned_playing'}:
        if value == 'unknown':
            return 'unknown'
        if value in (0, 1, True, False) and not isinstance(value, str):
            return '1' if value else '0'
        return None
    if key == 'status' and value == 'none':
        return 'none'
    if key in {'status', 'seen', 'kept', 'ambiguous', 'count', 'targets', 'port'}:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if key == 'port':
            return str(value) if 1 <= value <= 65535 else None
        if key == 'status':
            return str(value) if -1 <= value <= 99 else None
        return str(value) if 0 <= value <= 9999 else None
    return None


def log_interruption(active, **fields) -> None:
    """One decision line. A no-op unless interruption is explicitly active.

    Callers pass ``active=True`` only while the setting is on. Fields outside
    the fixed token set are dropped, so titles and page text cannot leak.
    """
    if active is not True:
        return
    phase = fields.get('phase')
    if phase not in _LOG_PHASES:
        return
    parts = ['phase=%s' % phase]
    for key in _LOG_ORDER:
        if key not in fields:
            continue
        rendered = _log_token(key, fields[key])
        if rendered is not None:
            parts.append('%s=%s' % (key, rendered))
    _log.info(' '.join(parts))


def netease_match(app_id):
    """Exact NetEase process name or app-id prefix: cloudmusic or orpheus.

    A name that merely contains those words does not match. ``netease`` is not
    a match. Paths contribute only their final segment.
    """
    if not isinstance(app_id, str) or not app_id:
        return None
    text = app_id.replace('\\', '/').rsplit('/', 1)[-1] if ('\\' in app_id or '/' in app_id) else app_id
    folded = text.casefold()
    for token in ('cloudmusic', 'orpheus'):
        exe = token + '.exe'
        if folded == token or folded == exe:
            return token
        if folded.startswith(exe + '!') or folded.startswith(exe + '.'):
            return token
        if folded.startswith(token + '!') or folded.startswith(token + '.'):
            # cloudmusic.exe with any extra character is a different name.
            if folded.startswith(exe):
                return None
            return token
    return None


def focus_decision(owned_started_at, external_starts):
    """Who keeps playing when both sides are already playing.

    ``external_starts`` is ``(key, started_at)``. The library is paused only
    when one external start is strictly later than the library and strictly
    later than every other known external start. Missing or tied times keep
    the library; the caller pauses external music.
    Returns ``(action, key, reason)`` where action is ``pause-library`` or
    ``pause-external``.
    """
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and value not in (float('inf'), float('-inf'))

    owned_known = finite(owned_started_at)
    best_key = None
    best_time = None
    tie = False
    earlier_external = False
    for key, started in external_starts:
        if not finite(started):
            continue
        if owned_known and started < owned_started_at:
            earlier_external = True
        if best_time is None or started > best_time:
            best_key, best_time, tie = key, started, False
        elif started == best_time:
            tie = True
    if (owned_known and best_key is not None and not tie and best_time > owned_started_at):
        return ('pause-library', best_key, 'library-earlier')
    if earlier_external:
        return ('pause-external', None, 'external-earlier')
    return ('pause-external', None, 'unknown-order')


def same_session(left, right) -> bool:
    """Use native wrapper equality when supplied, never application IDs.

    Standard object equality has no stronger identity than ``is``. Native
    projections may implement equality for two wrappers of one underlying
    session; require unambiguous, symmetric Python bool results and fail closed
    on unsupported, exceptional or array-like comparisons.
    """
    if left is right:
        return True
    try:
        if type(left).__eq__ is object.__eq__ and type(right).__eq__ is object.__eq__:
            return False
        forward, backward = left == right, right == left
        return type(forward) is bool and type(backward) is bool and forward and backward
    except Exception:
        return False


class MediaKind(Enum):
    MUSIC = 'music'
    VIDEO = 'video'
    GAME = 'game'
    UNKNOWN = 'unknown'


class ReservedAudioDetector:
    """Future per-process game/video audio detection boundary; no probing today."""

    def detect(self, *_args, **_kwargs) -> MediaKind:
        return MediaKind.UNKNOWN


class InterruptionPolicy:
    """Shared gate for both directions. Reserved hooks never interfere today."""

    def video(self) -> bool:
        return False

    def game(self) -> bool:
        return False

    def allows(self, kind: MediaKind) -> bool:
        if kind is MediaKind.VIDEO:
            return self.video()
        if kind is MediaKind.GAME:
            return self.game()
        return kind is MediaKind.MUSIC


def _enum_value(value):
    if value is None:
        return None
    value = getattr(value, 'value', value)
    return value if type(value) is int else -1


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ''


class MediaClassifier:
    """Use current session evidence, independent of the application's category.

    Browser Music has the same permission as desktop Music. Explicit video or
    image evidence vetoes positive metadata and allowlist evidence. Art means
    decoded *raw SMTC* thumbnail bytes, never a downloaded or cached old cover.
    """
    MUSIC_APPS = frozenset(('cloudmusic.exe', 'orpheus.exe', 'qqmusic.exe', 'spotify.exe'))

    def classify(self, app_id, playback, properties, *, artwork_readable=False):
        if not isinstance(app_id, str) or not app_id or app_id.casefold() == 'gluspot':
            return MediaKind.UNKNOWN
        try:
            types = (_enum_value(getattr(playback, 'playback_type', None)),
                     _enum_value(getattr(properties, 'playback_type', None)))
            if 2 in types or 3 in types:
                return MediaKind.VIDEO
            if any(value not in (None, 0, 1) for value in types):
                return MediaKind.UNKNOWN
            if 1 in types:
                return MediaKind.MUSIC
            if app_id.casefold() in self.MUSIC_APPS or netease_match(app_id):
                return MediaKind.MUSIC
            title = _text(getattr(properties, 'title', ''))
            artist = _text(getattr(properties, 'artist', ''))
            if title and (artist or artwork_readable is True):
                return MediaKind.MUSIC
        except Exception:
            pass
        return MediaKind.UNKNOWN


class PauseResult(Enum):
    PAUSED = 'paused'
    REJECTED = 'rejected'  # Explicit false, followed by a fresh Playing read.
    UNCERTAIN = 'uncertain'
    STALE = 'stale'


@dataclass(frozen=True)
class Observation:
    binding_id: int
    session: object = field(compare=False, repr=False)
    app_id: str
    media_key: tuple
    kind: MediaKind
    playing: bool | None
    observed_at: float
    nonplaying_at: float | None = None
    started_at: float | None = None
    interval: int = 0
    media_generation: int = 0


@dataclass
class _Binding:
    number: int
    session: object
    app_id: str
    tokens: list = field(default_factory=list)
    observation: Observation | None = None
    revision: int = 0
    diag_state: object = None
    diag_failed: bool = False
    diag_stale: bool = False
    diag_status: object = None


class SmtcInterruptionObserver:
    """All external SMTC sessions on the existing provider asyncio loop.

    Membership uses retained wrapper identity or native equality, never AUMID
    alone. Unprovable wrapper replacement starts a new baseline. Ambiguous
    duplicate AUMIDs have no automatic permission. This fails closed on
    projections that cannot preserve or compare native identity.
    """
    EVENTS = ('playback_info_changed', 'media_properties_changed')

    def __init__(self, manager, on_change, *, thumbnail_reader=None,
                 classifier=None, clock=time.monotonic, poll_seconds=0.5,
                 read_timeout=0.8, pause_timeout=2.0, diagnostics=False):
        self.manager = manager
        self.on_change = on_change
        self.thumbnail_reader = thumbnail_reader
        self.classifier = classifier or MediaClassifier()
        self.clock = clock
        self.poll_seconds = max(0.5, float(poll_seconds))
        self.read_timeout = read_timeout
        self.pause_timeout = pause_timeout
        # The provider sets this only for an observer it created while the
        # setting is on. Tests and the OFF path leave it false, so logging
        # stays a no-op.
        self._diagnostics = diagnostics is True
        self._bindings = {}
        self._next_binding = 0
        self._manager_tokens = []
        self._loop = None
        self._wake = asyncio.Event()
        self._closed = False
        self._task = None
        self._collect_lock = asyncio.Lock()
        self._pause_requests = set()
        self._membership_signature = None

    def _diag(self, **fields):
        if not self._diagnostics or self._closed:
            return
        log_interruption(True, **fields)

    def snapshots(self) -> tuple[Observation, ...]:
        return tuple(binding.observation for binding in self._bindings.values()
                     if binding.observation is not None)

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self._subscribe(self.manager, ('sessions_changed',), self._manager_tokens)
        await self.collect_once()
        if not self._closed:
            self._task = self._loop.create_task(self._run())

    def _subscribe(self, source, events, tokens, binding=None):
        for event in events:
            add = getattr(source, 'add_' + event, None)
            remove = getattr(source, 'remove_' + event, None)
            if callable(add) and callable(remove):
                try:
                    def callback(*_args, target=binding):
                        if self._loop is not None and not self._closed:
                            try:
                                self._loop.call_soon_threadsafe(self._signal, target)
                            except RuntimeError:
                                pass
                    tokens.append((remove, add(callback)))
                except Exception:
                    pass

    def _signal(self, binding):
        if not self._closed:
            if binding is not None:
                binding.revision += 1
            self._wake.set()

    def _unsubscribe(self, tokens):
        for remove, token in tokens:
            try:
                remove(token)
            except Exception:
                pass
        tokens.clear()

    def _sessions(self):
        sessions = list(self.manager.get_sessions() or [])
        # Enumerations can repeat a native instance through equal wrappers.
        unique = []
        for session in sessions:
            matches = [item for item in unique if same_session(session, item)]
            if any(str(item.source_app_user_model_id) != str(session.source_app_user_model_id)
                   for item in matches):
                raise ValueError('Conflicting native session identity')
            if not matches:
                unique.append(session)
        return unique

    def _membership(self):
        try:
            sessions = self._sessions()
        except Exception:
            if self._membership_signature != ('conflict',):
                self._membership_signature = ('conflict',)
                self._diag(phase='membership', reason='identity-conflict', seen=0, kept=0, ambiguous=0)
            return []
        counts = Counter(str(getattr(s, 'source_app_user_model_id', '') or '').casefold()
                         for s in sessions)
        ambiguous = tuple(sorted(app for app, count in counts.items()
                                 if count > 1 and app and app != 'gluspot'))
        kept = [s for s in sessions
                if (app_id := str(getattr(s, 'source_app_user_model_id', '') or ''))
                and app_id.casefold() != 'gluspot' and counts[app_id.casefold()] == 1]
        signature = (len(sessions), len(kept), ambiguous)
        if signature != self._membership_signature:
            self._membership_signature = signature
            self._diag(phase='membership', reason='snapshot', seen=len(sessions),
                       kept=len(kept), ambiguous=len(ambiguous))
            for app in ambiguous:
                self._diag(phase='membership', reason='ambiguous-aumid', app_id=app,
                           count=counts[app])
        return kept

    async def collect_once(self):
        async with self._collect_lock:
            if self._closed:
                return
            sessions = self._membership()
            publications = []
            for number, binding in tuple(self._bindings.items()):
                if not any(same_session(binding.session, session) and
                           str(session.source_app_user_model_id) == binding.app_id
                           for session in sessions):
                    self._unsubscribe(binding.tokens)
                    self._bindings.pop(number, None)
                    if binding.observation is not None:
                        publications.append((None, False))
                        self._diag(phase='session', reason='removed', app_id=binding.app_id,
                                   playing='unknown', edge=0)
            for session in sessions:
                binding = next((b for b in self._bindings.values()
                                if same_session(b.session, session) and
                                str(session.source_app_user_model_id) == b.app_id), None)
                if binding is None:
                    self._next_binding += 1
                    binding = _Binding(self._next_binding, session,
                                       str(session.source_app_user_model_id))
                    self._bindings[binding.number] = binding
                    self._subscribe(session, self.EVENTS, binding.tokens, binding)
                before_read = binding.observation
                observation = await self._read(binding)
                if self._closed or self._bindings.get(binding.number) is not binding:
                    return
                if binding.observation is not before_read:
                    # An independent Pause readback arrived during this older
                    # metadata read. Preserve that observed state and read anew.
                    self._wake.set()
                    continue
                previous = binding.observation
                edge = (previous is not None and previous.playing is False
                        and observation.playing is True)
                if edge:
                    observation = replace(observation, started_at=observation.observed_at,
                                          interval=previous.interval + 1,
                                          nonplaying_at=previous.nonplaying_at)
                binding.observation = observation
                self._log_observation(binding, observation, edge, previous)
                publications.append((observation, edge))
            # Publish synchronously only after the complete observation batch.
            # An automatic action must see every newly observed candidate,
            # including ambiguous edges from another slow metadata read.
            for observation, edge in publications:
                self.on_change(observation, edge)

    def _log_observation(self, binding, observation, edge, previous):
        if binding.diag_failed:
            reason = 'read-failed'
        elif binding.diag_stale:
            reason = 'stale-read'
        elif edge:
            reason = 'start-edge'
        elif previous is None:
            reason = 'baseline'
        else:
            reason = 'update'
        playing = observation.playing
        status = binding.diag_status
        state = (observation.app_id, observation.kind.value, playing, edge, reason, status)
        previous_state = binding.diag_state
        if state == previous_state:
            return
        # A second poll of the same playback bit is not a new decision.
        if (reason == 'update' and previous_state is not None
                and previous_state[:4] == state[:4] and previous_state[5] == state[5]):
            binding.diag_state = state
            return
        binding.diag_state = state
        fields = dict(phase='session', app_id=observation.app_id, kind=observation.kind.value,
                      playing='unknown' if playing is None else int(bool(playing)),
                      status='none' if not isinstance(status, int) or isinstance(status, bool) else status,
                      edge=int(bool(edge)), reason=reason)
        matched = netease_match(observation.app_id)
        if matched:
            fields['match'] = matched
        self._diag(**fields)

    async def _read(self, binding):
        at = self.clock()  # State observation precedes metadata I/O.
        old = binding.observation
        revision = binding.revision
        binding.diag_failed = False
        binding.diag_stale = False
        binding.diag_status = None
        try:
            if str(getattr(binding.session, 'source_app_user_model_id', '') or '') != binding.app_id:
                raise ValueError('Session identity changed')
            playback = binding.session.get_playback_info()
            status = _enum_value(getattr(playback, 'playback_status', None))
            binding.diag_status = status if isinstance(status, int) and not isinstance(status, bool) else None
            playing = True if status == 4 else False if status in (0, 1, 3, 5) else None
            properties = await asyncio.wait_for(
                binding.session.try_get_media_properties_async(), self.read_timeout)
            key = tuple(_text(getattr(properties, name, ''))
                        for name in ('title', 'artist', 'album_title'))
            artwork_readable = False
            kind = self.classifier.classify(binding.app_id, playback, properties)
            if kind is MediaKind.UNKNOWN and key[0] and self.thumbnail_reader is not None:
                raw = await asyncio.wait_for(self.thumbnail_reader(properties, '\0'.join(key)),
                                             self.read_timeout)
                if raw:
                    from PySide6.QtGui import QImage
                    artwork_readable = not QImage.fromData(raw).isNull()
                    kind = self.classifier.classify(binding.app_id, playback, properties,
                                                   artwork_readable=artwork_readable)
            # A state event during metadata I/O cannot make stale metadata fresh.
            after = binding.session.get_playback_info()
            if (revision != binding.revision or
                    str(getattr(binding.session, 'source_app_user_model_id', '') or '') != binding.app_id or
                    _enum_value(getattr(after, 'playback_status', None)) != status):
                self._wake.set()
                playing, kind = None, MediaKind.UNKNOWN
                binding.diag_stale = True
            else:
                kind = self.classifier.classify(binding.app_id, after, properties,
                                                artwork_readable=artwork_readable)
        except Exception:
            playing, kind = None, MediaKind.UNKNOWN
            key = old.media_key if old else ()
            binding.diag_failed = True
        generation = (old.media_generation + (old.media_key != key)) if old else 0
        return Observation(binding.number, binding.session, binding.app_id, key, kind,
                           playing, at,
                           at if playing is False else old.nonplaying_at if old and playing else None,
                           old.started_at if old and playing else None,
                           old.interval if old else 0, generation)

    def binding_alive(self, binding_id):
        return not self._closed and binding_id in self._bindings

    def get_session(self, binding_id):
        binding = self._bindings.get(binding_id) if not self._closed else None
        return binding.session if binding is not None else None

    def is_current(self, observation):
        if self._closed:
            return False
        binding = self._bindings.get(observation.binding_id)
        latest = binding.observation if binding else None
        return bool(latest is not None and same_session(binding.session, observation.session)
                    and latest.playing is True and observation.playing is True
                    and latest.kind is MediaKind.MUSIC and observation.kind is MediaKind.MUSIC
                    and latest.interval == observation.interval
                    and latest.media_generation == observation.media_generation
                    and latest.media_key == observation.media_key)

    async def revalidate(self, observation):
        if not self.is_current(observation):
            return None
        binding = self._bindings[observation.binding_id]
        if not any(same_session(binding.session, s) and
                   str(s.source_app_user_model_id) == binding.app_id for s in self._membership()):
            return None
        fresh = await self._read(binding)
        if (not self.is_current(observation) or fresh.playing is not True
                or not any(same_session(binding.session, s) and
                   str(s.source_app_user_model_id) == binding.app_id for s in self._membership())
                or fresh.media_key != observation.media_key
                or fresh.media_generation != observation.media_generation
                or fresh.kind is not MediaKind.MUSIC):
            return None
        return fresh

    def _pause_diag(self, observation, result, *, status=None, accepted='none'):
        self._diag(phase='smtc-pause', app_id=getattr(observation, 'app_id', ''),
                   result=result.value,
                   status='none' if not isinstance(status, int) or isinstance(status, bool) else status,
                   accepted=accepted)

    async def pause(self, observation, current=lambda: True, *, request_id: str | None = None):
        # A later explicit owned intent may retry a rejected native Pause. A
        # repeated delivery of the same interval/intent never sends twice.
        key = (observation.binding_id, observation.interval, request_id)
        if key in self._pause_requests or not current():
            self._pause_diag(observation, PauseResult.STALE)
            return PauseResult.STALE
        fresh = await self.revalidate(observation)
        if fresh is None or not current():
            self._pause_diag(observation, PauseResult.STALE)
            return PauseResult.STALE
        self._pause_requests.add(key)
        self._pause_requests = {k for k in self._pause_requests
                                if k[0] in self._bindings and
                                k[1] == self._bindings[k[0]].observation.interval}
        if len(self._pause_requests) > 512:
            # Callers revoke prior intent guards before accepting a newer one.
            self._pause_requests = {key}
        accepted = None
        status = None
        try:
            accepted = await asyncio.wait_for(observation.session.try_pause_async(),
                                              self.pause_timeout)
            accepted_token = 'true' if accepted is True else 'false' if accepted is False else 'none'
            if not current() or self._closed:
                self._pause_diag(observation, PauseResult.UNCERTAIN, accepted=accepted_token)
                return PauseResult.UNCERTAIN
            status = _enum_value(getattr(observation.session.get_playback_info(),
                                         'playback_status', None))
            if status == 5:  # Independent raw Paused read, not command success.
                if not current() or self._closed:
                    self._pause_diag(observation, PauseResult.UNCERTAIN, status=status,
                                     accepted=accepted_token)
                    return PauseResult.UNCERTAIN
                binding = self._bindings.get(observation.binding_id)
                latest = binding.observation if binding else None
                if (latest is not None and same_session(binding.session, observation.session)
                        and latest.interval == observation.interval):
                    at = self.clock()
                    confirmed = replace(latest, playing=False, observed_at=at,
                                        nonplaying_at=at, started_at=None)
                    binding.revision += 1
                    binding.observation = confirmed
                    self.on_change(confirmed, False)
                self._pause_diag(observation, PauseResult.PAUSED, status=status,
                                 accepted=accepted_token)
                return PauseResult.PAUSED
            if accepted is False and status == 4:
                self._pause_diag(observation, PauseResult.REJECTED, status=status, accepted='false')
                return PauseResult.REJECTED
        except asyncio.TimeoutError:
            self._pause_diag(observation, PauseResult.UNCERTAIN, accepted='timeout')
            return PauseResult.UNCERTAIN
        except Exception:
            self._pause_diag(observation, PauseResult.UNCERTAIN, accepted='error')
            return PauseResult.UNCERTAIN
        accepted_token = 'true' if accepted is True else 'false' if accepted is False else 'none'
        self._pause_diag(observation, PauseResult.UNCERTAIN, status=status, accepted=accepted_token)
        return PauseResult.UNCERTAIN

    async def _run(self):
        while not self._closed:
            self._wake.clear()
            await self.collect_once()
            if self._wake.is_set():
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def close(self):
        self._closed = True
        self._unsubscribe(self._manager_tokens)
        for binding in self._bindings.values():
            self._unsubscribe(binding.tokens)
        self._bindings.clear()
        self._pause_requests.clear()
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
