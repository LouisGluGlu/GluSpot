# gpt-island Issues

## P0

- Add the `格律宋散` text-flow style as separate from the existing `波光逐流` style, including settings exclusivity with the old dual-line lyric option.
- Add `格律宋散` long-lyric split behavior instead of scrolling: split by chunks/words, center both lines, hide the smaller next-lyric row while split.
- Add `格律宋散` `逐字染色` mode: pure black music background, album-color high-saturation fill, and `#00D7FF` fallback for neutral album colors.
- Add no-lyrics album island mode for instrumental/no-lyrics/user-disabled lyrics, with 3/5/7 cover count and queue/history fallback.
- Define taskbar fusion as a separate Windows-taskbar mode: `Finder融合` enabled must block taskbar fusion; `Finder融合` disabled may recognize taskbar placement.
- Keep the lyric-led motion system free of visible progress-bar and drag-progress behavior.
- Keep one timeline module as the owner of active-line selection and line phase.
- Preserve intentional two-line lyric entries in compact and expanded layouts without changing the provider data contract.
- Do not use visible lyric-page UIA rows as playback anchors; they can be stale on non-lyric pages and during lyric dragging.
- Keep previous and next lyric lines visible in expanded mode while preserving active-line emphasis.
- Smooth provider playback position before it reaches lyric rendering.
- Keep app/settings/provider UI text encoded as UTF-8.
- Keep `track_id` stable when duration is missing or jittery.
- Decide the exact no-lyrics state for the expanded music island.
- Tune diagnostics wording for Windows media access, dependency state, and audio capture state.
- Verify NetEase lyric-page anchoring against mini-player, full lyric page, and non-lyric pages.
- Fix plugin side-reminder regressions: extreme boundary/floating layouts must not overlap, split into half-width remnants, or stack circular reminders vertically.
- Keep TIME reminder state isolated so a nearby countdown/alarm cannot reuse the abandoned yellow-background waiting style.
- Keep expanded TIME alarm due presentation isolated from the scrollable alarm list and add button.
- Fix Finder fused material painting so the MyFinder-derived cradle does not show a second standalone island layer.
- Fix severe Finder regression where Finder material replaced the island shell and made the original island transparent.
- Fix severe Finder material sampling so fused Finder backgrounds replicate the upper Finder bar material instead of cropping wallpaper/screen content.
- In Finder mode, expand the replicated Finder outer frame around plugin side reminders while preserving each side reminder's own semantic background.
- For taskbar island compact mode, shrink the main and side islands together, constrain them to taskbar-safe bounds, adapt height to the taskbar with small vertical insets, and change the main island radius from pill-like rounding to album-cover-like rounding.
- Keep taskbar fusion design docs isolated under `docs/taskbar-island/` until a code-ready implementation plan is accepted.
- Fix side-reminder fallback rules so reminders do not collapse before a real boundary and circular fallback keeps icons centered.

## P1

- Add visual regression coverage for compact and expanded taskbar island states once the screenshot harness is stable.
- Add separate compact and expanded music island duotone balance controls with animated color weighting.
- Parse enhanced lyric structures such as NetEase YRC/translated lyric pairing before enabling duet-style simultaneous two-line display.
- Continue polishing plugin island compact strip and separate down-expanded panel visual styles after the first functional panel pass.
- Add real word-level lyric data models and rendering after a source with word timestamps is wired in.
- Render Apple-like lyric motion: active-line lift, breathing scale, opacity falloff, soft glow, and background pulse.
- Preserve rich lyric word timing instead of downgrading YRC/enhanced LRC to line timing.
- Improve album-color selection for low-saturation covers.
- Keep `sea_level` ambience as the default and tune it against bright covers.
- Add visual regression coverage for TIME side reminders, expanded alarm due confirmation, and Finder fused mode once screenshot harness is stable.

## P2

- Expand plugin manager diagnostics and real controls after the initial time, weather, and agenda panels.
- Add provider fixtures for paused, seeking, unknown duration, no lyrics, line-synced lyrics, and plain lyrics.
- Add visual regression screenshots for compact and expanded music states.
- Expand diagnostics with manual refresh of current track, lyric source, active line, position drift, media provider state, and audio capture state.
- Package the app without committing local virtual environments.
