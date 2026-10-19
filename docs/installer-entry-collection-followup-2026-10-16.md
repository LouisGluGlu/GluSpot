# Installer, first-use placement and collection follow-up — October 16

This follows the October 16 storyboard build, retaining product version 6.412.
The earlier storyboard archive and its evidence remain separate.

## Requested behavior

The native installer accepts a held background/island drag before completion.
Its actionable controls remain client hit targets, and subsequent layered frames
keep the moved position. The directory capsule opens a manager with the complete
editable path and a browse-parent action for fresh installations. Registered
upgrades show their original directory read-only and retain the existing
upgrade/state-preservation boundary. Empty, relative, drive-relative, drive-root,
incomplete UNC and device paths do not replace the selected directory.

The green installer progress follows the exact 342-by-48 logical-pixel brand
island contour. Both use the same placement; the green stroke is inset, with no
separate white track. Cached progress and fade images use two physical pixels
per logical pixel after supersampling.

MyDockFinder exposes a single monitoring switch. Enabled monitoring checks all
seven supported runtimes; disabled monitoring stops process/bar detection and
grays the statuses. Old path/process restrictions remain compatibility data.
Read-only detection statuses and independent placement/style previews remain.

The first-use source-choice page uses the actual display top edge in Top mode.
A trusted, enabled and mapped MyFinder bar selects Finder Bar, with the existing
expanded cradle shell. A 100 ms timer reads the existing cached bar state while
the choice page is visible; it does not add native scans. Bar availability and
position changes update the page, and source/login navigation stops following.

Finder Bar and Tasker Bar share the existing three material choices: album
cover with no background, with a fading background, or with a colored capsule.
The setting and preview are named “沉浸栏样式”. The existing saved style remains
the one source of truth across placement changes; no independent taskbar style
setting is added. Tasker retains its actual taskbar geometry, auto-hide and
transport hit targets.

Collection actions share the DJ footer row. During private-radio introductions,
their horizontal avoidance must follow the existing DJ expansion animation.
Their reveal, opacity and edge exit follow ordinary player controls even when
DJ status remains independently visible. The chooser retains song/account
authority checks, uses the whole-scene control ink and real playlist cover
metadata, and keeps scrolling within the actual available surface height.

## Evidence and limits

The installer interaction fixture passed 40 native assertions. These exercise
WM_NCHITTEST, native modal controls and position retention across actual layered
frames. They do not establish physical mouse-drag behavior. Directory-manager
PrintWindow capture returned false, so no screenshot is claimed for that dialog.
The 25%, 50% and 100% perimeter captures read the actual presented PARGB DIB.
See `logs/previews/installation/2026-10-16-drag-directory-progress/evidence.json`.

Ten isolated installer scenarios passed with generated inert executables, along
with all 101 progress and 36 fade asset checks. Evidence is
`.runtime/installer-acceptance-2026-10-16-c72d29c5/result.json`.

First-use placement passed 111 related tests at each DPR 1 and 2. Native widget
renders cover Top, Finder, right-positioned Finder and restored Top; their bar
context is illustrative, not an OS screenshot. Settings passed 103 focused and
75 related checks. These fixtures do not change the physical display scale or
establish real multi-monitor behavior.

The earlier full-run native access violation remains unexplained; no change in
this follow-up is presented as its fix. No live account mutation, physical
headset gesture or long background listening certification is claimed.

Automatic approval review rejected cleanup of the exact inert native-proof-v2
fixture, reporting only `blocked by policy`. No workaround was attempted. The
fixture remains in ignored `.runtime` and does not alter the running user app.

The collection/DJ follow-up passed 190 focused tests, with 19 additional checks
after the first-frame chooser ink correction. Final narrow/wide renders are in
`.runtime/favorites-shared-footer-2026-10-16/`; the `*-final.png` frames show the
completed layout rather than intermediate iterations. The 303-by-400 speech
frame keeps both collection actions and narration on one row. The chooser uses
real playlist artwork metadata; these offline renders do not establish network
artwork delivery or live-account writes.

Shared immersive styles passed 163 focused tests at each DPR 1 and 2, followed
by 30 device/bar checks. Native hidden-widget previews cover 54 frames with 102
assertions in `.runtime/immersive-bar-preview/report.json`. The material context
is illustrative, not a desktop screenshot. A full-run failure exposed an
unattached Tasker state borrowing dark native-bar ink. The shared rendering and
ink predicate now requires cached real Tasker bounds; ordinary system-light
ambience retains black lyrics until attachment. The corrected lyric/bar suites
passed 788 tests in 3.888 seconds.

Final combined verification, including the subsequently reported NetEase JSON
lyric-header correction, passed **5029 native Windows Python tests in
316.990 seconds**, with no skips, Python tracebacks or Qt callback errors.
The complete log is `logs/oct16-style-footer-lyrics-full-verified.log`.
`git diff --check` and compilation of the changed bar/footer/controller modules
also passed. The product version remains 6.412.

The lyric correction filters structured metadata before plain-text synthesis
and reuses the same predicate in cover/external display preparation for already
cached rows. Original lyric times, source units and translation mapping remain
unchanged. The 51 display/cover checks also passed at DPR 2. See
[root cause and verification](cover-lyric-json-metadata-2026-10-16.md).

## Final rebuilt artifacts

The final build passed all 12 native audio suites. Explicit UTF-8 reading in
`build-installer.ps1` fixes Windows PowerShell 5.1 misreading the Chinese
animation manifest. The actual Windows PowerShell installer build completed
in 169.453 seconds. Installer behavior and prepared animation assets retain
the previously verified interaction boundaries described above.

The rebuilt EXE passed frozen audio-package acceptance; no GUI, user settings,
default-endpoint change or receiver session was started by that diagnostic.
Read-only package audit passed 246/246 checks across 100 source modules,
the entrypoint, resources and dependency provenance. Final evidence is
`logs/oct16-style-footer-lyrics-frozen-acceptance.json` and
`.runtime/release-audit-2026-10-16-style-footer/result-lyrics.json`.

The new portable archive contains 8,399 entries, including its build receipt
and Chinese instructions, under `GluSpot-6.412/`; ZIP CRC validation passed.
The dated Setup is byte-identical to the canonical Setup. Earlier dated ZIPs
remain unchanged, and the preceding canonical Setup is preserved in
`release/before-immersive-footer-2026-10-16/`.

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| `release/GluSpot-6.412/GluSpot.exe` | 10,321,257 | `21db040cc21992042ee266e47c3d1020a904dab7356fabb89834d2be7e8a6d68` |
| `release/GluSpot-6.412-2026-10-16-immersive-footer-Setup-x64.exe` | 161,684,478 | `3154ead9e5e326ae0f6df0171ce6f411245558f1a440e598e79089f228a56cd7` |
| `release/GluSpot-6.412-2026-10-16-immersive-footer-windows-x64.zip` | 212,807,539 | `f56ec38bd78dfc2cd8b205b8a1a9e0153fd459a4296bd1637820f6f686d4b5ff` |

Both dated distribution files have `.sha256` sidecars. The complete receipt is
`logs/oct16-style-footer-release-artifacts.json`. These remain unsigned internal
test builds, with the physical-display/network/hardware limitations above.
