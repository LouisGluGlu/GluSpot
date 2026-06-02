# TIME Stretch Alerts And Alarm Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the latest TIME plugin corrections: stretch countdown rings, complete focused countdown text, stretch alarm editing/scrolling, strict separated countdown/alarm side-alert styles, due-alarm confirmation, and future style selection placeholders.

**Architecture:** Keep the implementation PySide6-only in `local/island_stack_visual.py`; settings placeholders live in the existing settings surfaces. Do not touch WebView/HTML, music progress, lyric sync, NetEase jump logic, or provider boundaries except through existing settings keys.

**Tech Stack:** Python 3, PySide6 `QPainter`, `unittest`.

---

## Mapping Points

- **Stretch countdown idle:** only stretch mode changes from capsule presets to four large circular ring buttons. Double/triple layouts keep their current compact capsule behavior.
- **Focused TIME strip:** focused plugin cells must receive `summary` and `progress`, so countdown text such as `倒计时 1:02:03` remains complete instead of falling back to the clock/detail field.
- **Stretch alarm list:** the alarm list owns wheel scrolling whenever the cursor is inside its list rect, including triple/stretch layouts. At list bounds, the narrow/double page-switch behavior still works.
- **Stretch alarm editor:** wide alarm editor uses the same `frequency_unit` plus dynamic recurrence value mapping as the narrow editor, including dedicated weekly/monthly/yearly detail page commands.
- **Side-alert split:** countdown and alarm side pills remain separate virtual ids so both due states can appear at once. Countdown not due shows a black pill with yellow countdown symbol and static remaining time. Alarm not due shows a black pill with yellow alarm symbol and either note or time, controlled by a setting. Countdown due and alarm due now share the same single-layer yellow pill language with black icon/text; countdown shows `时间到`, alarm shows the alarm note, and neither uses the old oversized text wave or white focus-character scrolling.
- **Alarm due confirmation:** when TIME reports an alarm status, capture the latest alarm reminder in widget state. The alarm module is temporarily taken over by a large block-level confirmation until `alarm-due-confirm` is clicked; the alarm list and add action stay hidden during that due state. A newer alarm overwrites the old pending block.
- **Future style placeholders:** add settings keys and visible options for side-island, plugin-island, music-island, and TIME alarm side display style so future visual schemes have a stable home.

## Tasks

### Task 1: RED Tests

- [ ] Add failing tests for stretch countdown presets becoming four square/circular ring hit targets.
- [ ] Add failing tests proving focused TIME receives summary/progress and paints the full countdown string.
- [ ] Add failing tests for alarm list wheel scrolling in stretch layout.
- [ ] Add failing tests for stretch alarm editor registering `frequency_unit` plus weekly/monthly/yearly detail action targets.
- [ ] Add failing tests for alarm side display setting (`note` vs `time`).
- [ ] Add failing tests for alarm due pending capture, full-module confirm block registration, hidden list/add actions, and clearing by `alarm-due-confirm`.
- [ ] Add source/default tests for future style placeholder settings.

### Task 2: GREEN Implementation

- [ ] Update `_paint_time_countdown_module()` to branch stretch idle presets into four large ring buttons.
- [ ] Extend `_paint_plugin_focus_cell()` signature and `_paint_plugin()` call site to pass summary/progress into TIME compact painting.
- [ ] Relax `_handle_time_alarm_list_wheel()` so list scrolling works in all visible layouts when the cursor is over the list.
- [ ] Rework the wide alarm editor branch to use `frequency_unit`, `_alarm_editor_frequency_value()`, and existing detail-open commands.
- [ ] Add `_time_alarm_side_display_mode()` and apply it when building alarm side-alert candidates.
- [ ] Add pending alarm capture, confirmation block painter, and `alarm-due-confirm` action handling.
- [ ] Add settings defaults, snapshot/apply wiring, and settings-page combo boxes for style placeholders.

### Task 3: Verification

- [ ] Run focused new tests and verify they go RED before implementation.
- [ ] Run focused tests again after implementation and verify GREEN.
- [ ] Run `.\.venv\Scripts\python.exe -m py_compile local\island_stack_visual.py local\settings_window.py local\plugin_manager_window.py`.
- [ ] Run `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages tests.test_mac_preferences_shell`.
- [ ] Run `.\.venv\Scripts\python.exe -m unittest discover -s tests`.
- [ ] Run `git diff --check`.
