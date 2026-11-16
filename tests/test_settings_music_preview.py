"""Settings swatches retain the live track and share production painters."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from local import settings_store
from local.music_background_renderer import NativeMusicBackgroundRenderer, SolidMusicBackgroundRenderer
from local.settings_window import MusicAmbiencePreview, SettingsWindow
from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager


class SettingsMusicPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def cover(color):
        image = QImage(40, 40, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(color))
        return image

    def render(self, preview):
        image = QImage(preview.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        preview.render(image)
        return image

    def test_saved_gradient_migrates_on_read_and_is_written_as_capsule(self):
        for raw, expected in (("fade", "capsule"), (" FADE ", "capsule"), ("future", "transparent"), (None, "transparent")):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                path = Path(directory, "settings.json")
                path.write_text(json.dumps({"v2_finder_bar_style": raw}), encoding="utf-8")
                with patch.object(settings_store, "APP_DATA_DIR", directory), patch.object(settings_store, "SETTINGS_FILE", str(path)):
                    manager = settings_store.SettingsManager()
                    self.assertEqual(manager.get("v2_finder_bar_style"), expected)
                    manager.save_now()
                    self.assertEqual(json.loads(path.read_text())["v2_finder_bar_style"], expected)

    def test_production_sea_paints_once_until_track_or_palette_changes(self):
        island = FakeIslandWidget()
        island.album_art = self.cover("#c5413b")
        island.track_title = "First track"
        island.artist = "First artist"
        preview = MusicAmbiencePreview(island=island)
        preview.resize(320, 58)
        preview.set_preview_state("sea_level", album_color="#c5413b", secondary_color="#ef8a42")
        try:
            paint = NativeMusicBackgroundRenderer.paint_sea_level_ambience
            with patch.object(NativeMusicBackgroundRenderer, "paint_sea_level_ambience", autospec=True, side_effect=paint) as shared:
                first = self.render(preview)
                self.render(preview)
                preview.set_preview_state("sea_level", album_color="#c5413b", secondary_color="#ef8a42")
                self.render(preview)
                self.assertEqual(shared.call_count, 1)
                island.album_art = self.cover("#2a9c75")
                island.track_title = "Second track"
                preview.set_preview_state("sea_level", album_color="#2a9c75", secondary_color="#324fa5")
                second = self.render(preview)
                self.assertEqual(shared.call_count, 2)
                self.assertNotEqual(first, second)
                self.assertEqual(preview._title, "Second track")
                # The actual decoded cover occupies the thumbnail tile.
                self.assertEqual(second.pixelColor(32, 29), QColor("#2a9c75"))
        finally:
            preview.close()
            island.close()

    def test_album_swatch_uses_shared_solid_painter_and_live_palette(self):
        island = FakeIslandWidget()
        island.album_art = self.cover("#c5413b")
        island._solid_album_background_color = lambda: QColor("#593027")
        preview = MusicAmbiencePreview(island=island)
        preview.resize(320, 58)
        preview.set_preview_state("solid_album")
        paint = SolidMusicBackgroundRenderer.paint_solid_album
        try:
            with patch.object(SolidMusicBackgroundRenderer, "paint_solid_album", autospec=True, side_effect=paint) as shared:
                image = self.render(preview)
                shared.assert_called_once()
                self.assertEqual(image.pixelColor(270, 40), QColor("#593027"))
                self.assertEqual(image.pixelColor(32, 29), QColor("#c5413b"))
        finally:
            preview.close()
            island.close()

    def test_prepared_palette_completion_invalidates_without_track_change(self):
        island = FakeIslandWidget()
        island.album_art = self.cover("#c5413b")
        island._solid_album_palette_cache = SimpleNamespace(
            primary=QColor("#593027"), secondary=QColor("#744b27"), characteristic=QColor("#c5413b"))
        island._solid_album_background_color = lambda: island._solid_album_palette_cache.primary
        preview = MusicAmbiencePreview(island=island)
        preview.resize(320, 58)
        try:
            preview.set_preview_state("solid_album")
            before = self.render(preview)
            island._solid_album_palette_cache.primary = QColor("#234d38")
            preview.set_preview_state("solid_album")
            after = self.render(preview)
            self.assertNotEqual(before, after)
            self.assertEqual(after.pixelColor(270, 40), QColor("#234d38"))
        finally:
            preview.close()
            island.close()

    def test_visible_poll_only_invalidates_changed_track_and_stops_on_hide(self):
        island = FakeIslandWidget()
        island.album_art = self.cover("#c5413b")
        window = SettingsWindow(island, FakeSettingsManager())
        try:
            window.show()
            self.app.processEvents()
            self.assertTrue(window._preview_poll.isActive())
            window._update_previews()
            before = window.music_preview._state_key
            window._update_previews()
            self.assertEqual(window.music_preview._state_key, before)
            island.track_title = "New title"
            island.album_art = self.cover("#2a9c75")
            window._preview_poll.timeout.emit()
            self.assertNotEqual(window.music_preview._state_key, before)
            self.assertEqual(window.music_preview._cover.cacheKey(), island.album_art.cacheKey())
            window.hide()
            self.assertFalse(window._preview_poll.isActive())
        finally:
            window.close()
            island.close()
