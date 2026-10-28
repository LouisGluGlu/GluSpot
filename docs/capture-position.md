# Experimental external position capture

The `grok/capture` implementation covers PRD steps 1–6. It changes no release
version or package and deletes no legacy reader or provider path. All new
runtime work requires `v2_capture_position_enabled: true`; the default is false.
Program-owned playback remains independent behind `api/providers.py`.

## Module ownership

- `api/capture/contracts.py`: immutable physical observations and asynchronous
  PositionSource / SeekBackend / Calibrator protocols, expressed in seconds.
- `position.py`: deterministic bounded monotonic projection, once-mapped source
  wall timestamps, transport transitions and drift filtering. Accepted anchor
  timestamps reach both existing lyric consumers as confirmed reanchors;
  presentation hysteresis is 300 ms in `local/media_position_pipeline.py`.
- `smtc.py`: session reads, native event subscriptions and 2 Hz polling on the
  provider's existing background asyncio thread. Events arriving during a read
  remain pending; metadata loss, errors and read timeout invalidate prediction. A fresh native
  wrapper for the same valid identity does not interrupt an in-flight seek.
  Projection expires after two seconds without a successful observation.
- `seek.py`: serialized request/verification state machine. Each user request
  sends SMTC once, reads independent physical state over 600 ms (800 ms for
  NetEase/QQ), and permits one UIA fallback only after the SMTC mutation has
  finished. Tolerance is 1.5 seconds. Request IDs are idempotent within a bounded
  256-result history. An uncertain native mutation blocks subsequent requests
  for the provider's lifetime, including flag/profile toggles; restart the app to
  clear it. An optimistic target
  never supplies verification evidence, and failed lyric clicks show feedback.
- `uia.py`: one lazy STA worker, fresh progress-only calibration, and timeout
  quarantine. Normally samples every 7.5 seconds (at most every two seconds
  during anomalies). Windows dependencies import on the worker. A stalled COM
  call cannot be interrupted: its slot stays occupied and cannot receive a
  second operation. Failed reads invalidate native wrappers; UIPI/access denial
  fails closed. UIA is available only on Windows unless a test injects an adapter.
- `capabilities.py`: immutable AUMID / exact process-basename capability table;
  no executable-path requirement or filesystem/process probing. Unknown players
  use SMTC and learn trust from ordinary user-initiated seeks, never unsolicited
  test seeks. Diagnostics retain bounded per-player seek counts, success rate,
  readback error, SMTC trust, event rate/delay and UIA calls/timeouts/errors.
- `runtime.py`: session generation, lifecycle, model, optimistic UI and optional
  calibration coordination. Only `api/providers.py` attaches it to the app.
- `netease.py`: read-only install/process/version discovery and explicit
  launch/relaunch with random loopback debugging flags. Reconnect never stops
  or restarts a player.
- `cdp.py`, `_websocket.py`, `netease_js.py`: bounded loopback CDP session,
  standard-library WebSocket framing and versioned native-event JS adapter.
- `cef.py`: push-only physical position source and independent CEF seek
  readback using the shared `SeekExecutor`.

## Configuration and migration

The master flag is a developer opt-in in the existing settings JSON. To roll out
SMTC-only for NetEase while keeping QQ on the legacy route, for example:

```json
{
  "v2_capture_position_enabled": true,
  "v2_capture_app_profiles": {
    "netease": {"mode": "smtc"},
    "qq": {"mode": "off"}
  }
}
```

Modes are `auto`, `smtc`, `hybrid` and `off`. Auto chooses hybrid for NetEase/QQ,
SMTC for Spotify/browsers and SMTC with passive seek-trust observation for unknown
players. A hybrid override cannot invent an unsupported UIA adapter. The existing
global system lyric offset remains authoritative; legacy per-app offsets do not
return. Legacy app profiles remain fixed compatibility data, separate from the
new envelope.

Migration occurs only after explicit opt-in and uses raw saved legacy data
before existing fixed-default normalization. A configured path plus explicit
`sync_enabled: false` preserves manual disable. Empty path plus false migrates
to auto; those historic defaults cannot be distinguished from a manual save of
the identical pair. Use explicit new `mode: off` to preserve that intention.
New modes always take precedence and survive restart/master-flag disable.
No configured path is needed to enable a recognized player's adapter.

## Evidence and acceptance limits

`tests/fixtures/capture/` contains 41 deterministic mock timeline events and
explicit expected positions. These are serialized replay fixtures, **not live
Windows recordings**. Tests cover interpolation, pause/resume, forward/backward
seek, ends, clock adjustments, track/session replacement, fake acknowledgements,
timeouts, COM access denial, disappeared elements, no double mutation, event
races, flag isolation and settings migration. Linux offscreen discovery is
compared with the unchanged baseline; existing failures are documented in README.

Windows native acceptance remains required: NetEase/QQ/Spotify/browser ×
play/pause/buffering/drag/track-change/minimized/multiple sessions/restart;
event-arrival/latency measurements, UIA-call rate and multi-hour soak. In
particular, multiple sessions with the same AUMID and identical track metadata
can remain ambiguous, UIA layouts/permissions can change, and a late native
write after timeout cannot be physically rolled back. No real-player success
rate, physical seek behavior or performance claim is established by mocks.

## Linux verification record (2026-10-28)

Both runs used `/workspace/gluspot/qtvenv/bin/python` (Python 3.13.5) with
`QT_QPA_PLATFORM=offscreen` and `-m unittest discover tests -v`.

| Run | Tests | Seconds | Failures | Errors | Skips |
| --- | ---: | ---: | ---: | ---: | ---: |
| Unchanged baseline | 5,453 | 155.793 | 17 | 11 | 31 |
| Final implementation | 5,544 | 146.022 | 17 | 11 | 31 |

The failure/error identity sets match exactly: zero new and zero resolved.
All 91 added capture tests pass; the independent capture discovery passed all
91 in 1.242 seconds. The full run produced no unhandled-future warnings.
Compilation of changed application modules and `git diff --check` pass.
Inherited failures/errors cover missing optional `soundcard`, native Win32 mocks,
missing font assets, and offscreen rendering/transition assumptions. They were
not suppressed or modified to make this comparison pass.

Complete logs and machine-readable comparisons are ignored local evidence in
`.runtime/capture/{baseline,final}.log` and `{baseline,final}-summary.json`.
The final full run tested source `a99d8a5`; the subsequent amendment adds only
this evidence and the matching README record. No EXE was built or pushed.

## NetEase CEF experiment: transport A

The NetEase follow-up implements PRD P0/P1 through **transport A only**:
GluSpot launches or relaunches `cloudmusic.exe` with
`--remote-debugging-port=<random>` and
`--remote-debugging-address=127.0.0.1`. It never writes into NetEase's install
directory. There is no bridge DLL, pipe transport, native injection, installer
modification or dependency installation. The capture master flag defaults off,
as do `netease.cef.enabled` and `netease.transport`. The release remains 6.417.

An explicit development opt-in uses these existing settings JSON keys:

```json
{
  "v2_capture_position_enabled": true,
  "netease.transport": "relaunch",
  "netease.cef.enabled": true,
  "netease.adapter": "auto"
}
```

`netease.transport` supports only `relaunch` in this branch. `off` disables it;
the reserved `port` and `pipe` values do not enable another transport. The
standalone existing-port probe below is a diagnostic, not a second runtime
transport. `netease.adapter` accepts `auto` and `legacy-native-v1`; both require
the feature probe to succeed. Setting the capture profile to `off` still wins.

The settings panel shows **网易云精确进度（实验）** only when read-only Windows
discovery finds NetEase. **启用并重启网易云** presents one confirmation covering
playback interruption, the unauthenticated local debug port and NetEase terms.
Accepting sets the explicit flags, resets the NetEase capture profile to `auto`
and queues process work on the provider loop. The health lamp reports waiting,
connected or degraded seek state. **修复 / 重启网易云** explicitly reconnects
through another relaunch. There is no automatic process restart during CDP
reconnection. **关闭精确进度** turns the route off and detaches GluSpot; normally
exiting NetEase closes the debug port created for that process launch.

The relaunch closes only processes whose verified executable path matches the
selected `cloudmusic.exe`. It may discard unsaved NetEase UI state. Position
restoration is best effort through the same independently verified seek path:
post-restart SMTC metadata must match the original track, and a previously
known native track ID must also match. A missing observation or changed track
refuses restoration. An unknown native identity cannot establish recording or
version identity beyond the existing metadata match.

`CdpSession` discovers targets through loopback `/json`, selects the verified
`orpheus://orpheus/pub/app.html` page and enables Runtime bindings.

NetEase 3.1.42's native adapter has **one native callback slot per event**. The first
`appendRegisterCall` creates a websdk stub via `registerCallAdapter`, displacing
the native UI callback. Before injection, `registerCallMap` had no
`audioplayer.onPlayProgress`, `audioplayer.onPlayState`, `audioplayer.onLoad` or
`audioplayer.onEnd` entries. Receiving events after registration does not prove
that NetEase's UI callbacks or automatic track advance remain intact.
The UI therefore uses a separate native path. The adapter now installs a
small document-start shim with `Page.addScriptToEvaluateOnNewDocument`, then
reloads the page once so it runs before NetEase's own registration. The shim
observes the registration boundary (including `legacyNativeCmder` registration
methods, `channel.registerCall` and their supported adapter objects), wraps
matching named event handlers, and records the path and method in `hook_health()`
for the probe.
Each wrapper calls NetEase first and forwards to GluSpot afterward; detach only
clears GluSpot forwarding, so the native wrapper continues to invoke NetEase.
User-supplied document-start discovery on wuju PC (NetEase 3.1.42) found
`channel.registerCall` registering PlayProgress, PlayState and End. Load was
absent during the eight-second boot window; `appendRegisterCall` was unused
and `registerCallMap` remained empty. Readiness requires PlayProgress;
PlayState, End and Load are optional. The shim intercepts global assignment
and `Object.defineProperty`/`defineProperties` installs, and revisits assigned
methods/children during a boot scan bounded by five seconds or 100 ticks.
Health keeps the complete caught-event list and `registrations` records.
No live Windows page was accessed from this workspace for this follow-up.

The late fallback adapter must never call `appendRegisterCall` or
`registerCallAdapter`. It may only chain an existing callback in an already
populated `registerCallMap` queue with a callable `registerCallStubs` entry.
Require the progress queue, skip absent optional queues, and preflight every
present queue before any mutation. Each wrapper calls the original
callback first with the same `this` and arguments, preserves its return value
and error propagation, and swallows only observer errors. Close must restore
the original slot descriptor; reinstall must be idempotent. If no established
chain is available, return `api:false` with health reason
`native-callback-chain-unavailable` and use the existing fallback below. Do not
create queues or register callbacks to manufacture a chain. The observed empty
map is therefore safe only when the document-start shim has caught PlayProgress.
If the shim misses PlayProgress and no safe existing progress queue is available,
the adapter retains the refusal reason `native-callback-chain-unavailable`
and does not create a queue or displace NetEase.

Ordinary detach/close restores each still-owned callback's exact descriptor
before removing the Runtime binding and closing the socket. It preserves any
later callback replacement made by NetEase. If the socket is already dead,
Python cannot execute immediate page cleanup; wrappers still call their originals
and native registration is untouched. Reconnection reuses the same adapter
without double wrapping. Replacing the page destroys its old callbacks entirely.

The supplied NetEase 3.1.42 evidence establishes `PlayProgress(trackId, seconds,
ratio)` at approximately 30 ms intervals and
`channel.call('audioplayer.seek', callback, [trackId, uniqueSeekId, seconds])`.
Ordinary pushed samples are coalesced to at most 10 Hz; track, state and seek
events are delivered immediately. Ratios 0 or 1 cannot infer duration; a known
prior duration or explicit `Load.duration_seconds` is retained. Progress is never
polled. Seek retains the channel call above and independent physical readback.
Unknown versions must pass the same conservative chain preflight; the channel
feature probe independently determines whether seeking is available.
The numeric meanings of `PlayState` and whether native seeks work while paused
have not been verified on the real player. The adapter does not invent a
numeric state mapping, and its probe reports paused-seek support as unknown.
When native state is unknown, SMTC supplies only play/pause state; it cannot
refresh the native position timestamp. A confirmed paused position remains
valid while the adapter heartbeat is healthy. Provider track replacement
rejects the previous native track ID until a different native track event
arrives, including when metadata reaches GluSpot before the player event.
State arrival has its own display timestamp, so resuming after a long pause
does not project through the paused interval. Seek verification uses the
separate native progress receipt time and sequence; a state edge or repeated
adapter-cache read cannot become fresh progress evidence.

The WebSocket client in `api/capture/_websocket.py` is a small GluSpot-owned
standard-library implementation, not a downloaded or installed package. It is
limited to this loopback CDP use: masked client text frames, unmasked server
text frames, fragmentation and ping/pong/close, with a 4 MiB message limit.
Unsupported extensions, binary frames and remote target URLs fail closed.
Keeping this narrow client avoids adding an unpinned runtime dependency to the
Windows builds. Fake-server tests exercise its handshake, framing, discovery
and disconnection behavior. One writer lock serializes command and control
frames; the session has one receive task and dispatches responses by CDP ID.

`CefPositionSource` implements the existing physical-observation contract.
`CefSeekBackend` reuses `SeekExecutor` for identity fencing, one mutation per
request ID and independent physical readback. An accepted CDP command or the
requested target alone cannot confirm a seek. A missing or drifting readback
disables that precise seek route without blocking ordinary playback controls.
The existing `PositionModel` and calibrator remain unchanged.

The fallback order is CDP, a physical SMTC timeline only when nonzero, then an
available read-only UIA progress observation, then basic capture. Attach/probe
and lost-heartbeat failures invalidate CDP observations and retry with bounded
exponential backoff (10 seconds maximum). A disconnection never converts the
last CDP target or an optimistic position into fallback evidence.
UIA fallback reads are limited to approximately one per second when SMTC has
no usable timeline; they do not authorize UIA seek mutations in this chain.

CDP has no authentication. Loopback binding and a random port reduce accidental
exposure but do not prevent another local process or user from attaching. The
enable/restart flow discloses this and the possibility of a NetEase terms-of-use
conflict. Enabling precise capture does not decrypt media or bypass paid access.
Restart can interrupt playback; restoration is best effort and requires an
independent matching track observation.

### Existing-port diagnostic

Start NetEase with its loopback debugging flags, start playback, then run:

```powershell
python tools/netease_cdp_probe.py --port 9222
```

The tool attaches to that existing port; it never launches, relaunches or stops
NetEase. Without an established callback chain it must report `api:false` and
`native-callback-chain-unavailable`, without registering callbacks or seeking.
With a chain, it samples pushed progress for ten seconds, then verifies seeks to
15, 45 and 90 seconds through `CefSeekBackend` and `SeekExecutor`. Targets are
clamped inside the observed duration to avoid advancing to the next track. It
prints each physical readback error in milliseconds, then attempts to restore
the first observed position. A track change aborts further mutations rather
than seeking another song. An uncertain mutation remains quarantined by the
same executor, so restoration may be refused safely. Playback is not paused by
the tool. The last stdout line is a JSON summary; exit status is zero only if
all three seeks and restoration were independently verified.

Probe reports must include original callback counts, whether the callback chain
remains intact, and whether physical playback is advancing. Event delivery or
successful seek readback alone does not establish these properties. Auto-next
must be reported as **unverified unless checked on device**. This documentation
update performs no live probe and claims no live acceptance.
Callback counts establish invocation of the pre-existing WebSDK callbacks,
not audible output or execution of an unidentified native UI callback. The
`existing=true` offline fixture models a compatible pre-existing WebSDK route;
the reported clean 3.1.42 state uses the separate `existing=false` refusal case.

The supplied live-player timing evidence (25–34 ms to the first post-seek
progress event) is a pre-existing observation from NetEase 3.1.42, not a result
established by the fake server tests or acceptance of the conservative chain.
Windows player/version coverage, intact original callbacks, physical playback
advance, auto-next, actual relaunch/restore behavior, paused seeks, CPU use,
eight-hour soak and 1,000-seek acceptance remain required before enabling this
experiment by default.

### Non-invasive hook verification (2026-10-28)

The final source passed three consecutive runs with
`QT_QPA_PLATFORM=offscreen`, `/tmp/gluspot-capture-test-venv/bin/python`
(Python 3.13.5) and `-m unittest discover -s tests -p 'test_capture*.py'`:
**203 tests / 9.948 s**, **203 / 10.227 s**, **203 / 10.004 s**; zero failures,
errors or skips. Compilation and `git diff --check` passed. Offline tests cover
the confirmed first-APPEND displacement model, preserved native registrations
and original callback invocation, preflight of progress and present optional
queues, rollback, exact slot descriptor restoration, idempotent attach, observer exception isolation,
chain loss, refusal health, probe gating and close during failed-probe cleanup.
Channel tests cover definition/assignment installs, no-Load and progress-only
activation, missing-progress refusal, discovery records and bounded scan expiry.
The heartbeat reconnect test allows a five-second wait budget.
The existing unknown-duration, 10 Hz and independently verified seek tests pass.

No live NetEase process or installation was accessed from this workspace.
The user-supplied PC registration observation above is separate from these
offline tests. PC repetitions, lyric/audio continuity, sample rate, seek
latency and End/auto-next behavior remain unverified by this follow-up. After
syncing this commit, repeat the capture discovery three times from
`E:\grok-gluspot\_wt_capture2` using only
`E:\gpt-island\.venv\Scripts\python.exe`.

After the user restarts NetEase with `--remote-debugging-port=39222`, device
acceptance must check audible uninterrupted playback, in-app lyric scrolling,
pause/resume, seeks, End/auto-next, repeated attach and ordinary detach. The
reported empty-map state must refuse capture if the early shim missed progress;
it must not create WebSDK queues. When the shim caught progress or a genuine
compatible progress queue exists, check callback counters and chain integrity too.
These device checks and PC repetitions were not performed by this change.

### NetEase P0/P1 verification (2026-10-28)

These recorded runs predate the conservative callback-chain requirements above;
they do not validate those requirements or establish live acceptance.

Both full runs used the standard command with
`/workspace/gluspot/qtvenv/bin/python` (Python 3.13.5),
`QT_QPA_PLATFORM=offscreen` and `PYTHONFAULTHANDLER=1`:

```bash
/workspace/gluspot/qtvenv/bin/python -m unittest discover tests -v
```

| Source | Tests | Seconds | Failures | Errors | Skips |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline `a69e740` | 5,544 | 149.938 | 17 | 11 | 31 |
| P0/P1 `33d4d72` | 5,625 | 158.407 | 17 | 11 | 31 |

All 28 failure/error identities match: zero new and zero resolved. The
independent capture command, `-m unittest discover -s tests -p
'test_capture*.py' -v`, passes **172 tests in 4.422 seconds**: the inherited 91
plus 81 P0/P1 tests. Coverage includes a stdlib fake HTTP/CDP/WebSocket server,
the real JS adapter under the existing Node runtime, reconnect and heartbeat
loss, native/state timestamp separation, track races, independent seek
readback, default-off provider lifecycle, UI consent and the existing-port
probe's three seeks and restoration. Compilation and `git diff --check` pass.

Earlier full attempts crashed in native Qt while decoding an image in the
pre-existing artwork tests, before capture tests executed. The new UI test's
helper imports were deferred until after `QApplication` exists; the final
standard discovery run above completed without a custom loader or suppressed
tests. The underlying Qt/Python lifetime fault is not proven. This evidence
establishes no new observed test failures; it does not establish clean native
Qt behavior or real Windows/NetEase acceptance.

Ignored local evidence: `.runtime/capture/netease-baseline-recheck.log`,
`.runtime/capture/netease-ui-lazy-import-investigation.log` and
`.runtime/capture/netease-final-summary.json`. The source hash precedes only
this documentation commit. No EXE was built or pushed.
