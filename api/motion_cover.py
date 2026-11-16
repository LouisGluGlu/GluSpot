"""Dynamic album covers for any playing source.

NetEase `/api/songplay/dynamic-cover` and Apple Music `editorialVideo` are
unofficial. A miss, timeout, or bad payload is a cached "none": the static
cover stays. Nothing here logs tokens, cookies, or media URLs.

Apple's web-player token discovery follows the public bootstrap (the index
bundle's AMPWebPlay JWT, then the catalog albums endpoint with
`extend=editorialVideo`). coverpy is MIT; this is a separate implementation,
not a copy of that project.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

from api.artwork_sources import artwork_artist_key, artwork_duration_matches, artwork_text_key

MOTION_COVER_TTL_SECONDS = 12 * 60 * 60
MOTION_COVER_PROVIDER_TIMEOUT = 3.0
MOTION_COVER_CACHE_BYTES = 64 * 1024 * 1024
MOTION_COVER_FILE_BYTES = 16 * 1024 * 1024
MOTION_SOURCE_MAX_SIDE = 720
_EAPI_KEY = b"e82ckenh8dichen8"
_EAPI_PATH = "/api/songplay/dynamic-cover"
_EAPI_URL = "https://interface.music.163.com/eapi/songplay/dynamic-cover"
_SEARCH_URL = "https://music.163.com/api/cloudsearch/pc"
_APPLE_ORIGIN = "https://music.apple.com"
_APPLE_BOOTSTRAP = f"{_APPLE_ORIGIN}/us/album/1693323844"
_AMP_ALBUM = "https://amp-api.music.apple.com/v1/catalog/{storefront}/albums/{album_id}"
_ITUNES_SEARCH = "https://itunes.apple.com/search"
_USER_AGENT = "GluSpot/internal-test (Windows non-commercial motion cover)"
_IDLE_TITLES = frozenset({"", "暂无媒体", "暂无播放", "媒体初始化中"})
_SCRIPT_RE = re.compile(r"""src=["'](?P<path>/assets/index[^"']+\.js)["']""")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_STREAM_RE = re.compile(r"([A-Z0-9-]+)=(?:\"([^\"]*)\"|([^,]*))(?:,|$)")
_MAP_URI_RE = re.compile(r'URI="([^"]+)"')
_RESULT_NAMES = frozenset({"hit", "miss", "timeout", "error", "cancelled"})

_log = logging.getLogger("GluSpot.motion_cover")
_log.setLevel(logging.INFO)
if not _log.handlers and sys.stderr is not None:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    _log.addHandler(_handler)
    _log.propagate = False

# AES-128 S-box. Used only to speak NetEase eapi.
_SBOX = bytes((
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
))
_INV_SBOX = bytearray(256)
for _index, _value in enumerate(_SBOX):
    _INV_SBOX[_value] = _index
_INV_SBOX = bytes(_INV_SBOX)


def log_motion(provider: str, result: str, latency_ms: int, error: str = "none", *, cached: bool = False) -> None:
    """Log a controlled label. Callers must not pass secrets or URLs."""
    safe_provider = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(provider or "none"))[:40] or "none"
    safe_result = result if result in _RESULT_NAMES else "error"
    safe_error = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(error or "none"))[:40] or "none"
    _log.info(
        "motion_cover provider=%s result=%s latency_ms=%d cached=%d error=%s",
        safe_provider, safe_result, max(0, int(latency_ms)), int(bool(cached)), safe_error,
    )


def _xtime(value: int) -> int:
    return ((value << 1) ^ 0x1B) & 0xFF if value & 0x80 else (value << 1) & 0xFF


def _gmul(left: int, right: int) -> int:
    product = 0
    for _ in range(8):
        if right & 1:
            product ^= left
        high = left & 0x80
        left = (left << 1) & 0xFF
        if high:
            left ^= 0x1B
        right >>= 1
    return product


def _expand_key(key: bytes) -> bytes:
    if len(key) != 16:
        raise ValueError("AES-128 key must be 16 bytes")
    words = list(key)
    rcon = 1
    while len(words) < 176:
        temp = words[-4:]
        if len(words) % 16 == 0:
            temp = [_SBOX[temp[1]] ^ rcon, _SBOX[temp[2]], _SBOX[temp[3]], _SBOX[temp[0]]]
            rcon = _xtime(rcon)
        for index in range(4):
            words.append(words[-16] ^ temp[index])
    return bytes(words)


def _shift_rows(state: list[int], inverse: bool = False) -> list[int]:
    out = state[:]
    for row in range(1, 4):
        cells = [state[row + 4 * column] for column in range(4)]
        shift = (4 - row) if inverse else row
        cells = cells[shift:] + cells[:shift]
        for column in range(4):
            out[row + 4 * column] = cells[column]
    return out


def _mix_columns(state: list[int], inverse: bool = False) -> list[int]:
    out = state[:]
    for column in range(4):
        cells = state[4 * column:4 * column + 4]
        if inverse:
            mixed = [
                _gmul(cells[0], 0x0E) ^ _gmul(cells[1], 0x0B) ^ _gmul(cells[2], 0x0D) ^ _gmul(cells[3], 0x09),
                _gmul(cells[0], 0x09) ^ _gmul(cells[1], 0x0E) ^ _gmul(cells[2], 0x0B) ^ _gmul(cells[3], 0x0D),
                _gmul(cells[0], 0x0D) ^ _gmul(cells[1], 0x09) ^ _gmul(cells[2], 0x0E) ^ _gmul(cells[3], 0x0B),
                _gmul(cells[0], 0x0B) ^ _gmul(cells[1], 0x0D) ^ _gmul(cells[2], 0x09) ^ _gmul(cells[3], 0x0E),
            ]
        else:
            mixed = [
                _xtime(cells[0]) ^ _xtime(cells[1]) ^ cells[1] ^ cells[2] ^ cells[3],
                cells[0] ^ _xtime(cells[1]) ^ _xtime(cells[2]) ^ cells[2] ^ cells[3],
                cells[0] ^ cells[1] ^ _xtime(cells[2]) ^ _xtime(cells[3]) ^ cells[3],
                _xtime(cells[0]) ^ cells[0] ^ cells[1] ^ cells[2] ^ _xtime(cells[3]),
            ]
        out[4 * column:4 * column + 4] = mixed
    return out


def _add_round_key(state: list[int], expanded: bytes, offset: int) -> list[int]:
    return [state[index] ^ expanded[offset + index] for index in range(16)]


def _aes_block(block: bytes, expanded: bytes, *, decrypt: bool) -> bytes:
    state = list(block)
    if not decrypt:
        state = _add_round_key(state, expanded, 0)
        for round_index in range(1, 10):
            state = [_SBOX[value] for value in state]
            state = _shift_rows(state, False)
            state = _mix_columns(state, False)
            state = _add_round_key(state, expanded, round_index * 16)
        state = [_SBOX[value] for value in state]
        state = _shift_rows(state, False)
        state = _add_round_key(state, expanded, 160)
        return bytes(state)
    state = _add_round_key(state, expanded, 160)
    state = _shift_rows(state, True)
    state = [_INV_SBOX[value] for value in state]
    for round_index in range(9, 0, -1):
        state = _add_round_key(state, expanded, round_index * 16)
        state = _mix_columns(state, True)
        state = _shift_rows(state, True)
        state = [_INV_SBOX[value] for value in state]
    return bytes(_add_round_key(state, expanded, 0))


def aes128_ecb_encrypt(key: bytes, plain: bytes) -> bytes:
    expanded = _expand_key(key)
    if not plain or len(plain) % 16:
        raise ValueError("AES plaintext must be a positive multiple of 16 bytes")
    return b"".join(_aes_block(plain[offset:offset + 16], expanded, decrypt=False) for offset in range(0, len(plain), 16))


def aes128_ecb_decrypt(key: bytes, cipher: bytes) -> bytes:
    expanded = _expand_key(key)
    if not cipher or len(cipher) % 16:
        raise ValueError("AES ciphertext must be a positive multiple of 16 bytes")
    return b"".join(_aes_block(cipher[offset:offset + 16], expanded, decrypt=True) for offset in range(0, len(cipher), 16))


def _pkcs7_pad(data: bytes) -> bytes:
    count = 16 - (len(data) % 16)
    return data + bytes((count,)) * count


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise ValueError("invalid PKCS7 payload")
    count = data[-1]
    if count < 1 or count > 16 or data[-count:] != bytes((count,)) * count:
        raise ValueError("invalid PKCS7 padding")
    return data[:-count]


def eapi_plain(song_id: str) -> str:
    payload = {"songId": str(song_id), "header": {"os": "pc", "appver": "2.10.13"}}
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.md5(f"nobody{_EAPI_PATH}use{text}md5forencrypt".encode("utf-8")).hexdigest()
    return f"{_EAPI_PATH}-36cd479b6b5-{text}-36cd479b6b5-{digest}"


def eapi_form(song_id: str) -> bytes:
    cipher = aes128_ecb_encrypt(_EAPI_KEY, _pkcs7_pad(eapi_plain(song_id).encode("utf-8")))
    return urlencode({"params": cipher.hex().upper()}).encode("ascii")


def eapi_decrypt_payload(body: bytes) -> dict:
    raw = body.strip()
    if raw.startswith((b"{", b"[")):
        payload = json.loads(raw.decode("utf-8"))
    else:
        plain = _pkcs7_unpad(aes128_ecb_decrypt(_EAPI_KEY, bytes.fromhex(raw.decode("ascii").strip())))
        payload = json.loads(plain.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("dynamic cover payload is not an object")
    return payload


def dynamic_cover_url(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    try:
        if int(payload.get("code", 0)) != 200:
            return ""
    except (TypeError, ValueError):
        return ""
    data = payload.get("data")
    if isinstance(data, list):
        data = next((item for item in data if isinstance(item, dict)), None)
    if not isinstance(data, dict):
        return ""
    url = str(data.get("videoPlayUrl") or "").strip()
    return url if is_video_url(url) else ""


def is_video_url(url: str) -> bool:
    try:
        parsed = urlsplit(str(url or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    path = parsed.path.casefold()
    return path.endswith((".mp4", ".m3u8", ".mov", ".webm", ".m4v")) or ".m3u8" in path or ".mp4" in path


def netease_song_id(track_id: str, source_app: str = "") -> str:
    text = str(track_id or "").strip()
    parts = text.split(":")
    source = str(source_app or "")
    owned_netease = len(parts) >= 3 and parts[0] == "owned" and parts[1].casefold() in {"netease", "cloudmusic"}
    if owned_netease and parts[2].isdigit():
        return parts[2]
    netease = owned_netease or "netease" in source.casefold() or "网易" in source
    if netease and text.isdigit():
        return text
    return ""


@dataclass(frozen=True)
class MotionTrack:
    title: str
    artist: str = ""
    album: str = ""
    duration_seconds: float = 0.0
    track_id: str = ""
    source_app: str = ""

    def song_id(self) -> str:
        return netease_song_id(self.track_id, self.source_app)

    def cache_key(self) -> str:
        song = self.song_id()
        material = f"netease:{song}" if song else "\n".join((
            artwork_text_key(self.title), artwork_text_key(self.artist), artwork_text_key(self.album),
        ))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    def lookup_allowed(self) -> bool:
        return bool(self.song_id() or (self.title.strip() and self.title.strip() not in _IDLE_TITLES))


def _names(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    return "/".join(
        str(item.get("name") or "").strip()
        for item in value
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    )


def _artists_match(left: str, right: str) -> bool:
    first, second = artwork_artist_key(left), artwork_artist_key(right)
    if not first or not second:
        return True
    return first == second or bool(set(first) & set(second))


def select_netease_song_id(payload, track: MotionTrack) -> str:
    result = payload.get("result") if isinstance(payload, dict) else None
    songs = result.get("songs") if isinstance(result, dict) else None
    if not isinstance(songs, list):
        return ""
    best: tuple[tuple[int, int], str] | None = None
    for index, song in enumerate(songs):
        if not isinstance(song, dict):
            continue
        song_id = str(song.get("id") or "")
        title = str(song.get("name") or song.get("title") or "")
        if not song_id.isdigit() or not artwork_text_key(title) or artwork_text_key(title) != artwork_text_key(track.title):
            continue
        if not _artists_match(_names(song.get("artists") or song.get("ar")), track.artist):
            continue
        album = song.get("album") if isinstance(song.get("album"), dict) else song.get("al")
        album_name = str(album.get("name") or "") if isinstance(album, dict) else ""
        if track.album and artwork_text_key(album_name) != artwork_text_key(track.album):
            continue
        try:
            duration = float(song.get("duration") or song.get("dt") or 0.0) / 1000.0
        except (TypeError, ValueError):
            continue
        if not artwork_duration_matches(track.duration_seconds, duration, require_known=track.duration_seconds > 0):
            continue
        score = (
            1 if track.album and artwork_text_key(album_name) == artwork_text_key(track.album) else 0,
            1 if track.duration_seconds > 0 else 0,
        )
        # Lower index wins ties because search order is the provider's preference.
        rank = (score, -index)
        if best is None or rank > best[0]:
            best = (rank, song_id)
    return best[1] if best else ""


def select_itunes_album_id(payload, track: MotionTrack) -> str:
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return ""
    best: tuple[tuple[int, int], str] | None = None
    for index, item in enumerate(results):
        if not isinstance(item, dict):
            continue
        album_id = str(item.get("collectionId") or "")
        title = str(item.get("trackName") or item.get("collectionName") or "")
        if not album_id.isdigit():
            continue
        kind = str(item.get("kind") or item.get("wrapperType") or "")
        title_ok = artwork_text_key(title) == artwork_text_key(track.title)
        album_name = str(item.get("collectionName") or "")
        album_ok = bool(track.album) and artwork_text_key(album_name) == artwork_text_key(track.album)
        if kind == "song" and not title_ok:
            continue
        if kind != "song" and not album_ok and not title_ok:
            continue
        if not _artists_match(str(item.get("artistName") or ""), track.artist):
            continue
        if track.album and not album_ok:
            continue
        try:
            millis = float(item.get("trackTimeMillis") or 0.0)
        except (TypeError, ValueError):
            millis = 0.0
        duration = millis / 1000.0
        if kind == "song" and not artwork_duration_matches(track.duration_seconds, duration, require_known=track.duration_seconds > 0):
            continue
        score = (1 if album_ok else 0, 1 if title_ok else 0, 1 if track.duration_seconds > 0 and duration > 0 else 0)
        rank = (score, -index)
        if best is None or rank > best[0]:
            best = (rank, album_id)
    return best[1] if best else ""


def editorial_video_url(payload) -> str:
    data = payload.get("data") if isinstance(payload, dict) else None
    album = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None
    attributes = album.get("attributes") if isinstance(album, dict) else None
    editorial = attributes.get("editorialVideo") if isinstance(attributes, dict) else None
    if not isinstance(editorial, dict):
        return ""
    for key in ("motionDetailSquare", "motionSquareVideo1x1"):
        block = editorial.get(key)
        if isinstance(block, dict):
            video = str(block.get("video") or "").strip()
            if is_video_url(video):
                return video
    return ""


@dataclass(frozen=True)
class HlsVariant:
    url: str
    bandwidth: int
    width: int
    codecs: str


def parse_hls_variants(text: str, base_url: str) -> tuple[HlsVariant, ...]:
    lines = [line.strip() for line in str(text or "").splitlines()]
    variants: list[HlsVariant] = []
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attributes = {
            match.group(1): match.group(2) if match.group(2) is not None else match.group(3)
            for match in _STREAM_RE.finditer(line.split(":", 1)[1])
        }
        media = next((item for item in lines[index + 1:] if item and not item.startswith("#")), "")
        if not media:
            continue
        resolution = str(attributes.get("RESOLUTION") or "")
        width_text = resolution.partition("x")[0]
        bandwidth_text = str(attributes.get("AVERAGE-BANDWIDTH") or attributes.get("BANDWIDTH") or "0")
        variants.append(HlsVariant(
            url=urljoin(base_url, media),
            bandwidth=int(bandwidth_text) if bandwidth_text.isdigit() else 0,
            width=int(width_text) if width_text.isdigit() else 0,
            codecs=str(attributes.get("CODECS") or ""),
        ))
    return tuple(variants)


def choose_hls_variant(variants: tuple[HlsVariant, ...], target_width: int = 480) -> HlsVariant | None:
    if not variants:
        return None
    target = max(64, min(MOTION_SOURCE_MAX_SIDE, int(target_width or 480)))
    compatible = [item for item in variants if "avc1" in item.codecs.casefold()] or list(variants)
    bounded = [item for item in compatible if 0 < item.width <= MOTION_SOURCE_MAX_SIDE]
    pool = bounded or compatible
    return min(pool, key=lambda item: (abs((item.width or target) - target), item.bandwidth, item.width))


def direct_media_url(text: str, base_url: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#EXT-X-MAP:"):
            match = _MAP_URI_RE.search(stripped)
            if match:
                url = urljoin(base_url, match.group(1))
                if is_video_url(url) or url.casefold().split("?", 1)[0].endswith(".mp4"):
                    return url
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and ".mp4" in stripped.casefold():
            url = urljoin(base_url, stripped)
            if url.startswith("http"):
                return url
    return ""


def apple_script_path(page_html: str) -> str:
    match = _SCRIPT_RE.search(str(page_html or ""))
    return match.group("path") if match else ""


def _jwt_payload(token: str) -> dict:
    try:
        encoded = token.split(".")[1]
        padded = encoded + "=" * (-len(encoded) % 4)
        import base64
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def apple_web_token(script_text: str, *, now: float | None = None) -> tuple[str, float]:
    """Return the web-player JWT and its expiry. The token is not logged."""
    current = time.time() if now is None else float(now)
    for match in _JWT_RE.finditer(str(script_text or "")):
        token = match.group(0)
        payload = _jwt_payload(token)
        if payload.get("iss") != "AMPWebPlay":
            continue
        try:
            expiry = float(payload.get("exp") or 0.0)
        except (TypeError, ValueError):
            expiry = 0.0
        if expiry and expiry <= current:
            continue
        return token, expiry
    return "", 0.0


class CancelToken:
    def __init__(self):
        self._event = threading.Event()
        self._closers: list = []
        self._lock = threading.Lock()

    def is_set(self) -> bool:
        return self._event.is_set()

    def bind(self, closer) -> None:
        with self._lock:
            self._closers.append(closer)
            armed = self._event.is_set()
        if armed:
            try:
                closer()
            except Exception:
                pass

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            closers = list(self._closers)
        for closer in closers:
            try:
                closer()
            except Exception:
                pass


@dataclass(frozen=True)
class MotionResult:
    kind: str
    provider: str
    url: str = ""
    local_path: str = ""
    cached: bool = False


@dataclass(frozen=True)
class _Provider:
    name: str
    query: object


def race_providers(providers: tuple[_Provider, ...], track: MotionTrack, *, timeout: float = MOTION_COVER_PROVIDER_TIMEOUT,
                   cancel: CancelToken | None = None, clock=time.monotonic) -> MotionResult:
    """Run providers together. The first valid video wins and cancels the rest."""
    token = cancel or CancelToken()
    winner: dict[str, str] = {}
    lock = threading.Lock()
    done = threading.Event()
    pending = {"count": len(providers)}

    def finish() -> None:
        with lock:
            pending["count"] -= 1
            if pending["count"] <= 0:
                done.set()

    def run(provider: _Provider) -> None:
        started = clock()
        status = "miss"
        error = "none"
        url = ""
        try:
            if token.is_set():
                status = "cancelled"
            else:
                found = provider.query(track, token, started + timeout)
                url = str(found or "")
                if token.is_set() and not url:
                    status = "cancelled"
                elif is_video_url(url):
                    status = "hit"
                else:
                    url = ""
                    status = "miss"
        except TimeoutError:
            # A winner already cancelled this provider. That is not a failure.
            status = "cancelled" if token.is_set() else "timeout"
            error = "none" if token.is_set() else "TimeoutError"
        except Exception as exc:
            status = "cancelled" if token.is_set() else "error"
            error = type(exc).__name__
        latency = int(max(0.0, clock() - started) * 1000)
        claimed = False
        if status == "hit":
            with lock:
                if "url" not in winner:
                    winner["url"] = url
                    winner["provider"] = provider.name
                    claimed = True
            if claimed:
                token.cancel()
                done.set()
        log_motion(provider.name, status, latency, error)
        finish()

    threads = [threading.Thread(target=run, args=(provider,), name=f"GluSpotMotion{provider.name}", daemon=True)
               for provider in providers]
    for thread in threads:
        thread.start()
    done.wait(timeout)
    token.cancel()
    join_deadline = clock() + 0.3
    for thread in threads:
        remaining = join_deadline - clock()
        if remaining > 0:
            thread.join(remaining)
    with lock:
        url = winner.get("url", "")
        provider = winner.get("provider", "")
    if is_video_url(url):
        return MotionResult("hit", provider or "motion", url)
    return MotionResult("none", provider or "motion")


def default_motion_cache_dir() -> Path:
    override = str(os.environ.get("GLUSPOT_MOTION_COVER_CACHE_DIR", "") or "").strip()
    if override:
        return Path(override)
    from local.branding import is_packaged_app, local_app_data_dir
    local_root = str(os.environ.get("LOCALAPPDATA", "") or "").strip()
    if not local_root and not is_packaged_app():
        return Path(tempfile.gettempdir()) / "GluSpot" / "motion_cover"
    return Path(local_app_data_dir()) / "cache" / "motion_cover"


class MotionCoverCache:
    def __init__(self, directory: Path, *, ttl_seconds: float = MOTION_COVER_TTL_SECONDS,
                 max_bytes: int = MOTION_COVER_CACHE_BYTES, max_file_bytes: int = MOTION_COVER_FILE_BYTES,
                 clock=time.time):
        self.directory = Path(directory)
        self.ttl_seconds = float(ttl_seconds)
        self.max_bytes = int(max_bytes)
        self.max_file_bytes = int(max_file_bytes)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        self._loaded = False

    def _path(self) -> Path:
        return self.directory / "results.json"

    def _files(self) -> Path:
        return self.directory / "files"

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            payload = json.loads(self._path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if isinstance(entries, dict):
            self._entries = {str(key): value for key, value in entries.items() if isinstance(value, dict)}

    def _save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / "results.json.tmp"
        temporary.write_text(json.dumps({"version": 1, "entries": self._entries}, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self._path())

    def get(self, key: str, *, now: float | None = None) -> MotionResult | None:
        current = self._clock() if now is None else float(now)
        with self._lock:
            self._load()
            entry = self._entries.get(key)
            if not entry:
                return None
            try:
                stored = float(entry.get("stored_at") or 0.0)
            except (TypeError, ValueError):
                return None
            if current - stored > self.ttl_seconds:
                return None
            kind = str(entry.get("result") or "")
            provider = str(entry.get("provider") or "cache")
            if kind == "none":
                return MotionResult("none", provider, cached=True)
            if kind != "hit":
                return None
            url = str(entry.get("url") or "")
            filename = str(entry.get("file") or "")
            local = self._files() / filename if filename else None
            if local is not None and local.is_file():
                return MotionResult("hit", provider, url if is_video_url(url) else "", str(local), cached=True)
            if is_video_url(url):
                return MotionResult("hit", provider, url, cached=True)
            return None

    def put(self, key: str, result: MotionResult, *, now: float | None = None) -> None:
        current = self._clock() if now is None else float(now)
        record = {
            "result": "hit" if result.kind == "hit" and (result.local_path or is_video_url(result.url)) else "none",
            "provider": re.sub(r"[^a-zA-Z0-9_.-]", "_", result.provider)[:40] or "motion",
            "url": result.url if is_video_url(result.url) else "",
            "file": Path(result.local_path).name if result.local_path else "",
            "stored_at": current,
        }
        if record["result"] != "hit":
            record["url"] = ""
            record["file"] = ""
            record["result"] = "none"
        with self._lock:
            self._load()
            self._entries[key] = record
            while len(self._entries) > 400:
                self._entries.pop(next(iter(self._entries)))
            try:
                self._save()
            except OSError:
                return

    def remember_file(self, key: str, filename: str) -> None:
        with self._lock:
            self._load()
            entry = self._entries.get(key)
            if not entry or entry.get("result") != "hit":
                return
            entry["file"] = Path(filename).name
            try:
                self._save()
            except OSError:
                return

    def store_video(self, url: str, chunks, *, key: str = "") -> str:
        """Write a downloaded video under the size cap. Return the path, or ''."""
        if not is_video_url(url):
            return ""
        digest = hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()[:32]
        self._files().mkdir(parents=True, exist_ok=True)
        target = self._files() / f"{digest}.mp4"
        temporary = self._files() / f"{digest}.tmp"
        written = 0
        try:
            with temporary.open("wb") as output:
                for chunk in chunks:
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > self.max_file_bytes:
                        raise ValueError("motion video exceeds file cap")
                    output.write(chunk)
            if written <= 0:
                temporary.unlink(missing_ok=True)
                return ""
            os.replace(temporary, target)
        except (OSError, ValueError, TimeoutError):
            return ""
        finally:
            temporary.unlink(missing_ok=True)
        self._evict()
        if key:
            self.remember_file(key, target.name)
        return str(target)

    def _evict(self) -> None:
        files = sorted(self._files().glob("*.mp4"), key=lambda item: item.stat().st_mtime)
        total = sum(item.stat().st_size for item in files)
        for item in files:
            if total <= self.max_bytes:
                break
            size = item.stat().st_size
            item.unlink(missing_ok=True)
            total -= size


def download_video(url: str, cache: MotionCoverCache, key: str, cancel: CancelToken, *, timeout: float = 8.0) -> str:
    """Cache a direct video file. Playlists stay remote. Oversized files are skipped."""
    path = urlsplit(url).path.casefold()
    if not is_video_url(url) or path.endswith(".m3u8"):
        return ""
    if cancel.is_set():
        return ""
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        response = urlopen(request, timeout=timeout)
    except (OSError, HTTPError, URLError, TimeoutError, ValueError):
        return ""
    cancel.bind(response.close)
    try:
        length = response.headers.get("Content-Length") if getattr(response, "headers", None) is not None else None
        if length and str(length).isdigit() and int(length) > cache.max_file_bytes:
            return ""

        def chunks():
            remaining = cache.max_file_bytes
            while remaining > 0:
                if cancel.is_set():
                    raise TimeoutError("motion cover cancelled")
                block = response.read(min(64 * 1024, remaining + 1))
                if not block:
                    break
                if len(block) > remaining:
                    raise ValueError("motion video exceeds file cap")
                remaining -= len(block)
                yield block

        return cache.store_video(url, chunks(), key=key)
    except (OSError, HTTPError, URLError, TimeoutError, ValueError):
        return ""
    finally:
        response.close()


def urllib_fetch(method: str, url: str, *, headers: dict | None = None, body: bytes | None = None,
                 timeout: float = MOTION_COVER_PROVIDER_TIMEOUT, cancel: CancelToken | None = None) -> tuple[int, bytes]:
    if cancel is not None and cancel.is_set():
        raise TimeoutError("motion cover cancelled")
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("unsupported motion cover URL")
    request = Request(url, data=body, headers=dict(headers or {}), method=method.upper())
    try:
        response = urlopen(request, timeout=max(0.05, float(timeout)))
    except HTTPError as exc:
        status = int(getattr(exc, "code", 0) or 0)
        payload = exc.read(64 * 1024) if hasattr(exc, "read") else b""
        return status, payload
    if cancel is not None:
        cancel.bind(response.close)
        if cancel.is_set():
            response.close()
            raise TimeoutError("motion cover cancelled")
    try:
        return int(getattr(response, "status", 200) or 200), response.read(MOTION_COVER_FILE_BYTES + 1)
    except Exception:
        # Closing the response during cancel sets its body to None. The next
        # read then raises AttributeError. That is a cancel, not a bad payload.
        if cancel is not None and cancel.is_set():
            raise TimeoutError("motion cover cancelled") from None
        raise
    finally:
        try:
            response.close()
        except Exception:
            pass


def _json_request(fetch, method: str, url: str, cancel: CancelToken, deadline: float, *,
                  headers: dict | None = None, body: bytes | None = None, clock=time.monotonic):
    if cancel.is_set() or clock() >= deadline:
        raise TimeoutError("motion cover budget expired")
    status, payload = fetch(method, url, headers=headers, body=body, timeout=max(0.05, deadline - clock()), cancel=cancel)
    if cancel.is_set():
        raise TimeoutError("motion cover cancelled")
    if status != 200:
        raise ValueError(f"HTTP {status}")
    return payload


def query_netease(track: MotionTrack, cancel: CancelToken, deadline: float, fetch, clock=time.monotonic) -> str:
    song_id = track.song_id()
    headers = {
        "User-Agent": _USER_AGENT,
        "Referer": "https://music.163.com/",
        "Origin": "https://music.163.com",
    }
    if not song_id:
        query = " ".join(part for part in (track.title.strip(), track.artist.strip()) if part)
        if not query:
            return ""
        body = urlencode({"type": "1", "s": query, "offset": "0", "limit": "8"}).encode("utf-8")
        payload = _json_request(
            fetch, "POST", _SEARCH_URL, cancel, deadline, clock=clock,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"}, body=body,
        )
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("NetEase search JSON") from exc
        song_id = select_netease_song_id(parsed, track)
    if not song_id:
        return ""
    form = eapi_form(song_id)
    payload = _json_request(
        fetch, "POST", _EAPI_URL, cancel, deadline, clock=clock,
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"}, body=form,
    )
    try:
        return dynamic_cover_url(eapi_decrypt_payload(payload))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return ""


class AppleTokenStore:
    def __init__(self):
        self.token = ""
        self.expiry = 0.0
        self._lock = threading.Lock()

    def get(self, fetch, cancel: CancelToken, deadline: float, clock=time.monotonic) -> str:
        with self._lock:
            if self.token and (not self.expiry or self.expiry - 60 > time.time()):
                return self.token
        page = _json_request(fetch, "GET", _APPLE_BOOTSTRAP, cancel, deadline, clock=clock, headers={
            "User-Agent": _USER_AGENT,
        })
        script = apple_script_path(page.decode("utf-8", errors="replace"))
        if not script:
            return ""
        bundle = _json_request(fetch, "GET", urljoin(_APPLE_ORIGIN, script), cancel, deadline, clock=clock, headers={
            "User-Agent": _USER_AGENT,
        })
        token, expiry = apple_web_token(bundle.decode("utf-8", errors="replace"))
        if not token:
            return ""
        with self._lock:
            self.token = token
            self.expiry = expiry
        return token


def query_apple(track: MotionTrack, cancel: CancelToken, deadline: float, fetch, tokens: AppleTokenStore,
                clock=time.monotonic, storefront: str = "cn") -> str:
    title = track.title.strip() if isinstance(track.title, str) else ""
    artist = track.artist.strip() if isinstance(track.artist, str) else ""
    album = track.album.strip() if isinstance(track.album, str) else ""
    if not title or title in _IDLE_TITLES:
        return ""
    token = tokens.get(fetch, cancel, deadline, clock=clock)
    if not token or cancel.is_set():
        return ""
    term = " ".join(part for part in (title, artist, album) if part)
    album_id = ""
    regions = (storefront,) if storefront == "us" else (storefront, "us")
    for region in regions:
        if cancel.is_set() or clock() >= deadline:
            break
        search_url = _ITUNES_SEARCH + "?" + urlencode({
            "term": term, "entity": "song", "limit": "8", "country": region,
        })
        payload = _json_request(fetch, "GET", search_url, cancel, deadline, clock=clock, headers={"User-Agent": _USER_AGENT})
        try:
            album_id = select_itunes_album_id(json.loads(payload.decode("utf-8")), track)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("iTunes search JSON") from exc
        if album_id:
            storefront = region
            break
    if not album_id:
        return ""
    catalog = _AMP_ALBUM.format(storefront=storefront, album_id=album_id) + "?extend=editorialVideo"
    # The bearer token is a header only. It must never be written to a log.
    body = _json_request(fetch, "GET", catalog, cancel, deadline, clock=clock, headers={
        "User-Agent": _USER_AGENT,
        "Authorization": f"Bearer {token}",
        "Origin": _APPLE_ORIGIN,
    })
    try:
        video = editorial_video_url(json.loads(body.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not video or cancel.is_set() or clock() >= deadline:
        return video
    try:
        playlist = _json_request(fetch, "GET", video, cancel, deadline, clock=clock, headers={"User-Agent": _USER_AGENT})
    except (TimeoutError, ValueError, URLError):
        return video
    text = playlist.decode("utf-8", errors="replace")
    variants = parse_hls_variants(text, video)
    chosen = choose_hls_variant(variants)
    if chosen is None:
        return direct_media_url(text, video) or video
    if clock() >= deadline - 0.2 or cancel.is_set():
        return chosen.url
    try:
        media = _json_request(fetch, "GET", chosen.url, cancel, deadline, clock=clock, headers={"User-Agent": _USER_AGENT})
    except (TimeoutError, ValueError, URLError):
        return chosen.url
    return direct_media_url(media.decode("utf-8", errors="replace"), chosen.url) or chosen.url


class MotionCoverResolver:
    def __init__(self, cache: MotionCoverCache, fetch=None, *, tokens: AppleTokenStore | None = None, clock=time.monotonic):
        self.cache = cache
        self.fetch = fetch or urllib_fetch
        self.tokens = tokens or AppleTokenStore()
        self._clock = clock

    def resolve(self, track: MotionTrack, cancel: CancelToken | None = None) -> MotionResult:
        started = self._clock()
        if not track.lookup_allowed():
            log_motion("motion", "miss", 0, "idle")
            return MotionResult("none", "motion")
        cached = self.cache.get(track.cache_key())
        if cached is not None:
            log_motion(cached.provider, "hit" if cached.kind == "hit" else "miss",
                       int(max(0.0, self._clock() - started) * 1000), cached=True)
            return cached
        result = race_providers((
            _Provider("netease", lambda item, token, deadline: query_netease(item, token, deadline, self.fetch, self._clock)),
            _Provider("apple", lambda item, token, deadline: query_apple(item, token, deadline, self.fetch, self.tokens, self._clock)),
        ), track, cancel=cancel, clock=self._clock)
        self.cache.put(track.cache_key(), result)
        return result
