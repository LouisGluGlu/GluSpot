# Vendored AirPlay 2 sender core

Upstream: <https://github.com/akustikrausch/airplay2-sender-cpp>

Pinned commit: `fc913509039412790aa2427558d35178aa7842ea`
(2026-09-05). Imported on 2026-10-01.

Scope: `src/`, `test/`, `third_party/ed25519/`, `licenses/`, `CMakeLists.txt`,
`LICENSE`, `NOTICE`, `README.md`, `ROADMAP.md`, `SECURITY.md`. The CLI demo,
Git metadata, CI configuration, release artifacts and runtime files are omitted.
`GLUSPOT-UPSTREAM-SHA256.json` records the original imported files' SHA-256
hashes. This provenance file and that manifest are GluSpot additions.

Local modification: `test/loop_tests.cpp` uses the malformed AF_INET hostname
`invalid:host` instead of `no-such-host.invalid` for the resolution-failure
test. The workspace's DNS resolver returned a wildcard address for `.invalid`,
which made the original assertion environmental rather than deterministic.

Local modification: `src/raop_sender.h` and `src/raop_sender.cpp` add the opt-in
`setPreserveReceiverVolume(bool)` flag, defaulting to false to preserve upstream
behavior. GluSpot opts in, suppressing the implicit AirPlay 2 `SET_PARAMETER
volume: 0.000000` at session launch. Explicit `setVolume()` still works. Without
this change, the upstream sender can change a HomePod's current volume at
connection. GluSpot-specific
fake-receiver checks live outside the vendored tree in
`native/airplay_sender/sender_policy_tests.cpp`; the original upstream hash
manifest is retained so these changes can be independently audited.

Local modification (2026-10-01): `src/raop_sender.cpp` derives the transient
pairing event-channel keys from the **full 64-byte SRP session key** using the
existing HKDF primitive and `Events-Salt`/`Events-Write|Read-Encryption-Key`
labels. The reverse event connection uses Write for incoming requests and Read
for outgoing acknowledgements, matching the existing PIN pair-verify path.
Previously transient events were silently drained because their keys were
never set; the receiver's 25–30 second event watchdog could then close a session
despite valid `/feedback` requests. Protocol references checked:

- pyatv `hap_transient.py`, blob `635d6d42c765a655dc13ffb0a5dc4300808afeef`:
  <https://github.com/postlund/pyatv/blob/master/pyatv/protocols/airplay/auth/hap_transient.py>
  (`encryption_keys` uses the full SRP shared secret for HKDF).
- pyatv `ap2_session.py`, blob `aa5f1408f11d91a8be7545e9f706f28d2cde1424`:
  <https://github.com/postlund/pyatv/blob/master/pyatv/protocols/airplay/ap2_session.py>
  (event keys are reversed; feedback interval is 2 seconds).
- The pinned sender's README, section 6, documents the event acknowledgement
  requirement and 25–30 second disconnect symptom.

The existing HKDF, SRP, AEAD and framing primitives are unchanged. The old
transient test's "unkeyed channel" comment/check label in `test/core_tests.cpp`
is corrected: invalid event ciphertext is still dropped without a reply.
GluSpot's separate regression drives a cryptographic transient receiver through
65 seconds, replying to 13 encrypted event pushes and 32 feedbacks; it failed
at the 30-second simulated watchdog before the fix.

`src/raop_sender.h/.cpp` also expose bounded session diagnostics (feedback,
event acknowledgement, audio/silence and input backlog counters, a 512-character
last error, and separate RTP timeline versus reported receiver latency).
Only one unanswered feedback occupies the method FIFO; later timer firings are
counted rather than appended without limit. Diagnostics do not change receiver
volume or the existing 66150-frame RTP latency. Native helper JSON emits these
statistics every 500 ms and once before stop; no audio or key is logged.

Local modification (2026-10-02): `src/raop_sender.h/.cpp` expose actual
receiver-volume evidence in the existing diagnostics. The helper's explicit
slider reuses `setVolume()` and its original -30..0 dB / -144 mute mapping.
`requestReceiverVolume()` uses a read-only `GET_PARAMETER` text request. Missing
or unsupported volume has no invented default. `/info.initialVolume` and the
authenticated event channel's `updateInfo.value.initialVolume` are also read
when present; source units/type/range must pass validation. An explicit write
is confirmed only by its matching `SET_PARAMETER` 200 response, and failed
writes retain the prior confirmed state. One volume read/write may be pending,
with one latest explicit slider value coalesced; a three-second timeout retires
the transaction, and CSeq-based response matching ignores delayed acknowledgements
instead of applying them to newer commands. Volume protocol failures do not
stop the cast or affect the capture endpoint mute guard. The pure protocol
fixtures cover read, missing/malformed level, capability/event values, receiver
mute, write acknowledgement/rejection, coalescing, timeout and late response.

Protocol references checked, used as interface evidence without source copying:

- OpenAirPlay receiver pinned `6c343d3679ddb561c61566985acaaf587d0a3bd3`,
  `ap2-receiver.py:636` documents the read-only `GET_PARAMETER` volume text
  response: <https://github.com/openairplay/airplay2-receiver/blob/6c343d3679ddb561c61566985acaaf587d0a3bd3/ap2-receiver.py>.
- pyatv protocol notes pinned `b277a4c8222ecdcbaab8a24e3e713ca44765adb4`
  show an authenticated `updateInfo` with `initialVolume: -20.0`:
  <https://github.com/postlund/pyatv/blob/b277a4c8222ecdcbaab8a24e3e713ca44765adb4/docs/documentation/protocols.md>.

`test/loop_tests.cpp` now echoes the request's CSeq as RTSP requires. Its old
independent counter started at one while the sender started at zero; prior
FIFO-only matching masked that fixture error. This is separate from its
existing deterministic malformed-hostname change. The original imported hash
manifest remains untouched. The sender's preserved-volume default, pairing
crypto, audio payload and fixed 66150-frame timeline remain unchanged.

Local modification (2026-10-02): `src/raop_sender.h/.cpp` add the cumulative
`nonzeroSamplesSent` diagnostic. It counts nonzero samples in the final PCM
after resampling and silence padding, then accumulates after the audio datagram
is handed to `RaopIo::udpSend`. The counter saturates at `uint64_t` maximum and
resets through the existing session diagnostics reset. It stores no PCM and
does not change encoding, encryption, volume or packet timing. Empty-ring
padding and actual zero-valued input never prove a sound-bearing send; input
whose tiny impulse quantizes to zero during resampling also remains zero.
GluSpot's cryptographic AP2 receiver tests independently decrypt the emitted
audio and compare its actual nonzero sample count with this diagnostic,
including silence and session restart. This proves transport handoff, not
network delivery or receiver audibility.

The Apache-2.0 license and NOTICE apply to the upstream sender core. The
transport includes MIT-licensed pyatv attribution, preserved in
`licenses/THIRD-PARTY-NOTICES.txt`. The vendored ed25519 primitive retains its
zlib license and SOURCE.md. CMake fetches Mbed TLS 3.6.0 at the upstream's pinned
commit `2ca6c285a0dd3f33982dd57299012dacab1ff206`; its source and generated
libraries remain under the ignored build directory. The Windows build script
copies the applicable license texts beside the built helper for packaging.
This pinned crypto dependency should be reviewed before any public release.

GluSpot's own WASAPI capture, PCM conversion and helper process protocol live
in `native/airplay_sender/`. No AirFlash GPL code or AirSonic code is included.

The original import implements one AirPlay 2 realtime session with HAP transient
pairing, using the upstream's NTP timing path. Upstream's current README distinguishes its prior Qt build
hardware verification from the standalone C++ build. GluSpot performed its own
90.06-second HomePod mini pass on October 1, 2026, including encrypted event
acknowledgements and Bluetooth M6 mute/restoration; the user confirmed only
HomePod was audible and delay was acceptable. This does not establish long
sessions, other firmware or multiroom compatibility.

Local modification (2026-10-02, group playback work): `src/raop_sender.h/.cpp`
add an explicit coordinated mode. Authentication and stream SETUP still produce
the existing `launched` readiness callback, while the sender awaits the host's
group barrier without emitting its own PCM, padded silence or sync. Feedback,
encrypted event acknowledgement and explicit receiver-volume requests stay live.
The host arms each ready receiver with the **same** steady-clock frame-zero time,
64-bit NTP anchor and negotiated presentation lead, then supplies the same final
352-frame / 44.1-kHz stereo PCM packet and contiguous source frame index to each
member. Encoding, per-session encryption/nonces, retransmission and signal
diagnostics reuse the existing sender implementation. The coordinated core does
not consume its input ring or independently resample a source.

Audio RTP timestamps and periodic sync retain the shared source-frame-to-NTP
mapping. Once armed, NTP timing replies derive from that anchor plus steady-clock
elapsed time, so subsequent Windows wall-clock adjustment cannot move a group's
timeline. Before arming, timing replies use the existing system NTP clock; the
host must derive the shared anchor from that same NTP epoch. Sending before frame
zero, malformed PCM, a duplicate/skipped source index and PCM already past its
presentation deadline are rejected without advancing sequence, nonce or frame
counters. A bounded late host tick may still catch up before that deadline.
Stop/restart clears the arm and source frame index. The ordinary sender's ring,
resampler, NTP path, pairing and preserved-volume behavior remain unchanged.

Protocol evidence checked against pinned sources, without copying GPL code:

- OwnTone `4fa5dccfb69622f42e77bd05628034a69a567d57`, `src/outputs/airplay.c`:
  `master_session_make` reuses a master by audio quality/timing mode;
  `packets_send` distributes the same master RTP source packet to its members;
  `packets_sync_send` distributes the same source position/time mapping;
  `payload_make_setup_session_ntp` and `payload_make_setup_session_ptp` demonstrate
  that realtime type 96 supports both clocks. Buffered type 103 is not required
  merely to coordinate independent receivers:
  <https://github.com/owntone/owntone-server/blob/4fa5dccfb69622f42e77bd05628034a69a567d57/src/outputs/airplay.c>.
- MIT pyatv `b277a4c8222ecdcbaab8a24e3e713ca44765adb4`,
  `pyatv/protocols/raop/protocols/airplayv2.py` advertises NTP timing, realtime
  type 96, and the 11025..88200-frame latency range; `stream_client.py`
  documents the RTP/sync relationship already implemented by the imported sender:
  <https://github.com/postlund/pyatv/blob/b277a4c8222ecdcbaab8a24e3e713ca44765adb4/pyatv/protocols/raop/protocols/airplayv2.py>
  and
  <https://github.com/postlund/pyatv/blob/b277a4c8222ecdcbaab8a24e3e713ca44765adb4/pyatv/protocols/raop/stream_client.py>.

GluSpot's separate `native/airplay_sender/group_sender_tests.cpp` authenticates
two cryptographic fake receivers at different times, independently decrypts their
different-key ALAC streams and compares their identical PCM/RTP/NTP presentation
mapping. It covers future start barriers, malformed/duplicate/skipped/late packet
rejection, monotonic timing replies, retransmission, session reset and feedback/
event maintenance while setup-ready. Packet-arrival coincidence alone is never
used to assert synchronization. This establishes the coordinated wire contract,
not acoustic alignment of physical HomePods, Bluetooth transport latency, or
Apple Home stereo-pair/group-leader support. Those require separate device
evidence. No PTP service or receiver acoustic feedback is fabricated here.

The additional `meaningfulSamplesSent` counter counts final PCM16 samples whose
absolute value is greater than one, saturating after the same UDP handoff as
`nonzeroSamplesSent`. A Windows music-session mute probe suppressed the actual
music while leaving +/-1 LSB mixer dither, so nonzero samples alone are too weak
to certify flowing music. The original nonzero counter is retained as separate
diagnostic evidence. Cryptographic fake receivers independently decode a dither
packet (nonzero but no meaningful samples), then meaningful positive/negative
samples, and verify exact cumulative counts and restart isolation. These are
signal thresholds, not acoustic or receiver playback proof.

GluSpot's own group host and shared source-clock conditioner live outside this
vendored tree in `native/airplay_sender/group_main.cpp/.h`. The host uses one
verified music-process capture, one bounded final-PCM history, one shared small
rate correction for capture-clock drift, and independent encrypted sessions.
All output members see the same conditioned source-frame indices. No OwnTone,
libairptp or other GPL source was incorporated by this group implementation.

The group host separately guards receiver liveness from setup readiness onward.
A receiver must advance its successful authenticated feedback count within ten
steady-clock seconds. Rejected replies, outgoing packets, skipped feedback and
encrypted event acknowledgements cannot refresh that deadline. One failed
member stops the complete group, including while other members are still
pairing. This does not alter the ordinary sender's feedback policy. The group's
cryptographic fake tests preserve a healthy 65-second ready barrier, then cover
both unarmed and armed receivers that keep TCP/event traffic open while feedback
replies are blackholed, plus repeatedly rejected feedback. These tests validate
bounded session-loss detection; they do not establish receiver acoustic output.
