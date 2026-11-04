# t258 / t259 — artwork color timing and live glass backdrop

Box-only on `grok/capture-narrow-v4`, starting at `ff188dc`. The controls now
consume the same applied artwork/provider-color publication as the cover and
background, including prefetched handoff and same-track artwork upgrades.
Glass follows changing content behind its existing groups. Button geometry,
material/shader tokens, interaction, grouping and the t254/t256/t257 fixes are
preserved. No push, `grok/main`, PC, build or version operation occurred.

## Root causes and correction

Original references below are against **ff188dc**.

- `local/music_surface_window.py:591` consumed t243's `visualDataChanged`, but
  `:613` only advanced cover state for track/title/artist changes. Same-track
  originals waited for another paint/tick. At `:2414`, pending extraction
  retained the previous artwork palette. Accepted extraction at `:2469` only
  requested a later update. There is **no direct palette/play-state signal
  binding**: separate artwork completion plus cached presentation made a later
  play/progress update appear to trigger the correction.
- `_demos/narrow3/narrow_window.py:512` captured the backdrop before background
  painting; `:550` prepared control bands only after the content-key gate at
  `:540`. A changed snapshot at `:845` did not invalidate the displayed bands.
- The permanent glass freeze is the content-only renderer lifecycle:
  narrow `:1180` → shared `local/music_surface_window.py:7142` / `:7147` →
  `local/music_control_glass.py:430` replaces visible roles with an empty set.
  Cached narrow bands are composed afterward. Their next preparation at
  narrow `:1437` gets zero eligible refresh slots from renderer `:425`, and
  `:462` rejects every stale resting patch indefinitely.
- Adaptive tint read the raw background while refraction read composited
  content. Whole-image identities also refreshed patches for unrelated damage;
  resting band captures repeated every 100 ms even for identical pixels.
  The suspected t234 commits `678d3f3` and `48c68f9` change island measurement
  and hidden timer work only; neither introduced this chrome cache.

Current `local/music_surface_window.py:624` applies the artwork transition for
track, artwork/provenance and provider-color publications; accepted extraction
uses the same callback. Pending artwork uses the current provider tones through
the existing null-image palette helper. Extraction remains single-flight,
latest-pending and token guarded; old results cannot recolor the current track.
`_demos/narrow3/narrow_window.py:608` prepares the current background, composite
and control bands in that event, before a later media tick is needed.

Current narrow `:810` captures padded glass sampling regions through existing
cover/lyric/queue painters. Its normal cadence stays **10 Hz**, below the
requested 30 Hz ceiling. Artwork publication forces a single atomic material
refresh. Cached visible roles are restored before material preparation, removing
slot starvation. Tint/refraction share the same current or frozen composite;
renderer `local/music_control_glass.py:448` compares each padded source crop
before shading. Unchanged regions retain their material and static scenes stop
raster work. Existing width/resize freezes, visibility guards and two-slot
ambient scheduler remain; no new timer, provider polling or screen capture.

The existing **640 ms flip, 240 ms artwork-upgrade crossfade and 1.2 s background
field retarget** remain. They start from the same artwork event; controls sample
the displayed composite rather than a later playback event. Their existing
curves/durations were not replaced.

## Verification and evidence

- Fifteen new regressions cover paused publication before another clock,
  visible first/mid/end transition samples, real `PreparedAudio` handoff,
  same-track original upgrades, deferred/obsolete extraction, dirty-region
  sampling, static idling and the empty content-only renderer frame.
  Original HEAD fails all nine palette tests and five of six backdrop tests;
  the already-correct 10 Hz cap passes. Fixed focused groups pass. Peer reviews
  are `palette-peer-review.md` and `backdrop-peer-review.md`.
- `compare.png`: before/after flip and same-track upgrade with per-frame
  artwork-primary and actual shaded-control RGB swatches. RGB roles differ;
  this is not a false assertion that shaded glass equals an artwork swatch.
- `backdrop-strip.png`: before/after background/underlay/control samples over
  30 seconds, with playback paused at position 12 throughout. Paired raw
  reports contain **241 frames each**, actual 30 Hz product ticks and sampled
  3 Hz long-sequence rasters. The blue baseline has changing underlying pixels
  but utility patch version **2→2**; fixed **7→67**. All per-role shade intervals in the unchanged-track ambient sequences
  are at least 100 ms. `frame-verification.json` records assertions,
  source hashes, actual region dirtiness and raw raster paths.
- All **168 island rasters/geometry/reading calls** match t257, retaining the
  **138 non-radio expanded** baseline states. All **430 translation-toggle
  rasters** match t257 exactly, with **zero blank primary frames**. t254/t256
  render harnesses are reused; `preservation-verification.json` also binds
  unchanged lyric/island/provider/style sources and t257 restore methods.
- Full isolated suite: **356 modules / 6,292 tests / 178.024 s**; exactly t250's **52 failures / 48 errors / 32 skips**, **zero new or removed identities**. `suite-comparison.json` compares failure,
  error and skip identities with t250; baseline equivalence is not a green
  whole-suite claim. The runner supplements inherited native-incomplete
  modules individually and records unchanged source hashes. The release
  subclass outside its built-in fingerprint is checked separately.
- Initial full-run evidence is retained in `suite-initial/`. Its three new
  outcomes came from older tests requiring the previous pending palette and a
  failure mock that also rejected the null-image fallback. Final expectations
  follow the requested current-tone behavior while retaining single-worker,
  latest-pending, stale-result, shutdown/reopen and no-retry assertions.
- Python compilation and `git diff --check` pass.

## CPU

| Case | Before → after (% of one core) |
| --- | ---: |
| Animated 960×540, t234 idle / controls hidden | 38.57 → 35.60 |
| Animated 360×640, controls visible | 40.82 → 40.03 |
| Static 960×540, t234 idle / controls hidden | 15.54 → 15.76 |
| Static 360×640, controls visible | 14.06 → 14.10 |
| Animated 960×540, controls visible | 57.02 → 48.68 |

Raw reports, configuration/visibility state and sample deltas: `cpu-comparison.json`.

The t234 `profile_idle.py` harness is adapted only to instantiate the actual
release live responsive window, add 360×640 narrow and static cases, and keep
narrow chrome visible. Wide retains t234's normal control auto-hide policy;
the supplementary visible-wide measurement is listed separately. It retains
production owner/library/settings/timer inventories and an offline provider.
Primary cases use 180 s before / 300 s after warmup. The visible-wide pair uses
180 s warmup on both sources, pauses the processes before measurement, then
resumes them sequentially. Each case settles 8 s and samples 24 s. Heavy
verification does not overlap samples.
Animated samples decrease; static whole-process values rise by 0.211 and 0.043
percentage points. Both static sizes have **zero material refreshes, zero
backdrop preparations and zero changed snapshots** during the 24-second
measurement; the unpaired visible-wide static supplement measures 20.12%
and also has zero glass work.
These small whole-process deltas are reported, with no invented statistical
significance threshold or exact-zero CPU claim.

These are process CPU percentages of one core on Box/offscreen, with raw logs,
paint/timer inventories and source hashes. They establish observed sample
behavior, not native Windows compositor performance or statistical equivalence
on every machine. Static glass raster counters are reported separately from
whole-app polling/lyric work.

## Handoff

Runtime commits: `5336eaf`, `c31436a`, `1b030cb`; expectation update:
`8f2ba5a`. Final documentation commit and clean-tree/source binding are in
`final-state.json`.

All requested work is local. **PC 未验证.** No credential, account, audio-device,
network playback or physical input operation was used. Scoped process score:
**100/100 after corrections and final verification**; inherited suite failures
remain explicitly recorded.
