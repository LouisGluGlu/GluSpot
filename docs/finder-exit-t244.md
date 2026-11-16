# Finder exit frame recovery — t244

Exiting MyDockFinder now restores one live GluSpot island immediately, including an exit during expansion, lyric-width growth, owner hiding, or rapid Finder/Free reversals. The expanded state is retained. Finder placement snapshots, queued geometry, visibility/material/DJ reveal animation and old shell velocities are retired on exit; the shared media/lyric clocks, personal-radio controls and track/artwork transitions remain independent.

## Reproduced causes

The starting source is `81c1b2d7966859fc3bcb15c1c7598efd484ee184`. Line references in this paragraph refer to that snapshot:

- `local/island_stack_visual.py:4711` captures `self.grab()` on a Finder placement change. `set_location_mode():5087` requests the capture and `:5099` preserves it when successful. `_paint_surface_transition():4759` paints the captured island over the new live island. A rapid reversal captures the previous fading composite again, producing several visible shells. The snapshot is painted after the current-shell mask calculation at `:11351`, so the extra ink is absent from that calculation.
- `_reset_location_runtime_state():4790` does not invalidate `_queued_island_frame`. The queued-frame identity check at `:11339` accepts a Finder frame after switching to Free when the same motion objects survive. A hidden-owner regression isolates this defect without any screenshot capture. The same reset leaves the bar visibility spring and old Finder shell velocities alive.

`f06b927` already added unconditional `CompositionMode_Source` clearing (`:11355`) and accurate spring-pose masks. The ghost is repainted by the screenshot overlay after clearing; adding another transparent fill would not repair it. That commit also introduced queued geometry without invalidating it at the mode boundary. `bee947a` suppresses the legacy unavailable-Finder music cradle; it does not cancel captured composites or queued frames. Both commits' mask/cradle fixes are retained.

The correction is confined to `local/island_stack_visual.py`: `_begin_surface_transition():4707` declines screenshot overlays for Finder/standalone handoffs; `_reset_location_runtime_state():4796` invalidates queued geometry and retires Finder placement state; `set_location_mode():5183` reuses `_settle_island_return_motion()` to publish the destination at rest. The existing paint path still clears damaged translucent pixels with Source composition.

## Mask contract

Every masked Finder frame uses the actual painted shell plus the existing two-unit antialias margin, rounded outward for the native region. Free restores the existing empty native mask, as required by the standalone policy in `local/island_visual_stack.py:398` and Free drag tests. The exit does not retain a Finder region at the old origin. The reused raster has no ink outside the current shell's antialias bounds after exit. This preserves the established Free interaction policy rather than introducing a mask dependent on placement history.

## Box evidence

All runs use `/workspace/gluspot/qtvenv/bin/python` with `QT_QPA_PLATFORM=offscreen`. Evidence is external at `/workspace/gluspot/handoff/t244/`.

- `compare.png` pairs real production exit frames from the starting source and `9966847`. `render_exit.py` uses actual visible `QWidget.grab()` and the same persistent QImage across frames, with no fabricated ghost overlay. Four exit scenarios each cover 31 frames. DPR 1 falls from 53 dirty frames / 21,052 maximum stray alpha pixels to zero; DPR 2 falls from 54 / 85,322 to zero. Per-frame data and images are in `before`, `after`, `before-dpr2` and `after-dpr2`.
- Five regressions in `tests/test_finder_exit_t244.py` cover mid-expansion exit, eight rapid enter/exit cycles, expanded exit, owner-hide exit and hidden compact-width exit. The starting source records eight failing DPR subcases across the five tests; the fix passes all five. Finder frames assert tight mask equality; restored Free frames assert the native mask is empty. Expanded and rapid-cycle persistent renders exercise DPR 1/2.
- The unchanged t227 `mask_frames.py` passes 330 frames each at DPR 1/2 with Finder available and unavailable: zero clipped shells, oversized masks, stale ink, group cradles or `clearMask()` calls during those masked sequences. Reports are `masks-{dpr1,dpr2}` and `masks-unavailable-{dpr1,dpr2}`.
- The requested final isolated suite covers 349 modules / 6,234 tests in 158.330 seconds, retaining exactly t234's 54 failure / 48 error / 32 skip identities and skip reasons, with zero new identities and unchanged source hashes. The two inherited native-incomplete modules (`test_owned_audio_bridge_handoff`, 18 cases; `test_owned_queue_artwork`, 17) were completely supplemented individually. `suite/summary.json` and `suite-comparison.json` record the complete coverage and comparison. This is baseline equivalence, not a green suite. The initial run exposed one state-only fixture's missing presentation stub; `0966a7e` corrects that fixture and the final full run verifies it.

This is Box raster, state-machine and emulated native-region evidence. It does not establish native Windows compositor behavior or a PC verification. No version/build, push, PC operation, provider change, button style or glass change. Process score: 100/100 after verification; the inherited full-suite issues remain explicit.
