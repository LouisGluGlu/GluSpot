# t168 / t169 / t170 — capture seek, quiet status and login

Source-only B follow-up to `8caf672` on `grok/capture-narrow-v4`. No version/build/provider selection change. No PC launch, cloudmusic restart, login or live-settings write. The parent owns PC sync/restart and native acceptance.

## Behavior

- Remove the narrow capture-unavailable banner; retain frozen lyric highlighting and capture diagnostics. Library notices become hover descriptions without a footer or a layout shift. Retry/login/settings/playback failures remain visible. Retrying “play next” clears only that action's stale error on success.
- DJ preload/current-intro state uses the existing window/fullscreen icon outline and hover description. It cannot create a status capsule or force hidden controls visible. Pending/failed narration cannot replace island lyrics; actual narration and its existing outro icon lifecycle remain. Device discovery/member changes update confirmed output glyphs/count without name announcements. Retired plugin/island arrival compatibility stays retired.
- Narrow external and cover-inside lyrics become sharp during wheel browsing and restore their ordinary row blur 350 ms after the last accepted lyric wheel event. The four-second viewport browse timeout is independent. Control glass and the prepared heavier chrome-band veil remain intact. Wide browsing retains its established policy.
- Login reuses the source matrix, SVG cache and dark glass helpers. NetEase/Kugou/QQ remain enabled only when their real backend is available; Qishui/Spotify/Apple Music stay disabled. Cards use compact icon/text rows or a grid; upcoming captions are quieter and no longer repeated in the unavailable footer. The QR composition is centered, stacked on sufficiently tall narrow pages, with compact two-column fallback on short displays. The original seven supplied source SVGs are unchanged; two local vector marks replace the placeholders' shared note glyph. Product QR pixels still come exclusively from the service.

## Capture seek cause and bound

The old CEF policy required ≤50 ms error and waited only 40 + 40 + 60 = 140 ms. The independent verifier compares native position with `target + elapsed_at_native_progress_receipt * rate` while playing. Dispatch/native/CDP delivery time contributes to that error; the PC's 80–170 ms readbacks were therefore rejected even with a genuine acknowledgement. Every `unverified`/`fake-ack` then set `_cef_seek_enabled=False`; an unchanged track's early-return binding never cleared it.

CEF now accepts **absolute verification error ≤250 ms**, with delays **100 + 150 + 250 = 500 ms**, request timeout 800 ms and each read timeout 150 ms. This is an independent-readback bound around the progressing target, not a promise of physical display latency. New sequence/timestamp, generation, native/provider track and source guards still apply. An acknowledgement or state-only event is insufficient. Errors >250 ms and missing movement remain failures.

Completed unverified attempts allow the next explicit lyric gesture to retry. Each gesture gets a fresh existing `CefSeekBackend`, so preflight cannot remain gated by the preceding request's progress fence, including paused/no-move cases. A timed-out/exceptional mutation still disables its route and marks the shared executor uncertain; no second native write or fallback is allowed in that condition. No automatic player restart or capture disable is introduced. Existing-port `:4422` uses this shared path; no port is hardcoded.

Tests accept both signs of 80/170/249 ms, reject both signs of 251/500 ms, deliver a new event beyond the former 140 ms window, retry without rebind after failure, and retain stale/state-only/generation/uncertain-write protection. UI word-hit/offset/track-fence tests remain green.

## Owned setup isolation

`OwnedPlaybackTests` injected the music player/audio but omitted DJ player/audio. `OwnedMusicPlayer.__init__:107` consequently constructed real `QAudioOutput`, followed by real `QMediaPlayer`, before any network/login work. Four existing constructors now inject the independent DJ decoder/output too; the thread fixture also supplies inert prefetch tasks. A constructor guard forbids both native multimedia constructors in fully fake setup. A provider-routed scenario covers authorized play, pause/resume, progress, parsed YRC word-start seek and wrong-track refusal, previous/next and EOF auto-next; separate offscreen cases cover library return and owner/surface sync.

The historical hang did not reproduce in isolated baseline tests (75 tests / 2.923 s). The unwanted native initialization dependency is established; a specific missing plugin or OS deadlock is not proven. This fix isolates unit setup without changing product decoder behavior or adding a speculative product timeout. The updated owned module finishes with 77 tests / 2.892 s and exit 0, without a user login.

For live owned verification the user must explicitly enable **设置 → 播放与采集 → 程序内播放**, then select a song via the existing platform login/catalog. This task did not switch that setting on the PC.

## Verification and evidence limits

All commands use `/workspace/gluspot/qtvenv/bin/python`, offscreen Qt and disposable private settings/cache roots. Each process is externally bounded. Reproducible command wrapper, complete logs, the 删/改/留 string inventory and ten login renders are under `/workspace/gluspot/handoff/t168/`; generated evidence is outside Git. Login QR-like render artwork is an explicit offline fixture, never a login token; production still accepts service pixels only.

| Focus | Result | Evidence filename |
| --- | --- | --- |
| Capture subsystem: `unittest discover -s tests -p 'test_capture*.py' -v` | 265 passed / 12.459 s | `capture.log` |
| Narrow scroll/blur/veil/v4/content/background plus UI lyric seek | 72 passed / 4.631 s | `narrow-green.log` |
| Login/platform/source icons/resilience/bridge | 104 run / 3.422 s, 2 pinned-SDK skips, exit 0 | `login-final.log` |
| Owned playback unit module | 77 passed / 2.892 s | `owned-playback-tests.log` |
| Library window | 45 passed / 2.816 s | `library-window.log` |
| Library controller | 81 passed / 2.891 s | `library-controller.log` |
| Owned library back | 3 passed / 0.006 s | `owned-library-back-isolated-tests.log` |
| Owned app/surface sync | 10 passed / 0.874 s | `owned-app-sync.log` |
| DJ window/island/Finder, output motion, DJ controls, owned surface | 115 passed / 3.276 s | `toast-final-private-temp.log` |
| Final login geometry recheck | 53 passed / 1.335 s | `login-geometry-final.log` |

Changed-source compilation (22 Python files), both new SVG parses and `git diff --check` pass. Branding/spec/Windows version inputs are byte-identical to `8caf672`. Ten final login rasters (waiting/error at 330×400, 433×550, 433×720, 560×360, 960×540) were generated offscreen and representative narrow, wide and short-display frames visually inspected.

The full discovery command was deliberately not run, per the task's focused-test preference and native shutdown history. A broader 93-test library/back/surface invocation reached OK then exited 139 at Qt shutdown; subsequent isolated controller/back/surface commands exit 0. This is separate from owned setup and remains a native lifetime/order gap.

Two additional audit suites are not included in the green results: unchanged `test_narrow_live_lag` runs 15 tests with 25 errors because its non-widget `FrameHarness` copies a method that now calls `super()`; unchanged `test_retired_runtime_extras` runs five with one error because an old media fixture returns `None`. Both failures were reproduced from a clean exact `8caf672` archive with private settings; see `narrow-live-lag-baseline.log` and `retired-baseline.log`. No unrelated production clock/provider behavior was changed to make legacy fixtures pass.

Windows/PC `:4422` word-click playback, actual audio, compositor/high-DPI appearance and long-running behavior remain unverified here. The parent should sync the committed source and restart GluSpot, attach the existing cloudmusic endpoint, then check repeated word clicks (including recovery after a missed observation), ≤250 ms logged independent readback error, capture availability, scroll sharpening/restore and quiet DJ/output state. Do not restart NetEase to perform this acceptance.
