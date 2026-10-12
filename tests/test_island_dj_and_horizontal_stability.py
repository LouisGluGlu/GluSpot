from __future__ import annotations

import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.models import AudioFrame
from api.lyrics import LocalLyricsStore
from api.online_music import StreamInfo, LyricPayload
from api.providers import EmptyIslandDataProvider, PlaybackSourceProvider
from local.app import LocalIslandApp
from local.island_stack_visual import IslandKind, IslandStackVisual
from local.music_surface_window import MusicSurfaceWindow
from local.owned_music_player import OwnedMusicPlayer
from tests import test_island_lyric_follow_geometry as fixtures
from tests.test_owned_playback import Player, Tasks
from tests.test_personal_dj_overlay import Audio
from tests.test_personal_dj_playback import segments


class IslandDjAndHorizontalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_fit_margin_cannot_switch_centered_lyrics_to_right_alignment(self):
        visual = fixtures.IslandLyricFollowGeometryTests._visual()
        phase = {"progress": .8}
        centers = []
        for width in (208.9, 209., 209.1, 210., 210.1, 211., 209.5):
            rect = QRectF(200 - width / 2, 0, width, 44)
            x = visual._island_lyric_row_x(rect, 200., True, phase)
            centers.append(x + 100.)
        self.assertEqual(centers, [200.] * len(centers))

    def test_fit_to_overflow_boundary_is_continuous_under_spring_width_changes(self):
        visual = fixtures.IslandLyricFollowGeometryTests._visual()
        xs = []
        for width in (199.99, 200., 200.01):
            rect = QRectF(200 - width / 2, 0, width, 44)
            xs.append(visual._island_lyric_row_x(rect, 200., True, {"progress": .7}))
        self.assertLess(max(xs) - min(xs), .02)

    def test_whole_row_only_scrolls_forward_once_and_holds_tail(self):
        visual = fixtures.IslandLyricFollowGeometryTests._visual()
        rect = QRectF(10, 0, 200, 44)
        xs = []
        for i in range(100):
            visual.scroll_phase = i * .93  # unrelated, reversing marquee clock
            xs.append(visual._island_lyric_row_x(rect, 500., True,
                      {"whole_row": True, "elapsed": i / 10., "span": 4.}))
        self.assertTrue(all(a >= b for a, b in zip(xs, xs[1:])))
        self.assertEqual(xs[0], rect.left())
        self.assertEqual(xs[-1], rect.right() - 500.)

    def test_paused_row_and_shared_translation_ignore_global_scroll_phase(self):
        visual = fixtures.IslandLyricFollowGeometryTests._visual()
        phase = {"whole_block_progress": 1., "elapsed": 1.5, "span": 4.}
        rect = QRectF(10, 0, 200, 44)
        xs = []
        for i in range(100):
            visual.scroll_phase = i / 5.
            xs.append(visual._island_lyric_row_x(rect, 500., True, phase))
        self.assertEqual(len(set(xs)), 1)

    def test_actual_low_perception_phase_stays_stable_with_paused_source_clock(self):
        visual = fixtures.IslandLyricFollowGeometryTests._visual()
        visual.music_surface_reading_strategy = "low_perception"
        visual.media_position_seconds = 2.
        visual.media_is_playing = False
        poses = []
        for now in (100., 101., 103., 106., 109.):
            visual.scroll_phase = now
            phase = visual._lyric_phase(now)
            self.assertTrue(phase["whole_row"])
            self.assertIn("whole_block_progress", phase)
            poses.append(visual._island_lyric_row_x(QRectF(10, 0, 200, 44), 500., True, phase))
        self.assertEqual(len(set(poses)), 1)

    def test_live_whole_row_painter_no_longer_uses_metadata_marquee(self):
        visual = fixtures.IslandLyricFollowGeometryTests._visual()
        image = QImage(300, 90, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        text = "This is a deliberately long lyric sentence wider than the island"
        with patch.object(visual, "_scrolling_text_x", side_effect=AssertionError("metadata marquee")), QPainter(image) as painter:
            visual._paint_live_lyric_text(painter, QRectF(10, 10, 180, 60), text, 18,
                visual._music_lyric_base_text_color(), visual._music_lyric_text_color(), True,
                {"whole_row": True, "elapsed": 2., "span": 5.}, centered=True, centered_scroll=True)
            visual._paint_wave_stream_whole_block(painter,
                ((QRectF(10, 10, 180, 60), text, 18, True, visual._music_lyric_base_text_color(),
                  visual._music_lyric_text_color(), None),),
                {"whole_block_progress": 1., "elapsed": 2., "span": 5.})

    def test_provider_snapshot_uses_actual_speech_and_clears_after_its_end(self):
        visual = fixtures.IslandLyricFollowGeometryTests._visual()
        provider = Mock()
        visual.data_provider = provider
        provider.get_personal_dj_presentation.return_value = {"speaking": True, "next_title": "下一首歌名", "token": 3}
        provider.get_owned_personal_dj_audio_frame.return_value = AudioFrame(.3, .1, .4)
        self.assertTrue(visual._sync_personal_dj_island(100.))
        self.assertTrue(visual._island_dj_speaking)
        self.assertEqual(visual._island_dj_title, "下一首歌名")
        self.assertEqual(visual._island_dj_frame, AudioFrame(.3, .1, .4))
        provider.get_personal_dj_presentation.return_value = {"speaking": False}
        self.assertTrue(visual._sync_personal_dj_island(102.))
        self.assertFalse(visual._island_dj_speaking)
        self.assertIsNone(visual._island_dj_frame)
        provider.get_owned_personal_dj_audio_frame.assert_called_once_with()

    def test_dj_paint_uses_cached_title_and_frame_with_no_provider_io_or_old_lyrics(self):
        visual = fixtures.IslandLyricFollowGeometryTests._visual()
        visual._island_dj_speaking = True
        visual._island_dj_title = "下一首歌名"
        visual._island_dj_frame = AudioFrame(.3, .1, .4)
        visual._island_dj_started = 100.
        visual._wave_fill_color = visual._music_lyric_text_color
        visual.data_provider = Mock()
        image = QImage(440, 90, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch.object(visual, "_record_visual_paint_section"), \
             patch.object(visual, "_execute_music_surface_paint_step") as step, \
             patch.object(visual, "_paint_compact_lyric_viewport", side_effect=AssertionError("old lyrics")), \
             patch("local.island_stack_visual.time.monotonic", return_value=100.5), QPainter(image) as painter:
            visual._paint_music(painter, QRectF(1, 1, 438, 88), False)
        self.assertTrue(step.called)  # Existing ambience is still executed.
        self.assertNotIn("compact_content", [call.args[0] for call in step.call_args_list])
        visual.data_provider.get_personal_dj_presentation.assert_not_called()
        self.assertGreater(sum(image.pixelColor(x, y).lightness() > 180 for y in range(20, 70) for x in range(100, 430)), 100)

    def test_missing_frequency_frame_does_not_hide_actual_speech(self):
        visual = fixtures.IslandLyricFollowGeometryTests._visual()
        visual.data_provider = Mock()
        visual.data_provider.get_personal_dj_presentation.return_value = {
            "speaking": True, "next_title": "下一首", "token": 7}
        visual.data_provider.get_owned_personal_dj_audio_frame.side_effect = RuntimeError("FFT unavailable")
        self.assertTrue(visual._sync_personal_dj_island(100.))
        self.assertTrue(visual._island_dj_speaking)
        self.assertEqual(visual._island_dj_title, "下一首")
        self.assertIsNone(visual._island_dj_frame)
        self.assertTrue(visual._active_visual_animation_plan(100.).active)

    def test_dj_content_has_no_card_or_solid_ball_and_large_independent_rings(self):
        visual = fixtures.IslandLyricFollowGeometryTests._visual()
        visual._island_dj_speaking = True
        visual._island_dj_title = "下一首"
        visual._island_dj_started = 100.
        visual._wave_fill_color = lambda: QColor("#86b7df")
        image = QImage(440, 112, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch("local.island_stack_visual.time.monotonic", return_value=100.5), \
             patch("local.island_stack_visual.paint_personal_dj_icon") as rings, QPainter(image) as painter:
            visual._paint_personal_dj_island(painter, QRectF(0, 0, 440, 112))
        self.assertGreaterEqual(rings.call_args.args[1].height(), visual._u(96))
        # With rings mocked out their entire area and the shell corner remain
        # transparent: neither a ball nor a nested capsule was painted.
        self.assertEqual(image.pixelColor(55, 56).alpha(), 0)
        self.assertEqual(image.pixelColor(220, 3).alpha(), 0)

    def test_dj_grows_actual_top_island_and_survives_missing_music_metadata(self):
        visual = fixtures.IslandLyricFollowGeometryTests._visual()
        visual._island_dj_speaking = True
        visual._island_dj_title = "下一首"
        visual.music_playing = visual.music_auto_detect = True
        visual.live_audio_available = False
        visual.track_title = visual.artist = ""
        visual.lyrics = []
        visual.media_is_playing = False
        with patch.object(visual, "_taskbar_compact_active", return_value=False):
            width, height = visual._base_main_size_for_kind(IslandKind.MUSIC, visual._u(440))
        self.assertEqual(width, visual._u(440))
        self.assertEqual(height, visual._u(112))
        self.assertGreater(height, visual._u(80))
        self.assertTrue(visual._music_enabled_for_stack())

    def test_real_provider_publishes_speech_to_island_with_hidden_music_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            tasks = Tasks()
            music, voice = Player(), Player()
            service = SimpleNamespace(
                stream_url=lambda source, song: StreamInfo(source, song, f"https://example.test/{song}.mp3", True),
                lyrics=lambda source, song: LyricPayload(source, song, raw_lrc="[00:01]歌词"),
                personal_dj=lambda source: segments(2))
            owned = OwnedMusicPlayer(service, LocalLyricsStore(Path(temporary)), player=music,
                audio=Audio(), dj_player=voice, dj_audio=Audio(), tasks=tasks,
                artwork_tasks=Tasks(), prefetch_tasks=Tasks(), stream_cache=None)
            system = EmptyIslandDataProvider()
            system.suspend_live_audio = Mock()
            provider = PlaybackSourceProvider(system, owned, lambda key, default=None: default)
            visual = IslandStackVisual(data_provider=provider)
            visual.timer.stop()
            surface = MusicSurfaceWindow(visual)
            visual.music_fullscreen_overlay = surface
            app = SimpleNamespace(data_provider=provider, owned_player=owned, window=visual, _owned_surface_key=None)
            changed = lambda: LocalIslandApp._owned_playback_changed(app)
            owned.changed.connect(changed)
            try:
                self.assertFalse(surface.isVisible())
                owned.personal_dj_enabled = True
                owned._replace_queue(segments(1), fm_source="netease", fm_kind="dj")
                for kind in ("stream", "dj_stream"):
                    token, action = next(job for job in reversed(tasks.jobs) if job[0][0] == kind)
                    tasks.completed.emit(token, action(), None)
                self.assertTrue(provider.is_owned_personal_dj_speaking())
                self.assertTrue(visual._island_dj_speaking)
                self.assertEqual(visual._island_dj_title, owned.track.title)
                self.assertEqual(visual.media_track_id, "owned:netease:song-1")
                self.assertFalse(surface.isVisible())
                voice.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
                self.assertFalse(visual._island_dj_speaking)
            finally:
                owned.changed.disconnect(changed)
                provider._owned_audio_bridge.close()
                owned.close()
                surface.shutdown()
                visual.close()


if __name__ == "__main__":
    unittest.main()
