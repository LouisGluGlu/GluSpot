from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QObject, QRect, QTimer, Signal
from PySide6.QtWidgets import QApplication

from api.online_music import AccountSnapshot, LoginSnapshot, MusicSource
from local.music_tasks import MusicTasks
from local.playback_entry_controller import (
    COMPLETED_KEY, OWNED_KEY, SYSTEM_KEY, PlaybackEntryController,
)


class FakeSettings:
    def __init__(self, *, completed=False, owned=True, system=False):
        self.values = {COMPLETED_KEY: completed, OWNED_KEY: owned, SYSTEM_KEY: system}
        self.saved = 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save_now(self):
        self.saved += 1


class FakeTasks(QObject):
    completed = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self.jobs = []
        self.closed = False
        self.accepting = True

    def submit(self, token, action):
        if self.closed or not self.accepting:
            return False
        self.jobs.append((token, action))
        return True

    def latest(self, kind):
        return next(job for job in reversed(self.jobs) if job[0][0] == kind)

    def run(self, job):
        token, action = job
        try:
            result, error = action(), None
        except Exception:
            result, error = None, "request_failed"
        self.completed.emit(token, result, error)

    def close(self):
        self.closed = True


class FakeWindow(QObject):
    selectionAccepted = Signal(bool, bool)
    sourceSelected = Signal(str)
    refreshRequested = Signal()
    closed = Signal()
    backRequested = Signal()
    loginAccepted = Signal(str)

    def __init__(self):
        super().__init__()
        self.page = ""
        self.selected_source = ""
        self.visible = False
        self.snapshots = []
        self.sources = ()
        self.contracts = []
        self.closed_count = 0

    def show_choices(self):
        self.page = "modes"

    def show_login(self):
        self.page = "sources"
        self.selected_source = ""

    def set_sources(self, sources):
        self.sources = tuple(sources)

    def set_mode_back_enabled(self, enabled):
        self.back_enabled = enabled

    def set_login_snapshot(self, snapshot):
        self.snapshots.append(snapshot)

    def choose_source(self, source):
        self.selected_source = source
        self.sourceSelected.emit(source)

    select_source = choose_source

    def set_account(self, account):
        self.account = account

    def set_account_artwork(self, image):
        self.account_artwork = image

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def isVisible(self):
        return self.visible

    def raise_(self):
        pass

    def activateWindow(self):
        pass

    def contract_to(self, target, finished):
        self.contracts.append(QRect(target))
        finished()

    def close(self):
        self.closed_count += 1
        self.visible = False
        self.closed.emit()


class FakeIsland:
    def __init__(self):
        self.shown = 0
        self.location = self.preset = None
        self.topmost = False

    def set_location_mode(self, mode, *, notify):
        self.location = mode

    def apply_position_preset(self, preset, *, notify):
        self.preset = preset

    def set_super_topmost(self, value, *, notify):
        self.topmost = value

    def show(self):
        self.shown += 1

    def raise_(self):
        pass

    def _stack_target_width(self, kind):
        return 300

    def _main_kind(self):
        return "music"

    def _main_size_for_kind(self, kind, stack_width):
        return stack_width, 54

    def screen(self):
        return SimpleNamespace(geometry=lambda: QRect(0, 0, 1920, 1080))


class FakeSystem:
    def __init__(self, *, playing=False, status="ready"):
        self._provider_status = status
        self.playing = playing
        self.calls = 0

    def get_visual_data(self):
        self.calls += 1
        return SimpleNamespace(track=SimpleNamespace(
            is_playing=self.playing, source_app_id="fixture-player" if self.playing else ""))


class FakeService:
    def __init__(self, *, saved=()):
        self.sources = (MusicSource("netease", "网易云音乐", True),
                        MusicSource("qq", "QQ 音乐", True),
                        MusicSource("qishui", "汽水音乐", False))
        self.saved = set(saved)
        self.calls = []
        self.cancelled = []
        self.invalidated_sessions = []
        self.invalidated_sources = []

    def list_sources(self):
        self.calls.append(("sources", threading.get_ident()))
        return self.sources

    def has_login_record(self, source):
        self.calls.append(("record", source, threading.get_ident()))
        return source in self.saved

    def account_status(self, source, refresh=False):
        self.calls.append(("account", source, threading.get_ident()))
        return AccountSnapshot(source, source in self.saved, "fixture" if source in self.saved else "",
                               "测试账户", state="verified" if source in self.saved else "signed_out")

    def begin_login(self, source):
        self.calls.append(("begin", source, threading.get_ident()))
        return LoginSnapshot("fixture-session", source, "waiting")

    def poll_login(self, session):
        self.calls.append(("poll", session, threading.get_ident()))
        return LoginSnapshot(session, "netease", "waiting")

    def cancel_login(self, session):
        self.cancelled.append(session)

    def invalidate_login(self, session):
        self.invalidated_sessions.append(session)

    def invalidate_source_login(self, source):
        self.invalidated_sources.append(source)


class PlaybackEntryControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_controller(self, *, completed=False, owned=True, system=False,
                        playing=False, status="ready", saved=(), tasks=None, service=None):
        self.settings = FakeSettings(completed=completed, owned=owned, system=system)
        self.window, self.island = FakeWindow(), FakeIsland()
        self.system = FakeSystem(playing=playing, status=status)
        self.service = service or FakeService(saved=saved)
        self.tasks = tasks or FakeTasks()
        self.controller = PlaybackEntryController(
            self.settings, self.system, self.island, self.service,
            window=self.window, tasks=self.tasks)
        self.addCleanup(self.controller.close)
        return self.controller

    def show_sources(self):
        self.controller.show_login()
        self.tasks.run(self.tasks.latest("sources"))

    def start_waiting(self, source="netease", session="fixture-session"):
        self.window.choose_source(source)
        job = self.tasks.latest("begin")
        self.tasks.completed.emit(job[0], LoginSnapshot(session, source, "waiting"), None)
        return job

    def test_first_start_displays_choices_without_account_or_system_io(self):
        self.make_controller().start()
        self.assertEqual(self.window.page, "modes")
        self.assertTrue(self.window.visible)
        self.assertEqual(self.tasks.jobs, [])
        self.assertEqual(self.service.calls, [])
        self.assertEqual(self.system.calls, 0)

    def test_no_mode_selection_is_rejected_without_persistence(self):
        self.make_controller().accept_selection(False, False)
        self.assertEqual(self.settings.saved, 0)
        self.assertFalse(self.settings.get(COMPLETED_KEY))
        self.assertEqual(self.tasks.jobs, [])

    def test_first_owned_only_goes_to_login_without_external_probe(self):
        self.make_controller().accept_selection(True, False)
        self.assertEqual(self.window.page, "sources")
        self.assertTrue(self.settings.get(COMPLETED_KEY))
        self.assertEqual(self.settings.saved, 1)
        self.assertEqual(self.system.calls, 0)
        self.assertEqual(self.service.calls, [])
        self.assertEqual(self.tasks.jobs[-1][0][0], "sources")

    def test_first_system_only_goes_directly_to_existing_top_island(self):
        self.make_controller().accept_selection(False, True)
        self.assertEqual(self.island.shown, 1)
        self.assertEqual((self.island.location, self.island.preset), ("top", "top_center"))
        self.assertTrue(self.island.topmost)
        self.assertEqual(self.tasks.jobs, [])
        self.assertEqual(self.system.calls, 0)

    def test_first_dual_mode_uses_current_system_playback_without_waiting_for_accounts(self):
        self.make_controller(playing=True).accept_selection(True, True)
        self.assertEqual(self.island.shown, 1)
        self.assertFalse(self.controller._probe_timer.isActive())

    def test_first_dual_mode_without_system_playback_goes_to_login(self):
        self.make_controller().accept_selection(True, True)
        self.assertEqual(self.island.shown, 0)
        self.tasks.run(self.tasks.latest("accounts"))
        self.assertEqual(self.window.page, "sources")
        self.assertFalse(self.controller._probe_timer.isActive())

    def test_restart_uses_saved_record_without_network_account_validation(self):
        self.make_controller(completed=True, saved=("qq",)).start()
        self.assertEqual(self.service.calls, [])
        self.assertEqual(self.system.calls, 0)
        self.tasks.run(self.tasks.latest("accounts"))
        self.assertEqual(self.island.shown, 1)
        self.assertTrue(any(call[:2] == ("record", "qq") for call in self.service.calls))
        self.assertFalse(any(call[0] in {"begin", "poll"} for call in self.service.calls))

    def test_restart_without_record_or_playback_goes_to_login(self):
        self.make_controller(completed=True).start()
        self.tasks.run(self.tasks.latest("accounts"))
        self.assertEqual(self.window.page, "sources")
        self.assertEqual(self.island.shown, 0)

    def test_restart_with_system_playback_does_not_wait_for_record_io(self):
        self.make_controller(completed=True, system=True, playing=True).start()
        self.assertEqual(self.island.shown, 1)
        self.assertFalse(self.controller._probe_timer.isActive())

    def test_cancelled_route_cannot_reopen_login_after_system_only_selection(self):
        self.make_controller().accept_selection(True, True)
        old_accounts = self.tasks.latest("accounts")
        self.controller.accept_selection(False, True)
        self.tasks.completed.emit(old_accounts[0], False, None)
        with patch("local.playback_entry_controller.time.monotonic", return_value=10**10):
            self.controller._probe()
        self.assertEqual(self.island.shown, 1)
        self.assertFalse(self.window.visible)
        self.assertFalse(self.controller._probe_timer.isActive())

    def test_expired_first_route_is_bounded_when_account_task_is_pending(self):
        self.make_controller(status="starting").accept_selection(True, True)
        with patch("local.playback_entry_controller.time.monotonic", return_value=10**10):
            self.controller._probe()
        self.assertEqual(self.window.page, "sources")
        self.assertFalse(self.controller._probe_timer.isActive())

    def test_stale_source_catalog_cannot_replace_reopened_catalog(self):
        self.make_controller()
        self.controller.show_login()
        old = self.tasks.latest("sources")
        self.controller.show_login()
        current = self.tasks.latest("sources")
        self.tasks.completed.emit(current[0], self.service.sources, None)
        self.tasks.completed.emit(old[0], (), None)
        self.assertEqual(self.window.sources, self.service.sources)

    def test_disabled_source_does_not_submit_login(self):
        self.make_controller()
        self.show_sources()
        count = len(self.tasks.jobs)
        self.window.choose_source("qishui")
        self.assertEqual(len(self.tasks.jobs), count)

    def test_cancel_clears_qr_polling_and_cancels_session_asynchronously(self):
        self.make_controller()
        self.show_sources()
        self.start_waiting()
        self.window.backRequested.emit()
        self.assertEqual(self.controller._session, "")
        self.assertFalse(self.controller._poll_timer.isActive())
        self.assertEqual(self.service.cancelled, [])
        self.assertEqual(self.service.invalidated_sources, ["netease"])
        self.assertEqual(self.service.invalidated_sessions, ["fixture-session"])
        self.tasks.run(self.tasks.latest("cancel"))
        self.assertEqual(self.service.cancelled, ["fixture-session"])

    def test_refresh_cancels_late_begin_and_keeps_new_session(self):
        self.make_controller()
        self.show_sources()
        self.window.choose_source("netease")
        old = self.tasks.latest("begin")
        self.window.refreshRequested.emit()
        current = self.tasks.latest("begin")
        self.tasks.completed.emit(current[0], LoginSnapshot("new-session", "netease", "waiting"), None)
        count = len(self.window.snapshots)
        self.tasks.completed.emit(old[0], LoginSnapshot("old-session", "netease", "waiting"), None)
        self.tasks.run(self.tasks.latest("cancel"))
        self.assertEqual(self.service.cancelled, ["old-session"])
        self.assertEqual(self.controller._session, "new-session")
        self.assertEqual(len(self.window.snapshots), count)

    def test_source_switch_discards_old_confirmation_and_preserves_current_poll(self):
        self.make_controller()
        self.show_sources()
        self.start_waiting()
        self.controller._poll_login()
        old_poll = self.tasks.latest("poll")
        self.start_waiting("qq", "qq-session")
        self.controller._poll_login()
        current_poll = self.tasks.latest("poll")
        self.tasks.completed.emit(old_poll[0], LoginSnapshot("fixture-session", "netease", "confirmed"), None)
        self.assertTrue(self.controller._poll_pending)
        self.assertNotIn("v2_online_music_source", self.settings.values)
        self.assertEqual(self.controller._session, "qq-session")
        self.tasks.completed.emit(current_poll[0], LoginSnapshot("qq-session", "qq", "scanned"), None)
        self.assertFalse(self.controller._poll_pending)
        self.assertEqual(self.window.snapshots[-1].source_id, "qq")

    def test_only_one_poll_can_be_in_flight_for_a_session(self):
        self.make_controller()
        self.show_sources()
        self.start_waiting()
        self.controller._poll_login()
        self.controller._poll_login()
        self.assertEqual(sum(job[0][0] == "poll" for job in self.tasks.jobs), 1)

    def test_confirmation_saves_source_and_waits_for_user_continue(self):
        self.make_controller()
        self.show_sources()
        job = self.start_waiting()
        self.tasks.completed.emit(job[0], LoginSnapshot("fixture-session", "netease", "confirmed"), None)
        self.assertEqual(self.settings.get("v2_online_music_source"), "netease")
        self.assertEqual(self.settings.saved, 1)
        self.assertFalse(self.controller._poll_timer.isActive())
        self.assertEqual(self.island.shown, 0)
        self.window.loginAccepted.emit("netease")
        self.assertEqual(self.island.shown, 1)
        self.assertEqual(len(self.window.contracts), 1)
        self.assertFalse(self.window.visible)

    def test_request_error_is_safe_and_stops_polling(self):
        self.make_controller()
        self.show_sources()
        job = self.start_waiting()
        self.tasks.completed.emit(job[0], None, "cookie=private-fixture")
        self.assertEqual(self.window.snapshots[-1].state, "error")
        self.assertNotIn("private-fixture", self.window.snapshots[-1].message)
        self.assertFalse(self.controller._poll_timer.isActive())

    def test_saved_account_shows_card_without_starting_new_qr(self):
        self.make_controller(saved=("qq",))
        entered = []
        self.controller.login_callback = entered.append
        self.show_sources()
        self.window.choose_source("qq")
        self.tasks.run(self.tasks.latest("account"))
        self.assertTrue(self.window.account.logged_in)
        self.assertEqual(self.window.account.source_id, "qq")
        self.assertFalse(any(job[0][0] == "begin" for job in self.tasks.jobs))
        self.window.loginAccepted.emit("qq")
        self.assertEqual(entered, ["qq"])
        self.assertEqual(self.island.shown, 0)

    def test_signed_out_account_starts_qr_after_worker_validation(self):
        self.make_controller()
        self.controller.login_callback = lambda source: None
        self.show_sources()
        self.window.choose_source("netease")
        self.assertFalse(any(call[0] == "account" for call in self.service.calls))
        self.tasks.run(self.tasks.latest("account"))
        self.assertEqual(self.tasks.latest("begin")[0][2], "netease")

    def test_switch_source_discards_stale_account_card(self):
        self.make_controller(saved=("netease", "qq"))
        self.controller.login_callback = lambda source: None
        self.show_sources()
        self.window.choose_source("netease")
        old = self.tasks.latest("account")
        self.window.choose_source("qq")
        self.tasks.run(old)
        self.assertFalse(hasattr(self.window, "account"))
        self.tasks.run(self.tasks.latest("account"))
        self.assertEqual(self.window.account.source_id, "qq")

    def test_production_restart_enters_saved_library_when_system_is_idle(self):
        self.make_controller(completed=True, saved=("qq",))
        entered = []
        self.controller.login_callback = entered.append
        self.controller.start()
        self.tasks.run(self.tasks.latest("accounts"))
        self.tasks.run(self.tasks.latest("sources"))
        self.tasks.run(self.tasks.latest("account"))
        self.assertEqual(entered, ["qq"])
        self.assertEqual(self.island.shown, 0)

    def test_production_confirmed_login_opens_library_immediately(self):
        self.make_controller()
        entered = []
        self.controller.login_callback = entered.append
        self.show_sources()
        self.window.choose_source("netease")
        self.tasks.run(self.tasks.latest("account"))
        job = self.tasks.latest("begin")
        self.tasks.completed.emit(job[0], LoginSnapshot("new", "netease", "confirmed"), None)
        self.assertEqual(entered, ["netease"])
        self.assertFalse(self.window.visible)
        self.assertEqual(self.island.shown, 0)

    def test_close_stops_timers_tasks_and_ignores_late_ui_results(self):
        self.make_controller()
        self.show_sources()
        job = self.start_waiting()
        before = len(self.window.snapshots)
        self.controller.close()
        self.tasks.completed.emit(job[0], LoginSnapshot("fixture-session", "netease", "confirmed"), None)
        self.assertTrue(self.tasks.closed)
        self.assertIn("fixture-session", self.service.invalidated_sessions)
        self.assertFalse(self.controller._poll_timer.isActive())
        self.assertFalse(self.controller._probe_timer.isActive())
        self.assertFalse(self.window.visible)
        self.assertEqual(len(self.window.snapshots), before)
        self.assertNotIn("v2_online_music_source", self.settings.values)

    def test_account_and_qr_io_run_off_qt_thread_while_gui_remains_responsive(self):
        service = FakeService(saved=("qq",))
        entered, release = threading.Event(), threading.Event()
        original_begin = service.begin_login

        def blocked_begin(source):
            entered.set()
            release.wait(timeout=2.0)
            return original_begin(source)

        service.begin_login = blocked_begin
        tasks = MusicTasks()
        self.make_controller(completed=True, tasks=tasks, service=service).start()
        self.addCleanup(release.set)
        deadline = time.monotonic() + 2.0
        while self.island.shown == 0 and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertEqual(self.island.shown, 1)
        self.controller.show_login()
        while not self.window.sources and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(self.window.sources)
        self.window.choose_source("netease")
        self.assertTrue(entered.wait(timeout=1.0))
        heartbeats = []
        QTimer.singleShot(0, lambda: heartbeats.append(True))
        self.app.processEvents()
        self.assertEqual(heartbeats, [True])
        self.assertEqual(self.window.snapshots[-1].state, "starting")
        release.set()
        while self.window.snapshots[-1].state == "starting" and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertEqual(self.window.snapshots[-1].state, "waiting")
        gui_thread = threading.get_ident()
        self.assertTrue(service.calls)
        self.assertTrue(all(call[-1] != gui_thread for call in service.calls))


if __name__ == "__main__":
    unittest.main()
