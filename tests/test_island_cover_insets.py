"""Equal artwork insets belong only to the collapsed shell (t254)."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual


class IslandCoverInsetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.visual = IslandStackVisual(EmptyIslandDataProvider())
        self.visual.timer.stop()
        self.visual._run_background = lambda _name, action: action()
        self.visual.location_mode = "free"
        self.visual.finder_mode = False
        self.visual.music_auto_detect = False
        self.visual.music_playing = self.visual.media_is_playing = True
        self.visual.track_title = "Equal cover insets"
        self.visual.artist = "GluSpot fixture"
        self.visual.media_track_id = "cover-inset-fixture"
        self.visual.media_duration_seconds = 180.
        self.visual.media_position_seconds = self.visual.lyric_last_sync_position = 30.
        self.visual._estimated_media_position = lambda _now: 30.
        self.visual.lyrics = ["A line beside the cover", "A following line"]
        self.visual.timed_lyrics = [(0., self.visual.lyrics[0]), (40., self.visual.lyrics[1])]
        self.visual.timed_lyric_units = [(), ()]
        self.visual.timed_lyric_timing_kinds = ["line", "line"]
        self.visual.lyric_index = self.visual.prev_lyric_index = 0
        self.visual.lyric_changed_at = 0.
        self.visual.compact_lyric_cover_enabled = True
        self.visual.album_art = QImage(80, 80, QImage.Format.Format_RGB32)
        self.visual.album_art.fill(QColor("#087d86"))
        self.visual.album_color = QColor("#087d86")
        self.visual.secondary_color = QColor("#184f72")
        self.visual.music_surface_transition_started_at = 0.
        self.visual.music_expanded_layout_style = "standard"
        self.visual._finder_bar_active = lambda: self.visual.location_mode == "finder"
        self.visual._finder_bar_available = lambda: True
        self.visual._taskbar_compact_active = lambda: self.visual.location_mode == "tasker"
        self.visual.last_taskbar_rect = QRectF(0., 0., 960., 48.)
        self.visual.finder_bar_style = "capsule"

    def tearDown(self):
        self.visual.close()
        self.visual.deleteLater()
        self.app.processEvents()

    def paint(self, rect, *, expanded=False, style="sea_level", mode="free", dj=False, dpr=1):
        visual = self.visual
        visual.expanded, visual.expansion = expanded, float(expanded)
        visual.location_mode, visual.finder_mode = mode, mode == "finder"
        visual.finder_bar_rect = QRectF(0., 0., 960., rect.height())
        visual._island_dj_speaking = dj
        visual._island_dj_title, visual._island_dj_artist = "Radio title", "Radio artist"
        visual._finder_dj_reveal_progress = lambda: float(dj)
        visual.set_music_ambience_style(style, notify=False)
        image = QImage(650 * dpr, 200 * dpr, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        with patch("local.island_stack_visual.system_dark_mode", return_value=False), \
                patch("local.island_stack_visual.time.monotonic", return_value=100.):
            with QPainter(image) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                visual._paint_music(painter, rect, expanded)
        shell = visual._immersive_bar_content_rect(rect) if mode in {"finder", "tasker"} and not expanded else QRectF(rect)
        if mode in {"finder", "tasker"} and not expanded and visual.finder_bar_style == "capsule":
            shell.adjust(0., 1., 0., -1.)
        self.last_image = image
        return shell

    def assert_equal_insets(self, shell):
        cover = QRectF(self.visual.music_cover_rect)
        self.assertFalse(cover.isEmpty())
        self.assertTrue(shell.contains(cover))
        left = cover.left() - shell.left()
        self.assertAlmostEqual(cover.top() - shell.top(), left)
        self.assertAlmostEqual(shell.bottom() - cover.bottom(), left)
        self.assertAlmostEqual(cover.height(), shell.height() - 2. * left)
        self.assertAlmostEqual(cover.width(), cover.height())
        return cover

    def test_all_backgrounds_use_outer_shell_insets_only_when_collapsed(self):
        for style in ("sea_level", "solid_album", "system"):
            for height in (48., 64.):
                with self.subTest(style=style, height=height):
                    shell = self.paint(QRectF(13., 11., 520., height), style=style)
                    cover = self.assert_equal_insets(shell)
                    if height == 48.:
                        self.assertEqual(cover.size().width(), 32.)
                    for button in self.visual.music_button_rects.values():
                        self.assertFalse(cover.intersects(button))

    def test_native_bars_and_radio_use_the_visible_capsule_insets(self):
        for style in ("sea_level", "solid_album", "system"):
            for mode in ("free", "finder", "tasker"):
                for dj in (False, True):
                    with self.subTest(style=style, mode=mode, dj=dj):
                        height = 32. if mode != "free" else 84.
                        shell = self.paint(QRectF(13., 11., 520., height),
                                           style=style, mode=mode, dj=dj)
                        self.assert_equal_insets(shell)

    def test_transparent_finder_and_fade_keep_equal_insets(self):
        for face in ("transparent", "fade"):
            self.visual.finder_bar_style = face
            for dj in (False, True):
                with self.subTest(face=face, dj=dj):
                    shell = self.paint(QRectF(13., 11., 420., 28.), mode="finder", dj=dj)
                    self.assert_equal_insets(shell)

    def test_width_and_height_animation_frames_keep_equal_insets_at_both_dprs(self):
        for dpr in (1, 2):
            for width, height in ((520., 48.), (380.5, 56.25), (280., 72.),
                                  (380.5, 100.25), (520., 136.)):
                with self.subTest(dpr=dpr, width=width, height=height):
                    shell = self.paint(QRectF(13.25, 11.5, width, height), dpr=dpr)
                    self.assert_equal_insets(shell)

    def test_resolved_no_lyrics_keeps_current_cover_in_the_shared_slot(self):
        self.visual.lyric_flow_style = "album_island"
        prior_art = self.visual.album_art.copy()
        prior_art.fill(QColor("#c0ab30"))
        self.visual.album_history = [{"track_id": "prior", "album_art": prior_art}]
        next_art = self.visual.album_art.copy()
        next_art.fill(QColor("#ce2040"))
        self.visual.album_queue = [{"track_id": "next", "album_art": next_art}]
        for style in ("sea_level", "solid_album", "system"):
            with self.subTest(style=style), \
                    patch.object(self.visual, "_paint_album_island_slot",
                                 wraps=self.visual._paint_album_island_slot) as slots:
                shell = self.paint(QRectF(13., 11., 520., 56.), style=style)
                cover = self.assert_equal_insets(shell)
                side_slots = [call.args[1] for call in slots.call_args_list if not call.args[1]["center"]]
                expected = set() if style == "sea_level" else {-1, 1}
                self.assertEqual({slot["side"] for slot in side_slots}, expected)
                for slot in side_slots:
                    self.assertGreater(QRectF(slot["rect"]).left(), cover.right())
                    self.assertTrue(shell.contains(QRectF(slot["rect"])))

    def test_expanded_standard_matches_b4601bd_geometry_in_every_background_and_mode(self):
        visual = self.visual
        u = visual._u
        for style in ("sea_level", "solid_album", "system"):
            for mode in ("free", "finder", "tasker"):
                for dj in (False, True):
                    for width, height in ((520., 136.), (380.5, 100.25)):
                        rect = QRectF(13.25, 11.5, width, height)
                        with self.subTest(style=style, mode=mode, dj=dj, rect=rect), \
                                patch.object(visual, "_paint_scrolling_text") as text, \
                                patch.object(visual, "_paint_lyric_viewport") as lyrics:
                            self.paint(rect, expanded=True, style=style, mode=mode, dj=dj)
                            cover = QRectF(rect.left() + u(16), rect.top() + u(14), u(58), u(58))
                            self.assertEqual(visual.music_cover_rect, cover)
                            info_left = cover.right() + u(15)
                            info_width = rect.right() - cover.right() - u(132) - u(48)
                            self.assertEqual(text.call_args_list[0].args[1],
                                QRectF(info_left, cover.top() + u(2), info_width, u(34)))
                            self.assertEqual(text.call_args_list[1].args[1],
                                QRectF(info_left, cover.top() + u(37), info_width, u(24)))
                            for index, command in enumerate(("music:previous", "music:toggle_play_pause", "music:next")):
                                expected = QRectF(rect.right() - u(148) + index * u(45),
                                    cover.center().y() - u(18), u(36), u(36))
                                self.assertEqual(visual.music_button_rects[command], expected)
                            if dj:
                                lyrics.assert_not_called()
                            else:
                                self.assertEqual(lyrics.call_args.args[1],
                                    QRectF(rect.left() + u(22), cover.bottom() + u(10),
                                           rect.width() - u(44), rect.bottom() - cover.bottom() - u(28)))

    def test_expanded_dual_panel_matches_b4601bd_cover_and_lyric_viewport(self):
        visual = self.visual
        u = visual._u
        visual.music_expanded_layout_style = "dual_panel"
        for style in ("sea_level", "solid_album", "system"):
            for mode in ("free", "finder", "tasker"):
                rect = QRectF(13., 11., 520., 136.)
                lyric_panel, info_panel = visual._dual_panel_music_rects(rect)
                cover_size = max(u(48), min(u(66), info_panel.height() - u(26)))
                with self.subTest(style=style, mode=mode), \
                        patch.object(visual, "_paint_lyric_viewport") as lyrics:
                    self.paint(rect, expanded=True, style=style, mode=mode)
                    self.assertEqual(visual.music_cover_rect,
                        QRectF(info_panel.left() + u(22), info_panel.center().y() - cover_size / 2,
                               cover_size, cover_size))
                    self.assertEqual(lyrics.call_args.args[1],
                        lyric_panel.adjusted(u(30), u(16), -u(30), -u(14)))

    def test_expanded_album_stage_does_not_move_slots_to_the_compact_lane(self):
        visual = self.visual
        u = visual._u
        visual.lyric_flow_style = "album_island"
        visual.expanded = True
        visual._album_island_transition_progress = lambda: 1.
        previous_art, next_art = visual.album_art.copy(), visual.album_art.copy()
        previous_art.fill(QColor("#c0ab30"))
        next_art.fill(QColor("#ce2040"))
        visual.album_history = [{"track_id": "prior", "album_art": previous_art}]
        visual.album_queue = [{"track_id": "next", "album_art": next_art}]
        rect = QRectF(13., 11., 520., 136.)
        stage = QRectF(rect.left() + u(18), rect.top() + u(58), rect.width() - u(36), rect.height() - u(76))
        for style in ("sea_level", "solid_album", "system"):
            for mode in ("free", "finder", "tasker"):
                expected = {int(slot["side"]): QRectF(slot["rect"])
                            for slot in visual._album_island_slots(stage, reserve_text=True)}
                with self.subTest(style=style, mode=mode), \
                        patch.object(visual, "_paint_album_island_slot") as slots, \
                        patch.object(visual, "_paint_scrolling_text") as text:
                    self.paint(rect, expanded=True, style=style, mode=mode)
                    actual = {int(call.args[1]["side"]): QRectF(call.args[1]["rect"])
                              for call in slots.call_args_list}
                    self.assertEqual(actual, expected)
                    self.assertEqual(text.call_args_list[0].args[1],
                        QRectF(rect.left() + u(18), rect.top() + u(13), rect.width() - u(186), u(24)))

    def test_expanded_dual_panel_still_paints_lyric_ink_in_the_baseline_panel(self):
        self.visual.music_expanded_layout_style = "dual_panel"
        rect = QRectF(13., 11., 520., 136.)
        self.paint(rect, expanded=True)
        with_lyrics = self.last_image.copy()
        lyric_panel, _info_panel = self.visual._dual_panel_music_rects(rect)
        crop = lyric_panel.toAlignedRect()
        with patch.object(self.visual, "_paint_lyric_viewport"):
            self.paint(rect, expanded=True)
        self.assertNotEqual(with_lyrics.copy(crop), self.last_image.copy(crop))

    def test_crowded_taskbar_slots_keep_equal_insets_and_separate_actions(self):
        for width in (28., 52., 58.):
            for style in ("sea_level", "solid_album", "system"):
                with self.subTest(width=width, style=style):
                    shell = self.paint(QRectF(13., 11., width, 44.), mode="tasker", style=style)
                    cover = self.assert_equal_insets(shell)
                    self.assertEqual(set(self.visual.music_button_rects), {"music:toggle_play_pause"})
                    button = self.visual.music_button_rects["music:toggle_play_pause"]
                    self.assertTrue(shell.contains(button))
                    self.assertFalse(cover.intersects(button))
                    self.assertEqual(self.visual._hit_test(cover.center()), "music:open_surface")
                    self.assertEqual(self.visual._hit_test(button.center()), "music:toggle_play_pause")


if __name__ == "__main__":
    unittest.main()
