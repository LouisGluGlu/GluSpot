"""Account-scoped catalog I/O and handoff to the existing V2 music surface."""
from __future__ import annotations

from collections import deque
from dataclasses import replace

from PySide6.QtCore import QObject, QRect
from PySide6.QtGui import QImage

from api.online_music import AccountSnapshot, OnlinePlaylist, OnlineSong
from api.online_music_artwork import fetch_online_thumbnail
from local.music_library_window import LibraryCollection, MusicLibraryWindow
from local.music_tasks import MusicTasks


class MusicLibraryController(QObject):
    def __init__(self, settings, service, player, island, entry, parent=None, *, window=None, tasks=None, art_tasks=None):
        super().__init__(parent)
        self.settings, self.service, self.player = settings, service, player
        self.island, self.entry = island, entry
        self.window = window or MusicLibraryWindow()
        self.tasks = tasks or MusicTasks(self)
        self.art_tasks = art_tasks or MusicTasks(self)
        self.tasks.completed.connect(self._completed)
        self.art_tasks.completed.connect(self._art_completed)
        self.window.sourceSwitchRequested.connect(self.switch_source)
        self.window.backRequested.connect(self.back)
        self.window.logoutRequested.connect(self.logout)
        self.window.collectionRequested.connect(self.play_collection)
        self.window.searchRequested.connect(self.search)
        self.window.moreRequested.connect(self.load_more)
        self.window.artworkNeeded.connect(self._queue_art)
        self.window.closed.connect(self._invalidate)
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
        self._logout_pending = False
        self._opening = None
        self._closed = False
        self._art_queue = deque()
        self._art_inflight = 0
        self._art_keys = set()

    def open_source(self, source_id):
        if self._closed:
            return
        self._epoch += 1
        self._request += 1
        self._source = source_id
        self._query = ""
        self._collections = self._search_results = ()
        self._catalog_offset = 0
        self._catalog_has_more = False
        self._offsets = {"songs": 0, "playlists": 0}
        self._more = {"songs": False, "playlists": False}
        self._art_queue.clear()
        self._art_keys.clear()
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
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self._pending = True
        epoch, request = self._epoch, self._request
        def fetch():
            if self._closed or epoch != self._epoch:
                return None
            account = self.service.account_status(source_id)
            if not account.logged_in:
                account = self.service.account_status(source_id, refresh=True)
            if not account.logged_in:
                return account, None
            try:
                page = self.service.playlists(source_id, limit=50, offset=0)
            except Exception:
                page = None
            return account, page
        if not self.tasks.submit(("open", epoch, request), fetch):
            self._pending = False
            self.window.set_error("服务繁忙，请稍后重新进入")

    def switch_source(self):
        if self._closed:
            return
        self._invalidate()
        self._source = ""
        self.window.set_loading(False)
        self.window.hide()
        self.entry.show_login()

    def back(self):
        if self._closed:
            return
        if self.window.search.text().strip() and not self._logout_pending:
            # Match the existing Escape behavior: leave search before leaving
            # the account's library. Account records and playback stay intact.
            if self._opening is not None:
                self._opening = None
                self._opening_ready = False
                self.player.stop()
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
        if track is None or not track.track_id.startswith("owned:"):
            return False
        source_id = track.track_id.split(":", 2)[1]
        self._opening = None
        self._opening_ready = False
        self.window.finish_open_transition()
        if getattr(self.island, "_music_surface_active", False):
            self.island._restore_island_from_music_surface()
        self.island.hide()
        if hasattr(self.entry, "window"):
            self.entry.window.hide()
        account = self.window._account
        if self._source == source_id and account is not None and account.logged_in:
            self.window.set_loading(False)
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
        else:
            self.open_source(source_id)
        return True

    def _invalidate(self):
        self._epoch += 1
        self._request += 1
        self._pending = False
        was_opening, self._opening = self._opening, None
        if was_opening is not None:
            self.player.stop()
        self._art_queue.clear()
        self._art_keys.clear()

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

    def search(self, query):
        if self._closed or not self._source or self._logout_pending:
            return
        self._request += 1
        self._query = str(query).strip()
        self._offsets = {"songs": 0, "playlists": 0}
        self._more = {"songs": False, "playlists": False}
        self._search_results = ()
        if not self._query:
            self._pending = False
            self.window.set_loading(False)
            self.window.set_collections(self._collections)
            self.window.set_has_more(getattr(self, "_catalog_has_more", False))
            return
        self.window.set_search_results(())
        self._fetch_search()

    def _fetch_search(self):
        self._pending = True
        self.window.set_loading(True, "正在搜索当前平台…")
        epoch, request, source, query = self._epoch, self._request, self._source, self._query
        offsets = dict(self._offsets)
        kinds = tuple(k for k in offsets if offsets[k] == 0 or self._more[k])
        def fetch():
            pages, errors = {}, []
            for kind in kinds:
                if self._closed or epoch != self._epoch or request != self._request:
                    return None
                try:
                    pages[kind] = self.service.search(source, query, kind=kind, limit=30, offset=offsets[kind])
                except Exception:
                    errors.append(kind)
            return pages, errors
        if not self.tasks.submit(("search", epoch, request), fetch):
            self._pending = False
            self.window.set_error("服务繁忙，请稍后搜索")

    def load_more(self):
        if self._pending or self._closed or self._logout_pending:
            return
        if self._query:
            if any(self._more.values()):
                self._fetch_search()
            return
        if not getattr(self, "_catalog_has_more", False):
            return
        self._pending = True
        epoch, request, source, offset = self._epoch, self._request, self._source, self._catalog_offset
        if not self.tasks.submit(("catalog", epoch, request), lambda: self.service.playlists(source, limit=50, offset=offset)
                if not self._closed and epoch == self._epoch and request == self._request else None):
            self._pending = False
            self.window.set_error("歌单加载繁忙，请稍后滚动重试")

    @staticmethod
    def _rows(items):
        rows = []
        for item in items:
            if isinstance(item, OnlinePlaylist):
                rows.append(LibraryCollection(f"playlist:{item.source_id}:{item.playlist_id}", "playlist", item.title,
                                              item.artwork_url, item.playlist_id))
            elif isinstance(item, OnlineSong):
                rows.append(LibraryCollection(f"song:{item.source_id}:{item.song_id}", "song", item.title,
                                              item.artwork_url, song=item))
        return tuple(rows)

    @staticmethod
    def _append_unique(previous, incoming):
        keys = {row.key for row in previous}
        return previous + tuple(row for row in incoming if row.key not in keys and not keys.add(row.key))

    def _completed(self, token, result, error):
        kind, epoch, request, *extra = token
        if kind == "recommendation_art":
            if self._closed or epoch != self._epoch or error or not result:
                return
            cover = next((song.artwork_url for song in result if isinstance(song, OnlineSong) and song.artwork_url), "")
            if cover:
                key = extra[0]
                self._collections = tuple(replace(row, artwork_url=cover) if row.key == key else row
                                          for row in self._collections)
                self.window.set_collections(self._collections)
            return
        if self._closed or epoch != self._epoch or request != self._request:
            return
        self._pending = False
        self.window.set_loading(False)
        if error or (result is None and kind != "logout"):
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
            account, page = result
            if not account.logged_in:
                self.window.hide()
                self.entry.show_login(self._source)
                return
            self.window.set_account(account)
            self.window.search.setEnabled(True)
            self.settings.set("v2_online_music_source", self._source)
            self.settings.save_now()
            if self._source == "netease":
                self._collections = (
                    LibraryCollection("daily", "daily", "每日推荐", ""),
                    LibraryCollection("fm", "fm", "私人漫游", ""),
                    LibraryCollection("dj", "dj", "私人DJ", ""),
                )
            if page is not None:
                self._append_catalog(page)
            else:
                self.window.set_collections(self._collections)
                self.window.set_error("歌单暂未加载，请切换平台后重新进入；推荐入口仍可使用")
            if self._source == "netease":
                # The catalog has no recommendation artwork. Retrieve actual
                # song covers independently, without delaying the first list.
                for key, fetch in (("daily", self.service.daily_recommendations),
                                   ("fm", self.service.personal_fm)):
                    source = self._source
                    self.tasks.submit(("recommendation_art", self._epoch, self._request, key),
                        lambda fetch=fetch, source=source, epoch=self._epoch:
                            fetch(source) if not self._closed and epoch == self._epoch else ())
        elif kind == "catalog":
            self._append_catalog(result)
        elif kind == "search":
            pages, errors = result
            for kind, page in pages.items():
                self._search_results = self._append_unique(self._search_results, self._rows(page.items))
                old_offset = self._offsets[kind]
                self._offsets[kind] = page.next_offset
                self._more[kind] = bool(page.has_more and page.next_offset > old_offset)
            for kind in errors:
                self._more[kind] = False
            self.window.set_search_results(self._search_results)
            self.window.set_has_more(any(self._more.values()))
            if errors:
                self.window.set_error("部分搜索结果暂不可用，请重新搜索")

    def _append_catalog(self, page):
        old_offset = self._catalog_offset
        self._collections = self._append_unique(self._collections, self._rows(page.items))
        self._catalog_offset = page.next_offset
        self._catalog_has_more = bool(page.has_more and page.next_offset > old_offset)
        self.window.set_collections(self._collections)
        self.window.set_has_more(self._catalog_has_more)

    def _queue_art(self, key, url):
        if key in self._art_keys:
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
        while not self._closed and self._art_inflight < 2 and self._art_queue:
            epoch, key, url = self._art_queue.popleft()
            if epoch != self._epoch:
                continue
            if self.art_tasks.submit(("art", epoch, key), lambda url=url, epoch=epoch:
                    fetch_online_thumbnail(url) if not self._closed and epoch == self._epoch else QImage()):
                self._art_inflight += 1
            else:
                self._art_keys.discard(key)
                self.window.set_artwork(key, QImage())

    def _art_completed(self, token, result, error):
        self._art_inflight = max(0, self._art_inflight - 1)
        _, epoch, key = token
        if epoch == self._epoch:
            self._art_keys.discard(key)
        if not self._closed and epoch == self._epoch:
            image = result if not error and isinstance(result, QImage) else QImage()
            self.window.set_artwork(key, image)
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
        rect = QRect(global_cover_rect)
        snapshot = QImage()
        if rect.isValid():
            local = QRect(self.window.mapFromGlobal(rect.topLeft()), rect.size()).intersected(self.window.rect())
            if local.isValid():
                snapshot = self.window.grab(local).toImage()
        previous_track = self.player.track
        self._opening_ready = False
        self._opening = (mode, rect, snapshot)
        if row.kind == "daily":
            accepted = self.player.play_daily(self._source)
        elif row.kind == "fm":
            accepted = self.player.play_personal_fm(self._source)
        elif row.kind == "dj":
            accepted = self.player.play_personal_dj(self._source)
        elif row.kind == "playlist":
            accepted = self.player.play_playlist(self._source, row.playlist_id)
        elif row.kind == "song" and row.song is not None and row.song.source_id == self._source:
            accepted = self.player.play_queue((row.song,))
        else:
            accepted = False
        if not accepted:
            self._opening = None
            self.window.finish_open_transition()
            self.window.set_error("此项目暂不可播放，请重试")
        elif self.player.track is not None and self.player.track is not previous_track:
            self._player_changed()
        elif self._opening is not None:
            self.window.set_loading(True, "正在加载歌曲…")

    def _player_status(self, status):
        if self._opening and status == "loading_stream":
            # Emitted after the new TrackData exists. stop/setSource can emit
            # old position/state changes synchronously before this point.
            self._opening_ready = True
        if self._opening and status in {"busy", "catalog_unavailable", "stream_unavailable", "playback_failed"}:
            self._opening = None
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
            self.window.set_error(failures.get(getattr(self.player, "failure_reason", ""), messages[status]))

    def _player_changed(self):
        if self._opening is None or self.player.track is None:
            return
        # Old playback can emit position signals while a catalog request runs.
        if not self._opening_ready or self.player.status not in {"playing", "trial"} or not self.player.track.is_playing:
            return
        mode, rect, snapshot = self._opening
        self._opening = None
        visual = self.player.visual_data()
        if visual is not None:
            self.island._apply_visual_data(visual)
        self.window.set_loading(False)
        self.window.finish_open_transition()
        if mode == "island":
            self.window.hide()
            self.island._restore_island_from_music_surface()
            self.entry._show_island()
            return
        # A subsequent library selection replaces an existing hidden surface.
        if getattr(self.island, "_music_surface_active", False):
            self.island._restore_island_from_music_surface()
        self.island.show()
        self.island._show_music_fullscreen(fullscreen=None if mode == "default" else mode == "fullscreen",
                                           origin_geometry=rect, origin_snapshot=snapshot)
        if getattr(self.island, "_music_surface_active", False):
            self.window.hide()

    def close(self):
        self._closed = True
        self._invalidate()
        self.tasks.close()
        self.art_tasks.close()
        self.window.close()
