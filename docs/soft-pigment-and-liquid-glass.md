# Soft pigments and native Liquid Glass

October 12, 2026 source follow-up for GluSpot `6.412`. Restart this checkout's
`launch.bat` to load the changes; version and the existing EXE are unchanged.

## High-purity album backgrounds

The supplied blue cover exposed a limitation of a luminance-only cap: pure
blue can have a very high channel value while its relative luminance remains
low. The background renderer now continuously reduces saturation and value
for bright, highly pure pigments, retaining their hue. A second luminance cap
relative to the previous display color prevents desaturation's added red/green
channels from making blue brighter. The supplied blue's standalone display
mapping changes approximately from `(29,29,220)` to `(53,53,150)`.

This adjustment belongs to background rendering. Original artwork pixels,
extracted/source colors, the characteristic lyric/control hue and the explicit
Solid island background remain authoritative. Gray ordering, photographic
shadows, moderate pigments and existing single-hue layers retain their mappings.
The renderer still owns one raster bounded to 256 pixels and its existing clock.

## October 12 correction: original faces with a glass rim

The latest correction follows the user's explicit direction: keep original
button shapes and state colors, reduce their own opacity slightly, and add a
fine glass boundary. It supersedes the sampled backing described below.
The helper draws the supplied original RGB with alpha multiplied by .80, then
two narrow inset edge highlights. There is no scene sample, extra plate, tint
wash or broad center bloom. Fully transparent originals keep their empty center.

Song transport regains its original 44px glyphs without added circular plates.
Standard actions use their original Qt/QSS bevel, radius and states; labels and
icons stay sharp. Platform cards retain dark faces and source-colored rims.
Color selectors retain their original full swatch. Real cover pixels are unchanged.
Selected device rows preserve actual volume-fill proportions; unselected rows
retain transparent faces. Connection feedback uses one continuously interpolated
original pale-to-dark face. Tiny switch captions use readable dark ink over
the restored green track. Caption contrast remains tested at 4.5 or above.

The sampled lens, material/geometry caches and album-frame freeze are removed.
Owners no longer compute discarded background regions every frame. Existing
blur/prewarm/hide animation, translation cutouts, first-run reading sweep,
DJ frequency outlines, focus/disabled feedback and device actions remain.
The pure-pigment background and 2-second ring timing are unchanged. No browser
dependency or scene capture is added by the material; this is a native edge treatment, not a
claim of full optical refraction. See the
[fresh live-page and pinned source evidence](liquid-glass-references.md).

## Historical optical backing, superseded by the correction above

`local/liquid_glass.py` supplies an original native Qt material over existing
button geometry. Song-page controls, DJ backing, first-run choices, login,
library actions and presentation controls, settings actions/switches and device
rows use the material. Real clickable cover artwork keeps its actual image;
only a missing-image placeholder uses a glass face. Color selection retains a
small exact-color center. Native OS file/color dialogs retain their platform UI.

When an existing background raster is available, a local rounded-box lens
samples that raster, softens its interior and displaces samples near the rim.
Directional opposing edge highlights and low-alpha neutral tint provide the
glass boundary. Uncached island/settings/library backgrounds use a transparent
optical approximation over the already-painted surface, without actual scene
refraction. No browser engine or external glass dependency is installed.
The [public reference review](liquid-glass-references.md) records inspected
implementation revisions, license boundaries and actual evidence.

Geometry and scene materials have independent caches. Shape maps have at most
24 entries and 2 MiB; material rasters have at most 96 entries and 4 MiB, with
each side capped at 160 pixels. Stable scene slots refresh at most ten times per
second and replace prior frames rather than retaining a song-long history.
No timer, desktop capture, recursive widget rendering or network work is added.
The shared controls freeze one small backing at the existing blur-prewarm
lead-in and keep it through hiding/reversal; a new generation re-arms prewarm
for the next cycle. This avoids returning to an earlier background frame when
the cached hide layer replaces a visible face. Device rows sample their current
morph position rather than their final layout location.
The existing full-resolution vector glyphs/text are painted after the material.
Press, hover, focus, selection, disabled state, translation's transparent cutout,
volume fill/mute/solo/join feedback and shared idle-hide motion remain distinct.
Standard buttons keep Qt keyboard, label padding, icon and clicked semantics.

## DJ rings

Ring travel lasts exactly 2 seconds, followed by a 0.5-second hold. The existing
smooth pulse keeps its fast-to-slow release. Ink is determined by each ring's
current horizontal position: the center receives the song's characteristic
color, fading continuously towards lighter versions on both sides. Hollow
outlines, left-bright/right-dark lighting, the single traveling halo, actual
PCM frequency response and the independent loading/success/failure outlines
remain. The latest speech-over-music follow-up shows the introduced song's
cover and lyrics immediately, plays music at 20% gain beneath the DJ, and keeps
only the cover visually small. Music gain returns over one second after speech;
see [current behavior and verification](dj-voices-hover-and-ducking.md).

## Verification

The current rim/hover correction passed **4212 final Python tests in 246.139
seconds**, without skips or Qt callback tracebacks. Final control/lifetime
checks passed 97 tests at each DPR 1/2, plus a consecutive DPR 1 repeat; native
previews preserve original faces and fine rims. Compilation, dependency and
Node syntax checks passed. Current DJ, hover, playback and verification details
are in [the current handoff](dj-voices-hover-and-ducking.md).

The 4078-test result below is historical evidence for the preceding
optical-backing version.

The final frozen source passed **4078 Python tests in 211.312 seconds**, with no
skips, using `.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest discover
tests -v`. Compilation, `pip check`, staged whitespace and independent source
review passed. The existing version/build files are unchanged.

The final fullscreen/material regression passed 274 tests (15.124 seconds),
following the 290-test prewarm fix verification. DPR 1/2 each passed the 885-test
island/connection set and the 43-test widget/volume/morph/hover set. Core
widget/ring/material checks also passed at both scales. Focused tests cover
actual background sampling, local crop mapping, painter
state restoration, cache bounds/refresh, button keyboard and theme semantics,
volume/route hit regions, unchanged cover pixels, ring timing and positional
color continuity. Native previews use synthetic data and the supplied cover;
they do not log in, play audio or change physical outputs. Six ring-preview
profiles across three sizes and DPR 1/2 passed 144 checks; the final 960px
previews use a 5.5-second GIF with verified encoded timing. The final supplied
cover previews passed 17 checks at each DPR, and 14 entry/login/library/settings/
device previews were rebuilt and inspected. Prior native 520ms hover flakiness
did not recur in the final 43-test runs or the full suite; no hover-production
behavior or original timing assertion was changed.

Only reviewed source, tests and documentation are committed. Generated previews,
logs, research downloads and account/runtime data remain ignored. The existing
private-sync guard checks repository privacy, sole-owner access, disabled Actions/
Pages/hooks/keys, author identity and unchanged other refs before/after pushing
only `codex/owned-playback-sources`; `main` is preserved.
