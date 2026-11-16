"""Playlist sync window. Reverse copying stays in the engine and off this window."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QVBoxLayout,
)

from local.apple_music_sync_flow import REVERSE_SYNC_ENABLED, sync_direction_allowed
from local.playback_entry_window import SourceFlatButton


class SourceGlassCombo(QComboBox):
    """Source-page glass field: translucent fill, 1 px rim, no specular highlight."""

    GLASS_FILL = 56
    GLASS_RIM = 56
    POPUP_STYLE = (
        "QAbstractItemView { background-color: rgba(22, 24, 31, 236); color: #ececec;"
        " border: 1px solid rgba(255, 255, 255, 96); outline: 0; }"
        "QAbstractItemView::item { min-height: 32px; padding: 4px 14px;"
        " background: transparent; color: #ececec; }"
        "QAbstractItemView::item:hover, QAbstractItemView::item:selected {"
        " background-color: rgba(255, 255, 255, 36); color: #f6f6f6; }"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(36)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setFrame(False)
        view = self.view()
        view.setStyleSheet(self.POPUP_STYLE)
        view.setFrameShape(QFrame.Shape.NoFrame)

    def paintEvent(self, _event):
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if rect.width() < 2 or rect.height() < 2:
            return
        radius = min(rect.width(), rect.height()) / 2
        fill = self.GLASS_FILL
        rim = self.GLASS_RIM
        if self.isEnabled() and self.underMouse():
            fill += 16
        if not self.isEnabled():
            fill, rim = 32, 40
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(255, 255, 255, rim), 1))
            painter.setBrush(QColor(255, 255, 255, fill))
            painter.drawRoundedRect(rect, radius, radius)
            painter.setPen(QColor("#ececec") if self.isEnabled() else QColor("#9296a1"))
            painter.setFont(self.font())
            text_rect = self.rect().adjusted(16, 0, -28, 0)
            label = painter.fontMetrics().elidedText(
                self.currentText(), Qt.TextElideMode.ElideRight, max(1, text_rect.width()),
            )
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
            painter.setPen(QPen(QColor(255, 255, 255, 140), 1.2))
            center_x = rect.right() - 16
            center_y = rect.center().y()
            painter.drawLine(QPointF(center_x - 4, center_y - 2), QPointF(center_x, center_y + 2))
            painter.drawLine(QPointF(center_x, center_y + 2), QPointF(center_x + 4, center_y - 2))


class PlaylistSyncDialog(QDialog):
    previewRequested = Signal()
    confirmRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("歌单同步")
        self.setMinimumSize(640, 520)
        self._direction = "netease_to_apple"
        self._ready = False
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)
        note = QLabel("确认之后才会写入，而且只加歌。现在只从网易云导入到 Apple Music。")
        note.setWordWrap(True)
        root.addWidget(note)
        directions = QHBoxLayout()
        self.netease_button = SourceFlatButton("网易云 → Apple Music", self, primary=True)
        self.apple_button = SourceFlatButton("Apple Music → 网易云", self, primary=False)
        self.netease_button.clicked.connect(lambda _checked=False: self.set_direction("netease_to_apple"))
        self.apple_button.clicked.connect(lambda _checked=False: self.set_direction("apple_to_netease"))
        if not REVERSE_SYNC_ENABLED:
            self.apple_button.hide()
        directions.addWidget(self.netease_button)
        directions.addWidget(self.apple_button)
        root.addLayout(directions)
        self.source_label = QLabel("要搬走的歌单")
        self.source_list = SourceGlassCombo(self)
        self.source_list.setFont(self.netease_button.font())
        self.target_label = QLabel("放到哪一个歌单")
        self.target_list = SourceGlassCombo(self)
        self.target_list.setFont(self.netease_button.font())
        self.name_label = QLabel("新建歌单的名字")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：从网易云搬来")
        self.name_edit.setMaxLength(40)
        for widget in (self.source_label, self.source_list, self.target_label, self.target_list, self.name_label, self.name_edit):
            root.addWidget(widget)
        actions = QHBoxLayout()
        self.preview_button = SourceFlatButton("先看预览", self, primary=True)
        self.confirm_button = SourceFlatButton("确认写入", self, primary=False)
        self.cancel_button = SourceFlatButton("取消", self, primary=False)
        self.preview_button.clicked.connect(lambda _checked=False: self.previewRequested.emit())
        self.confirm_button.clicked.connect(lambda _checked=False: self._confirm())
        self.cancel_button.clicked.connect(lambda _checked=False: self.reject())
        self.confirm_button.setEnabled(False)
        for button in (self.preview_button, self.confirm_button, self.cancel_button):
            actions.addWidget(button)
        root.addLayout(actions)
        self.uncertain = QListWidget()
        self.uncertain.setAccessibleName("不确定的歌，勾选后才会一起写入")
        root.addWidget(self.uncertain)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setAccessibleName("同步预览和结果")
        root.addWidget(self.detail, 1)
        self.setStyleSheet(
            "QDialog { background: rgba(22, 24, 31, 236); }"
            "QLabel { color: #d7dbe4; background: transparent; }"
            "QLineEdit, QListWidget, QPlainTextEdit {"
            " color: #eef1f6; background: rgba(255,255,255,0.04);"
            " border: 1px solid rgba(255,255,255,0.28); border-radius: 8px; padding: 4px; }"
        )
        self.set_direction(self._direction)

    def set_direction(self, direction: str):
        if not sync_direction_allowed(direction):
            return
        self._direction = direction
        self.netease_button.primary = self._direction == "netease_to_apple"
        self.apple_button.primary = self._direction == "apple_to_netease"
        self.netease_button.update()
        self.apple_button.update()
        self._ready = False
        self.confirm_button.setEnabled(False)
        creating_apple = self._direction == "netease_to_apple"
        self.name_label.setText("新建 Apple Music 歌单的名字" if creating_apple else "新建网易云歌单的名字")
        self.name_edit.setPlaceholderText("选了新建时才用这个名字")

    def direction(self) -> str:
        return self._direction

    def set_source_playlists(self, rows):
        self._fill(self.source_list, rows)

    def set_target_playlists(self, rows):
        label = "新建一个 Apple Music 歌单" if self._direction == "netease_to_apple" else "新建一个网易云歌单"
        self._fill(self.target_list, [("", label), *rows])

    def set_preview(self, text: str, uncertain):
        self.detail.setPlainText(text)
        self.uncertain.clear()
        for song_id, label in uncertain:
            item = QListWidgetItem(label)
            item.setData(256, song_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.uncertain.addItem(item)
        self._ready = True
        self.confirm_button.setEnabled(True)

    def set_result(self, text: str):
        self.detail.setPlainText(text)
        self.confirm_button.setEnabled(False)
        self._ready = False

    def set_busy(self, busy: bool):
        self.preview_button.setEnabled(not busy)
        self.confirm_button.setEnabled((not busy) and self._ready)

    def request(self) -> dict:
        return {
            "direction": self._direction,
            "source_playlist_id": self.source_list.currentData() or "",
            "target_playlist_id": self.target_list.currentData() or "",
            "new_name": self.name_edit.text().strip(),
            "include_uncertain": self._checked_uncertain(),
        }

    def _checked_uncertain(self) -> tuple[str, ...]:
        chosen = []
        for index in range(self.uncertain.count()):
            item = self.uncertain.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                chosen.append(str(item.data(256)))
        return tuple(chosen)

    def _confirm(self):
        if self._ready:
            self.confirmRequested.emit()

    @staticmethod
    def _fill(box: QComboBox, rows):
        current = box.currentData()
        box.clear()
        for playlist_id, title in rows:
            box.addItem(str(title), str(playlist_id))
        if current:
            index = box.findData(current)
            if index >= 0:
                box.setCurrentIndex(index)
