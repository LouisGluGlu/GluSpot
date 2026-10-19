"""Offline native Qt screenshots for catalog and immediate playback loading.

Run once per DPR: set QT_SCALE_FACTOR=1 or 2 before invoking this script.
All widgets stay WA_DontShowOnScreen; preview-style widgets supply in-memory
state. No SettingsManager, playback engine, account service or receiver runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from api.online_music import AccountSnapshot
from local.music_library_window import LibraryCollection, MusicLibraryWindow
from local.music_surface_window import MusicSurfaceWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expect-dpr", type=float, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    callbacks, checks, frames = [], [], []
    report = {"hidden_native_widgets": True, "offline_in_memory_fixtures": True,
              "callbacks": callbacks, "checks": checks, "frames": frames,
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                for name in ("local/music_library_window.py", "local/music_surface_window.py")}}
    original_hook = sys.excepthook
    sys.excepthook = lambda kind, value, tb: callbacks.append("".join(traceback.format_exception(kind, value, tb)))
    library = owner = surface = None

    def check(label, value, detail=None):
        checks.append({"check": label, "passed": bool(value), "detail": detail})
        if not value:
            raise AssertionError(label)

    def stop_timers(widget):
        for timer in widget.findChildren(QTimer):
            timer.stop()

    def capture(widget, name, seconds):
        with patch("time.monotonic", return_value=seconds):
            image = widget.grab().toImage()
        check(name + " is a valid frame", not image.isNull())
        path = args.output / (name + ".png")
        check(name + " saved", image.save(str(path)))
        frames.append({"name": name, "file": path.name, "pixels": [image.width(), image.height()],
                       "logical_size": [widget.width(), widget.height()], "dpr": image.devicePixelRatio()})
        return image

    try:
        library = MusicLibraryWindow()
        library.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        library.resize(960, 760)
        library.set_account(AccountSnapshot("netease", True, "offline-preview", "离线验收音乐库", state="verified"))
        library.set_catalog_loading("recommended", True)
        library.set_catalog_loading("personal", True)
        library.show()
        app.processEvents()
        stop_timers(library)
        check("actual DPR matches requested run", abs(library.devicePixelRatioF() - args.expect_dpr) < .01,
              library.devicePixelRatioF())
        check("catalog skeleton visible", library._catalog_skeleton.isVisible())
        check("catalog skeleton is mouse transparent", library._catalog_skeleton.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        check("search remains enabled during catalog load", library.search.isEnabled())
        first = capture(library, "01-library-loading-a", 100.1)
        second = capture(library, "02-library-loading-b", 100.7)
        # Compare only the actual skeleton's own pixels, independent of the
        # page background, to prove its light sweep changes with frame time.
        with patch("time.monotonic", return_value=100.1):
            skeleton_a = library._catalog_skeleton.grab().toImage()
        with patch("time.monotonic", return_value=100.7):
            skeleton_b = library._catalog_skeleton.grab().toImage()
        check("skeleton light sweep changes across time", skeleton_a != skeleton_b)
        rows = (LibraryCollection("daily", "daily", "每日推荐", ""),
                LibraryCollection("fm", "fm", "私人漫游", ""),
                LibraryCollection("playlist-1", "playlist", "午后的小确幸", "", "offline-1"))
        library.set_collections(rows)
        library.set_catalog_loading("personal", False)
        check("partial result retains recommendation skeleton", library._catalog_skeleton.isVisible())
        check("partial result preserves loaded collections", library.collections == rows)
        capture(library, "03-library-partial", 100.7)

        # Same value-only native QWidget pattern as the existing
        # preview_single_hue_background fixture; no test-module syntax/runtime
        # dependency is needed by the Python 3.8 / Qt 5 compatibility recipe.
        owner = QWidget()
        owner.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        owner._music_windowed_preference = False
        owner.media_track_id = "offline-previous-track"
        owner.track_title, owner.artist = "旧歌曲", "离线验收"
        owner.album_color, owner.secondary_color = QColor("#835d69"), QColor("#3c4158")
        owner.album_art = QImage()
        owner.media_position_seconds = 4.
        owner._estimated_media_position = lambda _: owner.media_position_seconds
        owner.timed_lyrics = [(0., "旧歌曲歌词"), (4., "不会出现在等待界面")]
        owner.lyrics = [text for _, text in owner.timed_lyrics]
        owner.media_is_playing = False
        owner.data_provider = SimpleNamespace(_owned_active=lambda: False)
        owner._music_library_back_callback = Mock()
        surface = MusicSurfaceWindow(owner)
        surface.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        surface.resize(960, 540)
        surface._fullscreen = False
        surface.show()
        app.processEvents()
        stop_timers(surface)
        # Seed regions that a previous song/page could have left behind.
        trap = QRectF(400, 250, 120, 50)
        surface._lyric_hit_rects = {0: QRectF(trap)}
        surface._queue_hit_rects = {0: QRectF(trap)}
        surface._media_button_rects = {"next": QRectF(trap)}
        surface._metadata_hit_rects = {"title": QRectF(trap)}
        surface._lyric_clip_rect = surface._queue_view_rect = QRectF(trap)
        surface._button_rects.update({key: QRectF(trap) for key in ("favorite", "playlist_add", "personal_dj")})
        with patch("time.monotonic", return_value=100.1):
            surface.set_library_loading("午后的小确幸", QImage())
        with patch.object(surface, "_paint_song_collection_control") as collection_paint, \
                patch.object(surface, "_paint_personal_dj_control") as dj_paint:
            capture(surface, "04-playback-pending-cover", 100.2)
            collection_paint.assert_not_called()
            dj_paint.assert_not_called()
        check("no previous lyric hits", not surface._lyric_hit_rects)
        check("no previous queue hits", not surface._queue_hit_rects)
        check("no previous transport hits", not surface._media_button_rects)
        check("no previous metadata hits", not surface._metadata_hit_rects)
        check("no previous lyric or queue scrolling regions", surface._lyric_clip_rect.isEmpty() and surface._queue_view_rect.isEmpty())
        check("no previous favorites or DJ hits", not {"favorite", "playlist_add", "personal_dj"}.intersection(surface._button_rects))
        check("return to library available before track exists", surface._library_back_available())
        check("library return has a current hit target", "library" in surface._button_rects)
        check("library return invokes existing callback", surface.return_to_library())
        check("library callback invoked once", owner._music_library_back_callback.call_count == 1)
        cover = QImage(320, 320, QImage.Format.Format_RGB32)
        with QPainter(cover) as painter:
            gradient = QLinearGradient(0, 0, 320, 320)
            gradient.setColorAt(0, QColor("#c3845e")); gradient.setColorAt(1, QColor("#4b507e"))
            painter.fillRect(cover.rect(), gradient)
        with patch("time.monotonic", return_value=100.3):
            surface.set_library_loading("午后的小确幸", cover)
        capture(surface, "05-playback-pending-artwork", 100.7)
        check("selection title retained", surface._library_loading[0] == "午后的小确幸")
        check("selection cover handed to navigation", not surface.navigation_cover_state()[1].isNull())
        check("no Qt callback exceptions", not callbacks)
    except Exception:
        report["failure"] = traceback.format_exc()
        raise
    finally:
        if surface is not None:
            surface.shutdown(); surface.deleteLater()
        if owner is not None:
            owner.close(); owner.deleteLater()
        if library is not None:
            library.close(); library.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        report["passed"] = not report.get("failure") and not callbacks
        (args.output / "evidence.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        sys.excepthook = original_hook
    print(json.dumps({"passed": report["passed"], "checks": len(checks), "frames": len(frames),
                      "dpr": args.expect_dpr, "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
