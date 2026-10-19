"""Finder narration stays a single row and returns gently to real lyrics."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.music_surface_palette import MusicSurfacePalette
from tests import test_island_lyric_follow_geometry as fixtures


class FinderDjCompactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def visual(self, height=24.):
        visual = fixtures.IslandLyricFollowGeometryTests._visual()
        visual.data_provider = Mock()
        # Optional FFT is genuinely unavailable until a test supplies numeric
        # bands; an auto-generated Mock is not a provider audio-frame contract.
        visual.data_provider.get_owned_personal_dj_audio_frame.return_value = None
        visual.expanded = False
        visual.mydockfinder_running = True
        visual.finder_mode = True
        visual.finder_bar_enabled = True
        visual.finder_bar_rect = QRectF(0., 0., 1600., height)
        visual.finder_bar_style = "transparent"
        visual.finder_material = SimpleNamespace(mid_color=QColor("#303236"))
        visual.plugin_panel_open = False
        visual._taskbar_compact_active = lambda: False
        visual._set_expanded = Mock(side_effect=lambda value: setattr(visual, "expanded", value))
        visual._music_surface_active = False
        visual.scene_suppressed = False
        visual.isVisible = lambda: True
        visual._music_meta_text_color = lambda alpha=246: QColor(255, 255, 255, alpha)
        visual._personal_dj_island_color = lambda: QColor("#d08773")
        visual._personal_dj_island_backdrop = lambda *args, **kwargs: QColor("#303236")
        visual._solid_album_surface_palette = lambda: MusicSurfacePalette(
            QColor("#70483e"), QColor("#303236"), QColor("#d08773"))
        return visual

    def sync(self, visual, state, now):
        visual.data_provider.get_personal_dj_presentation.return_value = state
        return visual._sync_personal_dj_island(now)

    def speaking(self, visual, now=100.):
        return self.sync(visual, {"speaking": True, "token": 1, "next_title": visual.track_title}, now)

    def completion(self, visual, now=101.):
        return {"speaking": False, "completion_id": 1,
            "completed_track_id": visual.media_track_id, "completed_at": now}

    @staticmethod
    def render(visual, width=440., height=24., now=100.6):
        shell = QRectF(0., 0., width, height)
        image = QImage(round(width), round(height), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        texts = []
        with patch("local.island_stack_visual.time.monotonic", return_value=now), QPainter(image) as painter:
            original = painter.drawText
            def record(*args):
                texts.append((QRectF(args[0]), args[-1], QFontMetrics(painter.font())))
                return original(*args)
            with patch.object(painter, "drawText", side_effect=record):
                visual._paint_personal_dj_island(painter, shell)
        return shell, texts

    def test_visible_genuine_completion_consumed_without_expanding_finder(self):
        visual = self.visual()
        self.speaking(visual)
        finished = self.completion(visual)
        self.assertTrue(self.sync(visual, finished, 101.))
        self.assertFalse(visual.expanded)
        visual._set_expanded.assert_not_called()
        self.assertEqual(visual._island_dj_completion_seen, 1)
        self.assertFalse(visual._personal_dj_island_presenting())
        self.assertEqual(getattr(visual, "_island_dj_outro_until", 0.), 0.)
        self.sync(visual, finished, 104.)
        self.assertFalse(visual.expanded)
        visual._set_expanded.assert_not_called()

    def test_top_free_tasker_keep_existing_completion_hold(self):
        for mode in ("top", "free", "tasker"):
            with self.subTest(mode=mode):
                visual = self.visual()
                visual.finder_mode = False
                visual.location_mode = mode
                self.speaking(visual)
                self.sync(visual, self.completion(visual), 101.)
                self.assertTrue(visual.expanded)
                self.assertEqual(visual._island_dj_outro_until, 103.32)
                visual._set_expanded.assert_called_once_with(True)

    def test_completion_does_not_close_a_user_opened_finder(self):
        visual = self.visual()
        self.speaking(visual)
        visual.expanded = True
        self.sync(visual, self.completion(visual), 101.)
        self.assertTrue(visual.expanded)
        visual._set_expanded.assert_not_called()

    def test_narration_metadata_is_one_fitted_song_artist_line(self):
        visual = self.visual()
        self.speaking(visual)
        shell, texts = self.render(visual)
        self.assertEqual([item[1] for item in texts], ["Follow geometry - Test singer"])
        self.assertTrue(shell.contains(texts[0][0]))
        self.assertGreaterEqual(texts[0][2].height(), 12)
        self.assertEqual(visual.music_button_rects, {})

    def test_loading_ready_failure_evidence_stays_on_the_single_row(self):
        for state, field, caption in (("loading", "introduction_pending", "加载中"),
                ("ready", "introduction_pending", "已就绪"),
                ("failed", "introduction_failed", "失败")):
            with self.subTest(state=state):
                visual = self.visual()
                self.sync(visual, {"speaking": False, field: True, "state": state,
                    "token": 1, "next_title": visual.track_title}, 100.)
                shell, texts = self.render(visual)
                self.assertEqual([item[1] for item in texts], [caption, "Follow geometry - Test singer"])
                status, metadata = texts
                self.assertTrue(all(shell.contains(item[0]) for item in texts))
                self.assertLess(metadata[0].right(), status[0].left())
                self.assertEqual(metadata[0].center().y(), status[0].center().y())
                self.assertGreaterEqual(status[0].width(), status[2].horizontalAdvance(caption))
                self.assertFalse(visual._island_dj_speaking)
                visual.data_provider.get_owned_personal_dj_audio_frame.assert_not_called()
                self.assertTrue(visual.media_is_playing)

    def test_real_bar_sizes_measure_full_metadata_and_preserve_height(self):
        for height in (20., 24., 30., 40.):
            with self.subTest(height=height):
                visual = self.visual(height)
                self.speaking(visual)
                with patch("local.island_stack_visual.time.monotonic", return_value=100.6):
                    width, actual_height = visual._finder_bar_music_size()
                shell, texts = self.render(visual, width, height)
                self.assertEqual(actual_height, height)
                self.assertLessEqual(width, visual.finder_bar_rect.width())
                self.assertEqual(texts[0][1], "Follow geometry - Test singer")
                self.assertTrue(shell.contains(texts[0][0]))

    def test_long_metadata_elides_inside_bounded_row_beside_real_status(self):
        visual = self.visual()
        visual.track_title = "A very long next song title " * 8
        visual.artist = "Singer with a very long stage name " * 8
        self.sync(visual, {"speaking": False, "introduction_failed": True,
            "state": "failed", "token": 1, "next_title": visual.track_title}, 100.)
        shell, texts = self.render(visual, width=180.)
        status, metadata = texts
        self.assertEqual(status[1], "失败")
        self.assertTrue(all(shell.contains(item[0]) for item in texts))
        self.assertLess(metadata[0].right(), status[0].left())
        self.assertLessEqual(metadata[2].horizontalAdvance(metadata[1]), metadata[0].width())
        self.assertNotEqual(metadata[1], f"{visual.track_title} - {visual.artist}")

    def test_entry_and_completion_fade_preserve_real_provider_state(self):
        visual = self.visual()
        self.speaking(visual)
        self.assertEqual(visual._finder_dj_reveal_progress(100.), 0.)
        self.assertAlmostEqual(visual._finder_dj_reveal_progress(100.21), .5)
        self.assertAlmostEqual(visual._finder_dj_reveal_progress(100.42), 1.)
        self.sync(visual, self.completion(visual), 101.)
        self.assertFalse(visual._personal_dj_island_presenting())
        self.assertEqual(visual._island_dj_title, "")
        self.assertEqual(visual._finder_dj_retained_title, "Follow geometry")
        self.assertEqual(visual._finder_dj_retained_artist, "Test singer")
        self.assertIsNone(visual._island_dj_frame)
        self.assertEqual(visual._finder_dj_reveal_progress(101.), 1.)
        self.assertAlmostEqual(visual._finder_dj_reveal_progress(101.21), .5)
        self.assertAlmostEqual(visual._finder_dj_reveal_progress(101.42), 0.)
        with patch("local.island_stack_visual.time.monotonic", return_value=101.21):
            self.assertTrue(visual._finder_dj_render_active())
        self.sync(visual, self.completion(visual), 101.43)
        with patch("local.island_stack_visual.time.monotonic", return_value=101.43):
            self.assertFalse(visual._finder_dj_render_active())
        self.assertEqual(visual._finder_dj_retained_title, "")
        visual.data_provider.request_control.assert_not_called()

    def test_pending_to_speech_uses_same_continuous_reveal(self):
        visual = self.visual()
        self.sync(visual, {"speaking": False, "introduction_pending": True,
            "state": "loading", "token": 1, "next_title": visual.track_title}, 100.)
        reveal = visual._finder_dj_reveal_progress(100.15)
        self.speaking(visual, now=100.15)
        self.assertAlmostEqual(visual._finder_dj_reveal_progress(100.15), reveal)
        self.assertEqual(visual._finder_dj_reveal_at, 100.)
        self.assertEqual(visual._finder_dj_retained_status, "")

    def test_completion_fade_continues_idle_phase_and_does_not_retain_speech_fft(self):
        visual = self.visual()
        visual.data_provider.get_owned_personal_dj_audio_frame.return_value = SimpleNamespace(low=.1, mid=.2, high=.3)
        self.speaking(visual)
        self.sync(visual, self.completion(visual), 101.)
        with patch("local.island_stack_visual.paint_personal_dj_icon") as icon:
            self.render(visual, now=101.1)
        self.assertAlmostEqual(icon.call_args.kwargs["elapsed"], 1.1)
        self.assertIsNone(icon.call_args.kwargs["bands"])

    def test_returning_to_finder_consumes_old_top_completion_expansion(self):
        visual = self.visual()
        visual.finder_mode = False
        self.speaking(visual)
        finished = self.completion(visual)
        self.sync(visual, finished, 101.)
        self.assertTrue(visual.expanded)
        visual.finder_mode = True
        self.sync(visual, finished, 101.1)
        self.assertFalse(visual.expanded)
        self.assertEqual(visual._island_dj_outro_until, 0.)

    def test_new_introduction_during_fade_reverses_without_opacity_jump(self):
        visual = self.visual()
        self.speaking(visual)
        self.sync(visual, self.completion(visual), 101.)
        reveal = visual._finder_dj_reveal_progress(101.15)
        self.sync(visual, {"speaking": True, "token": 2, "next_title": "Next title"}, 101.15)
        self.assertAlmostEqual(visual._finder_dj_reveal_progress(101.15), reveal)
        self.assertEqual(visual._finder_dj_retained_title, "Next title")
        self.assertAlmostEqual(visual._finder_dj_reveal_progress(101.57), 1.)

    def test_hidden_departed_or_changed_owner_cancels_retained_content(self):
        for reason in ("page", "suppressed", "hidden", "mode", "track"):
            with self.subTest(reason=reason):
                visual = self.visual()
                self.speaking(visual)
                finished = self.completion(visual)
                self.sync(visual, finished, 101.)
                if reason == "page":
                    visual._music_surface_active = True
                    visual.music_fullscreen_overlay = SimpleNamespace(isVisible=lambda: True, isMinimized=lambda: False)
                elif reason == "suppressed":
                    visual.scene_suppressed = True
                elif reason == "hidden":
                    visual.isVisible = lambda: False
                elif reason == "mode":
                    visual.finder_mode = False
                else:
                    visual.media_track_id = "replacement"
                self.sync(visual, finished, 101.1)
                self.assertEqual(visual._finder_dj_reveal_progress(101.1), 0.)
                self.assertEqual(visual._finder_dj_retained_title, "")
                self.assertEqual(visual._finder_dj_retained_artist, "")

    def test_partial_fade_width_interpolates_without_changing_bar_height(self):
        visual = self.visual()
        visual.track_title = "A very long title to reserve much more room than this lyric"
        self.speaking(visual)
        widths = []
        for now in (100., 100.105, 100.21, 100.315, 100.42):
            with patch("local.island_stack_visual.time.monotonic", return_value=now):
                width, height = visual._finder_bar_music_size()
            widths.append(width)
            self.assertEqual(height, 24.)
        self.assertTrue(all(a <= b for a, b in zip(widths, widths[1:])))
        self.assertAlmostEqual(widths[2], (widths[0] + widths[-1]) / 2.)
        self.assertGreater(widths[-1], widths[0])


if __name__ == "__main__":
    unittest.main()
