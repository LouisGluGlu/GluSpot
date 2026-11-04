"""Opt-in classification/SMTC boundaries, with no native player or network."""
import asyncio
import unittest
from enum import IntEnum
from types import SimpleNamespace

from api.capture.interruption import (
    InterruptionPolicy, MediaClassifier, MediaKind, PauseResult,
    ReservedAudioDetector, SmtcInterruptionObserver,
)
from tests.capture_async import CaptureAsyncTestCase
from tests.test_capture_smtc import Session as TimelineSession


class Session(TimelineSession):
    """Reuse the capture collector's event-token fake, adding media and Pause."""

    def __init__(self, app='chrome.exe', *, playing=True, media_type=1,
                 title='Song', artist='Artist'):
        super().__init__()
        self.source_app_user_model_id = app
        self.playing = playing
        self.status = None
        self.media_type = media_type
        self.property_type = None
        self.title, self.artist = title, artist
        self.pauses = 0
        self.pause_answer = True
        self.pause_changes_state = True
        self.reads = 0
        self.properties_hook = None

    def get_playback_info(self):
        self.reads += 1
        return SimpleNamespace(playback_status=self.status if self.status is not None else
                               4 if self.playing else 5, playback_type=self.media_type)

    async def try_get_media_properties_async(self):
        if self.properties_hook is not None:
            await self.properties_hook()
        return SimpleNamespace(title=self.title, artist=self.artist, album_title='',
                               playback_type=self.property_type, thumbnail=None)

    async def try_pause_async(self):
        self.pauses += 1
        if isinstance(self.pause_answer, Exception):
            raise self.pause_answer
        if self.pause_changes_state:
            self.playing = False
        return self.pause_answer


class Manager:
    def __init__(self, sessions):
        self.sessions = sessions
        self.events = {}
        self.removed = []

    def get_sessions(self):
        return self.sessions

    def add_sessions_changed(self, callback):
        self.events['sessions_changed'] = callback
        return 'sessions_changed'

    def remove_sessions_changed(self, token):
        self.removed.append(token)


class ClassificationTests(unittest.TestCase):
    def classify(self, app='chrome.exe', type_=None, property_type=None,
                 title='', artist='', art=False):
        return MediaClassifier().classify(
            app, SimpleNamespace(playback_type=type_),
            SimpleNamespace(playback_type=property_type, title=title, artist=artist),
            artwork_readable=art)

    def test_browser_music_explicit_type_or_readable_metadata(self):
        for kwargs in ({'type_': 1}, {'property_type': 1},
                       {'title': 'Song', 'artist': 'Artist'},
                       {'title': 'Song', 'art': True}):
            with self.subTest(kwargs=kwargs):
                self.assertIs(self.classify(**kwargs), MediaKind.MUSIC)

    def test_video_and_image_veto_music_and_app_identity(self):
        for negative in (2, 3):
            for app in ('chrome.exe', 'cloudmusic.exe'):
                self.assertIs(self.classify(app, 1, negative, 'Song', 'Artist', True),
                              MediaKind.VIDEO)

    def test_unknown_or_invalid_types_never_gain_permission(self):
        for type_ in (None, 0, True, '1', 12, object()):
            self.assertIs(self.classify(type_=type_), MediaKind.UNKNOWN)
        for type_ in (True, '1', 12):
            self.assertIs(self.classify(type_=type_, title='Song', artist='Artist'),
                          MediaKind.UNKNOWN)

    def test_exact_allowlist_not_keywords_or_whitespace(self):
        for app in ('cloudmusic.exe', 'QQMUSIC.EXE', 'spotify.exe'):
            self.assertIs(self.classify(app), MediaKind.MUSIC)
        for app in ('foo.spotify.bar', 'cloudmusic.exe ', 'spotify-player.exe', 'python.exe'):
            self.assertIs(self.classify(app), MediaKind.UNKNOWN)

    def test_native_enum_shape_and_self_exclusion(self):
        class NativeType(IntEnum):
            MUSIC = 1
        self.assertIs(self.classify(type_=NativeType.MUSIC), MediaKind.MUSIC)
        self.assertIs(self.classify('GluSpot', 1, title='Song', artist='Artist'), MediaKind.UNKNOWN)
        self.assertIs(self.classify('python.exe', 1), MediaKind.MUSIC)

    def test_reserved_policy_hooks_and_detector_are_noops(self):
        policy = InterruptionPolicy()
        self.assertTrue(policy.allows(MediaKind.MUSIC))
        for kind in (MediaKind.VIDEO, MediaKind.GAME, MediaKind.UNKNOWN):
            self.assertFalse(policy.allows(kind))
        self.assertFalse(policy.video())
        self.assertFalse(policy.game())
        self.assertIs(ReservedAudioDetector().detect(object(), audio_sessions=True), MediaKind.UNKNOWN)


class ObserverTests(CaptureAsyncTestCase):
    def setUp(self):
        self.now = 10.0
        self.session = Session(playing=False)
        self.manager = Manager([self.session])
        self.changes = []
        self.observer = SmtcInterruptionObserver(
            self.manager, lambda observation, edge: self.changes.append((observation, edge)),
            clock=lambda: self.now)

    async def asyncTearDown(self):
        await self.observer.close()

    async def collect(self):
        await self.observer.collect_once()
        return self.observer.snapshots()[0] if self.observer.snapshots() else None

    async def test_constructor_is_inert(self):
        self.assertEqual(self.session.reads, 0)
        self.assertEqual(self.session.events, {})
        self.assertEqual(self.observer.snapshots(), ())
        self.assertEqual(self.session.pauses, 0)

    async def test_first_playing_and_newly_discovered_playing_only_baseline(self):
        self.session.playing = True
        await self.collect()
        self.assertFalse(self.changes[-1][1])
        replacement = Session(playing=True)
        self.manager.sessions = [replacement]
        snapshot = await self.collect()
        self.assertFalse(self.changes[-1][1])
        self.assertIsNone(snapshot.started_at)
        self.assertEqual(replacement.pauses, 0)

    async def test_observed_paused_to_playing_edge_and_repeat_no_edge(self):
        old = await self.collect()
        self.now = 11.0
        self.session.playing = True
        edge = await self.collect()
        self.assertTrue(self.changes[-1][1])
        self.assertEqual((edge.nonplaying_at, edge.started_at), (10, 11))
        self.assertEqual(edge.interval, old.interval + 1)
        self.now = 12
        await self.collect()
        self.assertFalse(self.changes[-1][1])

    async def test_unknown_status_to_playing_is_not_proven_edge(self):
        self.session.status = 2
        await self.collect()
        self.session.status = None
        self.session.playing = True
        await self.collect()
        self.assertFalse(self.changes[-1][1])

    async def test_metadata_and_seek_cannot_manufacture_start(self):
        self.session.playing = True
        first = await self.collect()
        self.now = 12
        self.session.title = 'Next song'
        self.session.position = 0
        second = await self.collect()
        self.assertFalse(self.changes[-1][1])
        self.assertEqual(first.interval, second.interval)
        self.assertNotEqual(first.media_generation, second.media_generation)
        self.assertFalse(self.observer.is_current(first))

    async def test_self_and_duplicate_app_ids_have_no_bindings(self):
        self.manager.sessions = [Session('GluSpot'), Session('chrome.exe'), Session('CHROME.EXE')]
        await self.collect()
        self.assertEqual(self.observer.snapshots(), ())
        self.assertTrue(all(not s.events for s in self.manager.sessions))

    async def test_final_revalidation_blocks_video_changed_content_and_exit(self):
        self.session.playing = True
        snapshot = await self.collect()
        self.session.media_type = 2
        self.assertIsNone(await self.observer.revalidate(snapshot))
        self.assertIs(await self.observer.pause(snapshot), PauseResult.STALE)
        self.session.media_type = 1
        self.session.title = 'Other content'
        self.assertIsNone(await self.observer.revalidate(snapshot))
        self.session.title = 'Song'
        self.manager.sessions = []
        self.assertIsNone(await self.observer.revalidate(snapshot))
        self.assertEqual(self.session.pauses, 0)

    async def test_session_replacement_during_metadata_wait_blocks_dispatch(self):
        self.session.playing = True
        snapshot = await self.collect()
        async def replace_while_reading():
            self.manager.sessions = [Session(playing=True)]
        self.session.properties_hook = replace_while_reading
        self.assertIs(await self.observer.pause(snapshot), PauseResult.STALE)
        self.assertEqual(self.session.pauses, 0)

    async def test_bound_session_identity_mutation_invalidates_permission(self):
        self.session.playing = True
        snapshot = await self.collect()
        self.session.source_app_user_model_id = 'another.exe'
        self.assertIs(await self.observer.pause(snapshot), PauseResult.STALE)
        self.assertEqual(self.session.pauses, 0)

    async def test_pause_explicit_and_deduplicated_without_optimistic_truth(self):
        self.session.playing = True
        snapshot = await self.collect()
        self.assertIs(await self.observer.pause(snapshot), PauseResult.PAUSED)
        self.assertEqual(self.session.pauses, 1)
        # A native write does not invent a raw observed Paused snapshot.
        self.assertTrue(self.observer.snapshots()[0].playing)
        self.assertIs(await self.observer.pause(snapshot), PauseResult.STALE)
        self.assertEqual(self.session.pauses, 1)

    async def test_pause_false_and_exception_are_distinct(self):
        self.session.playing = True
        self.session.pause_answer = False
        self.session.pause_changes_state = False
        snapshot = await self.collect()
        self.assertIs(await self.observer.pause(snapshot), PauseResult.REJECTED)
        self.session.playing = False
        await self.collect()
        self.session.playing = True
        snapshot = await self.collect()
        self.session.pause_answer = RuntimeError('unsupported')
        self.assertIs(await self.observer.pause(snapshot), PauseResult.UNCERTAIN)
        self.assertEqual(self.session.pauses, 2)

    async def test_distinct_user_intents_can_each_attempt_once_in_same_interval(self):
        self.session.playing = True
        self.session.pause_answer = False
        self.session.pause_changes_state = False
        snapshot = await self.collect()
        self.assertIs(await self.observer.pause(snapshot, request_id='on-1'), PauseResult.REJECTED)
        self.assertIs(await self.observer.pause(snapshot, request_id='on-1'), PauseResult.STALE)
        self.assertIs(await self.observer.pause(snapshot, request_id='on-2'), PauseResult.REJECTED)
        self.assertIs(await self.observer.pause(snapshot, request_id='on-2'), PauseResult.STALE)
        self.assertEqual(self.session.pauses, 2)

    async def test_new_user_intent_guard_blocks_native_dispatch(self):
        self.session.playing = True
        snapshot = await self.collect()
        self.assertIs(await self.observer.pause(snapshot, current=lambda: False), PauseResult.STALE)
        self.assertEqual(self.session.pauses, 0)

    async def test_real_resume_is_new_interval_after_own_pause_ack(self):
        self.session.playing = True
        snapshot = await self.collect()
        await self.observer.pause(snapshot)
        await self.collect()
        self.assertFalse(self.changes[-1][1])
        self.now = 10.1
        self.session.playing = True
        new = await self.collect()
        self.assertTrue(self.changes[-1][1])
        self.assertEqual(new.interval, snapshot.interval + 1)

    async def test_close_unsubscribes_manager_and_sessions(self):
        await self.observer.start()
        self.assertEqual(len(self.session.events), 2)
        await self.observer.close()
        self.assertCountEqual(self.session.removed, ['playback_info_changed', 'media_properties_changed'])
        self.assertEqual(self.manager.removed, ['sessions_changed'])
        self.assertEqual(self.observer.snapshots(), ())

    async def test_event_during_metadata_read_keeps_wakeup_and_rejects_stale_evidence(self):
        await self.collect()
        self.observer._loop = asyncio.get_running_loop()
        async def event_during_read():
            self.session.events['media_properties_changed']()
            await asyncio.sleep(0)
        self.session.properties_hook = event_during_read
        self.session.playing = True
        snapshot = await self.collect()
        self.assertIsNone(snapshot.playing)
        self.assertFalse(self.changes[-1][1])
        self.assertTrue(self.observer._wake.is_set())

    async def test_raw_artwork_reader_is_bounded_and_decoded(self):
        self.session.media_type = None
        self.session.artist = ''
        self.session.playing = True
        calls = []
        async def reader(properties, key):
            calls.append(key)
            return b'broken image'
        self.observer.thumbnail_reader = reader
        snapshot = await self.collect()
        self.assertIs(snapshot.kind, MediaKind.UNKNOWN)
        self.assertEqual(len(calls), 1)
        async def valid_reader(properties, key):
            from PySide6.QtCore import QBuffer, QByteArray, QIODevice
            from PySide6.QtGui import QImage
            image = QImage(2, 2, QImage.Format.Format_RGB32)
            image.fill(0xff228855)
            payload = QByteArray()
            buffer = QBuffer(payload)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, 'PNG')
            return bytes(payload)
        self.observer.thumbnail_reader = valid_reader
        snapshot = await self.collect()
        self.assertIs(snapshot.kind, MediaKind.MUSIC)


if __name__ == '__main__':
    unittest.main()
