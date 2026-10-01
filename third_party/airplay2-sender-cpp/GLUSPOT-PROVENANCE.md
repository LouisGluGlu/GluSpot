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
statistics every five seconds and once before stop; no audio or key is logged.

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

This import implements only one AirPlay 2 realtime session with HAP transient
pairing. It uses the upstream's NTP timing path, not AirPlay 2 synchronized
multiroom/PTP. Upstream's current README distinguishes its prior Qt build
hardware verification from the standalone C++ build. GluSpot performed its own
90.06-second HomePod mini pass on October 1, 2026, including encrypted event
acknowledgements and Bluetooth M6 mute/restoration; the user confirmed only
HomePod was audible and delay was acceptable. This does not establish long
sessions, other firmware or multiroom compatibility.
