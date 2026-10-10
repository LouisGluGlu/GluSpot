"""Owned online music accounts. Network methods belong on a worker thread.

No credential is exposed by the public immutable snapshots. Source authentication
is optional and uses a separately installed, pinned Node bridge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Any


@dataclass(frozen=True)
class MusicSource:
    source_id: str
    name: str
    available: bool
    reason: str = ""
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountSnapshot:
    source_id: str
    logged_in: bool = False
    user_id: str = ""
    nickname: str = ""
    avatar_url: str = ""
    state: str = "signed_out"
    message: str = ""


@dataclass(frozen=True)
class LoginSnapshot:
    session_id: str
    source_id: str
    state: str
    qr_image_bytes: bytes = field(default=b"", repr=False)
    qr_url: str = field(default="", repr=False)
    expires_at: float = 0.0
    message: str = ""
    account: AccountSnapshot | None = None


@dataclass(frozen=True)
class OnlineSong:
    source_id: str
    song_id: str
    title: str
    artist: str = ""
    album: str = ""
    duration_seconds: float = 0.0
    artwork_url: str = ""


@dataclass(frozen=True)
class OnlinePlaylist:
    source_id: str
    playlist_id: str
    title: str
    artwork_url: str = ""
    track_count: int = 0


@dataclass(frozen=True)
class CatalogPage:
    items: tuple[OnlineSong | OnlinePlaylist, ...]
    next_offset: int = 0
    has_more: bool = False
    total: int | None = None


@dataclass(frozen=True)
class StreamInfo:
    source_id: str
    song_id: str
    url: str = field(default="", repr=False)
    playable: bool = False
    trial: bool = False
    quality: str = ""
    restriction: str = ""
    expires_at: float = 0.0


@dataclass(frozen=True)
class LyricPayload:
    source_id: str
    song_id: str
    raw_lrc: str = ""
    raw_yrc: str = ""
    translation: str = ""
    romanization: str = ""
    source: str = ""


class OnlineMusicError(RuntimeError):
    def __init__(self, code: str, source_id: str = "", message: str = "在线音乐请求失败"):
        super().__init__(message)
        self.code = code
        self.source_id = source_id
        self.message = message


class OnlineBackend(Protocol):
    def available(self, source_id: str) -> tuple[bool, str]: ...
    def request(self, source_id: str, operation: str, params: dict[str, Any], credential: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def close(self) -> None: ...


import base64
import math
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from api.online_music_credentials import ProtectedCredentialStore, CredentialProtectionError


@dataclass
class _LoginAttempt:
    snapshot: LoginSnapshot
    key: str = field(repr=False)
    generation: int = 0


class OnlineMusicService:
    _names = (("netease", "网易云音乐"), ("kugou", "酷狗音乐"), ("qq", "QQ音乐"), ("qishui", "汽水音乐"))
    _capabilities = {"netease": ("qr_login", "playlist", "daily", "personal_fm", "stream", "lyrics", "search"), "qq": ("qr_login", "playlist", "stream", "search"), "kugou": ("qr_login", "playlist", "stream", "search"), "qishui": ()}

    def __init__(self, backend: OnlineBackend | None = None, credential_store: ProtectedCredentialStore | None = None, *, clock=time.time):
        if backend is None:
            from api.online_music_bridge import NodeOnlineMusicBackend
            backend = NodeOnlineMusicBackend()
        self.backend = backend
        self.credential_store = credential_store or ProtectedCredentialStore()
        self._clock = clock
        self._lock = threading.RLock()
        self._accounts = {source: AccountSnapshot(source) for source, _ in self._names}
        self._credentials: dict[str, dict[str, Any]] = {}
        self._attempts: dict[str, _LoginAttempt] = {}
        self._generations = {source: 0 for source, _ in self._names}
        self._closed = False

    def list_sources(self) -> tuple[MusicSource, ...]:
        sources = []
        for source, name in self._names:
            ready, reason = self.backend.available(source)
            sources.append(MusicSource(source, name, ready, reason, self._capabilities[source] if ready else ()))
        return tuple(sources)

    def _check_source(self, source_id: str) -> None:
        if source_id not in self._accounts:
            raise OnlineMusicError("unsupported", source_id, "未知音乐来源")
        if self._closed:
            raise OnlineMusicError("closed", source_id, "在线音乐服务已关闭")
        ready, reason = self.backend.available(source_id)
        if not ready:
            raise OnlineMusicError("unavailable", source_id, reason)

    def _load_credential(self, source_id: str) -> dict[str, Any] | None:
        with self._lock:
            if source_id in self._credentials:
                return self._credentials[source_id]
        try:
            credential = self.credential_store.load(source_id)
        except CredentialProtectionError:
            raise OnlineMusicError("credential", source_id, "本机受保护的登录记录无法读取，请重新登录") from None
        if credential:
            with self._lock:
                self._credentials[source_id] = credential
        return credential

    def has_login_record(self, source_id: str) -> bool:
        """Local protected-record existence, without claiming live account validity."""
        if source_id not in self._accounts:
            return False
        credential = self._load_credential(source_id)
        if not credential:
            return False
        if source_id == "qq":
            return bool(credential.get("token") and isinstance(credential.get("sessions"), list) and credential["sessions"])
        cookies = credential.get("cookie")
        if not isinstance(cookies, dict):
            return False
        if source_id == "netease":
            return bool(cookies.get("MUSIC_U"))
        if source_id == "kugou":
            return bool(cookies.get("token") and cookies.get("userid"))
        return False

    @staticmethod
    def _image(data: Any) -> bytes:
        if not isinstance(data, str) or len(data) > 2 * 1024 * 1024:
            raise OnlineMusicError("response", message="音乐服务未返回有效二维码")
        value = data.split(",", 1)[1] if data.startswith("data:image/png;base64,") else data
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, TypeError):
            raise OnlineMusicError("response", message="音乐服务未返回有效二维码") from None
        if not decoded.startswith(b"\x89PNG\r\n\x1a\n") or len(decoded) > 1024 * 1024:
            raise OnlineMusicError("response", message="音乐服务未返回有效二维码")
        return decoded

    @staticmethod
    def _account(source_id: str, data: Any) -> AccountSnapshot:
        if not isinstance(data, dict) or data.get("logged_in") is not True or not str(data.get("user_id", "")).strip():
            return AccountSnapshot(source_id, state="signed_out", message="此来源尚未登录或登录已过期")
        return AccountSnapshot(source_id, True, str(data["user_id"])[:128], str(data.get("nickname", ""))[:256], OnlineMusicService._http_url(data.get("avatar_url", "")), "verified", "")

    @staticmethod
    def _http_url(value: Any) -> str:
        if not isinstance(value, str) or len(value) > 16384:
            return ""
        try:
            parsed = urlsplit(value)
            return value if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password else ""
        except ValueError:
            return ""

    def begin_login(self, source_id: str) -> LoginSnapshot:
        self._check_source(source_id)
        with self._lock:
            self._generations[source_id] += 1
            generation = self._generations[source_id]
            for attempt in self._attempts.values():
                if attempt.snapshot.source_id == source_id and attempt.snapshot.state not in {"confirmed", "expired", "cancelled", "error"}:
                    attempt.snapshot = self._snapshot_state(attempt.snapshot, "cancelled", "登录已取消")
            session_id = secrets.token_hex(16)
        # Deliberately do not import an existing session for a fresh QR attempt.
        response = self.backend.request(source_id, "begin_login", {})
        data = response.get("data", {})
        if not isinstance(data, dict) or not isinstance(data.get("key"), str) or not data["key"]:
            raise OnlineMusicError("response", source_id, "音乐服务未返回有效二维码")
        image = self._image(data.get("qr_image"))
        qr_url = self._http_url(data.get("qr_url", ""))
        try:
            ttl = max(30.0, min(300.0, float(data.get("ttl", 180))))
        except (ValueError, TypeError):
            ttl = 180.0
        snapshot = LoginSnapshot(session_id, source_id, "waiting", image, qr_url, self._clock() + ttl, "使用手机音乐应用扫码登录")
        with self._lock:
            if self._closed or generation != self._generations[source_id]:
                snapshot = self._snapshot_state(snapshot, "cancelled", "登录已取消")
            self._attempts[session_id] = _LoginAttempt(snapshot, data["key"], generation)
            # Bound retained terminal attempts. Active attempts are never removed.
            if len(self._attempts) > 16:
                for key, attempt in tuple(self._attempts.items()):
                    if key != session_id and attempt.snapshot.state in {"confirmed", "expired", "cancelled", "error"}:
                        del self._attempts[key]
                        if len(self._attempts) <= 16:
                            break
        return snapshot

    @staticmethod
    def _snapshot_state(snapshot: LoginSnapshot, state: str, message: str, account: AccountSnapshot | None = None) -> LoginSnapshot:
        # Terminal states drop QR account material from public snapshots.
        keep_qr = state in {"waiting", "scanned"}
        return LoginSnapshot(snapshot.session_id, snapshot.source_id, state, snapshot.qr_image_bytes if keep_qr else b"", snapshot.qr_url if keep_qr else "", snapshot.expires_at, message, account)

    def poll_login(self, session_id: str) -> LoginSnapshot:
        with self._lock:
            attempt = self._attempts.get(session_id)
            if attempt is None:
                raise OnlineMusicError("expired", message="二维码会话不存在，请刷新")
            snapshot = attempt.snapshot
            if snapshot.state not in {"waiting", "scanned"}:
                return snapshot
            if self._clock() >= snapshot.expires_at:
                attempt.snapshot = self._snapshot_state(snapshot, "expired", "二维码已过期，请刷新")
                return attempt.snapshot
            source_id, generation, key = snapshot.source_id, attempt.generation, attempt.key
        try:
            response = self.backend.request(source_id, "poll_login", {"key": key})
            data = response.get("data", {})
            state = data.get("state") if isinstance(data, dict) else None
            if state not in {"waiting", "scanned", "confirmed", "expired", "error", "cancelled"}:
                raise OnlineMusicError("response", source_id, "音乐服务返回的数据无效")
            account = None
            credential = response.get("credential")
            if state == "confirmed":
                if not isinstance(credential, dict):
                    raise OnlineMusicError("response", source_id, "音乐服务未返回可验证的登录会话")
                verified = self.backend.request(source_id, "account_status", {}, credential)
                account = self._account(source_id, verified.get("data"))
                credential = verified.get("credential", credential)
                if not account.logged_in:
                    raise OnlineMusicError("auth", source_id, "扫码确认后未能验证账号，请重试")
            messages = {"waiting": "使用手机音乐应用扫码登录", "scanned": "已扫码，请在手机上确认", "confirmed": "登录成功", "expired": "二维码已过期，请刷新", "cancelled": "登录已取消", "error": "音乐服务登录失败，请刷新"}
            with self._lock:
                if self._closed or generation != self._generations[source_id] or attempt.snapshot.state == "cancelled":
                    return attempt.snapshot
                if self._clock() >= snapshot.expires_at:
                    attempt.snapshot = self._snapshot_state(snapshot, "expired", messages["expired"])
                    return attempt.snapshot
                if state == "confirmed":
                    try:
                        self.credential_store.save(source_id, credential)
                    except (CredentialProtectionError, OSError):
                        raise OnlineMusicError("credential", source_id, "账号已确认，但本机加密保存失败，请重试") from None
                    self._credentials[source_id] = credential
                    self._accounts[source_id] = account
                attempt.snapshot = self._snapshot_state(snapshot, state, messages[state], account)
                return attempt.snapshot
        except OnlineMusicError as error:
            with self._lock:
                if generation != self._generations[source_id] or attempt.snapshot.state == "cancelled":
                    return attempt.snapshot
                attempt.snapshot = self._snapshot_state(snapshot, "error", error.message)
                return attempt.snapshot

    def invalidate_login(self, session_id: str) -> None:
        """Invalidate immediately without any network or child-process operation."""
        with self._lock:
            attempt = self._attempts.get(session_id)
            if attempt is None or attempt.snapshot.state in {"confirmed", "cancelled", "expired"}:
                return
            source_id = attempt.snapshot.source_id
            self._generations[source_id] += 1
            attempt.snapshot = self._snapshot_state(attempt.snapshot, "cancelled", "登录已取消")

    def invalidate_source_login(self, source_id: str) -> None:
        """Also fence an in-flight begin that has not produced a session id yet."""
        with self._lock:
            if source_id not in self._generations:
                return
            self._generations[source_id] += 1
            for attempt in self._attempts.values():
                if attempt.snapshot.source_id == source_id and attempt.snapshot.state in {"waiting", "scanned"}:
                    attempt.snapshot = self._snapshot_state(attempt.snapshot, "cancelled", "登录已取消")

    def cancel_login(self, session_id: str) -> None:
        self.invalidate_login(session_id)
        with self._lock:
            attempt = self._attempts.get(session_id)
            if attempt is None or attempt.snapshot.state == "confirmed":
                return
            source_id, key = attempt.snapshot.source_id, attempt.key
        try:
            self.backend.request(source_id, "cancel_login", {"key": key})
        except OnlineMusicError:
            pass

    def account_status(self, source_id: str, refresh: bool = False) -> AccountSnapshot:
        if source_id not in self._accounts:
            raise OnlineMusicError("unsupported", source_id, "未知音乐来源")
        if not refresh:
            with self._lock:
                return self._accounts[source_id]
        self._check_source(source_id)
        credential = self._load_credential(source_id)
        if not credential:
            return AccountSnapshot(source_id)
        with self._lock:
            generation = self._generations[source_id]
        try:
            response = self.backend.request(source_id, "account_status", {}, credential)
            account = self._account(source_id, response.get("data"))
            refreshed = response.get("credential")
        except OnlineMusicError as error:
            account = AccountSnapshot(source_id, state="error", message=error.message)
            refreshed = None
        with self._lock:
            if not self._closed and generation == self._generations[source_id]:
                self._accounts[source_id] = account
                if account.logged_in and isinstance(refreshed, dict):
                    try:
                        self.credential_store.save(source_id, refreshed)
                        self._credentials[source_id] = refreshed
                    except (CredentialProtectionError, OSError):
                        self._accounts[source_id] = AccountSnapshot(source_id, state="error", message="本机加密保存失败，请重新登录")
                return self._accounts[source_id]
            return self._accounts[source_id]

    def logout(self, source_id: str) -> None:
        if source_id not in self._accounts:
            raise OnlineMusicError("unsupported", source_id, "未知音乐来源")
        with self._lock:
            self._generations[source_id] += 1
            self._accounts[source_id] = AccountSnapshot(source_id)
            self._credentials.pop(source_id, None)
            for attempt in self._attempts.values():
                if attempt.snapshot.source_id == source_id:
                    attempt.snapshot = self._snapshot_state(attempt.snapshot, "cancelled", "登录已取消")
            try:
                self.credential_store.delete(source_id)
            except (CredentialProtectionError, OSError):
                raise OnlineMusicError("credential", source_id, "本机登录记录删除失败") from None
        try:
            self.backend.request(source_id, "logout", {})
        except OnlineMusicError:
            pass

    def _catalog(self, source_id: str, operation: str, params: dict[str, Any]) -> Any:
        self._check_source(source_id)
        if not self.account_status(source_id).logged_in:
            account = self.account_status(source_id, refresh=True)
            if not account.logged_in:
                raise OnlineMusicError("auth_required", source_id, account.message or "请先登录此音乐来源")
        credential = self._load_credential(source_id)
        with self._lock:
            generation = self._generations[source_id]
        response = self.backend.request(source_id, operation, params, credential)
        with self._lock:
            if self._closed or generation != self._generations[source_id]:
                raise OnlineMusicError("cancelled", source_id, "音乐请求已取消")
            refreshed = response.get("credential")
            if isinstance(refreshed, dict):
                try:
                    self.credential_store.save(source_id, refreshed)
                    self._credentials[source_id] = refreshed
                except (CredentialProtectionError, OSError):
                    raise OnlineMusicError("credential", source_id, "本机加密保存失败") from None
        data = response.get("data")
        if not isinstance(data, dict):
            raise OnlineMusicError("response", source_id, "音乐服务返回的数据无效")
        if data.get("code") in {301, 401, 403}:
            with self._lock:
                self._accounts[source_id] = AccountSnapshot(source_id, state="expired", message="登录已过期，请重新登录")
            raise OnlineMusicError("auth_required", source_id, "登录已过期，请重新登录")
        return data

    @staticmethod
    def _page_bounds(limit: int, offset: int) -> tuple[int, int]:
        if isinstance(limit, bool) or isinstance(offset, bool):
            raise OnlineMusicError("argument", message="无效的歌单分页参数")
        try:
            return max(1, min(1000, int(limit))), max(0, min(100000, int(offset)))
        except (ValueError, TypeError, OverflowError):
            raise OnlineMusicError("argument", message="无效的歌单分页参数") from None

    @staticmethod
    def _nonnegative_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (ValueError, TypeError, OverflowError):
            return 0

    @staticmethod
    def _list(data: dict[str, Any], *names: str) -> list[dict[str, Any]]:
        containers = [data]
        for key in ("data", "result"):
            if isinstance(data.get(key), dict):
                containers.append(data[key])
        for container in containers:
            for name in names:
                value = container.get(name)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _song(source_id: str, item: dict[str, Any]) -> OnlineSong:
        nested = item.get("songInfo") or item.get("songinfo") or item.get("musicData")
        if isinstance(nested, dict):
            item = nested
        album = item.get("al") or item.get("album") or item.get("albuminfo") or {}
        album = album if isinstance(album, dict) else {"name": str(album)}
        artists = item.get("ar") or item.get("artists") or item.get("singer") or item.get("singerinfo") or []
        artist = " / ".join(str(a.get("name", "")) for a in artists if isinstance(a, dict)) if isinstance(artists, list) else ""
        artist = artist or str(item.get("artist") or item.get("author_name") or item.get("SingerName") or "")
        duration = item.get("dt", item.get("duration", item.get("interval", item.get("timelen", item.get("Duration", 0)))))
        try:
            duration = float(duration)
            # NetEase's older personal-FM song shape names its milliseconds
            # `duration`; QQ/Kugou duration/interval fields remain seconds.
            if "dt" in item or "timelen" in item or source_id == "netease" and "duration" in item:
                duration /= 1000
            duration = max(0.0, duration) if math.isfinite(duration) else 0.0
        except (ValueError, TypeError):
            duration = 0.0
        song_id = item.get("mid") or item.get("songmid") or item.get("hash") or item.get("FileHash") or item.get("id") or item.get("songid") or ""
        cover = album.get("picUrl") or item.get("picUrl") or item.get("cover") or item.get("img") or ""
        if source_id == "qq" and not cover and isinstance(album.get("mid"), str) and re.fullmatch(r"[A-Za-z0-9]{1,64}", album["mid"]):
            cover = f"https://y.gtimg.cn/music/photo_new/T002R500x500M000{album['mid']}.jpg"
        if isinstance(cover, str):
            cover = cover.replace("{size}", "500")
        return OnlineSong(source_id, str(song_id), str(item.get("name") or item.get("title") or item.get("songname") or item.get("SongName") or item.get("audio_name") or item.get("filename") or item.get("FileName") or ""), artist, str(album.get("name") or item.get("album_name") or item.get("AlbumName") or ""), duration, OnlineMusicService._http_url(cover))

    @staticmethod
    def _playlist(source_id: str, item: dict[str, Any]) -> OnlinePlaylist:
        cover = item.get("coverImgUrl") or item.get("cover_url") or item.get("bigpicUrl") or item.get("picUrl") or item.get("picurl") or item.get("img") or item.get("imgurl") or item.get("logo") or ""
        if isinstance(cover, str):
            cover = cover.replace("{size}", "500")
        return OnlinePlaylist(
            source_id,
            str((item.get("global_collection_id") or item.get("gid") if source_id == "kugou" else None) or item.get("id") or item.get("tid") or item.get("dissid") or item.get("dirId") or item.get("dirid") or item.get("global_collection_id") or item.get("listid") or item.get("specialid") or ""),
            str(item.get("name") or item.get("title") or item.get("diss_name") or item.get("dissname") or item.get("listname") or item.get("specialname") or ""),
            OnlineMusicService._http_url(cover),
            OnlineMusicService._nonnegative_int(item.get("trackCount") or item.get("song_cnt") or item.get("count") or item.get("song_count") or item.get("songnum")),
        )

    def playlists(self, source_id: str, limit: int = 100, offset: int = 0) -> CatalogPage:
        limit, offset = self._page_bounds(limit, offset)
        account = self.account_status(source_id)
        if not account.logged_in:
            account = self.account_status(source_id, refresh=True)
        data = self._catalog(source_id, "playlists", {"user_id": account.user_id, "limit": limit, "offset": offset})
        raw = self._list(data, "playlist", "playlists", "v_playlist", "list", "lists", "info")
        if source_id == "qq":
            raw = raw[offset:offset + limit]
        items = tuple(self._playlist(source_id, item) for item in raw)
        items = tuple(item for item in items if item.playlist_id and item.title)
        return CatalogPage(items, offset + len(raw), bool(data.get("more") or len(raw) == limit), None)

    def search(self, source_id: str, query: str, kind: str = "songs", limit: int = 30, offset: int = 0) -> CatalogPage:
        self._check_source(source_id)
        if not isinstance(query, str) or not query.strip() or len(query) > 200 or any(ord(char) < 32 for char in query) or kind not in {"songs", "playlists"}:
            raise OnlineMusicError("argument", source_id, "请输入有效关键词并选择歌曲或歌单")
        limit, offset = self._page_bounds(limit, offset)
        limit = min(100, limit)
        data = self._catalog(source_id, "search", {"query": query.strip(), "kind": kind, "limit": limit, "offset": offset})
        raw = self._list(data, "items")
        if kind == "songs":
            items = tuple(self._song(source_id, item) for item in raw)
            items = tuple(item for item in items if item.song_id and item.title)
        else:
            items = tuple(self._playlist(source_id, item) for item in raw if source_id != "kugou" or item.get("gid") or item.get("global_collection_id"))
            items = tuple(item for item in items if item.playlist_id and item.title)
        total = data.get("total")
        total = self._nonnegative_int(total) if total is not None else None
        next_offset = self._nonnegative_int(data.get("next_offset"))
        has_more = data.get("has_more") is True and next_offset > offset
        return CatalogPage(items, max(offset, next_offset), has_more, total)

    def playlist_tracks(self, source_id: str, playlist_id: str, limit: int = 100, offset: int = 0) -> CatalogPage:
        limit, offset = self._page_bounds(limit, offset)
        if source_id == "qq":
            # Pinned owned-playlist API accepts at most 100 songs per window.
            limit = min(100, limit)
        data = self._catalog(source_id, "playlist_tracks", {"playlist_id": str(playlist_id), "limit": limit, "offset": offset})
        raw = self._list(data, "songs", "songlist", "songList", "tracks", "info", "list", "lists")
        if source_id == "qq" and not data.get("_windowed"):
            raw = raw[offset:offset + limit]
        items = tuple(self._song(source_id, item) for item in raw)
        items = tuple(item for item in items if item.song_id and item.title)
        return CatalogPage(items, offset + len(raw), bool(data.get("more") or len(raw) == limit), None)

    def daily_recommendations(self, source_id: str = "netease") -> tuple[OnlineSong, ...]:
        if source_id != "netease":
            raise OnlineMusicError("unsupported", source_id, "此来源暂不支持每日推荐")
        data = self._catalog(source_id, "daily_recommendations", {})
        raw = self._list(data, "dailySongs", "recommend", "songs")
        return tuple(song for item in raw if (song := self._song(source_id, item)).song_id and song.title)

    def personal_fm(self, source_id: str = "netease") -> tuple[OnlineSong, ...]:
        if source_id != "netease":
            raise OnlineMusicError("unsupported", source_id, "此来源暂不支持私人漫游")
        data = self._catalog(source_id, "personal_fm", {})
        raw = data.get("data") if isinstance(data.get("data"), list) else self._list(data, "songs")
        return tuple(song for item in raw if isinstance(item, dict) and (song := self._song(source_id, item)).song_id and song.title)

    def stream_url(self, source_id: str, song_id: str) -> StreamInfo:
        data = self._catalog(source_id, "stream_url", {"song_id": str(song_id)})
        if source_id == "netease":
            entries = data.get("data")
            value = entries[0] if isinstance(entries, list) and entries and isinstance(entries[0], dict) else {}
            url = self._http_url(value.get("url"))
            trial = bool(value.get("freeTrialInfo"))
            return StreamInfo(source_id, str(song_id), url, bool(url), trial, str(value.get("level") or "standard"), "试听片段" if trial else ("平台未授权可播放地址" if not url else ""), self._clock() + self._nonnegative_int(value.get("expi")))
        value = data.get("data") if isinstance(data.get("data"), dict) else data
        urls = value.get("url") or value.get("playUrl") or value.get("play_url") or ""
        if isinstance(urls, list):
            urls = urls[0] if urls else ""
        url = self._http_url(urls)
        return StreamInfo(source_id, str(song_id), url, bool(url), bool(value.get("trial") or value.get("freeTrialInfo")), "standard", "" if url else "平台未授权可播放地址")

    def lyrics(self, source_id: str, song_id: str) -> LyricPayload:
        if source_id != "netease":
            raise OnlineMusicError("unsupported", source_id, "此来源暂不支持逐字歌词获取")
        data = self._catalog(source_id, "lyrics", {"song_id": str(song_id)})
        def text(key: str) -> str:
            value = data.get(key)
            result = value.get("lyric", "") if isinstance(value, dict) else ""
            return result[:2 * 1024 * 1024] if isinstance(result, str) else ""
        return LyricPayload(source_id, str(song_id), text("lrc"), text("yrc"), text("ytlrc") or text("tlyric"), text("yromalrc") or text("romalrc"), "netease-account")

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for source_id in self._generations:
                self._generations[source_id] += 1
            for attempt in self._attempts.values():
                if attempt.snapshot.state in {"waiting", "scanned"}:
                    attempt.snapshot = self._snapshot_state(attempt.snapshot, "cancelled", "登录已取消")
            self._credentials.clear()
        self.backend.close()


def make_online_music_service() -> OnlineMusicService:
    return OnlineMusicService()
