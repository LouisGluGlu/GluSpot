"""Bounded source-owned artwork requests; callers run these off the GUI thread.

No request carries an account cookie, and failures never expose signed URLs.
"""
from __future__ import annotations

import time
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


def _decoded_artwork_is_bounded(content: bytes) -> bool:
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
        return False
    image = reader.read()
    return not image.isNull() and image.width() == width and image.height() == height


def fetch_online_artwork(url: str) -> bytes:
    """Fetch a validated raster within the byte, decode and request budgets.

    Return empty bytes on unavailable/invalid artwork. Unknown and signed URLs
    retain their exact request target; only the existing NetEase CDN helper may
    request larger pixels from the same known image object.
    """
    value = _safe_artwork_url(url)
    if not value:
        return b""
    value = normalize_artwork_url(value)
    deadline = time.monotonic() + ONLINE_ARTWORK_BUDGET_SECONDS
    try:
        request = Request(value, headers={"User-Agent": ARTWORK_USER_AGENT})
        opener = build_opener(_ArtworkRedirectHandler())
        with opener.open(request, timeout=3.0) as response:
            if not _safe_artwork_url(response.geturl()):
                return b""
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > ONLINE_ARTWORK_MAX_BYTES:
                return b""
            content = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    return b""
                chunk = response.read1(min(64 * 1024, ONLINE_ARTWORK_MAX_BYTES + 1 - len(content)))
                if time.monotonic() >= deadline:
                    return b""
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > ONLINE_ARTWORK_MAX_BYTES:
                    return b""
        result = bytes(content)
        return result if result and _decoded_artwork_is_bounded(result) else b""
    except Exception:
        # Do not propagate arbitrary HTTP/decoder exception text: it can include
        # the complete signed request target or remote response contents.
        return b""


def fetch_online_thumbnail(url: str) -> QImage:
    """Decode and downsample list/avatar artwork on the requesting worker."""
    content = fetch_online_artwork(url)
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
