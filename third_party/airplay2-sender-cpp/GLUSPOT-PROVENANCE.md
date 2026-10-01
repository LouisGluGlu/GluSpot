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
connection. No cryptographic or wire-format code is changed. GluSpot-specific
fake-receiver checks live outside the vendored tree in
`native/airplay_sender/sender_policy_tests.cpp`; the original upstream hash
manifest is retained so these changes can be independently audited.

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
multiroom/PTP. It does not claim verified physical HomePod playback: upstream's
current README distinguishes its prior Qt build hardware verification from
the standalone C++ build, and GluSpot requires its own hardware pass.
