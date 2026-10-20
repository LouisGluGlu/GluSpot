"""First-run routing and asynchronous QR lifecycle for the existing V2 island."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QTransform

from api.online_music import LoginSnapshot
from local.music_tasks import MusicTasks
from local.playback_entry_window import ChoicePlacement, PlaybackEntryWindow
from local.music_navigation_transition import MusicNavigationTransition, capture_navigation_frame
from local.runtime_platform import normalize_playback_selection, owned_playback_only, system_media_unavailable_reason


OWNED_KEY = "v2_owned_playback_enabled"
SYSTEM_KEY = "v2_system_capture_enabled"
COMPLETED_KEY = "v2_playback_onboarding_completed"


def startup_destination(*, first_run, owned, system, playing, has_login):
    """Only explicit user requirements decide the destination."""
    if first_run:
        return "login" if owned else "island"
    if not playing and not has_login:
        return "login"
    return "island"


def system_session_hint(status, snapshot=None):
    """Only published SMTC state is observable; a player's private switch is not."""
    if owned_playback_only():
        return system_media_unavailable_reason()
    track = getattr(snapshot, "track", None)
    if getattr(track, "source_app_id", ""):
        return "已检测到 Windows SMTC 会话，可同步外部播放器。"
    if status == "missing_winsdk":
        return "系统媒体组件不可用，请修复 Windows SMTC 运行依赖。"
    if status == "starting":
        return "正在等待 Windows SMTC 会话；你可以先继续。"
    return "未发现 Windows SMTC 会话。若音乐正在播放，请在播放器中开启系统媒体控制（SMTC）；GluSpot 无法读取其内部开关。"


class PlaybackEntryController(QObject):
    def __init__(self, settings, system_provider, island, service, parent=None, *, window=None, tasks=None, login_callback=None):
        super().__init__(parent)
        self.settings = settings
        self.system = system_provider
        self.island = island
        self.service = service
        self.login_callback = login_callback
        self.window = window or PlaybackEntryWindow(
            owned=bool(settings.get(OWNED_KEY, True)), system=bool(settings.get(SYSTEM_KEY, True)))
        self.tasks = tasks or MusicTasks(self)
        # Optional avatar I/O must not occupy the two authentication workers.
        # One bounded existing worker queue is enough for this selected account.
        self._account_art_tasks = MusicTasks(self, worker_count=1) if tasks is None else tasks
        if self._account_art_tasks is not self.tasks:
            self._account_art_tasks.completed.connect(self._completed)
        self.tasks.completed.connect(self._completed)
        self.window.selectionAccepted.connect(self.accept_selection)
        self.window.sourceSelected.connect(self.select_source)
        self.window.refreshRequested.connect(self._refresh_login)
        self.window.closed.connect(self._entry_closed)
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
        self._navigation = None
        self._probe_deadline = 0.0
        self._probe_timer = QTimer(self)
        self._probe_timer.setInterval(100)
        self._probe_timer.timeout.connect(self._probe)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._poll_login)
        self._hint_timer = QTimer(self)
        self._hint_timer.setInterval(1000)
        self._hint_timer.timeout.connect(self._refresh_system_hint)
        self._placement_timer = QTimer(self)
        self._placement_timer.setInterval(100)
        self._placement_timer.timeout.connect(self._refresh_choices_placement)
        self._choices_screen_anchor = QPoint()
        self._account_source = ""

    def _refresh_login(self):
        source = self.window.selected_source
        if getattr(self, "_verification_retry", False) and self._session:
            session, generation = self._session, self._generation
            self._verification_retry = False
            self._poll_pending = self.tasks.submit(("poll", generation, source),
                lambda: self.service.retry_login_verification(session)
                if not self._closed and generation == self._generation else None)
            return
        if getattr(self, "_account_retry_source", "") == source:
            self.select_source(source)
        else:
            self.begin_login(source)

    def _refresh_system_hint(self):
        if self._closed or self.window.page != "modes" or not self.window.isVisible():
            self._hint_timer.stop()
            return
        setter = getattr(self.window, "set_system_session_hint", None)
        if setter is not None:
            setter(system_session_hint(getattr(self.system, "_provider_status", "starting"),
                                       getattr(self.system, "_last_data", None)))

    def _entry_closed(self):
        self._hint_timer.stop()
        self._placement_timer.stop()
        if self._navigation is not None:
            self._navigation.cancel()
        if not self._closed:
            self._show_island()

    def start(self, installer_handoff: QRect | None = None):
        if not bool(self.settings.get(COMPLETED_KEY, False)):
            self._position_startup_island()
            origin = installer_handoff if isinstance(installer_handoff, QRect) and installer_handoff.isValid() else self._island_target_geometry()
            self._choices_screen_anchor = origin.center()
            self._refresh_choices_placement(require_visible=False)
            choices_from = getattr(self.window, "show_choices_from", None)
            if choices_from is not None:
                choices_from(origin)
            else:
                self.window.show_choices()
            self.window.show()
            self.window.raise_()
            self._refresh_system_hint()
            self._hint_timer.start()
            self._placement_timer.start()
        else:
            self._start_route(first_run=False)

    def accept_selection(self, owned, system):
        owned, system = normalize_playback_selection(owned, system)
        if not owned and not system:
            return
        self._hint_timer.stop()
        self._placement_timer.stop()
        self.settings.set(OWNED_KEY, bool(owned))
        self.settings.set(SYSTEM_KEY, bool(system))
        self.settings.set(COMPLETED_KEY, True)
        self.settings.save_now()
        self._start_route(first_run=True)

    def _start_route(self, *, first_run):
        self._route_generation += 1
        owned = bool(self.settings.get(OWNED_KEY, True))
        system = bool(self.settings.get(SYSTEM_KEY, True))
        if first_run:
            # The explicit program-playback selection is an instruction to
            # connect an account, even when an external SMTC session exists.
            if owned:
                self.show_login()
            else:
                self._show_island()
            return
        self._route_pending = (first_run, owned, system)
        self._probe_deadline = time.monotonic() + 4.0
        self._has_login = False
        self._accounts_ready = False
        generation = self._route_generation
        def accounts():
            if self._closed or generation != self._route_generation:
                return None
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
        if self._closed or self._shell_hidden():
            return
        self._placement_timer.stop()
        self._route_pending = None
        self._route_generation += 1
        self._probe_timer.stop()
        self._cancel_login()
        self._enter_saved_source = ""
        self.window.set_mode_back_enabled(False)
        prepare_geometry = getattr(self.window, "_prepare_shared_music_geometry", None)
        if callable(prepare_geometry):
            prepare_geometry()
        self.window.show_login()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        frame = getattr(self, "_library_origin", None)
        self._library_origin = None
        if frame is not None:
            if self._navigation is None:
                self._navigation = MusicNavigationTransition()
            self._navigation.start(frame, self.window)
        generation = self._generation
        self._select_after_sources = (source_id, enter_saved)
        if not self.tasks.submit(("sources", generation), lambda: self.service.list_sources()
                if not self._closed and generation == self._generation else None):
            self.window.set_sources(())

    def select_source(self, source_id):
        if self.login_callback is None:
            self.begin_login(source_id)
            return
        if self._closed or source_id not in {s.source_id for s in self._sources if s.available}:
            return
        self._cancel_login()
        self._account_source = source_id
        self._account_retry_source = ""
        generation = self._generation
        self.window.set_login_snapshot(LoginSnapshot("", source_id, "loading", message="正在读取账号…"))
        def account():
            if self._closed or generation != self._generation:
                return None
            # A card verified in this session is already the provider's source
            # of truth. Do not repeat the live login-status request on every
            # return from the library; logged-out/startup records still validate.
            restore = getattr(self.service, "restore_account", None)
            cached = restore(source_id) if callable(restore) else self.service.account_status(source_id)
            if cached.logged_in or self._closed or generation != self._generation:
                return cached
            if cached.state == "cached":
                return cached
            return self.service.account_status(source_id, refresh=True)
        if not self.tasks.submit(("account", generation, source_id), account):
            self.window.set_login_snapshot(LoginSnapshot("", source_id, "error", message="服务繁忙，请稍后重试"))

    def enter_account(self, source_id):
        if self._closed or self._shell_hidden():
            return
        if self.login_callback is None:
            self._show_island()
            return
        self._cancel_login()
        self._library_origin = capture_navigation_frame(self.window)
        self.window.hide()
        self.login_callback(source_id)

    def begin_login(self, source_id):
        if self._closed or source_id not in {source.source_id for source in self._sources if source.available}:
            return
        self._cancel_login()
        self._attempt_source = source_id
        generation = self._generation
        self.window.set_login_snapshot(LoginSnapshot("", source_id, "starting", message="正在获取登录二维码…"))
        if not self.tasks.submit(("begin", generation, source_id), lambda: self.service.begin_login(source_id)
                if not self._closed and generation == self._generation else None):
            self.window.set_login_snapshot(LoginSnapshot("", source_id, "error", message="服务繁忙，请稍后刷新"))

    def _poll_login(self):
        if not self._session or self._poll_pending or self._closed:
            return
        session = self._session
        generation = self._generation
        self._poll_pending = self.tasks.submit(("poll", generation, self.window.selected_source),
            lambda: self.service.poll_login(session)
            if not self._closed and generation == self._generation and session == self._session else None)

    def _cancel_login(self):
        self._generation += 1
        self._poll_timer.stop()
        self._poll_pending = False
        self._verification_retry = False
        session, self._session = self._session, ""
        source, self._attempt_source = self._attempt_source, ""
        account_source = self._account_source
        self._account_source = ""
        discard = getattr(self.tasks, "discard", None)
        if callable(discard):
            discard(lambda token: token[0] not in {"accounts"} and token[1] != self._generation)
        if self._account_art_tasks is not self.tasks:
            self._account_art_tasks.discard(lambda token: token[1] != self._generation)
        if source:
            self.service.invalidate_source_login(source)
        elif account_source:
            cancel = getattr(self.service, "cancel_account_validation", None)
            if callable(cancel):
                cancel(account_source)
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
            if result is not None and getattr(result, "session_id", "") and result.state != "cancelled":
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
            generation = self._generation
            if self.window.isVisible() and self.window.page == "sources":
                available = tuple(source.source_id for source in self._sources if source.available)
                selected = source or self.settings.get("v2_online_music_source", "netease")
                sources = (selected,) if selected in available else available[:1]
                def prepare():
                    for source_id in sources:
                        if self._closed or generation != self._generation:
                            break
                        self.service.prepare_login_runtime(source_id)
                self.tasks.submit(("prepare", generation), prepare)
            return
        if kind == "account":
            source = extra[0]
            self._account_source = ""
            if result is not None and result.state == "cached":
                # The library displays this account's protected local snapshot
                # while independently validating; logged_in remains False.
                self.enter_account(source)
                return
            if error or result is None or result.state == "error":
                self._account_retry_source = source
                self.window.set_login_snapshot(LoginSnapshot("", source, "error", message="账号读取失败，请重试"))
            elif result.logged_in:
                self.window.set_account(result)
                if result.avatar_url:
                    from api.online_music_artwork import fetch_online_thumbnail
                    self._account_art_tasks.submit(("avatar", generation, source), lambda: fetch_online_thumbnail(result.avatar_url)
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
                message="二维码获取失败，请重试" if kind == "begin" else "扫码状态检测失败，请重试",
                failure_category=str(error) if str(error) in {"network", "timeout", "runtime", "credential", "cancelled"} else "request_failed",
                failure_stage="begin_login" if kind == "begin" else "poll_login"))
            return
        self._session = result.session_id
        self._verification_retry = result.state == "error" and result.failure_stage == "account_status"
        self.window.set_login_snapshot(result)
        if result.state in {"waiting", "scanned", "loading"}:
            self._poll_timer.setInterval(max(1000, round(getattr(result, "retry_after_seconds", 0.) * 1000)))
            self._poll_timer.start()
        else:
            self._poll_timer.stop()
        if result.state == "confirmed":
            self.settings.set("v2_online_music_source", result.source_id)
            self.settings.save_now()
            if self.login_callback is not None:
                self.enter_account(result.source_id)

    def _show_island(self):
        if self._navigation is not None:
            self._navigation.cancel()
        self.island._music_library_active = False
        self._route_pending = None
        self._route_generation += 1
        self._probe_timer.stop()
        self._cancel_login()
        if self._shell_hidden():
            return
        self._position_startup_island()
        self.island.show()
        self.island.raise_()
        if self.window.isVisible():
            self.window.contract_to(self._island_target_geometry(), self.window.hide)

    def _position_startup_island(self):
        # Use the island's existing validated Finder-bar presence. A running
        # process alone does not prove that its bar is enabled or available.
        finder_available = getattr(self.island, "_finder_bar_available", None)
        finder = bool(finder_available is not None and finder_available())
        self.island.set_location_mode("finder" if finder else "top", notify=False)
        if not finder:
            self.island.apply_position_preset("top_center", notify=False)
        self.island.set_super_topmost(True, notify=False)

    def _refresh_choices_placement(self, *, require_visible=True):
        """Read existing validated, mapped bar state; never probe native windows."""
        if self._closed or (require_visible and (self.window.page != "modes" or not self.window.isVisible())):
            self._placement_timer.stop()
            return
        setter = getattr(self.window, "set_choices_placement", None)
        if setter is None:  # Controller-only fixtures do not own a painted shell.
            return
        available = getattr(self.island, "_finder_bar_available", None)
        bar = QRectF(getattr(self.island, "finder_bar_rect", QRectF()))
        finder = bool(available is not None and available() and not bar.isEmpty())
        if finder:
            unit = self.island._u
            # These are the expanded main-panel pads used by the existing
            # Finder frame; its shared helper owns the lateral cradle geometry.
            pad_x, pad_y = unit(9.0), unit(8.0)
            body = QRectF(0, 0, min(370.0, max(1.0, bar.width() - 2 * (pad_x + unit(32)))), 176)
            body.moveTop(bar.bottom() + unit(4))
            ratio = float(getattr(self.island, "finder_bar_center_ratio", .5))
            body.moveLeft(bar.left() + bar.width() * ratio - body.width() / 2)
            shell = self.island._myfinder_expanded_shell_path(body.adjusted(-pad_x, -pad_y, pad_x, pad_y))
            # Keep both sides of the actual extended shell on its real bar.
            bounds = shell.boundingRect()
            shift = max(0.0, bar.left() - bounds.left()) - max(0.0, bounds.right() - bar.right())
            if shift:
                body.translate(shift, 0)
                shell = self.island._myfinder_expanded_shell_path(body.adjusted(-pad_x, -pad_y, pad_x, pad_y))
            geometry = shell.boundingRect().toAlignedRect()
            body.translate(-geometry.x(), -geometry.y())
            shell = QTransform.fromTranslate(-geometry.x(), -geometry.y()).map(shell)
            placement = ChoicePlacement("finder", geometry, body, shell,
                                        self.island._finder_material_display_color())
        else:
            screen = QGuiApplication.screenAt(self._choices_screen_anchor) or QGuiApplication.primaryScreen()
            if screen is None:
                return
            # Top is the physical display edge, even when availableGeometry
            # reserves a visible menu bar or taskbar above its work area.
            bounds = screen.geometry()
            width, height = min(370, bounds.width()), min(176, bounds.height())
            geometry = QRect(bounds.center().x() - (width - 1) // 2, bounds.top(), width, height)
            body = QRectF(0, 0, width, height)
            shell = self.island._edge_notch_path(body, "top")
            placement = ChoicePlacement("top", geometry, body, shell, QColor("#000000"))
        setter(placement)

    def _island_target_geometry(self) -> QRect:
        target_getter = getattr(self.island, "_current_main_target_global_rect", None)
        if target_getter is not None:
            target = target_getter()
            if target is not None and target.isValid():
                return target.toAlignedRect() if hasattr(target, "toAlignedRect") else QRect(target)
        kind = self.island._main_kind()
        stack_width = self.island._stack_target_width(kind)
        width, height = (round(value) for value in self.island._main_size_for_kind(kind, stack_width))
        screen = self.island.screen().geometry()
        return QRect(screen.center().x() - width // 2, screen.top(), width, height)

    def _shell_hidden(self):
        return (bool(getattr(self.island, "_tray_hidden", False)) or
                bool(getattr(self.island, "_application_quitting", False)))

    def close(self):
        if self._navigation is not None:
            self._navigation.cancel()
        frame, self._library_origin = getattr(self, "_library_origin", None), None
        if frame is not None and hasattr(frame, "release"):
            frame.release()
        self._cancel_login()
        self._closed = True
        self._hint_timer.stop()
        self._placement_timer.stop()
        self._probe_timer.stop()
        self.tasks.close()
        if self._account_art_tasks is not self.tasks:
            self._account_art_tasks.close()
        self.window.close()
