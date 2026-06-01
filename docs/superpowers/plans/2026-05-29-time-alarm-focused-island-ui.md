# TIME Alarm Focused Island UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tighten the focused plugin island and finish the TIME alarm editor behavior: bottom actions, taller default panel, unified note limits, and weekly/monthly/yearly picker flows.

**Architecture:** Keep the work inside the PySide hand-painted TIME plugin surface in `local/island_stack_visual.py`, with behavior coverage in `tests/test_island_stack_visual_messages.py`. Do not touch provider, music progress, lyric sync, or NetEase jump paths.

**Tech Stack:** Python 3, PySide6 `QPainter`, `unittest`.

---

## Mapping Points

- Focused plugin strip: cap the active cell so the focused TIME chip is shorter and leaves readable room for the other plugin chips.
- TIME dashboard height: increase the default plugin panel height and let modules consume the added height without adding dead bottom space.
- Alarm editor actions: move cancel/confirm to a bottom action row, make them wider and taller in both reference and narrow layouts.
- Alarm note: input max remains 10 characters; display budget is 5 characters including punctuation. More than 5 characters scroll in reminder surfaces. Attempting the 11th character preserves the note and exposes a short hint state for the painter.
- Recurrence detail pages: weekly opens a Mon-Sun selector; monthly opens a day picker with special labels for 29 and 31; yearly opens month/day pickers with leap-year-aware clamping.

## Tasks

### Task 1: RED Tests

- [x] Add tests for focused plugin width caps.
- [x] Add tests for increased TIME dashboard height and bottom action geometry.
- [x] Add tests for 5-character alarm note display, 10-character input cap, and over-limit hint.
- [x] Add tests for weekly/monthly/yearly detail layout and special recurrence labels.
- [x] Run focused tests and verify they fail for the expected missing behavior.

### Task 2: GREEN Implementation

- [x] Tune `_plugin_widths()` so focused cells are capped while unfocused cells stay readable.
- [x] Tune `_plugin_panel_height()` and `_time_plugin_panel_layout()` for taller, denser default modules.
- [x] Rework `_time_alarm_editor_layout()` action geometry to bottom wide buttons.
- [x] Add note display/hint helpers and wire them into key handling, compact reminders, and side alert scrolling.
- [x] Update recurrence labels and detail picker geometry/action rects.

### Task 3: Verification

- [x] Run `.\.venv\Scripts\python.exe -m py_compile local\island_stack_visual.py`.
- [x] Run `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages`.
- [x] Run `.\.venv\Scripts\python.exe -m unittest discover -s tests`.
- [x] Run `git diff --check`.
- [x] Generate a TIME preview screenshot if the preview harness is available.
