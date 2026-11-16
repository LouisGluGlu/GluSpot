"""Source page: logout lives in the right-hand liquid-glass panel.

Both options use the music-library frosted backdrop and keep the status lamp
on the icon's bottom-right. A puts account and logout in one glass panel.
B splits the account card from a separate actions card.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from local.music_source_icons import paint_music_source_icon
from local.playback_entry_window import ENTRY_SOURCES

from _demos.choices_t110.common import (
    SourceFlatButton, Stage, application, capture_window, ink, paint_panel, ui_font,
)


STATUS = {
    "netease": "connected",
    "kugou": "error",
    "qq": "signed_out",
}


def lamp_rect(icon: QRectF) -> QRectF:
    """Same corner as the source button: bottom-right of the icon."""
    size = 8.0
    return QRectF(icon.right() - size + 2, icon.bottom() - size + 2, size, size)


class SourceDemo(Stage):
    def __init__(self):
        super().__init__("6  来源页", (("A", "A  一块详情"), ("B", "B  操作分卡")))
        self.resize(1040, 720)
        self.selected = "netease"
        self.status = dict(STATUS)
        self.confirm = False
        self.logout_button = SourceFlatButton("退出登录", self, primary=False)
        self.use_button = SourceFlatButton("继续使用", self, primary=True)
        self.cancel_button = SourceFlatButton("取消", self, primary=False)
        self.confirm_button = SourceFlatButton("确认退出", self, primary=False)
        self.logout_button.clicked.connect(self.ask_logout)
        self.use_button.clicked.connect(lambda: self.note("继续留在网易云音乐"))
        self.cancel_button.clicked.connect(self.cancel_logout)
        self.confirm_button.clicked.connect(self.finish_logout)
        self.message = "点左侧图标切换平台。退出只发生在这块演示里。"

    def description(self) -> str:
        if self.option == "A":
            return "已选定 A：右侧一整块详情玻璃就够了。退出登录在这块玻璃的右下角。背景和音乐库同一套 frosted。状态灯在图标右下角。"
        return "右侧分成两块玻璃：上面是账号，下面单独放继续使用和退出登录。背景和状态灯与 A 相同。"

    def note(self, text: str) -> None:
        self.message = text
        self.update()

    def ask_logout(self) -> None:
        if self.status.get(self.selected) != "connected":
            self.note("这个平台还没有登录")
            return
        self.confirm = True
        self.relayout()
        self.note("确认后，状态灯会变成未登录")

    def cancel_logout(self) -> None:
        self.confirm = False
        self.relayout()
        self.note("已取消退出")

    def finish_logout(self) -> None:
        self.status[self.selected] = "signed_out"
        self.confirm = False
        self.relayout()
        self.note("已退出，状态灯现在在图标右下角显示未登录")

    def on_option(self, _key: str) -> None:
        self.confirm = False
        self.relayout()

    def rail_icons(self) -> list[tuple[str, QRectF]]:
        body = self.body_rect()
        rail = QRectF(body.left() + 8, body.top() + 8, 84, body.height() - 16)
        y = rail.top() + 18
        icons = []
        for source in ENTRY_SOURCES:
            icons.append((source.source_id, QRectF(rail.center().x() - 16, y, 32, 32)))
            y += 48
        return icons

    def panels(self) -> tuple[QRectF, QRectF | None]:
        body = self.body_rect()
        right = QRectF(body.left() + 116, body.top() + 18, body.width() - 136, body.height() - 36)
        if self.option == "A":
            return right, None
        account = QRectF(right.left(), right.top(), right.width(), right.height() * 0.62)
        actions = QRectF(right.left(), account.bottom() + 12, right.width(), right.bottom() - account.bottom() - 12)
        return account, actions

    def relayout(self) -> None:
        account, actions = self.panels()
        host = account if actions is None else actions
        if self.confirm:
            self.logout_button.hide()
            self.use_button.hide()
            self.cancel_button.show()
            self.confirm_button.show()
            self.cancel_button.setGeometry(int(host.right() - 250), int(host.bottom() - 56), 100, 36)
            self.confirm_button.setGeometry(int(host.right() - 140), int(host.bottom() - 56), 112, 36)
        else:
            self.cancel_button.hide()
            self.confirm_button.hide()
            connected = self.status.get(self.selected) == "connected"
            self.logout_button.setVisible(connected)
            self.use_button.setVisible(connected)
            self.use_button.setGeometry(int(host.right() - 250), int(host.bottom() - 56), 100, 36)
            self.logout_button.setGeometry(int(host.right() - 140), int(host.bottom() - 56), 112, 36)

    def mousePressEvent(self, event) -> None:
        for source_id, rect in self.rail_icons():
            if rect.contains(event.position()):
                source = next(item for item in ENTRY_SOURCES if item.source_id == source_id)
                if not source.login_supported:
                    self.note(f"{source.name} 即将支持")
                    event.accept()
                    return
                self.selected = source_id
                self.confirm = False
                self.relayout()
                self.note(f"正在看{source.name}")
                self.update()
                event.accept()
                return
        super().mousePressEvent(event)

    def paint_body(self, painter: QPainter, backdrop) -> None:
        body = self.body_rect()
        painter.setPen(ink(170))
        painter.setFont(ui_font(12))
        painter.drawText(QRectF(body.left() + 8, body.bottom() - 22, body.width() - 16, 18),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "背景：音乐库 frosted    " + self.message)
        self._paint_rail(painter)
        account, actions = self.panels()
        paint_panel(painter, account, backdrop, radius=24)
        if actions is not None:
            paint_panel(painter, actions, backdrop, radius=24)
            painter.setPen(ink())
            painter.setFont(ui_font(13, bold=True))
            painter.drawText(QRectF(actions.left() + 20, actions.top() + 14, 160, 24),
                             Qt.AlignmentFlag.AlignVCenter, "账号操作")
        self._paint_account(painter, account)

    def _paint_rail(self, painter: QPainter) -> None:
        for source in ENTRY_SOURCES:
            rect = dict(self.rail_icons())[source.source_id]
            selected = source.source_id == self.selected
            painter.setPen(QPen(QColor(255, 255, 255, 150 if selected else 40), 1))
            painter.setBrush(QColor(255, 255, 255, 36 if selected else 16))
            painter.drawRoundedRect(rect.adjusted(-6, -6, 6, 6), 12, 12)
            painter.save()
            if not source.login_supported:
                painter.setOpacity(0.4)
            paint_music_source_icon(painter, rect, source.source_id)
            painter.restore()
            if source.login_supported:
                self._paint_lamp(painter, rect, self.status.get(source.source_id, "signed_out"))

    def _paint_lamp(self, painter: QPainter, icon: QRectF, status: str) -> None:
        dot = lamp_rect(icon)
        color = {"connected": QColor("#3fd27f"), "error": QColor("#ff5a5a")}.get(status, QColor("#7b7b82"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(dot)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(29, 29, 32, 230), 1.2))
        painter.drawEllipse(dot.adjusted(.6, .6, -.6, -.6))

    def _paint_account(self, painter: QPainter, panel: QRectF) -> None:
        source = next(item for item in ENTRY_SOURCES if item.source_id == self.selected)
        avatar = QRectF(panel.left() + 28, panel.top() + 36, 84, 84)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(source.color))
        painter.drawEllipse(avatar)
        painter.setPen(ink())
        painter.setFont(ui_font(18, bold=True))
        painter.drawText(avatar, Qt.AlignmentFlag.AlignCenter, source.name[:1])
        status = self.status.get(source.source_id, "signed_out")
        caption = {"connected": "已连接", "error": "登录异常", "signed_out": "未登录"}.get(status, "")
        painter.setFont(ui_font(20, bold=True))
        painter.drawText(QRectF(avatar.right() + 20, avatar.top() + 8, panel.width() - 150, 32),
                         Qt.AlignmentFlag.AlignVCenter, "Glu 的账号" if status == "connected" else source.name)
        painter.setFont(ui_font(13))
        painter.setPen(ink(190))
        painter.drawText(QRectF(avatar.right() + 20, avatar.top() + 42, panel.width() - 150, 24),
                         Qt.AlignmentFlag.AlignVCenter, f"{source.name} · {caption}")
        if self.confirm:
            painter.setPen(ink())
            painter.setFont(ui_font(13))
            painter.drawText(QRectF(panel.left() + 28, panel.bottom() - 58, panel.width() - 300, 36),
                             Qt.AlignmentFlag.AlignVCenter, f"退出{source.name}？")


def capture(directory) -> list:
    from pathlib import Path
    directory = Path(directory)
    icon = QRectF(10, 10, 32, 32)
    lamp = lamp_rect(icon)
    assert lamp.center().x() > icon.center().x()
    assert lamp.center().y() > icon.center().y()
    assert lamp.right() <= icon.right() + 2.1
    app = application()
    window = SourceDemo()
    from _demos.choices_t110.common import assert_painted, contact_sheet, save_image
    frames = []
    window.choose("A")
    window.relayout()
    image = capture_window(window)
    assert_painted(image, "source A")
    account, actions = window.panels()
    assert actions is None
    assert window.logout_button.geometry().right() <= account.right() + 1
    assert window.logout_button.geometry().bottom() <= account.bottom() + 1
    frames.append(("A  一块玻璃详情，退出在右下", image))
    save_image(image, directory / "source_A.png")
    window.choose("B")
    window.confirm = True
    window.relayout()
    image = capture_window(window)
    assert_painted(image, "source B")
    _account, actions = window.panels()
    assert actions is not None
    assert actions.contains(QRectF(window.confirm_button.geometry()).center())
    frames.append(("B  操作分卡，确认退出", image))
    save_image(image, directory / "source_B.png")
    sheet = contact_sheet(frames, directory / "source_sheet.png", columns=2)
    window.close()
    del app
    return ["source page", str(sheet)]


def main() -> None:
    app = application()
    window = SourceDemo()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
