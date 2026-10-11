from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

from api.lyrics import LocalLyricsStore
from api.online_music import OnlineSong, StreamInfo
from api.providers import EmptyIslandDataProvider, PlaybackSourceProvider, empty_visual_data
from local.app import LocalIslandApp
from local.island_stack_visual import IslandStackVisual
from local.music_surface_window import MusicSurfaceWindow
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_playback import Player, Tasks


class OwnedSurfaceBoundarySyncTests(unittest.TestCase):
    """Exercise publication order without an audio endpoint or native window."""

    def setUp(self):
        self.old = replace(empty_visual_data("old song", "artist", "GluSpot").track,
            track_id="owned:netease:old", is_playing=True, position_seconds=120.0)
        self.current = replace(self.old, title="current song", track_id="owned:netease:current",
            position_seconds=0.0)
        self.system = empty_visual_data("external song", "artist", "system")
        self.owned = SimpleNamespace(track=self.old)
        self.active = True
        self.reads = []
        def visual_data():
            self.reads.append(self.owned.track)
            return replace(self.system, track=self.owned.track) if self.active else self.system
        self.provider = SimpleNamespace(_owned_active=lambda: self.active, get_visual_data=visual_data)
        self.surface = SimpleNamespace(isVisible=lambda: True, update=Mock(),
            _finish_stale_track_transition=Mock())
        self.window = SimpleNamespace(last_media_refresh_at=99.9, media_track_id=self.old.track_id,
            media_is_playing=True, track_title=self.old.title, update=Mock(),
            music_fullscreen_overlay=self.surface)
        self.refreshes = []
        def refresh(now):
            # An app boundary must bypass a recent regular poll explicitly.
            self.assertEqual(self.window.last_media_refresh_at, 0.0)
            self.refreshes.append(now)
            visual = self.provider.get_visual_data()
            self.window.media_track_id = visual.track.track_id
            self.window.media_is_playing = visual.track.is_playing
            self.window.track_title = visual.track.title
            self.window.last_media_refresh_at = now
            return True
        self.window._refresh_media_data = refresh
        self.app = SimpleNamespace(data_provider=self.provider, owned_player=self.owned,
            window=self.window, _owned_surface_key=None)

    def publish(self):
        with patch("local.app.time.monotonic", return_value=100.0):
            LocalIslandApp._owned_playback_changed(self.app)

    def test_new_playing_track_updates_shared_owner_without_waiting_for_poll(self):
        self.publish()
        self.owned.track = self.current
        self.publish()
        self.assertEqual(self.window.media_track_id, self.current.track_id)
        self.assertEqual(self.window.track_title, "current song")
        self.assertTrue(self.window.media_is_playing)
        self.assertEqual(self.refreshes, [100.0, 100.0])
        self.assertEqual(self.window.update.call_count, 2)
        self.assertEqual(self.surface.update.call_count, 2)
        self.assertEqual(self.surface._finish_stale_track_transition.call_count, 2)

    def test_position_and_lyric_progress_keep_existing_bounded_polling(self):
        self.publish()
        for position in range(200):
            self.owned.track = replace(self.old, position_seconds=float(position),
                position_sampled_at=100.0 + position / 10.0, lyrics=(f"line {position}",))
            self.publish()
        self.assertEqual(len(self.reads), 1)
        self.assertEqual(len(self.refreshes), 1)

    def test_same_track_pause_resume_updates_owner_on_each_boundary(self):
        self.publish()
        self.owned.track = replace(self.old, is_playing=False)
        self.publish()
        self.assertFalse(self.window.media_is_playing)
        self.owned.track = replace(self.old, is_playing=True)
        self.publish()
        self.assertTrue(self.window.media_is_playing)
        self.assertEqual(len(self.reads), 3)

    def test_stop_reads_actual_provider_fallback_once(self):
        self.publish()
        self.active, self.owned.track = False, None
        self.publish()
        self.publish()
        self.assertEqual(self.window.track_title, "external song")
        self.assertEqual(len(self.reads), 2)

    def test_hidden_surface_is_not_repainted_or_reopened(self):
        self.surface.isVisible = lambda: False
        self.owned.track = self.current
        self.publish()
        self.assertEqual(self.window.track_title, "current song")
        self.surface.update.assert_not_called()
        self.surface._finish_stale_track_transition.assert_not_called()
        self.window.update.assert_called_once()


class OwnedSurfaceQtIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_source_switch_updates_native_owner_and_finishes_previous_song_bitmap(self):
        with tempfile.TemporaryDirectory() as temporary:
            tasks = Tasks()
            qt_player = Player()
            owned = OwnedMusicPlayer(SimpleNamespace(stream_url=lambda *_args: None, lyrics=lambda *_args: None),
                LocalLyricsStore(root=Path(temporary)), player=qt_player, audio=object(),
                tasks=tasks, artwork_tasks=Tasks(), prefetch_tasks=Tasks(), stream_cache=None)
            system = EmptyIslandDataProvider()
            system.suspend_live_audio = Mock()
            provider = PlaybackSourceProvider(system, owned, lambda _key, default=None: default)
            visual = IslandStackVisual(data_provider=provider)
            visual.timer.stop()
            surface = MusicSurfaceWindow(visual)
            visual.music_fullscreen_overlay = surface
            app = SimpleNamespace(data_provider=provider, owned_player=owned, window=visual,
                _owned_surface_key=None)
            changed = lambda: LocalIslandApp._owned_playback_changed(app)
            owned.changed.connect(changed)
            try:
                surface.resize(640, 360)
                surface.show()
                surface._timer.stop()
                surface._audio_output_status_timer.stop()
                songs = (OnlineSong("netease", "old", "old song", duration_seconds=120),
                         OnlineSong("netease", "current", "current song", duration_seconds=120))
                owned.play_queue(songs)
                token = next(token for token, _action in reversed(tasks.jobs) if token[0] == "stream")
                tasks.completed.emit(token, StreamInfo("netease", "old", "https://example.test/old.mp3", True), None)
                self.assertEqual(visual.media_track_id, "owned:netease:old")
                self.assertTrue(visual.media_is_playing)
                surface._track_transition(time.monotonic(), QColor("red"), QColor("blue"))
                # Model an in-flight presentation whose cached page belongs
                # to the previous song while the normal media poll is recent.
                surface._geometry_transition_snapshot = QImage(640, 360, QImage.Format.Format_ARGB32)
                surface._geometry_transition_snapshot.fill(QColor("red"))
                surface._mode_lyric_snapshot_track = visual.media_track_id
                surface._pending_fullscreen = False
                destination = QRect(40, 60, 720, 405)
                surface._mode_animation.setEndValue(destination)
                visual.last_media_refresh_at = time.monotonic()
                self.assertTrue(owned.send_playback_command("next"))
                # No QApplication.processEvents or timer/poll is needed for
                # the same Qt-thread source-boundary signal to publish state.
                self.assertEqual(visual.media_track_id, "owned:netease:current")
                self.assertEqual(visual.track_title, "current song")
                self.assertFalse(visual.media_is_playing)
                self.assertTrue(surface._geometry_transition_snapshot.isNull())
                self.assertIsNone(surface._pending_fullscreen)
                self.assertEqual(surface.geometry(), destination)
                token = next(token for token, _action in reversed(tasks.jobs) if token[0] == "stream")
                tasks.completed.emit(token, StreamInfo("netease", "current", "https://example.test/current.mp3", True), None)
                self.assertTrue(visual.media_is_playing)
                self.assertEqual(visual.media_track_id, owned.track.track_id)
                surface._track_transition(time.monotonic(), QColor("red"), QColor("blue"))
                self.assertEqual(surface._track_visual[0], owned.track.track_id)
                owned.stop()
                self.assertFalse(visual.media_track_id.startswith("owned:"))
            finally:
                owned.changed.disconnect(changed)
                provider._owned_audio_bridge.close()
                owned.close()
                surface.shutdown()
                visual.close()


class HiddenMusicPageIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.owner.data_provider = SimpleNamespace()
        self.owner.media_track_id = "owned:netease:old"
        self.owner.track_title, self.owner.artist = "old title", "old artist"
        self.owner.media_position_seconds, self.owner.media_position_sync_at = 83.5, 19.9
        self.owner.media_is_playing = True
        self.owner.media_duration_seconds = 120.0
        self.owner.timed_lyrics = [(float(index * 20), f"lyric {index}") for index in range(6)]
        self.owner.lyrics = [text for _start, text in self.owner.timed_lyrics]
        self.owner.album_color, self.owner.secondary_color = QColor("red"), QColor("blue")
        self.owner.album_art = QImage(64, 64, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor("red"))
        self.owner.album_art_source, self.owner.album_art_lookup_state = "original", "original"
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(640, 360)
        self.window.show()
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window._reset_control_idle(now=10.0)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def render(self, now):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window.render(image)
        return image

    def select_current_track(self):
        self.owner.media_track_id = "owned:netease:current"
        self.owner.track_title, self.owner.artist = "current title", "current artist"
        self.owner.album_art = QImage(64, 64, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor("blue"))

    def test_hidden_song_change_reopens_current_page_without_previous_song_in_destination(self):
        self.render(10.0)
        self.window.hide()
        self.select_current_track()
        texts = []
        original = self.window._paint_scrolling_metadata
        def observe(painter, rect, text, *args, **kwargs):
            texts.append(text)
            return original(painter, rect, text, *args, **kwargs)
        clocks = (self.owner.media_position_seconds, self.owner.media_position_sync_at)
        with patch.object(self.window, "_paint_scrolling_metadata", side_effect=observe):
            with patch("local.music_surface_window.time.monotonic", return_value=20.0):
                self.window.show_from_island(fullscreen=False, origin_geometry=QRect(80, 50, 96, 40))
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()
        self.assertEqual(self.window._track_visual[0], self.owner.media_track_id)
        self.assertIsNone(self.window._previous_track_visual)
        self.assertEqual(self.window._mode_lyric_snapshot_track, self.owner.media_track_id)
        self.assertFalse(self.window._geometry_transition_snapshot.isNull())
        self.assertIn("current title", texts)
        self.assertNotIn("old title", texts)
        self.assertNotIn("old artist", texts)
        self.assertEqual((self.owner.media_position_seconds, self.owner.media_position_sync_at), clocks)

    def test_same_song_reopen_preserves_existing_track_handoff_and_clock(self):
        self.render(10.0)
        self.window.hide()
        clocks = (self.owner.media_position_seconds, self.owner.media_position_sync_at)
        with patch.object(self.window, "_on_artwork_track_changed", wraps=self.window._on_artwork_track_changed) as handoff:
            with patch("local.music_surface_window.time.monotonic", return_value=20.0):
                self.window.show_from_island(fullscreen=False, origin_geometry=QRect(80, 50, 96, 40))
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()
        handoff.assert_not_called()
        self.assertEqual(self.window._track_visual[0], self.owner.media_track_id)
        self.assertEqual(self.window._mode_lyric_snapshot_track, self.owner.media_track_id)
        self.assertEqual((self.owner.media_position_seconds, self.owner.media_position_sync_at), clocks)

    def test_visible_track_metadata_crossfade_never_draws_both_titles_at_full_opacity(self):
        self.render(10.0)
        self.select_current_track()
        self.window._track_transition(20.0, QColor("red"), QColor("blue"))
        for elapsed, current_alpha, previous_alpha in ((0.0, 0.0, 1.0), (0.18, 0.5, 0.5), (0.37, 1.0, None)):
            seen = {}
            original = self.window._paint_scrolling_metadata
            def observe(painter, rect, text, *args, **kwargs):
                seen[text] = painter.opacity()
                return original(painter, rect, text, *args, **kwargs)
            with patch.object(self.window, "_paint_scrolling_metadata", side_effect=observe):
                self.render(20.0 + elapsed)
            self.assertAlmostEqual(seen["current title"], current_alpha, places=4)
            self.assertAlmostEqual(seen["current artist"], current_alpha, places=4)
            if previous_alpha is None:
                self.assertNotIn("old title", seen)
                self.assertNotIn("old artist", seen)
            else:
                self.assertAlmostEqual(seen["old title"], previous_alpha, places=4)
                self.assertAlmostEqual(seen["old artist"], previous_alpha, places=4)


if __name__ == "__main__":
    unittest.main()
