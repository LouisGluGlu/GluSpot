# Translation toggle continuity — t256

The windowed and fullscreen song pages keep their current lyric visible while
translations expand or collapse. Companion height, opacity and reveal use a
320 ms `cubic-bezier(0.28, 0, 0.72, 1)` curve. A new click samples the current
pose before choosing the opposite destination; delayed provider translations
start that same curve when their shaped rows become available.

## Reproduced causes

References in this paragraph refer to the pre-change `d217416` source, archived
under `/workspace/gluspot/handoff/t256/before-src/`:

- `local/music_surface_window.py:3412` selected a different presentation when
  translation visibility changed. The click invalidated its cache at line
  3711; companion strings were part of the whole-song layout key at line 5743.
  Songs over 48 rows used the bounded builder at line 8007. Until completion,
  lines 5815–5816 returned empty layouts, leaving the lyric paint loop without
  any primary rows. The authored 120-row fixture reproduces missing primary
  paint calls and the corresponding luminance drop in both directions and
  both player sizes.
- Lines 3758 and 3765 reserved full translation height before entrance. Lines
  3743–3748 faded outgoing ink while holding that height, then lines 3731–3733
  dropped the reservation. Height therefore did not follow the visible fade.
- Cover-lyrics centering included companion height at lines 8253–8262. Changing
  that height also changed the primary lyric anchor. External lyrics already
  centered the primary block and retain that rule.

The reproduced empty frames did not show a lyric skeleton. This was an empty
layout result, rather than a full-page opacity reset or a blank artwork frame.

## Implementation boundaries

The song-page presentation retains bilingual content and Qt layouts while
companions are hidden. Visibility belongs to `_translation_layer_pose`, so a
cached toggle reuses the existing primary and companion layouts. If a provider
adds or removes translations during its settings refresh, the bounded builder
retains the committed layouts only when primary text, fonts, geometry,
parentheticals and duet structure still match. Painting uses the committed
companion strings until the replacement is ready; it never combines a new
string with an old shaped layout. This reuse also requires the same track
identity: a 192-row replacement song with identical originals and different
translations must never display the previous song's companion ink. Exact
complete-layout hits can safely reuse shapes and acquire the new identity.

When the page first opens before bilingual preparation finishes, it reuses an
already-ready compatible original presentation. This keeps lyric word seeks
available and avoids an extra loading cycle. Outgoing translation snapshots
retain their own source during collapse.

Animated translation reservations move neighboring rows directly along the
requested curve. Only that contribution is rebased in the existing logical
springs; normal lyric follow retains its position and velocity. Cover lyrics
retain their current block anchor until focus changes. The translated aside
and sentence translation share the changing reservation.

The shared preparation store can contain both visibility selections. Island
fallback now requests a compatible result with its own selected translation
state, including a bounded cached match, so the song page's retained bilingual
presentation cannot leak into a translation-disabled island. Its renderer,
word progress and expanded geometry are unchanged.

CLASSIC typography, colors, karaoke and default selection remain. Explicit
A/B/C focus-entry treatments remain. Buttons, glass, provider interfaces,
version and Windows packaging are unchanged. The t254 island geometry rollback
and skeleton rules remain in force.

## Evidence and validation

All evidence is in `/workspace/gluspot/handoff/t256/`. `compare.png` places the
four required window/fullscreen ON/OFF sequences before and after the fix.
`compare-cover.png` supplements the cover-lyrics path. Exact-size PNGs, frame
strips, JSON and CSV values are in `before/`, `after/`, `before-cover/` and
`after-cover/`. `metrics.png` and `metrics-cover.png` plot all sampled frames.

The authored 120-row song is paused on a fixed current row; the ordinary
background continues running. Chrome stays awake to isolate translation from
the independent control-idle/cover-geometry animation. Each sequence includes
43 frames at 60 Hz, from the frame before the click through 683 ms afterward.
Luminance is the mean Rec.709 RGB value on a 0–255 scale. Changed-pixel fraction
counts pixels with any channel changing by more than 3/255 from the previous
frame. Reports include both the full frame and actual lyric viewport. Current
line y comes from real Qt painter dispatch after its transform and spring
offset; separate raster regressions also require continuous bright primary ink.

The final eight ON/OFF sequences contain 344 measured frames with zero missing
primary frames. External primary y displacement is exactly zero. Cover-lyrics
displacement is at most `1.478e-12` px (floating point rounding), compared with
9.2 px at 960×540 and 17.408 px at 1920×1080 before the fix. Eight initial
rasters exactly match baseline. The 86 rapid-toggle frames have zero blanks;
eight same-time toggle events, including six reversals, have exactly zero
position or opacity discontinuity.

| External lyrics | Blank frames, before → after | Minimum lyric luminance, before → after | Maximum changed lyric pixels, before → after | After current-line y span |
| --- | ---: | ---: | ---: | ---: |
| 960×540 ON | 10 → 0 | 107.745 → 115.276 | 26.347% → 17.571% | 0 px |
| 960×540 OFF | 9 → 0 | 108.173 → 115.250 | 26.304% → 17.243% | 0 px |
| 1920×1080 ON | 11 → 0 | 107.702 → 115.157 | 20.681% → 15.047% | 0 px |
| 1920×1080 OFF | 9 → 0 | 108.129 → 115.004 | 20.660% → 15.041% | 0 px |

Baseline ON blanks begin at the click; baseline OFF blanks begin at 350 ms,
after the 340 ms retained-source deadline, and continue through 483.33 ms.
The changed-pixel values include the intended movement of neighboring lyrics
and the running background. Primary paint/raster continuity and the absence
of the luminance dip distinguish the fix from merely reducing total motion.

`tests/test_translation_toggle_t256.py` covers both sizes and directions,
cached-layout reuse, primary ink and anchoring, cover anchoring through
settlement, provider removal, delayed bilingual delivery and interruption both
at paint time and between paints. Existing companion, cover and fullscreen
tests retain their identities and check the requested motion and retained
internal presentation. The old overshoot expectation is superseded by the
explicit cubic toggle requirement.

Final-source island preservation renders also verify all 168 t254 rasters,
geometry captures and reading calls exactly, including all 138 non-radio
expanded rasters/geometry against `b4601bd`. The 18 retained radio-control
states match t254; collapsed sea word progress advances from 6 to 18
characters. See `island-preservation-comparison.json` and `review.md`.

The first complete isolated run found the cold-page word-seek regression and
four obsolete interlude-accessor assertions. The source fix reuses compatible
ready preparation; the interlude test now checks retained bilingual data plus
zero hidden companion paint. Independent review reproduced and fenced the
identical-original replacement-song case. Initial logs and the first suite are
retained under `full-suite-initial.log` and `suite-initial/`; final readiness
uses the subsequent complete run on the corrected source.

The complete verification results and failure-identity comparison with t250 are
recorded in `/workspace/gluspot/handoff/t256/FINAL.md`. All rendering and tests
use Box offscreen Qt; physical Windows compositor smoothness is unverified.
No push, main-branch operation, PC operation or build was performed.
