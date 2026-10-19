# Installer progress and application handoff — October 17, 2026

Source version remains **6.412**. This follows the user's request to remove the
island perimeter progress and eliminate the disappearance before application
startup. The existing brand, four-mode animation, slogan and eased upward travel
remain the authority for the completion storyboard.

## Progress

The installation-folder row becomes a native GDI+ capsule at logical
`(16, 214, 338, 30)`, with a maximum 15-pixel radius. The fill and percentage
come from Inno Setup's actual extraction gauge, without elapsed-time estimates.
Before installation, the existing directory selector and installation action
remain in that row. During installation, neither leaves an invisible input
target over the progress indicator.

The card stays 258 logical pixels tall throughout extraction. On completion,
the lower animation and progress fade for 600 ms; the body then smoothly
contracts from 258 to 64 logical pixels. The retired perimeter and fade PNG
frames are no longer generated, loaded or accepted by the asset-reuse check.

## Handoff

The old sequence launched GluSpot only after the upward travel, then closed
the installer after a fixed 900 ms. Qt initialization had no readiness contract
with that timer, so the installer could disappear before the app displayed.

Installation completion now starts the installed application while the
completion storyboard runs. Three session-local Win32 events use a bounded
PID/tick token, carried with the existing validated physical island rectangle:

- `ready`: application initialization has completed and its event-loop gate
  can wait without blocking Qt.
- `reveal`: the native installer has arrived at its final screen-top position.
- `painted`: a watched application surface has finished a paint and remains
  visible when the deferred acknowledgment runs.

The app opens existing installer events; it does not create an absent session.
Until reveal it defers the normal first-use/configured startup route. The
installer keeps its final island until the app acknowledges a frame. Entry,
island and music-library destinations are watched, and a snapshot of an entry
that hides in the same turn cannot acknowledge the destination. Legacy
rectangle-only launches and ordinary startup retain their existing routing.

The native wait is bounded at 30 seconds and reports a launch/display failure
without undoing the completed installation. An abandoned installer cannot hide
the app indefinitely: its gate also expires and continues ordinary startup.
Event handles and Qt filters/timers are released on completion or shutdown.

## Evidence

`tools/verify_installer_progress.py` compiles the real native compositor at
DPR 1 and 2, exports its presented PARGB DIB and exits from initialization
before installation. Its **18 checks** cover 0/1/25/50/75/100 percent fill widths,
constant height, independent brand pixels, fade and absence of residual rims.
The checked frames are in the ignored
`.runtime/installer-bottom-progress-2026-10-17-v2/` directory.

`tools/verify_installer_handoff.py` exercises actual native completion against a
separate inert offscreen WinForms process using the named events. Its **16
checks** cover fast preparation, preparation finishing after arrival, and a
missing paint acknowledgment. In the final fixture the installer retained its
surface for approximately 281 ms after arrival in the fast case and 1703 ms in
the slow case; a shortened fixture timeout rejected missing paint. These are
fixture timings, not claims about frozen application startup speed. Evidence
is in `.runtime/installer-handoff-cross-process-2026-10-17-final/`.

The fixtures perform no actual installation, registry registration, user-app
launch, device playback or physical input. Python regressions also cover native
event exchange, strict CLI parsing, monitor mapping, deferred acknowledgment,
hidden-source snapshots, expiration and repeated cleanup. The final combined
native Windows regression passed **5061 tests in 297.613 seconds**, with no
skips or Qt callback tracebacks. Its log is
`logs/oct17-installer-audio-lifecycle-full.log`; release artifacts and frozen
package verification are recorded in the combined release report.
