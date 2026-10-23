"""Retired extras cannot collect data through the active media boundary."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


from api.fake_dataset import FAKE_MESSAGES, FAKE_PLUGINS
from api.fake_provider import FakeIslandDataProvider
from api.providers import EmptyIslandDataProvider, PlaybackSourceProvider, WindowsMediaProvider, empty_visual_data
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



if __name__ == "__main__":
    unittest.main()
