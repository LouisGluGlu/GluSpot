"""Independent speech/music transports, exact ducking and cancellation fences."""
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import LyricPayload, PersonalDjVoice, StreamInfo
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_playback import Player, Tasks
from tests.test_personal_dj_playback import segments


class Audio(QObject):
    volumeChanged = Signal(float)
    def __init__(self, volume=.65):
        super().__init__()
        self.gain = volume
    def volume(self): return self.gain
    def setVolume(self, value):
        self.gain = value
        self.volumeChanged.emit(value)


class PersonalDjOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.music, self.voice, self.audio, self.voice_audio = Player(), Player(), Audio(), Audio(.9)
        self.tasks, self.prefetch = Tasks(), Tasks()
        self.service = SimpleNamespace(
            stream_url=lambda source, song: StreamInfo(source, song, f"https://example.test/{song}.mp3", True),
            lyrics=lambda source, song: LyricPayload(source, song, raw_lrc="[00:01]保留歌词"),
            personal_dj=lambda source: segments(3), personal_fm=lambda source: (segments(3)[1],))
        self.owned = OwnedMusicPlayer(self.service, LocalLyricsStore(Path(self.folder.name)),
            player=self.music, audio=self.audio, dj_player=self.voice, dj_audio=self.voice_audio,
            tasks=self.tasks, prefetch_tasks=self.prefetch, artwork_tasks=Tasks(), stream_cache=None)
        self.owned.personal_dj_enabled = True
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.owned.close)

    def finish(self, kind):
        token, action = next(job for job in reversed(self.tasks.jobs) if job[0][0] == kind)
        self.tasks.completed.emit(token, action(), None)
        return token

    def start(self, batch=None):
        self.owned._replace_queue(batch or segments(1) + segments(2), fm_source="netease", fm_kind="dj")
        self.finish("dj_stream")
        self.finish("stream")
        self.finish("dj_lyrics")

    def test_music_prepared_first_waits_and_voice_starts_only_after_music_playing(self):
        self.owned._replace_queue(segments(1), fm_source="netease", fm_kind="dj")
        self.finish("stream")
        self.assertNotEqual(self.music.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertNotEqual(self.voice.state, QMediaPlayer.PlaybackState.PlayingState)
        self.finish("dj_stream")
        self.assertEqual(self.music.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.voice.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertIn("song-1", self.music.source().toString())
        self.assertIn("voice-1", self.voice.source().toString())
        self.assertAlmostEqual(self.audio.volume(), .65 * .2)
        self.assertAlmostEqual(self.voice_audio.volume(), .9)

    def test_first_entry_prioritized_intro_waits_for_its_voice_and_uses_existing_duck_timeline(self):
        self.service.personal_dj = lambda source: (segments(9)[1],) + segments(4) + segments(5)
        self.assertTrue(self.owned.play_personal_fm())
        self.finish("catalog")
        self.finish("stream")
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-4")
        self.assertNotEqual(self.music.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertNotEqual(self.voice.state, QMediaPlayer.PlaybackState.PlayingState)
        self.finish("dj_stream")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertAlmostEqual(self.audio.volume(), .65 * .2)
        self.assertEqual(self.owned.personal_dj_music_gain(), .2)
        self.music.positionChanged.emit(12500)
        self.voice.position = 12500
        self.voice.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.track.position_seconds, 12.5)
        self.music.positionChanged.emit(13500)
        self.owned._restore_dj_music_gain()
        self.assertAlmostEqual(self.audio.volume(), .65)
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-4")

    def test_pause_before_neither_source_ready_prevents_automatic_start(self):
        self.owned._replace_queue(segments(1), fm_source="netease", fm_kind="dj")
        self.owned.send_playback_command("toggle_play_pause")
        self.finish("dj_stream"); self.finish("stream")
        self.assertFalse(self.owned._autoplay_after_load)
        self.assertNotEqual(self.music.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertNotEqual(self.voice.state, QMediaPlayer.PlaybackState.PlayingState)
        self.owned.send_playback_command("toggle_play_pause")
        self.assertTrue(self.owned.is_personal_dj_speaking())

    def test_pause_with_music_ready_but_voice_pending_keeps_both_paused(self):
        self.owned._replace_queue(segments(1), fm_source="netease", fm_kind="dj")
        self.finish("stream")
        self.owned.send_playback_command("toggle_play_pause")
        self.finish("dj_stream")
        self.assertFalse(self.owned._autoplay_after_load)
        self.assertNotEqual(self.music.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertNotEqual(self.voice.state, QMediaPlayer.PlaybackState.PlayingState)

    def test_voice_end_restores_gain_without_reopening_music_clock_or_lyrics(self):
        self.start()
        self.music.positionChanged.emit(6500)
        before = (self.owned._epoch, self.music.source(), self.owned.track.timed_lyrics)
        with patch.object(self.music, "stop", wraps=self.music.stop) as stop, \
                patch.object(self.music, "setSource", wraps=self.music.setSource) as source:
            self.voice.position = 6500
            self.voice.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual((self.owned._epoch, self.music.source(), self.owned.track.timed_lyrics), before)
        stop.assert_not_called(); source.assert_not_called()
        self.assertEqual(self.owned.track.position_seconds, 6.5)
        self.assertTrue(self.owned.track.is_playing)
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.assertAlmostEqual(self.audio.volume(), .13)
        self.music.positionChanged.emit(7000)
        self.owned._restore_dj_music_gain()
        self.assertAlmostEqual(self.audio.volume(), .65 * .6)
        self.music.positionChanged.emit(7500)
        self.owned._restore_dj_music_gain()
        self.assertAlmostEqual(self.audio.volume(), .65)
        self.assertIsNone(self.owned._dj_restore_start_seconds)

    def test_pause_resume_controls_both_and_paused_next_always_resumes_target(self):
        self.start()
        self.owned.send_playback_command("toggle_play_pause")
        self.assertEqual(self.music.state, QMediaPlayer.PlaybackState.PausedState)
        self.assertEqual(self.voice.state, QMediaPlayer.PlaybackState.PausedState)
        self.owned.send_playback_command("next")
        self.finish("dj_stream"); self.finish("stream")
        self.assertEqual(self.music.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.voice.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-2")

    def test_paused_previous_keeps_target_paused(self):
        self.start()
        self.owned.send_playback_command("next")
        self.finish("dj_stream"); self.finish("stream")
        self.owned.send_playback_command("toggle_play_pause")
        self.owned.send_playback_command("previous")
        self.finish("dj_stream"); self.finish("stream")
        self.assertEqual(self.music.state, QMediaPlayer.PlaybackState.PausedState)
        self.assertNotEqual(self.voice.state, QMediaPlayer.PlaybackState.PlayingState)

    def test_voice_error_and_disable_leave_healthy_music_and_original_gain(self):
        self.start()
        before = (self.owned._epoch, self.music.source(), self.owned.track.timed_lyrics)
        self.voice.errorOccurred.emit(QMediaPlayer.Error.ResourceError, "fixture")
        self.assertTrue(self.owned.track.is_playing)
        self.assertAlmostEqual(self.audio.volume(), .65)
        self.assertEqual((self.owned._epoch, self.music.source(), self.owned.track.timed_lyrics), before)
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "failed")
        self.owned.set_personal_dj_enabled(False)
        self.assertEqual((self.owned._epoch, self.music.source(), self.owned.track.timed_lyrics), before)

    def test_stale_voice_request_after_song_choice_cannot_restore_old_voice(self):
        self.owned._replace_queue(segments(1), fm_source="netease", fm_kind="dj")
        token, action = next(job for job in self.tasks.jobs if job[0][0] == "dj_stream")
        result = action()
        self.owned.play_queue((segments(9)[1],))
        self.finish("stream")
        self.tasks.completed.emit(token, result, None)
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-9")
        self.assertTrue(self.voice.source().isEmpty())
        self.assertAlmostEqual(self.audio.volume(), .65)

    def test_music_clock_advances_during_speech_and_voice_clock_cannot_replace_it(self):
        self.start()
        self.music.positionChanged.emit(9500)
        self.voice.positionChanged.emit(1200)
        self.assertEqual(self.owned.track.position_seconds, 9.5)
        self.assertTrue(self.owned.track.is_playing)
        self.assertTrue(self.owned.is_personal_dj_speaking())

    def test_multipart_speech_keeps_single_music_source_and_clock(self):
        voice, song = segments(1)
        stream = replace(voice.direct_stream, song_id="dj:part-two")
        part = replace(voice, song_id=stream.song_id, direct_stream=stream)
        self.start((voice, part, song))
        self.music.positionChanged.emit(5000)
        before = (self.owned._epoch, self.music.source(), self.owned.track.timed_lyrics)
        self.voice.position = 5000
        self.voice.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertTrue(self.owned.get_personal_dj_presentation()["cover_paused"])
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.finish("dj_stream")
        self.assertEqual((self.owned._epoch, self.music.source(), self.owned.track.timed_lyrics), before)
        self.assertEqual(self.owned.track.position_seconds, 5)
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertAlmostEqual(self.audio.volume(), .13)

    def test_new_speech_and_source_change_cancel_old_gain_restore(self):
        self.start()
        self.music.position = 5000
        self.music.positionChanged.emit(5000)
        self.voice.position = 5000
        self.voice.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.music.positionChanged.emit(5250)
        self.owned._restore_dj_music_gain()
        self.assertGreater(self.audio.volume(), .13)
        self.owned.send_playback_command("next")
        self.finish("dj_stream"); self.finish("stream")
        self.assertIsNone(self.owned._dj_restore_start_seconds)
        self.assertAlmostEqual(self.audio.volume(), .13)
        self.assertAlmostEqual(self.owned._dj_music_gain, .65)
        self.owned.play_queue((segments(9)[1],))
        self.finish("stream")
        self.owned._restore_dj_music_gain()
        self.assertAlmostEqual(self.audio.volume(), .65)

    def test_gain_restore_follows_music_clock_and_deliberate_gain_change(self):
        self.start()
        self.music.position = 2000
        self.music.positionChanged.emit(2000)
        self.voice.position = 2000
        self.voice.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.music.positionChanged.emit(2250)
        self.owned._restore_dj_music_gain()
        self.audio.setVolume(.8)
        self.assertAlmostEqual(self.audio.volume(), .8 * .325)
        self.owned.send_playback_command("toggle_play_pause")
        unchanged = self.audio.volume()
        self.owned._restore_dj_music_gain()
        self.assertEqual(self.audio.volume(), unchanged)
        self.music.positionChanged.emit(3000)
        self.owned._restore_dj_music_gain()
        self.assertAlmostEqual(self.audio.volume(), .8)

    def preview_catalog(self):
        voices = tuple(PersonalDjVoice(key, name, preview_stream=StreamInfo("netease", f"dj-preview:{key}",
            f"https://example.test/{key}.mp3", True, quality="dj_preview"), preview_duration_seconds=7)
            for key, name in (("A", "First real sample"), ("B", "Latest real sample")))
        self.service.personal_dj_voices_cached = lambda source: voices

    def test_latest_preview_waits_for_genuine_intro_without_changing_music_identity(self):
        self.preview_catalog(); self.start()
        before = (self.owned._epoch, self.music.source(), self.owned.track.track_id, self.owned.queue)
        genuine = self.voice.source()
        self.assertTrue(self.owned.preview_personal_dj_voice("A"))
        self.assertTrue(self.owned.preview_personal_dj_voice("B"))
        self.assertEqual(self.voice.source(), genuine)
        self.voice.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.finish("dj_stream")
        self.assertIn("B.mp3", self.voice.source().toString())
        self.assertEqual((self.owned._epoch, self.music.source(), self.owned.track.track_id, self.owned.queue), before)
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertAlmostEqual(self.owned._dj_music_gain, .65)

    def test_new_preview_cancels_old_pending_authorization_and_stop_releases_it(self):
        self.preview_catalog(); self.start()
        self.voice.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.owned.preview_personal_dj_voice("A")
        stale, action = next(job for job in reversed(self.tasks.jobs) if job[0][0] == "dj_stream")
        result = action()
        self.owned.preview_personal_dj_voice("B")
        self.tasks.completed.emit(stale, result, None)
        self.assertTrue(self.voice.source().isEmpty())
        self.finish("dj_stream")
        self.assertIn("B.mp3", self.voice.source().toString())
        self.owned.stop()
        self.assertTrue(self.voice.source().isEmpty())
        self.assertIsNone(self.owned._dj_pending_preview)
        self.assertAlmostEqual(self.audio.volume(), .65)

    def test_paused_preview_is_immediate_and_saved_preference_restore_never_auditions(self):
        self.preview_catalog(); self.start()
        self.voice.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.owned.send_playback_command("toggle_play_pause")
        self.owned.preview_personal_dj_voice("A")
        self.finish("dj_stream")
        self.assertIn("A.mp3", self.voice.source().toString())
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertEqual(self.music.state, QMediaPlayer.PlaybackState.PausedState)
        before = self.voice.source()
        self.owned.restore_personal_dj_voice("B")
        self.assertEqual(self.voice.source(), before)
        self.voice.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertAlmostEqual(self.audio.volume(), .65)

    def test_off_or_no_song_preview_is_immediate_without_enabling_or_loading_song(self):
        self.preview_catalog()
        self.owned.personal_dj_enabled = False
        self.assertTrue(self.owned.preview_personal_dj_voice("A"))
        self.finish("dj_stream")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertIsNone(self.owned.track)
        self.assertEqual(self.owned.queue, ())
        self.assertFalse(self.owned.personal_dj_enabled)
        self.assertNotEqual(self.music.state, QMediaPlayer.PlaybackState.PlayingState)

    def test_no_song_preview_account_replacement_rejects_old_download_and_active_sample(self):
        self.preview_catalog()
        self.owned.preview_personal_dj_voice("A")
        stale, action = next(job for job in reversed(self.tasks.jobs) if job[0][0] == "dj_stream")
        result = action()
        # Logout removes the cache; a new account may later have the same keys.
        self.preview_catalog()
        self.tasks.completed.emit(stale, result, None)
        self.assertTrue(self.voice.source() is None or self.voice.source().isEmpty())
        self.owned._guard_dj_preview()
        self.assertFalse(self.owned._dj_overlay_voices)
        self.owned.preview_personal_dj_voice("B")
        self.finish("dj_stream")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.service.personal_dj_voices_cached = lambda source: ()
        self.owned._guard_dj_preview()
        self.assertTrue(self.voice.source().isEmpty())

    def test_music_resumes_and_pauses_during_continuing_preview_without_source_reset(self):
        self.preview_catalog(); self.start()
        self.voice.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.music.positionChanged.emit(3000)
        self.owned.send_playback_command("toggle_play_pause")
        self.owned.preview_personal_dj_voice("A"); self.finish("dj_stream")
        before = (self.owned._epoch, self.music.source(), self.voice.source(), self.owned.queue)
        self.voice.position = 500
        self.owned.send_playback_command("toggle_play_pause")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertAlmostEqual(self.audio.volume(), .13)
        self.assertAlmostEqual(self.owned._dj_music_start_seconds, 2.5)
        token = self.owned.personal_dj_audio_token()
        self.music.positionChanged.emit(3200); self.voice.position = 700
        self.owned.send_playback_command("toggle_play_pause")
        self.assertEqual(self.voice.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertAlmostEqual(self.audio.volume(), .65)
        self.assertNotEqual(token, self.owned.personal_dj_audio_token())
        self.voice.position = 1200
        self.owned.send_playback_command("toggle_play_pause")
        self.assertAlmostEqual(self.owned._dj_music_start_seconds, 2.)
        self.assertAlmostEqual(self.audio.volume(), .13)
        self.assertEqual((self.owned._epoch, self.music.source(), self.voice.source(), self.owned.queue), before)


if __name__ == "__main__": unittest.main()
