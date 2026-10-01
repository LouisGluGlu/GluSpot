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
- During a cast, display compensation is fully automatic, starting from the
  existing 1.5-second protocol buffer and following valid sender-buffer evidence
  with a bounded slew. Previous manual-delay/automation settings are ignored;
  the old compatibility setters cannot disable automation or save a new delay.
  Compensation never rewrites player position or lyric Seek targets and is not
  measured acoustic latency. No per-song reconnection or microphone is used.
- The chooser removes **更多选项**, the delay slider and automation switch.
  Manual IPv4 discovery remains behind a small **＋** dialog. Four circular
  controls match the user's reference; the linked-ring SVG replaces the former
  output symbol. Supplied SVGs distinguish explicit HomePod, HomePod mini,
  AirPods Pro and speaker kinds; an unknown receiver is not called a HomePod.
- Each connected/controllable device exposes a slim volume slider in its row.
  Windows uses CoreAudio master scalar, set and readback; it does not unmute a
  suppressed cast source. Only the current authenticated receiver's actual
  `/info`/`updateInfo`, volume read or matching command acknowledgement supplies
  HomePod volume. Unknown is unavailable, with no fabricated default. Dragging
  never selects a route. Commands coalesce to the latest value per device,
  old-session commands are fenced, and failures retain the cast and local mute.
- Selected rows use a pale capsule with dark text, and an active AirPlay row
  shows its automatic compensation beside the volume bar. The popup blurs a
  half-size crop of the current music page with album tint and a neutral veil;
  capture omits child widgets to prevent recursive panel images. Material is
  cached, built outside paint, and refreshed at most once per second while
  visible. Hidden panels stop their timers. A long list scrolls at 640 × 360
  and opens with the active/pending device visible; keyboard navigation remains.

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
.\release\GluSpot-6.405\GluSpot.exe --audio-package-check C:\Temp\audio-check.json
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


## 6.404 follow-up: lyric discontinuities and compensation (historical behavior)

The source position, original observation time/value and display offset are
separate. Projection preserves both observation fields. A new event stamp with
the same wrong position cannot confirm a playing seek; neither background-work
latency nor repaint cadence creates independent evidence. Pause transitions and
app/adapter changes discard incompatible candidates. A verified local Seek or a
fresh event from an already-paused source retains its immediate source reanchor,
while the lyric viewport keeps scrolling from its physical pose. Same-song row
or timing enrichment remaps that pose without confirming a pending source jump.
GSMTC natural progress can establish a 5/10-second observation cadence; frozen
positions, marker-only updates and real discontinuities do not extend authority.

During a connected cast, **自动补偿** uses a sender buffer estimate. Every 500 ms
the helper separately converts its fixed RTP protocol buffer at 44.1 kHz and
input queue at the captured sample rate. One clean flowing interval is required;
startup, underrun, capture drop/discontinuity, stale data, stop/error or a previous
session cannot publish valid timing. The controller slews compensation by at most
80 ms per second. Missing evidence holds the current delay. Turning automation
off freezes it for manual adjustment; existing nondefault manual settings stay
manual on migration. Rapid slider/toggle requests keep the latest value.

This estimates sender buffering, not measured end-to-end sound. It excludes
network RTT and receiver-reported latency, which cannot safely be added to the
RTP timeline. No microphone is opened and no per-song reconnection is performed.
HomePod feedback in this path supplies no verified rendered playback timestamp.
Exact physical calibration needs a separate reliable timing measurement; the
manual slider remains available for a persistent audible offset.

The October 1 M6/HomePod 90-second confirmation remains evidence for 6.403.
The 6.404 clock and compensation changes require a new multi-song audible
check; automated fixtures do not establish actual HomePod/lyric alignment.

The final 6.404 Python suite passed 2720 tests in 78.577 seconds. Five native
CTest suites passed during `build-exe.bat`, and the frozen package acceptance
check exited 0. Helper hash, packaged provenance, LGPL source integrity and
replaceable discovery files match the source build. Native Qt fixtures show
intermediate scrolling poses through cast-delay changes, frozen bad positions,
pause and a real paused Seek; the compact menu remains scrollable at 640 × 360.
Python compilation and tracked/new-file whitespace checks passed. The first
full run rejected an annotation fixture whose supplied progress advanced faster
than its simulated clock; its timestamps were corrected, preserving all visual
assertions and the runtime guard, before the final complete run.

The helper SHA-256 is
`8ece8ecc8ab7db216ec79fdef9e272e7374c8954309c83e00c6b7cd2eb38ea50`.
Detailed local evidence is in ignored `logs/lyric-delay-6.404-final-full-tests.log`,
`logs/build-6.404.log`, `logs/audio-package-6.404-check.json` and
`logs/audio-package-6.404-artifact-proof.json`.

## 6.405 follow-up: automatic compensation, frosted chooser and actual volume

The current behavior above supersedes 6.404's manual slider and automation
migration rules. Source-clock continuity and the validated fixed RTP buffer
remain in place. The UI never treats a queued receiver command as confirmation;
a short pending slider pose yields to actual readback on confirmation or timeout.
HomePod reads are bounded, run every five seconds, and preserve actual receiver
information when a read is unsupported. Matching CSeq 200 confirms writes;
rejections and timeouts retain the prior volume. The fake receiver's old CSeq
counter was corrected to echo requests, so delayed replies cannot acknowledge a
newer transaction. See native README and vendored provenance for exact contracts.

New volume read/write handling has not yet been exercised on physical HomePod
hardware. The earlier 90-second 6.403 listening pass does not validate this new
control path or new multi-song lyric alignment. The supplied dual-HomePod fourth
level reference is a future UI direction, not implemented multiroom/fanout.
Native Qt screenshots use explicit fixture devices/volumes. Final 6.405
verification and package evidence are recorded in the canonical README.
