# Plugin Alert And Finder Layer Regression Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the current plugin side-reminder layout/style regressions, fix expanded TIME alarm due confirmation layout, and keep Finder mode from replacing the island's own opaque shell.

**Architecture:** Keep fixes inside the existing PySide6 hand-painted island surface. TIME countdown and alarm waiting/due states stay separated in `local/island_stack_visual.py`; behavior protection lives in `tests/test_island_stack_visual_messages.py`; product decisions are recorded in `docs/PRD.md` and `docs/ISSUES.md`.

**Tech Stack:** Python 3, PySide6 `QPainter`, `unittest`.

---

## Mapping Points

- Multiple side reminders should stay in one horizontal fallback row. If both sides cannot fit two circular alerts on the same side, split the pair around the main island instead of clamping them into overlap.
- Future/scheduled alarms are waiting reminders, not due reminders. They must carry `running` style state until TIME reports an actual `alarm`/due status.
- Expanded alarm due confirmation reserves its own block and the alarm list reserves room above the add button; no confirmation/list/add hit rects may overlap.
- Due alarm side reminders always use the alarm scrolling text renderer so the white focus character behavior is visible for both short and long notes.
- Finder mode must keep the music island's own opaque shell. The Finder cradle/material may exist behind or around the island, but it must not become the island style or make the original island transparent.
- Side reminders collapse only under true boundary pressure. If two full reminders cannot fit on one side but the main island is not at a boundary, they split across left/right instead of shrinking to circles.
- Circular TIME reminders center their icon inside the circular background and skip text painting; the circular fallback must not reuse the rectangular left-icon layout.

## Tasks

### Task 1: RED Tests

- [x] Add a failing test for two circular side reminders in an extreme floating boundary: they must not intersect each other or the main island.
- [x] Add a failing test proving scheduled alarm compact segments use waiting/running state instead of due/alarm state.
- [x] Add a failing test for expanded alarm due confirmation geometry: due block, list, and add button do not overlap, and the due block has a dedicated height.
- [x] Add a failing test requiring due alarm side reminders to call the alarm scrolling text renderer even for short notes.
- [x] Add a failing test proving Finder-owned music surfaces skip standalone music background painters. Superseded by the severe transparent-island regression.
- [x] Add a failing test proving Finder mode still paints the normal opaque music shell and border.
- [x] Add a failing test proving non-boundary multi-reminders split as full rectangles instead of shrinking.
- [x] Add a failing test proving circular TIME waiting reminders center their icon and do not paint side text.

### Task 2: GREEN Implementation

- [x] Update `_plugin_alert_rect()` to split two circular fallback alerts across both sides when neither side can fit the full row.
- [x] Update `_time_compact_segments()` so scheduled alarm rows emit `running` state.
- [x] Update `_paint_time_alarm_module()` to reserve add-button space in `list_rect` and enlarge the due confirmation block.
- [x] Update `_paint_time_plugin_alert()` so due alarm notes consistently use `_paint_alarm_scrolling_text()`.
- [x] Add a Finder-owned surface guard and use it in `_paint_music()` to skip standalone shell/background layers after the Finder cradle has been painted. Superseded by the severe transparent-island regression.
- [x] Roll back the Finder-owned surface skip so `_paint_music()` keeps the normal shell in Finder mode.
- [x] Change multi-reminder circular fallback to require boundary pressure or true single-alert space exhaustion.
- [x] Center TIME circular waiting icons and return before rectangular text layout.

### Task 3: Verification

- [x] Run the new focused tests and verify RED before implementation.
- [x] Run the focused tests again after implementation and verify GREEN.
- [x] Run `.\.venv\Scripts\python.exe -m py_compile local\island_stack_visual.py`.
- [x] Run `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages`.
- [x] Run `.\.venv\Scripts\python.exe -m unittest discover -s tests`.
- [x] Run `git diff --check`.
- [x] Generate and inspect a PySide screenshot covering side reminders, expanded TIME alarm due confirmation, and Finder fused mode.
- [x] Generate and inspect a PySide screenshot covering Finder opaque island shell, non-boundary rectangular reminders, and boundary circular centered icons.
