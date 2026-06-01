# gpt-island Issues

## P0

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
- Fix expanded TIME alarm due presentation so the pending confirmation block and scrollable alarm list do not overlap the add button.
- Fix Finder fused material painting so the MyFinder-derived cradle does not show a second standalone island layer.
- Fix severe Finder regression where Finder material replaced the island shell and made the original island transparent.
- Fix severe Finder material sampling so fused Finder backgrounds replicate the upper Finder bar material instead of cropping wallpaper/screen content.
- In Finder mode, expand the replicated Finder outer frame around plugin side reminders while preserving each side reminder's own semantic background.
- Fix side-reminder fallback rules so reminders do not collapse before a real boundary and circular fallback keeps icons centered.

## P1

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
