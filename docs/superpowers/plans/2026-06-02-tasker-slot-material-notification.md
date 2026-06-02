# Tasker Slot Material Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement or audit this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Tasker docking so it activates only on real taskbar contact, shows usable left/right taskbar slots, adapts to light taskbar material, shrinks under congestion, and renders Tasker message notifications as taskbar-native strips/blocks.

**Architecture:** Keep Tasker as a non-invasive overlay mode in `local/island_stack_visual.py`. Use taskbar bounds plus detected/estimated occupied rectangles to derive available slots; use the active dragged island rectangle for contact gating; sample taskbar material only to select Tasker visual treatment. Persist new user-facing style choices through the existing settings store/window.

**Tech Stack:** Python 3, PySide6 painting/layout, Win32 taskbar probing through `ctypes`, `unittest`, existing V2 renderer/settings.

---

## File Structure

- Modify `local/island_stack_visual.py`: contact gating, left/right slot computation, multi-zone overlay, Tasker docking slot selection, material sampling, compact-to-cover shrink, Tasker message notification layout/painting.
- Modify `local/settings_store.py`: defaults for Tasker message compact style and light-material behavior if persisted.
- Modify `local/settings_window.py`: expose Tasker message notification style under the position/Tasker section.
- Modify `local/app.py`: load new persisted settings.
- Modify `tests/test_island_stack_visual_messages.py`: behavior tests for contact-only recognition, two slot zones, selected-slot docking, material/light background, shrink-to-cover, and Tasker message styles.
- Modify `tests/test_mac_preferences_shell.py`: defaults and settings UI coverage.
- Modify `docs/taskbar-island/PRD.md`: add the refined Tasker slot/material/message rules.

## Task 1: Contact-Only Taskbar Recognition

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests:

```python
def test_taskbar_preview_requires_main_rect_contact_not_only_lower_third(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.finder_mode = False
    visual.manual_finder_available = False
    visual.location_mode = "free"
    visual.taskbar_recognition_threshold = 0.66
    taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
    floating = QRectF(760.0, 930.0, 360.0, 38.0)
    touching = QRectF(760.0, 1026.0, 360.0, 38.0)

    self.assertFalse(visual._should_show_taskbar_preview_for_rect(0.95, floating, taskbar))
    self.assertTrue(visual._should_show_taskbar_preview_for_rect(0.95, touching, taskbar))
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_taskbar_preview_requires_main_rect_contact_not_only_lower_third -v
```

Expected: fail because the helper does not exist.

- [ ] **Step 3: Implement minimal contact gating**

Add `_taskbar_contacting_rect()` and `_should_show_taskbar_preview_for_rect()`; route drag preview through the current main island global rect instead of pointer ratio alone.

- [ ] **Step 4: Run test and verify GREEN**

Run the same command. Expected: pass.

## Task 2: Left/Right Available Taskbar Slots

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing slot tests**

Add tests that a taskbar with occupied Start/search/center/tray rectangles yields two available zones and that docking chooses the zone nearest the drag point.

- [ ] **Step 2: Run and verify RED**

Run the two new tests; expected helper methods missing or returning only one centered zone.

- [ ] **Step 3: Implement slot segmentation**

Add `_taskbar_available_slot_rects()`, `_taskbar_drop_zones_for_screen()`, and `_select_taskbar_drop_zone()`. Keep `_taskbar_drop_zone_for_screen()` as a compatibility wrapper returning the selected/first zone.

- [ ] **Step 4: Update overlay and docking**

Make `TaskbarDropOverlay` draw multiple zones. Store `taskbar_selected_drop_zone` during drag and use it in `_dock_to_taskbar_task_area()`.

- [ ] **Step 5: Run focused tests**

Run the slot tests plus existing Tasker tests. Expected: all pass.

## Task 3: Taskbar Material And Congestion Adaptation

**Files:**
- Modify: `local/island_stack_visual.py`
- Modify: `local/settings_store.py`
- Modify: `local/settings_window.py`
- Modify: `local/app.py`
- Test: `tests/test_island_stack_visual_messages.py`
- Test: `tests/test_mac_preferences_shell.py`

- [ ] **Step 1: Write failing material tests**

Cover: bright taskbar material classifies as light, Tasker can use pure white music background, Tasker lyric text switches to dark-capable colors, and congested slots shrink music to cover-only width.

- [ ] **Step 2: Run and verify RED**

Run the new tests. Expected: missing style/default/helper behavior.

- [ ] **Step 3: Implement material classification**

Add taskbar material sampling/classification helpers and a `pure_white` music ambience style. In Tasker, use light background when material is light or the user selects pure white.

- [ ] **Step 4: Implement congestion shrink**

If selected slot width cannot hold the normal compact music width, return a cover-only Tasker music size bounded by taskbar height.

- [ ] **Step 5: Run focused tests**

Expected: material and shrink tests pass.

## Task 4: Tasker Message Notification Style

**Files:**
- Modify: `local/island_stack_visual.py`
- Modify: `local/settings_store.py`
- Modify: `local/settings_window.py`
- Modify: `local/app.py`
- Test: `tests/test_island_stack_visual_messages.py`
- Test: `tests/test_mac_preferences_shell.py`

- [ ] **Step 1: Write failing message-style tests**

Cover: Tasker folded messages use side strips or blocks, user style is persisted, blocks collapse to rails when count/available capacity is exceeded, and notification count is capped by taskbar bounds.

- [ ] **Step 2: Run and verify RED**

Run the new focused tests. Expected: current messages are dot circles and no setting exists.

- [ ] **Step 3: Implement message style defaults and setters**

Add `v2_taskbar_message_compact_style` default `strip`, setter, snapshot, app loading, and settings combo.

- [ ] **Step 4: Implement Tasker message rects and painting**

Route `_message_dot_rect()` through a Tasker-specific helper that returns strip/block geometry. Add `_taskbar_message_effective_style()` to collapse blocks to strips when count or available width is too high.

- [ ] **Step 5: Run focused tests**

Expected: message-style tests pass.

## Task 5: Verification

**Files:**
- No production files unless verification finds a gap.

- [ ] **Step 1: Focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages tests.test_mac_preferences_shell -v
```

- [ ] **Step 2: Full suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover tests -v
```

- [ ] **Step 3: Diff hygiene**

Run:

```powershell
git diff --check
```

## Self-Review

- Spec coverage: contact-only Tasker activation, left/right slots, light taskbar material, pure white music background, black lyric readiness, congestion shrink, taskbar message strip/block style, block-to-strip overflow, and settings persistence are covered.
- Placeholder scan: no `TBD`, `TODO`, or "implement later" placeholders.
- Type consistency: new setting key is `v2_taskbar_message_compact_style`; Tasker slot helpers consistently use `QRectF`.
