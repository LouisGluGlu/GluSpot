# Plugin Island iOS Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完善插件岛的提醒副岛、管理界面和全局深色模式策略，让设置与插件 UI 统一成接近 macOS 偏好设置中心的苹果风格。

**Architecture:** 保持现有 V2 岛与 provider 分层，不恢复旧 Pill UI。插件数据仍由 `api/plugin_provider.py` 输出 `PluginData`，UI 只消费状态、主值、副值与提醒文案；主题策略集中在 `local/ui_theme.py`，设置窗口和插件窗口通过同一个 `ios_theme()` 获得浅色/深色样式。

**Tech Stack:** Python 3、PySide6、unittest、Windows provider 层、Open-Meteo 免 Key 天气查询、本地 JSON/ICS 日程。

---

## File Structure

- Modify: `api/models.py`
  - 为插件数据增加可选提醒文案字段，兼容旧的 7 元组消费路径。
- Modify: `api/plugin_provider.py`
  - 输出更完整的 timer/weather/agenda 状态和提醒文案。
  - 保持天气默认使用 Open-Meteo 免 Key，不把真实/假数据泄漏到 UI 层。
- Modify: `local/ui_theme.py`
  - 增加 `system/light/dark/schedule` 四种主题模式和跨午夜时间段判断。
  - 保留 Windows 系统主题作为默认模式。
- Modify: `local/settings_store.py`
  - 增加主题模式、深色开始时间、深色结束时间的默认值。
- Modify: `local/app.py`
  - 启动时把 settings getter 注入主题模块。
- Modify: `local/settings_window.py`
  - 在外观页加入深色模式策略控件。
  - 调整整体样式为 mac 偏好设置中心：侧栏、内容区、卡片、输入控件、下拉框统一浅/深色。
- Modify: `local/plugin_manager_window.py`
  - 将时间/天气/日程管理窗口统一为 mac 偏好设置中心风格。
  - 完善天气刷新、日程新增/清空、倒计时开始/清除的状态展示。
- Modify: `local/island_stack_visual.py`
  - 扩展副岛提醒状态，天气 loading/error 也可触发右侧副岛。
  - 调整插件岛绘制为纯色、现代 iOS 控制中心风格，保留现有点击展开/聚焦行为。
- Test: `tests/test_ui_theme.py`
  - 覆盖强制深色、强制浅色、自定时间深色、跨午夜区间、系统模式回退。
- Test: `tests/test_plugin_provider.py`
  - 覆盖 timer/weather/agenda 的提醒文案和状态。
- Test: `tests/test_island_stack_visual_messages.py`
  - 覆盖 loading/error 副岛排序与提醒文案。

---

### Task 1: Theme Policy

**Files:**
- Modify: `local/ui_theme.py`
- Modify: `local/settings_store.py`
- Test: `tests/test_ui_theme.py`

- [ ] **Step 1: Write failing tests for custom theme modes**

Add tests shaped like:

```python
def test_app_dark_mode_can_be_forced_dark(self):
    settings = lambda key, default=None: {"v2_theme_mode": "dark"}.get(key, default)
    self.assertTrue(ui_theme.app_dark_mode(settings_getter=settings))

def test_app_dark_mode_can_be_forced_light(self):
    settings = lambda key, default=None: {"v2_theme_mode": "light"}.get(key, default)
    self.assertFalse(ui_theme.app_dark_mode(settings_getter=settings))

def test_scheduled_dark_mode_handles_cross_midnight_window(self):
    values = {
        "v2_theme_mode": "schedule",
        "v2_theme_dark_start": "22:00",
        "v2_theme_dark_end": "07:30",
    }
    settings = lambda key, default=None: values.get(key, default)
    self.assertTrue(ui_theme.app_dark_mode(settings_getter=settings, now=time(23, 15)))
    self.assertTrue(ui_theme.app_dark_mode(settings_getter=settings, now=time(6, 20)))
    self.assertFalse(ui_theme.app_dark_mode(settings_getter=settings, now=time(12, 0)))
```

- [ ] **Step 2: Run theme tests and verify red**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_ui_theme`

Expected: FAIL because `app_dark_mode()` does not yet accept `settings_getter` or `now`.

- [ ] **Step 3: Implement theme policy**

Add:

```python
_theme_settings_getter = None

def set_theme_settings_getter(getter):
    global _theme_settings_getter
    _theme_settings_getter = getter

def _theme_setting(key: str, default=None, settings_getter=None):
    getter = settings_getter or _theme_settings_getter
    if getter is None:
        return default
    try:
        return getter(key, default)
    except Exception:
        return default
```

Then update `app_dark_mode(settings_getter=None, now=None)` so:

- `v2_theme_mode == "dark"` returns `True`
- `v2_theme_mode == "light"` returns `False`
- `v2_theme_mode == "schedule"` returns the configured time-window result
- otherwise it keeps the existing Windows/palette behavior

Add defaults:

```python
"v2_theme_mode": "system",
"v2_theme_dark_start": "22:00",
"v2_theme_dark_end": "07:00",
```

- [ ] **Step 4: Run theme tests and verify green**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_ui_theme`

Expected: OK.

---

### Task 2: Plugin Alert Data

**Files:**
- Modify: `api/models.py`
- Modify: `api/plugin_provider.py`
- Test: `tests/test_plugin_provider.py`

- [ ] **Step 1: Write failing tests for plugin alert fields**

Add tests shaped like:

```python
def test_running_timer_exports_alert_copy(self):
    provider = RealPluginProvider(lambda key, default=None: "2099-01-01T00:00:00" if key == "v2_timer_end_at" else default)
    plugin = provider._time_plugin()
    self.assertEqual(plugin.alert_title, "计时进行中")
    self.assertIn("倒计时", plugin.alert_body)

def test_weather_loading_exports_alert_copy(self):
    provider = RealPluginProvider(lambda key, default=None: {"v2_weather_location": "北京"}.get(key, default))
    with provider._weather_lock:
        provider._weather_refreshing = True
        provider._weather_detail = "北京"
        provider._weather_summary = "天气刷新中"
    plugin = provider._weather_plugin()
    self.assertEqual(plugin.status, "loading")
    self.assertEqual(plugin.alert_title, "天气刷新中")

def test_schedule_soon_exports_alert_copy(self):
    now = datetime.now().astimezone()
    soon = (now + timedelta(minutes=4)).isoformat(timespec="minutes")
    provider = RealPluginProvider()
    provider._load_events = lambda: [(provider._parse_datetime(soon), "准备开会")]
    plugin = provider._schedule_plugin()
    self.assertEqual(plugin.status, "soon")
    self.assertEqual(plugin.alert_title, "日程将近")
    self.assertIn("准备开会", plugin.alert_body)
```

- [ ] **Step 2: Run plugin provider tests and verify red**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_plugin_provider`

Expected: FAIL because `PluginData` has no `alert_title` or `alert_body`.

- [ ] **Step 3: Implement minimal alert fields**

Add optional fields to `PluginData`:

```python
alert_title: str = ""
alert_body: str = ""
alert_priority: int = 50
```

Set values in provider:

- Timer running: title `计时进行中`, body uses countdown summary, priority 30.
- Timer done: title `计时结束`, body `点击查看或清除计时器`, priority 5.
- Weather loading: title `天气刷新中`, body uses city/detail, priority 40.
- Weather error: title `天气不可用`, body uses error or cached summary, priority 20.
- Agenda due: title `日程到点`, body uses event title, priority 0.
- Agenda soon: title `日程将近`, body uses time + title, priority 10.

- [ ] **Step 4: Run plugin provider tests and verify green**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_plugin_provider`

Expected: OK.

---

### Task 3: Plugin Alert Island

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests for alert consumption**

Add tests shaped like:

```python
def test_weather_loading_creates_plugin_alert(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.persistent_plugin = True
    visual.expanded = False
    visual.enabled_plugin_count = 3
    visual.plugin_samples = [
        ("TIME", "20:00", "05/27", QColor(67, 162, 255), "●", "time", "normal", "", "", 50),
        ("WEATHER", "北京", "天气刷新中", QColor(80, 210, 255), "☁", "weather", "loading", "天气刷新中", "北京", 40),
        ("AGENDA", "暂无日程", "JSON / ICS", QColor(118, 236, 160), "■", "agenda", "normal", "", "", 50),
    ]
    alerts = visual._active_plugin_alerts(1)
    self.assertEqual([alert[6] for alert in alerts], ["weather"])

def test_plugin_alert_text_prefers_provider_alert_copy(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    title, body = visual._plugin_alert_text_parts("WEATHER", "北京", "天气刷新中", "loading", "天气刷新中", "北京")
    self.assertEqual(title, "WEATHER · 天气刷新中")
    self.assertEqual(body, "北京")
```

- [ ] **Step 2: Run island tests and verify red**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_island_stack_visual_messages`

Expected: FAIL because tuple parsing and alert text do not consume provider alert fields.

- [ ] **Step 3: Implement tuple compatibility and alert status expansion**

Update `_plugin_tuple()` to return:

```python
(label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority)
```

For existing 7-tuples, fill `alert_title=""`, `alert_body=""`, `alert_priority=50`.

Update plugin sample construction from `visual_data.plugins` to include new fields. Update alert status set to include `loading` and `error`, and sort first by provider `alert_priority`, then by existing status priority, then by plugin order.

Update `_plugin_alert_text_parts()` to accept optional provider title/body and prefer them.

- [ ] **Step 4: Run island tests and verify green**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_island_stack_visual_messages`

Expected: OK.

---

### Task 4: Settings and Plugin UI

**Files:**
- Modify: `local/app.py`
- Modify: `local/settings_window.py`
- Modify: `local/plugin_manager_window.py`

- [ ] **Step 1: Wire theme getter**

In `local/app.py`, import and call:

```python
from local.ui_theme import set_theme_settings_getter

self.settings = SettingsManager()
set_theme_settings_getter(self.settings.get)
```

- [ ] **Step 2: Add appearance controls for custom dark mode**

In `SettingsWindow`:

- Add `THEME_MODES = (("跟随系统", "system"), ("浅色", "light"), ("深色", "dark"), ("自定时间", "schedule"))`.
- Add combo row `界面模式`.
- Add line edits `深色开始` and `深色结束`, default `22:00` and `07:00`.
- Add setters `_set_theme_mode`, `_set_theme_dark_start`, `_set_theme_dark_end`.
- After saving theme settings, call `_apply_theme()`, refresh combo popups, update child switches, and `island_widget.update()`.

- [ ] **Step 3: Restyle settings window**

Keep the existing layout but tune QSS toward mac preferences:

- Sidebar: `panel_alt`, 190 px, selected row blue with 8-10 px radius.
- Content: `window` background, 28-32 px margin.
- Cards: `card`, border 8-10 px radius, subtle border, no nested card.
- Inputs and combo boxes: black/white aware via `ios_theme()`.
- Switches: keep `IosSwitch` with left label/right toggle.

- [ ] **Step 4: Restyle plugin manager**

Keep current plugin-specific pages, but tune QSS and dimensions:

- 560 x 560 minimum.
- Header title + muted subtitle.
- Cards with 8-10 px radius, not gradient.
- Buttons pure system colors: primary blue, danger red, neutral gray.
- Weather page clearly says no API key required and shows Open-Meteo diagnostics.
- Agenda page keeps JSON/ICS path plus local add/clear controls.

- [ ] **Step 5: Static verification**

Run:

```powershell
.\\.venv\\Scripts\\python.exe -m py_compile local/app.py local/settings_window.py local/plugin_manager_window.py local/ui_theme.py api/plugin_provider.py api/models.py
```

Expected: exit 0.

---

### Task 5: Full Verification and Cleanup

**Files:**
- Verify all touched files
- Remove generated runtime cache if present: `lyrics/christian kuria - deep green.lrc`

- [ ] **Step 1: Run focused tests**

Run:

```powershell
.\\.venv\\Scripts\\python.exe -m unittest tests.test_plugin_provider tests.test_ui_theme tests.test_island_stack_visual_messages
```

Expected: OK.

- [ ] **Step 2: Run full test suite**

Run:

```powershell
.\\.venv\\Scripts\\python.exe -m unittest discover -s tests
```

Expected: OK.

- [ ] **Step 3: Inspect diff**

Run:

```powershell
git status --short
git diff -- api/models.py api/plugin_provider.py local/ui_theme.py local/settings_store.py local/app.py local/settings_window.py local/plugin_manager_window.py local/island_stack_visual.py tests/test_ui_theme.py tests/test_plugin_provider.py tests/test_island_stack_visual_messages.py docs/superpowers/plans/2026-05-27-plugin-island-ios-settings.md
```

Expected: no unrelated lyric/provider regressions introduced by this plan.

---

## Self-Review

- Spec coverage: 插件副岛、插件管理界面、设置 UI、深色自定时间均有任务覆盖；歌词/网易云进度不是本分支当前目标，不在本计划内改动。
- Placeholder scan: 本计划没有 TBD/implement later；每个任务有目标文件、测试、实现要点和验证命令。
- Type consistency: `PluginData.alert_title/alert_body/alert_priority` 在 provider、visual tuple 和测试中一致。
