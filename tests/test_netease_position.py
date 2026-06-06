from __future__ import annotations

import sys
import tempfile
import types
import unittest
import time
from pathlib import Path
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

    def test_adjacent_track_from_queue_returns_next_song_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            queue = Path(root) / "playingList"
            queue.write_text(
                """
                {
                  "list": [
                    {"name": "Current", "duration": 180000, "artists": [{"name": "A"}], "album": {"name": "One"}},
                    {"name": "Next", "duration": 201000, "artists": [{"name": "B"}], "album": {"name": "Two"}}
                  ]
                }
                """,
                encoding="utf-8",
            )
            reader = NeteasePositionReader(queue_paths=(queue,))

            track = reader.adjacent_track("Current", "A", "next")

        self.assertIsNotNone(track)
        self.assertEqual(track.title, "Next")
        self.assertEqual(track.artist, "B")
        self.assertEqual(track.album_title, "Two")
        self.assertAlmostEqual(track.duration_seconds, 201.0)

    def test_queue_track_reads_album_art_url(self):
        reader = NeteasePositionReader()
        track = reader._queue_track_from_item(
            {
                "name": "Next",
                "duration": 201000,
                "artists": [{"name": "B"}],
                "album": {"name": "Two", "picUrl": "https://example.test/cover.jpg"},
            }
        )

        self.assertIsNotNone(track)
        self.assertEqual(track.album_art_url, "https://example.test/cover.jpg")

    def test_upcoming_tracks_from_queue_returns_multiple_future_tracks(self):
        with tempfile.TemporaryDirectory() as root:
            queue = Path(root) / "playingList"
            queue.write_text(
                """
                {
                  "list": [
                    {"name": "Current", "duration": 180000, "artists": [{"name": "A"}], "album": {"name": "One"}},
                    {"name": "Next", "duration": 201000, "artists": [{"name": "B"}], "album": {"name": "Two", "picUrl": "https://example.test/next.jpg"}},
                    {"name": "Third", "duration": 202000, "artists": [{"name": "C"}], "album": {"name": "Three", "picUrl": "https://example.test/third.jpg"}}
                  ]
                }
                """,
                encoding="utf-8",
            )
            reader = NeteasePositionReader(queue_paths=(queue,))

            tracks = reader.upcoming_tracks("Current", "A", 2)

        self.assertEqual([track.title for track in tracks], ["Next", "Third"])
        self.assertEqual([track.album_art_url for track in tracks], ["https://example.test/next.jpg", "https://example.test/third.jpg"])

    def test_current_track_reads_visible_window_title(self):
        reader = NeteasePositionReader()

        class FakeInfo:
            class_name = "OrpheusBrowserHost"
            process_id = 2924
            process_name = "cloudmusic.exe"

        class FakeWindow:
            element_info = FakeInfo()

            def window_text(self):
                return "Press Play - MAETA"

        class FakeDesktop:
            def windows(self):
                return [FakeWindow()]

        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": FakeDesktop())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            track = reader.current_track()
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertIsNotNone(track)
        self.assertEqual(track.title, "Press Play")
        self.assertEqual(track.artist, "MAETA")

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

    def test_progress_image_fallback_estimates_netease_red_bottom_line(self):
        reader = NeteasePositionReader()
        image = Image.new("RGB", (1000, 600), (247, 249, 252))
        y = 538
        for x in range(0, 371):
            image.putpixel((x, y), (246, 112, 126))
            image.putpixel((x, y + 1), (246, 112, 126))
        for x in range(371, 1000):
            image.putpixel((x, y), (231, 232, 234))
            image.putpixel((x, y + 1), (231, 232, 234))
        for yy in range(y + 2, 600):
            for x in range(0, 1000):
                image.putpixel((x, yy), (240, 242, 246))
        for x in range(70, 122):
            for yy in range(560, 592):
                image.putpixel((x, yy), (42, 38, 40))
        for x in range(488, 536):
            for yy in range(560, 592):
                image.putpixel((x, yy), (218, 45, 70))

        fraction = reader._progress_fraction_from_image(image)

        self.assertIsNotNone(fraction)
        self.assertAlmostEqual(fraction, 0.37, delta=0.02)

    def test_progress_image_fallback_ignores_hover_preview_after_white_thumb(self):
        reader = NeteasePositionReader()
        image = Image.new("RGB", (1000, 600), (247, 249, 252))
        y = 538
        for x in range(0, 821):
            image.putpixel((x, y), (246, 112, 126))
            image.putpixel((x, y + 1), (246, 112, 126))
        for x in range(821, 1000):
            image.putpixel((x, y), (231, 232, 234))
            image.putpixel((x, y + 1), (231, 232, 234))
        for x in range(236, 245):
            image.putpixel((x, y), (255, 255, 255))
            image.putpixel((x, y + 1), (255, 255, 255))
        for yy in range(y + 2, 600):
            for x in range(0, 1000):
                image.putpixel((x, yy), (240, 242, 246))
        for x in range(70, 122):
            for yy in range(560, 592):
                image.putpixel((x, yy), (42, 38, 40))

        fraction = reader._progress_fraction_from_image(image)

        self.assertIsNotNone(fraction)
        self.assertAlmostEqual(fraction, 0.24, delta=0.02)

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

    def test_interactive_refresh_does_not_capture_image_when_controls_are_hidden(self):
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

        self.assertEqual(calls, ["progress"])
        self.assertEqual(reader.diagnostics()["last_source"], "")
        self.assertEqual(reader.diagnostics()["last_confidence"], 0.0)

    def test_forced_refresh_uses_image_progress_when_controls_are_hidden(self):
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

        reader._refresh_sample("song\x00artist", "Song", "Artist", (TimedLyricLine(120.0, "line"),), 240.0, force_progress_image=True)

        self.assertEqual(calls, ["progress", "image-progress"])
        self.assertEqual(reader.diagnostics()["last_source"], "netease-uia-progress")

    def test_visible_progress_image_uses_window_capture_without_printwindow(self):
        reader = NeteasePositionReader()
        progress_image = Image.new("RGB", (1000, 600), (247, 249, 252))
        y = 538
        for x in range(0, 501):
            progress_image.putpixel((x, y), (246, 112, 126))
            progress_image.putpixel((x, y + 1), (246, 112, 126))
        for x in range(501, 1000):
            progress_image.putpixel((x, y), (231, 232, 234))
            progress_image.putpixel((x, y + 1), (231, 232, 234))
        calls = []

        class FakeWindow:
            def capture_as_image(self):
                calls.append("screen")
                return progress_image

        reader._netease_windows = lambda _desktop, _title, require_title=False: [FakeWindow()]
        reader._capture_window_image_offscreen = lambda _window: (_ for _ in ()).throw(AssertionError("PrintWindow should not run by default"))
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            sample = reader._position_from_visible_progress_image("Song", 240.0)
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertIsNotNone(sample)
        self.assertEqual(calls, ["screen"])
        self.assertAlmostEqual(sample.position_seconds, 120.0, delta=6.0)

    def test_progress_point_maps_bottom_progress_bar(self):
        reader = NeteasePositionReader()

        class FakeRect:
            left = 100
            top = 100
            right = 1100
            bottom = 960

        class FakeWindow:
            def rectangle(self):
                return FakeRect()

        reader._netease_windows = lambda _desktop, _title, require_title=False: [FakeWindow()]
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            position = reader.position_from_progress_point((700, 868), 240.0)
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertIsNotNone(position)
        self.assertAlmostEqual(position, 144.0, delta=1.0)

    def test_progress_point_ignores_bottom_controls(self):
        reader = NeteasePositionReader()

        class FakeRect:
            left = 100
            top = 100
            right = 1100
            bottom = 960

        class FakeWindow:
            def rectangle(self):
                return FakeRect()

        reader._netease_windows = lambda _desktop, _title, require_title=False: [FakeWindow()]
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            position = reader.position_from_progress_point((700, 916), 240.0)
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertIsNone(position)

    def test_interactive_refresh_keeps_text_progress_without_image_capture(self):
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

        self.assertEqual(calls, ["progress"])
        self.assertEqual(reader.diagnostics()["last_source"], "netease-uia-progress")
        self.assertEqual(reader._last_sample.position_seconds, 54.0)

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
        reader._visible_lyric_rows = lambda _desktop, _title, require_title=True: list(rows)
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
        self.assertEqual(sample.position_seconds, 160.0)

    def test_visible_lyrics_click_does_not_require_window_title_match(self):
        reader = NeteasePositionReader()
        calls = []
        lines = (
            TimedLyricLine(96.0, "before"),
            TimedLyricLine(160.0, "target"),
            TimedLyricLine(166.0, "after"),
        )
        rows = (
            {
                "left": 180.0,
                "right": 520.0,
                "top": 154.0,
                "bottom": 186.0,
                "container_left": 100.0,
                "container_right": 900.0,
                "container_top": 80.0,
                "container_bottom": 420.0,
                "texts": ("target",),
                "max_text_height": 18.0,
            },
        )

        def visible_rows(_desktop, _title, require_title=True):
            calls.append(require_title)
            return list(rows)

        reader._visible_lyric_rows = visible_rows
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            sample = reader._position_from_visible_lyrics("Different Window Title", "Artist", lines, 240.0, click_pos=(780, 168))
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertIsNotNone(sample)
        self.assertEqual(calls, [False])
        self.assertEqual(sample.lyric_index, 1)
        self.assertEqual(sample.position_seconds, 160.0)

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

    def test_visible_lyrics_click_uses_geometry_before_slow_row_scan(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(10.0, "first"),
            TimedLyricLine(20.0, "second"),
            TimedLyricLine(30.0, "third"),
            TimedLyricLine(40.0, "fourth"),
        )
        reader._last_sample = NeteasePositionSample(21.0, 120.0, "netease-uia-progress", 0.74, sampled_at=100.0)
        reader._visible_lyric_rows = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("slow row scan should be skipped"))

        class FakeWindow:
            def rectangle(self):
                return SimpleNamespace(left=0, top=0, right=1000, bottom=800)

        reader._netease_windows = lambda _desktop, _title, require_title=False: [FakeWindow()]
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            sample = reader._position_from_visible_lyrics("Song", "Artist", lines, 120.0, click_pos=(500, 500))
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertIsNotNone(sample)
        self.assertEqual(sample.source, "netease-uia-lyrics")
        self.assertEqual(sample.confidence, 0.62)
        self.assertEqual(sample.lyric_index, 2)

    def test_visible_lyrics_click_falls_back_to_row_scan_when_geometry_misses(self):
        reader = NeteasePositionReader()
        calls = []
        lines = (
            TimedLyricLine(10.0, "first"),
            TimedLyricLine(20.0, "target"),
            TimedLyricLine(30.0, "third"),
        )
        rows = (
            {
                "left": 180.0,
                "right": 520.0,
                "top": 154.0,
                "bottom": 186.0,
                "container_left": 100.0,
                "container_right": 900.0,
                "container_top": 80.0,
                "container_bottom": 420.0,
                "texts": ("target",),
                "max_text_height": 18.0,
            },
        )
        reader._last_sample = None
        reader._netease_windows = lambda *_args, **_kwargs: []

        def visible_rows(_desktop, _title, require_title=True):
            calls.append(require_title)
            return list(rows)

        reader._visible_lyric_rows = visible_rows
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            sample = reader._position_from_visible_lyrics("Song", "Artist", lines, 120.0, click_pos=(260, 168))
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertIsNotNone(sample)
        self.assertEqual(calls, [False])
        self.assertEqual(sample.lyric_index, 1)

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

    def test_read_immediate_force_progress_image_refreshes_while_background_read_is_running(self):
        reader = NeteasePositionReader()
        calls = []

        def progress(_title, _duration):
            calls.append("progress")
            return None

        def progress_image(_title, _duration):
            calls.append("image")
            return NeteasePositionSample(172.0, 240.0, "netease-uia-progress", 0.90, sampled_at=100.0)

        reader._position_from_visible_progress = progress
        reader._position_from_visible_progress_image = progress_image
        reader._last_track_key = "song\x00artist"
        reader._last_sample = NeteasePositionSample(96.0, 240.0, "netease-uia-progress", 0.92, sampled_at=99.0)
        reader._refreshing = True

        sample = reader.read_immediate("Song", "Artist", (), 240.0, force_progress_image=True)

        self.assertIsNotNone(sample)
        self.assertEqual(sample.position_seconds, 172.0)
        self.assertEqual(calls, ["progress", "image"])
        self.assertFalse(reader.diagnostics()["refreshing"])

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
