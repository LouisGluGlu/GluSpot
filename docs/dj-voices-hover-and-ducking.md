# Four DJ voices, shared hover and speech presentation

October 12, 2026 source follow-up for GluSpot `6.412`. This document records
the current requested behavior and final source verification.
Version and previously packaged EXE are unchanged.

## Real voice selection

The official NetEase catalog verifies 豆花、黄俊、李源、庞舸. The old bridge
omitted `timbreKey` and its PC profile returned an empty catalog. The corrected
two DJ requests use the observed official public client contract behind the
existing service/provider boundary. The catalog supplies names, descriptions,
keys and defaults; the UI never invents a mapping. See
[public-client and actual production evidence](personal-dj-client-contract.md).

Settings → Media → Playback Sources → Private DJ Voice remains a keyboard
accessible entry. On the song page, hovering over the lower-right DJ icon grows
it with a half-second decelerating curve, then opens a categorical glass slider
below the rings, in the same position and dimensions as the speech caption.
The four voice names select actual catalog keys. Clicking the rings still
toggles DJ; choosing a voice never changes that toggle or restarts current music.
Left/right arrows select voices, Escape dismisses, and a two-second pointer-leave
grace lets the pointer cross from the icon to the slider. Keyboard focus does not
pin the selector after the pointer leaves. Worker results are fenced after
close/reload/account changes; fetching never blocks Qt.

Voice choice is saved as `v2_personal_dj_voice_key`. An empty choice means the
actual catalog default. Changing a choice preserves the current introduction
and music, invalidates old future speech/prepared batches and fetches subsequent
recommendations with the new voice. The official service sometimes supplies
individual songs without an introduction. Those songs remain in official order;
the app does not fabricate commentary for them.

Deliberate selection auditions the selected catalog's genuine `playUrl` through
the provider and prepared-audio boundary. These are the four official self
introductions, not the originally requested fixed sentence. Read-only bounded
downloads and local offline transcription established the difference; the user
explicitly chose real official auditions. `defaultAudio` is a boolean default
marker, not an audio URL. Startup preference restoration never auditions.
New selections replace older pending auditions; a current introduction finishes
before the latest audition. Preview never changes the song queue or music source.
An explicit audition can sound while music is paused, with DJ disabled, or before
a song is selected; it does not start music or enable DJ. Source/account changes
and preparation faults retain their own cancellation boundaries. The four
auditions use their actual individual self-introductions; no audio is bundled
and no system-speech voice impersonates an official DJ.

An unchanged catalog refresh for the same account reuses its immutable voice
snapshots, so reopening the selector does not cancel an authorized audition.
Logout, account replacement or changed audition fields/addresses produce new
identity and invalidate old pending/active samples.

## Speech over music

The latest user instruction supersedes the preceding paused-before-music rule:
the introduced song actually plays while commentary speaks. Music is temporarily
ducked to 20% of its prior gain and speech retains normal gain. At speech end,
music rises smoothly to its prior gain over one second. The cover alone
uses the paused smaller pose, returning to normal size at speech end. Playback
controls and lyrics retain the actual music state and advancing song clock.
Manual Next resumes from a paused song; Previous retains the existing policy.

The music decoder remains the music clock. A separate speech decoder consumes
genuine prepared audio; cancellation, seek, shutdown and speech faults release
its lease and restore music gain without reopening healthy music. Single-device
outputs share the selected device; group PCM uses the established authenticated
stream epoch and mixes music/speech before delivery. User/device volume settings
are not rewritten to perform ducking. The muted checks below exercise actual
Qt buffers with a packet-recording coordinator, without physical receivers.

During a deliberate audition, pausing music leaves speech playing independently.
Future decoded music stays queued through speech completion or cancellation.
Resuming remaps the live speech clock and applies a single 20% music gain without
reopening the song or its direct-feed epoch. Ordinary introductions retain the
existing shared pause/resume policy.

Island speech paints larger independent hollow rings and the associated title
directly on the original ambience and shell. There is no nested rounded card or
solid icon ball. Its existing spring grows the actual island to 112 logical units
in Free/top placement; Tasker retains its physical taskbar bounds. Speech end
returns normal music content. It reads the same provider snapshot even with the
song page hidden; optional FFT failure cannot clear authoritative speech state.
The painter uses cached state and performs no account/catalog I/O.

## Shared button motion and glass

Native buttons, switches, settings editors/sliders/dropdowns/sidebar items,
entry/library actions, device rows, song controls and island transport retain
their original state faces. Fine directional rims and 80% original alpha make
the glass edge and translucency visible. There is no added replacement plate.
The unversioned iOS demo and pinned rdev source are documented in
[reference evidence](liquid-glass-references.md).

Hover grows controls to 110% over 0.5 seconds with rapidly diminishing growth;
large cover cards use a restrained 103%. Same-group neighbors move away with
distance-decaying displacement and a bounded damped spring, including animated
return. Device menus, DJ voice selection, dropdowns and details wait for a
continuous 0.5-second hover. Ordinary click/keyboard actions remain available.
Paint and pointer mapping use the same pose; native widget layout positions
and existing graphics effects remain unchanged. Moving widget pixels are
composited through a transparent overlay that is disposed with its owning
container. No background scene or desktop is captured for this effect.

## Island horizontal lyric stability

Whole-row island lyrics previously inherited the global reversing metadata
marquee; a ten-pixel fit margin could also switch near-fitting text between
centered and edge-aligned poses. Lyrics now remain centered whenever they fit.
Overflowing whole rows make one forward traversal based on the current row's
playback time and hold their tail, so paused lyrics cannot reverse with the
metadata clock. Unit-timed reading keeps its actual reading front. Original
source timing, seek behavior, vertical lift and glyph shaping remain.

## Verification

The final frozen source passed **4212 Python tests in 246.139 seconds**, without
skips or Qt callback tracebacks:

```powershell
.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest discover tests -v
```

The final focused playback set passed 367 tests in 12.330 seconds. Actual muted
native Qt checks then passed **101 checks**, sequentially rather than alongside
another Qt test process. Synthetic local WAVs exercise independent music/voice
decoders, actual advancing clocks and lyrics, 20% ducking, one-second gain
restoration, multipart preparation, paused Next, hidden-page provider updates,
and paused/off/no-song auditions. Repeated music pause/resume during an audition
retains source, queue and direct-feed epoch; speech completion/account cancellation
does not flush future paused music. Recorded ordinary and multipart PCM match
the expected mix at every sample (match 1.0, maximum delta 0), with no PCM failure.
These checks privately mute both Qt outputs and use a packet-recording coordinator;
they do not establish physical HomePod/Bluetooth acoustic alignment.

Final control/lifetime checks passed 97 tests at DPR 1 (5.471 seconds) and DPR 2
(5.700 seconds), plus a consecutive DPR 1 repeat (5.303 seconds). Constructor
reentry and incomplete QObject animation state leave native events untouched.
Low-alpha faces compare exact native premultiplied pixels rather than relaxed
RGB tolerances. The existing popup capture/blur contract remains tested. Device
volume uses the real keyboard scrubber path; its mouse name/volume zone retains
the authorized mute behavior. A transition fixture supplies genuinely different
geometry even when a DPR 2 desktop equals its starting window size.

The island/DJ/horizontal/voice UI set passed 150 tests at each DPR (7.284 and
8.013 seconds). The service/parser/actual-child bridge set passed 63 tests in
3.232 seconds after account-bound snapshot reuse. Read-only real production
catalog and four selected-voice requests, genuine audition downloads and local
offline transcription are documented in the verified client contract. No
protected credential bytes changed in those probes.

Native Free/top island and four-voice slider previews at DPR 1/2 were visually
inspected using synthetic covers and song data. They show original island
ambience, independent hollow rings and one original slider face, without an
additional ball or card. Ten-control half-second synthetic hover measurements
recorded P95 5.08 ms / 2.22 ms at DPR 1/2; a settled 120-render sequence needed
no further raster captures or animation timer. This is bounded fixture work,
not whole-application FPS or long-session compositor evidence.

Python compilation, `pip check`, Node bridge syntax, source review and whitespace
checks passed. Version/build files and the previously packaged EXE are unchanged.
Restart this checkout's `launch.bat`; the old running process does not hot-load
the edited modules. Long real-account sessions and physical mixed-device listening
remain QA limits.

Only reviewed code, tests and documentation enter Git. Runtime audio, official
client references, credentials, logs, caches and previews remain ignored. The
existing guarded sync verifies private visibility, sole-owner access, author
identity, disabled Actions/Pages/hooks/keys and unchanged other refs, then pushes
only `codex/owned-playback-sources`. The local and remote `main` remain
`8d937f2ebad8514144b9e0fa3c238b12ededb78e`.
