# Music surface commit A — t173 / t168 / t171

Source-only work on `grok/capture-narrow-v4`, based on
`dde98b2a7d5243d986f959c50450fbb306a223f8`. No version or packaging change.
The parent owns PC synchronization, restart and physical acceptance. No PC app
or NetEase launch/restart, push, main-branch operation or seek change occurred.

## Measured problem and cause

The parent supplied this PC baseline before the fix (2026-11-03): tip
`dde98b2`, launcher PID 22252, app PID 31344, CDP 4422 owned by
`cloudmusic.exe` PID 28292. Eight one-second playback samples on 12 logical
cores consumed **0.906–1.109 CPU seconds/second**, approximately **90–111% of
one core**, with **450–455 MB** working set. Saved animation and lyric FPS
were both **165**, layout `lyrics`, system capture enabled. Failed-seek
`unverified` / `_cef_seek_enabled=False` / `cef-seek-disabled` flapping was
observed separately and remains outside commit A.

The Box reproduced the saturation at an ordinary 60 Hz display cap too.
Profiling attributed **3.721 of 4.144 seconds** to
`BackgroundEdgeVeil.prepare()`, versus **0.138 seconds** in visible painting.
The changing album-flow cache key caused approximately ten preparations each
second. Every preparation enlarged a small backdrop, calculated eight blur
levels, cropped/blended individual rows and rebuilt thousands of Python LUT
entries. Long GUI ticks, rather than paint cost alone, explained the stalls.
No stuck resize skeleton was present in the measured steady state.

The island's hidden 6 ms timer and simultaneous 16 ms surface / 40 ms study
timers were secondary sources of redundant work. The study timer throttled
surface update requests into whole-window paints but did not stop the other
clocks or their preparation work.

## Implementation

- Keep the continuous eight-level veil and transparent sharp core. Never
  upscale its source before filtering; bound the overlay to 256 pixels on
  its longest side. Cache the geometry-dependent masks/LUTs and blend/mask
  pixels in NumPy, already a dependency. Skip unused chrome backdrop capture
  while chrome is fully hidden. The album-flow background remains animated.
- Live narrow and release surfaces use the existing surface timer at 33 ms
  for preparation and presentation. Their study timer stays stopped. Trace
  sampling follows the active timer. The hidden island keeps a 33 ms owner
  clock, preserving provider refresh and lyric state; the saved 165 Hz choice
  returns with island presentation. This bounds live wide presentation too.
- Store the user's layout separately from a temporary effective layout.
  Resolved absent/placeholder lyrics in `lyrics` or `cover_lyrics` animate
  through the existing content snapshots to `cover`. Pending lookup or an
  unfinished loading cycle cannot trigger that override. A new pending song,
  a same-song retry, or available lyrics restore the saved choice. One sung
  row is sufficient to avoid the temporary override. No temporary path calls
  the settings setter. Existing sparse-song cover centering is retained.
- Keep t151 glass groups and button styles. Narrow transport uses a 24 px
  bottom margin (center Y 504 at 433×550; Y 462 at 391×508). Header/title and
  lyric ink have 24 px clearance; lyrics stop 20 px before transport. Wide
  transport also uses its existing bottom safe area. Countdown markers paint
  only within their real countdown interval. Source `(x3)`/`(×3)` annotations
  keep their existing styling and cannot slide upward through primary ink.

## Reproducible Box evidence

`handoff/t168/perf_probe.py` is an opt-in bounded wrapper, with no installed
runtime hook. It measures inclusive Qt callback times and whole-process CPU
for at least three seconds, after warmup, reports timer states and existing
paint/commit/veil counters, then removes its instrumentation. Fixture mode
uses the provider boundary, a private temporary settings/cache profile and
no audio/network. It simulates the island's 165 Hz cap while Qt's offscreen
screen remains 60 Hz. This is neither compositor FPS nor live-player evidence.

Identical 433×550 / DPR 1, 3 s warmup + approximately 5 s sample, permanently
revealed chrome, no tests running during the sample:

| Metric | Before `dde98b2` | Commit A |
| --- | ---: | ---: |
| Sample wall time | 5.001 s | 5.117 s |
| Whole-process CPU, one core | 78.19% | 40.45% |
| Visible paints | 121 | 155 |
| Paint p50 / p95 / max | 3.152 / 5.609 / 6.881 ms | 3.748 / 5.092 / 8.185 ms |
| Narrow tick p50 / p95 / max | 6.871 / 68.029 / 108.519 ms | 5.819 / 17.690 / 22.038 ms |
| Veil refresh p95 | 64.084 ms | 13.191 ms |
| Veil refresh inclusive total | 2590.258 ms | 499.505 ms |
| Veil prepares | 41 | 39 |
| Active island / surface / study interval | 6 / 16 / 40 ms | 33 / 33 / stopped |

CPU falls **48.3%**, tick p95 falls **74.0%**, and paint p95 stays well below
both a 60 Hz frame budget and the new 33 ms presentation period. The largest
removed stalls were in preparation callbacks; painting was already cheaper.

Additional final samples: 391×508 revealed chrome uses **40.38%** of one core,
paint p95 **5.104 ms**, tick p95 **23.623 ms**. At 433×550 with chrome hidden,
CPU is **32.83%**, paint p95 **5.660 ms**, tick p95 **13.574 ms**, with 155 paints
and 39 veil preparations over 5.100 s. Hidden and revealed samples are distinct
scenarios. Ambient field rendering and visible glass preparation still cost
CPU; this change does not claim 60/165 FPS music presentation.

Raw JSON, offscreen PNGs at 433×550 / 391×508 / 1100×640, temporary-cover
transition frames, restored-layout proof and command logs are outside Git in
`/workspace/gluspot/handoff/t168/`. Generated runtime artifacts stay untracked.

## Verification and handoff

Verification results and the final SHA are recorded in the external
`/workspace/gluspot/handoff/t168/A_FINAL.md` after the single commit is created.
Focused coverage includes pending/empty/restored layout and unchanged saved
settings, single-clock lifecycle, native paint cadence, continuous veil
properties at both DPRs, narrow geometry and repeat/countdown rendering.

Final focused runs pass **170 core cases + 23 narrow cases at DPR 1**, and
**191 cases at DPR 2**. The DPR 2 selection excludes exactly the two first-wide
display/roundtrip geometry cases independently reproduced on baseline: the
offscreen screen clamps their requested wide geometry. An earlier combined
run hit the narrow fixture's one-second asynchronous preparation deadline;
its complete module and the final DPR 2 run both pass without product changes.
The shared surface's **226 window cases pass**, excluding its one baseline
edge-resize method with seven failing subtests and one error.

Inherited failures have been checked against an untouched archive of
`dde98b2`: fullscreen edge-resize subtests have seven failures plus one missing
fit-state error. The historical `_demos.narrow3.check_live` harness has eight
failures/two errors before this change, including outdated mounted-lyric/fade
expectations. Complete suite status must be read from the handoff; these are
not silently treated as passes.

Ordinary frozen-source discovery was attempted on both baseline and commit A;
both processes ended **143 (`Terminated`)** without a summary or report. No
agent sent a termination signal; the cause is unknown. Their partial logs
contain 3,136 common test statuses with no changes, which establishes only
that observed prefix. An earlier 5,935-case discovery finished with 31
failures/48 errors/31 skips, but product source changed during that run and
source-inspection tests became unreliable; it is retained as investigation
evidence and is not final-source validation. No full-suite pass or complete
baseline parity is claimed.

Parent PC remeasurement after its approved restart, from the synced checkout:

```powershell
E:\gpt-island\.venv\Scripts\python.exe handoff\t168\perf_probe.py --repo . --live --output E:\grok-gluspot\pc-t168-after.json
```

Open the music surface, play steadily and keep the same size/DPR/chrome state
for at least eight seconds. The wrapper starts GluSpot normally and never
restarts NetEase; close the prior GluSpot instance as part of the parent's
restart procedure. `--live --narrow-live` selects the existing attach-only
entry if needed. The tool writes only callback/timer/count/CPU data and its
requested report; normal app behavior/profile remains owned by the app.

Repeat the parent's 8×1 s app-PID CPU/working-set sample separately. The PC
target is below **40% of one core** with hidden chrome, below **50%** with
revealed chrome at these narrow DPR 1 sizes, and paint p95 comfortably below
16.7 ms with no approximately 60–100 ms preparation stalls. These are targets,
not measured PC results. Verify real no-lyrics/loading/next-track restoration,
391×508 title/repeat spacing, reveal/resize glass continuity and higher DPI on
the PC. Toast, seek, blur-on-scroll, owned playback and login belong to B.
