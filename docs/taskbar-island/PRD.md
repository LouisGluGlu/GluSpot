# Taskbar Island PRD

Status: current taskbar adaptation specification for the Tasker branch.

## Goal

Build a Windows taskbar-aware Tasker mode for gpt-island's V2 island. The feature should keep real provider-backed media, lyric, message, and plugin behavior, while letting the island settle into a dense Windows taskbar placement when the user intentionally drags it there.

Do not reintroduce the old Pill UI or removed live-media source tree.

## Placement Modes

Settings use one placement mode selector:

- `Finder`: MyFinder/Finder-style placement.
- `Free`: normal floating V2 island.
- `Tasker`: Windows taskbar placement.

Mode rules:

- `v2_location_mode` is the persisted source of truth.
- Legacy Finder/taskbar settings may be read only for migration.
- Finder and Tasker are mutually exclusive.
- If the user manually confirms MyFinder exists, Tasker must be removed from the selectable modes or forced back to Free.
- When a Tasker island leaves the taskbar zone during dragging, it automatically reverts to Free.

## Taskbar Recognition

Recognition starts from Free mode:

- When the user drags the island into the lower screen region, the app checks whether a visible Windows taskbar exists on that screen.
- Recognition follows the lower third behavior and is not exposed as a user-facing threshold setting.
- Lower-screen position alone is not enough: the dragged main island rectangle must contact the visible taskbar rectangle before Tasker preview or docking activates.
- Finder mode and manual MyFinder availability block taskbar recognition.
- Auto-hidden or unavailable taskbar rectangles hide the Tasker preview.

Tasker runtime follow rules:

- Once Tasker is active, fullscreen or maximized foreground windows must not suppress the island.
- Tasker refreshes its position from the current taskbar geometry while idle, so it keeps following taskbar movement.
- When the taskbar auto-hides or returns no visible rectangle, Tasker uses the last known taskbar band as a fallback and stays clamped to the screen edge.
- Tasker follow must preserve the user's horizontal taskbar position ratio rather than recentering.

## Drop Target Behavior

Tasker recognition should feel calm and non-magnetic:

- Do not show a standalone blue placement frame or text prompt.
- Recognition may keep internal taskbar/slot state, but it must not draw a floating "drop here" overlay above the taskbar.
- Empty or crowded taskbars should compute left and right available ranges, not a forced center slot.
- When taskbar components split the usable area, compute the best available left/right range pair based on taskbar occupancy.
- The final accepted drop uses the user's current drag/probe position inside the taskbar track, rather than snapping to a preselected slot center.

## Task Area Awareness

Tasker should prefer the taskbar task-button area instead of blindly using the whole taskbar:

- On Windows, detect task-list child windows when available.
- Detect occupied taskbar component rectangles and cut them out of the task area before choosing a Tasker slot.
- If precise detection is unavailable, reserve leading Start space and trailing tray space before computing the drop zone.
- The first implementation remains non-invasive: it aligns and overlays the island in the taskbar task area; it does not take ownership of Windows taskbar internals.

## Compact Tasker Layout

When Tasker is active:

- Keep the main island and plugin side surfaces inside taskbar-safe height.
- The island can be dragged horizontally inside the taskbar track instead of snapping back to the center.
- The far-left outside edge exposes a thin rounded vertical rail only while hovering the Tasker island or while dragging that rail; after hover leaves, it remains available for 3 seconds so the user can grab it.
- Dragging the left rail adjusts the Tasker island width within the taskbar track.
- Tasker drag and resize movement must clamp the expanded main island against the bottom screen boundary, so body dragging cannot push the island below the screen or make it disappear.
- Use Windows corner policy:
  - Win10: square corners.
  - Win11: small rounded corners.
- Compact album art uses the same radius as the outer Tasker shell.
- Avoid pill-like rounding in Tasker.
- Keep album art, title/lyric, and compact controls readable.
- If the selected taskbar slot is too crowded for the full compact music island, shrink the music island to an album-cover-only form bounded by the slot.
- Pure white is a normal music background style in every state; it uses dark lyric text for readability.
- On pure white, lyric scrolling, bilingual lyric lines, title/artist text, album-island text, and controls use dark text. Wave-stream and Gelv color-fill/highlight effects are disabled on white.
- If taskbar material is detected as light, the default music background may switch to pure white in Tasker.
- All music background styles available to the normal music island must remain selectable in Tasker.
- If music auto-detect is enabled, media metadata alone must not keep the music island visible; the island needs a real live-audio signal from the provider/audio layer.
- Preserve lyric-led behavior and do not introduce a progress-bar-first UI.

## Taskbar Material And Theme

Tasker should visually belong to the current Windows taskbar:

- Sample the taskbar material color/lightness when Tasker docks or refreshes.
- Expanded Tasker outer frames inherit taskbar material tokens for fill, stroke, shadow, and corner policy.
- The expanded material frame wraps the modules; it must not replace the opaque V2 music shell.
- When the settings theme mode changes while Tasker is active, sync the music background to the theme:
  - Light or system-light: pure white.
  - Dark or system-dark: pure black.
- Empty-island flow previews in Settings must show the real selected palette colors, including preset palettes and custom colors.

## Plugin Compact Styles

Collapsed Tasker plugin reminders use compact built-in presentations:

- `Rail`: a pure vertical strip with no border. Black is the base, and the plugin/theme color fills the active/progress/urgent ratio.
- `Block`: a square compact tile with embedded icon, concise value/capacity text, and optional progress ring.

Plugin styles must preserve each plugin's semantic color and state. Obsolete Tasker compact-style controls are not exposed in settings.

## Message Compact Styles

Folded Tasker message notifications use compact built-in presentations:

- `Strip`: side-mounted vertical strips that replace the old circular message dots.
- `Block`: square compact message tiles for low counts.
- `Block` uses the app's solid notification color and app icon/glyph. It must not use a black outer box or the sender's first character as the main identity.
- When block notifications exceed taskbar capacity or message count becomes crowded, they collapse back to strips.
- Strip notifications attach flush to the Tasker island edge without leaving a visual notch in the taskbar.
- Tasker message strips support at most three simultaneous rails.
- Obsolete Tasker message-style controls are not exposed in the compact settings UI.

## TIME Due Alerts

Tasker handles due countdown/alarm alerts as temporary main-island overlays:

- When a TIME countdown or alarm reaches its due state in Tasker, the due presentation temporarily covers the current main island rectangle.
- If music is visible, the due alert overlays the music island; if music is hidden, it overlays the current main island.
- The overlay uses the Tasker corner policy and replaces the old side-island due surface while active.
- Countdown and alarm identities remain separate.

## Expanded Tasker Layout

Expanded Tasker uses the existing Finder-like expansion logic but Windows styling:

- Expanded sections are wrapped in a Windows material outer frame.
- The V2 island shell remains opaque and provider-backed.
- Win10/Win11 corner settings apply to the expanded frame and music shell.
- Expanded panels show existing modules rather than inventing a separate taskbar-only data model.

## Bilingual Lyrics

When the user enables bilingual music lyrics:

- The user can actively set the secondary bilingual line font size.
- Compact and expanded lyric painters must use this configured size.
- Existing secondary weight and spacing settings remain available.

## Settings Organization

Settings should be arranged by function:

- `位置`: placement mode, manual MyFinder confirmation, Tasker corner, topmost, bounds, expand direction, message detection.
- `媒体播放`: music visibility, media source, music background style, lyric flow, bilingual lyric controls, album island controls.
- `插件`: plugin island enablement, three plugin checkboxes, plugin style, time/weather/schedule controls.
- `外观`: theme, palette, font, animation, empty island, side island visuals.
- `弹幕`: barrage behavior.
- `诊断`: diagnostics and debug helpers.

Settings shell requirements:

- The settings window uses rounded outer corners and compact cards.
- The overall minimum size is approximately 80% of the previous shell.
- Setting rows are about 30% shorter while preserving readable text.
- The top-left traffic controls show close/minimize/maximize symbols while hovered.

## Data And Provider Boundary

- Fake and real data remain behind `api/providers.py`.
- Tasker consumes the same `IslandVisualData` and `TrackData` contract as the normal island.
- If real media is unavailable, show the existing empty/diagnostic state adapted to Tasker size.
- Do not restore removed media source trees unless rebuilt as a V2 provider.

## Acceptance Criteria

- Settings expose `Finder / Free / Tasker` modes, not separate Finder fusion and taskbar fusion toggles.
- Manual MyFinder blocks Tasker selection and forces Tasker back to Free.
- Free-mode dragging into the lower taskbar region does not show a standalone blue placement frame or text prompt.
- The drop zone uses taskbar task-area detection when available and reserves Start/tray space otherwise.
- Tasker preview requires real island/taskbar contact and does not activate from lower-screen position alone.
- Once Tasker is active, fullscreen/maximized foreground windows do not hide it.
- Tasker follows taskbar movement and auto-hide fallback geometry without losing its horizontal position.
- Tasker computes left/right available taskbar ranges and does not force a center slot.
- Accepting the drop switches to Tasker and aligns the island by the current drag/probe position without magnetic snap.
- Tasker can be dragged left/right inside the taskbar range after docking.
- The external left rail handle appears only on Tasker hover/rail drag, remains reachable for 3 seconds after hover, and can adjust Tasker width without exceeding the taskbar track.
- Tasker drag/resize clamps the expanded main island to the screen bottom.
- Dragging Tasker away from the taskbar zone returns it to Free.
- Expanded Tasker outer frames inherit sampled taskbar material.
- Music background style is the settings name; pure white is selectable in every state, uses dark lyrics/text, and disables white-background scrolling color-fill/highlights.
- All normal music background styles are available in Tasker.
- Theme-mode changes in Tasker map light/system-light to pure white and dark/system-dark to pure black.
- With music auto-detect enabled, real no-audio state hides the music island even if media metadata still exists.
- Congested taskbar slots shrink the music island to an album-cover-only form.
- Tasker compact and expanded states use Win10/Win11 corner policy.
- Tasker album cover radius matches the outer shell radius.
- Plugin selection is controlled by `v2_enabled_plugins` (`time`, `weather`, `agenda`) rather than a plugin-count slider.
- Plugin compact `Rail` and `Block` presentations preserve plugin semantics.
- Message compact `Strip` and `Block` presentations keep crowded blocks collapsing to strips, cap Tasker message rails at three, and avoid taskbar edge gaps.
- Message blocks use app-colored icon blocks rather than black boxes with sender initials.
- TIME due alerts temporarily overlay the main island in Tasker.
- Bilingual lyric font size is user configurable.
- Empty-flow preview colors reflect the selected real palette.
- Settings are grouped by functional pages, use the compact rounded shell, and show hover symbols on the top-left window controls.
