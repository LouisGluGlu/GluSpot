# UI visual alignment and composed-control repair

October 13, 2026 follow-up for GluSpot **6.412**, on
`codex/ui-visual-alignment`. The source baseline is
`0cde741f522530284d344e12ef85a19ce248de3d`, the latest referenced
`codex/owned-playback-sources` commit. It directly descends from the previous
main/worktree baseline `8d937f2` by 21 commits. The current worktree was clean
before creating this branch at that latest source; the referenced worktree was
not edited.

The user requested a visual alignment pass across the UI and reported the
device-panel screenshot's duplicate-looking volume track plus a conflict
between hover and click. This pass audits existing surfaces and repairs
measurable layout and interaction inconsistencies. Version and packaging inputs
remain **6.412**; the existing EXE has not been rebuilt.

## Scope and source of truth

`README.md`, the current native widgets, their existing call sites and tests,
and the user's screenshot define the scope. The October 12 device-control
contract remains authoritative: a bounded 360-logical-pixel chooser, selected
72-pixel/inactive 56-pixel rows, separate confirmed/target gain, and distinct
solo, mute/restore and join actions. See
[device-control redesign](device-control-redesign.md) and
[preceding output interaction](fm-preload-and-output-interaction.md).

The visual matrix covers first-run mode choices; source selection; synthetic
login QR placement and verified-account cards; expanded/compact music library,
search, empty and error states; all four settings pages in light/dark themes;
window/fullscreen music at normal and minimum sizes; visible/idle controls,
hovered controls, DJ captions and voice choices; and Free, Finder Bar and Tasker
Bar compact/expanded island rendering. Output-panel evidence additionally
exercises the screenshot's selected/inactive rows and pending volume-target
feedback using a synthetic provider.

The matrix uses synthetic provider/account/lyric data and clearly labelled
placeholder images. It does not log in, access network services, change saved
settings, send music, or alter physical output devices. Island captures reuse
the production surface painters with synthetic placement/material inputs;
they do not establish real MyDockFinder/taskbar or multi-monitor behavior.

## Actual alignment changes

| Surface | Observed inconsistency | Scoped correction |
| --- | --- | --- |
| Source/login header | Navigation sat four logical pixels below the library controls, and the title had a different vertical center | Back/close now use the library's `y=16`, 44-pixel target; title uses `y=20`, height 36, center 38. A source header without Back starts at the common 24-pixel outer gutter. |
| Output gain | The slider widget's invisible thumb margins were aligned with text rather than its painted track | The actual track begins at the 52-pixel name/status column and ends at the value's right edge (`row width - 14`). The icon's solo target ends above the volume lane. |
| Output device name | A fixed minimum percentage slot consumed unnecessary width even for `58%` | The value slot measures its real text with a smaller floor, retaining enough space for pending/error labels and increasing readable device-name width. |
| Settings media status | A lamp and two grid gaps added extra width before values, unlike neighboring form controls | The lamp and gaps now occupy the existing label slot. Status values and normal controls share the same left edge; long status labels wrap. |
| Settings path pickers | Browse buttons and neighboring inputs had different vertical extents | Browse buttons expand vertically within their existing row so their top/bottom match the input. |
| Plugin timer editor | Start button and minutes input had different vertical extents | The existing Start button expands vertically within the input row. |
| Settings control faces | Broad `QWidget` selectors made child buttons and input controls transparent inside cards/details, including white Primary text on pale backgrounds | Exact `.QWidget` selectors retain transparent structural containers while restoring the established blue Primary face and native input faces in both themes, including nested details. |

The pass keeps source tiles, QR/account content, library navigation, lyric
typography, player/provider boundaries and route semantics intact. Surface font
differences are retained where they already serve distinct roles; they are not
replaced by an invented global typography system. Settings traffic-light
controls retain their supplied SVGs, occupied-area coloring, stationary
reserved footprint and existing callbacks.

## Duplicate volume-track cause and paint ownership

The row's elastic hover image is captured with `DrawChildren`. Its volume
slider and action children are consequently already present in that composed
image. The old event filter suppressed the registered row's native paint while
it moved, but allowed descendant widgets to paint again at their unchanged
native coordinates. The moving row snapshot and the independently painted
slider could therefore show two tracks/thumbs. This is a UI compositing defect;
the screenshot does not establish a duplicate audio stream or a device-volume
readback error.

The existing `WidgetHoverGroup` now finds the registered ancestor that owns a
descendant's pixels. While that composed control is moving, descendant paint
events invalidate its bounded snapshot and request the overlay repaint instead
of leaving an additional native child image underneath. Explicit native
rendering still includes the children, and settled controls return to ordinary
native painting. Child resize, move, show, hide and style/font/palette changes
retire stale cached pixels. No additional renderer or provider interface is
introduced.

Regression evidence checks pixels unique to the old native child rectangle,
the transformed visible child, snapshot replacement after a child move/hide,
and native `render()` compatibility. That makes paint ownership testable
without guessing from screenshots alone.

## Hover, click and tooltip agreement

The existing inverse transform is now shared by pointer dispatch, descendant
hover feedback and tooltip lookup. It keeps the retained native target and
maps transformed pixels back to the same actual child. The mapped descendant
chain receives matching Enter/Leave events and `underMouse` state, including
nested children. Tooltip events resolve the composed child and its local
coordinate, with the established parent-tooltip fallback.

Native row boundaries can move while the pointer stays inside the same visible
capsule. A Leave event now checks the mapped row before canceling the selected
row's half-second detail dwell. An actual departure still clears the dwell.
Inactive rows retain their name typography while their independent join
button receives correct hover feedback.

A deliberate press owns the action until release. Shared native controls and
song-page output/DJ buttons now suppress their pending delayed hover-menu open
during that press. This prevents the same release from immediately toggling
shut a menu that opened halfway through the click. Hover-only opening remains
available for a fresh dwell. Existing held-child capture, transformed dragging,
wheel scrolling, hide cancellation and stale-route owner fencing remain in
place.

## Source availability follow-up

The user's later source-page screenshot shows all four platforms disabled and
the generic service-unavailable message. This new checkout had its Python
environment, but lacked the ignored optional `build/online-music` runtime.
`NodeOnlineMusicBackend.available()` checks the local Node executable, bridge
and pinned package manifests; `list_sources()` does not request the network.
The missing runtime therefore explains those disabled source tiles.

The existing `native/online_music/prepare-runtime.ps1` prepared an independent
runtime in this checkout using the committed lockfile, `npm ci --ignore-scripts`
and Node **v24.19.0** with its matching retained license. It installed 397
packages and retained 397 provenance records and 378 notice files. The source
lockfile and copied Node binary match the referenced checkout's hashes; that
checkout and its dependencies were not changed. Runtime files remain ignored.

A real offline preload also exposed a separate NetEase cold-cache defect.
The pinned `util/request.js` reads `anonymous_token` at import time. Normal
requests initialized that owned empty file, whereas `prepare_login` imported
the module first. Both paths now reuse the existing initialization through a
small helper: exclusive `wx` creation, no truncation, and only `EEXIST` ignored
when the file already exists or another child wins the creation race. Other
filesystem failures remain failures. No credentials, token contents, QR or
account interfaces are added to preparation.

Two real Node regression fixtures mirror the pinned module's top-level file
read. They prove empty-cache initialization and preservation of existing
synthetic bytes and modification time. Their callable network/QR functions
throw if preparation invokes them. These tests passed with the 33-test bridge
suite. The earlier real-runtime 18-second timeout was not causally isolated;
it is not attributed solely to the cold-cache defect.

The final actual-runtime check uses a private workspace `LOCALAPPDATA` and an
empty fixture credential store. NetEase, Kugou and QQ preloaded successfully
in **922 / 265 / 172 ms**, respectively, in one observation. The source window
then showed those three enabled platforms, retained unsupported Qishui as
disabled, displayed `选择音乐来源`, and emitted the three corresponding click
signals. No login controller is attached, so this check requests no QR,
account, catalog, stream or playback operations. These measurements establish
local adapter loading, not external service or authenticated playback health.
The offline claim is supported by the invoked operation paths and inspected
pinned imports; this check does not intercept or count socket activity.
Evidence is in `logs/ui-alignment-online-runtime-prepare.log`,
`logs/ui-alignment-online-runtime-verify.log` and
`.runtime/ui-visual-alignment/online-runtime/evidence.json`, with
`sources-ready.png` retaining the restored native source page.

An already open source page can retain its earlier unavailable result. Close
the application normally and reopen this checkout's `launch.bat` to use the
prepared runtime and updated bridge. Version and existing EXE remain unchanged.

## Verification ledger

Verification is performed serially with native Windows Qt so separate Qt
processes do not steal the pointer/focus from each other's interaction tests.
The frozen final source must pass the full command before this report claims
readiness. Ignored logs, fixture scripts, PNGs and cache/runtime outputs remain
outside source control.

| Check | Recorded result | Evidence |
| --- | --- | --- |
| Final focused suites, DPR 1 | **299 tests, 61.544 seconds, OK** | `logs/ui-alignment-final-focused-dpr1.log` |
| Final focused suites, DPR 2 | **299 tests, 59.005 seconds, OK** | `logs/ui-alignment-final-focused-dpr2.log` |
| UI-pass complete discovery, before source-runtime follow-up, DPR 1 | **4,525 tests, 333.327 seconds, OK; exit 0; no skips, errors or Qt callback tracebacks** | `logs/ui-alignment-final-full.log`; includes the UI-pass high-DPI/test-input refinements at DPR 1 |
| Source/service/entry/startup focus after runtime repair | **122 tests, 8.889 seconds, OK** | `logs/ui-alignment-runtime-final-focused.log` |
| Device-panel focus after fixture-input correction, DPR 1 | **103 tests, 20.565 seconds, OK** | `logs/ui-alignment-runtime-panel-focused-dpr1.log` |
| Device-panel focus after fixture-input correction, DPR 2 | **103 tests, 21.410 seconds, OK** | `logs/ui-alignment-runtime-panel-focused-dpr2.log` |
| Final combined UI/runtime complete discovery, DPR 1 | **4,527 tests, 269.540 seconds, OK; exit 0; no skips, errors or Qt callback tracebacks** | `logs/ui-alignment-runtime-verified-full.log`; frozen production/test source hashes in `logs/ui-alignment-runtime-frozen-source.json` |
| Final UI matrix, DPR 1 | **40 frames, 359 checks, no failures** | `.runtime/ui-visual-alignment/dpr-1/final/evidence.json`; `logs/ui-alignment-matrix-dpr1.log` |
| Final UI matrix, DPR 2 | **40 frames, 359 checks, no failures** | `.runtime/ui-visual-alignment/dpr-2/final/evidence.json`; `logs/ui-alignment-matrix-dpr2.log` |
| Final output-panel native previews, DPR 1/2 | **8 frames and 24 checks at each DPR, all passed** | `.runtime/ui-visual-alignment/dpr-{1,2}/device/evidence.json`; `logs/ui-alignment-device-preview-dpr{1,2}.log` |
| Python compilation, dependencies, launcher and whitespace | **Passed** | `compileall -q api local tests`; `pip check`; `launch.bat -CheckOnly -NoPause` (`logs/ui-alignment-launch-check.log`); `git diff --check` |

The final captures total **96 frames and 766 checks** across DPR 1/2. Native
rasters and overview sheets were visually inspected, including the reported
AirPods 58% / Realtek fixture in light/dark, resting/hovered/muted/pending-target
states. The hovered captures show one composed volume track and thumb, with
the track aligned to the text column and percentage's right edge. Settings
captures confirm the restored blue Primary faces and input backgrounds.

The preceding broad baseline matrix recorded 34 frames and 325 checks at each
DPR. Its initial three-line song fixture intentionally selected the product's
short-track album layout, and its Finder fixture initially lacked enabled-bar
evidence; those captures are not the final representative lyric/Finder QA.
The final matrix supplies six bilingual timestamp pairs, completed original
artwork, enabled synthetic Finder presence, hover/DJ captions and synthetic
voice selections, and retains the native rasters plus overview sheets.

Meaningful new regressions cover single descendant paint ownership; nested
hover/tooltip mapping; moved/hidden descendant cache retirement; held-click
menu fencing; selected-row dwell across native boundaries; actual slider
endpoints and volume-only dispatch; readable names beside short percentages;
settings status/control columns and path/timer vertical edges; retained native
input faces; and source/header alignment with and without Back.

The high-DPI verification also corrected test inputs without changing product
behavior: new raster samples now convert logical coordinates to physical
pixels. Two stationary-pointer timer fixtures use an offscreen native Qt
window and hold their synthetic cursor throughout the real 520 ms wait, rather
than mixing that input with unrelated desktop Enter/Leave events. The original
timer failure was also reproduced against the source baseline. Separate
real-pointer settings checks retain actual mouse moves and bounded first-show
waiting; their fixture temporarily stays above the existing desktop's topmost
window. HWND traces showed that this unrelated window had intercepted the
previous moves even when the cursor lay inside the fixture's visible mask.

The first whole-suite rerun after the source-runtime repair ran 4,527 tests in
274.511 seconds, with two device-panel fixture failures. An isolated diagnostic
reproduced both without changing product code. The unchanged reorder/focus
test never established an active owner or initial slider focus; an inactive
fixture produced the same `focusWidget() == None`, while an active owner passed
the same assertions. It now activates the fixture, waits for actual activation
and asserts initial slider focus before checking that the row retains it.
The new Leave/dwell fixture could inherit a real-time hover start from native
handoff, then compare it against simulated time 101.51. That negative interval
reproduced its failure. It now clears the old dwell, verifies its simulated
start is exactly 101, keeps the supplied cursor coherent and avoids unrelated
desktop events during this synthetic sequence. All existing final assertions
remain. Evidence is `logs/ui-alignment-runtime-fixture-inputs.json`; the failed
whole run remains recorded in `logs/ui-alignment-runtime-final-full.log`.

## Limits and release state

Native synthetic screenshots and geometry/pixel assertions validate the
reported rendering and interaction mechanisms. They do not establish physical
AirPods/Core Audio behavior, acoustic synchronization, long-session playback,
account/service availability, actual Finder/taskbar auto-hide or physical
multi-monitor interaction. Arbitrary fractional scaling requires separate
interactive QA beyond the recorded DPR 1/2 matrix.

The scoped source change is **ready to hand off**, with a process score of
**100/100 after correcting the identified fixture issues and omitted runtime**.
Interfaces and source were read before edits, the user confirmed whole-program
scope, existing UI/provider boundaries were reused, focused/full/native verification
passed, and generated evidence remains ignored. The hardware and fractional-scaling
limits above remain separate release QA work. No version bump, native-helper
rebuild or EXE packaging is part of this source UI pass; loading the changes
requires restarting this checkout's `launch.bat`.
