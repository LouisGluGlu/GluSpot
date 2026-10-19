# Owned output lifecycle and stopped PCM retention

Date: October 17, 2026. Source version remains `6.412`.

The user reported noticeable stutter when returning from multiple outputs to a
single output and suspected unreleased memory. This inspection distinguishes
active synchronization resources from stopped-state retention; it does not
equate a playback delay with a memory leak.

## Observed ownership

An established owned group intentionally keeps its native helper, authenticated
PCM writer and delayed presentation timeline when its membership becomes one
Windows output. This existing contract prevents that remaining output from
jumping to Qt's decoder clock, which runs ahead of the delayed presentation.
The group reconfiguration path does not pause or restart that decoder. The
native member-retirement path and UI presentation have separate verification.

The Python controller's selection/stop work runs on its routing worker. Its
snapshot/command locks protect short copies and state changes; native teardown
and Qt request waits do not run while the controller holds those locks. A live
single-member synchronized group still has active resources by design. This
inspection alone does not establish their acoustic latency or prove the cause
of the reported stutter.

## Corrected stopped-state retention

`OwnedPcmProcess.stop()` previously cancelled its consumer without draining the
pending `Queue`. A deterministic inert fixture retained **327,680 bytes** of
queued PCM after stop, within the existing **352,800-byte** bound. It also kept
the stopped group's active membership objects. This was bounded retained state,
not evidence of unbounded growth across repeated sessions.

Stop now clears queued PCM and byte accounting immediately after cancellation,
then drops active device/membership evidence once the session ends. Historical
terminal and timing diagnostics remain available. A second stop/epoch fence
inside the submission lock prevents a producer that passed the initial check
from appending audio after stop has drained the queue.

Pipe cancellation, process teardown, writer disposal and active-pipe reference
release use `finally` boundaries so an exception while closing one component
cannot skip the remaining cleanup. Retired writer completion remains fenced by
its exact pipe/epoch identity. The writer's existing clamped byte subtraction
cannot turn the stopped byte count negative.

## Verification and scope

The three original lifecycle regressions failed before the repair: stopped PCM
remained queued, repeated stop retained accounting, and active membership
references survived. The final five lifecycle tests also cover a submitter
waiting on the PCM lock and a pipe-close exception. The final related command
passed **173 tests in 17.287 seconds**:

```powershell
.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest tests.test_owned_pcm_lifecycle tests.test_owned_pcm_membership tests.test_owned_audio_outputs tests.test_owned_output_reconfiguration tests.test_owned_audio_bridge_handoff tests.test_owned_output_preparation tests.test_personal_dj_pcm_mix tests.test_airplay_process tests.test_airplay_group_process -v
```

An additional **200-cycle** inert resource profile repeatedly created the
transport's real Python threads, queued five 65,536-byte PCM packets and stopped
it. The fake process and delayed pipe never contacted a native helper or device.
After each 50-cycle batch, Windows process handles remained **186**, Python
threads remained **1**, and retained PCM bytes, queued packets and reader
references were **0**. Active process, PCM writer and pipe references were
absent. Traced current allocations were 15,608–17,152 bytes with a fixed
685,198-byte peak; the small increasing report samples account for retained
measurement objects. This fixture did not show accumulating worker/handle/PCM
resources.

Evidence remains under ignored runtime storage:

- `.runtime/owned-pcm-lifecycle-before-2026-10-17.log`
- `.runtime/owned-pcm-lifecycle-after-2026-10-17.log`
- `.runtime/owned-output-lifecycle-focused-2026-10-17.log`
- `.runtime/owned-pcm-lifecycle-profile-2026-10-17.json`

No real GluSpot process, receiver, system default, endpoint volume or endpoint
mute was changed. The profile establishes the inert Python lifecycle and its
bounded resources; it does not establish physical device smoothness, the native
receiver's retirement behavior or memory use of the user's running process.

The subsequent combined native Windows regression passed **5061 tests in
297.613 seconds** without skips or Python/Qt callback tracebacks. The log is
`logs/oct17-installer-audio-lifecycle-full.log`; all 105 frozen implementation
hashes were unchanged after the run. Native member-retirement and the related
idle-hover cleanup measurements are recorded separately in
[the resource profile](output-resource-profile-2026-10-17.md).
