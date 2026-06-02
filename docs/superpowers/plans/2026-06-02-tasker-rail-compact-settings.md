# Tasker Rail And Compact Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement or audit this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace center-biased Tasker docking with left/right taskbar lanes and taskbar-range dragging, simplify obsolete Tasker settings, compact the settings UI, and switch plugin count selection to per-plugin checkboxes.

**Architecture:** Keep the V2 renderer as the owner of Tasker geometry. Taskbar preview computes two side lanes instead of a single centered fallback; once in Tasker mode, horizontal placement is stored as a ratio and constrained to the taskbar task area. Settings changes stay in `local/settings_window.py` and persisted defaults stay in `local/settings_store.py`.

**Tech Stack:** Python 3, PySide6 widgets/painting, `unittest`, existing settings store and renderer tests.

---

## File Structure

- Modify `local/island_stack_visual.py`: left/right lane computation, Tasker horizontal drag, left resize/hover handle geometry, pure-white background style availability, plugin enablement model.
- Modify `local/settings_store.py`: remove obsolete Tasker defaults, add per-plugin enabled defaults.
- Modify `local/settings_window.py`: remove obsolete Tasker controls, compact window styling, rename music background setting, replace plugin count slider with three checkboxes.
- Modify `local/app.py`: load new plugin enabled settings and stop loading removed Tasker controls.
- Modify `tests/test_island_stack_visual_messages.py`: behavior tests for lanes, drag ratio, handle rect, pure-white colors, plugin enablement.
- Modify `tests/test_mac_preferences_shell.py`: settings/default/source tests.
- Modify `docs/taskbar-island/PRD.md`: updated Tasker rail and settings rules.

## Task 1: Tasker Left/Right Lanes And Drag Rail

- [ ] Write failing tests for two side lanes when no occupied taskbar rects exist and when occupied rects split the taskbar.
- [ ] Write failing tests that Tasker horizontal placement clamps to taskbar track and updates a persisted ratio.
- [ ] Write failing tests for the left rounded Tasker resize/hover rail rect.
- [ ] Implement side-lane helpers, Tasker drag helper, and rail rect.
- [ ] Run focused tests and verify green.

## Task 2: Remove Obsolete Tasker Settings

- [ ] Write failing tests that defaults and settings source no longer expose Tasker trigger threshold, plugin compact style, or message compact style.
- [ ] Remove those defaults from `local/settings_store.py`, settings snapshot, app loading, and placement page controls.
- [ ] Keep internal fallbacks where old user settings may exist, but do not persist or expose them.
- [ ] Run focused settings tests and verify green.

## Task 3: Music Background Style And Pure White Everywhere

- [ ] Write failing tests that Settings labels use `音乐岛背景样式` and not `音乐岛光效`.
- [ ] Write failing tests that `pure_white` remains selectable even when lyric color-fill effects are enabled.
- [ ] Ensure pure-white active/default lyric colors are dark in compact and expanded music layouts.
- [ ] Run focused tests and verify green.

## Task 4: Compact Settings Window

- [ ] Write failing source/behavior tests for 80% window minimum, rounded shell/card styling, and reduced row vertical padding.
- [ ] Update window minimum, sidebar/title/page/card/row spacing, and stylesheet radius tokens.
- [ ] Run settings tests and verify green.

## Task 5: Plugin Checkboxes

- [ ] Write failing tests for `v2_enabled_plugins` defaults and settings source using three plugin checkboxes instead of plugin count slider.
- [ ] Add `enabled_plugin_ids` support while preserving `enabled_plugin_count` compatibility.
- [ ] Update plugin sample filtering and plugin page controls.
- [ ] Run focused tests and full suite.

## Task 6: Verification

- [ ] Run `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages tests.test_mac_preferences_shell -v`.
- [ ] Run `.\.venv\Scripts\python.exe -m unittest discover tests -v`.
- [ ] Run `git diff --check`.

## Self-Review

- Spec coverage: left/right lanes, no center fallback, Tasker rail drag, left handle, removed obsolete settings, music background label, pure-white universal availability, compact settings UI, and plugin checkboxes are covered.
- Placeholder scan: no placeholders.
- Type consistency: plugin enablement key is `v2_enabled_plugins`; Tasker horizontal placement uses `taskbar_position_ratio`.
