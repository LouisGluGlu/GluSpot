# Owned output preparation and repeated introductions

Date: October 17, 2026. Source version remains `6.412`.

The reported symptom was a short early segment on another output when changing
songs or adding devices, followed by the beginning playing again with the
synchronized group. The owned Qt-to-group preparation path contained a concrete
cause: its presentation-capable helper deliberately received PCM while the Qt
output remained audible. At the delayed group presentation boundary, Qt was
muted and the buffered segment played again. Track/seek restarts also released
the prior private mute before starting that preparation path.

## Corrected source behavior

Cold synchronization preparation now reserves the player's private mute and
holds the real decoder clock before receiver authentication. Muting without
pausing would discard the introduction while authentication completed, so the
music decoder and any active DJ voice pause at their actual positions. A song
whose prepared file arrives during authentication also waits; all music play
requests pass the same preparation gate.

The helper's `waiting_audio` event releases the decoder hold and starts feeding
its existing native schedule. The Qt music and voice outputs remain privately
muted throughout the native presentation reserve. The introduction therefore
enters the group once, from its original position. This retains the established
protocol buffer rather than guessing a shorter HomePod or Bluetooth delay.

Source changes and seek discontinuities establish the hold synchronously on
Qt's thread, before the route worker tears down the old group. Cold membership
changes keep that hold/mute across replacement. An established capable group's
warm membership command keeps its existing authenticated pipe, source epoch,
receivers and source-frame timeline; it never takes this cold preparation path.

The user can pause or resume while preparation is pending. Readiness cannot
undo an explicit pause. Preparation and stream recovery share that intent, and
release cannot bypass recovery's loaded-media/seek-readback checks. DJ music and
speech retain their own actual positions; their existing mixing and gain rules
are unchanged.

Stop and failure release only the bridge-owned mute and restore the retained
play intent. Preparation failures release the exact attempted reservation epoch
as well as the prior session. This also covers a GUI preparation slot completing
after the routing worker's bounded wait and an old helper teardown failing. An
old epoch release cannot unmute a later reservation.

## Native group inspection

The unchanged native group loop waits for every initial receiver's SETUP-ready
state before assigning one steady/NTP anchor and arming all receiver/local
timelines (`native/airplay_sender/group_main.cpp`, lines 586–618 at this source
freeze). New members use `groupJoinFrame(nextFrame)` and a shifted mapping on
the existing timeline (lines 629–630); only applied membership confirms the
new selection (line 702). This inspection did not identify a group-internal
early-arm/reset path for AirPlay receivers. It does not establish every physical
receiver's actual output behavior or prove that the Qt prefill explains every
reported external-device symptom.

## Verification

The final focused command passed **287 tests in 11.211 seconds**:

```powershell
.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest tests.test_owned_output_preparation tests.test_owned_output_reconfiguration tests.test_owned_audio_bridge_handoff tests.test_owned_audio_outputs tests.test_owned_playback tests.test_personal_dj_playback tests.test_personal_dj_pcm_mix tests.test_owned_buffered_playback tests.test_music_recovery tests.test_owned_stream_faults tests.test_owned_selection_cancel -v
```

Coverage includes cold/track/seek preparation, unchanged warm membership,
bounded reservation rollback, helper startup/teardown failure, stale epochs,
async file completion, pause/resume intent, recovery seek readback, DJ
music/speech pause and resume, and the established PCM mixing/stream/fault
contracts. Python compilation and the focused diff whitespace check passed.
The final combined native Windows suite passed **5061 tests in 297.613
seconds**, without skips or Qt callback tracebacks. The complete log is
`logs/oct17-installer-audio-lifecycle-full.log`; package results belong to the
combined release report.

An additional actual Qt decode fixture passed **12 checks**:

```powershell
.venv/Scripts/python.exe -X utf8 -X faulthandler tools/smoke_owned_output_preparation.py
```

It held actual `QMediaPlayer` decoding for two seconds during each simulated
authentication wait. After release, the observed first PCM timestamps were
**0 µs** for the first song, **2,000,000 µs** after a two-second seek and **0 µs**
for the next song. It also checked pause cancellation, explicit resume, stop
recovery, continuously muted forwarding and volume-zero fixture outputs.
Its backend, receiver and readiness objects are in-memory. Only its own
`QAudioOutput` values change; it contacts no real receiver and changes no system
endpoint volume, mute, default or device route.

Evidence stays in ignored runtime storage:

- `.runtime/owned-output-preparation-focused-2026-10-17.log`
- `.runtime/owned-output-preparation-2026-10-17.json`
- `.runtime/owned-output-preparation-realqt-2026-10-17.log`

Physical HomePod mini, AirPort/M6, other AirPlay and Bluetooth listening,
receiver-buffer behavior, long-session drift and recovery after real wireless
interruptions remain outside this fixture's evidence. Authentication and the
existing synchronized presentation reserve still take time; the correction
prevents early local playback and preserves the beginning during that wait.
