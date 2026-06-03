# gpt-island

Windows floating music island built with Python and PySide6.

This README is the canonical product, implementation, and pending-work document for the current branch. It replaces the old scattered PRD, ISSUES, taskbar, lyric-flow, plugin, handoff, and superpower plan Markdown files.

## Run

Start the app from `local/main.py`, or double-click `launch.bat`.

If dependencies are missing, run `setup-env.bat` once, then launch again. Startup errors are written to `logs/gpt-island.log`.

## Guardrails

- Keep the runnable app at `local/main.py`.
- Keep fake and real media data behind `api/providers.py`.
- Do not reintroduce the old Pill UI.
- Do not restore the removed live-media source tree unless it is rebuilt as a V2 provider.
- Treat the current V2 island as the only product surface.
- Keep music sync adapters optional: disabled adapters should not hold polling loops, plugin handles, or lyric-sync memory.

## Current Product Direction

The app is a Windows music island with plugin reminders and optional taskbar fusion.

The music surface should feel like a compact live object: album art, playback controls, lyrics, and album-color ambience remain readable in compact, expanded, Finder, Free, and Tasker placement modes.

The lyric target is Apple-like live lyric motion, not a visible progress bar. Line-level LRC timing selects the active line and drives soft phase, scale, opacity, glow, and background breathing. Word-perfect karaoke must wait for a structured word-timestamp model.

## Placement Modes

- `Free`: normal floating island.
- `Finder`: used only when MyDockFinder is detected or explicitly available. Finder material may frame the island, but the island shell stays its own opaque V2 rendering path.
- `Tasker`: Windows taskbar fusion. The island follows the taskbar, including taskbar auto-hide. Fullscreen detection must not make Tasker disappear.

MyDockFinder and Tasker are mutually exclusive:

- MyDockFinder running: allowed modes are `Free` and `Finder`.
- MyDockFinder not running: allowed modes are `Free` and `Tasker`.

## Tasker Rules

- Tasker position is draggable across the taskbar range, including both bottom corners.
- Tasker should not snap to center by default.
- The left width handle is outside the island, appears on hover, and should hide after a short delay.
- Tasker compact layout can shrink down to album-cover mode when the taskbar is crowded.
- Tasker material and expanded outer frame should sample the taskbar style, not a generic wallpaper crop.
- Tasker mode keeps island visibility separate from fullscreen, plugin alerts, and barrage.

## Barrage And Fullscreen Alerts

These are separate rules and must not be treated as a two-choice switch:

- Island visibility rule: decides whether the normal island window is shown, hidden, or taskbar-following.
- Barrage rule: decides whether barrage preview or barrage messages render across the screen.
- Plugin alert rule: decides whether plugin due notifications render as island, toast, or barrage while fullscreen or maximized content is detected.

Current implementation:

- `FullscreenPluginAlertOverlay` renders plugin alert card styles.
- `BarrageOverlay` renders barrage preview and fullscreen barrage messages independently.
- Tasker fullscreen detection uses `last_scene_probe_reason` even when `scene_suppressed` is forced false for a visible taskbar island.
- Settings barrage preview syncs only the barrage overlay, not the plugin alert overlay.

## Music And Lyrics

- Real media state comes from `WindowsMediaProvider`.
- No active media session should show an empty or diagnostic state, not fake sample tracks.
- Local lyrics live in `lyrics/`.
- `TrackData` remains the shared media contract.
- `TimedLyricLine` remains line-level; future word-level lyrics need a new structured model.
- NetEase or other music-app sync adapters are optional attachments. Only configured app paths should be listened to.
- BetterNCM remains the NetEase plugin route; generic Windows media remains the baseline provider route.

## Visual Styles

- Music island background styles should remain available in Free, Finder, and Tasker.
- Pure white and pure black styles must adapt lyric and metadata text colors.
- `wave_stream` and `gelv_songscatter` stay separate lyric-flow styles.
- `gelv_songscatter` uses Source Han Serif SC when available, with PingFang SC fallback.
- Album island mode is the no-lyrics/instrumental fallback and supports compact and expanded size settings.

## Plugins

- TIME is the currently active plugin surface.
- Weather and calendar/agenda are reserved as not-yet-implemented and should not be enabled until real provider code is restored safely.
- Countdown and alarm due states must keep separate identities.
- Fullscreen plugin reminders can be shown as:
  - island-style card,
  - toast card,
  - barrage burst.
- Barrage notifications may be clickable only through masked clickable regions; normal rolling barrage should remain non-intrusive.

## Current Branch Fixes

- Split barrage overlay from plugin alert overlay so preview, fullscreen alerts, and Tasker island visibility no longer block each other.
- Fixed Tasker fullscreen barrage generation when Tasker keeps the island visible.
- Kept barrage preview usable from settings without forcing island suppression.
- Added regression tests for the separated overlay rules.

## Pending Work

- Manually verify barrage preview in the settings panel on a real desktop.
- Manually verify plugin fullscreen alert styles in fullscreen, maximized, windowed, and Tasker modes.
- Improve fullscreen barrage acknowledgement UX: decide whether click-to-confirm is enough or whether a dismiss affordance is needed.
- Continue diagnosing MyDockFinder listener reliability on real installed paths.
- Finish Tasker outer-frame material sampling and animation smoothness against real Windows 10 and Windows 11 taskbars.
- Verify NetEase/BetterNCM lyric sync behavior with configured app paths only.
- Continue memory profiling for optional lyric sync adapters: disabled adapters must release timers, handles, caches, and plugin hooks.
- Add screenshot or visual regression coverage once the PySide screenshot harness is stable.

## Key Files

- `local/main.py`: app entry point.
- `local/app.py`: tray app, settings wiring, provider wiring.
- `local/island_stack_visual.py`: island layout, Tasker/Finder/Free visual behavior, lyrics, plugin alerts, barrage.
- `local/settings_window.py`: settings UI.
- `local/settings_store.py`: persisted settings.
- `api/models.py`: shared data contracts.
- `api/providers.py`: Windows media provider and provider boundary.
- `api/lyrics.py`: local and online lyric resolution.
- `api/lyric_timeline.py`: active lyric line and line phase calculation.
- `tests/`: behavior tests.

## Verification

Use focused tests while changing behavior, then run the full suite before handoff:

```powershell
python -m unittest tests.test_island_stack_visual_messages -v
python -m unittest discover tests -v
git diff --check
```
