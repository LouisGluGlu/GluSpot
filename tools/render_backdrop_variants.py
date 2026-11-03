"""Offline backdrop contact sheets from real library A / login A2 windows.

Only local artwork and model snapshots are used; no provider/controller is
created. All settings/cache directories are private and removed on exit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("/workspace/gluspot/handoff/backdrop-variants"))
    parser.add_argument("--variants", nargs="+", choices=("classic", "flow", "frosted", "noir"),
                        default=("classic", "flow", "frosted", "noir"))
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(args.repo.resolve()))
    os.environ.update(QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR="1",
                      GLUSPOT_LIBRARY_VARIANT="sidebar", GLUSPOT_LOGIN_VARIANT="sidebar")
    with tempfile.TemporaryDirectory(prefix="gluspot-backdrop-renders-") as private:
        for key in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                    "XDG_RUNTIME_DIR", "GLUSPOT_LYRICS_CACHE_DIR", "GLUSPOT_ALBUM_ART_CACHE_DIR"):
            folder = Path(private, key)
            folder.mkdir(mode=0o700)
            os.environ[key] = str(folder)
        from PySide6.QtCore import QByteArray, QBuffer, QCoreApplication, QEvent, QIODevice, QRect, QRectF, QSize, Qt
        from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
        from PySide6.QtWidgets import QApplication
        from api.models import ColorToken, TrackData
        from api.online_music import AccountSnapshot
        from local.music_library_window import LibraryCollection, MusicLibraryWindow
        from local.music_surface_background import AlbumFlowBackgroundRenderer
        from local.music_surface_palette import artwork_surface_palette
        from local.playback_entry_window import ENTRY_SOURCES, PlaybackEntryWindow

        app = QApplication.instance() or QApplication([])
        app.setFont(QFont("Noto Sans CJK SC", 10))
        shots = args.output / "shots"
        shots.mkdir(parents=True, exist_ok=True)
        account = AccountSnapshot("netease", True, "fixture", "LouisHerm", state="verified")
        specs = (("daily", "daily", "每日推荐", "for_you"), ("fm", "fm", "私人漫游", "for_you"),
                 ("r1", "playlist", "日落之前", "recommended"), ("r2", "playlist", "城市轻节奏", "recommended"),
                 ("p1", "playlist", "我的心动收藏", "personal"), ("p2", "playlist", "深夜，耳机与我", "personal"))
        catalog = tuple(LibraryCollection(key, kind, title, f"fixture://{key}",
                                         key if kind == "playlist" else "", group=group)
                        for key, kind, title, group in specs)
        palettes = (("warm", ("#3b2419", "#ae743e", "#edbd76")),
                    ("red", ("#3e0712", "#dd173e", "#ff5c56")))
        sizes = ((960, 540), (433, 550))
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1920, 1080),
                                 geometry=lambda: QRect(0, 0, 1920, 1080))

        def artwork(title, colors):
            # A tracked photo retains image detail under controlled warm/red
            # duotones, so the sheets compare extracted pigments, not fake cards.
            import numpy as np
            image = QImage(str(args.repo / "_demos/narrow3/covers/sunset_city.jpg"))
            if image.isNull():
                raise RuntimeError("Missing local sunset photo fixture")
            image = image.scaled(320, 320).convertToFormat(QImage.Format.Format_RGBA8888)
            rgb = np.frombuffer(image.constBits(), np.uint8).reshape(320, 320, 4)[..., :3]
            light = (rgb.astype(float) @ np.array((.2126, .7152, .0722))) / 255.
            stops = np.array([QColor(color).getRgb()[:3] for color in colors], float)
            fraction = np.minimum(light * 2, 1.)[..., None]
            low = stops[0] * (1 - fraction) + stops[1] * fraction
            fraction = np.maximum(light * 2 - 1, 0.)[..., None]
            tinted = low * (1 - fraction) + stops[2] * fraction
            output = np.empty((320, 320, 4), np.uint8)
            output[..., :3] = tinted.astype(np.uint8)
            output[..., 3] = 255
            return QImage(output.data, 320, 320, 1280, QImage.Format.Format_RGBA8888).copy()

        def dispose(window):
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()

        manifest = {}
        with patch.object(QGuiApplication, "screenAt", return_value=screen):
            for variant in args.variants:
                os.environ["GLUSPOT_BACKDROP_VARIANT"] = variant
                frames = []
                for palette_name, colors in palettes:
                    cover = artwork("TRIPPIN", colors)
                    primary, secondary = QColor(colors[1]), QColor(colors[0])
                    palette = artwork_surface_palette(cover, primary, secondary)
                    pigments = palette.ambient_colors or (palette.primary, palette.secondary)
                    # Record bounded raw pixels too, for pre-change classic parity.
                    for width, height in sizes:
                        raw = AlbumFlowBackgroundRenderer().render(QSize(width, height), pigments, 0.,
                                                                  intensity=0., weights=palette.ambient_weights)
                        manifest[f"{variant}-{palette_name}-{width}-raw"] = hashlib.sha256(bytes(raw.constBits())).hexdigest()
                    data = QByteArray()
                    buffer = QBuffer(data)
                    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                    cover.save(buffer, "PNG")
                    buffer.close()
                    track = TrackData("Trippin", "Kara Marni", (), ColorToken(*primary.getRgb()[:3]),
                                      ColorToken(*secondary.getRgb()[:3]), (), album_art_bytes=bytes(data),
                                      duration_seconds=172, position_seconds=96, track_id="fixture:trippin", is_playing=True)
                    row = []
                    for page in ("library", "login"):
                        for width, height in sizes:
                            if page == "library":
                                window = MusicLibraryWindow()
                                window.set_background_context({"v2_music_flow_intensity": 0.}.get,
                                                              lambda name, prepare: prepare())
                                window.resize(width, height)
                                window.set_account(account)
                                window.set_collections(catalog)
                                for item in catalog:
                                    window.set_artwork(item.key, artwork(item.title, colors))
                                window.set_now_playing(track)
                            else:
                                window = PlaybackEntryWindow()
                                # Exercise the same prepared-palette input wired by the controller.
                                window.set_background_palette(palette)
                                window.show_sources()
                                window.set_sources(tuple(SimpleNamespace(source_id=s.source_id, available=s.login_supported)
                                                         for s in ENTRY_SOURCES))
                                window.select_source("netease")
                                window._expansion.stop()
                                window._expanded = 1.
                                window.setMinimumSize(0, 0)
                                window.resize(width, height)
                                window.set_account(account)
                                # Leave the account avatar on its platform fallback.
                            window.show()
                            app.processEvents()
                            if page == "library":
                                window._layout()
                                window.search.settle()
                                window._artwork_skeleton_timer.stop()
                                window._background_timer.stop()
                                window._header_motion.stop()
                                window._variant_view.status_timer.stop()
                                window._variant_view.advance_status_pulse(window._variant_view.status_started_at + 1.)
                            else:
                                window._layout_children()
                                window._status_timer.stop()
                                window._advance_status_pulse(window._status_pulse_started_at + 1.)
                            image = window.grab().toImage()
                            assert image.size().toTuple() == (width, height)
                            if page == "login":
                                assert window.page == "sources" and window.login_state == "confirmed"
                            name = f"{variant}-{palette_name}-{page}-{width}x{height}"
                            if not image.save(str(shots / f"{name}.png")):
                                raise RuntimeError(name)
                            manifest[name] = hashlib.sha256(bytes(image.constBits())).hexdigest()
                            row.append(image)
                            dispose(window)
                    frames.append(row)
                sheet = QImage(2 * (960 + 433) + 5 * 16, 2 * (550 + 44) + 16, QImage.Format.Format_RGB32)
                sheet.fill(QColor("#10131b"))
                with QPainter(sheet) as p:
                    font = QFont("Noto Sans CJK SC")
                    font.setPixelSize(14)
                    p.setFont(font)
                    p.setPen(QColor("#e6edf6"))
                    for row_index, ((palette_name, _colors), row) in enumerate(zip(palettes, frames)):
                        x, y = 16, 16 + row_index * 594
                        for index, frame in enumerate(row):
                            page = "Library A-refined · loaded" if index < 2 else "Login A2 · connected"
                            p.drawText(QRectF(x, y, frame.width(), 28), Qt.AlignmentFlag.AlignVCenter,
                                       f"{variant} / {palette_name} · {page} · {frame.width()} × {frame.height()}")
                            p.drawImage(x, y + 28, frame)
                            x += frame.width() + 16
                filename = {"classic": "backdrop-baseline.png", "flow": "backdrop-1.png",
                            "frosted": "backdrop-2.png", "noir": "backdrop-3.png"}[variant]
                path = args.output / filename
                if not sheet.save(str(path)) or path.stat().st_size < 50_000:
                    raise RuntimeError(f"Missing or unexpectedly small contact sheet: {path}")
                print(f"{path} ({path.stat().st_size:,} bytes)")
        (args.output / "pixels.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
