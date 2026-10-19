"""Account-bound current-song favorite actions, independent of surface painting."""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections import OrderedDict, deque
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

from api.online_music import AccountSnapshot, CatalogPage, OnlineMusicError, OnlinePlaylist, OnlineSong
from api.online_music_artwork import fetch_online_thumbnail
from local.music_tasks import MusicTasks


_CURRENT_AUTHORITY = object()


@dataclass(frozen=True)
class SongCollectionState:
    track_id: str = ""
    source_id: str = ""
    user_id: str = ""
    available: bool = False
    liked: bool | None = None
    busy: bool = False
    message: str = ""
    playlists: tuple[OnlinePlaylist, ...] = ()
    playlists_pending: bool = False
    playlist_has_more: bool = False
    playlists_loaded: bool = False


class SongCollectionController(QObject):
    """Use explicit song/account fences for every asynchronous completion/write.

    ``context`` is a cheap immutable snapshot for a visible owned track. The
    surface must additionally gate it on the provider's currently active track;
    a retained owned-player song must not add controls to a system-media page.
    """
    changed = Signal()
    _ERRORS = {
        "auth_required": "请先登录此音乐来源",
        "unsupported": "此来源暂不支持收藏或添加到歌单",
        "cancelled": "账号或歌曲已变化，请重试",
        "account_changed": "账号已变化，请重新打开收藏面板",
        "not_writable": "请选择自己创建的歌单",
        "timeout": "请求超时，请刷新状态后重试",
        "mutation_failed": "平台未确认操作，请刷新后重试",
        "unavailable": "音乐服务暂不可用",
        "credential": "登录记录不可用，请重新登录",
    }

    def __init__(self, service, player, parent=None, *, tasks=None, art_tasks=None, artwork_lookup=None):
        super().__init__(parent)
        self.service, self.player = service, player
        self.tasks = tasks or MusicTasks(self)
        self.tasks.completed.connect(self._completed)
        self._closed = False
        self._epoch = 0
        self._serial = 0
        self._song = None
        self._account_authority = None
        self._gesture_authority = None
        self._selection = None
        self._observed_account = None
        self._state = SongCollectionState()
        self._tokens = {}
        self._playlist_offset = 0
        self._art_tasks = art_tasks
        self._artwork_lookup = artwork_lookup
        self._artwork = OrderedDict()
        self._art_queue = deque()
        self._art_pending = set()
        self._art_inflight = {}
        self._art_attempts = {}
        self._art_retry_at = {}
        self._art_retry_timer = QTimer(self)
        self._art_retry_timer.setSingleShot(True)
        self._art_retry_timer.timeout.connect(self._retry_playlist_artwork)
        if art_tasks is not None:
            art_tasks.completed.connect(self._art_completed)
        self.player.changed.connect(self._player_changed)
        self._player_changed()

    def _visual_song(self):
        track_id = str(getattr(getattr(self.player, "track", None), "track_id", ""))
        if not track_id.startswith("owned:"):
            return None
        return next((song for song in self.player.queue if isinstance(song, OnlineSong)
                     and song.media_kind == "song"
                     and track_id == f"owned:{song.source_id}:{song.song_id}"), None)

    def _cached_account(self, source):
        # The existing service's default account_status is lock-only: neither
        # credential access nor network refresh occurs on the UI thread.
        account = self.service.account_status(source)
        return account if isinstance(account, AccountSnapshot) else AccountSnapshot(source)

    def _account_key(self, source):
        account = self._cached_account(source)
        # Immutable cached snapshots are replaced on account restoration/login.
        # Their identity also fences a logout/login of the same user ID, which
        # a user-ID-only guard cannot distinguish from an old open dialog.
        return account.logged_in, account.user_id, id(account)

    def _account_matches(self, source, user_id):
        key = self._account_key(source)
        return key[:2] == (True, user_id) and key == self._observed_account

    def _player_changed(self):
        if self._closed:
            return
        song = self._visual_song()
        key = (song.source_id, song.song_id) if song else None
        account_key = self._account_key(song.source_id) if song else None
        if key == self._selection and account_key == self._observed_account:
            return
        self._select(song, account_key)

    def _select(self, song, account_key):
        self._epoch += 1
        self._gesture_authority = object() if song else None
        self._tokens.clear()
        self._song = song
        # Retain the actual immutable object while its identity is used as an
        # authority key; otherwise Python could reuse its id after a logout.
        self._account_authority = self._cached_account(song.source_id) if song else None
        self._selection = (song.source_id, song.song_id) if song else None
        self._observed_account = account_key
        self._playlist_offset = 0
        self._art_queue.clear()
        self._art_pending.clear()
        self._art_attempts.clear()
        self._art_retry_at.clear()
        self._art_retry_timer.stop()
        if song is None:
            self._set(SongCollectionState())
            return
        track_id = f"owned:{song.source_id}:{song.song_id}"
        if song.source_id != "netease":
            self._set(SongCollectionState(track_id, song.source_id,
                                         message=self._ERRORS["unsupported"]))
            return
        self._set(SongCollectionState(track_id, song.source_id, busy=True, message="正在读取收藏状态…"))
        epoch = self._epoch
        def fetch():
            if self._closed or epoch != self._epoch:
                return None
            account = self.service.account_status(song.source_id)
            if not account.logged_in:
                account = self.service.account_status(song.source_id, refresh=True)
            if not account.logged_in:
                raise OnlineMusicError("auth_required", song.source_id)
            if self._closed or epoch != self._epoch:
                return None
            try:
                liked = self.service.favorite_status(song.source_id, song.song_id,
                                                     expected_user_id=account.user_id)
                return account, liked, ""
            except OnlineMusicError as error:
                return account, None, error.code
        self._submit("status", fetch)

    def _set(self, state):
        if state != self._state:
            self._state = state
            self.changed.emit()

    def context(self, track_id: str) -> SongCollectionState:
        if self._closed or not track_id or track_id != self._state.track_id:
            return SongCollectionState()
        state = self._state
        if state.user_id and not self._account_matches(state.source_id, state.user_id):
            return replace(state, available=False, liked=None, busy=False, playlists=(),
                           playlists_pending=False, playlist_has_more=False,
                           message=self._ERRORS["account_changed"])
        return state

    def refresh(self):
        if not self._closed:
            song = self._visual_song()
            self._select(song, self._account_key(song.source_id) if song else None)

    def capture_authority(self, expected_track_id: str):
        """Capture an opaque song/account generation for a new UI gesture.

        A caller must retain the returned object rather than recapture it on
        release. Cached account identity includes same-user logout/re-login.
        """
        self._player_changed()
        token = self._gesture_authority
        return token if self.authority_matches(expected_track_id, token) else None

    def authority_matches(self, expected_track_id: str, token) -> bool:
        if self._closed or token is None or token is not self._gesture_authority:
            return False
        song = self._visual_song()
        return bool(song is not None and self._selection == (song.source_id, song.song_id)
                    and expected_track_id == self._state.track_id
                    and expected_track_id == f"owned:{song.source_id}:{song.song_id}"
                    and self._account_key(song.source_id) == self._observed_account)

    def _ready(self, expected_track_id, expected_authority=_CURRENT_AUTHORITY):
        self._player_changed()
        if (expected_authority is not _CURRENT_AUTHORITY
                and not self.authority_matches(expected_track_id, expected_authority)):
            return False
        state = self.context(expected_track_id)
        return bool(state.available and state.user_id and state.track_id == expected_track_id
                    and self._song is not None)

    def _safe_action(self, action):
        epoch = self._epoch
        source = self._state.source_id
        account_key = self._observed_account
        def run():
            if (self._closed or epoch != self._epoch
                    or self._account_key(source) != account_key):
                raise OnlineMusicError("cancelled", source)
            return action()
        return run

    def _submit(self, kind, action):
        self._serial += 1
        token = (kind, self._epoch, self._serial)
        self._tokens[kind] = token
        if self.tasks.submit(token, action):
            return True
        self._tokens.pop(kind, None)
        pending = bool(self._tokens.get("status") or self._tokens.get("favorite") or self._tokens.get("add"))
        self._set(replace(self._state, busy=pending,
                          playlists_pending=bool(self._tokens.get("playlists")),
                          message="服务繁忙，请稍后重试"))
        return False

    def toggle_favorite(self, expected_track_id: str, *, expected_authority=_CURRENT_AUTHORITY):
        if not self._ready(expected_track_id, expected_authority) or self._state.busy:
            return False
        # Unknown state is explicitly refreshed first; it must never be
        # mistaken for "not liked" and accidentally undo an existing favorite.
        if self._state.liked is None:
            self.refresh()
            return False
        song, user_id, target = self._song, self._state.user_id, not self._state.liked
        self._set(replace(self._state, busy=True, message="正在更新收藏…"))
        return self._submit("favorite", self._safe_action(lambda: self.service.set_favorite(
            song.source_id, song.song_id, target, expected_user_id=user_id)))

    def load_playlists(self, expected_track_id: str, more: bool = False, *, expected_authority=_CURRENT_AUTHORITY):
        if not self._ready(expected_track_id, expected_authority) or self._state.playlists_pending:
            return False
        if more and not self._state.playlist_has_more:
            return False
        song, user_id = self._song, self._state.user_id
        offset = self._playlist_offset if more else 0
        self._set(replace(self._state, playlists_pending=True, message="正在读取可收藏歌单…"))
        return self._submit("playlists", self._safe_action(lambda: (
            offset, self.service.writable_playlists(song.source_id, limit=100, offset=offset,
                                                    expected_user_id=user_id))))

    def add_to_playlist(self, playlist_id: str, expected_track_id: str, *, expected_authority=_CURRENT_AUTHORITY):
        if not self._ready(expected_track_id, expected_authority) or self._state.busy:
            return False
        if not any(playlist.playlist_id == playlist_id for playlist in self._state.playlists):
            self._set(replace(self._state, message="请选择列表中自己的歌单"))
            return False
        song, user_id = self._song, self._state.user_id
        self._set(replace(self._state, busy=True, message="正在添加到歌单…"))
        return self._submit("add", self._safe_action(lambda: self.service.add_song_to_playlist(
            song.source_id, song.song_id, playlist_id, expected_user_id=user_id)))

    @staticmethod
    def _art_identity(playlist):
        return playlist.source_id, playlist.playlist_id, playlist.artwork_url

    def playlist_artwork(self, playlist, expected_track_id, *, expected_authority=_CURRENT_AUTHORITY):
        """Return cached pixels only; widgets request work outside painting."""
        if (not isinstance(playlist, OnlinePlaylist)
                or not self._ready(expected_track_id, expected_authority)
                or playlist not in self._state.playlists):
            return QImage()
        key = self._art_identity(playlist)
        image = self._artwork.get(key)
        if image is None and callable(self._artwork_lookup):
            image = self._artwork_lookup(playlist)
            if isinstance(image, QImage) and not image.isNull():
                self._cache_playlist_artwork(key, image)
        return QImage(image) if isinstance(image, QImage) else QImage()

    def _cache_playlist_artwork(self, key, image):
        self._artwork[key] = QImage(image)
        self._artwork.move_to_end(key)
        while len(self._artwork) > 128:
            self._artwork.popitem(last=False)

    def request_playlist_artwork(self, playlist, expected_track_id, *, expected_authority=_CURRENT_AUTHORITY):
        if (not isinstance(playlist, OnlinePlaylist) or not playlist.artwork_url
                or not self._ready(expected_track_id, expected_authority)
                or playlist not in self._state.playlists):
            return False
        key = self._art_identity(playlist)
        if (not self.playlist_artwork(playlist, expected_track_id, expected_authority=expected_authority).isNull()
                or key in self._art_pending or key in self._art_retry_at
                or self._art_attempts.get(key, 0) >= 3 or len(self._art_queue) >= 32):
            return False
        self._art_pending.add(key)
        self._art_queue.append((self._epoch, playlist))
        self._pump_playlist_artwork()
        return True

    def _pump_playlist_artwork(self):
        while not self._closed and len(self._art_inflight) < 2 and self._art_queue:
            epoch, playlist = self._art_queue.popleft()
            key = self._art_identity(playlist)
            if epoch != self._epoch or playlist not in self._state.playlists:
                self._art_pending.discard(key)
                continue
            if self._art_tasks is None:
                self._art_tasks = MusicTasks(self)
                self._art_tasks.completed.connect(self._art_completed)
            self._serial += 1
            token = ("playlist-art", epoch, self._serial, key)
            account_key = self._observed_account
            self._art_attempts[key] = self._art_attempts.get(key, 0) + 1
            self._art_inflight[token] = playlist
            def fetch(playlist=playlist, epoch=epoch, account_key=account_key):
                if self._closed or epoch != self._epoch or self._account_key(playlist.source_id) != account_key:
                    return QImage()
                return fetch_online_thumbnail(playlist.artwork_url)
            if not self._art_tasks.submit(token, fetch):
                self._art_inflight.pop(token, None)
                self._art_pending.discard(key)
                self._schedule_playlist_art_retry(playlist)

    def _schedule_playlist_art_retry(self, playlist):
        key = self._art_identity(playlist)
        attempts = self._art_attempts.get(key, 0)
        if self._closed or attempts >= 3 or playlist not in self._state.playlists:
            return
        self._art_retry_at[key] = (playlist, time.monotonic() + .75 * max(1, attempts))
        self._art_retry_timer.start(750)

    def _retry_playlist_artwork(self):
        now = time.monotonic()
        for key, (playlist, due) in tuple(self._art_retry_at.items()):
            if due <= now:
                self._art_retry_at.pop(key, None)
                self.request_playlist_artwork(playlist, self._state.track_id,
                                              expected_authority=self._gesture_authority)
        if self._art_retry_at:
            self._art_retry_timer.start(max(1, round((min(item[1] for item in self._art_retry_at.values()) - now) * 1000)))

    def _art_completed(self, token, result, error):
        playlist = self._art_inflight.pop(token, None)
        if playlist is None:
            return
        key = self._art_identity(playlist)
        if token[1] == self._epoch:
            self._art_pending.discard(key)
        if (not self._closed and token[1] == self._epoch and playlist in self._state.playlists
                and self._account_matches(self._state.source_id, self._state.user_id)):
            if isinstance(result, QImage) and not result.isNull() and not error:
                self._cache_playlist_artwork(key, result)
                self._art_attempts.pop(key, None)
                self._art_retry_at.pop(key, None)
                self.changed.emit()
            else:
                self._schedule_playlist_art_retry(playlist)
        self._pump_playlist_artwork()

    def _completed(self, token, result, error):
        if self._closed or not isinstance(token, tuple) or len(token) != 3:
            return
        kind, epoch, _serial = token
        if epoch != self._epoch or self._tokens.get(kind) != token:
            return
        # A logout has no player signal of its own. Re-evaluate cached account
        # before publishing either success or a previously loaded playlist.
        if kind != "status" and not self._ready(self._state.track_id):
            return
        self._tokens.pop(kind, None)
        state = self._state
        busy = bool(self._tokens.get("status") or self._tokens.get("favorite") or self._tokens.get("add"))
        state = replace(state, busy=busy, playlists_pending=bool(self._tokens.get("playlists")))
        if error:
            self._set(replace(state, message=self._ERRORS.get(error, "操作暂未完成，请稍后重试"),
                              liked=None if kind == "favorite" else state.liked))
            return
        if kind == "status":
            if (not isinstance(result, tuple) or len(result) != 3
                    or not isinstance(result[0], AccountSnapshot)):
                self._set(replace(state, message="收藏状态暂不可用，请重试"))
                return
            account, liked, status_error = result
            account_key = self._account_key(state.source_id)
            if account_key != (True, account.user_id, id(account)):
                self.refresh()
                return
            if account_key != self._observed_account:
                self._gesture_authority = object()
            self._observed_account = account_key
            self._account_authority = account
            self._set(replace(state, user_id=account.user_id,
                              available=status_error not in {"unsupported", "unavailable", "auth_required", "account_changed", "cancelled"},
                              liked=liked if isinstance(liked, bool) else None,
                              message=self._ERRORS.get(status_error, "收藏状态暂不可用，请重试") if status_error else ""))
        elif kind == "favorite":
            if not isinstance(result, bool):
                self._set(replace(state, liked=None, message="收藏操作未确认，请刷新状态"))
                return
            self._set(replace(state, liked=result, message="已收藏" if result else "已取消收藏"))
        elif kind == "playlists":
            if (not isinstance(result, tuple) or len(result) != 2
                    or not isinstance(result[1], CatalogPage)):
                self._set(replace(state, message="歌单暂不可用，请重试"))
                return
            offset, page = result
            if page.next_offset < offset or page.has_more and page.next_offset <= offset:
                self._set(replace(state, message="歌单分页暂不可用，请重试"))
                return
            playlists = list(state.playlists) if offset else []
            seen = {playlist.playlist_id for playlist in playlists}
            for playlist in page.items:
                if isinstance(playlist, OnlinePlaylist) and playlist.source_id == state.source_id and playlist.playlist_id not in seen:
                    playlists.append(playlist)
                    seen.add(playlist.playlist_id)
            self._playlist_offset = page.next_offset
            self._set(replace(state, playlists=tuple(playlists), playlists_loaded=True,
                              playlist_has_more=page.has_more,
                              message="" if playlists else "尚无可添加的自建歌单"))
        elif kind == "add":
            self._set(replace(state, message="已添加到歌单"))

    def close(self):
        self._closed = True
        self._epoch += 1
        self._tokens.clear()
        self._state = SongCollectionState()
        self._account_authority = None
        self._gesture_authority = None
        self._art_queue.clear()
        self._art_pending.clear()
        self._art_retry_at.clear()
        self._art_retry_timer.stop()
        if self._art_tasks is not None:
            self._art_tasks.close()
        self.tasks.close()
