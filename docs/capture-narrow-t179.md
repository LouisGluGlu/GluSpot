# t179: login availability, hidden palette and artwork recovery

Source follow-through from `1246878` on `grok/capture-narrow-v4`, November 3,
2026. Product implementation: sol. No version change or executable build.

## Platform login (item 5)

The parent already established the PC cause at `1246878`: the worktree lacked
`build/online-music`, so `NodeOnlineMusicBackend.available()` returned false
with `在线音乐适配依赖未安装，请运行 prepare-runtime.ps1`. The restyle did not
disconnect QR actions. Supported-but-unavailable tiles painted their face and
icon at opacity 1.0 while dimming only their caption.

Unavailable faces/icons now paint at 0.45 opacity. Disabled controls cannot
paint checked/focus rings or active/hover/pressed material states. The existing
status caption, full backend tooltip and accessible description remain.

Runtime preparation prefers the full Node license beside the selected binary.
If the official download fails, it retains a separate explicit notice and
verifies the installed adapters with a scoped verifier flag. The receipt marks
the Node license `unavailable`; strict standalone verification and Windows
packaging continue to require the full license. Partial downloads are removed.
The ordinary visible source page already requests offline `prepare_login`
through `prepare_login_runtime`; this change retains that path and the
existing request deadlines. No platform-specific QQ workaround was added.

Parent-supplied PC evidence after runtime preparation, **not re-run here**:

- `E:\grok-gluspot\_logs\release-t178\login-qr-probe.json`: NetEase QR PNG
  1,981 bytes, three `waiting` polls and cancellation succeeded; Kugou QR PNG
  2,387 bytes, `waiting` and cancellation succeeded. QQ was available but its
  single begin attempt failed with a network error.
- NetEase image: `E:\grok-gluspot\_logs\release-t178\login-qr-netease.png`.
- This source run performed no PC launch, player restart, scan, completed
  platform login or credential save. Offscreen pixels do not establish live QR
  operation; the above evidence belongs to the parent's prior PC probe.

## Hidden-control palette (item 3 / t180)

The presentation clock now requests the existing cached/single-worker artwork
palette before the commit no-op check, even with controls fully hidden. Commit,
veil and control identities include the resolved palette and provider colors.
An artwork/palette change releases the old composed chrome backdrop and scene
ink key without rasterizing hidden content. The veil samples the raw scene
after the shared background painter publishes it, avoiding a prior-art raster
prepared before painting. A changed palette basis also refreshes the next
control capture.

Every ten seconds the clock compares current palette/control/veil identities,
including the actual background raster. Drift invalidates the normal keys and
authorizes a refresh. Healthy comparisons do no raster work. Existing resize
freezes and the 100 ms limit for ordinary moving veil sources remain in force;
no new timer or per-frame forced preparation was introduced.

## Owned artwork (item 4 / t181)

PC artwork logs supplied in this task are insufficient to identify a particular
CDN response or system thumbnail failure. Code inspection and controlled tests
established a narrower bug: an initial transient owned-cover failure immediately
published `failed` while its retry timer or worker still had a valid retry.

Scheduled/inflight retries now remain `pending`. Network, timeout and incomplete
transfers get one additional retry: at most four jobs with 0.75, 1.5 and 2.25 s
backoffs. Busy workers, corrupt-image responses and tiny-image upgrades retain
their existing three-job cap. A shared count prevents category changes from
resetting the budget. Terminal/exhausted failure ends the loading state while
retaining any usable preview.

The existing NetEase upgrade-to-exact-original fallback was already present;
new timeout/incomplete regressions verify it. HTTP size/decode/request budgets,
two workers, queue/request fences and sharper-preview retention are preserved.
System URL artwork and GSMTC thumbnails have separate provider-owned paths;
their cooldowns, complete reads and guards were inspected and left unchanged.
PrintWindow/layered-window capture and CEF seek are outside this change.

## Verification

All runs use `/workspace/gluspot/qtvenv/bin/python`, offscreen Qt and disposable
settings/cache roots through the existing private test wrapper. Logs/JSON are
outside Git under `/workspace/gluspot/handoff/t179/`.

| Final focused run | Result |
| --- | --- |
| Login UI/controller/service/bridge/runtime | 244 run, 241 passed, 3 expected skips; 11.828 s |
| Owned/queue artwork, fetch/fallback, priorities, system thumbnail guards | 188 passed; 4.077 s |
| Narrow palette/veil/chrome/flicker/scroll and resize/freeze | 94 passed; 13.060 s |
| DPR 2 tile, hidden palette/veil and resize/freeze | 97 passed; 12.089 s |

Ordinary full discovery completed on both trees with the same private wrapper:
final **5,964 tests / 190.777 s**, clean `1246878` archive **5,950 / 186.802 s**.
Both report **54 failures, 48 errors and 31 skips**, with identical failure/error
identities and ordering (zero added or removed). All fourteen new cases passed.
`full-final.log/json`, `full-baseline.log/json` and `full-comparison.json` retain
the complete results outside Git. Inherited failures include missing font assets,
old island/compact/transition fixtures and the non-widget narrow-lag harness;
this is not a green whole-branch suite. Python compilation, Node syntax and
`git diff --check` pass. The scoped focused suites are green.

The runtime script test uses temporary PowerShell 7.4.13, actual Node and the
production verifier with offline adapter fixtures. A forced license download
failure successfully reaches verification; adding an adjacent full license
switches the receipt back to complete and removes the fallback notice. Invalid
adapter notices and truncated full Node licenses remain rejected. No real npm
installation or account operation was performed by these tests.

New hidden tests cover art/cacheKey changes, same-image provider color changes,
ten-second drift recovery and no reveal. Across 660 steady hidden helper samples
over 22 seconds, preparations and control bands remain unchanged. A separate
61-frame tick/paint sample advances the raw scene over two seconds and asserts
at most 21 veil preparations, preserving the existing 10 Hz budget. These are
controlled offscreen checks, not physical-display or long-session PC acceptance.

Items 1–2 remain deferred: neither repository notes nor the prior t179 plan
specifies their behavior. No additional product scope was inferred.
