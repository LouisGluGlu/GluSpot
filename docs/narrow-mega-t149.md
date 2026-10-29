# Narrow mega t149

Source-only work on `grok/capture-narrow-v4`, starting at `e429489` with preserved slide/veil/singer WIP. No release or packaging version changes.

## Slide, blur and flicker

Chrome moves off the top/right and bottom edges; opacity stays full above reveal 0.2 and only tapers at the endpoint. Hits use the painted pose and remain disabled below reveal 0.75. Narrow background edge treatments are prepared from raw background pixels as two bounded frozen endpoints. Lyric and icon ink stay independent. Material blur at level 50 is 1.45, with a 224 px raster cap; the existing 100 ms throttle and cache bounds remain. Changing level clears shared material/control caches and narrow chrome bands together.

Verification: 56 focused offscreen unittests passed in 4.457 s on a separate checkout of the exact staged step, including both width suites, chrome, veil, glass levels and material caches. Ten repeated steady-state frames are pixel-identical. Logs: `/workspace/gluspot/handoff/narrow-v4/t149_step1_isolated_tests.log`. Physical screen smoothness, blur contrast and native cost remain unverified.

## Singer tags

Recovered the retained singer WIP through the existing presentation/preparation and timed-unit paths. Only closed leading role tags 男、女、合、男声、女声、合唱 are extracted; ordinary parentheses retain their previous backing-vocal policy. A small dim role pill stays outside the timed lyric run. Male/female duet grouping preserves source timestamps, raw provider text, translations and both full-tag/body-only unit conventions. Wide and narrow surfaces retain exact word hit targets.

Verification: 130 focused singer, presentation, preparation, unit-timing, visual-layer, recovery and narrow-flicker tests passed in 5.204 s on a separate checkout of the staged singer step. Log: `/workspace/gluspot/handoff/narrow-v4/t149_step2_isolated_tests.log`. The retained stash and initial dirty files remain backed up outside source control; no broad stash pop was used.

## Content-driven width breakpoint (t146)

The shared decision shapes five CJK glyphs and three Latin probe words with the real QTextLayout/CJK spacing path. It evaluates the hypothetical wide lyric font for lyrics and playlist, and derives hysteresis from half a CJK glyph on each side. The 180 px/control-row floors and independent 960/936 trigger are removed. Cover modes keep their content layout; explicit window-mode selection bypasses automatic fitting until another resize. StudyWindow drives one chrome animation while live lyrics reflow directly into their target column. Four-mode painting and input routing remain aligned.

Verification: 65 focused and adjacent tests passed in 13.256 s on a separate checkout of the exact staged step. The unchanged geometry check passes. Logs: `/workspace/gluspot/handoff/narrow-v4/t149_step4_isolated_tests.log` and `t149_width_*.log`. The unchanged legacy demo live/input checks have the same eight failures and two errors as the isolated step1 baseline; the old veil check likewise divides by zero because mounted lyrics bypass its legacy cache counters. These checks are retained unchanged, with failures recorded rather than counted as acceptance.

The plan specifies required-width ± half a glyph; its last-wide-frame five-CJK checklist is stricter than that formula. The implementation follows the explicit formula and records the discrepancy for PC review. Three Latin words use a fixed shaped probe; arbitrary long English words remain a soft guarantee under the existing wrapping policy.

## Single reading scheme and original lift (t147 + t148)

Gold is removed globally: no strategy control, setting, accessor, confirmation command, warm ring profile or alternate timing branch remains. Low-perception behavior preserves real-unit reading and falls back to existing whole-line/block treatment otherwise. Former gold users intentionally stop receiving its estimated sparse highlights. Both retired keys are filtered through the established settings migration; startup wiring, owner setters and persisted payload fields are deleted. Word-click seeking remains independent.

「读字抬升高度」= `v2_music_surface_reading_lift_level`, **quarter units of the original reading lift**, NOT window height. `_reading_lift_height()` now returns `GLYPH_FLOAT_LIFT` directly: 5.2, equivalent to level **4 ⇒ 1.0× = 原高度**, with 16 full bands and 160 samples. Historical default 2 was half-height.

Verification: 483 focused tests passed in 38.114 s, including settings control/key removal, original lift continuity, shared wide/narrow ink and exact word targets. Production offline rendering passed 664 checks and 132 player frames at each DPR 1/2. Logs/reports: `/workspace/gluspot/handoff/narrow-v4/t149_gold_lift_green.log`, `t149_render_dpr1_report.json`, `t149_render_dpr2_report.json`. An expanded 674-test run exposed an unrelated audio panel label-size failure (32 vs 30), reproduced unchanged on the step1 baseline; the lift-dependent displacement budget was then normalized to the full original displacement. No clean full discovery is claimed.

Migration limit: the established loader schedules its existing two-second autosave after migrations. Adding these keys to its filter does not guarantee that a future normal cold launch waits for a manual save. This task uses only temporary settings fixtures; real settings were not read or written for migration. PC verification likewise uses private settings, and the normal-launch timing remains an explicit plan discrepancy.
