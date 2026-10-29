# Capture wrap and narrow integration — 2026-10-29

This is source-only experimental work. It is **not ready for native acceptance**:
the current session has no PC connector, so fresh PC capture tests, NetEase
End/auto-next and real-playback screenshots are unverified. No push, package or
version change was made.

## Branch decision

Keep both input branches intact and integrate on **`grok/capture-narrow-v4`**, in
`/workspace/gluspot/grok-integrate4`. It starts from capture `fbe1afa`, merges
narrow-v3 `53f2b84` in `bb102ae`, and merges the capture audit `a9408ad` in
`895fd0d`. This preserves the accepted capture behavior and the narrow study's
history without rebasing either branch or touching `grok/main`.

`grok/capture` ends at **`a9408ad`**, a documentation-only wrap of the accepted
`fbe1afa` runtime. Wuju confirmed precise progress, NetEase's own seek and
GluSpot's word-click seek ("效果很棒"). That acceptance does not establish
End/auto-next or draggable GluSpot playback progress.

## Capture progress target

The actual island and `MusicSurfaceWindow` were audited before editing an input
interface. Neither has a playback progress bar at this revision.
`MusicSurfaceWindow._paint_media_controls` paints previous/play-pause/next;
the island has no timeline hit target. `LibraryProgress` is display-only and is
explicitly excluded by the task. No drag control was added without a confirmed
target. A clarification asking whether to add a non-narrow seek strip or identify
another existing control is still pending.

An approved future progress control should use the same seconds-based
`provider.seek_playback_position(..., expected_track_id=...)` route. Captured
NetEase continues through `CaptureRuntime` and `CefSeekBackend`; owned playback
retains its existing provider route. Source/track replacement must invalidate
the gesture. No progress bar is present in the narrow study.

## Real provider narrow entry

From the integration checkout on Windows:

```powershell
E:\gpt-island\.venv\Scripts\python.exe -X utf8 local/main.py --narrow-live
# Explicitly opt into the approved capture/relaunch route:
E:\gpt-island\.venv\Scripts\python.exe -X utf8 local/main.py --narrow-live --netease-relaunch --grab E:\grok-gluspot\_logs\narrow4-real
```

This source-only entry leaves ordinary application startup unchanged. It uses
`WindowsMediaProvider` and `IslandStackVisual` with in-memory settings and private
temporary lyric/art cache overrides. It never constructs `SettingsManager` or
loads/saves account settings. Default launch follows external SMTC playback;
the explicit relaunch flag enables the existing capture stack. The launcher
does not construct an owned player, so external sources without a queue or
collection/DJ authority do not acquire fixture capabilities. An owner injected
with the existing owned provider can expose those existing capabilities.

The narrow surface reads prepared source lyrics, genuine word units, metadata,
artwork and the production stable lyric clock. Wrapped primary/translation/aside
blocks use production layout and reading helpers; no synthetic clock increment
is applied. Painted word geometry and a painted model signature fence clicks
against source changes and same-track timing/translation enrichment. Word clicks
reuse `_seek_to_lyric`, subtract the lyric offset once, and retain the provider's
expected-track fence. Queue selection retains its existing generation token.

Idle lyrics/list fill the same viewport as the revealed state. Pointer activity
reveals overlay header/controls, then expires while the pointer stays still.
Top/bottom blur and dim increase without moving content. Three equal 44px
transport slots sit above the lower utility row; the DJ-dependent utility shift
is retained. Hidden/minimized live windows stop their study timer; the production
16ms and status timers stay stopped. No narrow progress bar was introduced.

`--grab` waits up to 40 seconds for playing media with a positive duration, a real
source, a nonempty source lyric model and a nonempty position source. Explicit
capture mode additionally requires `capture-model`. It then grabs the idle and
mouse-revealed native Qt window and records source/track/timing/opacity metadata.
Empty/dependency fallback screens do not become real evidence. This path has
only had a Linux empty-source launch smoke check; native evidence remains due.

## Fade CPU and its limits

The most-recent veil-key shortcut avoids repeatedly hashing nested source keys;
the six-entry atlas bound is retained. During fixture and paused-provider fades, two opaque full
background/content endpoints are prepared outside paint and blended at the
existing 33ms cadence. Paused current-row ink is reused at its exact source
position. Two 360×640 DPR2 endpoint images use **7,372,800 bytes**, in addition to
the existing background/control caches, and are released when the fade settles.
Advancing live lyrics keep their variable-height source reading current and
do **not** use those fixed endpoint images. Paused live lyrics include their
exact current block in the endpoints; source/content changes invalidate them.
Do not transfer the fixture CPU claim to active real playback.

Final `check_veil.py --frames 180` results (Linux Qt offscreen, 180 paint samples
per mode and scale; CPU is one 300ms paused-fixture fade at 33ms cadence):

| Metric | DPR1 lyrics | DPR1 list | DPR2 lyrics | DPR2 list |
| --- | ---: | ---: | ---: | ---: |
| Fade process CPU | 5.19% | 5.02% | 12.27% | 10.07% |
| Fade paint p95 / p99 (ms) | 0.58 / 0.94 | 0.50 / 0.66 | 0.98 / 1.23 | 1.02 / 1.33 |
| Advancing-clock steady paint p95 (ms) | 6.94 | 5.58 | 8.02 | 5.94 |
| Advancing-clock fade paint p95 (ms) | 1.34 | 0.53 | 1.70 | 0.89 |

Both DPRs now assert the existing **CPU ≤15%** requirement, rather than warning
for DPR2. Pixel/cache assertions passed: unchanged clear middle, zero content
motion, actual lyric-scroll hit rate **95.65%**. Atlas storage remains six
entries, **1,823,040 / 2,730,240 bytes** at DPR1/2. Raw veil-only 60Hz stress is
still **20.38% / 19.38%** at DPR2 lyrics/list; it is reported separately and has
not been presented as a pass at 15%. Native Windows CPU, active real-provider
fade CPU and physical-display smoothness remain unverified. A separate DPR2
48-line provider-fixture probe through `live=True` improved from **28.21 /
25.27 / 26.10%** to **14.49 / 8.84 / 8.82%** over three paused fades after caching unchanged
painted hit geometry. Intermediate attempts (**18.00%**, then **16.24%** first
transitions) are retained alongside the final `live-fixture-fade-hit-cache.log`.
This passes the paused provider-fixture probe, not active real playback or PC
acceptance. Hit geometry is invalidated by source/model/scroll changes and by
returning from the wide renderer.

Evidence: `/workspace/gluspot/grok/final4/check-veil.log` and
`veil/veil_acceptance_dpr{1,2}.json`. Historical final3 numbers remain in the
[study README](../_demos/narrow3/README.md).

## Verification and artifacts

- Capture discovery on Box: **226 tests ×3**, all passed in **11.534 / 11.589 /
  11.713 seconds**. Focused lyric seek/capture integration/NetEase UI: **25 /
  1.347 seconds**, passed. Runtime is unchanged by `a9408ad`.
- Integration full discovery: **5,691 / 168.043 seconds**, **17 failures, 11
  errors, 31 skips**. All 28 failure/error identities occur in the saved capture
  baseline; there are no new identities. Nine former conversion failures were
  not observed in this environment; no conversion code changed. This is not a
  clean full-suite pass. Exact comparison: `final4/full-comparison.json`.
- Focused checks passed: **20 live-provider fixture tests / 2.731 seconds**,
  **5 input tests / 1.450 seconds**, **25 layout assertions**, and **130 glass
  tests / 4.263 seconds**. The live tests include hidden/minimized refresh
  suspension, final-row veil alignment, paused-source endpoint authority and
  painted-hit cache invalidation. All six changed/new Python files compile
  without bytecode output; staged whitespace checks pass.
- Synthetic design previews are in `/workspace/gluspot/grok/final4/fixtures/`.
  They are **fixtures**, not real playback screenshots.
- Requested real playback `narrow_A_idle.png`, `narrow_B_mouse_moved.png` and
  `live-evidence.json` belong in `/workspace/gluspot/grok/final4/real/` after PC
  execution and transfer. They have not been produced in this session.

PC handoff scripts and bundles are kept outside source control in
`/workspace/gluspot/handoff/capture-narrow4/`. `sync-and-test.ps1` guards a clean
capture branch and fast-forwards only to the exact expected bundle tip before
capture tests ×3. `grab-integration.ps1` requires the exact clean capture tip,
creates a dedicated integration worktree, runs focused checks and requests real
native screenshots. PowerShell is unavailable on Box: target-side parser review
and execution are explicitly pending. Neither script writes player-install files
or account settings. End/auto-next still needs a separate live check.

Process evidence starts at 100: interfaces were read, provider/renderer paths
were reused, and verification results and limits are disclosed. Readiness is
withheld while the progress target and requested PC evidence remain unresolved;
the numeric process discipline does not substitute for those acceptance gates.
