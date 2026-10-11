# Personal DJ presentation and manual navigation

October 11, 2026 source follow-up for GluSpot `6.412`. Private DJ remains a
NetEase personal-FM feature. The packaged executable is unchanged; restart
this checkout's `launch.bat` after exiting the running source instance.

## Requested visual behavior

The supplied references define the bottom-right location, five hollow overlapping
elliptical outlines, a circular enabled/disabled face, a larger speech glyph above
a small next-song title capsule, and a separate loading-state outline. The
colored rings in the motion reference identify movement only; those colors are
not a product palette.

The compact control shares the other controls' idle hiding. Speech reveals
the DJ control at twice its compact diameter. During speech, the backing
circle unfolds into the title capsule beneath the rings; the title identifies
the upcoming music, while measured low/mid/high speech energy drives the glyph
and outline. Completion reverses the presentation into the compact control.
The October 12 follow-up uses the current song's album accent at the center,
with continuously lighter ring ink towards both sides, and retains one traveling
halo. It moves along
a straight right-to-left path, changing size with smooth cosine depth, over
2 seconds, followed by a 0.5-second hold. A short smooth attack and long
deceleration make the speed pulse visible while slowing the full cycle. The
original time law integrates `56*p*(1-p)^6`; speed is zero at both ends.
Wrapping rings fade smoothly before changing sides. Fractional geometry and
native antialiasing preserve the motion at both display scales.

The latest correction removes every ring's interior fill. Album detail and
other ring outlines remain visible through each ellipse; the single accent
halo and measured-frequency motion remain. Each ring's left half is brighter
than its right half, using a native stroke gradient for side lighting. The
single accent halo retains its album hue and the same directional light.
During speech, the larger glyph sits above the wider next-song title capsule
at the reference's yellow locations. One shared dark corner mask occupies
the reference's red lower-right region, spanning 32% of width and 56% of
height. Its elliptical falloff originates at the page's bottom-right corner
and fades toward the left/top rather than making a small spotlight behind
the icon. It stays anchored while the glyph moves and fades with speech
expansion. This is a native vector soft mask, without per-frame background
capture or raster blur. Speech includes the entire mask in local repaint
damage; ordinary idle retains smaller damage. Previous damage is erased at
exit, and the mask does not enlarge the button hit area.

An additional blue outline indicates introduction loading, green indicates a
prepared introduction, and red indicates failure. Blue and green glow rises
and fades. The underlying idle motion continues in each state; state colors
do not recolor every ring. These controls remain independent of lyric ripples.

## Audio and visual identity

One existing Qt decoder plays the authentic service-provided introduction and
its associated song in sequence. Voice segments are audio identities, not
full-page songs. Preparing a genuine introduction switches the page to its
associated next song: title, duration and cached cover change immediately,
and that song's real lyric request supplies the paused lyric page as soon
as it completes. Its music clock stays at zero during speech. Loading art
and lyrics keep their existing loading states rather than borrowing the
previous song's assets. The title capsule identifies the same upcoming
song. Speech completion starts that song without resetting its already
prepared visual/lyric payload; speech clock and duration never overwrite
the music clock. Multiple introduction parts preserve this same paused
song, and stale lyric replies cannot overwrite a later explicit selection.
Offline translation parsing can prepare verified target-song lyrics before
the spoken audio is ready. Decoder and preference epochs still reject stale
results; a multi-part introduction reparses retained payload when needed.
An empty or failed song lyric response uses the ordinary song's no-lyrics
state, terminating loading without inserting spoken-introduction text.

Manual Next and Previous use a real introduction already associated with
their target song when available. Another Next during speech selects the
following song's introduction. A song without a supplied introduction is not
given fabricated speech. Single-song repeat repeats music. Pausing during
preparation or speech preserves user intent, and disabling DJ removes speech
without starting paused music. Failure falls through to the associated song
with a brief red state rather than leaving an endless introduction wait.

The existing provider boundary exposes a bounded presentation snapshot with
state, next-song title, speaking flag and token. Actual decoded PCM continues
to drive the frequency frame through `OwnedAudioBridge`; no second decoder,
system capture, synthetic speech or persona imitation is introduced. A real
introduction can open its paused song page from the library after actual
playback is established; a loading or paused decoder cannot open it early.

## Animation references

The public [Math Curve Loaders gallery](https://paidax01.github.io/math-curve-loaders/)
was inspected in the browser, including its enlarged animation and public
code. Its [shared frame geometry and phase trail](https://github.com/Paidax01/math-curve-loaders/blob/70f4e00a6d452532039ff7c2ccb4c379ec90c772/main.js#L1060)
and [absolute-time frame loop](https://github.com/Paidax01/math-curve-loaders/blob/70f4e00a6d452532039ff7c2ccb4c379ec90c772/main.js#L1603)
inform consistent phase and smooth detail. Its sine pulse is symmetric;
the requested asymmetric speed law above is an original GluSpot adaptation.

[ThinkingOrbs](https://github.com/haplollc/ThinkingOrbs) supplies useful
evidence for [fast-to-slow travel and independent rest](https://github.com/haplollc/ThinkingOrbs/blob/e2c07bbdec4db797fb302300ef0159b1806a909f/Sources/ThinkingOrbs/Engine/OrbEngine.swift#L251),
[stable geometry and paint order](https://github.com/haplollc/ThinkingOrbs/blob/e2c07bbdec4db797fb302300ef0159b1806a909f/Sources/ThinkingOrbs/Engine/OrbEngine.swift#L108)
and [native pixel/scale comparisons](https://github.com/haplollc/ThinkingOrbs/blob/e2c07bbdec4db797fb302300ef0159b1806a909f/Tests/ThinkingOrbsTests/RenderingTests.swift#L26).
ThinkingOrbs is MIT licensed with Haplo LLC and Jakub Antalik notices; the
inspected Math Curve Loaders revision has no declared license. This change
implements the motion and stroke shading in existing PySide code; no source
from either project is bundled. Neither reference establishes the requested
left-bright/right-dark light model, which is an original pen-gradient treatment.

## Verification and limits

The October 12 material/timing follow-up and its latest validation are documented
in [soft pigments and Liquid Glass](soft-pigment-and-liquid-glass.md). The counts
below record the preceding October 11 implementation.

The final frozen source passed **4046 Python tests in 252.956 seconds**, with
no skips, using `.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest
discover tests -v`.

The final player/provider/translation/recovery/catalog focused run passed
**256 tests**. The control tests passed **47 tests at each of DPR 1 and DPR 2**.
Six native preview profiles (640 x 360, 960 x 540, 1920 x 1080 at both DPRs)
passed **144 checks**, including left/right stroke luminance, empty centers,
smooth pulse endpoints, exact cycle-boundary pixels, corner falloff, local
damage, clear exit, hit areas, waveform response and artwork/transport safety.
The 4.5-second animation preview covers more than two complete 2.15-second
cycles. Full-page images, aspect-preserving comparisons and transparent/grid
ring images were inspected.

Across the pixel-boundary checks, the exact hold-to-cycle boundary renders
identically. A 0.0001-second step into the next pulse at DPR 2 changes only
0.0227% of pixels, by at most 3/255 in a channel (RMS 0.0198/255), within
antialiased outline edges. Geometry remains continuous; these differences
are raster quantization rather than a visible position jump. An ignored
preview-only diagnostic briefly failed by reading a temporary image's pixel
buffer after its owner was freed; it was removed before the final full-render
preview runs. Production code was unaffected.

The introduced-song visual change passed **351 actual muted Qt checks across
nine scenarios** before the final lyric-order follow-up. After that follow-up,
the existing native `sequence` scenario passed **38 checks in 5.547 seconds**,
covering speech PCM, target-song paused visuals/lyrics, music start and further
batch continuation. Eight new controlled-delivery regressions cover translation
before stream/audio readiness, multiple speech parts, no lyrics, rejected or
failed fetch/parse, and preserving a valid pending payload. These use real lyric
parsing while controlling worker order. No real accounts or system endpoint
volumes are changed. Compilation, dependency consistency, whitespace and
independent architecture/identity/privacy review passed.

### Previous checkpoints

The preceding hollow-ring source passed **4031 Python tests in 208.109 seconds**, with no
skips, using `.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest
discover tests -v`. Compilation, dependency consistency, whitespace and
independent paint/boundary/privacy review passed.

The hollow-ring correction additionally passed **43 focused control tests at
each of DPR 1 and DPR 2**, using `-m unittest tests.test_personal_dj_control -v`.
The native previews passed **114 checks** across 640 x 360, 960 x 540 and
1920 x 1080 at both DPRs. Pixel checks cover transparent and colored ring
centers, overlapping outlines, soft-mask alpha and feathering, inherited
opacity, speech entrance/exit, local repaint bounds and unchanged normal or
centered artwork, library, top controls and transport regions. Animation and
checkerboard previews were inspected. Audio/provider behavior is unchanged
by this paint-only correction; the actual Qt audio scenarios below belong
to the preceding presentation checkpoint.

The preceding presentation source passed **4026 Python tests in 192.253 seconds**, with
no skips, using `.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest
discover tests -v`. Compilation, dependency consistency and whitespace checks
passed. An earlier full run was deliberately stopped after discovering a
synchronous cancellation edge; the complete result above includes its repair.
Native scenarios use generated loopback audio and mute only their own Qt
output. Reports, audio fixtures and animation previews remain ignored under
`.runtime/` and `logs/`.

| Focused/native verification | Result |
| --- | --- |
| Player/provider/catalog/stream/cache/DJ and library integration | 254 tests passed |
| DJ control, ring timing, state colors, hit testing, title transition and ripple isolation at DPR 1 and DPR 2 | 38 tests each passed |
| Three viewport sizes at DPR 1 and DPR 2 | 60 preview checks passed; screenshots inspected |
| Actual Qt automatic continuation, pause, seek, repeat, enable/disable, manual Next/Previous, rejected voice download and multi-part introduction | 333 checks across nine scenarios passed |

The repeatable native command is `tools/smoke_personal_dj.py --scenario all`.
It checks actual decoding and presentation-time frequency matching, paused music
metadata during speech, real introduction/song order, manual navigation, rejected
loopback voice downloads and cache cleanup. Cancellation, generation and lyric
preference regressions additionally use controlled worker deliveries. Native
fixtures do not establish physical HomePod acoustic alignment, long online
sessions or the identity of the service's default voice.

Four-persona switching needs a verified service interface and voice mapping.
The returned voice must not be labeled **豆花、黄俊、李源、庞舸** based on an
unidentified timbre key. See [continued voice research](personal-dj-research.md).
