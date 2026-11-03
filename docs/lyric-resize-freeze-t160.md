# Shared lyric width freeze (t160)

Dragging window width invalidated the full-song shaping key on every width
step. Before edits, a 26-frame shared-surface trace from 900 to 400 measured
18 song rebuilds, 432 wrapped-layout misses, 360 evictions from the 96-entry
row LRU, and 18 drops each of glow and reading prewarm state. Maximum synthetic
frame cost was 122.211 ms. This confirms the reported cache/prewarm cause.

The fix belongs to `MusicSurfaceWindow` through `MusicLyricResizeMixin`.
The narrow shell only forwards width/completion events and freezes its existing
veil/backdrop paths. Width events start a freeze; gesture marks sustain it, and
SPLITTING/MERGING plus chrome animation keep it alive after pointer release.
Marks without a width change do not capture. The t156 mark, duration, intent,
geometry and chrome reveal rules are preserved.

At entry, the shared renderer captures only the previous lyric column into a
transparent image at `devicePixelRatioF()`, using its already-shaped rows and
last resolved geometry. It records column width, complete main/companion wrap
signature and playback anchor. This avoids the recursive paint caused by
`QWidget.grab()` during a resize event. Frozen frames transform that image;
they do not call the song/row layout helpers or change their LRU/prewarm queues.

Karaoke progress is frozen during the drag. The existing snapshot rebase helper
rescales spring positions; it cannot advance highlighting in a bitmap. Playback
continues, and the first live frame reads the current clock. Word and row clicks
are disabled until settle, including cancellation of previously pressed targets.

After the last width event, or the end of the final queued animation, a precise
150 ms timer resolves final shaping inputs and measures wrap signatures once
outside the live LRU. An unchanged signature and unchanged content reuse the
actual QTextLayout objects and reposition rows. If font size changed, a uniform
font transform preserves those shapes. Changed text, duet pairing, font style
or wrap causes one full song build. `lyric_relayout_count` is a read-only
cumulative count of full-song build starts, including cold builds; signature
probes are not committed song rebuilds. Tests compare deltas from a warm frame.

Changed wraps blend for 140 ms with InOutCubic easing inside the lyric column.
Premultiplied endpoint weights are applied before additive composition, then
painted once over the background. This preserves coincident ink brightness;
sequential transparent SourceOver blending would dim it at the midpoint. Cover
lyric shading remains independent of that ink blend. Hide, navigation and an
explicit content selection cancel a pending bitmap through the shared lifecycle.

The narrow veil, sharp background and chrome tint source are held through motion
and the debounce interval. The settle callback forces exactly one veil refresh,
including roundtrips to the original width. Ordinary ambient refresh resumes
afterward. The wide shared renderer skips the unused legacy stanza atlas:
profiling found that its preparation alone cost 138.604 ms at DPR 2 and pushed
one pre-optimization settle to 406.627 ms. Settle now commits the already
resolved shaping inputs directly instead of preparing a discarded second ink
frame. All four content modes, current singer tags, reading style and the
provider/transport boundaries retain their existing implementation.

## Verification and limits

Final measurements and exact command reports are recorded in the external
`/workspace/gluspot/handoff/narrow-v4/FINAL_t160.md` and `box_t160/` bundle.
The task requires the four t156/veil/flicker/fit suites plus the new t160 suite;
the final run also covers resize tint, liquid glass and existing narrow content
selection. All **63 tests pass at each DPR**, with zero failures, errors or skips
(10.226 s / 11.185 s), private settings/cache paths, `QT_QPA_PLATFORM=offscreen`
and `/workspace/gluspot/qtvenv/bin/python`. The 17 new tests additionally cover
font reuse, consecutive captures, duet positions, alpha-preserving blending,
late same-track text, lifecycle cancellation and unused-atlas avoidance.

The serial fixture harness runs three 4 px-step 900→400→900 roundtrips and
one changed-wrap 900→800 probe per DPR. Final widths are [900, 900, 900, 800].

| DPR | Measured drag/animation frames | Drag relayouts | Settle relayouts | Veil prepares during drag | Veil prepares per settle | Max settle ms | Max measured frame ms |
| --- | ---: | ---: | --- | ---: | --- | ---: | ---: |
| 1 | 775 | 0 | [0, 0, 0, 1] | 0 | [1, 1, 1, 1] | 243.451 | 41.069 |
| 2 | 775 | 0 | [0, 0, 0, 1] | 0 | [1, 1, 1, 1] | 224.624 | 36.777 |

These are explicit synthetic render timings, including the first measured
settled render; they are not presented FPS or a 60 Hz guarantee. Settle wall
latency includes the 150 ms quiet period and its callback preparation.

Physical Windows drag appearance, inverse-scaled lyric-band residuals, wallpaper
luma, sustained drag performance and physical monitor DPR remain unverified.
The native fixture scripts and offline sync instructions are prepared in the
external `pc_verify_t160/` directory; no player restart or PC launch occurred.
Literal A5/A11 gates and unrelated capture readiness remain outside this task.

Broader checks retain known baseline problems: the same three offscreen geometry
failures in a 148-test comparison, and an existing eight-subcase edge-resize test
with seven aspect-ratio failures plus one uninitialized-fit error. The old live
lag harness also reproduces its 25 `FrameHarness`/`super()` errors across 15 tests
on untouched base `1e34fd5`. Exact baseline and changed-source logs are retained;
no full-suite success is claimed.
