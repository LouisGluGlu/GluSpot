# 6.410 lyric continuity and output selection

This change is isolated on `codex/lyrics-output-modes`, starting from the
verified 6.409 commit `f6749ab`. It preserves the combined album-flow background
and the native HomePod/AirPort/M6 synchronization engine.

## Device checkboxes and capture scope

The user requested Apple-style device checkboxes and explicitly retained
computer-wide capture for one AirPlay receiver. Selecting another receiver or
one Windows/Bluetooth endpoint creates a coordinated music-only group. Removing
members down to one receiver stops/reaps the shared renderer, restores owned
mute and application routing, then starts the computer-wide sender. Removing
the last receiver returns to local output; a local-only choice changes the
Windows default using the existing confirmed switching path.

The chooser labels single casting as “电脑全部声音” and coordinated playback as
“仅当前音乐软件”, including while switching. The immutable snapshot's
`capture_scope` is authoritative; member count determines only the displayed
count. Pending devices use connecting indicators until confirmed. The capture
or default endpoint is never implicitly checked as an additional group output.

Single casting checks durable application-route restoration before starting.
Unreturned writes or invalid ownership evidence prevent a new sender. A pure
paused `resume_verification_pending` record has already returned its owned
policies; it can coexist with system capture while the existing recovery
observer continues verifying the original stream after music resumes. A failed
repair stops single casting and preserves recovery evidence. Group routing
continues to fence that observer while it owns a live application transaction.

## Continuous lyric corrections

Previously, small negative player-clock corrections could hold the reading
front for several frames. A reproduced 35 ms correction held three frames at
120 Hz. Playing corrections now converge at a bounded rate while retaining
positive forward motion. Paused observations, verified local Seek, coarse row
ceilings and confirmation of large external jumps remain separate behaviors.

Sender estimates still come from protocol/capture-buffer evidence. A shared
display-only `OutputLatencyClock` follows batched estimates between frames,
limited to 160 ms of correction per second. Multiple views or paints at the
same timestamp cannot apply a correction twice. Playback and Seek targets
remain source-authoritative; this is not an acoustic latency measurement.
Ordinary compensation slewing preserves the in-flight OBO focus pose; genuine
presentation anchors and backward reading resets invalidate stale focus.

## Menu rendering

Unchanged full snapshots, style and action state skip redundant row rebuilding,
stylesheet work and layout/tab-order refresh. Volume request timeouts and
material/appearance updates still run. The frosted material crops the music
page's already displayed background raster with logical-to-raster coordinate
mapping instead of asking the whole lyric page to repaint. Ordinary QWidget
parents retain a child-free fallback.

Native Qt fixtures cover the 640 × 360 minimum: single/group scope, pending
spinners, inline volume, scrolling and reachable stop controls. In a local
60-refresh benchmark, forced warm material work fell from about 4–4.5 ms to
1.6 ms median; parent-page paints fell from 60 to zero. Reusing an unchanged
displayed raster took about 0.003 ms median. An unpainted window still requires
one cold fallback; these figures do not establish universal frame performance.

## Verification boundaries

Automated tests exercise public one/two/one/zero membership transitions,
music-source waiting, queued cancellation, stop/mute/application-restore order,
durable recovery, scope labels, confirmed selections, volume timeout and
background-raster updates. Lyric simulations use the real motion path at
30/60/120 Hz with source corrections and batched output estimates.

Native audio transport is unchanged from the 6.409 hardware check. The user
confirmed HomePod mini + AirPort Express + M6 basic synchronization in that
check; it is separate evidence from this display/menu patch. New real-player
listening across song changes, wider Windows drivers, multi-hour drift and
multiple physical HomePods remain hardware acceptance gates.

The final source passed all 3045 discovered Python tests in 82.451 seconds,
Python compilation, dependency checks and whitespace checks. The 6.410 onedir
package passed frozen dependency/device/capture/group-dispatch acceptance;
`local.music_output_clock` was imported from the frozen archive. Its helper
hash remains the verified 6.409 binary, so the prior ten native suites and
physical synchronization evidence are reused rather than claimed as a new
hardware run. Packaging versions are aligned in branding, spec, Windows
resources, build script and README. Focused real-unit render tests reproduce
the stale early-focus failures under the preceding implementation and pass
with this patch. Single recovery tests use temporary journals and memory
adapters, leaving the user's audio state untouched.
