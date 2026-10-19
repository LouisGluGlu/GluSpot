from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget

from local.island_stack_visual import IslandStackVisual
from local.music_navigation_transition import MusicNavigationTransition, capture_navigation_frame
from local.settings_store import SettingsManager


class LibraryNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _visible_library_owner():
        library = SimpleNamespace(isVisible=lambda: True, isMinimized=lambda: False)
        island = SimpleNamespace(_music_library_active=True, _music_surface_active=False,
                                 _music_library_windows=(library,), winId=Mock())
        island._navigation_window_visible = IslandStackVisual._navigation_window_visible
        island._navigation_surface_blocks_island = lambda: IslandStackVisual._navigation_surface_blocks_island(island)
        return island

    def test_library_ownership_blocks_native_showwindow_topmost_reinforcement(self):
        island = self._visible_library_owner()
        IslandStackVisual._force_super_topmost(island)
        island.winId.assert_not_called()

    def test_returning_surface_does_not_show_island_behind_owned_library(self):
        island = self._visible_library_owner()
        island._music_surface_active = True
        island._island_visible_before_music_surface = True
        island.music_fullscreen_overlay = SimpleNamespace(hide=Mock(), isVisible=lambda: False)
        island.show = Mock()
        IslandStackVisual._restore_island_from_music_surface(island)
        island.music_fullscreen_overlay.hide.assert_called_once()
        island.show.assert_not_called()
        self.assertFalse(island._music_surface_active)

    def test_navigation_cancel_restores_destination_opacity_and_releases_images(self):
        source, destination = QWidget(), QWidget()
        for window in (source, destination):
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        transition = MusicNavigationTransition()
        try:
            source.setGeometry(40, 40, 180, 140)
            destination.setGeometry(90, 90, 320, 280)
            source.show(); destination.show()
            self.app.processEvents()
            # Initial native exposure can place a new window. Pin the fixture
            # after that handoff so this remains an exact geometry assertion.
            source.setGeometry(40, 40, 180, 140)
            destination.setGeometry(90, 90, 320, 280)
            self.app.processEvents()
            self.assertEqual(source.geometry(), QRect(40, 40, 180, 140))
            self.assertEqual(destination.geometry(), QRect(90, 90, 320, 280))
            frame = capture_navigation_frame(source)
            self.assertIsNotNone(frame)
            transition.start(frame, destination)
            self.assertTrue(transition.isVisible())
            transition._advance(.5)
            self.assertEqual(transition.geometry(), QRect(65, 65, 250, 210))
            transition.cancel()
            self.assertFalse(transition.isVisible())
            self.assertEqual(destination.windowOpacity(), 1.0)
            self.assertTrue(transition._origin.isNull())
            self.assertTrue(transition._target.isNull())
        finally:
            transition.close(); source.close(); destination.close()

    def test_navigation_settings_persist_and_repair_corrupt_types(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch("local.settings_store.SETTINGS_FILE", str(path)), \
                    patch("local.settings_store.APP_DATA_DIR", directory), \
                    patch("local.settings_store.legacy_app_data_dirs", return_value=()):
                manager = SettingsManager()
                self.assertFalse(manager.get("v2_library_expanded"))
                self.assertEqual(manager.get("v2_library_last_playback_surface"), "fullscreen")
                manager.set("v2_library_expanded", True)
                manager.set("v2_library_last_playback_surface", "window")
                manager.save_now()
                restored = SettingsManager()
                self.assertTrue(restored.get("v2_library_expanded"))
                self.assertEqual(restored.get("v2_library_last_playback_surface"), "window")
                restored.set("v2_library_expanded", "false")
                restored.set("v2_library_last_playback_surface", "invalid")
                self.assertTrue(restored.get("v2_library_expanded"))
                self.assertEqual(restored.get("v2_library_last_playback_surface"), "window")
                restored.save_now()
                path.write_text(json.dumps({"v2_library_expanded": [], "v2_library_last_playback_surface": {}}), encoding="utf-8")
                repaired = SettingsManager()
                self.assertFalse(repaired.get("v2_library_expanded"))
                self.assertEqual(repaired.get("v2_library_last_playback_surface"), "fullscreen")
                repaired.save_now()
