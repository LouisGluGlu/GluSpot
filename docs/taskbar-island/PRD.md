# Taskbar Island PRD

Status: first direction document for the taskbar fusion branch. This is a product/design specification, not an implementation pass.

## Goal

Build a Windows taskbar-embedded music island inspired by Soda Music's taskbar placement logic, while keeping gpt-island's own V2 island identity: real Windows media state, album-art ambience, lyric-led motion, compact plugin surfaces, and no old Pill UI.

Taskbar fusion is a constrained Windows-taskbar mode. It should recognize when the user places the island into the Windows taskbar area, compress the main music island and secondary/plugin islands into taskbar-safe dimensions, and provide a first version of a music-first taskbar layout.

## Mode Relationship

Finder fusion and taskbar fusion are mutually exclusive.

- In Settings, Finder mode is named `Finder融合`.
- When `Finder融合` is enabled, the user is assumed to be using MyFinder and naturally hiding the Windows taskbar.
- With `Finder融合` enabled, taskbar fusion must not activate because the Windows taskbar is not the active placement target.
- When `Finder融合` is disabled, the user is assumed to be using the normal Windows taskbar.
- With `Finder融合` disabled, dragging/placing the island into the taskbar area may trigger taskbar fusion recognition.

The app should keep these states distinct:

- Standalone floating island.
- Finder fusion floating island for MyFinder users.
- Taskbar fusion island for Windows-taskbar users.

## Taskbar Recognition

When `Finder融合` is disabled, the app should detect taskbar placement by the island's position relative to the visible Windows taskbar/work area.

First-version behavior:

- Recognize placement near the Windows taskbar edge.
- Snap or settle into a taskbar-safe compact layout after the user intentionally places the island there.
- Avoid accidental activation during ordinary dragging.
- Keep normal boundary clamping behavior outside the taskbar zone.
- Account for monitor edge and taskbar orientation in later implementation planning.

Soda Music is the placement reference: the control should feel like it belongs inside the taskbar strip, with album art, track/lyric text, and compact playback controls aligned as a single dense bar.

## Compact Taskbar Layout

When taskbar fusion is active:

- Shrink every island surface, including the main music island and plugin side islands.
- Constrain all taskbar surfaces to taskbar-safe bounds.
- Fit the main island height to the Windows taskbar height with a small top and bottom inset.
- Change the main island corner radius from pill-like rounding to album-cover-like rounding.
- Keep album cover visible as the leading anchor.
- Keep previous, play/pause, and next controls compact and readable.
- Treat the favorite-like control from the Soda Music reference as visual inspiration only unless later explicitly scoped.
- Keep track title and active lyric text concise; use single-line clipping/marquee behavior when space is tight.
- Do not reintroduce visible progress-bar behavior.
- Convert side reminders/plugins into taskbar-native compact forms: badges, small chips, or a secondary rail designed specifically for this mode.

## Plugin Direction

Plugin taskbar style should not copy Soda Music. Soda Music is only the music-control placement reference.

gpt-island plugin behavior in taskbar fusion should:

- Preserve each plugin's semantic meaning and accent color.
- Avoid full floating reminder shapes inside the taskbar strip.
- Use compact icon-plus-signal forms for active reminders.
- Keep due/urgent states obvious without oversized blocks.
- Expand into a custom gpt-island panel rather than mimicking a music app popup.
- Keep TIME countdown/alarm identity isolation from the main PRD.

## Expanded Taskbar Layout

When the taskbar island expands:

- Keep the compact taskbar strip stable.
- Expand from the taskbar edge into a separate panel above the Windows taskbar.
- Use a taskbar-specific fused background treatment rather than Finder fusion.
- Do not reuse MyFinder material assumptions when Finder fusion is disabled.
- Preserve the V2 music island shell and lyric-led behavior.
- Keep album-color ambience in the music surface.
- Keep previous and next lyric context visible when space allows.
- Do not use a literal progress bar as the main lyric solution.

The expanded panel may borrow the idea of fused material continuity, but it must be implemented as a Windows-taskbar mode surface, not as Finder material replication.

## Visual Rules

- Compact taskbar shape: taskbar-height rounded rectangle with album-cover-level corner radius.
- Avoid full-pill rounding in taskbar fusion.
- Avoid old Pill UI layout.
- Avoid fake media, fake rhythm, or sample songs outside provider-backed preview paths.
- Keep `sea_level` album-color ambience as the music default unless a later taskbar-specific balance is documented.
- Use the existing icon language; do not use emoji UI.
- Keep text readable on bright and dark album palettes.
- Make the taskbar version feel embedded, dense, and useful, but still recognizably gpt-island.

## Data And Provider Boundary

- Real and fake data remain behind `api/providers.py`.
- Taskbar fusion consumes the same real `TrackData` contract as the normal island.
- If real media is unavailable, show the existing real empty/diagnostic state adapted to taskbar size.
- Do not invent word-perfect karaoke from line-level LRC data.
- Do not restore removed live-media source trees unless rebuilt as a V2 provider.

## First-Version Acceptance Criteria

- `Finder融合` enabled: taskbar fusion does not activate.
- `Finder融合` disabled: placing the island in the Windows taskbar area is recognized.
- Taskbar compact layout respects taskbar height with visible top/bottom inset.
- Main island radius changes from pill-like rounding to album-cover-like rounding.
- Main and side/plugin islands shrink together and stay within taskbar-safe bounds.
- Music layout references Soda Music placement logic without copying plugin design.
- Expanded taskbar panel uses a Windows-taskbar-specific fused surface, not Finder material replication.
- V2 island shell, real provider data, and lyric-led no-progress-bar behavior are preserved.

## Open Decisions

- Exact taskbar detection rule: threshold, snap timing, monitor edge handling, and taskbar orientation.
- Whether auto-hidden Windows taskbar should disable taskbar fusion or use a separate activation rule.
- Exact compact height formula: fixed inset versus proportional inset based on taskbar height.
- Minimum readable widths for album art, lyric text, title text, and controls.
- First plugin compact form: badges, chips, or secondary rail.
- Expanded panel geometry: width, edge anchoring, and whether plugin panel expansion shares the music panel or opens a separate strip.
- Whether taskbar fusion needs a dedicated album-color balance setting after compact/expanded duotone balance lands.
