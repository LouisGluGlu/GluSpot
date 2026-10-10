from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from local.lyric_unit_timing import TimedHighlightGroup
from local.music_lyric_motion import GLYPH_FLOAT_LIFT, SparseGlyphFrame
from local.music_surface_window import MusicSurfaceWindow


def _reference_sparse_run(window, painter, origin, line_height, text, font, progress, unread, states):
    """Per-glyph raster path used before resting-run composition."""
    image, _boundaries = window._glyph_run(text, font, "character")
    units = window._isolated_glyph_units(text, font, "character")
    lift_scale = max(0.5, max(1, font.pixelSize()) / window.LYRIC_PIXEL_SIZE)
    lift_height = window._reading_lift_height()
    top = origin.y() + (line_height - image.height()) / 2.0 + lift_height * lift_scale
    base_opacity = painter.opacity()
    front, feather = window._reading_front(progress, image.width(), font, text)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    baseline = max(5.0, max(1, font.pixelSize()) * 0.12) + QFontMetricsF(font).ascent()
    for (source, left), state in zip(units, states):
        unit_front = front
        if state.highlighted and state.read_fraction > 0.0:
            unit_front = max(
                unit_front,
                left + source.width() / max(1.0, source.devicePixelRatio())
                * state.read_fraction + feather,
            )
        y = top - state.lift * (lift_height / GLYPH_FLOAT_LIFT) * lift_scale
        subpixel = lift_height > 0.0 and 0.0 < state.lift < GLYPH_FLOAT_LIFT
        painter.save()
        painter.translate(QPointF(origin.x() + left + source.width() * 0.5, y + baseline))
        painter.scale(state.scale, state.scale)
        local = QPointF(-source.width() * 0.5, -baseline)
        if state.glow > 0.001 and (not state.highlighted or state.read_fraction >= 1.0 - 1e-9):
            halo, padding = window._sparse_glyph_halo_cache[source.cacheKey()]
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
            painter.setOpacity(base_opacity * state.glow)
            halo_origin = local - QPointF(padding, padding)
            if lift_height > 0.0:
                window._draw_subpixel_lyric_image(painter, halo_origin, halo)
            else:
                painter.drawImage(halo_origin, halo)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        window._draw_continuous_read_unit(
            painter, source, local, left, unit_front, feather, unread, base_opacity,
            subpixel_lift=subpixel,
            stable_subpixel_x=lift_height > 0.0,
            trailing_bleed=max(6.0, max(1, font.pixelSize()) * 0.20),
        )
        painter.restore()


class MusicSparseStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.window = MusicSurfaceWindow(self.owner)
        self.font = self.window._surface_font(52, QFont.Weight.Bold)
        self.unread = QColor(168, 112, 74)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _render(self, text, progress, position, dpr, *, reference=False, origin=QPointF(24.0, 18.0), opacity=1.0):
        image = QImage(round(1700 * dpr), round(180 * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor(57, 34, 18))
        groups = (TimedHighlightGroup(0, 5, 0.0, 5.0, (0.0,) * 5, (5.0,) * 5),)
        for source, _left in self.window._isolated_glyph_units(text, self.font, "character"):
            self.window._sparse_glyph_halo(source)
        states = self.window._real_sparse_glyph_states(text, self.font, 0, groups, progress, position)
        with QPainter(image) as painter:
            painter.setOpacity(opacity)
            if reference:
                _reference_sparse_run(
                    self.window, painter, origin, 100.0, text, self.font, progress, self.unread, states,
                )
            else:
                self.window._paint_sparse_glyph_run(
                    painter, origin, 100.0, text, self.font, progress, self.unread, glyph_states=states,
                )
        return image

    def test_resting_runs_preserve_pixels_across_wave_stages_and_device_scales(self):
        text = "Shine through a quiet moment while the music holds each word"
        for dpr in (1.0, 2.0):
            for stage in (0.0, 0.3, 1.4, 2.4, 3.3, 4.2, 5.0, 8.0):
                for fractional in (False, True):
                    with self.subTest(dpr=dpr, stage=stage, fractional=fractional):
                        progress = min(1.0, stage / 11.0)
                        origin = QPointF(24.29, 18.37) if fractional else QPointF(24.0, 18.0)
                        expected = self._render(text, progress, stage, dpr, reference=True, origin=origin)
                        actual = self._render(text, progress, stage, dpr, origin=origin)
                        difference = np.abs(
                            np.frombuffer(actual.constBits(), dtype=np.uint8).astype(np.int16)
                            - np.frombuffer(expected.constBits(), dtype=np.uint8).astype(np.int16)
                        )
                        self.assertLessEqual(int(difference.max()), 2)
                        self.assertLess(float(difference.mean()), 0.01)
        self.assertTrue(self.window._sparse_static_run_cache)

    def test_resting_run_cache_is_reused_and_reading_edge_stays_individual(self):
        text = "Shine through a quiet moment while the music holds each word"
        self._render(text, 0.04, 2.0, 2.0)
        cache_keys = tuple(self.window._sparse_static_run_cache)
        with patch.object(
            self.window, "_tinted_glyph_image", wraps=self.window._tinted_glyph_image,
        ) as tint:
            self._render(text, 0.04, 2.0, 2.0)
        self.assertEqual(tuple(self.window._sparse_static_run_cache), cache_keys)
        self.assertLess(tint.call_count, 10)

    def test_fading_text_keeps_original_per_glyph_composition(self):
        text = "Shine through a quiet moment"
        expected = self._render(text, 0.08, 2.4, 2.0, reference=True, opacity=0.6)
        actual = self._render(text, 0.08, 2.4, 2.0, opacity=0.6)
        self.assertEqual(bytes(actual.constBits()), bytes(expected.constBits()))
        self.assertFalse(self.window._sparse_static_run_cache)

    def test_bidi_resting_glyphs_keep_reference_ink(self):
        for text in (
            "Shine مرحبا بالعالم ثم نعود إلى الحب",
            "Shine حب love حب حب حب",
            "Shine 如梦似幻山河岁月 倏忽之间仍望着你",
        ):
            for dpr in (1.0, 2.0):
                with self.subTest(text=text, dpr=dpr):
                    expected = self._render(text, 0.05, 2.4, dpr, reference=True)
                    actual = self._render(text, 0.05, 2.4, dpr)
                    difference = np.abs(
                        np.frombuffer(actual.constBits(), dtype=np.uint8).astype(np.int16)
                        - np.frombuffer(expected.constBits(), dtype=np.uint8).astype(np.int16)
                    )
                    self.assertLessEqual(int(difference.max()), 2)
                    self.assertLess(float(difference.mean()), 0.01)

    def test_static_cache_stays_within_memory_limit_during_spring_phases(self):
        text = "Shine through a quiet moment while the music holds each word"
        for tick in range(96):
            self._render(text, 0.05, 2.4, 2.0, origin=QPointF(24.0, 18.0 + tick / 37.0))
        self.assertLessEqual(len(self.window._sparse_static_run_cache), 64)
        self.assertLessEqual(self.window._sparse_static_run_cache_bytes, 16 * 1024 * 1024)


    def test_sparse_lift_retains_horizontal_ink_position_before_and_after_motion(self):
        text = "i"
        units = self.window._isolated_glyph_units(text, self.font, "character")
        for dpr in (1.0, 2.0):
            centers = []
            for lift in (0.0, GLYPH_FLOAT_LIFT * 0.5, GLYPH_FLOAT_LIFT):
                image = QImage(round(180 * dpr), round(160 * dpr), QImage.Format.Format_ARGB32_Premultiplied)
                image.setDevicePixelRatio(dpr)
                image.fill(Qt.GlobalColor.transparent)
                states = tuple(SparseGlyphFrame(scale=1.0, lift=lift, read_fraction=0.0, glow=0.0) for _unit in units)
                with QPainter(image) as painter, patch.object(
                    self.window, "_reading_front", return_value=(0.0, 0.0),
                ):
                    self.window._paint_sparse_glyph_run(
                        painter, QPointF(10.35, 14.0), 100.0, text, self.font,
                        0.0, QColor(255, 255, 255), glyph_states=states,
                    )
                pixels = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.width(), 4)
                weights = pixels[:, :, 3].sum(axis=0).astype(float)
                centers.append(float(np.dot(np.arange(image.width()), weights) / weights.sum()))
            self.assertLess(max(centers) - min(centers), 0.12 * dpr)


if __name__ == "__main__":
    unittest.main()
