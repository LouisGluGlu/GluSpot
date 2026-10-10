# Fused output surface and playback motion

The October 9 reference replaces a separate output button and downward popup
with one continuous circle → first-device capsule → device list. Closing uses
the same pose and spring state, so reversal preserves geometry and velocity.
This source change keeps version 6.411.

## Behavior

- The panel takes the output control's top and right anchor. Its usual width
  follows the source circle (220–260 logical pixels), with viewport clamping.
  The surface suppresses its output graphic while the menu is visible,
  including closing, and keeps the adjacent controls visible. Closing restores
  the circle and starts the ordinary control idle timeout again.
- Horizontal expansion precedes list unfolding. The same row painter draws
  compressed and expanded states. Bounded row-spacing overshoot and closing
  circle compression retain visible spring settling; native row widgets
  resume only when position and velocity settle. Invisible rows cannot own
  pointer or wheel input. Existing selection, scope, volumes, white-fill
  volume dwell and list boundary feedback remain in place.
- Top placement reanchors on native movement, reappearance and follow ticks.
  Its main shell stays at the physical screen top while width, height and side
  messages animate. Dragging and Free/Finder/Tasker/Stick retain their rules.
  Loading a saved Free position is preserved.
- Window corners keep the artwork radius and fractional alpha coverage.
  Four small physical-pixel patches, with optional one-pixel right and bottom
  fringes, follow actual DPR and odd extents. The native input mask includes
  the antialiased fringe. Fullscreen stays opaque.
- Mouse movement revealing controls captures each overflowing metadata field
  once. After reveal settles, fields still too wide scroll immediately; those
  now fitting wait until controls hide and scroll if then too wide. Title and
  artist remain independent; track changes clear pending work. Automatic
  trips start every 15 seconds including travel time. Hover keeps continuous
  return behavior. Lyric-only paints retain metadata hits and measurements.
- Each GUI tick and paint resolves its lyric source once and reuses it within
  the frame. The next frame checks values again, including in-place row edits.
  Short-song classification stops after four sung rows.

## Evidence and limits

Focused native Windows Qt tests cover source replacement, circle coverage at
the 595 × 333 minimum surface, reversal, input masks, volume/wheel behavior,
top placement and settings load order, per-field scrolling, and cache cleanup
after exceptions. Rounded alpha is compared pixel-for-pixel with a full
antialiased shape at 960 × 540 and 961 × 541, using DPR 1, 1.25, 1.5, 1.75,
2 and 2.625; tiny transitions and repeated partial paints are included.

The same-process synthetic benchmark alternated baseline commit `531aa37`
and updated source. Repeated 960 × 540 long-song results measured median lyric
paint 6.41 → 5.91 ms, P95 8.76 → 8.14 ms; full paint P95 9.94 → 9.18 ms.
Reading-cadence tick P95 was 1.06 → 0.93 ms. These measure Qt raster work,
not compositor FPS. Occasional maximum spikes remain; this does not prove all
desktop stutter disappeared or diagnose audio-buffer underruns.

After the final fractional-DPR correction, another run with no full test suite
active measured repeated median lyric paint 8.49 → 8.17 ms, full paint P95
12.16 → 11.30 ms and reading tick P95 1.75 → 1.33 ms. Lyric P95 was effectively
unchanged (10.88 → 10.89 ms), and occasional maxima remained. Machine load
changed between runs; only each paired comparison is meaningful.

Ignored `logs/fused-output-preview.gif` uses the actual Qt surface and spring
with synthetic routes. It shows continuity and adjacent-control alignment,
not a live connection test. No native helper, audio policy or release package
changes. No casts, recordings or tones ran for this task. Realtek was read
back at zero; the user's existing AirPods output was preserved.

The isolated branch passed all 3357 discovered tests in 103.938 seconds using
native Windows Qt, verbose logging and fault handling. Scoped integration into
`E:/gpt-island` then passed all 3328 discovered tests in 102.006 seconds. The
29-test difference is the pre-existing main-directory baseline. Compilation,
tracked/new-file whitespace checks and integrated source equality passed.
The native helper SHA256 remains
`D358B1F9D4D06F7F45334C72BBB2592FD7CDF4C05BD1AC43738557ED7ADD999A`.
Final readback found Realtek volume 0 and the existing AirPods default output.
The old source instance remains running: desktop automation did not expose
the tray exit entry, and closing only the settings/island window does not
quit the application. A normal tray exit/restart is required to load new
Python modules; no forced termination or new quit interface was used.
The task retains the 100-point process score: interfaces and call sites were
read, behavior follows explicit references, existing providers and motion
boundaries are reused, and native verification is recorded. Hardware smoothness
is a remaining observation, rather than a claimed test result.
