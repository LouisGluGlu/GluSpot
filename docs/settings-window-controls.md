# Settings window controls

The October 13 request supplies a compact/hover capsule concept and three SVGs for settings close, minimize and maximize. The user confirmed that the occupied-area recoloring rule applies only to these three controls.

## Behavior and implementation

The existing `mac_traffic_lights` factory and window callbacks are retained. The default capsule is 86 × 32 logical pixels with plain 20-pixel red/yellow/green circles. Hover over any circle or the gaps enlarges the capsule to 116 × 38 and the circles to 24 pixels, revealing all three supplied SVGs together. Leaving reverses the 220 ms OutCubic transition from its displayed pose; blocked animation reconfiguration prevents Qt's intermediate endpoint values from creating jumps. Hiding resets the pose and stops animation.

The reserved widget footprint stays 116 × 40 throughout. Settings search and navigation do not move, and the three buttons bypass the elastic control overlay. The hit mask leaves a one-pixel fringe for antialiased painted edges. Existing NoFocus behavior is retained, so first opening focuses search and stays compact; clicking a window button cannot latch expansion through keyboard focus. Explicit focus events remain handled, without adding Tab navigation.

Close still invokes the settings window's save/close path, yellow minimizes, and green chooses maximize or restore from the actual window state. Tooltip and accessible names remain present. Light/dark capsule rims follow the existing application theme source.

The byte-identical user SVGs live in `assets/window_controls/`. A cached 256-pixel alpha mask measures actual occupied area, preserving transparency, holes and aspect ratio. Icon color retains the button background's hue with brightness fraction `0.38 + 0.25 × occupied_area`: a larger shape is darkened less. Pressed backgrounds use the same rule. Renderer, mask and coverage caches are bounded; the existing whole-assets packaging rule includes the files.

| Icon | Occupied area | Button background | Rendered ink |
| --- | ---: | --- | --- |
| Close | 44.72% | `#ff5f57` | `#7e2f2b` |
| Minimize | 13.31% | `#ffbd2e` | `#694e13` |
| Maximize | 19.69% | `#28c840` | `#11561b` |

## Verification and limits

- All **4,508** final Python tests passed in **249.396 seconds**, without skips, errors or Qt callback tracebacks. Command: `.venv/Scripts/python.exe -X faulthandler -m unittest discover tests -v`; ignored evidence: `logs/settings-window-controls-final-full.log`.
- **134** relevant tests passed at DPR 1 (**14.262 seconds**) and DPR 2 (**14.712 seconds**). They cover settings, shared control hover, existing material helpers and the 16 new window-control regressions. The final full command also exercises the last fixture waiting adjustment at DPR 1. Logs are `logs/settings-window-controls-final-focused.log` and `logs/settings-window-controls-final-focused-dpr2.log`.
- Regressions exercise unchanged callback routing, real pointer movement across children and gaps, continuous reversal, 21 intermediate geometry/mask poses, stationary search/navigation, compact first opening, click/focus/hide behavior, source SVG hashes, independently summed alpha coverage including holes, and area-ordered hue-preserving color. Windows first-show animation can initially leave another HWND under the pointer; the native test waits at most one second for actual entry/exit instead of synthesizing the group state or skipping assertions.
- Normally constructed native settings windows with synthetic provider data produced **28 inspected frames and 72 checks** across DPR 1/2: light/dark compact/intermediate/hover, full settings layout and each pressed button. The ignored script is `logs/preview_settings_window_controls.py`, with results under `.runtime/settings-window-controls/dpr1` and `dpr2`.
- Python compilation, tracked/new-file whitespace checks and byte comparison with all three supplied SVGs passed. Existing whole-assets packaging includes the directory; no packaging/version edit is required.

Native checks use the current Windows/Qt environment and forced DPR 1/2; arbitrary fractional scaling and physical multi-monitor behavior were not separately tested. Captures and logs remain ignored; reference SVG provenance is in the asset README. This source UI change retains version **6.412** and does not rebuild the existing EXE. Restart this checkout's `launch.bat` to load it.
