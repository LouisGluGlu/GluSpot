from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from local.music_surface_window import MusicSurfaceWindow


class MusicHighlightCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.window = MusicSurfaceWindow(self.owner)
        self.font = self.window._surface_font(52, QFont.Weight.Bold)
        self.unread = QColor(155, 95, 190)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _prepare(self, text, layout):
        for index in range(layout.lineCount()):
            line = layout.lineAt(index)
            segment = self.window._utf16_slice(text, line.textStart(), line.textLength())
            for source, _left in self.window._isolated_glyph_units(segment, self.font, "character"):
                self.window._tinted_glyph_image(source, self.unread)
                self.window._sparse_glyph_halo(source)

    def test_long_highlight_can_finish_prewarm_beyond_former_64_glyph_limit(self):
        text = "Shine through a quiet moment while the music holds every word of a long and carefully timed lyric tonight"
        layout, _height = self.window._wrapped_lyric_layout(text, self.font, 760.0)
        self.window._protect_lyric_emphasis_assets(text, self.font, layout)
        self._prepare(text, layout)
        self.assertGreater(len(self.window._emphasis_protected_source_keys), 64)
        self.assertTrue(self.window._lyric_emphasis_assets_ready(
            text, self.font, 760.0, layout, "scatter_glow", self.unread,
        ))

    def test_future_prewarm_cannot_revert_ready_current_effect_under_cache_pressure(self):
        text = "Shine through a quiet moment while the music holds every word"
        layout, _height = self.window._wrapped_lyric_layout(text, self.font, 760.0)
        self.window._protect_lyric_emphasis_assets(text, self.font, layout)
        self._prepare(text, layout)
        protected = set(self.window._emphasis_protected_source_keys)
        # Make pressure explicit instead of relying on the developer machine's
        # font metrics to fill an arbitrary number of entries.
        self.window.GLYPH_TINT_CACHE_MAX_BYTES = 2048
        self.window.SPARSE_GLYPH_HALO_CACHE_MAX_BYTES = 2048
        for index in range(18):
            future = f"Upcoming lyric {index} keeps another gently illuminated word"
            future_layout, _height = self.window._wrapped_lyric_layout(future, self.font, 760.0)
            self._prepare(future, future_layout)
            self.assertTrue(self.window._lyric_emphasis_assets_ready(
                text, self.font, 760.0, layout, "scatter_glow", self.unread,
            ))
            self.assertEqual(self.window._emphasis_protected_source_keys, protected)
        self.window._protect_lyric_emphasis_assets("", self.font, None)
        self.assertLessEqual(self.window._glyph_tint_cache_bytes, 2048)
        self.assertLessEqual(self.window._sparse_glyph_halo_cache_bytes, 2048)
        self.assertFalse(self.window._emphasis_protected_source_keys)

    def test_character_run_survives_other_rows_filling_the_shape_cache(self):
        text = "Shine through a quiet moment while the music holds every word"
        layout, _height = self.window._wrapped_lyric_layout(text, self.font, 760.0)
        self.window._protect_lyric_emphasis_assets(text, self.font, layout)
        self._prepare(text, layout)
        self.assertTrue(self.window._lyric_emphasis_assets_ready(
            text, self.font, 760.0, layout, "scatter_glow", self.unread,
        ))
        for index in range(135):
            future = f"New phrase {index} shining between a different set of stars"
            self.window._isolated_glyph_units(future, self.font, "character")
        self.assertTrue(self.window._lyric_emphasis_assets_ready(
            text, self.font, 760.0, layout, "scatter_glow", self.unread,
        ))

    def test_cold_line_keeps_unread_color_and_builds_no_per_glyph_images(self):
        text = "This moment moves slowly across the lyric tonight"
        layout, height = self.window._wrapped_lyric_layout(text, self.font, 1100.0)
        output = QImage(1200, 150, QImage.Format.Format_ARGB32_Premultiplied)
        output.fill(Qt.GlobalColor.transparent)
        with QPainter(output) as painter, patch.object(
            self.window, "_glyph_run", side_effect=AssertionError("cold per-glyph raster"),
        ), patch.object(
            self.window, "_sparse_glyph_halo", side_effect=AssertionError("cold halo raster"),
        ):
            self.window._paint_cold_emphasis_reading(
                painter, QRectF(20.0, 20.0, 1100.0, height), text, self.font,
                layout, 0.05, self.unread, (0.0,),
            )
        pixels = np.frombuffer(output.constBits(), dtype=np.uint8).reshape(150, 1200, 4)
        unread_pixels = pixels[:, 550:, :]
        visible = unread_pixels[:, :, 3] > 200
        self.assertTrue(visible.any())
        colors = unread_pixels[:, :, :3][visible]
        self.assertGreater(int(colors[:, 0].mean()), int(colors[:, 1].mean()) + 50)
        self.assertLess(int(colors[:, 1].mean()), 120)
        self.assertFalse(self.window._glyph_run_cache)
        self.assertFalse(self.window._sparse_glyph_halo_cache)


if __name__ == "__main__":
    unittest.main()
