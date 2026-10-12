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
            dj_player=Player(), dj_audio=object(), artwork_tasks=Tasks(), prefetch_tasks=Tasks(), stream_cache=None)
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

    def test_personal_fm_capabilities_do_not_overwrite_saved_playlist_preference(self):
        songs = (OnlineSong("netease", "one", "One"),)
        self.player.play_queue(songs)
        self.assertTrue(self.provider.set_owned_playback_mode("shuffle"))
        self.player.service.personal_fm = lambda *_: songs
        self.player.play_personal_fm()
        token, action = self.player.tasks.jobs[-1]
        self.player.tasks.completed.emit(token, action(), None)
        self.assertTrue(self.provider.is_owned_personal_fm())
        self.assertEqual(self.provider.get_owned_playback_modes(), ("sequential", "repeat_one"))
        self.assertFalse(self.provider.set_owned_playback_mode("shuffle"))
        self.assertTrue(self.provider.set_owned_playback_mode("repeat_one"))
        self.assertTrue(self.provider.set_owned_playback_mode("sequential"))
        self.assertEqual(self.saved, [("v2_owned_playback_mode", "shuffle")])
        self.player.play_queue(songs)
        self.assertFalse(self.provider.is_owned_personal_fm())
        self.assertEqual(self.provider.get_owned_playback_mode(), "shuffle")
        self.assertEqual(self.provider.get_owned_playback_modes(), ("sequential", "shuffle", "repeat_one"))

    def test_system_capture_has_no_owned_mode_context_or_capabilities(self):
        self.player._replace_queue((OnlineSong("netease", "one", "One"),), fm_source="netease")
        self.values["v2_owned_playback_enabled"] = False
        self.assertFalse(self.provider.is_owned_personal_fm())
        self.assertEqual(self.provider.get_owned_playback_modes(), ())

    def test_dj_toggle_is_available_only_in_active_netease_fm_and_saves_confirmed_boolean(self):
        self.assertIsNone(self.provider.get_owned_personal_dj_enabled())
        self.assertFalse(self.provider.set_owned_personal_dj_enabled(True))
        self.player.play_queue((OnlineSong("netease", "one", "One"),))
        self.assertIsNone(self.provider.get_owned_personal_dj_enabled())
        self.player.service.personal_fm = lambda *_: (OnlineSong("netease", "two", "Two"),)
        self.player.service.personal_dj = lambda *_: ()
        self.player._set_fm_source("netease")
        self.assertFalse(self.provider.get_owned_personal_dj_enabled())
        self.assertTrue(self.provider.set_owned_personal_dj_enabled(True))
        self.assertTrue(self.provider.get_owned_personal_dj_enabled())
        self.assertEqual(self.saved, [("v2_personal_dj_enabled", True)])
        self.assertFalse(self.provider.set_owned_personal_dj_enabled(1))
        self.values["v2_owned_playback_enabled"] = False
        self.assertIsNone(self.provider.get_owned_personal_dj_enabled())
        self.assertFalse(self.provider.set_owned_personal_dj_enabled(False))
        self.assertEqual(self.saved, [("v2_personal_dj_enabled", True)])

    def test_dj_closed_player_does_not_persist_false_success(self):
        self.player._replace_queue((OnlineSong("netease", "one", "One"),), fm_source="netease")
        self.player._closed = True
        self.assertFalse(self.provider.set_owned_personal_dj_enabled(True))
        self.assertEqual(self.saved, [])

    def test_dj_spectrum_cannot_leak_into_playlist_system_capture_or_paused_voice(self):
        from tests.test_personal_dj_playback import segments
        self.player.personal_dj_enabled = True
        self.player._replace_queue(segments(), fm_source="netease", fm_kind="dj")
        frame = object()
        self.provider._owned_audio_bridge.get_personal_dj_audio_frame = lambda: frame
        self.assertIsNone(self.provider.get_owned_personal_dj_audio_frame())
        self.player._stream_epoch = self.player._epoch
        self.player._dj_source_ready = True
        self.player.player.play()
        self.assertTrue(self.player.track.is_playing)
        self.assertTrue(self.player.is_personal_dj_speaking())
        self.assertIs(self.provider.get_owned_personal_dj_audio_frame(), frame)
        self.player.player.pause()
        self.assertIsNone(self.provider.get_owned_personal_dj_audio_frame())
        self.player.player.play()
        self.values["v2_owned_playback_enabled"] = False
        self.assertIsNone(self.provider.get_owned_personal_dj_audio_frame())
        self.values["v2_owned_playback_enabled"] = True
        self.player.play_queue((OnlineSong("netease", "one", "One"),))
        self.assertIsNone(self.provider.get_owned_personal_dj_audio_frame())


if __name__ == "__main__":
    unittest.main()
