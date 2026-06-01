# 插件岛展开界面完善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把插件岛展开态补成可读、可操作、可提醒融入的下挂功能面板，而不是把 48px 插件条本体拉高成占位信息展示。

**Architecture:** 保持插件数据仍由 `api/plugin_provider.py` 输出。岛上展开态由两层组成：上层固定 48px 插件条，下层独立 `plugin-panel` 承载运行状态、快捷动作和提醒融合视图。设置项继续合并在 `local/settings_window.py`，独立插件管理窗口只作为更完整的管理入口，不重新引入旧 Pill UI。

**Tech Stack:** Python, PySide6, unittest, 现有 V2 island 绘制系统。

---

### Task 1: 展开态功能模型

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests that assert the expanded plugin panel exposes a structured action model:

```python
def test_plugin_action_model_time_weather_agenda(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    time_action = visual._plugin_action_model("TIME", "00:24", "倒计时 0:24", "running")
    weather_action = visual._plugin_action_model("WEATHER", "26°C 晴", "北京 · 东南风", "normal")
    agenda_action = visual._plugin_action_model("AGENDA", "4 分钟后", "准备上课", "soon")

    self.assertEqual(time_action["command"], "timer-clear")
    self.assertIn("清除", time_action["label"])
    self.assertEqual(weather_action["command"], "weather-refresh")
    self.assertIn("刷新", weather_action["label"])
    self.assertEqual(agenda_action["command"], "agenda-open")
    self.assertIn("日程", agenda_action["label"])
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_plugin_action_model_time_weather_agenda
```

Expected: fails because `_plugin_action_model` does not exist.

- [ ] **Step 3: Implement minimal action model**

Add `_plugin_action_model()` to `IslandStackVisual` with commands for time, weather, and agenda.

- [ ] **Step 4: Run focused test**

Run the same focused unittest and confirm it passes.

### Task 2: 展开面板布局数据

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests that assert expanded panel sub-rects are stable and do not overlap:

```python
def test_plugin_panel_layout_has_nav_body_action_and_reminder_rail(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    rect = QRectF(0.0, 0.0, 420.0, 188.0)

    layout = visual._plugin_panel_layout(rect)

    self.assertTrue(layout["nav"].height() >= 30.0)
    self.assertGreater(layout["body"].top(), layout["nav"].bottom())
    self.assertGreater(layout["action"].top(), layout["body"].top())
    self.assertGreater(layout["reminders"].top(), layout["nav"].bottom())
    self.assertLessEqual(layout["action"].right(), rect.right())
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_plugin_panel_layout_has_nav_body_action_and_reminder_rail
```

Expected: fails because `_plugin_panel_layout` does not exist.

- [ ] **Step 3: Implement layout helper**

Add `_plugin_panel_layout()` returning `nav`, `body`, `icon`, `text`, `action`, and `reminders` rectangles. Use this helper from `_paint_plugin_function_panel()`.

- [ ] **Step 4: Run focused test**

Run the focused unittest and confirm it passes.

### Task 3: 副岛提醒融入展开面板

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests that assert panel reminder chips are derived from active plugin alerts:

```python
def test_plugin_panel_reminder_models_include_active_alerts(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.persistent_plugin = True
    visual.expanded = True
    visual.enabled_plugin_count = 3
    visual.plugin_samples = [
        ("TIME", "00:24", "倒计时 0:24", QColor(67, 162, 255), "●", "time", "running", "计时进行中", "倒计时 0:24", 30, 0.4),
        ("WEATHER", "雷雨", "北京", QColor(80, 210, 255), "☁", "weather", "severe", "严重天气提醒", "北京 · 雷雨", 2, 1.0),
        ("AGENDA", "4 分钟后", "准备上课", QColor(118, 236, 160), "■", "agenda", "soon", "日程将近", "4 分钟后 · 准备上课", 10, 0.6),
    ]

    reminders = visual._plugin_panel_reminder_models(limit=3)

    self.assertEqual([item["plugin_id"] for item in reminders], ["weather", "agenda", "time"])
    self.assertIn("雷雨", reminders[0]["body"])
```

- [ ] **Step 2: Run failing test**

Run the focused unittest. Expected: fails because `_plugin_panel_reminder_models` does not exist.

- [ ] **Step 3: Implement reminder models and painter**

Add `_plugin_panel_reminder_models()` and `_paint_plugin_panel_reminders()`. Render active reminders as small horizontal chips inside the expanded panel, using the same ring/progress language as side islands.

- [ ] **Step 4: Run focused test**

Run the focused unittest and confirm it passes.

### Task 4: 展开态操作点击

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests that assert clicking the action zone executes the correct plugin command:

```python
def test_plugin_action_click_refreshes_weather(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    calls = []
    visual.settings_setter = lambda key, value: calls.append((key, value))
    visual.refresh_plugin_data = lambda: calls.append(("refresh", "plugins"))

    visual._activate_plugin_action("weather-refresh")

    self.assertEqual(calls[-1], ("refresh", "plugins"))
    self.assertTrue(any(key == "v2_weather_refresh_nonce" for key, _value in calls if key != "refresh"))
```

- [ ] **Step 2: Run failing test**

Run the focused unittest. Expected: fails because `_activate_plugin_action` does not exist.

- [ ] **Step 3: Implement command dispatch**

Add `_activate_plugin_action()` for:
- `timer-clear`: clear `v2_timer_started_at` and `v2_timer_end_at`
- `weather-refresh`: update `v2_weather_refresh_nonce`
- `agenda-open`: open the plugin manager page or mark the plugin manager unavailable gracefully

Add hit testing for the action rectangle through a `plugin-action:<command>` key.

- [ ] **Step 4: Run focused test**

Run focused unittest and confirm it passes.

### Task 5: 插件管理窗口补齐功能状态

**Files:**
- Modify: `local/plugin_manager_window.py`
- Test: `tests/test_mac_preferences_shell.py`

- [ ] **Step 1: Write failing tests**

Add source-level tests that confirm plugin manager includes location toggle, reminder threshold, and timer start fields.

- [ ] **Step 2: Run failing tests**

Run `.\.venv\Scripts\python.exe -m unittest tests.test_mac_preferences_shell`.

- [ ] **Step 3: Implement missing management controls**

Add the same weather location toggle and agenda reminder threshold controls into `PluginManagerWindow`, reusing existing settings keys. Keep style in the mac preferences shell.

- [ ] **Step 4: Run tests**

Run mac preferences tests again.

### Task 6: Verification

**Files:**
- All touched files.

- [ ] **Step 1: Run focused island tests**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages
```

- [ ] **Step 2: Run plugin provider tests**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_plugin_provider tests.test_mac_preferences_shell
```

- [ ] **Step 3: Run all tests**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

- [ ] **Step 4: Compile touched modules**

```powershell
.\.venv\Scripts\python.exe -m py_compile local\island_stack_visual.py local\plugin_manager_window.py local\settings_window.py api\plugin_provider.py
```

- [ ] **Step 5: Diff hygiene**

```powershell
git diff --check
```
