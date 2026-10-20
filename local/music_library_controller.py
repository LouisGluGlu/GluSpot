"""Account-scoped catalog I/O and handoff to the existing V2 music surface."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import time

from PySide6.QtCore import QEvent, QObject, QRect, QTimer
from PySide6.QtGui import QGuiApplication, QImage

from api.online_music import AccountSnapshot, OnlinePlaylist, OnlineSong
from api.online_music_artwork import fetch_online_thumbnail
from api.online_music_diagnostics import record_login_phase
from local.music_library_window import LibraryCollection, MusicLibraryWindow
from local.music_tasks import MusicTasks
from local.music_navigation_transition import MusicNavigationTransition


class MusicLibraryController(QObject):
    def __init__(self, settings, service, player, island, entry, parent=None, *, window=None, tasks=None, art_tasks=None):
        super().__init__(parent)
        self.settings, self.service, self.player = settings, service, player
        self.island, self.entry = island, entry
        self.window = window or MusicLibraryWindow()
        self.window.set_background_context(settings.get, getattr(island, "_run_background", None))
        settings_changed = getattr(settings, "setting_changed", None)
        if settings_changed is not None:
            settings_changed.connect(self.window.on_background_setting_changed)
        self.tasks = tasks or MusicTasks(self)
        self.art_tasks = art_tasks or MusicTasks(self)
        self.tasks.completed.connect(self._completed)
        self.art_tasks.completed.connect(self._art_completed)
        self.window.sourceSwitchRequested.connect(self.switch_source)
        self.window.backRequested.connect(self.back)
        self.window.logoutRequested.connect(self.logout)
        self.window.collectionRequested.connect(self.play_collection)
        now_playing = getattr(self.window, "nowPlayingRequested", None)
        if now_playing is not None:
            now_playing.connect(self.open_now_playing)
        self.window.queueNextRequested.connect(self.queue_next)
        self.window.libraryExpandChanged.connect(self._save_expanded)
        self.window.searchRequested.connect(self.search)
        self.window.search.textChanged.connect(self._search_edited)
        self.window.moreRequested.connect(self.load_more)
        self.window.artworkNeeded.connect(self._queue_art)
        self.window.closed.connect(self._library_closed)
        entry_closed = getattr(getattr(self.entry, "window", None), "closed", None)
        if entry_closed is not None:
            entry_closed.connect(self._entry_closed)
        self.player.changed.connect(self._player_changed)
        self.player.statusChanged.connect(self._player_status)
        self._epoch = 0
        self._request = 0
        self._source = ""
        self._query = ""
        self._collections = ()
        self._search_results = ()
        self._offsets = {"songs": 0, "playlists": 0}
        self._more = {"songs": False, "playlists": False}
        self._pending = False
        self._catalog_pending = False
        self._logout_pending = False
        self._opening = None
        self._closed = False
        self._pending_collection = None
        self._pending_row = None
        self._opening_origin_guard = False
        self._opening_origin_before = self._opening_origin = None
        self._active_collection = None
        self._library_geometry = QRect()
        self._library_placement_epoch = 0
        self._art_queue = deque()
        self._art_inflight = 0
        self._art_keys = set()
        self._catalog_art_pending = deque()
        self._catalog_art_seen = set()
        self._art_retry_counts = {}
        self._art_retry_at = {}
        self._art_cache_sources = {}
        self._recommendation_art_pending = set()
        self._recommendation_art_retries = {}
        self._recommendation_art_retry_at = {}
        self._art_retry_timer = QTimer(self)
        self._art_retry_timer.setSingleShot(True)
        self._art_retry_timer.timeout.connect(self._retry_art)
        self._recommended_pending = False
        self._recommended_offset = 0
        self._recommended_has_more = False
        self._recommended_retry = False
        self._catalog_retry_at = {}
        self._catalog_retry_counts = {}
        self._search_retry_kinds = ()
        self._search_pending_kinds = set()
        self._search_failed_kinds = set()
        self._search_batch_base = ()
        self._search_batch_pages = {}
        self._catalog_retry_timer = QTimer(self)
        self._catalog_retry_timer.setSingleShot(True)
        self._catalog_retry_timer.timeout.connect(self._retry_catalog)
        # Login, library and playback are one visual journey. Share its
        # existing overlay so a fast reverse samples the currently visible
        # pose instead of starting a second independent handoff window.
        self._navigation = getattr(entry, "_navigation", None) or MusicNavigationTransition()
        entry._navigation = self._navigation
        self.window.set_library_expanded(bool(settings.get("v2_library_expanded", False)), animate=False)
        self.window.set_now_playing(self.player.track)
        self._minimum_surface_sync_pending = False
        self._navigation_windows = tuple(window for window in (self.window, getattr(self.entry, "window", None))
                                         if window is not None)
        self._music_geometry_applying = False
        available = (QGuiApplication.screenAt(self.window.frameGeometry().center())
                     or QGuiApplication.primaryScreen())
        area = available.availableGeometry() if available is not None else QRect(0, 0, 1920, 1080)
        self._library_geometry = QRect(area.x() + (area.width() - self.window.width()) // 2,
                                       area.y() + (area.height() - self.window.height()) // 2,
                                       self.window.width(), self.window.height())
        self._publish_shared_music_geometry(self._library_geometry)
        entry_window = getattr(self.entry, "window", None)
        if entry_window is not None:
            entry_window._prepare_shared_music_geometry = self._capture_player_geometry
        self.island._music_library_windows = self._navigation_windows
        for window in self._navigation_windows:
            window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (watched in getattr(self, "_navigation_windows", ())
                and event.type() in (QEvent.Type.Move, QEvent.Type.Resize)
                and watched.isVisible() and not self._music_geometry_applying
                and getattr(watched, "page", "sources") == "sources"
                and getattr(watched, "_contraction_target", None) is None
                and getattr(watched, "_page_morph_to", QImage()).isNull()):
            self._publish_shared_music_geometry(watched.geometry())
        if watched is self.window and event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress):
            # A user's new interaction owns placement over delayed native-map
            # repair callbacks from the preceding page handoff.
            self._library_placement_epoch += 1
        if watched is self.window and event.type() in (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.WindowStateChange):
            QTimer.singleShot(0, self._arm_catalog_retry)
            QTimer.singleShot(0, self._arm_art_retry)
        if (watched in getattr(self, "_navigation_windows", ())
                and event.type() in (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.WindowStateChange)
                and not self._minimum_surface_sync_pending):
            self._minimum_surface_sync_pending = True
            # Complete a page-to-page handoff before deciding which surface remains.
            QTimer.singleShot(0, self._sync_minimum_surface)
        return super().eventFilter(watched, event)

    def _publish_shared_music_geometry(self, geometry):
        """One normal-window rectangle belongs to login, library and playback."""
        if not geometry.isValid():
            return
        self._library_geometry = QRect(geometry)
        self.island._music_page_geometry = QRect(geometry)
        entry_window = getattr(self.entry, "window", None)
        if entry_window is not None:
            entry_window._shared_music_geometry = QRect(geometry)
        surface = getattr(self.island, "music_fullscreen_overlay", None)
        if surface is not None:
            surface._normal_geometry = QRect(geometry)

    def _capture_player_geometry(self):
        surface = getattr(self.island, "music_fullscreen_overlay", None)
        geometry = getattr(surface, "_normal_geometry", QRect())
        if geometry.isValid():
            self._publish_shared_music_geometry(geometry)

    def _sync_minimum_surface(self):
        self._minimum_surface_sync_pending = False
        if not self._closed:
            sync = getattr(self.island, "_sync_minimum_surface", None)
            if callable(sync):
                sync()

    def _save_expanded(self, expanded):
        self.settings.set("v2_library_expanded", bool(expanded))
        self.settings.save_now()

    def _remember_surface(self):
        overlay = getattr(self.island, "music_fullscreen_overlay", None)
        if getattr(self.island, "_music_surface_active", False) and overlay is not None:
            pending = getattr(overlay, "_pending_fullscreen", None)
            fullscreen = pending if pending is not None else getattr(overlay, "_fullscreen", True)
            mode = "fullscreen" if fullscreen else "window"
        elif (not getattr(self.island, "_music_library_active", False)
              and callable(getattr(self.island, "isVisible", None)) and self.island.isVisible()):
            mode = "island"
        else:
            return
        self.settings.set("v2_library_last_playback_surface", mode)
        self.settings.save_now()

    def _own_library_surface(self):
        # Native topmost reinforcement must never resurrect the island behind
        # this page. Keep the guard through platform selection as well.
        self.island._music_library_active = True
        if getattr(self.island, "_music_surface_active", False):
            self.island._restore_island_from_music_surface()
        self.island.hide()
        entry_window = getattr(self.entry, "window", None)
        if entry_window is not None:
            entry_window.hide()

    def _library_closed(self):
        self._navigation.cancel()
        self._invalidate()
        self.island._music_library_active = False
        restore = getattr(self.island, "ensure_minimum_surface", None)
        if callable(restore):
            restore()

    def _entry_closed(self):
        if not self.window.isVisible():
            self._navigation.cancel()
            self.island._music_library_active = False
            restore = getattr(self.island, "ensure_minimum_surface", None)
            if callable(restore):
                restore()

    def focus_navigation_window(self):
        if not getattr(self.island, "_music_library_active", False):
            return False
        for window in (self.window, getattr(self.entry, "window", None)):
            if window is not None and window.isVisible():
                if bool(getattr(window, "isMinimized", lambda: False)()):
                    window.showNormal()
                window.raise_()
                window.activateWindow()
                return True
        return False

    def open_source(self, source_id):
        if self._closed:
            return
        self._remember_surface()
        self._login_visible_started = time.monotonic()
        frame = getattr(self.entry, "_library_origin", None)
        self.entry._library_origin = None
        if frame is None:
            overlay = getattr(self.island, "music_fullscreen_overlay", None)
            frame = self._navigation.origin_frame(overlay) or self._navigation.origin_frame(getattr(self.entry, "window", None))
        self._own_library_surface()
        self._epoch += 1
        self._request += 1
        self._source = source_id
        self._query = ""
        self._collections = self._search_results = ()
        self._catalog_offset = 0
        self._catalog_has_more = False
        self._offsets = {"songs": 0, "playlists": 0}
        self._more = {"songs": False, "playlists": False}
        self._recommended_pending = False
        self._recommended_offset = 0
        self._recommended_has_more = False
        self._recommended_retry = False
        self._clear_catalog_retries()
        self._art_queue.clear()
        self._art_keys.clear()
        self._clear_catalog_artwork()
        self._opening = None
        self.window.set_account(AccountSnapshot(source_id))
        self._logout_pending = False
        self.window.search.setEnabled(False)
        self.window.finish_open_transition()
        self.window.set_collections(())
        self.window.set_search_results(())
        self.window.set_has_more(False)
        self.window.set_toolbar_expanded(True)
        self.window.set_loading(True, "正在加载账号与歌单…")
        self._restore_library_geometry()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self._navigation.request_start(frame, self.window)
        self._pending = True
        self._catalog_pending = False
        restore = getattr(self.service, "restore_account", None)
        if callable(restore):
            epoch, request = self._epoch, self._request
            def resume():
                if self._closed or epoch != self._epoch:
                    return None
                account = restore(source_id)
                catalog = self.service.cached_catalog(source_id) if account.user_id else None
                recommended = self.service.cached_catalog(source_id, "recommended") if account.user_id else None
                return account, catalog, recommended
            if not self.tasks.submit(("resume", epoch, request), resume):
                self._fetch_account()
        else:
            self._fetch_account()

    def _fetch_account(self):
        self._pending = True
        self._account_validating = True
        epoch, request, source_id = self._epoch, self._request, self._source
        def fetch():
            if self._closed or epoch != self._epoch:
                return None
            account = self.service.account_status(source_id)
            if not account.logged_in:
                account = self.service.account_status(source_id, refresh=True)
            return account
        if not self.tasks.submit(("open", epoch, request, source_id), fetch):
            self._pending = False
            self._schedule_catalog_retry("account")

    def switch_source(self):
        if self._closed:
            return
        frame = self._navigation.origin_frame(self.window)
        self._invalidate()
        self._source = ""
        self.window.set_loading(False)
        self.window.hide()
        self.entry.show_login()
        self._navigation.request_start(frame, getattr(self.entry, "window", None))

    def back(self):
        if self._closed:
            return
        if self.window.search.text().strip() and not self._logout_pending:
            # Match the existing Escape behavior: leave search before leaving
            # the account's library. Account records and playback stay intact.
            if self._opening is not None:
                self._opening = None
                self._opening_ready = False
                self._cancel_pending_selection()
                self.window.finish_open_transition()
                self.window.set_loading(False)
            self.window.search.clear()
            return
        self.switch_source()

    def return_from_music_surface(self):
        """Reopen the last account catalog without restarting its playback."""
        if self._closed or not self.settings.get("v2_owned_playback_enabled", True):
            return False
        track = self.player.track
        was_opening = self._opening is not None
        if was_opening:
            self._cancel_pending_selection()
            self._set_surface_loading()
        if (was_opening or track is None) and self._source:
            source_id = self._source
        elif track is None or not track.track_id.startswith("owned:"):
            return False
        else:
            source_id = track.track_id.split(":", 2)[1]
        self._remember_surface()
        self._capture_player_geometry()
        frame = self._navigation.origin_frame(getattr(self.island, "music_fullscreen_overlay", None), separate_cover=True)
        self._opening = None
        self._opening_ready = False
        self._pending_row = self._pending_collection = None
        self.window.finish_open_transition()
        self._own_library_surface()
        account = self.window._account
        if self._source == source_id and account is not None and account.logged_in:
            self.window.set_loading(False)
            self.window.show()
            self._restore_library_geometry()
            self.window.raise_()
            self.window.activateWindow()
            self._navigation.request_start(frame, self.window)
        else:
            self.entry._library_origin = frame
            self.open_source(source_id)
        return True

    def open_now_playing(self):
        """The rotating library album always opens this track in a window."""
        if (self._closed or self._logout_pending or self._opening is not None
                or not self.settings.get("v2_owned_playback_enabled", True)):
            return False
        track = self.player.track
        if track is None or not track.track_id.startswith("owned:"):
            return False
        self._set_surface_loading()
        frame = self._navigation.origin_frame(self.window, separate_cover=True, freeze_cover=True)
        visual = self.player.visual_data()
        if visual is not None:
            self.island._apply_visual_data(visual)
        self.settings.set("v2_library_last_playback_surface", "window")
        self.settings.save_now()
        return self._present_music_surface("window", frame)

    def _present_music_surface(self, mode, frame):
        """Expose the final surface once, then animate only frozen layers."""
        if self.window.isVisible():
            self._publish_shared_music_geometry(self.window.geometry())
        if getattr(self.island, "_music_surface_active", False):
            self.island._restore_island_from_music_surface()
        self.island._music_library_active = False
        self.island.show()
        # An explicit empty origin bypasses the island's entire-window morph.
        # The separate cover handoff already owns this navigation animation.
        kwargs = {}
        if self._opening is not None and self._pending_row is not None:
            row = self._pending_row
            kwargs["loading_selection"] = (row.title, self.window._artwork.get(row.key, QImage()))
        self.island._show_music_fullscreen(fullscreen=mode == "fullscreen",
            origin_geometry=QRect(), origin_snapshot=QImage(), **kwargs)
        if getattr(self.island, "_music_surface_active", False):
            self._navigation.request_start(frame, getattr(self.island, "music_fullscreen_overlay", None))
            self.window.hide()
            return True
        if frame is not None:
            frame.release()
        self._own_library_surface()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        return False

    def _restore_library_geometry(self):
        if self._library_geometry.isValid():
            self._library_placement_epoch += 1
            epoch = self._library_placement_epoch
            self._music_geometry_applying = True
            try:
                self.window.setGeometry(self._library_geometry)
            finally:
                self._music_geometry_applying = False
            # Native first mapping may be placed after show() returns.
            expected = QRect(self._library_geometry)
            def settle():
                if (not self._closed and epoch == self._library_placement_epoch
                        and self.window.isVisible() and not self.window.isMinimized()
                        and getattr(self.window, "_drag_origin", None) is None):
                    self.window.setGeometry(expected)
            for delay in (0, 75, 200):
                QTimer.singleShot(delay, settle)

    def _set_surface_loading(self, row=None):
        surface = getattr(self.island, "music_fullscreen_overlay", None)
        setter = getattr(surface, "set_library_loading", None)
        if callable(setter):
            setter(row.title, self.window._artwork.get(row.key, QImage())) if row is not None else setter()

    def _origin_token(self):
        getter = getattr(self.player, "playback_origin_token", None)
        return getter() if callable(getter) else id(self.player.queue)

    def _collection_identity(self, row):
        account = self.window._account
        return (self._source, getattr(account, "user_id", ""), row.kind,
                row.playlist_id if row.kind == "playlist" else row.key)

    def _cancel_pending_selection(self):
        cancel = getattr(self.player, "cancel_pending_selection", None)
        if callable(cancel):
            return cancel()
        # Existing lightweight providers can still cancel their initial load.
        self.player.stop()
        return True

    def _invalidate(self):
        if getattr(self, "_account_validating", False):
            cancel = getattr(self.service, "cancel_account_validation", None)
            if callable(cancel):
                cancel(self._source)
            self._account_validating = False
        self._epoch += 1
        self._request += 1
        self._pending = False
        self._catalog_pending = False
        self._recommended_pending = False
        self._recommended_retry = False
        self._clear_catalog_retries()
        discard = getattr(self.tasks, "discard", None)
        if callable(discard):
            discard(lambda token: len(token) > 1 and token[1] != self._epoch)
        was_opening, self._opening = self._opening, None
        if was_opening is not None:
            self._cancel_pending_selection()
            self._set_surface_loading()
        self._pending_row = self._pending_collection = None
        self._art_queue.clear()
        self._art_keys.clear()
        self._clear_catalog_artwork()

    def logout(self):
        if self._closed or not self._source or self._logout_pending:
            return
        source = self._source
        self._invalidate()
        self._pending = True
        self._logout_pending = True
        self.window.search.setEnabled(False)
        self.window.set_loading(True, "正在退出当前账号…")
        if self.player.track is not None and self.player.queue[self.player.index].source_id == source:
            self.player.stop()
        epoch = self._epoch
        if not self.tasks.submit(("logout", epoch, self._request), lambda: self.service.logout(source)):
            self._pending = False
            self._logout_pending = False
            self.window.search.setEnabled(True)
            self.window.set_error("服务繁忙，退出未完成，请重试")

    def _search_edited(self, query):
        """Fence old results immediately while the view debounces network I/O."""
        query = str(query).strip()
        if self._closed or not self._source or self._logout_pending or query == self._query:
            return
        self._finish_catalog_retry("search")
        self._search_retry_kinds = ()
        self._request += 1
        self._query = query
        self._offsets = {"songs": 0, "playlists": 0}
        self._more = {"songs": False, "playlists": False}
        self._search_results = ()
        self._pending = False
        if self._opening is not None:
            self._opening = None
            self._opening_ready = False
            self._cancel_pending_selection()
            self.window.finish_open_transition()
        self.window.set_loading(False)

    def search(self, query):
        if self._closed or not self._source or self._logout_pending:
            return
        self._search_edited(query)
        self._finish_catalog_retry("search")
        self._search_retry_kinds = ()
        self._request += 1
        self._query = str(query).strip()
        self._offsets = {"songs": 0, "playlists": 0}
        self._more = {"songs": False, "playlists": False}
        self._search_results = ()
        if not self._query:
            self._pending = False
            self.window.set_loading(False)
            self.window.set_collections(self._collections)
            self.window.set_has_more(getattr(self, "_catalog_has_more", False) or self._recommended_has_more or self._recommended_retry)
            return
        self.window.set_search_results(())
        self._fetch_search()

    def _fetch_search(self, kinds=None):
        self._pending = True
        self._catalog_retry_at.pop("search", None)
        self.window.set_catalog_loading("search", True)
        self.window.set_loading(True, "正在搜索当前平台…")
        epoch, request, source, query = self._epoch, self._request, self._source, self._query
        offsets = dict(self._offsets)
        kinds = tuple(kinds) if kinds is not None else tuple(k for k in offsets if offsets[k] == 0 or self._more[k])
        self._search_retry_kinds = kinds
        self._search_pending_kinds = set(kinds)
        self._search_failed_kinds = set()
        self._search_batch_base = self._search_results
        self._search_batch_pages = {}
        for kind in kinds:
            def fetch(kind=kind):
                if self._closed or epoch != self._epoch or request != self._request:
                    return None
                try:
                    page = self.service.search(source, query, kind=kind, limit=30, offset=offsets[kind])
                    return {kind: page}, []
                except Exception:
                    return {}, [kind]
            if not self.tasks.submit(("search", epoch, request, kind), fetch):
                self._search_pending_kinds.discard(kind)
                self._search_failed_kinds.add(kind)
        if not self._search_pending_kinds:
            self._pending = False
            self.window.set_loading(False)
            self._schedule_catalog_retry("search")

    def load_more(self):
        if self._pending or self._closed or self._logout_pending:
            return
        if self._query:
            if any(self._more.values()):
                self._fetch_search()
            return
        if self._catalog_pending:
            return
        if self._recommended_retry:
            self._fetch_recommended()
            return
        if not getattr(self, "_catalog_has_more", False):
            if self._recommended_has_more:
                self._fetch_recommended()
            return
        self._fetch_catalog_page()

    def _fetch_catalog_page(self, *, initial=False):
        self._catalog_pending = True
        self.window.set_catalog_loading("personal", True)
        if not self._query:
            self._pending = True
        epoch, request, source, offset = self._epoch, self._request, self._source, self._catalog_offset
        kind = "initial_catalog" if initial else "catalog"
        self._catalog_retry_at.pop(kind, None)
        if not self.tasks.submit((kind, epoch, request), lambda: self.service.playlists(source, limit=50, offset=offset)
                if not self._closed and epoch == self._epoch else None):
            self._catalog_pending = False
            if not self._query:
                self._pending = False
            self._schedule_catalog_retry(kind)
            self.window.set_has_more(self._catalog_has_more or self._recommended_has_more or self._recommended_retry)

    @staticmethod
    def _catalog_loading_kind(kind):
        return "personal" if kind in {"catalog", "initial_catalog"} else kind

    def _schedule_catalog_retry(self, kind):
        """Recover incomplete network pages without a user confirmation loop."""
        if self._closed or not self._source:
            return
        count = self._catalog_retry_counts.get(kind, 0)
        if kind == "account" and count >= 3:
            self.window.set_loading(False)
            self.window.set_notice("网络暂不可用，本地快照已保留；可返回账号页重试核验")
            return
        self._catalog_retry_counts[kind] = min(count + 1, 6)
        self._catalog_retry_at[kind] = time.monotonic() + min(30., 2. ** min(count, 5))
        self.window.set_catalog_loading(self._catalog_loading_kind(kind), True)
        self._arm_catalog_retry()

    def _finish_catalog_retry(self, kind):
        self._catalog_retry_at.pop(kind, None)
        self._catalog_retry_counts.pop(kind, None)
        self.window.set_catalog_loading(self._catalog_loading_kind(kind), False)
        self._arm_catalog_retry()

    def _clear_catalog_retries(self):
        self._catalog_retry_timer.stop()
        self._catalog_retry_at.clear()
        self._catalog_retry_counts.clear()
        self._search_retry_kinds = ()
        for kind in tuple(self.window._catalog_loading):
            self.window.set_catalog_loading(kind, False)

    def _arm_catalog_retry(self):
        if self._closed or not self.window.isVisible() or self.window.isMinimized() or not self._catalog_retry_at:
            self._catalog_retry_timer.stop()
            return
        delay = min(self._catalog_retry_at.values()) - time.monotonic()
        self._catalog_retry_timer.start(max(1, round(delay * 1000)))

    def _retry_catalog(self):
        if self._closed or not self.window.isVisible() or self.window.isMinimized():
            self._catalog_retry_timer.stop()
            return
        now = time.monotonic()
        for kind, due in tuple(self._catalog_retry_at.items()):
            if due > now:
                continue
            if ((kind in {"account", "search"} and self._pending)
                    or (kind in {"catalog", "initial_catalog"} and self._catalog_pending)
                    or (kind == "recommended" and self._recommended_pending)):
                self._catalog_retry_at[kind] = now + .5
                continue
            self._catalog_retry_at.pop(kind, None)
            if kind == "account":
                self._fetch_account()
            elif kind == "recommended":
                self._fetch_recommended()
            elif kind == "search":
                self._fetch_search(self._search_retry_kinds)
            else:
                self._fetch_catalog_page(initial=kind == "initial_catalog")
        self._arm_catalog_retry()

    @staticmethod
    def _rows(items, group="personal"):
        rows = []
        for item in items:
            if isinstance(item, OnlinePlaylist):
                rows.append(LibraryCollection(f"playlist:{item.source_id}:{item.playlist_id}", "playlist", item.title,
                                              item.artwork_url, item.playlist_id, group=group))
            elif isinstance(item, OnlineSong):
                rows.append(LibraryCollection(f"song:{item.source_id}:{item.song_id}", "song", item.title,
                                              item.artwork_url, song=item, group=group))
        return tuple(rows)

    def cached_playlist_artwork(self, playlist):
        """Share already decoded matching library covers without I/O."""
        if self._closed or not isinstance(playlist, OnlinePlaylist):
            return QImage()
        key = f"playlist:{playlist.source_id}:{playlist.playlist_id}"
        image = self.window._artwork.get(key)
        # Current metadata cannot prove which URL produced older cached pixels.
        # Keep the successful completion's URL and Qt image identity together.
        matching = (isinstance(image, QImage) and not image.isNull()
                    and self._art_cache_sources.get(key) == (playlist.artwork_url, image.cacheKey()))
        image = image if matching else None
        return QImage(image) if isinstance(image, QImage) else QImage()

    def queue_next(self, row):
        if (self._closed or self._logout_pending or not isinstance(row, LibraryCollection)
                or row.kind != "song" or row.song is None or row.song.source_id != self._source
                or row.key not in {r.key for r in self.window.collections}):
            return
        if not self.settings.get("v2_owned_playback_enabled", True):
            self.window.set_notice("请先在设置中开启程序内播放")
            return
        if self.player.enqueue_next(row.song):
            self.window.set_notice("已加入播放列表，将在下一首播放")
        else:
            self.window.set_notice("请先播放一首歌曲；加载歌曲期间请稍后再添加")

    def _fetch_recommended(self):
        if self._recommended_pending or self._source not in {"netease", "qq"}:
            return
        epoch, source, offset = self._epoch, self._source, self._recommended_offset
        self._recommended_pending = True
        self._recommended_retry = False
        self._catalog_retry_at.pop("recommended", None)
        self.window.set_catalog_loading("recommended", True)
        def fetch():
            if self._closed or epoch != self._epoch:
                return None
            return self.service.recommended_playlists(source, limit=30, offset=offset)
        if not self.tasks.submit(("recommended_catalog", epoch, offset), fetch):
            self._recommended_pending = False
            self._recommended_retry = True
            self._schedule_catalog_retry("recommended")
            if not self._query:
                self.window.set_has_more(self._catalog_has_more or self._recommended_has_more or self._recommended_retry)

    @staticmethod
    def _append_unique(previous, incoming):
        keys = {row.key for row in previous}
        return previous + tuple(row for row in incoming if row.key not in keys and not keys.add(row.key))

    def _completed(self, token, result, error):
        kind, epoch, request, *extra = token
        if kind == "resume":
            if self._closed or epoch != self._epoch or request != self._request:
                return
            if not error and result:
                account, catalog, recommended = result
                if account.user_id:
                    self.window.set_account(account)
                    if catalog is not None and catalog.items:
                        self._collections = self._rows(catalog.items)
                    if recommended is not None and recommended.items:
                        self._collections = self._append_unique(self._collections,
                                                               self._rows(recommended.items, group="recommended"))
                    if self._collections:
                        self.window.set_collections(self._collections)
                        self._prefetch_catalog_artwork()
                    self.window.set_notice("正在核验登录，当前显示本地快照" if not account.logged_in else "")
                    self._record_library_visible()
            self._fetch_account()
            return
        if kind == "recommendation_cover":
            if self._closed or epoch != self._epoch or self._source != "netease":
                return
            self._recommendation_art_pending.discard(request)
            if not error and isinstance(result, str) and result:
                self._recommendation_art_retry_at.pop(request, None)
                self._collections = tuple(replace(row, artwork_url=result) if row.key == request else row
                                          for row in self._collections)
                if not self._query:
                    self.window.set_collections(self._collections)
                self._prefetch_catalog_artwork()
            else:
                self._retry_recommendation_cover_later(request)
            return
        if kind == "recommended_catalog":
            if self._closed or epoch != self._epoch or request != self._recommended_offset:
                return
            self._recommended_pending = False
            if (error or result is None
                    or result.has_more and (not result.items or result.next_offset <= request)):
                self._recommended_retry = True
                self._schedule_catalog_retry("recommended")
                if not self._query:
                    self.window.set_has_more(self._catalog_has_more or self._recommended_has_more or self._recommended_retry)
                return
            self._recommended_retry = False
            self._finish_catalog_retry("recommended")
            rows = self._rows((p for p in result.items if isinstance(p, OnlinePlaylist) and p.source_id == self._source), "recommended")
            self._collections = self._append_unique(self._collections, rows)
            self._recommended_offset = result.next_offset
            self._recommended_has_more = bool(result.has_more and result.next_offset > request)
            if not self._query:
                self.window.set_collections(self._collections)
                self.window.set_has_more(self._catalog_has_more or self._recommended_has_more or self._recommended_retry)
            self._prefetch_catalog_artwork()
            return
        if kind in {"initial_catalog", "catalog"}:
            # The account/avatar is already visible. Search may advance its
            # own request id while this original catalog arrives; retain the
            # source's catalog without replacing visible search results.
            if self._closed or epoch != self._epoch:
                return
            self._catalog_pending = False
            if not self._query:
                self._pending = False
                self.window.set_loading(False)
            if (error or result is None
                    or result.has_more and (not result.items or result.next_offset <= self._catalog_offset)):
                self._schedule_catalog_retry(kind)
                if not self._query:
                    self.window.set_has_more(self._catalog_has_more or self._recommended_has_more or self._recommended_retry)
            else:
                self._finish_catalog_retry(kind)
                self._append_catalog(result)
            return
        if self._closed or epoch != self._epoch or request != self._request:
            return
        if kind == "open":
            self._account_validating = False
        self._pending = False
        self.window.set_loading(False)
        if kind == "search" and extra and (error or result is None):
            result, error = ({}, [extra[0]]), None
        if error or (result is None and kind != "logout"):
            if kind == "open":
                self.window.set_loading(True, "正在连接账号…")
                self._schedule_catalog_retry("account")
                return
            if kind == "search":
                self._schedule_catalog_retry("search")
                return
            if kind == "logout":
                self._logout_pending = False
                self.window.search.setEnabled(True)
            self.window.search.setEnabled(kind != "open")
            self.window.set_error("请求失败，请重试或重新进入当前平台")
            return
        if kind == "logout":
            self._logout_pending = False
            source = self._source
            self.window.hide()
            self.entry.show_login(source)
            return
        if kind == "open":
            account = result
            if not account.logged_in:
                if account.state == "error":
                    self.window.set_account(account)
                    self.window.set_notice("网络暂不可用，已保留本地快照；将重试登录核验")
                    self._schedule_catalog_retry("account")
                    return
                self.window.hide()
                self.entry.show_login(self._source)
                return
            self._finish_catalog_retry("account")
            self.window.set_account(account)
            self.window.set_notice("")
            self._record_library_visible()
            self.window.search.setEnabled(True)
            self.settings.set("v2_online_music_source", self._source)
            self.settings.save_now()
            if self._source == "netease":
                self._collections = self._append_unique((
                    LibraryCollection("daily", "daily", "每日推荐", "", group="for_you"),
                    LibraryCollection("fm", "fm", "私人漫游", "", group="for_you"),
                ), self._collections)
            self.window.set_collections(self._collections)
            self.window.set_loading(True, "正在加载歌单…")
            source = self._source
            self._fetch_recommended()
            self._fetch_catalog_page(initial=True)
            # Account and catalog work is already submitted and visible. These
            # independent workers never hold the first library frame for art.
            if source == "netease":
                self._fetch_recommendation_cover("daily")
                self._fetch_recommendation_cover("fm")
        elif kind == "search":
            pages, errors = result
            errors = list(errors)
            pending = getattr(self, "_search_pending_kinds", set())
            pending.difference_update((*pages, *errors))
            for kind, page in pages.items():
                self._search_batch_pages[kind] = self._rows(page.items)
                old_offset = self._offsets[kind]
                if page.has_more and (not page.items or page.next_offset <= old_offset):
                    errors.append(kind)
                    continue
                self._offsets[kind] = page.next_offset
                self._more[kind] = bool(page.has_more and page.next_offset > old_offset)
            self._search_results = self._search_batch_base
            for result_kind in ("songs", "playlists"):
                self._search_results = self._append_unique(self._search_results,
                    self._search_batch_pages.get(result_kind, ()))
            self.window.set_search_results(self._search_results)
            self.window.set_has_more(any(self._more.values()))
            # Publish each kind immediately. A slow playlist search must not
            # withhold already returned songs and their cover requests.
            self._search_failed_kinds.update(errors)
            self._pending = bool(pending)
            self.window.set_loading(bool(pending))
            self.window.set_catalog_loading("search", bool(pending))
            if pending:
                return
            if self._search_failed_kinds:
                self._search_retry_kinds = tuple(k for k in ("songs", "playlists") if k in self._search_failed_kinds)
                self._schedule_catalog_retry("search")
            else:
                self._search_retry_kinds = ()
                self._finish_catalog_retry("search")

    def _record_library_visible(self):
        start = getattr(self, "_login_visible_started", None)
        if start is not None:
            record_login_phase(self._source, "open", "library_visible", time.monotonic() - start, self._epoch)
            self._login_visible_started = None

    def _clear_catalog_artwork(self):
        self._catalog_art_pending.clear()
        self._catalog_art_seen.clear()
        self._art_retry_counts.clear()
        self._art_retry_at.clear()
        self._art_cache_sources.clear()
        self._recommendation_art_pending.clear()
        self._recommendation_art_retries.clear()
        self._recommendation_art_retry_at.clear()
        self._art_retry_timer.stop()

    def _fetch_recommendation_cover(self, kind):
        if self._closed or self._source != "netease" or kind in self._recommendation_art_pending:
            return
        epoch = self._epoch
        self._recommendation_art_pending.add(kind)
        if not self.tasks.submit(("recommendation_cover", epoch, kind),
                lambda: self.service.recommendation_artwork(kind, "netease")
                if not self._closed and epoch == self._epoch else ""):
            self._recommendation_art_pending.discard(kind)
            self._retry_recommendation_cover_later(kind)

    def _retry_recommendation_cover_later(self, kind):
        count = self._recommendation_art_retries.get(kind, 0)
        if self._closed or self._source != "netease":
            return
        self._recommendation_art_retries[kind] = min(2, count + 1)
        delay = .75 * (count + 1) if count < 2 else 30.
        self._recommendation_art_retry_at[kind] = time.monotonic() + delay
        self._arm_art_retry()

    def _prefetch_catalog_artwork(self):
        # Playlist metadata already contains its cover URL. Fill all known
        # covers independently of folding/scrolling, without ever asking for
        # playlist songs. Daily/FM preview batches stay owned by the service
        # until actual playback consumes them once.
        # The backlog stores URL references only; two workers decode at a time.
        for row in self._collections:
            identity = row.key, row.artwork_url
            if row.artwork_url and identity not in self._catalog_art_seen:
                self._catalog_art_seen.add(identity)
                self._catalog_art_pending.append((self._epoch, row.key, row.artwork_url))
        self._pump_art()

    def _append_catalog(self, page):
        old_offset = self._catalog_offset
        incoming = self._rows(page.items)
        personal_keys = {row.key for row in incoming}
        retained = tuple(row for row in self._collections
                         if not (row.group == "recommended" and row.key in personal_keys))
        self._collections = self._append_unique(retained, incoming)
        self._catalog_offset = page.next_offset
        self._catalog_has_more = bool(page.has_more and page.next_offset > old_offset)
        if not self._query:
            self.window.set_collections(self._collections)
            self.window.set_has_more(self._catalog_has_more or self._recommended_has_more or self._recommended_retry)
        self._prefetch_catalog_artwork()

    def _queue_art(self, key, url):
        if key in self._art_keys:
            return
        if self._art_retry_at.get((key, url), 0.) > time.monotonic():
            # Repainting/scrolling a failed tile must not bypass its backoff.
            # The view registered this signal as in-flight before emitting;
            # no worker owns it, so release that slot while the timer waits.
            self.window.set_artwork(key, QImage())
            return
        if len(self._art_queue) >= 32:
            old_epoch, old_key, _ = self._art_queue.popleft()
            self._art_keys.discard(old_key)
            if old_epoch == self._epoch:
                # Let an evicted visible slot request again on a later scroll.
                self.window.set_artwork(old_key, QImage())
        self._art_keys.add(key)
        self._art_queue.append((self._epoch, key, url))
        self._pump_art()

    def _pump_art(self):
        while not self._closed and self._art_inflight < 2 and (self._art_queue or self._catalog_art_pending):
            visible = bool(self._art_queue)
            epoch, key, url = (self._art_queue if visible else self._catalog_art_pending).popleft()
            if epoch != self._epoch:
                continue
            if self._art_retry_at.get((key, url), 0.) > time.monotonic():
                self._art_keys.discard(key)
                continue
            if not visible:
                cached = self.window._artwork.get(key)
                if key in self._art_keys or cached is not None and not cached.isNull():
                    continue
                self._art_keys.add(key)
            if self.art_tasks.submit(("art", epoch, key, url), lambda url=url, epoch=epoch:
                    fetch_online_thumbnail(url) if not self._closed and epoch == self._epoch else QImage()):
                self._art_inflight += 1
            else:
                self._art_keys.discard(key)
                self.window.set_artwork(key, QImage())
                self._schedule_art_retry(key, url)

    def _schedule_art_retry(self, key, url):
        identity = key, url
        count = self._art_retry_counts.get(identity, 0)
        if self._closed or not url:
            return
        self._art_retry_counts[identity] = min(2, count + 1)
        delay = .75 * (count + 1) if count < 2 else 30.
        self._art_retry_at[identity] = time.monotonic() + delay
        self._arm_art_retry()

    def _arm_art_retry(self):
        due = (*self._art_retry_at.values(), *self._recommendation_art_retry_at.values())
        if self._closed or not self.window.isVisible() or self.window.isMinimized() or not due:
            self._art_retry_timer.stop()
            return
        self._art_retry_timer.start(max(1, round((min(due) - time.monotonic()) * 1000)))

    def _retry_art(self):
        if self._closed or not self.window.isVisible() or self.window.isMinimized():
            self._art_retry_timer.stop()
            return
        now = time.monotonic()
        rows = {row.key: row.artwork_url for row in self._collections + self._search_results}
        if self.window._account is not None:
            rows["account"] = self.window._account.avatar_url
        for (key, url), due in tuple(self._art_retry_at.items()):
            if due <= now:
                if self._art_inflight >= 2:
                    self._art_retry_at[key, url] = now + 1.
                    continue
                self._art_retry_at.pop((key, url), None)
                if rows.get(key) == url:
                    self._queue_art(key, url)
        for kind, due in tuple(self._recommendation_art_retry_at.items()):
            if due <= now:
                self._recommendation_art_retry_at.pop(kind, None)
                self._fetch_recommendation_cover(kind)
        self._arm_art_retry()

    def _art_completed(self, token, result, error):
        self._art_inflight = max(0, self._art_inflight - 1)
        _, epoch, key, *extra = token
        if epoch == self._epoch:
            self._art_keys.discard(key)
        if not self._closed and epoch == self._epoch:
            image = result if not error and isinstance(result, QImage) else QImage()
            self.window.set_artwork(key, image)
            cached = self.window._artwork.get(key)
            if extra and isinstance(cached, QImage) and not cached.isNull() and not image.isNull():
                self._art_cache_sources[key] = (extra[0], cached.cacheKey())
                for expired in tuple(self._art_cache_sources):
                    if expired not in self.window._artwork:
                        self._art_cache_sources.pop(expired, None)
            else:
                self._art_cache_sources.pop(key, None)
            if image.isNull() and extra:
                self._schedule_art_retry(key, extra[0])
            elif extra:
                self._art_retry_at.pop((key, extra[0]), None)
        self._pump_art()

    def play_collection(self, row, mode, global_cover_rect):
        if self._closed or not self._source or not isinstance(row, LibraryCollection):
            return
        if self._logout_pending:
            self.window.finish_open_transition()
            return
        if not self.settings.get("v2_owned_playback_enabled", True):
            self.window.finish_open_transition()
            self.window.set_error("请先在设置中开启程序内播放")
            return
        if row.key not in {r.key for r in self.window.collections} or mode not in {"default", "fullscreen", "window", "island"}:
            self.window.finish_open_transition()
            return
        identity = self._collection_identity(row)
        if (self._active_collection == (identity, self._origin_token())
                and self.player.track is not None
                and self.player.status not in {"idle", "stream_unavailable", "playback_failed"}):
            self.window.finish_open_transition()
            self.open_now_playing()
            return
        rect = QRect(global_cover_rect)
        snapshot = QImage()
        if rect.isValid():
            local = QRect(self.window.mapFromGlobal(rect.topLeft()), rect.size()).intersected(self.window.rect())
            if local.isValid():
                snapshot = self.window.grab(local).toImage()
        previous_track = self.player.track
        if mode == "default":
            mode = "window"
        self.settings.set("v2_library_last_playback_surface", mode)
        self.settings.save_now()
        self._opening_ready = False
        self._opening = (mode, rect, snapshot)
        self._opening_origin_guard = callable(getattr(self.player, "playback_origin_token", None))
        self._opening_origin_before = self._origin_token()
        self._opening_origin = None
        self._pending_collection = identity
        self._pending_row = row
        frame = self._navigation.origin_frame(self.window, separate_cover=True, cover_state=(rect, snapshot, 0.))
        if frame is not None and rect.width() > 0:
            frame.cover_roundness = min(.5, 14. / rect.width())
        if row.kind == "daily":
            accepted = self.player.play_daily(self._source)
        elif row.kind == "fm":
            accepted = self.player.play_personal_fm(self._source)
        elif row.kind == "playlist":
            accepted = self.player.play_playlist(self._source, row.playlist_id)
        elif row.kind == "song" and row.song is not None and row.song.source_id == self._source:
            songs = tuple(r.song for r in self._search_results if r.kind == "song" and r.song is not None)
            if row.song not in songs:
                songs = (row.song,)
            index = songs.index(row.song)
            accepted = self.player.play_queue(songs, index=index)
        else:
            accepted = False
        if not accepted:
            if frame is not None:
                frame.release()
            failure_reported = self._opening is None
            self._opening = None
            self._pending_row = self._pending_collection = None
            self.window.finish_open_transition()
            if not failure_reported:
                self.window.set_error("此项目暂不可播放，请重试")
        else:
            if self._opening is None and self.player.status in {"busy", "catalog_unavailable", "stream_unavailable", "playback_failed"}:
                if frame is not None:
                    frame.release()
                return
            if self.player.track is not None and self.player.track is not previous_track:
                self._player_changed()
            # Expose a real playback page before catalog/stream I/O finishes.
            if mode == "island":
                if frame is not None:
                    frame.release()
                self.window.hide()
                self.island._music_library_active = False
                self.island._restore_island_from_music_surface()
                self.entry._show_island()
            else:
                self._present_music_surface(mode, frame)
                if self._opening is not None:
                    self._set_surface_loading(row)

    def _player_status(self, status):
        if self._opening and status == "loading_stream":
            # Emitted after the new TrackData exists. stop/setSource can emit
            # old position/state changes synchronously before this point.
            origin = self._origin_token()
            # Headset Next can load another song from the retained queue
            # while the selected catalog is still fetching. Only a replaced
            # queue belongs to this library request.
            if not self._opening_origin_guard or origin != self._opening_origin_before:
                self._opening_origin = origin
                self._opening_ready = True
            else:
                # Owned Next cancels the catalog reservation itself. Keep
                # that queue's real identity and reveal its new song, rather
                # than leaving an orphaned selection skeleton on screen.
                self._opening = None
                self._pending_row = self._pending_collection = None
                self._set_surface_loading()
                self.window.set_loading(False)
                self.window.finish_open_transition()
        if self._opening and status in {"busy", "catalog_unavailable", "stream_unavailable", "playback_failed"}:
            continuing_current = (status in {"busy", "catalog_unavailable"}
                and not self._opening_ready and self.player.track is not None
                and self.player.track.is_playing)
            self._opening = None
            self._pending_collection = None
            self._set_surface_loading()
            self.window.finish_open_transition()
            self.window.set_loading(False)
            messages = {
                "busy": "播放服务繁忙，请稍后重试",
                "catalog_unavailable": "未能加载可播放歌曲，请重试或选择其他歌单",
                "stream_unavailable": "平台未提供此歌曲的播放地址，请检查账号权限或选择其他歌曲",
                "playback_failed": "音频加载失败，请检查网络后重试",
            }
            failures = {
                "auth_required": "登录已失效，请返回平台入口重新登录",
                "credential": "账号读取失败，请返回平台入口重试",
                "timeout": "播放请求超时，请检查网络后重试",
                "network": "音频连接失败，请检查网络后重试",
                "format": "播放器无法解码此音频，请尝试其他歌曲",
                "access_denied": "平台拒绝了此音频的访问，请检查账号权限",
                "invalid_stream": "平台返回的播放地址无效，请重试",
                "invalid_response": "音乐服务返回了无效音频，请重试",
                "incomplete": "音频下载未完成，请检查网络后重试",
                "expired": "音频授权已过期，请重新选择此项目",
                "too_large": "当前音频过大，暂时无法播放，请选择其他歌曲",
                "storage": "无法保存播放缓存，请检查磁盘可用空间后重试",
            }
            message = failures.get(getattr(self.player, "failure_reason", ""), messages[status])
            if continuing_current:
                message += "；当前歌曲继续播放"
            self.window.set_error(message)
            if not self.window.isVisible():
                self._navigation.cancel()
                self._own_library_surface()
                self.window.show()
                self._restore_library_geometry()
                self.window.raise_()
                self.window.activateWindow()

    def _player_changed(self):
        self.window.set_now_playing(self.player.track)
        if self._opening is None or self.player.track is None:
            return
        # Old playback can emit position signals while a catalog request runs.
        speaking = getattr(self.player, "is_personal_dj_speaking", None)
        audible = self.player.track.is_playing or callable(speaking) and speaking()
        if (not self._opening_ready or self.player.status not in {"playing", "trial"} or not audible
                or self._opening_origin_guard and self._origin_token() != self._opening_origin):
            return
        mode, rect, snapshot = self._opening
        self._opening = None
        if self._pending_collection is not None:
            self._active_collection = (self._pending_collection, self._origin_token())
        self._pending_collection = None
        self._pending_row = None
        self._set_surface_loading()
        visual = self.player.visual_data()
        if visual is not None:
            self.island._apply_visual_data(visual)
        self.window.set_loading(False)
        self.window.finish_open_transition()

    def close(self):
        self._closed = True
        self._navigation.cancel()
        self._navigation.close()
        self._invalidate()
        self.tasks.close()
        self.art_tasks.close()
        self.window.close()
