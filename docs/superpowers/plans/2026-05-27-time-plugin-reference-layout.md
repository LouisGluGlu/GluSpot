# Time Plugin Reference Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the TIME plugin expanded panel to match the reference images: left real-time clock, middle countdown presets or active countdown ring, right alarm switch list, and an inline alarm settings editor.

**Architecture:** Keep the app Python/PySide6-only. Plugin runtime data stays behind `api/plugin_provider.py`; visual state, hit targets, wheel interaction, and drawing stay in `local/island_stack_visual.py`. Remove the old side-reminder rail from the plugin panel and fold countdown/alarm reminders into TIME's middle and right modules.

**Tech Stack:** Python, PySide6 `QPainter`, `unittest`.

---

### Task 1: Panel Model And Hit Targets

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests for `_time_plugin_panel_model()` to return `mode`, `clock`, `countdown_presets`, `countdown_active`, and `alarms`, and for `_plugin_panel_layout()` to expose `time_clock`, `time_countdown`, and `time_alarm` without a reminder rail dependency.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_time_plugin_panel_model_defaults_to_three_reference_modules tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_time_plugin_running_model_uses_center_ring`

Expected: FAIL because `_time_plugin_panel_model()` does not exist yet.

- [ ] **Step 3: Implement the model helpers**

Add pure helpers for time display, countdown presets `[5, 15, 10, 20]`, active countdown text/progress, default alarm rows, and editable alarm state.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same focused tests.

### Task 2: Interaction Commands

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests that `timer-start-5` writes `v2_timer_started_at` and `v2_timer_end_at`, `alarm-toggle-0` updates the panel state, and `alarm-add-open` switches the TIME panel into alarm editor mode.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_time_countdown_preset_action_starts_timer tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_alarm_add_opens_inline_editor`

Expected: FAIL because these commands are not handled yet.

- [ ] **Step 3: Implement command handling**

Extend `_activate_plugin_action()` for countdown presets, alarm toggles, add/edit mode, save mode, and cancel mode. Keep writes behind `settings_setter`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same focused tests.

### Task 3: Alarm Editor Wheel State

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests that wheel commands over hour, minute, frequency, and weekday hit zones update internal editor state.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_alarm_editor_wheel_updates_hour_minute_and_frequency`

Expected: FAIL because wheel handling is absent.

- [ ] **Step 3: Implement wheel handling**

Add `wheelEvent()` and editor hit target maps so columns behave like lightweight picker wheels.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same focused test.

### Task 4: Painter Replacement

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add source/behavior tests ensuring TIME panel painting no longer calls `_paint_plugin_panel_reminders()` and registers action targets for presets, alarm toggles, and alarm add.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_time_panel_paint_registers_reference_actions_without_reminder_rail`

Expected: FAIL until painter is replaced.

- [ ] **Step 3: Implement painter**

Draw the reference layout with opaque dark modules, yellow outlines, analog clock, preset circles, active countdown ring, iOS-style toggles, and the inline alarm editor.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same focused test.

### Task 5: Verification

**Files:**
- Test only.

- [ ] Run `.\.venv\Scripts\python.exe -m py_compile local\island_stack_visual.py`
- [ ] Run `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages`
- [ ] Run `.\.venv\Scripts\python.exe -m unittest discover -s tests`
- [ ] Run `git diff --check`
