# Lyric following, glyph stability and rapid album changes

The October 9 follow-up fixes four visual playback defects in source version
6.411. It keeps the existing media-provider boundary, lyric timing evidence,
reading strategies and audio routing. No release executable was rebuilt.

## Behavior

- Unblurred window/fullscreen lyric blocks retain the same pre-shaped native
  text placement during the low-perception wipe, spring motion and rest.
  Switching to a raster at rest previously rounded the fractional origin
  differently, producing the reported sideways shift. Blurred future rows
  keep their existing raster path.
- Wave and Gelv islands choose their active row from the validated playback
  clock on the first visible frame. One phase snapshot supplies each frame's
  text, width, height, bilingual layout and reading progress. UIA-only clock
  evidence remains capped; paused seeks, output compensation and real unit
  timestamps retain their existing rules. The final timed row measures its
  fallback interval from its source start, not an old owner-row wall clock.
- Island lyric slots reserve the actual shaped font height and full reading
  lift. Standard top placement grows when a large font needs more space;
  fixed taskbar geometry and an opening spring fit the font to their available
  height. Primary, translation and expanded neighbor rows use the same font
  measurements. This prevents tall glyphs from hitting the old outer clip.
- Artwork ripples use their original radial color profile directly at the
  target resolution instead of enlarging a 384-pixel ring. Partial repaint
  regions still cover the old and new annuli when a wave restarts.
- Full-quality scaled current, previous and revisited covers share a bounded
  24-entry, 64 MiB cache. Cover-color extraction batches its samples without
  changing the original classification and exposes a 64-entry shared palette
  cache. Cache results copy mutable Qt color values and preserve native
  HSV/HSL/CMYK parameters as well as validity and RGB precision in their keys.
  RGB8 conversion reproduces Qt's float32 arithmetic and stored 16-bit HSV
  quantization; the runtime-Qt regression checks 102,904 colors. Future-lyric
  contrast candidates are generated only as needed, in their original order.
- First-time palette extraction runs through the existing background runner.
  Each surface merges rapid requests into one active extraction and the
  latest pending cover. Completion must still match the current image and
  source colors. A cold cover retains the last completed palette, or the
  existing source-color fallback on first open; normal color transitions
  handle the completed result. Failed or obsolete work cannot repeatedly
  retry on every paint or overwrite a newer cover. Hiding, closing and
  returning to the surface preserve lifecycle checks.

## Verification and limits

The low-perception pixel regression renders 1,092 continuous frames across
Latin, CJK and RTL text, single/multiple lines, translations and DPR 1, 1.25,
1.5 and 2. The reproduced horizontal centroid difference fell from 0.784 to
0.004 physical pixels. Native text drawing costs approximately 0.061 ms per
warmed block versus 0.018 ms for the old resting raster in that microbenchmark;
it requires no new shaping or cold raster at the spring's rest boundary.

Island regressions cover both styles and reading strategies, first-frame
return from a hidden large surface, bilingual row changes, final rows, UIA
freezes, seeks and actual unit timing. Maximum-font pixel checks include
accented Latin, CJK and fallback scripts at four DPRs. A DPR 1.25 standard-top
example previously lost 70 Wave and 228 Gelv ink pixels; the new layout loses
zero while retaining the original font choice.

Palette golden fixtures record outputs from base commit `a40b4e3`. Random
cover comparisons preserve all palette fields, including alpha and source
color; tests also cover cache bounds, independent mutable return values and
queued completion lifecycle. Ripple comparisons cover both normal and gold
profiles, display scaling and repeated partial repaint.

The final paired cold-cover benchmark uses base `a40b4e3` and the updated
source in one process, 30 previously unseen 1800-pixel multicolor textures
(seed 20261009), physical 1920 × 1080 at DPR 1, 180 frames and a new cover
every six simulated 60 Hz frames. All 30 covers miss the shared cache.
The updated run starts 31 background jobs including startup, with a maximum
of one active job.

| Measured work | Base | Updated |
| --- | ---: | ---: |
| Cold GUI palette request, P95 | 46.17 ms | 0.69 ms |
| Switch frame, median | 79.13 ms | 34.21 ms |
| Whole tick + paint + GUI events, P95 | 80.48 ms | 35.18 ms |
| Whole tick + paint + GUI events, median | 13.17 ms | 15.35 ms |

The expensive switching spikes are smaller; routine-frame median rises
under the concurrent extraction and color-handoff work. First-time cover
scaling, background fields and lyric-color preparation still exceed a 60 Hz
frame budget in this deliberately continuous cold-cover workload. Measurements
are Qt tick-and-raster work with synthetic media, not desktop compositor FPS.
The ignored evidence is `logs/unique-cover-worker-benchmark.json`; related
source/Qt equivalence logs are `logs/ripple-vector-lazy-final-regression.log`
and `logs/review-palette-final.json`.

No music stream, speaker, microphone,
capture scope, native helper or device volume is changed by this task.
Realtek is checked read-only and remains at zero. Actual desktop playback
and long-session smoothness still need observation after a normal source-app
restart. High-resolution full-surface raster work remains a limitation;
these results do not promise every frame meets the display refresh budget.

The isolated baseline repair passed all 3434 discovered tests in 92.355 s;
exact HSV and lazy-ink changes then passed all 3436 in 95.841 s. The final
native-color cache-key edge correction passed 87 focused tests. Scoped
integration into `E:/gpt-island` passed all 3408 tests in 93.256 s with that
correction present. Compilation and diff checks passed. All scoped source,
tests and this evidence document match between the two trees; the main
directory's pre-existing unrelated diff and index were preserved. Native
helper SHA-256 remains
`D358B1F9D4D06F7F45334C72BBB2592FD7CDF4C05BD1AC43738557ED7ADD999A`.
Realtek volume was read as 0.0 and the existing AirPods Pro default route
was preserved. No casts, recordings, tones or volume writes ran for this task.
