"""Dark and light pages with an Apple-style clear-to-tint glass slider.

The page itself changes color. Clear glass shows the cover through a bright
rim. Tinted glass becomes a solid tinted plate. Text and icons stay sharp.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSlider

from local.music_source_icons import paint_music_source_icon

from _demos.choices_t110.common import (
    SourceFlatButton, Stage, application, capture_window, ui_font,
)


COVERS = Path(__file__).resolve().parents[1] / "narrow3" / "covers"


def mix_color(start: QColor, end: QColor, amount: float) -> QColor:
    amount = 0.0 if amount < 0.0 else 1.0 if amount > 1.0 else amount
    return QColor(
        round(start.red() + (end.red() - start.red()) * amount),
        round(start.green() + (end.green() - start.green()) * amount),
        round(start.blue() + (end.blue() - start.blue()) * amount),
        round(start.alpha() + (end.alpha() - start.alpha()) * amount),
    )


def theme_ink(amount: float) -> QColor:
    return mix_color(QColor("#f7f4ef"), QColor("#1a1816"), amount)


def glass_fill(theme: float, tint: float) -> QColor:
    """tint 0 is clear, tint 1 is a colored plate. theme 0 is dark, 1 is light."""
    tint = 0.0 if tint < 0 else 1.0 if tint > 1 else tint
    dark = mix_color(QColor(255, 255, 255, 28), QColor(36, 28, 24, 228), tint)
    light = mix_color(QColor(255, 255, 255, 42), QColor(255, 247, 239, 236), tint)
    return mix_color(dark, light, theme)


def page_veil(theme: float) -> QColor:
    return mix_color(QColor(7, 8, 12, 132), QColor(250, 246, 240, 168), theme)


class LightDemo(Stage):
    def __init__(self):
        super().__init__("7  浅色界面", (("solid", "已选更实的浅色"),))
        self.resize(1180, 760)
        self.mix = 1.0
        self.level = 72
        self.photos = {
            "sunset": self._load("sunset_city.jpg"),
            "sea": self._load("night_sea.jpg"),
            "forest": self._load("pink_forest.jpg"),
        }
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(0, 100)
        self.slider.setValue(self.level)
        self.slider.setAccessibleName("透明到色调")
        self.slider.valueChanged.connect(self._set_level)
        self.slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,80); border-radius: 2px; }"
            "QSlider::handle:horizontal { width: 18px; height: 18px; margin: -7px 0; background: white; border-radius: 9px; }"
        )
        self.theme_button = SourceFlatButton("切换到深色", self, primary=True)
        self.theme_button.clicked.connect(self.toggle_theme)
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(520)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.anim.valueChanged.connect(self._set_mix)
        self.anim.finished.connect(self._retitle)

    def _load(self, name: str) -> QImage:
        image = QImage(str(COVERS / name))
        if image.isNull():
            image = QImage(8, 8, QImage.Format.Format_RGB32)
            image.fill(QColor("#c8874d"))
        return image

    def description(self) -> str:
        mode = "浅色" if self.mix > 0.5 else "深色"
        return f"现在是{mode}。滑块左端「透明」透出封面和亮边，右端「色调」变成有颜色的实底。页面底色本身也会变，不只是玻璃换白。"

    def relayout(self) -> None:
        self.theme_button.setGeometry(self.width() - 168, 74, 148, 34)
        self.slider.setGeometry(168, self.header_height() + 8, max(160, self.width() - 390), 22)

    def _set_level(self, value: int) -> None:
        self.level = int(value)
        self.update()

    def toggle_theme(self) -> None:
        target = 0.0 if self.mix > 0.5 else 1.0
        self.anim.stop()
        self.anim.setStartValue(float(self.mix))
        self.anim.setEndValue(target)
        self.anim.start()

    def keyPressEvent(self, event) -> None:
        if event.key() in {Qt.Key.Key_T, Qt.Key.Key_Space}:
            self.toggle_theme()
            event.accept()
            return
        super().keyPressEvent(event)

    def _set_mix(self, value) -> None:
        self.mix = float(value)
        self.update()

    def _retitle(self) -> None:
        self.theme_button.setText("切换到深色" if self.mix > 0.5 else "切换到浅色")
        self.theme_button.update()

    def set_mix(self, value: float) -> None:
        self.anim.stop()
        self.mix = float(value)
        self._retitle()
        self.update()

    def paint_body(self, painter: QPainter, _backdrop) -> None:
        body = self.body_rect().adjusted(0, 36, 0, -28)
        gap = 12.0
        width = (body.width() - gap * 2) / 3.0
        cards = (
            ("岛", self.photos["sunset"], self._paint_island),
            ("音乐库", self.photos["sea"], self._paint_library),
            ("平台页", self.photos["forest"], self._paint_platform),
        )
        for index, (label, photo, paint) in enumerate(cards):
            rect = QRectF(body.left() + index * (width + gap), body.top(), width, body.height())
            self._paint_page(painter, rect, photo, label)
            paint(painter, rect)
        painter.setPen(theme_ink(0.0))
        painter.setFont(ui_font(13, bold=True))
        painter.drawText(QRectF(body.left(), self.header_height() + 6, 52, 24), Qt.AlignmentFlag.AlignVCenter, "透明")
        painter.drawText(QRectF(self.slider.geometry().right() + 8, self.header_height() + 6, 52, 24),
                         Qt.AlignmentFlag.AlignVCenter, "色调")
        painter.setFont(ui_font(12))
        painter.drawText(QRectF(body.left(), self.body_rect().bottom() - 24, body.width(), 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "「透明」更通透，「色调」增加不透明度。深色底和浅色底一起过渡，字和图标保持清楚。")

    def _paint_page(self, painter: QPainter, rect: QRectF, photo: QImage, label: str) -> None:
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, 20, 20)
        painter.setClipPath(clip)
        self._draw_cover(painter, rect, photo)
        painter.fillRect(rect, page_veil(self.mix))
        painter.restore()
        painter.setPen(QPen(mix_color(QColor(255, 255, 255, 36), QColor(40, 32, 24, 40), self.mix), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(.5, .5, -.5, -.5), 20, 20)
        painter.setPen(theme_ink(self.mix))
        painter.setFont(ui_font(13, bold=True))
        painter.drawText(rect.adjusted(14, 10, -14, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, label)

    def _draw_cover(self, painter: QPainter, rect: QRectF, photo: QImage) -> None:
        scaled = photo.scaled(max(2, int(rect.width())), max(2, int(rect.height())),
                              Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                              Qt.TransformationMode.SmoothTransformation)
        painter.drawImage(rect.topLeft(), scaled)

    def _glass(self, painter: QPainter, rect: QRectF, radius: float | None = None) -> None:
        tint = self.level / 100.0
        fill = glass_fill(self.mix, tint)
        radius = min(rect.width(), rect.height()) / 2.0 if radius is None else radius
        painter.setPen(QPen(QColor(255, 255, 255, int(214 - 110 * tint)), 1.25))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect.adjusted(.6, .6, -.6, -.6), radius, radius)
        painter.setPen(QPen(QColor(255, 255, 255, int(150 * (1.0 - 0.45 * tint))), 1))
        inner = rect.adjusted(2, 2, -2, -rect.height() + 4)
        if inner.width() > 4:
            painter.drawLine(int(inner.left()), int(inner.top()), int(inner.right()), int(inner.top()))

    def _paint_island(self, painter: QPainter, rect: QRectF) -> None:
        pill = QRectF(rect.left() + 16, rect.center().y() - 6, rect.width() - 32, 72)
        self._glass(painter, pill, 22)
        cover = QRectF(pill.left() + 10, pill.center().y() - 24, 48, 48)
        self._draw_rounded_cover(painter, cover, self.photos["sunset"], 12)
        color = theme_ink(self.mix)
        painter.setPen(color)
        painter.setFont(ui_font(15, bold=True))
        painter.drawText(QRectF(cover.right() + 12, pill.top() + 14, pill.width() - 86, 22),
                         Qt.AlignmentFlag.AlignVCenter, "What Would U Do")
        painter.setFont(ui_font(12))
        painter.setPen(QColor(color.red(), color.green(), color.blue(), 180))
        painter.drawText(QRectF(cover.right() + 12, pill.top() + 36, pill.width() - 86, 18),
                         Qt.AlignmentFlag.AlignVCenter, "Haley Smalls")

    def _paint_library(self, painter: QPainter, rect: QRectF) -> None:
        search = QRectF(rect.left() + 14, rect.top() + 40, rect.width() - 28, 36)
        self._glass(painter, search, 18)
        color = theme_ink(self.mix)
        painter.setPen(QColor(color.red(), color.green(), color.blue(), 170))
        painter.setFont(ui_font(13))
        painter.drawText(search.adjusted(14, 0, -14, 0), Qt.AlignmentFlag.AlignVCenter, "搜索歌单")
        rows = (("夜航", self.photos["sunset"]), ("通勤", self.photos["sea"]), ("私藏", self.photos["forest"]))
        y = search.bottom() + 12
        for name, photo in rows:
            row = QRectF(rect.left() + 14, y, rect.width() - 28, 52)
            if row.bottom() > rect.bottom() - 12:
                break
            self._glass(painter, row, 16)
            thumb = QRectF(row.left() + 8, row.center().y() - 16, 32, 32)
            self._draw_rounded_cover(painter, thumb, photo, 8)
            painter.setPen(color)
            painter.setFont(ui_font(14, bold=True))
            painter.drawText(row.adjusted(48, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter, name)
            y += 60

    def _paint_platform(self, painter: QPainter, rect: QRectF) -> None:
        color = theme_ink(self.mix)
        rail_x = rect.left() + 16
        y = rect.top() + 44
        for key in ("netease", "qq", "kugou", "apple_music"):
            icon = QRectF(rail_x, y, 36, 36)
            self._glass(painter, icon, 12)
            paint_music_source_icon(painter, icon.adjusted(7, 7, -7, -7), key)
            y += 46
        panel = QRectF(rect.left() + 68, rect.top() + 44, rect.width() - 84, rect.height() - 60)
        self._glass(painter, panel, 22)
        avatar = QRectF(panel.left() + 16, panel.top() + 18, 54, 54)
        self._draw_rounded_cover(painter, avatar, self.photos["forest"], 27)
        painter.setPen(color)
        painter.setFont(ui_font(16, bold=True))
        painter.drawText(QRectF(avatar.right() + 12, avatar.top() + 4, panel.width() - 90, 24),
                         Qt.AlignmentFlag.AlignVCenter, "Glu 的账号")
        painter.setFont(ui_font(12))
        painter.setPen(QColor(color.red(), color.green(), color.blue(), 180))
        painter.drawText(QRectF(avatar.right() + 12, avatar.top() + 28, panel.width() - 90, 18),
                         Qt.AlignmentFlag.AlignVCenter, "网易云音乐 · 已连接")
        button = QRectF(panel.right() - 92, panel.bottom() - 44, 76, 30)
        self._glass(painter, button, 15)
        painter.setPen(color)
        painter.setFont(ui_font(12))
        painter.drawText(button, Qt.AlignmentFlag.AlignCenter, "退出登录")

    def _draw_rounded_cover(self, painter: QPainter, rect: QRectF, photo: QImage, radius: float) -> None:
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        painter.setClipPath(clip)
        self._draw_cover(painter, rect, photo)
        painter.restore()


def capture(directory) -> list:
    from pathlib import Path
    directory = Path(directory)
    assert theme_ink(0).lightness() > 180
    assert theme_ink(1).lightness() < 80
    assert page_veil(0).lightness() < page_veil(1).lightness() - 80
    assert glass_fill(1, 0).alpha() < glass_fill(1, 1).alpha() - 80
    assert glass_fill(0, 1).lightness() < glass_fill(1, 1).lightness() - 40
    app = application()
    window = LightDemo()
    from _demos.choices_t110.common import assert_painted, contact_sheet, save_image
    frames = []
    for theme, level, label in (
        (0.0, 0, "深色 · 透明"),
        (0.0, 100, "深色 · 色调"),
        (1.0, 0, "浅色 · 透明"),
        (1.0, 100, "浅色 · 色调"),
    ):
        window.set_mix(theme)
        window.slider.setValue(level)
        image = capture_window(window)
        assert_painted(image, label)
        frames.append((label, image))
        save_image(image, directory / f"light_t{int(theme)}_l{level}.png")
    sheet = contact_sheet(frames, directory / "light_sheet.png", columns=2)
    window.close()
    del app
    return ["apple glass", str(sheet)]


def main() -> None:
    app = application()
    window = LightDemo()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
