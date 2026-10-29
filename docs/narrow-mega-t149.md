# Narrow mega t149

Source-only work on `grok/capture-narrow-v4`, starting at `e429489` with preserved slide/veil/singer WIP. No release or packaging version changes.

## Slide, blur and flicker

Chrome moves off the top/right and bottom edges; opacity stays full above reveal 0.2 and only tapers at the endpoint. Hits use the painted pose and remain disabled below reveal 0.75. Narrow background edge treatments are prepared from raw background pixels as two bounded frozen endpoints. Lyric and icon ink stay independent. Material blur at level 50 is 1.45, with a 224 px raster cap; the existing 100 ms throttle and cache bounds remain. Changing level clears shared material/control caches and narrow chrome bands together.

Verification: 56 focused offscreen unittests passed in 4.457 s on a separate checkout of the exact staged step, including both width suites, chrome, veil, glass levels and material caches. Ten repeated steady-state frames are pixel-identical. Logs: `/workspace/gluspot/handoff/narrow-v4/t149_step1_isolated_tests.log`. Physical screen smoothness, blur contrast and native cost remain unverified.
