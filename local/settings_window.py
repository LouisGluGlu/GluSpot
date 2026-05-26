from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QDialog,
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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from local.ios_widgets import IosSwitch
from local.ui_theme import ios_theme, is_theme_change_event, style_combo_popup


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
        "HarmonyOS Sans SC",
        "MiSans",
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "Source Han Sans SC",
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "Segoe UI",
        "Arial",
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
    )

    MUSIC_PLAYER_MODES = (
        ("网易云音乐", "netease"),
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

    LYRIC_ELASTIC_MODES = (
        ("双侧悦动", "both"),
        ("左侧轻推", "left"),
    )

    def __init__(self, island_widget: QWidget, settings_manager=None, parent=None):
        super().__init__(parent)
        self.island_widget = island_widget
        self.settings = settings_manager
        self._barrage_adjust_generation = 0
        self._barrage_page_index = 3
        self._diagnostics_page_index = 4
        self._diagnostic_labels: dict[str, QLabel] = {}
        self._applying_theme = False
        self.setWindowTitle("gpt-island 设置")
        self.setMinimumSize(860, 620)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self._build_ui()

    def _theme(self) -> dict[str, str]:
        return ios_theme()

    def _apply_theme(self):
        if getattr(self, "_applying_theme", False):
            return
        self._applying_theme = True
        t = self._theme()
        try:
            self.setStyleSheet(
                f"""
                QDialog, QWidget {{ background: {t["window"]}; color: {t["text"]}; selection-background-color: #007aff; selection-color: #ffffff; }}
                QScrollArea {{ border: 0; background: transparent; }}
                QScrollArea > QWidget > QWidget {{ background: {t["window"]}; color: {t["text"]}; }}
                QListWidget {{ background: {t["panel"]}; color: {t["text"]}; border: 0; outline: 0; padding: 16px 10px; font-size: 14px; }}
                QListWidget::item {{ border-radius: 12px; padding: 12px 14px; margin: 3px 0; color: {t["text"]}; }}
                QListWidget::item:hover {{ background: {t["popup_hover"]}; color: {t["text"]}; }}
                QListWidget::item:focus {{ outline: 0; border: 0; }}
                QListWidget::item:selected {{ background: #007aff; color: white; }}
                QLabel {{ background: transparent; color: {t["text"]}; }}
                QLabel#Title {{ font-size: 28px; font-weight: 700; color: {t["text"]}; padding: 0 0 14px 0; }}
                QLabel#Section {{ font-size: 13px; font-weight: 700; color: {t["muted"]}; padding: 20px 0 8px 2px; text-transform: uppercase; }}
                QFrame#Card {{ background: {t["card"]}; border: 1px solid {t["card_border"]}; border-radius: 14px; }}
                QFrame#Card QWidget {{ background: transparent; color: {t["text"]}; }}
                QLabel#RowTitle {{ font-size: 14px; color: {t["text"]}; }}
                QLabel#Value {{ color: {t["muted"]}; min-width: 58px; }}
                QLineEdit, QComboBox {{ color: {t["text"]}; min-height: 36px; border: 1px solid {t["card_border"]}; border-radius: 10px; padding: 4px 12px; background: {t["input"]}; }}
                QLineEdit:focus, QComboBox:focus {{ border-color: #007aff; background: {t["input_focus"]}; }}
                QComboBox QAbstractItemView {{ background: {t["card"]}; color: {t["text"]}; border: 1px solid {t["card_border"]}; selection-background-color: #007aff; selection-color: #ffffff; outline: 0; }}
                QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 6px 10px; background: transparent; color: {t["text"]}; }}
                QComboBox QAbstractItemView::item:hover {{ background: {t["popup_hover"]}; color: {t["text"]}; }}
                QComboBox QAbstractItemView::item:selected {{ background: #007aff; color: #ffffff; }}
                QPushButton {{ min-height: 36px; border: 0; border-radius: 10px; background: {t["button"]}; color: {t["text"]}; padding: 0 16px; }}
                QPushButton:hover {{ background: {t["button_hover"]}; }}
                QPushButton#Primary {{ background: #007aff; color: white; }}
                QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: {t["track"]}; }}
                QSlider::sub-page:horizontal {{ height: 6px; border-radius: 3px; background: #007aff; }}
                QSlider::handle:horizontal {{ width: 22px; height: 22px; margin: -8px 0; border-radius: 11px; background: #ffffff; border: 1px solid {t["track"]}; }}
                QScrollBar:vertical {{ background: transparent; width: 10px; margin: 8px 2px 8px 2px; }}
                QScrollBar::handle:vertical {{ background: {t["scroll"]}; border-radius: 5px; min-height: 36px; }}
                QScrollBar::handle:vertical:hover {{ background: {t["muted"]}; }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
                """
            )
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

    def _build_ui(self):
        self._apply_theme()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(10, 12, 10, 12)
        side_layout.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setFixedWidth(172)
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        side_layout.addWidget(self.nav, 1)
        close_btn = QPushButton("关闭")
        close_btn.setFixedHeight(34)
        close_btn.clicked.connect(self.close)
        side_layout.addWidget(close_btn)
        root.addWidget(side)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        pages = (
            ("系统", self._system_page()),
            ("媒体播放", self._media_page()),
            ("外观", self._appearance_page()),
            ("弹幕", self._barrage_page()),
            ("快捷动作", self._actions_page()),
            ("诊断", self._diagnostics_page()),
        )
        pages = pages[:4] + pages[5:]
        for name, page in pages:
            self.nav.addItem(QListWidgetItem(name))
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.currentRowChanged.connect(self._settings_page_changed)
        self.nav.setCurrentRow(0)

    def _page(self, title: str) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 30, 32, 30)
        layout.setSpacing(16)
        label = QLabel(title)
        label.setObjectName("Title")
        layout.addWidget(label)
        scroll.setWidget(page)
        scroll._content_layout = layout
        return scroll

    def _card(self, parent_layout: QVBoxLayout) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(0)
        parent_layout.addWidget(card)
        return card

    def _row(self, layout: QVBoxLayout, title: str, control: QWidget):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 11, 0, 11)
        row_layout.setSpacing(16)
        label = QLabel(title)
        label.setObjectName("RowTitle")
        label.setMinimumWidth(150)
        control.setSizePolicy(QSizePolicy.Policy.Expanding, control.sizePolicy().verticalPolicy())
        row_layout.addWidget(label, 0)
        row_layout.addWidget(control, 1)
        layout.addWidget(row)

    def _preview_frame(self, height: int = 42) -> QFrame:
        frame = QFrame()
        frame.setFixedHeight(height)
        frame.setStyleSheet("QFrame { border-radius: 8px; background: #111827; border: 1px solid #e5e7eb; }")
        return frame

    def _color_buttons_row(self, layout: QVBoxLayout, title: str, colors) -> list[ColorButton]:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 10, 0, 10)
        row_layout.setSpacing(12)
        label = QLabel(title)
        label.setObjectName("RowTitle")
        label.setMinimumWidth(150)
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
        grid.setContentsMargins(0, 11, 0, 11)
        grid.setHorizontalSpacing(14)
        title_label = QLabel(title)
        title_label.setObjectName("RowTitle")
        value_label = QLabel(f"{value}{suffix}")
        value_label.setObjectName("Value")
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider.valueChanged.connect(lambda v: value_label.setText(f"{v}{suffix}"))
        slider.valueChanged.connect(callback)
        grid.addWidget(title_label, 0, 0)
        grid.addWidget(value_label, 0, 1)
        grid.addWidget(slider, 1, 0, 1, 2)
        layout.addWidget(row)
        return slider

    def _combo(self, items: list[tuple[str, object]], current, callback) -> QComboBox:
        combo = QComboBox()
        for label, data in items:
            combo.addItem(label, data)
        for index in range(combo.count()):
            if combo.itemData(index) == current:
                combo.setCurrentIndex(index)
                break
        combo.currentIndexChanged.connect(callback)
        self._style_combo_popup(combo)
        return combo

    def _style_combo_popup(self, combo: QComboBox):
        style_combo_popup(combo, self._theme())

    def _system_page(self) -> QWidget:
        page = self._page("系统")
        layout: QVBoxLayout = page._content_layout
        card = self._card(layout)
        c = card.layout()
        self.music_checkbox = self._check("音乐岛", bool(getattr(self.island_widget, "music_playing", True)), self._set_music_playing)
        self.music_auto_checkbox = self._check("音乐岛自动识别：无音频时隐藏", bool(getattr(self.island_widget, "music_auto_detect", False)), self._set_music_auto_detect)
        self.plugin_checkbox = self._check("插件岛", bool(getattr(self.island_widget, "persistent_plugin", True)), self._set_persistent_plugin)
        self.finder_checkbox = self._check("Finder 融合", bool(getattr(self.island_widget, "finder_mode", True)), self._set_finder_mode)
        self.message_detection_checkbox = self._check("消息识别总开关", bool(self._setting_value("v2_enable_message_detection", True)), self._set_message_detection)
        c.addWidget(self.music_checkbox)
        c.addWidget(self.music_auto_checkbox)
        c.addWidget(self.plugin_checkbox)
        c.addWidget(self.finder_checkbox)
        c.addWidget(self.message_detection_checkbox)

        message_title = QLabel("消息识别")
        message_title.setStyleSheet(self._section_style(16, 2))
        c.addWidget(message_title)
        self.wechat_notification_checkbox = self._check("微信新消息通知", bool(self._setting_value("v2_enable_wechat_notification", True)), self._set_wechat_notification)
        self.wechat_detail_checkbox = self._check("微信详细消息通知（高性能）", bool(self._setting_value("v2_enable_wechat_detail_notification", False)), self._set_wechat_detail_notification)
        self.qq_notification_checkbox = self._check("QQ（待适配）", bool(self._setting_value("v2_enable_qq_notification", False)), self._set_qq_notification)
        self.dingtalk_notification_checkbox = self._check("钉钉（待适配）", bool(self._setting_value("v2_enable_dingtalk_notification", False)), self._set_dingtalk_notification)
        c.addWidget(self.wechat_notification_checkbox)
        c.addWidget(self.wechat_detail_checkbox)
        c.addWidget(self.qq_notification_checkbox)
        c.addWidget(self.dingtalk_notification_checkbox)

        self.manual_finder_checkbox = self._check("手动确认存在 MyFinder", bool(getattr(self.island_widget, "manual_finder_available", False)), self._set_manual_finder_available)
        self.expanded_checkbox = self._check("默认展开", bool(getattr(self.island_widget, "expanded", False)), self._set_expanded)
        c.addWidget(self.manual_finder_checkbox)
        c.addWidget(self.expanded_checkbox)
        self._update_message_controls()
        c.addStretch(1)
        layout.addStretch(1)
        return page

    def _media_page(self) -> QWidget:
        page = self._page("媒体播放")
        layout: QVBoxLayout = page._content_layout
        card = self._card(layout)
        c = card.layout()

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

        self.lyric_offset = self._slider_row(c, "歌词偏移", -12000, 12000, int(round(float(getattr(self.island_widget, "lyric_offset_seconds", 0.0)) * 1000)), self._set_lyric_offset, " ms")
        self.lyric_font_size = self._slider_row(c, "歌词字号", 14, 24, max(14, min(24, int(getattr(self.island_widget, "lyric_font_size", 18)))), self._set_lyric_font_size, " px")
        self.lyric_elastic_strength = self._slider_row(c, "悦动强度", 0, 160, max(0, min(160, int(getattr(self.island_widget, "lyric_elastic_strength", 100)))), self._set_lyric_elastic_strength, "%")
        self.lyric_word_spacing = self._slider_row(c, "字词间距", 60, 150, max(60, min(150, int(getattr(self.island_widget, "lyric_word_spacing", 100)))), self._set_lyric_word_spacing, "%")
        self.dual_line_lyrics_checkbox = self._check(
            "允许双行歌词",
            bool(getattr(self.island_widget, "enable_dual_line_lyrics", False)),
            self._set_dual_line_lyrics,
        )
        c.addWidget(self.dual_line_lyrics_checkbox)
        self.dual_line_secondary_weight = self._slider_row(c, "次行字重", 40, 100, max(40, min(100, int(getattr(self.island_widget, "dual_line_secondary_weight", 72)))), self._set_dual_line_secondary_weight, "%")
        self.dual_line_secondary_spacing = self._slider_row(c, "次行字词间距", 60, 150, max(60, min(150, int(getattr(self.island_widget, "dual_line_secondary_spacing", 100)))), self._set_dual_line_secondary_spacing, "%")
        self.lyric_elastic_mode = self._combo(list(self.LYRIC_ELASTIC_MODES), str(getattr(self.island_widget, "lyric_elastic_mode", "both")), self._set_lyric_elastic_mode)
        self._row(c, "悦动方式", self.lyric_elastic_mode)
        hint = QLabel("会根据当前播放器优先选择歌词源，并缓存到项目 lyrics 目录；也可手动放入 .lrc 文件。")
        hint.setWordWrap(True)
        hint.setStyleSheet(self._hint_style(2, 8))
        c.addWidget(hint)
        c.addStretch(1)
        layout.addStretch(1)
        return page

    def _appearance_page(self) -> QWidget:
        page = self._page("外观")
        layout: QVBoxLayout = page._content_layout
        card = self._card(layout)
        c = card.layout()

        self.seconds_checkbox = self._check("时间插件显示秒数", bool(getattr(self.island_widget, "show_seconds", True)), self._set_show_seconds)
        c.addWidget(self.seconds_checkbox)
        self.high_quality_font_checkbox = self._check("高质量字体渲染", bool(getattr(self.island_widget, "high_quality_font_rendering", False)), self._set_high_quality_font_rendering)
        c.addWidget(self.high_quality_font_checkbox)
        self.high_quality_font_checkbox.hide()
        self.plugin_count = self._slider_row(c, "插件数量", 0, 3, max(0, min(3, int(getattr(self.island_widget, "enabled_plugin_count", 3)))), self._set_plugin_count)
        if self.plugin_count.parentWidget():
            self.plugin_count.parentWidget().hide()
        self.music_ambience_style = self._combo(list(self.MUSIC_AMBIENCE_STYLES), str(getattr(self.island_widget, "music_ambience_style", "sea_level")), self._set_music_ambience_style)
        self._row(c, "音乐岛光效", self.music_ambience_style)
        self.music_preview = self._preview_frame(54)
        c.addWidget(self.music_preview)
        self.wave_slider = self._slider_row(c, "氛围光增强", 0, 100, max(0, min(100, int(getattr(self.island_widget, "ambient_intensity", 0.0) * 100))), self._set_wave_intensity, "%")
        self.music_flow_slider = self._slider_row(c, "流光浮动", 0, 100, max(0, min(100, int(getattr(self.island_widget, "music_flow_intensity", 0.74) * 100))), self._set_music_flow_intensity, "%")
        self.compact_duotone_balance = self._slider_row(c, "收缩双色占比", 0, 100, max(0, min(100, int(getattr(self.island_widget, "compact_duotone_balance", 50)))), self._set_compact_duotone_balance, "%")
        self.expanded_duotone_balance = self._slider_row(c, "展开双色占比", 0, 100, max(0, min(100, int(getattr(self.island_widget, "expanded_duotone_balance", 50)))), self._set_expanded_duotone_balance, "%")
        self.fps_slider = self._slider_row(c, "动画帧率", 24, 90, max(24, min(90, int(getattr(self.island_widget, "animation_fps", 60)))), self._set_animation_fps, " FPS")
        self.animation_speed = self._combo(list(self.ANIMATION_SPEEDS), float(getattr(self.island_widget, "animation_speed", 1.0)), self._set_animation_speed)
        self._row(c, "动画倍速", self.animation_speed)

        if self.fps_slider.parentWidget():
            self.fps_slider.parentWidget().hide()
        if self.animation_speed.parentWidget():
            self.animation_speed.parentWidget().hide()

        font_combo = QComboBox()
        font_combo.setEditable(True)
        font_combo.addItems(self.FONT_CHOICES)
        self._style_combo_popup(font_combo)
        self.font_combo = font_combo
        self._set_combo_text(font_combo, str(getattr(self.island_widget, "ui_font_family", self.FONT_CHOICES[0])))
        font_combo.currentTextChanged.connect(self._set_font_family)
        self._row(c, "全局字体", font_combo)

        self.empty_palette = self._combo([(label, key) for key, label in self.EMPTY_BAR_PALETTES], str(getattr(self.island_widget, "empty_bar_palette", "aurora")), self._set_empty_bar_palette)
        self._row(c, "空态流光", self.empty_palette)
        self.empty_preview = self._preview_frame(34)
        c.addWidget(self.empty_preview)
        self.empty_bar_width = self._slider_row(c, "横条长度", 70, 260, int(getattr(self.island_widget, "empty_bar_width", 92)), self._set_empty_bar_width, " px")
        self.empty_bar_height = self._slider_row(c, "横条粗细", 8, 28, int(getattr(self.island_widget, "empty_bar_height", 14)), self._set_empty_bar_height, " px")
        self.empty_bar_y_offset = self._slider_row(c, "横条位置", -80, 160, int(getattr(self.island_widget, "empty_bar_y_offset", 0)), self._set_empty_bar_y_offset, " px")
        self.empty_bar_opacity = self._slider_row(c, "横条不透明度", 10, 100, int(getattr(self.island_widget, "empty_bar_opacity", 100)), self._set_empty_bar_opacity, "%")
        self.custom_color_inputs = self._color_buttons_row(c, "自定义颜色", getattr(self.island_widget, "empty_bar_custom_colors", ("#46D6FF", "#73FFA6", "#FF5A86")))
        self._update_previews()
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
        pairs = (
            (self.music_checkbox, bool(getattr(self.island_widget, "music_playing", True))),
            (self.music_auto_checkbox, bool(getattr(self.island_widget, "music_auto_detect", False))),
            (self.plugin_checkbox, bool(getattr(self.island_widget, "persistent_plugin", True))),
            (self.finder_checkbox, bool(getattr(self.island_widget, "finder_mode", True))),
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
            (self.high_quality_font_checkbox, bool(getattr(self.island_widget, "high_quality_font_rendering", False))),
            (self.auto_bounds_checkbox, bool(getattr(self.island_widget, "auto_expand_bounds", True))),
            (self.barrage_backdrop, bool(getattr(self.island_widget, "barrage_use_backdrop", True))),
            (self.barrage_icon, bool(getattr(self.island_widget, "barrage_show_icon", True))),
        )
        for widget, value in pairs:
            widget.blockSignals(True)
            widget.setChecked(value)
            widget.blockSignals(False)

        slider_values = (
            (self.plugin_count, max(0, min(3, int(getattr(self.island_widget, "enabled_plugin_count", 3))))),
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
            (self.lyric_elastic_strength, max(0, min(160, int(getattr(self.island_widget, "lyric_elastic_strength", 100))))),
            (self.lyric_word_spacing, max(60, min(150, int(getattr(self.island_widget, "lyric_word_spacing", 100))))),
            (self.dual_line_secondary_weight, max(40, min(100, int(getattr(self.island_widget, "dual_line_secondary_weight", 72))))),
            (self.dual_line_secondary_spacing, max(60, min(150, int(getattr(self.island_widget, "dual_line_secondary_spacing", 100))))),
        )
        for slider, value in slider_values:
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)

        self.font_combo.blockSignals(True)
        self._set_combo_text(self.font_combo, str(getattr(self.island_widget, "ui_font_family", self.FONT_CHOICES[0])))
        self.font_combo.blockSignals(False)
        self._set_combo_data(self.empty_palette, str(getattr(self.island_widget, "empty_bar_palette", "aurora")))
        self._set_combo_data(self.expand_direction, str(getattr(self.island_widget, "expand_direction", "auto")))
        self._set_combo_data(self.animation_speed, float(getattr(self.island_widget, "animation_speed", 1.0)))
        self._set_combo_data(self.music_ambience_style, str(getattr(self.island_widget, "music_ambience_style", "sea_level")))
        self._set_combo_data(self.music_player_mode, self._music_player_mode_setting())
        self._set_combo_data(self.lyric_elastic_mode, str(getattr(self.island_widget, "lyric_elastic_mode", "both")))
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
        self.lyric_elastic_strength.blockSignals(True)
        self.lyric_elastic_strength.setValue(max(0, min(160, int(getattr(self.island_widget, "lyric_elastic_strength", 100)))))
        self.lyric_elastic_strength.blockSignals(False)
        self.lyric_word_spacing.blockSignals(True)
        self.lyric_word_spacing.setValue(max(60, min(150, int(getattr(self.island_widget, "lyric_word_spacing", 100)))))
        self.lyric_word_spacing.blockSignals(False)
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
        self._update_previews()
        self._refresh_diagnostics()

    def _update_player_progress_controls(self):
        enabled = bool(getattr(self, "player_progress_plugin_checkbox", None) and self.player_progress_plugin_checkbox.isChecked())
        auto_detect = bool(getattr(self, "player_progress_auto_detect_checkbox", None) and self.player_progress_auto_detect_checkbox.isChecked())
        if hasattr(self, "player_progress_auto_detect_checkbox"):
            self.player_progress_auto_detect_checkbox.setEnabled(enabled)
        if hasattr(self, "music_player_mode"):
            self.music_player_mode.setEnabled(enabled and not auto_detect)

    def _update_message_controls(self):
        enabled = bool(getattr(self, "message_detection_checkbox", None) and self.message_detection_checkbox.isChecked())
        for name in ("wechat_notification_checkbox", "wechat_detail_checkbox", "qq_notification_checkbox", "dingtalk_notification_checkbox"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(enabled)

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
            colors = self._current_custom_colors()
            bg = f"qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {colors[0]}, stop:0.5 {colors[1]}, stop:1 {colors[2]})"
            self.empty_preview.setStyleSheet(f"QFrame {{ border-radius: 12px; border: 1px solid #e5e7eb; background: {bg}; }}")

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

    def _setting_value(self, key: str, default=None):
        if self.settings:
            return self.settings.get(key, default)
        return default

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
        return mode if mode in {"netease", "qishui", "qq", "kugou"} else "netease"

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

    def _set_finder_mode(self, enabled: bool):
        if hasattr(self.island_widget, "set_finder_mode"):
            self.island_widget.set_finder_mode(enabled, notify=False)
        else:
            self.island_widget.finder_mode = enabled
        self._save("v2_finder_mode", enabled)
        self._touch()

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
        self._save("v2_manual_mydockfinder", enabled)
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

    def _set_weather_location(self, value: str):
        self._save("v2_weather_location", str(value or "").strip() or "Beijing")

    def _set_plugin_timezone(self, value: str):
        self._save("v2_plugin_timezone", str(value or "").strip() or "local")

    def _set_timer_end_at(self, value: str):
        self._save("v2_timer_end_at", str(value or "").strip())

    def _set_schedule_json_path(self, value: str):
        self._save("v2_schedule_json_path", str(value or "").strip())

    def _set_schedule_ics_path(self, value: str):
        self._save("v2_schedule_ics_path", str(value or "").strip())

    def _set_plugin_count(self, count: int):
        self.island_widget.enabled_plugin_count = count
        self._save("v2_plugin_count", count)
        self.island_widget._set_plugin_focus(min(self.island_widget.plugin_focus_target, max(0, count - 1)), immediate=True)
        self._touch()

    def _set_wave_intensity(self, value: int):
        intensity = max(0.0, min(1.0, value / 100.0))
        self.island_widget.ambient_intensity = intensity
        self._save("v2_ambient_intensity", intensity)
        self.island_widget.update()
        self._update_previews()

    def _set_music_ambience_style(self, _index: int = -1):
        style = self.music_ambience_style.currentData() or "sea_level"
        if hasattr(self.island_widget, "set_music_ambience_style"):
            self.island_widget.set_music_ambience_style(style, notify=False)
        else:
            self.island_widget.music_ambience_style = style
            self.island_widget.update()
        self._save("v2_music_ambience_style", style)
        self._update_previews()

    def _set_music_flow_intensity(self, value: int):
        intensity = max(0.0, min(1.0, value / 100.0))
        if hasattr(self.island_widget, "set_music_flow_intensity"):
            self.island_widget.set_music_flow_intensity(intensity, notify=False)
        else:
            self.island_widget.music_flow_intensity = intensity
            self.island_widget.update()
        self._save("v2_music_flow_intensity", intensity)
        self._update_previews()

    def _set_compact_duotone_balance(self, value: int):
        value = max(0, min(100, int(value)))
        if hasattr(self.island_widget, "set_duotone_balance"):
            self.island_widget.set_duotone_balance(compact=value, notify=False)
        else:
            self.island_widget.compact_duotone_balance = value
            self.island_widget.update()
        self._save("v2_compact_duotone_balance", value)
        self._update_previews()

    def _set_expanded_duotone_balance(self, value: int):
        value = max(0, min(100, int(value)))
        if hasattr(self.island_widget, "set_duotone_balance"):
            self.island_widget.set_duotone_balance(expanded=value, notify=False)
        else:
            self.island_widget.expanded_duotone_balance = value
            self.island_widget.update()
        self._save("v2_expanded_duotone_balance", value)
        self._update_previews()

    def _set_player_progress_auto_detect(self, enabled: bool):
        self._save("v2_player_progress_auto_detect", bool(enabled))
        self._update_player_progress_controls()
        self._refresh_diagnostics()
        self._touch()

    def _set_music_player_mode(self, _index: int = -1):
        mode = str(self.music_player_mode.currentData() or "netease")
        self._save("v2_music_player_mode", mode)
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
        self._save("v2_enable_dual_line_lyrics", enabled)
        self._touch()

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
