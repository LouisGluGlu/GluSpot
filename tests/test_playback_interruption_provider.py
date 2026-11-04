"""Exercise real provider arbitration with fake native sessions and decoders."""
import asyncio
from dataclasses import replace
import gc
import logging
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
from unittest.mock import AsyncMock, patch

from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import OnlineSong, StreamInfo
from api.providers import EmptyIslandDataProvider, PlaybackSourceProvider, WindowsMediaProvider
from local.owned_music_player import OwnedMusicPlayer
from tests.capture_async import CaptureAsyncTestCase
from tests.test_media_interruption import Manager, Session
from tests.test_owned_playback import Tasks, ThreadRecordingPlayer


class InterruptionProviderTests(CaptureAsyncTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tasks = Tasks()
        self.decoder = ThreadRecordingPlayer()
        self.owned = OwnedMusicPlayer(SimpleNamespace(stream_url=lambda *_: None),
            LocalLyricsStore(root=Path(self.temp.name)), player=self.decoder, audio=object(),
            dj_player=ThreadRecordingPlayer(), dj_audio=object(), tasks=self.tasks,
            artwork_tasks=Tasks(), prefetch_tasks=Tasks(), stream_cache=None)
        self.settings = {'v2_owned_playback_enabled': True, 'v2_system_capture_enabled': False}
        self.system = object.__new__(WindowsMediaProvider)
        self.system._loop = asyncio.get_running_loop()
        self.system._manager = Manager([])
        self.system._music_cache_lock = threading.RLock()
        self.system._music_cache_epoch = 0
        self.system._refresh_future = None
        self.system._last_data = None
        self.system._netease_cdp_session = None
        self.system._audio_route_controller = None
        self.system.audio_analyzer = None
        self.system._render_peak_meter = None
        self.saved = []
        self.system.settings_setter = lambda *args: self.saved.append(args)
        self.provider = PlaybackSourceProvider(self.system, self.owned,
            lambda key, default=None: self.settings.get(key, default))
        self.signals = 0
        def changed():
            self.signals += 1
        self.provider.sourceChanged.connect(changed)
        self.song = OnlineSong('netease', 'one', 'Owned song', duration_seconds=120)

    async def asyncTearDown(self):
        self.provider.set_playback_interruption_enabled(False)
        await self.provider._close_interruption(self.provider._interruption_generation)
        self.provider._owned_audio_bridge.close()
        self.owned.close()
        await self.flush()
        self.temp.cleanup()
        gc.collect()

    async def flush(self):
        # Explicit loop turns, no wall-clock sleeps or real player/network.
        for _ in range(24):
            await asyncio.sleep(0)

    async def enable(self, sessions):
        self.system._manager.sessions = sessions
        self.assertTrue(self.provider.set_playback_interruption_enabled(True))
        await self.flush()
        self.assertIsNotNone(self.provider._interruption_observer)

    async def start_owned(self):
        self.assertTrue(self.owned.play_queue((self.song,)))
        token = next(t for t, _ in reversed(self.tasks.jobs) if t[0] == 'stream')
        self.tasks.completed.emit(token, StreamInfo('netease', 'one', 'https://example.test/one', True), None)
        await self.flush()

    async def collect(self):
        await self.provider._interruption_observer.collect_once()
        await self.flush()

    def owned_pauses(self):
        return sum(name == 'pause' for name, _ in self.decoder.calls)

    async def test_library_start_pauses_browser_music_but_never_video_or_unknown(self):
        music = Session('chrome.exe')
        video = Session('edge.exe', media_type=2)
        unknown = Session('unknown.exe', media_type=None, title='', artist='')
        await self.enable([music, video, unknown])
        await self.start_owned()
        self.assertEqual((music.pauses, video.pauses, unknown.pauses), (1, 0, 0))
        self.assertEqual(self.owned_pauses(), 0)
        self.assertTrue(self.provider._owned_active())
        self.assertEqual(self.saved, [])

    async def test_later_browser_music_pauses_library_and_pins_without_settings_writes(self):
        music = Session(playing=False)
        await self.enable([music])
        await self.start_owned()
        await self.collect()  # A non-playing observation after owned's start.
        queue, position = self.owned.queue, self.owned.track.position_seconds
        music.playing = True
        await self.collect()
        self.assertEqual(self.owned_pauses(), 1)
        self.assertFalse(self.owned.interruption_playing())
        self.assertIs(self.provider._interruption_pin.session, music)
        self.assertIs(await self.system._get_session(), music)
        self.assertFalse(self.provider._owned_active())
        self.assertTrue(self.provider._system_enabled())
        self.assertFalse(self.provider._owned_output_mode())
        self.assertIsNone(self.provider.get_owned_playback_mode())
        self.assertEqual(self.provider.get_owned_playback_queue(), (None, -1, ()))
        self.assertEqual((self.owned.queue, self.owned.track.position_seconds), (queue, position))
        self.assertEqual(self.saved, [])
        self.assertFalse(self.settings['v2_system_capture_enabled'])

    async def test_video_unknown_and_reserved_game_never_interrupt_library(self):
        video = Session('video.exe', playing=False, media_type=2)
        unknown = Session('unknown.exe', playing=False, media_type=None, title='', artist='')
        await self.enable([video, unknown])
        await self.start_owned()
        await self.collect()
        video.playing = unknown.playing = True
        await self.collect()
        self.assertEqual(self.owned_pauses(), 0)
        self.assertEqual((video.pauses, unknown.pauses), (0, 0))
        self.assertIsNone(self.provider._interruption_pin)

    async def test_toggle_on_with_both_playing_is_only_baseline(self):
        await self.start_owned()
        music = Session()
        await self.enable([music])
        await self.collect()
        self.assertEqual((music.pauses, self.owned_pauses()), (0, 0))
        self.assertIsNone(self.provider._interruption_pin)

    async def test_off_does_no_monitor_reads_native_calls_or_new_owned_reads(self):
        music = Session()
        self.system._manager.sessions = [music]
        with patch('api.capture.interruption.SmtcInterruptionObserver', side_effect=AssertionError('OFF monitor')):
            with patch.object(self.owned, 'interruption_playing', side_effect=AssertionError('OFF owned read')):
                self.assertTrue(self.provider.set_playback_interruption_enabled(False))
                await self.start_owned()
                self.owned.send_playback_command('toggle_play_pause')
                self.owned.send_playback_command('toggle_play_pause')
                await self.flush()
        self.assertEqual((music.reads, music.pauses), (0, 0))
        self.assertEqual(music.events, {})
        self.assertIsNone(self.provider._interruption_observer)
        self.assertEqual(self.saved, [])

    async def test_pause_ack_repetition_and_metadata_do_not_ping_pong(self):
        music = Session()
        await self.enable([music])
        await self.start_owned()
        self.assertEqual(music.pauses, 1)
        for _ in range(3):
            music.title = 'Updated title'
            await self.collect()
            self.owned.changed.emit()
        self.assertEqual((music.pauses, self.owned_pauses()), (1, 0))
        self.assertTrue(self.owned.interruption_playing())

    async def test_real_resume_after_own_pause_retained_through_cooldown(self):
        music = Session()
        await self.enable([music])
        await self.start_owned()
        self.assertIs(self.provider._interruption_observer.snapshots()[0].playing, False)
        music.playing = True
        await self.collect()
        self.assertEqual(self.owned_pauses(), 0)
        self.assertIsNotNone(self.provider._interruption_candidate)
        # Advance the policy deadline without sleeping a real second.
        self.provider._interruption_cooldown_until = 0
        task = self.provider._interruption_yield_task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await self.provider._yield_to_external()
        self.assertEqual(self.owned_pauses(), 1)
        self.assertEqual(music.pauses, 1)

    async def test_disable_invalidates_queued_pause_and_releases_temporary_route(self):
        music = Session(playing=False)
        await self.enable([music])
        await self.start_owned()
        await self.collect()
        music.playing = True
        await self.provider._interruption_observer.collect_once()
        self.provider.set_playback_interruption_enabled(False)
        await self.flush()
        self.assertEqual(self.owned_pauses(), 0)
        self.assertIsNone(self.provider._interruption_pin)
        reads = music.reads
        await self.flush()
        self.assertEqual(music.reads, reads)
        self.assertEqual(music.pauses, 0)

    async def test_manual_source_selection_cancels_old_edge_until_new_user_play(self):
        music = Session(playing=False)
        await self.enable([music])
        await self.start_owned()
        await self.collect()
        self.provider.playback_source_changed()
        music.playing = True
        await self.collect()
        self.assertEqual(self.owned_pauses(), 0)
        self.assertTrue(self.provider._owned_active())
        await self.start_owned()
        self.assertEqual(music.pauses, 1)

    async def test_pinned_session_track_changes_stay_pinned_and_removal_releases(self):
        music = Session(playing=False)
        await self.enable([music])
        await self.start_owned()
        await self.collect()
        music.playing = True
        await self.collect()
        music.title = 'Next song'
        await self.collect()
        self.assertIs(await self.system._get_session(), music)
        self.system._manager.sessions = []
        await self.collect()
        self.assertIsNone(self.provider._interruption_pin)
        self.assertFalse(self.owned.interruption_playing())

    async def test_netease_rejected_smtc_uses_existing_bound_cdp_without_relaunch(self):
        music = Session('cloudmusic.exe')
        music.pause_answer = False
        music.pause_changes_state = False
        cdp = SimpleNamespace(ready=True, generation=7, latest={'trackId': 'native'},
            pause=AsyncMock(return_value=True), set_pause_observation_enabled=AsyncMock(return_value=True),
            start=AsyncMock(side_effect=AssertionError('no start/relaunch')))
        self.system._netease_cdp_session = cdp
        data = EmptyIslandDataProvider().get_visual_data()
        self.system._last_data = replace(data, track=replace(data.track, title=music.title,
            artist=music.artist, source_app_id='cloudmusic.exe', track_id='captured'))
        self.system._capture_runtime = SimpleNamespace(source=SimpleNamespace(session=music),
            _cef_source=SimpleNamespace(session=cdp, native_track_id='native', generation=7, fresh=True),
            identity=('cloudmusic.exe', 'captured'))
        await self.enable([music])
        await self.start_owned()
        self.assertEqual(music.pauses, 1)
        cdp.pause.assert_awaited_once()
        self.assertEqual(cdp.pause.await_args.args[0], 'native')
        cdp.start.assert_not_called()
        self.assertEqual(self.saved, [])
        # Returning from a temporary pin clears the display cache, but can
        # retain the already-verified native binding as read-only evidence.
        self.provider._set_interruption_route(self.provider._interruption_observer.snapshots()[0])
        self.system._last_data = replace(data, track=replace(data.track, title=music.title,
            artist=music.artist, source_app_id='cloudmusic.exe', track_id='captured'))
        await self.start_owned()
        self.assertEqual(cdp.pause.await_count, 2)
        self.assertEqual(music.pauses, 2)
        self.assertIsNone(self.provider._interruption_pin)

    async def test_temporary_read_provenance_cannot_ensure_cdp_after_unpin_or_off(self):
        music = Session('cloudmusic.exe')
        track = replace(EmptyIslandDataProvider().get_visual_data().track,
                        source_app_id='cloudmusic.exe')
        runtime = SimpleNamespace(cef_session=None, cef_requested=True,
                                  bind=AsyncMock(), close=AsyncMock())
        self.system._capture_runtime = runtime
        self.system._capture_is_enabled = lambda: True
        self.system._netease_cef_enabled = lambda: True
        self.system._ensure_netease_cdp = AsyncMock(side_effect=AssertionError('no ensure/start/reload'))
        self.system._interruption_session = None
        self.system._interruption_controller_enabled = False
        task = asyncio.current_task()
        task._gluspot_interruption_read = True  # Captured when this read began under t260.
        try:
            with patch('api.capture.capabilities.capture_profile', return_value='smtc'):
                await self.system._bind_capture_session(music, track)
        finally:
            del task._gluspot_interruption_read
        runtime.bind.assert_awaited_once()
        self.system._ensure_netease_cdp.assert_not_called()

    async def test_uncertain_smtc_never_falls_back_to_cdp(self):
        music = Session('cloudmusic.exe')
        music.pause_answer = RuntimeError('uncertain write')
        music.pause_changes_state = False
        cdp = SimpleNamespace(ready=True, generation=7, latest={'trackId': 'native'},
            pause=AsyncMock(), set_pause_observation_enabled=AsyncMock(return_value=True))
        self.system._netease_cdp_session = cdp
        await self.enable([music])
        await self.start_owned()
        self.assertEqual(music.pauses, 1)
        cdp.pause.assert_not_called()

    async def test_new_user_resume_revokes_old_queued_external_handoff(self):
        music = Session(playing=False)
        await self.enable([music])
        await self.start_owned()
        await self.collect()
        original = self.provider._source_dispatch.call
        intercepted = []
        async def queued(guard, action):
            if not intercepted:
                intercepted.append(True)
                # Real explicit Pause and Resume supersede the queued a intent.
                self.assertTrue(self.owned.send_playback_command('toggle_play_pause'))
                self.assertTrue(self.owned.send_playback_command('toggle_play_pause'))
            return await original(guard, action)
        with patch.object(self.provider._source_dispatch, 'call', side_effect=queued):
            music.playing = True
            await self.collect()
        self.assertEqual(self.owned_pauses(), 1)  # The user's Pause only.
        self.assertTrue(self.owned.interruption_playing())
        self.assertIsNone(self.provider._interruption_pin)
        self.assertEqual(music.pauses, 1)

    async def test_video_update_at_final_gui_dispatch_revokes_music_permission(self):
        music = Session(playing=False)
        await self.enable([music])
        await self.start_owned()
        await self.collect()
        original = self.provider._source_dispatch.call
        intercepted = []
        async def queued(guard, action):
            if not intercepted:
                intercepted.append(True)
                music.media_type = 2
                await self.provider._interruption_observer.collect_once()
            return await original(guard, action)
        with patch.object(self.provider._source_dispatch, 'call', side_effect=queued):
            music.playing = True
            await self.collect()
        self.assertEqual(self.owned_pauses(), 0)
        self.assertTrue(self.owned.interruption_playing())
        self.assertEqual(music.pauses, 0)
        self.assertIsNone(self.provider._interruption_pin)

    async def test_new_external_edge_cancels_pending_library_pause_transaction(self):
        earlier = Session('earlier.exe')
        later = Session('later.exe', playing=False)
        await self.enable([earlier, later])
        reading = asyncio.Event()
        release = asyncio.Event()
        async def suspended_properties():
            earlier.properties_hook = None
            reading.set()
            await release.wait()
        earlier.properties_hook = suspended_properties
        await self.start_owned()
        self.assertTrue(reading.is_set())
        await self.collect()  # Observe later non-playing after owned started.
        later.playing = True
        await self.collect()
        release.set()
        await self.flush()
        self.assertEqual((earlier.pauses, later.pauses), (0, 0))
        self.assertEqual(self.owned_pauses(), 1)
        self.assertIs(self.provider._interruption_pin.session, later)

    async def test_frozen_music_targets_do_not_include_late_unknown_metadata(self):
        unknown = Session('unknown.exe', media_type=None, artist='')
        await self.enable([unknown])
        # Connected after the provider: eligibility freezes first, then native
        # metadata arrives before the asynchronous pause transaction executes.
        self.owned.playbackStarted.connect(lambda _event: setattr(unknown, 'artist', 'Late artist'))
        await self.start_owned()
        self.assertEqual(unknown.pauses, 0)
        self.assertTrue(self.owned.interruption_playing())
        self.assertIsNone(self.provider._interruption_pin)

    async def test_manager_reconnect_releases_old_pin_and_rebaselines_playing(self):
        old = Session(playing=False)
        await self.enable([old])
        await self.start_owned()
        await self.collect()
        old.playing = True
        await self.collect()
        self.assertIs(self.provider._interruption_pin.session, old)
        old_observer = self.provider._interruption_observer
        replacement = Session(playing=True)
        self.system._manager = Manager([replacement])
        await old_observer.collect_once()
        await self.flush()
        self.assertIsNot(self.provider._interruption_observer, old_observer)
        self.assertIsNone(self.provider._interruption_pin)
        self.assertIsNone(self.system._interruption_session)
        self.assertFalse(self.owned.interruption_playing())
        self.assertEqual(replacement.pauses, 0)
        snapshot = self.provider._interruption_observer.snapshots()[0]
        self.assertIsNone(snapshot.started_at)

    async def test_equal_timestamp_external_candidates_do_not_choose_a_winner(self):
        first = Session('first.exe', playing=False)
        second = Session('second.exe', playing=False)
        await self.enable([first, second])
        await self.start_owned()
        observer = self.provider._interruption_observer
        observer.clock = lambda: self.provider._interruption_owned_at + 1.0
        await self.collect()
        original = self.provider._source_dispatch.call
        release = asyncio.Event()
        async def queued(guard, action):
            await release.wait()
            return await original(guard, action)
        with patch.object(self.provider._source_dispatch, 'call', side_effect=queued):
            first.playing = second.playing = True
            await observer.collect_once()
            release.set()
            await self.flush()
        self.assertEqual(self.owned_pauses(), 0)
        self.assertTrue(self.owned.interruption_playing())
        self.assertEqual((first.pauses, second.pauses), (0, 0))
        self.assertIsNone(self.provider._interruption_pin)

    async def test_off_emits_no_interruption_log_and_sends_no_command(self):
        from api.capture.interruption import log_interruption
        cdp = SimpleNamespace(ready=True, generation=1, port=4588, latest={'trackId': 'native'},
            pause=AsyncMock(side_effect=AssertionError('pause')),
            set_pause_observation_enabled=AsyncMock(side_effect=AssertionError('observe')),
            start=AsyncMock(side_effect=AssertionError('start')))
        self.system._netease_cdp_session = cdp
        self.system._manager.sessions = [Session('cloudmusic.exe', title='SECRET_TITLE_XYZ')]
        with _capture_interruption_log() as lines:
            log_interruption(False, phase='enable', app_id='SECRET_TITLE_XYZ')
            self.assertTrue(self.provider.set_playback_interruption_enabled(False))
            await self.start_owned()
            self.owned.send_playback_command('toggle_play_pause')
            await self.flush()
        self.assertEqual(lines, [])
        cdp.pause.assert_not_called()
        cdp.set_pause_observation_enabled.assert_not_called()
        cdp.start.assert_not_called()

    async def test_both_already_playing_logs_baseline_without_titles_or_pause(self):
        await self.start_owned()
        music = Session(r'C:\Users\secret\cloudmusic.exe', title='SECRET_TITLE_XYZ',
                        artist='SECRET_ARTIST_XYZ')
        with _capture_interruption_log() as lines:
            await self.enable([music])
        text = '\n'.join(lines)
        self.assertIn('phase=enable decision=baseline reason=owned-already-playing owned_playing=1', text)
        self.assertIn('phase=session app_id=cloudmusic.exe kind=music playing=1', text)
        self.assertIn('reason=baseline', text)
        self.assertNotIn('phase=owned-start', text)
        self.assertNotIn('phase=smtc-pause', text)
        self.assertNotIn('SECRET', text)
        self.assertNotIn('secret', text)
        self.assertNotIn('Users', text)
        self.assertEqual((music.pauses, self.owned_pauses()), (0, 0))

    async def test_library_start_logs_pause_result_without_titles(self):
        music = Session('cloudmusic.exe', title='SECRET_TITLE_XYZ', artist='SECRET_ARTIST_XYZ')
        with _capture_interruption_log() as lines:
            await self.enable([music])
            await self.start_owned()
        text = '\n'.join(lines)
        self.assertIn('phase=enable decision=armed reason=waiting-for-start owned_playing=0', text)
        self.assertIn('phase=observer result=started', text)
        self.assertIn('phase=owned-start decision=pause reason=eligible targets=1 observer=present', text)
        self.assertIn('phase=smtc-pause app_id=cloudmusic.exe result=paused', text)
        self.assertIn('phase=pause-external app_id=cloudmusic.exe kind=music result=paused', text)
        self.assertNotIn('SECRET', text)
        self.assertEqual(music.pauses, 1)

    async def test_failed_cdp_observation_is_retried_without_a_pause_command(self):
        calls = {'n': 0}

        async def observe(enabled, guard=lambda: True):
            # Startup still clears observation. Only an enabled arm is under test.
            if enabled is not True:
                return True
            calls['n'] += 1
            return calls['n'] >= 2

        cdp = SimpleNamespace(ready=True, generation=3, port=4588, latest={'trackId': 'native'},
            pause=AsyncMock(side_effect=AssertionError('observation must not pause')),
            set_pause_observation_enabled=observe)
        self.system._netease_cdp_session = cdp
        music = Session('cloudmusic.exe', playing=False)
        with _capture_interruption_log() as lines:
            await self.enable([music])
            await self.flush()
        # The background poll retries a failed arm; success then sticks.
        self.assertGreaterEqual(calls['n'], 2)
        self.assertIsNotNone(self.provider._interruption_cdp_key)
        text = '\n'.join(lines)
        self.assertIn('phase=cdp-observe result=failed reason=rejected port=4588', text)
        self.assertIn('phase=cdp-observe result=armed reason=armed port=4588', text)
        self.assertNotIn('phase=cdp-pause', text)
        cdp.pause.assert_not_called()

    async def test_rejected_smtc_for_other_app_logs_mismatch_and_skips_cdp(self):
        music = Session('chrome.exe')
        music.pause_answer = False
        music.pause_changes_state = False
        cdp = SimpleNamespace(ready=True, generation=7, port=4588, latest={'trackId': 'native'},
            pause=AsyncMock(side_effect=AssertionError('not cloudmusic')),
            set_pause_observation_enabled=AsyncMock(return_value=True))
        self.system._netease_cdp_session = cdp
        with _capture_interruption_log() as lines:
            await self.enable([music])
            await self.start_owned()
        text = '\n'.join(lines)
        self.assertIn('phase=smtc-pause app_id=chrome.exe result=rejected status=4 accepted=false', text)
        self.assertIn('phase=cdp-fallback app_id=chrome.exe decision=skip reason=app-id-mismatch', text)
        self.assertNotIn('phase=cdp-pause', text)
        cdp.pause.assert_not_called()
        self.assertEqual(music.pauses, 1)


def _capture_interruption_log():
    logger = logging.getLogger('GluSpot.capture.interruption')
    lines = []

    class _Collect(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    handler = _Collect()

    class _Context:
        def __enter__(self):
            logger.addHandler(handler)
            return lines

        def __exit__(self, *args):
            logger.removeHandler(handler)

    return _Context()
