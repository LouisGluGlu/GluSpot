from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from api.online_music import OnlineSong
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider


class _ArtworkEvents(QObject):
    changed = Signal()


class _ArtworkProvider(_Provider):
    def __init__(self):
        super().__init__()
        self.events = _ArtworkEvents()
        self.token = 47
        self.current = 0
        self.songs = tuple(OnlineSong("netease", str(i), f"Song {i}") for i in range(100))
        self.prefetch_owned_queue_artwork = Mock(return_value=True)

    def get_owned_playback_queue(self):
        return self.token, self.current, self.songs

    def get_owned_queue_artwork(self, _song):
        return b""

    def get_owned_queue_artwork_changed_signal(self):
        return self.events.changed


class MusicSurfaceQueueArtworkHintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.provider = _ArtworkProvider()
        self.owner.data_provider = self.provider
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window.set_content_layout("playlist", animate=False)
        self.window.show()
        self.window._timer.stop(); self.window._audio_output_status_timer.stop()
        self.render()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def render(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.window.render(image)
        return image

    def flush(self):
        self.window._queue_artwork_hint_timer.stop()
        self.window._flush_queue_artwork_hint()

    def test_paint_defers_bounded_visible_hint_and_repeated_frames_do_not_resubmit(self):
        self.provider.prefetch_owned_queue_artwork.assert_not_called()
        expected = tuple(index for index, rect in self.window._queue_hit_rects.items() if not rect.isEmpty())[:24]
        self.flush()
        self.provider.prefetch_owned_queue_artwork.assert_called_once_with(expected, expected_queue_token=47)
        self.assertGreater(len(expected), 0)
        self.assertLessEqual(len(expected), 24)
        self.render(); self.flush()
        self.assertEqual(self.provider.prefetch_owned_queue_artwork.call_count, 1)

    def test_scroll_discards_pending_old_view_and_submits_new_painted_rows(self):
        before = self.window._queue_artwork_hint_pending
        event = SimpleNamespace(pixelDelta=lambda: QPoint(0, -1200), angleDelta=lambda: QPoint())
        self.window._scroll_playback_queue(event)
        self.assertIsNone(self.window._queue_artwork_hint_pending)
        self.flush()
        self.provider.prefetch_owned_queue_artwork.assert_not_called()
        self.render()
        after = self.window._queue_artwork_hint_pending
        self.assertNotEqual(before[2], after[2])
        self.flush()
        self.provider.prefetch_owned_queue_artwork.assert_called_once_with(after[2], expected_queue_token=47)

    def test_generation_change_replaces_pending_hint_and_layout_exit_clears_visible_window(self):
        self.provider.token = 48
        self.provider.current = 70
        self.render()
        self.flush()
        indices = self.provider.prefetch_owned_queue_artwork.call_args.args[0]
        self.assertIn(70, indices)
        self.provider.prefetch_owned_queue_artwork.assert_called_once_with(indices, expected_queue_token=48)
        self.window.set_content_layout("cover", animate=False)
        self.assertEqual(self.provider.prefetch_owned_queue_artwork.call_args.args, ((),))
        self.assertEqual(self.provider.prefetch_owned_queue_artwork.call_args.kwargs, {"expected_queue_token": 48})

    def test_paused_visible_playlist_repaints_only_queue_when_artwork_arrives(self):
        self.owner.media_is_playing = False
        with patch.object(self.window, "update") as update:
            self.provider.events.changed.emit()
        update.assert_called_once_with(self.window._queue_view_rect.toAlignedRect())

    def test_hide_clears_visible_window_and_reopen_connects_exactly_once(self):
        self.flush()
        self.provider.prefetch_owned_queue_artwork.reset_mock()
        self.window.hide()
        self.provider.prefetch_owned_queue_artwork.assert_called_once_with((), expected_queue_token=47)
        with patch.object(self.window, "update") as update:
            self.provider.events.changed.emit()
        update.assert_not_called()
        self.window.show()
        self.window._timer.stop(); self.window._audio_output_status_timer.stop()
        self.window._sync_queue_artwork_connection()
        self.window._sync_queue_artwork_connection()
        self.render()
        self.flush()
        self.assertEqual(self.provider.prefetch_owned_queue_artwork.call_count, 2)
        with patch.object(self.window, "update") as update:
            self.provider.events.changed.emit()
        update.assert_called_once_with(self.window._queue_view_rect.toAlignedRect())

    def test_provider_replacement_disconnects_old_signal_and_clears_old_hint(self):
        self.flush()
        replacement = _ArtworkProvider()
        self.owner.data_provider = replacement
        self.window._sync_queue_artwork_connection()
        self.assertEqual(self.provider.prefetch_owned_queue_artwork.call_args.args, ((),))
        with patch.object(self.window, "update") as update:
            self.provider.events.changed.emit()
        update.assert_not_called()
        with patch.object(self.window, "update") as update:
            replacement.events.changed.emit()
        update.assert_called_once_with(self.window._queue_view_rect.toAlignedRect())

    def test_hidden_pending_hint_and_shutdown_signals_do_not_launch_work_or_callback(self):
        self.window.hide()
        self.flush()
        self.provider.prefetch_owned_queue_artwork.assert_not_called()
        self.window.shutdown()
        with patch.object(self.window, "update") as update:
            self.provider.events.changed.emit()
        update.assert_not_called()
        self.assertFalse(self.window._queue_artwork_hint_timer.isActive())
