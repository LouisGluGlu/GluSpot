from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from api.models import TimedLyricUnit
from local.music_lyric_presentation import INTRO_COUNTDOWN_TEXT
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_lyric_visual_layers import _LayerOwner


class CoverLyricContinuityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _LayerOwner()
        self.owner.album_art = QImage(400, 400, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor("#425766"))
        self.owner.media_position_seconds = 25.0
        self.window = MusicSurfaceWindow(self.owner)
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.window.resize(372, 432)
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def render(self):
        dpr = self.window.devicePixelRatioF()
        image = QImage(round(self.window.width() * dpr), round(self.window.height() * dpr),
                       QImage.Format.Format_RGB32)
        image.setDevicePixelRatio(dpr)
        self.window.render(image)
        return image

    def lyrics(self, count):
        self.owner.timed_lyrics = [
            (20.0 * index, f"Put your pride aside right here next to mine {index}\n翻译：把你的骄傲放在这里，紧靠着我。")
            for index in range(count)
        ]
        self.owner.timed_lyric_units = [()] * count
        self.owner.timed_lyric_timing_kinds = ["line"] * count
        self.window.set_content_layout("cover_lyrics", animate=False)
        self.window._cover_layout_from = self.window._cover_layout_to = 1.0

    def test_cover_and_external_lyrics_share_clean_rows_translation_and_source_units(self):
        credit = '{"t":-1,"c":[{"tx":"作曲: "},{"tx":"Henry Mancini"}]}'
        units = (TimedLyricUnit(10., 11., "Hello "), TimedLyricUnit(11., 12., "world"))
        source = [(0., credit), (10., "Hello world\n翻译：你好世界"), (20., "Next line")]
        self.owner.timed_lyrics = list(source)
        self.owner.timed_lyric_units = [(), units, ()]
        self.owner.timed_lyric_timing_kinds = ["synthetic", "unit", "line"]
        self.owner.media_position_seconds = 10.5
        self.window.resize(960, 540)
        for translated in (False, True):
            self.owner.enable_dual_line_lyrics = translated
            expected = [(10., "Hello world\n你好世界" if translated else "Hello world"), (20., "Next line")]
            for layout in ("cover_lyrics", "lyrics"):
                with self.subTest(layout=layout, translated=translated), patch(
                    "local.music_surface_window.time.monotonic", return_value=100.,
                ):
                    self.window.set_content_layout(layout, animate=False)
                    self.window._cover_layout_from = self.window._cover_layout_to = 1. if layout == "cover_lyrics" else 0.
                    self.render()
                    _lines, timed = self.window._display_lyrics()
                    self.assertEqual(timed, [(0., INTRO_COUNTDOWN_TEXT), expected[0], (12., INTRO_COUNTDOWN_TEXT), expected[1]])
                    self.assertEqual(self.window._display_timed_units, ((), units, (), ()))
                    self.assertEqual(self.window._display_timed_kinds, ("line", "unit", "line", "line"))
                    self.assertEqual(self.window._display_raw_source_texts[0], INTRO_COUNTDOWN_TEXT)
                    self.assertTrue(all(credit not in row for row in self.window._display_raw_source_texts))
                    self.assertEqual(self.owner.timed_lyrics, source)

    def test_metadata_only_cached_song_never_paints_json_in_either_layout(self):
        credits = ['{"t":-1,"c":[{"tx":"作词: "},{"tx":"Jerry Murad, Al Fiore"}]}',
                   '{"t":-1,"c":[{"tx":"作曲: "},{"tx":"Henry Mancini"}]}']
        self.owner.track_title = "Lujon"
        self.owner.artist = "Henry Mancini"
        self.owner.lyrics = credits
        self.window.resize(960, 540)
        for cached_timing in (False, True):
            self.owner.timed_lyrics = list(enumerate(credits)) if cached_timing else []
            self.owner.timed_lyric_units = [(), ()] if cached_timing else []
            self.owner.timed_lyric_timing_kinds = ["synthetic"] * 2 if cached_timing else []
            for layout in ("cover_lyrics", "lyrics"):
                with self.subTest(layout=layout, cached_timing=cached_timing), patch(
                    "local.music_surface_window.time.monotonic", return_value=100.,
                ):
                    self.window.set_content_layout(layout, animate=False)
                    self.window._cover_layout_from = self.window._cover_layout_to = 1. if layout == "cover_lyrics" else 0.
                    with patch.object(self.window, "_draw_sprung_cached_wrapped_layout",
                                      wraps=self.window._draw_sprung_cached_wrapped_layout) as ordinary, \
                            patch.object(self.window, "_paint_wrapped_lifted_lyric",
                                         wraps=self.window._paint_wrapped_lifted_lyric) as current:
                        self.render()
                    self.assertEqual(self.window._display_lyrics(), (["暂无歌词"], []))
                    drawn = [call.args[3] for call in ordinary.call_args_list]
                    drawn += [call.args[2] for call in current.call_args_list]
                    self.assertTrue(all(row not in drawn for row in credits))

    def test_long_bilingual_song_keeps_layout_translation_and_hits_through_control_and_pause_scaling(self):
        self.lyrics(80)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.render()
            self.window._advance_song_layout_build(max_rows=None)
            self.render()
            layout_key = self.window._song_layout_key
            for reveal, scale in ((1., 1.), (.8, 1.), (.4, 1.), (0., 1.), (.5, .8), (1., .8)):
                with (
                    self.subTest(reveal=reveal, scale=scale),
                    patch.object(self.window, "_control_reveal_at", return_value=reveal),
                    patch.object(self.window, "_cover_scale_at", return_value=scale),
                    patch.object(self.window, "_paint_wrapped_lifted_lyric",
                                 wraps=self.window._paint_wrapped_lifted_lyric) as current,
                    patch.object(self.window, "_draw_sprung_cached_wrapped_layout",
                                 wraps=self.window._draw_sprung_cached_wrapped_layout) as ordinary,
                ):
                    self.render()
                    self.assertIsNone(self.window._song_layout_build)
                    self.assertEqual(self.window._song_layout_key, layout_key)
                    drawn = [call.args[2] for call in current.call_args_list]
                    drawn += [call.args[3] for call in ordinary.call_args_list]
                    self.assertIn("把你的骄傲放在这里，紧靠着我。", drawn)
                    cover = self.window._scaled_cover_rect(self.window._current_cover_rect(100.), scale)
                    self.assertTrue(cover.contains(self.window._lyric_clip_rect))
                    self.assertTrue(all(cover.contains(hit) for hit in self.window._lyric_hit_rects.values()
                                        if not hit.isEmpty()))

    def test_cover_translation_respects_toggle_and_keeps_multiline_companion_inside_cover(self):
        self.lyrics(4)
        self.owner.music_surface_reading_strategy = "low_perception"
        self.owner.media_is_playing = False
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.render()
            with patch.object(self.window, "_draw_sprung_cached_wrapped_layout",
                              wraps=self.window._draw_sprung_cached_wrapped_layout) as draw:
                self.render()
            companions = [call for call in draw.call_args_list if call.args[3] == "把你的骄傲放在这里，紧靠着我。"]
            self.assertTrue(companions)
            focus = min(companions, key=lambda call: abs(call.args[2].y() - self.window._lyric_clip_rect.center().y()))
            painter, layout, origin = focus.args[:3]
            self.assertGreater(layout.lineCount(), 0)
            self.assertGreater(origin.y(), self.window._current_cover_rect(100.).top())
            self.assertLess(origin.y() + focus.args[6], self.window._current_cover_rect(100.).bottom())
        self.owner.enable_dual_line_lyrics = False
        with patch("local.music_surface_window.time.monotonic", return_value=101.0):
            self.window.on_translation_visibility_changed(False)
            self.render()
        with patch("local.music_surface_window.time.monotonic", return_value=102.0):
            self.render()
            self.assertTrue(all(layer is None for layer in self.window._song_secondary_layouts))

    def test_external_lyric_pixels_and_blur_flow_past_internal_insets_to_real_window_edges(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.window.set_content_layout("lyrics", animate=False)
        self.window._cover_layout_from = self.window._cover_layout_to = 0.
        self.owner.timed_lyrics = [(4.0 * i, f"row {i}") for i in range(30)]
        self.owner.media_position_seconds = 40.0
        self.window._browse_index = 10.0
        self.window._browse_until = float("inf")
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.render()
            normal = self.render()
            with patch.object(self.window, "_draw_sprung_cached_wrapped_layout"):
                absent = self.render()
        clip = self.window._lyric_clip_rect
        dpr = normal.devicePixelRatio()
        def different(region):
            region = region.intersected(QRectF(self.window.rect()))
            return sum(normal.pixel(x, y) != absent.pixel(x, y)
                       for y in range(max(0, int(region.top() * dpr)), min(normal.height(), int(region.bottom() * dpr)))
                       for x in range(max(0, int(region.left() * dpr)), min(normal.width(), int(region.right() * dpr))))
        self.assertGreater(different(QRectF(clip.left(), 0., clip.width(), clip.top())), 0)
        self.assertGreater(different(QRectF(clip.left(), clip.bottom(), clip.width(), self.window.height() - clip.bottom())), 0)
        # Real cached blur spreads into the left inset instead of stopping at
        # the text column. It remains naturally bounded by its finite kernel.
        self.assertGreater(different(QRectF(clip.left() - 24., 0., 24., self.window.height())), 0)
        self.assertEqual(different(QRectF(0., 0., clip.left() - 50., self.window.height())), 0)

    def test_external_lyric_painter_exposes_both_horizontal_gutters_but_cover_stays_bounded(self):
        for layout in ("lyrics", "cover_lyrics"):
            self.window.resize(1190, 666)
            self.lyrics(12)
            self.window.set_content_layout(layout, animate=False)
            self.window._cover_layout_from = self.window._cover_layout_to = 0. if layout == "lyrics" else 1.
            clips = []
            draw = self.window._draw_sprung_cached_wrapped_layout

            def capture(painter, *args, **kwargs):
                clips.append(painter.clipBoundingRect())
                return draw(painter, *args, **kwargs)

            with patch("local.music_surface_window.time.monotonic", return_value=100.), patch.object(
                self.window, "_draw_sprung_cached_wrapped_layout", side_effect=capture,
            ):
                self.render()
            self.assertTrue(clips)
            if layout == "lyrics":
                self.assertTrue(all(clip.left() <= 0. and clip.right() >= self.window.width() for clip in clips))
            else:
                self.assertTrue(all(clip.width() < self.window.width() for clip in clips))


if __name__ == "__main__":
    unittest.main()
