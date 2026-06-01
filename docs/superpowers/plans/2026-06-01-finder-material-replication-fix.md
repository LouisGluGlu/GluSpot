# Finder Material Replication Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the severe Finder material regression where the fused background uses a wallpaper/full-screen crop. Finder fused backgrounds should replicate the visible upper Finder bar glass material and use that generated material texture for the cradle fill.

**Architecture:** Keep the fix inside the existing PySide6 hand-painted Finder path. `local/mydockfinder.py` owns material sampling and generated texture metadata. `local/island_stack_visual.py` continues to draw the Finder cradle from the provided material and must keep the music island's own opaque shell.

**Tech Stack:** Python 3, PySide6 `QImage`/`QPainter`, `unittest`.

---

## Mapping Points

- `sample_live_finder_bar()` may capture the visible top Finder bar to infer colors and generate a repeatable material texture.
- `sample_live_finder_bar()` must not store a full-screen wallpaper/screen image as `FinderGlassMaterial.texture_image`.
- Generated Finder material textures are local/repeatable textures, so `texture_is_full_screen` must remain `False`.
- Generated Finder material textures use already-composited, opaque sampled colors so the fused cradle does not reveal wallpaper a second time.
- The existing island painter may stretch this generated material texture into the fused cradle background.
- Finder-mode plugin side reminders keep their own semantic backgrounds. The replicated Finder outer frame expands to include side reminders so they sit on a shared material backdrop without replacing their own alert styling.

## Tasks

### Task 1: RED Tests

- [x] Add a failing test with a neutral Finder bar and saturated wallpaper below it, requiring `texture_image` to be a small low-saturation material texture rather than a full-screen crop.
- [x] Verify the test fails against the old implementation with `texture_is_full_screen=True`.
- [x] Add a failing assertion that generated Finder material texture pixels are opaque enough for background fill.

### Task 2: GREEN Implementation

- [x] Remove the full-screen capture texture path from `sample_live_finder_bar()`.
- [x] Keep the top-bar material texture generated from the Finder bar sample.
- [x] Mark generated Finder textures as non-full-screen textures.
- [x] Store generated material texture pixels as opaque already-composited colors.

### Task 3: Verification

- [x] Run the focused new Finder material test.
- [x] Run related Finder/island tests.
- [x] Run full test discovery.
- [x] Run `git diff --check`.
- [x] Generate and inspect a PySide visual check for Finder material replication.

### Task 4: Finder Outer Frame Covers Side Alerts

- [x] Add failing tests proving Finder-mode TIME and generic plugin side alerts keep their own backgrounds instead of using a material replacement hook.
- [x] Add a failing test proving the Finder outer frame expands to include plugin side-alert rects.
- [x] Remove the side-alert material replacement hook from TIME and generic plugin alert painting.
- [x] Include `PLUGIN_ALERT` visuals in the Finder outer-frame bounds calculation.
- [x] Run focused side-alert frame tests.
- [x] Run full verification and generate a visual check covering Finder outer frame expansion around side alerts.
