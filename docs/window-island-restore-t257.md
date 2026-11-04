# Narrow window → island → original window (t257)

The top-right island control now collapses the live narrow player directly to
the existing V2 island. Reopening preserves the player's normal geometry and
window/fullscreen preference, including a manually resized narrow window.

## Causes and correction

Line numbers below refer to starting HEAD `dd00911`, unless marked current.

- `_demos/narrow3/narrow_window.py:2477–2478` dispatched the live `return` action
  to `toggle_wide()`. That helper at line 2188 selects 960 × 540 or 360 × 640;
  it does not perform an island handoff. The release entry constructs this
  subclass with `live=True` in `local/island_stack_visual.py:7185`, so the demo
  dispatch affected the actual player. The subclass's `return_to_island()` at
  line 2208 already correctly delegated every live window to the production
  lifecycle. The fix calls that method from the live click handler (current
  line 2479); the standalone study's size toggles and explicit N/W keys retain
  their behavior. The live accessible label now says `返回岛模式`.
- `local/music_surface_window.py:1091–1092` correctly remembered the current
  normal rectangle, but left the shared library/login/player rectangle stale.
  `show_from_island()` at lines 703–705 then loaded that old
  `owner._music_page_geometry` over the remembered normal size. The library
  controller initializes this state at `local/music_library_controller.py:137`
  and publishes it at line 181; its player capture at line 189 runs during
  navigation rather than every player resize. A concrete offscreen reproduction
  resized a shared 960 × 540 player to 483 × 560, returned correctly to the
  island, then reopened as 960 × 540. Current lines 1093–1097 publish a copy of
  the remembered normal rectangle to the existing shared state before hiding.
  Fullscreen returns publish their retained normal size. Existing guards keep
  intermediate transition rectangles out of normal-size memory.

`_music_windowed_preference` distinguishes windowed from fullscreen; it does
not encode narrow/wide. `_sync_narrow_mode()` derives narrow presentation from
the actual geometry and the existing content/font breakpoint. The existing
overlay retains its selected content layout across hide/show. No additional
layout-mode state or restore interface is needed.

## Verification and evidence

`tests/test_music_island_restore_t257.py` adds seven regressions using the real
island owner, `EmptyIslandDataProvider`, release entry and actual Qt clicks:
repeated default/manual narrow and wide round trips, stale shared rectangles in
both width directions, fullscreen Escape/restore, and fullscreen return with a
resized normal window after library entry. Fullscreen's visible mode control
continues to switch to windowed; Escape with no library destination exercises
the applicable island-return route.

The seven new tests and the three updated narrow navigation/accessibility
modules pass all 29 focused tests. Old assertions that explicitly required the
reported widening now follow the requested island action. The existing
transition/default/compact modules also ran: all four failures and eleven
errors in that group are inherited t250 identities.

The requested full isolated runner covers 354 modules / 6,277 tests in 209.716 s,
with the exact same 52 failure / 48 error / 32 skip identities as t250: zero new
or removed identities. Three grouped modules hit native crashes
(`test_owned_audio_bridge_handoff`, `test_owned_queue_artwork`,
`test_owned_playback`); the runner completed all 18 / 17 / 77 individual tests
for those modules. Source hashes remained unchanged throughout the run; an
additional fingerprint covers the narrow subclass outside the runner's source
directories. This establishes baseline equivalence, not a green whole suite.

Preservation rerenders exactly match all 168 t254/t256 island states and all
430 t256 ordinary/rapid translation-toggle frames, with zero blank primary
frames. All 138 non-radio expanded island states also match b4601bd. Source
comparison limits runtime changes to the live dispatch, its accessible label,
and the shared rectangle publication; no painter method changed. Initial
collapse-comparison rasters can differ slightly because of asynchronous
palette/background preparation; `render-verification.json` records the
measured variation instead of claiming exact initial-frame parity.

Detailed commands and checks are in `/workspace/gluspot/handoff/t257/FINAL.md`.

The handoff directory contains:

- `compare.png`: baseline `dd00911` and fixed actual offscreen Qt renders of the
  highlighted control, island and restored window; unannotated rasters and
  machine-readable states are preserved alongside it.
- `transition-tests-before.log`: actual narrow click failures before the fix.
- `audit-restore-before.json` / `audit-restore-after.json`: the stale shared
  geometry reproduction, with exact normal/shared/restored rectangles.
- `focused-final.log`, `suite/summary.json`, `suite-comparison.json`: focused
  verification and the requested isolated-suite identity comparison to t250.
- The preservation reports and raw frames for t254 island states and t256
  ordinary/rapid translation toggles.

These checks run only on Box with `/workspace/gluspot/qtvenv/bin/python` and
`QT_QPA_PLATFORM=offscreen`. They establish settled Qt lifecycle and rendered
state; native Windows compositor and physical PC behavior remain 未验证. No
version/build, provider, button/glass styling, push or PC operation is included.
