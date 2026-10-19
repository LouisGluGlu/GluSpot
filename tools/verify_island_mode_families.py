"""Render native Qt island silhouettes without showing or probing the desktop."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from tests.test_finder_bar_surface import FinderBarSurfaceTests


def bounds(rect):
    return [round(v, 3) for v in (rect.x(), rect.y(), rect.width(), rect.height())]


def render(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    app = QApplication.instance() or QApplication([])
    from local.island_stack_visual import IslandStackVisual
    IslandStackVisual.load_embedded_lyric_fonts()
    app.setFont(QFont(IslandStackVisual.WAVE_FONT_FAMILY_BOLD, 10))
    fixture = FinderBarSurfaceTests()
    fixture.app = app
    fixture.setUp()
    visual = fixture.visual
    visual.music_ambience_style = "pure_black"
    visual._music_system_dark = True
    visual._solid_album_palette_async_enabled = False
    visual._taskbar_height = lambda: 40.
    visual.taskbar_material_color = QColor("#e4e4e3")
    visual.finder_material.mid_color = QColor(visual.taskbar_material_color)
    visual.finder_bar_rect = QRectF(0, 0, 660, 24)
    visual.finder_bar_enabled = True
    visual.taskbar_follow_base_rect = QRectF(0, 44, 660, 40)
    visual.taskbar_selected_drop_zone = QRectF()
    cases = [("free", "capsule"), ("top", "capsule")]
    cases.extend((mode, style) for mode in ("tasker", "finder")
                 for style in ("transparent", "fade", "capsule"))
    records = []
    try:
        for dpr in (1., 2.):
            directory = output / f"dpr-{int(dpr)}"
            directory.mkdir()
            frames = []
            for mode, style in cases:
                visual.location_mode = mode
                visual.finder_mode = mode == "finder"
                visual.mydockfinder_running = mode == "finder"
                visual.finder_bar_style = style
                rect = {"free": QRectF(24, 28, 360, 46),
                        "top": QRectF(24, 0, 360, 62),
                        "tasker": QRectF(24, 44, 280, 40),
                        "finder": QRectF(150, 0, 360, 24)}[mode]
                image = QImage(round(660 * dpr), round(120 * dpr),
                               QImage.Format.Format_ARGB32_Premultiplied)
                image.setDevicePixelRatio(dpr)
                image.fill(QColor("#20252a"))
                with patch("local.island_stack_visual.time.monotonic", return_value=100.), QPainter(image) as painter:
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    if mode in {"finder", "tasker"}:
                        host = QRectF(0, 0 if mode == "finder" else 44, 660,
                                      24 if mode == "finder" else 40)
                        painter.fillRect(host, visual.taskbar_material_color)
                    visual._paint_music(painter, rect, False)
                    painter.setFont(QFont(IslandStackVisual.WAVE_FONT_FAMILY_BOLD, 10))
                    painter.setPen(QColor("#d7dadd"))
                    painter.drawText(QRectF(24, 96, 610, 20),
                                     Qt.AlignmentFlag.AlignVCenter,
                                     f"{mode.title()} / {style}  |  native Qt  |  DPR {int(dpr)}")
                content = visual._immersive_bar_content_rect(rect)
                if mode == "tasker":
                    assert content == rect.adjusted(0, 5, 0, -5)
                    assert content.contains(visual.music_cover_rect)
                cover = QRectF(visual.music_cover_rect)
                path = directory / f"{mode}-{style}.png"
                assert image.save(str(path))
                frames.append(image)
                records.append({"dpr": dpr, "mode": mode, "style": style,
                                "path": str(path), "native_row": bounds(rect),
                                "paint_content": bounds(content),
                                "cover_action": bounds(cover),
                                "cover_center_hit": visual._hit_test(cover.center())})
            montage = QImage(round(1320 * dpr), round(480 * dpr),
                             QImage.Format.Format_ARGB32_Premultiplied)
            montage.setDevicePixelRatio(dpr)
            montage.fill(QColor("#20252a"))
            with QPainter(montage) as painter:
                for index, frame in enumerate(frames):
                    painter.drawImage(QRectF((index % 2) * 660, (index // 2) * 120, 660, 120), frame)
            assert montage.save(str(directory / "montage.png"))
    finally:
        fixture.tearDown()
    report = {"scope": "offscreen native Qt; no desktop input, screen capture or system bar mutation",
              "frames": records, "frame_count": len(records),
              "tasker_native_hit_row": "unchanged; five logical pixels of paint inset at both vertical edges",
              "free_expanded": "unchanged; existing readable expanded content layout retained"}
    (output / "evidence.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = render(args.output.resolve())
    print(json.dumps({"frames": result["frame_count"], "output": str(args.output.resolve())}))
