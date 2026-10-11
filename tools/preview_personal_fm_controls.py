"""Native Qt FM/repeat screenshots using only offline surface fixtures."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from local.music_source_icons import _rendered_icon, _svg_renderer, paint_music_source_icon
from local.music_surface_window import MusicSurfaceWindow
from tests.test_owned_playback_surface import _Provider


def native_image(width, height, dpr):
    image = QImage(math.ceil(width * dpr), math.ceil(height * dpr),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    image.fill(Qt.GlobalColor.transparent)
    return image


def physical_rect(rect, dpr):
    return QRectF(rect.x() * dpr, rect.y() * dpr,
                  rect.width() * dpr, rect.height() * dpr).toAlignedRect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/personal-fm-preview"))
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    if app.platformName() != "windows":
        raise RuntimeError("Run this preview with the native Windows Qt platform.")
    proof = {"platform": app.platformName(), "offline_fixture_only": True, "themes": []}
    for theme, source in (("dark", "#171d27"), ("light", "#eee9d9")):
        owner = QWidget()
        provider = _Provider()
        provider.fm = True
        owner.data_provider = provider
        owner._music_library_back_callback = lambda: None
        owner.album_color = owner.secondary_color = QColor(source)
        cover = QImage(256, 256, QImage.Format.Format_RGB32)
        cover.fill(QColor(source))
        owner.album_art = cover
        owner.track_title, owner.artist, owner.album_title = "私人漫游 · OFFLINE FIXTURE", "布局检查", "离线封面"
        owner.media_track_id, owner.media_position_seconds, owner.media_is_playing = "fm-preview", 5.0, False
        owner.music_flow_intensity = 0.0
        owner.timed_lyrics = [(0, "私人漫游：持续获取下一首"), (4, "仅检查实际界面与图标颜色"), (8, "无账户、网络或声音设备操作")]
        owner.lyrics = [line for _, line in owner.timed_lyrics]
        owner._estimated_media_position = lambda _: owner.media_position_seconds
        window = MusicSurfaceWindow(owner)
        window.resize(960, 540)
        window._fullscreen = True
        window.show()
        window._timer.stop()
        window._audio_output_status_timer.stop()
        window._sync_audio_output_status(settle=True)
        app.processEvents()
        dpr = window.devicePixelRatioF()
        rows = []
        for label, mode, instant in (("accent", "sequential", 0), ("blend", "sequential", 1),
                                     ("neutral", "sequential", 2), ("repeat", "repeat_one", 1)):
            provider.mode = mode
            window._sync_owned_playback_mode()
            window._reset_control_idle(now=instant)
            window._control_layer_next_prewarm_at = math.inf
            with patch("local.music_surface_window.time.monotonic", return_value=instant), \
                    patch("local.music_surface_window.paint_music_source_icon", wraps=paint_music_source_icon) as painted:
                frame = window.grab().toImage()
            asset = "personal_fm" if mode == "sequential" else mode
            call = next(call for call in reversed(painted.call_args_list) if call.args[2] == asset)
            tint = QColor(call.kwargs["tint"])
            path = options.output / f"{theme}-{label}.png"
            if not frame.save(str(path)):
                raise RuntimeError(f"Could not save {path}")
            rect = window._button_rects["playback_mode"].adjusted(-8, -8, 8, 8)
            crop = frame.copy(physical_rect(rect, dpr))
            crop_path = options.output / f"{theme}-{label}-control.png"
            crop.save(str(crop_path))
            rows.append({"label": label, "mode": mode, "instant": instant,
                         "tint": tint.name(QColor.NameFormat.HexArgb),
                         "path": str(path.resolve()), "crop_path": str(crop_path.resolve()),
                         "crop": crop})
        sheet = native_image(560, 192, dpr)
        sheet.fill(QColor(source))
        with QPainter(sheet) as painter:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            font = QFont("Segoe UI", 10)
            painter.setFont(font)
            painter.setPen(QColor("#222222" if theme == "light" else "#eeeeee"))
            for index, row in enumerate(rows):
                x = index * 140
                painter.drawImage(QRectF(x + 18, 20, 104, 104), row["crop"])
                painter.drawText(QRectF(x, 132, 140, 23), Qt.AlignmentFlag.AlignCenter,
                                 f"{row['label']} · {row['instant']}s")
                painter.drawText(QRectF(x, 156, 140, 22), Qt.AlignmentFlag.AlignCenter, row["tint"])
        sheet_path = options.output / f"{theme}-comparison.png"
        sheet.save(str(sheet_path))
        provider.mode = "sequential"
        window._sync_owned_playback_mode()
        window._reset_control_idle(now=100)
        # Visible tint frames must reuse the same native-size SVG mask and
        # allocate no further control/blur cache entries after warmup.
        before = _rendered_icon.cache_info()
        layer_entries = len(window._control_layer_cache)
        layer_bytes = window._control_layer_cache_bytes
        def paint_controls_at(instant):
            image = native_image(window.width(), 100, dpr)
            with QPainter(image) as painter, patch("local.music_surface_window.time.monotonic", return_value=instant):
                window._paint_window_controls(painter, QRectF(window.rect()))
        for index in range(120):
            paint_controls_at(100 + index / 60)
        after = _rendered_icon.cache_info()
        if after.misses != before.misses or len(window._control_layer_cache) != layer_entries:
            raise AssertionError("FM tint frames added SVG or control cache entries after warmup.")
        if window._control_layer_cache_bytes != layer_bytes:
            raise AssertionError("FM tint frames changed control-layer raster bytes.")
        # Exercise the real final-two-second prewarm gate, then confirm that
        # fade frames keep one stable tint/cache key at the actual window DPR.
        window._reset_control_idle(now=200)
        window._control_layer_next_prewarm_at = 0
        idle = window.FULLSCREEN_CONTROL_IDLE_SECONDS
        for index in range(120):
            paint_controls_at(200 + idle - 1.9 + index / 60)
        fade_entries = len(window._control_layer_cache)
        fade_bytes = window._control_layer_cache_bytes
        for offset in (.13, .2, .27):
            paint_controls_at(200 + idle + offset)
        if len(window._control_layer_cache) != fade_entries or window._control_layer_cache_bytes != fade_bytes:
            raise AssertionError("Frozen FM fade tint added control-layer cache entries.")
        if fade_entries > 48 or fade_bytes > window.CONTROL_LAYER_CACHE_MAX_BYTES:
            raise AssertionError("FM prewarm exceeded the existing control-cache budget.")
        if provider.calls:
            raise AssertionError("Preview issued a playback-mode action.")
        proof["themes"].append({"theme": theme, "source_color": source, "dpr": dpr,
            "comparison": str(sheet_path.resolve()),
            "frames": [{key: value for key, value in row.items() if key != "crop"} for row in rows],
            "tint_frames": 120, "rendered_cache_miss_delta": after.misses - before.misses,
            "rendered_cache_entries": after.currsize, "rendered_cache_limit": after.maxsize,
            "svg_cache_entries": _svg_renderer.cache_info().currsize,
            "svg_cache_limit": _svg_renderer.cache_info().maxsize,
            "control_cache_entries": layer_entries, "control_cache_bytes": layer_bytes,
            "prewarm_and_fade_entries": fade_entries, "prewarm_and_fade_bytes": fade_bytes,
            "fade_cache_growth": 0,
            "control_cache_max_entries": 48,
            "control_cache_max_bytes": window.CONTROL_LAYER_CACHE_MAX_BYTES})
        window.shutdown()
        owner.close()
        app.processEvents()
    (options.output / "evidence.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"platform": app.platformName(), "themes": len(proof["themes"]),
                      "output": str(options.output.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
