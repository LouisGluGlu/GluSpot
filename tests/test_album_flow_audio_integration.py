"""Exercise the album-flow page and output chooser together without devices."""
from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from local.music_surface_window import MusicSurfaceWindow
from tests.test_audio_output_panel import _RouteProvider
from tests.test_music_fullscreen import _Owner


class AlbumFlowAudioIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.provider = _RouteProvider()
        self.owner.data_provider = self.provider
        self.owner.music_flow_intensity = 1.0
        self.owner.music_surface_reading_strategy = "gold"
        self.owner.media_track_id = "album-flow-output-integration"
        self.owner.track_title = "Album colours"
        self.owner.artist = "GluSpot test fixture"
        self.owner.media_position_seconds = 1.0
        self.owner.media_is_playing = False
        self.owner.timed_lyrics = [(0.0, "A readable lyric"), (8.0, "Next sentence")]
        self.owner.album_art = QImage(120, 120, QImage.Format.Format_RGB32)
        with QPainter(self.owner.album_art) as painter:
            for index, color in enumerate(("#cd283e", "#edbe52", "#3154b7", "#65a878")):
                painter.fillRect(index * 30, 0, 30, 120, QColor(color))
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window.show()
            self.window._timer.stop()
            self.app.processEvents()
        self.addCleanup(self._dispose)

    def _dispose(self):
        self.window.shutdown()
        self.window.deleteLater()
        self.owner.close()
        self.owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _render_page(self, now):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window.render(image)
        return image

    def _background_state(self):
        return (self.window._background_cache.cacheKey(),
                self.window._background_cache_key, self.window._background_cache_at)

    def _open_with_pointer(self, fullscreen=False, now=100.0):
        if self.window._audio_output_panel is not None:
            self.window._audio_output_panel.dismiss()
        self.window._fullscreen = fullscreen
        self.window._reset_control_idle(now=now)
        self._render_page(now)
        button = self.window._button_rects["audio_output"]
        with patch("local.music_surface_window.time.monotonic", return_value=now + 0.5):
            QTest.mouseClick(self.window, Qt.MouseButton.LeftButton, pos=button.center().toPoint())
        panel = self.window._audio_output_panel
        self.assertIsNotNone(panel)
        self.assertTrue(panel.isVisible())
        self.assertFalse(self.window._moving)
        panel._animation_timer.stop()
        panel._poll_timer.stop()
        panel._appearance_elapsed = 1.0
        panel._opacity.setOpacity(1.0)
        panel.move(panel._anchor)
        return panel

    def test_chooser_capture_holds_displayed_flow_until_a_complete_page_paint(self):
        for fullscreen in (False, True):
            with self.subTest(fullscreen=fullscreen):
                self.window._fullscreen = fullscreen
                self.window._reset_control_idle(now=100.0)
                self._render_page(100.0)
                before = self._background_state()
                displayed = QImage(self.window._background_cache)
                samples = self.window._background_sample_colors(self.window.rect())
                panel = self._open_with_pointer(fullscreen)
                self.assertEqual(self._background_state(), before)
                # Both opening and polling crop the page after another ambient
                # frame is due. A new patch must not split the displayed field.
                with patch("local.audio_output_panel.time.monotonic", return_value=102.0):
                    panel._capture_material(force=True)
                    panel._capture_material()
                self.assertEqual(self._background_state(), before)
                self.assertEqual(self.window._background_cache, displayed)
                self.assertEqual(self.window._background_sample_colors(self.window.rect()), samples)
                panel.dismiss()
                self._render_page(104.0)
                self.assertNotEqual(self._background_state()[0], before[0])
                self.assertNotEqual(self.window._background_cache, displayed)

    def test_material_excludes_visible_child_and_keeps_album_colour_and_opacity(self):
        panel = self._open_with_pointer()
        marker = QWidget(self.window)
        marker.setGeometry(panel.geometry())
        marker.setAutoFillBackground(True)
        palette = marker.palette()
        palette.setColor(marker.backgroundRole(), QColor("#ff00ff"))
        marker.setPalette(palette)
        before = self._background_state()
        with patch("local.audio_output_panel.time.monotonic", return_value=102.0):
            panel._capture_material(force=True)
            without_child = QImage(panel._material)
            marker.show()
            marker.lower()
            self.assertTrue(marker.isVisible())
            # Establish that this child would visibly contaminate a capture.
            child = QImage(marker.size(), QImage.Format.Format_RGB32)
            marker.render(child)
            self.assertEqual(child.pixelColor(child.width() // 2, child.height() // 2), QColor("#ff00ff"))
            panel._capture_material(force=True)
        self.assertEqual(panel._material, without_child)
        self.assertEqual(self._background_state(), before)
        material = panel._material
        self.assertFalse(material.isNull())
        pixels = [material.pixelColor(x, y)
                  for y in range(0, material.height(), 8)
                  for x in range(0, material.width(), 8)]
        self.assertTrue(all(color.alpha() == 255 for color in pixels))
        self.assertGreater(max(max(color.getRgb()[:3]) - min(color.getRgb()[:3]) for color in pixels), 3)
        self.assertGreaterEqual(MusicSurfaceWindow._contrast_ratio(panel._ink, panel._background), 4.5)
        self.assertGreaterEqual(MusicSurfaceWindow._contrast_ratio(panel._muted, panel._background), 4.5)
        marker.hide()

    def test_circular_output_control_selects_fake_route_and_escape_dismisses_chooser(self):
        for fullscreen in (False, True):
            with self.subTest(fullscreen=fullscreen):
                self.window._fullscreen = fullscreen
                self.window._reset_control_idle(now=100.0)
                closed_page = self._render_page(100.0)
                panel = self._open_with_pointer(fullscreen)
                buttons = self.window._button_rects
                self.assertEqual(set(buttons), {"audio_output", "translation",
                                                "mode" if fullscreen else "return"})
                output, translation = buttons["audio_output"], buttons["translation"]
                self.assertAlmostEqual(output.width(), output.height())
                self.assertEqual(output.size(), translation.size())
                self.assertLess(output.right(), translation.left())
                with patch.object(self.provider, "get_audio_route_snapshot",
                                  side_effect=AssertionError("provider read during paint")):
                    open_page = self._render_page(100.5)
                closed_face = closed_page.copy(output.toAlignedRect())
                open_face = open_page.copy(output.toAlignedRect())

                def blue_pixels(image):
                    return sum(color.blue() > 180 and color.blue() > color.red() + 45
                               and color.green() > 100
                               for y in range(image.height()) for x in range(image.width())
                               for color in (image.pixelColor(x, y),))

                self.assertGreater(blue_pixels(open_face), blue_pixels(closed_face) + 8)
                side_x, middle_y = open_face.width() // 5, open_face.height() // 2
                face = open_face.pixelColor(side_x, middle_y)
                self.assertEqual(face.alpha(), 255)
                self.assertGreater(face.lightness(), closed_face.pixelColor(side_x, middle_y).lightness() + 100)
                self.assertGreater(face.lightness(), open_face.pixelColor(1, 1).lightness() + 80)
                self.provider.calls.clear()
                QTest.mouseClick(panel._rows["bluetooth"], Qt.MouseButton.LeftButton)
                self.assertEqual(self.provider.calls, [("select", "bluetooth")])
                self.provider.snapshot = replace(self.provider.snapshot, active_id="bluetooth")
                panel.sync_snapshot()
                self.assertEqual(panel._rows["bluetooth"].state, "active")
                self.assertEqual(panel._rows["local"].state, "available")
                with patch.object(self.window, "return_to_island") as return_to_island:
                    QTest.keyClick(panel, Qt.Key.Key_Escape)
                return_to_island.assert_not_called()
                self.assertFalse(panel.isVisible())
                self.assertFalse(panel._animation_timer.isActive())
                self.assertFalse(panel._poll_timer.isActive())


if __name__ == "__main__":
    unittest.main()
