"""Open one picker for the six t110 choice demos.

Run from the repository root:

    python -X utf8 _demos/choices_t110/run_all.py
    python -X utf8 _demos/choices_t110/run_all.py --demo magnet
    python -X utf8 _demos/choices_t110/run_all.py --grab shots
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from _demos.choices_t110.common import SourceFlatButton, application, ink, library_backdrop, ui_font
from _demos.choices_t110.demo_active import ActiveDemo, capture as capture_active
from _demos.choices_t110.demo_light import LightDemo, capture as capture_light
from _demos.choices_t110.demo_magnet import MagnetDemo, capture as capture_magnet
from _demos.choices_t110.demo_narrow import NarrowDemo, capture as capture_narrow
from _demos.choices_t110.demo_playlist import PlaylistDemo, capture as capture_playlist
from _demos.choices_t110.demo_source import SourceDemo, capture as capture_source
from _demos.choices_t110.demo_wide import WideDemo, capture as capture_wide


DEMOS = (
    ("magnet", "1  磁吸停靠", "Finder 栏和任务栏的吸附磁贴", MagnetDemo, capture_magnet),
    ("active", "2  译文图标动画", "文和 A 换位，其他按钮不变色", ActiveDemo, capture_active),
    ("narrow", "3  窄窗隐藏", "封面标题贴顶，播报占封面槽", NarrowDemo, capture_narrow),
    ("playlist", "4  添加到歌单", "只加入；排序在个人歌单", PlaylistDemo, capture_playlist),
    ("wide", "5  宽版布局", "已选定布局 G", WideDemo, capture_wide),
    ("source", "6  来源页", "退出登录放进右侧玻璃", SourceDemo, capture_source),
    ("light", "7  浅色界面", "岛、音乐库、平台页一起变", LightDemo, capture_light),
)


class Picker(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GluSpot 选择演示")
        self.resize(560, 680)
        self.windows = []
        self.buttons = []
        for key, title, _blurb, _cls, _capture in DEMOS:
            button = SourceFlatButton(title, self, primary=False)
            button.setAccessibleName(title)
            button.clicked.connect(lambda _checked=False, key=key: self.open_demo(key))
            self.buttons.append(button)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        y = 78
        for button in self.buttons:
            button.setGeometry(24, y, self.width() - 48, 64)
            y += 74

    def open_demo(self, key: str) -> None:
        demo = next(item for item in DEMOS if item[0] == key)
        window = demo[3]()
        window.show()
        self.windows.append(window)

    def paintEvent(self, _event) -> None:
        backdrop = library_backdrop(self.width(), self.height())
        with QPainter(self) as painter:
            painter.drawImage(self.rect(), backdrop)
            painter.setPen(ink())
            painter.setFont(ui_font(20, bold=True))
            painter.drawText(24, 36, "选择一个演示")
            painter.setFont(ui_font(12))
            painter.setPen(ink(190))
            painter.drawText(24, 58, "数字键在每个窗口里切换 A / B / C。Esc 关闭该窗口。")


def grab_all(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, title, _blurb, _cls, capture in DEMOS:
        print(f"grab {key}", flush=True)
        lines.extend(f"{key}: {item}" for item in capture(directory / key))
    report = directory / "report.txt"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report.read_text(encoding="utf-8"), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="打开 GluSpot t110 选择演示")
    parser.add_argument("--demo", choices=[item[0] for item in DEMOS])
    parser.add_argument("--grab", type=Path)
    args = parser.parse_args()
    if args.grab is not None:
        grab_all(args.grab)
        return
    app = application()
    if args.demo:
        window = next(item[3] for item in DEMOS if item[0] == args.demo)()
        window.show()
    else:
        window = Picker()
        window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
