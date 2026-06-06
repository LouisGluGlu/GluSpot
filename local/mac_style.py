from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton

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
        self._color = QColor(self.COLORS.get(object_name, "#8e8e93"))
        self.setObjectName(object_name)
        self.setToolTip(tooltip)
        self.setFixedSize(10, 10)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

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
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QColor(0, 0, 0, 36))
        painter.setBrush(self._color)
        painter.drawEllipse(rect)

        highlight = QColor(255, 255, 255, 72 if self._hovered else 48)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(highlight)
        painter.drawEllipse(QRectF(rect.left() + 2.0, rect.top() + 1.6, 3.2, 2.4))

        if self._hovered:
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setPen(QColor(70, 50, 46, 210))
            painter.setFont(QFont("Segoe UI Symbol", 6, QFont.Weight.Bold))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._symbol)
        painter.end()


class MacTrafficCapsule(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MacTrafficCapsule")
        self.setProperty("legacyObjectName", "MacTrafficLights")
        self.setFixedSize(48, 18)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QColor(255, 255, 255, 72))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 10, 10)
        painter.end()
        super().paintEvent(event)


def mac_traffic_lights(window=None) -> QFrame:
    traffic = MacTrafficCapsule()
    layout = QHBoxLayout(traffic)
    layout.setContentsMargins(5, 4, 5, 4)
    layout.setSpacing(4)
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
        button = MacTrafficButton(name, tooltip, symbol)
        button.clicked.connect(callback)
        layout.addWidget(button)
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
            min-width: 10px;
            max-width: 10px;
            min-height: 10px;
            max-height: 10px;
            border-radius: 5px;
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
