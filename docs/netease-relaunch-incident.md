# NetEase relaunch incident — 2026-10-29

## Failure mechanism

The old locator stopped every matching executable before spawning the debug
instance. Its stop/spawn sequence had no recovery block: a partial stop error,
a failed spawn, an early-exiting child, or a later CDP failure could leave the
player unavailable. The provider swallowed startup exceptions, while both
`Popen` and `CdpSession.start()` returned before useful startup was established.
Its lifecycle lock belonged to one provider, and Repair could close a session
and authorize another relaunch after two seconds. Independent providers and
app processes had no shared owner, cooldown or healthy-port reuse.

These are verified code-level causes of the kill-without-restore failure mode.
The original PC exception/exit status has not been supplied, so the precise
trigger on that PC remains unconfirmed. The user's observed baseline was normal
NetEase playback, without a debug flag, since approximately 13:41 Asia/Shanghai.

## Correction

`NetEaseLocator` is the only owner of destructive process changes. All callers
share an executable-specific OS file lock under the private temporary
`GluSpot/netease-relaunch` namespace. It reserves a 120-second cooldown before
stopping anything, and saves phase changes through a flushed temporary file
and atomic replacement so interrupted updates preserve the reservation.

Healthy existing debug ports are discovered from the saved owner record and
actual CloudMusic command lines, then validated with the existing loopback CDP
target discovery. They are reused before checking relaunch authorization.
Explicit stops are logged and executable paths are rechecked. A failed stop,
spawn, startup probe or cancellation requests normal, unflagged startup without
stopping surviving player processes. Cancellation waits for the owned worker
and recovery. Normal-start failure is surfaced rather than hidden.

The provider joins an existing session on a repeated repair trigger. Automatic
retries cannot authorize another stop. Late CDP failure detaches the failed
session and requests normal recovery, and health retains the actual error.
A reused port does not schedule position restoration and rewind current
playback. Position restoration after a real relaunch retains the existing
independent track/readback checks.

Recovery verifies matching processes, which may include a surviving renderer.
A usable native player and sustained playback must therefore be checked live;
Windows refusing normal startup cannot be described as successful recovery.
No version, packaging input, player-install file or real user setting changes.

## Verification

- Terra supplied ten isolated process-model regressions; root added interrupted
  state-update and independent-interpreter OS-lock checks. The final focused
  locator/provider result is 32 tests / 0.709 seconds, passed.
- Box capture discovery, narrow checks and full-suite comparison: final results
  are recorded below after verification completes.
- Fresh PC capture discovery ×3, native capture/narrow focus, the one approved
  relaunch, second-trigger no-stop proof and final running-player check remain
  pending. This session exposes no PC execution/file-transfer tool. No live
  kill/relaunch was attempted from Box and no PC result is claimed.

Guarded PC artifacts are outside source control at
`/workspace/gluspot/handoff/netease-relaunch-incident/`. The Python runner
requires the exact clean integration worktree and expected bundle tip, uses
only `E:\gpt-island\.venv\Scripts\python.exe`, runs the requested suites and
keeps logs in `E:\grok-gluspot\_logs\netease-relaunch-incident`. Its optional
live path audits stops from the actual narrow entry, blocks any second-trigger
stop before execution, observes sustained NetEase processes and SMTC playback,
and requests unflagged startup in its final recovery block if the player is
missing. Its syntax is checked on Box; Windows execution is pending.

Safe ordinary launch from `E:\grok-gluspot\_wt_capture2`:

```powershell
E:\gpt-island\.venv\Scripts\python.exe -X utf8 local/main.py --narrow-live
```

Process discipline: interfaces and callers were inspected, tests exercise the
failure paths, and native evidence is explicitly withheld. No push was made.
The incident is not ready for acceptance until the requested PC/live checks
complete, regardless of the process score.
