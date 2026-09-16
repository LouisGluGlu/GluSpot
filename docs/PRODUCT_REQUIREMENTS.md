# GluSpot Product Requirements Document

Status: Draft for user review
Owner: GluSpot project
Last updated: 2026-06-28
Source of truth inputs: current README, current implementation behavior, current user feedback, current bug reports, and the agreed workflow of PRD -> Pencil UI/UX review -> implementation -> testing.

## 1. Problem Statement

GluSpot has grown from a Windows floating music island into a mixed desktop product containing music display, lyric motion, album art, background ambience, taskbar fusion, plugin reminders, barrage, message notifications, settings, diagnostics, packaging, and optional app-specific lyric sync. The current product direction is valuable, but the implementation and product surface are no longer sufficiently separated.

The user-facing symptoms are:

- Music-reactive backgrounds can look gray, muddy, twitchy, visually discontinuous, or detached from the selected background style.
- Some styles, especially sea-level and full-duotone, do not feel like their own native motion; they look like generic effects overlaid on top.
- Lyric follow can lag, stutter, or visually freeze, with Ember Fade showing the issue most obviously.
- Album art can darken when message popups or overlay surfaces mount.
- Compact, expanded, Tasker, Finder, and Fullscreen Music Mode do not yet have distinct performance and rendering budgets.
- Settings expose many controls, but product intent and user workflows are not clear enough.
- The implementation tends to mix product state, rendering decisions, Qt painting, lyric synchronization, overlay behavior, and diagnostics in the same surfaces.

The product needs a reset at the specification level before further visual or code work. The next phase must define the product shape, UI/UX, architecture seams, visual acceptance criteria, migration strategy, and verification gates before more implementation.

## 2. Product Positioning

GluSpot is a Windows desktop music companion centered on a compact live island. It enhances existing music playback with readable album art, lyrics, controls, audio-reactive ambience, notifications, and optional taskbar fusion.

GluSpot is not currently positioned as a full replacement music player. Its baseline model is companion playback through Windows media sessions and optional app-profile integrations. Fullscreen Music Mode is part of GluSpot Next, but it must be designed as a deliberate music mode with its own UI, performance budget, and playback constraints. It must not be a stretched version of the compact island.

### Product Promise

GluSpot should feel like a small, living desktop object that stays readable, calm, and useful in normal work, while becoming more expressive when expanded.

### Product Principles

- The compact island prioritizes low distraction, low resource use, quick glance value, and reliable lyric follow.
- The expanded island prioritizes richer controls, stronger visual identity, clearer lyrics, and more expressive audio-reactive motion.
- Fullscreen Music Mode is a required GluSpot Next product mode. It prioritizes immersive playback, richer album/lyric visuals, queue context, and deliberately higher resource use while active.
- Music-reactive effects must be style-native. Each background style owns its own motion model.
- Lyrics and album art are primary content. Backgrounds and overlays must not pollute or obscure them.
- Notifications and plugin surfaces are overlays. They must not mutate album art, background, lyric, or media state.
- Settings should expose meaningful product choices, not every internal rendering variable.
- Every visible feature must have a defined verification path: automated tests where possible, plus screenshot, recording, or runtime diagnostics where required.

## 3. Strategic Decision: Current Repo vs New Project

The project should not immediately move to a disconnected new repository. The current repository contains valuable product rules, Windows integration, provider work, settings, packaging, assets, tests, and accumulated behavior knowledge.

The project also should not continue as direct patching inside the existing large visual and settings surfaces.

The required path is GluSpot Next:

- Keep the current repository as the product asset base and migration environment.
- Write the full PRD in the current repository.
- Design the future UI/UX in Pencil before implementation.
- Build new architecture through a clean implementation channel inside the current repository or an isolated worktree/branch.
- Preserve the current runnable app as the baseline until the new surface proves replacement readiness.
- Use strangler migration: replace one vertical slice at a time after the new slice passes acceptance criteria.

An independent new project becomes justified only if one of these decisions is made:

- GluSpot changes from a Windows media companion into a full embedded playback application with third-party web login as the primary playback model.
- The implementation stack moves away from the current Windows/PySide direction.
- Existing assets, tests, provider behavior, settings migration, packaging, and Windows integration are no longer strategic assets.

Until that decision is explicit, GluSpot Next should live in the current project context.

## 4. Target Users

Primary users:

- Windows desktop users who play music while working and want a small high-quality live music surface.
- Users who care about lyric display, album art, visual ambience, and quick playback control.
- Users who want a polished desktop object that can sit free-floating, near the taskbar, or integrated with a dock/taskbar-like workflow.

Secondary users:

- Users who want notification and reminder surfaces while music is visible.
- Users who want visually expressive music experiences when intentionally expanded.
- Testers and maintainers validating media integrations, lyrics, and real desktop behavior.

Non-primary users:

- Users looking for a full music library manager.
- Users looking for a streaming-service replacement client.
- Users who require word-perfect karaoke before a structured word-timestamp model exists.

## 5. Product Modes

### 5.1 Compact Island

Compact Island is the default always-available surface.

Goals:

- Show current media state quickly.
- Keep album art and lyric text readable.
- Provide basic playback controls without visual clutter.
- Use low CPU/GPU budget.
- Support low-amplitude, style-native ambience.
- Avoid large layout shifts.

Must support:

- Album art.
- Track title and artist where space allows.
- Current lyric or album island fallback.
- Play/pause and basic controls where enabled.
- Background style selection.
- Optional music-reactive background.
- Notification/plugin overlay coexistence without darkening album art.

### 5.2 Expanded Island

Expanded Island is an intentional user action that permits richer visuals and controls.

Goals:

- Show larger album art and lyrics.
- Make background style more visible.
- Expose richer playback controls.
- Allow stronger music-reactive motion.
- Keep lyrics as the visual priority.

Must support:

- Larger lyric layout.
- More visible album art.
- Clear playback controls.
- More expressive but non-polluting background motion.
- Queue/context surface only if it does not overload the island.
- Higher animation budget than Compact Island.

### 5.3 Fullscreen Music Mode

Fullscreen Music Mode is a required GluSpot Next mode, not a resized island. It is entered from the island as an explicit expansion action and returns to the compact or expanded island without leaving background loops, high-cost rendering, or fullscreen-only state active.

Goals:

- Provide an immersive music experience.
- Use album art, lyrics, queue/context, and audio-reactive backgrounds at larger scale.
- Permit higher rendering cost while active.
- Make exit and collapse obvious.
- Keep the compact island lightweight after collapse.
- Make the current track, lyric line, and playback state immediately legible at room-view distance.
- Provide a designed place for stronger audio-reactive visuals without polluting compact mode.

Must support:

- Pencil-reviewed UI/UX.
- Explicit entry from Compact Island and Expanded Island.
- Explicit exit back to island mode.
- Large album art or album-art-derived visual field.
- Large lyric focus with current line, surrounding lines, and instrumental/no-lyrics fallback.
- Playback controls with visible state.
- Queue or context rail if it does not compete with lyric focus.
- Music-reactive background at fullscreen scale, driven by the same reactive frame contract.
- Distinct performance budget and diagnostics.
- Real screenshot/recording acceptance before claiming completion.

Non-goals:

- It is not a replacement for the compact island.
- It is not allowed to keep compact mode in high-cost rendering after exit.
- It is not allowed to silently switch the product into third-party web playback.
- It is not allowed to reuse compact island layout by simple scaling.

### 5.4 Settings Window

Settings is an operational surface, not a showcase.

Goals:

- Help users configure startup, placement, media behavior, lyrics, ambience, plugins, notifications, diagnostics, and about/build information.
- Group controls by user task.
- Hide advanced tuning behind explicit advanced sections.
- Prevent accidental wheel changes.
- Keep naming aligned with GluSpot.

### 5.5 Notification And Plugin Overlay Surfaces

Overlays include message notifications, plugin reminders, barrage, fullscreen alerts, and preview surfaces.

Goals:

- Notify without corrupting media visuals.
- Keep overlay identity separate from album art and background state.
- Support fullscreen and Tasker behavior without suppressing unrelated surfaces.
- Maintain clickable regions only where interaction is explicit.

## 6. Placement Modes

Placement modes define where the island lives. They must not redefine media state, lyric state, or overlay ownership.

### 6.1 Free

Normal floating island. Supports compact and expanded behavior.

### 6.2 Finder

Dock-adjacent mode used only when Finder-like shell availability is detected or explicitly allowed. Finder material may frame the island, but the island remains its own rendering surface.

### 6.3 Tasker

Taskbar fusion mode. The island follows taskbar position and taskbar constraints. Tasker keeps its visibility rules separate from fullscreen alerts and barrage.

### 6.4 Stick

Internal bottom-edge behavior for auto-hide taskbar flows. It should remain a placement behavior, not a separate product mode.

## 7. Playback Model

### 7.1 Baseline Playback

The baseline playback model is companion mode:

- GluSpot reads real media state from the operating system media session provider.
- GluSpot sends supported playback commands through the current media provider contract.
- No active media should produce fake sample tracks in normal runtime.

### 7.2 App-Specific Integrations

NetEase, BetterNCM, QQ, Qishui, Kugou, and similar integrations are optional attachments.

Rules:

- Disabled integrations must not keep polling loops, handles, plugin hooks, or lyric-sync memory active.
- Integrations must be configured explicitly.
- Generic Windows media remains the fallback route.
- App-specific position correction must not mutate unrelated media state.

### 7.3 Web / Third-Party Intermediate Player Decision

The idea of becoming a third-party web-login intermediate player remains a strategic option, not the current baseline.

Before adopting it, the project must answer:

- Which service(s) are embedded or authenticated?
- Is playback inside GluSpot or merely controlled by GluSpot?
- What are the legal, privacy, session, and distribution risks?
- How are lyrics, album art, queue, and controls retrieved?
- What is the resource budget in compact, expanded, and fullscreen modes?
- Can compact mode still remain low-cost?

If the product chooses intermediate-player mode, it should be scoped as GluSpot Player Mode V2/V3, not silently mixed into the companion provider.

### 7.4 Fullscreen Music Playback Rule

Fullscreen Music Mode uses the same playback model as the rest of GluSpot unless a separate intermediate-player decision is approved.

Rules:

- Companion playback remains the baseline in fullscreen.
- Fullscreen can show richer controls, queue context, lyrics, and visuals.
- Fullscreen cannot invent a new provider path that Compact Island does not understand.
- If future embedded playback is approved, it must be introduced behind a provider adapter and tested independently from the fullscreen UI.

## 8. Media And Data Interfaces

The product should retain a provider seam for fake and real media data.

Core media contract:

- Track identity.
- Title, artist, album where available.
- Playback state.
- Position and duration where available.
- Album art bytes or stable album art handle.
- Playback command capability.
- Source/provider identity.

Provider rules:

- Fake media remains test-only.
- Real media comes through provider adapters.
- Provider adapters should not know how the island paints itself.
- Visual modules should not know provider implementation details.

## 9. Lyric Experience

### 9.1 Product Goal

Lyrics should feel Apple-like: alive, smooth, readable, and synchronized enough for line-level LRC.

### 9.2 Line-Level Source Of Truth

Current lyric data is line-level. The product must not promise word-perfect karaoke without a structured word-timestamp model.

Line-level behavior:

- The active lyric line is selected from media position and timed lyric lines.
- Visual styles may create soft progression, scale, opacity, blur, or breathing.
- Visual styles must not mutate synchronized lyric index.
- Style-local offsets may affect paint state but must not rewrite the playback timeline.

### 9.3 Future Word-Level Lyrics

Word-level lyric behavior requires a new structured model.

Requirements:

- Per-word or per-syllable timestamps.
- Clear fallback to line-level.
- Separate tests for line selection and word progress.
- Separate UI copy so users know whether a track supports word-level timing.

### 9.4 Lyric Styles

Current and future lyric styles should remain separate from background styles.

Known lyric styles:

- Wave Stream.
- Gelv Songscatter.
- Ember Fade.

Rules:

- A lyric style can influence text motion, opacity, typography, blur, and line layout.
- A lyric style cannot own music provider timing.
- A lyric style cannot force background renderer state.
- A lyric style cannot darken album art or overlay surfaces.

### 9.5 Lyric Performance Requirements

Lyrics must remain responsive under music-reactive backgrounds.

Acceptance:

- No visible line-follow freeze during normal playback.
- No repeated visual stutter caused by background rendering load.
- Positive or negative style offset must not desynchronize underlying media position.
- Diagnostics must expose whether lag is from provider position, lyric timeline, paint budget, or reactive effects.

## 10. Album Art Experience

Album art is primary content.

Rules:

- Album art must not darken because a message popup, plugin overlay, barrage, or settings preview mounted.
- Overlay dimming must be scoped to the overlay surface.
- Album art updates should reuse unchanged art and avoid redundant decoding.
- Album art fallback should be visually intentional, not a blackened broken state.

Acceptance:

- When a message notification appears, album art maintains its expected brightness unless the selected product mode explicitly dims the whole island.
- If whole-island dimming is introduced, it must be a named state with user-visible purpose, not a side effect.

## 11. Music-Reactive Backgrounds

### 11.1 Product Goal

Music-reactive backgrounds should make the island feel alive without harming lyric readability or album art clarity.

They must be style-native:

- Do not add a generic flashing template on top of all backgrounds.
- Do not create gray wash, black flash, muddy overlays, or visual discontinuities.
- Preserve the lyric center by default.
- React smoothly to audio energy, frequency, and volume without twitching.

### 11.2 Shared Reactive Inputs

Reactive visuals should consume a single smoothed music reactive frame.

The frame should provide:

- Low, mid, and high bands.
- Energy.
- Beat or pulse drive.
- Glow drive.
- Lift/bass drive.
- Frequency emphasis where available.
- Staleness/cache status.
- Whether values are live, cached, fallback, or disabled.

Rules:

- Background renderers consume the reactive frame.
- Background renderers do not sample audio directly.
- Render cadence and audio sampling cadence remain separate.
- Compact mode can use cached/smoothed low-cost values.
- Expanded/fullscreen modes can use richer live analysis when enabled.

### 11.3 Sea-Level Background

Sea-level must feel like water.

Native motion:

- Lower water band motion.
- Layered waves with smooth phase movement.
- Tidal rise/lift from low-frequency energy.
- Crest shimmer in the lower region.
- Minimal center pollution.

Anti-goals:

- No twitching blob.
- No vertical slab pulsing.
- No generic flash overlay.
- No frame-dropping appearance.

Acceptance:

- The lower water area moves continuously across frames.
- Bass/low energy visibly affects wave amplitude or tide.
- Lyric center remains readable.
- Compact mode remains subtle; expanded mode is more expressive.

### 11.4 Full-Duotone Background

Full-duotone must keep its two-zone identity.

Native motion:

- Upper/lower color fields remain legible.
- Split transition breathes softly.
- Side interference and wave energy can move at the edges.
- Energy does not erase the two-color structure.

Anti-goals:

- No uniform whole-island flicker.
- No central mud.
- No effect that makes the style indistinguishable from other backgrounds.

Acceptance:

- Low/mid/high differences produce visible but controlled changes.
- The split band remains recognizable.
- Lyrics remain readable.

### 11.5 Bilateral Diffraction Background

Bilateral diffraction should prioritize left and right frequency waveforms.

Native motion:

- Left and right waveforms or diffraction rings driven by frequency response.
- Center remains protected for lyrics and album art.
- Ring speed and amplitude reflect energy without jerky jumps.
- Expanded mode can show richer side-wave geometry.

Anti-goals:

- No center-heavy rendering as the primary effect.
- No barely visible motion.
- No one-frame pulse look.

Acceptance:

- Left and right sides visibly respond to audio.
- The center remains visually calm.
- Motion remains smooth at target frame cadence.

### 11.6 Pure Black And Pure White Backgrounds

Pure backgrounds should not become visually dead when reactive mode is enabled.

Native motion:

- Use extracted album/accent color, adapted for surface brightness.
- On pure black, accent can be richer but must not overpower lyrics.
- On pure white, accent should use light, transparent, color-safe motion.
- Avoid black flash on white surfaces.

Acceptance:

- Reactive motion is visible on both black and white.
- White surfaces never black-flash because of alpha/multiply mistakes.
- Text contrast remains correct.

### 11.7 Yaoguang Background

Yaoguang keeps its layered-rectangle identity.

Native motion:

- Blur layer intensity or inner band presence may breathe.
- The crisp black outer frame remains stable.
- Album-derived or inverse colors remain intentional.

Acceptance:

- Motion strengthens the layered design instead of covering it.
- Compact and expanded variants remain distinct.

## 12. Visual Design Requirements

The next UI must be designed before implementation.

The design workflow is:

1. Produce PRD.
2. Derive UI/UX flows and screens.
3. Design in Pencil.
4. User reviews and approves Pencil design.
5. Convert approved design into implementation tasks.
6. Implement.
7. Test with screenshots, runtime diagnostics, and automated tests.

Pencil design must include:

- Compact Island.
- Expanded Island.
- Fullscreen Music Mode as a core screen, not only a concept.
- Music-reactive background states for each major style.
- Lyric style examples.
- Album island fallback.
- Message notification overlay.
- Plugin reminder overlay.
- Tasker placement.
- Settings information architecture.
- Diagnostics/export surface.

Design acceptance:

- No nested decorative card stacks for the primary island surface.
- Controls use familiar icon affordances where appropriate.
- Text must fit in compact, expanded, and fullscreen layouts.
- Music visual effects must not obstruct primary content.
- Settings surfaces should be dense, readable, and operational rather than marketing-like.
- Fullscreen Music Mode must feel like a dedicated music surface, not a stretched compact island.
- Fullscreen Music Mode must show how the user exits back to the island.
- Fullscreen Music Mode must reserve the lyric center from reactive visual pollution.

## 13. Notification And Plugin Requirements

Notifications and plugins must be separate product surfaces.

### 13.1 Message Notifications

Supported message detection includes WeChat, QQ, and DingTalk where configured.

Rules:

- Basic notification toggles and detail notification toggles remain separate.
- Message popups do not mutate album art, lyrics, or background state.
- Clickable regions are explicit.
- Message display must not steal focus in normal mode.

### 13.2 TIME, Weather, And Agenda

TIME remains the default plugin surface.

Weather and agenda remain optional until real-world QA is complete.

Rules:

- Countdown and alarm due states keep separate identities.
- Plugin due surfaces can render as island card, toast card, or barrage depending on settings and fullscreen rule.
- Plugin surfaces do not change island visibility rules except through defined overlay policy.

### 13.3 Barrage And Fullscreen Alerts

Barrage and fullscreen plugin alerts remain separate decisions:

- Island visibility rule.
- Barrage rule.
- Plugin alert rule.

Acceptance:

- Barrage preview in settings does not force island suppression.
- Tasker can remain visible while fullscreen barrage works.
- Plugin alerts in fullscreen do not corrupt music surface state.

## 14. Settings Requirements

Settings must be organized by user intent.

Primary settings groups:

- Basic settings: theme, startup, placement, empty island behavior, font, animation.
- Media control: provider/player mode, app profiles, lyric style, lyric offsets, album island, ambience.
- Music visuals: background style, reactive background, intensity, per-style advanced controls.
- Plugin settings: TIME, weather, agenda, fullscreen plugin alert style.
- Notification settings: barrage, message notifications, detail toggles.
- Diagnostics: runtime evidence, export, performance warnings, test recommendations.
- About: version, project location, license/distribution notes.

Rules:

- Advanced visual tuning should be nested behind advanced disclosure.
- Sliders must have meaningful labels and default values.
- Settings that affect performance should indicate compact/expanded/fullscreen impact.
- Settings must avoid accidental wheel changes.
- New user-visible names use GluSpot.

## 15. Performance Requirements

Performance must be defined by product mode.

### 15.1 Compact Budget

Goals:

- Low CPU/GPU use.
- Stable lyric updates.
- No background effect that blocks paint cadence.
- Smoothed/cached audio reactive values are acceptable.

Requirements:

- Music-reactive background can degrade gracefully.
- Visual effects reduce complexity when system load is high.
- No disabled adapter keeps active loops.

### 15.2 Expanded Budget

Goals:

- Stronger motion and richer controls.
- Higher visual budget than compact.
- Still responsive to playback controls and lyric updates.

Requirements:

- Background rendering cannot starve lyrics.
- Diagnostics must show if reactive effects dominate paint time.

### 15.3 Fullscreen Budget

Goals:

- Immersive visuals.
- Higher rendering allowance.
- Clear exit path.

Requirements:

- Fullscreen mode may enable richer analysis and effects.
- Resource usage must be measured before release.
- Compact mode must return to low-cost behavior after collapse.

## 16. Architecture Requirements

The next implementation must deepen modules around clear interfaces. The interface is the test surface.

### 16.1 Required Interfaces

Media Session Interface:

- Provides normalized track, playback, position, duration, album art, capabilities, and source identity.
- Has real and test adapters.
- Does not expose rendering details.

Playback Command Interface:

- Sends play, pause, next, previous, seek, and provider-supported controls.
- Reports unsupported commands clearly.

Lyric Timeline Interface:

- Selects active line from media position and timed lyric lines.
- Provides line-level phase.
- Does not know background or renderer details.

Lyric Paint State Interface:

- Translates lyric timeline plus style-local options into paint-ready visual state.
- May apply visual offsets.
- Does not mutate synchronized lyric state.

Reactive Audio Frame Interface:

- Provides smoothed bands, energy, frequency emphasis, source status, cache age, and enabled/disabled state.
- Separates audio sampling cadence from render cadence.

Background Style Plan Interface:

- Converts reactive frame and style settings into style-native paint plans.
- Owns visual geometry and intensity decisions.
- Is pure enough to test without Qt.

Renderer Interface:

- Maps paint plans into actual drawing operations.
- Does not decide product logic, provider timing, or overlay state.

Overlay Surface Interface:

- Renders message/plugin/barrage/fullscreen alert surfaces.
- Cannot mutate album art, lyric timeline, media state, or background plan.

Placement Controller Interface:

- Owns Free, Finder, Tasker, and Stick placement.
- Does not own media state or visual style rules.

Settings Profile Interface:

- Persists user choices.
- Provides validated settings to runtime modules.
- Keeps migration aliases separate from new user-visible naming.

Diagnostics Evidence Interface:

- Exposes runtime facts: provider status, lyric lag, reactive input status, paint budget, overlay state, and remediation suggestions.
- Produces exportable evidence for QA.

### 16.2 Architecture Rules

- Provider adapters do not paint.
- Renderers do not poll providers.
- Background styles do not own lyric synchronization.
- Lyric styles do not own background rendering.
- Overlays do not affect album art unless explicitly specified by a global dimming mode.
- Compact, expanded, and fullscreen modes have explicit budgets.
- Product state, visual state, and paint state are separate.
- New modules should hide complexity behind small interfaces.
- New seams require at least production and test usefulness.

## 17. Migration Strategy

Migration should be staged.

Phase 0: PRD and design

- Complete this PRD.
- Produce Pencil designs.
- Review and approve user-facing flows.

Phase 1: Interface skeleton

- Define new interfaces for media session, lyric timeline, reactive audio frame, background style plan, renderer, overlay, placement, settings, diagnostics.
- Add test adapters where useful.

Phase 2: Compact Island vertical slice

- Implement a compact island slice with media, album art, line-level lyrics, one background style, and one overlay.
- Verify screenshot, runtime diagnostics, and behavior tests.

Phase 3: Music-reactive background system

- Implement style-native plans for sea-level, full-duotone, bilateral diffraction, pure black/white, and Yaoguang.
- Add visual acceptance checks and diagnostics.

Phase 4: Expanded Island

- Implement expanded layout and richer controls based on Pencil approval.
- Verify performance and lyric stability.

Phase 5: Fullscreen Music Mode

- Implement fullscreen entry and exit from the approved Pencil design.
- Keep playback state shared with the island.
- Add fullscreen-specific visual budget and diagnostics.
- Verify collapse returns compact/expanded modes to their lower-cost budgets.

Phase 6: Notifications and plugins

- Reconnect message notifications, TIME, plugin alerts, barrage, weather, and agenda through overlay interfaces.

Phase 7: Tasker/Finder/Stick

- Reconnect placement modes through the placement controller.
- Verify desktop behavior.

Phase 8: Intermediate Player decision

- Decide whether third-party web-login/intermediate playback is a separate future product path.
- Keep it out of the fullscreen UI implementation unless explicitly approved.

Phase 9: Release hardening

- Packaging, signing, installer/update, Steamworks decision, SteamPipe, clean desktop QA, copyright/data audit, and crash/support diagnostics.

## 18. User Stories

1. As a Windows music listener, I want a compact floating island, so that I can see music status while working.
2. As a music listener, I want album art to stay clear, so that the island feels visually anchored.
3. As a lyric-focused user, I want line-level lyrics to follow reliably, so that I can read along without stutter.
4. As a lyric-focused user, I want visual lyric motion to feel alive, so that line-level lyrics do not look static.
5. As a user, I want the app to avoid claiming word-level karaoke without word timestamps, so that expectations are clear.
6. As a user, I want music-reactive backgrounds to match each background style, so that the effect feels designed instead of pasted on.
7. As a user, I want sea-level backgrounds to move like water, so that the theme reads as oceanic rather than twitchy.
8. As a user, I want full-duotone backgrounds to keep their two-color structure, so that the style remains recognizable.
9. As a user, I want bilateral diffraction to react from the sides, so that the center remains clean for lyrics.
10. As a user, I want pure black and pure white backgrounds to have tasteful accent motion, so that reactive mode is visible without flashing.
11. As a user, I want music-reactive mode to be subtle in compact mode, so that it does not distract during work.
12. As a user, I want music-reactive mode to be stronger in expanded mode, so that it feels worth expanding.
13. As a user, I want Fullscreen Music Mode to be immersive, so that I can intentionally enjoy music visually.
14. As a user, I want Fullscreen Music Mode to be separate from the compact island, so that the compact island remains lightweight.
15. As a user, I want playback controls to be clear and familiar, so that I can control music quickly.
16. As a user, I want unsupported playback commands to fail gracefully, so that I understand provider limitations.
17. As a user, I want message notifications to appear without darkening album art, so that notifications do not corrupt the music surface.
18. As a user, I want plugin reminders to have clear styles, so that reminders are noticeable but not chaotic.
19. As a user, I want barrage and fullscreen alerts to be separate controls, so that I can tune interruption level.
20. As a user, I want Tasker mode to follow the taskbar, so that the island fits my desktop workflow.
21. As a user, I want Finder mode to remain available only when appropriate, so that placement choices are predictable.
22. As a user, I want Stick behavior for auto-hide taskbar edge cases, so that the island can stay reachable.
23. As a user, I want settings grouped by task, so that I can find media, visual, notification, and plugin controls.
24. As a user, I want advanced visual tuning hidden behind advanced controls, so that normal settings stay understandable.
25. As a user, I want diagnostics to explain lyric lag or visual stutter, so that bugs can be reported with evidence.
26. As a tester, I want runtime exports, so that visual and performance issues can be reproduced.
27. As a maintainer, I want provider logic separated from rendering, so that media bugs do not require visual changes.
28. As a maintainer, I want lyric timeline logic separated from lyric paint state, so that visual styles cannot desynchronize playback.
29. As a maintainer, I want background style plans testable without Qt, so that visual geometry can be changed safely.
30. As a maintainer, I want overlay surfaces isolated, so that notification fixes do not affect album art or lyrics.
31. As a maintainer, I want placement logic isolated, so that Tasker/Finder fixes do not affect provider or lyric code.
32. As a maintainer, I want settings validation centralized, so that invalid combinations are rejected predictably.
33. As a maintainer, I want compact, expanded, and fullscreen budgets to be explicit, so that performance tradeoffs are not guessed.
34. As a release owner, I want Windows package rules documented, so that test builds remain repeatable.
35. As a release owner, I want copyright and data policy checked before public release, so that lyrics and bundled assets are safe to distribute.
36. As a user upgrading from older builds, I want existing settings migrated, so that I do not lose configuration.
37. As a user, I want old names hidden from new UI, so that GluSpot feels coherent.
38. As a user, I want visual designs reviewed before implementation, so that the app does not keep drifting through code-only changes.
39. As a user, I want implementation to happen only after PRD and Pencil approval, so that token and development cost are not wasted.
40. As a tester, I want final acceptance to include real desktop playback, so that automated tests are not mistaken for visual proof.
41. As a user, I want to enter Fullscreen Music Mode from the island, so that I can move from glanceable mode into an immersive music surface.
42. As a user, I want to exit Fullscreen Music Mode clearly, so that I can return to low-cost desktop companion behavior.
43. As a user, I want fullscreen lyrics to be large and readable, so that the mode is useful beyond decoration.
44. As a user, I want fullscreen audio-reactive visuals to use the whole screen tastefully, so that they feel intentionally designed.
45. As a maintainer, I want fullscreen rendering to have its own budget, so that compact-mode performance does not regress.

## 19. Implementation Decisions

- The project will proceed through PRD, Pencil UI/UX review, implementation, and testing gates.
- The current repository remains the working context and asset base unless a future playback or technology decision justifies a new repository.
- GluSpot Next should be built as a clean implementation channel, not as more direct patching into existing large surfaces.
- The current runnable app remains a baseline during migration.
- Media data remains behind provider adapters.
- Fake media remains test-only.
- App-specific integrations remain optional and must release resources when disabled.
- Line-level lyrics remain the current source of truth.
- Word-level lyrics require a new structured model and are not implied by visual read effects.
- Lyrics and background rendering are separate product surfaces.
- Audio reactive input is sampled and smoothed separately from painting.
- Background style plans own geometry and intensity decisions for each style.
- Renderers map plans to drawing operations.
- Overlay surfaces are isolated from album art and media state.
- Placement modes are separate from media and visual state.
- Settings expose product-level controls and hide low-level tuning unless advanced mode is explicit.
- Diagnostics are a first-class part of runtime verification.
- Fullscreen Music Mode is a required GluSpot Next mode requiring explicit design approval before implementation.
- Fullscreen Music Mode shares playback state with the island and must not introduce an unapproved provider path.
- Fullscreen Music Mode has its own visual budget and must release high-cost rendering when closed.

## 20. Testing Decisions

Good tests should verify external behavior through stable interfaces, not internal painter details.

Required automated testing areas:

- Media provider contracts with real and fake adapters.
- Playback command capability behavior.
- Lyric timeline line selection and phase behavior.
- Lyric paint state style offsets and non-mutation of synchronized lyric index.
- Reactive audio frame smoothing, cache/staleness, enabled/disabled behavior.
- Background style plan outputs for sea-level, full-duotone, bilateral diffraction, pure surfaces, and Yaoguang.
- Renderer smoke tests for stable invocation and absence of obvious crashes.
- Overlay isolation: notifications do not mutate album art or media state.
- Placement policy: Free, Finder, Tasker, Stick rules.
- Settings validation and migration aliases.
- Diagnostics export and bottleneck classification.

Required visual/runtime verification:

- Compact island screenshot.
- Expanded island screenshot.
- Music-reactive background recording for each major style.
- Lyric follow recording during reactive background playback.
- Message popup over album art screenshot.
- Tasker desktop behavior screenshot/recording.
- Fullscreen Music Mode screenshot/recording.
- Fullscreen Music Mode entry/exit runtime verification.
- Runtime diagnostics export for any reported stutter.

Regression gates:

- Focused tests for changed modules.
- Full test suite before handoff.
- Diff whitespace/check validation.
- No claim of visual fix without screenshot/recording/runtime evidence.

## 21. Acceptance Criteria

This PRD phase is accepted when:

- The PRD identifies GluSpot's product position.
- The PRD defines compact, expanded, Fullscreen Music Mode, settings, overlay, and placement modes.
- The PRD defines companion playback as baseline and treats intermediate player mode as a future decision.
- The PRD defines lyric source-of-truth rules.
- The PRD defines style-native music-reactive background rules.
- The PRD defines overlay isolation to address album art darkening class bugs.
- The PRD defines performance budgets by mode.
- The PRD defines architecture interfaces and migration strategy.
- The PRD defines Pencil UI/UX review as a gate before implementation.
- The PRD defines testing and runtime verification gates.

The Pencil phase is accepted when:

- The user reviews and approves the core screens and flows.
- Compact, expanded, and Fullscreen Music Mode are represented as separate product surfaces.
- Music-reactive background behavior is represented visually enough to guide implementation.
- Settings and diagnostics information architecture are represented.

The implementation phase is accepted only when:

- Approved Pencil design has been translated into tasks.
- New architecture interfaces have tests.
- Compact vertical slice runs.
- Existing baseline behavior is not broken.
- Runtime evidence supports visual and performance claims.

## 22. Out Of Scope

Out of scope for this PRD phase:

- Direct code implementation.
- Continuing the unfinished music-reactive renderer patch.
- Committing or staging changes.
- Declaring the current visual bugs fixed.
- Releasing a public build.
- Replacing Windows media companion mode with a web-login player without a separate decision.
- Word-perfect karaoke without a word-timestamp model.
- Steam release work beyond documenting requirements.

## 23. Risks

- Current worktree has many uncommitted and untracked changes, so implementation must be staged carefully.
- Existing automated tests may be strong for behavior but weak for visual truth.
- Music-reactive effects can starve lyric paint if cadence and budgets are not enforced.
- App-specific lyric sync can regress if provider loops and position correction are not isolated.
- Overlay darkening bugs can recur if album art and overlay surfaces share visual state.
- Fullscreen Music Mode can become a second product if playback model and performance budgets are not defined.
- Rewriting too much at once can lose existing working Windows behavior.
- Continuing patch-based development can preserve the current coupling.

## 24. Open Decisions

These decisions must be made before implementation beyond the first compact vertical slice:

- Should intermediate-player mode be researched as a separate future product path?
- Which background style should be the first GluSpot Next vertical slice?
- Which lyric style should be the first GluSpot Next vertical slice?
- Which overlay should be used to prove album art isolation first?
- What compact-mode CPU/paint budget is acceptable on a normal Windows desktop?
- What expanded-mode budget is acceptable?
- What Fullscreen Music Mode CPU/GPU/paint budget is acceptable while active?
- Which diagnostics evidence is required for user-reported stutter?

## 25. Further Notes

The current implementation contains useful work and should not be dismissed wholesale. The project has product identity, Windows integration, settings, packaging, tests, media providers, lyric parsing, notification surfaces, and diagnostics work that can be reused or migrated.

The current failure pattern is not lack of effort. It is insufficient separation between product state, visual state, paint state, overlay state, and runtime evidence. The next phase must treat separation and verification as product requirements, not optional cleanup.

The next recommended artifact after this PRD is a Pencil design brief derived from this document. That brief should list the exact canvases/screens to design and the acceptance checklist for user review.
