"""Runnable comparison of the current lyric page and an Apple-style page.

Nothing here writes settings, starts a player, or changes product widgets.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from paint import COVERS, Scene, render_scene
from timeline import LOOP_END, APPLE_KNOB_PRESETS, AppleKnobs, loop_time


class LyricCanvas(QWidget):
    def __init__(self, owner: "DemoWindow"):
        super().__init__()
        self.owner = owner
        self.setMinimumSize(640, 480)

    def paintEvent(self, _event) -> None:
        image = render_scene(self.width(), self.height(), self.owner.scene())
        painter = QPainter(self)
        painter.drawImage(self.rect(), image)
        painter.end()


class DemoWindow(QWidget):
    def __init__(self, preset: str | None = None):
        super().__init__()
        self.setWindowTitle("歌词样式对照（示例，不连播放器）")
        self.resize(1180, 760)
        self.style_name = "apple"
        self.theme = "dark"
        self.cover_index = 0
        self.t = 2.70
        self.playing = True
        self.knobs = AppleKnobs()
        self._last = time.monotonic()
        self.canvas = LyricCanvas(self)
        self.readout = QLabel()
        self.readout.setWordWrap(True)
        self._sliders: list[QSlider] = []
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.canvas, 1)
        root.addWidget(self._build_panel(), 0)
        self.set_preset(preset)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def scene(self) -> Scene:
        return Scene(self.style_name, self.theme, self.t, self.knobs, COVERS[self.cover_index])

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        cover = QLabel()
        self.cover_label = cover
        cover.setPixmap(self._thumb(COVERS[0]))
        layout.addWidget(cover)
        self.cover_name = QLabel(COVERS[0].name)
        layout.addWidget(self.cover_name)

        styles = QHBoxLayout()
        self.current_button = self._mode_button("现在的歌词", "current")
        self.apple_button = self._mode_button("仿 Apple", "apple")
        styles.addWidget(self.current_button)
        styles.addWidget(self.apple_button)
        layout.addLayout(styles)
        themes = QHBoxLayout()
        self.dark_button = self._theme_button("深色", "dark")
        self.light_button = self._theme_button("白底", "light")
        themes.addWidget(self.dark_button)
        themes.addWidget(self.light_button)
        layout.addLayout(themes)

        jumps = QHBoxLayout()
        jumps.addWidget(self._jump("看长音", 2.40))
        jumps.addWidget(self._jump("看提前换行", 3.40))
        jumps.addWidget(self._jump("看长间隔", 6.60))
        layout.addLayout(jumps)

        box = QGroupBox("仿 Apple 参数（不写入设置）")
        form = QFormLayout(box)
        self._add_slider(form, "块间距 ×字号", 120, 240, 175, 100, "block_gap_ratio")
        self._add_slider(form, "译文间距 ×字号", 8, 45, 25, 100, "translation_gap_ratio")
        self._add_slider(form, "其他行缩放", 80, 95, 88, 100, "other_scale")
        self._add_slider(form, "模糊 px", 0, 80, 22, 10, "blur_px")
        self._add_slider(form, "未读透明度", 40, 70, 55, 100, "unread_alpha")
        self._add_slider(form, "羽化 ×字宽", 30, 150, 75, 100, "feather_chars")
        self._add_slider(form, "滚动 ms", 250, 600, 350, 1, "scroll_ms")
        self._add_slider(form, "提前 ms", 200, 600, 350, 1, "lead_ms")
        layout.addWidget(box)
        reset = QPushButton("恢复视频估计值")
        reset.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        reset.clicked.connect(self._reset_knobs)
        layout.addWidget(reset)
        hint = QLabel("← → 切换样式 · B 白底/深色 · 1–4 封面 · 空格暂停 · Esc 退出")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(self.readout)
        layout.addStretch(1)
        self._sync_buttons()
        return panel

    def _mode_button(self, text: str, style_name: str) -> QPushButton:
        button = QPushButton(text)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCheckable(True)
        button.clicked.connect(lambda: self.set_style(style_name))
        return button

    def _theme_button(self, text: str, theme: str) -> QPushButton:
        button = QPushButton(text)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCheckable(True)
        button.clicked.connect(lambda: self.set_theme(theme))
        return button

    def _jump(self, text: str, moment: float) -> QPushButton:
        button = QPushButton(text)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(lambda: self.seek(moment, play=True))
        return button

    def _add_slider(self, form: QFormLayout, name: str, low: int, high: int, value: int, scale: int, field: str) -> None:
        label = QLabel()
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setValue(value)
        slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        slider.setProperty("field", field)
        slider.setProperty("scale", scale)

        def refresh(raw: int) -> None:
            number = raw / scale
            label.setText(f"{name}  {number:.2f}" if scale > 1 else f"{name}  {int(number)}")
            self.knobs = replace(self.knobs, **{field: number})
            self.canvas.update()

        slider.valueChanged.connect(refresh)
        refresh(value)
        form.addRow(label)
        form.addRow(slider)
        self._sliders.append(slider)

    def _reset_knobs(self) -> None:
        self._set_knobs(AppleKnobs())

    def set_preset(self, name: str | None = None) -> None:
        self._set_knobs(AppleKnobs.from_preset(name))

    def _set_knobs(self, knobs: AppleKnobs) -> None:
        self.knobs = knobs
        for slider in self._sliders:
            field = slider.property("field")
            scale = int(slider.property("scale"))
            slider.setValue(int(round(getattr(knobs, field) * scale)))
        self.canvas.update()

    def set_style(self, style_name: str) -> None:
        self.style_name = style_name
        enabled = style_name == "apple"
        for slider in self._sliders:
            slider.setEnabled(enabled)
        self._sync_buttons()
        self.canvas.update()

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        self._sync_buttons()
        self.canvas.update()

    def set_cover(self, index: int) -> None:
        self.cover_index = index % len(COVERS)
        path = COVERS[self.cover_index]
        self.cover_label.setPixmap(self._thumb(path))
        self.cover_name.setText(path.name)
        self.canvas.update()

    @staticmethod
    def _thumb(path: Path) -> QPixmap:
        image = QImage(str(path)).scaled(
            72, 72,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        return QPixmap.fromImage(image)

    def seek(self, moment: float, play: bool = False) -> None:
        self.t = loop_time(moment)
        self.playing = play
        self._last = time.monotonic()
        self.canvas.update()

    def _sync_buttons(self) -> None:
        self.current_button.setChecked(self.style_name == "current")
        self.apple_button.setChecked(self.style_name == "apple")
        self.dark_button.setChecked(self.theme == "dark")
        self.light_button.setChecked(self.theme == "light")

    def _tick(self) -> None:
        now = time.monotonic()
        if self.playing:
            self.t = loop_time(self.t + min(0.05, now - self._last))
            self.canvas.update()
        self._last = now
        from timeline import describe
        self.readout.setText(describe(self.t, self.style_name, self.knobs))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Left:
            self.set_style("current")
        elif key == Qt.Key.Key_Right:
            self.set_style("apple")
        elif key == Qt.Key.Key_B:
            self.set_theme("light" if self.theme == "dark" else "dark")
        elif key in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_4):
            self.set_cover(key - Qt.Key.Key_1)
        elif key == Qt.Key.Key_Space:
            self.playing = not self.playing
            self._last = time.monotonic()
        elif key == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=tuple(APPLE_KNOB_PRESETS))
    args = parser.parse_args()
    app = QApplication(sys.argv)
    window = DemoWindow(args.preset)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
