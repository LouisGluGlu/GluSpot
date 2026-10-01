# Audio outputs and single HomePod casting

The level 1–2 implementation uses the existing V2 music surface and media
provider boundary. `local/main.py` remains the runnable entry. It does not
implement HomePod multiroom groups or synchronized Bluetooth/AirPlay fanout.

## Product behavior

- A passive background worker enumerates active Windows render endpoints by
  stable ID. Newly available endpoints cause a short, nonactivating connection
  notice; the initial inventory does not announce every existing device.
- The full-screen and windowed music pages have an **输出** button beside the
  existing controls. The chooser lists computer speakers, already connected
  Bluetooth outputs and discovered AirPlay receivers. Bluetooth pairing stays
  in Windows settings. Device kinds come from endpoint properties; Bluetooth
  identification uses the endpoint/PnP container relation, not brand names.
- Selecting a local output changes the Windows console and multimedia defaults,
  then confirms their actual state. The communications default is unchanged.
  External players pinned to a device or using exclusive mode may not migrate.
- Opening the chooser starts IPv4 mDNS discovery for `_airplay._tcp` and
  `_raop._tcp`. Both advertisements merge into one receiver. Manual HomePod IPv4
  entry is available if discovery is blocked; adding an IP does not claim that
  the device connected.
- Selecting one HomePod starts a native helper that captures the current
  Windows output's shared system mix and sends AirPlay 2 realtime audio. This
  includes notifications and other apps using that output. No microphone is
  opened, and protected content excluded by Windows is not bypassed.
- The local output can remain audible: this version neither installs a virtual
  audio driver nor promises that muting a physical endpoint preserves loopback
  capture on every driver. This is an audio cast, not an exclusive replacement
  Windows endpoint.
- Connection, authenticated waiting for source audio, flowing audio, failure and
  stop are separate states. Rapid choices serialize; switching to a local device
  stops the old cast first. A source disconnect or external default-output
  change stops casting with an actionable message rather than silently
  capturing a different output. Retrying is explicit in the chooser.
- **停止投放** and application exit stop/reap the owned helper. No auto-cast
  occurs at startup. Only the UI initiates audio capture/casting.
- During a cast, **歌词延后** adjusts the displayed lyric clock by 0–5 seconds
  (initial value 1.5 seconds), without rewriting the player's position or
  applying the cast delay to lyric Seek commands. This is an adjustable buffer
  estimate, not an acoustic latency measurement.

## Architecture and references

`api/providers.py` exposes immutable `AudioRouteSnapshot` values and queues
actions to `AudioRouteController`. UI paints never enumerate devices, browse
the network, block on COM, or read process pipes. Endpoint polling, discovery
and process ownership stay in background workers. Audio pacing stays in the
native sender, independent of the lyric/animation clock.

| Component | Source / reason |
| --- | --- |
| Windows endpoint backend | Windows Core Audio enumeration/property APIs; default selection uses PolicyConfig, also demonstrated by MIT [AudioDeviceCmdlets](https://github.com/frgnca/AudioDeviceCmdlets). GluSpot uses its own ctypes wrapper and transactional confirmation. |
| Windows PCM acquisition | Shared-mode WASAPI fixed-render-endpoint loopback; GluSpot native capture/format conversion. |
| Single receiver transport | Apache-2.0 [airplay2-sender-cpp](https://github.com/akustikrausch/airplay2-sender-cpp), pinned to `fc913509039412790aa2427558d35178aa7842ea`; provenance and original hashes are in `third_party/airplay2-sender-cpp/`. |
| AirPlay discovery | Unmodified `python-zeroconf` 0.151.5, LGPL-2.1-or-later; the Windows onedir package keeps it replaceable and includes its exact source distribution and notices. |
| Protocol/synchronization references | MIT [pyatv](https://github.com/postlund/pyatv) and [OwnTone's libairptp](https://github.com/owntone/owntone-server/tree/master/src/libairptp). No PTP/group coordinator is included in this level 2 path. |

AirFlash and AirSonic were compared during research. Their GPL/commercial and
noncommercial implementations are not copied into GluSpot. Upstream sender
and cryptographic dependency notices must accompany binaries. The packaging
script verifies the discovery source archive against PyPI's SHA-256 metadata.

## Build and verification

Install `requirements.txt` in `.venv`. Native build additionally requires
CMake 3.20+, a C++20 Windows compiler/SDK, and Ninja for the portable LLVM-MinGW
path. See `native/airplay_sender/README.md`. `build-exe.bat` builds/tests the
helper, prepares discovery source/notices, then packages the entire onedir app.
The spec fails if the native helper or required source/notices are absent.

The source app locates `build/native-airplay/GluSpotAirPlay.exe`; a packaged app
locates `_internal/native/GluSpotAirPlay.exe`. `GLUSPOT_AIRPLAY_ENGINE` is an
explicit development override. The source/helper identity is installation
specific and saved under the GluSpot settings namespace.

Automated coverage includes endpoint rollback/readback, lifecycle races,
source loss, mDNS merge/malformed ads, bounded subprocess events and teardown,
UI state/animation, and output-delay Seek separation. Native suites verify HAP
authentication, encrypted ALAC against a simulated receiver, local sockets and
PCM conversion. A real Windows loopback probe and real HomePod mini mDNS
discovery have passed. These do not establish audible HomePod playback.

Hardware release gates remain: actual HomePod mini authentication and audible
playback, Windows firewall/inbound UDP behavior, Bluetooth switching with
actual players, sustained playback and stop/exit recovery. HomePod access
settings must allow the PC on the same LAN. PIN/password receivers are outside
this transient-authentication path. Do not represent CI tests or a `streaming`
event as a measured acoustic or long-running playback result.
