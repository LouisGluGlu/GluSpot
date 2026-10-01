from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_track_metadata_uses_local_queue_when_player_window_is_hidden(self):
        payload = {
            "tracks": [
                {
                    "name": "Hidden Song",
                    "duration": 201000,
                    "artists": [{"name": "Artist"}],
                    "album": {"name": "Album", "picUrl": "https://example.test/original.jpg"},
                }
            ]
        }
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "playingList"
            path.write_text(json.dumps(payload), encoding="utf-8")
            reader = NeteasePositionReader(queue_paths=(path,))

            track = reader.track_metadata("Hidden Song", "Artist")

        self.assertIsNotNone(track)
        self.assertEqual(track.album_art_url, "https://example.test/original.jpg")

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

    def test_queue_snapshot_is_shared_and_invalidated_when_playlist_changes(self):
        with tempfile.TemporaryDirectory() as root:
            queue = Path(root) / "playingList"
            first = {
                "list": [
                    {"name": "Current", "duration": 180000, "artists": [{"name": "A"}]},
                    {"name": "Next", "duration": 201000, "artists": [{"name": "B"}]},
                ]
            }
            queue.write_text(json.dumps(first), encoding="utf-8")
            reader = NeteasePositionReader(queue_paths=(queue,))
            read_text = Path.read_text
            reads = []

            def counted_read(path, *args, **kwargs):
                reads.append(path)
                return read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", counted_read):
                self.assertEqual(reader.track_metadata("Current", "A").title, "Current")
                self.assertEqual(reader.duration_for_track("Current", "A"), 180.0)
                self.assertEqual(reader.adjacent_track("Current", "A", "next").title, "Next")
                self.assertEqual([track.title for track in reader.upcoming_tracks("Current", "A", 2)], ["Next"])
                self.assertEqual(reads, [queue])

                changed = {
                    "list": [
                        {"name": "Current", "duration": 180000, "artists": [{"name": "A"}]},
                        {"name": "Next Version", "duration": 203000, "artists": [{"name": "B"}]},
                    ]
                }
                queue.write_text(json.dumps(changed), encoding="utf-8")
                self.assertEqual(reader.adjacent_track("Current", "A", "next").title, "Next Version")
                self.assertEqual(reads, [queue, queue])

    def test_queue_snapshot_detects_same_size_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            queue = Path(root) / "playingList"
            first = '{"list":[{"name":"Current","artists":"A"},{"name":"Next","artists":"B"}]}'
            second = first.replace('"Next"', '"Last"')
            self.assertEqual(len(first), len(second))
            queue.write_text(first, encoding="utf-8")
            reader = NeteasePositionReader(queue_paths=(queue,))
            self.assertEqual(reader.adjacent_track("Current", "A", "next").title, "Next")
            old = queue.stat()
            replacement = queue.with_suffix(".new")
            replacement.write_text(second, encoding="utf-8")
            os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
            os.replace(replacement, queue)

            self.assertEqual(reader.adjacent_track("Current", "A", "next").title, "Last")

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
            def windows(self, **_filters):
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

    def test_window_enumeration_includes_hidden_top_level_player_safely(self):
        reader = NeteasePositionReader()

        class FakeWindow:
            def __init__(self, class_name, title, process_name, visible=False):
                self.element_info = SimpleNamespace(
                    class_name=class_name, process_id=234, process_name=process_name
                )
                self.title = title
                self.visible = visible

            def window_text(self):
                return self.title

            def is_visible(self):
                return self.visible

        hidden_player = FakeWindow("OrpheusBrowserHost", "Song - Artist", "cloudmusic.exe")
        unrelated = FakeWindow("Chrome_WidgetWin_1", "Song - Artist", "browser.exe")
        stale = FakeWindow("OrpheusBrowserHost", "Other - Artist", "cloudmusic.exe")
        partial_title = FakeWindow("OrpheusBrowserHost", "Song Again - Artist", "cloudmusic.exe")

        class BrokenWindow:
            @property
            def element_info(self):
                raise RuntimeError("window closed during enumeration")

        class FakeDesktop:
            filters = None

            def windows(self, **filters):
                self.filters = filters
                return [BrokenWindow(), unrelated, hidden_player, stale, partial_title]

        desktop = FakeDesktop()
        windows = reader._netease_windows(desktop, "Song", require_title=True)

        self.assertEqual(
            desktop.filters,
            {"top_level_only": True, "visible_only": False, "enabled_only": False},
        )
        self.assertEqual(windows, [hidden_player])
        self.assertFalse(hidden_player.is_visible())

    def test_hidden_native_handle_falls_back_to_uia_wrapper_only_for_cloudmusic(self):
        reader = NeteasePositionReader()
        requested_handles = []

        class FakeWindow:
            element_info = SimpleNamespace(
                class_name="OrpheusBrowserHost", process_id=29060, process_name="cloudmusic.exe"
            )

            def window_text(self):
                return "Song - Artist"

        class FakeDesktop:
            def windows(self, **_filters):
                return []

            def window(self, *, handle):
                requested_handles.append(handle)
                return SimpleNamespace(wrapper_object=lambda: FakeWindow())

        fake_gui = types.SimpleNamespace(
            IsWindowVisible=lambda _handle: False,
            GetClassName=lambda handle: "OrpheusBrowserHost" if handle != 11 else "Browser",
            EnumWindows=lambda callback, arg: [callback(handle, arg) for handle in (10, 11, 12)],
        )
        fake_process = types.SimpleNamespace(
            GetWindowThreadProcessId=lambda handle: (1, {10: 29060, 11: 29060, 12: 9876}[handle])
        )
        reader._native_process_executable = lambda pid: "cloudmusic.exe" if pid == 29060 else "other.exe"

        with patch.dict(sys.modules, {"win32gui": fake_gui, "win32process": fake_process}):
            windows = reader._netease_windows(FakeDesktop(), "Song", require_title=True)

        self.assertEqual(len(windows), 1)
        self.assertEqual(requested_handles, [10])

    def test_hidden_native_player_reveal_restores_visibility_and_foreground(self):
        reader = NeteasePositionReader()
        reader._native_process_executable = lambda pid: "cloudmusic.exe" if pid == 29060 else ""
        state = {"visible": False, "minimized": False, "foreground": 99}
        events = []

        class FakeWindow:
            handle = 10
            element_info = SimpleNamespace(class_name="OrpheusBrowserHost", process_id=29060)

            def window_text(self):
                return "Song - Artist"

        window = FakeWindow()
        foreground_window = SimpleNamespace(set_focus=lambda: set_foreground(99))
        fake_con = types.SimpleNamespace(SW_SHOW=5, SW_RESTORE=9, SW_MINIMIZE=6, SW_HIDE=0)

        def show_window(handle, command):
            events.append(("show", handle, command))
            state["visible"] = command != fake_con.SW_HIDE
            state["minimized"] = command == fake_con.SW_MINIMIZE

        def set_foreground(handle):
            events.append(("foreground", handle))
            state["foreground"] = handle

        fake_gui = types.SimpleNamespace(
            IsWindow=lambda handle: handle in (10, 99),
            IsWindowVisible=lambda _handle: state["visible"],
            IsIconic=lambda _handle: state["minimized"],
            GetForegroundWindow=lambda: state["foreground"],
            GetClassName=lambda _handle: "OrpheusBrowserHost",
            GetWindowText=lambda _handle: "Song - Artist",
            ShowWindow=show_window,
            SetForegroundWindow=set_foreground,
        )
        fake_process = types.SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 29060))
        fake_desktop = types.SimpleNamespace(
            window=lambda *, handle: SimpleNamespace(
                wrapper_object=lambda: window if handle == 10 else foreground_window
            )
        )
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": fake_desktop)

        with patch.dict(
            sys.modules,
            {"win32con": fake_con, "win32gui": fake_gui, "win32process": fake_process, "pywinauto": fake_pywinauto},
        ):
            with reader._temporarily_reveal_player_window(window, "Song", "Artist") as revealed:
                self.assertIs(revealed, window)
                self.assertTrue(state["visible"])
                state["foreground"] = 10

        self.assertFalse(state["visible"])
        self.assertFalse(state["minimized"])
        self.assertEqual(state["foreground"], 99)
        self.assertEqual(events, [("show", 10, 5), ("show", 10, 0), ("foreground", 99)])

    def test_hidden_player_range_value_seek_does_not_use_mouse(self):
        reader = NeteasePositionReader()
        calls = []

        class FakeRange:
            CurrentMinimum = 0.0
            CurrentMaximum = 100.0
            CurrentIsReadOnly = False
            CurrentValue = 0.0

            def SetValue(self, value):
                self.CurrentValue = value
                calls.append(("range", value))

        class FakeControl:
            iface_range_value = FakeRange()

            def rectangle(self):
                return SimpleNamespace(left=180, top=850, right=1020, bottom=870)

        class FakeWindow:
            element_info = SimpleNamespace(
                class_name="OrpheusBrowserHost", process_id=234, process_name="cloudmusic.exe"
            )

            def window_text(self):
                return "Song - Artist"

            def is_visible(self):
                return False

            def rectangle(self):
                return SimpleNamespace(left=100, top=100, right=1100, bottom=960)

            def descendants(self, control_type=None):
                return [FakeControl()] if control_type == "Slider" else []

            def set_focus(self):
                raise AssertionError("hidden writable range must not focus the player")

            def click_input(self, **_kwargs):
                raise AssertionError("hidden writable range must not click")

        class FakeDesktop:
            def windows(self, **_filters):
                return [FakeWindow()]

        reader.verify_playback_position = lambda *_args, **_kwargs: bool(calls)
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": FakeDesktop())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            result = reader.seek_playback_position(120.0, 240.0, title="Song", artist="Artist")
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertTrue(result)
        self.assertEqual(calls, [("range", 50.0)])

    def test_range_value_readback_alone_does_not_confirm_real_seek(self):
        reader = NeteasePositionReader()
        calls = []

        class FakeRange:
            CurrentMinimum = 0.0
            CurrentMaximum = 100.0
            CurrentIsReadOnly = False
            CurrentValue = 0.0

            def SetValue(self, value):
                self.CurrentValue = value
                calls.append(value)

        class FakeControl:
            iface_range_value = FakeRange()

            def rectangle(self):
                return SimpleNamespace(left=180, top=850, right=1020, bottom=870)

        class FakeWindow:
            def window_text(self):
                return "Song - Artist"

            def rectangle(self):
                return SimpleNamespace(left=100, top=100, right=1100, bottom=960)

            def descendants(self, control_type=None):
                return [FakeControl()] if control_type == "Slider" else []

        reader._netease_windows = lambda _desktop, _title, require_title=False: [FakeWindow()]
        reader.verify_playback_position = lambda *_args, **_kwargs: False
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            result = reader.seek_playback_position(120.0, 240.0, title="Song", artist="Artist")
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertFalse(result)
        self.assertEqual(calls, [50.0])

    def test_seek_write_a_cannot_use_same_track_progress_from_window_b(self):
        reader = NeteasePositionReader()
        writes = []

        class FakeRange:
            CurrentMinimum = 0.0
            CurrentMaximum = 100.0
            CurrentIsReadOnly = False
            CurrentValue = 0.0

            def SetValue(self, value):
                self.CurrentValue = value
                writes.append(value)

        class FakeControl:
            iface_range_value = FakeRange()

            def rectangle(self):
                return SimpleNamespace(left=180, top=850, right=1020, bottom=870)

        class FakeText:
            def window_text(self):
                return "02:00 / 04:00"

            def rectangle(self):
                return SimpleNamespace(left=200, top=850, right=320, bottom=875)

        class WindowA:
            def window_text(self):
                return "Song - Artist"

            def rectangle(self):
                return SimpleNamespace(left=100, top=100, right=1100, bottom=960)

            def descendants(self, control_type=None):
                if control_type == "Slider":
                    return [FakeControl()]
                return []

        class WindowB:
            def window_text(self):
                return "Song - Artist"

            def is_visible(self):
                return False

            def rectangle(self):
                return SimpleNamespace(left=1200, top=100, right=2200, bottom=960)

            def descendants(self, control_type=None):
                return [FakeText()] if control_type == "Text" else []

        window_a = WindowA()
        window_b = WindowB()
        reader._netease_windows = lambda _desktop, _title, require_title=False: [window_a, window_b]
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            result = reader.seek_playback_position(120.0, 240.0, title="Song", artist="Artist")
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertFalse(result)
        self.assertEqual(writes, [50.0])
        self.assertFalse(reader._last_progress_probe["matched"])

    def test_seek_rejects_native_window_with_untrusted_process(self):
        reader = NeteasePositionReader()
        calls = []

        class FakeWindow:
            handle = 101
            element_info = SimpleNamespace(process_id=987, class_name="OrpheusBrowserHost")

            def window_text(self):
                return "Song - Artist"

        fake_gui = types.SimpleNamespace(
            IsWindow=lambda _handle: True,
            GetClassName=lambda _handle: "OrpheusBrowserHost",
            GetWindowText=lambda _handle: "Song - Artist",
        )
        fake_process = types.SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 987))
        reader._native_process_executable = lambda _pid: "other.exe"
        reader._netease_windows = lambda _desktop, _title, require_title=False: [FakeWindow()]
        reader._progress_seek_controls = lambda _window: calls.append("range") or []
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        with patch.dict(sys.modules, {"win32gui": fake_gui, "win32process": fake_process, "pywinauto": fake_pywinauto}):
            result = reader.seek_playback_position(80.0, 200.0, title="Song", artist="Artist")

        self.assertFalse(result)
        self.assertEqual(calls, [])

    def test_qq_seek_window_requires_qqmusic_process_identity(self):
        reader = QQPositionReader()

        class FakeWindow:
            handle = 202
            element_info = SimpleNamespace(process_id=456, class_name="QQMusicMainWindow")

            def window_text(self):
                return "Song - Artist"

        window = FakeWindow()
        fake_gui = types.SimpleNamespace(
            IsWindow=lambda _handle: True,
            GetClassName=lambda _handle: "QQMusicMainWindow",
            GetWindowText=lambda _handle: "Song - Artist",
        )
        fake_process = types.SimpleNamespace(GetWindowThreadProcessId=lambda _handle: (1, 456))

        with patch.dict(sys.modules, {"win32gui": fake_gui, "win32process": fake_process}):
            reader._native_process_executable = lambda _pid: "other.exe"
            self.assertFalse(reader._seek_window_matches_track(window, "Song", "Artist"))
            reader._native_process_executable = lambda _pid: "qqmusic.exe"
            self.assertTrue(reader._seek_window_matches_track(window, "Song", "Artist"))

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

    def test_duplicate_visible_lyric_uses_occurrence_nearest_previous_position(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(8.0, "same chorus"),
            TimedLyricLine(12.0, "verse"),
            TimedLyricLine(68.0, "same chorus"),
        )
        reader._last_sample = NeteasePositionSample(
            66.8, 120.0, "netease-uia-progress", 0.92, sampled_at=100.0
        )

        index = reader._nearest_visible_lyric_index(
            reader._lyric_indices_map(lines)["samechorus"], lines, reader._last_sample
        )

        self.assertEqual(index, 2)

    def test_repeated_text_clock_alone_cannot_advance_occurrence(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(10.0, "repeat"), TimedLyricLine(13.0, "repeat"),
            TimedLyricLine(16.0, "repeat"), TimedLyricLine(19.0, "different"),
        )
        previous = NeteasePositionSample(
            11.93, 30.0, "netease-uia-lyrics", 0.86, 0, "repeat", 100.0, True
        )
        reader._last_sample = previous
        row = {"top": 545, "bottom": 605, "container_left": 1, "container_right": 2,
               "container_top": 442, "container_bottom": 876,
               "texts": ("repeat",), "max_text_height": 29}
        reader._visible_lyric_rows = lambda *_args, **_kwargs: [row]
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        with patch.dict(sys.modules, {"pywinauto": fake_pywinauto}), patch(
            "api.netease_position.time.monotonic", return_value=100.55
        ):
            sample = reader._position_from_visible_lyrics("Song", "Artist", lines, 30.0)

        self.assertEqual(sample.lyric_index, 0)

    def test_repeated_visible_rows_use_distinct_neighbour_to_identify_occurrence(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(10.0, "repeat"), TimedLyricLine(13.0, "repeat"),
            TimedLyricLine(16.0, "repeat"), TimedLyricLine(19.0, "unique ending"),
        )
        previous = NeteasePositionSample(
            11.93, 30.0, "netease-uia-lyrics", 0.86, 0, "repeat", 100.0, True
        )
        indexed = reader._lyric_indices_map(lines)
        group = {"container_left": 1, "container_right": 2,
                 "container_top": 442, "container_bottom": 876}
        rows = [
            (dict(group, texts=("repeat",)), "repeat", indexed["repeat"]),
            (dict(group, texts=("repeat",)), "repeat", indexed["repeat"]),
            (dict(group, texts=("repeat",)), "repeat", indexed["repeat"]),
            (dict(group, texts=("unique ending",)), "unique ending", indexed["uniqueending"]),
        ]

        self.assertEqual(reader._contextual_visible_lyric_index(1, rows, lines, previous), 1)
        self.assertEqual(reader._contextual_visible_lyric_index(2, rows, lines, previous), 2)

    def test_identical_chorus_context_stays_on_previous_occurrence(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(10.0, "A"), TimedLyricLine(12.0, "same"),
            TimedLyricLine(14.0, "B"), TimedLyricLine(60.0, "A"),
            TimedLyricLine(62.0, "same"), TimedLyricLine(64.0, "B"),
        )
        previous = NeteasePositionSample(
            12.9, 90.0, "netease-uia-lyrics", 0.86, 1, "same", 100.0, True
        )
        indexed = reader._lyric_indices_map(lines)
        group = {"container_left": 1, "container_right": 2,
                 "container_top": 442, "container_bottom": 876}
        rows = [
            (dict(group, texts=(text,)), text, indexed[reader._normalize_text(text)])
            for text in ("A", "same", "B")
        ]

        self.assertEqual(reader._contextual_visible_lyric_index(1, rows, lines, previous), 1)

    def test_translation_at_same_timestamp_does_not_bias_context_gap(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(10.0, "A"), TimedLyricLine(10.0, "翻译"),
            TimedLyricLine(12.0, "B"), TimedLyricLine(60.0, "A"),
            TimedLyricLine(62.0, "B"),
        )
        previous = NeteasePositionSample(
            10.5, 90.0, "netease-uia-progress", 0.92, sampled_at=100.0
        )
        indexed = reader._lyric_indices_map(lines)
        group = {"container_left": 1, "container_right": 2,
                 "container_top": 442, "container_bottom": 876}
        rows = [
            (dict(group, texts=("A", "翻译")), "A", indexed["a"]),
            (dict(group, texts=("B",)), "B", indexed["b"]),
        ]

        self.assertEqual(reader._contextual_visible_lyric_index(0, rows, lines, previous), 0)

    def test_transient_lyric_fallback_keeps_recent_precise_progress(self):
        reader = NeteasePositionReader()
        progress = NeteasePositionSample(
            66.0, 120.0, "netease-uia-progress", 0.92, sampled_at=100.0
        )
        fallback = NeteasePositionSample(
            8.8, 120.0, "netease-uia-lyrics", 0.86, 0, "same chorus", 100.6
        )

        self.assertIs(reader._prefer_recent_progress_sample(progress, fallback), progress)

    def test_distant_lyric_fallback_after_progress_expiry_requires_confirmation(self):
        reader = NeteasePositionReader()
        progress = NeteasePositionSample(
            66.0, 120.0, "netease-uia-progress", 0.92, sampled_at=100.0
        )
        first = NeteasePositionSample(
            8.8, 120.0, "netease-uia-lyrics", 0.86, 0, "same chorus", 101.6
        )
        second = NeteasePositionSample(
            8.8, 120.0, "netease-uia-lyrics", 0.86, 0, "same chorus", 102.0
        )
        confirmed = NeteasePositionSample(
            8.8, 120.0, "netease-uia-lyrics", 0.86, 0, "same chorus", 102.5
        )

        self.assertIs(reader._debounce_visible_line_jump(progress, first), progress)
        self.assertIs(reader._debounce_visible_line_jump(progress, second), progress)
        self.assertIs(reader._debounce_visible_line_jump(progress, confirmed), confirmed)

    def test_new_precise_progress_clears_pending_coarse_lyric_jump(self):
        reader = NeteasePositionReader()
        reader._last_track_key = "song\x00artist"
        reader._pending_lyric_index = 0
        reader._pending_lyric_index_since = 100.0
        reader._position_from_visible_progress = lambda *_args: NeteasePositionSample(
            66.0, 120.0, "netease-uia-progress", 0.92, sampled_at=101.0
        )

        reader._refresh_sample("song\x00artist", "Song", "Artist", (), 120.0)

        self.assertEqual(reader._pending_lyric_index, -1)
        self.assertEqual(reader._pending_lyric_index_since, 0.0)

    def test_empty_scan_keeps_precise_source_and_age_before_lyric_fallback(self):
        reader = NeteasePositionReader()
        reader._last_track_key = "song\x00artist"
        exact = NeteasePositionSample(
            66.0, 120.0, "netease-uia-progress", 0.92, sampled_at=100.0
        )
        reader._last_sample = exact
        reader._position_from_visible_progress = lambda *_args: None
        reader._position_from_visible_lyrics = lambda *_args: None

        reader._refresh_sample("song\x00artist", "Song", "Artist", (), 120.0)

        retained = reader._last_sample
        self.assertEqual(retained.source, "netease-uia-progress")
        self.assertEqual(retained.sampled_at, 100.0)
        wrong_lyric = NeteasePositionSample(
            8.8, 120.0, "netease-uia-lyrics", 0.86, 0, "same chorus", 100.6
        )
        self.assertIs(reader._prefer_recent_progress_sample(retained, wrong_lyric), retained)

    def test_track_reader_generation_never_reuses_token_after_a_b_a(self):
        reader = NeteasePositionReader()
        with patch.object(reader, "_refresh_sample"):
            with patch("api.netease_position.time.monotonic", side_effect=(10.0, 11.0, 12.0)):
                reader.read("A", "Artist", (TimedLyricLine(0.0, "one"),), 100.0)
                token_a = reader._refresh_token
                reader.read("B", "Artist", (TimedLyricLine(0.0, "two"),), 100.0)
                token_b = reader._refresh_token
                reader.read("A", "Artist", (TimedLyricLine(0.0, "one"),), 100.0)
                token_a_again = reader._refresh_token

        self.assertLess(token_a, token_b)
        self.assertLess(token_b, token_a_again)

    def test_same_title_new_version_clears_cached_position_generation(self):
        reader = NeteasePositionReader()
        first = (TimedLyricLine(0.0, "opening"), TimedLyricLine(30.0, "ending"))
        alternate = (TimedLyricLine(0.0, "different opening"), TimedLyricLine(30.0, "ending"))
        with patch.object(reader, "_refresh_sample"):
            reader.read("Song", "Artist", first, 120.0)
            first_token = reader._refresh_token
            reader._last_sample = NeteasePositionSample(90.0, 120.0, "netease-uia-progress", 0.92, sampled_at=1.0)
            self.assertIsNone(reader.read("Song", "Artist", alternate, 120.0))
            second_token = reader._refresh_token
            reader._last_sample = NeteasePositionSample(90.0, 120.0, "netease-uia-progress", 0.92, sampled_at=1.0)
            self.assertIsNone(reader.read("Song", "Artist", alternate, 180.0))
        self.assertLess(first_token, second_token)
        self.assertLess(second_token, reader._refresh_token)

    def test_same_track_duration_jitter_keeps_cached_position(self):
        reader = NeteasePositionReader()
        lines = (TimedLyricLine(0.0, "opening"),)
        with patch.object(reader, "_refresh_sample"):
            reader.read("Song", "Artist", lines, 212.79)
            token = reader._refresh_token
            cached = NeteasePositionSample(90.0, 212.79, "netease-uia-progress", 0.92, sampled_at=1.0)
            reader._last_sample = cached
            self.assertIs(reader.read("Song", "Artist", lines, 212.80), cached)
        self.assertEqual(reader._refresh_token, token)

    def test_translation_enrichment_keeps_clock_and_remaps_raw_source_indexes(self):
        reader = NeteasePositionReader()
        lines = (TimedLyricLine(10.0, "current"), TimedLyricLine(20.0, "later"))
        enriched = (TimedLyricLine(10.0, "current"), TimedLyricLine(10.0, "翻译"),
                    TimedLyricLine(20.0, "later\n后句翻译"))
        with patch("api.netease_position.threading.Thread"):
            reader.read("Song", "Artist", lines, 100.0)
            previous = NeteasePositionSample(21.4, 100.0, "netease-uia-lyrics", 0.86, 1, "later", 100.0, True)
            reader._last_sample = previous
            reader._pending_lyric_index = 1
            reader._pending_lyric_index_since = 100.2
            reader._pending_lyric_index_last_seen = 100.5
            old_token = reader._refresh_token
            enriched_sample = reader.read("Song", "Artist", enriched, 100.0)

        self.assertEqual(enriched_sample.position_seconds, previous.position_seconds)
        self.assertEqual(enriched_sample.sampled_at, previous.sampled_at)
        self.assertEqual(enriched_sample.lyric_index, 2)
        self.assertEqual(reader._pending_lyric_index, 2)
        self.assertEqual(reader._pending_lyric_index_since, 100.2)
        self.assertEqual(reader._pending_lyric_index_last_seen, 100.5)
        self.assertGreater(reader._refresh_token, old_token)

    def test_merged_translation_subrows_match_original_raw_source_index(self):
        reader = NeteasePositionReader()
        lines = (TimedLyricLine(10.0, "current\n翻译"), TimedLyricLine(20.0, "later\n后句翻译"))
        indexed = reader._lyric_indices_map(lines)
        self.assertEqual(indexed["current"], (0,))
        self.assertEqual(indexed["翻译"], (0,))
        self.assertEqual(indexed["later"], (1,))
        self.assertEqual(reader._row_match_text(("current", "翻译"), indexed), "current")

    def test_removed_companion_remaps_to_its_timestamp_not_inherited_index(self):
        reader = NeteasePositionReader()
        lines = (TimedLyricLine(10.0, "current"), TimedLyricLine(10.0, "翻译"), TimedLyricLine(20.0, "later"))
        original_only = (TimedLyricLine(10.0, "current"), TimedLyricLine(20.0, "later"))
        with patch("api.netease_position.threading.Thread"):
            reader.read("Song", "Artist", lines, 100.0)
            reader._last_sample = NeteasePositionSample(11.4, 100.0, "netease-uia-lyrics", 0.86, 1, "翻译", 100.0, True)
            reader._pending_lyric_index = 1
            retained = reader.read("Song", "Artist", original_only, 100.0)
        self.assertEqual(retained.lyric_index, 0)
        self.assertEqual(retained.position_seconds, 11.4)
        self.assertEqual(reader._pending_lyric_index, 0)

    def test_enrichment_invalidates_old_source_scan_without_replacing_clock(self):
        reader = NeteasePositionReader()
        lines = (TimedLyricLine(10.0, "current"), TimedLyricLine(20.0, "later"))
        enriched = tuple(TimedLyricLine(line.time_seconds, line.text + "\n翻译") for line in lines)
        previous = NeteasePositionSample(11.0, 100.0, "netease-uia-lyrics", 0.86, 0, "current", 100.0, True)
        with patch("api.netease_position.threading.Thread"):
            reader.read("Song", "Artist", lines, 100.0)
            reader._last_sample = previous
            old_token = reader._refresh_token
            reader.read("Song", "Artist", enriched, 100.0)
        reader._position_from_visible_progress = lambda *_args: NeteasePositionSample(
            90.0, 100.0, "netease-uia-progress", 0.92, sampled_at=101.0,
        )
        reader._refresh_sample("song\x00artist", "Song", "Artist", lines, 100.0, refresh_token=old_token)
        self.assertEqual(reader._last_sample.position_seconds, previous.position_seconds)
        self.assertEqual(reader._last_sample.sampled_at, previous.sampled_at)

    def test_plain_browsed_rows_cannot_anchor_to_another_paragraph_while_playing(self):
        reader = NeteasePositionReader()
        lines = (TimedLyricLine(10.0, "old"), TimedLyricLine(30.0, "current"),
                 TimedLyricLine(70.0, "future"))
        previous = NeteasePositionSample(31.0, 100.0, "netease-uia-lyrics", 0.86, 1, "current", 100.0, True)
        reader._last_sample = previous
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        with patch.dict(sys.modules, {"pywinauto": fake_pywinauto}):
            for text in ("old", "future"):
                reader._visible_lyric_rows = lambda *_args, **_kwargs: [
                    {"top": 400, "texts": (text,), "max_text_height": 29},
                ]
                for now in (100.5, 101.4, 102.1):
                    with patch("api.netease_position.time.monotonic", return_value=now):
                        self.assertIsNone(reader._position_from_visible_lyrics("Song", "Artist", lines, 100.0))
        self.assertIs(reader._last_sample, previous)

    def test_past_tall_visible_row_cannot_beat_current_source_time_group(self):
        reader = NeteasePositionReader()
        lines = (TimedLyricLine(10.0, "old"), TimedLyricLine(30.0, "current"), TimedLyricLine(70.0, "future"))
        reader._last_sample = NeteasePositionSample(31.0, 100.0, "netease-uia-lyrics", 0.86, 1, "current", 100.0, True)
        reader._visible_lyric_rows = lambda *_args, **_kwargs: [
            {"top": 400, "texts": ("old",), "max_text_height": 80},
            {"top": 470, "texts": ("current",), "max_text_height": 24},
        ]
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        with patch.dict(sys.modules, {"pywinauto": fake_pywinauto}), patch(
            "api.netease_position.time.monotonic", return_value=100.5,
        ):
            sample = reader._position_from_visible_lyrics("Song", "Artist", lines, 100.0)
        self.assertEqual(sample.lyric_index, 1)

    def test_visible_lyric_back_jump_requires_stable_repeated_samples(self):
        reader = NeteasePositionReader()
        previous = NeteasePositionSample(
            68.8, 120.0, "netease-uia-lyrics", 0.86, 2, "same chorus", 100.0
        )
        first = NeteasePositionSample(
            8.8, 120.0, "netease-uia-lyrics", 0.86, 0, "same chorus", 100.6
        )
        second = NeteasePositionSample(
            8.8, 120.0, "netease-uia-lyrics", 0.86, 0, "same chorus", 101.0
        )
        confirmed = NeteasePositionSample(
            8.8, 120.0, "netease-uia-lyrics", 0.86, 0, "same chorus", 101.5
        )

        self.assertIs(reader._debounce_visible_line_jump(previous, first), previous)
        self.assertIs(reader._debounce_visible_line_jump(previous, second), previous)
        self.assertIs(reader._debounce_visible_line_jump(previous, confirmed), confirmed)

    def test_future_neighbour_cannot_win_visible_lyric_height_ranking(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(10.0, "current"),
            TimedLyricLine(12.0, "next"),
            TimedLyricLine(14.0, "future"),
        )
        reader._last_sample = NeteasePositionSample(
            11.4, 30.0, "netease-uia-lyrics", 0.86, 0, "current", 100.0, True
        )
        reader._visible_lyric_rows = lambda *_args, **_kwargs: [
            {"top": 400, "texts": ("current",), "max_text_height": 24},
            {"top": 470, "texts": ("next",), "max_text_height": 29},
            {"top": 550, "texts": ("future",), "max_text_height": 34},
        ]
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        with patch.dict(sys.modules, {"pywinauto": fake_pywinauto}), patch(
            "api.netease_position.time.monotonic", return_value=100.5
        ):
            sample = reader._position_from_visible_lyrics("Song", "Artist", lines, 30.0)

        self.assertIsNotNone(sample)
        self.assertEqual(sample.lyric_index, 1)

    def test_forward_two_line_coarse_jump_waits_until_timeline_is_due(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(10.0, "current"),
            TimedLyricLine(12.0, "next"),
            TimedLyricLine(14.0, "future"),
        )
        previous = NeteasePositionSample(
            11.4, 30.0, "netease-uia-lyrics", 0.86, 0, "current", 100.0, True
        )
        early = NeteasePositionSample(
            14.5, 30.0, "netease-uia-lyrics", 0.86, 2, "future", 100.5, True
        )
        next_line = NeteasePositionSample(
            12.5, 30.0, "netease-uia-lyrics", 0.86, 1, "next", 100.5, True
        )

        self.assertIs(reader._debounce_visible_line_jump(previous, early, lines), previous)
        self.assertIs(reader._debounce_visible_line_jump(previous, next_line, lines), next_line)

    def test_paused_forward_row_seek_is_not_blocked_by_previous_clock(self):
        reader = NeteasePositionReader()
        lines = (TimedLyricLine(10.0, "current"), TimedLyricLine(40.0, "seek target"))
        reader._last_sample = NeteasePositionSample(
            10.5, 80.0, "netease-uia-lyrics", 0.86, 0, "current", 100.0, False
        )
        reader._visible_lyric_rows = lambda *_args, **_kwargs: [
            {"top": 400, "texts": ("current",), "max_text_height": 24},
            {"top": 470, "texts": ("seek target",), "max_text_height": 29},
        ]
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        with patch.dict(sys.modules, {"pywinauto": fake_pywinauto}), patch(
            "api.netease_position.time.monotonic", return_value=100.5
        ):
            sample = reader._position_from_visible_lyrics("Song", "Artist", lines, 80.0)

        self.assertIsNotNone(sample)
        self.assertEqual(sample.lyric_index, 1)

    def test_playing_external_seek_row_at_playback_slot_requires_repeat_confirmation(self):
        reader = NeteasePositionReader()
        lines = (TimedLyricLine(10.0, "old"), TimedLyricLine(30.0, "seek target"))
        previous = NeteasePositionSample(
            10.5, 60.0, "netease-uia-lyrics", 0.86, 0, "old", 100.0, True
        )
        reader._last_sample = previous
        reader._visible_lyric_rows = lambda *_args, **_kwargs: [
            {"top": 475, "bottom": 525, "container_top": 442, "container_bottom": 876,
             "texts": ("old",), "max_text_height": 24},
            {"top": 545, "bottom": 605, "container_top": 442, "container_bottom": 876,
             "texts": ("seek target",), "max_text_height": 29},
        ]
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        with patch.dict(sys.modules, {"pywinauto": fake_pywinauto}):
            with patch("api.netease_position.time.monotonic", return_value=100.5):
                first = reader._position_from_visible_lyrics("Song", "Artist", lines, 60.0)
            self.assertEqual(first.lyric_index, 1)
            self.assertIs(reader._debounce_visible_line_jump(previous, first, lines), previous)
            with patch("api.netease_position.time.monotonic", return_value=101.4):
                second = reader._position_from_visible_lyrics("Song", "Artist", lines, 60.0)
            self.assertIs(reader._debounce_visible_line_jump(previous, second, lines), second)

    def test_plain_wrapped_future_row_is_not_active_playback_slot(self):
        baseline = {"top": 460, "bottom": 510, "container_top": 442, "container_bottom": 876,
                    "texts": ("previous",), "max_text_height": 24}
        row = {"top": 530, "bottom": 620, "container_top": 442, "container_bottom": 876,
               "texts": ("wrapped neighbour",), "max_text_height": 49}
        after = {"top": 640, "bottom": 690, "container_top": 442, "container_bottom": 876,
                 "texts": ("next",), "max_text_height": 24}
        matched = [(baseline, "previous", (0,)), (row, "wrapped neighbour", (1,)), (after, "next", (2,))]
        self.assertEqual(NeteasePositionReader._visually_active_lyric_rows(matched), set())
        row["max_text_height"] = 59
        self.assertEqual(NeteasePositionReader._visually_active_lyric_rows(matched), {1})
        for item, _text, _indices in matched:
            for key in ("top", "bottom", "container_top", "container_bottom", "max_text_height"):
                item[key] *= 1.5
        self.assertEqual(NeteasePositionReader._visually_active_lyric_rows(matched), {1})

    def test_coarse_lyric_jump_confirmation_expires_after_scan_gap(self):
        reader = NeteasePositionReader()
        previous = NeteasePositionSample(
            68.8, 120.0, "netease-uia-lyrics", 0.86, 2, "same chorus", 100.0
        )
        first = NeteasePositionSample(
            8.8, 120.0, "netease-uia-lyrics", 0.86, 0, "same chorus", 100.6
        )
        much_later = NeteasePositionSample(
            8.8, 120.0, "netease-uia-lyrics", 0.86, 0, "same chorus", 105.0
        )

        self.assertIs(reader._debounce_visible_line_jump(previous, first), previous)
        self.assertIs(reader._debounce_visible_line_jump(previous, much_later), previous)
        self.assertAlmostEqual(reader._pending_lyric_index_since, 105.0)

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

    def test_paused_visible_line_anchor_does_not_advance_with_wall_clock(self):
        reader = NeteasePositionReader()
        lines = (
            TimedLyricLine(10.0, "first"),
            TimedLyricLine(14.0, "second"),
            TimedLyricLine(18.0, "third"),
        )
        previous = NeteasePositionSample(
            15.4, 30.0, "netease-uia-lyrics", 0.86, 1, "second", 100.0, False
        )
        sample = NeteasePositionSample(
            15.4, 30.0, "netease-uia-lyrics", 0.86, 1, "second", 101.0, False
        )

        self.assertIs(reader._smooth_visible_sample(previous, sample, lines), sample)

    def test_progress_text_extracts_current_and_duration(self):
        reader = NeteasePositionReader()

        position, duration = reader._progress_from_texts(("01:12 / 03:30",), 210.0)

        self.assertEqual(position, 72.0)
        self.assertEqual(duration, 210.0)

    def test_separate_time_labels_require_progress_bar_geometry(self):
        reader = NeteasePositionReader()

        position, duration = reader._progress_from_texts(("01:12", "03:30"), 210.0)

        self.assertIsNone(position)
        self.assertIsNone(duration)

    def test_real_playlist_durations_do_not_become_playback_position(self):
        reader = NeteasePositionReader()
        texts = (
            "03:13", "02:54", "02:37", "04:10", "03:14", "03:24",
            "02:57", "04:05", "04:58", "02:57", "02:58", "02:13",
            "02:55", "02:15", "03:34", "03:50", "02:18", "03:55",
            "03:34", "03:25", "04:35", "04:29", "02:52",
        )

        self.assertEqual(reader._progress_from_texts(texts, 194.002), (None, None))

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

    def test_combined_progress_label_requires_player_bar_geometry(self):
        reader = NeteasePositionReader()
        playlist_label = {
            "text": "01:50 / 03:30", "top": 300.0, "bottom": 322.0,
            "window_top": 0.0, "window_bottom": 720.0,
        }
        player_label = {
            "text": "01:12 / 03:30", "top": 650.0, "bottom": 672.0,
            "window_top": 0.0, "window_bottom": 720.0,
        }
        self.assertEqual(reader._progress_from_combined_text_controls((playlist_label,), 210.0), (None, None))
        self.assertEqual(reader._progress_from_combined_text_controls(({"text": "01:50 / 03:30"},), 210.0), (None, None))
        self.assertEqual(
            reader._progress_from_combined_text_controls((playlist_label, player_label), 210.0),
            (72.0, 210.0),
        )

    def test_progress_read_filters_same_title_with_different_artist(self):
        reader = NeteasePositionReader()
        wrong = SimpleNamespace(window_text=lambda: "Song - Other Artist")
        right = SimpleNamespace(window_text=lambda: "Song - Artist")
        reader._netease_windows = lambda _desktop, _title, require_title=False: [wrong, right]
        seen = []
        reader._position_from_progress_windows = lambda windows, _duration: seen.extend(windows) or None
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        with patch.dict(sys.modules, {"pywinauto": fake_pywinauto}):
            reader._position_from_visible_progress("Song", 180.0, "Artist")
        self.assertEqual(seen, [right])

    def test_lyric_row_read_filters_same_title_with_different_artist(self):
        reader = NeteasePositionReader()
        wrong = SimpleNamespace(window_text=lambda: "Song - Other Artist")
        reader._netease_windows = lambda _desktop, _title, require_title=False: [wrong]

        self.assertEqual(reader._visible_lyric_rows(object(), "Song", artist="Artist"), [])

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
                self.requested_types = []
                self._controls = (
                    FakeTextControl("Song", 20, 20, 120, 40),
                    FakeTextControl("01:12", 100, 910, 150, 930),
                    FakeTextControl("03:30", 760, 910, 820, 930),
                )

            def descendants(self, control_type=None):
                self.descendant_calls += 1
                self.requested_types.append(control_type)
                return list(self._controls) if control_type == "Text" else []

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
        self.assertEqual(window.requested_types.count("Text"), 1)
        self.assertEqual(window.requested_types.count("Button"), 1)

    def test_player_play_button_reports_paused_state(self):
        reader = NeteasePositionReader()

        class FakeButton:
            element_info = SimpleNamespace(automation_id="btn_pc_minibar_play", name="play")

            def window_text(self):
                return "play"

        class FakeWindow:
            def descendants(self, control_type=None):
                return [FakeButton()] if control_type == "Button" else []

        self.assertIs(reader._playback_state_from_windows((FakeWindow(),)), False)

    def test_player_pause_button_reports_playing_state(self):
        reader = NeteasePositionReader()

        class FakeButton:
            element_info = SimpleNamespace(automation_id="btn_pc_minibar_play", name="pause")

            def window_text(self):
                return "pause"

        class FakeWindow:
            def descendants(self, control_type=None):
                return [FakeButton()] if control_type == "Button" else []

        self.assertIs(reader._playback_state_from_windows((FakeWindow(),)), True)

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

        def progress(_title, _duration, _artist):
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

        def progress(_title, _duration, _artist):
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

        def progress(_title, _duration, _artist):
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

    def test_seek_uses_wide_bottom_range_control_instead_of_volume(self):
        reader = NeteasePositionReader()

        class FakeRange:
            CurrentMinimum = 0.0
            CurrentMaximum = 100.0
            CurrentIsReadOnly = False

            def __init__(self):
                self.values = []
                self.CurrentValue = 10.0

            def SetValue(self, value):
                self.values.append(value)
                self.CurrentValue = value

        class FakeControl:
            def __init__(self, left, top, right, bottom):
                self._rect = SimpleNamespace(left=left, top=top, right=right, bottom=bottom)
                self.iface_range_value = FakeRange()

            def rectangle(self):
                return self._rect

        progress = FakeControl(180, 850, 1020, 870)
        volume = FakeControl(930, 810, 1040, 830)

        class FakeWindow:
            def window_text(self):
                return "Song - Artist"

            def rectangle(self):
                return SimpleNamespace(left=100, top=100, right=1100, bottom=960)

            def descendants(self, control_type=None):
                return [progress, volume] if control_type == "Slider" else []

        reader._netease_windows = lambda _desktop, _title, require_title=False: [FakeWindow()]
        reader.verify_playback_position = lambda *_args, **_kwargs: bool(progress.iface_range_value.values)
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            result = reader.seek_playback_position(120.0, 240.0, title="Song", artist="Artist")
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertTrue(result)
        self.assertEqual(progress.iface_range_value.values, [50.0])
        self.assertEqual(volume.iface_range_value.values, [])

    def test_seek_does_not_physically_click_read_only_progress_bar(self):
        reader = NeteasePositionReader()
        clicks = []

        class FakeRange:
            CurrentMinimum = 0.0
            CurrentMaximum = 100.0
            CurrentIsReadOnly = True

        class FakeControl:
            iface_range_value = FakeRange()

            def rectangle(self):
                return SimpleNamespace(left=200, top=820, right=1000, bottom=840)

            def click_input(self, coords=None):
                clicks.append(coords)

        control = FakeControl()

        class FakeWindow:
            def window_text(self):
                return "Song - Artist"

            def rectangle(self):
                return SimpleNamespace(left=100, top=100, right=1100, bottom=960)

            def descendants(self, control_type=None):
                return [control] if control_type == "ProgressBar" else []

        reader._netease_windows = lambda _desktop, _title, require_title=False: [FakeWindow()]
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            result = reader.seek_playback_position(60.0, 240.0, title="Song", artist="Artist")
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertFalse(result)
        self.assertEqual(clicks, [])

    def test_seek_does_not_click_hidden_progress_strip_under_an_overlay(self):
        reader = NeteasePositionReader()
        clicks = []

        class FakeButton:
            element_info = SimpleNamespace(automation_id="btn_pc_minibar_play")

            def rectangle(self):
                return SimpleNamespace(left=1372, top=874, right=1413, bottom=914)

        class FakeWindow:
            def window_text(self):
                return "Song - Artist"

            def rectangle(self):
                return SimpleNamespace(left=859, top=182, right=1916, bottom=934)

            def descendants(self, control_type=None):
                return [FakeButton()] if control_type == "Button" else []

            def click_input(self, coords=None):
                clicks.append(coords)

        reader._netease_windows = lambda _desktop, _title, require_title=False: [FakeWindow()]
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            result = reader.seek_playback_position(96.0, 192.0, title="Song", artist="Artist")
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertFalse(result)
        self.assertEqual(clicks, [])

    def test_hidden_netease_strip_click_requires_focus_and_progress_readback(self):
        reader = NeteasePositionReader()
        clicks = []
        events = []

        class FakeButton:
            element_info = SimpleNamespace(automation_id="btn_pc_minibar_play")

            def rectangle(self):
                return SimpleNamespace(left=1372, top=874, right=1413, bottom=914)

        class FakeWindow:
            active = False

            def window_text(self):
                return "Song - Artist"

            def is_visible(self):
                return True

            def is_minimized(self):
                return False

            def set_focus(self):
                events.append("focus")
                self.active = True

            def is_active(self):
                return self.active

            def rectangle(self):
                return SimpleNamespace(left=859, top=182, right=1916, bottom=934)

            def descendants(self, control_type=None):
                return [FakeButton()] if control_type == "Button" else []

            def click_input(self, coords=None):
                events.append("click")
                clicks.append(coords)

        window = FakeWindow()
        reader._netease_windows = lambda _desktop, _title, require_title=False: [window]
        reader.verify_playback_position = lambda *_args, **_kwargs: bool(clicks)
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            result = reader.seek_playback_position(96.0, 192.0, title="Song", artist="Artist")
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertTrue(result)
        self.assertEqual(events, ["focus", "click"])
        self.assertEqual(len(clicks), 1)

    def test_hidden_netease_strip_click_without_readback_is_failure(self):
        reader = NeteasePositionReader()
        clicks = []

        class FakeButton:
            element_info = SimpleNamespace(automation_id="btn_pc_minibar_play")

            def rectangle(self):
                return SimpleNamespace(left=500, top=850, right=540, bottom=890)

        class FakeWindow:
            def window_text(self):
                return "Song - Artist"

            def is_visible(self):
                return True

            def is_minimized(self):
                return False

            def set_focus(self):
                return None

            def is_active(self):
                return True

            def rectangle(self):
                return SimpleNamespace(left=100, top=100, right=1100, bottom=960)

            def descendants(self, control_type=None):
                return [FakeButton()] if control_type == "Button" else []

            def click_input(self, coords=None):
                clicks.append(coords)

        reader._netease_windows = lambda _desktop, _title, require_title=False: [FakeWindow()]
        reader.verify_playback_position = lambda *_args, **_kwargs: False
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            result = reader.seek_playback_position(120.0, 240.0, title="Song", artist="Artist")
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertFalse(result)
        self.assertEqual(len(clicks), 1)

    def test_seek_rejects_range_when_set_value_has_no_readback_effect(self):
        reader = NeteasePositionReader()

        class StaleRange:
            CurrentMinimum = 0.0
            CurrentMaximum = 100.0
            CurrentIsReadOnly = False
            CurrentValue = 12.0

            def SetValue(self, _value):
                return None

        class FakeControl:
            iface_range_value = StaleRange()

            def rectangle(self):
                return SimpleNamespace(left=180, top=850, right=1020, bottom=870)

        class FakeWindow:
            def window_text(self):
                return "Song - Artist"

            def rectangle(self):
                return SimpleNamespace(left=100, top=100, right=1100, bottom=960)

            def descendants(self, control_type=None):
                return [FakeControl()] if control_type == "Slider" else []

        reader._netease_windows = lambda _desktop, _title, require_title=False: [FakeWindow()]
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            result = reader.seek_playback_position(120.0, 240.0, title="Song", artist="Artist")
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertFalse(result)

    def test_seek_rejects_range_if_track_caption_changes_during_set(self):
        reader = NeteasePositionReader()

        class FakeWindow:
            caption = "Song - Artist"

            def window_text(self):
                return self.caption

            def rectangle(self):
                return SimpleNamespace(left=100, top=100, right=1100, bottom=960)

            def descendants(self, control_type=None):
                return [control] if control_type == "Slider" else []

        window = FakeWindow()

        class FakeRange:
            CurrentMinimum = 0.0
            CurrentMaximum = 100.0
            CurrentIsReadOnly = False
            CurrentValue = 10.0

            def SetValue(self, value):
                self.CurrentValue = value
                window.caption = "Next Song - Other Artist"

        class FakeControl:
            iface_range_value = FakeRange()

            def rectangle(self):
                return SimpleNamespace(left=180, top=850, right=1020, bottom=870)

        control = FakeControl()
        reader._netease_windows = lambda _desktop, _title, require_title=False: [window]
        fake_pywinauto = types.SimpleNamespace(Desktop=lambda backend="uia": object())
        previous = sys.modules.get("pywinauto")
        sys.modules["pywinauto"] = fake_pywinauto
        try:
            result = reader.seek_playback_position(120.0, 240.0, title="Song", artist="Artist")
        finally:
            if previous is None:
                sys.modules.pop("pywinauto", None)
            else:
                sys.modules["pywinauto"] = previous

        self.assertFalse(result)

    def test_interactive_refresh_keeps_text_progress_without_image_capture(self):
        reader = NeteasePositionReader()
        calls = []

        def stale_progress(_title, _duration, _artist):
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
        reader._position_from_visible_progress = lambda _title, _duration, _artist: NeteasePositionSample(
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
