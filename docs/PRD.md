# gpt-island PRD

## Goal

Build a Windows music island that feels alive with the current song. The island should read real media state, use the album art palette, resolve lyrics, and render a lyric-forward experience without exposing a progress bar.

## Product Direction

The music island is the primary product surface. It should behave like a compact, floating music object: album art and controls stay readable, the background carries the album colors, and the lyrics move with the song.

The desired lyric behavior is Apple-like live lyric motion rather than a standard playback timeline. The active lyric line should be selected by the current playback position, then rendered with motion, scale, opacity, and soft light changes that make the line feel sung in real time. When only line-level LRC timing is available, the UI may estimate in-line phase, but it must not pretend to be accurate word-level karaoke.

## Core Requirements

- Start from `local/main.py`.
- Use `WindowsMediaProvider` as the real media source. When real media is unavailable, show a real empty/diagnostic state instead of sample songs.
- Keep fake data only for standalone visual preview code.
- Keep fake and real data behind the provider layer.
- Keep the current V2 island, not the old Pill UI.
- Keep playback controls: previous, play/pause, next.
- Use album art colors for the music island ambience.
- Keep the `sea_level` ambience style as the default album-color background.
- Remove visible progress-bar behavior from the target lyric experience.
- Use playback position to select the active lyric line.
- Use lyric offset setting for manual correction.
- Cache local lyrics in `lyrics/`.
- Keep Settings available from the tray icon.

## Lyric Experience

The lyric island should have three levels of fidelity:

1. Line-synced lyrics: use LRC timestamps to select the active line and calculate a soft phase until the next line.
2. Enhanced line motion: use the phase to drive scale, opacity, vertical easing, glow, and background pulses.
3. Word-synced lyrics: only enable true per-word progress after the data model supports word timestamps.

The UI should avoid a literal progress bar. Song progress should be expressed through active-line motion, gentle text emphasis, background breathing, and soft highlight movement.

## Visual Requirements

- Background color should cover the whole island, not sit as a hard bottom strip.
- Color transitions must be soft, blurred, and mixed.
- Avoid sharp light balls, hard color bands, blinking, and obvious layer seams.
- Compact and expanded music islands may use separate album-color duotone balance settings. The balance should affect soft color weighting, not create a hard split.
- Active lyric text should be readable on bright and dark album palettes.
- Lyric movement should feel continuous even when media position updates arrive in coarse intervals.
- The island should remain compact and usable when lyrics are unavailable.
- Expanded lyrics should keep the previous and next lines visible around the active line when space allows.
- When one timed lyric entry contains an intentional two-line lyric, compact and expanded layouts should preserve the two-line reading instead of flattening it into one crowded line.
- Do not infer simultaneous two-line lyrics from adjacent lyric lines. Adjacent lines are usually upcoming lyrics unless an enhanced lyric source proves they are sung together.

## Plugin Experience

- The plugin island should feel like a compact modern dashboard strip rather than a legacy pill cluster.
- Compact plugin cells should emphasize icon, one-line summary, and a subtle colored accent.
- Expanded plugin cells should present a clear focus item while keeping non-focused items lightweight.
- Each plugin should have a management surface that can grow into real settings and diagnostics instead of remaining a placeholder.

## Data Requirements

- `TrackData` should remain the shared contract for media state.
- Current line-level lyrics use `TimedLyricLine`.
- Future word-level lyrics need a new structured model instead of overloading plain LRC lines.
- Media progress should be smoothed locally between provider refreshes.
- Provider refresh jitter must not directly shake lyric rendering.
- NetEase Cloud Music may expose no Windows playback position. In that case, use its visible lyric page as a real line anchor when available instead of inventing progress.
- Media provider failures should be visible as dependency/no-session states, not hidden behind sample content.
- Audio-reactive movement should use loopback audio only when real audio capture is available; otherwise it should fall back to calm idle motion, not fake rhythm.

## Non-Goals

- Do not rebuild the removed Pill UI.
- Do not restore removed live-media source trees unless rebuilt as a provider.
- Do not fake word-perfect karaoke from line-level LRC data.
- Do not use a visible music progress bar as the main lyric solution.

## Todo

- Add compact and expanded duotone balance controls for the music island, with animated transitions and no hard color seam.
- Preserve intentional two-line lyric entries in compact and expanded layouts when the lyric source provides explicit line grouping.
- Add a structured enhanced-lyrics parser for NetEase YRC/translated lyric pairing before attempting duet-style simultaneous two-line display.
- Use a temporary compact island height increase for current two-line lyrics rather than spawning a second island by default.
- Keep previous and next lyric lines visible in expanded mode while preserving the active lyric highlight.
- Modernize plugin island visuals.
- Start replacing plugin placeholder windows with useful plugin-specific management panels.
