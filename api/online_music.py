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
    failure_category: str = ""
    failure_stage: str = ""
    retry_after_seconds: float = 0.


@dataclass(frozen=True)
class OnlineSong:
    source_id: str
    song_id: str
    title: str
    artist: str = ""
    album: str = ""
    duration_seconds: float = 0.0
    artwork_url: str = ""
    media_kind: str = "song"
    # Service-provided DJ speech has its own expiring authorized address. It
    # must never be sent to the ordinary song URL or lyric endpoints.
    direct_stream: StreamInfo | None = field(default=None, repr=False)
    # Verified service association; speech and visual songs are distinct.
    introduced_song_id: str = ""


@dataclass(frozen=True)
class OnlinePlaylist:
    source_id: str
    playlist_id: str
    title: str
    artwork_url: str = ""
    track_count: int = 0


@dataclass(frozen=True)
class PersonalDjVoice:
    timbre_key: str
    name: str
    description: str = ""
    is_default: bool = False
    # Genuine catalog audition; service addresses stay out of repr/logging.
    preview_stream: StreamInfo | None = field(default=None, repr=False)
    preview_duration_seconds: float = 0.0


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
    raw_qrc: str = ""
    raw_ttml: str = ""
    raw_netease: str = ""


class OnlineMusicError(RuntimeError):
    def __init__(self, code: str, source_id: str = "", message: str = "在线音乐请求失败", *, stage="", category="", session_lost=False):
        super().__init__(message)
        self.code = code
        self.source_id = source_id
        self.message = message
        self.stage = stage if stage in {"begin_login", "poll_login", "account_status", "catalog", "auth_queue", "runtime_start", "request", "module_load"} else ""
        self.category = category if category in {"network", "timeout", "runtime", "expired", "auth_required", "invalid_response", "account_changed", "cancelled"} else code
        self.session_lost = session_lost is True


class OnlineBackend(Protocol):
    def available(self, source_id: str) -> tuple[bool, str]: ...
    def request(self, source_id: str, operation: str, params: dict[str, Any], credential: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def close(self) -> None: ...


import base64
import json
import math
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from api.online_music_credentials import ProtectedCredentialStore, CredentialProtectionError
from api.online_music_diagnostics import current_login_trace, login_phase, login_request_guard


@dataclass
class _LoginAttempt:
    snapshot: LoginSnapshot
    key: str = field(repr=False)
    generation: int = 0
    pending_credential: dict | None = field(default=None, repr=False)
    failures: int = 0
    retry_at: float = 0.


class OnlineMusicService:
    _names = (("netease", "网易云音乐"), ("kugou", "酷狗音乐"), ("qq", "QQ音乐"), ("qishui", "汽水音乐"))
    _capabilities = {"netease": ("qr_login", "playlist", "recommendations", "daily", "personal_fm", "personal_dj", "stream", "lyrics", "search", "favorite", "playlist_write"), "qq": ("qr_login", "playlist", "recommendations", "stream", "lyrics", "search"), "kugou": ("qr_login", "playlist", "stream", "search"), "qishui": ()}

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
        self._dj_voice_catalogs: dict[str, tuple[int, tuple[PersonalDjVoice, ...]]] = {}
        # Only the two NetEase entry cards use this cache. A preview keeps its
        # real batch for the next playback call instead of throwing away FM's
        # advancing recommendation window just to acquire a picture.
        self._recommendation_locks = {kind: threading.Lock() for kind in ("daily", "fm")}
        self._recommendation_previews: dict[str, tuple[int, float, tuple[OnlineSong, ...]]] = {}
        self._recommendation_covers: dict[str, tuple[int, float, str]] = {}
        self._closed = False
        self._validation_locks = {source: threading.Lock() for source, _ in self._names}
        self._validation_serials = {source: 0 for source, _ in self._names}
        self._validation_epochs = {source: 0 for source, _ in self._names}
        self._validation_completed_keys = {source: None for source, _ in self._names}
        self._login_cancel_events = {source: threading.Event() for source, _ in self._names}
        self._validation_cancel_events = {source: threading.Event() for source, _ in self._names}

    def _cancel_source_generation(self, source_id):
        """Called under _lock; transport reads these events without locking back."""
        self._login_cancel_events[source_id].set()
        self._login_cancel_events[source_id] = threading.Event()
        self._validation_cancel_events[source_id].set()
        self._validation_cancel_events[source_id] = threading.Event()

    def cancel_account_validation(self, source_id):
        """Abandon a verification without invalidating an established account/catalog."""
        with self._lock:
            if source_id not in self._validation_epochs:
                return
            self._validation_epochs[source_id] += 1
            self._validation_cancel_events[source_id].set()
            self._validation_cancel_events[source_id] = threading.Event()
            # Cancellation only signals/terminates the owned child; it never
            # waits for network I/O. Keep registration fenced until signalled.
            cancel = getattr(self.backend, "cancel_login_request", None)
            if callable(cancel):
                cancel(source_id)

    def restore_account(self, source_id: str) -> AccountSnapshot:
        """Worker-only local presentation restore, never grants authorization."""
        if source_id not in self._accounts:
            raise OnlineMusicError("unsupported", source_id)
        with self._lock:
            generation = self._generations[source_id]
            account = self._accounts[source_id]
            if account.logged_in:
                return account
        credential = self._load_credential(source_id)
        from api.online_music_resume import cached_account, read_resume
        restored = cached_account(read_resume(credential, source_id, self._clock()), source_id)
        with self._lock:
            if not self._closed and generation == self._generations[source_id] and not self._accounts[source_id].logged_in:
                if restored.state == "cached":
                    self._accounts[source_id] = restored
            return self._accounts[source_id]

    def cached_catalog(self, source_id: str, section="playlists") -> CatalogPage:
        """Account-bound display data only; callers still gate every live action."""
        if section not in {"playlists", "recommended"}:
            return CatalogPage(())
        credential = self._load_credential(source_id)
        from api.online_music_resume import read_resume, read_catalog
        with self._lock:
            account = self._accounts[source_id]
            data = read_resume(credential, source_id, self._clock())
            if not data or account.user_id != data["profile"]["user_id"]:
                return CatalogPage(())
        return read_catalog(data, source_id, section)

    def _save_catalog_snapshot(self, source_id, section, page, expected_user_id):
        from api.online_music_resume import attach_catalog
        with self._lock:
            account = self._accounts[source_id]
            credential = self._credentials.get(source_id)
            if not account.logged_in or account.user_id != expected_user_id or not credential:
                return
            updated = attach_catalog(credential, section, page)
            try:
                self.credential_store.save(source_id, updated)
                self._credentials[source_id] = updated
            except (CredentialProtectionError, OSError):
                # Optional presentation persistence cannot invalidate live auth.
                pass

    def _clear_recommendation_previews(self, source_id):
        if source_id == "netease":
            self._recommendation_previews.clear()
            self._recommendation_covers.clear()

    def list_sources(self) -> tuple[MusicSource, ...]:
        sources = []
        for source, name in self._names:
            ready, reason = self.backend.available(source)
            sources.append(MusicSource(source, name, ready, reason, self._capabilities[source] if ready else ()))
        return tuple(sources)

    def prepare_login_runtime(self, source_id: str) -> bool:
        """Optional, offline module preparation when the login page is shown."""
        self._check_source(source_id)
        prepare = getattr(self.backend, "prepare_login", None)
        return bool(prepare(source_id)) if callable(prepare) else False

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
            generation = self._generations[source_id]
        try:
            credential = self.credential_store.load(source_id)
        except CredentialProtectionError:
            raise OnlineMusicError("credential", source_id, "本机受保护的登录记录无法读取，请重新登录") from None
        if credential:
            with self._lock:
                if self._closed or generation != self._generations[source_id]:
                    return self._credentials.get(source_id)
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
        identity = data.get("user_id") if isinstance(data, dict) else None
        if (not isinstance(data, dict) or data.get("logged_in") is not True
                or type(identity) not in {str, int} or not str(identity).strip()):
            return AccountSnapshot(source_id, state="signed_out", message="此来源尚未登录或登录已过期")
        return AccountSnapshot(source_id, True, str(data["user_id"])[:128], str(data.get("nickname", ""))[:256], OnlineMusicService._artwork_url(data.get("avatar_url", "")), "verified", "")

    @staticmethod
    def _http_url(value: Any) -> str:
        if not isinstance(value, str) or len(value) > 16384:
            return ""
        try:
            parsed = urlsplit(value)
            return value if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password else ""
        except ValueError:
            return ""

    @staticmethod
    def _artwork_url(value: Any) -> str:
        """Normalize actual platform image templates; retain strict URL checks."""
        if not isinstance(value, str):
            return ""
        value = value.replace("{size}", "500")
        if value.startswith("//"):
            value = "https:" + value
        return OnlineMusicService._http_url(value)

    def begin_login(self, source_id: str) -> LoginSnapshot:
        self._check_source(source_id)
        with self._lock:
            self._generations[source_id] += 1
            self._cancel_source_generation(source_id)
            self._clear_recommendation_previews(source_id)
            generation = self._generations[source_id]
            cancelled = self._login_cancel_events[source_id]
            for attempt in self._attempts.values():
                if attempt.snapshot.source_id == source_id and attempt.snapshot.state not in {"confirmed", "expired", "cancelled", "error"}:
                    attempt.snapshot = self._snapshot_state(attempt.snapshot, "cancelled", "登录已取消")
            session_id = secrets.token_hex(16)
        # Deliberately do not import an existing session for a fresh QR attempt.
        device = None
        if source_id == "qq":
            saved = self._load_credential(source_id)
            if saved and isinstance(saved.get("device_context"), dict):
                device = {"device_context": saved["device_context"]}
        with login_request_guard(cancelled), login_phase("qr_acquisition"):
            response = self.backend.request(source_id, "begin_login", {}, device) if device else self.backend.request(source_id, "begin_login", {})
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
            elif source_id == "qq" and isinstance(response.get("credential"), dict) and isinstance(response["credential"].get("device_context"), dict):
                saved = dict(self._credentials.get(source_id) or {})
                saved["device_context"] = response["credential"]["device_context"]
                try:
                    self.credential_store.save(source_id, saved)
                    self._credentials[source_id] = saved
                except (CredentialProtectionError, OSError):
                    pass
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
            cancelled = self._login_cancel_events[source_id]
            if self._clock() < attempt.retry_at:
                return snapshot
        try:
            if attempt.pending_credential is None:
                with login_request_guard(cancelled), login_phase("scan_detection"):
                    response = self.backend.request(source_id, "poll_login", {"key": key})
                data = response.get("data", {})
            else:
                response = {"credential": attempt.pending_credential}
                data = {"state": "confirmed"}
            state = data.get("state") if isinstance(data, dict) else None
            if state not in {"waiting", "scanned", "confirmed", "expired", "error", "cancelled"}:
                raise OnlineMusicError("response", source_id, "音乐服务返回的数据无效")
            account = None
            credential = response.get("credential")
            if state == "confirmed":
                if not isinstance(credential, dict):
                    raise OnlineMusicError("response", source_id, "音乐服务未返回可验证的登录会话")
                with self._lock:
                    if self._closed or generation != self._generations[source_id] or attempt.snapshot.state == "cancelled":
                        return attempt.snapshot
                    attempt.pending_credential = credential
                with login_request_guard(cancelled), login_phase("confirmed_account_validation"):
                    verified = self.backend.request(source_id, "account_status", {}, credential)
                verified_data = verified.get("data")
                account = self._account(source_id, verified_data)
                if (not isinstance(verified_data, dict) or not isinstance(verified_data.get("logged_in"), bool)
                        or verified_data.get("logged_in") is True and not account.logged_in):
                    raise OnlineMusicError("invalid_response", source_id, "账号核验响应不完整，请重试")
                credential = verified.get("credential", credential)
                if not account.logged_in:
                    raise OnlineMusicError("auth", source_id, "扫码确认后未能验证账号，请重试")
            messages = {"waiting": "使用手机音乐应用扫码登录", "scanned": "已扫码，请在手机上确认", "confirmed": "登录成功", "expired": "二维码已过期，请刷新", "cancelled": "登录已取消", "error": "音乐服务登录失败，请刷新"}
            with self._lock:
                if self._closed or generation != self._generations[source_id] or attempt.snapshot.state == "cancelled":
                    return attempt.snapshot
                if self._clock() >= snapshot.expires_at:
                    attempt.pending_credential = None
                    attempt.snapshot = self._snapshot_state(snapshot, "expired", messages["expired"])
                    return attempt.snapshot
                if state == "confirmed":
                    from api.online_music_resume import attach_profile
                    credential = attach_profile(credential, account, self._credentials.get(source_id), self._clock())
                    try:
                        self.credential_store.save(source_id, credential)
                    except (CredentialProtectionError, OSError):
                        raise OnlineMusicError("credential", source_id, "账号已确认，但本机加密保存失败，请重试") from None
                    self._credentials[source_id] = credential
                    self._accounts[source_id] = account
                    self._dj_voice_catalogs.pop(source_id, None)
                    self._clear_recommendation_previews(source_id)
                    attempt.pending_credential = None
                attempt.failures = 0
                attempt.snapshot = self._snapshot_state(snapshot, state, messages[state], account)
                if state == "error":
                    from dataclasses import replace
                    category = data.get("failure_category")
                    category = category if isinstance(category, str) and category in {"network", "timeout", "runtime", "invalid_response", "auth", "auth_required"} else "request_failed"
                    safe_messages = {"network": "登录确认连接失败，请刷新二维码", "timeout": "登录确认超时，请刷新二维码",
                                     "runtime": "登录服务异常，请刷新二维码", "invalid_response": "扫码确认数据无效，请刷新二维码"}
                    attempt.snapshot = replace(attempt.snapshot, failure_category=category, failure_stage="poll_login",
                                               message=safe_messages.get(category, messages[state]))
                return attempt.snapshot
        except OnlineMusicError as error:
            with self._lock:
                if generation != self._generations[source_id] or attempt.snapshot.state == "cancelled":
                    return attempt.snapshot
                if error.session_lost and attempt.pending_credential is None:
                    from dataclasses import replace
                    attempt.snapshot = replace(self._snapshot_state(snapshot, "error", "登录服务已重启，请刷新二维码"),
                                               failure_category="runtime", failure_stage="poll_login")
                    return attempt.snapshot
                attempt.failures += 1
                if error.code in {"network", "timeout", "runtime", "invalid_response", "response"} and attempt.failures <= 3 and self._clock() < snapshot.expires_at:
                    delay = min(4., .75 * 2 ** (attempt.failures - 1))
                    attempt.retry_at = self._clock() + delay
                    stage = "account_status" if attempt.pending_credential is not None else "poll_login"
                    state = "scanned" if attempt.pending_credential is not None else snapshot.state
                    message = "已确认，账号核验暂不可用，正在重试" if stage == "account_status" else "网络暂不可用，保留二维码并自动重试"
                    attempt.snapshot = LoginSnapshot(snapshot.session_id, source_id, state,
                        snapshot.qr_image_bytes, snapshot.qr_url, snapshot.expires_at, message,
                        failure_category=error.code, failure_stage=stage, retry_after_seconds=delay)
                    return attempt.snapshot
                from dataclasses import replace
                stage = "account_status" if attempt.pending_credential is not None else "poll_login"
                category = error.code if error.code in {"network", "timeout", "runtime", "invalid_response", "response", "auth", "auth_required", "credential", "expired"} else "request_failed"
                if category not in {"network", "timeout", "runtime", "invalid_response", "response", "credential"}:
                    attempt.pending_credential = None
                message = "扫码已确认，账号核验失败；点击重试核验" if attempt.pending_credential is not None else error.message
                attempt.snapshot = replace(self._snapshot_state(snapshot, "error", message),
                                           failure_category=category, failure_stage=stage)
                return attempt.snapshot

    def retry_login_verification(self, session_id):
        """Explicit retry of a confirmed QR's verification, without another scan."""
        with self._lock:
            attempt = self._attempts.get(session_id)
            if attempt is None:
                raise OnlineMusicError("expired", message="二维码会话已结束，请刷新")
            if (attempt.generation != self._generations[attempt.snapshot.source_id]
                    or attempt.snapshot.state != "error" or attempt.pending_credential is None):
                return attempt.snapshot
            if self._clock() >= attempt.snapshot.expires_at:
                attempt.pending_credential = None
                attempt.snapshot = self._snapshot_state(attempt.snapshot, "expired", "登录确认已过期，请重新扫码")
                return attempt.snapshot
            attempt.failures = 0
            attempt.retry_at = 0.
            attempt.snapshot = self._snapshot_state(attempt.snapshot, "scanned", "扫码已确认，正在重试账号核验")
        return self.poll_login(session_id)

    def invalidate_login(self, session_id: str) -> None:
        """Fence replacement registration while signalling owned cancellation."""
        with self._lock:
            attempt = self._attempts.get(session_id)
            if attempt is None or attempt.snapshot.state in {"confirmed", "cancelled", "expired"}:
                return
            source_id = attempt.snapshot.source_id
            if attempt.generation != self._generations[source_id]:
                attempt.snapshot = self._snapshot_state(attempt.snapshot, "cancelled", "登录已取消")
                attempt.pending_credential = None
                return
            self._generations[source_id] += 1
            self._cancel_source_generation(source_id)
            self._clear_recommendation_previews(source_id)
            attempt.snapshot = self._snapshot_state(attempt.snapshot, "cancelled", "登录已取消")
            attempt.pending_credential = None
            cancel = getattr(self.backend, "cancel_login_request", None)
            if callable(cancel):
                cancel(source_id)

    def invalidate_source_login(self, source_id: str) -> None:
        """Also fence an in-flight begin that has not produced a session id yet."""
        with self._lock:
            if source_id not in self._generations:
                return
            self._generations[source_id] += 1
            self._cancel_source_generation(source_id)
            self._clear_recommendation_previews(source_id)
            for attempt in self._attempts.values():
                if attempt.snapshot.source_id == source_id and attempt.snapshot.state in {"waiting", "scanned"}:
                    attempt.snapshot = self._snapshot_state(attempt.snapshot, "cancelled", "登录已取消")
                    attempt.pending_credential = None
            cancel = getattr(self.backend, "cancel_login_request", None)
            if callable(cancel):
                cancel(source_id)

    def cancel_login(self, session_id: str) -> None:
        with self._lock:
            attempt = self._attempts.get(session_id)
            if attempt is None or attempt.generation != self._generations[attempt.snapshot.source_id]:
                # A retired attempt cannot cancel the new source's live child.
                return
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
        with self._lock:
            serial = self._validation_serials[source_id]
            validation_key = (self._generations[source_id], self._validation_epochs[source_id])
            cancelled = (self._login_cancel_events[source_id], self._validation_cancel_events[source_id])
        trace = current_login_trace()
        budget = max(0., (trace.deadline - time.monotonic()) if trace else 18.)
        with login_phase("auth_queue"):
            acquired = self._validation_locks[source_id].acquire(timeout=budget)
        if not acquired:
            raise OnlineMusicError("timeout", source_id, "账号核验排队超时，请重试", stage="auth_queue")
        try:
            with self._lock:
                if validation_key != (self._generations[source_id], self._validation_epochs[source_id]):
                    raise OnlineMusicError("cancelled", source_id, "账号核验已取消")
                if (serial != self._validation_serials[source_id]
                        and self._validation_completed_keys[source_id] == validation_key):
                    return self._accounts[source_id]
            with login_request_guard(*cancelled):
                return self._validate_account(source_id, validation_key)
        finally:
            with self._lock:
                self._validation_serials[source_id] += 1
                self._validation_completed_keys[source_id] = validation_key
            self._validation_locks[source_id].release()

    def _validate_account(self, source_id, validation_key):
        credential = self._load_credential(source_id)
        if not credential:
            return AccountSnapshot(source_id)
        with self._lock:
            generation, validation_epoch = validation_key
            if (self._closed or generation != self._generations[source_id]
                    or validation_epoch != self._validation_epochs[source_id]):
                return self._accounts[source_id]
        try:
            with login_phase("saved_account_validation"):
                response = self.backend.request(source_id, "account_status", {}, credential)
            data = response.get("data")
            account = self._account(source_id, data)
            if (not isinstance(data, dict) or not isinstance(data.get("logged_in"), bool)
                    or data.get("logged_in") is True and not account.logged_in):
                raise OnlineMusicError("invalid_response", source_id, "账号核验响应不完整，请重试")
            from api.online_music_resume import read_resume
            resume = read_resume(credential, source_id, self._clock())
            if account.logged_in and resume and resume["profile"]["user_id"] != account.user_id:
                raise OnlineMusicError("account_changed", source_id, "账号身份已变化，请重新登录")
            refreshed = response.get("credential")
        except OnlineMusicError as error:
            account = AccountSnapshot(source_id, state="error", message=error.message)
            refreshed = None
            if error.code in {"auth_required", "auth", "account_changed"}:
                account = AccountSnapshot(source_id, state="expired", message="登录已失效，请重新登录")
        with self._lock:
            if (not self._closed and generation == self._generations[source_id]
                    and validation_epoch == self._validation_epochs[source_id]):
                self._accounts[source_id] = account
                if account.logged_in:
                    from api.online_music_resume import attach_profile
                    refreshed = attach_profile(refreshed if isinstance(refreshed, dict) else credential,
                                               account, credential, self._clock())
                    try:
                        self.credential_store.save(source_id, refreshed)
                        self._credentials[source_id] = refreshed
                    except (CredentialProtectionError, OSError):
                        self._accounts[source_id] = AccountSnapshot(source_id, state="error", message="本机加密保存失败，请重新登录")
                elif account.state == "error":
                    from api.online_music_resume import cached_account, read_resume
                    from dataclasses import replace
                    cached = cached_account(read_resume(credential, source_id, self._clock()), source_id)
                    if cached.user_id:
                        self._accounts[source_id] = replace(cached, state="error", message="网络暂不可用，已保留本地快照，可重试核验")
                else:
                    # An explicit signed-out/mismatched response invalidates the
                    # matching generation only. Transient failures never do.
                    self._credentials.pop(source_id, None)
                    try:
                        self.credential_store.delete(source_id)
                    except (CredentialProtectionError, OSError):
                        pass
                if not self._accounts[source_id].logged_in:
                    self._dj_voice_catalogs.pop(source_id, None)
                    self._clear_recommendation_previews(source_id)
                return self._accounts[source_id]
            return self._accounts[source_id]

    def logout(self, source_id: str) -> None:
        if source_id not in self._accounts:
            raise OnlineMusicError("unsupported", source_id, "未知音乐来源")
        with self._lock:
            self._generations[source_id] += 1
            self._cancel_source_generation(source_id)
            self._accounts[source_id] = AccountSnapshot(source_id)
            self._credentials.pop(source_id, None)
            self._dj_voice_catalogs.pop(source_id, None)
            self._clear_recommendation_previews(source_id)
            for attempt in self._attempts.values():
                if attempt.snapshot.source_id == source_id:
                    attempt.snapshot = self._snapshot_state(attempt.snapshot, "cancelled", "登录已取消")
                    attempt.pending_credential = None
            try:
                self.credential_store.delete(source_id)
            except (CredentialProtectionError, OSError):
                raise OnlineMusicError("credential", source_id, "本机登录记录删除失败") from None
        try:
            self.backend.request(source_id, "logout", {})
        except OnlineMusicError:
            pass

    def _catalog(self, source_id: str, operation: str, params: dict[str, Any], *, expected_generation: int | None = None) -> Any:
        self._check_source(source_id)
        with self._lock:
            if expected_generation is not None and expected_generation != self._generations[source_id]:
                raise OnlineMusicError("cancelled", source_id, "音乐请求已取消")
        if not self.account_status(source_id).logged_in:
            account = self.account_status(source_id, refresh=True)
            if not account.logged_in:
                raise OnlineMusicError("auth_required", source_id, account.message or "请先登录此音乐来源")
        credential = self._load_credential(source_id)
        with self._lock:
            generation = self._generations[source_id]
            if expected_generation is not None and expected_generation != generation:
                raise OnlineMusicError("cancelled", source_id, "音乐请求已取消")
        response = self.backend.request(source_id, operation, params, credential)
        with self._lock:
            if self._closed or generation != self._generations[source_id]:
                raise OnlineMusicError("cancelled", source_id, "音乐请求已取消")
            refreshed = response.get("credential")
            if isinstance(refreshed, dict):
                try:
                    from api.online_music_resume import attach_profile
                    account = self._accounts[source_id]
                    refreshed = attach_profile(refreshed, account, credential, self._clock())
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
                self._dj_voice_catalogs.pop(source_id, None)
                self._clear_recommendation_previews(source_id)
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
        album = item.get("al") or item.get("album") or item.get("albuminfo") or item.get("album_info") or {}
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
        transform = item.get("trans_param")
        transform = transform if isinstance(transform, dict) else {}
        cover = album.get("picUrl") or album.get("sizable_cover") or album.get("cover") or item.get("picUrl") or item.get("sizable_cover") or transform.get("union_cover") or item.get("cover") or item.get("img") or ""
        if source_id == "qq" and not cover and isinstance(album.get("mid"), str) and re.fullmatch(r"[A-Za-z0-9]{1,64}", album["mid"]):
            cover = f"https://y.gtimg.cn/music/photo_new/T002R500x500M000{album['mid']}.jpg"
        return OnlineSong(source_id, str(song_id), str(item.get("name") or item.get("title") or item.get("songname") or item.get("SongName") or item.get("audio_name") or item.get("filename") or item.get("FileName") or ""), artist, str(album.get("name") or item.get("album_name") or item.get("AlbumName") or ""), duration, OnlineMusicService._artwork_url(cover))

    @staticmethod
    def _playlist(source_id: str, item: dict[str, Any]) -> OnlinePlaylist:
        cover = item.get("coverImgUrl") or item.get("cover_url") or item.get("bigpicUrl") or item.get("picUrl") or item.get("picurl") or item.get("coverUrl") or item.get("sizable_cover") or item.get("pic") or item.get("img") or item.get("imgurl") or item.get("logo") or item.get("albumPicUrl") or ""
        return OnlinePlaylist(
            source_id,
            str((item.get("global_collection_id") or item.get("gid") if source_id == "kugou" else None) or item.get("id") or item.get("tid") or item.get("dissid") or item.get("dirId") or item.get("dirid") or item.get("global_collection_id") or item.get("listid") or item.get("specialid") or ""),
            str(item.get("name") or item.get("title") or item.get("dirName") or item.get("dirname") or item.get("diss_name") or item.get("dissname") or item.get("listname") or item.get("specialname") or ""),
            OnlineMusicService._artwork_url(cover),
            OnlineMusicService._nonnegative_int(item.get("trackCount") or item.get("songNum") or item.get("song_cnt") or item.get("count") or item.get("song_count") or item.get("songnum") or item.get("m_count") or item.get("music_num")),
        )

    def playlists(self, source_id: str, limit: int = 100, offset: int = 0) -> CatalogPage:
        limit, offset = self._page_bounds(limit, offset)
        account = self.account_status(source_id)
        if not account.logged_in:
            account = self.account_status(source_id, refresh=True)
        data = self._catalog(source_id, "playlists", {"user_id": account.user_id, "limit": limit, "offset": offset})
        raw = self._list(data, "playlist", "playlists", "v_playlist", "list", "lists", "info")
        total = None
        if source_id == "qq":
            total = len(raw)
            raw = raw[offset:offset + limit]
        elif source_id == "kugou":
            # get_all_list mixes playlists and favorited albums. Consume raw
            # rows for pagination, but never send an album to the playlist URL.
            payload = data.get("data") if isinstance(data.get("data"), dict) else data
            for key in ("total", "list_count", "total_count", "count"):
                value = payload.get(key)
                if isinstance(value, (int, str)) and not isinstance(value, bool) and str(value).isdigit():
                    total = int(value)
                    break
        items = tuple(self._playlist(source_id, item) for item in raw
                      if source_id != "kugou" or str(item.get("source", "")) != "2" and "musiclib_id" not in item and "musicLibId" not in item)
        items = tuple(item for item in items if item.playlist_id and item.title)
        next_offset = offset + len(raw)
        has_more = next_offset < total if total is not None else bool(data.get("more") or len(raw) == limit)
        page = CatalogPage(items, next_offset, has_more and bool(raw), total)
        if offset == 0:
            self._save_catalog_snapshot(source_id, "playlists", page, account.user_id)
        return page

    def _favorite_account(self, source_id: str, expected_user_id: str = "") -> tuple[AccountSnapshot, int]:
        """Capture the existing account authority before any favorite operation.

        These methods run on the caller's worker, like the catalog methods. The
        UI may pass the displayed account ID so an old dialog cannot write to a
        newly signed-in account. Unsupported adapters never make a fake local
        favorite or send a guessed platform write.
        """
        self._check_source(source_id)
        if source_id != "netease":
            raise OnlineMusicError("unsupported", source_id, "此来源暂不支持收藏或添加到歌单")
        if not isinstance(expected_user_id, str):
            raise OnlineMusicError("argument", source_id, "无效的账号标识")
        with self._lock:
            generation = self._generations[source_id]
        account = self.account_status(source_id)
        if not account.logged_in:
            account = self.account_status(source_id, refresh=True)
        with self._lock:
            if self._closed or generation != self._generations[source_id]:
                raise OnlineMusicError("cancelled", source_id, "账号已变化，请重试")
            if not account.logged_in:
                raise OnlineMusicError("auth_required", source_id, account.message or "请先登录此音乐来源")
            if expected_user_id and expected_user_id != account.user_id:
                raise OnlineMusicError("cancelled", source_id, "账号已变化，请重试")
        return account, generation

    @staticmethod
    def _favorite_id(source_id: str, value: str) -> str:
        if source_id != "netease":
            raise OnlineMusicError("unsupported", source_id, "此来源暂不支持收藏或添加到歌单")
        if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
            raise OnlineMusicError("argument", source_id, "无效的歌曲或歌单标识")
        return value

    def favorite_status(self, source_id: str, song_id: str, *, expected_user_id: str = "") -> bool:
        """Read confirmed service state; an unavailable status is never false."""
        song_id = self._favorite_id(source_id, song_id)
        account, generation = self._favorite_account(source_id, expected_user_id)
        data = self._catalog(source_id, "favorite_status", {"song_id": song_id, "user_id": account.user_id},
                             expected_generation=generation)
        if not isinstance(data.get("liked"), bool):
            raise OnlineMusicError("response", source_id, "收藏状态暂不可用，请重试")
        return data["liked"]

    def set_favorite(self, source_id: str, song_id: str, liked: bool, *, expected_user_id: str = "") -> bool:
        """Set an explicit target, without retrying an uncertain account write."""
        song_id = self._favorite_id(source_id, song_id)
        if not isinstance(liked, bool):
            raise OnlineMusicError("argument", source_id, "无效的收藏状态")
        account, generation = self._favorite_account(source_id, expected_user_id)
        data = self._catalog(source_id, "set_favorite", {"song_id": song_id, "user_id": account.user_id, "liked": liked},
                             expected_generation=generation)
        if data.get("ok") is not True or data.get("liked") is not liked:
            raise OnlineMusicError("response", source_id, "收藏操作未确认，请刷新后重试")
        return liked

    def writable_playlists(self, source_id: str, limit: int = 100, offset: int = 0, *, expected_user_id: str = "") -> CatalogPage:
        """Owned lists only; subscribed/recommended lists cannot be destinations."""
        account, generation = self._favorite_account(source_id, expected_user_id)
        limit, offset = self._page_bounds(limit, offset)
        limit = min(100, limit)
        data = self._catalog(source_id, "writable_playlists", {"user_id": account.user_id, "limit": limit, "offset": offset},
                             expected_generation=generation)
        if data.get("code") != 200 or not isinstance(data.get("playlist"), list):
            raise OnlineMusicError("response", source_id, "可收藏歌单暂不可用，请重试")
        raw = data["playlist"][:limit]
        items, seen = [], set()
        for row in raw:
            if not isinstance(row, dict):
                continue
            creator = row.get("creator")
            if (not isinstance(creator, dict) or str(creator.get("userId", "")) != account.user_id
                    or row.get("subscribed") is True):
                continue
            playlist = self._playlist(source_id, row)
            if (not re.fullmatch(r"[1-9][0-9]{0,19}", playlist.playlist_id)
                    or not playlist.title.strip() or playlist.playlist_id in seen):
                continue
            seen.add(playlist.playlist_id)
            items.append(playlist)
        next_offset = offset + len(raw)
        # Offset consumes original rows, including subscribed lists, so an
        # empty owned page does not strand later genuine owned playlists.
        return CatalogPage(tuple(items), next_offset, data.get("more") is True and bool(raw), None)

    def add_song_to_playlist(self, source_id: str, song_id: str, playlist_id: str, *, expected_user_id: str = "") -> None:
        """The bridge rechecks the selected playlist's actual creator before writing."""
        song_id, playlist_id = self._favorite_id(source_id, song_id), self._favorite_id(source_id, playlist_id)
        account, generation = self._favorite_account(source_id, expected_user_id)
        data = self._catalog(source_id, "add_song_to_playlist",
                             {"song_id": song_id, "playlist_id": playlist_id, "user_id": account.user_id},
                             expected_generation=generation)
        if data.get("ok") is not True:
            raise OnlineMusicError("response", source_id, "添加歌单操作未确认，请刷新后重试")

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

    def recommended_playlists(self, source_id: str, *, limit: int = 30, offset: int = 0) -> CatalogPage:
        """Read the platform's finite recommendation shelf, independently of owned lists.

        The pinned endpoints return a complete shelf rather than a server-side
        cursor. The bridge bounds it to 100 entries and slices that real shelf;
        QQ supplies editorial hot playlists, not a personalized daily mix.
        """
        self._check_source(source_id)
        if source_id not in {"netease", "qq"}:
            raise OnlineMusicError("unsupported", source_id, "此来源暂不支持推荐歌单")
        limit, offset = self._page_bounds(limit, offset)
        limit = min(100, limit)
        account = self.account_status(source_id)
        data = self._catalog(source_id, "recommended_playlists", {"limit": limit, "offset": offset})
        if not isinstance(data.get("items"), list):
            raise OnlineMusicError("response", source_id, "推荐歌单返回的数据无效")
        items = []
        seen = set()
        for raw in self._list(data, "items")[:limit]:
            item = self._playlist(source_id, raw)
            if item.playlist_id and item.title.strip() and item.playlist_id not in seen:
                seen.add(item.playlist_id)
                items.append(item)
        next_offset = min(offset + limit, max(offset, self._nonnegative_int(data.get("next_offset"))))
        total = min(100, self._nonnegative_int(data["total"])) if data.get("total") is not None else None
        has_more = data.get("has_more") is True and next_offset > offset and (total is None or next_offset < total)
        page = CatalogPage(tuple(items), next_offset, has_more, total)
        if offset == 0 and account.logged_in:
            self._save_catalog_snapshot(source_id, "recommended", page, account.user_id)
        return page

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
        return self._recommendation_songs("daily", source_id)

    def personal_fm(self, source_id: str = "netease") -> tuple[OnlineSong, ...]:
        return self._recommendation_songs("fm", source_id)

    def recommendation_artwork(self, kind: str, source_id: str = "netease") -> str:
        """Acquire real entry artwork while preserving the batch for playback.

        This worker-only path shares the same per-kind lock with actual playback.
        A simultaneous play therefore takes the prepared songs once, or supplies
        the cover from its own request; neither ordering skips a radio batch.
        """
        if kind not in {"daily", "fm"}:
            raise OnlineMusicError("argument", source_id, "无效的推荐入口")
        return self._recommendation_songs(kind, source_id, preview=True)

    def _recommendation_songs(self, kind, source_id, *, preview=False):
        if source_id != "netease":
            raise OnlineMusicError("unsupported", source_id, "此来源暂不支持此推荐入口")
        self._check_source(source_id)
        with self._recommendation_locks[kind]:
            with self._lock:
                generation = self._generations[source_id]
                now = self._clock()
                if not self._accounts[source_id].logged_in:
                    self._clear_recommendation_previews(source_id)
                cover = self._recommendation_covers.get(kind)
                if preview and cover is not None and cover[0] == generation and now < cover[1]:
                    return cover[2]
                pending = self._recommendation_previews.get(kind)
                # Daily picks may refresh after five minutes. An unread FM
                # batch deliberately survives this TTL: refetching it for a
                # picture would discard music. At most one batch per kind is
                # retained, bounded by the transport's existing 8 MiB response.
                if pending is not None and pending[0] == generation and (kind == "fm" or now < pending[1]):
                    songs = pending[2]
                    if not preview:
                        self._recommendation_previews.pop(kind, None)
                    return next((song.artwork_url for song in songs if song.artwork_url), "") if preview else songs
                self._recommendation_previews.pop(kind, None)
            operation = "daily_recommendations" if kind == "daily" else "personal_fm"
            data = self._catalog(source_id, operation, {}, expected_generation=generation)
            raw = (self._list(data, "dailySongs", "recommend", "songs") if kind == "daily" else
                   data.get("data") if isinstance(data.get("data"), list) else self._list(data, "songs"))
            songs = tuple(song for item in raw if isinstance(item, dict)
                          and (song := self._song(source_id, item)).song_id and song.title)
            artwork = next((song.artwork_url for song in songs if song.artwork_url), "")
            with self._lock:
                if self._closed or generation != self._generations[source_id]:
                    raise OnlineMusicError("cancelled", source_id, "音乐请求已取消")
                # Another recommendation may expire this account without
                # changing its login generation while this request is in
                # flight. A successful old response cannot restore that cache.
                if not self._accounts[source_id].logged_in:
                    self._clear_recommendation_previews(source_id)
                    raise OnlineMusicError("auth_required", source_id, "请先登录此音乐来源")
                expires = self._clock() + 300.0
                self._recommendation_covers[kind] = (generation, expires, artwork)
                if preview and songs:
                    self._recommendation_previews[kind] = (generation, expires, songs)
            return artwork if preview else songs

    def personal_dj_voices_cached(self, source_id: str = "netease") -> tuple[PersonalDjVoice, ...]:
        """Current account-generation snapshot, without network or account data."""
        with self._lock:
            cached = self._dj_voice_catalogs.get(source_id)
            if (self._closed or cached is None or cached[0] != self._generations.get(source_id)
                    or not self._accounts.get(source_id, AccountSnapshot(source_id)).logged_in):
                return ()
            return cached[1]

    def personal_dj_voices(self, source_id: str = "netease") -> tuple[PersonalDjVoice, ...]:
        """Fetch only the real service catalog; name-to-key mappings are not invented."""
        if source_id != "netease":
            raise OnlineMusicError("unsupported", source_id, "此来源暂不支持私人DJ音色")
        with self._lock:
            generation = self._generations[source_id]
        data = self._catalog(source_id, "personal_dj_voices", {})
        payload = data.get("data")
        rows = payload.get("timbreOptionsInfos") if isinstance(payload, dict) else None
        if data.get("code") != 200 or not isinstance(rows, list) or not 1 <= len(rows) <= 64:
            raise OnlineMusicError("response", source_id, "私人DJ音色目录暂不可用，请重试")
        voices, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise OnlineMusicError("response", source_id, "私人DJ音色目录无效")
            key, name, desc, default = (row.get("timbreKey"), row.get("name"), row.get("desc"), row.get("defaultAudio"))
            if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key) or key in seen
                    or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80
                    or not isinstance(desc, str) or len(desc) > 512 or not isinstance(default, bool)
                    or any(ord(c) < 32 for c in name + desc)):
                raise OnlineMusicError("response", source_id, "私人DJ音色目录无效")
            # Mode-bound celebrity voices do not belong to default personal FM.
            binding = row.get("bindingMode")
            if binding not in (None, ""):
                continue
            seen.add(key)
            preview, duration = None, 0.0
            if row.get("playUrl") not in (None, ""):
                url, duration_value = self._http_url(row.get("playUrl")), row.get("duration")
                if (not url or any(ord(c) < 32 for c in url) or isinstance(duration_value, bool)
                        or not isinstance(duration_value, (int, float)) or not 0 < duration_value <= 120
                        or not math.isfinite(duration_value)):
                    raise OnlineMusicError("response", source_id, "私人DJ试听音频无效")
                duration = float(duration_value)
                preview = StreamInfo(source_id, "dj-preview:" + key, url, True, quality="dj_preview")
            voices.append(PersonalDjVoice(key, name.strip(), desc, default, preview, duration))
        if not voices:
            raise OnlineMusicError("response", source_id, "私人DJ音色目录暂不可用，请重试")
        result = tuple(voices)
        with self._lock:
            if self._closed or generation != self._generations[source_id]:
                raise OnlineMusicError("cancelled", source_id, "音乐请求已取消")
            cached = self._dj_voice_catalogs.get(source_id)
            if cached is not None and cached[0] == generation:
                previous = {voice.timbre_key: voice for voice in cached[1]}
                # A same-account refresh must not impersonate account
                # replacement to an active audition's identity fence.
                # Dataclass equality includes every retained field and the
                # full StreamInfo (including its hidden URL).
                result = tuple(previous[voice.timbre_key]
                    if voice.timbre_key in previous and previous[voice.timbre_key] == voice else voice
                    for voice in result)
            self._dj_voice_catalogs[source_id] = (generation, result)
        return result

    def personal_dj(self, source_id: str = "netease", timbre_key: str = "") -> tuple[OnlineSong, ...]:
        """Preserve the actual aidj resource order, including genuine speech."""
        if source_id != "netease":
            raise OnlineMusicError("unsupported", source_id, "此来源暂不支持私人DJ")
        if not isinstance(timbre_key, str) or timbre_key and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", timbre_key):
            raise OnlineMusicError("argument", source_id, "无效的私人DJ音色")
        with self._lock:
            generation = self._generations[source_id]
        voices = self.personal_dj_voices_cached(source_id) or self.personal_dj_voices(source_id)
        selected = next((voice for voice in voices if voice.timbre_key == timbre_key), None) if timbre_key else next((voice for voice in voices if voice.is_default), voices[0])
        if selected is None:
            raise OnlineMusicError("argument", source_id, "此私人DJ音色当前不可用")
        return self._personal_dj_batch(self._catalog(source_id, "personal_dj", {"timbre_key": selected.timbre_key},
            expected_generation=generation), selected.timbre_key)

    def _personal_dj_batch(self, data: Any, expected_timbre_key: str = "") -> tuple[OnlineSong, ...]:
        # Schema checked against an authenticated response from pinned 4.41.1.
        # Reject incomplete batches rather than silently lose introductions.
        def invalid():
            raise OnlineMusicError("response", "netease", "私人DJ未返回完整的语音与歌曲队列，请重试")
        if not isinstance(data, dict) or data.get("code") != 200:
            invalid()
        payload = data.get("data")
        resources = payload.get("aiDjResources") if isinstance(payload, dict) else None
        if not isinstance(resources, list) or not 1 <= len(resources) <= 64:
            invalid()
        songs = {}
        for resource in resources:
            if not isinstance(resource, dict) or not isinstance(resource.get("value"), dict):
                invalid()
            if resource.get("type") == "song":
                value = resource["value"]
                raw = value.get("songData")
                identity = str(value.get("songId", ""))
                if not isinstance(raw, dict) or not re.fullmatch(r"[0-9]{1,24}", identity):
                    invalid()
                song = self._song("netease", raw)
                if song.song_id != identity or not song.title:
                    invalid()
                songs[identity] = song
        result = []
        voices = 0
        for resource_position, resource in enumerate(resources):
            kind, value = resource.get("type"), resource["value"]
            if kind == "song":
                result.append(songs[str(value["songId"])])
            elif kind == "audio":
                audios = value.get("audioList")
                if not isinstance(audios, list) or not 1 <= len(audios) <= 8:
                    invalid()
                for audio_position, audio in enumerate(audios):
                    if not isinstance(audio, dict):
                        invalid()
                    if expected_timbre_key and audio.get("timbreKey") != expected_timbre_key:
                        invalid()
                    url = self._http_url(audio.get("audioUrl"))
                    identity = audio.get("audioId")
                    duration, ttl = audio.get("duration"), audio.get("validTime")
                    if (not url or any(ord(c) < 32 for c in url)
                            or not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity)
                            or isinstance(duration, bool) or not isinstance(duration, (int, float))
                            or not 0 < duration <= 1800 or not math.isfinite(duration)
                            or isinstance(ttl, bool) or not isinstance(ttl, (int, float))
                            or not 0 < ttl <= 86400 or not math.isfinite(ttl)):
                        invalid()
                    related = audio.get("introductionRelatedSongIds")
                    following = next((following for following in resources[resource_position + 1:]
                        if following.get("type") == "song"), None)
                    following_id = str(following["value"]["songId"]) if following is not None else ""
                    # An observed opening prefix has no song association. It is
                    # not an introduction: ignore only this bounded first-item
                    # shape, before a real same-list introduction to the first
                    # song. Every audio still receives schema/key validation.
                    if (resource_position == 0 and audio_position == 0
                            and (related is None or related == []) and following_id
                            and any(isinstance(later, dict)
                                and isinstance(later.get("introductionRelatedSongIds"), list)
                                and following_id in {str(key) for key in later["introductionRelatedSongIds"][:16]}
                                for later in audios[1:])):
                        continue
                    if not isinstance(related, list):
                        invalid()
                    if following_id not in {str(key) for key in related[:16]}:
                        invalid()
                    associated = songs[following_id]
                    label = audio.get("textType")
                    label = label if isinstance(label, str) and len(label) <= 24 and not any(ord(c) < 32 for c in label) else "语音"
                    stream = StreamInfo("netease", "dj:" + identity, url, True, quality="dj_voice", expires_at=self._clock() + ttl)
                    result.append(OnlineSong("netease", stream.song_id, "私人DJ · " + (label or "语音"),
                        "私人DJ", associated.title if associated else "", float(duration),
                        associated.artwork_url if associated else "", "dj_voice", stream, following_id))
                    voices += 1
            else:
                invalid()
            if len(result) > 96:
                invalid()
        if not songs or not voices:
            invalid()
        return tuple(result)

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
        if source_id not in {"netease", "qq"}:
            raise OnlineMusicError("unsupported", source_id, "此来源暂不支持逐字歌词获取")
        data = self._catalog(source_id, "lyrics", {"song_id": str(song_id)})
        if source_id == "qq":
            from api.qrc import decrypt_qrc
            def plain(key):
                value = data.get(key)
                return value[:2 * 1024 * 1024] if isinstance(value, str) else ""
            def decoded(key):
                try:
                    return decrypt_qrc(data.get(key)) if data.get(key) else ""
                except ValueError:
                    return ""
            return LyricPayload(source_id, str(song_id), plain("raw_lrc"), "",
                decoded("trans") or plain("translation"), decoded("roma"), "qq-account", decoded("lyric"))
        def text(key: str) -> str:
            value = data.get(key)
            result = value.get("lyric", "") if isinstance(value, dict) else ""
            return result[:2 * 1024 * 1024] if isinstance(result, str) else ""
        raw = {key: data[key] for key in (
            "lrc", "yrc", "klyric", "tlyric", "ytlrc", "romalrc", "yromalrc", "romaLrc", "romalyrics", "language", "lyric")
            if key in data}
        return LyricPayload(source_id, str(song_id), text("lrc"), text("yrc"), text("ytlrc") or text("tlyric"),
            text("yromalrc") or text("romalrc"), "netease-account", raw_netease=json.dumps(raw, ensure_ascii=False, separators=(",", ":")))

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for source_id in self._generations:
                self._generations[source_id] += 1
                self._login_cancel_events[source_id].set()
                self._validation_cancel_events[source_id].set()
            for attempt in self._attempts.values():
                if attempt.snapshot.state in {"waiting", "scanned"}:
                    attempt.snapshot = self._snapshot_state(attempt.snapshot, "cancelled", "登录已取消")
                attempt.pending_credential = None
            self._credentials.clear()
            self._dj_voice_catalogs.clear()
            self._recommendation_previews.clear()
            self._recommendation_covers.clear()
        self.backend.close()


def make_online_music_service() -> OnlineMusicService:
    return OnlineMusicService()
