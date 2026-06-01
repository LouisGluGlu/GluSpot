# Plugin TIME Adaptive UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the PySide6 TIME plugin panel an adaptive hand-painted component: wide panels show clock, countdown, and alarm together; narrow panels show only countdown by default and allow wheel navigation to alarm and clock.

**Architecture:** Keep all UI in `local/island_stack_visual.py`, with behavior protected by `tests/test_island_stack_visual_messages.py`. Add pure helpers for TIME panel mode, adaptive height, active narrow module, and wheel switching. Do not touch provider, lyrics, music progress, or NetEase position code.

**Tech Stack:** Python, PySide6 `QPainter`, `unittest`.

---

## UI Specification From Images 2-4

- **Image 2 dashboard idle:** three independent modules on a dark strip. Clock and countdown are square-ish instrument blocks; alarm is wider and owns list density. Labels sit below each module in gold.
- **Image 3 dashboard running:** countdown changes from 2x2 preset buttons to a centered gold ring with `4:59`; clock and alarm keep the same geometry. No visible music progress behavior is introduced.
- **Image 4 alarm editor:** a single focused settings surface. Title is centered in gold; fields are large bordered modules. Time uses a wheel stack; note is a bordered input; frequency uses two compact modules.
- **Narrow behavior:** when the available panel body is too narrow for the three modules, show a one-module viewport. The default module is countdown. Mouse wheel over the panel rotates `countdown -> alarm -> clock -> countdown`. Alarm toggles and add/edit actions remain clickable when alarm is active.
- **Adaptive height:** dashboard height derives from module mode rather than staying fixed. Narrow single-module dashboard is shorter than the old fixed panel; alarm editor remains taller because it needs picker and note controls.

## Addendum: Images 3-6 Adaptive Ratio And Countdown Controls

- **Extreme proportions:** the three-module dashboard uses content-driven clamps instead of stretching by raw percentage. Clock stays close to a square instrument, countdown has a wider control bay when space allows, and alarm keeps enough width for two dense rows. Below the minimum combined width, the panel becomes a single-module viewport instead of producing crushed text.
- **Running countdown, wide module:** the countdown card shows the ring plus explicit pause and cancel controls. The ring interior is also a pause hit target so the previous tall/ring-first layout gains pause without requiring a new gesture.
- **Running countdown, compact module:** when the countdown card is not wide enough for text buttons, controls collapse into icon circles on the right side while the ring remains readable.
- **Paused countdown:** after pause, the ring stays in place and shows a play action plus a red cancel action, matching image 6. Resume re-creates the remaining timer; cancel clears the countdown state.
- **Scope boundary:** these controls only touch TIME countdown settings and local TIME panel state. They do not alter music progress, lyric timing/offset, NetEase position anchors, WebView/HTML, or provider data outside the TIME plugin path.

## Addendum 2: Corrected Layout Rules From Follow-Up Images

- **Remove the useless dashboard split strip:** the TIME expanded dashboard no longer paints the centered handle/right-time strip between the compact plugin row and the modules. The panel keeps only the left `TIME` title and lets the module row sit directly below it.
- **Layout modes:** `triple` is the default three-module layout; `double` is the over-narrow two-module layout with clock fixed on the left and the right module paging between countdown and alarm; `stretch` is a three-module layout where the countdown card grows horizontally.
- **Over-narrow paging:** mouse wheel switches the right module between countdown and alarm only. The clock is always visible in the left slot. Two page dots are painted on the right edge.
- **Countdown idle state:** idle countdown cards only expose preset start actions. They must not register pause, resume, or cancel actions before a timer is actually running or paused.
- **Countdown pause state:** default and over-narrow layouts register pause on the running countdown ring only. After pause, a dark translucent overlay appears on the countdown card with continue and cancel buttons. Stretch layout may show pause/cancel as side controls, matching the longer card reference.
- **Timer count cap:** countdown rendering accepts at most two active timer entries. Default and over-narrow layouts show the most recent entry first; stretch layout can show two rings side by side.

### Task 1: Adaptive Model And Layout Tests

**Files:**
- Modify: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests for layout mode, adaptive height, and narrow default module:

```python
def test_time_panel_layout_switches_to_single_module_when_narrow(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    narrow = visual._plugin_panel_layout(QRectF(0.0, 0.0, 260.0, 148.0))
    wide = visual._plugin_panel_layout(QRectF(0.0, 0.0, 560.0, 188.0))

    self.assertEqual(narrow["time_layout_mode"], "single")
    self.assertEqual(narrow["time_visible_module"], "countdown")
    self.assertEqual(narrow["time_clock"].width(), 0.0)
    self.assertGreater(narrow["time_countdown"].width(), 150.0)
    self.assertEqual(wide["time_layout_mode"], "triple")
    self.assertGreater(wide["time_alarm"].width(), wide["time_countdown"].width())

def test_plugin_panel_height_adapts_for_narrow_time_and_editor(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.plugin_time_mode = "dashboard"

    narrow_height = visual._plugin_panel_height(260.0)
    wide_height = visual._plugin_panel_height(620.0)
    visual.plugin_time_mode = "alarm_editor"
    editor_height = visual._plugin_panel_height(420.0)

    self.assertLess(narrow_height, wide_height)
    self.assertGreater(editor_height, narrow_height)
    self.assertGreaterEqual(editor_height, visual._u(168.0))

def test_time_panel_wide_height_preserves_reference_module_depth(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    height = visual._plugin_panel_height(620.0)
    layout = visual._plugin_panel_layout(QRectF(0.0, 0.0, 620.0, height))

    self.assertGreaterEqual(height, visual._u(216.0))
    self.assertGreaterEqual(layout["time_countdown"].height(), visual._u(82.0))
    self.assertGreaterEqual(layout["time_alarm"].height(), visual._u(82.0))

def test_time_countdown_running_ring_is_centered_like_reference(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    rect = QRectF(120.0, 40.0, 142.0, 88.0)

    ring = visual._time_countdown_ring_rect(rect)

    self.assertAlmostEqual(ring.center().x(), rect.center().x(), delta=1.0)
    self.assertAlmostEqual(ring.center().y(), rect.center().y() + visual._u(4.0), delta=2.0)
    self.assertLessEqual(ring.width(), rect.height() - visual._u(18.0))

def test_time_panel_narrow_active_module_defaults_to_countdown(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    self.assertEqual(visual._time_visible_module_for_width(260.0), "countdown")
```

- [ ] **Step 2: Run tests to verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_time_panel_layout_switches_to_single_module_when_narrow tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_plugin_panel_height_adapts_for_narrow_time_and_editor tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_time_panel_narrow_active_module_defaults_to_countdown`

Expected: FAIL because `_plugin_panel_height()` does not accept width, layout has no `time_layout_mode`, and `_time_visible_module_for_width()` does not exist.

### Task 2: Narrow Wheel Switching Tests

**Files:**
- Modify: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing test**

Add a small fake wheel event and test module cycling:

```python
def test_time_panel_wheel_cycles_narrow_modules(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.plugin_time_mode = "dashboard"
    visual.plugin_panel_last_width = 260.0
    visual.plugin_time_visible_module = "countdown"
    visual.update = lambda: None

    class FakeDelta:
        def __init__(self, y):
            self._y = y
        def y(self):
            return self._y

    class FakeWheel:
        def __init__(self, y):
            self.accepted = False
            self._delta = FakeDelta(y)
        def angleDelta(self):
            return self._delta
        def accept(self):
            self.accepted = True

    event = FakeWheel(-120)

    self.assertTrue(visual._handle_time_panel_module_wheel(event))
    self.assertEqual(visual.plugin_time_visible_module, "alarm")
    self.assertTrue(event.accepted)
```

- [ ] **Step 2: Run test to verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_time_panel_wheel_cycles_narrow_modules`

Expected: FAIL because `_handle_time_panel_module_wheel()` does not exist.

### Task 3: Adaptive Implementation

**Files:**
- Modify: `local/island_stack_visual.py`

- [ ] **Step 1: Add pure helpers**

Implement:

```python
def _time_panel_breakpoint(self) -> float:
    return self._u(360.0)

def _time_panel_modules(self) -> list[str]:
    return ["countdown", "alarm", "clock"]

def _time_visible_module_for_width(self, width: float) -> str:
    if float(width) >= self._time_panel_breakpoint():
        return "all"
    module = str(getattr(self, "plugin_time_visible_module", "countdown") or "countdown")
    return module if module in self._time_panel_modules() else "countdown"
```

- [ ] **Step 2: Make panel height width-aware**

Change `_plugin_panel_height(self, width: float | None = None)` to return `148` for narrow dashboard, `188` for wide dashboard, and `178` for alarm editor. Update `_expanded_stack_height()` and `_build_visuals()` calls to pass `stack_w`.

- [ ] **Step 3: Make layout adaptive**

Update `_plugin_panel_layout()` so narrow TIME mode sets `time_layout_mode="single"`, `time_visible_module` to the active module, and assigns the whole body module area to only the visible module while the hidden module rects are zero-width.

- [ ] **Step 4: Add wheel cycling**

Add `_handle_time_panel_module_wheel(event)` and call it from `wheelEvent()` after the alarm editor wheel handler. It should only act in dashboard mode when the last panel width is below the breakpoint.

- [ ] **Step 5: Paint only visible narrow module**

Update `_paint_time_plugin_panel()` to store `plugin_panel_last_width`, read `time_layout_mode`, and paint only the visible module in single mode. Keep command hit targets for the painted module only.

### Task 4: Visual Polish And Verification

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages`

- [ ] **Step 2: Render visual previews**

Render temporary previews for wide idle, wide running, narrow countdown, narrow alarm, and alarm editor to `logs/time-adaptive-check.png`.

- [ ] **Step 3: Run full verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m py_compile local\island_stack_visual.py
.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages
.\.venv\Scripts\python.exe -m unittest discover -s tests
git diff --check
```

- [ ] **Step 4: Scope check**

Inspect `git diff -- local/island_stack_visual.py tests/test_island_stack_visual_messages.py docs/superpowers/plans/2026-05-28-plugin-time-adaptive-ui.md` and confirm no WebView/HTML, provider, lyric, music progress, or NetEase changes were introduced by this task.
