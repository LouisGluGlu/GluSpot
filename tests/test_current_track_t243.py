"""Every presentation consumes the completed provider-selected owner snapshot."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRect
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from api.models import ColorToken, IslandVisualData, TrackData
from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual
from local.music_surface_window import MusicSurfaceWindow
from local.music_lyric_preparation_store import LyricPresentationStore
from tests.test_music_lyric_preparation import ControlledTasks


def track(identity, *, lyrics=("查找歌词中",), artwork=b"", source="", state="pending"):
    return TrackData(identity, "Artist " + identity, lyrics,
                     ColorToken(40, 80, 120), ColorToken(20, 30, 60), (),
                     track_id=identity, album_art_bytes=artwork,
                     album_art_source=source, album_art_lookup_state=state,
                     duration_seconds=180.)


class SharedCurrentTrackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.clock = patch("local.music_surface_window.time.monotonic", return_value=100.)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.provider = EmptyIslandDataProvider()
        self.data = IslandVisualData(track("first"), (), ())
        self.provider.get_visual_data = lambda: self.data
        self.owner = IslandStackVisual(self.provider)
        self.owner.timer.stop()
        self.owner._lyric_presentation_store.close()
        self.tasks = ControlledTasks()
        self.owner._lyric_presentation_store = LyricPresentationStore(self.owner, tasks=self.tasks)
        self.windows = []
        self.addCleanup(self.dispose)

    def dispose(self):
        for window in self.windows:
            window.shutdown()
            window.deleteLater()
        self.owner.close()
        self.owner.deleteLater()
        self.app.processEvents()

    def window(self, *, live=False):
        if live:
            from _demos.narrow3.narrow_window import NarrowStudyWindow
            window = NarrowStudyWindow(self.owner, "B", "left", live=True)
        else:
            window = MusicSurfaceWindow(self.owner)
        window._timer.stop()
        window._audio_output_status_timer.stop()
        self.windows.append(window)
        return window

    def publish(self, incoming, now=100.):
        self.data = IslandVisualData(incoming, (), ())
        self.owner.last_media_refresh_at = 0.
        with patch("local.island_stack_visual.time.monotonic", return_value=now):
            self.owner._refresh_media_data(now)

    def test_rapid_switch_publishes_only_fully_applied_state_to_all_windows(self):
        windows = [self.window(), self.window(live=True)]
        observed = []
        self.owner.visualDataChanged.connect(lambda data: observed.append((
            data.track.track_id, self.owner.media_track_id, self.owner.track_title,
            self.owner.current_visual_data.track.track_id)))
        for index, identity in enumerate(("second", "third", "fourth")):
            self.publish(track(identity), now=100. + index)
            self.assertEqual(self.owner.current_visual_data.track.track_id, identity)
            for window in windows:
                self.assertIs(window.owner, self.owner)
                self.assertEqual(window._visual_track_identity(), identity)
                self.assertEqual(window._track_visual[:3], (identity, identity, "Artist " + identity))
        self.assertEqual(observed, [(key, key, key, key) for key in ("second", "third", "fourth")])

    def test_window_created_or_hidden_during_switch_has_current_state_before_paint(self):
        hidden = self.window()
        hidden.hide()
        self.publish(track("new-current"))
        created = self.window(live=True)
        self.assertFalse(hidden._timer.isActive())
        for window in (hidden, created):
            self.assertEqual(window._visual_track_identity(), "new-current")
            window._on_owner_visual_data_changed()
            self.assertEqual(window._owner_visual_key[0], "new-current")
        hidden.show()
        self.app.processEvents()
        self.assertEqual(hidden._track_visual[0], "new-current")

    def test_track_publication_retires_frozen_old_song_without_window_timer(self):
        window = self.window()
        window.resize(960, 540)
        snapshot = QImage(40, 30, QImage.Format.Format_RGB32)
        snapshot.fill(QColor("red"))
        window._geometry_transition_snapshot = snapshot
        window._mode_lyric_snapshot_track = "first"
        window._pending_fullscreen = False
        window._mode_animation.setEndValue(QRect(0, 0, 960, 540))
        window._layout_snapshot = snapshot
        window._compact_snapshot = snapshot
        window._lyric_resize_snapshot = snapshot
        self.publish(track("next"))
        for image in (window._geometry_transition_snapshot, window._layout_snapshot,
                      window._compact_snapshot, window._lyric_resize_snapshot):
            self.assertTrue(image.isNull())
        self.assertEqual(window._track_visual[0], self.owner.media_track_id)
        self.assertIsNone(window._pending_fullscreen)

    def test_same_track_artwork_updates_do_not_extend_lyric_deadline(self):
        window = self.window()
        self.publish(track("slow"))
        started = window._lyric_loading_started_at
        self.publish(replace(self.data.track, album_art_lookup_state="failed"), now=115.)
        self.assertEqual(window._lyric_loading_started_at, started)
        window._update_lyric_loading_state(started + 30.)
        window._sync_no_lyrics_content_layout(started + 30.)
        self.assertFalse(window._lyric_loading_visible(started + 30.))
        self.assertEqual(window._effective_content_layout(), "cover")
        self.publish(replace(self.data.track, lyrics=tuple(f"Sung verse {i}" for i in range(5))), now=132.)
        while self.tasks.actions:
            self.tasks.finish()
        window._update_lyric_loading_state(132.)
        window._sync_no_lyrics_content_layout(132.)
        self.assertEqual(window._lyric_content_state(132.), "loaded")
        self.assertEqual(window._effective_content_layout(), "lyrics")

    def test_artwork_visual_deadline_keeps_thumbnail_sharp_and_accepts_late_original(self):
        window = self.window()
        image = QImage(64, 64, QImage.Format.Format_RGB32)
        image.fill(QColor("blue"))
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        self.publish(track("slow-art", artwork=bytes(encoded), source="thumbnail"))
        primary, secondary = QColor(40, 80, 120), QColor(20, 30, 60)
        window._track_transition(129.9, primary, secondary)
        self.assertTrue(window._artwork_is_loading())
        window._track_transition(130., primary, secondary)
        self.assertFalse(window._artwork_is_loading())
        self.assertFalse(window._should_blur_preview(self.owner.album_art))
        self.assertFalse(self.owner.album_art.isNull())
        self.publish(replace(self.data.track, album_art_source="original", album_art_lookup_state="original"), now=131.)
        self.assertFalse(window._artwork_is_loading())
        self.assertEqual(self.owner.album_art_source, "original")


if __name__ == "__main__":
    unittest.main()
