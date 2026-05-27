# Plugin Island Functional Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the plugin island so compact/secondary rows stay balanced by default, focused items expand only during interaction, alert sub-islands become animation-led, and settings vs runtime function surfaces have a clear product split.

**Architecture:** `api/plugin_provider.py` remains the data owner. `local/settings_window.py` owns persistent plugin configuration. `local/island_stack_visual.py` owns runtime display: compact strip, focused strip, alert sub-islands, and the expanded function panel.

**Tech Stack:** Python 3, PySide6 painting, unittest, Superpowers TDD workflow.

---

## Settings vs Function Surface Allocation

| Plugin | Settings window owns | Expanded island owns |
| --- | --- | --- |
| TIME | seconds toggle, timezone, custom timezone, quick timer duration, custom timer duration, clear timer | live clock, countdown ring, current timer state, remaining time, completion prompt |
| WEATHER | location permission preference, manual fallback city, refresh weather request | current weather, location summary, loading/error state, no API-key public weather status |
| AGENDA | JSON path, ICS path, future class schedule import route, future class reminder route | next event, due/soon reminder, empty state, class schedule roadmap preview |

The island must not expose persistent configuration fields. The settings window must not duplicate the animated runtime cards.

---

### Task 1: Plugin Strip Layout

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests proving that an expanded secondary strip defaults to equal widths and only grows a focused item when focus is active:

```python
def test_expanded_plugin_strip_defaults_to_equal_widths_without_hover(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.expanded = True
    visual.enabled_plugin_count = 3
    visual.plugin_focus_from = 0
    visual.plugin_focus_target = 0
    visual.plugin_focus_changed_at = 0.0

    cells = visual._plugin_item_rects(QRectF(0.0, 0.0, 420.0, 48.0), 1.0, expand_active=False)

    widths = [round(cell.width(), 2) for _index, cell in cells]
    self.assertEqual(widths, [140.0, 140.0, 140.0])

def test_expanded_plugin_strip_keeps_unfocused_cells_readable(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.expanded = True
    visual.enabled_plugin_count = 3
    visual.plugin_focus_from = 0
    visual.plugin_focus_target = 1
    visual.plugin_focus_changed_at = -10.0

    cells = visual._plugin_item_rects(QRectF(0.0, 0.0, 420.0, 48.0), 1.0, expand_active=True)

    widths = [cell.width() for _index, cell in cells]
    self.assertGreater(widths[1], widths[0])
    self.assertGreaterEqual(widths[0], 90.0 * IslandStackVisual.VISUAL_SCALE)
    self.assertGreaterEqual(widths[2], 90.0 * IslandStackVisual.VISUAL_SCALE)
```

- [ ] **Step 2: Verify red**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages
```

Expected: FAIL because `_plugin_item_rects()` does not accept `expand_active` and expanded plugin rows always collapse non-focused cells into icon slots.

- [ ] **Step 3: Implement layout switch**

Update `_plugin_widths(count, total_width, now, expand_active=True)` so:

```python
if not self.expanded or count == 1 or not expand_active:
    return [total_width / count] * count
compact_slot = max(self._u(90.0), min(self._u(126.0), total_width * 0.28))
focus_width = max(total_width - compact_slot * (count - 1), total_width / count)
```

Update `_plugin_item_rects(rect, now, expand_active=True)` to pass the flag into `_plugin_widths()`. In `_paint_plugin()`, set `strip_expand_active = rect.height() > self._u(88.0) or self.hovered_key == "plugin"` and use equal widths for the secondary row when not hovered.

- [ ] **Step 4: Verify green**

Run the same unittest command. Expected: OK.

---

### Task 2: Alert Sub-Island Animation Language

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests for alert value-only text:

```python
def test_plugin_alert_display_value_is_value_only(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    value = visual._plugin_alert_display_value("TIME", "12:05:49", "倒计时 0:24", "running", "计时进行中", "倒计时 0:24")

    self.assertEqual(value, "0:24")
    self.assertNotIn("倒计时", value)

def test_plugin_alert_width_is_small_for_value_only_alerts(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    width = visual._plugin_alert_width_for("TIME", "12:05:49", "0:24")

    self.assertLessEqual(width, 174.0 * IslandStackVisual.VISUAL_SCALE)
```

- [ ] **Step 2: Verify red**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages
```

Expected: FAIL because `_plugin_alert_display_value()` does not exist and the alert width is still text-heavy.

- [ ] **Step 3: Implement value-only alert drawing**

Add `_plugin_alert_display_value()` and update `_paint_plugin_alert()` so alert cards draw:

```python
value = self._plugin_alert_display_value(label, detail, summary, status, alert_title, alert_body)
```

The painter should show a solid rounded capsule, an animated icon box with a progress ring/pulse, and one right-side value line. Use `time.monotonic()` for pulse alpha and pass plugin progress into `_paint_plugin_progress_ring()`.

- [ ] **Step 4: Verify green**

Run the same unittest command. Expected: OK.

---

### Task 3: Expanded Function Panel First Version

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests proving the expanded plugin panel is shorter and exposes runtime lines by plugin:

```python
def test_expanded_plugin_panel_is_not_overlong(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.expanded = True

    _width, height = visual._main_size_for_kind(IslandKind.PLUGIN, 420.0)

    self.assertLessEqual(height, 190.0 * IslandStackVisual.VISUAL_SCALE)

def test_plugin_function_lines_are_runtime_not_settings(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    title, lines = visual._plugin_function_lines("WEATHER", "26°C 晴", "北京 · 东南风 · 湿度 40%", "normal")

    self.assertIn("天气", title)
    self.assertIn("26°C 晴", lines)
    self.assertNotIn("手动城市", " ".join(lines))
```

- [ ] **Step 2: Verify red**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages
```

Expected: FAIL because the plugin panel is 220 visual units tall and `_plugin_function_lines()` does not exist.

- [ ] **Step 3: Implement the shorter function panel**

Change plugin expanded height to `188.0` visual units. Add `_plugin_function_lines()` returning runtime copy:

```python
if label == "TIME": return "时间", (...)
if label == "WEATHER": return "天气", (...)
if label == "AGENDA": return "日程", (...)
return label, (...)
```

Update `_paint_plugin_function_panel()` so the body contains a left animated focus icon/ring and right runtime lines. Keep the top nav as three compact selectors.

- [ ] **Step 4: Verify green**

Run the same unittest command. Expected: OK.

---

### Task 4: Full Verification

**Files:**
- All touched files

- [ ] **Step 1: Compile**

Run:

```powershell
.\.venv\Scripts\python.exe -m py_compile local/island_stack_visual.py
```

- [ ] **Step 2: Focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages tests.test_plugin_provider tests.test_mac_preferences_shell
```

- [ ] **Step 3: Full tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

- [ ] **Step 4: Cleanup**

Remove project `api/local/tests` `__pycache__` folders and untracked runtime lyric cache files.

---

## Self-Review

- Spec coverage: covers balanced strip layout, alert sub-island animation language, expanded function panel, and settings/function allocation.
- Placeholder scan: no TBD markers; future class features are represented as existing roadmap lines, not implementation promises.
- Type consistency: `_plugin_widths()`, `_plugin_item_rects()`, `_plugin_alert_display_value()`, and `_plugin_function_lines()` are consistently named across tests and implementation steps.
