from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.online_music import OnlineSong
from local.music_lyric_motion import LogicalLineSpringChain
from local.music_surface_layout import CONTENT_LAYOUTS, normalize_content_layout, paint_content_layout_glyph
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider
from tests.test_song_collection_surface import Collection


class MusicSurfaceLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.data_provider = _Provider()
        self.saved = {}
        self.owner.settings_setter = lambda key, value: self.saved.update({key: value})
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window._sync_owned_playback_mode()
        self.window._reset_control_idle()

    def tearDown(self):
        self.window.shutdown(); self.owner.close()

    def render(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.window.render(image)
        return image

    def event(self, point):
        return SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton, position=lambda: point,
            globalPosition=lambda: QPointF(self.window.mapToGlobal(point.toPoint())), accept=lambda: None)

    def settle(self):
        self.window._layout_animation.stop()
        self.window._finish_content_layout()
        self.window._cover_layout_from = self.window._cover_layout_to = float(self.window._no_lyrics_cover_target())
        self.render()

    def prepare_bottom_clearance_song(self):
        self.owner.media_track_id = "owned:netease:1"
        self.owner._song_collection_controller = Collection()
        self.owner.media_is_playing = False
        self.owner.timed_lyrics = [(float(i * 10), f"Readable row {i:02d}") for i in range(12)]
        self.owner.lyrics = [text for _, text in self.owner.timed_lyrics]
        self.window.set_content_layout("lyrics", animate=False)

    def render_lyric_ink(self, *, faded=True):
        ratio = self.window.devicePixelRatioF()
        ink = QImage(round(self.window.width() * ratio), round(self.window.height() * ratio),
                     QImage.Format.Format_ARGB32_Premultiplied)
        ink.setDevicePixelRatio(ratio)
        ink.fill(Qt.GlobalColor.transparent)
        with QPainter(ink) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            paint = (self.window._paint_live_lyric_column if faded else
                     getattr(self.window, "_paint_lyric_column_ink", self.window._paint_live_lyric_column))
            paint(painter, QRectF(self.window.rect()))
        return ink

    def test_wide_full_height_column_keeps_active_anchor_and_softly_clears_controls(self):
        self.prepare_bottom_clearance_song()
        for size in ((640, 360), (960, 540), (1040, 600), (1280, 720)):
            self.window.resize(*size)
            self.assertFalse(self.window._content_layout_is_narrow())
            surface = QRectF(self.window.rect())
            content, _cover, column = self.window._content_column_geometry()
            self.assertEqual(column.top(), surface.top())
            self.assertEqual(column.bottom(), surface.bottom())
            for active in (0, 5, 11):
                with self.subTest(size=size, active=active):
                    self.owner.media_position_seconds = active * 10. + 1.
                    with patch.object(self.window, "_lyric_view_anchor", return_value=(float(active), active, True)):
                        self.render()
                        ink = self.render_lyric_ink()
                    controls = self.window._media_control_panel_rect
                    collection = self.window._song_collection_geometry(surface, shifted=False)
                    self.assertTrue(collection.isValid())
                    boundary = min(controls.top(), collection.top())
                    # Layout/hit geometry can reach into chrome; only ink fades.
                    clip = self.window._lyric_clip_rect
                    self.assertGreater(clip.bottom(), boundary)
                    self.assertGreater(column.bottom(), boundary)
                    expected_center = self.window._wide_layout_g(surface).axis
                    self.assertAlmostEqual(self.window._lyric_hit_rects[active].center().y(), expected_center)
                    self.assertEqual(self.window._lyric_ink_clip_rect.top(), surface.top())
                    self.assertEqual(self.window._lyric_ink_clip_rect.bottom(), surface.bottom())
                    self.assertEqual(ink.height(), round(surface.height() * ink.devicePixelRatioF()))

    def test_wide_bottom_feather_continues_through_controls_to_the_window_edge(self):
        self.prepare_bottom_clearance_song()
        self.owner.media_position_seconds = 51.
        for size in ((960, 540), (640, 360)):
            with self.subTest(size=size), \
                    patch.object(self.window, "_lyric_view_anchor", return_value=(5., 5, True)), \
                    patch.object(self.window, "_lyric_blur_follow_levels", return_value=((0.,) * 12, False)):
                self.window.resize(*size)
                self.render()
                plain = self.render_lyric_ink(faded=False).convertToFormat(QImage.Format.Format_RGBA8888)
                faded = self.render_lyric_ink().convertToFormat(QImage.Format.Format_RGBA8888)
                alpha_plain = np.frombuffer(plain.constBits(), np.uint8).reshape(plain.height(), plain.width(), 4)[:, :, 3]
                alpha_faded = np.frombuffer(faded.constBits(), np.uint8).reshape(faded.height(), faded.width(), 4)[:, :, 3]
                fade_top, fade_bottom = self.window._lyric_bottom_fade_band(QRectF(self.window.rect()))
                controls = self.window._media_control_panel_rect
                collection = self.window._song_collection_geometry(QRectF(self.window.rect()), shifted=False)
                self.assertAlmostEqual(fade_bottom, self.window.height())
                # Wide transport now sits under the cover, so the fade clears
                # the bottom collection instead of that left-hand group.
                chrome_top = collection.top() if collection.isValid() else controls.top()
                if self.window._content_layout_is_narrow():
                    chrome_top = min(controls.top(), chrome_top)
                self.assertLess(fade_top, chrome_top)
                ratio = self.window.devicePixelRatioF()
                start, end = int(fade_top * ratio), int(np.ceil(fade_bottom * ratio))
                self.assertTrue(np.any((alpha_faded[start:end] > 0) & (alpha_faded[start:end] < alpha_plain[start:end])))
                self.assertTrue(np.all(alpha_faded <= alpha_plain))
                core_start = round(self.window.height() * .20 * ratio)
                self.assertTrue(np.array_equal(alpha_plain[core_start:start], alpha_faded[core_start:start]))
                self.assertFalse(np.any(alpha_faded[end:]))
                # The soft mask retains ink through the chrome envelope.
                ink_bottom = np.nonzero(alpha_faded)[0].max() / ratio
                self.assertLess(fade_bottom - ink_bottom, fade_bottom - fade_top)
                self.assertGreater(ink_bottom, fade_top)

    def test_bottom_fade_preserves_readable_lines_of_a_tall_active_row(self):
        self.prepare_bottom_clearance_song()
        self.owner.timed_lyrics[5] = (50., "A long active lyric wraps across many visual lines. " * 12)
        self.owner.lyrics = [text for _, text in self.owner.timed_lyrics]
        self.owner.media_position_seconds = 51.
        with patch.object(self.window, "_lyric_view_anchor", return_value=(5., 5, True)), \
                patch.object(self.window, "_lyric_blur_follow_levels", return_value=((0.,) * 12, False)):
            plain = self.render_lyric_ink(faded=False)
            faded = self.render_lyric_ink()
        column = self.window._content_column_geometry()[2]
        self.assertGreater(self.window._song_layouts[5][0].lineCount(), 4)
        ratio = self.window.devicePixelRatioF()
        core = QRect(round(column.left() * ratio), round(column.height() * .20 * ratio),
                     round(column.width() * ratio), round(column.height() * .35 * ratio))
        self.assertTrue(any(bytes(faded.copy(core).constBits())))
        self.assertEqual(faded.copy(core), plain.copy(core))

    def test_reused_lyric_shapes_keep_ink_clip_at_the_physical_column_edges(self):
        self.prepare_bottom_clearance_song()
        self.render()
        column = self.window._content_column_geometry()[2]
        # The wide anchor has already moved these springs. Reuse shaping is a
        # new coordinate space and should not inherit that roll.
        self.window._logical_line_spring = LogicalLineSpringChain()
        clips = []
        original = self.window._draw_future_wrapped_layout
        def observe(painter, *args, **kwargs):
            self.assertFalse(painter.worldTransform().isIdentity())
            clips.append(painter.worldTransform().mapRect(painter.clipBoundingRect()))
            return original(painter, *args, **kwargs)
        with patch.object(self.window, "_lyric_resize_reuse_scale", self.window._surface_scale() * .5), \
                patch.object(self.window, "_draw_future_wrapped_layout", side_effect=observe):
            self.render_lyric_ink(faded=False)
        self.assertTrue(clips)
        for clip in clips:
            self.assertAlmostEqual(clip.top(), column.top())
            self.assertAlmostEqual(clip.bottom(), column.bottom())

    def test_full_column_and_control_fade_envelope_stay_stable_through_reveals(self):
        self.prepare_bottom_clearance_song()
        self.owner.media_position_seconds = 51.
        for size in ((640, 360), (960, 540), (1280, 720)):
            self.window.resize(*size)
            surface = QRectF(self.window.rect())
            expected_column = self.window._content_column_geometry()[2]
            expected_band = None
            expected_active = None
            for enabled in (False, True):
                self.window._owned_personal_dj_enabled = enabled
                for reveal in (0., .5, 1.):
                    for activity in (0., .5, 1.):
                        with self.subTest(size=size, enabled=enabled, reveal=reveal, activity=activity), \
                                patch.object(self.window, "_lyric_view_anchor", return_value=(5., 5, True)):
                            self.window._control_reveal_from = self.window._control_reveal_target = reveal
                            self.window._footer_lane_from = self.window._footer_lane_target = 400.
                            self.window._footer_lane_started = 100.
                            self.window._dj_activity_from = self.window._dj_activity_to = activity
                            band = self.window._lyric_bottom_fade_band(surface)
                            if expected_band is None:
                                expected_band = band
                            self.assertEqual(band, expected_band)
                            self.assertEqual(self.window._content_column_geometry()[2], expected_column)
                            self.assertEqual(self.window._personal_dj_footer_height(surface), 0.)
                            icon, caption = self.window._personal_dj_geometry(surface, 100., expansion=activity, posed=False)
                            self.assertEqual(band[1], surface.bottom())
                            self.assertLess(band[0], min(icon.top(), caption.top()))
                            self.assertGreater(expected_column.bottom(), caption.top())
                            ink = self.render_lyric_ink().convertToFormat(QImage.Format.Format_RGBA8888)
                            alpha = np.frombuffer(ink.constBits(), np.uint8).reshape(ink.height(), ink.width(), 4)[:, :, 3]
                            self.assertFalse(np.any(alpha[int(np.ceil(band[1] * ink.devicePixelRatioF())):]))
                            active = self.window._lyric_hit_rects[5]
                            if expected_active is None:
                                expected_active = active
                            self.assertEqual(active, expected_active)

    def test_stacked_lyric_viewport_spans_the_window_height(self):
        self.prepare_bottom_clearance_song()
        for size in ((372, 432), (433, 550), (480, 640)):
            with self.subTest(size=size):
                self.window.resize(*size)
                self.assertTrue(self.window._content_layout_is_narrow())
                self.render()
                column = self.window._content_column_geometry()[2]
                self.assertEqual(column.top(), 0.)
                self.assertEqual(column.bottom(), self.window.height())
                self.assertEqual(self.window._lyric_clip_rect.top(), 0.)
                self.assertEqual(self.window._lyric_clip_rect.bottom(), self.window.height())

    def test_resolved_no_lyrics_uses_cover_immediately_and_restores_outside_lyrics(self):
        self.window.show()
        for saved in ("lyrics", "cover_lyrics"):
            with self.subTest(saved=saved):
                self.window.set_content_layout(saved, animate=False)
                original_settings = dict(self.saved)
                with patch.object(self.window, "_display_lyrics", return_value=(["暂无歌词"], [])), \
                     patch.object(self.window, "_lyrics_lookup_pending", return_value=False), \
                     patch.object(self.window, "_lyric_loading_holds_result", return_value=False):
                    self.window._sync_no_lyrics_content_layout(100.)
                self.assertEqual(self.window._effective_content_layout(), "cover")
                self.assertEqual(self.window._content_layout, saved)
                self.assertTrue(self.window._layout_snapshot.isNull())
                self.assertEqual(self.saved, original_settings)
                self.owner.media_track_id = "next-" + saved
                with patch.object(self.window, "_display_lyrics", return_value=(["Sung line"], [(0., "Sung line")])), \
                     patch.object(self.window, "_lyrics_lookup_pending", return_value=False), \
                     patch.object(self.window, "_lyric_loading_holds_result", return_value=False):
                    self.window._sync_no_lyrics_content_layout(101.)
                self.assertEqual(self.window._effective_content_layout(), "lyrics")
                self.assertEqual(self.saved, original_settings)

    def test_loading_skeleton_does_not_trigger_temporary_cover(self):
        for pending, held in ((True, False),):
            with self.subTest(pending=pending, skeleton=held), \
                 patch.object(self.window, "_display_lyrics", return_value=(["暂无歌词"], [])), \
                 patch.object(self.window, "_lyrics_lookup_pending", return_value=pending), \
                 patch.object(self.window, "_lyric_loading_holds_result", return_value=held):
                self.window._sync_no_lyrics_content_layout(100.)
                self.assertEqual(self.window._effective_content_layout(), "lyrics")
                self.assertIsNone(self.window._temporary_content_layout)
        self.assertEqual(self.saved, {})

    def test_one_sung_row_is_not_resolved_absence_and_all_absent_modes_degrade(self):
        with patch.object(self.window, "_display_lyrics", return_value=(["Sung"], [(0., "Sung")])), \
             patch.object(self.window, "_lyrics_lookup_pending", return_value=False), \
             patch.object(self.window, "_lyric_loading_holds_result", return_value=False):
            self.window._sync_no_lyrics_content_layout(100.)
            self.assertIsNone(self.window._temporary_content_layout)
        for saved in ("cover", "playlist"):
            self.window.set_content_layout(saved, animate=False)
            original_settings = dict(self.saved)
            with patch.object(self.window, "_display_lyrics", return_value=([], [])), \
                 patch.object(self.window, "_lyrics_lookup_pending", return_value=False), \
                 patch.object(self.window, "_lyric_loading_holds_result", return_value=False):
                self.window._sync_no_lyrics_content_layout(100.)
            self.assertEqual(self.window._effective_content_layout(), "cover")
            self.assertEqual(self.saved, original_settings)

    def drag_resize(self, width, height):
        start = QPointF(self.window.width() - 1, self.window.height() - 1)
        global_start = QPointF(self.window.mapToGlobal(start.toPoint()))
        delta = QPointF(width - self.window.width(), height - self.window.height())
        self.window.mousePressEvent(self.event(start))
        move = SimpleNamespace(position=lambda: start + delta,
            globalPosition=lambda: global_start + delta,
            buttons=lambda: Qt.MouseButton.LeftButton, accept=lambda: None)
        with patch.object(self.window, "_available_geometry", return_value=QRect(0, 0, 1920, 1080)):
            self.window.mouseMoveEvent(move)
        self.window.mouseReleaseEvent(self.event(QPointF(width - 1, height - 1)))

    def finish_presentation(self):
        target = self.window._mode_animation.endValue()
        self.window._mode_animation.stop()
        self.window.setGeometry(target)
        self.window._finish_mode_transition()
        self.window._timer.stop(); self.window._audio_output_status_timer.stop()

    def test_four_reference_glyphs_are_distinct_nonempty_and_inputs_are_normalized(self):
        pixels = []
        for layout in CONTENT_LAYOUTS:
            image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                paint_content_layout_glyph(painter, QRectF(0, 0, 100, 100), layout, QColor("white"))
            pixels.append(bytes(image.constBits()))
        self.assertEqual(len(set(pixels)), 4)
        self.assertTrue(all(any(data) for data in pixels))
        self.assertEqual(normalize_content_layout({}), "lyrics")

    def test_control_sits_between_playback_and_devices_in_both_presentations_and_sizes(self):
        for size in ((640, 360), (960, 540), (1920, 1080)):
            self.window.resize(*size)
            for fullscreen in (False, True):
                self.window._fullscreen = fullscreen
                self.window._reset_control_idle()
                self.window._sync_window_control_rects()
                audio, layout, playback = [self.window._button_rects[key] for key in ("audio_output", "content_layout", "playback_mode")]
                self.assertLess(audio.right(), layout.left())
                self.assertLess(layout.right(), playback.left())
                self.assertTrue(QRectF(self.window.rect()).contains(audio))

    def test_single_click_cycles_four_modes_without_menu_or_playback_command(self):
        self.window.show(); self.window._timer.stop(); self.window._audio_output_status_timer.stop()
        self.window._reset_control_idle(); self.window._sync_window_control_rects()
        event = self.event(self.window._button_rects["content_layout"].center())
        self.window.mousePressEvent(event); self.window.mouseReleaseEvent(event)
        self.assertEqual(self.window._content_layout, "playlist")
        self.assertEqual(self.saved["v2_music_content_layout"], "playlist")
        self.assertFalse(hasattr(self.window, "_layout_picker"))
        self.assertEqual(self.owner.data_provider.calls, [])
        self.assertLess(self.window._layout_transition_fraction, 1.)
        self.assertFalse(self.window._layout_snapshot.isNull())
        for expected in ("cover", "cover_lyrics", "lyrics"):
            self.window.mousePressEvent(event); self.window.mouseReleaseEvent(event)
            self.assertEqual(self.window._content_layout, expected)
        self.assertEqual(self.owner.data_provider.calls, [])

    def test_requested_layout_survives_both_widths_without_settings_writes(self):
        for layout in CONTENT_LAYOUTS:
            self.window.set_content_layout(layout, animate=False)
            self.saved.clear()
            for size in ((372, 432), (960, 540), (665, 628), (960, 540)):
                with self.subTest(layout=layout, size=size):
                    self.window.resize(*size)
                    self.window._resize_content_layout()
                    self.assertEqual(self.window._content_layout, layout)
                    self.assertEqual(self.window._effective_content_layout(), layout)
                    self.assertEqual(self.saved, {})

    def test_both_widths_cycle_four_modes_starting_with_current_layouts_own_pair(self):
        cycles = {
            "lyrics": ("playlist", "cover", "cover_lyrics", "lyrics"),
            "playlist": ("lyrics", "cover_lyrics", "cover", "playlist"),
            "cover": ("cover_lyrics", "lyrics", "playlist", "cover"),
            "cover_lyrics": ("cover", "playlist", "lyrics", "cover_lyrics"),
        }
        for fullscreen in (False, True):
            self.window._fullscreen = fullscreen
            for size in ((372, 432), (665, 628), (960, 540)):
                self.window.resize(*size)
                for current, cycle in cycles.items():
                    with self.subTest(fullscreen=fullscreen, size=size, current=current):
                        self.window.set_content_layout(current, animate=False)
                        for expected in cycle * 2:
                            self.window._cycle_content_layout()
                            self.assertEqual(self.window._effective_content_layout(), expected)
        self.assertEqual(self.owner.data_provider.calls, [])

    def test_landscape_without_a_side_column_still_renders_external_modes(self):
        songs = tuple(OnlineSong("netease", str(i), f"Song {i}") for i in range(8))
        self.owner.data_provider.get_owned_playback_queue = lambda: (19, 2, songs)
        for fullscreen in (False, True):
            self.window._fullscreen = fullscreen
            self.window.resize(665, 628)
            self.assertGreater(self.window.width(), self.window.height())
            self.assertTrue(self.window._content_layout_is_narrow())
            for layout in ("lyrics", "playlist"):
                with self.subTest(fullscreen=fullscreen, layout=layout):
                    self.window.set_content_layout(layout, animate=False)
                    self.settle()
                    self.assertEqual(self.window._effective_content_layout(), layout)
                    if layout == "playlist":
                        self.assertTrue(self.window._queue_hit_rects)
                        self.assertTrue(self.window._lyric_clip_rect.isEmpty())
                    else:
                        self.assertTrue(self.window._lyric_hit_rects)
                        self.assertEqual(self.window._queue_hit_rects, {})

    def test_explicit_layout_and_width_family_changes_restart_same_pair_priority(self):
        self.window.set_content_layout("playlist", animate=False)
        self.window._cycle_content_layout()
        self.assertEqual(self.window._content_layout, "lyrics")
        self.window.set_content_layout("cover", animate=False)
        self.window._cycle_content_layout()
        self.assertEqual(self.window._content_layout, "cover_lyrics")
        self.window.resize(665, 628)
        self.window._resize_content_layout()
        self.window.resize(960, 540)
        self.window._resize_content_layout()
        self.window._cycle_content_layout()
        self.assertEqual(self.window._content_layout, "cover")

    def test_album_only_hides_lyric_hits_and_cover_lyrics_reuses_seekable_real_rows(self):
        self.window.set_content_layout("cover", animate=False); self.settle()
        self.assertTrue(self.window._no_lyrics_cover_target())
        self.assertFalse(self.window._lyric_hit_rects)
        self.assertTrue(self.window._lyric_clip_rect.isEmpty())
        self.window.set_content_layout("cover_lyrics", animate=False); self.settle()
        cover = self.window._scaled_cover_rect(self.window._current_cover_rect(), self.window._cover_scale_at(100.))
        self.assertTrue(cover.contains(self.window._lyric_clip_rect))
        self.assertTrue(self.window._lyric_hit_rects)
        self.assertEqual(self.owner.timed_lyrics[0], (0., "first line"))

    def test_playlist_renders_bounded_rows_dispatches_displayed_generation_and_keeps_left_cover_clear(self):
        songs = tuple(OnlineSong("netease", str(i), f"Song {i}", artist=f"Artist {i}") for i in range(100))
        provider = self.owner.data_provider
        provider.get_owned_playback_queue = lambda: (19, 2, songs)
        provider.select_owned_queue_index = Mock(return_value=True)
        self.window.set_content_layout("playlist", animate=False); self.settle()
        self.assertFalse(self.window._lyric_hit_rects)
        self.assertTrue(self.window._lyric_clip_rect.isEmpty())
        self.assertGreater(len(self.window._queue_hit_rects), 0)
        self.assertLess(len(self.window._queue_hit_rects), 20)
        index = next(iter(self.window._queue_hit_rects))
        self.window._select_playback_queue(index)
        provider.select_owned_queue_index.assert_called_once_with(index, 19)
        self.assertFalse(self.window._no_lyrics_cover_target())

    def test_queue_reveals_active_row_and_only_decodes_visible_cached_artwork(self):
        songs = tuple(OnlineSong("netease", str(i), f"Song {i}", artist="Artist") for i in range(100))
        provider = self.owner.data_provider
        provider.get_owned_playback_queue = lambda: (24, 90, songs)
        artwork = QImage(16, 16, QImage.Format.Format_RGB32); artwork.fill(QColor("red"))
        data = QByteArray(); buffer = QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(artwork.save(buffer, "PNG"))
        provider.get_owned_queue_artwork = Mock(return_value=bytes(data))
        self.window.set_content_layout("playlist", animate=False); self.settle()
        self.assertIn(90, self.window._queue_hit_rects)
        self.assertGreater(self.window._queue_scroll, 0.)
        self.assertLess(len(self.window._queue_art_cache), 20)
        self.assertTrue(self.window._queue_art_cache)
        self.assertTrue(all(not image.isNull() for image in self.window._queue_art_cache.values()))
        self.assertLess(provider.get_owned_queue_artwork.call_count, 40)

    def test_held_queue_row_rejects_repainted_replacement_and_keeps_pressed_generation(self):
        songs = tuple(OnlineSong("netease", str(i), f"Song {i}") for i in range(8))
        state = {"token": 19}
        provider = self.owner.data_provider
        provider.get_owned_playback_queue = lambda: (state["token"], 0, songs)
        provider.select_owned_queue_index = Mock(return_value=True)
        self.window.show(); self.window._timer.stop(); self.window._audio_output_status_timer.stop()
        self.window.set_content_layout("playlist", animate=False); self.settle()
        index = 1
        event = self.event(self.window._queue_hit_rects[index].center())
        self.window.mousePressEvent(event)
        state["token"] = 20
        self.render()
        self.window.mouseReleaseEvent(event)
        provider.select_owned_queue_index.assert_not_called()
        self.assertIsNone(self.window._queue_pressed)
        self.assertIsNone(self.window._queue_pressed_token)
        self.window.mousePressEvent(event); self.window.mouseReleaseEvent(event)
        provider.select_owned_queue_index.assert_called_once_with(index, 20)

    def test_held_queue_row_without_repaint_dispatches_original_token_to_provider(self):
        songs = tuple(OnlineSong("netease", str(i), f"Song {i}") for i in range(8))
        provider = self.owner.data_provider
        provider.get_owned_playback_queue = lambda: (19, 0, songs)
        provider.select_owned_queue_index = Mock(return_value=False)
        self.window.show(); self.window._timer.stop(); self.window._audio_output_status_timer.stop()
        self.window.set_content_layout("playlist", animate=False); self.settle()
        event = self.event(self.window._queue_hit_rects[1].center())
        self.window.mousePressEvent(event)
        provider.get_owned_playback_queue = lambda: (20, 0, songs)
        self.window.mouseReleaseEvent(event)
        provider.select_owned_queue_index.assert_called_once_with(1, 19)

    def test_layout_transition_releases_old_frame_on_track_change_and_shutdown(self):
        self.window.show(); self.window._timer.stop(); self.window._audio_output_status_timer.stop()
        self.window.set_content_layout("cover")
        self.assertFalse(self.window._layout_snapshot.isNull())
        self.owner.media_track_id = "different"
        self.render()
        self.assertTrue(self.window._layout_snapshot.isNull())
        self.window.set_content_layout("playlist")
        self.window.shutdown()
        self.assertTrue(self.window._layout_snapshot.isNull())

    def test_column_fit_uses_available_width_at_current_height_instead_of_fixed_cutoff(self):
        self.window.resize(665, 628)
        content, cover, column = self.window._content_column_geometry()
        self.assertTrue(self.window._content_layout_is_narrow())
        self.assertEqual(column.top(), 0.)
        self.assertEqual(column.bottom(), self.window.height())
        self.assertGreaterEqual(column.width(), cover.width())
        self.window.set_content_layout("lyrics", animate=False)
        self.assertEqual(self.window._content_layout, "lyrics")
        self.assertEqual(self.window._effective_content_layout(), "lyrics")
        self.window.resize(640, 360)
        self.assertFalse(self.window._content_layout_is_narrow())
        content, cover, column = self.window._content_column_geometry()
        self.assertGreater(column.left(), cover.right())
        self.assertEqual(self.window._effective_content_layout(), "lyrics")
        self.window.resize(960, 540)
        self.assertEqual(self.window._effective_content_layout(), "lyrics")

    def test_every_layout_keeps_artwork_lyric_queue_transport_and_buttons_inside_window(self):
        provider = self.owner.data_provider
        songs = tuple(OnlineSong("netease", str(i), "A long queue song title " * 4, artist="Artist") for i in range(20))
        provider.get_owned_playback_queue = lambda: (44, 3, songs)
        self.owner._music_library_back_callback = Mock()
        self.owner.track_title = "A long song title that should stay inside its available area"
        self.owner.artist = "Artist"
        self.owner.timed_lyrics = [(float(i*4), "oh i just wanna hold you right now 多希望此刻能拥抱你" * 2) for i in range(12)]
        self.owner.lyrics = [text for _,text in self.owner.timed_lyrics]
        for size in ((665,628),(640,360),(372,432),(960,540),(900,900),(200,432),(100,360)):
            self.window.resize(*size)
            for layout in CONTENT_LAYOUTS:
                with self.subTest(size=size,layout=layout):
                    self.window.set_content_layout(layout,animate=False)
                    self.window._reset_control_idle()
                    self.settle()
                    bounds = QRectF(self.window.rect()).adjusted(1.,1.,-1.,-1.)
                    cover = self.window._scaled_cover_rect(self.window._current_cover_rect(),self.window._cover_scale_at(100.))
                    self.assertTrue(bounds.contains(cover), (bounds,cover))
                    if not self.window._lyric_clip_rect.isEmpty():
                        lyric_bounds = (QRectF(self.window.rect()) if layout == "lyrics" else bounds)
                        self.assertTrue(lyric_bounds.contains(self.window._lyric_clip_rect), self.window._lyric_clip_rect)
                    if self.window._effective_content_layout() == "playlist":
                        self.assertTrue(bounds.contains(self.window._queue_view_rect))
                        self.assertTrue(all(self.window._queue_view_rect.contains(row)
                            for row in self.window._queue_hit_rects.values() if not row.isEmpty()))
                    self.assertTrue(bounds.contains(self.window._media_control_panel_rect),self.window._media_control_panel_rect)
                    self.assertTrue(all(bounds.contains(button) for button in self.window._button_rects.values()),self.window._button_rects)

    def test_user_resize_keeps_every_mode_without_writing_a_new_selection(self):
        self.window.show(); self.window._timer.stop(); self.window._audio_output_status_timer.stop()
        for layout in CONTENT_LAYOUTS:
            self.window.resize(372,432)
            self.window.set_content_layout(layout,animate=False)
            self.saved.clear()
            self.drag_resize(665,628)
            self.assertEqual(self.window._effective_content_layout(),layout)
            self.assertEqual(self.window.height(),628)
            self.drag_resize(960,540)
            self.assertEqual(self.window._effective_content_layout(),layout)
            self.assertEqual(self.saved,{})
            self.drag_resize(1000,540)
            self.assertEqual(self.window._effective_content_layout(),layout)
            self.drag_resize(372,432)
            self.assertEqual(self.window._effective_content_layout(),layout)
            self.assertEqual(self.saved,{})
        self.assertEqual(self.window._minimum_window_size().width(),330)
        self.assertEqual(self.window._minimum_window_size().height(),400)

    def test_saved_cover_forms_survive_first_wide_display_without_writing_settings(self):
        for compact in ("cover", "cover_lyrics"):
            self.window.shutdown()
            self.owner.settings_getter = lambda key, default, chosen=compact: chosen if key == "v2_music_content_layout" else default
            self.window = MusicSurfaceWindow(self.owner)
            self.window._normal_geometry = QRect(100, 80, 960, 540)
            self.saved.clear()
            self.window.show_from_island(fullscreen=False)
            self.window._timer.stop(); self.window._audio_output_status_timer.stop()
            self.assertEqual(self.window._content_layout, compact)
            self.assertEqual(self.window._effective_content_layout(), compact)
            self.assertFalse(self.window._last_content_layout_narrow)
            self.assertEqual(self.saved, {})

    def test_island_roundtrip_freezes_resize_family_until_final_destination(self):
        origin = QRect(120, 20, 320, 60)
        for compact in ("cover", "cover_lyrics"):
            self.window._normal_geometry = QRect(100, 80, 960, 540)
            self.window.resize(960, 540)
            self.window.set_content_layout(compact, animate=False)
            self.saved.clear()
            self.window.show_from_island(fullscreen=False, origin_geometry=origin)
            self.assertFalse(self.window._last_content_layout_narrow)
            self.window.setGeometry(100, 80, 372, 432)
            self.assertFalse(self.window._last_content_layout_narrow)
            self.finish_presentation()
            self.window.return_to_island()
            self.finish_presentation()
            self.window.hide()
            self.window.show_from_island(fullscreen=False, origin_geometry=origin)
            self.finish_presentation()
            self.assertEqual(self.window._content_layout, compact)
            self.assertEqual(self.window._effective_content_layout(), compact)
            self.assertFalse(self.window._last_content_layout_narrow)
            self.assertEqual(self.saved, {})

    def test_fullscreen_morph_interruption_and_next_real_drag_preserve_choice(self):
        self.window.show(); self.window._timer.stop(); self.window._audio_output_status_timer.stop()
        self.window.set_content_layout("cover", animate=False)
        self.saved.clear()
        with patch.object(self.window, "_fullscreen_geometry", return_value=QRect(0, 0, 1920, 1080)):
            self.window.toggle_presentation_mode()
            self.window.setGeometry(100, 80, 372, 432)
            self.assertFalse(self.window._last_content_layout_narrow)
            self.finish_presentation()
            self.window.toggle_presentation_mode()
            self.window.setGeometry(100, 80, 372, 432)
            self.window._cancel_mode_transition()
            self.window.setGeometry(100, 80, 960, 540)
        self.assertEqual(self.window._content_layout, "cover")
        self.assertFalse(self.window._last_content_layout_narrow)
        self.assertEqual(self.saved, {})
        self.drag_resize(372, 432)
        self.drag_resize(960, 540)
        self.assertEqual(self.window._content_layout, "cover")
        self.assertEqual(self.saved, {})

    def test_layout_animation_preserves_unrelated_cached_control_layers(self):
        marker = object()
        self.window._control_layer_cache["unrelated"] = marker
        self.window._control_layer_cache_bytes = 123
        self.window._advance_content_layout(.5)
        self.assertIs(self.window._control_layer_cache["unrelated"],marker)
        self.assertEqual(self.window._control_layer_cache_bytes,123)

    def test_layout_handoff_reuses_content_snapshots_but_keeps_controls_live(self):
        self.window.show(); self.window._timer.stop(); self.window._audio_output_status_timer.stop()
        self.window.set_content_layout("playlist")
        self.assertFalse(self.window._layout_target_snapshot.isNull())
        self.assertLessEqual(max(self.window._layout_target_snapshot.size().toTuple()),1600)
        with patch.object(self.window,"_paint_background",wraps=self.window._paint_background) as background, \
                patch.object(self.window,"_paint_playback_queue",wraps=self.window._paint_playback_queue) as queue, \
                patch.object(self.window,"_paint_content_layout_icon",wraps=self.window._paint_content_layout_icon) as icon:
            self.window._advance_content_layout(.25); self.render()
            self.window._advance_content_layout(.75); self.render()
            background.assert_not_called(); queue.assert_not_called()
            self.assertGreaterEqual(icon.call_count,2)
        self.assertEqual(self.window._queue_hit_rects,{})
        self.assertEqual(self.window._lyric_hit_rects,{})
        self.window._finish_content_layout()
        with patch.object(self.window,"_paint_background",wraps=self.window._paint_background) as background:
            self.render()
            background.assert_called_once()
        self.assertTrue(self.window._layout_target_snapshot.isNull())

    def test_layout_snapshot_resize_and_hide_release_both_frames(self):
        self.window.show(); self.window._timer.stop(); self.window._audio_output_status_timer.stop()
        self.window.set_content_layout("cover")
        self.window.resize(1040,540); self.render()
        self.assertTrue(self.window._layout_snapshot.isNull())
        self.assertTrue(self.window._layout_target_snapshot.isNull())
        self.assertTrue(self.window._layout_composite.isNull())
        self.window.set_content_layout("playlist")
        self.window.hide()
        self.assertTrue(self.window._layout_snapshot.isNull())
        self.assertTrue(self.window._layout_target_snapshot.isNull())
        self.assertTrue(self.window._layout_composite.isNull())

    def test_long_song_target_is_shaped_before_it_is_frozen(self):
        self.window.show(); self.window._timer.stop(); self.window._audio_output_status_timer.stop()
        self.window.set_content_layout("cover",animate=False)
        self.owner.timed_lyrics=[(float(index*3),f"A long lyric line {index} with several words") for index in range(80)]
        self.owner.lyrics=[line for _,line in self.owner.timed_lyrics]
        self.window.set_content_layout("lyrics")
        self.assertIsNone(self.window._song_layout_build)
        self.assertEqual(len(self.window._song_layouts),80)
        self.assertFalse(self.window._layout_target_snapshot.isNull())
