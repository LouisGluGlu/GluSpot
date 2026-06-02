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
- Finder-style material should replicate the visible upper Finder bar material and use that replicated glass texture to fill the fused background. It must not use a full-screen wallpaper crop as the island/cradle background.
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
- Hovering or single-clicking the plugin strip should keep the 48px strip in place and add a separate downward expanded panel below it.
- Expanded plugin panels should present a clear focus item while keeping non-focused entries lightweight.
- Expanded plugin panels should include a contextual action and fold active side reminders into an in-panel reminder rail.
- Each plugin should have a management surface that can grow into real settings and diagnostics instead of remaining a placeholder.
- Side reminder islands must arrange multiple active reminders horizontally, including the circular boundary fallback state. They must never stack circular reminders vertically beside the main island.
- Boundary-aware side reminders should consider the combined footprint of the main island and side reminders before choosing right, left, or circular fallback placement.
- TIME countdown and alarm reminders must keep separate virtual identities so countdown and alarm due states can appear at the same time. Waiting countdowns and alarms use black side islands with yellow icons/text; due countdowns and due alarms use the same yellow side-island shell with black icon/text, where countdown shows `时间到` and alarm shows the alarm note.
- Expanded TIME alarm due confirmation should temporarily take over the alarm module as a large block-level reminder, hiding the alarm list and add action until the user confirms it.
- Starting or running a countdown must not overwrite an already captured alarm due confirmation label or alarm time.
- Confirming an alarm due reminder must suppress the same provider alarm payload until a different alarm arrives or TIME leaves alarm status.
- Finder-style mode must not replace the island's own visual shell. Finder material may frame or sample behind the island, but the music island itself must remain opaque and use the normal island rendering path.
- In Finder-style mode, plugin side reminders keep their own semantic backgrounds while the Finder fused outer material frame expands horizontally to include active side reminders, preventing side islands from floating outside the shared background.

## Boundary And Drag Requirements

- In standalone mode, dragging must clamp the main island to the visible screen on all four edges.
- When the main island has already shrunk to its minimum boundary width, continued dragging toward that screen edge must not move the island farther out of bounds.
- Side reminder islands should transition smoothly when switching sides or changing between rectangular and circular states, using the existing spring motion rather than abrupt jumps.
- Side reminder islands may collapse to circular fallback only at a real boundary pressure state. When the main island is not at a boundary, multiple reminders should remain full rectangular side islands, splitting across both sides if needed.

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
- Continue tuning plugin island visual polish after the first functional expanded panel.
- Expand plugin-specific management diagnostics beyond the current time, weather, and agenda controls.
