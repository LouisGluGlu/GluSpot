"""The visible island artwork follows one shell-relative inset in every pose."""
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

    def test_all_backgrounds_and_expanded_layouts_use_outer_shell_insets(self):
        for style in ("sea_level", "solid_album", "system"):
            for expanded, layout, height in ((False, "standard", 48.), (False, "standard", 64.),
                                               (True, "standard", 136.), (True, "dual_panel", 136.)):
                with self.subTest(style=style, expanded=expanded, layout=layout, height=height):
                    self.visual.music_expanded_layout_style = layout
                    shell = self.paint(QRectF(13., 11., 520., height), expanded=expanded, style=style)
                    cover = self.assert_equal_insets(shell)
                    if height == 48.:
                        self.assertEqual(cover.size().width(), 32.)
                    for button in self.visual.music_button_rects.values():
                        self.assertFalse(cover.intersects(button))

    def test_native_bars_and_radio_use_the_visible_capsule_insets(self):
        for style in ("sea_level", "solid_album", "system"):
            for mode in ("free", "finder", "tasker"):
                for expanded in (False, True):
                    for dj in (False, True):
                        with self.subTest(style=style, mode=mode, expanded=expanded, dj=dj):
                            height = 136. if expanded else 32. if mode != "free" else 84.
                            shell = self.paint(QRectF(13., 11., 520., height), expanded=expanded,
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
            for expanded in (False, True):
                for width, height in ((520., 48.), (380.5, 56.25), (280., 72.),
                                      (380.5, 100.25), (520., 136.)):
                    with self.subTest(dpr=dpr, expanded=expanded, width=width, height=height):
                        shell = self.paint(QRectF(13.25, 11.5, width, height), expanded=expanded, dpr=dpr)
                        self.assert_equal_insets(shell)

    def test_resolved_no_lyrics_keeps_current_cover_in_the_shared_slot(self):
        self.visual.lyric_flow_style = "album_island"
        prior_art = self.visual.album_art.copy()
        prior_art.fill(QColor("#c0ab30"))
        self.visual.album_history = [{"track_id": "prior", "album_art": prior_art}]
        next_art = self.visual.album_art.copy()
        next_art.fill(QColor("#ce2040"))
        self.visual.album_queue = [{"track_id": "next", "album_art": next_art}]
        for expanded in (False, True):
            for style in ("sea_level", "solid_album", "system"):
                with self.subTest(expanded=expanded, style=style), \
                        patch.object(self.visual, "_paint_album_island_slot",
                                     wraps=self.visual._paint_album_island_slot) as slots:
                    shell = self.paint(QRectF(13., 11., 520., 136. if expanded else 56.),
                                       expanded=expanded, style=style)
                    cover = self.assert_equal_insets(shell)
                    side_slots = [call.args[1] for call in slots.call_args_list if not call.args[1]["center"]]
                    expected = set() if style == "sea_level" and not expanded else {-1, 1}
                    self.assertEqual({slot["side"] for slot in side_slots}, expected)
                    for slot in side_slots:
                        self.assertGreater(QRectF(slot["rect"]).left(), cover.right())
                        self.assertTrue(shell.contains(QRectF(slot["rect"])))

    def test_expanded_text_and_lyrics_reserve_the_full_cover(self):
        for layout in ("standard", "dual_panel"):
            self.visual.music_expanded_layout_style = layout
            with self.subTest(layout=layout), patch.object(self.visual, "_paint_scrolling_text") as text, \
                    patch.object(self.visual, "_paint_lyric_viewport") as lyrics:
                shell = self.paint(QRectF(13., 11., 520., 136.), expanded=True)
                cover = self.assert_equal_insets(shell)
                for call in text.call_args_list + lyrics.call_args_list:
                    self.assertGreaterEqual(call.args[1].left(), cover.right())

    def test_expanded_dual_panel_still_paints_lyric_ink_beside_the_cover(self):
        self.visual.music_expanded_layout_style = "dual_panel"
        rect = QRectF(13., 11., 520., 136.)
        self.paint(rect, expanded=True)
        with_lyrics = self.last_image.copy()
        cover = QRectF(self.visual.music_cover_rect)
        lyric_panel, _info_panel = self.visual._dual_panel_music_rects(rect)
        crop = QRectF(cover.right() + 1., lyric_panel.top(),
                      lyric_panel.right() - cover.right() - 1., lyric_panel.height()).toAlignedRect()
        with patch.object(self.visual, "_paint_lyric_viewport"):
            self.paint(rect, expanded=True)
        self.assertNotEqual(with_lyrics.copy(crop), self.last_image.copy(crop))


if __name__ == "__main__":
    unittest.main()
