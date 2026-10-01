from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

from local.music_surface_window import MusicSurfaceWindow


class _Owner(QWidget):
    def __init__(self):
        super().__init__()
        self._music_windowed_preference = False
        self.media_track_id = "short-track"
        self.track_title = "A Complete Song Title"
        self.artist = "A Complete Artist Name"
        self.album_color = QColor(55, 35, 75)
        self.secondary_color = QColor(37, 28, 49)
        self.album_art = QImage(800, 800, QImage.Format.Format_RGB32)
        self.album_art.fill(QColor(120, 80, 140))
        self.media_is_playing = True
        self.media_position_seconds = 1.0
        self.timed_lyrics = [(0.0, "first"), (4.0, "second"), (8.0, "third")]
        self.lyrics = [line for _time, line in self.timed_lyrics]

    def _estimated_media_position(self, _now):
        return self.media_position_seconds


class CenteredAlbumLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(1190, 666)
        self.window._reset_control_idle(now=100.0)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _render(self, now: float) -> QImage:
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window.render(image)
        return image

    def test_short_song_centers_art_metadata_and_transport_in_both_modes(self):
        for fullscreen in (True, False):
            with self.subTest(fullscreen=fullscreen):
                self.window._fullscreen = fullscreen
                self.window._cover_layout_from = 1.0
                self.window._cover_layout_to = 1.0
                self.window._cover_layout_started_at = 100.0
                self._render(100.1)
                art = self.window._current_cover_rect(100.1)
                original, _centered = self.window._cover_layout_endpoints(100.1)
                title, artist, controls = self.window._centered_group_rects(
                    art, self.window._centered_album_plan(original),
                )
                panel = self.window._media_control_panel_rect
                self.assertAlmostEqual(art.center().x(), self.window.width() / 2.0)
                self.assertAlmostEqual(title.center().x(), art.center().x())
                self.assertAlmostEqual(artist.center().x(), art.center().x())
                self.assertGreater(title.top(), art.bottom())
                self.assertGreater(artist.top(), title.bottom())
                self.assertGreater(controls.top(), artist.bottom())
                self.assertAlmostEqual(panel.center().x(), art.center().x())
                self.assertLessEqual(controls.bottom(), self.window.height())
                self.assertEqual(self.window._lyric_hit_rects, {})
                self.assertFalse(self.window._has_timed_lyrics)

    def test_idle_art_grows_and_controls_return_below_artist(self):
        self.window._cover_layout_from = 1.0
        self.window._cover_layout_to = 1.0
        self.window._cover_layout_started_at = 100.0
        self._render(100.0)
        shown_cover = self.window._current_cover_rect(100.0)
        self.assertEqual(len(self.window._media_button_rects), 3)
        self._render(103.0)
        idle_cover = self.window._current_cover_rect(103.0)
        self.assertGreater(idle_cover.width(), shown_cover.width())
        self.assertEqual(self.window._media_button_rects, {})
        with patch.object(self.window, "isActiveWindow", return_value=True):
            self.window._note_pointer_moved(103.0)
        self._render(103.6)
        restored_cover = self.window._current_cover_rect(103.6)
        self.assertAlmostEqual(restored_cover.width(), shown_cover.width(), delta=0.01)
        self.assertEqual(len(self.window._media_button_rects), 3)
        original, _centered = self.window._cover_layout_endpoints(103.6)
        _title, artist, controls = self.window._centered_group_rects(
            restored_cover, self.window._centered_album_plan(original),
        )
        self.assertAlmostEqual(self.window._media_control_panel_rect.top(), controls.top(), delta=0.01)
        self.assertGreater(controls.top(), artist.bottom())

    def test_windowed_center_controls_have_clickable_targets_under_artist(self):
        self.window._fullscreen = False
        self.window._cover_layout_from = 1.0
        self.window._cover_layout_to = 1.0
        self.window._cover_layout_started_at = 100.0
        self.owner._send_playback_command_async = Mock()
        self._render(100.1)
        original, cover = self.window._cover_layout_endpoints(100.1)
        _title, artist, _controls = self.window._centered_group_rects(
            cover, self.window._centered_album_plan(original),
        )
        for command, target in self.window._media_button_rects.items():
            self.assertGreater(target.center().y(), artist.bottom())
            point = target.center()
            event = SimpleNamespace(
                button=lambda: Qt.MouseButton.LeftButton,
                position=lambda: point,
                globalPosition=lambda: point,
                accept=lambda: None,
            )
            with patch("local.music_surface_window.time.monotonic", return_value=100.1):
                self.window.mousePressEvent(event)
                self.window.mouseReleaseEvent(event)
            self.owner._send_playback_command_async.assert_any_call(command)
        self.assertEqual(self.owner._send_playback_command_async.call_count, 3)

    def test_long_names_wrap_fully_within_minimum_window_and_plan_is_reused(self):
        self.window.resize(595, 333)
        self.owner.track_title = "A Very Long Song Title With Distinctive Words " * 4
        self.owner.artist = "An Artist With A Long Name Featuring Another Artist " * 3
        self.window._cover_layout_from = 1.0
        self.window._cover_layout_to = 1.0
        self.window._cover_layout_started_at = 100.0
        with patch.object(self.window, "_centered_text_layout", wraps=self.window._centered_text_layout) as shape:
            self._render(100.1)
            first_count = shape.call_count
            self._render(100.2)
            self.assertEqual(shape.call_count, first_count)
        original, cover = self.window._cover_layout_endpoints(100.2)
        plan = self.window._centered_album_plan(original)
        title, artist, controls = self.window._centered_group_rects(cover, plan)
        self.assertGreater(plan["title_layout"].lineCount(), 1)
        self.assertGreater(plan["artist_layout"].lineCount(), 1)
        for field, text in (("title", self.owner.track_title), ("artist", self.owner.artist)):
            layout = plan[f"{field}_layout"]
            last = layout.lineAt(layout.lineCount() - 1)
            self.assertGreaterEqual(last.textStart() + last.textLength(), len(" ".join(text.split())))
        self.assertGreaterEqual(cover.top(), 0.0)
        self.assertGreater(title.top(), cover.bottom())
        self.assertGreater(artist.top(), title.bottom())
        self.assertLessEqual(controls.bottom(), self.window.height())
        self.assertFalse(self.window._metadata_marquee_moving)

    def test_track_change_hides_lyrics_at_first_sparse_frame_then_restores_for_long_song(self):
        self.owner.timed_lyrics = [(float(i), f"line {i}") for i in range(6)]
        self.owner.lyrics = [text for _time, text in self.owner.timed_lyrics]
        self._render(100.0)
        self.assertTrue(self.window._has_timed_lyrics)
        self.owner.media_track_id = "short-next"
        self.owner.timed_lyrics = [(0.0, "first"), (5.0, "second")]
        self.owner.lyrics = [text for _time, text in self.owner.timed_lyrics]
        self._render(100.01)
        self.assertFalse(self.window._has_timed_lyrics)
        self.assertEqual(self.window._lyric_hit_rects, {})
        self._render(100.7)
        self.assertGreater(self.window._cover_layout_fraction_at(100.7), 0.99)
        self.owner.media_track_id = "long-next"
        self.owner.timed_lyrics = [(float(i), f"line {i}") for i in range(7)]
        self.owner.lyrics = [text for _time, text in self.owner.timed_lyrics]
        self._render(100.71)
        self.assertTrue(self.window._has_timed_lyrics)
        self._render(101.4)
        self.assertAlmostEqual(self.window._cover_layout_fraction_at(101.4), 0.0)

    def test_layout_transition_repaints_former_metadata_and_destination(self):
        self.owner.timed_lyrics = [(float(i), f"line {i}") for i in range(6)]
        self.owner.lyrics = [text for _time, text in self.owner.timed_lyrics]
        self._render(100.0)
        old_title = self.window._metadata_layout(
            QRectF(60, 60, 404, 404), QRectF(60, 60, 1070, 546), 1.0, 1.0,
        )[1]
        self.owner.media_track_id = "sparse-destination"
        self.owner.timed_lyrics = [(0.0, "one"), (5.0, "two")]
        self.owner.lyrics = ["one", "two"]
        self._render(100.01)
        self._render(100.25)
        dirty = self.window._cover_animation_rect
        original, centered = self.window._cover_layout_endpoints(100.25)
        plan = self.window._centered_album_plan(original)
        title, artist, controls = self.window._centered_group_rects(centered, plan)
        self.assertTrue(dirty.contains(old_title.toAlignedRect()))
        self.assertTrue(dirty.contains(centered.toAlignedRect()))
        self.assertTrue(dirty.contains(title.toAlignedRect()))
        self.assertTrue(dirty.contains(artist.toAlignedRect()))
        self.assertTrue(dirty.contains(controls.toAlignedRect()))

    def test_async_loading_does_not_choose_center_until_lyrics_resolve(self):
        self.owner.timed_lyrics = []
        self.owner.lyrics = ["查找歌词中"]
        self._render(100.0)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.assertFalse(self.window._no_lyrics_cover_target())
        self.assertEqual(self.window._cover_layout_to, 0.0)
        self.owner.lyrics = ["暂无歌词"]
        self._render(100.1)
        with patch("local.music_surface_window.time.monotonic", return_value=100.1):
            self.assertTrue(self.window._no_lyrics_cover_target())
        self.assertEqual(self.window._cover_layout_to, 1.0)

    def test_next_track_holds_center_while_loading_then_enters_lyrics(self):
        self.owner.timed_lyrics = []
        self.owner.lyrics = ["暂无歌词"]
        self._render(100.0)
        self._render(100.7)
        self.assertAlmostEqual(self.window._cover_layout_fraction_at(100.7), 1.0)

        self.owner.media_track_id = "next-track"
        self.owner.track_title = "Next song"
        self.owner.timed_lyrics = []
        self.owner.lyrics = ["查找歌词中"]
        self._render(100.8)
        with patch("local.music_surface_window.time.monotonic", return_value=100.8):
            self.assertTrue(self.window._no_lyrics_cover_target())
        self.assertEqual(self.window._cover_layout_to, 1.0)
        self.assertFalse(self.window._has_timed_lyrics)
        with patch.object(self.window, "_paint_lyric_loading") as loading:
            self._render(104.0)
        loading.assert_not_called()
        self.assertEqual(self.window._cover_layout_to, 1.0)

        self.owner.timed_lyrics = [
            (0.0, "first"), (4.0, "second"),
            (8.0, "third"), (12.0, "fourth"),
        ]
        self.owner.lyrics = [text for _time, text in self.owner.timed_lyrics]
        self._render(104.1)
        self.assertEqual(self.window._cover_layout_to, 0.0)
        self.assertEqual(self.window._lyric_hit_rects, {})
        with patch.object(self.window, "_paint_scrolling_metadata", wraps=self.window._paint_scrolling_metadata) as normal_metadata:
            self._render(104.3)
        # The cover only leaves the center after real lyrics arrive.
        fraction = self.window._cover_layout_fraction_at(104.3)
        self.assertGreater(fraction, 0.0)
        self.assertLess(fraction, 1.0)
        self.assertGreater(self.window._lyric_horizontal_slide(fraction, 1190.0, 564.0), 0.0)
        self._render(104.8)
        self.assertAlmostEqual(self.window._cover_layout_fraction_at(104.8), 0.0)
        self.assertTrue(self.window._lyric_hit_rects)

    def test_lyric_column_slide_runs_only_rightward_for_hiding(self):
        slide = self.window._lyric_horizontal_slide
        distances = [slide(fraction, 1190.0, 564.0) for fraction in (0.0, 0.25, 0.5, 0.75, 1.0)]
        self.assertEqual(distances[0], 0.0)
        self.assertEqual(distances, sorted(distances))
        self.assertGreater(distances[-1], 1190.0 - 564.0)

    def test_previous_song_lyrics_do_not_reappear_when_loading_resolves_to_sparse_song(self):
        old_lines = [f"old line {index}" for index in range(6)]
        self.owner.timed_lyrics = [(float(index * 4), text) for index, text in enumerate(old_lines)]
        self.owner.lyrics = old_lines
        self._render(100.0)
        self.assertIsNotNone(self.window._last_visible_lyric_snapshot)

        self.owner.media_track_id = "sparse-next"
        self.owner.track_title = "Next song"
        self.owner.timed_lyrics = []
        self.owner.lyrics = ["查找歌词中"]
        self._render(100.01)
        with patch.object(self.window, "_song_lyric_layouts", wraps=self.window._song_lyric_layouts) as layouts:
            self._render(100.20)
        self.assertEqual(layouts.call_args.args[0], [])
        self.assertIsNone(self.window._last_visible_lyric_snapshot)

        self.owner.timed_lyrics = [(0.0, "new short line"), (5.0, "another short line")]
        self.owner.lyrics = [text for _at, text in self.owner.timed_lyrics]
        with patch.object(self.window, "_song_lyric_layouts", wraps=self.window._song_lyric_layouts) as layouts:
            self._render(100.25)
        self.assertEqual(layouts.call_args.args[0], [])
        self.assertFalse(self.window._has_timed_lyrics)
        self.assertEqual(self.window._lyric_hit_rects, {})

        with patch.object(self.window, "_song_lyric_layouts", wraps=self.window._song_lyric_layouts) as layouts:
            self._render(100.80)
        self.assertEqual(layouts.call_args.args[0], [])

    def test_same_song_lyric_resolution_can_slide_its_own_ink_right(self):
        lines = [f"same song line {index}" for index in range(6)]
        self.owner.timed_lyrics = [(float(index * 4), text) for index, text in enumerate(lines)]
        self.owner.lyrics = lines
        self._render(100.0)

        # A corrected lyric source for this song can make it sparse without
        # changing the media identity. Keep its exit motion in that case.
        self.owner.timed_lyrics = [(0.0, "short line"), (5.0, "another short line")]
        self.owner.lyrics = [text for _at, text in self.owner.timed_lyrics]
        self._render(100.01)
        with patch.object(self.window, "_song_lyric_layouts", wraps=self.window._song_lyric_layouts) as layouts:
            self._render(100.20)
        self.assertEqual(layouts.call_args.args[0], lines)

    def test_paused_track_still_repaints_lyric_sweep_during_center_exit(self):
        self.owner.media_is_playing = False
        self.owner.timed_lyrics = []
        self.owner.lyrics = ["暂无歌词"]
        self._render(100.0)
        self._render(100.7)
        self.owner.media_track_id = "lyrics-after-instrumental"
        self.owner.timed_lyrics = [(float(i * 4), f"line {i}") for i in range(5)]
        self.owner.lyrics = [text for _time, text in self.owner.timed_lyrics]
        self._render(100.8)
        self._render(101.0)
        self.assertTrue(self.window._lyric_animation_rect.isValid())
        with (
            patch("local.music_surface_window.time.monotonic", return_value=101.05),
            patch.object(self.window, "update") as update,
        ):
            self.window._on_animation_tick()
        self.assertTrue(any(
            not call.args or call.args[0] == self.window._lyric_animation_rect
            for call in update.call_args_list
        ))

    def test_control_and_cover_dirty_regions_include_centered_group_sweep(self):
        self.window._cover_layout_from = 1.0
        self.window._cover_layout_to = 1.0
        self.window._cover_layout_started_at = 100.0
        self._render(100.0)
        original, _centered = self.window._cover_layout_endpoints(100.0)
        plan = self.window._centered_album_plan(original)
        idle_title, idle_artist, idle_controls = self.window._centered_group_rects(plan["idle_cover"], plan)
        with patch.object(self.window, "update") as update:
            self.window._update_control_animation_regions(102.25, force=True)
        dirty = [args.args[0] for args in update.call_args_list if args.args]
        self.assertTrue(any(region.contains(idle_title.toAlignedRect()) for region in dirty))
        self.assertTrue(any(region.contains(idle_artist.toAlignedRect()) for region in dirty))
        visible_controls = idle_controls.intersected(QRectF(self.window.rect())).toAlignedRect()
        self.assertTrue(any(region.contains(visible_controls) for region in dirty))


if __name__ == "__main__":
    unittest.main()
