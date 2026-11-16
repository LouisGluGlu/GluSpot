"""Runnable preview of style-two broadcast loops. Not the player.

Windows: double-click 播报动画.bat in this directory.
From the repo root:

    python _demos/broadcast_motion/demo.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Property, QObject, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from local.spring_motion import WIDE_NARROW_SPRING, Spring

from clips import CLIPS


COVER_DIR = ROOT / "_demos" / "narrow3" / "covers"
COVERS = (
    ("night_sea.jpg", "夜海"),
    ("sunset_city.jpg", "日落"),
    ("pink_forest.jpg", "粉林"),
    ("clip.jpg", "街景"),
)
PLAYER = HERE / "player.qml"


class ClipControl(QObject):
    mediaUrlChanged = Signal()
    holdChanged = Signal()
    shownChanged = Signal()
    generationChanged = Signal()

    def __init__(self):
        super().__init__()
        self._media_url = ""
        self._hold = True
        self._shown = 0.0
        self._generation = 0

    def get_media_url(self):
        return self._media_url

    def set_source(self, value: str) -> None:
        if value != self._media_url:
            self._media_url = value
            self.mediaUrlChanged.emit()

    def get_hold(self):
        return self._hold

    def set_paused(self, value: bool) -> None:
        value = bool(value)
        if value != self._hold:
            self._hold = value
            self.holdChanged.emit()

    def get_shown(self):
        return self._shown

    def set_amount(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._shown) > 0.001:
            self._shown = value
            self.shownChanged.emit()

    def get_generation(self):
        return self._generation

    def restart(self) -> None:
        self._generation += 1
        self.generationChanged.emit()

    mediaUrl = Property(str, get_media_url, set_source, notify=mediaUrlChanged)
    hold = Property(bool, get_hold, set_paused, notify=holdChanged)
    shown = Property(float, get_shown, set_amount, notify=shownChanged)
    generation = Property(int, get_generation, notify=generationChanged)


class Stage(QWidget):
    """Cover slot. Our timer runs only while the enter/exit spring is moving."""

    def __init__(self, clip: ClipControl, view: QQuickWidget):
        super().__init__()
        self.setMinimumSize(640, 480)
        self.clip = clip
        self.view = view
        self.view.setParent(self)
        self.candidate = CLIPS[0][0]
        self.theme = "dark"
        self.cover_index = 0
        self._covers = [QImage(str(COVER_DIR / name)) for name, _label in COVERS]
        if any(image.isNull() for image in self._covers):
            missing = [name for image, (name, _label) in zip(self._covers, COVERS) if image.isNull()]
            raise RuntimeError(f"Missing cover: {missing}")
        self._spring = Spring(WIDE_NARROW_SPRING, 0.0, 0.0, 0.0)
        self._on_air = False
        self._slot = QRectF()
        self._last = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._auto = QTimer(self)
        self._auto.setSingleShot(True)
        self._auto.timeout.connect(lambda: self.set_broadcast(False))
        self._apply_clip(restart=False)

    @property
    def spring(self) -> Spring:
        return self._spring

    @property
    def timer_active(self) -> bool:
        return self._timer.isActive()

    def _apply_clip(self, restart: bool) -> None:
        if self.candidate == "together":
            from local.broadcast_face_player import tinted_face_path
            from local.music_surface_palette import artwork_surface_palette
            palette = artwork_surface_palette(
                self._covers[self.cover_index], QColor(44, 44, 44), QColor(14, 14, 14))
            path = tinted_face_path(palette.characteristic)
        else:
            path = HERE / "lottie" / f"{self.candidate}.json"
        self.clip.set_source(QUrl.fromLocalFile(str(path)).toString())
        if restart and self._on_air:
            self.clip.set_paused(False)
            self.clip.restart()

    def set_candidate(self, name: str) -> None:
        self.candidate = name
        self._apply_clip(restart=True)
        self.update()

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        self.update()

    def step_cover(self, delta: int) -> None:
        self.cover_index = (self.cover_index + delta) % len(self._covers)
        self._apply_clip(restart=self._on_air)
        self.update()

    def set_broadcast(self, on: bool) -> None:
        self._on_air = bool(on)
        if not on:
            self._auto.stop()
        else:
            self.clip.set_paused(False)
            self.clip.restart()
            self.view.setVisible(True)
        self._spring.retarget(1.0 if on else 0.0)
        self._sync_presence()
        self._ensure_timer()
        self.update()

    def toggle_broadcast(self) -> None:
        self.set_broadcast(not self._on_air)

    def play_round(self) -> None:
        self.set_broadcast(True)
        self._auto.start(4200)

    def step(self, dt: float) -> None:
        self._spring.step(dt)
        self._sync_presence()
        self.update()

    def _sync_presence(self) -> None:
        amount = self._spring.value
        self.clip.set_amount(amount)
        self.view.setVisible(amount > 0.01)
        if amount <= 0.01 and not self._on_air:
            self.clip.set_paused(True)

    def _needs_timer(self) -> bool:
        return self.isVisible() and not self._spring.settled

    def _ensure_timer(self) -> None:
        if self._needs_timer():
            if not self._timer.isActive():
                self._last = time.perf_counter()
                self._timer.start()
        elif self._timer.isActive():
            self._timer.stop()
            self._last = None

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = 0.016 if self._last is None else min(0.05, max(0.0, now - self._last))
        self._last = now
        self.step(dt)
        self._ensure_timer()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        side = min(self.width(), self.height()) * 0.62
        left = (self.width() - side) / 2.0
        top = (self.height() - side) / 2.0 + 8.0
        self._slot = QRectF(left, top, side, side)
        self.view.setGeometry(int(left), int(top), max(1, int(side)), max(1, int(side)))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._ensure_timer()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        self._last = None
        self.clip.set_paused(True)
        super().hideEvent(event)

    def paintEvent(self, _event) -> None:
        dark = self.theme != "light"
        page = QColor("#14181f") if dark else QColor("#f3f4f6")
        label = dict(CLIPS)[self.candidate]
        cover_label = COVERS[self.cover_index][1]
        state = "播报中" if self._on_air or self._spring.value > 0.04 else "封面"
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.fillRect(self.rect(), page)
            slot = self._slot
            if slot.isValid():
                cover = self._covers[self.cover_index]
                path = QPainterPath()
                radius = slot.width() * 0.055
                path.addRoundedRect(slot, radius, radius)
                painter.save()
                painter.setClipPath(path)
                # The glass face sits on the photo. The photo only dims a little.
                painter.setOpacity(1.0 - 0.28 * self._spring.value)
                if not cover.isNull():
                    side = min(cover.width(), cover.height())
                    source = QRectF((cover.width() - side) / 2.0, (cover.height() - side) / 2.0, side, side)
                    painter.drawImage(slot, cover, source)
                painter.restore()
            painter.setPen(QColor("#f4f6fb") if dark else QColor("#1c2430"))
            font = QFont("Noto Sans CJK SC")
            font.setPixelSize(18)
            painter.setFont(font)
            painter.drawText(
                QRectF(16, self.height() - 36, self.width() - 32, 28),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                f"{label} · {cover_label} · {state}",
            )


class DemoWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("播报动画示例")
        self.resize(880, 720)
        self.clip = ClipControl()
        self.view = QQuickWidget()
        self.view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.view.setClearColor(QColor(0, 0, 0, 0))
        self.view.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.view.setInitialProperties({"clip": self.clip})
        self.view.setSource(QUrl.fromLocalFile(str(PLAYER)))
        self.stage = Stage(self.clip, self.view)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 12)
        root.addWidget(self.stage, 1)
        candidates = QHBoxLayout()
        for name, label in CLIPS:
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, picked=name: self.stage.set_candidate(picked))
            candidates.addWidget(button)
        root.addLayout(candidates)
        actions = QHBoxLayout()
        start = QPushButton("开始 / 停止")
        start.clicked.connect(self.stage.toggle_broadcast)
        show = QPushButton("演示进出")
        show.clicked.connect(self.stage.play_round)
        theme = QPushButton("深色 / 浅色")
        theme.clicked.connect(self._toggle_theme)
        previous = QPushButton("上一张封面")
        previous.clicked.connect(lambda: self.stage.step_cover(-1))
        following = QPushButton("下一张封面")
        following.clicked.connect(lambda: self.stage.step_cover(1))
        for button in (start, show, theme, previous, following):
            actions.addWidget(button)
        root.addLayout(actions)
        hint = QPushButton("1 涟漪张望    2 涟漪    3 张望    空格 开始/停止    B 深浅    ← → 封面    Esc 退出")
        hint.setEnabled(False)
        hint.setFlat(True)
        root.addWidget(hint)

    def _toggle_theme(self) -> None:
        self.stage.set_theme("light" if self.stage.theme == "dark" else "dark")

    def keyPressEvent(self, event) -> None:
        keys = {Qt.Key.Key_1: CLIPS[0][0], Qt.Key.Key_2: CLIPS[1][0], Qt.Key.Key_3: CLIPS[2][0]}
        if event.key() in keys:
            self.stage.set_candidate(keys[event.key()])
            return
        if event.key() == Qt.Key.Key_Space:
            self.stage.toggle_broadcast()
            return
        if event.key() == Qt.Key.Key_B:
            self._toggle_theme()
            return
        if event.key() == Qt.Key.Key_Left:
            self.stage.step_cover(-1)
            return
        if event.key() == Qt.Key.Key_Right:
            self.stage.step_cover(1)
            return
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


def main() -> None:
    app = QApplication.instance() or QApplication([])
    window = DemoWindow()
    window.show()
    raise SystemExit(app.exec())


def _prepare_quick_software() -> None:
    """Offscreen grabs have no GPU. A normal Windows launch leaves this unset."""
    QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)


if __name__ == "__main__":
    main()
