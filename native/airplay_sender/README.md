# GluSpot AirPlay helper

The helper sends stereo PCM through the vendored AirPlay 2 sender. Exclusive
casting prefers Windows process loopback excluding the helper's own process
tree, capturing all computer application playback across output endpoints.
This is the single-receiver scope. Coordinated groups use verified
music-process-tree capture, as documented below.
Process loopback requires Windows build 20348 or later; the helper dynamically
loads `ActivateAudioInterfaceAsync` and checks actual activation/initialization.
If unavailable, it falls back to shared-mode WASAPI loopback on the selected
render endpoint. Ordinary nonexclusive capture uses that endpoint directly.
The selected endpoint remains fixed for validation, mute ownership and source
guards even in process mode; an external default-output change ends the cast.
No microphone or virtual driver is used. Protected audio that Windows excludes
from loopback is not bypassed.

The single-receiver path uses HAP transient pairing; coordinated groups use
the same authentication per receiver. Receiver SRP
proof checking is strict; a proof mismatch fails the session. PIN/password
devices are not configured by this helper. The sender uses a fixed 66150-frame
RTP timeline offset (1.5 seconds at 44.1 kHz); this is a sender setting, not a
measured receiver minimum or audible end-to-end delay. The helper does not change lyric timing; group mode adds a shared
presentation timeline without claiming acoustic latency measurement.
Connection preserves the receiver's existing volume. The helper never sends
the upstream's automatic 0 dB volume request; a muted HomePod stays muted.
Transient pairing derives both control and event keys from full SRP K; the
reverse event channel decrypts receiver requests and answers an encrypted,
minimal 200 OK. Sending feedback alone does not satisfy the event watchdog.

## Build

Use CMake 3.20+ and a Windows C++20 compiler. An MSVC Developer PowerShell with
the Windows SDK is supported, or a portable LLVM-MinGW x86_64 UCRT toolchain:

```powershell
.\packaging\windows\build-airplay.ps1
.\packaging\windows\build-airplay.ps1 -ToolchainBin 'C:/tools/llvm-mingw/bin'
```

For this workspace, the verified portable toolchain is under ignored
`build/airplay-tools/llvm-mingw-20260922-ucrt-x86_64/bin`. CMake/Ninja were
installed as build tools into `.venv`. The first build fetches only the pinned
Mbed TLS build dependency. Output is `build/native-airplay/GluSpotAirPlay.exe`
and redistribution notices under `build/native-airplay/licenses/`.

The script runs the upstream cryptographic/protocol fake receiver tests, the
real local socket host tests, and GluSpot PCM conversion, capture-signal and
volume-policy tests. None contacts a physical HomePod. Separately, on October 1,
2026, the GluSpot controller and this helper cast to HomePod mini “起居室” for 5.03 seconds
and stopped cleanly; the user confirmed normal audible computer sound.
After fixing transient event acknowledgement and integrating exclusive output,
the controller/helper sustained a 90.06-second HomePod mini session on the M6
source endpoint: local mute remained active, 3 event requests were acknowledged
without decrypt errors, and 44 feedback requests received replies. Stopping
restored the original unmuted state. The user confirmed that only the HomePod
played and the delay was acceptable. The final helper also polls external
unmute at 250 ms; that timing refinement was checked offline, while the 90-second
pass used the same capture/mute/transport path with a 2-second poll.

On October 2, 2026, the repaired process-loopback path sustained a separate
90.032-second Realtek-to-HomePod mini cast on Windows build 26200. Local mute
stayed active, 45 feedback replies and one encrypted event acknowledgement
were received, with no capture drops, discontinuities or decrypt failures.
Stop restored the original unmuted state and unchanged 0.56 output scalar;
the test did not change receiver volume. The user confirmed normal sound with
only HomePod audible. Seven native suites and thirteen actual-helper lifecycle
cases passed, and the 6.406 package's bundled process-capture probe returned
nonzero PCM. This does not certify other Windows builds/drivers or multi-hour
sessions; see the canonical project README for final package evidence.

Run the actual helper's offline lifecycle pass with an active render endpoint:

```powershell
./.venv/Scripts/python.exe native/airplay_sender/test_helper_lifecycle.py
```

It checks invalid arguments, missing endpoint, refused local connection,
partial-handshake stop, stdin EOF, connection timeout and real WASAPI capture.

## Parent process contract

```text
GluSpotAirPlay.exe --host 192.168.1.25 --port 7000 --endpoint {Windows render ID}
                  --device-id {mDNS receiver ID} --name LivingRoom
                  --source-id {persistent MAC-like GluSpot installation ID}
                  [--exclusive-output]
```

The host must be a numeric IPv4 address. The source endpoint must be explicit
and active. stdout is flushed newline-delimited JSON (UTF-8); stderr carries
upstream diagnostic logs. The parent sends a newline-terminated `stop` through
stdin. Closing stdin also stops the session. Ctrl+C is supported interactively.

After `waiting_audio`, the parent may send one newline-delimited JSON command:

```json
{"command":"set_volume","volume":0.35}
```

`volume` is finite and normalized to 0–1. This is an explicit receiver change,
not a Windows capture endpoint change. It reuses the sender's existing
`SET_PARAMETER volume` mapping: nonzero 0–100% maps to -30–0 dB; zero uses
the -144 dB mute sentinel. The capture endpoint stays muted in exclusive mode.
Connecting never supplies a default 50% or implicitly changes receiver volume.
The native stdin drain keeps only the latest level per bounded 4096-byte poll;
the sender keeps one pending read/write transaction plus the newest queued
explicit value. Each volume transaction times out after three seconds without
stopping audio, and late responses are matched by CSeq rather than confirming
a different newer command. Invalid commands are ignored; `stop` and pipe EOF
retain their existing meanings.

Events:

- `connecting`: capture format validated, networking begins.
- `waiting_audio`: authenticated session started; waiting for source packets.
- `streaming`: accepted capture contains nonzero PCM no older than one second,
  and the sender has handed nonzero final PCM to its UDP transport. Capture starts after
  owned local mute is applied in exclusive mode; pre-mute packets cannot alone
  satisfy readiness. This is signal/transport evidence, not receiver audibility.
- `stats`: captured_frames, dropped_samples and discontinuities every 500 ms.
  A bounded `protocol` object also reports feedback sent/replied/rejected,
  event requests/acknowledgements/decrypt failures, event socket/key state,
  audio/silence frames, `nonzero_samples_sent`, queued input frames and the last
  transport error. `nonzero_samples_sent` counts nonzero final PCM only after
  the UDP transport call; queueing a packet alone does not increase it. This
  does not confirm network delivery or decoding by the receiver.
  A final stats event precedes stop. `protocol_latency_frames` is the sender's
  fixed 66150-frame RTP timeline offset (1.5 s at 44.1 kHz);
  `receiver_latency_frames` is only the receiver's reported RECORD value when
  present, not a measurement of audible end-to-end delay. These fields do not
  reduce the receiver's buffer.
  A `timing` object contains `method: protocol_plus_input_queue`,
  `estimated_delay_ms`, `protocol_buffer_ms`, `input_queue_ms` and `valid`.
  The estimate is `protocol_latency_frames / 44100` plus
  `queued_input_frames / input_sample_rate`, including the resampler staging
  queue. It is sender scheduling time, not acoustic calibration or a measured
  receiver playhead. Network RTT and the optional receiver RECORD latency are
  not added: they are different quantities and may overlap this schedule.
  `valid` requires a complete interval with increasing capture/audio counters
  and no new padded silence, dropped capture samples or discontinuities. A
  stopped session, startup sample or non-flowing interval is invalid; the next
  clean flowing interval can recover. The Python owner's `latency_estimate()`
  returns an isolated dict with `seconds`, `source`, `sample_age_seconds`,
  `protocol_buffer_seconds` and `input_queue_seconds`, or `None` for invalid,
  stale (over 2 seconds), stopped, failed or previous-session samples.
  The lyric controller may use this estimate without reconnecting on track
  changes; player/driver timing, actual HomePod output delay and acoustic delay
  remain outside this estimate. Captured SILENT packets still count as flow,
  so `valid` does not assert that a listener can hear audio.
  A `capture_signal` object reports `mode`/`scope` as
  `process_loopback`/`all_other_processes` or
  `endpoint_loopback`/`selected_endpoint`, cumulative `nonzero_samples` accepted
  into the bounded ring, `last_nonzero_age_ms` (null before any signal), and a
  normalized interval `peak`. `meaningful_samples` and
  `last_meaningful_age_ms` separately count samples above one PCM16 LSB;
  `protocol.meaningful_samples_sent` counts these samples after transport
  handoff. A real session-mute probe left +/-1 LSB dither with music suppressed,
  so startup requires fresh meaningful capture and transport evidence. The
  diagnostic nonzero counter alone cannot acknowledge playback.
  A dropped packet does not count as accepted
  signal. No PCM content is retained. The process owner stamps receipt using
  its monotonic clock, overriding any helper-supplied `_received_at`. The route
  controller accepts valid current-session evidence with advancing receipt
  times at most two seconds old. Fresh evidence of three seconds without source
  signal shows
  “已连接 HomePod，暂未检测到电脑声音”; signal recovery clears that status without
  reconnecting, seeking or changing volume. Silence can mean a paused player or
  quiet passage, so the status does not diagnose a driver or receiver failure.
- `error`: message describing initialization, capture, pairing or network failure.
- `stopped`: the sender has requested TEARDOWN and closed its sockets.
- `mute_state`: in exclusive-output mode, `prepared` reports the original
  explicit endpoint mute before networking; `muted` follows authentication;
  `restored` follows checked restoration. Each carries `endpoint_id`,
  `original_muted` and `applied`. The parent must retain this state and restore
  through the endpoint backend after a failed or forced helper termination.
- `receiver_volume`: carries a bounded `receiver_volume` object also included
  in `stats`: `volume` (normalized or null), `known`, `source`, `operation`,
  `pending`, and `last_code`. A current value comes only from `/info`
  `initialVolume`, an authenticated `/command` `updateInfo.value.initialVolume`
  (`receiver_info`), `GET_PARAMETER volume` (`receiver_read`), or a matching
  `SET_PARAMETER` 200 response (`receiver_ack`). Sending a command does not
  confirm it. `operation` is `info`, `read` or `set`; `last_code=0` means no
  response rather than a fabricated HTTP status. Unsupported reads retain any
  existing actual receiver evidence; absent evidence, malformed values and
  timeouts are unknown. Rejected writes retain the previous confirmed value.
  The helper queries the receiver after authentication and every five seconds
  while connected; one unanswered volume request cannot grow the response
  queue. Level reads do not change the receiver or local mute state.

`AirPlayProcess.set_volume(0..1)` is called on the audio controller worker and
returns only pipe/enqueue success. `receiver_volume()` returns the latest
actual read or acknowledged level, or `None` when unknown/disconnected.
`receiver_volume_state()` returns the state above; old helpers without volume
events remain unknown. State is fenced per process/session, and normal errors,
stop or forced termination cannot make an old receiver control a new session.

Exit codes: 0 clean stop, 1 runtime failure, 2 invalid arguments. Connection is
bounded to 20 seconds. Process activation waits at most four seconds, supports
stop cancellation and retains callback state safely if completion arrives late.
Pending stop/stdin EOF is checked before receiver networking begins. The network
poll maximum is 20 ms, capture stop wait is
5 ms, and upstream TEARDOWN flush is bounded to 800 ms. The Python owner should
still enforce its own process timeout and terminate a stuck helper on shutdown.

`--probe-capture [--endpoint ID]` initializes loopback capture for 250 ms and
reports its format/counters without contacting or sending to any network device.
It generates no test audio and saves no captured content. Adding
`--process-loopback` requires process capture and reports its `capture_signal`;
an unavailable API fails that diagnostic rather than silently testing endpoint
fallback. `--process-loopback` is rejected outside a capture probe.
It rejects `--exclusive-output`; a diagnostic capture probe never mutes.

`--exclusive-output` opts into local endpoint mute while casting. Its guard
reads the original state without changing it, then mutes only after the receiver
authenticates and before capture starts. The original mute state is restored on
stop, error, stdin EOF and normal unwinding; the guard never changes master/channel volume or the
default output. External unmute ends the cast instead of repeatedly overriding
the user's hardware controls. The Python owner provides recovery if the helper
is killed before its guard can run. Only the selected endpoint is muted:
applications pinned to another output can still be audible on that output even
though process capture forwards their playback. Endpoint-mute loopback behavior
depends on the driver. M6 retained PCM while muted; the user's Realtek endpoint
returned all-zero PCM. A process-loopback probe on that Realtek setup retained
nonzero PCM while mute was confirmed and restored the prior mute without changing
volume. This capture result does not itself establish HomePod playback.

Windows firewall must permit inbound UDP for the helper; the receiver initiates
timing/retransmission packets. The helper does not change firewall rules.

## Synchronized group mode (development)

`--group` dispatches the same executable to the shared music-output host.
It requires `--music-pid`, `--music-created-at` (Windows process creation time)
and `--source-endpoint`. Each receiver is explicit and starts with
`--receiver-id`, followed by `--host`, `--port`, `--device-id`, and `--name`.
Repeat `--local-endpoint` once for each Windows/Bluetooth member. Every local
output must be distinct from the suppressed source and from every other local
output, with the original opaque endpoint ID retained. A group accepts at least
two Windows outputs without AirPlay, or the existing AirPlay membership, up to
eight total playback outputs. Invalid arguments are rejected before Windows state or
receiver networking is touched.

The host uses one verified INCLUDE process-tree capture, an all-SETUP barrier,
one future steady/NTP/RTP origin and one final 352-frame stereo packet for all
receivers. A shared bounded rate correction keeps capture-clock drift from
exhausting the 150 ms queue; every receiver and the local history sees that
same conditioned PCM. Each local WASAPI renderer reads the history by frame index
and uses its own endpoint clock for continuous bounded drift correction. All
renderers arm against the same presentation epoch; their reads never consume
the shared history or another renderer’s cursor. It does
not consume the capture ring or invent a Bluetooth acoustic delay.

Without network receivers, the same capture, source mute guard, conditioned PCM
history and local renderer barrier run against one steady-clock epoch. This
path retains the existing 77175-frame (1.75 s) presentation reserve; reducing it
to 250 ms would not cover the supported WASAPI render queues. The reserve is a
scheduling budget, not measured Bluetooth delay. No socket, authentication or
RTP sender is created. A separate available source endpoint remains required;
the application routes verified music to an unoccupied spare when its current
endpoint is a playback member, and restores that route on stop/failure.

Pure-local stats identify `group.method: shared_pcm_local_clock`, total
`group.members`, zero `group.airplay_members`, and
`timing.method: windows_shared_pcm_clock`. Their timing base is
`presentation_buffer_ms`, not a fictitious protocol buffer. Network counters
remain zero; streaming and valid estimates require meaningful recent capture
and every local renderer's actual PCM evidence. Independent Bluetooth radios,
codec buffers and device DSP still require physical synchronization checks.

Group stats append named `members`, `group.local_endpoints`, and a
`local_renders` array whose entries include `endpoint_id` and per-output clock,
PCM and underrun evidence. Exactly one local output also retains the legacy
`group.local_endpoint` and `local_render` aliases; multiple outputs never
misidentify the first renderer as the whole group. Receiver volume commands use
`{"command":"set_volume","receiver":0,"volume":0.35}`, where the integer
index belongs to this helper's fixed receiver order. A group is declared
streaming only after meaningful music reaches every network member and the
selected local renderers. A failing renderer stops all local workers before
source suppression is restored. Any member failure, source identity loss, capture
overrun or missed presentation deadline stops the whole group and restores
owned source mute. Source suppression and application routing limitations,
physical verification gates and protocol references are recorded in
[`docs/audio-group-sync.md`](../../docs/audio-group-sync.md).

`test_group_helper_lifecycle.py --idle-test-process` exercises the actual
helper with a verified silent process and local fake pairing sockets. It
does not authenticate a receiver, mute an endpoint, save PCM or contact a
physical receiver. The native group suite independently decrypts two fake
receivers to verify identical final PCM and presentation mappings; it also
checks history bounds and continuous correction under simulated clock drift.

## Owned-player direct PCM

The source app can use the same group coordinator without process loopback:
`--group --pcm-pipe GluSpotPCM-<32 lowercase hex> --pcm-parent-pid <PID>
--pcm-parent-created-at <creation token> --pcm-epoch <positive epoch>` plus
one to eight Windows/AirPlay output arguments. Process capture arguments are
forbidden in this mode. The original capture mode retains its separate guards.

The helper creates a local, first-instance-only inbound pipe and verifies the
connected client's exact PID against a retained process handle/creation token.
The client also verifies the helper identity. Each message uses a 24-byte
little-endian `<4sQQI>` header: `GPCM`, epoch, sequence starting at one, frames.
Payload is 44.1 kHz stereo PCM16; frames must be 1..16384 and sequence contiguous.
EOF, wrong identity/header or bounded-ring overflow terminates the session.
The ring's power-of-two storage holds at most 65536 stereo frames (about 1.486 s).
Overlapped connection/read cancellation is drained before releasing its storage.

Direct input skips source-endpoint and endpoint-mute guards entirely; the Qt
bridge controls only its own audio output mute. It keeps the shared conditioner,
history, receiver authentication and local presentation clocks unchanged. Stats
identify `capture_signal.mode="direct_pcm"`, scope `owned-player`, and
`group.pcm_epoch`. See [source-app behavior and evidence](../../docs/owned-playback-refinements.md).

## Verification limits

The buffer conversion accepts float32/64, PCM8/16/24/32 and up to eight labelled
speaker channels, keeps centre audio in a surround downmix, and drops bounded
packets if a network consumer stalls. A silent source may not generate loopback
packets; authenticated `waiting_audio` is distinct from flowing audio. There is
no claim that endpoint master-volume mute preserves captured audio on all
drivers. Process capture avoids the reproduced Realtek mute suppression; the
fallback endpoint path can still be affected on systems where process activation
is unavailable. Raising receiver volume does not solve missing source PCM.
Other HomePod firmware/access
settings and networks, sustained playback, sleep/reconnect and audible latency
still need hardware verification before a release.
Receiver volume read, write and external-change handling were verified with
the cryptographic fake receiver and actual helper command grammar only. The
new control path has not been exercised on a physical HomePod; the earlier
90-second audio pass does not verify these new volume controls.
