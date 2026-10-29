# t156 narrow refinement

Branch `grok/capture-narrow-v4`, base `12c1e0fa9524da4982b5932d9d591d8f13ffb0bb`. Authority: `/workspace/gluspot/handoff/narrow-v4/narrow_t156_refine_plan.md`. No version/build, provider, clock, seek, player lifecycle or real settings change.

The backdrop uses height-normalized 14% plateaus and 20% smooth ramps, eight interpolated radius samples up to 6 px, and one full-frame premultiplied overlay over the live sharp background. There are two background image draws altogether. `TOP_END` remains only a compatibility distance for chrome travel. The clear core comes from mask alpha falling to zero. Preparation is limited to the current background epoch at 100 ms cadence and freezes throughout resize and width animation. The transparent lyric-ink veil also receives the normalized eight-level profile; its older multipass compositor remains to preserve moving ink and viewport clipping.

Resize owns `_resize_chrome_hold`: chrome reveal immediately becomes 1, idle hiding stops, and collection/DJ remain visible through the old choreography gap. A DJ entry that cannot fit the compact row temporarily uses an independent second-row circle. Release resumes the existing 8 px entry / 24 px exit logic with a fresh idle timer. Leave, deactivation and hide clear the hold. Above the maximum aspect, both native resize paths use the existing `constrained_window_size` helper to grow height. Animation callbacks only update their phase and request repaint.

`WidthTransition` has `NARROW`, `SPLITTING`, `WIDE`, `MERGING` states and an independent queued destination. The gesture freezes height, mark 10's minimum, content-derived mark 25's W_b, and mark 50's available-width/aspect limit. W_b inverts the existing shaped five-CJK / three-Latin-word wide-column fit; its value follows font, DPI and height. A W_b beyond the maximum disables splitting and publishes `split_unreachable`. Crossing 25 starts splitting; below 24 requests merging; the band between preserves intent. An active handoff completes before a queued reversal starts. Releasing at exactly 25 preserves the chosen endpoint in the separate font-fit hysteresis.

Duration is `clamp(.5 / (1 + .8 * speed / 60), .2, .5)` seconds, with speed smoothed over 120 ms and a single jump exceeding 20 marks choosing .2 seconds. Each animation keeps its duration and uses InOutCubic. Content, cover and column interpolate together, with a nonoverlap clamp. Four logical lyric widths/fonts provide shaping poses; intermediate frames transform shared cached ink and mapped hits. These synchronized poses preserve the established wide and immersive endpoint layouts.

Chrome tint samples the current raw backdrop at sixteen points per face. A bounded per-role cache includes background epoch, rect, DPR and size; geometry and epoch changes share a 100 ms budget. A reusable scratch buffer and direct pixel reads avoid image allocation in tint sampling. The existing fill-color function selects the opposite neutral face, applies the plan's alpha formula, and raises border alpha by 20%. Frozen blur and live tint use separate scene references. Playback readiness retains the existing advancing-playback gate, while lyric readiness is separate: after 45 seconds, advancing playback without lyrics attaches as degraded and reports word-click acceptance UNVERIFIED.

## Verification

Final focused runs pass **219 tests at DPR 1 (24.056 s)** and **219 at DPR 2 (43.213 s)**, without failures, errors or skips. They include new t156 tests, width/fit/layout, narrow chrome/back/runtime, glass, surface transitions/recovery and startup. Compilation of `_demos/narrow3`, `local`, `api`, `tests` and both probe directories passes. All 45 layout cases and `git diff --check` pass. Private temporary settings and caches are used throughout.

Evidence is outside source control at `/workspace/gluspot/handoff/narrow-v4/box_t156/`: reproducible `focused.py` / `run_tests.py`, `focused-dpr{1,2}.json/log`, veil scans and four PNGs, five real shared-renderer transition poses and geometry, layout and broad-test logs. On the controlled uniform-orange backdrop, weighted centerline luma maximum steps are 0.006726 / 0.006726 at 360×640 DPR 1/2 and 0.007560 / 0.005609 at 480×800, all below 3/255. This fixture isolates the veil; real artwork/lyric centerline contrast is a separate PC diagnostic.

Full discovery was attempted during implementation with isolated settings and caches. It exited **139 (SIGSEGV)** at `test_music_library_header.MusicLibraryHeaderTests.test_album_click_has_its_own_signal_and_hover_does_not_expand_header`, also recorded by the preceding t151 work. It produced no complete suite summary and is not a final-tip full-suite pass. The required island-message suite ran 502 checks with ten failures matching the previously documented font-path, removed-blur-layer and historical Gelv expectations. This task does not establish a broad-suite green result.

## Acceptance limits

| Plan gate | Box outcome | Evidence / limit |
| --- | --- | --- |
| A1 / A2 | PASS for controlled fixture/profile | Both sizes and DPRs, monotone ramps, weighted luma <3/255 |
| A3 | PASS for backdrop | One sharp draw plus one masked overlay; transparent lyric compositor retains multiple passes |
| A4 | PASS for reveal ownership and cleanup | Hold/progress, release hysteresis, lost release and collection/DJ gap tests; physical every-button appearance pending |
| A5 | FAIL for literal constant ratio | All rectangles interpolate together, but existing wide/immersive endpoint cover-to-column ratios differ; ≤2% constant ratio is incompatible with preserving those endpoints |
| A6 / A7 / A8 | PASS for state/configured timing | Real Qt finished callback chains full handoffs; 500 ms normal, 200 ms fast floor, 24–25 deadband; physical timestamps pending |
| A9 / A10 | PASS for tested invariants | Frozen W_b, font changes, release at 25, update-only callback and four shaping poses |
| A11 | FAIL for universal alpha-difference assertion | Exact formula and 100 ms epoch update pass; equal-distance bright/dark luma has equal alpha, so the specified formula cannot guarantee ≥12 difference for every bright/dark pair |
| A12 | UNVERIFIED for whole window | Warm veil paint and tint sampling allocate no QImage; existing complete paint/resize paths still allocate images |
| A13 | PASS | Degraded readiness, preserved advancing-playback gate and UNVERIFIED word-click tests |
| A14 | UNVERIFIED | PC explicitly not run |

The existing countdown diameter gate remains outside scope. Native monitor scaling, compositor motion, real artwork seams, audio continuity and long-session cost remain unverified. Process discipline score: 100/100; this does not close the acceptance gaps above or claim physical readiness.

## PC handoff

Tracked scripts are in `handoff/narrow-v4/pc_verify_t156/` with `handoff/narrow-v4/MEGA_T156_PC.md`. After the clean commit, `pack_box.py` creates an incremental Git bundle, exact-tip manifest and verified ZIP outside the checkout. Intended results go to **Desktop\_flicker_shared\mega_t156\**, with DPR 1/2 captures, readiness fields, seam scan, 4 px resize samples, queued-return and fast-drag timing, plus acceptance-table JSON/Markdown. There is no mounted PC Desktop/executor here: transfer and execution are pending. The runner preserves a visible existing GluSpot view and never restarts NetEase.
