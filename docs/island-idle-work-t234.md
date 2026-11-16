# t234 — personal-radio regressions and idle island work

This source-only pass continues `b1095df` on `grok/capture-narrow-v4`.
All Qt execution uses Box, `QT_QPA_PLATFORM=offscreen` and
`/workspace/gluspot/qtvenv/bin/python`. No PC operation, push, version/build,
provider-interface change or button/glass restyle is included.

## Two suite regressions

Both failures introduced by `b1095df` were stale fixtures or expectations:

- The Tasker narration test required an empty cover hitbox, contradicting the
  README's intended ring entry to the player window/fullscreen. It now checks
  the live hit target within the bar, while retaining the material, single-row
  metadata, no lyric viewport and no Finder reveal assertions.
- The cover motion/parent clip test used a `SimpleNamespace` without the
  extracted `_paint_music_cover_presentation_overlay` helper. It now binds the
  production helper and checks retained painter clip/transform as well.

All 31 tests across the two affected modules and
`tests.test_island_personal_radio_controls` pass, including all ten radio tests.
The actual radio behavior remains the one documented by `b1095df`: collapsed
rings open the existing player surface; manual expansion retains standard
Play/Pause and Next, with Previous only when the existing owned FM queue has
ordinary-song history. No unsupported QQ/Kugou radio or external-player
capabilities are inferred.

## Stable lyric geometry

`_music_lyric_measurement_lines` keeps four immutable text results, returning a
fresh list to callers. Its exact value key includes passed and current raw
source contents, prepared cleaned rows, artist, translation policy, expansion
and the current chunk limit. Reading the prepared result before looking up the
key preserves pending/ready/failed transitions, compatible previous results
and in-place lyric edits. No persistent lyric cache or source row changes.

The existing representative-width cache owns font-dependent measurements. Its
key now includes the actual font description, DPR and logical DPI; a changed
device metric context clears the inner layout and representative caches.
Width arithmetic and the 72nd-percentile selection remain unchanged. Four
geometry fixtures now initialize their QWidget measurement device; their
width expectations are unchanged.

Eight new tests cover exact float-width agreement with the original algorithm
across compact/expanded poses and settings, content/artist/translation edits,
worker completion/failure, caller mutation, bounded storage, repeated-song
work and font/device metric invalidation. The unfinished t234 draft supplied
the first four cases and was reviewed before reuse.

## Hidden owner clock

The owner keeps a runtime-only tick at up to 100 ms while hidden/minimized or
while a visible navigation page owns presentation. It wakes sooner at existing
provider deadlines (including 120/180/650 ms intervals). Theme/provider state,
DJ completion consumption, scene state and timed/fallback lyric progress
continue; hidden hover, wave, geometry, topmost and paint work stop. Showing
the island immediately restarts the selected frame cadence and rebases its
animation delta.

An explicit native taskbar-follow hide retains its recovery probe, guarded
against navigation/tray/quit ownership. Native bar opacity/position transitions
keep their visible-widget frame path. The library child retains its separate
33 ms presentation clock. Eight new tests cover these boundaries. The existing
narrow cadence test now expects the hidden runtime rate and explicitly shows
the island before asserting restoration of the saved 165 Hz setting.

## Verification and evidence

The requested isolated runner completed 340 modules / 6,171 tests, including
individual-process supplements for two incomplete modules. Its 54 failure,
48 error and 32 skip identities exactly match the t225 round-two baseline;
there are zero new identities and no changed skip reasons. Python source
hashes stayed unchanged during the run. This is baseline equivalence, not a
green suite or release acceptance.

Evidence is retained outside source control at
`/workspace/gluspot/handoff/t234/`: `radio-regressions.md`,
`radio-capabilities.md`, `radio-3x2.png`, `suite-final/summary.json`,
`suite-final-comparison.json`, `cpu-diagnosis.md` and `FINAL.md`.
The radio proof was regenerated through the final production painter and
visually inspected in all three backgrounds.

Paired profiles use the same 180 s warmup, 8 s settle and 20 s per case with
cProfile enabled. CPU is percent of one core; paint rates are Qt events, not
display FPS:

| Case | CPU before → after | Paint Hz before → after | Owner timer Hz before → after |
| --- | ---: | ---: | ---: |
| Wide player | 83.50 → 89.36 | 75.78 → 80.70 | 30.29 → 10.65 (hidden) |
| Collapsed island | 99.89 → 72.64 | 11.11 → 58.40 | 11.11 → 58.50 |
| Expanded island | 99.79 → 63.54 | 11.45 → 58.68 | 11.45 → 58.83 |
| Library footer | 41.46 → 34.68 | 31.80 → 31.75 | 58.84 → 10.65 (hidden) |

Collapsed/expanded geometry preparation falls from 81.51/77.52 ms to
4.75/1.43 ms per paint. Whole-song measurement falls from 17.15/17.52 s to
0.50/0.53 s per 20 s sample despite about five times as many paints. Both
hidden cases have 213 runtime ticks. The library footer retains its own
presentation cadence. Wide CPU rises 5.86 percentage points alongside more
background snapshots and paints; this single paired sample does not establish
a wide-mode CPU win. The worker renderer and full-window repaint scheduling
remain proposal-only; no image/motion/cadence tradeoff was made there. Detailed
hotspots, timer inventory, source hashes and ranked proposals are in the
companion handoff diagnosis.

Windows compositor, physical hover/window/fullscreen transitions, real account
streams and end-of-batch continuation, hardware/headset commands, high-DPI
desktops and native hidden-bar behavior remain **未验证**. This pass does not
establish native presentation FPS or long-session playback performance.
