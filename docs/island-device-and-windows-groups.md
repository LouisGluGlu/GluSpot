# Island device notices and Windows-only groups

This change is isolated on `codex/island-device-battery`, based on `e1f9e52`.
The executable version remains 6.411; existing packages do not acquire source
changes automatically.

## Island presentation

Connection/switch events use the V2 island instead of a separate toast. Validated
battery evidence produces a left accessory glyph, the live lyric viewport in
the center and a green 0–100% ring on the right. Without battery evidence, the
glyph and accessory name appear. A smooth return starts five real seconds after
the event, without pausing or rewriting the music position or lyric clock.

The existing frame timer and shell springs handle transitions. Repeated metadata
does not restart the lifetime. Fast arrivals blend from the displayed pose;
hiding or opening a music window clears the transient. Expanding retains the
user's expanded layout while the notice expires. Message/reminder priority is
preserved. Compact idle/plugin islands also use their existing main shell.
Tasker stays inside its selected zone and truncates names where necessary.

Ink is selected from the existing background-only raster, including album/white
transitions. No screenshots or device discovery happen during paint. Existing
per-line lyric opacity is composited during entrance/exit so it cannot defeat
the notice fade.

## Identification and battery

The user's `Airpods.svg` is preserved as `assets/audio_outputs/airpods.fill.svg`.
Standard AirPods and Pro have distinct kinds/assets; Max and generic headsets
retain the headphone fallback. Explicit model matching accepts Chinese prefixes
without the former Unicode word-boundary failure. Remembered PnP/Find My nodes
do not establish playback availability; an active CoreAudio render endpoint is
still required.

Battery is optional, read through cfgmgr32 on the existing device worker.
Physical remote Bluetooth containers establish identity; profile battery
properties can only join the exact container. A Bluetooth serial service in the
computer's container cannot classify Realtek as Bluetooth or supply its battery.
Invalid, conflicting or stale evidence remains unknown. Provider/UI validate
an integer 0–100, source and at most 30 seconds since reading the driver cache.
That timestamp is **not** the accessory's actual measurement time.

October 8 read-only evidence: M6 exposes a 90% driver cache, Air4 a 100% cache.
AirPods Pro later appeared as an active output and was correctly classified,
but exposed no driver battery. It uses the name layout. No BLE scanner or
guessed nearby-AirPods battery association is included. HomePods have no ring.

## Pure Windows / Bluetooth groups

Multiple Windows outputs can use the existing shared music capture/independent
WASAPI renderer group without AirPlay. Two Bluetooth outputs or Bluetooth/wired
combinations need independent active Windows render endpoints. Groups capture
only the verified music application process tree; a single Windows selection
remains the ordinary computer-wide output switch.

PCM history, presentation epoch, per-endpoint device clocks, drift correction,
mute ownership, source routing and stop/error restoration are reused. Pure-local
groups report `windows_shared_pcm_clock`; AirPlay retains protocol timing. The
local-only history/startup reserve retains the supported 1.75-second schedule.
It is a scheduling budget, not a measured acoustic or Bluetooth correction.

The combined budget remains eight playback sinks. Eight Bluetooth endpoints is
a software ceiling, not measured adapter capacity. Concurrent A2DP streams
depend on adapters, drivers and accessories; codec/DSP/wireless queues can cause
audible offsets even when renderer clocks agree.

A suitable unselected capture output remains required. If the music source is
selected, the controller routes it to a spare and restores it after stop.
Selecting every available output without a spare fails explicitly. This change
does not install a virtual driver or widen groups to all computer audio.

## Verification

Tests cover battery typing/identity, glyphs, five-second motion, actual background
contrast, live lyric continuity, Tasker geometry, lifecycle, source fences,
membership, pure-local timing and stop/error restoration. Native checks exercise
the reused clock/history/renderer without physical receivers. Final counts are
recorded in README after verification. Native Qt previews live in ignored logs.
Physical two-Bluetooth acoustic alignment and a new EXE are not claimed by the
source/offline checks.

Final source verification: 3239 Python tests and 11 native suites passed in the
isolated branch. Scoped hunks were applied to `E:/gpt-island`; five files were
adapted against its existing interfaces to avoid importing unfinished acoustic
calibration code. Its 3210 Python tests and all 11 native suites passed after an
independent native rebuild. The rebuilt helper was installed at the source
runtime path, with SHA256
`D358B1F9D4D06F7F45334C72BBB2592FD7CDF4C05BD1AC43738557ED7ADD999A`.
The final read-only device snapshot exposed only active Realtek; two independent
Bluetooth endpoints were unavailable for an audible check. No audio, microphone
or Bluetooth connection was changed by this validation. The packaged 6.411
application remains unchanged.
