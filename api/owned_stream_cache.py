"""Session-only, bounded and verified audio downloads for owned Qt playback.

Call prepare on a worker. Signed addresses stay in memory. An incomplete HTTP
body is never published to a decoder. This module does not obtain credentials,
replace sources, bypass trial limits or infer playback time from download bytes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from api.online_music import StreamInfo


class StreamDownloadError(RuntimeError):
    def __init__(self, code):
        super().__init__("Owned audio preparation failed")
        self.code = code


@dataclass(frozen=True)
class PreparedAudio:
    lease: str = field(repr=False)
    path: Path = field(repr=False)
    stream: StreamInfo = field(repr=False)
    byte_count: int
    request_count: int
    resumed: bool = False


@dataclass
class _Entry:
    path: Path
    byte_count: int
    request_count: int
    resumed: bool
    used_at: float
    pins: int = 0


def _safe_url(url):
    if not isinstance(url, str) or not url or len(url) > 16384 or any(ord(c) < 32 or ord(c) == 127 for c in url):
        return False
    try:
        parsed = urlsplit(url)
        return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            and not parsed.username and not parsed.password and not parsed.fragment
            and parsed.port != 0)
    except ValueError:
        return False


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        if not _safe_url(url):
            raise StreamDownloadError("invalid_response")
        return super().redirect_request(request, response, code, message, headers, url)


class OwnedStreamCache:
    MAX_FILE_BYTES = 64 * 1024 * 1024
    MAX_CACHE_BYTES = 192 * 1024 * 1024
    MAX_CACHE_FILES = 4
    MAX_ACTIVE_DOWNLOADS = 2
    MAX_REQUESTS = 3
    DOWNLOAD_SECONDS = 45.0
    IO_TIMEOUT_SECONDS = 2.0
    OVERLAP_BYTES = 64 * 1024
    _SESSION_NAME = re.compile(r"audio-[0-9a-f]{32}\Z")
    _FILE_NAME = re.compile(r"[0-9a-f]{32}\.(?:part|audio)\Z")

    def __init__(self, root=None, *, opener=None, clock=time.monotonic, wall_clock=time.time):
        base = Path(root) if root is not None else Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "GluSpot" / "audio-cache"
        self.root = base.absolute()
        self._session = self.root / ("audio-" + secrets.token_hex(16))
        self._opener = opener or build_opener(_SafeRedirect())
        self._clock, self._wall_clock = clock, wall_clock
        self._lock = threading.RLock()
        self._entries, self._leases = {}, {}
        self._orphans = {}
        self._active = 0
        self._closed = False
        self._session_lock = None

    @staticmethod
    def _safe_directory(path):
        for parent in (path, *path.parents):
            if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
                raise StreamDownloadError("storage")

    @staticmethod
    def _file_lock(handle, unlock=False):
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _delete_session(self, folder):
        # No recursive or arbitrary path deletion: only our flat random files.
        self._safe_directory(folder)
        if folder.parent != self.root or not self._SESSION_NAME.fullmatch(folder.name):
            return
        children = list(folder.iterdir())
        if any(child.is_symlink() or not child.is_file()
                or (child.name != "owner.lock" and not self._FILE_NAME.fullmatch(child.name)) for child in children):
            return
        # Keep ownership evidence if a data-file deletion fails midway.
        for child in sorted(children, key=lambda child: child.name == "owner.lock"):
            child.unlink(missing_ok=True)
        folder.rmdir()

    def _ensure_session(self):
        if self._session_lock is not None:
            return
        self._safe_directory(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._safe_directory(self.root)
        for folder in self.root.iterdir():
            if not self._SESSION_NAME.fullmatch(folder.name) or folder == self._session:
                continue
            try:
                self._safe_directory(folder)
                lock_path = folder / "owner.lock"
                if lock_path.is_symlink() or not lock_path.is_file():
                    continue
                with lock_path.open("r+b") as handle:
                    self._file_lock(handle)
                    self._file_lock(handle, unlock=True)
                self._delete_session(folder)
            except (OSError, StreamDownloadError):
                pass  # Another live cache keeps its lock; do not disturb it.
        self._safe_directory(self._session)
        self._session.mkdir(exist_ok=True)
        handle = (self._session / "owner.lock").open("w+b")
        try:
            handle.write(b"0")
            handle.flush()
            self._file_lock(handle)
        except Exception:
            handle.close()
            raise
        self._session_lock = handle

    def _lease(self, key, entry, stream):
        lease = secrets.token_hex(16)
        entry.pins += 1
        entry.used_at = self._clock()
        self._leases[lease] = key
        return PreparedAudio(lease, entry.path, stream, entry.byte_count, entry.request_count, entry.resumed)

    def _reserve(self):
        if self._active >= self.MAX_ACTIVE_DOWNLOADS:
            raise StreamDownloadError("busy")
        for path in tuple(self._orphans):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            del self._orphans[path]
        blocked = set()
        while (len(self._entries) + len(self._orphans) + self._active >= self.MAX_CACHE_FILES or
                sum(entry.byte_count for entry in self._entries.values()) + sum(self._orphans.values())
                + (self._active + 1) * self.MAX_FILE_BYTES > self.MAX_CACHE_BYTES):
            available = [(key, entry) for key, entry in self._entries.items() if not entry.pins and key not in blocked]
            if not available:
                raise StreamDownloadError("busy")
            key, entry = min(available, key=lambda pair: pair[1].used_at)
            try:
                entry.path.unlink(missing_ok=True)
            except OSError:
                blocked.add(key)
                continue
            del self._entries[key]
        self._active += 1

    def _discard_partial(self, path):
        try:
            path.unlink(missing_ok=True)
            self._orphans.pop(path, None)
        except OSError:
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                return
            except OSError:
                size = self.MAX_FILE_BYTES
            # An undeletable partial remains part of the bounded session.
            self._orphans[path] = max(0, min(size, self.MAX_FILE_BYTES))

    def prepare(self, stream, cancelled=lambda: False):
        try:
            valid_expiry = (isinstance(stream.expires_at, (int, float)) and not isinstance(stream.expires_at, bool)
                and math.isfinite(stream.expires_at) and stream.expires_at >= 0)
        except (AttributeError, OverflowError, TypeError, ValueError):
            valid_expiry = False
        if (not isinstance(stream, StreamInfo) or stream.playable is not True or not _safe_url(stream.url)
                or not isinstance(stream.trial, bool) or not isinstance(stream.quality, str) or len(stream.quality) > 512
                or not all(isinstance(value, str) and 0 < len(value) <= 512
                    and not any(ord(character) < 32 or ord(character) == 127 for character in value)
                    for value in (stream.source_id, stream.song_id))
                or any(ord(character) < 32 or ord(character) == 127 for character in stream.quality)
                or not valid_expiry):
            raise StreamDownloadError("invalid_response")
        if stream.expires_at > 0 and self._wall_clock() >= stream.expires_at:
            raise StreamDownloadError("expired")
        try:
            key = hashlib.sha256((stream.source_id + "\n" + stream.song_id + "\n" + str(stream.trial)
                + "\n" + stream.quality + "\n" + stream.url).encode("utf-8")).digest()
        except UnicodeError:
            raise StreamDownloadError("invalid_response") from None
        try:
            with self._lock:
                if self._closed or cancelled():
                    raise StreamDownloadError("cancelled")
                self._ensure_session()
                entry = self._entries.get(key)
                if entry is not None:
                    return self._lease(key, entry, stream)
                self._reserve()
        except StreamDownloadError:
            raise
        except Exception:
            raise StreamDownloadError("storage") from None
        token = secrets.token_hex(16)
        partial = self._session / (token + ".part")
        final = self._session / (token + ".audio")
        try:
            size, attempts, resumed = self._download(stream, partial, cancelled)
            self._validate_audio(partial, size)
            with self._lock:
                if self._closed or cancelled():
                    raise StreamDownloadError("cancelled")
                entry = self._entries.get(key)
                if entry is None:
                    partial.replace(final)
                    entry = _Entry(final, size, attempts, resumed, self._clock())
                    self._entries[key] = entry
                return self._lease(key, entry, stream)
        except StreamDownloadError:
            raise
        except Exception:
            raise StreamDownloadError("storage") from None
        finally:
            with self._lock:
                self._discard_partial(partial)
                self._active -= 1
                if self._closed and not self._active:
                    self._cleanup()

    @staticmethod
    def _positive_int(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,12}", value.strip()):
            raise StreamDownloadError("invalid_response")
        number = int(value)
        if number <= 0:
            raise StreamDownloadError("invalid_response")
        return number

    def _check_cancel(self, cancelled, deadline):
        if self._closed or cancelled():
            raise StreamDownloadError("cancelled")
        if self._clock() >= deadline:
            raise StreamDownloadError("timeout")

    def _download(self, stream, path, cancelled):
        deadline = self._clock() + self.DOWNLOAD_SECONDS
        offset, total, validator, resumed = 0, 0, "", False
        with path.open("x+b") as target:
            for attempt in range(1, self.MAX_REQUESTS + 1):
                self._check_cancel(cancelled, deadline)
                if stream.expires_at > 0 and self._wall_clock() >= stream.expires_at:
                    raise StreamDownloadError("expired")
                start = max(0, offset - self.OVERLAP_BYTES)
                headers = {"User-Agent": "GluSpot/owned-playback", "Accept-Encoding": "identity"}
                if offset:
                    headers["Range"] = f"bytes={start}-"
                    if validator:
                        headers["If-Range"] = validator
                try:
                    timeout = min(self.IO_TIMEOUT_SECONDS, max(0.01, deadline - self._clock()))
                    with self._opener.open(Request(stream.url, headers=headers), timeout=timeout) as response:
                        self._check_cancel(cancelled, deadline)
                        if not _safe_url(response.geturl()) or response.status not in {200, 206}:
                            raise StreamDownloadError("invalid_response")
                        encoding = (response.headers.get("Content-Encoding") or "identity").strip().lower()
                        if encoding != "identity" or response.headers.get("Transfer-Encoding"):
                            raise StreamDownloadError("invalid_response")
                        length = self._positive_int(response.headers.get("Content-Length"))
                        content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                        if content_type and not (content_type.startswith("audio/") or content_type in
                                {"application/octet-stream", "binary/octet-stream", "application/ogg", "video/mp4"}):
                            raise StreamDownloadError("invalid_response")
                        etag = (response.headers.get("ETag") or "").strip()
                        current_validator = etag if (len(etag) <= 1024 and etag.startswith('"') and etag.endswith('"')
                            and not any(ord(character) < 32 or ord(character) == 127 for character in etag)) else ""
                        overlap = b""
                        if response.status == 200:
                            if response.headers.get("Content-Range"):
                                raise StreamDownloadError("invalid_response")
                            offset, total, validator = 0, length, current_validator
                            target.seek(0)
                            target.truncate()
                        else:
                            match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)", response.headers.get("Content-Range") or "")
                            if not match:
                                raise StreamDownloadError("invalid_response")
                            low, high, remote_total = map(int, match.groups())
                            if (low != start or high < low or high >= remote_total or length != high - low + 1
                                    or (total and remote_total != total) or (validator and current_validator != validator)):
                                raise StreamDownloadError("invalid_response")
                            total = remote_total
                            if not validator:
                                validator = current_validator
                            target.seek(start)
                            overlap = target.read(offset - start)
                            target.seek(offset)
                        if total <= 0 or total > self.MAX_FILE_BYTES:
                            raise StreamDownloadError("too_large")
                        received, overlap_read = 0, bytearray()
                        while True:
                            self._check_cancel(cancelled, deadline)
                            chunk = response.read1(min(64 * 1024, length - received + 1))
                            self._check_cancel(cancelled, deadline)
                            if not chunk:
                                break
                            received += len(chunk)
                            if received > length:
                                raise StreamDownloadError("invalid_response")
                            if len(overlap_read) < len(overlap):
                                needed = len(overlap) - len(overlap_read)
                                overlap_read.extend(chunk[:needed])
                                chunk = chunk[needed:]
                                if overlap[:len(overlap_read)] != overlap_read:
                                    raise StreamDownloadError("invalid_response")
                            if chunk:
                                if offset + len(chunk) > total:
                                    raise StreamDownloadError("invalid_response")
                                target.write(chunk)
                                offset += len(chunk)
                        if received == length and offset == total and len(overlap_read) == len(overlap):
                            target.flush()
                            return total, attempt, resumed
                        resumed = resumed or bool(offset)
                except HTTPError as error:
                    status = error.code
                    error.close()
                    if status in {401, 403}:
                        raise StreamDownloadError("access_denied") from None
                    if status not in {408, 429, 500, 502, 503, 504}:
                        raise StreamDownloadError("network") from None
                except StreamDownloadError:
                    raise
                except Exception:
                    resumed = resumed or bool(offset)
                    # Only safe categories leave this boundary, never URLs,
                    # headers, response text or arbitrary socket exceptions.
            raise StreamDownloadError("incomplete")

    @staticmethod
    def _validate_audio(path, size):
        with path.open("rb") as source:
            head = source.read(16)
            if head.startswith(b"ID3"):
                tag = head[6:10]
                if len(tag) != 4 or any(value & 128 for value in tag):
                    raise StreamDownloadError("invalid_response")
                tag_size = sum(value << shift for value, shift in zip(tag, (21, 14, 7, 0)))
                skip = 10 + tag_size + (10 if head[5] & 16 else 0)
                if skip + 4 > size:
                    raise StreamDownloadError("invalid_response")
                source.seek(skip)
                head = source.read(16)
            mp3 = (len(head) >= 4 and head[0] == 255 and head[1] & 224 == 224
                and (head[1] >> 3) & 3 != 1 and (head[1] >> 1) & 3 != 0
                and (head[2] >> 4) not in {0, 15} and (head[2] >> 2) & 3 != 3)
            aac = len(head) >= 7 and head[0] == 255 and head[1] in {241, 249}
            known = (head[:4] in {b"fLaC", b"OggS"} or head[4:8] == b"ftyp"
                or (head[:4] == b"RIFF" and head[8:12] == b"WAVE"))
            if not (mp3 or aac or known):
                raise StreamDownloadError("invalid_response")

    def release(self, prepared):
        if not isinstance(prepared, PreparedAudio):
            return
        with self._lock:
            key = self._leases.pop(prepared.lease, None)
            entry = self._entries.get(key)
            if entry is not None:
                entry.pins = max(0, entry.pins - 1)

    def _cleanup(self):
        if self._session_lock is None:
            return
        self._entries.clear()
        self._leases.clear()
        handle = self._session_lock
        self._session_lock = None
        try:
            try:
                self._file_lock(handle, unlock=True)
            finally:
                handle.close()
        except OSError:
            pass
        try:
            self._delete_session(self._session)
            self._orphans.clear()
        except (OSError, StreamDownloadError):
            pass

    def close(self):
        with self._lock:
            self._closed = True
            if not self._active:
                self._cleanup()
