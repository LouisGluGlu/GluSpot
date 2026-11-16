"""The playing surface keeps its album animation independently of page defaults."""
from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.models import ColorToken, IslandVisualData, TrackData
from local.island_stack_visual import IslandStackVisual
from local.music_library_window import MusicLibraryWindow
from local.music_surface_window import MusicSurfaceWindow
from local.playback_entry_window import PlaybackEntryWindow


class MusicSurfaceDynamicDefaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_surface(self):
        owner = IslandStackVisual()
        owner.timer.stop()
        surface = MusicSurfaceWindow(owner)
        owner.music_fullscreen_overlay = surface
        surface.resize(960, 540)

        def dispose():
            surface.shutdown()
            owner.close()
            surface.deleteLater()
            owner.deleteLater()
            self.app.processEvents()

        self.addCleanup(dispose)
        return owner, surface

    @staticmethod
    def cover_bytes():
        image = QImage(320, 320, QImage.Format.Format_RGB32)
        image.fill(QColor("#129bb7"))
        with QPainter(image) as painter:
            painter.fillRect(0, 0, 150, 320, QColor("#d2552e"))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(data)

    def test_unset_main_uses_ready_artwork_palette_and_keeps_moving(self):
        with patch.dict(os.environ):
            os.environ.pop("GLUSPOT_BACKDROP_VARIANT", None)
            owner, surface = self.make_surface()
            self.assertEqual(surface._album_flow_background.backdrop_variant, "classic")
            track = TrackData(
                "Album animation", "Artist", ("A lyric",),
                ColorToken(48, 20, 72), ColorToken(16, 12, 24), (),
                album_art_bytes=self.cover_bytes(), track_id="t229-dynamic",
                album_art_source="original", album_art_lookup_state="complete",
            )
            owner._apply_visual_data(IslandVisualData(track, (), ()))
            expected = (owner.album_art.cacheKey(), owner.album_color.rgba(), owner.secondary_color.rgba())
            deadline = time.monotonic() + 3.
            while surface._art_palette_cache_key != expected and time.monotonic() < deadline:
                surface._art_palette()
                self.app.processEvents()
                time.sleep(.005)
            self.assertEqual(surface._art_palette_cache_key, expected)
            palette = surface._art_palette()
            # The supplied provider fallback is purple; the decoded cover is
            # orange/cyan and must be the source of the background pigments.
            self.assertNotEqual(palette.primary, owner.album_color)
            self.assertGreater(len(palette.ambient_colors), 1)

            image = QImage(960, 540, QImage.Format.Format_RGB32)

            def paint(now):
                with QPainter(image) as painter:
                    surface._paint_background(
                        painter, QRectF(0, 0, 960, 540), palette.primary, palette.secondary,
                        now, ambient_colors=palette.ambient_colors,
                        ambient_weights=palette.ambient_weights,
                    )
                return QImage(surface._background_cache)

            first = paint(0.)
            for step in range(1, 91):
                last = paint(step * .1)
            self.assertNotEqual(first, last)
            self.assertGreater(surface._album_flow_background._geometry_seconds, 0.)

    def test_unset_library_and_login_retain_their_frosted_default(self):
        with patch.dict(os.environ):
            os.environ.pop("GLUSPOT_BACKDROP_VARIANT", None)
            library = MusicLibraryWindow()
            login = PlaybackEntryWindow()
            try:
                self.assertEqual(library.backdrop_variant, "frosted")
                self.assertEqual(login.backdrop_variant, "frosted")
            finally:
                library.close()
                login.close()
                library.deleteLater()
                login.deleteLater()
                self.app.processEvents()

    def test_explicit_main_background_variants_are_preserved(self):
        for variant in ("classic", "flow", "frosted", "noir"):
            with self.subTest(variant=variant), patch.dict(os.environ, {"GLUSPOT_BACKDROP_VARIANT": variant}):
                _owner, surface = self.make_surface()
                self.assertEqual(surface._album_flow_background.backdrop_variant, variant)


if __name__ == "__main__":
    unittest.main()
