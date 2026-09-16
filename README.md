# GluSpot

Windows floating music island built with Python and PySide6.

This README is the canonical product, implementation, release-readiness, and pending-work document for the current branch.

## Current Status

- Product name: `GluSpot`.
- Current test-build version: `6.402`.
- Development state: internal alpha / test build, not ready for paid Steam release.
- Runnable app: `local/main.py`, or `launch.bat`.
- Main UI surface: the V2 island only.
- Test baseline command: `python -m unittest discover tests -v`.
- Release blocker level: the 6.402 Windows test package can be built, but signing, installer/update flow, Steamworks integration, real-desktop QA, performance profiling, and copyright-safe lyrics/data policy are still required before public paid release.

## Run

Start the app from `local/main.py`, or double-click `launch.bat`.

If dependencies are missing, run `setup-env.bat` once, then launch again. Runtime logs are written under `logs/`, which is ignored and can be deleted safely between runs.

## Windows Test Build

GluSpot 6.402 uses a PyInstaller `onedir` package for local testing. This gives testers a normal executable while keeping Qt plugins, fonts, icons, data files, and local lyrics easy to inspect.

Build from the project root:

```powershell
.\build-exe.bat
```

Expected output:

```text
release\GluSpot-6.402\GluSpot.exe
```

The package is an unsigned internal test build. It is not an installer, does not include Steamworks ownership validation, and should not be treated as a public release artifact.

Known 6.402 test-build caveats:

- PyInstaller reports optional missing modules from Unix-only Python paths, NumPy/Pillow optional helpers, and setuptools internals; these are expected in a Windows GUI package unless a runtime feature proves otherwise.
- PyInstaller also reports missing `UIAutomationClient_VC140_X64.dll` / `UIAutomationClient_VC140_X86.dll` from the `uiautomation` dependency. The build still completes, but WeChat/QQ/DingTalk detail-detection paths should be checked on a real desktop.

## Guardrails

- Keep the runnable app at `local/main.py`.
- Keep fake and real media data behind `api/providers.py`.
- Do not reintroduce the old Pill UI.
- Do not restore the removed live-media source tree unless it is rebuilt as a V2 provider.
- Treat the current V2 island as the only product surface.
- Keep music sync adapters optional: disabled adapters should not hold polling loops, plugin handles, or lyric-sync memory.
- Keep generated runtime folders, logs, caches, and temporary reference projects out of source control.

## Naming And Assets

- `GluSpot` is the product name, app name, startup task/app ID, runtime lock namespace, and settings namespace.
- The current app version is `6.402`, defined in `local/branding.py`, `GluSpot.spec`, and `packaging/windows/version_info.txt`.
- `assets/brand/` stores the generated GluSpot PNG and ICO brand assets.
- User-selected global icons are copied into the GluSpot settings data directory under `icons/` and saved as PNG plus ICO.
- `assets/logos/` stores TIME reminder SVG assets. The old root-level `logos/` folder has been removed.
- `assets/music_controls/` stores SVG playback control assets.
- `assets/fonts/AlibabaPuHuiTi/` stores the embedded Alibaba PuHuiTi Bold/Heavy fonts used by the Wave Stream lyric style and settings UI.
- `assets/fonts/NotoSerifSC/` stores the embedded Noto Serif SC ExtraBold font used by the Gelv lyric style.
- `assets/fonts/JiangChengXieSong/` stores the embedded JiangCheng XieSong 700W/900W fonts used by the Ember Fade lyric style and settings UI.
- Old settings directories are retained only as migration aliases so existing user settings can be copied into the GluSpot settings directory on first launch.

## Startup

User-selectable startup is implemented in `local/startup.py` and exposed in the basic settings page.

Available modes:

- `none`: remove both supported startup hooks.
- `registry` / Windows startup item: use the current user's `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` entry.
- `task`: create a current-user scheduled task on logon. This is the recommended mode for GluSpot because it is an interactive tray/floating-window app.

Windows Service startup is intentionally not implemented. Services are not a good fit for this app because the island needs to interact with the signed-in user's desktop session.

## Current Product Direction

GluSpot is a Windows music island with plugin reminders, message notifications, and optional taskbar fusion.

The music surface should feel like a compact live object: album art, playback controls, lyrics, and album-color ambience remain readable in compact, expanded, Finder, Free, and Tasker placement modes.

The lyric target is Apple-like live lyric motion, not a visible progress bar. Line-level LRC timing selects the active line and drives soft phase, scale, opacity, glow, and background breathing. Word-perfect karaoke should wait for a structured word-timestamp model.

## Settings Surface

The settings window is organized into:

- Basic settings: theme mode, startup mode, placement, Tasker/MyDockFinder controls, empty-island behavior, font and animation options.
- Media control: player mode, app profiles, BetterNCM path, lyric style, album island, lyric offsets, and ambience controls.
- Plugin settings: TIME controls, weather settings, fullscreen plugin alert style, and agenda JSON/ICS controls.
- Notification settings: barrage and message notification controls.
- About: diagnostics and local project location.

Settings UI style lives mainly in `local/settings_window.py` and `local/mac_style.py`; small shared controls live in `local/no_wheel_widgets.py` and `local/ui_theme.py`.

## Placement Modes

- `Free`: normal floating island.
- `Finder`: used only when MyDockFinder is detected or explicitly available. Finder material may frame the island, but the island shell stays its own opaque V2 rendering path.
- `Tasker`: Windows taskbar fusion. The island follows the taskbar, including taskbar auto-hide. Fullscreen detection must not make Tasker disappear.
- `Stick`: internal auto-hide-taskbar placement. When Windows taskbar auto-hide is enabled, dragging the island to the bottom edge pins the free-style island just above the taskbar instead of entering Tasker.

MyDockFinder and Tasker are mutually exclusive:

- MyDockFinder running: allowed modes are `Free` and `Finder`.
- MyDockFinder not running: allowed modes are `Free` and `Tasker`; `Stick` can be reached only from the auto-hide-taskbar bottom-edge drag gesture.
- MyDockFinder detection has priority over taskbar auto-hide recognition, because Finder-style shells can hide the Windows taskbar themselves.

## Tasker Rules

- Tasker position is draggable across the taskbar range, including both bottom corners.
- Tasker should not snap to center by default.
- The left width handle is outside the island, appears on hover, and should hide after a short delay.
- Tasker compact layout can shrink down to album-cover mode when the taskbar is crowded.
- Tasker material and expanded outer frame should sample the taskbar style, not a generic wallpaper crop.
- Tasker mode keeps island visibility separate from fullscreen, plugin alerts, and barrage.
- With taskbar auto-hide disabled, dragging the island to the taskbar enters `Tasker`, and dragging Tasker out returns to `Free`.
- With taskbar auto-hide enabled, the center prompt enters `Tasker`, while the bottom edge enters `Stick`.

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
- Local and online lyric files live in `lyrics/`; this folder is the active lyric library for desktop testing.
- The generated lyric cache is bounded by age and file count when a separate generated cache root is configured by tests or future packaging.
- `TrackData` remains the shared media contract.
- `TimedLyricLine` remains line-level; future word-level lyrics need a new structured model.
- NetEase, BetterNCM, QQ, Qishui, and Kugou integration paths are optional app-profile attachments. Only configured app paths should be listened to.
- Generic Windows media remains the baseline provider route.

## Visual Styles

- Music island background styles should remain available in Free, Finder, and Tasker.
- Current ambience styles include `sea_level`, `duotone_full`, `yaoguang`, `pure_black`, `bilateral_diffraction`, and `pure_white`.
- `yaoguang` is a generated blurred layered-rectangle background: the user can use a custom color, extract the saturated album color, or use the album inverse color. The crisp black outer frame stays unblurred and even-width, while blur strength applies only to the inner color layers. Compact mode uses equal-height inner color bands; expanded mode keeps the nested layered-rectangle model. It is treated as a light lyric surface, so text and controls default to black.
- Pure white and pure black styles must adapt lyric and metadata text colors.
- `wave_stream`, `gelv_songscatter`, and `ember_fade` stay separate lyric-flow styles.
- `wave_stream` uses embedded Alibaba PuHuiTi Bold by default, with Heavy selectable from the detailed lyric settings.
- `gelv_songscatter` uses embedded Noto Serif SC ExtraBold, with embedded Alibaba PuHuiTi fallback.
- `ember_fade` uses embedded JiangCheng XieSong 700W by default, with 900W selectable for both text layers from the detailed lyric settings, optional extra italic slant, split-line or scrolling lyric modes, dual-line display that forces scrolling mode, an independent album-cover toggle, sparse spacing, blur-fade per-word lyric progression, neutral-background color fill, adjustable rear-layer gray/opacity, colored-background rear-layer gray support for sea-level, full-duotone, and solid-album ambience, a tunable blur panel between text layers, and a positive style-local lyric offset.
- Album island mode is the no-lyrics/instrumental fallback and supports compact and expanded size settings.

## Plugins And Notifications

- TIME is the default enabled plugin surface.
- Weather can be enabled from plugin settings and uses Open-Meteo without an API key by default; it still needs real-world QA before release.
- Agenda can be enabled from plugin settings and can read local JSON/ICS-like schedule data; it still needs real-world QA before release.
- Countdown and alarm due states must keep separate identities.
- Fullscreen plugin reminders can be shown as island-style card, toast card, or barrage burst.
- Barrage notifications may be clickable only through masked clickable regions; normal rolling barrage should remain non-intrusive.
- Message detection supports WeChat, QQ, and DingTalk settings, with detail notification toggles separated from basic notification toggles.

## Completeness Assessment

Version 6.402 is suitable for a controlled desktop test pass, not for public release.

| Area | Assessment | Notes |
| --- | --- | --- |
| Product identity and migration | Mostly complete | GluSpot branding, runtime namespaces, icons, startup IDs, and legacy settings import aliases are in place. |
| Core V2 island | Mostly complete for alpha | Free/Finder/Tasker placement, compact/expanded layouts, barrage separation, plugin alerts, lyrics, and album fallback are implemented with behavior tests. |
| Media and lyric integration | Partially complete | Generic Windows media is the baseline. NetEase, BetterNCM, QQ, Qishui, and Kugou paths are optional profile attachments that still need real-app QA. |
| Plugins and notifications | Partially complete | TIME is the active default. Weather and agenda code exist, but both still need real-world validation before normal-user enablement. |
| Settings and startup | Mostly complete for alpha | Settings pages and startup modes exist. Clean-account startup and first-launch migration still need manual verification. |
| Packaging | Test-build complete | `build-exe.bat` produces `release\GluSpot-6.402\GluSpot.exe`; signing, installer/update behavior, and distribution hardening are pending. |
| Release operations | Incomplete | Steamworks, SteamPipe, crash/support diagnostics, copyright/data audit, and multi-machine QA remain blockers. |

Overall project completeness: about 75% for an internal alpha/testable Windows desktop app, about 45% for a public paid Windows release, and about 30% for a Steam-ready release pipeline.

## Release Readiness

Current assessment:

- Stability: strong automated behavior coverage, but not enough manual desktop coverage for a public paid release.
- Completeness: suitable for internal alpha or controlled 6.402 test builds; not Steam-ready yet.
- Performance: animation and adapter controls exist, but cold-start time, memory use, CPU use, FPS stability, and long-running behavior still need measured reports.

Steam release requirements still pending:

- Replace the internal PyInstaller test package with a signed release package and installer/update workflow.
- Decide and implement Steamworks ownership validation / DRM wrapper behavior.
- Add SteamPipe build workflow with private beta branches before public updates.
- Verify all listed store features on clean Windows 10 and Windows 11 machines.
- Validate startup mode, tray behavior, fullscreen behavior, MyDockFinder, Tasker, media controls, message detection, and plugin alerts on real desktops.
- Audit lyrics and bundled media/data for copyright and redistribution safety.
- Add crash reporting or at least structured runtime diagnostics suitable for support.

## Current Branch Fixes

- Moved generated brand icons into `assets/brand/`.
- Moved TIME reminder SVGs into `assets/logos/`.
- Added GluSpot 6.402 version constants, Windows executable version metadata, and PyInstaller test-build packaging.
- Added user-selectable startup modes: none, registry, and scheduled task.
- Split barrage overlay from plugin alert overlay so preview, fullscreen alerts, and Tasker island visibility no longer block each other.
- Fixed Tasker fullscreen barrage generation when Tasker keeps the island visible.
- Kept barrage preview usable from settings without forcing island suppression.
- Added bounded lyric cache pruning support and indexed local lyric filename matching for the `lyrics/` library.
- Reduced album-island refresh stalls by reusing unchanged queue art and current cover decoding, and by filtering duplicate current-track queue entries.
- Restored NetEase/QQ lyric follow to the lightweight reader baseline from the preferred reference copy: timed lyrics enable normal player-position polling, while click hit-tests, forced screenshot anchors, and post-click retry bursts stay out of the default refresh path.
- Added regression tests for separated overlay rules, MyDockFinder listener behavior, no-wheel controls, message detection, provider state, startup modes, and settings structure.

## Pending Work

- Manually verify first-launch settings migration from older local builds.
- Manually verify startup modes on a clean Windows user account.
- Manually verify barrage preview in the settings panel on a real desktop.
- Manually verify plugin fullscreen alert styles in fullscreen, maximized, windowed, and Tasker modes.
- Manually verify weather and agenda plugin flows with real location data, JSON schedules, and ICS schedules.
- Continue diagnosing MyDockFinder listener reliability on real installed paths.
- Finish Tasker outer-frame material sampling and animation smoothness against real Windows 10 and Windows 11 taskbars.
- Verify NetEase, BetterNCM, QQ, Qishui, and Kugou lyric/progress sync behavior with configured app paths only.
- Continue memory profiling for optional lyric sync adapters: disabled adapters must release timers, handles, caches, and plugin hooks.
- Add screenshot or visual regression coverage once the PySide screenshot harness is stable.
- Prepare signing, installer/update workflow, Steamworks, SteamPipe, and release QA before paid distribution.

## Key Files

- `local/main.py`: app entry point.
- `local/branding.py`: GluSpot name, version, icons, and settings namespace helpers.
- `local/app.py`: tray app, settings wiring, provider wiring, MyDockFinder polling, startup setting application.
- `local/startup.py`: Windows startup mode manager.
- `local/island_stack_visual.py`: island layout, Tasker/Finder/Free visual behavior, lyrics, plugin alerts, barrage.
- `local/settings_window.py`: settings UI and most settings-page styling.
- `local/mac_style.py`: macOS-like preferences styling, sidebar icons, traffic lights, and shared UI polish.
- `local/no_wheel_widgets.py`: controls that ignore accidental wheel changes.
- `local/ui_theme.py`: light/dark/scheduled theme helpers.
- `local/settings_store.py`: persisted settings and defaults.
- `api/models.py`: shared data contracts.
- `api/providers.py`: Windows media provider and provider boundary.
- `api/plugin_provider.py`: TIME, weather, and agenda plugin data.
- `api/message_detector.py`: WeChat, QQ, DingTalk notification detection.
- `api/lyrics.py`: local and online lyric resolution.
- `api/lyric_timeline.py`: active lyric line and line phase calculation.
- `assets/brand/`: GluSpot PNG and ICO brand assets.
- `assets/logos/`: TIME reminder SVG assets.
- `assets/music_controls/`: playback control SVG assets.
- `GluSpot.spec`: PyInstaller `onedir` test-build definition.
- `build-exe.bat`: repeatable Windows test-build command for version 6.402.
- `packaging/windows/version_info.txt`: Windows executable version metadata.
- `tests/`: behavior tests.

## Verification

Use focused tests while changing behavior, then run the full suite before handoff:

```powershell
python -m unittest tests.test_startup -v
python -m unittest tests.test_island_stack_visual_messages -v
python -m unittest discover tests -v
git diff --check
```
