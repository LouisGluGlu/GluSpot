"""First-run routing and asynchronous QR lifecycle for the existing V2 island."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QRect, QTimer

from api.online_music import LoginSnapshot
from local.music_tasks import MusicTasks
from local.playback_entry_window import PlaybackEntryWindow


OWNED_KEY = "v2_owned_playback_enabled"
SYSTEM_KEY = "v2_system_capture_enabled"
COMPLETED_KEY = "v2_playback_onboarding_completed"


def startup_destination(*, first_run, owned, system, playing, has_login):
    """Only explicit user requirements decide the destination."""
    if first_run:
        if system and not owned:
            return "island"
        return "island" if system and playing else "login"
    if not playing and not has_login:
        return "login"
    return "island"


class PlaybackEntryController(QObject):
    def __init__(self, settings, system_provider, island, service, parent=None, *, window=None, tasks=None, login_callback=None):
        super().__init__(parent)
        self.settings = settings
        self.system = system_provider
        self.island = island
        self.service = service
        self.login_callback = login_callback
        self.window = window or PlaybackEntryWindow(
            owned=bool(settings.get(OWNED_KEY, True)), system=bool(settings.get(SYSTEM_KEY, False)))
        self.tasks = tasks or MusicTasks(self)
        self.tasks.completed.connect(self._completed)
        self.window.selectionAccepted.connect(self.accept_selection)
        self.window.sourceSelected.connect(self.select_source)
        self.window.refreshRequested.connect(lambda: self.begin_login(self.window.selected_source))
        self.window.closed.connect(self._cancel_login)
        self.window.backRequested.connect(self._cancel_login)
        self.window.loginAccepted.connect(self.enter_account)
        self._generation = 0
        self._route_generation = 0
        self._session = ""
        self._attempt_source = ""
        self._poll_pending = False
        self._closed = False
        self._sources = ()
        self._has_login = False
        self._saved_sources = ()
        self._route_pending = None
        self._probe_deadline = 0.0
        self._probe_timer = QTimer(self)
        self._probe_timer.setInterval(100)
        self._probe_timer.timeout.connect(self._probe)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._poll_login)

    def start(self):
        if not bool(self.settings.get(COMPLETED_KEY, False)):
            self.window.show_choices()
            self.window.show()
            self.window.raise_()
        else:
            self._start_route(first_run=False)

    def accept_selection(self, owned, system):
        if not owned and not system:
            return
        self.settings.set(OWNED_KEY, bool(owned))
        self.settings.set(SYSTEM_KEY, bool(system))
        self.settings.set(COMPLETED_KEY, True)
        self.settings.save_now()
        self._start_route(first_run=True)

    def _start_route(self, *, first_run):
        self._route_generation += 1
        owned = bool(self.settings.get(OWNED_KEY, True))
        system = bool(self.settings.get(SYSTEM_KEY, False))
        if first_run and system and not owned:
            self._show_island()
            return
        if first_run and not system:
            self.show_login()
            return
        self._route_pending = (first_run, owned, system)
        self._probe_deadline = time.monotonic() + 4.0
        self._has_login = False
        self._accounts_ready = False
        generation = self._route_generation
        def accounts():
            # DPAPI/disk work remains off the GUI thread. This checks persisted
            # account records without waiting for a live internet validation.
            return tuple(source.source_id for source in self.service.list_sources()
                         if self.service.has_login_record(source.source_id))
        if not self.tasks.submit(("accounts", generation), accounts):
            self._accounts_ready = True
        self._probe_timer.start()
        self._probe()

    def _probe(self):
        if self._route_pending is None:
            self._probe_timer.stop()
            return
        first_run, owned, system = self._route_pending
        playing, ready = False, not system
        if system:
            snapshot = self.system.get_visual_data()
            playing = bool(snapshot.track.is_playing and snapshot.track.source_app_id)
            status = getattr(self.system, "_provider_status", "starting")
            ready = status in {"ready", "no_session", "missing_winsdk"}
        expired = time.monotonic() >= self._probe_deadline
        if not playing and not expired and (not ready or not self._accounts_ready):
            return
        if not self._accounts_ready and not first_run and not playing:
            return
        destination = startup_destination(first_run=first_run, owned=owned, system=system,
                                          playing=playing, has_login=self._has_login)
        self._route_pending = None
        self._probe_timer.stop()
        if destination == "login":
            self.show_login()
        elif owned and not playing and self._has_login and self.login_callback is not None:
            preferred = self.settings.get("v2_online_music_source", "")
            if preferred not in self._saved_sources:
                preferred = self._saved_sources[0] if self._saved_sources else ""
            self.show_login(preferred, enter_saved=True)
        else:
            self._show_island()

    def show_login(self, source_id="", *, enter_saved=False):
        if self._closed:
            return
        self._route_pending = None
        self._route_generation += 1
        self._probe_timer.stop()
        self._cancel_login()
        self._enter_saved_source = ""
        self.window.set_mode_back_enabled(False)
        self.window.show_login()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        generation = self._generation
        self._select_after_sources = (source_id, enter_saved)
        if not self.tasks.submit(("sources", generation), self.service.list_sources):
            self.window.set_sources(())

    def select_source(self, source_id):
        if self.login_callback is None:
            self.begin_login(source_id)
            return
        if self._closed or source_id not in {s.source_id for s in self._sources if s.available}:
            return
        self._cancel_login()
        generation = self._generation
        self.window.set_login_snapshot(LoginSnapshot("", source_id, "loading", message="正在读取账号…"))
        if not self.tasks.submit(("account", generation, source_id),
                lambda: self.service.account_status(source_id, refresh=True)):
            self.window.set_login_snapshot(LoginSnapshot("", source_id, "error", message="服务繁忙，请稍后重试"))

    def enter_account(self, source_id):
        if self.login_callback is None:
            self._show_island()
            return
        self._cancel_login()
        self.window.hide()
        self.login_callback(source_id)

    def begin_login(self, source_id):
        if self._closed or source_id not in {source.source_id for source in self._sources if source.available}:
            return
        self._cancel_login()
        self._attempt_source = source_id
        generation = self._generation
        self.window.set_login_snapshot(LoginSnapshot("", source_id, "starting", message="正在获取登录二维码…"))
        if not self.tasks.submit(("begin", generation, source_id), lambda: self.service.begin_login(source_id)):
            self.window.set_login_snapshot(LoginSnapshot("", source_id, "error", message="服务繁忙，请稍后刷新"))

    def _poll_login(self):
        if not self._session or self._poll_pending or self._closed:
            return
        session = self._session
        self._poll_pending = self.tasks.submit(("poll", self._generation, self.window.selected_source),
                                               lambda: self.service.poll_login(session))

    def _cancel_login(self):
        self._generation += 1
        self._poll_timer.stop()
        self._poll_pending = False
        session, self._session = self._session, ""
        source, self._attempt_source = self._attempt_source, ""
        if source:
            self.service.invalidate_source_login(source)
        if session:
            self.service.invalidate_login(session)
        if session:
            self.tasks.submit(("cancel", self._generation), lambda: self.service.cancel_login(session))

    def _completed(self, token, result, error):
        if self._closed:
            return
        kind, generation, *extra = token
        if kind == "accounts":
            if generation == self._route_generation:
                self._has_login = bool(result) if not error else False
                self._saved_sources = tuple(result) if isinstance(result, (tuple, list)) and not error else ()
                self._accounts_ready = True
                self._probe()
            return
        if kind == "begin" and generation != self._generation:
            if result is not None and getattr(result, "session_id", ""):
                self.tasks.submit(("cancel", self._generation), lambda: self.service.cancel_login(result.session_id))
            return
        if generation != self._generation:
            return
        if kind == "sources":
            self._sources = result or ()
            self.window.set_sources(self._sources)
            source, enter_saved = getattr(self, "_select_after_sources", ("", False))
            self._select_after_sources = ("", False)
            if source in {s.source_id for s in self._sources if s.available}:
                self._enter_saved_source = source if enter_saved else ""
                self.window.select_source(source)
            return
        if kind == "account":
            source = extra[0]
            if error or result is None or result.state == "error":
                self.window.set_login_snapshot(LoginSnapshot("", source, "error", message="账号读取失败，请重试"))
            elif result.logged_in:
                self.window.set_account(result)
                if result.avatar_url:
                    from api.online_music_artwork import fetch_online_thumbnail
                    self.tasks.submit(("avatar", generation, source), lambda: fetch_online_thumbnail(result.avatar_url)
                                      if not self._closed and generation == self._generation else None)
                if getattr(self, "_enter_saved_source", "") == source:
                    self._enter_saved_source = ""
                    self.enter_account(source)
            else:
                self.begin_login(source)
            return
        if kind == "avatar":
            if not error and result:
                from PySide6.QtGui import QImage
                if isinstance(result, QImage) and not result.isNull():
                    self.window.set_account_artwork(result)
            return
        if kind not in {"begin", "poll"}:
            return
        self._poll_pending = False
        if error or result is None:
            self._poll_timer.stop()
            self.window.set_login_snapshot(LoginSnapshot(self._session, extra[0], "error",
                message="登录请求失败，请刷新二维码重试"))
            return
        self._session = result.session_id
        self.window.set_login_snapshot(result)
        if result.state in {"waiting", "scanned", "loading"}:
            self._poll_timer.start()
        else:
            self._poll_timer.stop()
        if result.state == "confirmed":
            self.settings.set("v2_online_music_source", result.source_id)
            self.settings.save_now()
            if self.login_callback is not None:
                self.enter_account(result.source_id)

    def _show_island(self):
        self._route_pending = None
        self._route_generation += 1
        self._probe_timer.stop()
        self._cancel_login()
        self.island.set_location_mode("top", notify=False)
        self.island.apply_position_preset("top_center", notify=False)
        self.island.set_super_topmost(True, notify=False)
        self.island.show()
        self.island.raise_()
        if self.window.isVisible():
            kind = self.island._main_kind()
            stack_width = self.island._stack_target_width(kind)
            width, height = (round(value) for value in self.island._main_size_for_kind(kind, stack_width))
            screen = self.island.screen().geometry()
            target = QRect(screen.center().x() - width // 2, screen.top(), width, height)
            self.window.contract_to(target, self.window.hide)

    def close(self):
        self._cancel_login()
        self._closed = True
        self._probe_timer.stop()
        self.tasks.close()
        self.window.close()
