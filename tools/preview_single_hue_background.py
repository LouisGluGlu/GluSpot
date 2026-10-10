"""Native before/after page captures for synthetic single-hue cover fixtures.

The comparison disables only the new tonal-role helper for the previous
renderer path. It does not load accounts, play audio or access the network.
Generated captures belong in the ignored .runtime directory.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from api.audio_routes import AudioRouteSnapshot
from local.music_surface_window import MusicSurfaceWindow


class _FixtureProvider:
    def get_audio_route_snapshot(self):
        return AudioRouteSnapshot(state="unavailable", capture_scope="owned-player")


def _cover(colors: tuple[QColor, ...]) -> QImage:
    image = QImage(288, 288, QImage.Format.Format_RGB32)
    with QPainter(image) as painter:
        for index, color in enumerate(colors):
            first, last = round(index * 288 / len(colors)), round((index + 1) * 288 / len(colors))
            painter.fillRect(QRect(0, first, 288, last - first), color)
    return image


def _capture(app, label, colors, previous: bool) -> QImage:
    owner = QWidget()
    owner.data_provider = _FixtureProvider()
    owner._music_library_back_callback = lambda: None
    owner.album_color, owner.secondary_color = colors[0], colors[-1]
    owner.album_art = _cover(colors)
    owner.track_title, owner.artist, owner.album_title = label + " · UI FIXTURE", "离线原生调色预览", "人工色块封面"
    owner.media_track_id, owner.media_position_seconds, owner.media_is_playing = label, 5.0, True
    owner.timed_lyrics = [(0.0, "同一色相，保留柔和的明暗层次"), (4.0, "让歌词与封面有各自的空间"),
                          (8.0, "这是一组离线原生界面预览"), (12.0, "不含账号和真实播放数据")]
    owner.lyrics = [text for _, text in owner.timed_lyrics]
    owner._estimated_media_position = lambda _: owner.media_position_seconds
    comparison = patch("local.music_surface_background.single_hue_background_colors", return_value=())
    with comparison if previous else nullcontext():
        window = MusicSurfaceWindow(owner)
        window.resize(1200, 675)
        window.show()
        window._timer.stop()
        window._fullscreen = True
        app.processEvents()
        window._reset_control_idle()
        frame = window.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
        window.shutdown()
        window.close()
    owner.close()
    return frame


def _comparison(rows: list[tuple[str, QImage, QImage]]) -> QImage:
    width, height = rows[0][1].width(), rows[0][1].height()
    caption = 50
    sheet = QImage(width * 2, (height + caption) * len(rows), QImage.Format.Format_RGB32)
    sheet.fill(QColor("#15181d"))
    with QPainter(sheet) as painter:
        painter.setPen(QColor("#f2f2f2"))
        painter.setFont(QFont("Microsoft YaHei UI", 18))
        for row, (label, before, after) in enumerate(rows):
            y = row * (height + caption)
            painter.drawText(QRect(20, y, width - 40, caption), Qt.AlignmentFlag.AlignVCenter,
                             label + " / 修改前")
            painter.drawText(QRect(width + 20, y, width - 40, caption), Qt.AlignmentFlag.AlignVCenter,
                             label + " / 柔和同色相层次")
            painter.drawImage(0, y + caption, before)
            painter.drawImage(width, y + caption, after)
    return sheet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/single-hue-background-preview"))
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    fixtures = (
        ("red", "鲜红", (QColor(233, 31, 20),)),
        ("cyan", "青色近单色", (QColor(16, 135, 164), QColor(30, 177, 201), QColor(90, 186, 209))),
        ("yellow", "鲜黄", (QColor(242, 204, 12),)),
        ("purple", "鲜紫", (QColor(181, 35, 230),)),
        ("multicolor", "多色对照", (QColor(222, 53, 44), QColor(35, 120, 216), QColor(199, 170, 52))),
        ("gray", "灰阶对照", (QColor(8, 8, 8), QColor(112, 112, 112), QColor(235, 235, 235))),
        ("dark", "暗摄影对照", (QColor(7, 22, 38),) * 14 + (QColor(20, 130, 240),) * 5 + (QColor(180, 207, 235),)),
    )
    comparisons = []
    for key, label, colors in fixtures:
        before, after = _capture(app, label, colors, True), _capture(app, label, colors, False)
        before.save(str(options.output / f"{key}-before.png"))
        after.save(str(options.output / f"{key}-after.png"))
        comparisons.append((label, before, after))
    _comparison(comparisons[:2]).save(str(options.output / "red-cyan-comparison.png"))
    _comparison(comparisons[2:4]).save(str(options.output / "yellow-purple-comparison.png"))
    _comparison(comparisons[4:]).save(str(options.output / "controls-comparison.png"))
    print(options.output.resolve())


if __name__ == "__main__":
    main()
