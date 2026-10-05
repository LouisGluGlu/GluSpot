# Bluetooth early-playback investigation

The user reported that Bluetooth plays significantly early while the wired
output and HomePod remain aligned in 6.411. Work is isolated on
`codex/bluetooth-presentation-fix`, starting from `0742070`. The audible
problem is not yet reproduced or fixed by this investigation.

## Evidence

The running app is the delivered 6.411 executable. Its native helper matches
the verified 6.411 build hash
`bbd8419e7dd2c0cb9466a2446b9c542a6bd3687c14c099be79607a51facc0fac`.
M6 is correctly identified as Bluetooth. During read-only inspection there
was no active cast helper and the music application's sessions were inactive.
That snapshot cannot establish whether direct music leaked during the reported
failure. Existing controller migration requires the verified music sessions to
reach the spare output, and excludes every selected playback endpoint from
source candidates.

Two separate seven-second silence-only probes exercised the existing
production renderer against M6 and Realtek. They connected no AirPlay receiver,
captured no input, saved no PCM, and performed no route, default, volume or mute
operations. Both reported valid endpoint clocks and zero meaningful samples.
M6's final write queue was about 39.8 ms with 20 ms padding; Realtek's was about
40.1 ms with the same padding. Their reported maximum stream latency was zero.
These measurements describe silence at the Windows endpoint, not Bluetooth
acoustic alignment during real music.

The preceding 6.409 music run reported about 239.5 ms M6 queue with 20 ms
padding and zero maximum stream latency. The user confirmed basic alignment
in that historical run. Its difference from today's silent probe does not
identify an acoustic correction and is not reused as a fixed offset.

## Clock contract and next verification

The scheduler derives the first newly written frame's presentation time from
the stream-relative position divided by its paired frequency, the pre-write
frame count, and the correlated 100 ns QPC timestamp. The queue delay is reduced
by the timestamp age. Adding maximum stream latency to a valid device-clock
estimate would count a pipeline again. Invalid or initial observations currently
fall back to padding plus reported maximum latency; the transition between
those estimates also needs live evidence.

The existing calculation is exposed to offline tests without changing its
production policy. Tests can now cover frequency units, QPC age, driver queue
versus padding, initial zero position, invalid observations and fallback
transitions instead of testing only a precomputed desired cursor.

Four diagnostics exported during the reported issue are now available. In the
6.411 onedir app it is written beneath `_internal/logs/`. Relevant evidence is
the selected members, source process/output, complete named `local_renders`
and current timing. A playback listening check remains necessary after a
specific correction; neither a renderer phase near zero nor the silent probe
proves speakers are acoustically synchronized.

The final native rebuild passed all 11 CTest suites in 47.57 seconds, including
the new direct estimator cases in the existing music-audio suite. Whitespace
checking passed. No Python code, scheduling policy or packaged executable was
changed by that first investigation commit, and no acoustic synchronization pass is claimed.

## Authorized acoustic measurement and optional endpoint holds

The first authorized measurement used the explicit Pro-9500 microphone; it
never opened the M6 headset microphone. The microphone's 48 kHz stereo packets
had valid contiguous device positions and QPC timestamps, with a maximum
clock-fit residual of about 0.151 ms. The native measurement tool reused the
production renderer and authenticated coordinated HomePod sender. It emitted
five low-amplitude coded pulses sequentially to Pro-9500, M6 and HomePod.
Transport counters confirmed every probe reached each output without a local
underrun. Source sample-zero epochs were reported against QPC, rather than
using process launch time. Recording stayed in memory and was discarded;
only scalar timing, confidence and restoration evidence were saved in ignored
logs. Every endpoint volume/mute and both default-output roles were restored.

The first probe's room-distorted pulse correlations were ambiguous and failed
the confidence gate. No delay from that run was accepted, persisted or applied.
The separate investigation tool now uses distinct band-limited pseudorandom
waveforms, retaining the same rejection thresholds. Its 23 synthetic checks
passed, including severe low-pass filtering, reflections, missing pulses and
cross-pulse rejection. The second physical run also had valid microphone timing
(maximum residual about 0.109 ms) but failed the acoustic confidence gate for
all three outputs. M6 had a stable candidate sequence, while the wired and
HomePod references were too weak to compare reliably. No compensation profile
has been written. The original volume/mute/default state was confirmed restored
after both runs, and no sender or microphone worker remained running.
Microphone clock linearity does not rule out internal microphone DSP delay;
relative measurements share that delay, while speaker-to-microphone distance
still affects their interpretation.

The source adds optional `--local-compensation-frames`, bound immediately to
its preceding `--local-endpoint`. The native renderer shifts only that output's
initial presentation epoch; it does not change the shared AirPlay/lyric epoch,
device-clock estimator, or a running cursor. Default zero preserves the existing
behavior. The 0–22050-frame range and combined three-second history budget are
validated before arming; exceeding the budget stops every output through the
existing lifecycle. Named diagnostic evidence reports the actual extra delay.

Python reads an optional scalar profile at group startup. Matching requires the
exact complete device membership, Windows build, presentation policy and current
Windows endpoint audio-format/driver fingerprints. A stale, unavailable or
malformed profile leaves compensation at zero. The settings UI does not gain a
manual delay control, and ordinary playback never opens a microphone. A profile
is not written until a reliable repeated measurement and acoustic replay have
verified it. The current release helper is preserved while that gate is pending.

The fingerprint reads `IAudioClient::GetMixFormat` without calling Initialize
or Start, then hashes the full bounded format, endpoint container and sorted
present-driver identities/versions/providers. An effects-override property read
with S_OK and VT_EMPTY records the explicit raw state `unset`; it is never
interpreted as effects enabled or disabled. A missing/failed/ill-typed required
read invalidates the profile. Both actual outputs yielded repeatable fingerprints
without changing volume, mute or defaults. This evidence cannot detect every
speaker DSP setting, physical placement or later buffering change; a saved
calibration still requires acoustic acceptance and is not a universal latency.

The current microphone arrangement has not established the relative acoustic
offset or its sign. A microphone placement that clearly hears all speakers, or
a separate microphone without the speaker's own echo-cancellation processing,
is required before another physical measurement. Echo cancellation is a possible
explanation, not a diagnosed cause. The reported early M6 playback remains an
open hardware acceptance issue; software phase agreement and transport counters
do not certify the audible result.

Final source verification passed 3097 Python tests in 92.599 seconds
(`logs/bluetooth-calibration-python-final.log`) and all 11 native CTest suites in
49.82 seconds (`logs/native-local-compensation-final-build.log`). Python
compilation and whitespace checks passed. The development helper SHA-256 is
`198601542a257c41638fc8009121d5830b3421e8af16e3a6f767442ed91e3827`;
the release helper still has the original verified 6.411 hash. The process
checklist score is 100 for this scoped implementation/investigation, with the
unresolved acoustic acceptance gate explicitly retained. This is not a claim
that the user's Bluetooth synchronization goal is complete.

Reference interfaces:

- [IAudioClock::GetPosition](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nf-audioclient-iaudioclock-getposition)
- [IAudioClient::GetStreamLatency](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nf-audioclient-iaudioclient-getstreamlatency)
- [IAudioClock2::GetDevicePosition](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nf-audioclient-iaudioclock2-getdeviceposition)

Raw Clock2 hardware frames can use a different sampling rate from the client.
They cannot be subtracted directly from 44.1 kHz submitted frames and do not
establish the remote Bluetooth speaker's acoustic position.
