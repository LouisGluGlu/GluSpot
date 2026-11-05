"""Full-window lyric ink, edge retreat and proportional columns (t205-t210)."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from _demos.narrow3.layout import NarrowLayout
from _demos.narrow3.narrow_window import NarrowStudyWindow
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider


class ResponsiveLyricEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.data_provider = _Provider()
        self.owner._music_library_back_callback = Mock()
        self.owner.media_position_seconds = self.owner.lyric_last_sync_position = 51.
        self.owner.lyric_index = 5
        self.owner.lyric_changed_at = 99.
        self.owner.media_duration_seconds = 160.
        self.owner.media_capture_epoch_id = 1
        self.owner.enable_dual_line_lyrics = False
        self.owner.timed_lyrics = [(float(i * 10), f"A readable lyric row number {i}") for i in range(14)]
        self.owner.lyrics = [text for _, text in self.owner.timed_lyrics]
        self.owner.album_art = QImage(320, 320, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor("#24392d"))
        self.window = NarrowStudyWindow(self.owner, "B", "left", live=True)
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window.set_content_layout("lyrics", animate=False)
        self.window._finish_content_layout()

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.owner.close()

    def pose(self, narrow, size):
        self.window._explicit_width_target = (*size, narrow)
        self.window.resize(*size)
        self.window._sync_narrow_mode()
        self.window._finish_width_chrome()
        self.window._cancel_lyric_resize()
        self.window._finish_width_layout()
        self.window.narrow = narrow
        self.window._width_layout_fraction = float(narrow)

    def reveal(self, amount):
        self.window.visibility.progress = amount
        fade = self.window.visibility.opacity
        fade.start = fade.target = amount
        fade.duration = 0.
        self.window._control_reveal_from = self.window._control_reveal_target = amount
        self.window._control_transition_started_at = 100.
        self.window._control_last_motion_at = 100.

    def ink(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.window._paint_live_lyric_column(painter, QRectF(self.window.rect()))
        return image

    def test_narrow_and_wide_shown_and_hidden_have_no_interior_rectangular_clip(self):
        for narrow, size in ((True, (511, 540)), (False, (690, 540))):
            self.pose(narrow, size)
            active = None
            for shown in (False, True):
                with self.subTest(narrow=narrow, shown=shown):
                    self.reveal(float(shown))
                    clips = []
                    original = self.window._draw_future_wrapped_layout
                    def observe(painter, *args, **kwargs):
                        clips.append(painter.worldTransform().mapRect(painter.clipBoundingRect()))
                        return original(painter, *args, **kwargs)
                    with patch("local.music_surface_window.time.monotonic", return_value=100.), \
                            patch.object(self.window, "_lyric_view_anchor", return_value=(5., 5, True)), \
                            patch.object(self.window, "_lyric_blur_follow_levels", return_value=((0.,) * 14, False)), \
                            patch.object(self.window, "_draw_future_wrapped_layout", side_effect=observe):
                        self.ink()
                    self.assertTrue(clips, "Actual lyric drawing must exercise the painter clip")
                    for clip in (self.window._lyric_clip_rect, self.window._lyric_ink_clip_rect, *clips):
                        self.assertAlmostEqual(clip.top(), 0., delta=1.)
                        self.assertAlmostEqual(clip.bottom(), size[1], delta=1.)
                    if active is None:
                        active = QRectF(self.window._lyric_hit_rects[5])
                    self.assertEqual(self.window._lyric_hit_rects[5], active)

    def test_alpha_feather_has_ink_at_every_interior_height_and_soft_edges(self):
        def fill_ink(painter, rect, **_kwargs):
            painter.fillRect(rect, Qt.GlobalColor.white)
        for narrow, size in ((True, (511, 540)), (False, (690, 540))):
            self.pose(narrow, size)
            for shown in (False, True):
                with self.subTest(narrow=narrow, shown=shown), \
                        patch.object(self.window, "_paint_lyric_column_ink", side_effect=fill_ink):
                    self.reveal(float(shown))
                    image = self.ink().convertToFormat(QImage.Format.Format_RGBA8888)
                    alpha = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.width(), 4)[:, :, 3]
                    column = alpha[:, image.width() // 2]
                    self.assertTrue(np.all(column[5:-5] > 0), "No opaque reserved interior band")
                    self.assertLessEqual(column[0], 3)  # Qt samples the first pixel centre.
                    self.assertEqual(column[size[1] // 2], 255)
                    self.assertLess(column[10], 255)
                    self.assertLess(column[-10], 255)
                    self.assertGreater(np.unique(column[:size[1] // 3]).size, 20)
                    self.assertGreater(np.unique(column[-size[1] // 3:]).size, 20)

    def test_each_group_retreats_vertically_along_its_own_edge(self):
        window = self.window
        for narrow, size in ((True, (511, 540)), (False, (800, 540))):
            self.pose(narrow, size)
            surface = QRectF(window.rect())
            top = window._window_control_base_rect(surface)
            transport = NarrowLayout(*size).transport_panel
            for reveal in (0., .25, .5, .75, 1.):
                self.reveal(reveal)
                for name, shift, sign in (
                    ("window", window._window_control_shift(top, reveal), -1),
                    ("library", window._library_control_shift(surface, reveal), -1),
                    ("transport", window._transport_control_shift(transport, reveal), 1),
                    ("dj", window._personal_dj_control_shift(surface, reveal), 1),
                ):
                    with self.subTest(narrow=narrow, name=name, reveal=reveal):
                        self.assertAlmostEqual(shift.x(), 0.)
                        self.assertGreaterEqual(shift.y() * sign, 0.)
                        if reveal < 1.:
                            self.assertGreater(shift.y() * sign, 0.)
                layout = NarrowLayout(*size)
                for key, base, sign in (("return", layout.top[-1], -1),
                                        ("library", layout.back, -1),
                                        ("media:next", layout.next, 1)):
                    actual = window.posed_rect(key, base)
                    self.assertEqual(actual.left(), base.left())
                    self.assertGreaterEqual((actual.top() - base.top()) * sign, 0.)

    def test_shrinking_wide_columns_share_the_width_budget_without_moving_breakpoint(self):
        widths = []
        for width in (960, 800, 690):
            self.pose(False, (width, 540))
            content, cover, lyric = self.window._wide_content_column_geometry()
            legacy = self.window._wide_content_column_geometry(measure_fit=True)
            self.reveal(1.)
            frame = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
            with patch("local.music_surface_window.time.monotonic", return_value=100.):
                self.window.render(frame)
            title_font, _title, artist_font, _artist = self.window._metadata_layout(
                cover, content, self.window._surface_scale(), 1.)
            with self.subTest(width=width):
                self.assertGreaterEqual(lyric.width(), content.width() * .45)
                self.assertEqual(cover.width(), cover.height())
                self.assertGreater(lyric.left(), cover.right())
                self.assertEqual(lyric.top(), 0.)
                self.assertEqual(lyric.bottom(), 540.)
                self.assertAlmostEqual(legacy[1].width(), 404. * self.window._surface_scale())
                self.assertAlmostEqual(legacy[2].left(), legacy[1].right() + 100. * self.window._surface_scale())
            widths.append((cover.width(), lyric.width(), title_font.pixelSize(),
                           artist_font.pixelSize(), self.window._media_control_panel_rect.width()))
        self.assertAlmostEqual(widths[0][0], widths[1][0], delta=1.)
        # Layout G caps the cover by 0.44×height, so these three widths share it.
        self.assertAlmostEqual(widths[1][0], widths[2][0], delta=1.)
        self.assertGreater(widths[0][1], widths[1][1])
        self.assertGreater(widths[1][1], widths[2][1])
        for slot in (2, 3):
            self.assertAlmostEqual(widths[0][slot], widths[1][slot], delta=1.)
            if slot == 2:
                # The 1.15 title emphasis rounds to the same pixel at these two units.
                self.assertGreaterEqual(widths[1][slot], widths[2][slot])
            else:
                self.assertGreater(widths[1][slot], widths[2][slot])
        self.assertAlmostEqual(widths[0][4], widths[1][4], delta=1.)
        self.assertAlmostEqual(widths[1][4], widths[2][4], delta=1.)


if __name__ == "__main__":
    unittest.main()
