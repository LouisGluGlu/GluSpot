# AirPort Express and HomePod compatibility

The 6.409 work is isolated on `codex/airport-homepod-sync`, based on combined
background/audio commit `1dd926452c50464bdfe912a8dad7dd1bc03d8817`.

## Checked hardware and protocol

The October 4 inventory found HomePod mini `AudioAccessory5,1`, OS `27.0`,
sender protocol `980.77.2`, and AirPort Express `AirPort10,115`, firmware
`p20.78100.3`, protocol `366.0`. Both advertise AirPlay/RAOP on port 7000.
The AirPort features match the pinned OwnTone AirPort example and support
AirPlay 2 transient pairing. Its RAOP `et=0,4` alone does not require a switch
to the old AuthSetup/ANNOUNCE path. Both completed authenticated setup with
the existing realtime sender. No GPL source was incorporated.

AirPort advertises its link-local IPv4 before its normal LAN address. Discovery
now prefers the first valid non-link-local IPv4; link-local-only devices remain
discoverable. Loopback, unspecified, multicast, reserved and malformed
addresses are excluded.

HomePod OS 27 introduces stricter client checks in other implementations:
[OwnTone protocol compatibility evidence](https://github.com/owntone/owntone-server/commit/34603ba998bbbe3f3fbb04c74fde4a0a39e0fd79).
This HomePod accepted `AirPlay/550.10` and three alternative client identifiers
in both RTSP and HTTP read-only `/info` probes. The actual authenticated sender
also succeeded. These observations do not support changing the client string
or Home access permissions as the cause of this reported error.

## Current fault evidence

The same controller chain as the UI reproduced a stop after entering casting:
`同步播放来源输出被另一应用占用`. A concurrent read-only Core Audio inventory
showed only the verified music process and that attempt's native capture helper
on the source endpoint. Exact PID, creation time and full executable path
identified the helper; filename alone cannot authorize an exclusion.

The helper's own session is now allowed only for the current live Popen
identity, checked again on each occupancy observation. Real external,
ambiguous and system-sound sessions must still stop the group. Source capture
remains restricted to the selected music application; no all-system fallback
is added.

## Audio quality repair

The user reported serious electrical noise on HomePod. Capture logs showed no
dropped samples or discontinuities, and shared packets showed no silence
padding; these counters alone did not establish clean audio.

Uncompressed ALAC packets now explicitly carry their 352-frame length rather
than depending on the receiver's decoder cookie to inherit SETUP's `spf=352`.
An independent FFmpeg decoder checked actual authenticated/decrypted sender
payloads sample for sample: 16 combinations cover standalone/group sending,
four synthetic signal profiles and 352/4096-frame cookies. The explicit-length
payload decoded exactly under both cookies; the old implicit-length form
failed under the 4096-frame cookie. This proves the framing compatibility
boundary, not the HomePod's actual internal cookie.

The group's continuous two-point interpolation also produced strong high-
frequency phase-dependent amplitude modulation in independent waveform
simulation. A shared 32-tap normalized Blackman windowed-sinc conditioner now
retains history, fractional phase and bounded lookahead across packet borders.
Real conditioner tests cover 1/8/18 kHz tones, a chirp, stereo separation,
441-frame capture batches, positive/negative clock drift, DC, signed extremes,
clipping and memory limits. All receivers still receive the same final PCM;
the source queue target and network RTP/NTP mapping are preserved.

After the ALAC and conditioner fixes, the user confirmed that the electrical
noise was gone. This listening result belongs to the current hardware and
material; it does not establish clean output for every driver or firmware.

## Local playback timing follow-up

M6 reappeared in the Windows inventory. HomePod mini + AirPort Express + M6
completed a 90.047-second transport/render/restoration check, but the user
reported that M6 played noticeably early. That run is not a passed acoustic
synchronization test. The published `source_frame` is the last submitted local
frame, not the audible playhead; the observed roughly 250 ms difference between
submitted frames and the device clock can include the Bluetooth pipeline and
must not by itself be treated as a measured alignment correction.

The current follow-up adds the declared AirPlay 2 realtime/NTP render lead of
11025 frames (250 ms) to the local presentation deadline and the group's lyric
delay estimate. The network RTP buffer gap remains 66150 frames (1.5 seconds).
This is a protocol policy derived from pinned reference behavior, not a
receiver-reported or microphone-measured acoustic delay; sync flag 7 does not
add another 250 ms. OwnTone's pinned implementation uses a
[network buffer of total delay minus 250 ms](https://github.com/owntone/owntone-server/blob/4fa5dccfb69622f42e77bd05628034a69a567d57/src/outputs/airplay.c#L1207),
[SETUP latencyMin of 11025](https://github.com/owntone/owntone-server/blob/4fa5dccfb69622f42e77bd05628034a69a567d57/src/outputs/airplay.c#L2629),
[the complete total delay for local output](https://github.com/owntone/owntone-server/blob/4fa5dccfb69622f42e77bd05628034a69a567d57/src/outputs/alsa.c#L1170)
and documents the
[render-lead/sync-flag relationship](https://github.com/owntone/owntone-server/blob/4fa5dccfb69622f42e77bd05628034a69a567d57/src/outputs/rtp_common.c#L318).
These are protocol references only; no GPL implementation was copied.

Local diagnostics now retain raw clock position/frequency/QPC, QPC age,
endpoint padding, the pre-write frame count and the chosen queue/presentation
offsets. They contain no PCM. After the render-lead change, the same UI
controller path completed 90.110 seconds with no capture drops,
discontinuities, starvation padding or local underruns, then restored original
endpoint mute, volume, default state and the active music route. During this
run the user confirmed that the three devices were basically synchronized.
144 complete read-only session inventories found only the exact current
helper active on M6; the music application was on the muted spare output,
excluding direct undelayed music leakage in those observations. This is basic
human listening acceptance, not a measured acoustic offset or long-session
drift result.

## Validation and pending acceptance

The pre-fix helper completed HomePod-only 45.094 seconds, AirPort-only 45.093
seconds and a direct two-receiver 25-second probe without transport interruption.
Local endpoint mute, volume and default state matched their initial values.
The 90-second controller pair attempt stopped on the reproduced occupancy
error and restored endpoint state; it is a failed validation, not a passed test.
An additional original single-receiver-path 30.063-second comparison completed
transport and restored endpoint state.

After the startup ownership fix, a 90.047-second controller pair check completed
transport and restored endpoint state. After the audio-quality fixes, a fresh
90.032-second HomePod mini + AirPort Express check also completed transport and
restored state; the user confirmed that the network pair synchronized very
well. A separate attempt ended with a remote FIN after 34.453 seconds; its
cause remains unassigned, so one successful rerun does not close long-session
stability. Another HomePod recheck stopped on an external Windows default-output
change; this is the deliberate source guard, not the old 30-second watchdog.

The final Python suite passed 3015 tests in 89.621 seconds after the render-lead
follow-up; 258 focused tests passed in 9.583 seconds. The preceding
native build passed all 10 suites, including the new waveform quality tests.
The final native rerun after the render-lead change passed all 10 suites in
46.60 seconds, including 25,362 group checks. These
offline passes and independent decoder checks do not replace hardware hearing.
The rebuilt 6.409 frozen package passed capture and group-dispatch acceptance;
its helper and provenance match the native build. Acceptance contacts no
receiver and does not change endpoint mute, volume or output policy.

AirPort's confirmed volume was initially zero; these probes retained receiver
volume. The early transport probes alone could not establish audibility at
that level; the later pair listening confirmation is separate evidence.

Development remains isolated on the compatibility branch. Reviewed 6.409
source/helper changes are integrated into `E:\gpt-island` and the combined
`gpt-island_merged` checkout using baseline guards, complete pre-update
backups and per-file hash verification. The dynamic background, settings,
lyric library and unrelated local changes remain outside this patch. The
existing running 6.408 process must be restarted to load new Python modules. Wider
hardware/topology coverage, measured acoustic offset and long-session recovery
remain acceptance gates beyond the current three-device listening pass.
No microphones or captured music recordings
were used. Temporary probes, raw device inventories and detailed hardware
reports stay in ignored `logs/`.
