"""Retired extras cannot collect data through the active media boundary."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QApplication

from api.fake_dataset import FAKE_MESSAGES, FAKE_PLUGINS
from api.fake_provider import FakeIslandDataProvider
from api.models import TimedLyricLine, TimedLyricUnit
from api.providers import EmptyIslandDataProvider, PlaybackSourceProvider, WindowsMediaProvider, empty_visual_data
from local.island_stack_visual import IslandStackVisual
from tests.test_provider_state import FakeLyricsStore, FakeManager, FakeSession


class RetiredRuntimeExtrasTests(unittest.TestCase):
    def setUp(self):
        self.data = replace(empty_visual_data("Song", "Artist", "fixture.music"),
                            messages=FAKE_MESSAGES, plugins=FAKE_PLUGINS)
        self.settings = {"v2_enabled_plugins": ["time", "weather", "agenda"],
            "v2_plugin_count": 3, "v2_message_detection_enabled": True,
            "v2_wechat_notification_enabled": True, "v2_wechat_detail_notification_enabled": True,
            "v2_qq_notification_enabled": True, "v2_dingtalk_notification_enabled": True}

    def make_provider(self):
        with patch.object(WindowsMediaProvider, "_start_loop"), \
                patch("api.providers.LocalLyricsStore", return_value=FakeLyricsStore()):
            provider = WindowsMediaProvider(fallback=SimpleNamespace(get_visual_data=lambda: self.data),
                settings_getter=lambda key, default=None: self.settings.get(key, default))
        provider._online_current_art_lookup_enabled = False
        return provider

    def assert_empty_extras(self, data):
        self.assertEqual(data.messages, ())
        self.assertEqual(data.plugins, ())

    def test_constructor_and_repeated_reads_never_create_collectors_threads_or_network(self):
        with patch("api.plugin_provider.RealPluginProvider.__init__", side_effect=AssertionError("plugin collector")), \
                patch("api.message_detector.WindowsMessageDetector.__init__", side_effect=AssertionError("message collector")), \
                patch("threading.Thread.start", side_effect=AssertionError("background thread")), \
                patch("api.plugin_provider.urlopen", side_effect=AssertionError("weather network")), \
                patch("api.message_detector.WindowsMessageDetector._collect_candidates", side_effect=AssertionError("UIA collection")):
            provider = self.make_provider()
            for _ in range(20):
                self.assert_empty_extras(provider.get_visual_data())
                self.assertEqual(provider.plugin_provider.plugins(), ())
                self.assertEqual(provider.message_detector.messages(), ())
                diagnostics = provider.diagnostics()
                self.assertFalse(diagnostics["plugins"]["enabled"])
                self.assertFalse(diagnostics["messages"]["message_detection_enabled"])
                self.assertEqual(diagnostics["messages"]["cached_messages"], 0)

    def test_empty_custom_and_fake_provider_outputs_preserve_music_but_drop_extras(self):
        provider = EmptyIslandDataProvider(self.data)
        self.assert_empty_extras(provider.get_visual_data())
        self.assertIs(provider.get_visual_data().track, self.data.track)
        self.assert_empty_extras(FakeIslandDataProvider().get_visual_data())
        # Historical isolated fixtures remain available for helper audits.
        self.assertTrue(FAKE_MESSAGES)
        self.assertTrue(FAKE_PLUGINS)

    def test_system_cache_status_and_busy_lock_paths_do_not_call_injected_collectors(self):
        provider = self.make_provider()
        forbidden = Mock(side_effect=AssertionError("retired collector call"))
        provider.plugin_provider = SimpleNamespace(plugins=forbidden)
        provider.message_detector = SimpleNamespace(messages=forbidden)
        provider._last_data, provider._last_ready_at = self.data, time.monotonic()
        provider._music_cache_lock = SimpleNamespace(acquire=lambda **_: False)
        data = provider.get_visual_data()
        self.assert_empty_extras(data)
        self.assertEqual(data.track.track_id, self.data.track.track_id)
        for status in ("starting", "ready", "missing_winsdk", "no_session"):
            provider._provider_status = status
            self.assert_empty_extras(provider._cached_or_status_visual(time.monotonic()))
        forbidden.assert_not_called()

    def test_real_media_snapshot_clears_fallback_extras_without_calling_collectors(self):
        provider = self.make_provider()
        provider._manager = FakeManager((FakeSession("fixture.music", "Actual Song", "Artist", playing=True, duration=120),))
        forbidden = Mock(side_effect=AssertionError("retired collector call"))
        provider.plugin_provider = SimpleNamespace(plugins=forbidden)
        provider.message_detector = SimpleNamespace(messages=forbidden)
        with patch("threading.Thread.start", side_effect=AssertionError("background thread")):
            data = asyncio.run(provider._read_visual_data())
        self.assert_empty_extras(data)
        self.assertEqual(data.track.title, "Actual Song")
        self.assertEqual(data.track.artist, "Artist")
        self.assertEqual(data.track.duration_seconds, 120)
        forbidden.assert_not_called()

    def test_owned_system_and_disabled_source_boundary_never_reads_auxiliary_data(self):
        forbidden = Mock(side_effect=AssertionError("retired collector call"))
        system = SimpleNamespace(get_visual_data=lambda: self.data,
            plugin_provider=SimpleNamespace(plugins=forbidden), message_detector=SimpleNamespace(messages=forbidden),
            get_audio_frame=lambda: "audio", has_live_audio=lambda: True)
        provider = PlaybackSourceProvider.__new__(PlaybackSourceProvider)
        provider.system, provider.owned = system, SimpleNamespace(track=self.data.track, visual_data=lambda: self.data)
        for owned, captured in ((True, False), (False, True), (False, False)):
            settings = {"v2_owned_playback_enabled": owned, "v2_system_capture_enabled": captured}
            provider.settings_getter = lambda key, default=None: settings.get(key, default)
            data = provider.get_visual_data()
            self.assert_empty_extras(data)
            if owned or captured:
                self.assertEqual(data.track.track_id, self.data.track.track_id)
            if captured:
                self.assertEqual(provider.get_audio_frame(), "audio")
                self.assertTrue(provider.has_live_audio())
        forbidden.assert_not_called()


class RetiredIslandRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        data = empty_visual_data("Song", "Artist", "fixture.music")
        self.data = replace(data, messages=FAKE_MESSAGES, plugins=FAKE_PLUGINS,
            track=replace(data.track, track_id="first", duration_seconds=120,
                timed_lyrics=(TimedLyricLine(0, "歌词", (TimedLyricUnit(0, 2, "歌词"),), "word"),)))
        self.provider = SimpleNamespace(get_visual_data=lambda: self.data)
        self.extras = patch.multiple("local.island_stack_visual",
            FullscreenPluginAlertOverlay=Mock(side_effect=AssertionError("plugin overlay")),
            BarrageOverlay=Mock(side_effect=AssertionError("barrage overlay")),
            IslandAudioDeviceMotion=Mock(side_effect=AssertionError("device notice motion")))
        self.extras.start()
        self.addCleanup(self.extras.stop)
        with patch("local.island_stack_visual.read_finder_material", return_value=SimpleNamespace(available=False)), \
                patch("local.island_stack_visual.QTimer.singleShot", side_effect=AssertionError("fake message scheduling")):
            self.visual = IslandStackVisual(self.provider)
        self.visual.timer.stop()
        self.addCleanup(self.visual.close)

    def assert_retired(self):
        visual = self.visual
        self.assertEqual(visual.messages, [])
        self.assertEqual(visual.message_samples, [])
        self.assertEqual(visual.plugin_samples, [])
        self.assertEqual(visual._enabled_plugin_ids(), [])
        self.assertEqual(visual._active_barrage_messages(), [])
        self.assertEqual(visual._fullscreen_plugin_alerts(), [])
        self.assertIsNone(visual.plugin_alert_overlay)
        self.assertIsNone(visual.barrage_overlay)
        self.assertIsNone(visual.fullscreen_plugin_alert_overlay)
        self.assertIsNone(visual._audio_device_pose())

    def test_live_constructor_and_refresh_drop_extras_but_keep_music_and_timed_units(self):
        self.assert_retired()
        self.assertFalse(self.visual.persistent_plugin)
        self.assertEqual(self.visual.track_title, "Song")
        self.assertEqual(self.visual.timed_lyrics, [(0, "歌词")])
        self.assertEqual(self.visual.timed_lyric_units[0], self.data.track.timed_lyrics[0].units)
        self.assertEqual(self.visual.timed_lyric_timing_kinds, ["word"])
        for index in range(5):
            self.data = replace(self.data, track=replace(self.data.track,
                track_id=f"new-{index}", title=f"New song {index}", position_seconds=index))
            self.visual._apply_visual_data(self.data)
            self.visual._ingest_provider_messages(self.data)
            self.assert_retired()
            self.assertEqual(self.visual.track_title, f"New song {index}")
            self.assertEqual(self.visual.media_track_id, f"new-{index}")
            self.assertEqual(self.visual.media_duration_seconds, 120)
            self.assertEqual(self.visual.timed_lyric_units[0], self.data.track.timed_lyrics[0].units)

    def test_legacy_settings_previews_actions_and_shortcuts_cannot_restart_extras(self):
        visual = self.visual
        visual.persistent_plugin, visual.enabled_plugin_count = True, 3
        visual.enabled_plugin_ids = {"time", "weather", "agenda"}
        visual.message_samples = [("app", "sender", "text", None, None)]
        forbidden = Mock(side_effect=AssertionError("retired action"))
        visual.settings_setter = forbidden
        visual._handle_alarm_editor_key_event = forbidden
        visual._handle_alarm_editor_key = forbidden
        for action in (visual.add_message, visual.show_barrage_preview,
                visual.show_plugin_fullscreen_alert_preview, visual._open_plugin_manager,
                visual.refresh_plugin_data):
            action()
        for action in (visual._sync_plugin_alert_overlay, visual._sync_barrage_overlay,
                visual._sync_fullscreen_plugin_alert_overlay, visual._ensure_fullscreen_plugin_barrage_messages):
            action(time.monotonic())
        visual._activate_plugin_action("timer-start")
        for key in (Qt.Key.Key_P, Qt.Key.Key_N, Qt.Key.Key_C, Qt.Key.Key_0,
                Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_T):
            visual.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
        event = QInputMethodEvent()
        event.setCommitString("2030")
        visual.inputMethodEvent(event)
        visual.on_audio_device_event(SimpleNamespace(event_serial=1), SimpleNamespace(name="Headphones"))
        self.assertEqual(visual.messages, [])
        self.assertEqual(visual.barrage_preview_messages, [])
        self.assertEqual(visual.plugin_fullscreen_alert_preview_until, 0)
        self.assertEqual(visual._enabled_plugin_ids(), [])
        self.assertIsNone(visual.plugin_alert_overlay)
        self.assertIsNone(visual.barrage_overlay)
        self.assertIsNone(visual._audio_device_motion)
        forbidden.assert_not_called()

    def test_live_tick_skips_extra_polling_and_overlay_work_on_both_music_surfaces(self):
        visual = self.visual
        forbidden = Mock(side_effect=AssertionError("retired tick work"))
        visual._prune_expired_messages = forbidden
        visual._barrage_hover_clock_plan = forbidden
        visual._sync_fullscreen_plugin_alert_overlay = forbidden
        visual._ensure_fullscreen_plugin_barrage_messages = forbidden
        visual._refresh_media_data = Mock(return_value=False)
        visual._update_scene_state = Mock()
        visual._handle_music_lyric_tick = Mock(return_value=False)
        visual._sync_music_background_to_theme = Mock(return_value=False)
        visual._handle_tick_follow_refresh = Mock()
        visual._handle_scene_suppressed_tick = Mock()
        visual._topmost_reinforcement_due = Mock(return_value=False)
        visual.scene_suppressed = True
        visual._tick()
        visual.music_fullscreen_overlay = SimpleNamespace(isVisible=lambda: True)
        visual._tick()
        visual.music_fullscreen_overlay = None
        self.assertEqual(visual._refresh_media_data.call_count, 2)
        self.assertEqual(visual._handle_music_lyric_tick.call_count, 2)
        self.assertEqual(visual.barrage_clock, 0)
        forbidden.assert_not_called()


if __name__ == "__main__":
    unittest.main()
