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
