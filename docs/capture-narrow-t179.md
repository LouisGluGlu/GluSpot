# t179: lyric clearance, cover/lyric recovery, palette and music chrome

Source follow-through from `1246878` on `grok/capture-narrow-v4`, November 3,
2026. Product implementation: sol. No version change or executable build.

## t184 follow-up (items 2 and 6)

One follow-up from `b0301cc` on `grok/capture-narrow-v4`. Earlier work is cited:
`e0ee9ca` implements items 3/4/5, `1faf68a` is the rejected hard reservation,
and `b0301cc` implements the accepted soft-fade replacement for item 1. Root
wrote all product and test changes in this follow-up; parallel work was review,
baseline verification and external offscreen evidence only. Model: sol.

### Item 2: recovered cover, quiet lyric retry

The shared Windows provider (also used by CEF capture) detects a same-song edge
from missing/preview artwork to changed original bytes while lyrics have
resolved absent. It reuses `LocalLyricsStore.resolve_in_background` with an
explicit, keyword-only `retry_missed` option. Under the existing cache lock this
reopens only that key's miss; resolved/manual results, pending-job guards,
recording identity and cache-generation fences remain authoritative. Unchanged
bytes, a source-only relabel, an already original cover, a new song or existing
lyrics cannot reopen that miss through this edge.

Owned playback uses the same cover edge to request its existing exact-ID lyric
task. One song/epoch intent survives a full bounded foreground queue and is
submitted on a subsequent foreground/prefetch completion. Pending parsing or
enrichment coalesces with it; a useful result, navigation or close discards the
intent. Failed retry results leave the resolved absence visible.

The existing temporary cover override now survives a same-track pending search.
The loader recognizes that override before cover travel settles, so retrying
does not paint a skeleton. Found lyrics use the existing content snapshots and
animation back to the saved lyric layout; another miss keeps the cover. A new
pending track still resets to its ordinary saved layout/loading behavior. No
automatic path calls the settings setter.

### Item 6: wide glass, title, lyric ink and tooltip

- **6a:** The responsive release window used to invalidate its composed chrome
  after publishing a changed backdrop only when narrow. The wide path could
  therefore paint gray pixels from its previous background. Both widths now
  invalidate that stale snapshot after publication; wide chrome also joins the
  existing ten-second drift comparison. The narrow veil, 100 ms material
  schedule and frozen resize endpoints keep their existing boundaries. All
  ordinary glass faces keep sampling the current cover-derived backdrop.
- **6b:** Resting overflowing title/artist text is left anchored and right
  elided. Its full text and original width remain the input to the existing
  15-second/hover marquee. Elision text is bounded in cache, and the existing
  idle prewarmer prepares its exact-size raster alongside the full text. Moving
  marquee frames can naturally crop at both edges while travelling; there is
  no centered, permanently clipped resting title.
- **6c:** The shared surface lyric selector converts its established contrast
  tone to neutral ink with the same luminance and alpha. Wide, narrow, cover,
  translation, parenthetical and countdown consumers share that decision;
  existing island reading already uses neutral ink. Genuine unit timestamps,
  lift/blur/reading plans and word-click targets are preserved. DJ/FM accents
  continue using the separate cover accent decision.
- **6d:** A music-page-scoped Qt tooltip stylesheet gives existing chrome and
  child-panel help text a dark face (`#24262b`), pale text and a quiet rim. This
  also styles the existing seek-failure tooltip. Login styling is untouched.

### Follow-up evidence and limits

All files below are outside Git in `/workspace/gluspot/handoff/t179/`.

| Evidence | Result |
| --- | --- |
| `t184-focused.log/json` | 359 tests passed, no failures/errors/skips |
| `t184-targeted.log/json` | 29 changed metadata/contrast/regression checks passed |
| `t184-dpr2-focused.log/json` | 128 tests passed, no failures/errors/skips |
| `t184-accents.log/json` | 58 DJ/FM/voice accent checks passed |
| `t184-dpr2-baseline-limits.log/json` | Two unchanged screen-size-dependent layout failures reproduced on clean `b0301cc` at DPR 2; excluded from the green DPR 2 scope |
| `t184-full-baseline.log/json` | Clean `b0301cc`: 5,970 tests, 55 failures, 48 errors, 32 skips |
| `t184-full-final.log/json`, `t184-full-comparison.json` | Final: 5,982 tests, 54 failures, 48 errors, 32 skips; no new failure/error identities |
| `t184-6a-wide-red.png`, `t184-6b-title-start.png` | Current responsive wide render; six red glass roles and a title beginning `Human Sacrifice` with a trailing ellipsis |
| `t184-6c-neutral-lyrics.png`, `t184-6c-neutral-column.png` | Partial reading has zero RGB-channel differences; 28 source-time word targets retained |
| `t184-6d-tooltip.png` | Actual Qt tooltip has dominant opaque RGB `(36, 38, 43)` |
| `t184-6-render-proof.json`, `t184_render_proof.py` | Material/ink/title/tooltip measurements, source hashes and reproducible fixture |
| `t184-item2-{wide,narrow}-sequence.png`, `t184-item2-proof.json`, `t184_item2_proof.py` | Pending over three seconds: zero skeleton paints, zero settings callbacks; failed retry retains cover; found lyrics have real transition snapshots and a 50% animation midpoint |

The latest **parent-supplied PC login proof**, cited without repeating the
operation, is `E:\grok-gluspot\_logs\release-t179-item1\login-pc-verify.json`
and sibling `login-qr-*.png`: with the runtime present at the live tip,
NetEase/Kugou/QQ each completed QR begin, three `waiting` polls and cancel.
This supersedes the older t178 single-attempt QQ network limit documented below.
No login was completed; this follow-up did not launch a PC or access credentials.

Screenshots use controlled provider data, a synthetic red cover and private
temporary settings/cache roots. They establish renderer behavior, not physical
Windows appearance, live CDN reliability, actual network lyric recovery or
display smoothness. The complete fresh discovery comparison against `b0301cc`
and its inherited failures is recorded in `t184-full-comparison.json` and the
final handoff. The baseline-only priority-glow timing failure did not recur;
this run difference is not claimed as a product fix. A green whole-branch
suite is not claimed. Item 1's parent PC
visual acceptance and live items 2/3/4/6 acceptance remain open. No version,
build, push, player restart or login redesign is part of this follow-up.

## Lyric bottom clearance (item 1)

The redo is one follow-up commit on `1faf68a`. The user rejected that
revision's hard `195 * scale` bottom reservation: it moved the active lyric
upward and left a large empty band. Wide columns now restore `e0ee9ca`'s full
content height with only ordinary `60 * scale` margins. Follow, wrapping and
hit geometry use that height; no control reserve shortens the column.

The existing reusable, pixel-aligned ink-only QImage still applies
DestinationIn alpha. Its fade starts one lyric row (font height plus sentence
gap) above the earliest bottom control top and reaches zero there. Transport
top resolves from the current surface and shared panel constants, avoiding the
previous-size panel rectangle that is painted later. Collection uses its
unshifted geometry. Available DJ uses its measured side and maximum expanded
icon envelope, independent of its animated lane width, enabled/activity state
or hide/reveal. Only ink changes; layout and the active anchor remain stable.
Resize capture reuses the source fade band, including the narrow-first to wide
wrap-reuse handoff. Narrow keeps the existing `156 * scale` reserve and veil.

All values below come from the same shared-renderer fixture with active row 5.
The two baseline panels render actual Git archives of `e0ee9ca` and `1faf68a`.

| Size / revision | Column bottom | Active row top | Ink bottom | Ink pixels below earliest control top |
| --- | ---: | ---: | ---: | ---: |
| 960 x 540 / `e0ee9ca` | 491.351 | 219.604 | 490 | 4,516 |
| 960 x 540 / `1faf68a` | 381.892 | 172.536 | 381 | 0 |
| 960 x 540 / redo | 491.351 | 219.604 | 476 | 0 |
| 640 x 360 / `e0ee9ca` | 327.568 | 147.923 | 327 | 3,165 |
| 640 x 360 / `1faf68a` | 254.595 | 116.544 | 253 | 0 |
| 640 x 360 / redo | 327.568 | 147.923 | 312 | 0 |

Without DJ, fade bands are y=416.654–476.757 and y=273.478–314.000,
respectively. Ink remains immediately above the controls. Expanded-DJ
comparisons also preserve the original column/anchor and have zero ink pixels
below their earliest control tops (381.730 and 218.216).

Evidence outside Git in `/workspace/gluspot/handoff/t179/`:

- `item1-redo-960x540-triple.png`, `item1-redo-640x360-triple.png`:
  BEFORE / rejected hard reserve / NEW, each panel at its stated size.
- `item1-redo-{960x540,640x360}-dj-triple.png`: expanded-DJ variants.
- `item1-redo-geometry.json`: column and ink bottoms, all control tops,
  fade bands, active-row y/center, below-control and rectangle-overlap counts.
- `item1_redo_proof.py` and `item1_redo_assemble.py`: isolated shared-renderer
  fixture and triple/JSON assembly; source roots recorded separately.
- `ITEM1_INTERIM.md`: exact redo SHA, validation and parent PC handoff.

The six existing item-1 tests now cover the full-height anchor, soft pixels
near control tops without a large vacant band, unchanged readable ink in tall
wrapped rows, transformed physical clip edges, stable DJ/control reveals and
the preserved stacked reservation. The continuity regression checks faded ink
at the full-height endpoint while retaining horizontal blur/inset coverage.

Final focused verification: **131 passed at DPR 1** (layout, continuity,
companions, visual layers, width transition/breakpoint, chrome and resize),
and **27 passed at DPR 2** (all six item-1 policies plus the resize suite).
Reports are `item1-redo-final-focus.{json,log}` and
`item1-redo-review-final-dpr2.{json,log}`. Python compilation and
`git diff --check` pass. Full discovery was not rerun, following the requested
focused scope; the earlier branch-wide inherited failures are not claimed
fixed. Process audit: 100/100 after interface lookup, independent review and
focused verification.

Parent PC interim acceptance remains pending. No PC launch, player restart,
completed login, version/build or push occurred. This session stops after
item 1; other numbered items were not resumed.

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

Item 1 is handled by the separately scoped redo above. Item 2 and the later
item 6 request are handled by the t184 follow-up at the top of this document.
