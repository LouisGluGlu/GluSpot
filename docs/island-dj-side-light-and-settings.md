# Island DJ side light and stationary settings

The October 12, 2026 follow-up changes the island ring lighting and removes
elastic interaction from Settings. Version 6.412 and the existing executable
remain unchanged; restart this branch's `launch.bat` to use the source changes.

## Island lighting

Every hollow ring now starts with the shared, contrast-protected song
characteristic color on its left rim and blends smoothly toward the actual
background on its right rim. Speech uses the background already painted in
this frame, including the moving sea-level surface. The existing device-menu
background buffer is reused: the background is painted once, three right-side
pixels are sampled per ring in DPR-aware logical coordinates, and the frame
readiness flag clears after painting. There is no desktop capture, new timer,
per-frame artwork extraction or accumulation of frame images.

Finder Bar's transparent and capsule styles use the detected bar material.
Its faded strip accounts for each ring's position and the original fade stops
and alpha, rather than using the strip's center pigment at an edge. The
0.32-second genuine-completion handoff uses the same side lighting and retains
the existing button ripple feedback before the expanded content is painted.

The separate window/fullscreen renderer keeps its established middle-theme,
paler-side ring colors. Hollow centers, the single halo, frequency response,
two-second travel and half-second rest retain their existing behavior.

## Settings interaction

Settings opts out of the shared elastic hover system before constructing its
children. Its sidebar, switches, sliders, inputs and detail buttons retain
native geometry: no growth, adjacent-control displacement or hover-triggered
detail expansion. Detail controls still open by click or keyboard, and native
hover feedback and the existing glass rim material remain. Controls initially
constructed without a parent also respect the opt-out after attachment.

Playback, library and device controls retain their existing hover interaction.
No preference value, provider contract or media clock changed.

## Verification

Focused native regressions cover black/white/gray/colored background pixels,
every ring's gradient, DPR-aware current-frame sampling, Finder's faded edge,
single-buffer reuse, genuine-completion lighting and button ripple feedback.
Settings regressions cover ancestor opt-out, late parenting, button clicks and
detail-panel toggling.

The native animation preview rendered 250 frames and passed 505 checks at each
of DPR 1 and 2, covering black, white, gray, characteristic and moving sea-level
backgrounds. PNGs and travel strips were visually inspected. The retained
buffer measured 136080 bytes at DPR 1 and 544320 at DPR 2. Warm isolated shell
paint P95 was at most 2.397/3.285 ms for solid cases and 8.257/13.058 ms for the
sea-level case respectively. These are raster timings, not compositor FPS or
whole-application performance. Ignored evidence is under
`.runtime/island-dj-side-light-preview/`.

Final native Windows verification passed all 4434 discovered Python tests in
261.726 seconds, with no skips or Qt callback tracebacks, using
`.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest discover tests -v`.
The final DPR 2 settings/island/control command passed 171 tests in 17.269
seconds. A preceding DPR command used two nonexistent test module names;
the command was corrected and rerun successfully. Final code/test hashes
remained unchanged throughout full verification. Dependency consistency,
Python compilation, Node syntax and diff whitespace checks passed.

Ignored logs are `logs/login-platform-settings-side-light-full-final.log` and
`logs/settings-side-light-final-dpr2-corrected.log`. Physical Finder material
changes, multi-monitor placement and long live-listening sessions remain
desktop acceptance work.
