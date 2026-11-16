"""Shared stage for the t110 choice demos.

The demos reuse GluSpot's frosted library backdrop, liquid-glass panels and
the source-page flat button. They do not change product behavior.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRect, QRectF, Qt, QSize
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from local.backdrop_variants import paint_backdrop_variant
from local.liquid_glass import paint_liquid_glass
from local.music_source_icons import paint_music_source_icon
from local.playback_entry_window import SourceFlatButton


# SourceFlatButton: flat translucent fill, 1 px rim, no specular highlight.
FILL_IDLE = 56
FILL_PRIMARY = 74
EDGE_IDLE = 56
EDGE_PRIMARY = 96

LIBRARY_COLORS = (
    QColor("#c8874d"), QColor("#bdcf9b"), QColor("#d493a2"), QColor("#f0c488"),
)
LIBRARY_WEIGHTS = (0.34, 0.22, 0.24, 0.20)

def application() -> QApplication:
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


def ui_font(pixel: int, *, bold: bool = False) -> QFont:
    families = set(QFontDatabase.families())
    if "Microsoft YaHei UI" in families:
        name = "Microsoft YaHei UI"
    elif "WenQuanYi Micro Hei" in families:
        name = "WenQuanYi Micro Hei"
    else:
        name = ""
    font = QFont(name) if name else QFont()
    font.setPixelSize(max(1, int(pixel)))
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


_backdrops: dict[tuple[int, int], QImage] = {}


def library_backdrop(width: int, height: int) -> QImage:
    """Music-library frosted plate. The real painter is capped at 256 px."""
    width, height = max(2, int(width)), max(2, int(height))
    key = (width, height)
    cached = _backdrops.get(key)
    if cached is not None:
        return cached
    long_edge = max(width, height)
    scale = min(1.0, 256.0 / long_edge)
    small = QSize(max(2, round(width * scale)), max(2, round(height * scale)))
    image = paint_backdrop_variant(small, LIBRARY_COLORS, LIBRARY_WEIGHTS, "frosted")
    if image.size() != QSize(width, height):
        image = image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
    _backdrops[key] = image
    return image


def paint_flat_disc(painter: QPainter, rect: QRectF, *, on: bool = False,
                    hovered: bool = False, pressed: bool = False,
                    fill_on: int | None = None, edge_on: int | None = None) -> None:
    """Icon button using the same flat glass numbers as SourceFlatButton."""
    if rect.width() < 2 or rect.height() < 2:
        return
    face = QRectF(rect).adjusted(.5, .5, -.5, -.5)
    radius = min(face.width(), face.height()) / 2
    fill = FILL_PRIMARY if on and fill_on is None else FILL_IDLE
    edge = EDGE_PRIMARY if on and edge_on is None else EDGE_IDLE
    if fill_on is not None and on:
        fill = fill_on
    if edge_on is not None and on:
        edge = edge_on
    if pressed:
        fill = max(36, fill - 12)
    elif hovered:
        fill += 16
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(255, 255, 255, edge), 1))
    painter.setBrush(QColor(255, 255, 255, fill))
    painter.drawRoundedRect(face, radius, radius)
    painter.restore()


def paint_panel(painter: QPainter, rect: QRectF, backdrop: QImage, *, radius: float = 22) -> None:
    """Existing liquid-glass sheet. Buttons on top stay flat."""
    if rect.width() < 4 or rect.height() < 4:
        return
    painter.save()
    paint_liquid_glass(
        painter, rect, radius=radius, light=False, refractive=True,
        backdrop=backdrop, backdrop_rect=QRectF(rect),
        border_color=QColor(255, 255, 255, 46),
        cache_token=("t110", round(rect.x()), round(rect.y()), round(rect.width()), round(rect.height()), radius),
    )
    painter.restore()


def paint_icon(painter: QPainter, rect: QRectF, key: str, color: QColor) -> None:
    paint_music_source_icon(painter, rect, key, tint=color)


def ink(alpha: int = 255) -> QColor:
    return QColor(244, 245, 247, alpha)


def capture_window(widget: QWidget) -> QImage:
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.show()
    QApplication.processEvents()
    widget.repaint()
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101114"))
    widget.render(image)
    widget.hide()
    return image


def assert_painted(image: QImage, name: str) -> None:
    if image.isNull() or image.width() < 40 or image.height() < 40:
        raise RuntimeError(f"{name} did not render")
    samples = []
    for x, y in ((8, 8), (image.width() // 2, image.height() // 2),
                 (image.width() - 8, image.height() - 8),
                 (image.width() // 3, image.height() // 3)):
        samples.append(image.pixel(x, y))
    if len(set(samples)) < 2:
        raise RuntimeError(f"{name} looks blank")


def save_image(image: QImage, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(path)):
        raise RuntimeError(f"could not save {path}")
    return path


def contact_sheet(cells: list[tuple[str, QImage]], path: Path, *, columns: int = 2) -> Path:
    if not cells:
        raise RuntimeError("empty sheet")
    columns = max(1, min(columns, len(cells)))
    rows = (len(cells) + columns - 1) // columns
    pad, label_h, gap = 18, 36, 16
    cell_w = max(image.width() for _label, image in cells)
    cell_h = max(image.height() for _label, image in cells)
    sheet = QImage(columns * cell_w + (columns + 1) * gap,
                   rows * (cell_h + label_h) + (rows + 1) * gap,
                   QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#0e1014"))
    painter = QPainter(sheet)
    painter.setFont(ui_font(16, bold=True))
    painter.setPen(ink())
    for index, (label, image) in enumerate(cells):
        column, row = index % columns, index // columns
        x = gap + column * (cell_w + gap)
        y = gap + row * (cell_h + label_h + gap)
        painter.drawText(QRectF(x, y, cell_w, label_h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        painter.drawImage(QRect(x, y + label_h, image.width(), image.height()), image)
    painter.end()
    return save_image(sheet, path)


class Stage(QWidget):
    """Option chips on top, demo body below. The window itself moves by its title bar."""

    def __init__(self, title: str, options: tuple[tuple[str, str], ...]):
        super().__init__()
        self.setWindowTitle(title)
        self.choice_title = title
        self.options = options
        self.option = options[0][0]
        self._buttons: list[tuple[str, SourceFlatButton]] = []
        for key, label in options:
            button = SourceFlatButton(label, self, primary=key == self.option)
            button.setAccessibleName(label)
            button.clicked.connect(lambda _checked=False, key=key: self.choose(key))
            self._buttons.append((key, button))
        self.setMinimumSize(720, 520)

    def description(self) -> str:
        return ""

    def choose(self, key: str) -> None:
        if key not in {item[0] for item in self.options}:
            return
        self.option = key
        for item, button in self._buttons:
            button.primary = item == key
            button.update()
        self.on_option(key)
        self.update()

    def on_option(self, key: str) -> None:
        return None

    def header_height(self) -> int:
        return 118

    def body_rect(self) -> QRectF:
        return QRectF(16, self.header_height(), max(1, self.width() - 32),
                      max(1, self.height() - self.header_height() - 16))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        x = 16
        for _key, button in self._buttons:
            width = max(108, button.fontMetrics().horizontalAdvance(button.text()) + 28)
            button.setGeometry(x, 74, width, 34)
            x += width + 8
        self.relayout()

    def relayout(self) -> None:
        return None

    def keyPressEvent(self, event) -> None:
        index = {Qt.Key.Key_1: 0, Qt.Key.Key_2: 1, Qt.Key.Key_3: 2}.get(event.key())
        if index is not None and index < len(self.options):
            self.choose(self.options[index][0])
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        backdrop = library_backdrop(self.width(), self.height())
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.drawImage(self.rect(), backdrop)
            painter.setPen(ink())
            painter.setFont(ui_font(20, bold=True))
            painter.drawText(QRectF(16, 10, self.width() - 32, 28), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.choice_title)
            painter.setFont(ui_font(13))
            painter.setPen(ink(210))
            painter.drawText(QRectF(16, 38, self.width() - 32, 32),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                             self.description())
            self.paint_body(painter, backdrop)

    def paint_body(self, painter: QPainter, backdrop: QImage) -> None:
        return None
