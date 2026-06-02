# Tasker Follow Material Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish Tasker mode so it follows the real taskbar/fullscreen state, inherits taskbar material, uses app-colored notifications, supports delayed resize rail use, and keeps settings/empty/audio states consistent.

**Architecture:** Keep behavior inside the existing V2 island surface. `local/island_stack_visual.py` owns Tasker layout, taskbar tracking, message/plugin/music painting, and audio-hide state. `local/settings_window.py` and `local/mac_style.py` own settings shell visuals and preview controls, with defaults/wiring in `local/settings_store.py` and `local/app.py`.

**Tech Stack:** Python, PySide6, Windows taskbar geometry helpers, `unittest`.

---

### Task 1: Tasker Tracking And Fullscreen/Hidden Taskbar

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [x] **Step 1: Write failing tests** for Tasker position refresh, fullscreen blocking of recognition, and hidden taskbar re-clamping.
- [x] **Step 2: Verify RED** with focused `unittest` cases.
- [x] **Step 3: Implement** helpers that refresh Tasker position from the current screen taskbar every tick, suppress Tasker preview/docking under fullscreen foreground, and keep Tasker attached when the taskbar rectangle moves or auto-hides.
- [x] **Step 4: Verify GREEN** with the same focused tests.

### Task 2: Tasker Expanded Material And Rounded Settings Shell

**Files:**
- Modify: `local/island_stack_visual.py`
- Modify: `local/settings_window.py`
- Modify: `local/mac_style.py`
- Test: `tests/test_island_stack_visual_messages.py`
- Test: `tests/test_mac_preferences_shell.py`

- [x] **Step 1: Write failing tests** for expanded Tasker material token usage and symmetric settings shell clipping.
- [x] **Step 2: Verify RED** with focused tests.
- [x] **Step 3: Implement** taskbar material tokens for expanded frame fill/stroke/radius and adjust settings shell/sidebar/content right-side rounding/clipping.
- [x] **Step 4: Verify GREEN** with focused tests.

### Task 3: Tasker Notifications And Resize Rail

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [x] **Step 1: Write failing tests** for app-colored message blocks, up to three vertical message rails, rail hover grace time, and overlaying urgent TIME alerts on the main island.
- [x] **Step 2: Verify RED** with focused tests.
- [x] **Step 3: Implement** message block color/icon semantics, message rail layout without taskbar gaps, three-rail cap, three-second resize rail grace, and Tasker urgent TIME overlay routing.
- [x] **Step 4: Verify GREEN** with focused tests.

### Task 4: Tasker Music Styles, Theme Sync, And Audio Hide

**Files:**
- Modify: `local/island_stack_visual.py`
- Modify: `local/settings_window.py`
- Modify: `local/settings_store.py`
- Modify: `local/app.py`
- Test: `tests/test_island_stack_visual_messages.py`
- Test: `tests/test_mac_preferences_shell.py`

- [x] **Step 1: Write failing tests** for all music background styles being allowed in Tasker, theme mode changes mapping light to pure white and dark to pure black, and no-audio auto-hide suppressing the music island.
- [x] **Step 2: Verify RED** with focused tests.
- [x] **Step 3: Implement** Tasker style availability, explicit theme sync helper, settings/app wiring, and real no-audio hide logic based on provider/audio state.
- [x] **Step 4: Verify GREEN** with focused tests.

### Task 5: Empty Flow Preview And PRD

**Files:**
- Modify: `local/settings_window.py`
- Modify: `docs/taskbar-island/PRD.md`
- Test: `tests/test_mac_preferences_shell.py`

- [x] **Step 1: Write failing tests** for a real empty-flow color preview widget/source markers and PRD acceptance text.
- [x] **Step 2: Verify RED** with focused tests.
- [x] **Step 3: Implement** the preview widget/stylesheet refresh and update the Tasker PRD.
- [x] **Step 4: Verify GREEN** with focused tests.

### Task 6: Final Verification

**Files:**
- Test-only execution.

- [x] **Step 1: Run focused Tasker/settings tests**: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages tests.test_mac_preferences_shell -v`.
- [x] **Step 2: Run all tests**: `.\.venv\Scripts\python.exe -m unittest discover tests -v`.
- [x] **Step 3: Run diff hygiene**: `git diff --check`.
