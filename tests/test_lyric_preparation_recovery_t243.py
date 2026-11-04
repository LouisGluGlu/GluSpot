"""Preparation failure publishes terminal loader state and has one recovery."""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from local.music_lyric_preparation import prepare_lyric_presentation
from local.music_lyric_preparation_store import LyricPresentationStore
from local.music_tasks import MusicTasks
from tests.test_music_lyric_preparation import ControlledTasks
from tests.test_music_surface_transition import _TransitionOwner


class LyricPreparationRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.addCleanup(self.owner.close)
        self.owner.media_track_id = "current"
        self.owner.timed_lyrics = [(1., "current words")]
        self.owner.lyrics = ["current words"]

    def test_failed_preparation_notifies_once_then_recovers_with_one_retry(self):
        tasks = ControlledTasks()
        store = LyricPresentationStore(self.owner, tasks=tasks)
        self.addCleanup(store.close)
        states = []
        store.changed.connect(lambda: states.append((store.failed is not None, store.current is not None)))
        store.get()
        failed, _action = tasks.actions.pop(0)
        tasks.completed.emit(failed, None, "request_failed")
        self.assertEqual(states, [(True, False)])
        self.assertIsNotNone(store.failed)
        self.assertEqual(len(tasks.actions), 1)
        self.assertNotEqual(tasks.actions[0][0], failed)
        tasks.completed.emit(failed, None, "late_old_failure")
        self.assertEqual(len(states), 1)
        tasks.finish()
        self.assertIsNone(store.failed)
        self.assertEqual(store.get().source.identity, "current")
        self.assertEqual(states, [(True, False), (False, True)])

    def test_second_failure_terminates_without_more_work(self):
        tasks = ControlledTasks()
        store = LyricPresentationStore(self.owner, tasks=tasks)
        self.addCleanup(store.close)
        store.get()
        for _ in range(2):
            token, _action = tasks.actions.pop(0)
            tasks.completed.emit(token, None, "timeout")
        self.assertIsNotNone(store.failed)
        for _ in range(5):
            self.assertIsNone(store.get())
        self.assertFalse(tasks.actions)
        self.assertIsNone(store.active)

    def test_hung_old_preparation_releases_latest_song_without_late_publication(self):
        tasks = MusicTasks(worker_count=1, task_timeout_seconds=.04)
        store = LyricPresentationStore(self.owner, tasks=tasks)
        self.addCleanup(store.close)
        entered, release = threading.Event(), threading.Event()
        published = []
        store.changed.connect(lambda: published.append(store.current[1].source.identity) if store.current else None)
        def prepare(source):
            if source.identity == "current":
                entered.set()
                release.wait(2.)
            return prepare_lyric_presentation(source)
        with patch("local.music_lyric_preparation_store.prepare_lyric_presentation", side_effect=prepare):
            try:
                store.get()
                self.assertTrue(entered.wait(1.))
                for index in range(30):
                    self.owner.media_track_id = f"rapid-{index}"
                    store.get()
                deadline = time.monotonic() + 2.
                while not published and time.monotonic() < deadline:
                    QTest.qWait(5)
                self.assertEqual(published, ["rapid-29"])
                self.assertEqual(store.discarded, 1)
                self.assertIsNone(store.active)
                self.assertIsNone(store.failed)
            finally:
                release.set()
                QTest.qWait(20)
        self.assertEqual(published, ["rapid-29"])
