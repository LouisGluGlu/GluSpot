from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from local import settings_store


class IslandStyleMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_retired_choices_migrate_and_persist_without_losing_other_settings(self):
        for background, expected in (("duotone_full", "sea_level"), ("pure_black", "system"), ("pure_white", "system")):
            with self.subTest(background=background), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "settings.json"
                path.write_text(json.dumps({
                    "v2_music_ambience_style": background,
                    "v2_music_background_follow_previous_style": "duotone_full",
                    "v2_lyric_flow_style": "ember_fade",
                    "v2_album_island_secondary_style": "shrink_dim",
                    "v2_music_ambience_values": {background: {"ambient_intensity": 0.31}},
                    "v2_lyric_font_size": 21,
                    "v2_music_app_profiles": {"netease": {"sync_enabled": True}},
                    "custom_future_setting": {"keep": True},
                }), encoding="utf-8")
                with mock.patch.object(settings_store, "APP_DATA_DIR", directory), mock.patch.object(settings_store, "SETTINGS_FILE", str(path)):
                    manager = settings_store.SettingsManager()
                    self.assertEqual(manager.get("v2_music_ambience_style"), expected)
                    self.assertEqual(manager.get("v2_music_background_follow_previous_style"), "sea_level")
                    self.assertEqual(manager.get("v2_lyric_flow_style"), "wave_stream")
                    self.assertEqual(manager.get("v2_album_island_secondary_style"), "perspective_dim")
                    self.assertEqual(manager.get("v2_music_ambience_values")[expected]["ambient_intensity"], 0.31)
                    self.assertEqual(manager.get("v2_lyric_font_size"), 21)
                    self.assertEqual(manager.get("v2_music_app_profiles"), {"netease": {"sync_enabled": True}})
                    manager.save_now()
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(saved["custom_future_setting"], {"keep": True})
                    self.assertEqual(saved["v2_music_ambience_style"], expected)
                    self.assertEqual(set(saved["v2_music_ambience_values"]), {"sea_level", "solid_album", "system"})
                    manager.deleteLater()

    def test_selected_legacy_values_win_but_canonical_values_take_precedence(self):
        raw = {"pure_white": {"ambient_intensity": 0.1}, "pure_black": {"ambient_intensity": 0.7}}
        values = settings_store.normalize_music_ambience_values(raw, "pure_white")
        self.assertEqual(values["system"]["ambient_intensity"], 0.1)
        raw["system"] = {"ambient_intensity": 0.42}
        values = settings_store.normalize_music_ambience_values(raw, "pure_white")
        self.assertEqual(values["system"]["ambient_intensity"], 0.42)
        values["solid_album"]["ambient_intensity"] = 0.99
        self.assertEqual(settings_store.DEFAULT_MUSIC_AMBIENCE_VALUES["solid_album"]["ambient_intensity"], 0.0)

    def test_late_setting_updates_cannot_reactivate_removed_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(settings_store, "APP_DATA_DIR", directory), mock.patch.object(settings_store, "SETTINGS_FILE", str(Path(directory) / "settings.json")), mock.patch.object(settings_store, "legacy_app_data_dirs", return_value=[]):
                manager = settings_store.SettingsManager()
                manager.set("v2_music_ambience_style", "pure_white")
                manager.set("v2_lyric_flow_style", "ember_fade")
                manager.set("v2_album_island_secondary_style", "shrink_dim")
                self.assertEqual(manager.get("v2_music_ambience_style"), "system")
                self.assertEqual(manager.get("v2_lyric_flow_style"), "wave_stream")
                self.assertEqual(manager.get("v2_album_island_secondary_style"), "perspective_dim")
                manager.set("v2_lyric_flow_style", [])
                manager.set("v2_music_ambience_style", {"invalid": True})
                self.assertEqual(manager.get("v2_music_ambience_style"), "sea_level")
                manager.save_now()
                manager.deleteLater()


if __name__ == "__main__":
    unittest.main()
