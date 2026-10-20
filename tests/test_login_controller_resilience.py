"""Offline account/controller recovery and bounded local-worker regressions.

All credentials are synthetic and stored under temporary directories. The Node
transport fixture speaks stdio only; it never contacts a music service.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from api.online_music import AccountSnapshot, CatalogPage, OnlineMusicError, OnlineMusicService, OnlinePlaylist
from api.online_music_bridge import NodeOnlineMusicBackend
from api.online_music_credentials import ProtectedCredentialStore
from local.music_library_controller import MusicLibraryController
from local.music_library_window import MusicLibraryWindow
from local.music_tasks import MusicTasks
from local.playback_entry_controller import PlaybackEntryController
from tests.test_music_library_controller import Entry, Island, Player, Service
from tests.test_online_music import Backend, FixtureProtector
from tests.test_playback_entry_controller import FakeIsland, FakeService, FakeSettings, FakeSystem, FakeTasks, FakeWindow


class LoginControllerResilienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ProtectedCredentialStore(self.temp.name, FixtureProtector())
        self.backend = Backend()
        self.service = self.make_service()

    def make_service(self):
        service = OnlineMusicService(self.backend, self.store, clock=lambda: 1000.)
        self.addCleanup(service.close)
        return service

    def login(self):
        snapshot = self.service.begin_login("netease")
        self.backend.state = "confirmed"
        self.assertEqual(self.service.poll_login(snapshot.session_id).state, "confirmed")

    def entry_controller(self, service=None, *, tasks=None):
        callback = Mock()
        controller = PlaybackEntryController(
            FakeSettings(completed=True), FakeSystem(), FakeIsland(), service or self.service,
            window=FakeWindow(), tasks=tasks, login_callback=callback)
        self.addCleanup(controller.close)
        return controller, callback

    def wait_until(self, predicate, timeout=2.):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.003)
        self.app.processEvents()
        self.assertTrue(predicate(), "Local task did not reach its expected bounded state")

    def test_malformed_account_response_retains_protected_record_and_profile(self):
        self.login()
        for malformed in (None, [], {}, {"logged_in": "false"}, {"logged_in": True},
                          *({"logged_in": True, "user_id": identity} for identity in (None, True, [], {}, 4.2))):
            with self.subTest(response_shape=type(malformed).__name__, fields=str(malformed)):
                with patch.object(self.backend, "request", return_value={"data": malformed}):
                    account = self.service.account_status("netease", refresh=True)
                self.assertEqual(account.state, "error")
                self.assertFalse(account.logged_in)
                self.assertEqual(account.user_id, "42")
                self.assertTrue(self.service.has_login_record("netease"))
                self.assertIsNotNone(self.store.load("netease"))
        self.assertTrue(self.service.account_status("netease", refresh=True).logged_in)

    def test_logout_after_credential_read_cannot_revive_cached_credential(self):
        self.login()
        entered, release = threading.Event(), threading.Event()
        original = self.service._load_credential
        results, errors = [], []

        def read_then_wait(source):
            value = original(source)
            entered.set()
            if not release.wait(2):
                raise AssertionError("Fixture credential-read barrier timed out")
            return value

        def validate():
            try:
                results.append(self.service.account_status("netease", refresh=True))
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=validate)
        with patch.object(self.service, "_load_credential", side_effect=read_then_wait):
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.service.logout("netease")
                calls_after_logout = len(self.backend.calls)
            finally:
                release.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors)
        self.assertEqual(results[0].state, "signed_out")
        self.assertFalse(results[0].logged_in)
        self.assertEqual(len(self.backend.calls), calls_after_logout)
        self.assertFalse(self.service.has_login_record("netease"))
        self.assertIsNone(self.store.load("netease"))

    def test_verified_entry_reuse_neither_revalidates_nor_invalidates_service(self):
        self.login()
        tasks = FakeTasks()
        controller, callback = self.entry_controller(tasks=tasks)
        controller.show_login("netease", enter_saved=True)
        tasks.run(tasks.latest("sources"))
        generations = dict(self.service._generations)
        validation_epochs = dict(self.service._validation_epochs)
        calls = len(self.backend.calls)
        tasks.run(tasks.latest("account"))
        callback.assert_called_once_with("netease")
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(self.service._generations, generations)
        self.assertEqual(self.service._validation_epochs, validation_epochs)
        self.assertTrue(self.service.account_status("netease").logged_in)
        controller.show_login("netease", enter_saved=True)
        tasks.run(tasks.latest("sources"))
        tasks.run(tasks.latest("account"))
        self.assertEqual(callback.call_count, 2)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(self.service._validation_epochs, validation_epochs)

    def test_cached_entry_handoff_does_not_promote_snapshot_to_authorization(self):
        self.login()
        restored = self.make_service()
        tasks = FakeTasks()
        controller, callback = self.entry_controller(restored, tasks=tasks)
        controller.show_login("netease", enter_saved=True)
        tasks.run(tasks.latest("sources"))
        calls = len(self.backend.calls)
        tasks.run(tasks.latest("account"))
        callback.assert_called_once_with("netease")
        cached = restored.account_status("netease")
        self.assertEqual(cached.state, "cached")
        self.assertFalse(cached.logged_in)
        self.assertEqual(len(self.backend.calls), calls)
        self.backend.error = True
        with self.assertRaises(OnlineMusicError) as raised:
            restored.playlists("netease")
        self.assertEqual(raised.exception.code, "auth_required")
        self.assertFalse(any(operation == "playlists" for _, operation, _ in self.backend.calls[calls:]))
        self.assertTrue(restored.has_login_record("netease"))

    def make_cached_library(self):
        service = Service()
        cached = AccountSnapshot("netease", False, "netease-user", "cached fixture", state="cached")
        service.restore_account = lambda source: cached
        service.cached_catalog = lambda source, kind="playlists": CatalogPage(
            (OnlinePlaylist(source, "cached-list", "saved playlist"),) if kind == "playlists" else (), 50, False)
        service.accounts["netease"] = replace(cached, state="error")
        tasks, art_tasks = FakeTasks(), FakeTasks()
        window, player, entry = MusicLibraryWindow(), Player(), Entry()
        controller = MusicLibraryController(FakeSettings(completed=True), service, player, Island(), entry,
            window=window, tasks=tasks, art_tasks=art_tasks)

        def close():
            controller.close()
            window.deleteLater()
            self.app.processEvents()

        self.addCleanup(close)
        controller.open_source("netease")
        tasks.run(tasks.latest("resume"))
        self.assertEqual([row.title for row in window.collections], ["saved playlist"])
        self.assertFalse(window._account.logged_in)
        self.assertFalse(window.search.isEnabled())
        return controller, service, tasks, window, player, entry

    def retry_account_now(self, controller, tasks):
        controller._catalog_retry_timer.stop()
        due = controller._catalog_retry_at["account"]
        with patch("local.music_library_controller.time.monotonic", return_value=due + .01):
            controller._retry_catalog()
        tasks.run(tasks.latest("open"))
        controller._catalog_retry_timer.stop()

    def test_library_temporary_failures_keep_snapshot_and_stop_after_three_retries(self):
        controller, service, tasks, window, player, entry = self.make_cached_library()
        tasks.run(tasks.latest("open"))
        delays = []
        for expected_count in range(1, 4):
            self.assertEqual(controller._catalog_retry_counts["account"], expected_count)
            delays.append(controller._catalog_retry_at["account"] - time.monotonic())
            self.assertEqual([row.title for row in window.collections], ["saved playlist"])
            self.assertFalse(window._account.logged_in)
            self.retry_account_now(controller, tasks)
        self.assertTrue(all(.8 < delay <= 4.1 for delay in delays))
        self.assertNotIn("account", controller._catalog_retry_at)
        self.assertFalse(controller._catalog_retry_timer.isActive())
        self.assertEqual(sum(call == ("account", "netease", True) for call in service.calls), 4)
        self.assertTrue(window.isVisible())
        self.assertFalse(window.search.isEnabled())
        self.assertIn("本地快照已保留", window._notice)
        self.assertEqual(entry.calls, [])
        self.assertEqual(player.calls, [])

    def test_library_retry_success_verifies_existing_snapshot_without_new_login(self):
        controller, service, tasks, window, _, entry = self.make_cached_library()
        tasks.run(tasks.latest("open"))
        service.accounts["netease"] = replace(window._account, logged_in=True, state="verified")
        self.retry_account_now(controller, tasks)
        self.assertTrue(window._account.logged_in)
        self.assertTrue(window.search.isEnabled())
        self.assertNotIn("account", controller._catalog_retry_at)
        self.assertNotIn("account", controller._catalog_retry_counts)
        self.assertIn("saved playlist", [row.title for row in window.collections])
        self.assertEqual(entry.calls, [])

    def test_blocked_avatar_and_pending_avatar_do_not_block_new_login(self):
        service = FakeService(saved=("netease",))
        original = service.account_status
        service.account_status = lambda source, refresh=False: replace(
            original(source, refresh), avatar_url="https://example.test/synthetic-avatar" if source == "netease" else "")
        entered, release = threading.Event(), threading.Event()
        avatar_calls = []

        def slow_avatar(url):
            avatar_calls.append(url)
            entered.set()
            release.wait(3)
            return QImage()

        with patch("api.online_music_artwork.fetch_online_thumbnail", side_effect=slow_avatar):
            controller, _ = self.entry_controller(service)
            self.assertIsNot(controller.tasks, controller._account_art_tasks)
            self.assertEqual(controller.tasks._worker_count, 2)
            self.assertEqual(controller._account_art_tasks._worker_count, 1)
            controller._sources = service.list_sources()
            controller.window.show()
            controller.window.choose_source("netease")
            try:
                self.wait_until(entered.is_set)
                controller.window.choose_source("netease")
                self.wait_until(lambda: controller._account_art_tasks._queue.qsize() == 1)
                started = time.monotonic()
                controller.window.choose_source("qq")
                self.wait_until(lambda: any(s.source_id == "qq" and s.state == "waiting"
                                           for s in controller.window.snapshots), timeout=1.5)
                self.assertLess(time.monotonic() - started, 1.5)
                self.assertFalse(release.is_set())
                self.assertEqual(len(avatar_calls), 1)
                self.assertEqual(controller._account_art_tasks._queue.qsize(), 0)
            finally:
                release.set()
                controller.close()


class LoginWorkerCancellationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, predicate, timeout=2.):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.003)
        self.app.processEvents()
        self.assertTrue(predicate(), "Local worker/stdio fixture exceeded its bounded wait")

    def test_cancel_two_running_auth_requests_releases_fixed_workers_for_new_login(self):
        bundled = Path(__file__).resolve().parents[1] / "build/online-music/node.exe"
        node = str(bundled) if bundled.is_file() else shutil.which("node")
        if not node:
            self.skipTest("Local Node runtime unavailable")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            modules = root / "node_modules"
            for name, version in (("@neteasecloudmusicapienhanced/api", "4.41.1"),
                                  ("@yakult-green-tea/qq-music-api", "3.1.4")):
                package = modules / name
                package.mkdir(parents=True)
                (package / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
            bridge = root / "fixture.cjs"
            bridge.write_text(r'''const fs=require('fs'),path=require('path');
              require('readline').createInterface({input:process.stdin}).on('line',line=>{
                const r=JSON.parse(line);
                fs.writeFileSync(path.join(__dirname,r.source+'-'+r.params.label),'');
                if(r.params.slow)return;
                process.stdout.write(JSON.stringify({id:r.id,ok:true,data:{ready:true}})+'\n');
              });''', encoding="utf-8")
            backend = NodeOnlineMusicBackend(node, modules, timeout_seconds=4)
            backend.bridge_path = bridge
            existing = set(threading.enumerate())
            tasks = MusicTasks()
            workers = {worker for worker in threading.enumerate() if worker not in existing
                       and worker.name.startswith("GluSpotMusic-")}
            completed = []
            tasks.completed.connect(lambda *result: completed.append(result))
            try:
                self.assertEqual(len(workers), 2)
                for cycle in range(2):
                    for source in ("netease", "qq"):
                        self.assertTrue(tasks.submit(("begin", cycle, source),
                            lambda source=source, cycle=cycle: backend.request(source, "begin_login",
                                {"slow": True, "label": f"old-{cycle}"})))
                    self.wait_until(lambda: all((root / f"{source}-old-{cycle}").exists()
                                               for source in ("netease", "qq")))
                    started = time.monotonic()
                    for source in ("netease", "qq"):
                        self.assertTrue(backend.cancel_login_request(source))
                    self.assertTrue(tasks.submit(("begin", 10 + cycle, "netease"),
                        lambda: backend.request("netease", "begin_login", {"label": "fresh"})))
                    self.wait_until(lambda: any(token == ("begin", 10 + cycle, "netease")
                                               for token, _, _ in completed), timeout=1.5)
                    self.assertLess(time.monotonic() - started, 1.5)
                    fresh = next(item for item in completed if item[0] == ("begin", 10 + cycle, "netease"))
                    self.assertIsNone(fresh[2])
                    self.assertTrue(fresh[1]["data"]["ready"])
                    self.wait_until(lambda: sum(token[1] == cycle for token, _, _ in completed) == 2)
                    self.assertTrue(all(error == "cancelled" for token, _, error in completed if token[1] == cycle))
                    self.assertTrue(all(worker.is_alive() for worker in workers))
                    self.assertEqual(len(backend._authentication_backends), 2)
                    self.assertEqual(tasks._queue.qsize(), 0)
            finally:
                tasks.close()
                lanes = [backend, *backend._authentication_backends.values()]
                children = [process for lane in lanes for process in [lane._process, *lane._retiring_processes]
                            if process is not None]
                backend.close()
                for process in children:
                    process.wait(timeout=3)
                for worker in workers:
                    worker.join(2)
            self.assertTrue(all(not worker.is_alive() for worker in workers))


if __name__ == "__main__":
    unittest.main()
