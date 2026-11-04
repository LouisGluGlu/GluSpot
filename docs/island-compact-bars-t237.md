# t237 / t245 — compact sea audio bars and equal artwork insets

The sea-level collapsed island uses the requested now-playing composition:
an opaque black shell, rounded-square artwork on the left, one centered bold
white lyric/title line, and exactly four rounded spectral columns on the
right. This deliberately replaces the collapsed water, rim and cover-light
wash with black. The four columns retain the existing extracted sea palette;
their HSL lightness is lifted to at least 0.62 for readable strokes on black.
Palette extraction and its existing fallback colors are unchanged.

Finder/Tasker sea capsules reuse the compact composition. Their transparent
and fading faces retain their material paths. DJ narration retains its ring
presentation, player entry and existing transport rules. The existing hide-
cover setting remains effective. Resolved lyricless compact sea uses the
current cover and title; other album-only presentations retain their queue
data and projected covers.

The provider still owns audio. `IslandAudioBars` consumes its existing 24
logarithmic FFT bands, grouping six adjacent bands into each column. Absolute
compression and a small noise gate avoid amplifying silence; there is no
oscillator, randomized motion or automatic quiet-signal gain. Legacy real
three-band providers can use their low/mid/high plus center-vocal energy;
peak-only or unavailable input releases to flat. The existing audio clock
owns 55 ms attack / 220 ms decay, and paint only reads four envelopes. No
new timer, capture worker or provider interface is introduced.

The existing width measurements and grow-before-reveal / reveal-before-
shrink snapshot remain. Sea compact width reserves 24 extra logical pixels
for the columns. Expanded sea background and solid_album/system materials
retain their original painters; only the requested shared artwork geometry
and the adjacent content space change.

One `_island_cover_geometry` helper calculates artwork from the current
painted shell, including each spring frame. Its normal inset is
`min(8, height / 6)` logical pixels, giving a 32 px square and three equal
8 px insets in a 48 px pill. The top, bottom and left insets remain equal in
compact, expanded, personal radio, native bars and album-only states. Native
capsules measure their actual face after its existing outer vertical margin;
Tasker also retains its existing native-row content margin. Artwork rounding
depends on the same geometry, so matching Finder/Tasker faces paint matching
corners. Very narrow cover/play slots reserve room in the same helper and
fit the existing painters into separate click targets. Button glyphs, fills,
grouping and command dispatch retain their existing paths.

Expanded metadata and lyric viewports begin to the right of the enlarged
cover. Album-only current artwork uses the same left slot, with the existing
projected neighboring covers in the remaining stage. Narration outro keeps
the shared slot and fades rings to artwork without unequal inset frames.

## Verification

All execution is Box-only, `QT_QPA_PLATFORM=offscreen`, using
`/workspace/gluspot/qtvenv/bin/python`. The starting HEAD is `339ba5e` on
`grok/capture-narrow-v4`; a source archive under the external handoff preserves
the exact before fixture. No push, main-branch operation, PC operation,
version/build change or unrelated button/glass restyle is included.

Seventeen new tests cover actual PCM-to-FFT frequency order, quiet absolute
levels, time-based attack/decay, provider-to-owner delivery, paint purity,
four raster components at DPR 1/2, palette contrast, one white line, width
snapshot retention, cover visibility, every artwork state and crowded native
click targets. Existing geometry/transport fixtures now assert the requested
insets and continue to verify commands, hover, narration fade and native
bounds. Old compact-water tests probe the retained expanded water instead;
their sky probe excludes the expanded waterline, matching the original
expanded fixture's water/sky difference of 1618/0. Retired-style fallback
assertions now expect the new compact sea paint plan after migration.

The final focused run passes 129 tests. Original t217/t227 harnesses,
same-fixture CPU results, the isolated full-suite identity comparison and
annotated render manifests are retained in
`/workspace/gluspot/handoff/t237/FINAL.md`. Twenty-two direct production
background images, with content omitted, are byte- and pixel-identical to
the starting HEAD at DPR 1/2: expanded sea standard/dual, solid_album and
system light/dark collapsed/expanded. This isolates unchanged backgrounds
from the intentional cover geometry change.

The requested isolated full run completes 351 modules / 6,251 tests in
163.084 seconds, with 52 failures / 48 errors / 32 skips. Compared with the
requested t234 baseline there are zero new failure/error/skip identities.
Two old retired-style fallback assertion failures are resolved by expecting
the current compact sea plan after migration. The two inherited native-
incomplete audio modules are supplemented by individual ordinary unittest
processes; all supplements complete. Source hashes remain unchanged during
the run. Existing failures/errors remain; this is not a green suite or
release acceptance. The complete report and identity delta are in
`suite/summary.json` and `suite-comparison.json` in the external handoff.

`compare.png` places nine production quiet/vocal/drum frames beside a drawn
mock labelled **reference**. `insets.png` annotates actual measured insets in
each state. The PCM fixtures use three cover palettes including a dark cover;
all 18 DPR 1/2 compact renders have exactly four separate bar components.

The final three alternating before/after CPU pairs use the same 486 × 48
logical-pixel fixture at DPR 1, warming 30 paints and measuring 160 paints in
each of nine cover/audio cases per process. The median across matched cases
is 2.0970 → 0.4330 ms/frame for the full island and 0.4957 → 0.0281 ms/frame
for the old wave / new bars. Corresponding P95 medians are 2.4467 → 0.5188
and 0.5864 → 0.0338 ms/frame. All nine full-paint cases improve. The dark
cover's bar contrast against black is 5.515:1. All 31 annotated state renders
have equal insets and separate cover/play actions where applicable.

Windows compositor/native masks, physical high-DPI displays, actual playback
devices, live capture and long-session performance remain **未验证**. Offscreen
paint costs measure raster work and do not establish desktop presentation FPS.
