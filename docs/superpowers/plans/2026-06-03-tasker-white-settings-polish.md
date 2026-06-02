# Tasker White Settings Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Tasker drop-frame magnet behavior, move the resize rail outside the island with hover visibility, harden Tasker bottom-bound dragging, complete pure-white text adaptation, and make the settings shell truly rounded with traffic-light symbols.

**Architecture:** Keep Tasker geometry in `local/island_stack_visual.py`, music color semantics in the existing music painters, and settings shell behavior in `local/settings_window.py`/`local/mac_style.py`. Add focused unittest coverage for each changed behavior before production edits.

**Tech Stack:** Python 3, PySide6, unittest.

---

## File Structure

- Modify `local/island_stack_visual.py`: no visible taskbar drop overlay, no preview-zone magnetic docking, external hover-only Tasker width rail, bottom-bound clamp, pure-white text/highlight rules.
- Modify `local/settings_window.py`: transparent top-level dialog and rounded shell container.
- Modify `local/mac_style.py`: rounded shell styling and traffic-light hover symbols.
- Modify `tests/test_island_stack_visual_messages.py`: behavior tests for no overlay, no magnet, rail geometry/visibility, Tasker clamp, pure-white text/fill.
- Modify `tests/test_mac_preferences_shell.py`: source tests for rounded shell and traffic-light symbols.
- Modify `docs/taskbar-island/PRD.md`: document the new no-prompt/no-magnet and white style rules.

## Task 1: Remove Drop Prompt And Magnet

- [ ] Write failing tests that `_sync_taskbar_drop_overlay` never creates/shows `TaskbarDropOverlay`.
- [ ] Write failing tests that docking uses current drag/probe x instead of selected zone center.
- [ ] Implement no-op overlay sync and probe-based Tasker docking.
- [ ] Run focused tests.

## Task 2: External Hover Resize Rail And Tasker Clamp

- [ ] Write failing tests that the left rail is outside the main rect, thinner, and only painted/hit-tested while hovering or dragging.
- [ ] Write failing tests that Tasker dragging clamps to screen/taskbar bottom bounds when expanded.
- [ ] Implement rail geometry/visibility and shared Tasker position clamp.
- [ ] Run focused tests.

## Task 3: Pure White Music Text Rules

- [ ] Write failing tests that expanded title/artist, album island title/artist, GELV rows, and live lyric chunks use black text on pure white.
- [ ] Write failing tests that pure-white wave-stream color fill is disabled and the light wave/highlight layer is skipped.
- [ ] Implement text color helpers and pure-white color-fill/highlight guards.
- [ ] Run focused tests.

## Task 4: Rounded Settings Shell

- [ ] Write failing tests that settings use `WA_TranslucentBackground`, a rounded `MacWindowShell`, and traffic-light hover symbols.
- [ ] Implement shell wrapper and traffic-light button painting.
- [ ] Run settings tests.

## Task 5: Verification

- [ ] Run `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages tests.test_mac_preferences_shell -v`.
- [ ] Run `.\.venv\Scripts\python.exe -m unittest discover tests -v`.
- [ ] Run `git diff --check`.

## Self-Review

- Spec coverage: covers all latest user points.
- Placeholder scan: no placeholders.
- Type consistency: Tasker geometry remains `QRectF`/`QPoint`, settings shell uses existing PySide6 widgets.
