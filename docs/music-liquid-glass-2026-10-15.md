# Song-page Liquid Glass material

October 15, 2026, GluSpot 6.412. Source changes only; version and packaged EXE
are unchanged. The user's later correction explicitly excludes previous,
play/pause and next: those remain the original bare, album-colored glyphs with
their existing hit slots, click feedback and idle animation. Hover no longer enlarges them.

## Scope and rendering

Window and fullscreen function buttons now sample the existing music-page
background raster. This covers the library entry, presentation mode,
translation, playback-order/continuation, output button and connection capsule,
DJ caption/icon and the child voice selector. The output panel uses one shared blurred, translucent shell when hosted by the song page, with restrained row state tint rather than stacking another neutral lens over every device. Other application buttons retain their original
rim-only default.

`paint_liquid_glass(refractive=True)` maps an explicit source-pixel rectangle,
samples a padded crop with clamped edges, applies Gaussian blur, and displaces
only a narrow rounded rim by less than one logical pixel. A mild neutral veil
follows the existing light/dark/system/scheduled setting through the owner's
settings getter. Hover, press and selection vary its alpha. Icons and text
remain separate sharp vector paint; foreground contrast uses the corresponding
sampled scene. Active translation uses a readable foreground symbol, replacing
the previous cutout which depended on an opaque white face.

This is a native Qt implementation of the requested Liquid Glass appearance,
with real application-background blur. It does not use Apple's proprietary
platform renderer or capture the desktop. The earlier
[reference review](liquid-glass-references.md) remains the provenance for the
general optical techniques; no external implementation or assets were copied.

Each material raster has a maximum 160-pixel side; the material LRU holds at
most 64 entries/4 MiB. Fixed slots refresh at most ten times per second. The
existing hide/reveal prewarm uses one frozen scene for its cycle and includes
theme/scene identity in glass-layer keys. The independently visible DJ and
voice selector continue sampling the live scene. Theme resolution is shared
within one music-page paint. No new timers, browser engine or dependency are
added.

## Interaction and supplied reference

The supplied second image is the visual authority: a rounded, translucent
capsule with blurred ambient colour, a soft light rim and sharp foreground.
Its green tint comes from the scene, rather than a fixed green theme.
The output shell previously replaced most scene colour with grey, reduced
saturation again, then added a heavy neutral veil. The song-page path now
retains the actual scene colour, uses Gaussian blur and one mild theme veil;
rows carry only their state tint and the shared optical edge. Existing dark,
light, system and scheduled settings select the mild neutral bias. Foreground
contrast chooses readable dark or light ink from the sampled scene.

All window/fullscreen function controls now retain stationary geometry on
hover. Their old neighbour spring and delayed hover-open timer are removed;
output and compact DJ selection open only by click. The top-left music-library
return alone keeps its existing hover expansion and matching painted hit shape.
The parent `controlHoverDisabled` also prevents generic child-button growth.
Song-page device names stay at 14 pixels, and pointer dwell cannot open volume
details; an explicit slider click or keyboard focus still reveals them. The
existing DJ pointer-leave grace, Escape, source invalidation and dismissal
remain. Tooltips now describe click expansion.

## Taste supervision

The explicitly requested `design-taste-frontend` SKILL.md and its
`references/critique.md` were read. This is a PySide6 native application,
which the skill normally excludes; its screenshot critique and look/fix loop
are adapted to actual native widget renders. The user's explicit Liquid Glass
reference overrides the skill's website no-glass rule. Website section draws,
photo-count rules and DOM/browser inspection do not apply, and no browser gate
is claimed.

ROUND 1
Seen in one second: album and song, with secondary controls following its ambient colour.
Idea (one sentence): the song's existing scene supplies the material behind its playback destinations.
Live thing: click-operated output routing and native DJ voice selection.
Layout words by section: music=split / functions=compact row / output=stacked capsules / voice=categorical slider.
Scores: design 7.5 · usability 8 · creativity 7 · content 8 → overall 7.6.
Caps applied: native-app/user-reference exceptions above; no external certification is implied.
Top three problems (most visible first), each with the screenshot it shows in and the fix:
1. The supplied first screenshot's output shell is flat grey. Remove the compounded desaturation/opaque veils and show the blurred current scene.
2. Hover could open a menu during a deliberate click. Remove delayed opening and non-return hover growth, and require matching press/release.
3. The old DJ tooltip still advertised hover opening. Change it to click opening; preserve the explicit return exception.

ROUND 2
Seen in one second: album first, then lyrics; function capsules and open output preserve the green/blue scene.
Idea (one sentence): the same album-derived scene unifies small controls and the expanded panels.
Live thing: fixed click targets, real volume drag/keyboard paths, and four native voice choices.
Layout words by section: music=split / functions=compact row / output=stacked capsules / voice=categorical slider.
Scores: design 8 · usability 8 · creativity 7 · content 8 → overall 7.8.
Caps applied: same native/reference exceptions; scores are a self-review.
Top three problems (most visible first), each with the screenshot it shows in and the fix:
1. `dpr-1/overview.png`: the first voice preview showed a fixture-only fetch failure because its provider omitted the catalog getter. Supply the existing four-entry fixture and regenerate; no product failure is inferred.
2. `dpr-1/window-dark-output.png`: the output panel uses a soft shell and light row rims; the stripe scene remains visible through it, while names and slider stay sharp. No remaining material correction was justified by the image.
3. `dpr-1/small-light-output.png`: the narrow viewport still contains the whole panel and controls. Automated fixed-bound and slider tests cover the matching hit targets; physical display motion remains a user acceptance check.

## Verification

The latest focused native check passed 166 tests in 15.342 seconds, followed by
482 related tests at DPR 2 in 37.703 seconds. Native screenshot fixtures cover
640×360, 960×540 and 1920×1080, window/fullscreen presentation, both themes,
normal/hover, output-panel and voice-selector states at DPR 1/2: 24 frames per
DPR, 48 total. All six panel-containment and stationary-hover cases per DPR
passed. Final frames and machine-readable checks are under ignored
`.runtime/glass-click-2026-10-15/dpr-1/` and `dpr-2/`.

The preceding 4626-test integration reported three assertion failures. Two
compared the opaque composed music page against an uncomposited translucent
panel; those fixtures now composite the native panel over the same scene and
retain strict color/alpha, routing, volume and Escape checks. The third closed
a bridge child before its request reached stdin; its fixture now waits for
each child's request-received marker before measuring prompt close and child
reaping. These corrections change tests, not production behavior. The complete
bridge module passed 33 tests in 7.110 seconds; final related cover/surface
checks passed 121 tests at DPR 2 in 8.704 seconds.

The final combined source passed **4627 tests in 322.896 seconds**, without
skips or Qt callback tracebacks, in
`logs/gluspot-integrated-final-2026-10-15.log`. Python compilation and
`git diff --check` passed. Generated fixtures, timing logs and previews stay
in ignored `logs/` and `.runtime/`; the user's live playback and account are
not used by the visual checks.

The related playback correction and its precise verification limits are in
[DJ background continuation](dj-background-continuation.md).
