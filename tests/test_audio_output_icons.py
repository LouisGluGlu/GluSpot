from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.audio_output_icons import (
    _icon_mask, _status_mask, _svg_renderer, _tinted_icon, _tinted_status,
    paint_audio_output_icon, paint_audio_output_status,
)
from local.branding import ROOT_DIR
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_lyric_visual_layers import _LayerOwner


class AudioOutputIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # Windows' offscreen plugin does not enumerate system fonts. Register
        # the existing bundled font so count verification exercises real digits.
        if not QFontDatabase.families():
            QFontDatabase.addApplicationFont(str(ROOT_DIR / "assets" / "fonts" / "AlibabaPuHuiTi" /
                                                 "AlibabaPuHuiTi-Bold.ttf"))

    def _render(self, kind, color=QColor(40, 130, 210), width=80, height=80):
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            paint_audio_output_icon(painter, QRectF(image.rect()), kind, color)
        return image

    def test_source_assets_render_in_requested_tint_and_keep_native_alpha(self):
        for kind in ("output", "speaker", "homepod", "homepod-mini", "airpods-pro", "airpods", "headphones"):
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
            headphones = self._render("earbuds")
        self.assertNotEqual(bytes(airplay.constBits()), bytes(headphones.constBits()))
        for image in (airplay, headphones):
            self.assertGreater(sum(image.pixelColor(x, y).alpha() > 0 for y in range(80) for x in range(80)), 200)

    def test_regular_airpods_supplied_asset_differs_from_pro_and_generic_headphones(self):
        images = [self._render(kind) for kind in ("airpods", "airpods-pro", "headphones")]
        self.assertEqual(len({bytes(image.constBits()) for image in images}), 3)
        self.assertIsNotNone(_svg_renderer("airpods"))
        # The new square regular-AirPods asset must retain its original aspect.
        points = [(x, y) for y in range(80) for x in range(80)
                  if images[0].pixelColor(x, y).alpha() > 50]
        self.assertAlmostEqual((min(x for x, _ in points) + max(x for x, _ in points)) / 2.0,
                               39.5, delta=2.0)
        self.assertGreater(max(y for _, y in points) - min(y for _, y in points), 55)
        regular = self._render_status(("airpods", "homepod-mini"))
        pro = self._render_status(("airpods-pro", "homepod-mini"))
        self.assertNotEqual(self._region_bytes(regular, 0, 0, 40, 49),
                            self._region_bytes(pro, 0, 0, 40, 49))

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

    def _render_status(self, kinds, color=QColor(40, 130, 210), diameter=80, *, count=None):
        image = QImage(diameter, diameter, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            paint_audio_output_status(painter, QRectF(image.rect()), kinds, color, count=count)
        return image

    def _region_bytes(self, image, x, y, width, height):
        region = image.copy(x, y, width, height)
        return bytes(region.constBits())

    def test_status_never_draws_the_caller_owned_face_or_ring(self):
        for kinds in ((), ("homepod",), ("homepod-mini",) * 5,
                      ("homepod", "homepod-mini"), ("airpods-pro", "speaker", "device")):
            with self.subTest(kinds=kinds):
                image = self._render_status(kinds)
                points = [(x, y) for y in range(80) for x in range(80)
                          if image.pixelColor(x, y).alpha() > 0]
                self.assertGreater(len(points), 200)
                self.assertTrue(all((x - 39.5) ** 2 + (y - 39.5) ** 2 < 38 ** 2 for x, y in points))
                self.assertEqual(image.pixelColor(40, 1).alpha(), 0)
                self.assertEqual(image.pixelColor(1, 40).alpha(), 0)

    def test_group_count_is_beneath_glyph_and_matches_total_members(self):
        two = self._render_status(("homepod-mini",) * 2)
        five = self._render_status(("homepod-mini",) * 5)
        # The same model keeps the same glyph while the readable lower digit changes.
        self.assertEqual(self._region_bytes(two, 0, 0, 80, 49),
                         self._region_bytes(five, 0, 0, 80, 49))
        self.assertNotEqual(self._region_bytes(two, 0, 52, 80, 28),
                            self._region_bytes(five, 0, 52, 80, 28))
        for image in (two, five):
            self.assertGreater(sum(image.pixelColor(x, y).alpha() > 30
                                   for y in range(52, 75) for x in range(22, 58)), 35)
        mixed_two = self._render_status(("homepod", "homepod-mini"))
        mixed_five = self._render_status(("homepod",) * 4 + ("homepod-mini",))
        self.assertEqual(self._region_bytes(mixed_two, 0, 0, 80, 49),
                         self._region_bytes(mixed_five, 0, 0, 80, 49))
        self.assertNotEqual(self._region_bytes(mixed_two, 0, 52, 80, 28),
                            self._region_bytes(mixed_five, 0, 52, 80, 28))

    def test_composition_uses_first_two_distinct_connection_kinds(self):
        first = self._render_status(("homepod", "homepod-mini", "speaker"))
        reordered = self._render_status(("speaker", "homepod_mini", "homepod"))
        self.assertNotEqual(self._region_bytes(first, 0, 0, 80, 49),
                            self._region_bytes(reordered, 0, 0, 80, 49))
        later = self._render_status(("homepod", "homepod-mini", "airpods-pro", "speaker"))
        self.assertEqual(self._region_bytes(first, 0, 0, 80, 49),
                         self._region_bytes(later, 0, 0, 80, 49))
        homogeneous = self._render_status(("homepod",) * 3)
        self.assertNotEqual(self._region_bytes(first, 0, 0, 80, 49),
                            self._region_bytes(homogeneous, 0, 0, 80, 49))

    def test_only_homepod_pair_overlaps_and_other_pairs_keep_ordered_sides(self):
        pod_first = self._render_status(("homepod", "homepod-mini"))
        mini_first = self._render_status(("homepod-mini", "homepod"))
        self.assertEqual(bytes(pod_first.constBits()), bytes(mini_first.constBits()))
        for pair in (("airpods-pro", "homepod"), ("homepod-mini", "headphones"),
                     ("speaker", "airplay"), ("device", "homepod")):
            with self.subTest(pair=pair):
                ordered = self._render_status(pair)
                reversed_order = self._render_status(tuple(reversed(pair)))
                self.assertNotEqual(self._region_bytes(ordered, 0, 0, 80, 49),
                                    self._region_bytes(reversed_order, 0, 0, 80, 49))
                # Two glyphs have a clear central gap; generic pairs do not
                # borrow the HomePod-only front/back overlap.
                self.assertTrue(all(ordered.pixelColor(x, y).alpha() == 0
                                    for y in range(8, 49) for x in range(39, 41)))

    def test_explicit_total_count_matches_full_membership_composition(self):
        representatives = self._render_status(("airpods-pro", "homepod"), count=5)
        full = self._render_status(("airpods-pro", "airpods-pro", "homepod", "speaker", "homepod-mini"))
        self.assertEqual(bytes(representatives.constBits()), bytes(full.constBits()))
        homogeneous = self._render_status(("homepod-mini",), count=3)
        all_minis = self._render_status(("homepod-mini",) * 3)
        self.assertEqual(bytes(homogeneous.constBits()), bytes(all_minis.constBits()))

    def test_mixed_homepod_front_glyph_keeps_original_translucent_ink(self):
        image = self._render_status(("homepod", "homepod-mini"), QColor(40, 130, 210), 200)
        # Front mini body overlaps the rear HomePod, without accumulating its alpha.
        body = image.pixelColor(108, 100)
        self.assertAlmostEqual(body.alpha(), round(255 * .425), delta=2)
        self.assertAlmostEqual(body.red(), 40, delta=3)
        self.assertAlmostEqual(body.green(), 130, delta=3)
        self.assertAlmostEqual(body.blue(), 210, delta=3)

    def test_generic_device_headphones_speaker_and_receiver_are_distinct(self):
        images = [self._render(kind) for kind in ("device", "headphones", "speaker", "airplay")]
        self.assertEqual(len({bytes(image.constBits()) for image in images}), len(images))
        unknown = self._render("unknown")
        self.assertEqual(bytes(unknown.constBits()), bytes(images[0].constBits()))
        headset = self._render("headset")
        self.assertEqual(bytes(headset.constBits()), bytes(images[1].constBits()))
        with patch("local.audio_output_icons._svg_renderer", side_effect=AssertionError("unidentified model asset")):
            generic = self._render_status(("airplay", "headphones", "unknown"))
        self.assertGreater(sum(generic.pixelColor(x, y).alpha() > 0
                               for y in range(80) for x in range(80)), 200)

    def test_status_repaints_reuse_bounded_composition_and_tint_caches(self):
        first = self._render_status(("homepod", "homepod-mini"))
        hits = _tinted_status.cache_info().hits
        with patch("local.audio_output_icons._status_mask", side_effect=AssertionError("uncached composition")):
            repeated = self._render_status(("homepod", "homepod-mini"))
        self.assertEqual(bytes(first.constBits()), bytes(repeated.constBits()))
        self.assertGreater(_tinted_status.cache_info().hits, hits)
        self.assertEqual(_status_mask.cache_info().maxsize, 128)
        self.assertEqual(_tinted_status.cache_info().maxsize, 256)

    def test_status_preserves_painter_state_and_invalid_rect_is_a_no_op(self):
        image = QImage(80, 80, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            painter.setOpacity(.4)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            before = bytes(image.constBits())
            paint_audio_output_status(painter, QRectF(), ("homepod",), QColor(0, 0, 0))
            self.assertEqual(bytes(image.constBits()), before)
            paint_audio_output_status(painter, QRectF(image.rect()), ("homepod",), QColor(0, 0, 0))
            self.assertAlmostEqual(painter.opacity(), .4)
            self.assertEqual(painter.compositionMode(), QPainter.CompositionMode.CompositionMode_SourceOver)
        self.assertLessEqual(max(image.pixelColor(x, y).alpha() for y in range(80) for x in range(80)), 87)

    def test_status_counts_stay_readable_and_inside_circle_on_high_dpi_devices(self):
        image = QImage(160, 160, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(2)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            paint_audio_output_status(painter, QRectF(0, 0, 80, 80), ("homepod-mini",) * 8,
                                      QColor(40, 130, 210, 128))
        points = [(x, y) for y in range(160) for x in range(160)
                  if image.pixelColor(x, y).alpha() > 0]
        self.assertGreater(sum(y >= 104 for _, y in points), 150)
        self.assertTrue(all((x - 79.5) ** 2 + (y - 79.5) ** 2 < 76 ** 2 for x, y in points))
        self.assertLessEqual(max(image.pixelColor(x, y).alpha() for x, y in points), 128)

    def test_status_shrinks_large_total_count_to_fit_without_model_name_guessing(self):
        image = self._render_status(("unrecognized HomePod headphones",) * 1000)
        generic = self._render_status(("device",) * 1000)
        self.assertEqual(bytes(image.constBits()), bytes(generic.constBits()))
        points = [(x, y) for y in range(52, 75) for x in range(80)
                  if image.pixelColor(x, y).alpha() > 0]
        self.assertGreater(len(points), 80)
        self.assertGreaterEqual(min(x for x, _ in points), 15)
        self.assertLessEqual(max(x for x, _ in points), 64)


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
