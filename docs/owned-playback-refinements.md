# Owned playback, navigation and monochromatic artwork

Date: 2026-10-10. Source branch: `codex/owned-playback-sources`.
Product version remains `6.412`; the packaged executable is unchanged.

## Visible behavior

The song page has a persistent music-library return button using the supplied
`音乐库.svg`. Click or Escape returns to the existing account catalog/search,
preserving playback and scroll state. Escape first dismisses an open device
panel. System-capture navigation keeps its existing destination.

The owned-player controls are ordered device, playback mode, translation,
window/island. The supplied SVGs represent sequential, random and single-song
modes. Clicking cycles through those three modes, and the choice persists as
`v2_owned_playback_mode`. Sequential playback loops the entire list, as confirmed
by the user. Single-song mode repeats at EOF while manual next/previous remain
available. Random mode plans actual upcoming songs and follows played history
when going back. Personal FM keeps requesting fresh batches; random mode shuffles
the unplayed batch instead of cycling through old recommendations.

The visible beta/gold badge is removed. Gold reading, word timing, lyric lift,
translation and the existing gold ripple remain available through the existing
reading settings. The ripple supplies the visual distinction.

Bright monochromatic covers use a separate display treatment: a dark tinted
base, middle tone, soft light area and smaller stronger hue area. The algorithm
recognizes the sampled hue family and its coverage; it does not rewrite the
original cover, extracted pigments or Solid island color. Multicolored, neutral,
low-chroma and dark covers retain the established rendering path. Real red and
cyan reference-cover crops pass the actual palette interface and produce four
display layers. Their dominant family coverage is 99.595% and 99.807%.

## Cover preparation

`OwnedMusicPlayer` prepares the actual next three songs' artwork and keeps the
previous three with the current cover. Cache keys include source, song identity
and artwork URL. The cache holds at most seven unique covers and 32 MiB; two
background artwork workers prioritize the current song. Metadata remains in the
existing queue, and cached artwork/colors appear immediately when switching.
Random-mode planning and personal-FM prefetch follow that same window. Stream
URLs are authorized afresh; decoded audio is not prefetched.

## Program audio outputs

`PlaybackSourceProvider` owns the route boundary. Device discovery reuses
`WindowsAudioDevices` and `AirPlayDiscovery`. A single Windows device is assigned
to the player's own `QAudioOutput`; it does not change the Windows default.
Multiple Windows outputs and one or more AirPlay outputs use the existing native
shared PCM coordinator. The device panel places status after the list, limits
visible error height and retains complete messages in tooltips/accessibility.

`OwnedAudioBridge` obtains 44.1 kHz stereo PCM16 with `QAudioBufferOutput` and
keeps Qt as the playback clock. That optional API is feature-detected: older
Qt versions keep direct single-Windows playback and report an unavailable direct
group capability. The native helper verifies the pipe client's PID and process
creation token; the Python client verifies the helper's live identity. Pipes
reject remote clients and duplicate server instances. Each packet has a strict
24-byte little-endian `GPCM`/epoch/sequence/frame-count header and at most 16384
frames. Queues are bounded; malformed, stale or overflowing input terminates the
session. Overlapped I/O is cancelled and drained before its storage is released.

Direct PCM does not capture a process tree, migrate application sessions, mute a
Windows endpoint or change the system default. It temporarily mutes only its own
Qt output while forwarding. Stop, failure and shutdown restore that private mute.
Track changes and seek discontinuities stop the old timeline and start a fresh
epoch. External process capture keeps its original process/source/mute guards.

## Evidence and limits

The helper was rebuilt in this checkout. All 12 native CTest suites passed in
49.84 seconds, including actual child-process pipe identity, fragmented frames,
bounded input and cancellation at connect/header/payload stages. Existing
multi-receiver shared-PCM tests still pass.

A real Qt-to-pipe-to-native-Windows-render smoke passed 17 lifecycle checks in
9.219 seconds: four epochs covering backward seek, new source, paused forward
seek and resume, each with actual meaningful render/streaming evidence. It sent
93 packets / 1399780 PCM bytes to an already silent endpoint. The system default,
endpoint volume/mute and the player's final private mute matched their initial
state. No real account or network receiver was used in this test.

Native previews cover red/cyan/yellow/purple, multiple/neutral/dark palettes,
short windows, DPI 1/2, playback modes and library navigation. Focused tests cover
queue history/cache limits, stale work, UI navigation, mode persistence and
output lifetime. Final full-suite evidence is recorded in README.

Generated reports/screenshots stay under ignored `.runtime/`; build artifacts
and logs remain ignored. Reproduce with this checkout's Python environment:

```powershell
.\.venv\Scripts\python.exe tools\smoke_owned_playback.py
.\.venv\Scripts\python.exe tools\smoke_music_library.py
.\.venv\Scripts\python.exe tools\smoke_owned_audio_bridge.py
.\.venv\Scripts\python.exe tools\smoke_owned_native_pcm.py
.\.venv\Scripts\python.exe tools\preview_single_hue_background.py
.\.venv\Scripts\python.exe tools\preview_music_navigation.py
.\.venv\Scripts\python.exe -X faulthandler -m unittest discover tests -v
```

The native smoke only runs against an endpoint that is already silent. The
available evidence does not establish physical multi-device acoustic alignment,
Bluetooth/HomePod long-session drift, every platform's account permissions or a
new frozen-package acceptance. Restart this branch's source app to load the
changes; a previously packaged executable does not contain them.
