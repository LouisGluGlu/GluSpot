# Narrow flicker v2 and live focus

This source change implements sections 1 and 2 of the supplied `narrow_flicker_v2_plan.md`, within the explicit narrow-window scope. Layout/popup work, new progressive idle blur, release packaging and provider/owner timing changes are excluded. Transport button painting and geometry are untouched. Existing checks, tests, thresholds and capture scripts are unchanged; new regression modules exercise the changed behavior.

## Static stanza composition

Veil preparation receives a frozen transparent neighbor image with `include_current=False`; requesting live ink as a veil source asserts. Preparation and presentation share the same cached image, while an independent cache slot retains active-ink content. A canonical stanza includes enough overscan for its initial scroll displacement. Its revealed endpoint is composed once; subsequent scroll frames transform the same surface without rasterizing neighbors or filtering again. Active ink and intro dots paint afterward. Dim and viewport fading stay in window coordinates. Paused fade endpoints use that same compositor and are prepared only after scrolling settles.

The present key uses rounded scroll and the existing 50 ms ink bucket instead of the raw playback clock. Unchanged keys skip presents. Diagnostics expose the selected paint source and veil cache-hit ratio. Intro dots use one opaque white raster per font/DPR. The bottom band's visible clip begins at `layout.viewport.bottom() + 6`, below all clipped lyric rows; transport controls retain their existing paint paths.

## Verification of section 1

Linux Qt uses `env QT_QPA_PLATFORM=offscreen /workspace/gluspot/qtvenv/bin/python`. New `tests.test_narrow_flicker_v2` passes five render invariants, including constant middle-ink alpha across reveal. The unchanged `check_live.py` passes all 27 checks (two fade invalidation checks failed on baseline); `check_veil.py --frames 180` passes at DPR 1 and 2. The preexisting intro-center assertion in `tests.test_narrow_v4` still fails: it checks the shared painter's global center while dots now rasterize into a local cached tile. No dot placement/layout change is included here.

Before changes, full discovery on an isolated archive of `e152b5e` completed 5,751 tests in 160.827 seconds: 20 failures, 11 errors and 31 skips. This establishes a local baseline, not a clean suite.

The same external `/tmp/gluspot-narrow-measure.py` captured 480 deterministic Qt fixture frames at 22 ms, spanning 12 line changes, before and after section 1. It keeps a fixed background and omits control rasters to isolate lyric composition. These explicitly chosen ROIs are not established as the historical GIF's ROI coordinates. All samples, including intentional scroll/line changes, remain in the measurements. The mean-centered RGB adjacent-frame MAD standard deviation / maximum is:

| ROI | Baseline | Section 1 |
| --- | ---: | ---: |
| lyric_left | 4.3451 / 19.6803 | 4.4458 / 13.6080 |
| lyric_mid | 3.1876 / 15.1374 | 3.2596 / 10.0197 |
| active_band | 5.6922 / 28.1692 | 5.4441 / 24.9387 |
| chrome_top | 0.8905 / 3.8018 | 0.8612 / 3.4502 |
| bottom_lyric | 0.4419 / 1.6567 | 0.8570 / 2.4945 |

Lyric layer builds decrease 128 → 15; skipped duplicate ticks increase 82 → 231. Sixty fixed-pose frames produce one hash cluster and SSIM 1.0 on both versions. That fixed-pose check does not establish absence of moving ghosts. The fixture MAD gate remains unmet, including worse bottom-row variation. No physical PC executor is exposed, so the prescribed native capture and live lag acceptance cannot be claimed.

Generated logs/captures remain outside source control in `/tmp/gluspot-narrow-baseline-e152b5e/`, `/tmp/gluspot-narrow-commit1-roi/` and `/tmp/gluspot-narrow-commit1-veil/`. The external measurement script's SHA-256 is `426c276b2c50569838d7116b065a4c6bf9fb92a70fff19ad368c38317100a0d1`. No build, version change or push.

## Immediate live focus

The live `_lyric_frame` no longer interpolates from `owner.prev_lyric_index` over 360 ms. Highlight binds immediately to the selected index. A stored scroll pose uses exponential easing with 120 ms tau only for fractional movement; a displacement of at least one row snaps. Track/first-sample changes reset that pose before rendering. Multiple frame requests at one timestamp are idempotent, and the small scroll tail settles to enable duplicate-frame skipping.

For position sources containing `cef` or `playprogress`, focus comes from the existing `active_index_for_position(timed, playback_position(), lead_seconds=.28)`. Real capture publishes `capture-model`, so the view reads the existing provider's cached `capture.position_route` once per tick to recognize its CEF route. This is diagnostic state lookup, not player polling or another playback clock. Other sources retain the owner's index, including UIA and unavailable capture. Word timing and seek position continue to read the unmodified shared position. No lead settings, rolling calibration or new source observer is introduced: the current adapter has no independent NetEase highlighted-index observation to calibrate against.

The diagnostic snapshot adds selected/owner indices, scroll index, source/route, position, effective lead and highlighted-line start. Its `lag_ms` is line-start minus position, a phase observation; it does not measure synchronization against an independent NetEase UI timestamp.

Fifteen new `tests.test_narrow_live_lag` cases pass. Together with timeline and capture integration, 28 focused checks pass. The combined narrow/flicker/shared-runtime command runs 45 tests with 43 passes and two failures: the baseline intro-center assertion described above and `test_same_line_highlight_after_settle_still_follows_owner_sample`, whose strict `30 < scroll < 31` expectation is superseded by the explicitly requested `>=1` snap (`31.0`). Both existing test files remain unchanged. All 27 unchanged live checks, five input checks and 25 layout checks pass. An AST comparison confirms `paint_controls`, `paint_header` and `refresh_controls` match baseline exactly.

Both final full-discovery attempts ended with exit 139 in the already documented Qt SIGSEGV at `ArtworkPlayerUrlUpgradeTests.test_normalized_original_cache_wins_supplied_preview`, before producing a suite summary. The second used `PYTHONFAULTHANDLER=1`; its stack stops in `api/providers.py:_album_art_min_side` while validating image bytes, outside the changed narrow code. Logs are `/tmp/gluspot-narrow-v2-final-full.log` and `/tmp/gluspot-narrow-v2-final-full-faulthandler.log`. No causal diagnosis or clean full-suite result is claimed.

The unchanged 480-frame/22 ms fixture harness after both sections reports:

| ROI | Final MAD std / max |
| --- | ---: |
| lyric_left | 1.4247 / 9.3744 |
| lyric_mid | 1.1296 / 7.5228 |
| active_band | 3.9397 / 26.2880 |
| chrome_top | 0.0187 / 0.1761 |
| bottom_lyric | 0.0173 / 0.1436 |

The full run including its static phase has 580 samples, 225 commits, 355 no-ops, 15 lyric builds and 0.9336 veil cache-hit ratio. Fixed-pose frames again have one hash cluster and SSIM 1.0 in every ROI. The active-band std and maximum-spike gates remain unmet; this fixture does not establish physical flicker, moving-ghost acceptance or the prescribed native ≥40-flip/three-minute ≤150 ms timing gate. Final artifacts are in `/tmp/gluspot-narrow-v2-final-roi/`.

Final unchanged `check_veil.py --frames 180` passes at both DPRs. Lyrics paint P95 is 1.817 / 4.992 ms and paused-fade CPU is 5.82% / 11.05%, within the existing gates. This carries a paint-cost regression from baseline P95 0.532 / 1.095 ms; scroll+fade P95 is 3.673 / 6.281 ms. Reports are in `/tmp/gluspot-narrow-v2-final-veil/`. Compilation and whitespace checks pass.
