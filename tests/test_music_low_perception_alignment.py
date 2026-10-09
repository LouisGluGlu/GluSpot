from __future__ import annotations

import math
import unittest
from unittest.mock import patch

import numpy as np

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.island_stack_visual import IslandStackVisual
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner


class LowPerceptionAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        IslandStackVisual.load_embedded_lyric_fonts()

    def setUp(self):
        self.owner = _TransitionOwner()
        self.window = MusicSurfaceWindow(self.owner)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    @staticmethod
    def _image(dpr: float) -> QImage:
        image = QImage(
            math.ceil(480 * dpr), math.ceil(280 * dpr),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        return image

    @staticmethod
    def _alpha(image: QImage):
        return np.frombuffer(image.constBits(), dtype=np.uint8).reshape(
            image.height(), image.bytesPerLine(),
        )[:, :image.width() * 4].reshape(image.height(), image.width(), 4)[:, :, 3]

    @classmethod
    def _horizontal_ink(cls, image: QImage):
        return cls._alpha(image).sum(axis=0, dtype=np.float64)

    def test_wipe_spring_and_rest_keep_identical_glyph_positions(self):
        # Native text and a bitmap have different fractional-origin rounding.
        # Render every frame across both the half-second wipe and the spring's
        # rest threshold, including the fractional Windows display scales.
        cases = (
            ("Shine softly", 380.0, False),
            ("春风吹过山坡 Hello world", 210.0, False),
            ("لا أحبك أبدا", 380.0, True),
        )
        for dpr in (1.0, 1.25, 1.5, 2.0):
            for text, width, align_right in cases:
                with self.subTest(dpr=dpr, text=text), patch.object(
                    self.window, "devicePixelRatioF", return_value=dpr,
                ):
                    font = self.window._surface_font(38, QFont.Weight.Bold)
                    layout, height = self.window._wrapped_lyric_layout(
                        text, font, width, align_right=align_right,
                    )
                    translation_font = self.window._surface_font(18, QFont.Weight.Normal)
                    translation, translation_height = self.window._wrapped_lyric_layout(
                        "A translated companion line", translation_font, width,
                        align_right=align_right,
                    )
                    for frame in range(91):
                        elapsed = frame / 60.0
                        spring = 12.0 * math.exp(-10.0 * elapsed)
                        primary_offsets = (spring,) * layout.lineCount()
                        secondary_offsets = (spring * 1.15,) * translation.lineCount()
                        origin = QPointF(40.6, 20.35)
                        secondary_origin = QPointF(40.6, 28.35 + height)
                        layers = (
                            (layout, origin, primary_offsets),
                            (translation, secondary_origin, secondary_offsets),
                        )
                        actual, expected = self._image(dpr), self._image(dpr)
                        progress = self.window._low_perception_block_progress(elapsed, 0.0)
                        with QPainter(actual) as painter:
                            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
                            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                            if progress < 1.0:
                                self.window._draw_low_perception_block_wipe(
                                    painter, layers, progress, QColor(75, 45, 95),
                                )
                            else:
                                for (layer, at, offsets), layer_text, layer_font, layer_height in (
                                    (layers[0], text, font, height),
                                    (layers[1], "A translated companion line", translation_font, translation_height),
                                ):
                                    self.window._draw_sprung_cached_wrapped_layout(
                                        painter, layer, at, layer_text, layer_font,
                                        width, layer_height, QColor("white"), offsets,
                                    )
                        with QPainter(expected) as painter:
                            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
                            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                            self.window._draw_low_perception_block_wipe(
                                painter, layers, 1.0, QColor("white"),
                            )
                        actual_ink = self._horizontal_ink(actual)
                        expected_ink = self._horizontal_ink(expected)
                        self.assertGreater(actual_ink.sum(), 0)
                        columns = np.arange(actual.width())
                        actual_center = (actual_ink * columns).sum() / actual_ink.sum()
                        expected_center = (expected_ink * columns).sum() / expected_ink.sum()
                        self.assertLess(abs(actual_center - expected_center), 0.03,
                                        (dpr, text, frame, spring))
                        # Gradient and solid pens can round a tiny amount of
                        # edge alpha differently. The horizontal ink profile
                        # and center must still agree well below one pixel.
                        profile_error = np.abs(
                            actual_ink / actual_ink.sum() - expected_ink / expected_ink.sum(),
                        ).sum()
                        self.assertLess(profile_error, 0.05, (dpr, text, frame))

    def test_first_resting_frame_needs_no_cold_raster_or_shaping(self):
        font = self.window._surface_font(38, QFont.Weight.Bold)
        text = "A pre-shaped resting lyric"
        layout, height = self.window._wrapped_lyric_layout(text, font, 300.0)
        with patch.object(self.window, "_lyric_raster", side_effect=AssertionError("cold raster")), \
                patch.object(self.window, "_wrapped_lyric_layout", side_effect=AssertionError("cold shaping")):
            for offset in (12.0, 0.02, 0.009, 0.0):
                image = self._image(1.0)
                with QPainter(image) as painter:
                    self.window._draw_sprung_cached_wrapped_layout(
                        painter, layout, QPointF(40.6, 20.35), text,
                        font, 300.0, height, QColor("white"),
                        (offset,) * layout.lineCount(),
                    )
                self.assertGreater(self._alpha(image).max(), 0)


if __name__ == "__main__":
    unittest.main()
