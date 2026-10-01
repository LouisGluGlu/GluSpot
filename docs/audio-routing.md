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
- After HomePod authenticates, the native helper mutes the selected local render
  endpoint. Stop, errors and stdin EOF restore its original mute; volume and
  default-output identity are unchanged. The parent retains per-session mute
  ownership for recovery after abnormal helper termination. A prepared session
  has no right to undo a user's mute, and a failed retry cannot inherit old
  ownership. External unmute ends the cast, with a 250 ms polling interval.
  This is a cast with local suppression, not a virtual Windows output device.
  Driver behavior matters: M6 was verified to retain real PCM while muted;
  drivers that suppress loopback on mute require a different capture path.
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
- The compact menu uses the music page's cached background and font, neutral
  device rows and existing control treatment. **更多选项** folds manual IP,
  lyric delay and the explanatory hint. At 640 × 360 the main casting controls
  remain visible; expanded options scroll, with keyboard access retained.

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

The explicit package acceptance command runs before normal GUI/single-instance
startup and writes only the requested JSON report:

```powershell
.\release\GluSpot-6.403\GluSpot.exe --audio-package-check C:\Temp\audio-check.json
```

It imports packaged dependencies, checks application module availability,
enumerates render endpoints, browses mDNS for two seconds and probes the native
helper's local capture for 250 ms. It does not access user settings, change the
default output, save audio or start a receiver session. Application modules are
checked with `find_spec`, separately from actual dependency imports. It does
not create a QApplication or validate window-platform startup; full desktop QA
remains separate. Exit codes are 0 (passed), 1 (failed checks in the report) and
2 (arguments or report-writing failure).

Automated coverage includes endpoint rollback/readback, lifecycle races,
source loss, mDNS merge/malformed ads, bounded subprocess events and teardown,
UI state/animation, and output-delay Seek separation. Native suites verify HAP
authentication, encrypted ALAC against a simulated receiver, local sockets and
PCM conversion and receiver-volume preservation. Four native test suites and
seven actual-helper lifecycle checks passed. On October 1, 2026, the real
controller discovered HomePod mini “起居室” (AudioAccessory5,1), authenticated,
cast the computer's current sound for 5.03 seconds, then stopped and closed.
The user confirmed: “听到了，声音正常”. This validates audible single-receiver
playback on that LAN, not measured latency or sustained compatibility.

Screen-lifetime regressions reproduce invalidation of PySide6's shared QScreen
wrapper with QWidget.screen() on a transient window. The notice and music
surface now query the application's screen without that transient ownership;
the music surface resolves its current monitor before the primary fallback.
The final complete Python suite passed 2648 tests in 96.093 seconds. Both
screen-lifetime regressions fail under their respective old QWidget queries.
Python compilation, tracked/new-file whitespace checks and native Qt panel
inspection passed, including scrolling controls at the 640 × 360 minimum.

## 6.403 follow-up: duplicate playback and 30-second disconnect

The 30-second failure was reproduced by a simulated receiver watchdog.
Transient SRP authentication derived control keys but omitted the reversed
event-channel keys, so encrypted HomePod events were discarded. The repair
derives them from the full 64-byte SRP key, using the same HKDF labels/direction
as the existing PIN path and pyatv. The regression fails at 30 seconds under
the old implementation and passes 65 simulated seconds with 13 event replies,
32 feedback replies and continued audio. Feedback method tracking is bounded
when a receiver stops answering.

On October 1, 2026, the real controller used Bluetooth speaker M6 as its fixed
capture source and cast to HomePod mini “起居室” for 90.06 seconds. All three
real encrypted event requests were acknowledged, all 44 feedbacks returned
200, there were no decrypt errors or dropped capture samples, and input
queue samples remained roughly 14–24 ms rather than accumulating. M6 was
muted throughout and restored to its original unmuted state on stop. The user
confirmed: “只有 HomePod，延迟可接受”. No microphone or acoustic latency
measurement was used. The earlier short local probe also confirmed nonzero
PCM while M6's mute readback was true, with volume unchanged.

The 1.5-second RTP timeline setting and adjustable lyric delay are retained.
They are separate from captured-input queue depth and a receiver-reported
latency, and are not a universal acoustic delay measurement. The follow-up
does not add an unverified lower-buffer mode. The final helper differs from
the physical-test helper only in checking external unmute every 250 ms rather
than two seconds; the final binary passed the native/offline lifecycle checks.

Subprocess diagnostics retain bounded per-session transport messages, final
stats/error, event replies and mute ownership. Fake-child regressions cover
failed retry, timeout/kill, pipe cleanup failures, no-UI-poll teardown and
preserving a user's mute during a session that only reached preparation.
Recovery requires recorded ownership: forcibly killing in the tiny interval
between SetMute and its ownership event, killing parent and helper together,
or unplugging the source can leave Windows mute needing manual restoration.
This is not an unconditional crash-recovery guarantee.

Hardware release gates remain: Bluetooth switching with actual players,
sustained playback, sleep/reconnect, other firmware/access settings and networks,
and acoustic latency measurement. HomePod access settings must allow the PC on
the same LAN. PIN/password receivers are outside this transient-authentication
path. CI tests and a `streaming` event alone are not audible-playback evidence.
