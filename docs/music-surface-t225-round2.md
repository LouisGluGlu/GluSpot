# t225 round 2 — evidence and limits

All five requested items have separate commits; this supplements the earlier
t225 rollback and t229 background notes. The inherited t229 cherry-pick's
README conflict was resolved retaining both histories and its 16 focused
checks passed. Product code remains Codex-authored. No version bump, build,
push, grok/main or PC operation.

## Finder masks and transparent pixels

At the base, `_set_expanded` cleared the native region and delayed restoring
it. Job J reserved the target/old/current rectangle union before paint and
held shrinkage through animation and pointer gates. The separate Finder
material cradle also painted outside the music pill.

The current spring pose and lyric snapshot are prepared before queuing a
native paint; that same frame paints inside its tight region. No target or
old region is retained. Transparent Source clearing replaces damaged pixels,
expanded music ink is clipped to its shell, and the live Finder path has no
second material cradle. The existing compact content ordering and marquee
remain. `GLUSPOT_MASK_DEBUG=1` prints timestamp, expanded state, window,
mask and painted bounds on every rendered frame.

57 focused checks passed. Persistent-buffer regression covers 330 frames at
DPR 1/2 with Finder available and unavailable (1,320 frames total), including
expansion, collapse, lyric growth/shrink and track change. A final independent
review reproduced the legacy cradle in the unavailable-bar fallback; that
current-runtime branch is also suppressed, retaining only dormant legacy
multi-item fixture behavior.
The original t217 normal and overflow harnesses each pass 240 frames with
zero shell/window/mask/cover/text clipping counters. Before/after native-region
emulation shows oversized mask frames 330→0 and outside-current-shell ink
frames 300→0 at each DPR. It deliberately retains one raster to test clearing;
it does not reproduce the native Windows black-fill symptom.

Evidence: `/workspace/gluspot/handoff/t227/before-after-strip.png`,
`frame-evidence.md`, `mask_frames.py`, `before/`, `after/`, `before-dpr2/`,
`after-dpr2/`, `t217-after/`, `t217-overflow-after/`, `focused-final.log`.
The original GIF is extracted separately in `user-input-strip.png`.

## Sea response

[Amplitude, reference phases, damage bounds and CPU evidence](island-sea-t225-round2.md)
records 83 focused checks, rendered peaks 0.339079 / 16.879722 / 19.033115 px
for quiet/vocal/six-frame onset, and five paired runs vs exact fee7410.
Median wave CPU ratios are .9703 / .9588 / .9974 / .9771 for steady DPR1/2
and track-reflection DPR1/2. These are offscreen CPU proxies.

Refreshed comparison: `/workspace/gluspot/handoff/t225/t225-compare.png`;
measurements: `proof-comparison-round2.json`, `cpu-comparison-round2.json`.

## Translation and backgrounds

Unset/empty/unknown translation selectors now resolve to CLASSIC. A/B/C
remain explicit `GLUSPOT_TRANSLATION_VARIANT` choices. 35 focused checks
passed; the old default-A annotation-gap expectation was restored to the
existing CLASSIC behavior. Bilingual narrow/wide/floating frames:
`/workspace/gluspot/handoff/t225/translation2/{variant-A,baseline}.png`.

Ordinary compact/expanded music already read the selected ambience. The
immersive capsule bypassed that plan and always painted the album-color
Finder background. It now reuses the existing selected background painters
and chooses matching contrast. The explicit transparent face remains
unbacked. 76 focused checks passed, including live switches on one instance,
Finder/Tasker and both system themes. Nine production render proofs:
`/workspace/gluspot/handoff/t225/styles/styles-3x3.png` plus individual
`{sea_level,solid_album,system}-{collapsed,expanded,finder}.png`.

## Lyric loader

A failed display-preparation job could retain the same track's cached loading
marker before reaching the terminal-failure fallback, keeping the seven-row
skeleton indefinitely despite ready raw timed lyrics. The ordering already
exists in ec89693; the evidence does not attribute it to Job J or translation A.
The correction skips only a failed cached loader; established lyric ink is
retained, and the existing unavailable fallback and exit cycle run.

`GLUSPOT_LYRIC_DEBUG=1` emits sanitized lookup begin/complete/publish state,
raw/display row counts, preparation active/pending/failed/closed, resize
capture/freeze/motion and skeleton exit reason. State transitions log
immediately; a stall repeats every five seconds. Track identifiers are hashed;
lyrics, titles, URLs, credentials and exception text are excluded.

70 focused checks passed. Injected failure with six ready raw/timed rows:
baseline lookup pending/skeleton visible after six seconds; corrected lookup
not pending/skeleton hidden. `/workspace/gluspot/handoff/t225/lyrics-failure-compare.png`
and `item5.md` document the fixture and attribution limits. The supplied PC
Keep Me Warm incident itself remains unresolved without live diagnostic evidence.

## Integrated verification

After cherry-picking, 126 island/style/mask checks and 45 translation/loader/
t229 lifecycle checks passed. Python compilation and diff whitespace checks pass.
All eight test modules adapted for default A in 395d6ed were restored
byte-for-byte to ec89693 CLASSIC expectations; explicit A/B/C tests remain.
113 final focused checks passed in 9.560 seconds. The first complete run
exposed 12 stale A assertions; its logs are retained separately.

The final requested module-isolated run covers **337 modules / 6,145 tests
in 153.337 seconds: 54 failures / 48 errors / 32 skips**, with **zero new or
removed failure/error/skip identities vs exact 395d6ed**. This is inherited
baseline equivalence, not a green whole suite. The same two native-incomplete
modules are individually supplemented by the requested runner. Normal GC and
private profiles remain enabled; tested source hashes stay unchanged.

Command: `/workspace/gluspot/qtvenv/bin/python
/workspace/gluspot/handoff/t202/run_full_isolated.py
/workspace/gluspot/grok-integrate4
/workspace/gluspot/handoff/t225/full-isolated-round2-final --workers 3`.
Evidence: `full-isolated-round2-final/summary.json`,
`full-identity-comparison-round2.json`, `source-bindings-round2.json` and
`/workspace/gluspot/handoff/t225/FINAL2.md` (external final handoff).

PC 未验证: native transparent-window/DWM behavior, actual Finder/Tasker settings
interaction, the actual owned NetEase payload/stall, Windows CPU and desktop
frame pacing. Offscreen fixtures do not certify these. No PC operation occurred.
