# GluSpot 6.412 — October 16 installation and UI follow-up

The October 14 source backup remains at `E:\Glusopt_waitingforlaunch\GluSpot-6.412-2026-10-14`. This follow-up retains version 6.412 and the current Liquid Glass controls. No running user instance was stopped or restarted.

## Installation and first use

- A traditional, per-user Windows installer selects a dedicated installation directory and registers a system uninstall entry. The default is `%LOCALAPPDATA%\Programs\GluSpot`.
- Frozen builds keep settings, encrypted account state and caches in `GluSpot\` beside the executable. A fresh extracted or installed directory does not silently import another copy's AppData onboarding flag. Source checkouts retain their existing data locations.
- Higher versions replace executable/dependencies while preserving the owned data directory. Same/lower versions require interactive overwrite confirmation; silent replacement is rejected. Busy executables block upgrade and uninstall. Failed upgrades restore the old executable and dependencies.
- An existing registered installation is upgraded in its original directory. A new installation may choose its directory; changing an existing installation's directory requires a prior uninstall, avoiding an accidental second copy with empty settings.
- Full uninstall removes GluSpot-owned binaries, settings, accounts and caches, while preserving unrelated files. Ownership/path and reparse checks protect neighboring directories.
- First use selects both program playback and Windows SMTC synchronization by default. At least one must remain selected; refusing the final deselection gives a short animated border and explanatory text. SMTC help describes session availability without pretending to inspect another player's private setting.

## Shared background and controls

The first-use page uses `AlbumFlowBackgroundRenderer`, exactly as window/fullscreen playback does, with the same flow intensity and 1.2-second smooth palette transition. Its target palette changes every five seconds. The extra white overlay was removed.

The native installer exports 600 frames from that renderer at a bounded 256×185 raster. The 20-second loop changes palette at five-second intervals, with the same palette interpolation. Only the final geometric pose is blended into the first rendered pose to close the loop. Adjacent palette-boundary maximum RGB deltas are 4, 3 and 2; loop wrap delta is zero. These are image continuity measurements, not physical display frame-rate claims.

Native timing observed 197 actual background updates in 6.703 seconds, about 29.39 Hz, with a maximum callback time of 16 ms. Initial frame assets total 85,450,236 bytes before compression. Installer assets precede the payload in the solid archive so displaying the wizard does not first decompress all application dependencies.

Settings traffic-light faces are circular 20×20 controls again: the generic minimum button height no longer overrides them. Rounded native settings masks leave room for the stylesheet antialias fringe. Finder Bar appearance selectors now have an immediate preview below them, sharing the live Finder background painter rather than approximating its material.

## Visibility and external listening

Closing normal application surfaces restores the island as the minimum display. Minimizing the player, library or login page also restores it; restoring a larger page hides the island without discarding navigation ownership. Explicit tray show/hide remains authoritative, including during asynchronous login/navigation completion and shutdown. The tray action includes a green/gray visibility indicator.

MyDockFinder has a right-aligned monitoring switch in settings and the tray. All seven existing process candidates are selected by default. Disabling monitoring stops its timer and probes, clears availability, and makes the indicator gray.

External music reading now uses Windows SMTC only. Player-specific UIA/progress/plugin settings are removed from the visible settings flow and normalized to dormant values. A single global system lyric offset remains; it must not alter program-owned playback. Existing own-session exclusion prevents GluSpot's published session from feeding back into its external reader.

## Lyrics, queue and artwork

- Sparse highlighter classification is fixed at 0.8 seconds per word/glyph, including migration of previously saved thresholds; its slider is removed.
- Intro/countdown conversion defaults off. Explicit production-role/copyright lines are still removed throughout the song, including repeated or coalesced credits. Repeated lyric-like uses of broad English words such as “Music: is my life” retain their lyric evidence. Source timestamps, translations and genuine unit clocks remain intact.
- Sparse highlighted glyphs use the existing neighboring read-lift function while keeping the highlight. Ordinary word-lift behavior remains unchanged. Qt grapheme boundaries handle combining marks and wrapped source offsets.
- External blurred lyrics extend beyond their internal text column and clip at the real surface; cover lyrics retain the cover boundary.
- Queue selection fill, text and current-song centering move together over 440 ms. Consecutive changes retarget from the displayed position; deliberate wheel scrolling cancels automatic centering.
- Library catalogs request all known playlist covers before requesting songs solely for artwork. Explicit playlist playback enables sequential 320-pixel queue thumbnail filling, while current/true-next originals retain priority. Two concurrent workers, generation fences, bounded retries and caches remain in place. Missing artwork has a shared shimmer that stops when hidden or fully loaded.

## Verification and limits

Final native Windows Qt verification passed **4922 tests in 288.260 seconds**, with no skips, failures or Qt callback tracebacks (`logs/oct16-verified-full.log`). The build passed all **12 native suites in 77.93 seconds**. All 119 changed/new Python files compiled, and `git diff --check` passed. Earlier focused evidence includes 209 artwork/queue tests at DPR 1, 29 at DPR 2, 94 entry/packaging tests at DPR 1, 46 at DPR 2, 116 settings/listener tests and 122 SMTC provider tests. Integration corrections additionally passed 92 SMTC/artwork/migration/glass tests, 180 lyric tests, 88 minimum-surface tests, eight lifecycle tests and 14 DPR 2 minimum-surface checks. Five related visibility modules passed 311 tests; the native-navigation geometry fixture retained its exact rectangle assertion after waiting for initial window placement.

The fresh frozen application passed **148 package/source/resource checks**, comparing every bundled project module with its current source and verifying the new helpers, native helper, runtime, fonts and license resources. NetEase, QQ and Kugou adapters loaded offline. Diagnostic-only frozen acceptance exited 0 without GUI startup or user settings access. It does not establish account/network health. Evidence is in `logs/package-contents-6.412-2026-10-16.json`, `logs/audio-package-6.412-2026-10-16.json` and `logs/build-exe-6.412-2026-10-16.log`.

Native preview evidence includes 18 settings/Finder frames at DPR 1/2, 12 entry frames, 20 artwork/queue frames and lyric boundary/highlight previews under ignored `.runtime/` directories. Taste v2 was consulted as requested; its documented website-only direction workflow does not apply to native app screens, so acceptance used native rendered screenshots while retaining the requested Liquid Glass appearance.

The native installer passed **nine isolated groups** using inert payloads and unique registry identities: fresh install/system uninstall, registered-directory enforcement, same/lower silent rejection, busy upgrade/uninstall protection, higher-version upgrade, interrupted-install rollback, missing-executable repair, installation discovery and full owned-state uninstall. These checks do not launch GluSpot or access the user's real configuration. Native wizard screenshot capture was unavailable after foreground-input protection blocked interaction; the timer measurement and exported frames do not establish a completed interactive visual review of every installer page. No physical headset gestures, real account/network login, acoustic output or long-session smoothness are claimed by these fixtures.

## October 16 artifacts

The installer below was rebuilt from the new October 16 application payload, after the source fixes. It supersedes the earlier rehearsal installer. Version remains 6.412, so installing over another 6.412 copy requires the explicit same-version overwrite confirmation.

| Artifact | Location | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Traditional installer | `release/GluSpot-6.412-Setup-x64.exe` | 139,569,956 | `84a85ab8b08835377170d609069817e06559adfd26fc18478603647ff7ed6731` |
| Dated portable ZIP | `release/GluSpot-6.412-2026-10-16-windows-x64.zip` | 198,335,431 | `e96065027cda2dd8316faad8ab3dd36d9feccb6127f04163d545d19890ce4762` |
| Application EXE | `release/GluSpot-6.412/GluSpot.exe` | 10,271,123 | `94d186c3f90b2803ffa24f76e143182b4726a5237b1ccac2d5849f022dc9840e` |

The ZIP passed CRC verification for all 8,391 files. Keep the complete application folder together. `BUILD-INFO.json` records the final test result, bundled project-source hashes and acceptance limits; `使用说明.txt` describes installation, local data and overwrite/uninstall behavior. Checksum sidecars accompany the ZIP and installer. Detailed receipts are `logs/package-artifact-proof-6.412-2026-10-16.json` and `logs/installer-artifact-proof-6.412-2026-10-16.json`. The October 15 dated ZIP remains intact.
