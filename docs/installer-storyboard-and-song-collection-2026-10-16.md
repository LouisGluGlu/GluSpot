# Installer storyboard and current-song collection

Date: 2026-10-16. Product/version: GluSpot 6.412.

## Behavior

The user's October 16 storyboard is the authority for this revision. The installer
has a white rounded body and one black island. The approved Stage3 v3 geometric
brand loop occupies that whole island rather than a second nested capsule.
Installation progress comes from the actual installer and follows the island's
outside perimeter. The lower four playback silhouettes use the existing player
mode coordinates and elastic motion, changing in place rather than sliding
across the card. Colored palettes exclude black/white/gray; the accompanying
short messages retain the user's supplied wording.

The installer now composes cached layers into one native premultiplied-alpha
DIB and presents that exact buffer with `UpdateLayeredWindow`. Rounded edges
retain fractional coverage instead of a binary `CreateRoundRectRgn` cutout.
Animation rasters retain 2x source resolution and are sampled at the actual
physical window size. The upward motion uses a quintic S curve with zero
velocity and acceleration at both endpoints over 1.6 seconds. These changes
apply to the installation animation and its demonstration.

Only the settings-style red close and yellow minimize buttons are exposed.
Pointer movement shows them, then inactivity fades them out. There is no
maximize control or fullscreen action. Close retains the installer's existing
cancel/rollback authority; minimize uses the native Windows operation.

Completion fades the lower illustration/text, progress and white body, waits
for a full five-second brand loop, expands the black island, and shows
`This is true music \^0^/` in Source Han Sans Heavy. The slogan fades away and
the island travels to the top of the selected monitor with a slow start,
accelerated middle and gradual deceleration to the endpoint, as corrected by
the user's latest feedback.
The app accepts only validated integer physical bounds and reuses the existing
monitor/DPI mapper before continuing the same object as a black expanded island.
Both playback choices default on; at least one stays enabled. Confirming program
playback opens the existing source/login page even if system media is active.
Closing login returns to the waiting island. A verified MyFinder bar uses Finder
Bar placement; otherwise initial waiting placement is Top. Already configured
upgrades retain their playback settings.

The player's library action becomes Login with the user's supplied avatar icon
when program playback is enabled and no account is logged in. Its existing
hover label reads “登录”. Truly empty media uses the actual Windows light/dark
achromatic palette. A paused real song keeps its album colors.

Search's queue action uses the supplied queue-plus SVG. Owned songs have one
glass capsule with a heart and folder-plus. NetEase favorites and writable
playlists use the pinned service contracts; QQ/Kugou collection writes remain
explicitly unsupported. Song/account authority is captured at press time, and
old panel rows cannot target a new song/account, including same-user re-login.
Opening a chooser during the initial status read fetches playlists once when
ready. An open chooser holds the ordinary player controls visible; dismissal
restarts their idle timer. Escape closes the chooser first.

## Sources and architecture

- `tools/preview_install_animation.py`: approved Stage3 word-block morph.
- `packaging/windows/prepare-installer-assets.py`: preloaded 2x animation rasters,
  supersampled silhouettes and the embedded Source Han Heavy slogan.
- `packaging/windows/gluspot-installer.iss`: native presentation, actual progress,
  exact-target process replacement, rollback and app handoff.
- `local/installer_handoff.py`: checked CLI and physical/logical monitor mapping.
- `local/playback_entry_window.py` and controller: black expanded-island setup,
  existing source/login widgets and navigation transitions.
- `local/music_song_collection.py` and controller: UI state and fenced actions;
  service/provider boundaries remain in `api/`.
- User SVG provenance: `assets/music_controls/README.md`.
- Platform evidence: `docs/song-collection-contract-2026-10-16.md`.

The previously rendered AlbumFlow installer assets are compatibility resources;
the current user storyboard supersedes that installer composition. The player
and music-library dynamic backgrounds retain their existing AlbumFlow renderer.
The installed Taste skill targets web pages; this native revision follows the
user storyboard and native screenshot review, without claiming its browser
inspector has validated Qt/Inno windows.

## Verification and artifacts

The final ordinary native Windows regression completed successfully: **4,987
tests in 283.762 seconds, exit code 0**, with normal garbage collection and no
additional cleanup patches. Its log is
`logs/oct16-storyboard-full-native-recheck.log`.

One preceding full run hit a Windows access violation at `processEvents()` in
`test_owned_audio_outputs`; that log remains at
`logs/oct16-storyboard-full-verified.log`. The isolated module (40 tests), its
preceding combination (67 tests), the same combination after full discovery
imports (67 tests), and the final full regression all passed without code or
fixture changes for that crash. Its cause remains unconfirmed; this report does
not claim a native-memory defect was fixed.

The application build and final Inno Setup 7 compilation both completed with
exit code 0. The frozen executable's audio-package acceptance also exited 0;
its report is `logs/oct16-storyboard-frozen-acceptance.json`. This diagnostic
checks packaged dependencies, the Windows endpoint and bundled native helper,
without opening the application UI or changing settings/default outputs.

A read-only audit passed 30/30 package checks across 8,397 files. The five supplied
SVGs, Source Han font/license/provenance and online-music bridge match their source
bytes. The frozen PYZ includes the handoff and song-collection modules. No app
configuration, accounts, runtime logs or caches were present. Evidence is
`.runtime/release-audit-2026-10-16-installer-refresh/result.json`.

The final native installer fixture passed all ten install, original-directory
upgrade, version rejection, lock handling, exact-target process replacement,
state preservation, rollback, repair, isolation and owned-state uninstall
scenarios. Evidence is
`.runtime/installer-acceptance-2026-10-16-ca93e056/result.json`. These scenarios
use inert executable fixtures rather than the user's running installed copy.

The native visual readback retains 33 alpha coverage levels at 100%, 150% and
200% physical raster fixtures. These fixtures do not change the OS display scale
and do not establish physical multi-monitor behavior. The updated demonstration
uses the exact premultiplied DIB presented to Windows and the real window
coordinates; it is not a browser recreation. Its path is
`logs/previews/installation/2026-10-16-native-alpha/gluspot-installer-native-alpha-eased.mp4`.
Measured first/middle/last-third upward speeds are 191.9/449.6/203.2 pixels per
second. The visual evidence and final installer source have matching SHA256
`42c1a5a794670bedf6bc785be187b3abae03ce0dd595a094ead25196c9cc9bdd`.

Focused verification includes 105 first-use tests and 45 geometry/collection
tests at each DPR 1/2, 91 backend/Node tests, 42 integrated handoff/package tests,
and 33 bridge tests. The close test now deliberately blocks all three real
child-process waits, proves close returns and pending requests are interrupted,
then releases the waits and verifies actual child reaping. This replaces a
150 ms wall-clock assertion that failed under concurrent packaging load; the
production close implementation was unchanged.

No live account favorite or playlist mutation is used for automated verification.
NetEase write support is checked against the pinned adapter and offline contracts;
QQ/Kugou collection writes remain explicitly unsupported. Physical headset
gestures, long background listening and real network latency are not established
by these checks.

### Rebuilt files

The version remains 6.412. Earlier dated portable archives and the previous
installer under `release/before-storyboard-2026-10-16/` remain available.
The new portable archive contains all 8,397 payload files under `GluSpot-6.412/`;
its complete ZIP CRC check passed. Keep the executable with its `_internal`
directory. Checksums are also stored beside the installer and archive.

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| `release/GluSpot-6.412/GluSpot.exe` | 10,305,467 | `a1c222209bbf1f144b60835cf61197402b80c6abbe69d0941b1f197912c79951` |
| `release/GluSpot-6.412-Setup-x64.exe` | 161,670,651 | `e5c8fc71adf6324d7b5f649af9498032cf4aed98d1e62604dda0d018e8c6a3b5` |
| `release/GluSpot-6.412-2026-10-16-storyboard-windows-x64.zip` | 212,783,803 | `cd5b34cc441627e6e150d10e69c1425171ce72df37ac5768a208dbdf43403a3a` |

Build logs are `logs/oct16-storyboard-build.log` and
`logs/oct16-storyboard-installer-build.log`; the artifact manifest is
`logs/oct16-storyboard-release-artifacts.json`.
