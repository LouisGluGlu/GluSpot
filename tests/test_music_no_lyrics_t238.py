"""The existing cover-only degradation is a presentation state, not a setting."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner


class NoLyricsLayoutStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.saved = {}
        self.owner.settings_setter = lambda key, value: self.saved.update({key: value})
        self.window = MusicSurfaceWindow(self.owner)
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window.resize(960, 540)
        self.clock = patch("local.music_surface_window.time.monotonic", return_value=100.)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.owner.close)
        self.addCleanup(self.window.shutdown)

    def publish(self, marker=None, *, identity=None, now=100.):
        if identity is not None:
            self.owner.media_track_id = identity
        self.owner.lyrics = [marker] if marker is not None else [f"Sung verse {i}" for i in range(5)]
        self.owner.timed_lyrics = [] if marker is not None else [(i * 5., row) for i, row in enumerate(self.owner.lyrics)]
        self.window._update_lyric_loading_state(now)
        self.window._sync_no_lyrics_content_layout(now)
        self.window._layout_animation.stop()
        self.window._finish_content_layout()

    def test_auto_degrade_all_saved_modes_and_restore_outside_lyrics_without_settings_writes(self):
        for width in (960, 883, 690, 610):
            for saved in ("lyrics", "cover_lyrics", "cover", "playlist"):
                with self.subTest(width=width, saved=saved):
                    self.window.resize(width, 540)
                    self.window.set_content_layout(saved, animate=False)
                    baseline = dict(self.saved)
                    self.publish("暂无歌词", identity=f"miss-{width}-{saved}")
                    self.assertEqual(self.window._effective_content_layout(), "cover")
                    self.assertEqual(self.window._content_layout, saved)
                    self.assertEqual(self.saved, baseline)
                    self.publish(identity=f"sung-{width}-{saved}")
                    self.assertEqual(self.window._effective_content_layout(), "lyrics")
                    self.assertEqual(self.saved, baseline)

    def test_no_lyrics_run_survives_next_pending_and_next_instrumental_track(self):
        self.publish("暂无歌词", identity="miss-one")
        self.publish("查找歌词中", identity="pending-two", now=101.)
        self.assertEqual(self.window._effective_content_layout(), "cover")
        self.assertFalse(self.window._lyric_loading_visible(103.))
        self.publish("纯音乐，请欣赏", identity="instrumental-two", now=104.)
        self.assertEqual(self.window._effective_content_layout(), "cover")
        self.publish(identity="sung-three", now=105.)
        self.assertEqual(self.window._effective_content_layout(), "lyrics")
        self.assertEqual(self.saved, {})

    def test_manual_no_lyrics_toggle_has_only_cover_and_list_at_every_width(self):
        self.publish("纯音乐，请欣赏")
        for width in (960, 883, 690, 610):
            self.window.resize(width, 540)
            for expected in ("playlist", "cover", "playlist", "cover"):
                with self.subTest(width=width, expected=expected):
                    self.window._cycle_content_layout()
                    self.window._layout_animation.stop()
                    self.window._finish_content_layout()
                    self.assertEqual(self.window._effective_content_layout(), expected)
                    self.window._sync_no_lyrics_content_layout(100.)
                    self.assertEqual(self.window._effective_content_layout(), expected)

    def test_manual_list_survives_lyric_arrival_and_manual_cover_restores_outside_lyrics(self):
        for manual in ("playlist", "cover"):
            with self.subTest(manual=manual):
                self.publish("暂无歌词", identity=f"absent-{manual}")
                self.window._cycle_content_layout()
                if manual == "cover":
                    self.window._cycle_content_layout()
                self.assertEqual(self.window._effective_content_layout(), manual)
                baseline = dict(self.saved)
                self.publish("暂无歌词", identity=f"next-absent-{manual}")
                self.assertEqual(self.window._effective_content_layout(), manual)
                self.publish(identity=f"sung-{manual}")
                self.assertEqual(self.window._effective_content_layout(), "playlist" if manual == "playlist" else "lyrics")
                self.assertEqual(self.saved, baseline)

    def test_provider_terminal_and_pure_markers_override_stale_display_loader(self):
        for marker in ("暂无歌词", "纯音乐，请欣赏", "Instrumental", "伴奏", "无人声"):
            with self.subTest(marker=marker):
                self.owner.lyrics = [marker]
                self.owner.timed_lyrics = []
                with patch.object(self.window, "_display_lyrics", return_value=(["加载歌词中"], [])), \
                     patch.object(self.owner, "_music_surface_waiting_for_loaded_lyrics", return_value=True, create=True):
                    self.assertEqual(self.window._lyric_content_state(100.), "no_lyrics")
                    self.window._update_lyric_loading_state(100.)
                    self.window._sync_no_lyrics_content_layout(100.)
                    self.assertEqual(self.window._effective_content_layout(), "cover")
                    self.assertFalse(self.window._lyric_loading_visible(101.5))

    def test_loading_deadline_degrades_once_and_late_lyrics_restore(self):
        self.publish("查找歌词中", identity="slow", now=100.)
        self.window._update_lyric_loading_state(101.5)
        self.assertTrue(self.window._lyric_loading_visible(101.5))
        self.assertEqual(self.window._lyric_content_state(129.9), "loading")
        self.window._update_lyric_loading_state(130.)
        self.window._sync_no_lyrics_content_layout(130.)
        self.assertEqual(self.window._lyric_content_state(130.), "no_lyrics")
        self.assertFalse(self.window._lyric_loading_visible(130.))
        self.assertEqual(self.window._effective_content_layout(), "cover")
        self.window._update_lyric_loading_state(131.)
        self.assertEqual(self.window._lyric_loading_started_at, 100.)
        self.publish(now=132.)
        self.assertEqual(self.window._lyric_content_state(132.), "loaded")
        self.assertEqual(self.window._effective_content_layout(), "lyrics")

    def test_raw_pending_overrides_retained_display_rows_without_resetting_deadline(self):
        self.owner.lyrics, self.owner.timed_lyrics = ["查找歌词中"], []
        self.owner.media_track_id = "pending-with-stale-display"
        with patch.object(self.window, "_display_lyrics", return_value=(["Old verse"], [(0., "Old verse")])):
            self.window._update_lyric_loading_state(100.)
            self.assertEqual(self.window._lyric_content_state(101.5), "loading")
            self.window._update_lyric_loading_state(101.5)
        self.assertEqual(self.window._lyric_loading_started_at, 100.)
        self.window._update_lyric_loading_state(102.)
        self.assertEqual(self.window._lyric_loading_started_at, 100.)

    def test_instrumental_markers_do_not_misclassify_a_song_with_other_sung_rows(self):
        self.owner.lyrics = ["Instrumental", "A real sung verse"]
        self.owner.timed_lyrics = [(0., "Instrumental"), (3., "A real sung verse")]
        self.assertEqual(self.window._lyric_content_state(100.), "loaded")

    def test_loaded_rows_remain_loaded_without_optional_track_title(self):
        del self.owner.track_title
        self.assertEqual(self.window._lyric_content_state(100.), "loaded")

    def test_terminal_miss_discards_existing_lyric_snapshot_even_when_cover_already_selected(self):
        self.window._layout_snapshot = QImage(10, 10, QImage.Format.Format_ARGB32_Premultiplied)
        self.window._layout_target_snapshot = QImage(self.window._layout_snapshot)
        self.window._layout_old = "lyrics"
        self.window._content_layout = "cover"
        self.publish("暂无歌词")
        self.assertTrue(self.window._layout_snapshot.isNull())

    def test_layout_icon_uses_effective_cover_list_and_restored_lyric_modes(self):
        self.publish("暂无歌词")
        for expected in ("cover", "playlist", "cover", "lyrics"):
            with self.subTest(expected=expected):
                if expected == "lyrics":
                    self.publish()
                elif expected != self.window._effective_content_layout():
                    self.window._cycle_content_layout()
                self.window._layout_animation.stop()
                self.window._finish_content_layout()
                image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                with patch("local.music_surface_layout.paint_content_layout_glyph") as paint, QPainter(image) as painter:
                    self.window._paint_content_layout_icon(painter, QRectF(0., 0., 100., 100.))
                self.assertEqual(paint.call_args.args[2], expected)


if __name__ == "__main__":
    unittest.main()
