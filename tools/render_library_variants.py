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
    parser.add_argument("--refined", action="store_true", help="Render only the A-refined review sheet")
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = "1"
    if args.refined:
        os.environ["GLUSPOT_BACKDROP_VARIANT"] = "classic"
    with tempfile.TemporaryDirectory(prefix="gluspot-library-renders-") as private:
        for key in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                    "XDG_RUNTIME_DIR", "GLUSPOT_LYRICS_CACHE_DIR", "GLUSPOT_ALBUM_ART_CACHE_DIR"):
            folder = Path(private, key)
            folder.mkdir(mode=0o700)
            os.environ[key] = str(folder)
        from PySide6.QtCore import QByteArray, QBuffer, QCoreApplication, QEvent, QIODevice, QRect, QRectF, Qt
        from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
        from PySide6.QtWidgets import QApplication
        from api.models import ColorToken, TrackData
        from api.online_music import AccountSnapshot
        from local.music_library_window import LibraryCollection, MusicLibraryWindow

        app = QApplication.instance() or QApplication([])
        app.setFont(QFont("Noto Sans CJK SC", 10))
        shots = args.output / "shots"
        shots.mkdir(parents=True, exist_ok=True)
        # Existing tracked photo/music demo assets; no fabricated cover painter.
        cover_paths = tuple(args.repo / "_demos/narrow3/covers" / name for name in
                            ("sunset_city.jpg", "night_sea.jpg", "pink_forest.jpg", "clip.jpg"))

        def artwork(index, title):
            image = QImage(str(cover_paths[index % len(cover_paths)]))
            if image.isNull():
                raise RuntimeError(f"Missing local fixture cover: {cover_paths[index % len(cover_paths)]}")
            return image

        names = (("daily", "daily", "每日推荐", "for_you"), ("fm", "fm", "私人漫游", "for_you"),
                 ("r1", "playlist", "日落之前", "recommended"), ("r2", "playlist", "慢慢喜欢你", "recommended"),
                 ("r3", "playlist", "城市轻节奏", "recommended"), ("r4", "playlist", "雨天爵士", "recommended"),
                 ("p1", "playlist", "我的心动收藏", "personal"), ("p2", "playlist", "深夜，耳机与我", "personal"))
        catalog = tuple(LibraryCollection(key, kind, title, f"fixture://{key}", key if kind == "playlist" else "", group=group)
                        for key, kind, title, group in names)
        art = {item.key: artwork(index, item.title) for index, item in enumerate(catalog)}
        cover = artwork(1, "TRIPPIN")
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
            variants = (("A", "sidebar"),) if args.refined else zip("ABC", ("sidebar", "listen", "compact"))
            for letter, variant in variants:
                images = {}
                for width, height in sizes:
                    for state, _label in states:
                        with patch.dict(os.environ, {"GLUSPOT_LIBRARY_VARIANT": variant}):
                            window = MusicLibraryWindow()
                        window.set_background_context({"v2_music_flow_intensity": 0.}.get, lambda name, prepare: prepare())
                        window.resize(width, height)
                        window.set_account(AccountSnapshot("netease", True, "fixture", "LouisHerm", state="verified"))
                        window.set_now_playing(track)
                        if state == "loaded":
                            window.set_collections(catalog)
                            for key, image in art.items():
                                window.set_artwork(key, image)
                            if args.refined:
                                window._variant_view.select_section({960: "for_you", 433: "recommended", 391: "personal"}[width])
                        elif state == "loading":
                            window.set_loading(True)
                            window.set_catalog_loading("for_you" if args.refined else "recommended", True)
                        else:
                            window.set_collections(())
                        window.show()
                        app.processEvents()
                        window._layout()
                        window.search.settle()
                        window._artwork_skeleton_timer.stop()
                        window._background_timer.stop()
                        window._header_motion.stop()
                        window._variant_view.status_timer.stop()
                        window._variant_view.advance_status_pulse(window._variant_view.status_started_at + 1.)
                        image = window.grab().toImage()
                        assert image.size() == window.size() and image.size().toTuple() == (width, height)
                        path = shots / f"{variant}{'-refined' if args.refined else ''}-{width}x{height}-{state}.png"
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
                                       f"{letter}{'-refined' if args.refined else ''} · {width} × {height} · {label}")
                            p.drawImage(x, y + 28, images[width, state])
                path = args.output / f"variant-{letter}{'-refined' if args.refined else ''}.png"
                if not sheet.save(str(path)) or path.stat().st_size < 50_000:
                    raise RuntimeError(f"Missing or unexpectedly small contact sheet: {path}")
                print(f"{path} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
