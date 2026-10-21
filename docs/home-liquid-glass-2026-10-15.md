# Home flow and DJ prompt — October 15, 2026

GluSpot 6.412, source changes only. The user confirmed source selection, login
and the signed-in music library as the home scope, and reaffirmed Liquid Glass
as the visual authority. The supplied second sketch specifies a rounded prompt
containing the message and existing DJ icon. Version and packaged EXE remain
unchanged; the user's running application was not restarted or controlled.

## DJ prompts

Current-introduction loading, ready and failure states, and next-introduction
waiting, loading, ready and failure states now share one scene-blurred rounded
rectangle. The message is left aligned and the existing hollow-ring icon stays
inside its right edge. The prompt uses the song page's existing theme policy,
including light/dark/system/scheduled modes, and contrast-selected foreground.
It replaces the floating status text and its shadow; the compact status icon
does not receive another circle or colored outer ring while the prompt is shown.

The font is semibold at a minimum 14 logical pixels and scales to 16 pixels
at the normal scene scale. Geometry reserves the longest real status and caches
its measured width; a loading-to-ready/error update cannot shift a held target.
The shared hit shape, button bounds, effect occlusion and repaint damage include
the complete rounded prompt. Clicking its text opens the existing voice choices;
only the existing explicit icon path can toggle DJ. Pointer dwell still cannot
open the selector. The genuine speech-title capsule, existing speech ring
animation, completion clock and fade/idle durations are preserved. No audio
behavior or media transport controls are added by this change.

## Source selection and login

Source tiles keep their platform names, colors and unavailable-source reasons.
Navigation, tiles, login/account panes and actions use the existing refractive
glass helper over a frozen in-app pigment raster. It is at most 256 pixels on
its longest edge; the frozen backdrop adds no continuing refresh/capture timer
or network request. The existing brief expansion animation remains. These pages
retain their existing dark appearance context; this change does not add a new
theme setting.

Selecting a source still begins the controller's existing account/QR flow.
Clicking the already selected source keeps the current QR or account card and
does not cancel/restart it. Back first returns to source selection and cancels
the current generation through the existing controller; it can then return to
playback-mode selection where onboarding allows that route.

The login pane groups QR on the left and platform-specific guidance, current
state and one action on the right. Standard waiting/scanned copy becomes
`等待扫码` / `已扫码，请在手机确认`; its full original copy remains available as
accessible description/tooltip. Other provider messages, including errors, are
preserved. Failure, expiry and cancellation provide deliberate recovery; busy,
unavailable and unsupported states disable retry while platform switching and
Back remain available. A verified account action is labeled `进入音乐库`.

Normal login expands to 560 logical pixels high, constrained by the actual
screen. Displays below 480 pixels use a compact source row and pane composition;
QR, status, 44-pixel retry and account action remain contained at 460/360 heights.
The compact decision follows the target display rather than an intermediate
animation height, keeping the source row stationary during normal expansion.
No login protocol, timeout, credential or service scheduling change is claimed.

## Signed-in library

The current source's existing brand color supplies a quiet, frozen background
for blurred glass rows, search and existing actions. Actual supplied album and
avatar artwork keep their original slots and retrieval paths. The nested opaque
plate behind each row's mode buttons is removed. All three modes remain visible
and gain `全屏` / `窗口` / `岛` labels with at least 44-pixel targets. Cover
hover/focus identifies its existing default-play action; no new command is added.

Search names the current platform, keeps Enter/debounced submission, adds
Ctrl+F, and exposes a 44-pixel clear/return button when a query is present.
The clear button reuses the controller's Back authority: a pending collection
or stream handoff is retired before returning to the catalog. Empty query
cannot accidentally switch platforms through this button. Existing request and
account generations reject old search/artwork completions.

The list distinguishes `我的音乐` from `搜索结果`. Search errors with no rows
offer `重新搜索` through the existing search signal; catalog errors with no rows
offer `切换平台` through the existing source-switch signal. Partial results and
their errors remain visible. Long titles are elided only in their constrained
reading lane; full cover tooltips and accessible names remain available. No
recommendations, collection data or controller API were invented.

## Pinned reference evidence

UI source text was read from the following fixed commits, without installing
or executing either reference. The adaptation uses GluSpot's existing Qt widgets,
signals, providers and generation fences; no reference implementation was copied.
Only the earlier cover excerpts, not these UI sources, exist in the ignored
`.runtime/artwork-reference/` directory.

- Folia Major:
  [provider/account status and current selection](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/library/suites/grid/account/OnlineProviderSwitcher.tsx#L212-L249)
  informed explicit source identity and status.
- Folia Major:
  [bounded login pane and QR/state/actions](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/library/suites/grid/account/OnlineProviderLoginModal.tsx#L65-L186)
  informed the grouped login composition and deliberate retry.
- Folia Major:
  [documented discoverability issue and visible choices](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/library/suites/grid/home/GridViewTabs.tsx#L7-L104)
  supported retaining visible, labeled presentation choices rather than hiding
  them behind an unexplained arrow.
- Mineradio:
  [distinct QR waiting/scanned/confirmed stages](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/public/js/modules/08-account/03-login-modal-flows.js#L1574-L1601)
  informed truthful state copy, within the stages GluSpot already exposes.
- Mineradio:
  [current-provider search scope](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/public/js/modules/05-playback/07-search.js#L163-L207)
  and [stale-request/empty/error distinctions](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/public/js/modules/05-playback/07-search.js#L1264-L1309)
  informed explicit platform scope and preserving existing query fences.
- Mineradio:
  [type-specific home action dispatch](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/public/js/modules/05-playback/05-home-actions.js#L50-L64)
  supported direct reuse of the existing collection/default-play commands.

## Taste screenshot review

The explicitly invoked
`C:/Users/USER/.codex/skills/taste-skill/SKILL.md` and its critique reference
guide native screenshot hierarchy, type, state, target and collision review.
This skill normally excludes app screens and bans website glass; the user's
explicit native Liquid Glass direction takes precedence. No random website
direction, marketing sections, photograph quota, DOM/browser inspector pass
or external certification is claimed. All imagery is either the actual native
UI or clearly marked offline fixtures; no stock assets or sample product facts
were introduced.

ROUND 1
Seen in one second: platform choices and account/search, followed by repeated dark boxes.
Idea (one sentence): the selected source supplies a quiet glass canvas for its genuine account and collection actions.
Live thing: genuine QR/account connection, current-provider search and default-cover playback.
Layout words by section: source=row / login=split / account=split / library=identity-search-list / DJ=inline prompt.
Scores: design 7.4 · usability 7.3 · creativity 7 · content 7.7 → overall 7.3.
Caps applied: native-app/user-reference exceptions above; this is a self-review.
Top three problems (most visible first), each with the screenshot it shows in and the fix:
1. The supplied DJ screenshot places status outside the icon, directly over saturated red. Put both inside one contrast-aware glass capsule.
2. The earlier native login screenshot repeats scan guidance in a central stack and active-source clicks can discard valid work. Group QR/state/action, condense only standard instructions and keep current-source clicks idempotent.
3. The earlier library row repeats an opaque mode plate and unlabeled glyphs. Remove the nested plate, label all three existing choices and identify cover default playback.

ROUND 2
Seen in one second: source/account identity, then search and real collection covers; DJ status is one readable object.
Idea (one sentence): existing platform/song context becomes the material behind each actual action.
Live thing: explicit state-matched recovery, query return and existing playback handoff.
Layout words by section: source=row / login=split / account=split / library=identity-search-list / DJ=inline prompt.
Scores: design 7.8 · usability 8.1 · creativity 7 · content 8 → overall 7.8.
Caps applied: same native/user-reference exceptions; no external score is implied.
Top three problems (most visible first), each with the screenshot it shows in and the fix:
1. `entry/login-qr-fixture.png` originally repeated scan instructions. Final frames use concise waiting/scanned status while preserving full provider diagnostics.
2. Short-display layout review found fixed status/action overlap below 474 pixels. `entry/login-compact-360-fixture.png` and `account-compact-360.png` now show separately contained QR/status/action lanes.
3. `library/search-results-520.png` elides long titles to preserve visible modes. Full tooltip/accessible text remains; this is a retained narrow-width tradeoff. The clear-search handoff race found during interaction review was repaired through existing controller Back authority and a real-controller regression.

## Verification

The final related modules passed 237 tests at DPR 2 in 14.808 seconds. The
preceding DPR 1 focused run passed 236 tests in 14.158 seconds, before the final
source-row animation regression was added. The shared widget material module
also passed all 14 tests separately at DPR 1 and DPR 2. The final combined
source passed all **4648 tests in 253.549 seconds**, without skips or Qt
callback tracebacks. Python compilation and `git diff --check` passed.
The integrated command was:

```powershell
$env:QT_SCALE_FACTOR='1'
.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest discover tests -v
```

Its complete ignored output is
`logs/home-liquid-glass-integrated-final-2026-10-15.log`. All Qt verification
ran serially, independently of the user's existing running application. A final
independent read-only audit of material/background ownership, geometry/hits and
controller cancellation found no actionable issue.

The first integrated run executed 4648 tests in 254.718 seconds and reported
four failed subtests plus one error, all in the old home material assertions.
Those expected source/navigation/library controls to ignore the backdrop and
retain solid faces, conflicting with the requested scene glass. The assertions
now require warm/cool backdrop transmission for these controls, preserve the
unchanged mode-sweep/cover identity checks and click signals, and verify the
source tile's actual ancestor raster, mapped sampling rectangle, refraction,
selection state, source-colored rim and original platform-artwork dispatch.
No production renderer was changed to satisfy these corrected assertions.

Native Windows fixtures at DPR 1/2 cover:

- 42 DJ prompt frames per DPR: seven real statuses, both themes, at
  640×360 / 960×540 / 1920×1080.
- 11 source/login/account frames per DPR, including QR/error/scanned states,
  source availability, and 460/360 compact compositions. Every frame confirms
  contained actions, no source-page animation timer and background edge ≤256.
- 13 library frames and 65 checks per DPR: 760×720 and 520×500/360, expanded
  and compact headers, source/account identity, query/error/empty/loading and
  supplied artwork. Both native matrices passed all checks.

These total 132 native frames. Ignored evidence is under
`.runtime/home-liquid-glass-2026-10-15/dpr-1|2/`,
`.runtime/library-home-2026-10-15/dpr-1|2/` and
`logs/home-liquid-glass-*.log`. Fixtures use synthetic QR-marked images and
account/artwork data with no credential or remote login access. The checks
establish rendering, layout and existing controller contracts; they do not
establish authenticated login latency, physical display motion or new
long-session playback evidence.
