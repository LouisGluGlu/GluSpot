# Restrained sea amplitude and refined crest — t225

Amplitude superseded by [t225 round 2](island-sea-t225-round2.md); this page records the original rollback evidence.

Base: `395d6ed0` on `grok/capture-narrow-v4`. Codex wrote all product
changes. Version and packaging remain 6.417. This task changes only sea
amplitude, its crest rendering, related tests and this documentation.

## Behavior and boundaries

The expanded sea again uses the exact `ebe525e` drive,
`(.20 + 12.5 * ripple_level) * shape`. The 60/250 ms absolute peak/vocal
envelope and gated 1.4 vocal contribution remain unchanged. The t205
low-band onset boost is removed; the existing `low` keyword remains accepted
for call-site compatibility. Compact spectrum/travelling-wave displacement
is unchanged. The renderer again uses the original unclamped surface and
the original damage strip, from waterline minus 18 units over 58 units.

The crest has a continuous 1.0 logical-unit base and at most 1.2 extra
units on rising intervals. Its color mixes the existing sea pigment with
46% tinted specular light, at alpha 154–182/255. The existing graded
under-crest light supplies its soft glow. No fixed white ink is introduced;
neutral covers retain the existing neutral palette behavior.

The bounded 34-sample, one-second history still identifies rising water.
A smoothstep rise gate tapers the accent to zero; its thickness follows a
160 ms EMA on the existing audio clock. Antialiased accent polygons stop at
exact signed-rise crossings, so accents cannot bleed into falling segments.
The base hairline uses the existing crest path with round joins. Repeated
paints do not advance history or smoothing. Track-change
reflection uses the same restrained accent bound; the existing flip, soft
reflection envelope, timing and cover light remain intact.

For bounded CPU work, painting reuses the audio clock's existing 49 water
samples and cached thickness tuples. The hairline reuses the existing crest
path, and track reflection reuses the same already-built accent polygons.
No additional timer, blur pass or wide glow stroke is added;
DPR 2 retains the existing logical-pixel wave-strip raster.

All other ec89693/395d6ed changes remain, including lyrics, cover geometry,
shared flips, library mini bar, resizing, settings thumbnails, Translation A,
collapsed-pill mask repair and palette synchronization. Provider, playback,
settings persistence and runtime namespace behavior are unchanged.

## Evidence

All scripts, raw frames, measurements and logs are outside source control at
`/workspace/gluspot/handoff/t225/`. `FINAL.md` there binds the final commit,
verification numbers and image paths. The user comparison is
`t225-compare.png`; individual ebe525e/new frames are in
`proofs/ebe525e/` and `proofs/new/`.

The identical 180-tick, 30 Hz reference fixtures measure:

| Moment | Dense peak, ebe525e and t225 |
| --- | ---: |
| Quiet, peak .0004 | 0.339568 px |
| Held bass/drum, peak .64 | 7.696877 px |
| Vocal, peak .64 and vocal .25 | 9.583367 px |

The red, magenta, blue-green and pale-cover comparisons use the real island
owner and its extracted palettes at DPR 1/2. Sea-only frames isolate the
edge; crest crops in the stitched image reveal its thickness and tint.

Focused checks cover restored amplitudes, low-input compatibility, EMA
attack, duplicate/reset clocks, bounded history, rising endpoint taper,
continuous quiet hairlines, palette/readability, damage bounds, repeat-paint
purity, preview modes and shared track transitions. The requested full
module-isolated suite compares failure/error identities against a verified
395d6ed baseline (54 failures / 48 errors / 32 skips); this is not a claim
that the inherited suite is green. Exact commands and results are in the
handoff's `FINAL.md` and `verification/` directory.

Final focused verification passed 80 tests in 2.862 s. The requested full
isolated run completed 6128 tests in 146.079 s with exactly the baseline's
54 failures / 48 errors / 32 skips and zero added or removed identities.
All 333 modules were recorded; two native-incomplete owned-audio/artwork
modules were supplemented individually by the supplied runner. Source hashes
were unchanged throughout. The first full run's individual-only high-refresh
timer failure was also reproduced on the untouched baseline; both that run
and the paired reproduction are retained. No unrelated tests were changed.
Compilation of api/local/tests and whitespace verification passed.

CPU uses the t205 520×136 state/body/wave pattern, with 60 warmup and 600
measured frames per case, steady and track-reflection cases at DPR 1/2.
Alternating matched runs compare 395d6ed with final source; raw timings and
source hashes are in `cpu/` and `cpu-comparison.json`. This is a Box Qt
raster proxy, not desktop FPS or physical Windows acceptance.

The final comparison uses the median of three alternating pairs pinned to
the same Box CPU core. Wave-only CPU seconds per 600 frames:

| Case | 395d6ed | t225 | Change |
| --- | ---: | ---: | ---: |
| Steady, DPR 1 | 0.286387 | 0.284039 | −0.8% |
| Steady, DPR 2 | 0.526382 | 0.501706 | −4.7% |
| Track reflection, DPR 1 | 0.503041 | 0.470685 | −6.4% |
| Track reflection, DPR 2 | 0.750767 | 0.725417 | −3.4% |

Combined body/wave paint ratios are 0.923 / 0.986 / 0.920 / 1.0003 in that
order; the full state-update/body/wave proxy ratios are 0.953 / 1.003 /
0.942 / 1.014. The EMA runs in the state update. These aggregate measurements
do not claim every timer or latency percentile improved. Unpinned diagnostic
runs and anomalous CPU-accounting spikes are retained alongside the final
affinity-controlled comparison.

PC 未验证. No PC operations, NetEase restart, version bump, build or push.
