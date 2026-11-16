"""OnlineMusicService backend for Apple Music. Other sources stay on the Node bridge."""
from __future__ import annotations

import threading

from api.apple_music_catalog import playlists_from_payload, service_song, songs_from_payload
from api.apple_music_companion import MemoryAppleMusicCompanion, ProcessAppleMusicCompanion
from api.apple_music_log import apple_log
from api.online_music import OnlineMusicError


class AppleMusicBackend:
    def __init__(self, companion=None):
        self.companion = companion if companion is not None else ProcessAppleMusicCompanion()
        self._window_callback = None
        setter = getattr(self.companion, "set_event_handler", None)
        if callable(setter):
            setter(self._on_companion_event)

    def watch_login_window(self, callback) -> None:
        self._window_callback = callback

    def notify_login_window_closed(self) -> None:
        self._on_companion_event("login-window-closed")

    def _on_companion_event(self, name: str) -> None:
        if name != "login-window-closed":
            return
        callback = self._window_callback
        if callback is not None:
            callback()

    def available(self, source_id: str) -> tuple[bool, str]:
        if source_id != "apple_music":
            return False, "未知音乐来源"
        return self.companion.available()

    def close(self) -> None:
        self.companion.close()

    def prepare_login(self, source_id: str) -> bool:
        return source_id == "apple_music" and self.available(source_id)[0]

    def cancel_login_request(self, source_id: str) -> None:
        """Ask the page to hide login without waiting. Callers may be the UI thread."""
        if source_id != "apple_music":
            return
        companion = self.companion

        def hide():
            try:
                companion.request("login_window", {"show": False}, timeout=20)
            except OnlineMusicError:
                pass

        threading.Thread(target=hide, name="GluSpot-apple-hide-login", daemon=True).start()

    def request(self, source_id: str, operation: str, params: dict | None = None, credential: dict | None = None) -> dict:
        if source_id != "apple_music":
            raise OnlineMusicError("unsupported", source_id, "未知音乐来源")
        params = dict(params or {})
        try:
            data = self._request(source_id, operation, params)
        except OnlineMusicError as exc:
            _log_failure(operation, params, exc.code)
            raise
        _log_success(operation, params, data)
        return data

    def _request(self, source_id: str, operation: str, params: dict) -> dict:
        if operation == "begin_login":
            # authorize() waits for the person. No ordinary request timeout,
            # and a slow login must not stop the companion.
            self.companion.request("login_window", {"show": True}, timeout=None)
            return {"data": {"key": "apple-window", "ttl": 300}}
        if operation == "poll_login":
            account = self._account()
            if account.get("logged_in") is True and account.get("user_id"):
                return {"data": {"state": "confirmed"}, "credential": _profile(account)}
            return {"data": {"state": "waiting"}}
        if operation == "cancel_login":
            self.cancel_login_request(source_id)
            return {"data": {"state": "cancelled"}}
        if operation == "logout":
            self.companion.request("sign_out")
            return {"data": {}}
        if operation == "account_status":
            account = self._account()
            logged_in = account.get("logged_in") is True and bool(account.get("user_id"))
            data = {"logged_in": logged_in, "user_id": account.get("user_id") or "", "nickname": account.get("nickname") or "Apple Music"}
            return {"data": data, "credential": _profile(account) if logged_in else {}}
        if operation == "playlists":
            body = self.companion.request("library_playlists", {"limit": params.get("limit", 100), "offset": params.get("offset", 0)})
            rows = list(playlists_from_payload(_library_payload(body)))
            return {"data": {"playlist": rows, "more": body.get("more") is True}}
        if operation == "playlist_tracks":
            body = self.companion.request("library_tracks", {"playlist_id": str(params.get("playlist_id") or ""), "limit": params.get("limit", 100), "offset": params.get("offset", 0)})
            songs = [service_song(song) for song in songs_from_payload(_library_payload(body))]
            filled = sum(1 for song in songs if song.get("picUrl"))
            lookup = body.get("artwork_lookup") if isinstance(body.get("artwork_lookup"), str) else ""
            reason = body.get("artwork_reason") if isinstance(body.get("artwork_reason"), str) else ""
            if not songs:
                apple_log("artwork", stage="tracks", result="miss", count=0, reason=reason or "empty-page")
            elif lookup == "error":
                apple_log("artwork", stage="catalog", result="error", count=filled, reason=reason or "request-failed")
            elif lookup == "miss" or filled < len(songs):
                apple_log("artwork", stage="catalog" if lookup == "miss" else "tracks", result="miss", count=filled, reason=reason or "no-art")
            else:
                apple_log("artwork", stage="tracks", result="ok", count=filled, reason=reason or "filled")
            return {"data": {"tracks": songs, "more": body.get("more") is True}}
        if operation == "song_lyrics":
            body = self.companion.request("song_lyrics", {
                "song_id": str(params.get("song_id") or ""),
                "catalog_id": str(params.get("catalog_id") or ""),
            })
            ttml = body.get("ttml") if isinstance(body, dict) and isinstance(body.get("ttml"), str) else ""
            step = body.get("step") if isinstance(body, dict) and body.get("step") in {"ok", "miss", "error"} else ""
            reason = body.get("reason") if isinstance(body, dict) and isinstance(body.get("reason"), str) else ""
            if reason in {"catalog-id", "include"}:
                apple_log("lyrics", stage="resolve", result="ok", reason=reason)
            elif reason:
                apple_log("lyrics", stage="resolve", result="miss" if step != "error" else "error", reason=reason)
            if len(ttml) > 700000 or "<tt" not in ttml:
                apple_log("lyrics", stage="ttml", result="error" if step == "error" else "miss", reason="catalog" if step == "error" else (reason or "empty"))
                ttml = ""
            else:
                apple_log("lyrics", stage="ttml", result="ok", reason=reason or "ttml")
            return {"data": {"ttml": ttml}}
        if operation == "search":
            args = {"query": params.get("query", ""), "limit": params.get("limit", 30), "offset": params.get("offset", 0)}
            if params.get("kind") == "playlists":
                # Song rows must never be read back as playlists.
                body = self.companion.request("find_playlists", args)
                rows = list(playlists_from_payload(_library_payload(body)))
                return {"data": {"items": rows, "has_more": False, "next_offset": args["offset"] + len(rows)}}
            body = self.companion.request("find_songs", args)
            songs = [service_song(song) for song in songs_from_payload(_library_payload(body))]
            return {"data": {"items": songs, "has_more": False, "next_offset": params.get("offset", 0) + len(songs)}}
        if operation == "recommended_playlists":
            # 为你精选 reads the account's own recommendations; 推荐歌单 the
            # storefront's top playlists. Both are one finite shelf.
            shelf = "for_you_playlists" if params.get("shelf") == "for_you" else "chart_playlists"
            body = self.companion.request(shelf, {})
            rows = list(playlists_from_payload(_library_payload(body)))
            offset = max(0, int(params.get("offset", 0) or 0))
            limit = max(1, int(params.get("limit", 30) or 30))
            window = rows[offset:offset + limit]
            return {"data": {"items": window, "has_more": offset + len(window) < len(rows),
                             "next_offset": offset + len(window), "total": len(rows)}}
        if operation == "companion_transport":
            action = params.get("action")
            body = self.companion.request(
                "transport",
                {"action": action, "song_id": params.get("song_id", ""), "seconds": params.get("seconds", 0)},
                timeout=20,
            )
            return {"data": body}
        if operation == "create_playlist":
            body = self.companion.request("make_playlist", {"name": params.get("name", "")})
            return {"data": body}
        if operation == "append_tracks":
            body = self.companion.request("append_tracks", {"playlist_id": params.get("playlist_id", ""), "song_ids": list(params.get("song_ids") or [])})
            return {"data": body}
        raise OnlineMusicError("unsupported", source_id, "Apple Music 还没有这个操作")

    def _account(self) -> dict:
        # The page retries until MusicKit is ready, then posts the account.
        # This wait is the same 20 second cap as every other query.
        return self.companion.request("account", timeout=20)


def _library_payload(body: dict):
    """Companion replies may already be resource rows, or the page's raw document."""
    if not isinstance(body, dict):
        return body
    if "payload" in body:
        return body.get("payload")
    return body.get("items", body)


def _profile(account: dict) -> dict:
    """A local profile marker. The WebView keeps the real session."""
    return {"profile": "webview", "user_id": str(account.get("user_id") or "")[:128]}


def _safe_result(code) -> str:
    text = code if isinstance(code, str) else ""
    return text if text and len(text) <= 32 and text.replace("_", "").isalnum() else "error"


def _log_failure(operation: str, params: dict, code: str) -> None:
    if operation == "begin_login":
        apple_log("login", stage="begin", result=_safe_result(code))
    elif operation == "logout":
        apple_log("login", stage="sign_out", result=_safe_result(code))
    elif operation == "playlists":
        apple_log("playlists", stage="list", result=_safe_result(code))
    elif operation == "playlist_tracks":
        apple_log("playlists", stage="tracks", result=_safe_result(code))
    elif operation == "song_lyrics":
        apple_log("lyrics", stage="request", result=_safe_result(code))
    elif operation == "search":
        apple_log("search", result=_safe_result(code))
    elif operation == "recommended_playlists":
        apple_log("playlists", stage="shelf", result=_safe_result(code))
    elif operation == "companion_transport" and str(params.get("action") or "") in {"play", "resume"}:
        apple_log("play", action=str(params.get("action")), result=_safe_result(code))
    elif operation == "create_playlist":
        apple_log("write", stage="create", result=_safe_result(code))
    elif operation == "append_tracks":
        apple_log("write", stage="append", result=_safe_result(code))


def _count(value) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _log_success(operation: str, params: dict, data: dict) -> None:
    body = data.get("data") if isinstance(data, dict) else None
    if operation == "begin_login":
        apple_log("login", stage="begin", result="ok")
    elif operation == "poll_login" and isinstance(body, dict) and body.get("state") == "confirmed":
        apple_log("login", stage="confirmed", result="ok")
    elif operation == "logout":
        apple_log("login", stage="sign_out", result="ok")
    elif operation == "playlists":
        apple_log("playlists", stage="list", result="ok", count=_count((body or {}).get("playlist") if isinstance(body, dict) else ()))
    elif operation == "playlist_tracks":
        apple_log("playlists", stage="tracks", result="ok", count=_count((body or {}).get("tracks") if isinstance(body, dict) else ()))
    elif operation == "search":
        apple_log("search", result="ok", count=_count((body or {}).get("items") if isinstance(body, dict) else ()))
    elif operation == "recommended_playlists":
        apple_log("playlists", stage="shelf", result="ok", count=_count((body or {}).get("items") if isinstance(body, dict) else ()))
    elif operation == "companion_transport" and str(params.get("action") or "") in {"play", "resume"}:
        preview = isinstance(body, dict) and body.get("preview") is True
        apple_log("play", action=str(params.get("action")), result="ok", preview="yes" if preview else "no")
    elif operation == "create_playlist":
        apple_log("write", stage="create", result="ok", count=1)
    elif operation == "append_tracks":
        apple_log("write", stage="append", result="ok", count=_count(params.get("song_ids")))
