# Gelv Tasker Settings Fonts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore full music background style availability, correct placement-mode availability, add Settings-only font import, and polish Tasker rails.

**Architecture:** Keep lyric and Tasker rendering in `local/island_stack_visual.py`; keep Settings UI behavior in `local/settings_window.py`/`local/mac_style.py`; persist only app-wide settings in `local/settings_store.py` and app loading in `local/app.py`.

**Tech Stack:** Python, PySide6, `unittest`.

---

### Task 1: Gelv And Theme Background Behavior

**Files:**
- Modify: `local/island_stack_visual.py`
- Modify: `local/settings_window.py`
- Test: `tests/test_island_stack_visual_messages.py`
- Test: `tests/test_mac_preferences_shell.py`

- [x] **Step 1: Write failing tests** proving Gelv color-fill no longer restricts music background choices and theme mode changes no longer force pure white/pure black.
- [x] **Step 2: Verify RED** with the focused tests.
- [x] **Step 3: Implement** by making forced black background apply only to the text-fill effect painting path, not to the stored/selected music background style; remove theme-sync side effects.
- [x] **Step 4: Verify GREEN** with the focused tests.

### Task 2: Placement Mode Availability And Disabled Visuals

**Files:**
- Modify: `local/settings_window.py`
- Modify: `local/mac_style.py`
- Test: `tests/test_mac_preferences_shell.py`

- [x] **Step 1: Write failing tests** for manual MyFinder false showing `Free/Tasker`, manual MyFinder true showing `Free/Finder`, and disabled options using gray styling.
- [x] **Step 2: Verify RED** with the focused tests.
- [x] **Step 3: Implement** availability logic and disabled combo/list styling.
- [x] **Step 4: Verify GREEN** with the focused tests.

### Task 3: Settings Font Selection And Import

**Files:**
- Modify: `local/settings_window.py`
- Modify: `local/settings_store.py`
- Test: `tests/test_mac_preferences_shell.py`

- [x] **Step 1: Write failing tests** for appearance font choices, imported Settings-only fonts, and default built-in choices.
- [x] **Step 2: Verify RED** with the focused tests.
- [x] **Step 3: Implement** Settings font combo, import button, `QFontDatabase.addApplicationFont`, and settings-window-only application.
- [x] **Step 4: Verify GREEN** with the focused tests.

### Task 4: Tasker Rail Color And Spacing

**Files:**
- Modify: `local/island_stack_visual.py`
- Modify: `local/settings_window.py`
- Modify: `local/settings_store.py`
- Modify: `local/app.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [x] **Step 1: Write failing tests** for default gray left handle color, configurable handle color persistence, and a slightly wider message rail gap.
- [x] **Step 2: Verify RED** with the focused tests.
- [x] **Step 3: Implement** the new color setting and spacing adjustment.
- [x] **Step 4: Verify GREEN** with focused tests.

### Task 5: Final Verification

- [x] **Step 1: Run focused Tasker/settings tests.**
- [x] **Step 2: Run all tests with `.\.venv\Scripts\python.exe -m unittest discover tests -v`.**
- [x] **Step 3: Run `git diff --check`.**
