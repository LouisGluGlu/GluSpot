from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api.plugin_provider import RealPluginProvider


class PluginProviderTests(unittest.TestCase):
    def test_reads_json_schedule_events(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "schedule.json"
            path.write_text('{"events":[{"title":"Class","time":"2099-01-01 09:00"}]}', encoding="utf-8")
            provider = RealPluginProvider(lambda key, default=None: str(path) if key == "v2_schedule_json_path" else default)

            events = provider._load_events()

        self.assertEqual(events[0][1], "Class")

    def test_timer_summary_uses_iso_setting(self):
        provider = RealPluginProvider(lambda key, default=None: "2099-01-01T00:00:00" if key == "v2_timer_end_at" else default)

        plugin = provider._time_plugin()

        self.assertIn("倒计时", plugin.summary)
        self.assertEqual(plugin.plugin_id, "time")
        self.assertEqual(plugin.status, "running")

    def test_time_plugin_uses_yellow_accent(self):
        provider = RealPluginProvider()

        plugin = provider._time_plugin()

        self.assertGreaterEqual(plugin.color.red, 240)
        self.assertGreaterEqual(plugin.color.green, 180)
        self.assertLessEqual(plugin.color.blue, 80)

    def test_active_plugins_only_export_time_until_weather_and_agenda_are_ready(self):
        provider = RealPluginProvider()

        plugins = provider.plugins()

        self.assertEqual([plugin.plugin_id for plugin in plugins], ["time"])

    def test_running_timer_exports_alert_copy(self):
        provider = RealPluginProvider(lambda key, default=None: "2099-01-01T00:00:00" if key == "v2_timer_end_at" else default)

        plugin = provider._time_plugin()

        self.assertEqual(plugin.alert_title, "计时进行中")
        self.assertIn("倒计时", plugin.alert_body)
        self.assertEqual(plugin.alert_priority, 30)

    def test_running_timer_exports_progress_fraction(self):
        now = datetime(2026, 5, 25, 12, 5, tzinfo=timezone.utc)
        provider = RealPluginProvider(
            lambda key, default=None: {
                "v2_timer_started_at": "2026-05-25T12:00:00+00:00",
                "v2_timer_end_at": "2026-05-25T12:10:00+00:00",
            }.get(key, default)
        )
        provider._plugin_now = lambda: now

        plugin = provider._time_plugin()

        self.assertEqual(plugin.status, "running")
        self.assertAlmostEqual(plugin.progress, 0.5)

    def test_timer_state_reports_done(self):
        now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
        provider = RealPluginProvider(lambda key, default=None: "2026-05-25T11:59:00+00:00" if key == "v2_timer_end_at" else default)

        self.assertEqual(provider.timer_state(now), "done")
        self.assertEqual(provider._timer_summary(now), "计时结束")

    def test_done_timer_exports_alert_copy(self):
        now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
        provider = RealPluginProvider(lambda key, default=None: "2026-05-25T11:59:00+00:00" if key == "v2_timer_end_at" else default)
        provider._plugin_now = lambda: now

        plugin = provider._time_plugin()

        self.assertEqual(plugin.status, "done")
        self.assertEqual(plugin.alert_title, "计时结束")
        self.assertIn("清除", plugin.alert_body)
        self.assertEqual(plugin.alert_priority, 5)

    def test_alarm_due_exports_alert_copy(self):
        now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
        provider = RealPluginProvider(lambda key, default=None: "2026-05-25T12:00:00+00:00" if key == "v2_alarm_at" else default)
        provider._plugin_now = lambda: now

        plugin = provider._time_plugin()

        self.assertEqual(plugin.status, "alarm")
        self.assertEqual(plugin.alert_title, "闹钟提醒")
        self.assertIn("12:00", plugin.alert_body)
        self.assertEqual(plugin.alert_priority, 3)

    def test_alarm_rows_due_exports_note_alert_copy(self):
        now = datetime(2026, 5, 25, 7, 30, 20, tzinfo=timezone.utc)
        rows = [
            {
                "time": "07:30",
                "label": "早课",
                "enabled": True,
                "frequency_unit": "daily",
            }
        ]
        provider = RealPluginProvider(lambda key, default=None: json.dumps(rows, ensure_ascii=False) if key == "v2_alarm_rows" else default)
        provider._plugin_now = lambda: now

        plugin = provider._time_plugin()

        self.assertEqual(plugin.status, "alarm")
        self.assertEqual(plugin.summary, "07:30")
        self.assertEqual(plugin.alert_title, "闹钟提醒")
        self.assertEqual(plugin.alert_body, "早课")
        self.assertEqual(plugin.alert_priority, 3)
        self.assertEqual(plugin.progress, 1.0)

    def test_alarm_rows_ignore_disabled_rows(self):
        now = datetime(2026, 5, 25, 7, 30, tzinfo=timezone.utc)
        rows = [{"time": "07:30", "label": "早课", "enabled": False, "frequency_unit": "daily"}]
        provider = RealPluginProvider(lambda key, default=None: json.dumps(rows, ensure_ascii=False) if key == "v2_alarm_rows" else default)
        provider._plugin_now = lambda: now

        plugin = provider._time_plugin()

        self.assertNotEqual(plugin.status, "alarm")

    def test_weekly_alarm_rows_fire_only_on_selected_weekday(self):
        rows = [
            {
                "time": "15:22",
                "label": "周会",
                "enabled": True,
                "frequency_unit": "weekly",
                "weekdays": [2, 4, 6],
            }
        ]
        provider = RealPluginProvider(lambda key, default=None: json.dumps(rows, ensure_ascii=False) if key == "v2_alarm_rows" else default)

        provider._plugin_now = lambda: datetime(2026, 5, 26, 15, 22, tzinfo=timezone.utc)
        self.assertEqual(provider._time_plugin().status, "alarm")

        provider._plugin_now = lambda: datetime(2026, 5, 27, 15, 22, tzinfo=timezone.utc)
        self.assertNotEqual(provider._time_plugin().status, "alarm")

    def test_monthly_alarm_rows_respect_month_length(self):
        rows = [
            {
                "time": "8:05",
                "label": "月报",
                "enabled": True,
                "frequency_unit": "monthly",
                "month_day": 31,
            }
        ]
        provider = RealPluginProvider(lambda key, default=None: json.dumps(rows, ensure_ascii=False) if key == "v2_alarm_rows" else default)

        provider._plugin_now = lambda: datetime(2026, 5, 31, 8, 5, tzinfo=timezone.utc)
        self.assertEqual(provider._time_plugin().status, "alarm")

        provider._plugin_now = lambda: datetime(2026, 6, 30, 8, 5, tzinfo=timezone.utc)
        self.assertNotEqual(provider._time_plugin().status, "alarm")

    def test_yearly_feb_29_alarm_rows_use_leap_year_rule(self):
        rows = [
            {
                "time": "9:00",
                "label": "闰年",
                "enabled": True,
                "frequency_unit": "yearly",
                "year_month": 2,
                "year_day": 29,
            }
        ]
        provider = RealPluginProvider(lambda key, default=None: json.dumps(rows, ensure_ascii=False) if key == "v2_alarm_rows" else default)

        provider._plugin_now = lambda: datetime(2028, 2, 29, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(provider._time_plugin().status, "alarm")

        provider._plugin_now = lambda: datetime(2027, 2, 28, 9, 0, tzinfo=timezone.utc)
        self.assertNotEqual(provider._time_plugin().status, "alarm")

    def test_timer_remaining_uses_plugin_timezone(self):
        now = datetime(2026, 5, 25, 20, 0, tzinfo=timezone(timedelta(hours=8)))
        provider = RealPluginProvider(
            lambda key, default=None: {
                "v2_plugin_timezone": "UTC+8",
                "v2_timer_end_at": "2026-05-25T20:25:00+08:00",
            }.get(key, default)
        )

        self.assertEqual(provider._timezone_label(), "UTC+8")
        self.assertEqual(provider.timer_remaining_seconds(now), 1500)

    def test_plugin_diagnostics_include_timer_fields(self):
        provider = RealPluginProvider(lambda key, default=None: "" if key == "v2_timer_end_at" else default)

        diagnostics = provider.diagnostics()

        self.assertEqual(diagnostics["timer_state"], "idle")
        self.assertIsNone(diagnostics["timer_remaining_seconds"])

    def test_weather_refreshes_without_api_key(self):
        provider = RealPluginProvider(
            lambda key, default=None: {
                "v2_weather_location": "北京",
                "v2_weather_api_key": "",
                "v2_weather_refresh_nonce": "1",
            }.get(key, default)
        )
        provider._resolve_weather_location = lambda _location: (39.9042, 116.4074, "北京市")
        provider._fetch_open_meteo_weather = lambda _lat, _lon: {
            "weather": "晴",
            "temperature": "26",
            "winddirection": "东南风",
            "windspeed": "12",
            "humidity": "40",
            "reporttime": "2026-05-26T12:00",
        }

        provider._refresh_weather("北京")
        plugin = provider._weather_plugin()

        self.assertEqual(plugin.status, "normal")
        self.assertEqual(plugin.detail, "26°C 晴")
        self.assertIn("北京市", plugin.summary)
        self.assertFalse(provider.diagnostics()["weather_has_api_key"])
        self.assertFalse(provider.diagnostics()["weather_requires_api_key"])

    def test_weather_prefers_detected_location_when_enabled(self):
        provider = RealPluginProvider(
            lambda key, default=None: {
                "v2_weather_use_location": True,
                "v2_weather_manual_location": "北京",
            }.get(key, default)
        )
        provider._detect_user_location = lambda: (31.2304, 121.4737, "上海市", "windows-location")
        provider._resolve_weather_location = lambda _location: (_ for _ in ()).throw(AssertionError("manual geocode should not run"))
        provider._fetch_open_meteo_weather = lambda _lat, _lon: {
            "weather": "晴",
            "temperature": "26",
            "winddirection": "东南风",
            "windspeed": "12",
            "humidity": "40",
            "reporttime": "2026-05-26T12:00",
        }

        provider._refresh_weather("auto:北京")

        plugin = provider._weather_plugin()
        self.assertIn("上海市", plugin.summary)
        self.assertEqual(provider.diagnostics()["weather_location_source"], "windows-location")

    def test_weather_falls_back_to_manual_city_when_location_unavailable(self):
        provider = RealPluginProvider(
            lambda key, default=None: {
                "v2_weather_use_location": True,
                "v2_weather_manual_location": "北京",
            }.get(key, default)
        )
        provider._detect_user_location = lambda: None
        provider._resolve_weather_location = lambda _location: (39.9042, 116.4074, "北京市")
        provider._fetch_open_meteo_weather = lambda _lat, _lon: {
            "weather": "晴",
            "temperature": "26",
            "winddirection": "东南风",
            "windspeed": "12",
            "humidity": "40",
            "reporttime": "2026-05-26T12:00",
        }

        provider._refresh_weather("auto:北京")

        plugin = provider._weather_plugin()
        self.assertIn("北京市", plugin.summary)
        self.assertEqual(provider.diagnostics()["weather_location_source"], "manual-fallback")

    def test_agenda_diagnostics_include_class_schedule_roadmap(self):
        provider = RealPluginProvider()

        diagnostics = provider.diagnostics()

        self.assertIn("课表读取录入", diagnostics["schedule_todo_items"])
        self.assertIn("课表提醒功能", diagnostics["schedule_todo_items"])

    def test_weather_loading_exports_alert_copy(self):
        provider = RealPluginProvider(lambda key, default=None: {"v2_weather_location": "北京"}.get(key, default))
        with provider._weather_lock:
            provider._weather_location = "北京"
            provider._weather_detail = "北京"
            provider._weather_summary = "天气刷新中"
            provider._weather_refreshing = True

        plugin = provider._weather_plugin()

        self.assertEqual(plugin.status, "loading")
        self.assertEqual(plugin.alert_title, "天气刷新中")
        self.assertEqual(plugin.alert_body, "北京")
        self.assertEqual(plugin.alert_priority, 40)

    def test_weather_error_exports_alert_copy(self):
        provider = RealPluginProvider(lambda key, default=None: {"v2_weather_location": "北京"}.get(key, default))
        with provider._weather_lock:
            provider._weather_location = "北京"
            provider._weather_cached_at = 999999999.0
            provider._weather_detail = "北京"
            provider._weather_summary = "上次数据"
            provider._weather_error = "network down"

        plugin = provider._weather_plugin()

        self.assertEqual(plugin.status, "error")
        self.assertEqual(plugin.alert_title, "天气不可用")
        self.assertIn("network down", plugin.alert_body)
        self.assertEqual(plugin.alert_priority, 20)

    def test_weather_rain_exports_alert_copy(self):
        provider = RealPluginProvider(lambda key, default=None: {"v2_weather_location": "北京"}.get(key, default))
        provider._resolve_weather_location = lambda _location: (39.9042, 116.4074, "北京市")
        provider._fetch_open_meteo_weather = lambda _lat, _lon: {
            "city": "北京市",
            "weather": "小雨",
            "temperature": "22",
            "winddirection": "东风",
            "windspeed": "8",
            "humidity": "80",
            "weather_code": 61,
            "precipitation": "1.2",
        }

        provider._refresh_weather("北京")
        plugin = provider._weather_plugin()

        self.assertEqual(plugin.status, "rain")
        self.assertEqual(plugin.alert_title, "降雨提醒")
        self.assertIn("小雨", plugin.alert_body)
        self.assertEqual(plugin.alert_priority, 12)

    def test_weather_severe_exports_alert_copy(self):
        provider = RealPluginProvider(lambda key, default=None: {"v2_weather_location": "北京"}.get(key, default))
        provider._resolve_weather_location = lambda _location: (39.9042, 116.4074, "北京市")
        provider._fetch_open_meteo_weather = lambda _lat, _lon: {
            "city": "北京市",
            "weather": "雷雨",
            "temperature": "25",
            "winddirection": "东风",
            "windspeed": "18",
            "humidity": "88",
            "weather_code": 95,
            "precipitation": "4.0",
        }

        provider._refresh_weather("北京")
        plugin = provider._weather_plugin()

        self.assertEqual(plugin.status, "severe")
        self.assertEqual(plugin.alert_title, "严重天气提醒")
        self.assertIn("雷雨", plugin.alert_body)
        self.assertEqual(plugin.alert_priority, 2)

    def test_open_meteo_geocode_parses_location(self):
        provider = RealPluginProvider()
        payload = {
            "results": [{"latitude": 31.23, "longitude": 121.47, "name": "上海", "admin1": "上海市"}],
        }
        provider._read_json_url = lambda _url: payload

        lat, lon, city = provider._resolve_weather_location("测试城市")

        self.assertEqual(lat, 31.23)
        self.assertEqual(lon, 121.47)
        self.assertEqual(city, "上海 · 上海市")

    def test_open_meteo_forecast_parses_current_weather(self):
        provider = RealPluginProvider()
        payload = {
            "current": {
                "temperature_2m": 26.4,
                "relative_humidity_2m": 40,
                "weather_code": 0,
                "wind_speed_10m": 12.1,
                "wind_direction_10m": 135,
                "time": "2026-05-26T12:00",
            }
        }
        provider._read_json_url = lambda _url: payload

        weather = provider._fetch_open_meteo_weather(39.9042, 116.4074)

        self.assertEqual(weather["weather"], "晴")
        self.assertEqual(weather["temperature"], "26.4")
        self.assertEqual(weather["humidity"], "40")
        self.assertEqual(weather["winddirection"], "东南风")

    def test_no_key_weather_live_parses_plugin_text(self):
        provider = RealPluginProvider(
            lambda key, default=None: {
                "v2_weather_location": "北京",
                "v2_weather_refresh_nonce": "1",
            }.get(key, default)
        )
        provider._resolve_weather_location = lambda _location: (39.9042, 116.4074, "北京市")
        provider._fetch_open_meteo_weather = lambda _lat, _lon: {
            "city": "北京市",
            "weather": "晴",
            "temperature": "26",
            "winddirection": "东南风",
            "windspeed": "12",
            "humidity": "40",
            "reporttime": "2026-05-26T12:00",
        }

        provider._refresh_weather("北京")
        plugin = provider._weather_plugin()

        self.assertEqual(plugin.detail, "26°C 晴")
        self.assertIn("北京市", plugin.summary)
        self.assertEqual(plugin.status, "normal")
        self.assertEqual(provider.diagnostics()["weather_report_time"], "2026-05-26T12:00")

    def test_amap_weather_forecast_parses_when_extensions_all(self):
        provider = RealPluginProvider()
        payload = {
            "status": "1",
            "forecasts": [
                {
                    "city": "北京市",
                    "reporttime": "2026-05-25 12:00:00",
                    "casts": [
                        {
                            "dayweather": "多云",
                            "daytemp": "28",
                            "nighttemp": "18",
                            "daywind": "东南",
                            "daypower": "3",
                        }
                    ],
                }
            ],
        }
        provider._read_json_url = lambda _url: payload

        weather = provider._fetch_weather("110000", "key")

        self.assertEqual(weather["city"], "北京市")
        self.assertEqual(weather["weather"], "多云")
        self.assertEqual(weather["temperature"], "18-28")

    def test_schedule_plugin_marks_due_and_soon_events(self):
        now = datetime.now().astimezone()
        due = (now - timedelta(minutes=2)).isoformat(timespec="minutes")
        soon = (now + timedelta(minutes=4)).isoformat(timespec="minutes")
        provider = RealPluginProvider()
        provider._load_events = lambda: [(provider._parse_datetime(soon), "下一项"), (provider._parse_datetime(due), "正在进行")]

        plugin = provider._schedule_plugin()

        self.assertEqual(plugin.status, "due")
        self.assertEqual(plugin.detail, "现在")
        self.assertEqual(plugin.summary, "正在进行")

    def test_schedule_soon_exports_alert_copy(self):
        now = datetime.now().astimezone()
        soon = (now + timedelta(minutes=4)).isoformat(timespec="minutes")
        provider = RealPluginProvider()
        provider._load_events = lambda: [(provider._parse_datetime(soon), "准备开会")]

        plugin = provider._schedule_plugin()

        self.assertEqual(plugin.status, "soon")
        self.assertEqual(plugin.alert_title, "日程将近")
        self.assertIn("准备开会", plugin.alert_body)
        self.assertEqual(plugin.alert_priority, 10)

    def test_schedule_due_exports_alert_copy(self):
        now = datetime.now().astimezone()
        due = (now - timedelta(minutes=1)).isoformat(timespec="minutes")
        provider = RealPluginProvider()
        provider._load_events = lambda: [(provider._parse_datetime(due), "正在开会")]

        plugin = provider._schedule_plugin()

        self.assertEqual(plugin.status, "due")
        self.assertEqual(plugin.alert_title, "日程到点")
        self.assertIn("正在开会", plugin.alert_body)
        self.assertEqual(plugin.alert_priority, 0)

    def test_schedule_uses_custom_reminder_thresholds(self):
        now = datetime.now().astimezone()
        upcoming = (now + timedelta(minutes=28)).isoformat(timespec="minutes")
        provider = RealPluginProvider(lambda key, default=None: "30,15,5" if key == "v2_schedule_reminder_minutes" else default)
        provider._load_events = lambda: [(provider._parse_datetime(upcoming), "准备上课")]

        plugin = provider._schedule_plugin()

        self.assertEqual(plugin.status, "soon")
        self.assertIn("准备上课", plugin.alert_body)
        self.assertGreater(plugin.progress, 0.0)


if __name__ == "__main__":
    unittest.main()
