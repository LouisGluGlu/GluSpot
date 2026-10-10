from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import OnlineSong
from api.providers import PlaybackSourceProvider
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_playback import Player, Tasks


class OwnedModeProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.player = OwnedMusicPlayer(SimpleNamespace(stream_url=lambda *_: None),
            LocalLyricsStore(root=Path(self.folder.name)), player=Player(), tasks=Tasks(),
            artwork_tasks=Tasks(), prefetch_tasks=Tasks())
        self.values = {"v2_owned_playback_enabled": True, "v2_system_capture_enabled": True}
        self.saved = []
        self.system = SimpleNamespace(suspend_live_audio=lambda: None, stop_audio_cast=lambda: None,
            settings_setter=lambda key, value: self.saved.append((key, value)))
        self.provider = PlaybackSourceProvider(self.system, self.player,
            lambda key, default=None: self.values.get(key, default))
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.player.close)
        self.addCleanup(self.provider._owned_audio_bridge.close)

    def test_active_owned_modes_reach_real_player_and_save_only_confirmed_values(self):
        self.player.play_queue((OnlineSong("netease", "one", "One"),))
        for mode in ("shuffle", "repeat_one", "sequential"):
            self.assertTrue(self.provider.set_owned_playback_mode(mode))
            self.assertEqual(self.player.playback_mode, mode)
            self.assertEqual(self.provider.get_owned_playback_mode(), mode)
        self.assertEqual(self.saved, [("v2_owned_playback_mode", mode)
            for mode in ("shuffle", "repeat_one", "sequential")])
        self.assertFalse(self.provider.set_owned_playback_mode("invalid"))
        self.assertEqual(len(self.saved), 3)

    def test_idle_disabled_and_system_sources_cannot_advertise_or_change_owned_mode(self):
        self.assertIsNone(self.provider.get_owned_playback_mode())
        self.assertFalse(self.provider.set_owned_playback_mode("shuffle"))
        self.player.play_queue((OnlineSong("netease", "one", "One"),))
        self.values["v2_owned_playback_enabled"] = False
        self.assertIsNone(self.provider.get_owned_playback_mode())
        self.assertFalse(self.provider.set_owned_playback_mode("shuffle"))
        self.assertEqual(self.saved, [])
        self.assertEqual(self.player.playback_mode, "sequential")

    def test_closed_player_rejects_mode_without_persisting_a_false_success(self):
        self.player.play_queue((OnlineSong("netease", "one", "One"),))
        self.player._closed = True
        self.assertFalse(self.provider.set_owned_playback_mode("repeat_one"))
        self.assertEqual(self.saved, [])


if __name__ == "__main__":
    unittest.main()
