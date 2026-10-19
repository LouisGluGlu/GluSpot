# Installer and library follow-up — October 18, 2026

Version remains **6.412**. This was delivered first as a source and installer-template follow-up in the `292c` checkout. The subsequent user-requested Setup/EXE/portable rebuild is documented in the [October 18 package report](windows-release-installer-library-2026-10-18.md); earlier dated packages remain preserved.

## Requested behavior

| Request | Result |
| --- | --- |
| Three installer silhouette bars disappear against their background | All three use the accent color; their cover/background retains the lighter color. |
| First-use choices appear in the center | The initial native show reapplies the actual Top/Finder attachment. Unchanged bar evidence also repairs displaced native geometry without restarting expansion. |
| White installer card has no depth | A soft alpha shadow occupies 24 logical pixels of transparent padding. The actual card size, hit areas and installer-to-island coordinates retain their content bounds. |
| Completion fills black before separately showing the slogan | Forty cached geometry frames shrink the white inner opening while preserving white slogan letters. There is no intervening all-black frame followed by text fading in. |
| Library loading is only bottom status text | Directory/search placeholders and missing artwork use the shared glowing skeleton painter. The old loading text lane is removed. |
| Navigation stalls and the library returns in the center | A bounded scene raster reduces repeated full-DPR composition. The cover remains an independent native-resolution layer. Library return restores its previous geometry across native mapping; new pointer/key interaction cancels delayed placement repair. |
| Clicking currently playing playlist/mode A restarts it | The account/source/collection identity is retained with the player's queue-origin generation. Reopening the same live origin returns to its playback page without resetting position, queue or pause. |
| Play waits for audio before leaving the library | A default click immediately exposes window playback with the selected title, cover and loading skeleton. Catalog/stream completion updates this page without another navigation animation. |

Explicit internal fullscreen/island routes remain supported, but ordinary library clicks now prefer window playback. The rotating current album also opens the existing song in window playback.

## Recovery and cancellation

Account, personal/recommended directory and partial search failures retry automatically with 1/2/4/8/16/30-second backoff. Missing/nonadvancing pages retain their cursor and successful rows. Cover downloads retain the existing two quick retries, followed by a 30-second recovery interval. Hidden/minimized libraries pause retry timers; source/account/query changes fence stale work. Artwork decoding retains its two-worker limit, and scrolling cannot bypass backoff. No playlist songs are fetched just to paint a library skeleton.

The origin generation differs from positional queue-click tokens: next/previous, queued additions and later playlist pages preserve the origin, while replacement/stop invalidates it. A headset Next during a pending replacement cancels that reservation in the existing player. The controller now recognizes this same-origin signal, removes the pending skeleton and preserves the actual previous playlist identity. Loading pages clear old lyric/queue hit regions and do not expose invisible favorite/playlist/DJ hit areas.

Failures restore the library with the existing specific error message and preserve healthy retained audio. Returning before an initial selection completes cancels that request and reopens the library the user selected, including when retained audio belongs to another platform.

## Verification

- Native installer: **71 assertions passed** — 25 visual/progress checks at DPR 1/2, 30 font-fallback checks and 16 handoff checks. These use offscreen inert fixtures and do not install the actual application or alter user playback. Evidence: `.runtime/installer-progress-48d92274/`, `.runtime/installer-fonts-3bfd9a9c/`, `.runtime/installer-handoff-6c3282e1/`.
- Focused modern Qt: **302 tests passed** at each DPR 1/2 in 18.751/19.689 seconds. Logs: `logs/ui-focused-final-2026-10-18.log`, `logs/ui-focused-dpr2-2026-10-18.log`.
- Navigation raster comparison: 96 frames per scenario, using synthetic entering/returning pages. DPR 2 P95: **21.55 → 12.86 ms** entering, **22.29 → 12.81 ms** returning. Average RGB difference is 0.054/0.053 on a 0–255 scale; native artwork remains separate. DPR 1 retains the original direct path and produces identical pixels; no DPR 1 speed improvement is claimed. Evidence and before/after PNGs: `.runtime/navigation-2026-10-18/evidence.json`; reproducer: `tools/benchmark_music_navigation.py`.
- Legacy source/UI check on this Windows 11 host: actual CPython 3.8.10 / Qt 5.15.2.1 imported **51 modules / 409 interfaces** and rendered the existing native fixtures without callback errors. Evidence: `.runtime/legacy-ui-2026-10-18/result.json`. The modern unittest fixture tree itself includes pre-existing Python 3.10 syntax and cannot be used wholesale with Python 3.8; that attempted run stopped during test-module import, not application code.
- New native UI screenshots: **30 checks / five frames passed per run** at modern Qt DPR 1/2 and Python 3.8 / Qt 5 DPR 1. All use hidden widgets and in-memory fixtures. The directory skeleton's light sweep changes over time; partially loaded content remains present; pending playback clears stale transport, lyric, queue and collection hit regions while retaining the library return action. Captured full/partial library and pending-cover/artwork frames were visually inspected. Evidence: `.runtime/library-loading-dpr1-2026-10-18/`, `.runtime/library-loading-dpr2-2026-10-18/`, `.runtime/library-loading-qt5-2026-10-18/`; reproducer: `tools/verify_library_loading_navigation.py`.
- The first full run completed 5223 tests with two obsolete artwork assertions: they expected terminal failure after two retries and stopped skeletons before daily/FM artwork resolved. Only these tests were updated to check continuing recovery, its 30-second backoff and stopping after all visible content completes. The related **26 tests passed** in 2.093 seconds (`logs/artwork-recovery-final-2026-10-18.log`).
- Final modern regression: **5223 tests passed in 300.816 seconds** with no skips or Qt callback tracebacks (`logs/ui-full-final-2026-10-18.log`). Changed Python modules compile, `git diff --check` passes, and the saved new UI/benchmark source hashes match the final source. No production source changed after this run began.

Actual Windows 7 installation, physical monitor smoothness, network outage/recovery and real HomePod/headset listening remain unverified by these offline fixtures. No user installation, receiver or live Dock process was modified for validation.
