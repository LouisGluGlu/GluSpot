from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.audio_output_icons import _icon_mask, _svg_renderer, _tinted_icon, paint_audio_output_icon
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_lyric_visual_layers import _LayerOwner


class AudioOutputIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _render(self, kind, color=QColor(40, 130, 210), width=80, height=80):
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            paint_audio_output_icon(painter, QRectF(image.rect()), kind, color)
        return image

    def test_source_assets_render_in_requested_tint_and_keep_native_alpha(self):
        for kind in ("output", "speaker", "homepod", "homepod-mini", "airpods-pro", "headphones"):
            with self.subTest(kind=kind):
                image = self._render(kind)
                ink = [image.pixelColor(x, y) for y in range(80) for x in range(80)
                       if image.pixelColor(x, y).alpha() > 80]
                self.assertGreater(len(ink), 200)
                for pixel in ink:
                    self.assertLess(abs(pixel.red() - 40), 4)
                    self.assertLess(abs(pixel.green() - 130), 4)
                    self.assertLess(abs(pixel.blue() - 210), 4)
        image = self._render("homepod")
        self.assertAlmostEqual(image.pixelColor(40, 50).alpha(), round(255 * 0.425), delta=2)
        self.assertAlmostEqual(image.pixelColor(40, 5).alpha(), round(255 * 0.85), delta=2)

    def test_airpods_wide_svg_is_centered_without_stretching(self):
        image = self._render("airpods-pro")
        points = [(x, y) for y in range(80) for x in range(80) if image.pixelColor(x, y).alpha() > 50]
        width = max(x for x, _ in points) - min(x for x, _ in points)
        height = max(y for _, y in points) - min(y for _, y in points)
        self.assertGreater(width / height, 1.45)
        self.assertAlmostEqual((min(y for _, y in points) + max(y for _, y in points)) / 2.0,
                               40.0, delta=2.0)

    def test_generic_receivers_and_earphones_do_not_claim_model_specific_assets(self):
        with patch("local.audio_output_icons._svg_renderer", side_effect=AssertionError("unidentified model asset")):
            airplay = self._render("airplay")
            headphones = self._render("airpods")
        self.assertNotEqual(bytes(airplay.constBits()), bytes(headphones.constBits()))
        for image in (airplay, headphones):
            self.assertGreater(sum(image.pixelColor(x, y).alpha() > 0 for y in range(80) for x in range(80)), 200)

    def test_repeated_icon_paints_reuse_bounded_svg_and_tint_caches(self):
        self._render("homepod-mini")
        hits = _tinted_icon.cache_info().hits
        with patch("local.audio_output_icons.QSvgRenderer", side_effect=AssertionError("uncached SVG")):
            first = self._render("homepod-mini")
            repeated = self._render("homepod_mini")
        self.assertEqual(bytes(first.constBits()), bytes(repeated.constBits()))
        self.assertGreater(_tinted_icon.cache_info().hits, hits)
        self.assertEqual(_svg_renderer.cache_info().maxsize, 8)
        self.assertEqual(_icon_mask.cache_info().maxsize, 128)
        self.assertEqual(_tinted_icon.cache_info().maxsize, 256)


class MusicSurfaceCircleControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _LayerOwner()
        self.owner.enable_dual_line_lyrics = False
        self.owner.data_provider = SimpleNamespace(get_audio_route_snapshot=lambda: None)
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(1190, 666)

    def tearDown(self):
        self.window._audio_output_panel = None
        self.window.shutdown()
        self.owner.close()

    def _controls(self, now=100.0):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter, patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window._paint_window_controls(painter, QRectF(self.window.rect()))
        return image

    def test_four_controls_share_circle_diameter_spacing_and_keep_idle_click_gate(self):
        self.window._reset_control_idle(100.0)
        image = self._controls()
        mode = self.window._button_rects["return"]
        translation = self.window._button_rects["translation"]
        output = self.window._button_rects["audio_output"]
        beta = self.window._obo_badge_rect(mode, self.window._surface_scale())
        circles = [output, translation, beta, mode]
        for circle in circles:
            self.assertEqual(circle.size(), mode.size())
            self.assertEqual(circle.width(), circle.height())
            self.assertEqual(image.pixelColor(round(circle.left()), round(circle.top())).alpha(), 0)
        for left, right in zip(circles, circles[1:]):
            self.assertAlmostEqual(right.left() - left.right(), 12.0)
        self._controls(103.0)
        self.assertEqual(self.window._button_rects, {})

    def test_output_open_state_tints_blue_without_reading_provider_in_paint(self):
        self.window._audio_output_panel = SimpleNamespace(isVisible=lambda: True)
        self.window._reset_control_idle(100.0)
        with patch.object(self.owner.data_provider, "get_audio_route_snapshot", side_effect=AssertionError("paint read")):
            image = self._controls()
        output = self.window._button_rects["audio_output"].toAlignedRect()
        blue = [image.pixelColor(x, y) for y in range(output.top(), output.bottom())
                for x in range(output.left(), output.right()) if image.pixelColor(x, y).blue()
                > image.pixelColor(x, y).red() + 80]
        self.assertGreater(len(blue), 60)
        self.assertGreater(image.pixelColor(output.center().x(), output.top() + 5).red(), 200)

    def test_translation_active_uses_transparent_symbol_cutout(self):
        self.owner.enable_dual_line_lyrics = True
        self.window._reset_control_idle(100.0)
        image = self._controls()
        circle = self.window._button_rects["translation"]
        path = self.window._translation_symbol_path(QRectF(circle.center().x() - circle.width() * 0.29,
                                                         circle.center().y() - circle.height() * 0.29,
                                                         circle.width() * 0.58, circle.height() * 0.58))
        points = [(x, y) for y in range(round(circle.top()) + 4, round(circle.bottom()) - 4)
                  for x in range(round(circle.left()) + 4, round(circle.right()) - 4)
                  if path.contains(QRectF(x + 0.2, y + 0.2, 0.6, 0.6))]
        self.assertGreater(len(points), 30)
        self.assertTrue(all(image.pixelColor(x, y).alpha() < 70 for x, y in points))
        self.assertGreater(image.pixelColor(round(circle.center().x()), round(circle.top()) + 3).red(), 200)

    def test_genuine_timing_switches_white_beta_to_gold_symbol_and_ring(self):
        self.window._reset_control_idle(100.0)
        inactive = self._controls()
        circle = self.window._obo_badge_rect(self.window._window_control_base_rect(QRectF(self.window.rect())),
                                             self.window._surface_scale()).toAlignedRect()
        self.owner.timed_lyrics = [(0.0, "Hello world"), (3.0, "Next")]
        self.owner.timed_lyric_units = [(SimpleNamespace(start_seconds=0.0, end_seconds=0.5, text="Hello "),
                                        SimpleNamespace(start_seconds=0.5, end_seconds=1.0, text="world")), ()]
        self.owner.timed_lyric_timing_kinds = ["unit", "line"]
        active = self._controls()
        gold_points = [(x, y) for y in range(circle.top(), circle.bottom())
                       for x in range(circle.left(), circle.right())
                       if active.pixelColor(x, y).red() > 130
                       and active.pixelColor(x, y).green() > 100
                       and active.pixelColor(x, y).blue() < 80]
        self.assertGreater(len(gold_points), 150)
        self.assertTrue(any(y <= circle.top() + 2 for _, y in gold_points))
        self.assertFalse(any(inactive.pixelColor(x, y).red() > 130
                             and inactive.pixelColor(x, y).blue() < 80 for x, y in gold_points))
