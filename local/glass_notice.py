"""A boxed message that eases in, stays five seconds and fades out.

One notice belongs to a host window (the music page). Panels and window
actions pass it their messages instead of growing their own red text rows,
so every hint in the page has the same liquid-glass box, timing and motion.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

SHOW_DELAY_MS = 160      # a message that is replaced at once never flashes
FADE_MS = 240
HOLD_S = 5.0             # every notice stays five seconds, then leaves
RADIUS = 14.0
PAD_X, PAD_Y = 14.0, 10.0
DOT = 7.0


def hold_seconds(text: str) -> float:
    """Five seconds for every message; the next trigger shows it again."""
    return HOLD_S


class GlassNotice(QWidget):
    def __init__(self, host: QWidget):
        super().__init__(host)
        self.host = host
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()
        self._text = ""
        self._kind = "info"
        self._anchor = QRect()
        self._source = None
        self._opacity = 0.0
        self._lines: list[str] = []
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.timeout.connect(self._begin)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self.dismiss)
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(FADE_MS)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(self._set_opacity)
        self._fade.finished.connect(self._fade_finished)

    # -- public ---------------------------------------------------------------
    def text(self) -> str:
        return self._text

    def kind(self) -> str:
        return self._kind

    def source(self):
        return self._source

    def show_message(self, text: str, *, kind: str = "info", anchor: QRect | None = None,
                     source=None, duration: float | None = None) -> None:
        text = " ".join(str(text or "").split())
        if not text:
            self.dismiss(source=source)
            return
        same = text == self._text and self._target_visible()
        self._text, self._kind = text, ("error" if kind == "error" else "info")
        self._anchor = QRect(anchor) if anchor is not None else QRect()
        self._source = source
        self.setAccessibleName(text)
        self.setToolTip("")
        self._place()
        hold = duration if duration is not None else hold_seconds(text)
        if same:
            # Repeated state: keep it up, do not replay the entrance.
            self._hold.start(round(hold * 1000))
            self.update()
            return
        self._hold.start(round(hold * 1000) + SHOW_DELAY_MS + FADE_MS)
        if not self.isVisible():
            self._delay.start(SHOW_DELAY_MS)
        else:
            self._begin()

    def dismiss(self, *, source=None) -> None:
        if source is not None and source is not self._source:
            return
        self._delay.stop()
        self._hold.stop()
        if not self.isVisible():
            self._text = ""
            return
        self._animate_to(0.0)

    def reposition(self, anchor: QRect | None = None) -> None:
        if anchor is not None:
            self._anchor = QRect(anchor)
        if self._text:
            self._place()

    # -- motion ---------------------------------------------------------------
    def _target_visible(self) -> bool:
        return self.isVisible() or self._delay.isActive()

    def _begin(self) -> None:
        self._place()
        self.show()
        self.raise_()
        self._animate_to(1.0)

    def _animate_to(self, value: float) -> None:
        self._fade.stop()
        self._fade.setStartValue(float(self._opacity))
        self._fade.setEndValue(float(value))
        self._fade.start()

    def _set_opacity(self, value) -> None:
        self._opacity = float(value)
        self.update()

    def _fade_finished(self) -> None:
        if self._opacity <= 0.001:
            self.hide()
            self._text = ""
            self._source = None

    def mousePressEvent(self, event):
        self.dismiss()
        event.accept()

    # -- layout and paint --------------------------------------------------------
    def _scale(self) -> float:
        scale_of = getattr(self.host, "_panel_scale", None)
        return float(scale_of()) if callable(scale_of) else 1.0

    def _font(self) -> QFont:
        factory = getattr(self.host, "_surface_font", None)
        if callable(factory):
            try:
                return QFont(factory(13))
            except Exception:
                pass
        font = QFont(self.host.font())
        font.setPixelSize(13)
        return font

    def _place(self) -> None:
        host = self.host
        margin = 12
        font = self._font()
        metrics = QFontMetricsF(font)
        widest = max(160.0, min(340.0 * self._scale(), host.width() - 2 * margin))
        text_width = widest - 2 * PAD_X - DOT - 8
        self._lines = self._wrap(self._text, metrics, text_width)
        used = max((metrics.horizontalAdvance(line) for line in self._lines), default=0.0)
        width = min(widest, used + 2 * PAD_X + DOT + 8)
        height = metrics.lineSpacing() * len(self._lines) + 2 * PAD_Y
        w, h = int(width + 0.999), int(height + 0.999)
        if self._anchor.isValid():
            x = self._anchor.center().x() - w // 2
            below = self._anchor.bottom() + 10
            y = below if below + h <= host.height() - margin else self._anchor.top() - h - 10
        else:
            x = (host.width() - w) // 2
            y = host.height() - h - round(84 * self._scale())
        x = max(margin, min(x, host.width() - w - margin))
        y = max(margin, min(y, host.height() - h - margin))
        self.setGeometry(x, y, w, h)

    @staticmethod
    def _wrap(text: str, metrics: QFontMetricsF, width: float) -> list[str]:
        """Character wrap that suits Chinese and keeps Latin words whole."""
        lines, current = [], ""
        for token in _tokens(text):
            trial = current + token
            if current and metrics.horizontalAdvance(trial) > width:
                lines.append(current.rstrip())
                current = token.lstrip()
            else:
                current = trial
        if current:
            lines.append(current.rstrip())
        return lines[:6]

    def _ink(self) -> QColor:
        ink = getattr(self.host, "_panel_glass_ink", None)
        return QColor(ink()) if callable(ink) else QColor("white")

    def paintEvent(self, event):
        if self._opacity <= 0.001 or not self._text:
            return
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setOpacity(self._opacity)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            glass = getattr(self.host, "_paint_panel_glass", None)
            ink = self._ink()
            if callable(glass):
                glass(painter, self, rect, RADIUS)
            else:
                path = QPainterPath()
                path.addRoundedRect(rect, RADIUS, RADIUS)
                face = QColor(24, 24, 27, 232) if ink.lightness() > 128 else QColor(250, 250, 252, 236)
                painter.fillPath(path, face)
                painter.setPen(QPen(QColor(255, 255, 255, 60), 1.0))
                painter.drawPath(path)
            error = self._kind == "error"
            dot = QColor("#ff6b6b" if ink.lightness() > 128 else "#d33a3a") if error else QColor(ink)
            if not error:
                dot.setAlpha(150)
            metrics_font = self._font()
            metrics = QFontMetricsF(metrics_font)
            first_center = PAD_Y + metrics.lineSpacing() / 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot)
            painter.drawEllipse(QPointF(PAD_X + DOT / 2, first_center), DOT / 2, DOT / 2)
            painter.setFont(metrics_font)
            painter.setPen(ink)
            x = PAD_X + DOT + 8
            for index, line in enumerate(self._lines):
                baseline = PAD_Y + metrics.ascent() + index * metrics.lineSpacing()
                painter.drawText(QPointF(x, baseline), line)


def _tokens(text: str):
    """Latin words and spaces stay together; every other character stands alone."""
    word = ""
    for ch in text:
        if ch.isascii() and not ch.isspace():
            word += ch
            continue
        if word:
            yield word
            word = ""
        yield ch
    if word:
        yield word


def notice_for(host: QWidget) -> GlassNotice | None:
    """The host's single shared notice, created on first use."""
    if host is None:
        return None
    notice = getattr(host, "_glass_notice", None)
    if notice is None:
        notice = GlassNotice(host)
        host._glass_notice = notice
    return notice
