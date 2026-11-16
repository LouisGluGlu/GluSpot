# Narrow-live flicker correction — 2026-10-29

The safe `local/main.py --narrow-live` entry now attaches an existing NetEase
debug endpoint through `WindowsMediaProvider` and `CaptureRuntime`. It does not
stop, relaunch or restore NetEase. No version, package, installation file or
real user setting changes are part of this source-only correction.

## Root causes and correction

The earlier safe entry disabled capture position and CEF entirely. It therefore
ignored the running player's debug port and retained the legacy SMTC clock.
When NetEase supplies an all-zero timeline, that path cannot establish real
progress. Returning legacy provider data after capture became invalid could
also revive an invented position. Attach-only discovery now reuses the existing
validated endpoint and the main application's CEF position/word-seek path.
Missing, unhealthy, stale and failed endpoints leave the player untouched.

When neither CEF nor a usable fallback observation exists, matching capture
data uses `capture-unavailable` and freezes at the last displayed capture
position, or zero before the first observation. Transport remains independent:
the music can still be playing. Owner and surface clocks stop lyric advancement,
and narrow displays “播放器进度不可用，歌词跟随已暂停”.

Flicker also had a separate repaint mechanism. Production material completion
and layout callbacks requested partial repaints between narrow's study ticks.
The narrow compositor advanced its whole background during those clipped paints,
leaving patches from different frames. A Box callback trace observed 102 tick,
55 material-completion and 44 content-edge update requests in one short sample.
The live window now coalesces those requests into its existing 33 ms full-frame
cadence. Pointer reveal/hide behavior and word-click routing are retained.

Each launch writes `logs/NarrowLive-*.log`, retaining ten launch files. Logs
capture output, exceptions, Qt warnings and bounded per-second timing/visibility
samples. `--trace PATH` additionally saves JSON on exit; `--seconds 30` permits
a bounded diagnostic run.

## Verification and limits

- Box capture discovery: **258 tests ×3**, passed in **12.379 / 12.041 /
  12.132 s**. Attach-only discovery/provider and CEF focus separately passed
  **38 tests / 0.130 s**.
  Regressions cover missing/unhealthy/stale endpoints, CDP startup/late failure,
  disabling during attach, repair without relaunch, repeated zero observations,
  and freezing the last capture position after a disconnect.
- Narrow live fixtures: **23 tests ×3**, passed in **3.437 / 3.370 / 3.403 s**.
  Input tests **5 ×3** and layout assertions **25 ×3** also passed.
  New checks exercise real
  paint events under 100 partial-update requests, frozen lyric following while
  transport remains playing, and log retention plus exception capture.
- Same Box fixture trace: before **281 paints / 5.121 s = 54.88 Hz**; after
  **164 / 5.416 s = 30.28 Hz**. Both had zero control-visibility retargets and
  unchanged show/hide counts. These are Linux Qt fixture results, not a live
  NetEase trace or proof of physical desktop smoothness.
- Ordinary full discovery did not complete. The first run hung in Qt garbage
  collection during library construction and required aborting that owned test
  process (`full.log`). A final-source retry terminated with SIGSEGV while
  downloading artwork (`full-final.log`); unchanged `a45b097` also terminated
  with SIGSEGV during artwork inspection (`full-baseline.log`). This reproduces
  baseline native instability but provides no complete test count or failure
  identity comparison. Full-suite stability remains unresolved.

Box logs and trace JSON are retained under
`/workspace/gluspot/handoff/flicker/box-evidence/`; generated evidence is outside
source control. Source compilation and whitespace checks passed.

Fresh PC focused tests ×3, baseline/live frame traces, monotonic real lyric
indices over 30 seconds, physical flicker and actual word-click seek remain
unverified: this session exposes no PC execution or transfer connector. Guarded
scripts are prepared outside source control at
`/workspace/gluspot/handoff/flicker/`; they use the designated Python and worktree,
refuse unexpected source state and leave NetEase running. No PC result or final
player-state observation is claimed from Box.

From `E:\grok-gluspot\_wt_capture2` after syncing the final commit:

```powershell
E:\gpt-island\.venv\Scripts\python.exe -X utf8 local/main.py --narrow-live
```

The source work is reviewable with the verification limits above; native/live
acceptance remains open. Generated evidence is kept outside source control.
