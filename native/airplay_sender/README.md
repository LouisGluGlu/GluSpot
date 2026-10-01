# GluSpot AirPlay helper

The helper captures one fixed Windows render endpoint through shared-mode
WASAPI loopback and sends stereo PCM through the vendored AirPlay 2 sender.
Capture stays on that endpoint when Windows' default device changes. This
captures the endpoint's system mix, not only the active GSMTC music app.
Protected audio that Windows excludes from loopback is not bypassed.

Only a single HomePod with HAP transient pairing is supported. Receiver SRP
proof checking is strict; a proof mismatch fails the session. PIN/password
devices are not configured by this helper. The sender uses a fixed 66150-frame
RTP timeline offset (1.5 seconds at 44.1 kHz); this is a sender setting, not a
measured receiver minimum or audible end-to-end delay. The helper does not
change lyric timing or offer synchronized multiroom.
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
real local socket host tests, and GluSpot PCM conversion and volume-policy
tests. None contacts a physical HomePod. Separately, on October 1, 2026, the
GluSpot controller and this helper cast to HomePod mini “起居室” for 5.03 seconds
and stopped cleanly; the user confirmed normal audible computer sound.
After fixing transient event acknowledgement and integrating exclusive output,
the controller/helper sustained a 90.06-second HomePod mini session on the M6
source endpoint: local mute remained active, 3 event requests were acknowledged
without decrypt errors, and 44 feedback requests received replies. Stopping
restored the original unmuted state. The user confirmed that only the HomePod
played and the delay was acceptable. The final helper also polls external
unmute at 250 ms; that timing refinement was checked offline, while the 90-second
pass used the same capture/mute/transport path with a 2-second poll.

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

Events:

- `connecting`: capture format validated, networking begins.
- `waiting_audio`: authenticated session started; waiting for source packets.
- `streaming`: at least one real captured packet entered the sender ring.
- `stats`: captured_frames, dropped_samples and discontinuities every 5 seconds.
  A bounded `protocol` object also reports feedback sent/replied/rejected,
  event requests/acknowledgements/decrypt failures, event socket/key state,
  audio/silence frames, queued input frames and the last transport error.
  A final stats event precedes stop. `protocol_latency_frames` is the sender's
  fixed 66150-frame RTP timeline offset (1.5 s at 44.1 kHz);
  `receiver_latency_frames` is only the receiver's reported RECORD value when
  present, not a measurement of audible end-to-end delay. These fields do not
  adjust lyric timing or reduce the receiver's buffer.
- `error`: message describing initialization, capture, pairing or network failure.
- `stopped`: the sender has requested TEARDOWN and closed its sockets.
- `mute_state`: in exclusive-output mode, `prepared` reports the original
  explicit endpoint mute before networking; `muted` follows authentication;
  `restored` follows checked restoration. Each carries `endpoint_id`,
  `original_muted` and `applied`. The parent must retain this state and restore
  through the endpoint backend after a failed or forced helper termination.

Exit codes: 0 clean stop, 1 runtime failure, 2 invalid arguments. Connection is
bounded to 20 seconds. The network poll maximum is 20 ms, capture stop wait is
5 ms, and upstream TEARDOWN flush is bounded to 800 ms. The Python owner should
still enforce its own process timeout and terminate a stuck helper on shutdown.

`--probe-capture [--endpoint ID]` initializes loopback capture for 250 ms and
reports its format/counters without contacting or sending to any network device.
It generates no test audio and saves no captured content.
It rejects `--exclusive-output`; a diagnostic capture probe never mutes.

`--exclusive-output` opts into local endpoint mute while casting. Its guard
reads the original state without changing it, then mutes only after the receiver
authenticates. The original mute state is restored on stop, error, stdin EOF
and normal unwinding; the guard never changes master/channel volume or the
default output. External unmute ends the cast instead of repeatedly overriding
the user's hardware controls. The Python owner provides recovery if the helper
is killed before its guard can run. Endpoint-mute loopback behavior depends on
the audio driver: the current M6 endpoint was verified to retain loopback PCM
while muted, but that result is not a claim for every Windows endpoint.

Windows firewall must permit inbound UDP for the helper; the receiver initiates
timing/retransmission packets. The helper does not change firewall rules.

## Verification limits

The buffer conversion accepts float32/64, PCM8/16/24/32 and up to eight labelled
speaker channels, keeps centre audio in a surround downmix, and drops bounded
packets if a network consumer stalls. A silent source may not generate loopback
packets; authenticated `waiting_audio` is distinct from flowing audio. There is
no claim that endpoint master-volume mute preserves captured audio on all
drivers. A driver that suppresses loopback after mute needs a different capture
path; raising receiver volume does not solve it. Other HomePod firmware/access
settings and networks, sustained playback, sleep/reconnect and audible latency
still need hardware verification before a release.
