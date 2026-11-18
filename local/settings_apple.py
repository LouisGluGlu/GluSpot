"""Apple Settings-style layout for the GluSpot settings window.

Only layout, hierarchy and interaction follow the platform Settings app:
a floating glass sidebar with search and coloured tiles, a large page title,
and inset grouped cards (gray group title above, gray footer below, rows with
the title on the left and the control on the right, separators that do not
cross the whole row). Icons are GluSpot's own SVGs.

The page builders in ``settings_window`` stay the single source of truth.
``AppleGroupedLayout`` receives exactly what they add to a card today and
sorts it: a section label becomes the group title, a hint becomes the footer,
everything else becomes a row of the current card.

Material: the window paints a soft colour field (tinted by the current album
when there is one); the sidebar is refractive liquid glass over that field,
and every card face follows the shared ``v2_music_glass_level`` tint.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor, QFont, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from local.liquid_glass import glass_material_tint, paint_liquid_glass, widget_glass_backdrop
from local.settings_store import MUSIC_GLASS_DEFAULT_LEVEL, normalize_music_glass_level

SETTINGS_LAYOUT_ENV = "GLUSPOT_SETTINGS_LAYOUT"
SETTINGS_LAYOUT_SHORTCUT = "Ctrl+Shift+L"
ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "seeting"

# Category tiles: own glyphs on a coloured rounded square.
# Glyphs: Ionicons 7.4.0 filled set (MIT, see assets/seeting/ionicons/LICENSE),
# tinted white on a flat system colour.
APPLE_SIDEBAR_TILES = {
    "音乐来源": ("#FF2D55", "ionicons/musical-notes.svg"),
    "播放": ("#FF9500", "ionicons/play-circle.svg"),
    "歌词": ("#AF52DE", "ionicons/chatbox-ellipses.svg"),
    "外观": ("#007AFF", "ionicons/color-palette.svg"),
    "位置": ("#34C759", "ionicons/location.svg"),
    "高级": ("#8E8E93", "ionicons/settings.svg"),
}

APPLE_SIDEBAR_WIDTH = 220
APPLE_COLUMN_MAX_WIDTH = 640
APPLE_CARD_RADIUS = 12.0
APPLE_ROW_HPAD = 16
APPLE_ROW_VPAD = 9


def apple_layout_from_env(environ=None) -> bool:
    """New layout unless the environment asks for the previous one."""
    value = str((environ if environ is not None else os.environ).get(SETTINGS_LAYOUT_ENV, "") or "")
    return value.strip().casefold() not in {"classic", "old", "legacy", "0", "off"}


def apple_settings_palette(dark: bool) -> dict[str, str]:
    if dark:
        return {
            "text": "#f5f5f7",
            "secondary": "#98989f",
            "tertiary": "rgba(235, 235, 245, 76)",
            "separator": "rgba(255, 255, 255, 30)",
            "accent": "#0a84ff",
            "control": "rgba(255, 255, 255, 26)",
            "control_hover": "rgba(255, 255, 255, 40)",
            "control_focus": "rgba(255, 255, 255, 34)",
            "hover": "rgba(255, 255, 255, 20)",
            "search": "rgba(255, 255, 255, 22)",
            "search_focus": "rgba(255, 255, 255, 34)",
            "track": "rgba(255, 255, 255, 46)",
            "handle_border": "rgba(0, 0, 0, 60)",
            "scroll": "rgba(235, 235, 245, 90)",
            "shell_edge": "rgba(255, 255, 255, 34)",
            "chevron": (ASSET_DIR / "apple-popup-chevron-dark.svg").as_posix(),
        }
    return {
        "text": "#1d1d1f",
        "secondary": "#6e6e73",
        "tertiary": "rgba(60, 60, 67, 76)",
        "separator": "rgba(60, 60, 67, 40)",
        "accent": "#007aff",
        "control": "rgba(118, 118, 128, 30)",
        "control_hover": "rgba(118, 118, 128, 46)",
        "control_focus": "rgba(255, 255, 255, 235)",
        "hover": "rgba(0, 0, 0, 14)",
        "search": "rgba(118, 118, 128, 30)",
        "search_focus": "rgba(255, 255, 255, 230)",
        "track": "rgba(118, 118, 128, 56)",
        "handle_border": "rgba(0, 0, 0, 38)",
        "scroll": "rgba(60, 60, 67, 110)",
        "shell_edge": "rgba(0, 0, 0, 40)",
        "chevron": (ASSET_DIR / "apple-popup-chevron-light.svg").as_posix(),
    }


def apple_settings_stylesheet(p: dict[str, str]) -> str:
    """Appended after the shared preferences sheet; same-specificity rules win."""
    return f"""
        QFrame#MacWindowShell {{ background: transparent; border: 0; }}
        QFrame#MacSidebar {{ background: transparent; border: 0; }}
        QWidget#MacContentPane {{ background: transparent; }}
        QFrame#MacTitleBar {{ background: transparent; border: 0; }}
        QLabel#MacTitle {{ font-size: 13px; font-weight: 600; color: {p["text"]}; }}
        QScrollArea#MacContentScroll {{ background: transparent; border: 0; }}
        QScrollArea#MacContentScroll QWidget#MacScrollBody {{ background: transparent; }}
        QScrollArea#MacContentScroll > QWidget > QWidget {{ background: transparent; }}
        QWidget#AppleColumn, QWidget#AppleDetailView, QWidget#AppleGroup {{ background: transparent; }}
        QLabel#AppleLargeTitle {{
            font-size: 26px; font-weight: 700; color: {p["text"]}; padding: 2px 4px 2px 4px;
        }}
        QPushButton#AppleBackButton {{
            background: transparent; border: 0; color: {p["accent"]};
            font-size: 13px; padding: 0 4px; min-height: 26px; text-align: left;
        }}
        QPushButton#AppleBackButton:hover {{ color: {p["accent"]}; background: transparent; }}
        QFrame#MacCard {{ background: transparent; border: 0; }}
        QFrame#MacCard .QWidget {{ background: transparent; }}
        QLabel#RowTitle {{ font-size: 13px; color: {p["text"]}; }}
        QLabel[appleSubtitle="true"], QLabel[appleValue="true"], QLabel[appleFooter="true"],
        QLabel#Muted, QLabel#Value, QLabel#DiagnosticValue {{
            color: {p["secondary"]}; font-size: 12px;
        }}
        QLabel[appleValue="true"] {{ font-size: 13px; }}
        QLineEdit#MacSearch {{
            min-height: 26px; border: 0; border-radius: 8px; padding: 2px 8px;
            background: {p["search"]}; color: {p["text"]};
        }}
        QLineEdit#MacSearch:focus {{ background: {p["search_focus"]}; border: 0; }}
        QListWidget#MacSidebarList {{
            background: transparent; border: 0; outline: 0; padding: 2px 0; font-size: 13px;
        }}
        QListWidget#MacSidebarList::item, QListWidget#MacSidebarList::item:hover,
        QListWidget#MacSidebarList::item:selected {{
            background: transparent; border: 0; color: {p["text"]};
        }}
        QLineEdit, QComboBox, QSpinBox {{
            min-height: 22px; border: 0; border-radius: 7px; padding: 3px 9px;
            background: {p["control"]}; color: {p["text"]};
        }}
        QComboBox {{ padding-right: 26px; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ background: {p["control_focus"]}; border: 0; }}
        QComboBox:hover {{ background: {p["control_hover"]}; }}
        QComboBox::drop-down {{
            subcontrol-origin: padding; subcontrol-position: center right;
            width: 22px; border: 0; background: transparent;
        }}
        QComboBox::down-arrow {{ image: url("{p["chevron"]}"); width: 10px; height: 14px; }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
            background: {p["control"]}; border: 0; color: {p["tertiary"]};
        }}
        QPushButton {{
            min-height: 26px; border: 0; border-radius: 7px; padding: 0 14px;
            background: {p["control"]}; color: {p["text"]};
        }}
        QPushButton:hover {{ background: {p["control_hover"]}; }}
        QPushButton:disabled {{ background: {p["control"]}; border: 0; color: {p["tertiary"]}; }}
        QPushButton#Primary {{ background: {p["accent"]}; color: #ffffff; border: 0; }}
        QPushButton[appleLinkRow="true"], QPushButton#Primary[appleLinkRow="true"],
        QPushButton#DetailToggle[appleLinkRow="true"] {{
            background: transparent; border: 0; border-radius: 8px; margin: 3px 6px;
            padding: 0 34px 0 10px; min-height: 34px; text-align: left;
            font-size: 13px; color: {p["text"]};
        }}
        QPushButton#Primary[appleLinkRow="true"] {{ color: {p["accent"]}; }}
        QPushButton[appleLinkRow="true"]:hover, QPushButton#Primary[appleLinkRow="true"]:hover,
        QPushButton#DetailToggle[appleLinkRow="true"]:hover,
        QPushButton#DetailToggle[appleLinkRow="true"]:checked {{
            background: {p["hover"]}; border: 0;
        }}
        QPushButton[appleLinkRow="true"]:disabled {{ background: transparent; color: {p["tertiary"]}; }}
        QFrame#DetailPanel {{ background: transparent; border: 0; }}
        QFrame#DetailBody {{ background: transparent; border: 0; margin: 0; }}
        QFrame#SearchResultRow {{ background: transparent; border: 0; }}
        QLabel#SearchResultSummary {{ color: {p["secondary"]}; font-size: 12px; font-weight: 600; padding: 0; }}
        QLabel#SearchResultMeta, QLabel#SearchResultEmpty {{ color: {p["secondary"]}; font-size: 12px; }}
        QSlider {{ min-height: 22px; background: transparent; }}
        QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: {p["track"]}; }}
        QSlider::sub-page:horizontal {{ height: 4px; border-radius: 2px; background: {p["accent"]}; }}
        QSlider::handle:horizontal {{
            width: 18px; height: 18px; margin: -7px 0; border-radius: 9px;
            background: #ffffff; border: 1px solid {p["handle_border"]};
        }}
        QSlider::sub-page:horizontal:disabled {{ background: {p["track"]}; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 6px 2px 6px 2px; }}
        QScrollBar::handle:vertical {{ background: {p["scroll"]}; border-radius: 3px; min-height: 40px; }}
    """


def apple_sidebar_tile_icon(color: str, svg_path: Path, side: int = 24) -> QIcon:
    """Flat system colour, slightly rounded square, white vector glyph.

    Rendered per device-pixel ratio so the glyph stays sharp at 150–300 %.
    """
    icon = QIcon()
    renderer = QSvgRenderer(str(svg_path))
    for scale in (1, 2, 3):
        pixels = side * scale
        image = QImage(pixels, pixels, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            radius = pixels * 0.2
            path.addRoundedRect(QRectF(0, 0, pixels, pixels), radius, radius)
            painter.fillPath(path, QColor(color))
            if renderer.isValid():
                glyph_side = round(pixels * 0.6)
                glyph = QImage(glyph_side, glyph_side, QImage.Format.Format_ARGB32_Premultiplied)
                glyph.fill(Qt.GlobalColor.transparent)
                with QPainter(glyph) as ink:
                    ink.setRenderHint(QPainter.RenderHint.Antialiasing)
                    renderer.render(ink, QRectF(0, 0, glyph_side, glyph_side))
                    ink.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                    ink.fillRect(glyph.rect(), QColor("#ffffff"))
                offset = (pixels - glyph_side) / 2
                painter.drawImage(QPointF(offset, offset), glyph)
        image.setDevicePixelRatio(scale)
        icon.addPixmap(QPixmap.fromImage(image))
    return icon


def apple_magnifier_icon(color: str, side: int = 14) -> QIcon:
    pixmap = QPixmap(side * 2, side * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    with QPainter(pixmap) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(QRectF(2.0, 2.0, side * 0.58, side * 0.58))
        reach = 2.0 + side * 0.58
        painter.drawLine(QPointF(reach - 1.4, reach - 1.4), QPointF(side - 2.0, side - 2.0))
    return QIcon(pixmap)


def _owner_dark(owner) -> bool:
    reader = getattr(owner, "_settings_dark_mode", None)
    return bool(reader()) if callable(reader) else False


def _owner_glass_level(owner) -> int:
    reader = getattr(owner, "_setting_value", None)
    value = reader("v2_music_glass_level", MUSIC_GLASS_DEFAULT_LEVEL) if callable(reader) else MUSIC_GLASS_DEFAULT_LEVEL
    return normalize_music_glass_level(value)


class AppleWindowShell(QFrame):
    """Window body. Paints the colour field the glass sidebar and cards sit on.

    ``_background_cache`` is the name ``widget_glass_backdrop`` already looks
    for, so the sidebar's refractive material samples this field directly.
    """

    RADIUS = 14.0
    SCALE = 4

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._apple_owner = owner
        self._background_cache = QImage()
        self._backdrop_key = None

    # Plain grouped-background grays, as in the platform Settings app.
    LIGHT_BACKGROUND = "#f2f2f7"
    DARK_BACKGROUND = "#1c1c1e"

    def refresh_backdrop(self, force: bool = False) -> bool:
        dark = _owner_dark(self._apple_owner)
        width = max(1, self.width() // self.SCALE)
        height = max(1, self.height() // self.SCALE)
        key = (dark, width, height)
        if key == self._backdrop_key and not force:
            return False
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(self.DARK_BACKGROUND if dark else self.LIGHT_BACKGROUND))
        self._background_cache = image
        self._backdrop_key = key
        self.update()
        return True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_backdrop()

    def paintEvent(self, event):
        if self._background_cache.isNull():
            self.refresh_backdrop()
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            path = QPainterPath()
            path.addRoundedRect(rect, self.RADIUS, self.RADIUS)
            painter.setClipPath(path)
            painter.drawImage(QRectF(self.rect()), self._background_cache)
            painter.setClipping(False)
            dark = _owner_dark(self._apple_owner)
            painter.setPen(QPen(QColor(255, 255, 255, 34) if dark else QColor(0, 0, 0, 40), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)


class AppleSidebarPanel(QFrame):
    """Floating liquid-glass sidebar, inset from the window edge."""

    INSET = 8.0
    # Concentric with the window corner: the window outline is a 14 px
    # radius drawn half a pixel in, so the gap stays 7.5 px around the bend.
    RADIUS = AppleWindowShell.RADIUS + 0.5 - INSET

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._apple_owner = owner

    def glass_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(self.INSET, self.INSET, -2.0, -self.INSET)

    def paintEvent(self, event):
        face = self.glass_rect()
        dark = _owner_dark(self._apple_owner)
        # The sidebar stays a step clearer than the cards so the field shows.
        level = round(_owner_glass_level(self._apple_owner) * 0.6)
        backdrop, source = widget_glass_backdrop(self, face)
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint_liquid_glass(painter, face, radius=self.RADIUS, light=not dark, refractive=True,
                               backdrop=backdrop, backdrop_rect=source, cache_token=("settings-sidebar", id(self)),
                               material_level=level)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 40) if dark else QColor(0, 0, 0, 22), 1.0))
            painter.drawRoundedRect(face.adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)


class AppleChevron(QWidget):
    """Disclosure chevron that rides on the right edge of a link row."""

    def __init__(self, owner, host: QWidget):
        super().__init__(host)
        self._apple_owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(8, 13)
        host.installEventFilter(self)
        self._place(host)

    def _place(self, host: QWidget):
        self.move(host.width() - 24 - self.width(), (host.height() - self.height()) // 2)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._place(watched)
        return False

    def paintEvent(self, event):
        dark = _owner_dark(self._apple_owner)
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(235, 235, 245, 110) if dark else QColor(60, 60, 67, 110), 1.7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            w, h = float(self.width()), float(self.height())
            path = QPainterPath(QPointF(1.5, 1.5))
            path.lineTo(QPointF(w - 2.0, h / 2.0))
            path.lineTo(QPointF(1.5, h - 1.5))
            painter.drawPath(path)


class AppleRowWrap(QWidget):
    """Row padding for a control added straight to a card; mirrors its visibility."""

    def __init__(self, child: QWidget):
        super().__init__()
        self.setProperty("appleRow", True)
        self.setObjectName("AppleRowWrap")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(APPLE_ROW_HPAD, APPLE_ROW_VPAD, APPLE_ROW_HPAD, APPLE_ROW_VPAD)
        layout.setSpacing(0)
        # A parentless widget reports isHidden() until first shown; only an
        # explicit hide (e.g. a panel that starts hidden) counts here.
        hidden = (child.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide)
                  and child.testAttribute(Qt.WidgetAttribute.WA_WState_Hidden))
        layout.addWidget(child)
        self._child = child
        child.installEventFilter(self)
        if hidden:
            # Never show() a parentless wrapper: it would become a window.
            self.setVisible(False)

    def eventFilter(self, watched, event):
        if watched is self._child and event.type() in (QEvent.Type.ShowToParent, QEvent.Type.HideToParent):
            self.setVisible(not self._child.isHidden())
        return False


class AppleCard(QFrame):
    """Inset grouped card: glass face, rows, separators between visible rows."""

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._apple_owner = owner
        self.setObjectName("MacCard")
        self._rows: list[QWidget] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(0)

    def has_rows(self) -> bool:
        return bool(self._rows)

    def add_row(self, widget: QWidget) -> QWidget:
        row = widget
        if isinstance(widget, QPushButton) and not widget.property("appleRow"):
            make_link_row(self._apple_owner, widget)
        elif not widget.property("appleRow"):
            row = AppleRowWrap(widget)
        self.layout().addWidget(row)
        self._rows.append(row)
        row.installEventFilter(self)
        self._sync_visibility()
        return row

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.ShowToParent, QEvent.Type.HideToParent):
            self._sync_visibility()
            self.update()
        return False

    def _visible_rows(self) -> list[QWidget]:
        return [row for row in self._rows if not row.isHidden()]

    def _sync_visibility(self):
        self.setVisible(bool(self._visible_rows()))

    def paintEvent(self, event):
        dark = _owner_dark(self._apple_owner)
        level = _owner_glass_level(self._apple_owner)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            face = glass_material_tint(light=not dark, material_level=level)
            paint_liquid_glass(painter, rect, radius=APPLE_CARD_RADIUS, base_color=face,
                               face_opacity=1.0, light=not dark)
            rows = self._visible_rows()
            painter.setPen(QPen(QColor(255, 255, 255, 30) if dark else QColor(60, 60, 67, 40), 1.0))
            for upper, lower in zip(rows, rows[1:]):
                y = round((upper.geometry().bottom() + lower.geometry().top() + 1) / 2.0) + 0.5
                painter.drawLine(QPointF(APPLE_ROW_HPAD, y), QPointF(self.width() - APPLE_ROW_HPAD, y))


def make_link_row(owner, button: QPushButton) -> QPushButton:
    """A bare push button in a card becomes a full-width row with a chevron."""
    button.setProperty("appleRow", True)
    button.setProperty("appleLinkRow", True)
    button.setProperty("glassFlat", True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if getattr(button, "_apple_chevron", None) is None:
        button._apple_chevron = AppleChevron(owner, button)
    button.style().unpolish(button)
    button.style().polish(button)
    return button


class AppleGroupedLayout(QVBoxLayout):
    """What a page builder adds to a card, sorted into title / rows / footer.

    * a widget marked ``appleSectionHeader`` is the group title above a card;
    * any other bare ``QLabel`` is gray footer text below the card (or a
      description between title and card when no row came yet);
    * everything else is a row. Rows after a footer start a new card, as on
      the platform when a footer separates two groups.
    """

    def __init__(self, owner, parent: QWidget | None = None):
        super().__init__(parent)
        self._apple_owner = owner
        self._card: AppleCard | None = None
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(6)

    def reset_groups(self):
        self._card = None

    def addWidget(self, widget, stretch=0, alignment=Qt.AlignmentFlag(0)):
        if widget.property("appleSectionHeader"):
            self._card = None
            widget.setContentsMargins(APPLE_ROW_HPAD, 4, APPLE_ROW_HPAD, 0)
            super().addWidget(widget)
            return
        if isinstance(widget, QLabel) and not widget.property("appleRow"):
            if self._card is not None and self._card.has_rows():
                self._card = None
            widget.setProperty("appleFooter", True)
            widget.setContentsMargins(APPLE_ROW_HPAD, 0, APPLE_ROW_HPAD, 2)
            super().addWidget(widget)
            return
        if self._card is None:
            self._card = AppleCard(self._apple_owner)
            super().addWidget(self._card)
        self._card.add_row(widget)

    def addStretch(self, stretch=0):
        # Cards hug their rows; the page column keeps its own stretch.
        return None


class AppleSidebarDelegate(QStyledItemDelegate):
    ROW_HEIGHT = 34

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._apple_owner = owner

    def sizeHint(self, option, index):
        return QSize(max(0, option.rect.width()), self.ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index):
        dark = _owner_dark(self._apple_owner)
        palette = apple_settings_palette(dark)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = QRectF(option.rect).adjusted(0.0, 1.0, 0.0, -1.0)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected:
            paint_liquid_glass(painter, rect, radius=8.0, base_color=QColor(palette["accent"]),
                               face_opacity=1.0, light=False)
        elif hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 22) if dark else QColor(0, 0, 0, 16))
            painter.drawRoundedRect(rect, 8.0, 8.0)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        side = 24
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(painter, QRect(int(rect.left()) + 6, int(rect.center().y() - side / 2 + 0.5), side, side))
        font = QFont(option.font)
        font.setPixelSize(13)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff") if selected else QColor(palette["text"]))
        text_rect = rect.adjusted(6 + side + 10, 0, -8, 0)
        text = painter.fontMetrics().elidedText(str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
                                                Qt.TextElideMode.ElideRight, int(text_rect.width()))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()


class AppleToast(QLabel):
    """Short notice when the layout is switched."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("AppleToast")
        # Own sheet: the toast shows in both layouts.
        self.setStyleSheet("QLabel#AppleToast { background: rgba(28, 28, 30, 215); color: #ffffff;"
                           " border-radius: 10px; padding: 7px 14px; font-size: 12px; }")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, msec: int = 1800):
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 28)
        self.raise_()
        self.show()
        self._timer.start(msec)


__all__ = [
    "APPLE_COLUMN_MAX_WIDTH",
    "APPLE_ROW_HPAD",
    "APPLE_ROW_VPAD",
    "APPLE_SIDEBAR_TILES",
    "APPLE_SIDEBAR_WIDTH",
    "ASSET_DIR",
    "AppleCard",
    "AppleChevron",
    "AppleGroupedLayout",
    "AppleRowWrap",
    "AppleSidebarDelegate",
    "AppleSidebarPanel",
    "AppleToast",
    "AppleWindowShell",
    "SETTINGS_LAYOUT_ENV",
    "SETTINGS_LAYOUT_SHORTCUT",
    "apple_layout_from_env",
    "apple_magnifier_icon",
    "apple_settings_palette",
    "apple_settings_stylesheet",
    "apple_sidebar_tile_icon",
    "make_link_row",
]
