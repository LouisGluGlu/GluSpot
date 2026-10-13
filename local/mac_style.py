from __future__ import annotations

from functools import lru_cache
import math

from PySide6.QtCore import QEasingCurve, QEvent, QRectF, QSignalBlocker, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QRegion
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QPushButton

from local.branding import ROOT_DIR
from local.ui_theme import app_dark_mode


def mac_preferences_theme(dark: bool | None = None) -> dict[str, str]:
    if dark is None:
        dark = app_dark_mode()
    if dark:
        return {
            "window": "#1f1f21",
            "sidebar": "#2a2a2c",
            "sidebar_border": "#3a3a3c",
            "sidebar_text": "#f5f5f7",
            "sidebar_hover": "rgba(255, 255, 255, 42)",
            "content": "#1f1f21",
            "titlebar": "#242426",
            "card": "#2c2c2e",
            "card_border": "#3a3a3c",
            "detail_body": "#242426",
            "search": "#343436",
            "search_border": "#464649",
            "text": "#f5f5f7",
            "section_text": "#f5f5f7",
            "muted": "#b8b8be",
            "secondary": "#8e8e93",
            "disabled_text": "#6f6f75",
            "control": "#3a3a3c",
            "control_hover": "#48484a",
            "popup_hover": "#3a3a3c",
            "track": "#515154",
            "scroll": "#77777c",
            "accent": "#0a84ff",
        }
    return {
        "window": "#ededf0",
        "sidebar": "#e1e1e5",
        "sidebar_border": "#c9c9d0",
        "sidebar_text": "#1d1d1f",
        "sidebar_hover": "rgba(36, 36, 38, 26)",
        "content": "#f4f4f7",
        "titlebar": "#f0f0f3",
        "card": "#fbfbfd",
        "card_border": "#d8d8df",
        "detail_body": "#ececf1",
        "search": "#f7f7f9",
        "search_border": "#c5c5cc",
        "text": "#1d1d1f",
        "section_text": "#111113",
        "muted": "#5f6368",
        "secondary": "#8e8e93",
        "disabled_text": "#a0a0a5",
        "control": "#ffffff",
        "control_hover": "#f7f7f7",
        "popup_hover": "#dedede",
        "track": "#d7d7d7",
        "scroll": "#9a9a9a",
        "accent": "#0a84ff",
    }


def mac_sidebar_icon_colors() -> tuple[str, ...]:
    return (
        "#0a84ff",
        "#34c759",
        "#ff9500",
        "#af52de",
        "#ff3b30",
        "#5856d6",
        "#5ac8fa",
        "#8e8e93",
    )


_WINDOW_ICON_KEYS = {
    "MacTrafficRed": "close",
    "MacTrafficYellow": "minimize",
    "MacTrafficGreen": "maximize",
}


@lru_cache(maxsize=3)
def _window_icon_renderer(key: str):
    renderer = QSvgRenderer(str(ROOT_DIR / "assets" / "window_controls" / f"{key}.svg"))
    return renderer if renderer.isValid() else None


@lru_cache(maxsize=96)
def _window_icon_mask(key: str, side: int) -> QImage:
    image = QImage(side, side, QImage.Format.Format_RGBA8888_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    renderer = _window_icon_renderer(key)
    if renderer is not None:
        view = renderer.viewBoxF()
        scale = side / max(1., view.width(), view.height())
        target = QRectF((side - view.width() * scale) / 2., (side - view.height() * scale) / 2.,
            view.width() * scale, view.height() * scale)
        with QPainter(image) as painter:
            renderer.render(painter, target)
    return image


@lru_cache(maxsize=3)
def _window_icon_coverage(key: str) -> float:
    """Alpha-weighted occupied area of the actual SVG, including its holes."""
    image = _window_icon_mask(key, 256)
    alpha = image.constBits()[3::4]
    return sum(alpha) / (255. * image.width() * image.height())


def _window_icon_tint(key: str, background: QColor) -> QColor:
    # Large shapes carry more ink, so they need less darkening than sparse
    # strokes. Keep the button's hue instead of the SVG's supplied white/gray.
    retained_brightness = .38 + .25 * _window_icon_coverage(key)
    return background.darker(round(100. / retained_brightness))


class MacTrafficButton(QPushButton):
    COLORS = {
        "MacTrafficRed": "#ff5f57",
        "MacTrafficYellow": "#ffbd2e",
        "MacTrafficGreen": "#28c840",
    }

    def __init__(self, object_name: str, tooltip: str, symbol: str, parent=None):
        super().__init__(parent)
        self._symbol = symbol
        self._hovered = False
        self._reveal = None
        self._icon_key = _WINDOW_ICON_KEYS.get(object_name, "close")
        self._color = QColor(self.COLORS.get(object_name, "#8e8e93"))
        self.setObjectName(object_name)
        self.setToolTip(tooltip)
        self.setFixedSize(20, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(tooltip)
        # Keep the previous window-control focus contract. Settings initially
        # focuses its search field; mouse clicks must not latch hover growth.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoDefault(False)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(.6, .6, -.6, -.6)
        painter.setPen(Qt.PenStyle.NoPen)
        background = self._color.darker(112) if self.isDown() else QColor(self._color)
        painter.setBrush(background)
        painter.drawEllipse(rect)
        reveal = self._reveal if self._reveal is not None else float(self._hovered or self.hasFocus())
        if reveal > 0.:
            side = rect.width() * .53
            icon_rect = QRectF(rect.center().x() - side / 2., rect.center().y() - side / 2., side, side)
            image = _window_icon_mask(self._icon_key, min(96, max(1, math.ceil(side * self.devicePixelRatioF())))).copy()
            with QPainter(image) as ink:
                ink.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                ink.fillRect(image.rect(), _window_icon_tint(self._icon_key, background))
            painter.setOpacity(reveal)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(icon_rect, image)
        if self.hasFocus():
            painter.setOpacity(1.)
            painter.setPen(QPen(_window_icon_tint(self._icon_key, background), 1.))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect.adjusted(1.5, 1.5, -1.5, -1.5))
        painter.end()


class MacTrafficCapsule(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MacTrafficCapsule")
        self.setProperty("legacyObjectName", "MacTrafficLights")
        # Reserve the expanded footprint. Growing this capsule never moves
        # the settings search/nav, and uses no elastic peer displacement.
        self.setFixedSize(116, 40)
        self._progress = 0.
        self._hovered = False
        self.buttons = []
        self._animation = QVariantAnimation(self)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_progress)
        self._set_progress(0.)

    def _capsule_rect(self):
        width, height = 86. + 30. * self._progress, 32. + 6. * self._progress
        return QRectF((self.width() - width) / 2., (self.height() - height) / 2., width, height)

    def _set_progress(self, value):
        self._progress = max(0., min(1., float(value)))
        rect = self._capsule_rect()
        side = round(20. + 4. * self._progress)
        inset = 6. + 3. * self._progress
        first = rect.left() + inset + side / 2.
        last = rect.right() - inset - side / 2.
        for index, button in enumerate(self.buttons):
            center = first + (last - first) * index / 2.
            button.setFixedSize(side, side)
            button.move(round(center - side / 2.), round(rect.center().y() - side / 2.))
            button._reveal = self._progress
            button.update()
        path = QPainterPath()
        # Integer regions are hit masks, not antialiased paint clips. Leave a
        # one-pixel fringe so the rounded stroke retains its edge coverage.
        hit_rect = rect.adjusted(-1., -1., 1., 1.)
        path.addRoundedRect(hit_rect, hit_rect.height() / 2., hit_rect.height() / 2.)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        self.update()

    def _set_expanded(self, expanded):
        target = float(bool(expanded))
        progress = self._progress
        if abs(target - progress) < .001:
            self._animation.stop()
            self._set_progress(target)
            return
        # Qt recalculates the current value when duration/endpoints change.
        # Retarget from the displayed pose without painting those intermediate
        # configurations at the previous animation's elapsed time.
        with QSignalBlocker(self._animation):
            self._animation.stop()
            self._animation.setDuration(max(1, round(220. * abs(target - progress))))
            self._animation.setStartValue(progress)
            self._animation.setEndValue(target)
            self._animation.setCurrentTime(0)
        self._animation.start()

    def enterEvent(self, event):
        self._hovered = True
        self._set_expanded(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._set_expanded(any(button.hasFocus() for button in self.buttons))
        super().leaveEvent(event)

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Type.FocusIn, QEvent.Type.FocusOut}:
            self._set_expanded(self._hovered or event.type() == QEvent.Type.FocusIn
                or any(button is not watched and button.hasFocus() for button in self.buttons))
        return super().eventFilter(watched, event)

    def hideEvent(self, event):
        self._hovered = False
        self._animation.stop()
        self._set_progress(0.)
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self._capsule_rect().adjusted(1.4, 1.4, -1.4, -1.4)
        dark = app_dark_mode()
        painter.setPen(QPen(QColor("#737378" if dark else "#bfc0c3"), 2.8))
        painter.setBrush(QColor(255, 255, 255, 12 if dark else 90))
        painter.drawRoundedRect(rect, rect.height() / 2., rect.height() / 2.)
        painter.end()


def mac_traffic_lights(window=None) -> QFrame:
    traffic = MacTrafficCapsule()
    controls = (
        ("MacTrafficRed", "关闭", "×", lambda: window.close() if window is not None else None),
        ("MacTrafficYellow", "最小化", "−", lambda: window.showMinimized() if window is not None else None),
        (
            "MacTrafficGreen",
            "缩放",
            "□",
            lambda: (
                window.showNormal()
                if window is not None and window.isMaximized()
                else window.showMaximized()
                if window is not None
                else None
            ),
        ),
    )
    for name, tooltip, symbol, callback in controls:
        button = MacTrafficButton(name, tooltip, symbol, traffic)
        button.clicked.connect(callback)
        button.installEventFilter(traffic)
        traffic.buttons.append(button)
    traffic._set_progress(0.)
    return traffic


def mac_sidebar_icon(color: str, glyph: str = "") -> QIcon:
    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(1, 1, 16, 16, 4, 4)
    if glyph:
        painter.setPen(QColor(255, 255, 255, 230))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, glyph[:1].upper())
    painter.end()
    return QIcon(pixmap)


def mac_preferences_stylesheet(theme: dict[str, str]) -> str:
    t = theme
    return f"""
        QDialog {{
            background: transparent;
            color: {t["text"]};
            selection-background-color: {t["accent"]};
            selection-color: #ffffff;
            border-radius: 14px;
        }}
        QFrame#MacWindowShell {{
            background: {t["window"]};
            border-radius: 14px;
        }}
        QWidget {{
            color: {t["text"]};
        }}
        QFrame#MacSidebar {{
            background: {t["sidebar"]};
            border-right: 1px solid {t["sidebar_border"]};
            border-top-left-radius: 14px;
            border-bottom-left-radius: 14px;
        }}
        QWidget#MacContentPane {{
            background: {t["content"]};
            border-top-right-radius: 14px;
            border-bottom-right-radius: 14px;
        }}
        QWidget#MacSidebarHeader {{
            background: transparent;
        }}
        QFrame#MacTrafficLights, QFrame#MacTrafficCapsule {{
            background: transparent;
            border: 0;
        }}
        QPushButton#MacTrafficRed, QPushButton#MacTrafficYellow, QPushButton#MacTrafficGreen {{
            background: transparent;
            border: 0;
            padding: 0;
        }}
        QLineEdit#MacSearch {{
            min-height: 24px;
            border: 1px solid {t["search_border"]};
            border-radius: 5px;
            padding: 2px 9px;
            background: {t["search"]};
            color: {t["text"]};
        }}
        QLineEdit#MacSearch:focus {{
            border-color: {t["accent"]};
            background: {t["control"]};
        }}
        QListWidget#MacSidebarList {{
            background: transparent;
            color: {t["sidebar_text"]};
            border: 0;
            outline: 0;
            padding: 6px 8px 10px 8px;
            font-size: 13px;
        }}
        QListWidget#MacSidebarList::item {{
            min-height: 30px;
            border-radius: 5px;
            padding: 5px 8px;
            margin: 1px 0;
            color: {t["sidebar_text"]};
        }}
        QListWidget#MacSidebarList::item:hover {{
            background: {t["sidebar_hover"]};
        }}
        QListWidget#MacSidebarList::item:selected {{
            background: {t["accent"]};
            color: #ffffff;
        }}
        QFrame#MacTitleBar {{
            background: {t["titlebar"]};
            border-bottom: 1px solid {t["card_border"]};
            border-top-right-radius: 14px;
        }}
        QLabel#MacTitle {{
            font-size: 14px;
            font-weight: 500;
            color: {t["text"]};
        }}
        QScrollArea#MacContentScroll {{
            border: 0;
            background: {t["content"]};
        }}
        QScrollArea#MacContentScroll QWidget#MacScrollBody {{
            background: {t["content"]};
        }}
        QScrollArea#MacContentScroll > QWidget > QWidget {{
            background: {t["content"]};
        }}
        QFrame#MacCard {{
            background: {t["card"]};
            border: 1px solid {t["card_border"]};
            border-radius: 10px;
        }}
        QFrame#MacCard QWidget {{
            background: transparent;
        }}
        QFrame#DetailPanel {{
            background: transparent;
            border: 0;
        }}
        QPushButton#DetailToggle {{
            text-align: left;
            min-height: 26px;
            border-radius: 7px;
            padding: 2px 10px;
            background: {t["control"]};
            border: 1px solid {t["card_border"]};
            color: {t["text"]};
        }}
        QPushButton#DetailToggle:hover {{
            background: {t["control_hover"]};
        }}
        QPushButton#DetailToggle:checked {{
            background: {t["control_hover"]};
            border-color: {t["accent"]};
        }}
        QFrame#DetailBody {{
            background: {t["detail_body"]};
            border: 1px solid {t["card_border"]};
            border-radius: 8px;
            margin-top: 5px;
        }}
        QFrame#DetailBody QWidget {{
            background: transparent;
        }}
        QLabel {{
            background: transparent;
            color: {t["text"]};
        }}
        QLabel#Title {{
            font-size: 20px;
            font-weight: 600;
            color: {t["text"]};
            padding: 0 0 8px 0;
        }}
        QLabel#Subtitle {{
            color: {t["muted"]};
            font-size: 12px;
        }}
        QLabel#Section {{
            color: {t["section_text"]};
            font-size: 12px;
            font-weight: 600;
            padding: 14px 0 6px 2px;
        }}
        QLabel#RowTitle {{
            font-size: 13px;
            color: {t["text"]};
        }}
        QLabel#SearchResultSummary {{
            color: {t["muted"]};
            font-size: 12px;
            padding: 2px 0 6px 2px;
        }}
        QFrame#SearchResultRow {{
            background: {t["card"]};
            border: 1px solid {t["card_border"]};
            border-radius: 8px;
        }}
        QLabel#SearchResultTitle {{
            color: {t["text"]};
            font-size: 13px;
            font-weight: 500;
        }}
        QLabel#SearchResultMeta, QLabel#SearchResultEmpty {{
            color: {t["muted"]};
            font-size: 12px;
        }}
        QPushButton#SearchResultAction {{
            min-width: 48px;
            padding: 0 11px;
        }}
        QLabel#Value, QLabel#Muted, QLabel#DiagnosticValue {{
            color: {t["muted"]};
            font-size: 12px;
        }}
        QLineEdit, QComboBox, QSpinBox {{
            min-height: 24px;
            border: 1px solid {t["card_border"]};
            border-radius: 5px;
            padding: 3px 8px;
            background: {t["control"]};
            color: {t["text"]};
        }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
            color: {t["disabled_text"]};
            background: {t["control"]};
            border-color: {t["card_border"]};
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
            border-color: {t["accent"]};
            background: {t["control_hover"]};
        }}
        QPushButton {{
            min-height: 24px;
            border: 1px solid {t["card_border"]};
            border-radius: 5px;
            background: {t["control"]};
            color: {t["text"]};
            padding: 0 13px;
        }}
        QPushButton:disabled {{
            color: {t["disabled_text"]};
            background: {t["control"]};
            border-color: {t["card_border"]};
        }}
        QPushButton:hover {{
            background: {t["control_hover"]};
        }}
        QPushButton#Primary {{
            background: {t["accent"]};
            border-color: {t["accent"]};
            color: #ffffff;
        }}
        QPushButton#Danger {{
            background: #ff3b30;
            border-color: #ff3b30;
            color: #ffffff;
        }}
        QSlider::groove:horizontal {{
            height: 4px;
            border-radius: 2px;
            background: {t["track"]};
        }}
        QSlider::sub-page:horizontal {{
            height: 4px;
            border-radius: 2px;
            background: {t["accent"]};
        }}
        QSlider::handle:horizontal {{
            width: 14px;
            height: 14px;
            margin: -5px 0;
            border-radius: 7px;
            background: #ffffff;
            border: 1px solid {t["secondary"]};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 12px;
            margin: 8px 3px 8px 3px;
        }}
        QScrollBar::handle:vertical {{
            background: rgba(118, 118, 124, 168);
            border-radius: 5px;
            min-height: 44px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
            border: 0;
        }}
        QComboBox QAbstractItemView {{
            background: {t["control"]};
            color: {t["text"]};
            border: 1px solid {t["card_border"]};
            selection-background-color: {t["accent"]};
            selection-color: #ffffff;
            outline: 0;
        }}
        QComboBox QAbstractItemView::item {{
            min-height: 26px;
            padding: 5px 9px;
            background: transparent;
            color: {t["text"]};
        }}
        QComboBox QAbstractItemView::item:disabled {{
            color: {t["disabled_text"]};
        }}
        QComboBox QAbstractItemView::item:hover {{
            background: {t["control_hover"]};
        }}
    """
