"""Opt-in transport-A controls; all player work stays behind the provider.

Installation discovery runs on the existing Qt worker pool. Provider methods
return ordinary Futures; a short GUI timer observes completion without waiting
on CDP, process shutdown or native calls in the GUI thread.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget

from api.capture.netease import NetEaseLocator
from local.liquid_glass import GlassPushButton


ENABLE_NOTICE = (
    "GluSpot 将启用精确歌词进度与点词跳转，并重启网易云音乐。"
    "当前播放会中断，GluSpot 会尽量恢复同一曲目的进度。\n\n"
    "网易云将开放仅监听本机 127.0.0.1 的调试端口。该端口没有鉴权，"
    "同机其他进程或用户可能连接；启用调试接口可能不符合网易云用户协议。\n\n"
    "是否启用并重启网易云音乐？"
)


class _LocateSignals(QObject):
    finished = Signal(object)


class _LocateJob(QRunnable):
    def __init__(self, locator):
        super().__init__()
        self.locator = locator
        self.signals = _LocateSignals()

    def run(self):
        try:
            installation = self.locator.locate()
        except Exception:
            installation = None
        self.signals.finished.emit(installation)


class NetEaseCaptureSettings(QWidget):
    """Reusable settings panel using the system provider's three public hooks.

    ``provider_getter`` returns the system provider, which owns
    repair_netease_capture(), disable_netease_capture() and
    netease_capture_health(). ``confirm`` and ``locator`` are injectable for
    offscreen tests; importing this module never probes or launches a player.
    """

    def __init__(self, settings_getter, settings_setter, provider_getter, parent=None,
                 *, locator=None, platform=None, confirm=None, lamp=None):
        super().__init__(parent)
        self.get_setting = settings_getter
        self.set_setting = settings_setter
        self.provider_getter = provider_getter
        self.confirm = confirm or self._confirm_enable
        self.installation = None
        self._job = None
        self._future = None
        self._error = ''
        self._pending_kind = ''
        self.setObjectName('NetEaseCaptureSettings')
        self.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(8)
        title = QLabel('网易云精确进度（实验）')
        title.setObjectName('RowTitle')
        layout.addWidget(title)
        row = QHBoxLayout()
        self.lamp = lamp or QLabel('●')
        row.addWidget(self.lamp, 0, Qt.AlignmentFlag.AlignVCenter)
        self.status_label = QLabel('正在检测网易云音乐…')
        self.status_label.setObjectName('Muted')
        self.status_label.setWordWrap(True)
        row.addWidget(self.status_label, 1)
        layout.addLayout(row)
        buttons = QHBoxLayout()
        self.enable_button = GlassPushButton('启用并重启网易云')
        self.repair_button = GlassPushButton('修复 / 重启网易云')
        self.disable_button = GlassPushButton('关闭精确进度')
        self.enable_button.clicked.connect(self.enable)
        self.repair_button.clicked.connect(self.repair)
        self.disable_button.clicked.connect(self.disable)
        self.repair_button.setToolTip('重启网易云音乐并重新连接精确进度；当前播放会中断。')
        for button in (self.enable_button, self.repair_button, self.disable_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        hint = QLabel('关闭本功能后，正常退出网易云音乐即可关闭此次启动的本机调试端口。')
        hint.setObjectName('Muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._health_timer = QTimer(self)
        self._health_timer.setInterval(1000)
        self._health_timer.timeout.connect(self.refresh)
        self._operation_timer = QTimer(self)
        self._operation_timer.setInterval(100)
        self._operation_timer.timeout.connect(self._check_operation)
        if (sys.platform if platform is None else platform) == 'win32':
            self._job = _LocateJob(locator or NetEaseLocator())
            self._job.signals.finished.connect(self._located, Qt.ConnectionType.QueuedConnection)
            QThreadPool.globalInstance().start(self._job)

    def _located(self, installation):
        self.installation = installation
        self._job = None
        self.setVisible(installation is not None)
        self.refresh()

    def _enabled(self):
        return (self.get_setting('v2_capture_position_enabled', False) is True
                and self.get_setting('netease.cef.enabled', False) is True
                and self.get_setting('netease.transport', 'off') == 'relaunch')

    def _confirm_enable(self):
        return QMessageBox.question(
            self, '启用网易云精确进度', ENABLE_NOTICE,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def enable(self):
        if self._future is not None or self.installation is None:
            return
        if not self.confirm():
            return
        self.set_setting('v2_capture_position_enabled', True)
        profiles = self.get_setting('v2_capture_app_profiles', {})
        profiles = dict(profiles) if isinstance(profiles, dict) else {}
        profiles['netease'] = {'mode': 'auto'}
        self.set_setting('v2_capture_app_profiles', profiles)
        self.set_setting('netease.transport', 'relaunch')
        self.set_setting('netease.cef.enabled', True)
        self._invoke('repair_netease_capture', 'restart')

    def repair(self):
        if self._future is None and self._enabled():
            self._invoke('repair_netease_capture', 'restart')

    def disable(self):
        if self._future is not None:
            return
        self.set_setting('netease.cef.enabled', False)
        self.set_setting('netease.transport', 'off')
        self._invoke('disable_netease_capture', 'disable')

    def _invoke(self, method_name, kind):
        self._error = ''
        self._pending_kind = kind
        try:
            method = getattr(self.provider_getter(), method_name, None)
            if not callable(method):
                raise RuntimeError('当前媒体来源不支持网易云精确进度')
            result = method()
            if hasattr(result, 'done') and hasattr(result, 'result'):
                self._future = result
                self._operation_timer.start()
            elif result is False:
                self._error = '操作未完成，请重试。'
        except Exception as error:
            self._error = str(error)
        self.refresh()

    def _check_operation(self):
        if self._future is None or not self._future.done():
            return
        future, self._future = self._future, None
        self._operation_timer.stop()
        try:
            if future.result() is False:
                self._error = '操作未完成，请重试。'
        except Exception as error:
            self._error = str(error)
        self.refresh()

    def refresh(self):
        enabled = self._enabled()
        busy = self._future is not None
        self.enable_button.setVisible(not enabled)
        self.repair_button.setVisible(enabled)
        self.disable_button.setVisible(enabled)
        for button in (self.enable_button, self.repair_button, self.disable_button):
            button.setEnabled(not busy and self.installation is not None)
        health = {}
        if enabled:
            method = getattr(self.provider_getter(), 'netease_capture_health', None)
            if callable(method):
                try:
                    health = method()
                except Exception:
                    health = {}
        health = health if isinstance(health, dict) else {}
        active = enabled and health.get('ready') is True
        message = ('正在关闭精确进度…' if self._pending_kind == 'disable'
                   else '正在重启并连接网易云音乐…') if busy else (
                       self._error or (str(health.get('message') or '等待连接精确进度')
                                       if enabled else '未启用'))
        self.status_label.setText(message)
        if hasattr(self.lamp, 'set_active'):
            self.lamp.set_active(active, message)
            self.lamp.set_monitor_enabled(enabled)
        else:
            self.lamp.setStyleSheet('color: ' + ('#1fc95b' if active else '#ff9f0a' if enabled else '#8e8e93'))
            self.lamp.setToolTip(message)
        self.lamp.setProperty('active', active)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._health_timer.start()
        if self._future is not None:
            self._operation_timer.start()

    def hideEvent(self, event):
        self._health_timer.stop()
        self._operation_timer.stop()
        super().hideEvent(event)
