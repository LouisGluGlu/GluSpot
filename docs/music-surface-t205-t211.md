# Full-window lyrics, responsive cover and shared track motion — t205–t211

Base: `ebe525eed9905349dcd0476b0ebcda85abae7258`, branch
`grok/capture-narrow-v4`. This is a source-only change; version and Windows
packaging inputs remain 6.417. Product changes were written by the parent
Codex agent; delegated work produced tests, independent audits and BOX proofs.

## Lyric clip and chrome causes

At the base, `local/music_surface_window.py:7676` starts lyrics below the
column top and `:7700` replaces the window-height ink clip with the column's
top/bottom. The actual lyric/loading painters intersect that rectangle at
`:8045` / `:8715`. Wide geometry is inset by `60*scale` vertically. The base
stacked fallback in `local/music_surface_layout.py:329–335` adds a 70 px header,
then reserves `156*scale` at the bottom. The runnable release additionally uses
`NarrowStudyWindow`: `_demos/narrow3/layout.py:31–37` reserves from
`cover.bottom()+24` (190 px) to `transport.top()-20`; its mounted shared painter
therefore hard-clips the screenshot's preceding row at y190 even with controls
hidden. This was the source of both the sliced line and the empty top area.

External lyric geometry and ink now span the real window's y0..height.
`local/music_surface_window.py:7282` composites only transparent lyric ink in
a reusable buffer with DestinationIn gradients at both edges, extending through
the stable chrome envelope to the actual bottom edge. There is no interior
rectangular vertical boundary or opaque header/footer plate. The responsive
release uses `NarrowLayout.lyric_viewport`; playlist geometry keeps its existing
viewport. Cover-internal lyrics keep their rounded-cover mask.

Base control exits target corners: window utilities `:9693` (+x,−y), library
`:8828` (−x,−y), transport `:9699` (−x,+y), DJ `:9246` (+x,+y). The release also
adds `(width+22)*hidden` in `NarrowStudyWindow.posed_rect` and its cached top-band
blit. All hide/show displacement now has dx=0: top groups/header move up,
bottom transport/collection/DJ move down. Existing easing/durations, icon/glass
styles and grouping are retained. Width split/merge choreography is unchanged.

The 511×540 hard-clip reproduction goes from ink y190..452 and published
viewport y199.72..442.82 to y0..540 for both. All ten narrow/wide shown/hidden
proof cases have matching full-height viewport/ink bounds and stable active
rows across control visibility. Regression tests observe the actual transformed
QPainter clip and sample a continuous alpha field, including both resize paths.

## Proportional wide geometry

Base `_wide_content_column_geometry` at `local/music_surface_layout.py:354`
allocates the height-limited cover first, then a fixed gap, leaving the lyrics
only the remainder. The visible cover now uses at most 44% of content width,
with a gap of at most 10%; lyrics retain at least 46%. Existing shaped fit and
breakpoint measurements explicitly use the old geometry. Metadata fonts and
transport dimensions scale with the cover's compression factor.

| Width ×540 | Base cover / lyric px | Final cover / lyric px | Final title / artist px | Final transport width px |
| --- | --- | --- | --- | --- |
| 960 | 327.568 / 454.054 | 327.568 / 454.054 | 28 / 18 | 277.568 |
| 800 | 327.568 / 294.054 | 309.189 / 323.243 | 26 / 17 | 261.994 |
| 690 | 327.568 / 184.054 | 260.789 / 272.643 | 22 / 14 | 220.982 |

The 960 px transport retains its previous width. No button styling or glass
capsule arrangement changes accompany the proportional scaling.

## Sea displacement and rising crest

`SeaWaveState.update_ripple` adds absolute low-band onset detection with
20/110 ms attack/release for punch, alongside the existing 60/250 ms absolute
level/vocal swell. Displacement changes from `(.20+12.5*level)*shape` to
`(.20+83*level**1.5+24*beat)*shape`. The nonlinear drive preserves near-calm
quiet input. Low input comes from the same actual provider frame as peak/vocal;
the shared background's cached/idle fallback bands cannot manufacture drum
onsets. The normal sea retains its palette body/glow/specular pipeline.

At the identical t202 phase/input, quiet peak .0004 measures raw .3396→.3281 px;
peak .64 without vocals measures 7.6969→44.9663 px (5.84×); peak .64 with
vocal .25 measures 9.5834→62.7824 px (6.55×). Visible vocal displacement reaches
50.6352 px after the shell/header clamp. A separate quiet→200 ms low-band punch
measures base/final raw 7.2639/59.3643 px (8.17×), visible 7.2470/55.9258 px
(7.72×). The clamp deliberately limits upward crests below the header while
allowing larger troughs in the lower island; it never pushes water outside
the shell. The real .75-unit island proof keeps the crest at or below y59.12,
clear of the cover bottom57.5 and transport bottom47.75.

The existing 33 ms audio clock stores at most 34 timestamped, 49-point water
samples. The edge compares current visible height to the interpolated height
one second earlier, using the same geometry limits on both. Non-rising/flat
points have exactly zero thick edge. Positive rise controls ribbon thickness
with a smooth gate and slow spatial noise; widths taper to zero at boundaries.
Between the 49 surface samples, each ribbon stops at the interpolated exact
zero-rise crossing, so it cannot extend across a falling part of the segment.
The thick ribbon uses a saturated sea/specular palette mix, with no white
stroke on falling water. Repeated paint cannot advance this state or history.
The sharp track-switch crest reflection uses the same rise gate; its soft
under-crest glow remains independent. A regression isolates that sharp light
and verifies that falling/stationary water produces no stroke during a flip.

## Cover transition

`local/track_cover_transition.py` shares the island's 640 ms eased perspective
projection and a soft dynamic-palette reflection along/below the lower edge.
The ordinary window and fullscreen cover painters, plus the responsive narrow
header, use it in lyric and lyricless cover modes. The existing host timers
advance paused switches. The narrow header is painted live outside the cached
utility band, so it does not freeze its flip inside a 10 Hz chrome image.

Track identity, rather than artwork cache identity, starts a flip. Same-track
SMTC→original artwork enrichment uses a quiet 240 ms crossfade without changing
the ongoing flip pose or starting another flip. Rapid changes snapshot the
visible pose into a bounded 192 px image. Parent painter clip/transform/opacity
are preserved; the repaint envelope contains the under-cover reflection.

## Evidence and limits

All generated artifacts remain outside source control under
`/workspace/gluspot/handoff/t205/box/`:

- `t209-hardclip-before-after.png`, `t209-narrow-before-after.png`,
  `t210-wide-before-after.png`, `layout-comparison.json`: 99/99 BOX checks.
- `after-511x540-narrow-{hide,show}-strip.png` and
  `after-800x540-wide-{hide,show}-strip.png`: control retreat/reveal direction.
- `de-{before,after}/`: quiet/beat/vocal water and full-island strips,
  window wide/narrow/fullscreen flip and same-track upgrade strips, including
  lyricless cover mode; exact raw/visible displacement and edge-width records.
- `layout_proof.py`, `compare_layout.py`, `t205_de_proof.py`, `environment.json`:
  offline fixtures, hashes, commands and private profile/cache isolation.
- Baseline/final ordinary full discovery logs and identity comparisons,
  focused logs and paired CPU proxy JSON are linked from the external
  `/workspace/gluspot/handoff/t205/FINAL.md` with the committed SHA.

CPU values are BOX-only synthetic Qt raster proxies, including state/paint;
they do not establish native Windows CPU, display cadence or live playback.
Both revisions use the same fixture/script, 60 warmup and 600 measured frames.
The final D pair also exercises the rising-only track reflection:

| Sea case / DPR | Base → final P95 ms | Base → final one-core CPU at 30 Hz |
| --- | --- | --- |
| Ordinary / 1 | 1.332 → 1.195 | 3.359% → 2.982% |
| Ordinary / 2 | 2.501 → 1.812 | 5.670% → 4.881% |
| Track light / 1 | 2.150 → 1.803 | 5.356% → 4.480% |
| Track light / 2 | 2.716 → 2.377 | 7.128% → 6.262% |

The responsive window proxy forces full frames and presentation/control sync
through 33 repeated switches. It includes the full-height lyric work and the
new live narrow header; it does not model steady partial repaint scheduling:

| Release pose | Base → final P95 ms | Base → final one-core CPU at 30 Hz |
| --- | --- | --- |
| Wide 960×540 | 38.246 → 39.538 | 55.634% → 58.543% |
| Narrow 510×650 | 15.858 → 19.675 | 20.906% → 30.402% |
| Fullscreen 1280×720 | 40.969 → 46.317 | 60.849% → 74.752% |

Window proxy cost increased, especially narrow/fullscreen. These results do
not establish a window performance improvement. The later island raw-low input
correction is outside both direct-state CPU fixtures; their measured sea and
window painter sources are unchanged, with hashes retained in the manifest.

Final verification passes 161 focused checks without failures/errors/skips.
Ordinary discovery completes 6,087 tests in 193.419 s against the exact base's
6,064 tests in 197.893 s. Both report the same 54 failures, 48 errors and 32
skips, with no new or removed identities, native crash or timeout. Core/test
and responsive-demo hashes stay unchanged through the final run. Compilation
of every changed Python module and whitespace checks pass.

An earlier unchanged-source full run had one extra asynchronous fixture setup
IndexError in `NarrowScrollReadabilityTests`, before the test body. Three paired
isolated module runs passed on each revision (24 checks), and the unchanged
ordinary rerun above matched baseline exactly. The earlier error is retained
in the external handoff; it was not reproduced on baseline, and its cause is
not claimed resolved. This evidence establishes the final comparison, not a
green whole branch or elimination of intermittent fixture timing.

PC 未验证. No PC launch, player restart, completed login, keys or API changes
were performed. The parent owns ONE PC restart after commit through
`C:\Users\Liwuju\Desktop\GluSpot 测试启动.bat` /
`E:\grok-gluspot\_launcher\start-gluspot-test.ps1`, with `bak-t205` retained.
Native live playback, DPI/compositor appearance and physical CPU acceptance
remain with that parent PC pass.

Process audit: 100/100 for this scoped source/BOX handoff after correcting the
interface, geometry, rise-boundary and fixture issues found during verification.
