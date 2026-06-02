from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainterPath, QRegion
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from local.ios_widgets import IosSwitch
from local.mac_style import (
    mac_preferences_stylesheet,
    mac_preferences_theme,
    mac_sidebar_icon,
    mac_sidebar_icon_colors,
    mac_traffic_lights,
)
from local.no_wheel_widgets import NoWheelComboBox, NoWheelSpinBox
from local.settings_store import DEFAULT_MUSIC_AMBIENCE_VALUES
from local.ui_theme import is_theme_change_event, style_combo_popup


class ColorButton(QPushButton):
    def __init__(self, color: str, callback, parent=None):
        super().__init__(parent)
        self._callback = callback
        self._color = "#46D6FF"
        self.setFixedSize(34, 34)
        self.clicked.connect(self._choose)
        self.set_color(color)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str):
        qcolor = QColor(str(color))
        if not qcolor.isValid():
            qcolor = QColor("#46D6FF")
        self._color = qcolor.name().upper()
        self.setStyleSheet(
            f"QPushButton {{ border: 2px solid #ffffff; border-radius: 17px; background: {self._color}; }}"
            "QPushButton:hover { border-color: #2f7df6; }"
        )

    def _choose(self):
        color = QColorDialog.getColor(QColor(self._color), self, "选择颜色")
        if color.isValid():
            self.set_color(color.name())
            self._callback()


class SettingsWindow(QDialog):
    FONT_CHOICES = (
        "PingFang SC",
        "Source Han Serif SC",
    )

    EMPTY_BAR_PALETTES = (
        ("aurora", "极光"),
        ("cyan", "青蓝"),
        ("violet", "紫蓝"),
        ("amber", "暖金"),
        ("rainbow", "彩虹"),
        ("custom", "自定义三色"),
    )

    MUSIC_AMBIENCE_STYLES = (
        ("海平面", "sea_level"),
        ("整岛双色", "duotone_full"),
        ("纯黑", "pure_black"),
        ("双侧衍射", "bilateral_diffraction"),
        ("纯白", "pure_white"),
    )

    MUSIC_ISLAND_STYLES = (
        ("当前样式", "standard"),
        ("预留样式 A", "scheme_a"),
    )

    SIDE_ISLAND_STYLES = (
        ("当前胶囊", "standard"),
        ("预留样式 A", "scheme_a"),
    )

    PLUGIN_ISLAND_STYLES = (
        ("当前样式", "standard"),
        ("预留样式 A", "scheme_a"),
    )

    TIME_ALARM_SIDE_DISPLAYS = (
        ("显示备注", "note"),
        ("显示时间", "time"),
    )

    MUSIC_PLAYER_MODES = (
        ("网易云音乐", "netease"),
        ("网易云音乐（BetterNCM）", "netease_betterncm"),
        ("QQ音乐", "qq"),
        ("汽水音乐（待适配）", "qishui"),
        ("酷狗音乐（待适配）", "kugou"),
    )

    ANIMATION_SPEEDS = (
        ("0.5x", 0.5),
        ("0.75x", 0.75),
        ("1x", 1.0),
        ("2x", 2.0),
    )

    TIMEZONES = (
        ("本地时区", "local"),
        ("中国 UTC+8", "UTC+8"),
        ("日本 UTC+9", "UTC+9"),
        ("美国西部 UTC-8", "UTC-8"),
        ("美国东部 UTC-5", "UTC-5"),
        ("伦敦 UTC+0", "UTC+0"),
    )

    THEME_MODES = (
        ("跟随系统", "system"),
        ("浅色", "light"),
        ("深色", "dark"),
        ("自定时间", "schedule"),
    )

    LYRIC_ELASTIC_MODES = (
        ("双侧悦动", "both"),
        ("左侧轻推", "left"),
    )

    LYRIC_FLOW_STYLES = (
        ("波光逐流", "wave_stream"),
        ("格律宋散", "gelv_songscatter"),
        ("专辑岛", "album_island"),
    )

    GELV_FONT_CHOICES = (
        ("思源宋体", "Source Han Serif SC"),
        ("PingFang", "PingFang SC"),
    )

    NO_LYRICS_DISPLAY_MODES = (
        ("显示特定文字", "text"),
        ("专辑岛模式", "album_island"),
    )

    ALBUM_ISLAND_COVER_COUNTS = (
        ("3 张", 3),
        ("5 张", 5),
        ("7 张", 7),
    )

    ALBUM_ISLAND_SECONDARY_STYLES = (
        ("平排", "shrink_dim"),
        ("透视", "perspective_dim"),
    )

    LOCATION_MODES = (
        ("Finder", "finder"),
        ("Free", "free"),
        ("Tasker", "tasker"),
    )

    TASKBAR_CORNER_STYLES = (
        ("Win11 小圆角", "win11"),
        ("Win10 直角", "win10"),
    )

    COMPACT_ROW_VPAD = 8

    def __init__(self, island_widget: QWidget, settings_manager=None, parent=None):
        super().__init__(parent)
        self.island_widget = island_widget
        self.settings = settings_manager
        self._barrage_adjust_generation = 0
        self._barrage_page_index = 4
        self._diagnostics_page_index = 5
        self._diagnostic_labels: dict[str, QLabel] = {}
        self._detail_panels: list[tuple[QPushButton, QFrame]] = []
        self._applying_theme = False
        self._window_drag_global = None
        self._window_drag_pos = None
        self.setWindowTitle("gpt-island 设置")
        self.setMinimumSize(688, 496)
        self.setWindowFlags(
            (self.windowFlags() | Qt.WindowType.FramelessWindowHint)
            & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._build_ui()
        self._apply_settings_font()

    def _theme(self) -> dict[str, str]:
        return mac_preferences_theme()

    def _apply_theme(self):
        if getattr(self, "_applying_theme", False):
            return
        self._applying_theme = True
        t = self._theme()
        try:
            self.setStyleSheet(mac_preferences_stylesheet(t))
        finally:
            self._applying_theme = False

    def _section_style(self, top: int = 12, bottom: int = 8) -> str:
        return f"font-size: 15px; font-weight: 700; color: {self._theme()['text']}; padding: {top}px 0 {bottom}px 0;"

    def _hint_style(self, top: int = 2, bottom: int = 8) -> str:
        return f"color: {self._theme()['muted']}; padding: {top}px 0 {bottom}px 0;"

    def changeEvent(self, event):
        if getattr(self, "_applying_theme", False):
            super().changeEvent(event)
            return
        if is_theme_change_event(event):
            self._apply_theme()
            for combo in self.findChildren(QComboBox):
                self._style_combo_popup(combo)
        super().changeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_rounded_window_mask()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_rounded_window_mask()

    def _apply_rounded_window_mask(self):
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 14.0, 14.0)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _build_ui(self):
        self._apply_theme()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("MacWindowShell")
        outer.addWidget(shell)

        root = QHBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        side = QFrame()
        side.setObjectName("MacSidebar")
        side.setFixedWidth(157)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(8, 8, 8, 10)
        side_layout.setSpacing(6)

        side_layout.addWidget(mac_traffic_lights(self), 0, Qt.AlignmentFlag.AlignLeft)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("MacSearch")
        self.search_input.setPlaceholderText("搜索")
        self.search_input.textChanged.connect(self._filter_settings_nav)
        side_layout.addWidget(self.search_input)

        self.nav = QListWidget()
        self.nav.setObjectName("MacSidebarList")
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        side_layout.addWidget(self.nav, 1)
        root.addWidget(side)

        content = QWidget()
        content.setObjectName("MacContentPane")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        title_bar = QFrame()
        title_bar.setObjectName("MacTitleBar")
        title_bar.setFixedHeight(42)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(18, 0, 18, 0)
        title_layout.setSpacing(8)
        self.title_label = QLabel()
        self.title_label.setObjectName("MacTitle")
        title_layout.addWidget(self.title_label)
        title_layout.addStretch(1)
        content_layout.addWidget(title_bar)

        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)
        root.addWidget(content, 1)

        pages = (
            ("位置", self._placement_page()),
            ("媒体播放", self._media_page()),
            ("插件", self._plugins_page()),
            ("外观", self._appearance_page()),
            ("弹幕", self._barrage_page()),
            ("诊断", self._diagnostics_page()),
        )
        colors = mac_sidebar_icon_colors()
        for index, (name, page) in enumerate(pages):
            self.nav.addItem(QListWidgetItem(mac_sidebar_icon(colors[index % len(colors)], name[:1]), name))
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self._select_settings_page)
        self.nav.setCurrentRow(0)

    def _select_settings_page(self, row: int):
        if row < 0 or row >= self.stack.count():
            return
        self.stack.setCurrentIndex(row)
        item = self.nav.item(row)
        if item is not None:
            self.title_label.setText(item.text())
        self._settings_page_changed(row)

    def _filter_settings_nav(self, text: str):
        query = str(text or "").strip().lower()
        first_visible = -1
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            hidden = bool(query and query not in item.text().lower())
            item.setHidden(hidden)
            if not hidden and first_visible < 0:
                first_visible = row
        current = self.nav.currentItem()
        if first_visible >= 0 and current is not None and current.isHidden():
            self.nav.setCurrentRow(first_visible)

    def _is_window_drag_area(self, point) -> bool:
        if point.y() > 44 and point.x() > 157:
            return False
        child = self.childAt(point.toPoint())
        if child is None:
            return True
        if isinstance(child, (QLineEdit, QListWidget, QComboBox, QPushButton, IosSwitch, QSlider, QSpinBox)):
            return False
        return True

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._is_window_drag_area(event.position()):
            self._window_drag_global = event.globalPosition().toPoint()
            self._window_drag_pos = self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._window_drag_global is not None and self._window_drag_pos is not None:
            self.move(self._window_drag_pos + event.globalPosition().toPoint() - self._window_drag_global)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._window_drag_global = None
        self._window_drag_pos = None
        super().mouseReleaseEvent(event)

    def _page(self, title: str) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("MacContentScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        page.setObjectName("MacScrollBody")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 12, 22, 20)
        layout.setSpacing(12)
        scroll.setWidget(page)
        scroll._content_layout = layout
        return scroll

    def _card(self, parent_layout: QVBoxLayout) -> QFrame:
        card = QFrame()
        card.setObjectName("MacCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(0)
        parent_layout.addWidget(card)
        return card

    def _row(self, layout: QVBoxLayout, title: str, control: QWidget):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, self.COMPACT_ROW_VPAD, 0, self.COMPACT_ROW_VPAD)
        row_layout.setSpacing(12)
        label = QLabel(title)
        label.setObjectName("RowTitle")
        label.setMinimumWidth(120)
        control.setSizePolicy(QSizePolicy.Policy.Expanding, control.sizePolicy().verticalPolicy())
        row_layout.addWidget(label, 0)
        row_layout.addWidget(control, 1)
        layout.addWidget(row)
        return row

    def _control_group(self, layout: QVBoxLayout) -> QFrame:
        group = QFrame()
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(0)
        layout.addWidget(group)
        return group

    def _detail_panel(self, layout: QVBoxLayout, title: str) -> tuple[QFrame, QVBoxLayout, QPushButton]:
        panel = QFrame()
        panel.setObjectName("DetailPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 4, 0, 0)
        panel_layout.setSpacing(0)

        toggle = QPushButton()
        toggle.setObjectName("DetailToggle")
        toggle.setCheckable(True)
        toggle.setProperty("detail_title", title)
        toggle.setText(f"{title} 详细设置")
        panel_layout.addWidget(toggle)

        body = QFrame()
        body.setObjectName("DetailBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 2, 0, 0)
        body_layout.setSpacing(0)
        body.setVisible(False)
        panel_layout.addWidget(body)

        def update_open_state(opened: bool):
            body.setVisible(opened)
            base_title = str(toggle.property("detail_title") or title)
            toggle.setText(f"{base_title} 详细设置" + (" · 收起" if opened else ""))

        toggle.toggled.connect(update_open_state)
        layout.addWidget(panel)
        self._detail_panels.append((toggle, body))
        return panel, body_layout, toggle

    def _set_detail_panel_title(self, toggle: QPushButton, title: str):
        toggle.setProperty("detail_title", title)
        opened = bool(toggle.isChecked())
        toggle.setText(f"{title} 详细设置" + (" · 收起" if opened else ""))

    def _collapse_detail_panels(self):
        for toggle, body in getattr(self, "_detail_panels", []):
            toggle.blockSignals(True)
            toggle.setChecked(False)
            toggle.blockSignals(False)
            body.setVisible(False)
            base_title = str(toggle.property("detail_title") or "详细")
            toggle.setText(f"{base_title} 详细设置")

    def _preview_frame(self, height: int = 42) -> QFrame:
        frame = QFrame()
        frame.setFixedHeight(height)
        frame.setStyleSheet("QFrame { border-radius: 8px; background: #111827; border: 1px solid #e5e7eb; }")
        return frame

    def _color_buttons_row(self, layout: QVBoxLayout, title: str, colors) -> list[ColorButton]:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, self.COMPACT_ROW_VPAD, 0, self.COMPACT_ROW_VPAD)
        row_layout.setSpacing(10)
        label = QLabel(title)
        label.setObjectName("RowTitle")
        label.setMinimumWidth(120)
        row_layout.addWidget(label, 0)
        buttons = [ColorButton(str(color), self._set_empty_bar_custom_colors) for color in list(colors)[:3]]
        while len(buttons) < 3:
            buttons.append(ColorButton(("#46D6FF", "#73FFA6", "#FF5A86")[len(buttons)], self._set_empty_bar_custom_colors))
        for button in buttons:
            row_layout.addWidget(button)
        row_layout.addStretch(1)
        layout.addWidget(row)
        return buttons

    def _check(self, text: str, checked: bool, callback) -> IosSwitch:
        box = IosSwitch(text, checked)
        box.setChecked(checked)
        box.toggled.connect(callback)
        return box

    def _slider_row(self, layout: QVBoxLayout, title: str, minimum: int, maximum: int, value: int, callback, suffix: str = "") -> QSlider:
        row = QWidget()
        grid = QGridLayout(row)
        grid.setContentsMargins(0, self.COMPACT_ROW_VPAD, 0, self.COMPACT_ROW_VPAD)
        grid.setHorizontalSpacing(12)
        title_label = QLabel(title)
        title_label.setObjectName("RowTitle")
        value_label = QLabel(f"{value}{suffix}")
        value_label.setObjectName("Value")
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider._value_label = value_label
        slider._value_suffix = suffix
        slider.valueChanged.connect(lambda v: value_label.setText(f"{v}{suffix}"))
        slider.valueChanged.connect(callback)
        grid.addWidget(title_label, 0, 0)
        grid.addWidget(value_label, 0, 1)
        grid.addWidget(slider, 1, 0, 1, 2)
        layout.addWidget(row)
        return slider

    def _combo(self, items: list[tuple[str, object]], current, callback) -> QComboBox:
        combo = NoWheelComboBox()
        for label, data in items:
            combo.addItem(label, data)
        for index in range(combo.count()):
            if combo.itemData(index) == current:
                combo.setCurrentIndex(index)
                break
        combo.currentIndexChanged.connect(callback)
        self._style_combo_popup(combo)
        return combo

    def _set_slider_value(self, slider: QSlider, value: int):
        slider.blockSignals(True)
        slider.setValue(value)
        slider.blockSignals(False)
        value_label = getattr(slider, "_value_label", None)
        if value_label is not None:
            value_label.setText(f"{value}{getattr(slider, '_value_suffix', '')}")

    def _style_combo_popup(self, combo: QComboBox):
        style_combo_popup(combo, self._theme())

    def _location_mode_items(self) -> list[tuple[str, str]]:
        manual_finder = bool(getattr(self.island_widget, "manual_finder_available", False))
        if manual_finder:
            return [("Free", "free"), ("Finder", "finder")]
        return [("Free", "free"), ("Tasker", "tasker")]

    def _placement_page(self) -> QWidget:
        page = self._page("位置")
        layout: QVBoxLayout = page._content_layout
        card = self._card(layout)
        c = card.layout()

        self.super_topmost_checkbox = self._check("灵动岛超级置顶", bool(getattr(self.island_widget, "super_topmost_enabled", True)), self._set_super_topmost)
        self.location_mode = self._combo(
            self._location_mode_items(),
            str(getattr(self.island_widget, "location_mode", self._setting_value("v2_location_mode", "free"))),
            self._set_location_mode,
        )
        self._row(c, "位置模式", self.location_mode)
        self.manual_finder_checkbox = self._check("手动确认存在 MyFinder", bool(getattr(self.island_widget, "manual_finder_available", False)), self._set_manual_finder_available)
        c.addWidget(self.manual_finder_checkbox)
        self.taskbar_corner_style = self._combo(
            list(self.TASKBAR_CORNER_STYLES),
            str(getattr(self.island_widget, "taskbar_corner_style", self._setting_value("v2_taskbar_corner_style", "win11"))),
            self._set_taskbar_corner_style,
        )
        self._row(c, "Tasker 圆角", self.taskbar_corner_style)
        self.taskbar_left_handle_color = ColorButton(
            str(getattr(self.island_widget, "taskbar_left_handle_color", self._setting_value("v2_taskbar_left_handle_color", "#8E8E93"))),
            self._set_taskbar_left_handle_color,
        )
        self._row(c, "Tasker 左侧竖线", self.taskbar_left_handle_color)
        c.addWidget(self.super_topmost_checkbox)
        self.expanded_checkbox = self._check("默认展开", bool(getattr(self.island_widget, "expanded", False)), self._set_expanded)
        c.addWidget(self.expanded_checkbox)
        self.auto_bounds_checkbox = self._check("自动识别上下边界", bool(getattr(self.island_widget, "auto_expand_bounds", True)), self._set_auto_expand_bounds)
        c.addWidget(self.auto_bounds_checkbox)
        self.expand_direction = self._combo([("自动", "auto"), ("向下", "down"), ("向上", "up")], str(getattr(self.island_widget, "expand_direction", "auto")), self._set_expand_direction)
        self._row(c, "展开方向", self.expand_direction)

        message_title = QLabel("消息识别")
        message_title.setStyleSheet(self._section_style(16, 2))
        c.addWidget(message_title)
        self.message_detection_checkbox = self._check("消息识别总开关", bool(self._setting_value("v2_enable_message_detection", True)), self._set_message_detection)
        c.addWidget(self.message_detection_checkbox)
        self.wechat_notification_checkbox = self._check("微信新消息通知", bool(self._setting_value("v2_enable_wechat_notification", True)), self._set_wechat_notification)
        self.wechat_detail_checkbox = self._check("微信详细消息通知（高性能）", bool(self._setting_value("v2_enable_wechat_detail_notification", False)), self._set_wechat_detail_notification)
        self.qq_notification_checkbox = self._check("QQ（待适配）", bool(self._setting_value("v2_enable_qq_notification", False)), self._set_qq_notification)
        self.dingtalk_notification_checkbox = self._check("钉钉（待适配）", bool(self._setting_value("v2_enable_dingtalk_notification", False)), self._set_dingtalk_notification)
        c.addWidget(self.wechat_notification_checkbox)
        c.addWidget(self.wechat_detail_checkbox)
        c.addWidget(self.qq_notification_checkbox)
        c.addWidget(self.dingtalk_notification_checkbox)

        self._update_message_controls()
        self._update_taskbar_controls()
        c.addStretch(1)
        layout.addStretch(1)
        return page

    def _media_page(self) -> QWidget:
        page = self._page("媒体播放")
        layout: QVBoxLayout = page._content_layout
        card = self._card(layout)
        c = card.layout()

        self.music_checkbox = self._check("音乐岛", bool(getattr(self.island_widget, "music_playing", True)), self._set_music_playing)
        self.music_auto_checkbox = self._check("音乐岛自动识别：无音频时隐藏", bool(getattr(self.island_widget, "music_auto_detect", False)), self._set_music_auto_detect)
        c.addWidget(self.music_checkbox)
        c.addWidget(self.music_auto_checkbox)

        progress_title = QLabel("进度识别")
        progress_title.setStyleSheet(self._section_style(2, 8))
        c.addWidget(progress_title)
        self.player_progress_plugin_checkbox = self._check(
            "启用进度插件",
            bool(self._setting_value("v2_enable_player_progress_plugin", False)),
            self._set_player_progress_plugin_enabled,
        )
        c.addWidget(self.player_progress_plugin_checkbox)
        self.player_progress_auto_detect_checkbox = self._check(
            "插件自动识别",
            bool(self._setting_value("v2_player_progress_auto_detect", False)),
            self._set_player_progress_auto_detect,
        )
        c.addWidget(self.player_progress_auto_detect_checkbox)
        self.music_player_mode = self._combo(
            list(self.MUSIC_PLAYER_MODES),
            self._music_player_mode_setting(),
            self._set_music_player_mode,
        )
        self._row(c, "进度识别服务", self.music_player_mode)
        self.ncm_path_input = QLineEdit(str(self._setting_value("v2_netease_betterncm_path", "") or ""))
        self.ncm_path_input.setPlaceholderText(r"C:\Program Files\NetEase\CloudMusic\cloudmusic.exe")
        self.ncm_path_input.editingFinished.connect(self._set_netease_betterncm_path)
        self._row(c, "NCM 路径", self.ncm_path_input)
        player_hint = QLabel("关闭插件时只使用系统媒体信息；开启后才会按所选服务调用专项进度插件。网易云与QQ已可用，其它服务先保留入口。")
        player_hint.setWordWrap(True)
        player_hint.setStyleSheet(self._hint_style(2, 12))
        c.addWidget(player_hint)
        self._update_player_progress_controls()

        lyric_title = QLabel("歌词显示")
        lyric_title.setStyleSheet(self._section_style(12, 8))
        c.addWidget(lyric_title)
        self.no_lyrics_input = QLineEdit(str(getattr(self.island_widget, "no_lyrics_text", "暂无歌词")))
        self.no_lyrics_input.setMaxLength(16)
        self.no_lyrics_input.setPlaceholderText("暂无歌词")
        self.no_lyrics_input.textChanged.connect(self._set_no_lyrics_text)
        self._row(c, "无歌词时显示", self.no_lyrics_input)

        self.lyric_flow_style = self._combo(
            list(self.LYRIC_FLOW_STYLES),
            str(getattr(self.island_widget, "lyric_flow_style", self._setting_value("v2_lyric_flow_style", "wave_stream"))),
            self._set_lyric_flow_style,
        )
        self._row(c, "文字流转", self.lyric_flow_style)
        self.lyric_offset = self._slider_row(c, "歌词偏移", -12000, 12000, int(round(float(getattr(self.island_widget, "lyric_offset_seconds", 0.0)) * 1000)), self._set_lyric_offset, " ms")

        self._gelv_controls, gelv_layout, self._gelv_detail_toggle = self._detail_panel(c, "格律宋散")
        self.gelv_font_family = self._combo(
            list(self.GELV_FONT_CHOICES),
            str(getattr(self.island_widget, "gelv_font_family", self._setting_value("v2_gelv_font_family", "Source Han Serif SC"))),
            self._set_gelv_font_family,
        )
        self._row(gelv_layout, "格律字体", self.gelv_font_family)
        self.gelv_primary_font_size = self._slider_row(gelv_layout, "格律主行字号", 14, 30, max(14, min(30, int(getattr(self.island_widget, "gelv_primary_font_size", self._setting_value("v2_gelv_primary_font_size", 22))))), self._set_gelv_primary_font_size, " px")
        self.gelv_secondary_font_size = self._slider_row(gelv_layout, "格律次行字号", 8, 24, max(8, min(24, int(getattr(self.island_widget, "gelv_secondary_font_size", self._setting_value("v2_gelv_secondary_font_size", 12))))), self._set_gelv_secondary_font_size, " px")
        self.gelv_initial_word_spacing = self._slider_row(gelv_layout, "格律初始间距", 100, 320, max(100, min(320, int(getattr(self.island_widget, "gelv_initial_word_spacing", self._setting_value("v2_gelv_initial_word_spacing", 220))))), self._set_gelv_initial_word_spacing, "%")
        self.gelv_word_spacing = self._slider_row(gelv_layout, "格律散开间距", 100, 420, max(100, min(420, int(getattr(self.island_widget, "gelv_word_spacing", self._setting_value("v2_gelv_word_spacing", 320))))), self._set_gelv_word_spacing, "%")
        self.gelv_size_shift_checkbox = self._check(
            "格律字号变化",
            bool(getattr(self.island_widget, "gelv_size_shift_enabled", True)),
            self._set_gelv_size_shift_enabled,
        )
        gelv_layout.addWidget(self.gelv_size_shift_checkbox)
        self.gelv_compact_cover_checkbox = self._check(
            "收缩态显示专辑封面",
            bool(getattr(self.island_widget, "compact_lyric_show_cover", self._setting_value("v2_compact_lyric_show_cover", True))),
            self._set_compact_lyric_show_cover,
        )
        gelv_layout.addWidget(self.gelv_compact_cover_checkbox)
        self.gelv_dual_line_checkbox = self._check(
            "格律双语显示",
            bool(getattr(self.island_widget, "enable_dual_line_lyrics", False)),
            self._set_dual_line_lyrics,
        )
        gelv_layout.addWidget(self.gelv_dual_line_checkbox)
        self.gelv_dual_line_font_size = self._slider_row(
            gelv_layout,
            "同行歌词字号",
            8,
            30,
            max(8, min(30, int(getattr(self.island_widget, "gelv_dual_line_font_size", self._setting_value("v2_gelv_dual_line_font_size", 17))))),
            self._set_gelv_dual_line_font_size,
            " px",
        )
        self.gelv_color_fill_checkbox = self._check(
            "逐字染色",
            bool(getattr(self.island_widget, "gelv_color_fill_enabled", False)),
            self._set_gelv_color_fill_enabled,
        )
        gelv_layout.addWidget(self.gelv_color_fill_checkbox)
        self.gelv_color_fill_fallback = ColorButton(
            str(getattr(self.island_widget, "gelv_color_fill_fallback", "#00D7FF")),
            self._set_gelv_color_fill_fallback,
        )
        self._row(gelv_layout, "染色默认色", self.gelv_color_fill_fallback)

        self._wave_stream_controls, wave_layout, self._wave_stream_detail_toggle = self._detail_panel(c, "波光逐流")
        self.lyric_font_size = self._slider_row(wave_layout, "波光字号", 14, 24, max(14, min(24, int(getattr(self.island_widget, "lyric_font_size", 18)))), self._set_lyric_font_size, " px")
        self.lyric_elastic_strength = self._slider_row(wave_layout, "悦动强度", 0, 160, max(0, min(160, int(getattr(self.island_widget, "lyric_elastic_strength", 100)))), self._set_lyric_elastic_strength, "%")
        self.lyric_word_spacing = self._slider_row(wave_layout, "字词间距", 60, 150, max(60, min(150, int(getattr(self.island_widget, "lyric_word_spacing", 100)))), self._set_lyric_word_spacing, "%")
        self.wave_color_fill_checkbox = self._check(
            "波光逐字染色",
            bool(getattr(self.island_widget, "wave_color_fill_enabled", False)),
            self._set_wave_color_fill_enabled,
        )
        wave_layout.addWidget(self.wave_color_fill_checkbox)
        self.wave_color_fill_fallback = ColorButton(
            str(getattr(self.island_widget, "wave_color_fill_fallback", "#00D7FF")),
            self._set_wave_color_fill_fallback,
        )
        self._row(wave_layout, "波光染色默认色", self.wave_color_fill_fallback)
        self.dual_line_lyrics_checkbox = self._check(
            "允许双行歌词",
            bool(getattr(self.island_widget, "enable_dual_line_lyrics", False)),
            self._set_dual_line_lyrics,
        )
        wave_layout.addWidget(self.dual_line_lyrics_checkbox)
        self.dual_line_font_size = self._slider_row(wave_layout, "双语字号", 8, 20, max(8, min(20, int(getattr(self.island_widget, "dual_line_font_size", self._setting_value("v2_dual_line_font_size", 12))))), self._set_dual_line_font_size, " px")
        self.dual_line_secondary_weight = self._slider_row(wave_layout, "次行字重", 40, 100, max(40, min(100, int(getattr(self.island_widget, "dual_line_secondary_weight", 72)))), self._set_dual_line_secondary_weight, "%")
        self.dual_line_secondary_spacing = self._slider_row(wave_layout, "次行字词间距", 60, 150, max(60, min(150, int(getattr(self.island_widget, "dual_line_secondary_spacing", 100)))), self._set_dual_line_secondary_spacing, "%")
        self.lyric_elastic_mode = self._combo(list(self.LYRIC_ELASTIC_MODES), str(getattr(self.island_widget, "lyric_elastic_mode", "both")), self._set_lyric_elastic_mode)
        self._row(wave_layout, "悦动方式", self.lyric_elastic_mode)
        self.wave_compact_cover_checkbox = self._check(
            "收缩态显示专辑封面",
            bool(getattr(self.island_widget, "compact_lyric_show_cover", self._setting_value("v2_compact_lyric_show_cover", True))),
            self._set_compact_lyric_show_cover,
        )
        wave_layout.addWidget(self.wave_compact_cover_checkbox)

        self.no_lyrics_display_mode = self._combo(
            list(self.NO_LYRICS_DISPLAY_MODES),
            str(getattr(self.island_widget, "no_lyrics_display_mode", self._setting_value("v2_no_lyrics_display_mode", "text"))),
            self._set_no_lyrics_display_mode,
        )
        self.no_lyrics_display_mode_row = self._row(c, "无歌词模式", self.no_lyrics_display_mode)
        self._album_island_controls, album_layout, self._album_island_detail_toggle = self._detail_panel(c, "专辑岛")
        self.album_island_cover_count = self._combo(
            list(self.ALBUM_ISLAND_COVER_COUNTS),
            int(getattr(self.island_widget, "album_island_cover_count", self._setting_value("v2_album_island_cover_count", 5))),
            self._set_album_island_cover_count,
        )
        self._row(album_layout, "专辑数量", self.album_island_cover_count)
        self.album_island_secondary_style = self._combo(
            list(self.ALBUM_ISLAND_SECONDARY_STYLES),
            str(getattr(self.island_widget, "album_island_secondary_style", self._setting_value("v2_album_island_secondary_style", "perspective_dim"))),
            self._set_album_island_secondary_style,
        )
        self._row(album_layout, "次专辑效果", self.album_island_secondary_style)
        self.album_island_perspective_amount = self._slider_row(
            album_layout,
            "透视程度",
            0,
            200,
            max(0, min(200, int(getattr(self.island_widget, "album_island_perspective_amount", self._setting_value("v2_album_island_perspective_amount", 100))))),
            self._set_album_island_perspective_amount,
            "%",
        )
        self.album_island_cover_size = self._slider_row(
            album_layout,
            "专辑封面大小",
            18,
            44,
            max(18, min(44, int(getattr(self.island_widget, "album_island_cover_size", self._setting_value("v2_album_island_cover_size", 30))))),
            self._set_album_island_cover_size,
            " px",
        )
        self.album_island_spacing = self._slider_row(
            album_layout,
            "专辑间距",
            48,
            170,
            max(48, min(170, int(getattr(self.island_widget, "album_island_spacing", self._setting_value("v2_album_island_spacing", 112))))),
            self._set_album_island_spacing,
            "%",
        )

        hint = QLabel("会根据当前播放器优先选择歌词源，并缓存到项目 lyrics 目录；也可手动放入 .lrc 文件。")
        hint.setWordWrap(True)
        hint.setStyleSheet(self._hint_style(2, 8))
        c.addWidget(hint)
        self._update_lyric_flow_controls()
        c.addStretch(1)
        layout.addStretch(1)
        return page

    def _appearance_page(self) -> QWidget:
        page = self._page("外观")
        layout: QVBoxLayout = page._content_layout
        card = self._card(layout)
        c = card.layout()

        self.theme_mode = self._combo(
            list(self.THEME_MODES),
            str(self._setting_value("v2_theme_mode", "system") or "system"),
            self._set_theme_mode,
        )
        self._row(c, "界面模式", self.theme_mode)
        self.settings_font_combo = self._combo(
            self._settings_font_items(),
            str(self._setting_value("v2_settings_font_family", self.FONT_CHOICES[0]) or self.FONT_CHOICES[0]),
            self._set_settings_font_family,
        )
        self._row(c, "设置字体", self.settings_font_combo)
        self.import_settings_font_button = QPushButton("导入字体")
        self.import_settings_font_button.clicked.connect(self._import_settings_font)
        self._row(c, "导入字体", self.import_settings_font_button)
        self.theme_dark_start = QLineEdit(str(self._setting_value("v2_theme_dark_start", "22:00") or "22:00"))
        self.theme_dark_start.setPlaceholderText("22:00")
        self.theme_dark_start.editingFinished.connect(self._set_theme_dark_start)
        self._row(c, "深色开始", self.theme_dark_start)
        self.theme_dark_end = QLineEdit(str(self._setting_value("v2_theme_dark_end", "07:00") or "07:00"))
        self.theme_dark_end.setPlaceholderText("07:00")
        self.theme_dark_end.editingFinished.connect(self._set_theme_dark_end)
        self._row(c, "深色结束", self.theme_dark_end)

        self.high_quality_font_checkbox = self._check("高质量字体渲染", bool(getattr(self.island_widget, "high_quality_font_rendering", False)), self._set_high_quality_font_rendering)
        c.addWidget(self.high_quality_font_checkbox)
        self.high_quality_font_checkbox.hide()
        self.music_ambience_style = self._combo(list(self.MUSIC_AMBIENCE_STYLES), str(getattr(self.island_widget, "music_ambience_style", "sea_level")), self._set_music_ambience_style)
        self._row(c, "音乐岛背景样式", self.music_ambience_style)
        self.music_island_style = self._combo(
            list(self.MUSIC_ISLAND_STYLES),
            str(getattr(self.island_widget, "music_island_style", self._setting_value("v2_music_island_style", "standard")) or "standard"),
            self._set_music_island_style,
        )
        self._row(c, "音乐岛样式", self.music_island_style)
        self.music_preview = self._preview_frame(54)
        c.addWidget(self.music_preview)
        self._music_ambience_controls, music_ambience_layout, self._music_ambience_detail_toggle = self._detail_panel(c, "音乐岛背景样式")
        self.wave_slider = self._slider_row(music_ambience_layout, "氛围光增强", 0, 100, max(0, min(100, int(getattr(self.island_widget, "ambient_intensity", 0.0) * 100))), self._set_wave_intensity, "%")
        self.music_flow_slider = self._slider_row(music_ambience_layout, "流光浮动", 0, 100, max(0, min(100, int(getattr(self.island_widget, "music_flow_intensity", 0.74) * 100))), self._set_music_flow_intensity, "%")
        self.compact_duotone_balance = self._slider_row(music_ambience_layout, "收缩双色占比", 0, 100, max(0, min(100, int(getattr(self.island_widget, "compact_duotone_balance", 50)))), self._set_compact_duotone_balance, "%")
        self.expanded_duotone_balance = self._slider_row(music_ambience_layout, "展开双色占比", 0, 100, max(0, min(100, int(getattr(self.island_widget, "expanded_duotone_balance", 50)))), self._set_expanded_duotone_balance, "%")
        self.fps_slider = self._slider_row(c, "动画帧率", 24, 90, max(24, min(90, int(getattr(self.island_widget, "animation_fps", 60)))), self._set_animation_fps, " FPS")
        self.animation_speed = self._combo(list(self.ANIMATION_SPEEDS), float(getattr(self.island_widget, "animation_speed", 1.0)), self._set_animation_speed)
        self._row(c, "动画倍速", self.animation_speed)

        if self.fps_slider.parentWidget():
            self.fps_slider.parentWidget().hide()
        if self.animation_speed.parentWidget():
            self.animation_speed.parentWidget().hide()

        self.font_combo = None

        self.empty_palette = self._combo([(label, key) for key, label in self.EMPTY_BAR_PALETTES], str(getattr(self.island_widget, "empty_bar_palette", "aurora")), self._set_empty_bar_palette)
        self._row(c, "空态流光", self.empty_palette)
        self.empty_preview = self._preview_frame(34)
        c.addWidget(self.empty_preview)
        self.empty_bar_width = self._slider_row(c, "横条长度", 70, 260, int(getattr(self.island_widget, "empty_bar_width", 92)), self._set_empty_bar_width, " px")
        self.empty_bar_height = self._slider_row(c, "横条粗细", 8, 28, int(getattr(self.island_widget, "empty_bar_height", 14)), self._set_empty_bar_height, " px")
        self.empty_bar_y_offset = self._slider_row(c, "横条位置", -80, 160, int(getattr(self.island_widget, "empty_bar_y_offset", 0)), self._set_empty_bar_y_offset, " px")
        self.empty_bar_opacity = self._slider_row(c, "横条不透明度", 10, 100, int(getattr(self.island_widget, "empty_bar_opacity", 100)), self._set_empty_bar_opacity, "%")
        self.custom_color_inputs = self._color_buttons_row(c, "自定义颜色", getattr(self.island_widget, "empty_bar_custom_colors", ("#46D6FF", "#73FFA6", "#FF5A86")))
        self._update_music_ambience_controls()
        self._update_previews()
        layout.addStretch(1)
        return page

    def _plugins_page(self) -> QWidget:
        page = self._page("插件")
        layout: QVBoxLayout = page._content_layout

        general = self._card(layout)
        g = general.layout()
        self.plugin_checkbox = self._check("插件岛", bool(getattr(self.island_widget, "persistent_plugin", True)), self._set_persistent_plugin)
        g.addWidget(self.plugin_checkbox)
        enabled_plugins = self._enabled_plugins_setting()
        self.plugin_time_enabled = self._check("时间插件", "time" in enabled_plugins, self._set_enabled_plugins_from_checks)
        self.plugin_weather_enabled = self._check("天气插件", "weather" in enabled_plugins, self._set_enabled_plugins_from_checks)
        self.plugin_agenda_enabled = self._check("日程插件", "agenda" in enabled_plugins, self._set_enabled_plugins_from_checks)
        g.addWidget(self.plugin_time_enabled)
        g.addWidget(self.plugin_weather_enabled)
        g.addWidget(self.plugin_agenda_enabled)
        self.side_island_style = self._combo(
            list(self.SIDE_ISLAND_STYLES),
            str(getattr(self.island_widget, "side_island_style", self._setting_value("v2_side_island_style", "standard")) or "standard"),
            self._set_side_island_style,
        )
        self._row(g, "副岛样式", self.side_island_style)
        self.plugin_island_style = self._combo(
            list(self.PLUGIN_ISLAND_STYLES),
            str(getattr(self.island_widget, "plugin_island_style", self._setting_value("v2_plugin_island_style", "standard")) or "standard"),
            self._set_plugin_island_style,
        )
        self._row(g, "插件岛样式", self.plugin_island_style)
        self.seconds_checkbox = self._check("时间显示秒数", bool(getattr(self.island_widget, "show_seconds", True)), self._set_show_seconds)
        g.addWidget(self.seconds_checkbox)

        time_title = QLabel("时间 / 计时器")
        time_title.setObjectName("Section")
        layout.addWidget(time_title)
        time_card = self._card(layout)
        t = time_card.layout()
        self.plugin_timezone = self._combo(
            list(self.TIMEZONES),
            str(self._setting_value("v2_plugin_timezone", "local") or "local"),
            self._set_plugin_timezone_combo,
        )
        self._row(t, "时区", self.plugin_timezone)
        self.plugin_timezone_text = QLineEdit(str(self._setting_value("v2_plugin_timezone", "local") or "local"))
        self.plugin_timezone_text.setPlaceholderText("local 或 UTC+8")
        self.plugin_timezone_text.editingFinished.connect(self._set_plugin_timezone_text)
        self._row(t, "自定义时区", self.plugin_timezone_text)
        self.plugin_alarm_summary = QLabel(self._alarm_rows_summary())
        self.plugin_alarm_summary.setObjectName("Value")
        self._row(t, "闹钟", self.plugin_alarm_summary)
        self.time_alarm_side_display = self._combo(
            list(self.TIME_ALARM_SIDE_DISPLAYS),
            str(getattr(self.island_widget, "time_alarm_side_display", self._setting_value("v2_time_alarm_side_display", "note")) or "note"),
            self._set_time_alarm_side_display,
        )
        self._row(t, "闹钟副岛", self.time_alarm_side_display)
        alarm_button_row = QHBoxLayout()
        alarm_open = QPushButton("打开闹钟设置")
        alarm_open.setObjectName("Primary")
        alarm_open.clicked.connect(self._open_time_alarm_editor)
        alarm_button_row.addWidget(alarm_open)
        alarm_button_row.addStretch(1)
        t.addLayout(alarm_button_row)
        quick_row = QHBoxLayout()
        for minutes in (5, 10, 25):
            button = QPushButton(f"{minutes} 分钟")
            button.clicked.connect(lambda _checked=False, m=minutes: self._start_plugin_timer(m))
            quick_row.addWidget(button)
        clear = QPushButton("清除")
        clear.setObjectName("Danger")
        clear.clicked.connect(self._clear_plugin_timer)
        quick_row.addWidget(clear)
        t.addLayout(quick_row)
        timer_row = QHBoxLayout()
        self.plugin_timer_minutes = NoWheelSpinBox()
        self.plugin_timer_minutes.setRange(1, 600)
        self.plugin_timer_minutes.setValue(15)
        self.plugin_timer_minutes.setSuffix(" 分钟")
        timer_row.addWidget(self.plugin_timer_minutes, 1)
        start = QPushButton("开始")
        start.setObjectName("Primary")
        start.clicked.connect(lambda: self._start_plugin_timer(int(self.plugin_timer_minutes.value())))
        timer_row.addWidget(start)
        t.addLayout(timer_row)

        weather_title = QLabel("天气")
        weather_title.setObjectName("Section")
        layout.addWidget(weather_title)
        weather_card = self._card(layout)
        w = weather_card.layout()
        self.weather_use_location = self._check(
            "优先读取定位",
            bool(self._setting_value("v2_weather_use_location", True)),
            self._set_weather_use_location,
        )
        w.addWidget(self.weather_use_location)
        self.weather_manual_location = QLineEdit(
            str(self._setting_value("v2_weather_manual_location", self._setting_value("v2_weather_location", "北京")) or "北京")
        )
        self.weather_manual_location.setPlaceholderText("北京、上海、深圳")
        self.weather_manual_location.editingFinished.connect(self._set_weather_manual_location)
        self._row(w, "手动城市", self.weather_manual_location)
        refresh = QPushButton("刷新天气")
        refresh.setObjectName("Primary")
        refresh.clicked.connect(lambda: self._save("v2_weather_refresh_nonce", str(time.monotonic())))
        w.addWidget(refresh, 0, Qt.AlignmentFlag.AlignRight)

        agenda_title = QLabel("日程")
        agenda_title.setObjectName("Section")
        layout.addWidget(agenda_title)
        agenda_card = self._card(layout)
        a = agenda_card.layout()
        self.schedule_json_input = QLineEdit(str(self._setting_value("v2_schedule_json_path", "") or ""))
        self.schedule_json_input.setPlaceholderText("data/schedule.json")
        self.schedule_json_input.editingFinished.connect(lambda: self._set_schedule_json_path(self.schedule_json_input.text()))
        self._row(a, "JSON", self.schedule_json_input)
        self.schedule_ics_input = QLineEdit(str(self._setting_value("v2_schedule_ics_path", "") or ""))
        self.schedule_ics_input.setPlaceholderText("data/schedule.ics")
        self.schedule_ics_input.editingFinished.connect(lambda: self._set_schedule_ics_path(self.schedule_ics_input.text()))
        self._row(a, "ICS", self.schedule_ics_input)
        self.schedule_reminder_minutes = QLineEdit(str(self._setting_value("v2_schedule_reminder_minutes", "15,30,5") or "15,30,5"))
        self.schedule_reminder_minutes.setPlaceholderText("15,30,5")
        self.schedule_reminder_minutes.editingFinished.connect(self._set_schedule_reminder_minutes)
        self._row(a, "提醒阈值", self.schedule_reminder_minutes)
        self._row(a, "待做清单", QLabel("课表读取录入"))
        self._row(a, "提醒路线", QLabel("课表提醒功能"))

        layout.addStretch(1)
        return page

    def _barrage_page(self) -> QWidget:
        page = self._page("弹幕")
        layout: QVBoxLayout = page._content_layout
        card = self._card(layout)
        c = card.layout()

        self.auto_bounds_checkbox = self._check("自动识别上下边界", bool(getattr(self.island_widget, "auto_expand_bounds", True)), self._set_auto_expand_bounds)
        c.addWidget(self.auto_bounds_checkbox)
        self.expand_direction = self._combo([("自动", "auto"), ("向下", "down"), ("向上", "up")], str(getattr(self.island_widget, "expand_direction", "auto")), self._set_expand_direction)
        self._row(c, "展开方向", self.expand_direction)
        self.barrage_y_offset = self._slider_row(c, "弹幕位置", 0, 240, int(getattr(self.island_widget, "barrage_y_offset", 0)), self._set_barrage_y_offset, " px")
        self.barrage_font_size = self._slider_row(c, "弹幕字号", 8, 20, int(getattr(self.island_widget, "barrage_font_size", 10)), self._set_barrage_font_size, " px")
        self.barrage_speed = self._slider_row(c, "弹幕速度", 40, 220, int(getattr(self.island_widget, "barrage_speed", 100)), self._set_barrage_speed, "%")
        self.barrage_backdrop = self._check("弹幕背景遮罩", bool(getattr(self.island_widget, "barrage_use_backdrop", True)), self._set_barrage_backdrop)
        self.barrage_icon = self._check("显示通知图标", bool(getattr(self.island_widget, "barrage_show_icon", True)), self._set_barrage_icon)
        c.addWidget(self.barrage_backdrop)
        c.addWidget(self.barrage_icon)
        layout.addStretch(1)
        return page

    def _actions_page(self) -> QWidget:
        page = self._page("快捷动作")
        layout: QVBoxLayout = page._content_layout
        card = self._card(layout)
        c = card.layout()

        row_one = QHBoxLayout()
        add_btn = QPushButton("新增消息")
        add_btn.setObjectName("Primary")
        add_btn.clicked.connect(self._add_message)
        clear_btn = QPushButton("清空消息")
        clear_btn.clicked.connect(self._clear_messages)
        row_one.addWidget(add_btn)
        row_one.addWidget(clear_btn)
        c.addLayout(row_one)

        row_two = QHBoxLayout()
        for index, label in enumerate(("插件 1", "插件 2", "插件 3")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, i=index: self._focus_plugin(i))
            row_two.addWidget(button)
        c.addLayout(row_two)

        hint = QLabel("设置会即时应用，并在下次启动时恢复。")
        hint.setStyleSheet(self._hint_style(18, 0))
        c.addWidget(hint)
        c.addStretch(1)
        layout.addStretch(1)
        return page

    def _diagnostics_page(self) -> QWidget:
        page = self._page("诊断")
        layout: QVBoxLayout = page._content_layout
        card = self._card(layout)
        c = card.layout()
        self._diagnostic_labels = {}
        for key, title in (
            ("provider_status", "媒体状态"),
            ("winsdk_loaded", "Windows 媒体 SDK"),
            ("music_player_mode", "进度识别服务"),
            ("player_progress_plugin_enabled", "进度插件"),
            ("player_progress_auto_detect", "插件自动识别"),
            ("music_player_adapter", "当前适配器"),
            ("media_session_count", "Windows 媒体会话"),
            ("media_session_candidates", "媒体会话候选"),
            ("source_app", "来源应用"),
            ("track_title", "歌曲标题"),
            ("track_artist", "歌曲作者"),
            ("track_id", "Track ID"),
            ("position_seconds", "当前位置"),
            ("position_source", "位置来源"),
            ("position_confidence", "位置置信度"),
            ("estimated_position_seconds", "估算位置"),
            ("duration_seconds", "歌曲时长"),
            ("lyric_offset_seconds", "歌词偏移"),
            ("lyrics_source", "歌词来源"),
            ("timed_lyrics_count", "带时间歌词数"),
            ("visible_lyric_index", "当前歌词索引"),
            ("visible_current_lyric", "当前歌词"),
            ("lyric_phase_progress", "歌词进度"),
            ("lyric_phase_span", "歌词跨度"),
            ("lyric_phase_source", "同步来源"),
            ("netease_position", "网易云锚点"),
            ("qq_position", "QQ进度"),
            ("audio_status", "音频采集"),
            ("audio_has_recent_frame", "实时音频帧"),
            ("audio_last_error", "音频错误"),
            ("message_cached", "消息缓存"),
            ("message_notification_access", "通知权限"),
            ("message_notification_apps", "通知应用"),
            ("message_detection_enabled", "消息识别总开关"),
            ("wechat_notification_enabled", "微信新消息通知"),
            ("wechat_detail_notification_enabled", "微信详细消息通知"),
            ("qq_notification_enabled", "QQ 消息"),
            ("dingtalk_notification_enabled", "钉钉消息"),
            ("message_wechat", "微信 UIA"),
            ("message_wechat_native", "微信窗口句柄"),
            ("message_wechat_items", "微信会话样本"),
            ("message_wechat_error", "微信识别错误"),
        ):
            label = QLabel("-")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._diagnostic_labels[key] = label
            self._row(c, title, label)
        refresh = QPushButton("刷新诊断")
        refresh.setObjectName("Primary")
        refresh.clicked.connect(self._refresh_diagnostics)
        c.addWidget(refresh)
        c.addStretch(1)
        layout.addStretch(1)
        self._refresh_diagnostics()
        return page

    def sync_from_widget(self):
        enabled_plugins = self._enabled_plugins_setting()
        pairs = (
            (self.music_checkbox, bool(getattr(self.island_widget, "music_playing", True))),
            (self.music_auto_checkbox, bool(getattr(self.island_widget, "music_auto_detect", False))),
            (self.plugin_checkbox, bool(getattr(self.island_widget, "persistent_plugin", True))),
            (self.plugin_time_enabled, "time" in enabled_plugins),
            (self.plugin_weather_enabled, "weather" in enabled_plugins),
            (self.plugin_agenda_enabled, "agenda" in enabled_plugins),
            (self.super_topmost_checkbox, bool(getattr(self.island_widget, "super_topmost_enabled", True))),
            (self.message_detection_checkbox, bool(self._setting_value("v2_enable_message_detection", True))),
            (self.wechat_notification_checkbox, bool(self._setting_value("v2_enable_wechat_notification", True))),
            (self.wechat_detail_checkbox, bool(self._setting_value("v2_enable_wechat_detail_notification", False))),
            (self.qq_notification_checkbox, bool(self._setting_value("v2_enable_qq_notification", False))),
            (self.dingtalk_notification_checkbox, bool(self._setting_value("v2_enable_dingtalk_notification", False))),
            (self.player_progress_plugin_checkbox, bool(self._setting_value("v2_enable_player_progress_plugin", False))),
            (self.player_progress_auto_detect_checkbox, bool(self._setting_value("v2_player_progress_auto_detect", False))),
            (self.dual_line_lyrics_checkbox, bool(getattr(self.island_widget, "enable_dual_line_lyrics", False))),
            (self.manual_finder_checkbox, bool(getattr(self.island_widget, "manual_finder_available", False))),
            (self.expanded_checkbox, bool(getattr(self.island_widget, "expanded", False))),
            (self.seconds_checkbox, bool(getattr(self.island_widget, "show_seconds", True))),
            (self.weather_use_location, bool(self._setting_value("v2_weather_use_location", True))),
            (self.high_quality_font_checkbox, bool(getattr(self.island_widget, "high_quality_font_rendering", False))),
            (self.auto_bounds_checkbox, bool(getattr(self.island_widget, "auto_expand_bounds", True))),
            (self.barrage_backdrop, bool(getattr(self.island_widget, "barrage_use_backdrop", True))),
            (self.barrage_icon, bool(getattr(self.island_widget, "barrage_show_icon", True))),
            (self.wave_compact_cover_checkbox, bool(getattr(self.island_widget, "compact_lyric_show_cover", self._setting_value("v2_compact_lyric_show_cover", True)))),
            (self.gelv_compact_cover_checkbox, bool(getattr(self.island_widget, "compact_lyric_show_cover", self._setting_value("v2_compact_lyric_show_cover", True)))),
        )
        for widget, value in pairs:
            widget.blockSignals(True)
            widget.setChecked(value)
            widget.blockSignals(False)

        slider_values = (
            (self.wave_slider, max(0, min(100, int(getattr(self.island_widget, "ambient_intensity", 0.0) * 100)))),
            (self.music_flow_slider, max(0, min(100, int(getattr(self.island_widget, "music_flow_intensity", 0.74) * 100)))),
            (self.compact_duotone_balance, max(0, min(100, int(getattr(self.island_widget, "compact_duotone_balance", 50))))),
            (self.expanded_duotone_balance, max(0, min(100, int(getattr(self.island_widget, "expanded_duotone_balance", 50))))),
            (self.fps_slider, max(24, min(90, int(getattr(self.island_widget, "animation_fps", 60))))),
            (self.empty_bar_width, int(getattr(self.island_widget, "empty_bar_width", 92))),
            (self.empty_bar_height, int(getattr(self.island_widget, "empty_bar_height", 14))),
            (self.empty_bar_y_offset, int(getattr(self.island_widget, "empty_bar_y_offset", 0))),
            (self.empty_bar_opacity, int(getattr(self.island_widget, "empty_bar_opacity", 100))),
            (self.barrage_y_offset, int(getattr(self.island_widget, "barrage_y_offset", 0))),
            (self.barrage_font_size, int(getattr(self.island_widget, "barrage_font_size", 10))),
            (self.barrage_speed, int(getattr(self.island_widget, "barrage_speed", 100))),
            (self.lyric_offset, int(round(float(getattr(self.island_widget, "lyric_offset_seconds", 0.0)) * 1000))),
            (self.lyric_font_size, max(14, min(24, int(getattr(self.island_widget, "lyric_font_size", 18))))),
            (self.gelv_primary_font_size, max(14, min(30, int(getattr(self.island_widget, "gelv_primary_font_size", self._setting_value("v2_gelv_primary_font_size", 22)))))),
            (self.gelv_secondary_font_size, max(8, min(24, int(getattr(self.island_widget, "gelv_secondary_font_size", self._setting_value("v2_gelv_secondary_font_size", 12)))))),
            (self.gelv_dual_line_font_size, max(8, min(30, int(getattr(self.island_widget, "gelv_dual_line_font_size", self._setting_value("v2_gelv_dual_line_font_size", 17)))))),
            (self.gelv_initial_word_spacing, max(100, min(320, int(getattr(self.island_widget, "gelv_initial_word_spacing", self._setting_value("v2_gelv_initial_word_spacing", 220)))))),
            (self.gelv_word_spacing, max(100, min(420, int(getattr(self.island_widget, "gelv_word_spacing", self._setting_value("v2_gelv_word_spacing", 320)))))),
            (self.album_island_cover_size, max(18, min(44, int(getattr(self.island_widget, "album_island_cover_size", self._setting_value("v2_album_island_cover_size", 30)))))),
            (self.album_island_spacing, max(48, min(170, int(getattr(self.island_widget, "album_island_spacing", self._setting_value("v2_album_island_spacing", 112)))))),
            (self.album_island_perspective_amount, max(0, min(200, int(getattr(self.island_widget, "album_island_perspective_amount", self._setting_value("v2_album_island_perspective_amount", 100)))))),
            (self.lyric_elastic_strength, max(0, min(160, int(getattr(self.island_widget, "lyric_elastic_strength", 100))))),
            (self.lyric_word_spacing, max(60, min(150, int(getattr(self.island_widget, "lyric_word_spacing", 100))))),
            (self.dual_line_font_size, max(8, min(20, int(getattr(self.island_widget, "dual_line_font_size", self._setting_value("v2_dual_line_font_size", 12)))))),
            (self.dual_line_secondary_weight, max(40, min(100, int(getattr(self.island_widget, "dual_line_secondary_weight", 72))))),
            (self.dual_line_secondary_spacing, max(60, min(150, int(getattr(self.island_widget, "dual_line_secondary_spacing", 100))))),
        )
        for slider, value in slider_values:
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)

        if self.font_combo is not None:
            self.font_combo.blockSignals(True)
            self._set_combo_text(self.font_combo, str(getattr(self.island_widget, "ui_font_family", self.FONT_CHOICES[0])))
            self.font_combo.blockSignals(False)
        if hasattr(self, "settings_font_combo"):
            self._refresh_settings_font_combo()
        self._set_combo_data(self.empty_palette, str(getattr(self.island_widget, "empty_bar_palette", "aurora")))
        self._refresh_location_mode_options()
        self._set_combo_data(self.location_mode, str(getattr(self.island_widget, "location_mode", self._setting_value("v2_location_mode", "free"))))
        self._set_combo_data(self.expand_direction, str(getattr(self.island_widget, "expand_direction", "auto")))
        self._set_combo_data(self.animation_speed, float(getattr(self.island_widget, "animation_speed", 1.0)))
        self._set_combo_data(self.music_ambience_style, str(getattr(self.island_widget, "music_ambience_style", "sea_level")))
        self._set_combo_data(self.music_island_style, str(getattr(self.island_widget, "music_island_style", self._setting_value("v2_music_island_style", "standard")) or "standard"))
        self._set_combo_data(self.side_island_style, str(getattr(self.island_widget, "side_island_style", self._setting_value("v2_side_island_style", "standard")) or "standard"))
        self._set_combo_data(self.plugin_island_style, str(getattr(self.island_widget, "plugin_island_style", self._setting_value("v2_plugin_island_style", "standard")) or "standard"))
        self._set_combo_data(self.time_alarm_side_display, str(getattr(self.island_widget, "time_alarm_side_display", self._setting_value("v2_time_alarm_side_display", "note")) or "note"))
        self._set_combo_data(self.music_player_mode, self._music_player_mode_setting())
        self.ncm_path_input.blockSignals(True)
        self.ncm_path_input.setText(str(self._setting_value("v2_netease_betterncm_path", "") or ""))
        self.ncm_path_input.blockSignals(False)
        self._set_combo_data(self.lyric_flow_style, str(getattr(self.island_widget, "lyric_flow_style", self._setting_value("v2_lyric_flow_style", "wave_stream"))))
        self.gelv_color_fill_checkbox.blockSignals(True)
        self.gelv_color_fill_checkbox.setChecked(bool(getattr(self.island_widget, "gelv_color_fill_enabled", False)))
        self.gelv_color_fill_checkbox.blockSignals(False)
        self.wave_color_fill_checkbox.blockSignals(True)
        self.wave_color_fill_checkbox.setChecked(bool(getattr(self.island_widget, "wave_color_fill_enabled", False)))
        self.wave_color_fill_checkbox.blockSignals(False)
        compact_cover = bool(getattr(self.island_widget, "compact_lyric_show_cover", self._setting_value("v2_compact_lyric_show_cover", True)))
        for checkbox in (getattr(self, "wave_compact_cover_checkbox", None), getattr(self, "gelv_compact_cover_checkbox", None)):
            if checkbox is None:
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(compact_cover)
            checkbox.blockSignals(False)
        self._set_combo_data(self.gelv_font_family, str(getattr(self.island_widget, "gelv_font_family", self._setting_value("v2_gelv_font_family", "Source Han Serif SC"))))
        self.gelv_color_fill_fallback.set_color(str(getattr(self.island_widget, "gelv_color_fill_fallback", self._setting_value("v2_gelv_color_fill_fallback", "#00D7FF"))))
        self.wave_color_fill_fallback.set_color(str(getattr(self.island_widget, "wave_color_fill_fallback", self._setting_value("v2_wave_color_fill_fallback", "#00D7FF"))))
        self._set_combo_data(self.no_lyrics_display_mode, str(getattr(self.island_widget, "no_lyrics_display_mode", self._setting_value("v2_no_lyrics_display_mode", "text"))))
        self._set_combo_data(self.album_island_cover_count, int(getattr(self.island_widget, "album_island_cover_count", self._setting_value("v2_album_island_cover_count", 5))))
        self._set_combo_data(self.album_island_secondary_style, str(getattr(self.island_widget, "album_island_secondary_style", self._setting_value("v2_album_island_secondary_style", "perspective_dim"))))
        if hasattr(self, "album_island_cover_size"):
            self.album_island_cover_size.blockSignals(True)
            self.album_island_cover_size.setValue(max(18, min(44, int(getattr(self.island_widget, "album_island_cover_size", self._setting_value("v2_album_island_cover_size", 30))))))
            self.album_island_cover_size.blockSignals(False)
        if hasattr(self, "album_island_spacing"):
            self.album_island_spacing.blockSignals(True)
            self.album_island_spacing.setValue(max(48, min(170, int(getattr(self.island_widget, "album_island_spacing", self._setting_value("v2_album_island_spacing", 112))))))
            self.album_island_spacing.blockSignals(False)
        if hasattr(self, "album_island_perspective_amount"):
            self.album_island_perspective_amount.blockSignals(True)
            self.album_island_perspective_amount.setValue(max(0, min(200, int(getattr(self.island_widget, "album_island_perspective_amount", self._setting_value("v2_album_island_perspective_amount", 100))))))
            self.album_island_perspective_amount.blockSignals(False)
        self._set_combo_data(self.lyric_elastic_mode, str(getattr(self.island_widget, "lyric_elastic_mode", "both")))
        self._set_combo_data(self.plugin_timezone, str(self._setting_value("v2_plugin_timezone", "local") or "local"))
        self.plugin_timezone_text.blockSignals(True)
        self.plugin_timezone_text.setText(str(self._setting_value("v2_plugin_timezone", "local") or "local"))
        self.plugin_timezone_text.blockSignals(False)
        if hasattr(self, "plugin_alarm_summary"):
            self.plugin_alarm_summary.setText(self._alarm_rows_summary())
        self.weather_manual_location.blockSignals(True)
        self.weather_manual_location.setText(
            str(self._setting_value("v2_weather_manual_location", self._setting_value("v2_weather_location", "北京")) or "北京")
        )
        self.weather_manual_location.blockSignals(False)
        self.schedule_json_input.blockSignals(True)
        self.schedule_json_input.setText(str(self._setting_value("v2_schedule_json_path", "") or ""))
        self.schedule_json_input.blockSignals(False)
        self.schedule_ics_input.blockSignals(True)
        self.schedule_ics_input.setText(str(self._setting_value("v2_schedule_ics_path", "") or ""))
        self.schedule_ics_input.blockSignals(False)
        self.schedule_reminder_minutes.blockSignals(True)
        self.schedule_reminder_minutes.setText(str(self._setting_value("v2_schedule_reminder_minutes", "15,30,5") or "15,30,5"))
        self.schedule_reminder_minutes.blockSignals(False)
        if hasattr(self, "theme_mode"):
            self._set_combo_data(self.theme_mode, str(self._setting_value("v2_theme_mode", "system") or "system"))
        if hasattr(self, "theme_dark_start"):
            self.theme_dark_start.blockSignals(True)
            self.theme_dark_start.setText(str(self._setting_value("v2_theme_dark_start", "22:00") or "22:00"))
            self.theme_dark_start.blockSignals(False)
        if hasattr(self, "theme_dark_end"):
            self.theme_dark_end.blockSignals(True)
            self.theme_dark_end.setText(str(self._setting_value("v2_theme_dark_end", "07:00") or "07:00"))
            self.theme_dark_end.blockSignals(False)
        colors = list(getattr(self.island_widget, "empty_bar_custom_colors", ("#46D6FF", "#73FFA6", "#FF5A86")))
        for edit, value in zip(self.custom_color_inputs, colors):
            if hasattr(edit, "set_color"):
                edit.set_color(str(value))
            else:
                edit.blockSignals(True)
                edit.setText(str(value))
                edit.blockSignals(False)
        self.lyric_offset.blockSignals(True)
        self.lyric_offset.setValue(int(round(float(getattr(self.island_widget, "lyric_offset_seconds", 0.0)) * 1000)))
        self.lyric_offset.blockSignals(False)
        self.lyric_font_size.blockSignals(True)
        self.lyric_font_size.setValue(max(14, min(24, int(getattr(self.island_widget, "lyric_font_size", 18)))))
        self.lyric_font_size.blockSignals(False)
        if hasattr(self, "gelv_primary_font_size"):
            self.gelv_primary_font_size.blockSignals(True)
            self.gelv_primary_font_size.setValue(max(14, min(30, int(getattr(self.island_widget, "gelv_primary_font_size", self._setting_value("v2_gelv_primary_font_size", 22))))))
            self.gelv_primary_font_size.blockSignals(False)
        if hasattr(self, "gelv_secondary_font_size"):
            self.gelv_secondary_font_size.blockSignals(True)
            self.gelv_secondary_font_size.setValue(max(8, min(24, int(getattr(self.island_widget, "gelv_secondary_font_size", self._setting_value("v2_gelv_secondary_font_size", 12))))))
            self.gelv_secondary_font_size.blockSignals(False)
        if hasattr(self, "gelv_dual_line_font_size"):
            self.gelv_dual_line_font_size.blockSignals(True)
            self.gelv_dual_line_font_size.setValue(max(8, min(30, int(getattr(self.island_widget, "gelv_dual_line_font_size", self._setting_value("v2_gelv_dual_line_font_size", 17))))))
            self.gelv_dual_line_font_size.blockSignals(False)
        if hasattr(self, "gelv_initial_word_spacing"):
            self.gelv_initial_word_spacing.blockSignals(True)
            self.gelv_initial_word_spacing.setValue(max(100, min(320, int(getattr(self.island_widget, "gelv_initial_word_spacing", self._setting_value("v2_gelv_initial_word_spacing", 220))))))
            self.gelv_initial_word_spacing.blockSignals(False)
        if hasattr(self, "gelv_word_spacing"):
            self.gelv_word_spacing.blockSignals(True)
            self.gelv_word_spacing.setValue(max(100, min(420, int(getattr(self.island_widget, "gelv_word_spacing", self._setting_value("v2_gelv_word_spacing", 320))))))
            self.gelv_word_spacing.blockSignals(False)
        self.lyric_elastic_strength.blockSignals(True)
        self.lyric_elastic_strength.setValue(max(0, min(160, int(getattr(self.island_widget, "lyric_elastic_strength", 100)))))
        self.lyric_elastic_strength.blockSignals(False)
        self.lyric_word_spacing.blockSignals(True)
        self.lyric_word_spacing.setValue(max(60, min(150, int(getattr(self.island_widget, "lyric_word_spacing", 100)))))
        self.lyric_word_spacing.blockSignals(False)
        if hasattr(self, "wave_color_fill_checkbox"):
            self.wave_color_fill_checkbox.blockSignals(True)
            self.wave_color_fill_checkbox.setChecked(bool(getattr(self.island_widget, "wave_color_fill_enabled", False)))
            self.wave_color_fill_checkbox.blockSignals(False)
        if hasattr(self, "wave_color_fill_fallback"):
            self.wave_color_fill_fallback.set_color(str(getattr(self.island_widget, "wave_color_fill_fallback", self._setting_value("v2_wave_color_fill_fallback", "#00D7FF"))))
        self.dual_line_lyrics_checkbox.blockSignals(True)
        self.dual_line_lyrics_checkbox.setChecked(bool(getattr(self.island_widget, "enable_dual_line_lyrics", False)))
        self.dual_line_lyrics_checkbox.blockSignals(False)
        if hasattr(self, "gelv_dual_line_checkbox"):
            self.gelv_dual_line_checkbox.blockSignals(True)
            self.gelv_dual_line_checkbox.setChecked(bool(getattr(self.island_widget, "enable_dual_line_lyrics", False)))
            self.gelv_dual_line_checkbox.blockSignals(False)
        if hasattr(self, "gelv_size_shift_checkbox"):
            self.gelv_size_shift_checkbox.blockSignals(True)
            self.gelv_size_shift_checkbox.setChecked(bool(getattr(self.island_widget, "gelv_size_shift_enabled", True)))
            self.gelv_size_shift_checkbox.blockSignals(False)
        self.dual_line_secondary_weight.blockSignals(True)
        self.dual_line_secondary_weight.setValue(max(40, min(100, int(getattr(self.island_widget, "dual_line_secondary_weight", 72)))))
        self.dual_line_secondary_weight.blockSignals(False)
        self.dual_line_secondary_spacing.blockSignals(True)
        self.dual_line_secondary_spacing.setValue(max(60, min(150, int(getattr(self.island_widget, "dual_line_secondary_spacing", 100)))))
        self.dual_line_secondary_spacing.blockSignals(False)
        self.no_lyrics_input.blockSignals(True)
        self.no_lyrics_input.setText(str(getattr(self.island_widget, "no_lyrics_text", "暂无歌词")))
        self.no_lyrics_input.blockSignals(False)
        self._update_player_progress_controls()
        self._update_message_controls()
        self._update_lyric_flow_controls()
        self._update_music_ambience_controls()
        self._update_previews()
        self._refresh_diagnostics()

    def _update_player_progress_controls(self):
        enabled = bool(getattr(self, "player_progress_plugin_checkbox", None) and self.player_progress_plugin_checkbox.isChecked())
        auto_detect = bool(getattr(self, "player_progress_auto_detect_checkbox", None) and self.player_progress_auto_detect_checkbox.isChecked())
        if hasattr(self, "player_progress_auto_detect_checkbox"):
            self.player_progress_auto_detect_checkbox.setEnabled(enabled)
        if hasattr(self, "music_player_mode"):
            self.music_player_mode.setEnabled(enabled and not auto_detect)
        if hasattr(self, "ncm_path_input"):
            mode = self._music_player_mode_setting()
            self.ncm_path_input.setEnabled(enabled and not auto_detect and mode == "netease_betterncm")

    def _update_message_controls(self):
        enabled = bool(getattr(self, "message_detection_checkbox", None) and self.message_detection_checkbox.isChecked())
        for name in ("wechat_notification_checkbox", "wechat_detail_checkbox", "qq_notification_checkbox", "dingtalk_notification_checkbox"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(enabled)

    def _update_lyric_flow_controls(self):
        style = str(getattr(self.island_widget, "lyric_flow_style", self._setting_value("v2_lyric_flow_style", "wave_stream")))
        gelv = style == "gelv_songscatter"
        album_flow = style == "album_island"
        wave = not gelv and not album_flow
        if hasattr(self, "_wave_stream_controls"):
            self._wave_stream_controls.setVisible(wave)
        if hasattr(self, "_gelv_controls"):
            self._gelv_controls.setVisible(gelv)
        if hasattr(self, "_album_island_controls"):
            album_mode = album_flow or str(
                getattr(self.island_widget, "no_lyrics_display_mode", self._setting_value("v2_no_lyrics_display_mode", "text"))
            ) == "album_island"
            self._album_island_controls.setVisible(album_mode)
        if hasattr(self, "lyric_offset") and self.lyric_offset.parentWidget() is not None:
            self.lyric_offset.parentWidget().setVisible(not album_flow)
        if hasattr(self, "no_lyrics_display_mode_row"):
            self.no_lyrics_display_mode_row.setVisible(not album_flow)
        for widget in (
            getattr(self, "gelv_font_family", None),
            getattr(self, "gelv_primary_font_size", None),
            getattr(self, "gelv_secondary_font_size", None),
            getattr(self, "gelv_dual_line_font_size", None),
            getattr(self, "gelv_initial_word_spacing", None),
            getattr(self, "gelv_word_spacing", None),
            getattr(self, "gelv_size_shift_checkbox", None),
            getattr(self, "gelv_compact_cover_checkbox", None),
            getattr(self, "gelv_dual_line_checkbox", None),
            getattr(self, "gelv_color_fill_checkbox", None),
            getattr(self, "gelv_color_fill_fallback", None),
        ):
            if widget is not None:
                widget.setEnabled(gelv)
        if hasattr(self, "gelv_dual_line_font_size"):
            dual_enabled = bool(getattr(self.island_widget, "enable_dual_line_lyrics", False))
            self.gelv_dual_line_font_size.setEnabled(gelv and dual_enabled)
        for widget in (
            getattr(self, "wave_color_fill_checkbox", None),
            getattr(self, "wave_color_fill_fallback", None),
            getattr(self, "wave_compact_cover_checkbox", None),
        ):
            if widget is not None:
                widget.setEnabled(wave)
        if hasattr(self, "dual_line_lyrics_checkbox"):
            self.dual_line_lyrics_checkbox.setEnabled(wave)
        dual_enabled = bool(getattr(self.island_widget, "enable_dual_line_lyrics", False))
        for widget in (
            getattr(self, "dual_line_font_size", None),
            getattr(self, "dual_line_secondary_weight", None),
            getattr(self, "dual_line_secondary_spacing", None),
        ):
            if widget is not None:
                widget.setEnabled(wave and dual_enabled)
        album_mode = str(
            getattr(self.island_widget, "no_lyrics_display_mode", self._setting_value("v2_no_lyrics_display_mode", "text"))
        ) == "album_island" or album_flow
        if hasattr(self, "album_island_cover_count"):
            self.album_island_cover_count.setEnabled(album_mode)
        if hasattr(self, "album_island_secondary_style"):
            self.album_island_secondary_style.setEnabled(album_mode)
        if hasattr(self, "album_island_cover_size"):
            self.album_island_cover_size.setEnabled(album_mode)
        if hasattr(self, "album_island_spacing"):
            self.album_island_spacing.setEnabled(album_mode)
        if hasattr(self, "album_island_perspective_amount"):
            perspective_enabled = album_mode and str(getattr(self.album_island_secondary_style, "currentData", lambda: "perspective_dim")() or "perspective_dim") == "perspective_dim"
            self.album_island_perspective_amount.setEnabled(perspective_enabled)

    def _active_color_fill_enabled_for_settings(self) -> bool:
        checker = getattr(self.island_widget, "_active_lyric_color_fill_enabled", None)
        if callable(checker):
            return bool(checker())
        style = str(getattr(self.island_widget, "lyric_flow_style", self._setting_value("v2_lyric_flow_style", "wave_stream")))
        if style == "gelv_songscatter":
            return bool(getattr(self.island_widget, "gelv_color_fill_enabled", False))
        return bool(getattr(self.island_widget, "wave_color_fill_enabled", False))

    def _music_ambience_values(self) -> dict[str, dict[str, float | int]]:
        values = {style: dict(defaults) for style, defaults in DEFAULT_MUSIC_AMBIENCE_VALUES.items()}
        raw = self._setting_value("v2_music_ambience_values", {}) or {}
        if isinstance(raw, dict):
            for style, style_values in raw.items():
                if style not in values or not isinstance(style_values, dict):
                    continue
                values[style].update(
                    {
                        "ambient_intensity": style_values.get("ambient_intensity", values[style]["ambient_intensity"]),
                        "music_flow_intensity": style_values.get("music_flow_intensity", values[style]["music_flow_intensity"]),
                        "compact_duotone_balance": style_values.get("compact_duotone_balance", values[style]["compact_duotone_balance"]),
                        "expanded_duotone_balance": style_values.get("expanded_duotone_balance", values[style]["expanded_duotone_balance"]),
                    }
                )
        return values

    def _current_music_ambience_style(self) -> str:
        style = str(getattr(self.island_widget, "music_ambience_style", self._setting_value("v2_music_ambience_style", "sea_level")) or "sea_level")
        return style if style in DEFAULT_MUSIC_AMBIENCE_VALUES else "sea_level"

    def _current_music_ambience_control_values(self) -> dict[str, float | int]:
        return {
            "ambient_intensity": max(0.0, min(1.0, int(self.wave_slider.value()) / 100.0)),
            "music_flow_intensity": max(0.0, min(1.0, int(self.music_flow_slider.value()) / 100.0)),
            "compact_duotone_balance": max(0, min(100, int(self.compact_duotone_balance.value()))),
            "expanded_duotone_balance": max(0, min(100, int(self.expanded_duotone_balance.value()))),
        }

    def _save_music_ambience_values_for_style(self, style: str | None = None):
        if not all(hasattr(self, name) for name in ("wave_slider", "music_flow_slider", "compact_duotone_balance", "expanded_duotone_balance")):
            return
        style = style or self._current_music_ambience_style()
        if style not in DEFAULT_MUSIC_AMBIENCE_VALUES:
            return
        values = self._music_ambience_values()
        current_values = self._current_music_ambience_control_values()
        values[style] = current_values
        self._save("v2_music_ambience_values", values)
        if style == self._current_music_ambience_style():
            self._save_legacy_music_ambience_values(current_values)

    def _save_legacy_music_ambience_values(self, values: dict[str, float | int]):
        self._save("v2_ambient_intensity", float(values["ambient_intensity"]))
        self._save("v2_music_flow_intensity", float(values["music_flow_intensity"]))
        self._save("v2_compact_duotone_balance", int(values["compact_duotone_balance"]))
        self._save("v2_expanded_duotone_balance", int(values["expanded_duotone_balance"]))

    def _apply_music_ambience_values_for_style(self, style: str):
        values = self._music_ambience_values().get(style, DEFAULT_MUSIC_AMBIENCE_VALUES["sea_level"])
        ambient = max(0.0, min(1.0, float(values.get("ambient_intensity", 0.0))))
        flow = max(0.0, min(1.0, float(values.get("music_flow_intensity", 0.74))))
        compact = max(0, min(100, int(values.get("compact_duotone_balance", 50))))
        expanded = max(0, min(100, int(values.get("expanded_duotone_balance", 50))))
        self.island_widget.ambient_intensity = ambient
        if hasattr(self.island_widget, "set_music_flow_intensity"):
            self.island_widget.set_music_flow_intensity(flow, notify=False)
        else:
            self.island_widget.music_flow_intensity = flow
        if hasattr(self.island_widget, "set_duotone_balance"):
            self.island_widget.set_duotone_balance(compact=compact, expanded=expanded, notify=False)
        else:
            self.island_widget.compact_duotone_balance = compact
            self.island_widget.expanded_duotone_balance = expanded
        if hasattr(self, "wave_slider"):
            self._set_slider_value(self.wave_slider, int(round(ambient * 100)))
            self._set_slider_value(self.music_flow_slider, int(round(flow * 100)))
            self._set_slider_value(self.compact_duotone_balance, compact)
            self._set_slider_value(self.expanded_duotone_balance, expanded)
        self._save_legacy_music_ambience_values(
            {
                "ambient_intensity": ambient,
                "music_flow_intensity": flow,
                "compact_duotone_balance": compact,
                "expanded_duotone_balance": expanded,
            }
        )
        if hasattr(self.island_widget, "update"):
            self.island_widget.update()

    def _update_music_ambience_controls(self):
        if not hasattr(self, "music_ambience_style"):
            return
        allowed = self._music_background_options_allowed()
        current_style = self._current_music_ambience_style()
        if current_style not in allowed:
            current_style = "pure_black"
            if hasattr(self.island_widget, "set_music_ambience_style"):
                self.island_widget.set_music_ambience_style(current_style, notify=False)
            else:
                self.island_widget.music_ambience_style = current_style
            self._save("v2_music_ambience_style", current_style)
            self._apply_music_ambience_values_for_style(current_style)
        for index in range(self.music_ambience_style.count()):
            style = str(self.music_ambience_style.itemData(index) or "")
            item = self.music_ambience_style.model().item(index)
            if item is not None:
                item.setEnabled(style in allowed)
        self._set_combo_data(self.music_ambience_style, current_style)
        if hasattr(self, "_music_ambience_detail_toggle"):
            self._set_detail_panel_title(self._music_ambience_detail_toggle, f"{self.music_ambience_style.currentText()}背景")

    def _music_background_options_allowed(self) -> set[str]:
        style = str(getattr(self.island_widget, "lyric_flow_style", self._setting_value("v2_lyric_flow_style", "wave_stream")) or "wave_stream")
        active_fill = self._active_color_fill_enabled_for_settings()
        tasker = str(getattr(self.island_widget, "location_mode", "free") or "free") == "tasker"
        if tasker or style == "gelv_songscatter":
            return set(DEFAULT_MUSIC_AMBIENCE_VALUES)
        if active_fill:
            return {"pure_black", "bilateral_diffraction", "pure_white"}
        return set(DEFAULT_MUSIC_AMBIENCE_VALUES)

    def _set_combo_text(self, combo: QComboBox, text: str):
        index = combo.findText(text)
        if index < 0:
            combo.addItem(text)
            index = combo.findText(text)
        combo.setCurrentIndex(index)

    def _set_combo_data(self, combo: QComboBox, data):
        combo.blockSignals(True)
        for index in range(combo.count()):
            if combo.itemData(index) == data:
                combo.setCurrentIndex(index)
                break
        combo.blockSignals(False)

    def _update_previews(self):
        if hasattr(self, "music_preview"):
            if str(getattr(self.island_widget, "music_ambience_style", "sea_level")) == "sea_level":
                bg = "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #122036, stop:0.48 #27B8FF, stop:1 #B143FF)"
            else:
                bg = "qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7A1DFF, stop:0.48 #25D5FF, stop:1 #FF4E90)"
            self.music_preview.setStyleSheet(f"QFrame {{ border-radius: 12px; border: 1px solid #e5e7eb; background: {bg}; }}")
        if hasattr(self, "empty_preview"):
            colors = self._empty_bar_preview_colors()
            bg = f"qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {colors[0]}, stop:0.5 {colors[1]}, stop:1 {colors[2]})"
            self.empty_preview.setStyleSheet(f"QFrame {{ border-radius: 12px; border: 1px solid #e5e7eb; background: {bg}; }}")

    def _empty_bar_preview_colors(self) -> list[str]:
        key = str(self.empty_palette.currentData() if hasattr(self, "empty_palette") else getattr(self.island_widget, "empty_bar_palette", "aurora") or "aurora")
        palettes = {
            "cyan": ["#32E0FF", "#469AFF", "#69FFCF"],
            "violet": ["#9066FF", "#4278FF", "#EC5CFF"],
            "amber": ["#FFCD5C", "#FF7E52", "#6EE0FF"],
            "rainbow": ["#FF5A86", "#4ED6FF", "#70FFA6"],
            "aurora": ["#50ACFF", "#7668FF", "#4CE5FF"],
        }
        if key == "custom":
            return self._current_custom_colors()
        return palettes.get(key, palettes["aurora"])

    def _current_custom_colors(self) -> list[str]:
        colors = []
        for widget in getattr(self, "custom_color_inputs", []):
            if hasattr(widget, "color"):
                colors.append(widget.color())
            else:
                colors.append(str(widget.text()).strip())
        while len(colors) < 3:
            colors.append(("#46D6FF", "#73FFA6", "#FF5A86")[len(colors)])
        return colors[:3]

    def _save(self, key: str, value):
        if self.settings:
            self.settings.set(key, value)

    def _refresh_theme_after_setting_change(self):
        self._apply_theme()
        for combo in self.findChildren(QComboBox):
            self._style_combo_popup(combo)
        for switch in self.findChildren(IosSwitch):
            switch.update()
        if hasattr(self.island_widget, "update"):
            self.island_widget.update()

    def _setting_value(self, key: str, default=None):
        if self.settings:
            return self.settings.get(key, default)
        return default

    def _imported_settings_fonts(self) -> list[str]:
        raw = self._setting_value("v2_imported_settings_fonts", [])
        if not isinstance(raw, (list, tuple, set)):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    def _settings_font_items(self) -> list[tuple[str, str]]:
        items = [(family, family) for family in self.FONT_CHOICES]
        existing = {value for _label, value in items}
        for family in self._imported_settings_fonts():
            if family not in existing:
                items.append((family, family))
                existing.add(family)
        return items

    def _refresh_settings_font_combo(self):
        current = str(self._setting_value("v2_settings_font_family", self.FONT_CHOICES[0]) or self.FONT_CHOICES[0])
        self.settings_font_combo.blockSignals(True)
        self.settings_font_combo.clear()
        for label, value in self._settings_font_items():
            self.settings_font_combo.addItem(label, value)
        self.settings_font_combo.blockSignals(False)
        self._set_combo_data(self.settings_font_combo, current)
        self._style_combo_popup(self.settings_font_combo)

    def _apply_settings_font(self):
        family = str(self._setting_value("v2_settings_font_family", self.FONT_CHOICES[0]) or self.FONT_CHOICES[0])
        self.setFont(QFont(family, 9))

    def _enabled_plugins_setting(self) -> list[str]:
        known = ["time", "weather", "agenda"]
        raw = getattr(self.island_widget, "enabled_plugin_ids", None)
        if raw is None:
            raw = self._setting_value("v2_enabled_plugins", known)
        if isinstance(raw, (list, tuple, set)):
            enabled = {str(item or "").strip().casefold() for item in raw}
            return [plugin_id for plugin_id in known if plugin_id in enabled]
        try:
            count = max(0, min(3, int(getattr(self.island_widget, "enabled_plugin_count", len(known)))))
        except (TypeError, ValueError):
            count = len(known)
        return known[:count]

    def _apply_enabled_plugins(self, plugin_ids: list[str]):
        known = ["time", "weather", "agenda"]
        enabled = [plugin_id for plugin_id in known if plugin_id in {str(item).casefold() for item in plugin_ids}]
        self.island_widget.enabled_plugin_ids = set(enabled)
        self.island_widget.enabled_plugin_count = len(enabled)
        if hasattr(self.island_widget, "_set_plugin_focus"):
            self.island_widget._set_plugin_focus(min(getattr(self.island_widget, "plugin_focus_target", 0), max(0, len(enabled) - 1)), immediate=True)
        if hasattr(self.island_widget, "update"):
            self.island_widget.update()

    def closeEvent(self, event):
        if hasattr(self.island_widget, "set_settings_window_active"):
            self.island_widget.set_settings_window_active(False)
        if self.settings and hasattr(self.settings, "save_now"):
            self.settings.save_now()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)

    def hideEvent(self, event):
        if hasattr(self.island_widget, "set_settings_window_active"):
            self.island_widget.set_settings_window_active(False)
        super().hideEvent(event)

    def _settings_page_changed(self, row: int):
        self._collapse_detail_panels()
        if row != self._barrage_page_index and hasattr(self.island_widget, "set_settings_window_active"):
            self.island_widget.set_settings_window_active(False)
        if row == self._diagnostics_page_index:
            self._refresh_diagnostics()

    def _refresh_diagnostics(self):
        diagnostics = {}
        try:
            diag_fn = getattr(self.island_widget, "provider_diagnostics", None)
            if callable(diag_fn):
                diagnostics = diag_fn()
        except Exception as exc:
            diagnostics = {"provider_status": f"error: {exc}"}
        audio = diagnostics.get("audio", {})
        if not isinstance(audio, dict):
            audio = {}
        netease_position = diagnostics.get("netease_position", {})
        if not isinstance(netease_position, dict):
            netease_position = {}
        qq_position = diagnostics.get("qq_position", {})
        if not isinstance(qq_position, dict):
            qq_position = {}
        messages = diagnostics.get("messages", {})
        if not isinstance(messages, dict):
            messages = {}
        position_confidence = diagnostics.get("position_confidence", diagnostics.get("visible_position_confidence", 0.0))
        values = {
            "provider_status": diagnostics.get("provider_status", "-"),
            "winsdk_loaded": "可用" if diagnostics.get("winsdk_loaded") else "不可用",
            "music_player_mode": self._music_player_mode_label(diagnostics.get("music_player_mode", self._music_player_mode_setting())),
            "player_progress_plugin_enabled": "启用" if diagnostics.get("player_progress_plugin_enabled") else "关闭",
            "player_progress_auto_detect": "启用" if diagnostics.get("player_progress_auto_detect") else "关闭",
            "music_player_adapter": diagnostics.get("music_player_adapter") or "-",
            "media_session_count": diagnostics.get("media_session_count", 0),
            "media_session_candidates": " | ".join(diagnostics.get("media_session_candidates") or ()) or diagnostics.get("media_session_error") or "-",
            "source_app": diagnostics.get("source_app") or diagnostics.get("visible_source_app") or "-",
            "track_title": diagnostics.get("track_title") or diagnostics.get("visible_title") or "-",
            "track_artist": diagnostics.get("track_artist") or diagnostics.get("visible_artist") or "-",
            "track_id": diagnostics.get("track_id") or diagnostics.get("visible_track_id") or "-",
            "position_seconds": self._format_seconds(diagnostics.get("position_seconds", 0.0)),
            "position_source": diagnostics.get("position_source") or diagnostics.get("visible_position_source") or "-",
            "position_confidence": f"{float(position_confidence or 0.0) * 100:.0f}%",
            "estimated_position_seconds": self._format_seconds(diagnostics.get("estimated_position_seconds", 0.0)),
            "duration_seconds": self._format_seconds(diagnostics.get("duration_seconds", 0.0)),
            "lyric_offset_seconds": f"{float(diagnostics.get('lyric_offset_seconds', 0.0) or 0.0):+.3f}s",
            "lyrics_source": diagnostics.get("lyrics_source") or "-",
            "timed_lyrics_count": diagnostics.get("timed_lyrics_count", 0),
            "visible_lyric_index": diagnostics.get("visible_lyric_index", 0),
            "visible_current_lyric": diagnostics.get("visible_current_lyric") or "-",
            "lyric_phase_progress": f"{float(diagnostics.get('lyric_phase_progress', 0.0) or 0.0) * 100:.1f}%",
            "lyric_phase_span": f"{float(diagnostics.get('lyric_phase_span', 0.0) or 0.0):.3f}s",
            "lyric_phase_source": diagnostics.get("lyric_phase_source") or "-",
            "netease_position": self._format_netease_position(netease_position),
            "qq_position": self._format_netease_position(qq_position),
            "audio_status": audio.get("status", "-"),
            "audio_has_recent_frame": "是" if audio.get("has_recent_frame") else "否",
            "audio_last_error": audio.get("last_error") or "-",
            "message_cached": messages.get("cached_messages", 0),
            "message_notification_access": messages.get("notification_access") or "-",
            "message_notification_apps": " / ".join(messages.get("last_notification_apps") or ()) or "-",
            "message_detection_enabled": "启用" if messages.get("message_detection_enabled") else "关闭",
            "wechat_notification_enabled": "启用" if messages.get("wechat_notification_enabled") else "关闭",
            "wechat_detail_notification_enabled": "启用" if messages.get("wechat_detail_notification_enabled") else "关闭",
            "qq_notification_enabled": "启用" if messages.get("qq_notification_enabled") else "关闭",
            "dingtalk_notification_enabled": "启用" if messages.get("dingtalk_notification_enabled") else "关闭",
            "message_wechat": f"{messages.get('wechat_backend') or '-'} / {messages.get('wechat_window_count', 0)} UIA窗口 / {messages.get('wechat_session_count', 0)} 会话 / {messages.get('wechat_total_unread_count', messages.get('wechat_unread_count', 0))} 总未读",
            "message_wechat_native": f"{messages.get('wechat_native_window_count', 0)} 窗口 / " + (" | ".join(messages.get("wechat_native_windows") or ()) or "-"),
            "message_wechat_items": " | ".join(messages.get("wechat_debug_items") or ()) or "-",
            "message_wechat_error": messages.get("wechat_error") or "-",
        }
        for key, label in self._diagnostic_labels.items():
            label.setText(str(values.get(key, "-")))

    def _music_player_mode_label(self, mode: object) -> str:
        mode = str(mode or "netease")
        for label, value in self.MUSIC_PLAYER_MODES:
            if value == mode:
                return label
        return mode

    def _music_player_mode_setting(self) -> str:
        mode = str(self._setting_value("v2_music_player_mode", "netease") or "netease").strip().lower()
        if mode in {"", "auto", "system"}:
            return "netease"
        return mode if mode in {"netease", "netease_betterncm", "qishui", "qq", "kugou"} else "netease"

    def _format_netease_position(self, diagnostics: dict[str, object]) -> str:
        source = diagnostics.get("last_source") or "-"
        confidence = float(diagnostics.get("last_confidence", 0.0) or 0.0) * 100.0
        lyric_text = str(diagnostics.get("last_lyric_text") or "").strip()
        if lyric_text:
            lyric_text = lyric_text[:36]
        error = str(diagnostics.get("last_error") or "").strip()
        if source != "-":
            return f"{source} / {confidence:.0f}% / {lyric_text or '-'}"
        return error or "-"

    def _format_seconds(self, value) -> str:
        try:
            seconds = max(0.0, float(value))
        except (TypeError, ValueError):
            return "0:00"
        minutes = int(seconds // 60)
        remain = int(round(seconds - minutes * 60))
        return f"{minutes}:{remain:02d}"

    def _begin_barrage_adjust_preview(self):
        if self.nav.currentRow() != self._barrage_page_index:
            return
        self._barrage_adjust_generation += 1
        generation = self._barrage_adjust_generation
        if hasattr(self.island_widget, "set_settings_window_active"):
            self.island_widget.set_settings_window_active(True)
        if hasattr(self.island_widget, "show_barrage_preview"):
            self.island_widget.show_barrage_preview()

        def release():
            if generation == self._barrage_adjust_generation and hasattr(self.island_widget, "set_settings_window_active"):
                self.island_widget.set_settings_window_active(False)

        QTimer.singleShot(6000, release)

    def _touch(self):
        self.island_widget._enforce_stack_limit()
        self.island_widget._pulse_stack()
        self.island_widget.update()

    def _set_music_playing(self, enabled: bool):
        self.island_widget.music_playing = enabled
        self._save("v2_music_playing", enabled)
        self._touch()

    def _set_music_auto_detect(self, enabled: bool):
        self.island_widget.music_auto_detect = bool(enabled)
        self._save("v2_music_auto_detect", bool(enabled))
        self._touch()

    def _set_persistent_plugin(self, enabled: bool):
        self.island_widget.persistent_plugin = enabled
        self._save("v2_persistent_plugin", enabled)
        self._touch()

    def _set_super_topmost(self, enabled: bool):
        if hasattr(self.island_widget, "set_super_topmost"):
            self.island_widget.set_super_topmost(bool(enabled), notify=False)
        else:
            self.island_widget.super_topmost_enabled = bool(enabled)
        self._save("v2_super_topmost", bool(enabled))
        self._touch()

    def _set_location_mode(self, _index: int = -1):
        mode = str(self.location_mode.currentData() or "free")
        if hasattr(self.island_widget, "set_location_mode"):
            self.island_widget.set_location_mode(mode, notify=False)
            mode = str(getattr(self.island_widget, "location_mode", mode))
        else:
            self.island_widget.location_mode = mode
            self.island_widget.finder_mode = mode == "finder"
            self.island_widget.update()
        self._save("v2_location_mode", mode)
        self._save("v2_finder_mode", mode == "finder")
        self._refresh_location_mode_options()
        self._set_combo_data(self.location_mode, mode)
        self._update_taskbar_controls()
        self._touch()

    def _set_taskbar_corner_style(self, _index: int = -1):
        style = str(self.taskbar_corner_style.currentData() or "win11")
        if hasattr(self.island_widget, "set_taskbar_corner_style"):
            self.island_widget.set_taskbar_corner_style(style, notify=False)
        else:
            self.island_widget.taskbar_corner_style = style
            self.island_widget.update()
        self._save("v2_taskbar_corner_style", style)
        self._touch()

    def _set_taskbar_left_handle_color(self):
        color = self.taskbar_left_handle_color.color()
        if hasattr(self.island_widget, "set_taskbar_left_handle_color"):
            self.island_widget.set_taskbar_left_handle_color(color, notify=False)
            color = str(getattr(self.island_widget, "taskbar_left_handle_color", color))
        else:
            self.island_widget.taskbar_left_handle_color = color
            self.island_widget.update()
        self.taskbar_left_handle_color.set_color(color)
        self._save("v2_taskbar_left_handle_color", color)
        self._touch()

    def _refresh_location_mode_options(self):
        if not hasattr(self, "location_mode"):
            return
        current = str(getattr(self.island_widget, "location_mode", self._setting_value("v2_location_mode", "free")))
        items = self._location_mode_items()
        self.location_mode.blockSignals(True)
        self.location_mode.clear()
        for label, value in items:
            self.location_mode.addItem(label, value)
        if current not in {value for _label, value in items}:
            current = "free"
            if hasattr(self.island_widget, "set_location_mode"):
                self.island_widget.set_location_mode(current, notify=False)
            else:
                self.island_widget.location_mode = current
            self._save("v2_location_mode", current)
        self.location_mode.blockSignals(False)
        self._set_combo_data(self.location_mode, current)
        self._style_combo_popup(self.location_mode)

    def _update_taskbar_controls(self):
        location = str(getattr(self.island_widget, "location_mode", self._setting_value("v2_location_mode", "free")) or "free")
        tasker_available = location != "finder" and not bool(getattr(self.island_widget, "manual_finder_available", False))
        for widget in (
            getattr(self, "taskbar_corner_style", None),
            getattr(self, "taskbar_left_handle_color", None),
        ):
            if widget is not None:
                widget.setEnabled(tasker_available)

    def _set_message_detection(self, enabled: bool):
        self._save("v2_enable_message_detection", bool(enabled))
        self._update_message_controls()
        self._refresh_diagnostics()
        self._touch()

    def _set_wechat_notification(self, enabled: bool):
        self._save("v2_enable_wechat_notification", bool(enabled))
        self._refresh_diagnostics()
        self._touch()

    def _set_wechat_detail_notification(self, enabled: bool):
        self._save("v2_enable_wechat_detail_notification", bool(enabled))
        self._refresh_diagnostics()
        self._touch()

    def _set_qq_notification(self, enabled: bool):
        self._save("v2_enable_qq_notification", bool(enabled))
        self._refresh_diagnostics()
        self._touch()

    def _set_dingtalk_notification(self, enabled: bool):
        self._save("v2_enable_dingtalk_notification", bool(enabled))
        self._refresh_diagnostics()
        self._touch()

    def _set_manual_finder_available(self, enabled: bool):
        if hasattr(self.island_widget, "set_manual_finder_available"):
            self.island_widget.set_manual_finder_available(enabled, notify=False)
        else:
            self.island_widget.manual_finder_available = enabled
            if enabled and str(getattr(self.island_widget, "location_mode", "free")) == "tasker":
                self.island_widget.location_mode = "free"
        self._save("v2_manual_mydockfinder", enabled)
        self._refresh_location_mode_options()
        self._save("v2_location_mode", str(getattr(self.island_widget, "location_mode", self._setting_value("v2_location_mode", "free"))))
        self._update_taskbar_controls()
        self._touch()

    def _set_expanded(self, enabled: bool):
        if hasattr(self.island_widget, "_set_expanded"):
            self.island_widget._set_expanded(enabled)
        else:
            self.island_widget.expanded = enabled
        self._save("v2_expanded", enabled)
        self._touch()

    def _set_show_seconds(self, enabled: bool):
        self.island_widget.show_seconds = enabled
        self._save("v2_show_seconds", enabled)
        self._touch()

    def _set_high_quality_font_rendering(self, enabled: bool):
        if hasattr(self.island_widget, "set_high_quality_font_rendering"):
            self.island_widget.set_high_quality_font_rendering(enabled, notify=False)
        else:
            self.island_widget.high_quality_font_rendering = bool(enabled)
            self.island_widget.update()
        self._save("v2_high_quality_font_rendering", bool(enabled))

    def _set_settings_font_family(self, _index: int = -1):
        family = str(self.settings_font_combo.currentData() or self.FONT_CHOICES[0]).strip() or self.FONT_CHOICES[0]
        self._save("v2_settings_font_family", family)
        self._apply_settings_font()

    def _import_settings_font(self):
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "导入字体",
            "",
            "Font Files (*.ttf *.otf *.ttc *.otc);;All Files (*)",
        )
        if not path:
            return
        font_id = QFontDatabase.addApplicationFont(path)
        if font_id < 0:
            return
        families = QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            return
        family = str(families[0])
        imported = self._imported_settings_fonts()
        if family not in imported and family not in self.FONT_CHOICES:
            imported.append(family)
            self._save("v2_imported_settings_fonts", imported)
        self._refresh_settings_font_combo()
        self._set_combo_data(self.settings_font_combo, family)
        self._set_settings_font_family()

    def _set_theme_mode(self, _index: int = -1):
        mode = str(self.theme_mode.currentData() or "system")
        if mode not in {"system", "light", "dark", "schedule"}:
            mode = "system"
        self._save("v2_theme_mode", mode)
        self._refresh_theme_after_setting_change()

    def _normalize_theme_time(self, value: str, fallback: str) -> str:
        text = str(value or "").strip()
        if not text:
            text = fallback
        parts = text.replace(".", ":").split(":", 1)
        try:
            hour = max(0, min(23, int(parts[0])))
            minute = max(0, min(59, int(parts[1][:2]) if len(parts) > 1 and parts[1] else 0))
            return f"{hour:02d}:{minute:02d}"
        except ValueError:
            return fallback

    def _set_theme_dark_start(self):
        value = self._normalize_theme_time(self.theme_dark_start.text(), "22:00")
        self.theme_dark_start.setText(value)
        self._save("v2_theme_dark_start", value)
        self._refresh_theme_after_setting_change()

    def _set_theme_dark_end(self):
        value = self._normalize_theme_time(self.theme_dark_end.text(), "07:00")
        self.theme_dark_end.setText(value)
        self._save("v2_theme_dark_end", value)
        self._refresh_theme_after_setting_change()

    def _set_no_lyrics_text(self, value: str):
        text = " ".join(str(value or "暂无歌词").split())[:16] or "暂无歌词"
        if text != value:
            cursor = min(self.no_lyrics_input.cursorPosition(), len(text))
            self.no_lyrics_input.blockSignals(True)
            self.no_lyrics_input.setText(text)
            self.no_lyrics_input.setCursorPosition(cursor)
            self.no_lyrics_input.blockSignals(False)
        if hasattr(self.island_widget, "set_no_lyrics_text"):
            self.island_widget.set_no_lyrics_text(text, notify=False)
        else:
            self.island_widget.no_lyrics_text = text
            self.island_widget.update()
        self._save("v2_no_lyrics_text", text)

    def _set_lyric_flow_style(self, _index: int = -1):
        style = str(self.lyric_flow_style.currentData() or "wave_stream")
        if hasattr(self.island_widget, "set_lyric_flow_style"):
            self.island_widget.set_lyric_flow_style(style, notify=False)
        else:
            self.island_widget.lyric_flow_style = style
            self.island_widget.update()
        self._save("v2_lyric_flow_style", style)
        actual_style = str(getattr(self.island_widget, "music_ambience_style", self._setting_value("v2_music_ambience_style", "sea_level")) or "sea_level")
        self._save("v2_music_ambience_style", actual_style)
        if hasattr(self, "music_ambience_style"):
            self._apply_music_ambience_values_for_style(actual_style)
            self._update_music_ambience_controls()
        self._update_lyric_flow_controls()
        self._collapse_detail_panels()
        self._touch()

    def _set_gelv_color_fill_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if hasattr(self.island_widget, "set_gelv_color_fill_enabled"):
            self.island_widget.set_gelv_color_fill_enabled(enabled, notify=False)
        else:
            self.island_widget.gelv_color_fill_enabled = enabled
            self.island_widget.update()
        self._save("v2_gelv_color_fill_enabled", enabled)
        if hasattr(self, "music_ambience_style"):
            actual_style = self._current_music_ambience_style()
            self._save("v2_music_ambience_style", actual_style)
            self._apply_music_ambience_values_for_style(actual_style)
            self._update_music_ambience_controls()
        self._touch()

    def _set_wave_color_fill_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if hasattr(self.island_widget, "set_wave_color_fill_enabled"):
            self.island_widget.set_wave_color_fill_enabled(enabled, notify=False)
        else:
            self.island_widget.wave_color_fill_enabled = enabled
            self.island_widget.update()
        self._save("v2_wave_color_fill_enabled", enabled)
        if hasattr(self, "music_ambience_style"):
            actual_style = self._current_music_ambience_style()
            self._save("v2_music_ambience_style", actual_style)
            self._apply_music_ambience_values_for_style(actual_style)
            self._update_music_ambience_controls()
        self._touch()

    def _set_gelv_font_family(self, _index: int = -1):
        family = str(self.gelv_font_family.currentData() or "Source Han Serif SC")
        if hasattr(self.island_widget, "set_gelv_font_family"):
            self.island_widget.set_gelv_font_family(family, notify=False)
        else:
            self.island_widget.gelv_font_family = family
            self.island_widget.update()
        self._save("v2_gelv_font_family", family)
        self._touch()

    def _set_gelv_primary_font_size(self, value: int):
        size = max(14, min(30, int(value)))
        if hasattr(self.island_widget, "set_gelv_primary_font_size"):
            self.island_widget.set_gelv_primary_font_size(size, notify=False)
        else:
            self.island_widget.gelv_primary_font_size = size
            self.island_widget.update()
        self._save("v2_gelv_primary_font_size", size)
        self._touch()

    def _set_gelv_secondary_font_size(self, value: int):
        size = max(8, min(24, int(value)))
        if hasattr(self.island_widget, "set_gelv_secondary_font_size"):
            self.island_widget.set_gelv_secondary_font_size(size, notify=False)
        else:
            self.island_widget.gelv_secondary_font_size = size
            self.island_widget.update()
        self._save("v2_gelv_secondary_font_size", size)
        self._touch()

    def _set_gelv_dual_line_font_size(self, value: int):
        size = max(8, min(30, int(value)))
        if hasattr(self.island_widget, "set_gelv_dual_line_font_size"):
            self.island_widget.set_gelv_dual_line_font_size(size, notify=False)
        else:
            self.island_widget.gelv_dual_line_font_size = size
            self.island_widget.update()
        self._save("v2_gelv_dual_line_font_size", size)
        self._touch()

    def _set_gelv_initial_word_spacing(self, value: int):
        spacing = max(100, min(320, int(value)))
        if hasattr(self.island_widget, "set_gelv_initial_word_spacing"):
            self.island_widget.set_gelv_initial_word_spacing(spacing, notify=False)
        else:
            self.island_widget.gelv_initial_word_spacing = spacing
            self.island_widget.update()
        self._save("v2_gelv_initial_word_spacing", spacing)
        self._touch()

    def _set_gelv_word_spacing(self, value: int):
        spacing = max(100, min(420, int(value)))
        if hasattr(self.island_widget, "set_gelv_word_spacing"):
            self.island_widget.set_gelv_word_spacing(spacing, notify=False)
        else:
            self.island_widget.gelv_word_spacing = spacing
            self.island_widget.update()
        self._save("v2_gelv_word_spacing", spacing)
        self._touch()

    def _set_gelv_size_shift_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if hasattr(self.island_widget, "set_gelv_size_shift_enabled"):
            self.island_widget.set_gelv_size_shift_enabled(enabled, notify=False)
        else:
            self.island_widget.gelv_size_shift_enabled = enabled
            self.island_widget.update()
        self._save("v2_gelv_size_shift_enabled", enabled)
        self._touch()

    def _set_compact_lyric_show_cover(self, enabled: bool):
        enabled = bool(enabled)
        if hasattr(self.island_widget, "set_compact_lyric_show_cover"):
            self.island_widget.set_compact_lyric_show_cover(enabled, notify=False)
        else:
            self.island_widget.compact_lyric_show_cover = enabled
            self.island_widget.update()
        for checkbox in (getattr(self, "wave_compact_cover_checkbox", None), getattr(self, "gelv_compact_cover_checkbox", None)):
            if checkbox is None:
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(enabled)
            checkbox.blockSignals(False)
        self._save("v2_compact_lyric_show_cover", enabled)
        self._touch()

    def _set_gelv_color_fill_fallback(self):
        color = self.gelv_color_fill_fallback.color()
        if hasattr(self.island_widget, "set_gelv_color_fill_fallback"):
            self.island_widget.set_gelv_color_fill_fallback(color, notify=False)
        else:
            self.island_widget.gelv_color_fill_fallback = color
            self.island_widget.update()
        self._save("v2_gelv_color_fill_fallback", color)
        self._touch()

    def _set_wave_color_fill_fallback(self):
        color = self.wave_color_fill_fallback.color()
        if hasattr(self.island_widget, "set_wave_color_fill_fallback"):
            self.island_widget.set_wave_color_fill_fallback(color, notify=False)
        else:
            self.island_widget.wave_color_fill_fallback = color
            self.island_widget.update()
        self._save("v2_wave_color_fill_fallback", color)
        self._touch()

    def _set_no_lyrics_display_mode(self, _index: int = -1):
        mode = str(self.no_lyrics_display_mode.currentData() or "text")
        if hasattr(self.island_widget, "set_no_lyrics_display_mode"):
            self.island_widget.set_no_lyrics_display_mode(mode, notify=False)
        else:
            self.island_widget.no_lyrics_display_mode = mode
            self.island_widget.update()
        self._save("v2_no_lyrics_display_mode", mode)
        self._update_lyric_flow_controls()
        self._touch()

    def _set_album_island_cover_count(self, _index: int = -1):
        count = int(self.album_island_cover_count.currentData() or 5)
        if hasattr(self.island_widget, "set_album_island_cover_count"):
            self.island_widget.set_album_island_cover_count(count, notify=False)
        else:
            self.island_widget.album_island_cover_count = count
            self.island_widget.update()
        self._save("v2_album_island_cover_count", count)
        self._touch()

    def _set_album_island_cover_size(self, value: int):
        size = max(18, min(44, int(value)))
        if hasattr(self.island_widget, "set_album_island_cover_size"):
            self.island_widget.set_album_island_cover_size(size, notify=False)
        else:
            self.island_widget.album_island_cover_size = size
            self.island_widget.update()
        self._save("v2_album_island_cover_size", size)
        self._touch()

    def _set_album_island_secondary_style(self, _index: int = -1):
        style = str(self.album_island_secondary_style.currentData() or "perspective_dim")
        if hasattr(self.island_widget, "set_album_island_secondary_style"):
            self.island_widget.set_album_island_secondary_style(style, notify=False)
        else:
            self.island_widget.album_island_secondary_style = style
            self.island_widget.update()
        self._save("v2_album_island_secondary_style", style)
        self._update_lyric_flow_controls()
        self._touch()

    def _set_album_island_perspective_amount(self, value: int):
        amount = max(0, min(200, int(value)))
        if hasattr(self.island_widget, "set_album_island_perspective_amount"):
            self.island_widget.set_album_island_perspective_amount(amount, notify=False)
        else:
            self.island_widget.album_island_perspective_amount = amount
            self.island_widget.update()
        self._save("v2_album_island_perspective_amount", amount)
        self._touch()

    def _set_album_island_spacing(self, value: int):
        spacing = max(48, min(170, int(value)))
        if hasattr(self.island_widget, "set_album_island_spacing"):
            self.island_widget.set_album_island_spacing(spacing, notify=False)
        else:
            self.island_widget.album_island_spacing = spacing
            self.island_widget.update()
        self._save("v2_album_island_spacing", spacing)
        self._touch()

    def _set_weather_location(self, value: str):
        self._save("v2_weather_location", str(value or "").strip() or "Beijing")

    def _set_weather_manual_location(self):
        value = str(self.weather_manual_location.text() or "").strip() or "北京"
        self.weather_manual_location.setText(value)
        self._save("v2_weather_manual_location", value)
        if not bool(self._setting_value("v2_weather_use_location", True)):
            self._save("v2_weather_location", value)
        self._save("v2_weather_refresh_nonce", str(time.monotonic()))

    def _set_weather_use_location(self, enabled: bool):
        self._save("v2_weather_use_location", bool(enabled))
        if not enabled:
            self._set_weather_manual_location()
        else:
            self._save("v2_weather_refresh_nonce", str(time.monotonic()))

    def _set_plugin_timezone(self, value: str):
        self._save("v2_plugin_timezone", str(value or "").strip() or "local")

    def _set_plugin_timezone_combo(self, _index: int = -1):
        value = str(self.plugin_timezone.currentData() or "local")
        self.plugin_timezone_text.setText(value)
        self._set_plugin_timezone(value)

    def _set_plugin_timezone_text(self):
        value = str(self.plugin_timezone_text.text() or "local").strip() or "local"
        self.plugin_timezone_text.setText(value)
        self._set_combo_data(self.plugin_timezone, value)
        self._set_plugin_timezone(value)

    def _alarm_rows_summary(self) -> str:
        raw = self._setting_value("v2_alarm_rows", "")
        if not raw:
            return "暂无闹钟"
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return "闹钟数据待整理"
        if isinstance(payload, dict):
            payload = payload.get("alarms") or payload.get("rows") or []
        if not isinstance(payload, list):
            return "闹钟数据待整理"
        rows = [row for row in payload if isinstance(row, dict)]
        enabled = sum(1 for row in rows if bool(row.get("enabled", True)))
        if not rows:
            return "暂无闹钟"
        return f"{enabled}/{len(rows)} 个已启用"

    def _open_time_alarm_editor(self):
        widget = self.island_widget
        widget.persistent_plugin = True
        enabled = set(self._enabled_plugins_setting())
        enabled.add("time")
        self._apply_enabled_plugins([plugin_id for plugin_id in ("time", "weather", "agenda") if plugin_id in enabled])
        widget.plugin_panel_open = True
        widget.plugin_time_mode = "alarm_editor"
        if hasattr(widget, "_set_plugin_focus"):
            widget._set_plugin_focus(0, immediate=True)
        if hasattr(widget, "_set_expanded"):
            widget._set_expanded(True)
        else:
            widget.expanded = True
        self._save("v2_persistent_plugin", True)
        self._save("v2_enabled_plugins", self._enabled_plugins_setting())
        self._save("v2_expanded", True)
        if hasattr(self, "plugin_alarm_summary"):
            self.plugin_alarm_summary.setText(self._alarm_rows_summary())
        self._touch()

    def _set_timer_end_at(self, value: str):
        self._save("v2_timer_end_at", str(value or "").strip())

    def _start_plugin_timer(self, minutes: int):
        minutes = max(1, min(600, int(minutes)))
        now = datetime.now().astimezone()
        self._save("v2_timer_started_at", now.isoformat(timespec="seconds"))
        self._save("v2_timer_end_at", (now + timedelta(minutes=minutes)).isoformat(timespec="seconds"))
        self._touch()

    def _clear_plugin_timer(self):
        self._save("v2_timer_started_at", "")
        self._save("v2_timer_end_at", "")
        self._touch()

    def _set_schedule_json_path(self, value: str):
        self._save("v2_schedule_json_path", str(value or "").strip())

    def _set_schedule_ics_path(self, value: str):
        self._save("v2_schedule_ics_path", str(value or "").strip())

    def _set_schedule_reminder_minutes(self):
        value = str(self.schedule_reminder_minutes.text() or "").strip() or "15,30,5"
        self.schedule_reminder_minutes.setText(value)
        self._save("v2_schedule_reminder_minutes", value)
        self._touch()

    def _set_enabled_plugins_from_checks(self, _checked: bool = False):
        enabled = []
        if getattr(self, "plugin_time_enabled", None) is not None and self.plugin_time_enabled.isChecked():
            enabled.append("time")
        if getattr(self, "plugin_weather_enabled", None) is not None and self.plugin_weather_enabled.isChecked():
            enabled.append("weather")
        if getattr(self, "plugin_agenda_enabled", None) is not None and self.plugin_agenda_enabled.isChecked():
            enabled.append("agenda")
        self._apply_enabled_plugins(enabled)
        self._save("v2_enabled_plugins", enabled)
        self._touch()

    def _set_side_island_style(self, _index: int = -1):
        style = str(self.side_island_style.currentData() or "standard")
        self.island_widget.side_island_style = style
        self._save("v2_side_island_style", style)
        self._touch()

    def _set_plugin_island_style(self, _index: int = -1):
        style = str(self.plugin_island_style.currentData() or "standard")
        self.island_widget.plugin_island_style = style
        self._save("v2_plugin_island_style", style)
        self._touch()

    def _set_time_alarm_side_display(self, _index: int = -1):
        mode = str(self.time_alarm_side_display.currentData() or "note")
        self.island_widget.time_alarm_side_display = mode
        self._save("v2_time_alarm_side_display", mode)
        self._touch()

    def _set_wave_intensity(self, value: int):
        intensity = max(0.0, min(1.0, value / 100.0))
        self.island_widget.ambient_intensity = intensity
        self._save("v2_ambient_intensity", intensity)
        self._save_music_ambience_values_for_style()
        self.island_widget.update()
        self._update_previews()

    def _set_music_ambience_style(self, _index: int = -1):
        previous_style = self._current_music_ambience_style()
        self._save_music_ambience_values_for_style(previous_style)
        style = self.music_ambience_style.currentData() or "sea_level"
        if hasattr(self.island_widget, "set_music_ambience_style"):
            self.island_widget.set_music_ambience_style(style, notify=False)
        else:
            self.island_widget.music_ambience_style = style
            self.island_widget.update()
        actual_style = self._current_music_ambience_style()
        self._save("v2_music_ambience_style", actual_style)
        self._set_combo_data(self.music_ambience_style, actual_style)
        self._apply_music_ambience_values_for_style(actual_style)
        self._update_music_ambience_controls()
        self._collapse_detail_panels()
        self._update_previews()

    def _set_music_island_style(self, _index: int = -1):
        style = str(self.music_island_style.currentData() or "standard")
        self.island_widget.music_island_style = style
        self._save("v2_music_island_style", style)
        self._touch()

    def _set_music_flow_intensity(self, value: int):
        intensity = max(0.0, min(1.0, value / 100.0))
        if hasattr(self.island_widget, "set_music_flow_intensity"):
            self.island_widget.set_music_flow_intensity(intensity, notify=False)
        else:
            self.island_widget.music_flow_intensity = intensity
            self.island_widget.update()
        self._save("v2_music_flow_intensity", intensity)
        self._save_music_ambience_values_for_style()
        self._update_previews()

    def _set_compact_duotone_balance(self, value: int):
        value = max(0, min(100, int(value)))
        if hasattr(self.island_widget, "set_duotone_balance"):
            self.island_widget.set_duotone_balance(compact=value, notify=False)
        else:
            self.island_widget.compact_duotone_balance = value
            self.island_widget.update()
        self._save("v2_compact_duotone_balance", value)
        self._save_music_ambience_values_for_style()
        self._update_previews()

    def _set_expanded_duotone_balance(self, value: int):
        value = max(0, min(100, int(value)))
        if hasattr(self.island_widget, "set_duotone_balance"):
            self.island_widget.set_duotone_balance(expanded=value, notify=False)
        else:
            self.island_widget.expanded_duotone_balance = value
            self.island_widget.update()
        self._save("v2_expanded_duotone_balance", value)
        self._save_music_ambience_values_for_style()
        self._update_previews()

    def _set_player_progress_auto_detect(self, enabled: bool):
        self._save("v2_player_progress_auto_detect", bool(enabled))
        self._update_player_progress_controls()
        self._refresh_diagnostics()
        self._touch()

    def _set_music_player_mode(self, _index: int = -1):
        mode = str(self.music_player_mode.currentData() or "netease")
        self._save("v2_music_player_mode", mode)
        self._update_player_progress_controls()
        self._refresh_diagnostics()
        self._touch()

    def _set_netease_betterncm_path(self):
        path = str(self.ncm_path_input.text() or "").strip().strip('"')
        self.ncm_path_input.setText(path)
        self._save("v2_netease_betterncm_path", path)
        self._refresh_diagnostics()
        self._touch()

    def _set_player_progress_plugin_enabled(self, enabled: bool):
        self._save("v2_enable_player_progress_plugin", bool(enabled))
        self._update_player_progress_controls()
        self._refresh_diagnostics()
        self._touch()

    def _set_auto_expand_bounds(self, enabled: bool):
        if hasattr(self.island_widget, "set_auto_expand_bounds"):
            self.island_widget.set_auto_expand_bounds(enabled, notify=False)
        else:
            self.island_widget.auto_expand_bounds = enabled
            self.island_widget.update()
        self._save("v2_auto_expand_bounds", enabled)

    def _set_expand_direction(self, _index: int):
        direction = self.expand_direction.currentData() or "auto"
        if hasattr(self.island_widget, "set_expand_direction"):
            self.island_widget.set_expand_direction(direction, notify=False)
        else:
            self.island_widget.expand_direction = direction
            self.island_widget.update()
        self._save("v2_expand_direction", direction)

    def _set_barrage_y_offset(self, value: int):
        if hasattr(self.island_widget, "set_barrage_settings"):
            self.island_widget.set_barrage_settings(y_offset=value, notify=False)
        else:
            self.island_widget.barrage_y_offset = value
            self.island_widget.update()
        self._begin_barrage_adjust_preview()
        self._save("v2_barrage_y_offset", value)

    def _set_barrage_font_size(self, value: int):
        if hasattr(self.island_widget, "set_barrage_settings"):
            self.island_widget.set_barrage_settings(font_size=value, notify=False)
        else:
            self.island_widget.barrage_font_size = value
            self.island_widget.update()
        self._save("v2_barrage_font_size", value)

    def _set_barrage_speed(self, value: int):
        if hasattr(self.island_widget, "set_barrage_settings"):
            self.island_widget.set_barrage_settings(speed=value, notify=False)
        else:
            self.island_widget.barrage_speed = value
            self.island_widget.update()
        self._begin_barrage_adjust_preview()
        self._save("v2_barrage_speed", value)

    def _set_barrage_backdrop(self, enabled: bool):
        if hasattr(self.island_widget, "set_barrage_settings"):
            self.island_widget.set_barrage_settings(use_backdrop=enabled, notify=False)
        else:
            self.island_widget.barrage_use_backdrop = enabled
            self.island_widget.update()
        self._save("v2_barrage_use_backdrop", enabled)

    def _set_barrage_icon(self, enabled: bool):
        if hasattr(self.island_widget, "set_barrage_settings"):
            self.island_widget.set_barrage_settings(show_icon=enabled, notify=False)
        else:
            self.island_widget.barrage_show_icon = enabled
            self.island_widget.update()
        self._save("v2_barrage_show_icon", enabled)

    def _set_lyric_offset(self, value: int):
        seconds = max(-12.0, min(12.0, float(value) / 1000.0))
        if hasattr(self.island_widget, "set_lyric_offset_seconds"):
            self.island_widget.set_lyric_offset_seconds(seconds, notify=False)
        else:
            self.island_widget.lyric_offset_seconds = seconds
            self.island_widget.update()
        self._save("v2_lyric_offset_seconds", seconds)

    def _set_lyric_font_size(self, value: int):
        size = max(14, min(24, int(value)))
        if hasattr(self.island_widget, "set_lyric_font_size"):
            self.island_widget.set_lyric_font_size(size, notify=False)
        else:
            self.island_widget.lyric_font_size = size
            self.island_widget.update()
        self._save("v2_lyric_font_size", size)

    def _set_lyric_elastic_strength(self, value: int):
        strength = max(0, min(160, int(value)))
        if hasattr(self.island_widget, "set_lyric_elastic_strength"):
            self.island_widget.set_lyric_elastic_strength(strength, notify=False)
        else:
            self.island_widget.lyric_elastic_strength = strength
            self.island_widget.update()
        self._save("v2_lyric_elastic_strength", strength)

    def _set_lyric_word_spacing(self, value: int):
        spacing = max(60, min(150, int(value)))
        if hasattr(self.island_widget, "set_lyric_word_spacing"):
            self.island_widget.set_lyric_word_spacing(spacing, notify=False)
        else:
            self.island_widget.lyric_word_spacing = spacing
            self.island_widget.update()
        self._save("v2_lyric_word_spacing", spacing)

    def _set_lyric_elastic_mode(self, _index: int = -1):
        mode = self.lyric_elastic_mode.currentData() or "both"
        if hasattr(self.island_widget, "set_lyric_elastic_mode"):
            self.island_widget.set_lyric_elastic_mode(mode, notify=False)
        else:
            self.island_widget.lyric_elastic_mode = mode
            self.island_widget.update()
        self._save("v2_lyric_elastic_mode", mode)

    def _set_dual_line_lyrics(self, enabled: bool):
        enabled = bool(enabled)
        if hasattr(self.island_widget, "set_enable_dual_line_lyrics"):
            self.island_widget.set_enable_dual_line_lyrics(enabled, notify=False)
        else:
            self.island_widget.enable_dual_line_lyrics = enabled
            self.island_widget.update()
        for checkbox in (getattr(self, "dual_line_lyrics_checkbox", None), getattr(self, "gelv_dual_line_checkbox", None)):
            if checkbox is None:
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(enabled)
            checkbox.blockSignals(False)
        self._save("v2_enable_dual_line_lyrics", enabled)
        self._update_lyric_flow_controls()
        self._touch()

    def _set_dual_line_font_size(self, value: int):
        size = max(8, min(20, int(value)))
        if hasattr(self.island_widget, "set_dual_line_font_size"):
            self.island_widget.set_dual_line_font_size(size, notify=False)
        else:
            self.island_widget.dual_line_font_size = size
            self.island_widget.update()
        self._save("v2_dual_line_font_size", size)

    def _set_dual_line_secondary_weight(self, value: int):
        weight = max(40, min(100, int(value)))
        if hasattr(self.island_widget, "set_dual_line_secondary_weight"):
            self.island_widget.set_dual_line_secondary_weight(weight, notify=False)
        else:
            self.island_widget.dual_line_secondary_weight = weight
            self.island_widget.update()
        self._save("v2_dual_line_secondary_weight", weight)

    def _set_dual_line_secondary_spacing(self, value: int):
        spacing = max(60, min(150, int(value)))
        if hasattr(self.island_widget, "set_dual_line_secondary_spacing"):
            self.island_widget.set_dual_line_secondary_spacing(spacing, notify=False)
        else:
            self.island_widget.dual_line_secondary_spacing = spacing
            self.island_widget.update()
        self._save("v2_dual_line_secondary_spacing", spacing)

    def _set_font_family(self, family: str):
        family = family.strip() or self.FONT_CHOICES[0]
        if hasattr(self.island_widget, "set_ui_font"):
            self.island_widget.set_ui_font(family, notify=False)
            family = str(getattr(self.island_widget, "ui_font_family", family))
        app = QApplication.instance()
        if app:
            app.setFont(QFont(family, 9))
        self._save("v2_font_family", family)

    def _set_animation_fps(self, value: int):
        if hasattr(self.island_widget, "set_animation_fps"):
            self.island_widget.set_animation_fps(value, notify=False)
        self._save("v2_animation_fps", value)

    def _set_animation_speed(self, _index: int = -1):
        value = float(self.animation_speed.currentData() or 1.0)
        if hasattr(self.island_widget, "set_animation_speed"):
            self.island_widget.set_animation_speed(value, notify=False)
        self._save("v2_animation_speed", value)

    def _set_empty_bar_palette(self, _index: int = -1):
        key = self.empty_palette.currentData() or "aurora"
        if hasattr(self.island_widget, "set_empty_bar_palette"):
            self.island_widget.set_empty_bar_palette(key, notify=False)
        else:
            self.island_widget.empty_bar_palette = key
            self.island_widget.update()
        self._save("v2_empty_bar_palette", key)
        self._update_previews()

    def _set_empty_bar_width(self, value: int):
        if hasattr(self.island_widget, "set_empty_bar_geometry"):
            self.island_widget.set_empty_bar_geometry(width=value, notify=False)
        else:
            self.island_widget.empty_bar_width = value
            self.island_widget.update()
        self._save("v2_empty_bar_width", value)
        self._update_previews()

    def _set_empty_bar_height(self, value: int):
        if hasattr(self.island_widget, "set_empty_bar_geometry"):
            self.island_widget.set_empty_bar_geometry(height=value, notify=False)
        else:
            self.island_widget.empty_bar_height = value
            self.island_widget.update()
        self._save("v2_empty_bar_height", value)
        self._update_previews()

    def _set_empty_bar_y_offset(self, value: int):
        if hasattr(self.island_widget, "set_empty_bar_geometry"):
            self.island_widget.set_empty_bar_geometry(y_offset=value, notify=False)
        else:
            self.island_widget.empty_bar_y_offset = value
            self.island_widget.update()
        self._save("v2_empty_bar_y_offset", value)
        self._update_previews()

    def _set_empty_bar_opacity(self, value: int):
        if hasattr(self.island_widget, "set_empty_bar_geometry"):
            self.island_widget.set_empty_bar_geometry(opacity=value, notify=False)
        else:
            self.island_widget.empty_bar_opacity = value
            self.island_widget.update()
        self._save("v2_empty_bar_opacity", value)
        self._update_previews()

    def _set_empty_bar_custom_colors(self):
        colors = self._current_custom_colors()
        if hasattr(self.island_widget, "set_empty_bar_custom_colors"):
            self.island_widget.set_empty_bar_custom_colors(colors, notify=False)
            colors = list(getattr(self.island_widget, "empty_bar_custom_colors", colors))
        else:
            self.island_widget.empty_bar_custom_colors = tuple(colors)
            self.island_widget.update()
        self._save("v2_empty_bar_custom_colors", colors)
        self._update_previews()

    def _add_message(self):
        self.island_widget.add_message()

    def _clear_messages(self):
        self.island_widget.messages.clear()
        self._touch()

    def _focus_plugin(self, index: int):
        self.island_widget._set_plugin_focus(index)
        if hasattr(self.island_widget, "_set_expanded"):
            self.island_widget._set_expanded(True)
        else:
            self.island_widget.expanded = True
        self.expanded_checkbox.setChecked(True)
        self._save("v2_expanded", True)
        self._touch()
