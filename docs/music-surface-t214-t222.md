# Library island, resize, backgrounds, translation and pill bounds — t214–t222

Base: `ec89693f1551249632e19817d0af976cf7373f6b` on
`grok/capture-narrow-v4`. Source only, version 6.417. All product changes are
written by Codex. The t205–t211 full-height lyric feathers, cover/lyric
co-compression, sea onset/rising edge and shared track flip are retained.
PC 未验证. No push, build, PC operation or player restart.

## F — Library now-playing bar

Library A/sidebar mounts `LibraryNowPlayingIsland`, an actual child subclass
of `IslandStackVisual`. Its fixed-height rectangle spans both the sidebar and
content within the window's existing 16 px outer margins. It calls the existing
expanded `_paint_music` renderer: cover, title, lyric viewport, three glass
transport buttons, sea/rising edge and 640 ms track flip share their production
paths. `v2_music_ambience_style` selects sea_level / solid_album / system.
The existing provider supplies audio; the library controller supplies live
TrackData and forwards the existing playback commands. An independent visible
33 ms presentation clock advances the embedded renderer while the floating
island is hidden. Hide, minimize and frozen navigation stop this clock. Hidden/default-unused
children also suspend palette/lyric workers; visible reopen starts a fresh
lyric store. A repeated eight-round close/reopen/delete regression joins all
its captured worker threads without a leak.

The platform/account chip remains above the bar on the sidebar/A2 narrow rail.
Navigation uses the embedded cover bounds and excludes its cover from the
foreground capture so the shared cover transition retains one visible cover.
Double-click stays within the embedded child instead of collapsing its parent.

The nine requested real-widget frames are under external
`/workspace/gluspot/handoff/t205/library-f-{sea_level,solid_album,system}-{960x540,433x550,391x508}.png`;
`library-f-all-modes.png` is the contact sheet. Geometry/control containment,
source hashes and CPU samples are in `library-f-evidence.json`; the reproducible
offline fixture is `render_library_f.py` in that directory.

CPU has a material cost: the final two-second, 960×540 offscreen samples measured
27.83% / 18.01% / 20.74% of one core for sea / solid / system, against
3.03% / 2.32% / 2.31% with the child hidden. Earlier samples reached 30%.
These are BOX Qt proxies, not Windows desktop FPS or native CPU acceptance.

## G — All resize edges and corners

At ec89693, `_demos/narrow3/narrow_window.py:2255` bypasses the base cursor path
in narrow mode and during width handoff. In the base window,
`local/music_surface_window.py:10730` and `:10753` can return for overlapping
lyric/control hits before the existing edge-cursor mapping at `:10756`.
There was no native WM_NCHITTEST path. The exact physical cause of the user's
wide-window observation remains unverified on Windows.

`local/music_resize_hit_test.py` supplies one seven-logical-pixel band, rounded
outwards at DPR, for Qt and native physical coordinates. Four edges and four
corners map to SizeHor / SizeVer / SizeBDiag / SizeFDiag and HTLEFT through
HTBOTTOMRIGHT. Player, responsive narrow, library and source/login reuse it.
Library/login use native WM_NCHITTEST and WM_SIZING with the existing minimum
and work-area bounds. Player edges explicitly retain the existing Qt/manual
drag path: Windows clamps native sizing at the minimum before the existing
white-feather overscroll gesture can run. Player WM_NCHITTEST therefore returns
HTCLIENT at its edges while setting the shared cursor; player Aero is not claimed.
This preserves its min/aspect bounds,
width/lyric skeleton, rebound and half-minimum handoff to Free island. The player
still shares the native/Qt hit mapping and edge cursors. Edge hover precedes content.
Glass shape and corners are unchanged. Real cursors and Aero: PC 未验证.

Seventy-two focused resize checks pass, plus all eleven new G checks at DPR 2.
The broader compact fixture's one failure and eleven errors match ec89693
exactly; logs are `job-g-*.log` in the handoff directory.

## H — Retire the gradient option and render real previews

`SettingsWindow.FINDER_BAR_STYLES` removes `专辑与渐变色背景` / `fade`.
The persistent key is `v2_finder_bar_style`, defaulted in
`local/settings_store.py:213`; its central `normalize_finder_bar_style` at line 155 maps saved
`fade` to `capsule` (专辑与背景). Unknown values retain the existing transparent
default. Load, write and the settings UI normalize the value, so old values
cannot create an invalid selection.

The interpretation of previews is a still thumbnail for the selected background
and the shared immersive lane. `MusicAmbiencePreview` now calls production
`NativeMusicBackgroundRenderer` / `SolidMusicBackgroundRenderer`, with current
artwork, metadata and the current prepared palette. Finder/Tasker previews reuse
the existing immersive shared painter and current cover. Missing art gets an
empty tile. No invented album picture or independent placeholder gradient is
used. The cached thumbnail changes for track, palette, style, size or DPR;
a visible-only 500 ms poll detects new track/palette data and stops on hide.
It does not render an audio animation on every frame.

119 focused checks pass, including prepared-palette-only changes. Exact-base
before/after panels, menus and all three backgrounds use the same tracked photo
fixture under `handoff/t205/settings_h/` outside the repository.

## I — Translation A by default

Integrated `547a2c5d9bc2251b26a308649332d30810df817f` from
`grok/translation-variants`; the music-window changes merge with ec89693 and the
README conflict retains both branches' records. `GLUSPOT_TRANSLATION_VARIANT`
is resolved once per surface: a→A, b→B, c→C, classic→CLASSIC;
unset, empty and unknown→A. No settings key or version change.

A keeps ratio .50, active/inactive alpha .30/.18, gap .30×main, no translation
karaoke, .25 s opacity delay, .40 s entry, .35 slide and .97 inactive scale.
It composes with full-height edge feathering and cover/lyric co-compression.
Generic geometry/opacity tests use these selected-style parameters. Only tests
specifically exercising the retained classic translation karaoke select CLASSIC
explicitly. The A selector/no-karaoke tests retain their independent coverage.

Before frames come from exact ec89693; after frames use A in the integrated
surface. `fj-translation-{433x550,960x540}-before-after.png`, the per-frame
`fj-translation/` captures and their opacity trace are in the external handoff.
The preserved classic reference is also available for direct comparison.

## J — Collapsed pill border/background cutoff

**User hypothesis confirmed: Y.** Both growth and shrinkage use the same
`SpringRect.step` loop at ec89693 `local/island_stack_visual.py:11133`, then the
same `_update_interaction_mask` call at `:11221`, after painting. The pure
`interaction_mask_update_plan` in `local/island_visual_stack.py:380` delays both
directions equally: Finder's interval is 240 ms, with pointer and explicit
defer gates. `setMask` clips pixels as well as input. A growing rounded pill
can therefore be painted against the previous smaller rectangular native
region: both rounded ends and background are sliced off, and its left-hand
cover disappears. This reproduces the supplied frame; karaoke timing is not
the diagnosed cause. The native window already has a roomy canvas/full bar
width; the changing native mask is the restrictive boundary.

Growth now reserves target/current mask bounds before the timer queues a paint,
then checks the same frame again before opening its QPainter. Necessary mask
growth bypasses throttles and unions the old extent. The painted pill grows
while retaining the previous line; after reaching its new width, new text fades
in over 100 ms. The first lyric after a gap can hold empty ink until ready.
Shrink reveals the shorter line first, holds width for its 100 ms reveal, then
animates inward; mask shrink waits until the shell settles. The cover is never
part of that text fade. Existing overlong-line marquee follows the reading
front and retains its bounded content clip.

`/workspace/gluspot/handoff/t217/pill_frames.py` exercises the production widget
and shared native-mask planner at 60 Hz, emulating the applied native region
because Qt offscreen cannot provide Windows compositor masks. Across 240 actual
short→long→short frames, exact base has 24 clipped-ink/shell frames and 23 frames
with a masked cover; final has zero. Every final frame has shell ⊆ window/mask,
cover visible and text boxes ⊆ shell. A separate 240-frame over-max sequence also
has zero violations; 103 overflow frames record real marquee movement.
`before-after-strip.png`, `{before,after,overflow}/frames.json` and `strip.png`
retain the proof. This checks the entire rounded raster against the mask,
including both ends; native paint-region scheduling remains PC 未验证.

The focused J/island regression selection passes 68 tests. Growth/shrink mask
ordering, busy gates, gaps, content timing and highlight-following overflow are
covered by `tests/test_collapsed_pill_width.py`.

## Verification and remaining limits

The complete paired module-isolated discovery ran **6,087 baseline / 6,126 final
tests**, both with **54 failures, 48 errors and 32 skips**. Failure, error and skip
identities match exactly; there are zero new identities. Each module uses an
ordinary unittest subprocess with normal GC, private profiles/caches and unset
variant selectors. The same two native-crashing modules on both sides
(`test_owned_audio_bridge_handoff`, `test_owned_queue_artwork`) were completed
through individual ordinary unittest subprocesses. Both summaries are complete,
and all source/test hashes remained unchanged during the runs and match the
final files. Logs: `fj-isolated-baseline/`, `fj-isolated-verified/`; exact comparison:
`fj-full-identity-comparison-final.json` under external
`/workspace/gluspot/handoff/t205/`. The inherited failures remain; this is not a
green branch.

The ordinary single-process full command was also attempted with fault handling.
Several final runs crashed in native Qt image decoding before a unittest summary
(`fj-final-initial/`, `fj-final/`, `fj-final-corrected/`). The last unchanged-source
attempt (`fj-final-verified/`) crashed after 41.646 s during garbage collection in
the library-layout fixture. An unchanged-base repeat crashed in native Qt
settings-icon painting (`fj-baseline-repeat/`), while its first ordinary discovery
completed with 54 failures, 49 errors and 32 skips; the extra asynchronous
narrow-scroll setup error is retained in that log. No crashing attempt is claimed
as a pass. A separate combined 193-check library/navigation/palette run completed;
the native crashes are intermittent, and the corrected hidden-worker lifetime is
not claimed as their cause. The paired module-isolated result above provides the
complete identity comparison. All 34 changed/new Python files compile, and
staged/unstaged whitespace checks pass. External evidence and the appended
Jobs F–J report are in `/workspace/gluspot/handoff/t205/FINAL.md`.

Existing offscreen DPR-2 layout assertions (27 state subcases) and 19 navigation
failure identities match exact base in the paired optional checks. The new
embedded renderer and resize tests pass at both DPRs. No native Windows cursor,
Aero, visible CPU or playback claim is
made. Provider boundaries, runtime naming and version/packaging inputs remain
unchanged. No PC launch/restart, NetEase restart, push, main-branch operation or
protected-directory write was performed.
