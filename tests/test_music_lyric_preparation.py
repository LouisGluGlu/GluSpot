from __future__ import annotations

from dataclasses import replace
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal, QEvent, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from api.models import TimedLyricUnit
from local.music_lyric_preparation import prepare_lyric_presentation, snapshot_lyric_input
from local.music_lyric_preparation import _row_plans
from local.music_lyric_preparation_store import LyricPresentationStore
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner


class ControlledTasks(QObject):
    completed = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self.actions = []
        self.closed = False

    def submit(self, token, action):
        self.actions.append((token, action))
        return True

    def finish(self):
        token, action = self.actions.pop(0)
        self.completed.emit(token, action(), None)

    def close(self):
        self.closed = True


class LyricPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.media_track_id = "preparation-fixture"
        self.owner.enable_dual_line_lyrics = True
        self.owner.artist = "Alice / Bob"
        self.owner.timed_lyrics = [(0., "作词 Lyricist：A"), (8., "Shine on\n翻译：继续闪耀"),
                                   (15., "Keep singing (ah)"), (22., "Next sentence")]
        self.owner.lyrics = [text for _start, text in self.owner.timed_lyrics]
        self.owner.timed_lyric_units = [(), (TimedLyricUnit(8., 9., "Shine "), TimedLyricUnit(9., 10., "on")),
                                        (TimedLyricUnit(15., 17., "Keep "), TimedLyricUnit(17., 20., "singing (ah)")), ()]
        self.owner.timed_lyric_timing_kinds = ["line", "unit", "unit", "line"]
        self.stores = []

    def tearDown(self):
        for store in self.stores:
            store.close()
        self.owner.close()

    def store(self, *, real=False):
        store = LyricPresentationStore(self.owner, tasks=None if real else ControlledTasks())
        self.stores.append(store)
        return store

    def test_immutable_snapshot_detaches_mutable_units_and_provider_containers(self):
        mutable = SimpleNamespace(start_seconds=8., end_seconds=10., text="Shine on")
        self.owner.timed_lyric_units[1] = [mutable]
        source = snapshot_lyric_input(self.owner)
        self.owner.timed_lyrics[1] = (8., "Wrong new text")
        self.owner.lyrics[1] = "Changed"
        mutable.text = "Mutated"
        self.assertEqual(source.timed[1][1], "Shine on\n翻译：继续闪耀")
        self.assertEqual(source.units[1][0].text, "Shine on")
        result = prepare_lyric_presentation(source)
        self.assertNotIn("Mutated", repr(result))
        self.assertNotIn("Wrong new text", repr(result))

    def test_shared_result_preserves_cleanup_gap_translation_and_timing_quality(self):
        source = snapshot_lyric_input(self.owner)
        result = prepare_lyric_presentation(source)
        self.assertNotIn("Lyricist", repr(result.lines))
        self.assertIn((10., "· · ·"), result.timed)
        at = next(index for index, row in enumerate(result.timed) if row[0] == 8.)
        self.assertEqual(result.timings[at].quality, "word")
        self.assertEqual(result.layers[at].translation_text, "继续闪耀")
        self.assertEqual(result.island_timings[1], result.timings[at])
        self.assertEqual(result.source, source)
        hidden = prepare_lyric_presentation(replace(source, translated=False))
        self.assertFalse(hidden.layers[at].translation_text)
        self.assertEqual(hidden.timings[at], result.timings[at])

    def test_untimed_surface_keeps_attached_backing_vocal_and_island_indexes(self):
        self.owner.timed_lyrics = []
        self.owner.timed_lyric_units = []
        self.owner.lyrics = ["Shine on tonight", "(oh oh)", "Keep on singing"]
        result = prepare_lyric_presentation(snapshot_lyric_input(self.owner))
        self.assertEqual(len(result.lines), 2)
        self.assertIn("oh oh", result.lines[0])
        self.assertEqual(len(result.island_rows), 3)
        self.assertEqual(result.island_rows[1], "(oh oh)")

    def test_identical_timelines_share_immutable_timing_without_a_second_pass(self):
        self.owner.timed_lyrics = [(0., "Shine on"), (4., "Keep singing")]
        self.owner.lyrics = [text for _start, text in self.owner.timed_lyrics]
        self.owner.timed_lyric_units = [(), ()]
        self.owner.timed_lyric_timing_kinds = ["line", "line"]
        source = snapshot_lyric_input(self.owner)
        with patch("local.music_lyric_preparation._row_plans", wraps=_row_plans) as prepare:
            result = prepare_lyric_presentation(source)
        self.assertEqual(result.timed, source.timed)
        self.assertIs(result.timings, result.island_timings)
        self.assertEqual(prepare.call_count, 1)
        # A real read-end override, including zero, changes the semantics of
        # the surface row and must not leak into the raw island timeline.
        with patch("local.music_lyric_preparation.attached_parenthetical_read_end", return_value=0.), \
                patch("local.music_lyric_preparation._row_plans", wraps=_row_plans) as prepare:
            independent = prepare_lyric_presentation(source)
        self.assertEqual(prepare.call_count, 2)
        self.assertIsNot(independent.timings, independent.island_timings)
        self.assertEqual(independent.island_timings, result.island_timings)

    def test_filtered_and_inserted_timelines_keep_island_raw_timing_projection(self):
        result = prepare_lyric_presentation(snapshot_lyric_input(self.owner))
        self.assertNotEqual(result.timed, result.source.timed)
        self.assertEqual(len(result.island_timings), len(self.owner.timed_lyrics))
        self.assertIsNot(result.timings, result.island_timings)
        self.assertIn((10., "· · ·"), result.timed)

    def test_compatible_previous_result_survives_translation_failure_but_not_new_song_or_reindexed_rows(self):
        store = self.store()
        store.get()
        store.tasks.finish()
        previous = store.get()
        self.owner.enable_dual_line_lyrics = False
        self.assertIsNone(store.get())
        self.assertIs(store.previous_compatible(), previous)
        token, _action = store.tasks.actions.pop()
        store.tasks.completed.emit(token, None, "failed")
        self.assertIs(store.previous_compatible(), previous)
        self.owner.timed_lyrics = self.owner.timed_lyrics[1:]
        self.assertIsNone(store.previous_compatible())
        self.owner.media_track_id = "another-song"
        self.assertIsNone(store.previous_compatible())

    def test_latest_pending_only_and_late_song_result_is_never_published(self):
        store = self.store()
        published = []
        store.changed.connect(lambda: published.append(store.current[1].source.identity))
        store.get()
        for index in range(40):
            self.owner.media_track_id = f"song-{index}"
            store.get()
        self.assertEqual(len(store.tasks.actions), 1)
        self.assertIsNotNone(store.pending)
        store.tasks.finish()
        self.assertEqual(published, [])
        self.assertEqual(len(store.tasks.actions), 1)
        store.tasks.finish()
        self.assertEqual(published, ["song-39"])
        self.assertEqual(store.submitted, 2)
        self.assertEqual(store.discarded, 1)
        self.assertIsNone(store.active)
        self.assertIsNone(store.pending)

    def test_hidden_owner_change_is_checked_again_on_delivery(self):
        store = self.store()
        store.get()
        self.owner.media_track_id = "changed-without-another-request"
        store.tasks.finish()
        self.assertEqual(store.discarded, 1)
        self.assertIsNone(store.current)

    def test_count_and_bytes_bounded_under_repeat_songs_and_translation(self):
        store = self.store()
        store.MAX_BYTES = 300000
        for index in range(30):
            self.owner.media_track_id = f"song-{index}"
            store.get(translated=bool(index % 2))
            store.tasks.finish()
            self.assertLessEqual(len(store.cache), store.MAX_ENTRIES)
            self.assertLessEqual(store.cache_bytes, store.MAX_BYTES)
        store.MAX_BYTES = 1
        self.owner.media_track_id = "oversized"
        store.get()
        store.tasks.finish()
        self.assertEqual(store.cache_bytes, 0)
        self.assertEqual(len(store.cache), 0)
        self.assertEqual(store.get().source.identity, "oversized")

    def test_paint_consumes_ready_result_and_miss_never_runs_preparation(self):
        store = self.store()
        self.owner._lyric_presentation_store = store
        window = MusicSurfaceWindow(self.owner)
        try:
            with patch("local.music_surface_window.prepare_lyric_presentation", side_effect=AssertionError("paint processing")):
                self.assertEqual(window._display_lyrics(), (["加载歌词中"], []))
                store.tasks.finish()
                first = window._display_lyrics()
                self.assertTrue(first[1])
                self.owner.lyrics[0] = "New source revision"
                self.assertIs(window._display_lyrics(), first)
                self.owner.media_track_id = "other-song"
                self.assertEqual(window._display_lyrics(), (["加载歌词中"], []))
        finally:
            window.shutdown()

    def test_worker_is_off_gui_and_result_signal_returns_on_gui_thread(self):
        store = self.store(real=True)
        gui = threading.get_ident()
        workers, deliveries = [], []
        original = prepare_lyric_presentation
        def prepare(source):
            workers.append(threading.get_ident())
            return original(source)
        store.changed.connect(lambda: deliveries.append(threading.get_ident()))
        with patch("local.music_lyric_preparation_store.prepare_lyric_presentation", side_effect=prepare):
            store.get()
            deadline = time.monotonic() + 3.
            while not deliveries and time.monotonic() < deadline:
                QTest.qWait(5)
        self.assertEqual(deliveries, [gui])
        self.assertEqual(len(workers), 1)
        self.assertNotEqual(workers[0], gui)
        store.close()
        self.assertTrue(store.closed)
        self.assertIsNone(store.get())

    def test_running_preparation_does_not_block_gui_clock_or_an_input_callback(self):
        store = self.store(real=True)
        entered, release = threading.Event(), threading.Event()
        original = prepare_lyric_presentation
        def slow(source):
            entered.set()
            release.wait(2.)
            return original(source)
        callbacks = []
        with patch("local.music_lyric_preparation_store.prepare_lyric_presentation", side_effect=slow):
            store.get()
            QTimer.singleShot(10, lambda: callbacks.append("input"))
            try:
                # The suite can have deferred Qt cleanup from earlier native
                # windows. Observe a bounded response while the worker stays
                # blocked, rather than assert one exact timer delivery slot.
                deadline = time.monotonic() + .75
                while (not entered.is_set() or not callbacks) and time.monotonic() < deadline:
                    QTest.qWait(5)
                self.assertTrue(entered.is_set())
                self.assertEqual(callbacks, ["input"])
                self.assertIsNotNone(store.active)
            finally:
                release.set()
            deadline = time.monotonic() + 2.
            while store.active is not None and time.monotonic() < deadline:
                QTest.qWait(5)
        self.assertIsNotNone(store.get())

    def test_minimize_stops_existing_surface_clocks_and_restore_restarts_them(self):
        window = MusicSurfaceWindow(self.owner)
        try:
            with patch.object(window, "isVisible", return_value=True), patch.object(window, "isMinimized", return_value=True):
                window._timer.start()
                window._audio_output_status_timer.start()
                window.changeEvent(QEvent(QEvent.Type.WindowStateChange))
                self.assertFalse(window._timer.isActive())
                self.assertFalse(window._audio_output_status_timer.isActive())
            with patch.object(window, "isVisible", return_value=True), patch.object(window, "isMinimized", return_value=False):
                window.changeEvent(QEvent(QEvent.Type.WindowStateChange))
                self.assertTrue(window._timer.isActive())
                self.assertTrue(window._audio_output_status_timer.isActive())
                self.assertEqual(window._timer.interval(), 16)
        finally:
            window.shutdown()


if __name__ == "__main__":
    unittest.main()
