# Narrow v4: shared sampling, countdown circles and centered lyrics

The narrow view follows the existing capture owner, lyric synchronization and fenced seek path. `local/main.py --narrow-live` remains attach-only and uses a temporary settings/cache profile. It discovers the already running NetEase debug endpoint; this work does not authorize a player restart. The public version stays 6.417 and no executable or release package is produced.

## Implemented behavior

Capture data now carries a capture epoch and first-sample marker through the existing provider/model boundary. The view settles that first sample directly on the owner's current lyric row. A track identity change clears the previous lyric/hit/render caches before publishing the new view in one commit. Neither operation creates a second position, lyric or seek clock. A first sample beyond 30 seconds and row 20 is recorded as `first_sample_settle_outlier` rather than silently treated as evidence that the source position was correct.

The narrow view coalesces actual view changes and counts discarded no-op candidates. Chrome changes reuse prepared lyric pixels and blur textures. Pointer reveal uses the specified 8-pixel entry and 24-pixel exit margins, with a 200 ms exit debounce; keyboard focus reveals controls immediately. The existing visibility driver samples one progress value for opacity, dim and blur. Its cubic ease-out takes 0.320 seconds, with no reveal translation or scale, a 0.18 dim cap and a 6-pixel blur cap. Transport and utility buttons call the same liquid-glass painter and fill-color function used by the island/wide surface.

Surface typography and intro countdown rendering live in `local/music_lyric_presentation.py`. Both widths call `surface_font`, `surface_pixel_size`, `intro_dot_radius` and `paint_intro_countdown`. Countdown rows paint three circles instead of shaping middle-dot glyphs. The active slot is `round(height * 0.5)`: 320 at 640 pixels tall and 450 at 900. Text uses cap-height alignment and intro circles occupy that same slot. Neighbor alpha/blur changes do not move their geometry or reflow lyrics.

`diagnostic_snapshot()` reports sample candidates, commits, no-op candidates, settle/swap commits, interpolation frames, visible windows, hidden-owner paints, controls retargets and lyric-layer builds. `noop_paints/commits` is the plan's requested ratio; since commits exclude discarded candidates, that ratio can exceed one. `snap_frames` counts visible interpolation during a settle and must remain zero. A model/counter check is not by itself proof that every physical frame contained no stale text.

## Verification and evidence boundaries

The authoritative acceptance targets are the supplied `narrow_v4_claude_plan.md`, including its P3.5 addendum and live auto-next check. Generated evidence, screenshots, frame CSVs and logs stay outside source control under `/workspace/gluspot/handoff/narrow-v4/`. `acceptance-table.md` and `acceptance-table.json` list every target and preserve separate Box fixture and native PC scopes.

The supplied pre-change PC trace reported approximately 20.66 paints/second, one first-CEF hard jump, five track-associated position jumps and 18 controls retargets in about 200 seconds. Its old 640-pixel anchor was 211.2, which is 108.8 pixels above half height. No stale-frame count or screenshot ink-center measurement was supplied. `baseline-supplied.json` keeps these distinctions explicit; it is not a newly collected baseline.

The Box fixture table currently has **20 passing, one failing and ten unverified rows**. Actual Latin/CJK ink bounds at both 640/900 have 0-pixel center offset and 0-pixel jitter across 30 repeated frames. First-sample settling produces zero intermediate interpolation frames; a fixture track swap produces one swap commit and zero stale queried frames. Sixty countdown frames match the legacy wide painter with zero changed pixels. Reveal duration is 0.320 seconds in both directions; sampled progress deviation is zero frames, translation/scale/reflow/overshoot are zero, dim/blur peak at 0.18/6.0 and the shared button fill differs by zero channels. Five warmed reveal composition frames allocate no `QImage` in the narrow/veil paths and each visibility advance samples progress once; a Python-wide no-allocation claim remains unverified.

**The 8–10-pixel intro diameter gate is not met.** The measured circles are 11/10/11 pixels at an alpha threshold of 127. The shared radius matches the wide adapter exactly and the legacy wide pixel comparison passes. The requested narrow diameter and preserved same-token wide behavior therefore need a human decision before this gate can be closed. The evidence is retained as a failure; it is not rounded down or reported as ready.

Native PC 200-second progress jumps, 60-second frame differences, live no-op/paint rates, CPU, real word-click latency, auto-next, audio continuity and physical flicker remain unverified until the executor runs the supplied scripts. No PC executor is available to this Box session. The known `check_live` residual flake remains out of scope unless it blocks these measurements. Full-discovery failures and the prior Qt crash recorded in `docs/narrow-shared-runtime.md` are not converted into a clean full-suite claim.

Focused offscreen verification passes 261 capture tests, 89 shared presentation/glass tests, 20 narrow-v4/bootstrap tests, 26 existing live-fixture tests, five input tests and 25 layout checks. Veil checks pass at DPR 1 and 2. An independent wide-intro comparison also passes all 128 before/after cases with zero byte differences. The full discovery command was attempted once and ended with exit 139/SIGSEGV during `ArtworkPlayerUrlUpgradeTests.test_normalized_original_cache_wins_supplied_preview`, after an album-flow failure; it produced no complete summary. No clean full pass is claimed. The short paused-fade CPU samples in the veil reports are not the required 60-second idle/active live CPU measurement.

Reproduce the fixture evidence with:

```bash
QT_QPA_PLATFORM=offscreen /workspace/gluspot/qtvenv/bin/python /workspace/gluspot/handoff/narrow-v4/measure_box.py --repo /workspace/gluspot/grok-integrate4 --out /workspace/gluspot/handoff/narrow-v4/box --baseline /workspace/gluspot/handoff/narrow-v4/baseline-wide-intro
/workspace/gluspot/qtvenv/bin/python /workspace/gluspot/handoff/narrow-v4/render_acceptance.py --box /workspace/gluspot/handoff/narrow-v4/box/box-measurements.json --out /workspace/gluspot/handoff/narrow-v4
```

The measurement command deliberately exits with status 1 while the diameter gate fails. The acceptance table distinguishes that measured failure from the missing native/live evidence.

## PC acceptance

Use only `E:\gpt-island\.venv\Scripts\python.exe` and the synchronized `E:\grok-gluspot\_wt_capture2` mirror. Copy the handoff scripts to an executor-owned directory and run:

```powershell
.\run_pc.ps1 -AutoNext -SeekTrials 10
```

The script refuses an absent port 13371, an unexpected mirror/branch/Python or another visible GluSpot Qt view. It never terminates or relaunches NetEase. It observes 200 seconds before any seek, measures 60 seconds each of idle/revealed chrome, clicks genuine visible words and then seeks once through the existing fenced view/provider path to about five seconds before the end. A separate process captures native screenshots and accounts only the GluSpot process's CPU. The auto-next report distinguishes track/lyric/transport state from audible continuity and actual stale-pixel review.

After writing `MEASUREMENT-DONE.txt`, the probe intentionally leaves the new narrow-live window running for wuju. The complete artifact list and exact evidence limits are in the handoff's `EXECUTOR.md`. To open a plain new view after sync, with port 13371 already up and the old GluSpot view closed:

```powershell
Set-Location E:\grok-gluspot\_wt_capture2
& E:\gpt-island\.venv\Scripts\python.exe -X utf8 local\main.py --narrow-live
```
