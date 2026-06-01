# TIME Alarm Narrow Recurrence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow alarm editor flow with note paging, weekly/monthly/yearly recurrence pickers, date boundary labeling, and note-based alarm side reminders.

**Architecture:** Keep this PySide-only inside `local/island_stack_visual.py`. Add pure helpers for alarm recurrence normalization, narrow layout geometry, picker display values, and alarm reminder text, then route painting and actions through the existing plugin action and wheel paths. Tests live in `tests/test_island_stack_visual_messages.py` and verify behavior before painter details.

**Tech Stack:** Python 3, PySide6 `QPainter`, `unittest`.

---

## Files

- Modify: `local/island_stack_visual.py`
  - Add alarm editor state fields: `frequency_unit`, `weekdays`, `month_day`, `year_month`, `year_day`, `editor_page`, `detail_mode`.
  - Add recurrence helpers near `_time_alarm_editor_state()`.
  - Extend `_adjust_alarm_editor_field()` and `_alarm_picker_display_values()` for unit/day/month/year fields.
  - Add narrow editor layout and detail picker painters near `_paint_time_alarm_editor()`.
  - Adjust TIME alarm side alert text source and width.
- Modify: `tests/test_island_stack_visual_messages.py`
  - Add red tests for narrow layout, note limit, recurrence labels, weekly/monthly/yearly picker behavior, side alert note text, and wheel animation coverage.
- Modify: `docs/superpowers/plans/2026-05-28-plugin-time-visual-reference.md`
  - Add a short addendum linking the implementation to the visual reference.

---

## Task 1: Alarm State And Recurrence Helpers

- [ ] **Step 1: Write failing helper tests**

Add tests:
```python
def test_alarm_editor_note_is_limited_to_ten_chars(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.plugin_time_mode = "alarm_editor"
    visual.plugin_alarm_editor = {"hour": 7, "minute": 30, "note": "", "frequency": "每天", "weekdays": "二、四"}
    visual.plugin_alarm_editor_focus = "note"
    visual.update = lambda: None

    for char in "一二三四五六七八九十十一":
        visual._handle_alarm_editor_key(char)

    self.assertEqual(visual.plugin_alarm_editor["note"], "一二三四五六七八九十")

def test_alarm_recurrence_label_handles_week_month_and_year(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    self.assertEqual(visual._alarm_recurrence_label({"frequency_unit": "weekly", "weekdays": [2, 4, 6]}), "每周")
    self.assertEqual(visual._alarm_recurrence_value_label({"frequency_unit": "weekly", "weekdays": [2, 4, 6]}), "2 4 6")
    self.assertEqual(visual._alarm_recurrence_label({"frequency_unit": "monthly", "month_day": 29}), "每闰月")
    self.assertEqual(visual._alarm_recurrence_label({"frequency_unit": "monthly", "month_day": 31}), "每大月")
    self.assertEqual(visual._alarm_recurrence_label({"frequency_unit": "yearly", "year_month": 4, "year_day": 28}), "每年")
    self.assertEqual(visual._alarm_recurrence_value_label({"frequency_unit": "yearly", "year_month": 4, "year_day": 28}), "4月28日")
```

- [ ] **Step 2: Run RED**

Run:
`.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_alarm_editor_note_is_limited_to_ten_chars tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_alarm_recurrence_label_handles_week_month_and_year`

Expected: FAIL because helper methods and 10-char note cap are missing.

- [ ] **Step 3: Implement helpers**

Add helpers:
```python
def _alarm_frequency_units(self) -> list[tuple[str, str]]:
    return [("daily", "每天"), ("weekly", "每周"), ("monthly", "每月"), ("yearly", "每年")]

def _days_in_month(self, year: int, month: int) -> int:
    month = max(1, min(12, int(month)))
    year = max(1, int(year))
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day

def _alarm_recurrence_label(self, editor):
    unit = str(editor.get("frequency_unit") or "").lower()
    if unit == "monthly":
        day = int(editor.get("month_day", 1) or 1)
        if day == 29:
            return "每闰月"
        if day == 31:
            return "每大月"
        return "每月"
    if unit == "yearly":
        return "每年"
    if unit == "weekly":
        return "每周"
    return "每天"
```

- [ ] **Step 4: Run GREEN**

Run the same focused tests. Expected: PASS.

## Task 2: Narrow Alarm Editor Layout

- [ ] **Step 1: Write failing layout tests**

Add tests:
```python
def test_alarm_editor_narrow_layout_has_main_and_note_pages(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    layout = visual._time_alarm_editor_layout(QRectF(0.0, 0.0, 320.0, 156.0))

    self.assertEqual(layout["mode"], "narrow")
    self.assertIn("main_page", layout)
    self.assertIn("note_page", layout)
    self.assertLess(layout["main_frequency"].left(), layout["main_note_dot"].left())
    self.assertGreater(layout["note"].width(), layout["time"].width())
```

- [ ] **Step 2: Run RED**

Run the single test. Expected: FAIL because narrow layout keys do not exist.

- [ ] **Step 3: Implement narrow geometry**

Extend `_time_alarm_editor_layout(rect)`:
- Return `mode: "narrow"` when `rect.width() < self._u(380.0)`.
- Top page has `time`, `frequency`, optional `frequency_value`, labels, and right page dots.
- Note page has one wide `note` field, label, page dots, save/cancel.
- Keep existing wide layout unchanged for current figure-2 tests.

- [ ] **Step 4: Run GREEN**

Run the narrow layout test plus existing figure-2 layout tests.

## Task 3: Weekly/Monthly/Yearly Pickers

- [ ] **Step 1: Write failing picker tests**

Add tests:
```python
def test_weekly_detail_mode_toggles_selected_weekdays(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.plugin_time_mode = "alarm_editor"
    visual.plugin_alarm_editor = {"frequency_unit": "weekly", "weekdays": [2, 4], "hour": 15, "minute": 22, "note": ""}

    visual._activate_plugin_action("alarm-editor-open-weekdays")
    visual._activate_plugin_action("alarm-editor-toggle-weekday-6")

    self.assertEqual(visual.plugin_alarm_editor["detail_mode"], "weekly")
    self.assertEqual(visual.plugin_alarm_editor["weekdays"], [2, 4, 6])

def test_monthly_and_yearly_day_adjustment_clamps_to_calendar_boundaries(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.plugin_alarm_editor = {"frequency_unit": "yearly", "year_month": 2, "year_day": 29}

    visual._adjust_alarm_editor_field("year_month", 1)
    self.assertLessEqual(visual.plugin_alarm_editor["year_day"], 31)
    visual.plugin_alarm_editor = {"frequency_unit": "yearly", "year_month": 2, "year_day": 30}
    visual._normalize_alarm_editor_recurrence(visual.plugin_alarm_editor)
    self.assertEqual(visual.plugin_alarm_editor["year_day"], 29)
```

- [ ] **Step 2: Run RED**

Run both tests. Expected: FAIL because actions and normalization are missing.

- [ ] **Step 3: Implement actions and clamp logic**

Add action commands:
- `alarm-editor-open-weekdays`
- `alarm-editor-open-month-day`
- `alarm-editor-open-year-date`
- `alarm-editor-toggle-weekday-N`

Update `_adjust_alarm_editor_field()` for:
- `frequency_unit`
- `month_day`
- `year_month`
- `year_day`

- [ ] **Step 4: Run GREEN**

Run picker tests and existing alarm editor tests.

## Task 4: Alarm Side Alert Text

- [ ] **Step 1: Write failing side alert tests**

Add tests:
```python
def test_alarm_side_alert_uses_note_or_default_alarm_text(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    self.assertEqual(visual._time_alarm_alert_text("下午茶时间很长"), "下午茶时间很长")
    self.assertEqual(visual._time_alarm_alert_text(""), "闹钟")

def test_alarm_side_alert_width_fits_at_least_five_chars(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    width = visual._time_plugin_alert_width_for("07:30", "闹钟")

    self.assertGreaterEqual(width, visual._u(150.0))
```

- [ ] **Step 2: Run RED**

Expected: helper missing and width too narrow.

- [ ] **Step 3: Implement side alert text and width**

Use note text for alarm done/running side alert. Empty note displays `闹钟`. Width reserves at least five Chinese characters; longer notes still scroll via existing scrolling text helper.

- [ ] **Step 4: Run GREEN**

Run side alert tests.

## Task 5: Verification

- [ ] Run `.\.venv\Scripts\python.exe -m py_compile local\island_stack_visual.py`
- [ ] Run `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages`
- [ ] Run `.\.venv\Scripts\python.exe -m unittest discover -s tests`
- [ ] Run `git diff --check`
- [ ] Generate preview screenshots for narrow main, narrow note, weekly detail, monthly detail, and yearly detail.

## Execution Notes

- Added TDD coverage for direct numeric input on monthly/yearly recurrence fields.
- Added TDD coverage for narrow detail painting so monthly and yearly picker fields register wheel/input rects.
- Implemented the narrow two-page editor flow: main page for time/frequency, note page for the 10-character remark.
- Implemented weekly, monthly, and yearly detail views with boundary-aware day clamping.
- Updated TIME alarm side alerts so alarm reminders prefer the remark, default to `闹钟`, fit at least five Chinese characters, and keep the existing side-alert width cap.
- Generated visual preview: `logs/time-alarm-narrow-recurrence-preview.png`.
- Verification completed:
  - `.\.venv\Scripts\python.exe -m py_compile local\island_stack_visual.py`
  - `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages`
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests`
  - `git diff --check`

## Follow-Up Adaptive Pass

- Added regression coverage that the reference-width alarm editor (`420px`) uses the paged narrow layout instead of the old single-row editor.
- Added regression coverage that the narrow page consumes more vertical height, keeping field labels within the lower content band while avoiding bottom overflow.
- Raised the alarm-editor narrow breakpoint to match the compact island reference width.
- Rebalanced narrow editor geometry: larger fields, lower content band, adaptive value field hiding under extreme width pressure, and wider note input on the note page.
- Improved picker rendering density by drawing the divider behind wheel values and clipping wheel rows inside the field.
- Generated visual preview: `logs/time-alarm-adaptive-reference-width-preview.png`.
