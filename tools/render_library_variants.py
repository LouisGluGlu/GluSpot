"""Render real library windows with local fixtures and private temporary settings.

No provider, network client, player or account controller is constructed.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("/workspace/gluspot/wt-library-variants"))
    parser.add_argument("--output", type=Path, default=Path("/workspace/gluspot/handoff/library-variants"))
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = "1"
    with tempfile.TemporaryDirectory(prefix="gluspot-library-renders-") as private:
        for key in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                    "XDG_RUNTIME_DIR", "GLUSPOT_LYRICS_CACHE_DIR", "GLUSPOT_ALBUM_ART_CACHE_DIR"):
            folder = Path(private, key)
            folder.mkdir(mode=0o700)
            os.environ[key] = str(folder)
        from PySide6.QtCore import QByteArray, QBuffer, QCoreApplication, QEvent, QIODevice, QRect, QRectF, Qt
        from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath
        from PySide6.QtWidgets import QApplication
        from api.models import ColorToken, TrackData
        from api.online_music import AccountSnapshot
        from local.music_library_window import LibraryCollection, MusicLibraryWindow

        app = QApplication.instance() or QApplication([])
        app.setFont(QFont("Noto Sans CJK SC", 10))
        shots = args.output / "shots"
        shots.mkdir(parents=True, exist_ok=True)
        palettes = (("#e7ad79", "#6d4654"), ("#bac5e7", "#4f5f83"), ("#8acbb8", "#335569"),
                    ("#e69c9b", "#6f425f"), ("#d1c89e", "#5d725e"), ("#a29dc5", "#3c4b78"),
                    ("#c4b497", "#5c585b"), ("#95b9c9", "#3f5267"))

        def artwork(index, title):
            image = QImage(320, 320, QImage.Format.Format_RGB32)
            a, b = palettes[index % len(palettes)]
            with QPainter(image) as p:
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                gradient = QLinearGradient(0, 0, 320, 320)
                gradient.setColorAt(0, QColor(a))
                gradient.setColorAt(1, QColor(b))
                p.fillRect(image.rect(), gradient)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 244, 225, 58))
                p.drawEllipse(QRectF(155, -70, 230, 230))
                p.setBrush(QColor(16, 28, 46, 44))
                p.drawEllipse(QRectF(-60, 180, 420, 250))
                p.setPen(QColor(255, 255, 255, 80))
                for y in range(56, 260, 16):
                    path = QPainterPath()
                    path.moveTo(30, y)
                    path.cubicTo(92, y - 70, 178, y + 70, 290, y - 10)
                    p.drawPath(path)
                font = QFont("Noto Sans CJK SC")
                font.setPixelSize(27)
                font.setBold(True)
                p.setFont(font)
                p.setPen(QColor("#fff7ec"))
                p.drawText(QRectF(25, 235, 270, 50), Qt.AlignmentFlag.AlignVCenter, title)
                font.setPixelSize(11)
                font.setBold(False)
                p.setFont(font)
                p.drawText(QRectF(27, 282, 260, 20), "G L U S P O T   /   M U S I C")
            return image

        names = (("daily", "daily", "每日推荐", "for_you"), ("fm", "fm", "私人漫游", "for_you"),
                 ("r1", "playlist", "日落之前", "recommended"), ("r2", "playlist", "慢慢喜欢你", "recommended"),
                 ("r3", "playlist", "城市轻节奏", "recommended"), ("r4", "playlist", "雨天爵士", "recommended"),
                 ("p1", "playlist", "我的心动收藏", "personal"), ("p2", "playlist", "深夜，耳机与我", "personal"))
        catalog = tuple(LibraryCollection(key, kind, title, f"fixture://{key}", key if kind == "playlist" else "", group=group)
                        for key, kind, title, group in names)
        art = {item.key: artwork(index, item.title) for index, item in enumerate(catalog)}
        cover = artwork(3, "TRIPPIN")
        buffer_data = QByteArray()
        buffer = QBuffer(buffer_data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        cover.save(buffer, "PNG")
        buffer.close()
        track = TrackData("Trippin", "Kara Marni", (), ColorToken(182, 110, 118), ColorToken(72, 89, 125), (),
                          album_art_bytes=bytes(buffer_data), duration_seconds=172, position_seconds=96,
                          track_id="fixture:trippin", is_playing=True)
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1920, 1080),
                                 geometry=lambda: QRect(0, 0, 1920, 1080))
        sizes = ((960, 540), (433, 550), (391, 508))
        states = (("loaded", "已加载"), ("loading", "加载中"), ("empty", "空音乐库"))
        with patch.object(QGuiApplication, "screenAt", return_value=screen):
            for letter, variant in zip("ABC", ("sidebar", "listen", "compact")):
                images = {}
                for width, height in sizes:
                    for state, _label in states:
                        with patch.dict(os.environ, {"GLUSPOT_LIBRARY_VARIANT": variant}):
                            window = MusicLibraryWindow()
                        window.set_background_context({"v2_music_flow_intensity": 0.}.get, lambda *args: None)
                        window.resize(width, height)
                        window.set_account(AccountSnapshot("netease", True, "fixture", "LouisHerm", state="verified"))
                        if state == "loaded":
                            window.set_collections(catalog)
                            for key, image in art.items():
                                window.set_artwork(key, image)
                            window.set_now_playing(track)
                        elif state == "loading":
                            window.set_loading(True, "正在整理你的音乐…")
                            window.set_catalog_loading("recommended", True)
                        else:
                            window.set_account(AccountSnapshot("netease", False))
                            window.set_collections(())
                        window.show()
                        app.processEvents()
                        window._layout()
                        window.search.settle()
                        window._artwork_skeleton_timer.stop()
                        window._background_timer.stop()
                        window._header_motion.stop()
                        image = window.grab().toImage()
                        assert image.size() == window.size() and image.size().toTuple() == (width, height)
                        path = shots / f"{variant}-{width}x{height}-{state}.png"
                        if not image.save(str(path)):
                            raise RuntimeError(f"Cannot save {path}")
                        images[width, state] = image
                        window.close()
                        window.deleteLater()
                        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                        app.processEvents()
                row_height = max(h for _w, h in sizes) + 46
                sheet = QImage(sum(w for w, _h in sizes) + 64, 16 + len(states) * row_height,
                               QImage.Format.Format_RGB32)
                sheet.fill(QColor("#11151d"))
                with QPainter(sheet) as p:
                    font = QFont("Noto Sans CJK SC")
                    font.setPixelSize(13)
                    p.setFont(font)
                    p.setPen(QColor("#e6edf6"))
                    for row, (state, label) in enumerate(states):
                        for column, (width, height) in enumerate(sizes):
                            x = 16 + sum(w + 16 for w, _h in sizes[:column])
                            y = 16 + row * row_height
                            p.drawText(QRectF(x, y, width, 24), Qt.AlignmentFlag.AlignVCenter,
                                       f"{letter} · {variant} · {width} × {height} · {label}")
                            p.drawImage(x, y + 28, images[width, state])
                path = args.output / f"variant-{letter}.png"
                if not sheet.save(str(path)) or path.stat().st_size < 50_000:
                    raise RuntimeError(f"Missing or unexpectedly small contact sheet: {path}")
                print(f"{path} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
