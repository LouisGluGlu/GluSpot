from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import urlopen

from api.models import ColorToken, PluginData


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
AMAP_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
AMAP_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
OPEN_METEO_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

COMMON_CITY_COORDS = {
    "北京": (39.9042, 116.4074, "北京市"),
    "北京市": (39.9042, 116.4074, "北京市"),
    "上海": (31.2304, 121.4737, "上海市"),
    "上海市": (31.2304, 121.4737, "上海市"),
    "广州": (23.1291, 113.2644, "广州市"),
    "广州市": (23.1291, 113.2644, "广州市"),
    "深圳": (22.5431, 114.0579, "深圳市"),
    "深圳市": (22.5431, 114.0579, "深圳市"),
    "杭州": (30.2741, 120.1551, "杭州市"),
    "杭州市": (30.2741, 120.1551, "杭州市"),
    "成都": (30.5728, 104.0668, "成都市"),
    "成都市": (30.5728, 104.0668, "成都市"),
    "南京": (32.0603, 118.7969, "南京市"),
    "南京市": (32.0603, 118.7969, "南京市"),
    "武汉": (30.5928, 114.3055, "武汉市"),
    "武汉市": (30.5928, 114.3055, "武汉市"),
    "西安": (34.3416, 108.9398, "西安市"),
    "西安市": (34.3416, 108.9398, "西安市"),
    "重庆": (29.5630, 106.5516, "重庆市"),
    "重庆市": (29.5630, 106.5516, "重庆市"),
    "天津": (39.3434, 117.3616, "天津市"),
    "天津市": (39.3434, 117.3616, "天津市"),
}

WEATHER_CODE_TEXT = {
    0: "晴",
    1: "大部晴朗",
    2: "少云",
    3: "阴",
    45: "雾",
    48: "霜雾",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    61: "小雨",
    63: "雨",
    65: "大雨",
    71: "小雪",
    73: "雪",
    75: "大雪",
    80: "阵雨",
    81: "强阵雨",
    82: "暴阵雨",
    95: "雷雨",
    96: "雷雨冰雹",
    99: "强雷雨冰雹",
}


class RealPluginProvider:
    def __init__(self, settings_getter: Callable[[str, object], object] | None = None):
        self.settings_getter = settings_getter or (lambda _key, default=None: default)
        self._weather_location = ""
        self._weather_coords: tuple[float, float, str] | None = None
        self._weather_cached_at = 0.0
        self._weather_summary = "未设置天气"
        self._weather_detail = "设置位置后刷新"
        self._weather_report_time = ""
        self._weather_error = ""
        self._weather_location_source = "manual"
        self._weather_last_nonce = ""
        self._weather_refreshing = False
        self._weather_lock = threading.Lock()

    def plugins(self) -> tuple[PluginData, ...]:
        return (
            self._time_plugin(),
            self._weather_plugin(),
            self._schedule_plugin(),
        )

    def diagnostics(self) -> dict[str, object]:
        now = self._plugin_now()
        return {
            "time_timezone": self._timezone_label(),
            "time_show_seconds": bool(self._setting("v2_show_seconds", True)),
            "time_now": now.isoformat(timespec="seconds"),
            "timer_end_at": str(self._setting("v2_timer_end_at", "") or ""),
            "timer_remaining_seconds": self.timer_remaining_seconds(now),
            "timer_state": self.timer_state(now),
            "weather_location": self._setting("v2_weather_location", "北京"),
            "weather_provider": "Open-Meteo 免 Key",
            "weather_has_api_key": False,
            "weather_requires_api_key": False,
            "weather_location_source": self._weather_location_source,
            "weather_extensions": str(self._setting("v2_weather_extensions", "base") or "base"),
            "weather_error": self._weather_error,
            "weather_report_time": self._weather_report_time,
            "schedule_json_path": self._setting("v2_schedule_json_path", ""),
            "schedule_ics_path": self._setting("v2_schedule_ics_path", ""),
            "schedule_todo_items": ("课表读取录入", "课表提醒功能"),
        }

    def _setting(self, key: str, default=None):
        try:
            return self.settings_getter(key, default)
        except Exception:
            return default

    def _time_plugin(self) -> PluginData:
        now = self._plugin_now()
        show_seconds = bool(self._setting("v2_show_seconds", True))
        detail = now.strftime("%H:%M:%S" if show_seconds else "%H:%M")
        summary = now.strftime("%m/%d %a")
        timer = self._timer_summary(now)
        status = self.timer_state(now)
        if timer:
            summary = timer
        alert_title = ""
        alert_body = ""
        alert_priority = 50
        if status == "running":
            alert_title = "计时进行中"
            alert_body = timer or summary
            alert_priority = 30
        elif status == "done":
            alert_title = "计时结束"
            alert_body = "点击查看或清除计时器"
            alert_priority = 5
        return PluginData(
            label="TIME",
            detail=detail,
            summary=summary,
            color=ColorToken(67, 162, 255),
            icon="◷",
            plugin_id="time",
            status=status,
            primary_value=detail,
            secondary_value=summary,
            alert_title=alert_title,
            alert_body=alert_body,
            alert_priority=alert_priority,
            progress=self.timer_progress(now),
        )

    def _parse_timezone(self, value: str):
        value = str(value or "").strip()
        if value.lower() in {"", "local"}:
            return None
        match = re.fullmatch(r"UTC\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", value, re.I)
        if not match:
            return None
        sign = 1 if match.group(1) == "+" else -1
        hours = int(match.group(2))
        minutes = int(match.group(3) or "0")
        if hours > 14 or minutes >= 60:
            return None
        return timezone(sign * timedelta(hours=hours, minutes=minutes))

    def _plugin_timezone(self):
        tz = str(self._setting("v2_plugin_timezone", "local") or "local").strip()
        return self._parse_timezone(tz)

    def _plugin_now(self) -> datetime:
        tzinfo = self._plugin_timezone()
        return datetime.now(tzinfo) if tzinfo is not None else datetime.now().astimezone()

    def _timezone_label(self) -> str:
        raw = str(self._setting("v2_plugin_timezone", "local") or "local").strip()
        if raw.lower() in {"", "local"}:
            return "本地时区"
        parsed = self._parse_timezone(raw)
        return raw.upper().replace(" ", "") if parsed is not None else "本地时区"

    def _timer_summary(self, now: datetime) -> str:
        remaining = self.timer_remaining_seconds(now)
        if remaining is None:
            return ""
        if remaining <= 0:
            return "计时结束"
        minutes, seconds = divmod(remaining, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"倒计时 {hours}:{minutes:02d}:{seconds:02d}"
        return f"倒计时 {minutes}:{seconds:02d}"

    def timer_end_at(self, now: datetime | None = None) -> datetime | None:
        now = now or self._plugin_now()
        raw = str(self._setting("v2_timer_end_at", "") or "").strip()
        if not raw:
            return None
        try:
            target = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=now.tzinfo)
        return target.astimezone(now.tzinfo)

    def timer_started_at(self, now: datetime | None = None) -> datetime | None:
        now = now or self._plugin_now()
        raw = str(self._setting("v2_timer_started_at", "") or "").strip()
        if not raw:
            return None
        try:
            started = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if started.tzinfo is None:
            started = started.replace(tzinfo=now.tzinfo)
        return started.astimezone(now.tzinfo)

    def timer_remaining_seconds(self, now: datetime | None = None) -> int | None:
        now = now or self._plugin_now()
        target = self.timer_end_at(now)
        if target is None:
            return None
        return max(0, int((target - now).total_seconds()))

    def timer_progress(self, now: datetime | None = None) -> float:
        now = now or self._plugin_now()
        target = self.timer_end_at(now)
        if target is None:
            return 0.0
        if target <= now:
            return 1.0
        started = self.timer_started_at(now)
        if started is None or started >= target:
            return 0.0
        total = max(1.0, (target - started).total_seconds())
        elapsed = max(0.0, (now - started).total_seconds())
        return max(0.0, min(1.0, elapsed / total))

    def timer_state(self, now: datetime | None = None) -> str:
        remaining = self.timer_remaining_seconds(now or self._plugin_now())
        if remaining is None:
            return "idle"
        if remaining <= 0:
            return "done"
        return "running"

    def _weather_plugin(self) -> PluginData:
        self._schedule_weather_refresh_if_needed()
        with self._weather_lock:
            detail = self._weather_detail
            summary = self._weather_summary
            error = self._weather_error
            refreshing = self._weather_refreshing
        status = "loading" if refreshing else ("error" if error else "normal")
        alert_title = ""
        alert_body = ""
        alert_priority = 50
        if status == "loading":
            alert_title = "天气刷新中"
            alert_body = detail or summary
            alert_priority = 40
        elif status == "error":
            alert_title = "天气不可用"
            alert_body = error or summary or detail
            alert_priority = 20
        return PluginData(
            label="WEATHER",
            detail=detail,
            summary=summary,
            color=ColorToken(80, 210, 255),
            icon="☁",
            plugin_id="weather",
            status=status,
            primary_value=detail,
            secondary_value=summary,
            alert_title=alert_title,
            alert_body=alert_body,
            alert_priority=alert_priority,
        )

    def _schedule_weather_refresh_if_needed(self):
        location = self._weather_request_location()
        nonce = str(self._setting("v2_weather_refresh_nonce", "") or "").strip()
        now = time.monotonic()
        if not location:
            with self._weather_lock:
                self._weather_summary = "未设置天气"
                self._weather_detail = "填写城市"
                self._weather_error = ""
            return
        with self._weather_lock:
            fresh = location == self._weather_location and now - self._weather_cached_at < 900
            refreshing = self._weather_refreshing
            force_refresh = nonce and nonce != self._weather_last_nonce
            if location != self._weather_location and not refreshing:
                self._weather_location = location
                self._weather_detail = location[:16]
                self._weather_summary = "天气刷新中"
                self._weather_error = ""
        if (fresh and not force_refresh) or refreshing:
            return
        with self._weather_lock:
            self._weather_refreshing = True
            self._weather_last_nonce = nonce
        thread = threading.Thread(target=self._refresh_weather, args=(location,), name="WeatherPluginRefresh", daemon=True)
        thread.start()

    def _weather_manual_location(self) -> str:
        unset = object()
        manual = self._setting("v2_weather_manual_location", unset)
        if manual is unset:
            manual = self._setting("v2_weather_location", "北京")
        return str(manual or "").strip() or "北京"

    def _weather_request_location(self) -> str:
        manual = self._weather_manual_location()
        unset = object()
        use_location = self._setting("v2_weather_use_location", unset)
        if use_location is unset:
            return manual
        if bool(use_location):
            return f"auto:{manual}"
        return manual

    def _detect_user_location(self) -> tuple[float, float, str, str] | None:
        try:
            from winsdk.windows.devices.geolocation import Geolocator

            geoposition = Geolocator().get_geoposition_async().get()
            point = geoposition.coordinate.point
            position = point.position
            return float(position.latitude), float(position.longitude), "当前位置", "windows-location"
        except Exception:
            return None

    def _refresh_weather(self, location: str):
        now = time.monotonic()
        summary = ""
        detail = ""
        error = ""
        report_time = ""
        location_source = "manual"
        request_location = str(location or "").strip() or self._weather_manual_location()
        self._weather_location = location
        try:
            if request_location.startswith("auto:"):
                manual_location = request_location.split(":", 1)[1].strip() or self._weather_manual_location()
                detected = self._detect_user_location()
                if detected is not None:
                    lat, lon, city_name, detected_source = detected
                    request_location = city_name or manual_location
                    location_source = detected_source or "detected"
                else:
                    lat, lon, city_name = self._resolve_weather_location(manual_location)
                    request_location = manual_location
                    location_source = "manual-fallback"
            else:
                lat, lon, city_name = self._resolve_weather_location(request_location)
            if not city_name:
                summary = "未找到城市"
                detail = request_location[:16]
                error = "geocoding failed"
                return
            weather = self._fetch_open_meteo_weather(lat, lon)
            city = str(weather.get("city") or city_name or request_location)
            weather_name = str(weather.get("weather") or "天气")
            temp = str(weather.get("temperature") or "--")
            wind_direction = str(weather.get("winddirection") or "")
            wind_speed = str(weather.get("windspeed") or "")
            humidity = str(weather.get("humidity") or "")
            report_time = str(weather.get("reporttime") or "")
            detail = f"{temp}°C {weather_name}"
            wind = f"{wind_direction}{wind_speed}km/h" if wind_direction and wind_speed else "风力未知"
            humidity_text = f"湿度 {humidity}%" if humidity else "湿度未知"
            summary = f"{city} · {wind} · {humidity_text}"
        except Exception as exc:
            with self._weather_lock:
                summary = self._weather_summary or "天气不可用"
                detail = self._weather_detail or request_location[:16]
            error = str(exc)
        finally:
            with self._weather_lock:
                if summary:
                    self._weather_summary = summary
                if detail:
                    self._weather_detail = detail
                self._weather_error = error
                self._weather_report_time = report_time
                self._weather_location_source = location_source
                self._weather_cached_at = now
                self._weather_refreshing = False

    def _resolve_weather_location(self, location: str) -> tuple[float, float, str]:
        key = self._normalize_location_key(location)
        direct = COMMON_CITY_COORDS.get(key)
        if direct is not None:
            return direct
        query = urlencode({"name": location, "count": 1, "language": "zh", "format": "json"})
        payload = self._read_json_url(f"{OPEN_METEO_GEOCODE_URL}?{query}")
        results = payload.get("results") or []
        if not results or not isinstance(results[0], dict):
            return 0.0, 0.0, ""
        item = results[0]
        lat = float(item.get("latitude") or 0.0)
        lon = float(item.get("longitude") or 0.0)
        name = str(item.get("name") or location)
        admin = str(item.get("admin1") or "")
        country = str(item.get("country") or "")
        display = " · ".join(part for part in (name, admin or country) if part)
        return lat, lon, display or location

    def _fetch_open_meteo_weather(self, latitude: float, longitude: float) -> dict[str, object]:
        current_fields = "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,wind_direction_10m"
        query = urlencode(
            {
                "latitude": f"{float(latitude):.4f}",
                "longitude": f"{float(longitude):.4f}",
                "current": current_fields,
                "timezone": "auto",
                "forecast_days": 1,
            }
        )
        payload = self._read_json_url(f"{OPEN_METEO_FORECAST_URL}?{query}")
        current = payload.get("current") or {}
        if not isinstance(current, dict) or not current:
            raise ValueError("missing open-meteo current weather")
        code = self._int_value(current.get("weather_code"), -1)
        return {
            "weather": WEATHER_CODE_TEXT.get(code, "天气"),
            "temperature": self._compact_number(current.get("temperature_2m")),
            "winddirection": self._wind_direction_name(self._float_value(current.get("wind_direction_10m"), 0.0)),
            "windspeed": self._compact_number(current.get("wind_speed_10m")),
            "humidity": self._compact_number(current.get("relative_humidity_2m")),
            "reporttime": str(current.get("time") or ""),
        }

    def _normalize_location_key(self, value: str) -> str:
        text = "".join(str(value or "").strip().split())
        for suffix in ("市", "城区", "区", "县"):
            if len(text) > 2 and text.endswith(suffix):
                text = text[: -len(suffix)]
                break
        return text

    def _wind_direction_name(self, degrees: float) -> str:
        names = ("北风", "东北风", "东风", "东南风", "南风", "西南风", "西风", "西北风")
        index = int((float(degrees) % 360.0 + 22.5) // 45.0) % len(names)
        return names[index]

    def _compact_number(self, value: object) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ""
        if number.is_integer():
            return str(int(number))
        return f"{number:.1f}".rstrip("0").rstrip(".")

    def _float_value(self, value: object, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def _int_value(self, value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def _geocode(self, location: str, api_key: str) -> tuple[str, str]:
        query = urlencode({"key": api_key, "address": location, "city": location})
        payload = self._read_json_url(f"{AMAP_GEOCODE_URL}?{query}")
        if str(payload.get("status")) != "1":
            raise ValueError(str(payload.get("info") or "amap geocode failed"))
        results = payload.get("geocodes") or []
        if not results:
            return "", ""
        item = results[0]
        adcode = str(item.get("adcode") or "").strip()
        city = item.get("city") or item.get("district") or location
        if isinstance(city, list):
            city = location
        return adcode, str(city or location)

    def _fetch_weather(self, adcode: str, api_key: str) -> dict[str, object]:
        extensions = str(self._setting("v2_weather_extensions", "base") or "base").strip().lower()
        if extensions not in {"base", "all"}:
            extensions = "base"
        query = urlencode({"key": api_key, "city": adcode, "extensions": extensions, "output": "json"})
        payload = self._read_json_url(f"{AMAP_WEATHER_URL}?{query}")
        if str(payload.get("status")) != "1":
            raise ValueError(str(payload.get("info") or "amap weather failed"))
        lives = payload.get("lives") or []
        if lives:
            current = lives[0]
            if not isinstance(current, dict):
                raise ValueError("invalid amap weather live data")
            return current
        forecasts = payload.get("forecasts") or []
        if forecasts and isinstance(forecasts[0], dict):
            forecast = forecasts[0]
            casts = forecast.get("casts") or []
            first_cast = casts[0] if casts and isinstance(casts[0], dict) else {}
            return {
                "city": forecast.get("city") or "",
                "weather": first_cast.get("dayweather") or first_cast.get("nightweather") or "预报",
                "temperature": self._forecast_temperature(first_cast),
                "winddirection": first_cast.get("daywind") or first_cast.get("nightwind") or "",
                "windpower": first_cast.get("daypower") or first_cast.get("nightpower") or "",
                "humidity": "",
                "reporttime": forecast.get("reporttime") or first_cast.get("date") or "",
            }
        raise ValueError("missing amap weather data")

    def _forecast_temperature(self, cast: dict[str, object]) -> str:
        day = str(cast.get("daytemp") or "").strip()
        night = str(cast.get("nighttemp") or "").strip()
        if day and night and day != night:
            return f"{night}-{day}"
        return day or night or "--"

    def _read_json_url(self, url: str) -> dict[str, object]:
        with urlopen(url, timeout=3.0) as response:
            return json.loads(response.read().decode("utf-8"))

    def _schedule_plugin(self) -> PluginData:
        events = self._load_events()
        now = datetime.now().astimezone()
        events.sort(key=lambda item: item[0])
        due = [event for event in events if -300 <= (event[0] - now).total_seconds() <= 0]
        upcoming = [event for event in events if event[0] > now]
        status = "normal"
        alert_title = ""
        alert_body = ""
        alert_priority = 50
        if due:
            _when, title = due[0]
            detail = "现在"
            summary = title[:24]
            status = "due"
            alert_title = "日程到点"
            alert_body = title[:36]
            alert_priority = 0
        elif upcoming:
            when, title = upcoming[0]
            delta = when - now
            if delta.days > 0:
                detail = f"{delta.days} 天后"
            else:
                minutes = max(0, math.ceil(delta.total_seconds() / 60))
                detail = f"{minutes} 分钟后"
                if minutes <= 10:
                    status = "soon"
                    alert_title = "日程将近"
                    alert_body = f"{detail} · {title[:28]}"
                    alert_priority = 10
            summary = title[:24]
        else:
            detail = "暂无日程"
            summary = "支持 JSON / ICS 导入"
        return PluginData(
            label="AGENDA",
            detail=detail,
            summary=summary,
            color=ColorToken(118, 236, 160),
            icon="□",
            plugin_id="agenda",
            status=status,
            primary_value=detail,
            secondary_value=summary,
            alert_title=alert_title,
            alert_body=alert_body,
            alert_priority=alert_priority,
        )

    def _load_events(self) -> list[tuple[datetime, str]]:
        events: list[tuple[datetime, str]] = []
        DATA_DIR.mkdir(exist_ok=True)
        raw_json_path = str(self._setting("v2_schedule_json_path", "") or "").strip()
        json_path = Path(raw_json_path).expanduser() if raw_json_path else DATA_DIR / "schedule.json"
        events.extend(self._read_json_events(json_path))
        countdown_path = DATA_DIR / "countdowns.json"
        events.extend(self._read_json_events(countdown_path))
        raw_ics_path = str(self._setting("v2_schedule_ics_path", "") or "").strip()
        ics_path = Path(raw_ics_path).expanduser() if raw_ics_path else None
        if ics_path is not None:
            events.extend(self._read_ics_events(ics_path))
        default_ics = DATA_DIR / "schedule.ics"
        if default_ics.exists():
            events.extend(self._read_ics_events(default_ics))
        return events

    def _read_json_events(self, path: Path) -> list[tuple[datetime, str]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return []
        if isinstance(payload, dict):
            payload = payload.get("events") or payload.get("countdowns") or []
        events = []
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "日程").strip()
            raw_time = str(item.get("time") or item.get("date") or item.get("at") or "").strip()
            when = self._parse_datetime(raw_time)
            if when is not None:
                events.append((when, title))
        return events

    def _read_ics_events(self, path: Path) -> list[tuple[datetime, str]]:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except Exception:
            return []
        events = []
        for block in text.split("BEGIN:VEVENT")[1:]:
            title = self._ics_value(block, "SUMMARY") or "日程"
            raw_time = self._ics_value(block, "DTSTART")
            when = self._parse_datetime(raw_time)
            if when is not None:
                events.append((when, title))
        return events

    def _ics_value(self, block: str, key: str) -> str:
        for line in block.splitlines():
            if line.startswith(key):
                return line.split(":", 1)[-1].strip()
        return ""

    def _parse_datetime(self, value: str) -> datetime | None:
        if not value:
            return None
        value = value.strip()
        formats = ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d")
        for fmt in formats:
            try:
                dt = datetime.strptime(value, fmt)
                if fmt.endswith("Z"):
                    return dt.replace(tzinfo=timezone.utc).astimezone()
                return dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
            except ValueError:
                continue
        try:
            dt = datetime.fromisoformat(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        except ValueError:
            return None
