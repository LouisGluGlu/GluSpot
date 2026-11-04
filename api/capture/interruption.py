"""Opt-in, provider-owned media classification and passive SMTC observation.

The provider creates this observer only while interruption is enabled. No native
imports, process discovery or audio monitoring happen here. The reserved audio
session detector is intentionally inert; adding a detector does not grant VIDEO
or GAME permission to interrupt music.
"""
from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from enum import Enum


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
    MUSIC_APPS = frozenset(('cloudmusic.exe', 'qqmusic.exe', 'spotify.exe'))

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
            if app_id.casefold() in self.MUSIC_APPS:
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


class SmtcInterruptionObserver:
    """All external SMTC sessions on the existing provider asyncio loop.

    Membership is proven by the retained native wrapper, never by AUMID alone.
    Wrapper replacement starts a new baseline. Ambiguous duplicate AUMIDs have
    no automatic permission. This deliberately fails closed on projections that
    cannot preserve or compare native identity.
    """
    EVENTS = ('playback_info_changed', 'media_properties_changed')

    def __init__(self, manager, on_change, *, thumbnail_reader=None,
                 classifier=None, clock=time.monotonic, poll_seconds=0.5,
                 read_timeout=0.8, pause_timeout=2.0):
        self.manager = manager
        self.on_change = on_change
        self.thumbnail_reader = thumbnail_reader
        self.classifier = classifier or MediaClassifier()
        self.clock = clock
        self.poll_seconds = max(0.5, float(poll_seconds))
        self.read_timeout = read_timeout
        self.pause_timeout = pause_timeout
        self._bindings = {}
        self._next_binding = 0
        self._manager_tokens = []
        self._loop = None
        self._wake = asyncio.Event()
        self._closed = False
        self._task = None
        self._collect_lock = asyncio.Lock()
        self._pause_requests = set()

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
        # Some fake/native enumerations repeat the very same wrapper.
        unique = []
        for session in sessions:
            if all(session is not item for item in unique):
                unique.append(session)
        return unique

    def _membership(self):
        try:
            sessions = self._sessions()
            counts = Counter(str(getattr(s, 'source_app_user_model_id', '') or '').casefold()
                             for s in sessions)
            return [s for s in sessions
                    if (app_id := str(getattr(s, 'source_app_user_model_id', '') or ''))
                    and app_id.casefold() != 'gluspot' and counts[app_id.casefold()] == 1]
        except Exception:
            return []

    async def collect_once(self):
        async with self._collect_lock:
            if self._closed:
                return
            sessions = self._membership()
            for number, binding in tuple(self._bindings.items()):
                if not any(binding.session is session for session in sessions):
                    self._unsubscribe(binding.tokens)
                    self._bindings.pop(number, None)
                    if binding.observation is not None:
                        self.on_change(None, False)
            for session in sessions:
                binding = next((b for b in self._bindings.values() if b.session is session), None)
                if binding is None:
                    self._next_binding += 1
                    binding = _Binding(self._next_binding, session,
                                       str(session.source_app_user_model_id))
                    self._bindings[binding.number] = binding
                    self._subscribe(session, self.EVENTS, binding.tokens, binding)
                observation = await self._read(binding)
                if self._closed or self._bindings.get(binding.number) is not binding:
                    return
                previous = binding.observation
                edge = (previous is not None and previous.playing is False
                        and observation.playing is True)
                if edge:
                    observation = replace(observation, started_at=observation.observed_at,
                                          interval=previous.interval + 1,
                                          nonplaying_at=previous.nonplaying_at)
                binding.observation = observation
                self.on_change(observation, edge)

    async def _read(self, binding):
        at = self.clock()  # State observation precedes metadata I/O.
        old = binding.observation
        revision = binding.revision
        try:
            if str(getattr(binding.session, 'source_app_user_model_id', '') or '') != binding.app_id:
                raise ValueError('Session identity changed')
            playback = binding.session.get_playback_info()
            status = _enum_value(getattr(playback, 'playback_status', None))
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
            else:
                kind = self.classifier.classify(binding.app_id, after, properties,
                                                artwork_readable=artwork_readable)
        except Exception:
            playing, kind = None, MediaKind.UNKNOWN
            key = old.media_key if old else ()
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
        return bool(latest is not None and binding.session is observation.session
                    and latest.playing is True and observation.playing is True
                    and latest.kind is MediaKind.MUSIC and observation.kind is MediaKind.MUSIC
                    and latest.interval == observation.interval
                    and latest.media_generation == observation.media_generation
                    and latest.media_key == observation.media_key)

    async def revalidate(self, observation):
        if not self.is_current(observation):
            return None
        binding = self._bindings[observation.binding_id]
        if not any(binding.session is s for s in self._membership()):
            return None
        fresh = await self._read(binding)
        if (not self.is_current(observation) or fresh.playing is not True
                or not any(binding.session is s for s in self._membership())
                or fresh.media_key != observation.media_key
                or fresh.media_generation != observation.media_generation
                or fresh.kind is not MediaKind.MUSIC):
            return None
        return fresh

    async def pause(self, observation, current=lambda: True, *, request_id: str | None = None):
        # A later explicit owned intent may retry a rejected native Pause. A
        # repeated delivery of the same interval/intent never sends twice.
        key = (observation.binding_id, observation.interval, request_id)
        if key in self._pause_requests or not current():
            return PauseResult.STALE
        fresh = await self.revalidate(observation)
        if fresh is None or not current():
            return PauseResult.STALE
        self._pause_requests.add(key)
        self._pause_requests = {k for k in self._pause_requests
                                if k[0] in self._bindings and
                                k[1] == self._bindings[k[0]].observation.interval}
        if len(self._pause_requests) > 512:
            # Callers revoke prior intent guards before accepting a newer one.
            self._pause_requests = {key}
        try:
            accepted = await asyncio.wait_for(observation.session.try_pause_async(),
                                              self.pause_timeout)
            if not current() or self._closed:
                return PauseResult.UNCERTAIN
            status = _enum_value(getattr(observation.session.get_playback_info(),
                                         'playback_status', None))
            if status == 5:  # Raw Paused read, never optimistic truth.
                return PauseResult.PAUSED
            if accepted is False and status == 4:
                return PauseResult.REJECTED
        except Exception:
            pass
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
