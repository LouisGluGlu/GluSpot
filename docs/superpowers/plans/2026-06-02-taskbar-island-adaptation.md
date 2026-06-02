# Taskbar Island Location Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement or audit this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the old Finder/taskbar fusion split with `Finder / Free / Tasker` placement modes, and make Tasker docking behave like a Windows taskbar-aware V2 island.

**Architecture:** Keep all behavior in the existing V2 island renderer and settings shell. `v2_location_mode` is the persisted placement source of truth, while legacy taskbar/finder settings are read only for migration. Taskbar preview and docking use the real taskbar/task-area rectangle when available, with a conservative fallback that reserves Start/tray space.

**Tech Stack:** Python 3, PySide6 painting/layout, Win32 taskbar window probing through `ctypes`, `unittest`, existing settings persistence.

---

## File Structure

- Modify `local/settings_store.py`: add `v2_location_mode`, Tasker defaults, and bilingual lyric font-size default; remove old taskbar hint/fusion defaults.
- Modify `local/app.py`: migrate legacy settings into `v2_location_mode` and load Tasker visual settings.
- Modify `local/island_stack_visual.py`: implement placement mode state, taskbar preview, task-area-aware docking, Tasker compact/expanded rendering, plugin compact styles, and dual-line font sizing.
- Modify `local/settings_window.py`: regroup settings by function and expose location, Tasker, plugin, media, and appearance controls.
- Modify `tests/test_island_stack_visual_messages.py`: cover location state, taskbar preview/drop behavior, task-area drop zone, compact plugin styles, radius rules, and dual-line font sizing.
- Modify `tests/test_mac_preferences_shell.py`: cover defaults and settings-shell structure.
- Modify `docs/taskbar-island/PRD.md`: align the product spec with `Finder / Free / Tasker`.

## Task 1: Placement State And Persistence

**Files:**
- Modify: `local/settings_store.py`
- Modify: `local/app.py`
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_mac_preferences_shell.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [x] **Step 1: Write failing tests for the new settings contract**

Add coverage that `DEFAULT_SETTINGS` contains `v2_location_mode == "free"`, Tasker recognition/corner/plugin-style defaults, and does not contain `v2_taskbar_fusion_mode` or `v2_taskbar_hint_text`.

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_mac_preferences_shell.MacPreferencesShellTests.test_location_and_tasker_defaults_are_present tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_location_tasker_settings_are_saved_in_snapshot -v
```

Expected before implementation: failing/missing keys.

- [x] **Step 2: Implement `v2_location_mode` as source of truth**

Add `set_location_mode()` and keep `set_taskbar_fusion_mode()` only as a compatibility shim. Map legacy `compact`, `expanded`, and `taskbar` to `tasker`; map legacy `off` and `preview` to `free`.

- [x] **Step 3: Preserve MyFinder safety**

When `v2_manual_mydockfinder` is enabled, canonicalize Tasker back to Free and remove Tasker from the settings combo.

## Task 2: Taskbar Preview And Docking

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [x] **Step 1: Write failing tests for lower-third recognition and MyFinder blocking**

Cover `_should_show_taskbar_preview(0.70) == True` in Free mode, and `False` when Finder/manual MyFinder is active.

- [x] **Step 2: Implement taskbar preview state**

During dragging, compute the pointer's screen-height ratio. Show preview only after the configured lower-third threshold and only while a visible taskbar rectangle can be found.

- [x] **Step 3: Replace text prompt with direct taskbar drawing**

Use `TaskbarDropOverlay` as a transparent top-level overlay that paints shadow, translucent fill, and dashed outline directly over the taskbar drop zone. Do not paint prompt text.

- [x] **Step 4: Prefer taskbar task area over whole taskbar**

Detect Windows taskbar task-list child windows (`MSTaskListWClass`, `MSTaskSwWClass`) when available. If detection fails, reserve leading Start space and trailing tray space before computing the drop zone.

- [x] **Step 5: Snap accepted drops into the same task area**

On accepted Tasker drop, set `location_mode = "tasker"` and move the island so the main island center aligns with the detected task-area drop zone.

## Task 3: Tasker Compact And Expanded Surfaces

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [x] **Step 1: Write failing geometry and radius tests**

Cover taskbar-safe compact height, Win10 square radius, Win11 small radius, album-cover radius matching the Tasker shell radius, and expanded Tasker music using Windows radius policy.

- [x] **Step 2: Implement Windows radius policy**

Use `win10` for square corners and `win11` for small rounded corners. Apply the same radius to compact album art and shell.

- [x] **Step 3: Reuse Finder-like expanded structure with Windows material**

When Tasker is expanded, paint a Windows-style outer material frame around expanded sections while preserving the opaque V2 island shell and real provider data.

## Task 4: Plugin And Music Controls

**Files:**
- Modify: `local/island_stack_visual.py`
- Modify: `local/settings_window.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [x] **Step 1: Write failing tests for plugin compact style routing**

Cover `rail` and `block` rendering routes.

- [x] **Step 2: Implement rail style**

Paint a pure-color vertical rail with no border. Use black as the base segment and plugin/theme color as the progress or urgency segment.

- [x] **Step 3: Implement block style**

Paint a compact square block with an embedded icon, short value/capacity text, and optional progress ring.

- [x] **Step 4: Add bilingual lyric font-size control**

Persist `v2_dual_line_font_size` and use it for the secondary bilingual line in compact and expanded lyric rendering.

## Task 5: Settings Reorganization

**Files:**
- Modify: `local/settings_window.py`
- Test: `tests/test_mac_preferences_shell.py`

- [x] **Step 1: Write failing settings-shell tests**

Cover functional page order: `位置`, `媒体播放`, `插件`, `外观`, `弹幕`, `诊断`.

- [x] **Step 2: Build functional pages**

Move location/Tasker controls to `位置`, music and lyric controls to `媒体播放`, plugin controls to `插件`, and visual controls to `外观`.

- [x] **Step 3: Remove old taskbar fusion UI**

Expose `LOCATION_MODES` and no longer expose `TASKBAR_FUSION_MODES`, `taskbar_fusion_mode`, or `taskbar_hint_text` in the settings window.

## Task 6: Verification

**Files:**
- No production files.

- [x] **Step 1: Focused behavior tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages tests.test_mac_preferences_shell -v
```

Expected: all focused tests pass.

- [x] **Step 2: Full unit suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover tests -v
```

Expected: full suite passes.

- [x] **Step 3: Diff hygiene**

Run:

```powershell
git diff --check
```

Expected: no whitespace errors.

## Self-Review

- Spec coverage: `Finder / Free / Tasker`, manual MyFinder blocking, lower-third recognition, direct taskbar drawing without prompt text, task-area-aware drop placement, Tasker auto-reverting to Free, plugin rail/block styles, bilingual font-size control, Windows radius policy, and settings regrouping are covered.
- Placeholder scan: no `TBD`, `TODO`, or "implement later" placeholders remain.
- Type consistency: persisted keys use `v2_location_mode`, `v2_taskbar_recognition_threshold`, `v2_taskbar_corner_style`, `v2_taskbar_plugin_compact_style`, and `v2_dual_line_font_size`.
