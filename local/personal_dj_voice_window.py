"""Small native selector for the authenticated service's Private DJ catalog."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

from api.online_music import PersonalDjVoice
from local.branding import APP_NAME
from local.liquid_glass import GlassPushButton
from local.mac_style import mac_preferences_stylesheet, mac_preferences_theme
from local.music_tasks import MusicTasks


class PersonalDjVoiceWindow(QDialog):
    def __init__(self, provider, parent=None, *, tasks=None):
        super().__init__(parent)
        self.provider = provider
        self.setWindowTitle(f"{APP_NAME} · 私人DJ音色")
        self.setMinimumWidth(420)
        self.setMaximumWidth(560)
        self.setStyleSheet(mac_preferences_stylesheet(mac_preferences_theme()))
        self._request = 0
        self._pending = False
        self._voices = ()
        self._buttons = []
        self.tasks = tasks or MusicTasks(self)
        self.tasks.completed.connect(self._completed)
        worker = self.tasks
        self.destroyed.connect(lambda: worker.close())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        title = QLabel("私人DJ音色")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)
        note = QLabel("仅用于网易云私人漫游。切换后，后续歌曲讲解使用所选音色。")
        note.setWordWrap(True)
        note.setObjectName("Muted")
        layout.addWidget(note)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(8)
        layout.addLayout(self.rows)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("Muted")
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.refresh = GlassPushButton("刷新音色")
        self.refresh.clicked.connect(self.reload)
        actions.addWidget(self.refresh)
        actions.addStretch()
        close = GlassPushButton("完成")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)

    def showEvent(self, event):
        super().showEvent(event)
        self.reload()

    def reload(self):
        self._request += 1
        self._pending = True
        self.refresh.setEnabled(False)
        for button in self._buttons:
            button.setEnabled(False)
        self.status.setText("正在获取可用音色…")
        getter = getattr(self.provider, "list_personal_dj_voices", None)
        if not callable(getter) or not self.tasks.submit(self._request, getter):
            self._completed(self._request, None, "unavailable")

    def _completed(self, token, result, error):
        if token != self._request or not self._pending:
            return
        self._pending = False
        self.refresh.setEnabled(True)
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._buttons = []
        self._voices = ()
        if error or not isinstance(result, tuple) or not result or not all(isinstance(v, PersonalDjVoice) for v in result):
            self.status.setText("请先登录网易云音乐，再刷新音色。" if error == "auth_required" else "暂时无法获取音色，请稍后刷新。")
            return
        self._voices = result
        getter = getattr(self.provider, "get_owned_personal_dj_voice_key", None)
        selected = getter() if callable(getter) else ""
        if not selected:
            selected = next((voice.timbre_key for voice in result if voice.is_default), "")
        for voice in result:
            button = GlassPushButton(voice.name)
            button.setCheckable(True)
            button.setChecked(voice.timbre_key == selected)
            button.setMinimumHeight(48)
            button.setAccessibleName(voice.name)
            button.setToolTip(voice.description)
            button.setStyleSheet("QPushButton { text-align: left; padding: 10px 14px; border-radius: 12px; } QPushButton:checked { border: 1px solid #6d9ee9; }")
            button.setProperty("glassRadius", 12.)
            button.clicked.connect(lambda checked=False, key=voice.timbre_key: self.choose(key))
            self.rows.addWidget(button)
            self._buttons.append(button)
        self.status.setText("音色选择仅保存在 GluSpot 中。")

    def choose(self, key):
        if self._pending or key not in {voice.timbre_key for voice in self._voices}:
            return
        setter = getattr(self.provider, "set_owned_personal_dj_voice", None)
        try:
            success = bool(setter(key)) if callable(setter) else False
        except Exception:
            success = False
        preview_accepted = False
        if success:
            preview = getattr(self.provider, "preview_owned_personal_dj_voice", None)
            if callable(preview):
                try:
                    preview_accepted = bool(preview(key))
                except Exception:
                    pass
        getter = getattr(self.provider, "get_owned_personal_dj_voice_key", None)
        selected = getter() if callable(getter) else ""
        if not selected:
            selected = next((voice.timbre_key for voice in self._voices if voice.is_default), "")
        for voice, button in zip(self._voices, self._buttons):
            button.setChecked(voice.timbre_key == selected)
        voice = next(voice for voice in self._voices if voice.timbre_key == key)
        self.status.setText(f"已选择{voice.name}，后续讲解使用此音色。" if success else "无法切换，请开启程序内播放并刷新音色。")
        if preview_accepted:
            self.status.setText(f"已选择{voice.name}，正在准备官方试听。")

    def done(self, result):
        self._request += 1
        self._pending = False
        super().done(result)
