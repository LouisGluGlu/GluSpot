"""Style-two broadcast face: one Lottie loop over the cover slot.

The clip plays only while a broadcast is active. Enter and exit use
``WIDE_NARROW_SPRING`` (damping ratio 1). Nothing here starts a timer;
the caller's existing clock supplies ``now``.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import Property, QObject, QRect, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor

from local.broadcast_indicator import broadcast_face_colors
from local.spring_motion import WIDE_NARROW_SPRING, Spring


ROOT = Path(__file__).resolve().parents[1]
FACE_JSON = ROOT / "assets" / "broadcast" / "face.json"
PLAYER_QML = ROOT / "assets" / "broadcast" / "player.qml"
# Same placeholder written by the clip generator. Eyes stay pure white.
_BODY_SENTINEL = (0.0, 0.5019607843, 1.0)
_TINT_DIR = Path(tempfile.gettempdir()) / "gluspot-broadcast-face"


def tinted_face_path(color: QColor) -> Path:
    """Copy the template with an opaque accent fill. Eyes are left white."""
    body, _eyes = broadcast_face_colors(QColor(color) if color is not None else QColor(92, 158, 232), "accent")
    red, green, blue, _alpha = body.getRgb()
    stamp = hashlib.sha256(FACE_JSON.read_bytes()).hexdigest()[:12]
    destination = _TINT_DIR / f"face-{stamp}-{red:02x}{green:02x}{blue:02x}.json"
    if destination.is_file():
        return destination
    template = json.loads(FACE_JSON.read_text(encoding="utf-8"))

    def walk(node) -> None:
        if isinstance(node, dict):
            channels = node.get("k")
            if node.get("a") == 0 and isinstance(channels, list) and len(channels) == 4:
                if all(abs(float(channels[index]) - _BODY_SENTINEL[index]) < 1e-3 for index in range(3)):
                    node["k"] = [red / 255.0, green / 255.0, blue / 255.0, 1.0]
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(template)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(template, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return destination


class BroadcastFaceMotion:
    """Critically damped 0..1 presence. Retargeting keeps the current speed."""

    def __init__(self) -> None:
        self.spring = Spring(WIDE_NARROW_SPRING, 0.0, 0.0, 0.0)
        self._last: float | None = None

    def advance(self, active: bool, now: float) -> float:
        now = float(now)
        if self._last is None:
            self._last = now
        dt = min(0.25, max(0.0, now - self._last))
        self._last = now
        target = 1.0 if active else 0.0
        if abs(self.spring.target - target) > 1e-9:
            self.spring.retarget(target)
        if dt > 0.0:
            self.spring.step(dt)
        return self.spring.value

    @property
    def playing(self) -> bool:
        return self.spring.target >= 0.5

    @property
    def idle(self) -> bool:
        return (not self.playing) and self.spring.settled and self.spring.value <= 0.001


class _Clip(QObject):
    mediaUrlChanged = Signal()
    holdChanged = Signal()
    shownChanged = Signal()
    generationChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._media_url = ""
        self._hold = True
        self._shown = 0.0
        self._generation = 0

    def get_media_url(self) -> str:
        return self._media_url

    def set_media_url(self, value: str) -> None:
        if value != self._media_url:
            self._media_url = value
            self.mediaUrlChanged.emit()

    def get_hold(self) -> bool:
        return self._hold

    def set_hold(self, value: bool) -> None:
        value = bool(value)
        if value != self._hold:
            self._hold = value
            self.holdChanged.emit()

    def get_shown(self) -> float:
        return self._shown

    def set_shown(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._shown) > 0.001:
            self._shown = value
            self.shownChanged.emit()

    def get_generation(self) -> int:
        return self._generation

    def restart(self) -> None:
        self._generation += 1
        self.generationChanged.emit()

    mediaUrl = Property(str, get_media_url, set_media_url, notify=mediaUrlChanged)
    hold = Property(bool, get_hold, set_hold, notify=holdChanged)
    shown = Property(float, get_shown, set_shown, notify=shownChanged)
    generation = Property(int, get_generation, notify=generationChanged)


def lottie_face_enabled() -> bool:
    """The Lottie clip is opt-in; the painted face is the default."""
    return FACE_JSON.is_file() and PLAYER_QML.is_file() and os.environ.get("GLUSPOT_LOTTIE_FACE") == "1"


def face_player_for(host) -> BroadcastFacePlayer:
    player = getattr(host, "_broadcast_face_player", None)
    if player is None:
        player = BroadcastFacePlayer()
        host._broadcast_face_player = player
    return player


class BroadcastFacePlayer:
    """One transparent Quick view. Created on the first real broadcast."""

    def __init__(self) -> None:
        self.motion = BroadcastFaceMotion()
        self._view = None
        self._clip: _Clip | None = None
        # Qt's VectorImage only plays Lottie with the Qt Lottie module, which the
        # bundled PySide6 does not ship; the view then loads but stays empty.
        # The painted face is the default, the Lottie clip is opt-in.
        self._ready = (FACE_JSON.is_file() and PLAYER_QML.is_file()
                       and os.environ.get("GLUSPOT_LOTTIE_FACE") == "1")

    @property
    def idle(self) -> bool:
        return self.motion.idle and self._view is None

    def park(self) -> None:
        self.motion.spring.retarget(0.0)
        self.motion.spring.value = 0.0
        self.motion.spring.velocity = 0.0
        if self._clip is not None:
            self._clip.set_hold(True)
            self._clip.set_shown(0.0)
        if self._view is not None:
            self._view.hide()

    def advance(self, parent, rect: QRectF, active: bool, now: float, color: QColor | None = None) -> float | None:
        """Move the spring and, once a broadcast starts, the Lottie view.

        ``None`` means the clip is missing and the caller should keep the
        previous face painter.
        """
        if not self._ready:
            return None
        amount = self.motion.advance(active, now)
        if self.motion.idle:
            if self._view is not None:
                self._clip.set_hold(True)
                self._clip.set_shown(0.0)
                self._view.hide()
            return 0.0
        self._ensure(parent)
        if self._view is None:
            return None
        if self._view.status() != self._view.Status.Ready or self._view.rootObject() is None:
            # The QML did not load (for example a Qt release dropped a
            # property). Keep the painted face instead of an empty view.
            self._view.hide()
            return None
        self._apply_color(color)
        if active and self._clip.hold:
            self._clip.set_hold(False)
            self._clip.restart()
        elif not active:
            self._clip.set_hold(True)
        self._clip.set_shown(amount)
        placed = QRect(
            int(round(rect.left())), int(round(rect.top())),
            max(1, int(round(rect.width()))), max(1, int(round(rect.height()))),
        )
        if self._view.geometry() != placed:
            self._view.setGeometry(placed)
        self._view.setVisible(amount > 0.01 and placed.width() > 1)
        if self._view.isVisible():
            self._view.raise_()
        return amount

    def _ensure(self, parent) -> None:
        if self._view is not None:
            if self._view.parent() is not parent:
                self._view.setParent(parent)
            return
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
            QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)
        from PySide6.QtQuickWidgets import QQuickWidget
        from PySide6.QtGui import QColor
        self._clip = _Clip()
        self._clip.set_media_url(QUrl.fromLocalFile(str(self._tinted(None))).toString())
        self._clip.set_hold(True)
        view = QQuickWidget(parent)
        view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        view.setClearColor(QColor(0, 0, 0, 0))
        view.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        view.setInitialProperties({"clip": self._clip})
        view.setSource(QUrl.fromLocalFile(str(PLAYER_QML)))
        view.hide()
        self._view = view

    def _tinted(self, color: QColor | None) -> Path:
        return tinted_face_path(color if color is not None else QColor(92, 158, 232))

    def _apply_color(self, color: QColor | None) -> None:
        url = QUrl.fromLocalFile(str(self._tinted(color))).toString()
        if url != self._clip.mediaUrl:
            self._clip.set_media_url(url)
