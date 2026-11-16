"""Talk to the Apple Music companion. The real player is a Windows program.

This process only exchanges one JSON object per line. Account cookies and
Apple tokens stay in the companion profile; they are not fields in the protocol.
"""
from __future__ import annotations

import json
import subprocess
import time
import sys
import threading
import uuid
from pathlib import Path

from api.apple_music_log import apple_note, apple_trace
from api.online_music import OnlineMusicError


PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 1024 * 1024
TRANSPORT_ACTIONS = frozenset({"play", "resume", "pause", "seek", "status"})
OPERATIONS = frozenset({
    "account", "login_window", "sign_out", "library_playlists", "library_tracks",
    "find_songs", "now_playing", "transport", "make_playlist", "append_tracks",
    "song_lyrics", "for_you_playlists", "chart_playlists", "find_playlists",
})


def _elapsed_ms(started: float) -> int:
    return max(0, min(600000, int((time.monotonic() - started) * 1000)))


def _login_wait(operation: str, args: dict | None) -> bool:
    """authorize() waits for the person. That wait is not a normal request."""
    return operation == "login_window" and dict(args or {}).get("show", True) is not False


# One file for a source checkout, one file beside a packaged GluSpot.exe.
# Nothing walks a list of directories.
_DEV_COMPANION = (
    Path(__file__).resolve().parent.parent
    / "native" / "apple_music_companion" / "bin" / "Release" / "net48"
    / "GluSpotAppleMusicCompanion.exe"
)


def companion_executable() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "GluSpotAppleMusicCompanion.exe"
    return _DEV_COMPANION


def availability() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "当前系统不支持启动 Apple Music 伴随程序，它只在 Windows 11 上运行"
    if not companion_executable().is_file():
        return False, "Apple Music 伴随程序未安装。需要 Windows 11、WebView2 和 .NET Framework 4.8"
    return True, ""


class MemoryAppleMusicCompanion:
    """In-process stand-in. Tests and the service share this request shape."""

    def __init__(self):
        self.authorized = False
        self.user_id = ""
        self.nickname = "Apple Music"
        self.storefront = "us"
        self.visible = False
        self.playlists = []
        self.tracks = {}
        self.responses = {}
        self.catalog = []
        self.lyrics = {}
        self.playback = {"playing": False, "position": 0.0, "duration": 0.0, "song_id": "", "preview": False}
        self.calls = []
        self.closed = False
        self.fail = ""

    def available(self) -> tuple[bool, str]:
        return True, ""

    def close(self) -> None:
        self.closed = True
        self.visible = False

    def request(self, operation: str, args: dict | None = None, timeout: float = 20.0) -> dict:
        del timeout
        if self.closed:
            raise OnlineMusicError("closed", "apple_music", "Apple Music 伴随程序已关闭")
        if operation not in OPERATIONS:
            raise OnlineMusicError("unsupported", "apple_music", "不支持的 Apple Music 操作")
        args = dict(args or {})
        self.calls.append((operation, args))
        if self.fail:
            raise OnlineMusicError(self.fail, "apple_music", "Apple Music 请求失败")
        if operation == "login_window":
            self.visible = args.get("show", True) is not False
            return {"shown": self.visible}
        if operation == "sign_out":
            self.authorized = False
            self.user_id = ""
            self.visible = False
            self.playback = {"playing": False, "position": 0.0, "duration": 0.0, "song_id": "", "preview": False}
            return {"ok": True}
        if operation == "account":
            return {
                "logged_in": self.authorized,
                "user_id": self.user_id if self.authorized else "",
                "nickname": self.nickname if self.authorized else "",
                "storefront": self.storefront if self.authorized else "",
            }
        if not self.authorized and operation != "now_playing":
            raise OnlineMusicError("auth_required", "apple_music", "请先登录 Apple Music")
        scripted = self.responses.get(operation)
        if scripted is not None:
            return scripted
        if operation == "library_playlists":
            return {"items": list(self.playlists), "more": False}
        if operation == "library_tracks":
            return {"items": list(self.tracks.get(str(args.get("playlist_id")), [])), "more": False}
        if operation == "song_lyrics":
            key = str(args.get("catalog_id") or args.get("song_id") or "")
            return {"ttml": str(self.lyrics.get(key) or ""), "kind": "syllable" if self.lyrics.get(key) else ""}
        if operation == "find_songs":
            query = str(args.get("query") or "").casefold().split()
            found = []
            for song in self.catalog:
                blob = json.dumps(song, ensure_ascii=False).casefold()
                if query and all(part in blob for part in query):
                    found.append(song)
            return {"items": found[: int(args.get("limit") or 8)]}
        if operation in {"for_you_playlists", "chart_playlists", "find_playlists"}:
            return {"items": []}
        if operation == "now_playing":
            return dict(self.playback)
        if operation == "transport":
            return self._transport(args)
        if operation == "make_playlist":
            name = str(args.get("name") or "").strip()
            if not name:
                raise OnlineMusicError("argument", "apple_music", "歌单名不能为空")
            playlist_id = "p." + uuid.uuid4().hex[:12]
            self.playlists.append({"id": playlist_id, "attributes": {"name": name, "trackCount": 0}})
            self.tracks[playlist_id] = []
            return {"ok": True, "playlist_id": playlist_id, "name": name}
        if operation == "append_tracks":
            playlist_id = str(args.get("playlist_id") or "")
            if playlist_id not in self.tracks:
                raise OnlineMusicError("response", "apple_music", "没有这个 Apple Music 歌单")
            for song_id in args.get("song_ids") or []:
                self.tracks[playlist_id].append({"id": str(song_id), "attributes": {"name": str(song_id), "artistName": "", "durationInMillis": 0}})
            return {"ok": True, "added": len(args.get("song_ids") or [])}
        raise OnlineMusicError("unsupported", "apple_music", "不支持的 Apple Music 操作")

    def _transport(self, args: dict) -> dict:
        action = args.get("action")
        if action not in TRANSPORT_ACTIONS:
            raise OnlineMusicError("argument", "apple_music", "只接受播放、暂停和跳转，不会改播放模式")
        if any(key in args for key in ("shuffle", "repeat", "playback_mode")):
            raise OnlineMusicError("argument", "apple_music", "不会修改 Apple Music 的播放模式")
        if action == "status":
            return dict(self.playback)
        if action == "pause":
            self.playback["playing"] = False
            return dict(self.playback)
        if action == "seek":
            self.playback["position"] = max(0.0, float(args.get("seconds") or 0))
            return dict(self.playback)
        song_id = str(args.get("song_id") or self.playback.get("song_id") or "")
        if action == "play" and not song_id:
            raise OnlineMusicError("argument", "apple_music", "没有要播放的歌")
        self.playback.update(playing=True, song_id=song_id, position=float(args.get("seconds") or 0), duration=float(args.get("duration") or self.playback.get("duration") or 0), preview=False)
        return dict(self.playback)


class ProcessAppleMusicCompanion:
    """Stdio client for GluSpotAppleMusicCompanion.exe. One request at a time."""

    def __init__(self, executable: Path | None = None, timeout: float = 20.0):
        self.executable = Path(executable) if executable else companion_executable()
        self.timeout = timeout
        self._lock = threading.RLock()
        self._wait_lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._waiters: dict[str, dict] = {}
        self._event_handler = None
        self._closed = False

    def set_event_handler(self, handler) -> None:
        self._event_handler = handler

    def available(self) -> tuple[bool, str]:
        if self.executable and self.executable.is_file():
            return True, ""
        return availability()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            process, self._process = self._process, None
        if process is None:
            return
        # Closing stdin is the parent's exit. The companion leaves by itself.
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=1)
            except Exception:
                pass
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def request(self, operation: str, args: dict | None = None, timeout: float | None = None) -> dict:
        if operation not in OPERATIONS:
            raise OnlineMusicError("unsupported", "apple_music", "不支持的 Apple Music 操作")
        if operation == "transport":
            action = (args or {}).get("action")
            if action not in TRANSPORT_ACTIONS or any(key in (args or {}) for key in ("shuffle", "repeat", "playback_mode")):
                raise OnlineMusicError("argument", "apple_music", "只接受播放、暂停和跳转，不会改播放模式")
        payload = {"v": PROTOCOL_VERSION, "id": uuid.uuid4().hex[:16], "op": operation, "args": dict(args or {})}
        line = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(line) > MAX_LINE_BYTES:
            raise OnlineMusicError("argument", "apple_music", "请求太大")
        login = _login_wait(operation, args)
        waiter = {"event": threading.Event(), "line": b"", "error": ""}
        started = time.monotonic()
        apple_trace("send", payload["id"], operation)
        with self._lock:
            if self._closed:
                raise OnlineMusicError("closed", "apple_music", "Apple Music 伴随程序已关闭")
            process = self._ensure()
            with self._wait_lock:
                self._waiters[payload["id"]] = waiter
            try:
                process.stdin.write(line)
                process.stdin.flush()
            except (OSError, ValueError) as exc:
                with self._wait_lock:
                    self._waiters.pop(payload["id"], None)
                self._retire(process)
                raise OnlineMusicError("runtime", "apple_music", "Apple Music 伴随程序中断") from exc
        # The wait stays outside the lock so a hide request can be written
        # while an account read is still outstanding. Replies are matched by id.
        if login and timeout is None:
            waiter["event"].wait()
            timed_out = False
        else:
            limit = self.timeout if timeout is None else timeout
            timed_out = not waiter["event"].wait(limit if limit and limit > 0 else self.timeout)
        if timed_out:
            with self._wait_lock:
                self._waiters.pop(payload["id"], None)
            apple_trace("reply", payload["id"], operation, result="timeout", ms=_elapsed_ms(started))
            # The companion reloads the page on its own 20 second clock.
            # Killing it here would throw that reload away.
            raise OnlineMusicError("timeout", "apple_music", "Apple Music 伴随程序超时")
        raw = waiter["line"]
        failed = bool(waiter["error"])
        if failed or not raw or len(raw) > MAX_LINE_BYTES:
            apple_trace("reply", payload["id"], operation, result="runtime", ms=_elapsed_ms(started))
            self._retire(process)
            raise OnlineMusicError("runtime", "apple_music", "Apple Music 伴随程序没有返回结果")
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise OnlineMusicError("response", "apple_music", "Apple Music 返回的数据无效") from exc
        if response.get("id") != payload["id"]:
            raise OnlineMusicError("response", "apple_music", "Apple Music 返回的数据对不上")
        if response.get("ok") is not True:
            code = response.get("error") if response.get("error") in {"auth_required", "timeout", "runtime", "unsupported", "argument"} else "response"
            apple_trace("reply", payload["id"], operation, result=code, ms=_elapsed_ms(started))
            raise OnlineMusicError(code, "apple_music", "Apple Music 请求失败")
        body = response.get("body")
        if not isinstance(body, dict):
            apple_trace("reply", payload["id"], operation, result="response", ms=_elapsed_ms(started))
            raise OnlineMusicError("response", "apple_music", "Apple Music 返回的数据无效")
        apple_trace("reply", payload["id"], operation, result="ok", ms=_elapsed_ms(started))
        return body

    def _ensure(self) -> subprocess.Popen:
        if self._process is not None and self._process.poll() is None:
            return self._process
        if self.executable is None or not self.executable.is_file():
            ready, reason = availability()
            raise OnlineMusicError("runtime", "apple_music", reason or "Apple Music 伴随程序未安装")
        try:
            self._process = subprocess.Popen(
                [str(self.executable)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise OnlineMusicError("runtime", "apple_music", "Apple Music 伴随程序没有启动") from exc
        threading.Thread(target=self._read_stdout, args=(self._process,), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(self._process,), daemon=True).start()
        return self._process

    def _read_stdout(self, process: subprocess.Popen) -> None:
        stream = process.stdout
        while stream is not None:
            try:
                line = stream.readline(MAX_LINE_BYTES + 1)
            except Exception:
                line = b""
            if not line:
                self._fail_waiters()
                return
            self._deliver(line)

    def _deliver(self, raw: bytes) -> None:
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            return
        request_id = response.get("id")
        if not isinstance(request_id, str) or not request_id:
            return
        with self._wait_lock:
            waiter = self._waiters.pop(request_id, None)
        if waiter is None:
            return
        waiter["line"] = raw
        waiter["event"].set()

    def _fail_waiters(self) -> None:
        with self._wait_lock:
            waiters = list(self._waiters.values())
            self._waiters.clear()
        for waiter in waiters:
            waiter["error"] = "closed"
            waiter["event"].set()

    def _read_stderr(self, process: subprocess.Popen) -> None:
        stream = process.stderr
        while stream is not None:
            try:
                raw = stream.readline(MAX_LINE_BYTES + 1)
            except Exception:
                return
            if not raw:
                return
            text = raw.decode("utf-8", "replace")
            try:
                apple_note(text)
            except Exception:
                pass
            if "event=login-window-closed" in text:
                handler = self._event_handler
                if handler is not None:
                    try:
                        handler("login-window-closed")
                    except Exception:
                        pass

    def _retire(self, process: subprocess.Popen) -> None:
        with self._lock:
            if self._process is process:
                self._process = None
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except Exception:
                process.kill()
