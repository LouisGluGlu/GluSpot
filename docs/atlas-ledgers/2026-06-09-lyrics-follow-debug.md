# Atlas Ledger: Lyrics Follow Debug

Date: 2026-06-09

## Symptoms

- Clicking player lyrics produced no visible GluSpot reaction.
- Click-jump lyric display lagged the player by about 0.5-1s.
- Saved progress polling appeared in settings but did not reliably trigger.
- Foreground window changes could clear/reload lyrics for the current track.

## Root Causes

- Reference-copy reconciliation showed the preferred behavior did not use click-event lyric recognition at all. It used lightweight player-position polling only when timed lyrics existed, with UIA text progress first and visible lyric rows as fallback.
- The later implementation range-shifted the problem into a heavy "click -> global hook -> UIA hit-test -> screenshot progress anchor -> retry burst" system. That made click recognition fragile and made ordinary progress dragging slower because the provider paid for pointer sampling, hit testing, image capture, and immediate refresh paths on hot updates.
- Click detection relied on periodic `GetAsyncKeyState` sampling from refresh paths; short clicks could be missed, and global clicks were ignored when the source app was only inside the foreground grace window.
- The previous click-jump regression baseline only proved the mocked "already hit a lyric row" provider path. It did not prove the real Windows click chain: low-level mouse event -> source-app grace -> UIA lyric row hit-test -> progress-image reread.
- Real player lyric rows can fail UIA geometry/text hit-tests even when the click is visibly inside the lyric/player content area, so an exact-row miss incorrectly suppressed progress recognition.
- The initial click anchor was consumed after one immediate read; no explicit post-click progress burst remained, so a slow player-side lyric jump could still settle after GluSpot had stopped forcing progress reads.
- Progress dragging and lyric clicking shared the same recent click/pointer window. The provider tested lyric hit geometry before checking whether the pointer was on the progress bar, so a normal progress drag could pay for UIA lyric row/content scanning first.
- "Force progress" still used the generic immediate reader. When image progress missed, that reader could fall back into UIA text scanning and even a second image pass, making drag/poll/click recovery slower than the progress-only behavior the feature was supposed to simulate.
- Click-triggered progress-image reads used the first captured progress value without compensating for player UI/screenshot latency.
- Progress polling could remain behind cached visual projection instead of forcing the provider into a real player-anchor refresh when the saved interval was due.
- Cached foreground-title correction treated transient window title changes as confirmed track identity changes and cleared loaded lyrics.

## Decisions

- Copy the reference baseline for default behavior: when timed lyrics are available, the provider calls the player-position reader's lightweight `read(...)` path; it does not start the global mouse hook, does not hit-test lyric clicks, does not call immediate screenshot anchors, and does not schedule click retry bursts.
- Treat the old forced-anchor/click-recognition path as superseded debug/compatibility code unless a future contract proves a faster end-to-end design with real desktop measurements.
- Keep progress follow freshness by allowing recent `*-uia-lyrics` samples while playing, matching the copy's visible-lyric-row fallback behavior.
- Do not refresh the NetEase reader when timed lyrics are absent; this preserves the copy's low-cost steady state and avoids reading the player just because a track exists.

## Superseded Forced-Anchor Decisions

These were the earlier attempted design. They are retained as failure history, not as the active contract.

- Use a lazy global mouse listener based on `WH_MOUSE_LL` with a message loop, falling back to the existing sampler if hook setup fails.
- Let source-app foreground grace accept global click coordinates for lyric hit testing.
- Keep lyric-click behavior as progress-image recognition only; no media-session seek and no lyric-geometry position inference.
- Add a conservative player-content click fallback for NetEase-style readers: if exact visible lyric row hit-testing misses, a click inside the central player content area may still force a progress-image reread. Bottom controls and window side chrome remain excluded.
- Schedule a short post-click progress burst after a lyric-click anchor so the player-side jump has several chances to settle without relying on generic polling.
- Expose `lyric_click_debug` in diagnostics so real failures can distinguish hook/sampler, click age/position, hit mode, and pending retry state.
- Classify progress-bar interaction before lyric-click interaction. A point that maps to the bottom progress bar becomes local seek + progress anchor immediately and never enters lyric hit-testing.
- Add `read_progress_immediate` as a progress-only reader path. Provider progress anchors use it when available, so drag, click retry, settings sync, lyrics-loaded sync, and progress polling avoid the generic lyric/text UIA path.
- Add a 0.65s lyric-click progress compensation for the observed player/UI capture lag.
- Add cached-refresh bypass for progress polling due times.
- Do not clear already loaded lyrics in cached foreground-title correction; confirmed media/session refresh remains responsible for true track changes.

## Verification Anchors

- `test_recent_lyric_click_uses_lightweight_reader_without_hit_test_or_immediate_sync`
- `test_read_without_timed_lyrics_does_not_refresh_uia_progress`
- `test_foreground_title_change_does_not_clear_loaded_lyrics_from_cache`

## Superseded Regression Anchors

The old forced-anchor regression tests were deleted after the cleanup contract landed. They are replaced by reverse-contract tests that assert the heavy provider and reader interfaces do not exist.
