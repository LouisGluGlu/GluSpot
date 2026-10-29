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
