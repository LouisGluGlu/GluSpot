from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from local.ios_widgets import IosSwitch
from local.ui_theme import ios_theme, is_theme_change_event, style_combo_popup


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"


class PluginManagerWindow(QDialog):
    TIMEZONES = (
        ("本地时区", "local"),
        ("中国 UTC+8", "UTC+8"),
        ("日本 UTC+9", "UTC+9"),
        ("美国西部 UTC-8", "UTC-8"),
        ("美国东部 UTC-5", "UTC-5"),
        ("伦敦 UTC+0", "UTC+0"),
    )

    def __init__(
        self,
        plugin_label: str,
        plugin_detail: str = "",
        plugin_summary: str = "",
        parent: QWidget | None = None,
        settings_getter: Callable[[str, object], object] | None = None,
        settings_setter: Callable[[str, object], None] | None = None,
        refresh_callback: Callable[[], None] | None = None,
        diagnostics_getter: Callable[[], dict[str, object]] | None = None,
    ):
        super().__init__(parent)
        self.plugin_label = str(plugin_label or "PLUGIN").strip().upper()
        self.plugin_detail = str(plugin_detail or "")
        self.plugin_summary = str(plugin_summary or "")
        self.settings_getter = settings_getter or (lambda _key, default=None: default)
        self.settings_setter = settings_setter or (lambda _key, _value: None)
        self.refresh_callback = refresh_callback
        self.diagnostics_getter = diagnostics_getter
        self._applying_theme = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_live_labels)
        self._timer.start(500)

        title = self._title_for_label(self.plugin_label)
        self.setWindowTitle(f"{title}管理")
        self.setMinimumSize(500, 520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self._apply_theme()

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("Title")
        root.addWidget(title_label)

        subtitle = QLabel(self._subtitle_for_label(self.plugin_label))
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        if self.plugin_label == "TIME":
            self._build_time_page(root)
        elif self.plugin_label == "WEATHER":
            self._build_weather_page(root)
        elif self.plugin_label == "AGENDA":
            self._build_agenda_page(root)
        else:
            self._build_placeholder_page(root)

        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        root.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        self._refresh_live_labels()

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
                QDialog {{ background: {t["window"]}; color: {t["text"]}; }}
                QLabel {{ background: transparent; color: {t["text"]}; }}
                QLabel#Title {{ font-size: 24px; font-weight: 700; color: {t["text"]}; }}
                QLabel#Subtitle {{ color: {t["muted"]}; font-size: 13px; }}
                QLabel#Section {{ color: {t["muted"]}; font-size: 12px; font-weight: 700; padding: 12px 0 4px 2px; }}
                QLabel#Value {{ color: {t["text"]}; font-size: 14px; }}
                QLabel#Muted {{ color: {t["muted"]}; font-size: 12px; }}
                QFrame#Card {{ background: {t["card"]}; border: 1px solid {t["card_border"]}; border-radius: 14px; }}
                QFrame#Card QWidget {{ background: transparent; color: {t["text"]}; }}
                QPushButton {{ min-height: 34px; border: 0; border-radius: 10px; background: {t["button"]}; color: {t["text"]}; padding: 0 14px; }}
                QPushButton:hover {{ background: {t["button_hover"]}; }}
                QPushButton#Primary {{ background: #007aff; color: white; }}
                QPushButton#Danger {{ background: #ff3b30; color: white; }}
                QLineEdit, QComboBox, QSpinBox {{ color: {t["text"]}; min-height: 34px; border: 1px solid {t["card_border"]}; border-radius: 10px; padding: 4px 10px; background: {t["input"]}; }}
                QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: #007aff; background: {t["input_focus"]}; }}
                QComboBox QAbstractItemView {{ background: {t["card"]}; color: {t["text"]}; border: 1px solid {t["card_border"]}; selection-background-color: #007aff; selection-color: #ffffff; outline: 0; }}
                QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 6px 10px; background: transparent; color: {t["text"]}; }}
                QComboBox QAbstractItemView::item:hover {{ background: {t["popup_hover"]}; color: {t["text"]}; }}
                QComboBox QAbstractItemView::item:selected {{ background: #007aff; color: #ffffff; }}
                """
            )
        finally:
            self._applying_theme = False

    def _style_combo_popup(self, combo: QComboBox):
        style_combo_popup(combo, self._theme())

    def changeEvent(self, event):
        if getattr(self, "_applying_theme", False):
            super().changeEvent(event)
            return
        if is_theme_change_event(event):
            self._apply_theme()
            for combo in self.findChildren(QComboBox):
                self._style_combo_popup(combo)
        super().changeEvent(event)

    def _build_time_page(self, root: QVBoxLayout):
        status = self._card(root)
        self.current_time_label = self._value_row(status, "当前时间", "-")
        self.timezone_label = self._value_row(status, "时区", "-")
        self.timer_state_label = self._value_row(status, "计时器", "-")

        actions_title = QLabel("快捷操作")
        actions_title.setObjectName("Section")
        root.addWidget(actions_title)
        action_row = QHBoxLayout()
        for minutes in (5, 10, 25):
            button = QPushButton(f"{minutes} 分钟")
            button.clicked.connect(lambda _checked=False, m=minutes: self._start_timer(m))
            action_row.addWidget(button)
        clear = QPushButton("清除")
        clear.setObjectName("Danger")
        clear.clicked.connect(self._clear_timer)
        action_row.addWidget(clear)
        root.addLayout(action_row)

        settings = self._card(root)
        self.seconds_switch = IosSwitch("显示秒数", bool(self._setting("v2_show_seconds", True)))
        self.seconds_switch.toggled.connect(lambda checked: self._save("v2_show_seconds", bool(checked)))
        settings.addWidget(self.seconds_switch)

        timezone_combo = QComboBox()
        for text, value in self.TIMEZONES:
            timezone_combo.addItem(text, value)
        self.timezone_combo = timezone_combo
        self._set_combo_data(timezone_combo, str(self._setting("v2_plugin_timezone", "local") or "local"))
        timezone_combo.currentIndexChanged.connect(self._set_timezone_from_combo)
        self._control_row(settings, "时区", timezone_combo)

        custom_tz = QLineEdit(str(self._setting("v2_plugin_timezone", "local") or "local"))
        custom_tz.setPlaceholderText("例如 UTC+8 或 local")
        custom_tz.editingFinished.connect(self._set_custom_timezone)
        self.custom_timezone_input = custom_tz
        self._control_row(settings, "自定义时区", custom_tz)

        timer_row = QHBoxLayout()
        self.minutes_input = QSpinBox()
        self.minutes_input.setRange(1, 600)
        self.minutes_input.setValue(15)
        self.minutes_input.setSuffix(" 分钟")
        timer_row.addWidget(self.minutes_input, 1)
        start = QPushButton("开始")
        start.setObjectName("Primary")
        start.clicked.connect(lambda: self._start_timer(int(self.minutes_input.value())))
        timer_row.addWidget(start)
        settings.addLayout(timer_row)

        diagnostics_title = QLabel("诊断")
        diagnostics_title.setObjectName("Section")
        root.addWidget(diagnostics_title)
        diagnostics = self._card(root)
        self.source_label = self._value_row(diagnostics, "数据来源", "本机时间")
        self.end_at_label = self._value_row(diagnostics, "结束时间", "-")
        self.remaining_label = self._value_row(diagnostics, "剩余秒数", "-")

    def _build_placeholder_page(self, root: QVBoxLayout):
        card = self._card(root)
        self._value_row(card, "当前状态", self.plugin_detail or "-")
        self._value_row(card, "摘要", self.plugin_summary or "-")
        if self.plugin_label == "WEATHER":
            self._value_row(card, "推进方式", "天气插件先保留当前展示，等数据源和隐私策略确认后再完善。")
        elif self.plugin_label == "AGENDA":
            self._value_row(card, "推进方式", "日程插件下一阶段先做本地 JSON 管理，再补 ICS。")
        else:
            self._value_row(card, "推进方式", "后续接入插件专属设置和诊断。")

    def _build_weather_page(self, root: QVBoxLayout):
        status = self._card(root)
        self.weather_detail_label = self._value_row(status, "当前天气", self.plugin_detail or "-")
        self.weather_summary_label = self._value_row(status, "摘要", self.plugin_summary or "-")
        self.weather_error_label = self._value_row(status, "状态", "-")

        actions_title = QLabel("快捷操作")
        actions_title.setObjectName("Section")
        root.addWidget(actions_title)
        action_row = QHBoxLayout()
        refresh = QPushButton("刷新天气")
        refresh.setObjectName("Primary")
        refresh.clicked.connect(self._refresh_weather)
        action_row.addWidget(refresh)
        root.addLayout(action_row)

        settings = self._card(root)
        self.weather_location_input = QLineEdit(str(self._setting("v2_weather_location", "北京") or "北京"))
        self.weather_location_input.setPlaceholderText("例如 北京、上海、深圳")
        self.weather_location_input.editingFinished.connect(self._save_weather_location)
        self._control_row(settings, "城市", self.weather_location_input)

        self._value_row(settings, "查询方式", "免 Key 直接查询")

        extensions = QComboBox()
        extensions.addItem("实时天气", "base")
        self.weather_extensions_combo = extensions
        self._set_combo_data(extensions, str(self._setting("v2_weather_extensions", "base") or "base"))
        extensions.currentIndexChanged.connect(self._save_weather_extensions)
        self._control_row(settings, "数据类型", extensions)

        diagnostics_title = QLabel("诊断")
        diagnostics_title.setObjectName("Section")
        root.addWidget(diagnostics_title)
        diagnostics = self._card(root)
        self.weather_provider_label = self._value_row(diagnostics, "数据源", "Open-Meteo")
        self.weather_report_label = self._value_row(diagnostics, "发布时间", "-")
        self.weather_key_state_label = self._value_row(diagnostics, "Key 状态", "无需 Key")

    def _build_agenda_page(self, root: QVBoxLayout):
        status = self._card(root)
        self.agenda_detail_label = self._value_row(status, "下一项", self.plugin_detail or "-")
        self.agenda_summary_label = self._value_row(status, "摘要", self.plugin_summary or "-")

        actions_title = QLabel("新增本地日程")
        actions_title.setObjectName("Section")
        root.addWidget(actions_title)
        add_card = self._card(root)
        self.agenda_title_input = QLineEdit()
        self.agenda_title_input.setPlaceholderText("日程标题")
        self._control_row(add_card, "标题", self.agenda_title_input)
        self.agenda_time_input = QLineEdit()
        self.agenda_time_input.setPlaceholderText("2026-05-25 20:30")
        self._control_row(add_card, "时间", self.agenda_time_input)
        action_row = QHBoxLayout()
        add_button = QPushButton("新增")
        add_button.setObjectName("Primary")
        add_button.clicked.connect(self._add_agenda_event)
        clear_button = QPushButton("清空本地")
        clear_button.setObjectName("Danger")
        clear_button.clicked.connect(self._clear_agenda_events)
        action_row.addWidget(add_button)
        action_row.addWidget(clear_button)
        add_card.addLayout(action_row)

        settings_title = QLabel("数据路径")
        settings_title.setObjectName("Section")
        root.addWidget(settings_title)
        settings = self._card(root)
        self.schedule_json_input = QLineEdit(str(self._setting("v2_schedule_json_path", "") or ""))
        self.schedule_json_input.setPlaceholderText(str(DATA_DIR / "schedule.json"))
        self.schedule_json_input.editingFinished.connect(self._save_schedule_json_path)
        self._control_row(settings, "JSON", self.schedule_json_input)
        self.schedule_ics_input = QLineEdit(str(self._setting("v2_schedule_ics_path", "") or ""))
        self.schedule_ics_input.setPlaceholderText(str(DATA_DIR / "schedule.ics"))
        self.schedule_ics_input.editingFinished.connect(self._save_schedule_ics_path)
        self._control_row(settings, "ICS", self.schedule_ics_input)

        diagnostics_title = QLabel("诊断")
        diagnostics_title.setObjectName("Section")
        root.addWidget(diagnostics_title)
        diagnostics = self._card(root)
        self.agenda_file_label = self._value_row(diagnostics, "本地文件", str(DATA_DIR / "schedule.json"))
        self.agenda_count_label = self._value_row(diagnostics, "本地条目", "-")

    def _card(self, root: QVBoxLayout) -> QVBoxLayout:
        frame = QFrame()
        frame.setObjectName("Card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        root.addWidget(frame)
        return layout

    def _value_row(self, layout: QVBoxLayout, key: str, value: str) -> QLabel:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 3, 0, 3)
        row_layout.setSpacing(16)
        key_label = QLabel(key)
        key_label.setObjectName("Muted")
        key_label.setFixedWidth(84)
        value_label = QLabel(str(value))
        value_label.setObjectName("Value")
        value_label.setWordWrap(True)
        row_layout.addWidget(key_label)
        row_layout.addWidget(value_label, 1)
        layout.addWidget(row)
        return value_label

    def _control_row(self, layout: QVBoxLayout, key: str, control: QWidget):
        if isinstance(control, QComboBox):
            self._style_combo_popup(control)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 3, 0, 3)
        row_layout.setSpacing(16)
        key_label = QLabel(key)
        key_label.setObjectName("Muted")
        key_label.setFixedWidth(84)
        row_layout.addWidget(key_label)
        row_layout.addWidget(control, 1)
        layout.addWidget(row)

    def _title_for_label(self, label: str) -> str:
        return {
            "TIME": "时间 / 计时器",
            "WEATHER": "天气插件",
            "AGENDA": "日程插件",
        }.get(label, f"{label} 插件")

    def _subtitle_for_label(self, label: str) -> str:
        return {
            "TIME": "管理插件岛上的当前时间、时区和倒计时。这里的改动会同步到插件岛展示。",
            "WEATHER": "设置城市后直接查询天气，不需要申请或填写平台 Key。",
            "AGENDA": "管理本地日程；临近或到点的事项会在岛旁出现临时提醒。",
        }.get(label, "查看插件状态，并为后续配置面板预留入口。")

    def _setting(self, key: str, default=None):
        try:
            return self.settings_getter(key, default)
        except Exception:
            return default

    def _save(self, key: str, value):
        self.settings_setter(key, value)
        if key == "v2_show_seconds" and hasattr(self.parent(), "show_seconds"):
            self.parent().show_seconds = bool(value)
        if self.refresh_callback:
            self.refresh_callback()
        self._refresh_live_labels()

    def _set_combo_data(self, combo: QComboBox, value: str):
        value = str(value or "local")
        combo.blockSignals(True)
        try:
            for index in range(combo.count()):
                if str(combo.itemData(index)).lower() == value.lower():
                    combo.setCurrentIndex(index)
                    return
            combo.setCurrentIndex(0)
        finally:
            combo.blockSignals(False)

    def _set_timezone_from_combo(self):
        value = str(self.timezone_combo.currentData() or "local")
        self.custom_timezone_input.setText(value)
        self._save("v2_plugin_timezone", value)

    def _set_custom_timezone(self):
        value = str(self.custom_timezone_input.text() or "local").strip() or "local"
        if not self._timezone_is_valid(value):
            value = "local"
            self.custom_timezone_input.setText(value)
        self._set_combo_data(self.timezone_combo, value)
        self._save("v2_plugin_timezone", value)

    def _start_timer(self, minutes: int):
        minutes = max(1, min(600, int(minutes)))
        target = self._now_for_plugin() + timedelta(minutes=minutes)
        self._save("v2_timer_end_at", target.isoformat(timespec="seconds"))

    def _clear_timer(self):
        self._save("v2_timer_end_at", "")

    def _save_weather_location(self):
        value = str(self.weather_location_input.text() or "").strip() or "北京"
        self.weather_location_input.setText(value)
        self._save("v2_weather_location", value)
        self._refresh_weather()

    def _save_weather_key(self):
        input_widget = getattr(self, "weather_key_input", None)
        if input_widget is not None:
            self._save("v2_weather_api_key", str(input_widget.text() or "").strip())

    def _save_weather_extensions(self):
        self._save("v2_weather_extensions", str(self.weather_extensions_combo.currentData() or "base"))
        self._refresh_weather()

    def _refresh_weather(self):
        self._save("v2_weather_refresh_nonce", str(time.time()))

    def _save_schedule_json_path(self):
        self._save("v2_schedule_json_path", str(self.schedule_json_input.text() or "").strip())

    def _save_schedule_ics_path(self):
        self._save("v2_schedule_ics_path", str(self.schedule_ics_input.text() or "").strip())

    def _agenda_json_path(self) -> Path:
        raw = str(self._setting("v2_schedule_json_path", "") or "").strip()
        return Path(raw).expanduser() if raw else DATA_DIR / "schedule.json"

    def _add_agenda_event(self):
        title = str(self.agenda_title_input.text() or "").strip()
        raw_time = str(self.agenda_time_input.text() or "").strip()
        if not title or not raw_time:
            return
        path = self._agenda_json_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        events = []
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(payload, dict):
                events = payload.get("events") or []
            elif isinstance(payload, list):
                events = payload
        except Exception:
            events = []
        if not isinstance(events, list):
            events = []
        events.append({"title": title, "time": raw_time})
        path.write_text(json.dumps({"events": events}, ensure_ascii=False, indent=2), encoding="utf-8")
        self.agenda_title_input.clear()
        self.agenda_time_input.clear()
        self._save("v2_schedule_refresh_nonce", str(time.time()))

    def _clear_agenda_events(self):
        path = self._agenda_json_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"events": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        self._save("v2_schedule_refresh_nonce", str(time.time()))

    def _diagnostics(self) -> dict[str, object]:
        if not self.diagnostics_getter:
            return {}
        try:
            data = self.diagnostics_getter()
        except Exception:
            return {}
        plugins = data.get("plugins", {})
        return plugins if isinstance(plugins, dict) else {}

    def _refresh_live_labels(self):
        if self.plugin_label == "WEATHER" and hasattr(self, "weather_detail_label"):
            diag = self._diagnostics()
            self.weather_detail_label.setText(self.plugin_detail or "-")
            self.weather_summary_label.setText(self.plugin_summary or "-")
            error = str(diag.get("weather_error") or "").strip()
            self.weather_error_label.setText(error or "正常")
            self.weather_provider_label.setText(str(diag.get("weather_provider") or "Open-Meteo"))
            self.weather_report_label.setText(str(diag.get("weather_report_time") or "-"))
            self.weather_key_state_label.setText("无需 Key")
            return
        if self.plugin_label == "AGENDA" and hasattr(self, "agenda_count_label"):
            path = self._agenda_json_path()
            count = 0
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
                events = payload.get("events") if isinstance(payload, dict) else payload
                count = len(events) if isinstance(events, list) else 0
            except Exception:
                count = 0
            self.agenda_detail_label.setText(self.plugin_detail or "-")
            self.agenda_summary_label.setText(self.plugin_summary or "-")
            self.agenda_file_label.setText(str(path))
            self.agenda_count_label.setText(str(count))
            return
        if self.plugin_label != "TIME" or not hasattr(self, "current_time_label"):
            return
        now = self._now_for_plugin()
        show_seconds = bool(self._setting("v2_show_seconds", True))
        self.current_time_label.setText(now.strftime("%H:%M:%S" if show_seconds else "%H:%M"))
        self.timezone_label.setText(self._timezone_label())
        remaining = self._remaining_seconds(now)
        end_at = str(self._setting("v2_timer_end_at", "") or "").strip()
        self.end_at_label.setText(end_at or "-")
        self.remaining_label.setText("-" if remaining is None else str(remaining))
        if remaining is None:
            self.timer_state_label.setText("未启动")
        elif remaining <= 0:
            self.timer_state_label.setText("计时结束")
        else:
            minutes, seconds = divmod(remaining, 60)
            hours, minutes = divmod(minutes, 60)
            if hours:
                text = f"剩余 {hours}:{minutes:02d}:{seconds:02d}"
            else:
                text = f"剩余 {minutes}:{seconds:02d}"
            self.timer_state_label.setText(text)
        diag = self._diagnostics()
        if diag and hasattr(self, "source_label"):
            self.source_label.setText(f"{diag.get('time_timezone') or self._timezone_label()} / 本机时间")

    def _now_for_plugin(self) -> datetime:
        tzinfo = self._timezone()
        return datetime.now(tzinfo) if tzinfo is not None else datetime.now().astimezone()

    def _timezone(self):
        value = str(self._setting("v2_plugin_timezone", "local") or "local").strip()
        return self._timezone_for_value(value)

    def _timezone_for_value(self, value: str):
        if value.lower() in {"", "local"}:
            return None
        normalized = value.upper().replace(" ", "")
        if normalized.startswith("UTC"):
            offset = normalized[3:]
            if not offset:
                return timezone.utc
            sign = 1
            if offset[0] == "-":
                sign = -1
                offset = offset[1:]
            elif offset[0] == "+":
                offset = offset[1:]
            parts = offset.split(":", 1)
            try:
                hours = int(parts[0])
                minutes = int(parts[1]) if len(parts) > 1 else 0
            except ValueError:
                return None
            if hours > 14 or minutes >= 60:
                return None
            return timezone(sign * timedelta(hours=hours, minutes=minutes))
        return None

    def _timezone_is_valid(self, value: str) -> bool:
        value = str(value or "").strip()
        return value.lower() in {"", "local"} or self._timezone_for_value(value) is not None

    def _timezone_label(self) -> str:
        raw = str(self._setting("v2_plugin_timezone", "local") or "local").strip()
        if raw.lower() in {"", "local"}:
            return "本地时区"
        return raw.upper().replace(" ", "") if self._timezone() is not None else "本地时区"

    def _remaining_seconds(self, now: datetime) -> int | None:
        raw = str(self._setting("v2_timer_end_at", "") or "").strip()
        if not raw:
            return None
        try:
            target = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=now.tzinfo)
        return max(0, int((target.astimezone(now.tzinfo) - now).total_seconds()))
