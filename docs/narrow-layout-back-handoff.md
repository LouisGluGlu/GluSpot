# Narrow chrome, four modes and t137 back-slot handoff

Source-only follow-up, 2026-10-29. No version or packaging change.

## Behavior and cause

The reported PC failure occurs with `v2_owned_playback_enabled=False`:
`_library_back_available()` returns false, and the former narrow layout placed
window-mode `return` at `(16,16)`. Clicking where back used to be therefore
called `toggle_wide()` and resized the view to 960 × 540. It was a misplaced
window-mode target, not library navigation requesting a resize.

The chrome commit (`ca63388`) keeps `return` after translation on the right.
Only collection reclaims the missing library slot on the left. Paint and input
use the same `NarrowLayout` with the library and DJ gates. The follow-up gives
library navigation priority if targets overlap and covers the fixed top-left
PC click, hidden/empty chrome, login/library routing and intentional right-side
return. Missing back must never redirect a top-left click to window-mode.

Both narrow and wide views select the same four content layouts. Width changes
adapt geometry while retaining selection and settings. Narrow external lyrics
keep the mounted shared renderer; cover, cover lyrics and queue use the shared
surface content with narrow chrome. Queue artwork requests, current-row motion
and deferred/paused repaints retain their normal runtime behavior.

## Verification

Using `/workspace/gluspot/qtvenv/bin/python` with `QT_QPA_PLATFORM=offscreen`:

- 46 focused tests pass: `tests.test_narrow_back_pc_regression`,
  `tests.test_narrow_back_resize`, `tests.test_library_back_owned_playback`,
  `tests.test_narrow_chrome`, `tests.test_music_surface_layouts`,
  `tests.test_narrow_content_layouts`, `tests.test_narrow_shared_runtime`.
- All eight `tests.test_music_native_display_cache` tests pass after its fit
  assertion was updated to measure the unadapted side column. The adapted
  stacked column is no longer the input to the width-fit decision.
- Final-source `python -X faulthandler -m unittest discover tests -v` completes
  **5,792 tests / 169.997 s**, with **26 failures, 36 errors and 31 skips**.
  All 54 focused checks also pass within discovery. No clean full-suite pass
  is claimed; the unrelated failures are retained in the external full log.
- Baseline `ca63388` ordinary full discovery terminates twice in SIGSEGV in
  `test_artwork_sources.ArtworkVisualPriorityTests.test_1417_smtc_image_is_usable_without_new_search`.
  A separate baseline narrow run confirms the existing three v4
  settling/countdown failures and 25 live-lag harness errors. The baseline also
  has two owned-back assertion failures and a missing-callback fixture error;
  those back fixtures are corrected in this follow-up to use the actual gate.
  The baseline crashes prevent a complete failure-identity comparison.

Native Windows execution, real-player continuity and physical display
acceptance remain pending. Offscreen passes do not establish those results.

## PC handoff and remaining work

Keep the prepared scripts, final bundles/manifests and `back_t137_pc.md` under
`/workspace/gluspot/handoff/narrow-v4/`. `pc_layout_sync.py` verifies the exact
tip/hash and fast-forwards a clean existing mirror. The layout probe cycles
four modes at both widths. The back probe first checks owned=False at fixed
`(34,34)`, then the intentional right-side return, then owned=True library/login
navigation. Probes use temporary settings and attach to the existing NetEase;
they deny NetEase launch/restore and guard unrelated playback/collection actions.

TODO t140: implement animated cover/chrome changes with width hysteresis and
capture real PC drags in both directions. The external plan
`/workspace/gluspot/grok/narrow_layout_modes_t138_t139_plan.md` records this
deferral. Breakpoint animation is not part of this handoff.
