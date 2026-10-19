from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.island_stack_visual import IslandStackVisual
from local.app import LocalIslandApp
from local.music_surface_palette import MusicSurfacePalette, relative_luminance
from local.personal_dj_icon import personal_dj_ring_color, personal_dj_lightening_limit
from tests import test_island_lyric_follow_geometry as fixtures


class IslandDjOutroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def visual(self):
        visual = fixtures.IslandLyricFollowGeometryTests._visual()
        visual.data_provider = Mock()
        visual.expanded = False
        visual.plugin_panel_open = False
        visual.finder_mode = False
        visual._taskbar_compact_active = lambda: False
        visual._set_expanded = lambda value: setattr(visual, "expanded", value)
        visual._music_surface_active = False
        visual.scene_suppressed = False
        visual.isVisible = lambda: True
        visual._music_meta_text_color = lambda alpha=246: QColor(255, 255, 255, alpha)
        return visual

    def sync(self, visual, state, now):
        visual.data_provider.get_personal_dj_presentation.return_value = state
        return visual._sync_personal_dj_island(now)

    def finish(self, visual, now=101.):
        self.sync(visual, {"speaking": True, "token": 1, "next_title": visual.track_title}, now - 1.)
        return {"speaking": False, "completion_id": 1,
            "completed_track_id": visual.media_track_id, "completed_at": now}

    def test_real_completion_morphs_then_holds_expanded_for_two_seconds(self):
        visual = self.visual()
        finished = self.finish(visual)
        self.assertTrue(self.sync(visual, finished, 101.))
        self.assertTrue(visual.expanded)
        with patch("local.island_stack_visual.time.monotonic", return_value=101.16):
            self.assertAlmostEqual(visual._personal_dj_island_outro_progress(), .5)
        with patch("local.island_stack_visual.time.monotonic", return_value=101.32):
            self.assertAlmostEqual(visual._personal_dj_island_outro_progress(), 1.)
        visual.left_stack_at = 99.
        self.assertFalse(visual._left_stack_collapse_due(103.31))
        self.assertFalse(visual._hover_auto_activation_due(103.31))
        self.sync(visual, finished, 103.319)
        self.assertTrue(visual.expanded)
        self.sync(visual, finished, 103.32)
        self.assertFalse(visual.expanded)
        self.assertEqual(visual._island_dj_outro_until, 0.)

    def test_pause_loading_failure_or_preview_never_become_completion(self):
        for snapshot in ({"speaking": False, "state": "ready"},
                {"speaking": False, "state": "loading"},
                {"speaking": False, "state": "failed"},
                {"speaking": False, "completion_id": 1, "completed_track_id": "follow-geometry",
                    "completed_at": 101., "is_preview": True}):
            with self.subTest(snapshot=snapshot):
                visual = self.visual()
                self.finish(visual)
                self.sync(visual, snapshot, 101.)
                self.assertFalse(visual.expanded)

    def test_hidden_or_stale_completion_is_consumed_without_replaying_on_return(self):
        for hidden, age in ((True, 0.), (False, 1.)):
            visual = self.visual()
            finished = self.finish(visual)
            visual._music_surface_active = hidden
            visual.music_fullscreen_overlay = (SimpleNamespace(isVisible=lambda: True, isMinimized=lambda: False)
                                               if hidden else None)
            self.sync(visual, finished, 101. + age)
            self.assertFalse(visual.expanded)
            visual._music_surface_active = False
            visual.music_fullscreen_overlay = None
            self.sync(visual, finished, 101. + age + .1)
            self.assertFalse(visual.expanded)

    def test_changed_track_source_new_speech_or_disabled_dj_cancel_hold(self):
        for case in ("track", "surface", "suppressed", "hidden", "speech", "disabled"):
            visual = self.visual()
            finished = self.finish(visual)
            self.sync(visual, finished, 101.)
            self.assertTrue(visual.expanded)
            if case == "track":
                visual.media_track_id = "replacement"
            elif case == "surface":
                visual._music_surface_active = True
                visual.music_fullscreen_overlay = SimpleNamespace(isVisible=lambda: True, isMinimized=lambda: False)
            elif case == "suppressed":
                visual.scene_suppressed = True
            elif case == "hidden":
                visual.isVisible = lambda: False
            elif case == "speech":
                finished = {"speaking": True, "token": 2}
            else:
                finished = {"speaking": False}
            self.sync(visual, finished, 101.2)
            self.assertFalse(visual.expanded, case)
            self.assertEqual(visual._island_dj_outro_until, 0., case)

    def test_different_track_completion_cannot_open_the_island(self):
        visual = self.visual()
        finished = self.finish(visual)
        finished["completed_track_id"] = "old-song"
        self.sync(visual, finished, 101.)
        self.assertFalse(visual.expanded)

    def test_genuine_loading_and_failure_remain_visible_without_claiming_speech(self):
        for state, field in (("loading", "introduction_pending"), ("ready", "introduction_pending"),
                ("failed", "introduction_failed")):
            visual = self.visual()
            snapshot = {"speaking": False, "state": state, field: True,
                "next_title": "Next song", "token": 4}
            self.sync(visual, snapshot, 100.)
            self.assertFalse(visual._island_dj_speaking)
            self.assertTrue(visual._personal_dj_island_presenting())
            self.assertTrue(visual._active_visual_animation_plan(100.).active)
            self.assertEqual(visual._island_dj_title, "Next song")
            visual.data_provider.get_owned_personal_dj_audio_frame.assert_not_called()
            snapshot[field] = False
            self.sync(visual, snapshot, 102.1)
            self.assertFalse(visual._personal_dj_island_presenting())
            self.assertFalse(visual.expanded)

    def test_future_preload_and_preview_do_not_announce_current_song_loading(self):
        for snapshot in ({"speaking": False, "next_preload_state": "ready"},
                {"speaking": False, "introduction_pending": True, "is_preview": True}):
            visual = self.visual()
            self.sync(visual, snapshot, 100.)
            self.assertFalse(visual._personal_dj_island_presenting())

    def test_application_publishes_new_song_before_speaking_artist_and_completion(self):
        visual = self.visual()
        visual.update = Mock()
        visual.music_fullscreen_overlay = None
        def refresh(now):
            visual.media_track_id, visual.track_title, visual.artist = "new-song", "New title", "New artist"
            return True
        visual._refresh_media_data = Mock(side_effect=refresh)
        app = SimpleNamespace(data_provider=SimpleNamespace(_owned_active=lambda: True),
            owned_player=SimpleNamespace(track=SimpleNamespace(track_id="new-song", is_playing=True)),
            window=visual, _owned_surface_key=None)
        visual.data_provider.get_personal_dj_presentation.return_value = {"speaking": True,
            "token": 1, "next_title": "New title"}
        with patch("local.app.time.monotonic", return_value=100.):
            LocalIslandApp._owned_playback_changed(app)
        self.assertEqual(visual._island_dj_observed_track, "new-song")
        self.assertEqual(visual._island_dj_artist, "New artist")
        visual.data_provider.get_personal_dj_presentation.return_value = {"speaking": False,
            "completion_id": 1, "completed_track_id": "new-song", "completed_at": 101.}
        # Same source/play-state still needs DJ synchronization after EOF.
        with patch("local.app.time.monotonic", return_value=101.):
            LocalIslandApp._owned_playback_changed(app)
        visual._refresh_media_data.assert_called_once()
        self.assertTrue(visual.expanded)

    def test_speech_title_and_actual_artist_are_both_painted(self):
        visual = self.visual()
        visual._island_dj_title = "Next song"
        visual._island_dj_artist = "Actual singer"
        visual._personal_dj_island_color = lambda: QColor("#6b85ab")
        image = QImage(440, 112, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter, patch.object(painter, "drawText", wraps=painter.drawText) as text:
            visual._paint_personal_dj_island(painter, QRectF(0., 0., 440., 112.))
        self.assertEqual([call.args[-1] for call in text.call_args_list], ["Next song", "Actual singer"])
        self.assertLess(text.call_args_list[0].args[0].bottom(), text.call_args_list[1].args[0].top())

    def test_neutral_covers_use_valid_contrasting_ink_without_inventing_color(self):
        visual = self.visual()
        for level in (0, 10, 80, 128, 180, 245, 255):
            with self.subTest(level=level):
                background = QColor(level, level, level)
                visual._solid_album_surface_palette = lambda: MusicSurfacePalette(background, background, background)
                # The legacy lyric fill intentionally cannot color gray covers.
                visual.album_color = background
                self.assertFalse(visual._wave_fill_color().isValid())
                ink = visual._personal_dj_island_color()
                self.assertTrue(ink.isValid())
                self.assertEqual(ink.red(), ink.green())
                self.assertEqual(ink.green(), ink.blue())
                a, b = relative_luminance(ink), relative_luminance(background)
                self.assertGreaterEqual((max(a, b) + .05) / (min(a, b) + .05), 2.30)

    def test_island_uses_characteristic_accent_instead_of_provider_primary_hue(self):
        visual = self.visual()
        palette = MusicSurfacePalette(QColor("#151515"), QColor("#222222"), QColor("#ba6039"))
        visual.album_color = QColor("#151515")
        visual._solid_album_surface_palette = lambda: palette
        ink = visual._personal_dj_island_color()
        self.assertTrue(ink.isValid())
        self.assertLess(abs(ink.hsvHue() - palette.characteristic.hsvHue()), 3)
        with patch("local.island_stack_visual.future_lyric_color", side_effect=AssertionError("must reuse cache")):
            self.assertEqual(visual._personal_dj_island_color(), ink)

    def test_light_and_gray_backdrops_do_not_disappear_along_ring_lightening_path(self):
        visual = self.visual()
        for level in (0, 80, 128, 200, 255):
            background = QColor(level, level, level)
            visual._solid_album_surface_palette = lambda: MusicSurfacePalette(background, background, background)
            ink = visual._personal_dj_island_color()
            limit = visual._island_dj_lightening
            self.assertEqual(limit, personal_dj_lightening_limit(ink, background))
            contrasts = []
            for step in range(37):
                ring = personal_dj_ring_color(ink, .5 + step / 100., max_lightening=limit)
                alpha = min(ring.alphaF(), 230. / 255.)
                composite = QColor(*(round(a * alpha + b * (1. - alpha))
                    for a, b in zip(ring.getRgb()[:3], background.getRgb()[:3])))
                a, b = relative_luminance(composite), relative_luminance(background)
                contrasts.append((max(a, b) + .05) / (min(a, b) + .05))
                self.assertGreaterEqual(ring.lightness(), ink.lightness())
            self.assertGreaterEqual(min(contrasts), min(2.30, contrasts[0]), level)
        self.assertEqual(personal_dj_lightening_limit(QColor(170, 170, 170), QColor(0, 0, 0)), .60)

    def test_taskbar_speech_title_and_artist_stay_inside_the_short_shell(self):
        visual = self.visual()
        visual._island_dj_title = "Next song"
        visual._island_dj_artist = "Actual singer"
        visual._personal_dj_island_color = lambda: QColor("#6b85ab")
        for height in (40, 48, 60, 112):
            image = QImage(440, height, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            shell = QRectF(0., 0., 440., float(height))
            with QPainter(image) as painter, patch.object(painter, "drawText", wraps=painter.drawText) as text:
                visual._paint_personal_dj_island(painter, shell)
            self.assertEqual(len(text.call_args_list), 2)
            for call in text.call_args_list:
                self.assertTrue(shell.contains(call.args[0]), (height, call.args[0]))

    def test_real_expanded_paint_morphs_rings_into_actual_cover_and_title_slot(self):
        visual = self.visual()
        visual._island_dj_outro_started = 100.
        visual._island_dj_outro_until = 102.32
        visual._normalize_music_expanded_layout_style = lambda value: "standard"
        visual._personal_dj_island_color = lambda: QColor("#6b85ab")
        image = QImage(580, 240, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        poses = []
        for now in (100., 100.08, 100.16, 100.24, 100.32):
            with patch("local.island_stack_visual.time.monotonic", return_value=now), \
                    patch.object(visual, "_paint_interactive_music_cover") as cover, \
                    patch.object(visual, "_paint_music_control_button"), \
                    patch.object(visual, "_paint_scrolling_text"), \
                    patch.object(visual, "_paint_lyric_viewport"), QPainter(image) as painter:
                visual._paint_music_expanded(painter, QRectF(0., 0., 580., 240.))
                poses.append(QRectF(cover.call_args.args[1]))
        self.assertTrue(all(a.width() >= b.width() for a, b in zip(poses, poses[1:])))
        self.assertEqual(poses[-1], QRectF(visual._u(16), visual._u(14), visual._u(58), visual._u(58)))

    def test_short_shell_loading_states_reserve_legible_complete_caption(self):
        visual = self.visual()
        visual._island_dj_title = "Next song"
        visual._island_dj_artist = "Actual singer with a long name"
        for height in (40, 48, 60):
            for state, caption in (("loading", "播报加载中"), ("ready", "播报已准备好"), ("failed", "播报加载失败")):
                visual._island_dj_status = state
                shell = QRectF(0., 0., 440., visual._u(height))
                image = QImage(440, 112, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                painted = []
                with QPainter(image) as painter:
                    original = painter.drawText
                    def record(*args):
                        painted.append((QRectF(args[0]), args[-1], QFontMetrics(painter.font())))
                        return original(*args)
                    with patch.object(painter, "drawText", side_effect=record):
                        visual._paint_personal_dj_island(painter, shell)
                matches = [item for item in painted if item[1] == caption]
                self.assertEqual(len(matches), 1, (height, state))
                box, text, metrics = matches[0]
                self.assertTrue(shell.contains(box))
                self.assertGreaterEqual(box.width(), metrics.horizontalAdvance(text))
                artist_box = painted[-1][0]
                self.assertLess(artist_box.right(), box.left())


if __name__ == "__main__":
    unittest.main()
