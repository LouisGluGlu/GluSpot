# Plugin Function and Settings Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按优先级 6 -> 1 -> 2 -> 3 -> 4 -> 5，把插件设置合并到主设置窗口，插件岛点击改为岛内功能展开，并改善插件岛、窗口控制、滚轮、天气与日程路线。

**Architecture:** 保持 `api/plugin_provider.py` 作为插件真实数据出口，UI 只消费 `PluginData`。主设置窗口承接插件设置；插件岛在 `local/island_stack_visual.py` 内绘制紧凑态与展开态，不再把插件点击导向独立设置窗口。mac 窗口控制抽到 `local/mac_style.py`，无滚轮控件抽到小型 widget helper。

**Tech Stack:** Python 3, PySide6, unittest, Open-Meteo, Windows location/privacy best-effort detection.

---

### Task 1: Shared UI Controls

**Files:**
- Modify: `local/mac_style.py`
- Create: `local/no_wheel_widgets.py`
- Test: `tests/test_mac_style.py`
- Test: `tests/test_no_wheel_widgets.py`

- [ ] **Step 1: Write failing tests**

Test that `mac_traffic_lights(window)` creates clickable buttons and that no-wheel combo/spin controls ignore wheel events:

```python
class FakeWheel:
    def __init__(self):
        self.ignored = False
    def ignore(self):
        self.ignored = True

event = FakeWheel()
NoWheelComboBox().wheelEvent(event)
self.assertTrue(event.ignored)
```

- [ ] **Step 2: Verify red**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_mac_style tests.test_no_wheel_widgets`

Expected: FAIL because `NoWheelComboBox` and clickable traffic-light helpers do not exist yet.

- [ ] **Step 3: Implement controls**

Add `MacTrafficButton`, update `mac_traffic_lights(window)` so:

```python
red.clicked.connect(window.close)
yellow.clicked.connect(window.showMinimized)
green.clicked.connect(lambda: window.showNormal() if window.isMaximized() else window.showMaximized())
```

Create `local/no_wheel_widgets.py` with `NoWheelComboBox` and `NoWheelSpinBox` overriding `wheelEvent()` to call `event.ignore()`.

- [ ] **Step 4: Verify green**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_mac_style tests.test_no_wheel_widgets`

Expected: OK.

---

### Task 2: Frameless Mac Settings Shell

**Files:**
- Modify: `local/settings_window.py`
- Modify: `local/plugin_manager_window.py`
- Test: `tests/test_mac_preferences_shell.py`

- [ ] **Step 1: Write failing tests**

Assert both windows use `Qt.WindowType.FramelessWindowHint`, pass `self` into `mac_traffic_lights(self)`, and no longer use native titlebar flags.

- [ ] **Step 2: Verify red**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_mac_preferences_shell`

Expected: FAIL because windows still use native titlebar hints and decorative traffic dots.

- [ ] **Step 3: Implement frameless shell**

Set the frameless flag on both dialogs, replace `mac_traffic_lights()` with `mac_traffic_lights(self)`, and add drag behavior for non-control areas of the custom title/sidebar shell.

- [ ] **Step 4: Verify green**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_mac_preferences_shell`

Expected: OK.

---

### Task 3: Merge Plugin Settings Into Settings Window

**Files:**
- Modify: `local/settings_window.py`
- Modify: `local/settings_store.py`
- Test: `tests/test_mac_preferences_shell.py`

- [ ] **Step 1: Write failing tests**

Assert settings source contains a `_plugins_page()` and nav title `插件`, and plugin manager is no longer opened from plugin click flow.

- [ ] **Step 2: Verify red**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_mac_preferences_shell`

Expected: FAIL because plugin settings page does not exist.

- [ ] **Step 3: Implement plugin settings page**

Add a `插件` page with plugin count, show seconds, timezone/custom timezone, timer quick actions, weather location controls, agenda JSON/ICS path, and schedule/todo roadmap rows. Use no-wheel combo/spin controls.

- [ ] **Step 4: Verify green**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_mac_preferences_shell`

Expected: OK.

---

### Task 4: Plugin Island Functional Expansion

**Files:**
- Modify: `api/models.py`
- Modify: `api/plugin_provider.py`
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_plugin_provider.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Assert running timer exports a progress fraction, plugin compact text is value-only, plugin click does not open `PluginManagerWindow`, and expanded plugin main surface is taller than compact.

- [ ] **Step 2: Verify red**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_plugin_provider tests.test_island_stack_visual_messages`

Expected: FAIL because progress and new expansion semantics do not exist.

- [ ] **Step 3: Implement provider and drawing**

Add `progress: float = 0.0` to `PluginData`; set timer progress from `v2_timer_started_at` and `v2_timer_end_at`. Extend plugin tuples compatibly. Compact plugin cells draw an icon square with an animated ring and one value text. Expanded plugin main surface becomes a functional panel with a large circular timer face or focused plugin card.

- [ ] **Step 4: Verify green**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_plugin_provider tests.test_island_stack_visual_messages`

Expected: OK.

---

### Task 5: Weather and Agenda Route

**Files:**
- Modify: `api/plugin_provider.py`
- Modify: `local/settings_store.py`
- Modify: `local/settings_window.py`
- Test: `tests/test_plugin_provider.py`

- [ ] **Step 1: Write failing tests**

Assert weather prefers detected/user location when enabled and falls back to manual city; diagnostics expose `weather_location_source`. Assert agenda diagnostics include class schedule roadmap flags.

- [ ] **Step 2: Verify red**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_plugin_provider`

Expected: FAIL because source routing and agenda roadmap diagnostics do not exist.

- [ ] **Step 3: Implement route**

Add best-effort location detection hooks and settings keys:

```python
"v2_weather_use_location": True
"v2_weather_manual_location": "北京"
```

Use public Open-Meteo geocode/forecast. If location cannot be read, fall back to manual city. Add agenda roadmap diagnostics for class schedule import and class reminders.

- [ ] **Step 4: Verify green**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_plugin_provider`

Expected: OK.

---

### Task 6: Full Verification

**Files:**
- All touched files

- [ ] **Step 1: Compile**

Run: `.\\.venv\\Scripts\\python.exe -m py_compile local/settings_window.py local/plugin_manager_window.py local/mac_style.py local/no_wheel_widgets.py local/island_stack_visual.py api/plugin_provider.py api/models.py`

- [ ] **Step 2: UI smoke test**

Run an offscreen QApplication that instantiates settings and plugin manager windows.

- [ ] **Step 3: Focused tests**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_mac_style tests.test_no_wheel_widgets tests.test_mac_preferences_shell tests.test_plugin_provider tests.test_island_stack_visual_messages`

- [ ] **Step 4: Full tests**

Run: `.\\.venv\\Scripts\\python.exe -m unittest discover -s tests`

- [ ] **Step 5: Cleanup**

Remove project `__pycache__` folders and any untracked runtime lyric/cache files created during verification.

---

## Self-Review

- Spec coverage: tasks cover priority 6, 1, 2, 3, 4, 5 in requested order.
- Placeholder scan: no TBD placeholders; future agenda items are implemented as explicit roadmap diagnostics and visible setting rows.
- Type consistency: `PluginData.progress` flows through provider -> visual tuple -> painter.
