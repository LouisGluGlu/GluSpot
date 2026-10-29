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
