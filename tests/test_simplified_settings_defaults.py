from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from local import settings_store


class SimplifiedSettingsDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_old_config_and_later_visual_export_cannot_restore_retired_controls(self):
        old = {
            "v2_enabled_plugins": ["time", "weather", "agenda"],
            "v2_persistent_plugin": True,
            "v2_enable_message_detection": True,
            "v2_enable_wechat_notification": True,
            "v2_tasker_follow_taskbar_color": True,
            "v2_location_mode": "tasker",
            "v2_window_pos": [12, 300],
            "v2_settings_font_family": "Arial",
            "v2_wave_font_weight": "heavy",
            "v2_lyric_font_size": 32,
            "v2_music_reactive_background_enabled": True,
            "v2_music_reactive_background_intensity": 100,
            "v2_music_flow_intensity": 0.01,
            "v2_music_ambience_values": {"sea_level": {"ambient_intensity": .99}},
            "v2_enable_dual_line_lyrics": True,
            "v2_dual_line_font_size": 30,
            "v2_album_island_cover_count": 7,
            "v2_plugin_count": 3,
            "v2_taskbar_fusion_mode": "expanded",
            "v2_owned_playback_mode": "shuffle",
            "v2_system_capture_enabled": True,
            "v2_mydockfinder_path": "C:/MyDockFinder/Dock.exe",
            "v2_mydockfinder_listener_process_names": ["dock.exe", "dock_64.exe"],
            "v2_music_surface_reading_strategy": "low_perception",
            "v2_animation_fps": 120,
            "v2_dual_line_secondary_source": "phonetic",
            "v2_custom_icon_path": "saved-icon.png",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps(old), encoding="utf-8")
            with patch.object(settings_store, "APP_DATA_DIR", directory), patch.object(settings_store, "SETTINGS_FILE", str(path)):
                manager = settings_store.SettingsManager()
                self.assertTrue(manager._dirty)
                for key in settings_store.FIXED_DEFAULT_SETTING_KEYS:
                    with self.subTest(key=key):
                        self.assertEqual(manager.get(key), settings_store.DEFAULT_SETTINGS[key])
                        manager.set(key, old.get(key, "unexpected visual export"))
                        self.assertEqual(manager.get(key), settings_store.DEFAULT_SETTINGS[key])
                for key in set(old) - settings_store.FIXED_DEFAULT_SETTING_KEYS - settings_store._REMOVED_VISUAL_SETTING_KEYS:
                    self.assertEqual(manager.get(key), old[key])
                for key in ("v2_plugin_count", "v2_taskbar_fusion_mode"):
                    manager.set(key, 3)
                    self.assertNotIn(key, manager._settings)
                manager.save_now()
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(settings_store._REMOVED_VISUAL_SETTING_KEYS.isdisjoint(saved))
                for key in settings_store.FIXED_DEFAULT_SETTING_KEYS:
                    self.assertEqual(saved[key], settings_store.DEFAULT_SETTINGS[key])

    def test_fixed_mutable_defaults_cannot_be_modified_through_get(self):
        original = copy.deepcopy(settings_store.DEFAULT_SETTINGS)
        with tempfile.TemporaryDirectory() as directory, patch.object(settings_store, "APP_DATA_DIR", directory), patch.object(settings_store, "SETTINGS_FILE", str(Path(directory) / "settings.json")):
            manager = settings_store.SettingsManager()
            manager.get("v2_enabled_plugins").append("weather")
            manager.get("v2_music_ambience_values")["sea_level"]["ambient_intensity"] = 1
            self.assertEqual(manager.get("v2_enabled_plugins"), [])
            self.assertEqual(manager.get("v2_music_ambience_values"), original["v2_music_ambience_values"])
        self.assertEqual(settings_store.DEFAULT_SETTINGS, original)

    def test_finder_styles_and_drag_ratio_normalize_without_reopening_global_position_settings(self):
        for raw, expected in (("capsule", "capsule"), (" FADE ", "fade"), ("transparent", "transparent"),
                              ("unknown", "transparent"), (None, "transparent")):
            with self.subTest(style=raw):
                self.assertEqual(settings_store.normalize_finder_bar_style(raw), expected)
        for raw, expected in ((-3, 0.0), (2, 1.0), (.23, .23), ("0.7", .7), (True, .5),
                              (None, .5), ("invalid", .5), (float("inf"), .5), (float("nan"), .5)):
            with self.subTest(ratio=raw):
                self.assertEqual(settings_store.normalize_finder_bar_center_ratio(raw), expected)
        with tempfile.TemporaryDirectory() as directory, patch.object(settings_store, "APP_DATA_DIR", directory), patch.object(settings_store, "SETTINGS_FILE", str(Path(directory) / "settings.json")):
            manager = settings_store.SettingsManager()
            manager.set("v2_finder_bar_style", "fade")
            manager.set("v2_finder_bar_center_ratio", .8)
            manager.set("v2_location_mode", "finder")
            manager.set("v2_stack_center_ratio", .8)
            manager.save_now()
            loaded = settings_store.SettingsManager()
            self.assertEqual(loaded.get("v2_finder_bar_style"), "fade")
            self.assertEqual(loaded.get("v2_finder_bar_center_ratio"), .8)
            self.assertEqual(loaded.get("v2_location_mode"), "top")
            self.assertEqual(loaded.get("v2_stack_center_ratio"), settings_store.DEFAULT_SETTINGS["v2_stack_center_ratio"])

    def test_invalid_finder_values_migrate_while_explicit_listener_choices_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"v2_finder_bar_style": "retired", "v2_finder_bar_center_ratio": 4,
                                        "v2_mydockfinder_listener_process_names": ["dock.exe"]}), encoding="utf-8")
            with patch.object(settings_store, "APP_DATA_DIR", directory), patch.object(settings_store, "SETTINGS_FILE", str(path)):
                manager = settings_store.SettingsManager()
                self.assertTrue(manager._dirty)
                self.assertEqual(manager.get("v2_finder_bar_style"), "transparent")
                self.assertEqual(manager.get("v2_finder_bar_center_ratio"), 1.0)
                self.assertEqual(manager.get("v2_mydockfinder_listener_process_names"), ["dock.exe"])
                manager.set("v2_finder_bar_style", None)
                manager.set("v2_finder_bar_center_ratio", float("nan"))
                manager.save_now()
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["v2_finder_bar_style"], "transparent")
                self.assertEqual(saved["v2_finder_bar_center_ratio"], .5)
                self.assertEqual(saved["v2_mydockfinder_listener_process_names"], ["dock.exe"])

    def test_session_translation_reaches_lyric_parser_without_changing_next_launch_default(self):
        from api.lyrics import LocalLyricsStore
        from local.app import LocalIslandApp

        with tempfile.TemporaryDirectory() as directory, patch.object(settings_store, "APP_DATA_DIR", directory), patch.object(settings_store, "SETTINGS_FILE", str(Path(directory) / "settings.json")):
            controller = LocalIslandApp.__new__(LocalIslandApp)
            controller.settings = settings_store.SettingsManager()
            self.assertFalse(controller._runtime_setting("v2_enable_dual_line_lyrics"))
            controller.window = SimpleNamespace(enable_dual_line_lyrics=False)
            store = LocalLyricsStore(root=Path(directory) / "lyrics", generated_root=Path(directory) / "cache", settings_getter=controller._runtime_setting)
            payload = dict(raw_lrc="[00:00.00]Shine on", translation="[00:00.00]继续闪耀", source="fixture")
            single_key = store._cache_preference_key()
            single = store.parse_online_lyrics(**payload)
            self.assertEqual(single.lyrics, ("Shine on",))
            controller.window.enable_dual_line_lyrics = True
            controller.settings.set("v2_enable_dual_line_lyrics", True)
            dual_key = store._cache_preference_key()
            dual = store.parse_online_lyrics(**payload)
            self.assertNotEqual(single_key, dual_key)
            self.assertTrue(controller._runtime_setting("v2_enable_dual_line_lyrics"))
            self.assertFalse(controller.settings.get("v2_enable_dual_line_lyrics"))
            self.assertIn("继续闪耀", "\n".join(dual.lyrics))
            controller.settings.set("v2_owned_playback_mode", "shuffle")
            self.assertEqual(controller._runtime_setting("v2_owned_playback_mode"), "shuffle")
            controller.settings.save_now()
            next_launch = settings_store.SettingsManager()
            self.assertFalse(next_launch.get("v2_enable_dual_line_lyrics"))
            controller.window.enable_dual_line_lyrics = False
            self.assertEqual(store._cache_preference_key(), single_key)
            self.assertEqual(store.parse_online_lyrics(**payload).lyrics, single.lyrics)
