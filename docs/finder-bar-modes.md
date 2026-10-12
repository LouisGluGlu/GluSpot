# Finder Bar and placement modes

This October 12 follow-up implements the requested MyFinder top-bar presentation within the existing V2 island. Product version remains **6.412**. The existing packaged EXE is unchanged; restart this branch's `launch.bat` to load the source changes.

The subsequent [material and compact DJ follow-up](finder-material-followup.md) uses trusted HWND height directly, excludes GluSpot's painted pixels, refreshes visible colors at a bounded 250 ms cadence and removes the striped expanded texture. It supersedes the screenshot edge/outline inference described below for the live Finder Bar path. Completed Finder narration stays compact.

## Public modes

| Public name | Internal value | Placement |
| --- | --- | --- |
| Tasker Bar | `tasker` | Occupies the Windows taskbar and follows its actual visibility. |
| Top Island | `top` | Pins the island to the screen's top edge. |
| Free | `free` | Floats away from the top and bottom placement edges. |
| Finder Bar | `finder` | Shows artwork and timed lyrics inside a genuinely enabled MyFinder top bar. |

The internal `stick` state preserves the established auto-hide-taskbar bottom-edge gesture. Settings and diagnostics present it as Free; it is not an additional public mode.

Media settings provide the current placement selector. With MyDockFinder closed, the choices are Top Island, Free and Tasker Bar. With Dock running and a real MyFinder bar enabled, the choices are Finder Bar and Free. With Dock running but MyFinder disabled or not found, only Free is available. Dock presence blocks Windows taskbar attachment and auto-hide inference even when the island is Free.

A newly detected enabled MyFinder bar automatically enters Finder Bar. Ordinary subsequent polling does not override a deliberate Free selection. Disabling or losing the real bar returns an attached Finder Bar to Free. Current placement is a runtime choice: restarting uses the fixed startup placement and real detection rather than restoring a saved mode.

## Finder appearance and interaction

The existing Media/MyDockFinder card exposes three Finder-only appearances:

| Style | Saved value | Presentation |
| --- | --- | --- |
| Transparent, default | `transparent` | Artwork and timed lyrics without an added background. |
| Rounded color capsule | `capsule` | Artwork and lyrics within a bounded rounded album-color capsule. |
| Horizontal fading color strip | `fade` | Album color behind the content, fading to transparency toward both sides. |

These use the current shared artwork, lyric timing/reading and contrast-aware color paths. They do not add independent fonts or reopen the removed global style controls. Transparent lyrics take their contrasting ink from the sampled real Finder material; the colored styles account for their displayed background.

Drag anywhere in the Finder presentation horizontally to reposition it within the real bar. Its bounds stay within that bar, and its horizontal ratio is retained. A deliberate downward pull detaches to Free; horizontal movement alone does not detach. The standard expanded island remains available below the compact Finder presentation.

The only new saved presentation keys are:

- `v2_finder_bar_style`: `transparent`, `capsule` or `fade`; malformed values return to `transparent`.
- `v2_finder_bar_center_ratio`: finite horizontal ratio clamped to `[0, 1]`, default `0.5`.

The previous global location/position, typography and geometry keys remain fixed to `DEFAULT_SETTINGS`. Existing user-selected MyDockFinder listening processes remain intact. New configurations listen for both `dock.exe` and `dock_64.exe` by default.

## Detection and visibility

Dock process presence and MyFinder enablement are distinct evidence. The settings card and diagnostics display them separately; a Dock process alone never creates a top bar. No manual “MyFinder enabled” switch is needed.

The existing 2.5-second listener discovers the selected runtime and reads its configuration. A Finder candidate must be a real `MyFinderApp` window belonging to the trusted matched runtime name/path, with valid bounds near its owning monitor's top edge. An installed file, unrelated same-class window or config flag alone cannot create a candidate. The runtime's explicit MyFinder setting supplements real window evidence. Cached HWND/process identity and previously observed resting bounds allow an enabled auto-hidden bar to stay associated with its owner without treating an unrelated hidden window as enabled.

While Finder Bar is active, native window state refresh is bounded to once per 100 ms. This cached path reads the known HWND rather than repeatedly enumerating processes or reading configuration. Physical native rectangles map into Qt logical coordinates on the matching monitor, preserving monitor origins and scaling dimensions separately. Every screen candidate must match the native monitor bounds, including when its name matches; differing friendly names are supported when that geometry identifies exactly one screen. Ambiguous matches are rejected. This prevents primary-screen fallback, negative-origin displacement and cross-monitor attachment. Live material sampling uses that screen's local logical coordinates and normalizes returned high-DPI pixels before bar-edge/color detection.

This follows [Qt's grabWindow contract](https://doc.qt.io/qt-6/qscreen.html#grabWindow), the [Qt 6.12 logical-to-native scaling](https://github.com/qt/qtbase/blob/v6.12.0/src/gui/kernel/qscreen.cpp#L654), and [Windows screen-origin handling](https://github.com/qt/qtbase/blob/v6.12.0/src/plugins/platforms/windows/qwindowsscreen.cpp#L571). The [Windows implementation](https://github.com/qt/qtbase/blob/v6.12.0/src/plugins/platforms/windows/qwindowsscreen.cpp#L194) can expose a friendly screen name, so native `MONITORINFOEX.szDevice` equality alone is insufficient.

Finder Bar and Tasker Bar are not hidden merely because an external application is fullscreen, maximized or playing video. Each follows its own real bar's visibility instead. A genuinely auto-hidden Finder/taskbar removes the corresponding drawing and input regions; it does not pause playback, reset the source clock or change the selected mode. When the owner bar becomes visible again, presentation resumes from current playback state.

Tasker previously kept its window visible and moved it by only one taskbar height. That left part of a larger expanded island on-screen. A successful hidden/visible follow transition now immediately feeds the existing scene-suppression path: the expanded face collapses, its full drawing fades out over 180 ms, and mouse input passes through. The provider refresh remains active; revealing the taskbar restores presentation without a media command.

## Verification and limits

- The final focused runs passed 1,179 tests at each DPR: 11.979 seconds at DPR 1 and 11.067 seconds at DPR 2. Coverage includes the final unique-screen mapping, logical high-DPI material and expanded Tasker auto-hide corrections.
- Native Finder previews covered 144 frames and 293 checks across the three appearances, bar sizes, artwork/background conditions and DPR 1/2. The rendered frames were visually inspected.
- Media settings passed 94 tests at each DPR, plus eight native state/size previews and 32 layout checks. The states distinguish Dock closed, Dock without Finder, visible Finder and enabled auto-hidden Finder.
- A normally constructed native QWidget with a synthetic V2 provider exercised the production Tasker follow, scene, paint and input paths. A 200-logical-pixel expanded island over a 40-pixel taskbar became fully transparent after 180 ms, retained provider refresh/clock/play state, and revealed without transport commands. DPR 1/2 each passed 40 checks with six frames; expanded, hidden and revealed captures were inspected. These checks use synthetic bar observations and no actual audio decoder or physical auto-hide change. Evidence remains in ignored `logs/preview_tasker_autohide.py` and `.runtime/tasker-autohide-preview/`.
- Read-only local inspection found the actual enabled top bar at **1,920 × 24 physical pixels**. This is evidence for detection on the current machine, not proof of every layout or external fullscreen interaction.
- Synthetic regression coverage checks negative monitor origins, mixed DPI, trusted window identity, enabled/visible/auto-hidden transitions, downward-only detachment, mode restrictions, fullscreen suppression policy and rendering/input continuity without audio commands.
- All 4,383 final Python tests passed in 250.812 seconds, with no skipped tests, errors or Qt callback tracebacks. This integrated run includes the preceding DJ short-caption correction. The verbose evidence is retained in ignored `logs/finder-bar-final-full-python.log`; final focused logs are `logs/finder-bar-final-all-focused.log` and `logs/finder-bar-final-all-focused-dpr2.log`. Compilation and `git diff --check` also passed.

Physical multi-monitor attachment, real external fullscreen/video overlap and long-session automatic-hide behavior still require interactive hardware QA. Synthetic tests and native renders do not establish those physical results. This change does not alter MyDockFinder's own configuration, add a new media provider or restart removed plugin/notification services.

Implementation is in `local/mydockfinder.py`, `local/taskbar_positioning.py`, `local/island_stack_visual.py`, `local/app.py`, `local/settings_store.py`, `local/settings_window.py` and `local/diagnostics_presentation.py`. Focused coverage includes `tests/test_finder_bar_detection.py`, `tests/test_finder_bar_surface.py`, `tests/test_mydockfinder_listener.py`, `tests/test_mydockfinder_material.py`, `tests/test_taskbar_positioning.py`, `tests/test_mac_preferences_shell.py`, `tests/test_simplified_settings_defaults.py` and `tests/test_diagnostics_presentation.py`. Generated runtime captures, logs and preview scripts remain outside source control.
