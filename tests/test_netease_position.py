from __future__ import annotations

import sys
import types
import unittest
import time
from types import SimpleNamespace

from PIL import Image

from api.models import TimedLyricLine
from api.netease_position import NeteasePositionReader, NeteasePositionSample, QQPositionReader


class NeteasePositionReaderTests(unittest.TestCase):
    def test_duration_from_nested_queue_payload(self):
        reader = NeteasePositionReader()
        payload = {
            "list": [
                {
                    "name": "Contagious",
                    "duration": 219637,
                    "artists": [{"name": "SAFE"}, {"name": "Kiana Lede"}],
                }
            ]
        }

        duration = reader._find_duration(payload, "Contagious", "SAFE")

        self.assertAlmostEqual(duration, 219.637)

    def test_lyric_index_map_uses_normalized_text(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(12.0, "Ooh, ooh-ooh"),
            TimedLyricLine(18.0, "I done lost my faith"),
        )

        indexed = reader._lyric_index_map(lines)

        self.assertEqual(indexed["oohoohooh"], 0)
        self.assertEqual(indexed["idonelostmyfaith"], 1)

    def test_visible_anchor_estimates_inside_current_line(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(10.0, "first"),
            TimedLyricLine(14.0, "second"),
            TimedLyricLine(18.0, "third"),
        )

        position = reader._estimated_anchor_position(lines, 1)

        self.assertGreater(position, 14.0)
        self.assertLess(position, 18.0)

    def test_same_visible_line_anchor_moves_forward(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(10.0, "first"),
            TimedLyricLine(14.0, "second"),
            TimedLyricLine(18.0, "third"),
        )
        previous = NeteasePositionSample(15.4, 30.0, "netease-uia-lyrics", 0.86, 1, "second", 100.0)
        sample = NeteasePositionSample(15.4, 30.0, "netease-uia-lyrics", 0.86, 1, "second", 100.6)

        smoothed = reader._smooth_visible_sample(previous, sample, lines)

        self.assertGreater(smoothed.position_seconds, sample.position_seconds)
        self.assertLess(smoothed.position_seconds, 18.0)

    def test_progress_text_extracts_current_and_duration(self):
        reader = NeteasePositionReader()

        position, duration = reader._progress_from_texts(("01:12 / 03:30",), 210.0)

        self.assertEqual(position, 72.0)
        self.assertEqual(duration, 210.0)

    def test_progress_text_pair_does_not_guess_from_loose_song_durations(self):
        reader = NeteasePositionReader()

        position, duration = reader._progress_from_texts(("01:12", "03:30"), 210.0)

        self.assertIsNone(position)
        self.assertIsNone(duration)

    def test_progress_time_pair_accepts_bottom_player_bar(self):
        reader = NeteasePositionReader()
        controls = (
            {
                "text": "01:12",
                "left": 186.0,
                "top": 665.0,
                "right": 222.0,
                "bottom": 684.0,
                "window_top": 0.0,
                "window_bottom": 720.0,
                "window_index": 0,
            },
            {
                "text": "03:30",
                "left": 1112.0,
                "top": 665.0,
                "right": 1150.0,
                "bottom": 684.0,
                "window_top": 0.0,
                "window_bottom": 720.0,
                "window_index": 0,
            },
        )

        position, duration = reader._progress_from_time_controls(controls, 210.0)

        self.assertEqual(position, 72.0)
        self.assertEqual(duration, 210.0)

    def test_progress_time_pair_accepts_bottom_player_bar_without_known_duration(self):
        reader = NeteasePositionReader()
        controls = (
            {
                "text": "01:12",
                "left": 186.0,
                "top": 665.0,
                "right": 222.0,
                "bottom": 684.0,
                "window_top": 0.0,
                "window_bottom": 720.0,
                "window_index": 0,
            },
            {
                "text": "03:30",
                "left": 1112.0,
                "top": 665.0,
                "right": 1150.0,
                "bottom": 684.0,
                "window_top": 0.0,
                "window_bottom": 720.0,
                "window_index": 0,
            },
        )

        position, duration = reader._progress_from_time_controls(controls, 0.0)

        self.assertEqual(position, 72.0)
        self.assertEqual(duration, 210.0)

    def test_progress_time_pair_rejects_list_row_durations(self):
        reader = NeteasePositionReader()
        controls = (
            {
                "text": "01:12",
                "left": 760.0,
                "top": 260.0,
                "right": 796.0,
                "bottom": 279.0,
                "window_top": 0.0,
                "window_bottom": 720.0,
                "window_index": 0,
            },
            {
                "text": "03:30",
                "left": 820.0,
                "top": 316.0,
                "right": 858.0,
                "bottom": 335.0,
                "window_top": 0.0,
                "window_bottom": 720.0,
                "window_index": 0,
            },
        )

        position, duration = reader._progress_from_time_controls(controls, 210.0)

        self.assertIsNone(position)
        self.assertIsNone(duration)

    def test_loose_progress_time_pair_accepts_noisy_bottom_player_bar(self):
        reader = NeteasePositionReader()
        controls = (
            {
                "text": "02:24",
                "left": 184.0,
                "top": 624.0,
                "right": 222.0,
                "bottom": 644.0,
                "window_left": 0.0,
                "window_top": 0.0,
                "window_right": 1180.0,
                "window_bottom": 720.0,
                "window_index": 0,
            },
            {
                "text": "03:30",
                "left": 1112.0,
                "top": 648.0,
                "right": 1150.0,
                "bottom": 668.0,
                "window_left": 0.0,
                "window_top": 0.0,
                "window_right": 1180.0,
                "window_bottom": 720.0,
                "window_index": 0,
            },
        )

        strict_position, strict_duration = reader._progress_from_time_controls(controls, 210.0)
        position, duration = reader._progress_from_loose_time_controls(controls, 210.0)

        self.assertIsNone(strict_position)
        self.assertIsNone(strict_duration)
        self.assertEqual(position, 144.0)
        self.assertEqual(duration, 210.0)

    def test_loose_progress_time_pair_rejects_playlist_like_times(self):
        reader = NeteasePositionReader()
        controls = (
            {
                "text": "01:12",
                "left": 760.0,
                "top": 530.0,
                "right": 796.0,
                "bottom": 549.0,
                "window_left": 0.0,
                "window_top": 0.0,
                "window_right": 1180.0,
                "window_bottom": 720.0,
                "window_index": 0,
            },
            {
                "text": "03:30",
                "left": 820.0,
                "top": 552.0,
                "right": 858.0,
                "bottom": 571.0,
                "window_left": 0.0,
                "window_top": 0.0,
                "window_right": 1180.0,
                "window_bottom": 720.0,
                "window_index": 0,
            },
        )

        position, duration = reader._progress_from_loose_time_controls(controls, 210.0)

        self.assertIsNone(position)
        self.assertIsNone(duration)

    def test_progress_image_fallback_estimates_bottom_bar_fraction(self):
        reader = NeteasePositionReader()
        image = Image.new("RGB", (1000, 600), (36, 39, 45))
        for x in range(0, 251):
            image.putpixel((x, 520), (125, 132, 144))

        fraction = reader._progress_fraction_from_image(image)

        self.assertIsNotNone(fraction)
        self.assertAlmostEqual(fraction, 0.25, delta=0.02)

    def test_progress_image_fallback_rejects_bottom_panel_edge(self):
        reader = NeteasePositionReader()
        image = Image.new("RGB", (1000, 600), (36, 39, 45))
        for x in range(80, 321):
            image.putpixel((x, 520), (97, 69, 60))

        fraction = reader._progress_fraction_from_image(image)

        self.assertIsNone(fraction)

    def test_progress_image_fallback_rejects_playlist_panel_separator(self):
        reader = NeteasePositionReader()
        image = Image.new("RGB", (1210, 861), (247, 249, 252))
        separator_y = 770
        for x in range(0, 451):
            image.putpixel((x, separator_y - 2), (246, 112, 126))
        for x in range(1, 451):
            image.putpixel((x, separator_y), (255, 255, 255))
        for y in range(separator_y - 12, separator_y + 13):
            for x in range(1188, 1205):
                image.putpixel((x, y), (255, 255, 255))

        fraction = reader._progress_fraction_from_image(image)

        self.assertIsNone(fraction)

    def test_progress_image_fallback_uses_thumb_center_when_visible(self):
        reader = NeteasePositionReader()
        image = Image.new("RGB", (1000, 600), (36, 39, 45))
        for x in range(0, 401):
            image.putpixel((x, 520), (125, 132, 144))
        center_x = 480
        center_y = 520
        radius = 10
        for y in range(center_y - radius, center_y + radius + 1):
            for x in range(center_x - radius, center_x + radius + 1):
                if (x - center_x) ** 2 + (y - center_y) ** 2 <= radius ** 2:
                    image.putpixel((x, y), (238, 238, 236))

        fraction = reader._progress_fraction_from_image(image)

        self.assertIsNotNone(fraction)
        self.assertAlmostEqual(fraction, 0.48, delta=0.02)

    def test_progress_image_fallback_runs_quickly_on_large_frame(self):
        reader = NeteasePositionReader()
        image = Image.new("RGB", (1280, 860), (36, 39, 45))
        for x in range(0, 410):
            image.putpixel((x, 804), (125, 132, 144))

        started = time.perf_counter()
        fraction = reader._progress_fraction_from_image(image)
        elapsed = time.perf_counter() - started

        self.assertIsNotNone(fraction)
        self.assertLess(elapsed, 0.25)

    def test_progress_image_fallback_ignores_missing_duration(self):
        reader = NeteasePositionReader()
        image = Image.new("RGB", (1000, 600), (36, 39, 45))
        for x in range(0, 251):
            image.putpixel((x, 520), (125, 132, 144))

        sample = reader._position_from_progress_image(image, 0.0)

        self.assertIsNone(sample)

    def test_lyric_time_row_anchor_matches_known_lyric_text(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(156.8, "previous"),
            TimedLyricLine(160.0, "那天我们走了很久没有争吵过"),
            TimedLyricLine(166.2, "next"),
        )
        rows = (
            {
                "top": 234.0,
                "bottom": 274.0,
                "container_top": 90.0,
                "container_bottom": 470.0,
                "texts": ("那天我们走了很久没有争吵过", "02:40"),
                "max_text_height": 24.0,
            },
        )

        sample = reader._progress_from_lyric_time_rows(rows, lines, 240.0)

        self.assertIsNotNone(sample)
        self.assertEqual(sample.position_seconds, 160.0)
        self.assertEqual(sample.source, "netease-uia-lyric-time")

    def test_lyric_time_row_anchor_rejects_unknown_row_text(self):
        reader = NeteasePositionReader()
        lines = (TimedLyricLine(160.0, "那天我们走了很久没有争吵过"),)
        rows = (
            {
                "top": 234.0,
                "bottom": 274.0,
                "container_top": 90.0,
                "container_bottom": 470.0,
                "texts": ("歌单里的另一首歌", "02:40"),
                "max_text_height": 24.0,
            },
        )

        sample = reader._progress_from_lyric_time_rows(rows, lines, 240.0)

        self.assertIsNone(sample)

    def test_interactive_refresh_uses_only_bottom_progress_for_position(self):
        reader = NeteasePositionReader()
        calls = []

        def progress(_title, _duration):
            calls.append("progress")
            return NeteasePositionSample(161.0, 240.0, "netease-uia-progress", 0.92, sampled_at=100.0)

        def lyric_time(_lyrics, _duration):
            calls.append("lyric-time")
            return NeteasePositionSample(160.0, 240.0, "netease-uia-lyric-time", 0.82, sampled_at=100.0)

        reader._duration_from_local_queue = lambda _title, _artist: 240.0
        reader._position_from_visible_progress = progress
        reader._position_from_visible_progress_image = lambda _title, _duration: None
        reader._position_from_visible_lyric_time_anchor = lyric_time
        reader._last_track_key = "song\x00artist"

        reader._refresh_sample("song\x00artist", "Song", "Artist", (TimedLyricLine(160.0, "line"),), 240.0, interactive=True)

        self.assertEqual(calls, ["progress"])
        self.assertEqual(reader.diagnostics()["last_source"], "netease-uia-progress")

    def test_interactive_refresh_does_not_fallback_to_lyric_time_anchor_without_click(self):
        reader = NeteasePositionReader()
        calls = []

        def progress(_title, _duration):
            calls.append("progress")
            return None

        def visible_lyrics(_title, _artist, _lyrics, _duration):
            calls.append("lyrics")
            return NeteasePositionSample(160.0, 240.0, "netease-uia-lyrics", 0.86, sampled_at=100.0)

        def lyric_time(_lyrics, _duration):
            calls.append("lyric-time")
            return NeteasePositionSample(160.0, 240.0, "netease-uia-lyric-time", 0.82, sampled_at=100.0)

        reader._duration_from_local_queue = lambda _title, _artist: 240.0
        reader._position_from_visible_progress = progress
        reader._position_from_visible_lyrics = visible_lyrics
        reader._position_from_visible_lyric_time_anchor = lyric_time
        reader._position_from_visible_progress_image = lambda _title, _duration: None
        reader._last_track_key = "song\x00artist"

        reader._refresh_sample("song\x00artist", "Song", "Artist", (TimedLyricLine(160.0, "line"),), 240.0, interactive=True)

        self.assertEqual(calls, ["progress"])
        self.assertEqual(reader.diagnostics()["last_source"], "")

    def test_interactive_refresh_uses_image_progress_when_controls_are_hidden(self):
        reader = NeteasePositionReader()
        calls = []

        def progress(_title, _duration):
            calls.append("progress")
            return None

        def image_progress(_title, _duration):
            calls.append("image-progress")
            return NeteasePositionSample(132.0, 240.0, "netease-uia-progress", 0.74, sampled_at=100.0)

        reader._duration_from_local_queue = lambda _title, _artist: 240.0
        reader._position_from_visible_progress = progress
        reader._position_from_visible_progress_image = image_progress
        reader._last_track_key = "song\x00artist"

        reader._refresh_sample("song\x00artist", "Song", "Artist", (TimedLyricLine(120.0, "line"),), 240.0, interactive=True)

        self.assertEqual(calls, ["image-progress"])
        self.assertEqual(reader.diagnostics()["last_source"], "netease-uia-progress")
        self.assertAlmostEqual(reader.diagnostics()["last_confidence"], 0.74)

    def test_interactive_refresh_prefers_image_progress_over_stale_text_progress(self):
        reader = NeteasePositionReader()
        calls = []

        def stale_progress(_title, _duration):
            calls.append("progress")
            return NeteasePositionSample(54.0, 171.0, "netease-uia-progress", 0.92, sampled_at=100.0)

        def image_progress(_title, _duration):
            calls.append("image-progress")
            return NeteasePositionSample(122.0, 171.0, "netease-uia-progress", 0.74, sampled_at=101.0)

        reader._duration_from_local_queue = lambda _title, _artist: 171.0
        reader._position_from_visible_progress = stale_progress
        reader._position_from_visible_progress_image = image_progress
        reader._last_track_key = "song\x00artist"

        reader._refresh_sample("song\x00artist", "Song", "Artist", (), 171.0, interactive=True)

        self.assertEqual(calls, ["image-progress"])
        self.assertEqual(reader.diagnostics()["last_source"], "netease-uia-progress")
        self.assertEqual(reader._last_sample.position_seconds, 122.0)

    def test_interactive_refresh_prefers_visible_lyrics_for_click_seek(self):
        reader = NeteasePositionReader()
        calls = []

        def progress(_title, _duration):
            calls.append("progress")
            return None

        def visible_lyrics(_title, _artist, _lyrics, _duration, click_pos=None):
            calls.append(("lyrics", click_pos))
            return NeteasePositionSample(160.0, 240.0, "netease-uia-lyrics", 0.86, 1, "line", 100.0)

        reader._duration_from_local_queue = lambda _title, _artist: 240.0
        reader._position_from_visible_progress = progress
        reader._position_from_visible_progress_image = lambda _title, _duration: None
        reader._position_from_visible_lyrics = visible_lyrics
        reader._last_track_key = "song\x00artist"

        reader._refresh_sample(
            "song\x00artist",
            "Song",
            "Artist",
            (TimedLyricLine(160.0, "line"),),
            240.0,
            interactive=True,
            allow_lyric_anchor=True,
            click_pos=(420, 260),
        )

        self.assertEqual(calls, [("lyrics", (420, 260))])
        self.assertEqual(reader.diagnostics()["last_source"], "netease-uia-lyrics")

    def test_click_seek_falls_back_to_progress_when_click_misses_lyrics(self):
        reader = NeteasePositionReader()
        calls = []

        def progress(_title, _duration):
            calls.append("progress")
            return NeteasePositionSample(96.0, 240.0, "netease-uia-progress", 0.92, sampled_at=100.0)

        def visible_lyrics(_title, _artist, _lyrics, _duration, click_pos=None):
            calls.append(("lyrics", click_pos))
            return None

        reader._duration_from_local_queue = lambda _title, _artist: 240.0
        reader._position_from_visible_progress = progress
        reader._position_from_visible_progress_image = lambda _title, _duration: None
        reader._position_from_visible_lyrics = visible_lyrics
        reader._last_track_key = "song\x00artist"

        reader._refresh_sample(
            "song\x00artist",
            "Song",
            "Artist",
            (TimedLyricLine(160.0, "line"),),
            240.0,
            interactive=True,
            allow_lyric_anchor=True,
            click_pos=(420, 650),
        )

        self.assertEqual(calls, [("lyrics", (420, 650)), "progress"])
        self.assertEqual(reader.diagnostics()["last_source"], "netease-uia-progress")

    def test_visible_lyrics_click_position_selects_clicked_row(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(96.0, "before"),
            TimedLyricLine(160.0, "target"),
            TimedLyricLine(166.0, "after"),
        )
        rows = (
            {
                "left": 180.0,
                "right": 520.0,
                "top": 100.0,
                "bottom": 132.0,
                "container_top": 80.0,
                "container_bottom": 420.0,
                "texts": ("before",),
                "max_text_height": 34.0,
            },
            {
                "left": 180.0,
                "right": 520.0,
                "top": 154.0,
                "bottom": 186.0,
                "container_top": 80.0,
                "container_bottom": 420.0,
                "texts": ("target",),
                "max_text_height": 18.0,
            },
            {
                "left": 180.0,
                "right": 520.0,
                "top": 208.0,
                "bottom": 240.0,
                "container_top": 80.0,
                "container_bottom": 420.0,
                "texts": ("after",),
                "max_text_height": 42.0,
            },
        )
        reader._visible_lyric_rows = lambda _desktop, _title: list(rows)
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            sample = reader._position_from_visible_lyrics("Song", "Artist", lines, 240.0, click_pos=(260, 168))
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertIsNotNone(sample)
        self.assertEqual(sample.lyric_index, 1)
        self.assertEqual(sample.lyric_text, "target")

    def test_click_geometry_fallback_selects_nearby_visible_line(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(10.0, "first"),
            TimedLyricLine(20.0, "second"),
            TimedLyricLine(30.0, "third"),
            TimedLyricLine(40.0, "fourth"),
        )
        reader._last_sample = NeteasePositionSample(31.0, 120.0, "netease-uia-progress", 0.74, sampled_at=100.0)
        window = SimpleNamespace(rectangle=lambda: SimpleNamespace(left=0, top=0, right=1000, bottom=800))

        sample = reader._position_from_click_geometry([window], lines, 120.0, click_pos=(500, 500))

        self.assertIsNotNone(sample)
        self.assertEqual(sample.lyric_index, 3)
        self.assertEqual(sample.lyric_text, "fourth")

    def test_read_immediate_refreshes_sample_synchronously(self):
        reader = NeteasePositionReader()
        calls = []

        def progress(title, duration):
            calls.append((title, duration))
            return NeteasePositionSample(42.0, 180.0, "netease-uia-progress", 0.92, sampled_at=100.0)

        reader._position_from_visible_progress = progress
        reader._position_from_visible_progress_image = lambda _title, _duration: None

        sample = reader.read_immediate("Song", "Artist", (), 180.0, interactive=True)

        self.assertIsNotNone(sample)
        self.assertEqual(sample.position_seconds, 42.0)
        self.assertEqual(calls, [("Song", 180.0)])
        self.assertFalse(reader.diagnostics()["refreshing"])
        self.assertEqual(reader.diagnostics()["last_source"], "netease-uia-progress")

    def test_read_immediate_interactive_refreshes_while_background_read_is_running(self):
        reader = NeteasePositionReader()
        calls = []
        lines = (TimedLyricLine(96.0, "before"), TimedLyricLine(160.0, "line"))

        def progress(_title, _duration):
            calls.append("progress")
            return None

        def visible_lyrics(_title, _artist, _lyrics, _duration, click_pos=None):
            calls.append(("lyrics", click_pos))
            return NeteasePositionSample(160.0, 240.0, "netease-uia-lyrics", 0.86, 1, "line", 100.0)

        reader._position_from_visible_progress = progress
        reader._position_from_visible_lyrics = visible_lyrics
        reader._last_track_key = "song\x00artist"
        reader._last_sample = NeteasePositionSample(96.0, 240.0, "netease-uia-progress", 0.92, sampled_at=99.0)
        reader._refreshing = True

        sample = reader.read_immediate("Song", "Artist", lines, 240.0, interactive=True, allow_lyric_anchor=True)

        self.assertIsNotNone(sample)
        self.assertEqual(sample.position_seconds, 160.0)
        self.assertEqual(calls, [("lyrics", None)])
        self.assertFalse(reader.diagnostics()["refreshing"])
        self.assertEqual(reader.diagnostics()["last_source"], "netease-uia-lyrics")

    def test_stale_background_refresh_cannot_overwrite_newer_immediate_sample(self):
        reader = NeteasePositionReader()
        reader._last_track_key = "song\x00artist"
        reader._refresh_token = 2
        reader._last_sample = NeteasePositionSample(160.0, 240.0, "netease-uia-lyrics", 0.86, 1, "line", 100.0)
        reader._position_from_visible_progress = lambda _title, _duration: NeteasePositionSample(
            96.0,
            240.0,
            "netease-uia-progress",
            0.92,
            sampled_at=99.0,
        )
        reader._position_from_visible_progress_image = lambda _title, _duration: None

        reader._refresh_sample("song\x00artist", "Song", "Artist", (), 240.0, interactive=True, refresh_token=1)

        self.assertEqual(reader.diagnostics()["last_source"], "netease-uia-lyrics")
        self.assertEqual(reader.diagnostics()["last_confidence"], 0.86)

    def test_qq_reader_uses_qq_service_prefix(self):
        reader = QQPositionReader()

        self.assertEqual(reader.source_prefix, "qq")
        self.assertEqual(reader.diagnostics()["service_name"], "qq")


if __name__ == "__main__":
    unittest.main()
