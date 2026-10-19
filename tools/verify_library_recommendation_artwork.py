"""Offline native-Qt verification of real daily/FM cover acquisition.

Uses the production service, controller, workers, HTTP decoder and cover widgets.
All accounts/images are labelled synthetic fixtures; only 127.0.0.1 is contacted.
No settings, account files, audio device or running user application is touched.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.online_music import OnlineMusicError, OnlineMusicService
from api.online_music_artwork import _safe_artwork_url
from local.music_library_controller import MusicLibraryController
from local.island_stack_visual import IslandStackVisual


class MemoryStore:
    def __init__(self):
        self.records = {"netease": {"cookie": {"MUSIC_U": "synthetic-fixture"}},
                        "qq": {"token": "synthetic-fixture", "sessions": []}}

    def load(self, source): return self.records.get(source)
    def save(self, source, value): self.records[source] = value
    def delete(self, source): self.records.pop(source, None)


class Backend:
    def __init__(self, base):
        self.base = base
        self.calls = []
        self.release = threading.Event()

    def available(self, _source): return True, ""
    def close(self): self.release.set()

    def request(self, source, operation, params, credential=None):
        self.calls.append(operation)
        if operation == "account_status":
            return {"data": {"logged_in": True, "user_id": "fixture", "nickname": "离线封面验证 · UI FIXTURE"}}
        if operation in {"daily_recommendations", "personal_fm"}:
            if not self.release.wait(5):
                raise OnlineMusicError("timeout")
            if operation == "personal_fm" and self.calls.count(operation) == 1:
                raise OnlineMusicError("network", message="synthetic retry fixture")
            kind = "daily" if operation == "daily_recommendations" else "fm"
            song = {"id": 1 if kind == "daily" else 2, "name": kind + " FIXTURE",
                    "al": {"picUrl": self.base + "/" + kind + ".png"}}
            return {"data": {"data": {"dailySongs": [song]}} if kind == "daily" else {"data": [song]}}
        if operation == "recommended_playlists":
            return {"data": {"items": [], "next_offset": 0, "has_more": False}}
        return {"data": {"playlist": []}}


class Player(QObject):
    changed = Signal()
    statusChanged = Signal(str)
    track = None


class Settings:
    def __init__(self): self.values = {"v2_library_expanded": True}
    def get(self, key, default=None): return self.values.get(key, default)
    def set(self, key, value): self.values[key] = value
    def save_now(self): pass


class Island:
    _music_surface_active = False
    def hide(self): pass


class Entry:
    window = None


def fixture_image(kind):
    image = QImage(256, 256, QImage.Format.Format_RGB32)
    image.fill(QColor("#bd6b43" if kind == "daily" else "#346b68"))
    with QPainter(image) as painter:
        painter.setPen(QColor("#fff8ed"))
        painter.setFont(QFont(IslandStackVisual.WAVE_FONT_FAMILY_BOLD, 22))
        painter.drawText(18, 70, "DAILY" if kind == "daily" else "RADIO")
        painter.setFont(QFont(IslandStackVisual.WAVE_FONT_FAMILY_BOLD, 13))
        painter.drawText(18, 110, "UI FIXTURE")
        painter.drawEllipse(86, 144, 84, 84)
    return image


def png(image):
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def wait(app, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    return bool(predicate())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    IslandStackVisual.load_embedded_lyric_fonts()
    app.setFont(QFont(IslandStackVisual.WAVE_FONT_FAMILY_BOLD, 10))
    images = {kind: fixture_image(kind) for kind in ("daily", "fm")}
    payloads = {"/" + kind + ".png": png(image) for kind, image in images.items()}
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/daily.png" and requests.count(self.path) == 1:
                self.send_error(503)
                return
            content = payloads.get(self.path)
            if content is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    backend = Backend(f"http://127.0.0.1:{server.server_port}")
    fixture_urls = {backend.base + suffix for suffix in payloads}
    # Production intentionally admits only ports 80/443. Admit this fixture's
    # exact ephemeral loopback targets inside this process only, preserving all
    # real HTTP, status, byte-budget, decoder and thumbnail behavior.
    admission = patch("api.online_music_artwork._safe_artwork_url", side_effect=lambda value:
        value if value in fixture_urls else _safe_artwork_url(value))
    admission.start()
    service = OnlineMusicService(backend, MemoryStore())
    controller = MusicLibraryController(Settings(), service, Player(), Island(), Entry())
    window = controller.window
    window.resize(920, 610)
    checks = []
    def check(label, result):
        checks.append({"check": label, "passed": bool(result)})
        if not result:
            raise AssertionError(label)
    try:
        controller.open_source("netease")
        check("account and normal catalog appear before recommendation artwork", wait(app,
            lambda: window._account.logged_in and not controller._catalog_pending))
        window.grab().save(str(options.output / "covers-pending.png"))
        check("pending cover requests do not block the library", window.isVisible() and not window._loading)
        backend.release.set()
        check("both actual decoded covers complete after independent retries", wait(app, lambda:
            all(not window._artwork.get(kind, QImage()).isNull() for kind in images)))
        check("metadata recovery is bounded", backend.calls.count("personal_fm") == 2)
        check("image recovery retries the real source", requests.count("/daily.png") == 2)
        check("daily metadata was not duplicated", backend.calls.count("daily_recommendations") == 1)
        window.set_library_expanded(True, animate=False)
        app.processEvents()
        window.grab().save(str(options.output / "covers-loaded.png"))
        for shelf in window._shelves:
            for row, cover, _label in shelf.tiles:
                if row.key in images:
                    check(row.key + " widget owns the decoded image", not cover.image.isNull())
                    rendered = cover.grab().toImage()
                    center = rendered.pixelColor(rendered.width() // 2, rendered.height() // 2)
                    expected = images[row.key].pixelColor(128, 128)
                    check(row.key + " actual rendered pixels match artwork", center == expected)
                    rendered.save(str(options.output / (row.key + "-cover.png")))
        before = tuple(backend.calls)
        check("daily playback receives the previewed songs", service.daily_recommendations()[0].song_id == "1")
        check("radio playback receives the previewed songs", service.personal_fm()[0].song_id == "2")
        check("playback does not refetch or skip either preview batch", tuple(backend.calls) == before)
        check("consumed previews release the song references", not service._recommendation_previews)
        epoch = controller._epoch
        controller.open_source("qq")
        check("alternate source opens independently", wait(app, lambda:
            window._account.source_id == "qq" and window._account.logged_in and not controller._catalog_pending))
        controller._completed(("recommendation_cover", epoch, "fm"), backend.base + "/fm.png", None)
        controller._art_completed(("art", epoch, "fm", backend.base + "/fm.png"), images["fm"], None)
        check("old source metadata and decoded image cannot enter the new account", not controller._collections
              and "fm" not in window._artwork and not controller._recommendation_art_retry_at)
        window.grab().save(str(options.output / "source-fenced.png"))
    finally:
        backend.release.set()
        controller.close()
        service.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
        admission.stop()
        app.processEvents()
    report = {"passed": all(item["passed"] for item in checks), "checks": checks,
              "device_pixel_ratio": window.devicePixelRatioF(), "http_requests": requests,
              "service_calls": backend.calls, "scope": "offline synthetic account; localhost images; no audio or user application",
              "fixture_adaptation": "exact loopback URL port admission only; production HTTP and image decoding unchanged"}
    path = options.output / "evidence.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks": len(checks), "passed": report["passed"], "report": str(path.resolve())}))


if __name__ == "__main__":
    main()
