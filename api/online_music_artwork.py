"""Bounded source-owned artwork requests; callers run these off the GUI thread.

No request carries an account cookie, and failures never expose signed URLs.
"""
from __future__ import annotations

import time
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QImageReader

from api.artwork_sources import ARTWORK_USER_AGENT, normalize_artwork_url
from api.models import ColorToken


ONLINE_ARTWORK_MAX_BYTES = 8 * 1024 * 1024
ONLINE_ARTWORK_MAX_SIDE = 4096
ONLINE_ARTWORK_MAX_PIXELS = 4096 * 4096
ONLINE_ARTWORK_BUDGET_SECONDS = 6.0


class OnlineArtworkError(Exception):
    """Sanitized retry evidence; never retain a request URL or response text."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _safe_artwork_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 16384:
        return ""
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return ""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.port not in {None, 80, 443}):
            return ""
    except ValueError:
        return ""
    return value


class _ArtworkRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if not _safe_artwork_url(new_url):
            raise ValueError("invalid artwork redirect")
        return super().redirect_request(request, response, code, message, headers, new_url)


def _decoded_artwork_is_bounded(content: bytes, *, raise_errors=False) -> bool:
    # Read dimensions before allocating the full bitmap. All decoding happens
    # on the requesting worker; no QPixmap or GUI object crosses this boundary.
    buffer = QBuffer()
    buffer.setData(QByteArray(content))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    width, height = size.width(), size.height()
    if (width < 1 or height < 1 or max(width, height) > ONLINE_ARTWORK_MAX_SIDE
            or width * height > ONLINE_ARTWORK_MAX_PIXELS):
        if raise_errors:
            raise OnlineArtworkError("invalid_image" if width < 1 or height < 1 else "invalid_response")
        return False
    image = reader.read()
    valid = not image.isNull() and image.width() == width and image.height() == height
    if not valid and raise_errors:
        raise OnlineArtworkError("invalid_image")
    return valid


def fetch_online_artwork(url: str, *, raise_errors=False, target_side=None) -> bytes:
    """Fetch a validated raster within the byte, decode and request budgets.

    Return empty bytes on unavailable/invalid artwork. Unknown and signed URLs
    retain their exact request target; only the existing NetEase CDN helper may
    request another size of the same known image object. Queue workers can
    request a small target without changing the full-artwork default.
    """
    original = _safe_artwork_url(url)
    if not original:
        if raise_errors:
            raise OnlineArtworkError("invalid_response")
        return b""
    upgraded = normalize_artwork_url(original) if target_side is None else normalize_artwork_url(original, target_side)
    deadline = time.monotonic() + ONLINE_ARTWORK_BUDGET_SECONDS
    failure = "invalid_response"
    # Only the known same-object size upgrade has a fallback. Unknown/signed
    # addresses retain their exact target, and every attempt shares one budget.
    for value in dict.fromkeys((upgraded, original)):
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OnlineArtworkError("timeout")
            request = Request(value, headers={"User-Agent": ARTWORK_USER_AGENT})
            opener = build_opener(_ArtworkRedirectHandler())
            with opener.open(request, timeout=min(3.0, remaining)) as response:
                if not _safe_artwork_url(response.geturl()):
                    raise OnlineArtworkError("invalid_response")
                header = response.headers.get("Content-Length")
                length = int(header) if header is not None else None
                if length is not None and not 0 < length <= ONLINE_ARTWORK_MAX_BYTES:
                    raise OnlineArtworkError("invalid_response")
                content = bytearray()
                while True:
                    if time.monotonic() >= deadline:
                        raise OnlineArtworkError("timeout")
                    chunk = response.read1(min(64 * 1024, ONLINE_ARTWORK_MAX_BYTES + 1 - len(content)))
                    if time.monotonic() >= deadline:
                        raise OnlineArtworkError("timeout")
                    if not chunk:
                        break
                    content.extend(chunk)
                    if len(content) > ONLINE_ARTWORK_MAX_BYTES:
                        raise OnlineArtworkError("invalid_response")
            if length is not None and len(content) != length:
                raise OnlineArtworkError("incomplete")
            result = bytes(content)
            if not result:
                raise OnlineArtworkError("incomplete")
            if not _decoded_artwork_is_bounded(result, raise_errors=True):
                raise OnlineArtworkError("invalid_image")
            return result
        except OnlineArtworkError as exc:
            failure = exc.code
        except HTTPError as exc:
            failure = ("network" if exc.code in {408, 429} or 500 <= exc.code < 600 else
                "access_denied" if exc.code in {401, 403} else "not_found" if exc.code == 404 else "response")
        except (TimeoutError, URLError) as exc:
            failure = "timeout" if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError) else "network"
        except IncompleteRead:
            failure = "incomplete"
        except OSError:
            failure = "network"
        except Exception:
            failure = "invalid_response"
    if raise_errors:
        raise OnlineArtworkError(failure)
    return b""


def online_artwork_min_side(content: bytes) -> int:
    """Read dimensions on the artwork worker; the GUI receives plain metadata."""
    image = QImage.fromData(content)
    return min(image.width(), image.height()) if not image.isNull() else 0


def online_artwork_thumbnail_bytes(content: bytes, side: int = 320) -> bytes:
    """Bound queue-cache pixels on the requesting worker, retaining small previews."""
    image = QImage.fromData(content)
    if image.isNull():
        return b""
    if max(image.width(), image.height()) <= side:
        return content
    image = image.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    return bytes(data) if image.save(buffer, "PNG") else b""


def fetch_online_thumbnail(url: str) -> QImage:
    """Decode and downsample list/avatar artwork on the requesting worker."""
    # The known CDN can deliver the list-sized object directly. Downloading
    # the player original here made every search tile compete for 2560px
    # artwork before its small preview could appear.
    content = fetch_online_artwork(url, target_side=320)
    if not content:
        return QImage()
    return QImage.fromData(content).scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)


def online_artwork_colors(content: bytes, fallback: ColorToken, secondary: ColorToken) -> tuple[ColorToken, ColorToken]:
    """Reuse the existing provider palette without initializing system capture."""
    from api.providers import WindowsMediaProvider

    # The inspected palette methods use only their inputs and other palette
    # methods. Construction would initialize the unrelated Windows providers.
    palette = WindowsMediaProvider.__new__(WindowsMediaProvider)
    return palette._colors_from_art(content, fallback, secondary)
