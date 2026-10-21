# Windows SMTC for owned playback — October 15, 2026

GluSpot publishes its program-owned music to Windows System Media Transport
Controls (SMTC). Windows can route supported headset/keyboard media commands to
the selected GluSpot session: Previous, Next, Play and Pause. The system panel
receives title, artist, album, original available artwork, playback status and
the actual owned song timeline. Supported system position requests use the
existing guarded seek method.

## Implementation boundary

`local/windows_smtc.py` uses the installed `winsdk` projection. A source-less,
muted WinRT `MediaPlayer` supplies the native SMTC session only; its automatic
command manager is disabled. It never receives a music source or plays audio.
The existing Qt decoder, local output, DJ layer and device routing remain the
audio authorities. SMTC is a control/metadata interface, not a second output
channel or an audio cast.

The app already assigns the explicit desktop AppUserModelID `GluSpot`. Its
owned session is enabled only while the owned playback source has a track;
stopping, disabling that source or quitting closes/unregisters it. Paused owned
playback remains available for a headset Play request. WindowsMediaProvider
excludes that exact identity from external session selection, including its
current-session fallback. Unrelated `python.exe` sessions remain eligible.

WinRT creation/publication/artwork/cleanup run on one worker. A one-slot mailbox
coalesces clock updates to at most two publications per source-clock second,
with immediate metadata/state changes. Native callbacks send a queued Qt
signal containing the track ID and existing player generations. Commands older
than two seconds, commands for a retired generation, commands after close and
commands after source disable are discarded on the GUI thread. Play/Pause are
idempotent and respect pending catalog/DJ/recovery resume intent. Previous/Next
reuse the owned queue, personal FM and DJ behavior.

Artwork uses an in-memory WinRT stream, with no new cache or network request.
The previous thumbnail is cleared when the next track has no artwork. A
thumbnail failure cannot prevent title/transport publication. The installed
`winsdk` runtime rejects `None` for the thumbnail setter despite its Optional
stub, so clearing uses the verified `display_updater.clear_all()` method.

## Interface evidence

Looked up the installed `winsdk` stubs for `MediaPlayer`, its
`system_media_transport_controls`/`command_manager`, SMTC event tokens,
`SystemMediaTransportControlsTimelineProperties`, `DataWriter`,
`InMemoryRandomAccessStream` and `RandomAccessStreamReference`. A separate
non-Qt process verified a real source-less native session before integration:
title, album, thumbnail and a 10/180-second timeline were read back from the
Windows global session manager. Native Pause, Next and Seek requests all
returned true and generated callbacks on WinRT threads. The test used a unique
explicit AppUserModelID and did not address another application's session.

## Repeatable verification

Serial Qt unit/provider/application-boundary command:

```powershell
.venv/Scripts/python.exe -X utf8 -m unittest tests.test_windows_smtc tests.test_provider_state tests.test_app_owned_surface_sync -v
```

Real offline Windows/Qt acceptance:

```powershell
.venv/Scripts/python.exe -X utf8 tools/smoke_windows_smtc.py --output .runtime/windows-smtc-smoke/result.json
```

The smoke tool creates generated local WAVs and a unique process AppUserModelID,
mutes both Qt outputs, serves the generated WAV through its own loopback HTTP
server to preserve the player's existing HTTP(S) stream contract, and targets
only that exact system session. It verifies
metadata/artwork/timeline readback; Pause, Play, Next, Previous and Seek reaching
the actual `OwnedMusicPlayer`; GUI-thread command dispatch; the source-less
muted native session; and removal after source disable. It uses no credentials,
external network, system volume or another application's audio-control API.

The root agent's first focused unit/provider/application-boundary run passed
153 tests. The real Windows/Qt smoke then passed all 18 checks in 2.96 seconds;
its bounded machine-readable evidence is
`.runtime/windows-smtc-smoke/result.json`. The initial smoke correctly exposed
that a file URI is outside the existing stream contract; only the fixture was
changed to loopback HTTP. The final combined source passed **4627 tests in
322.896 seconds**, without skips or Qt callback tracebacks, including the SMTC
unit/provider/application boundary checks. Its ignored log is
`logs/gluspot-integrated-final-2026-10-15.log`; Python compilation and
`git diff --check` also passed.

The Windows spec explicitly includes the WinRT media/playback projection used by this bridge for the next packaging run. Version and existing EXE are unchanged; no new package was built. Physical headset firmware and
Windows' choice among several concurrently playing apps require interactive
device testing; a successful Windows session command is not a claim that every
headset gesture has been physically verified. Headset volume keys retain their
normal Windows endpoint-volume behavior.
