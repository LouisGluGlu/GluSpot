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

    def test_lightweight_reader_has_no_forced_anchor_interfaces(self):
        reader = NeteasePositionReader()

        for name in (
            "read_immediate",
            "read_progress_immediate",
            "lyric_click_point_hits_visible_row",
            "_position_from_visible_progress_image",
            "_position_from_progress_image",
            "_progress_fraction_from_image",
            "_position_from_click_geometry",
            "_point_may_hit_player_content_rect",
        ):
            self.assertFalse(hasattr(reader, name), name)

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

    def test_progress_text_pair_uses_matching_duration(self):
        reader = NeteasePositionReader()

        position, duration = reader._progress_from_texts(("01:12", "03:30"), 210.0)

        self.assertEqual(position, 72.0)
        self.assertEqual(duration, 210.0)

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

    def test_visible_progress_reuses_one_text_snapshot_for_text_and_time_controls(self):
        reader = NeteasePositionReader()

        class FakeRect:
            left = 0
            top = 0
            right = 1000
            bottom = 1000

        class FakeTextControl:
            def __init__(self, text: str, left: int, top: int, right: int, bottom: int):
                self._text = text
                self._rect = SimpleNamespace(left=left, top=top, right=right, bottom=bottom)

            def window_text(self):
                return self._text

            def rectangle(self):
                return self._rect

        class FakeWindow:
            def __init__(self):
                self.descendant_calls = 0
                self._controls = (
                    FakeTextControl("Song", 20, 20, 120, 40),
                    FakeTextControl("01:12", 100, 910, 150, 930),
                    FakeTextControl("03:30", 760, 910, 820, 930),
                )

            def descendants(self, control_type=None):
                self.descendant_calls += 1
                self.requested_type = control_type
                return list(self._controls)

            def rectangle(self):
                return FakeRect()

        window = FakeWindow()
        reader._netease_windows = lambda _desktop, _title, require_title=False: [window]

        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            sample = reader._position_from_visible_progress("Song", 210.0)
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertIsNotNone(sample)
        self.assertAlmostEqual(sample.position_seconds, 72.0)
        self.assertEqual(window.descendant_calls, 1)

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

        self.assertEqual(calls, ["progress", "lyrics"])
        self.assertEqual(reader.diagnostics()["last_source"], "netease-uia-lyrics")

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

    def test_read_without_timed_lyrics_does_not_refresh_uia_progress(self):
        reader = NeteasePositionReader()
        calls = []

        def refresh(*_args, **_kwargs):
            calls.append("refresh")

        reader._refresh_sample = refresh
        reader._last_track_key = "song\x00artist"

        sample = reader.read("Song", "Artist", (), 240.0, interactive=True)

        self.assertIsNone(sample)
        self.assertEqual(calls, [])
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
