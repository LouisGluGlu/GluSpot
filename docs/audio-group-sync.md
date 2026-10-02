# Synchronized music outputs: levels 3 and 4

This work continues from the verified 6.406 single-HomePod repair on branch
`codex/audio-homepod-level3-4`, in the existing isolated audio checkout. The
6.407 development package passed frozen acceptance. Multi-device implementation
is available for testing; full levels 3–4 acceptance remains in progress.

## Required behavior and verification

- Multiple independent HomePods receive the same music PCM frame with the
  same target playback time. Independent simultaneous sender processes do not
  satisfy this requirement. Stereo-paired HomePods require a separate leader
  and topology check; two independent room receivers do not prove that case.
- A selected Windows/Bluetooth output receives those same PCM frames delayed
  to the group presentation timeline, with bounded device-clock correction.
  WASAPI timing is endpoint scheduling evidence, not a measurement of wireless
  codec delay or HomePod acoustic output. Listening verification is required.
- Capture includes only the selected Windows media application's verified
  process tree. Failure or ambiguity must not fall back to system-wide sound.
- Device selection, per-device volume and stop remain in the existing frosted
  chooser. Confirmed snapshots own selection and volume; enqueue success is
  not a receiver acknowledgement. Lyrics follow the group's display delay,
  without changing source playback position or issuing automatic seeks.
- Capture, queued PCM and receiver histories stay bounded. Cancellation,
  errors, source exit, receiver loss and shutdown stop/reap owned resources
  and restore owned source suppression without rewriting volumes.

## Protocol basis

The existing Apache-2.0 realtime sender is extended with coordinated mode.
All receivers finish authentication/SETUP before one group barrier establishes
a monotonic origin, NTP origin and presentation offset. One capture produces
each 352-frame stereo PCM block once. Each receiver independently encrypts
that block while preserving the same RTP-to-NTP mapping. Receiver timing
replies use the shared NTP origin plus monotonic elapsed time, avoiding later
wall-clock steps. Feedback and encrypted event acknowledgements remain active
while the group waits at the barrier.

The checked reference is [OwnTone commit
4fa5dccfb69622f42e77bd05628034a69a567d57](https://github.com/owntone/owntone-server/blob/4fa5dccfb69622f42e77bd05628034a69a567d57/src/outputs/airplay.c): its master session and packet/sync
distribution share RTP position and timestamps across realtime receivers;
NTP and PTP setup paths are distinct. This is protocol evidence, not copied
GPL implementation. The standalone `src/libairptp` has its own MIT license,
but no PTP code is included in this path. The pinned Apache sender core and
local changes remain documented in its provenance file.

## Music-source and suppression evidence

GSMTC supplies an application identity, not a process ID. Core Audio sessions
identify actual playback PIDs. Exact executable path/basename or packaged
AUMID matching, ancestry and process creation times select one live root.
Foreground-window PID and translated display names cannot authorize capture.
Native capture holds and verifies that process handle and uses Windows
`INCLUDE_TARGET_PROCESS_TREE`; the original all-other-process capture remains
only in the legacy single-receiver path.

A read-only live probe resolved `cloudmusic.exe` to PID 24084 with creation
token 134353908200167471 and an active Realtek render session. The separately
authorized short session-mute test showed music signal suppression: baseline
peak 0.310608 fell to 0.0000305176 (one PCM16 LSB) under session mute and
returned to 0.31189 after restoration. Session volume remained 0.45. Nonzero
dither alone cannot establish that music survived; session mute is therefore
not used as a transparent interception path.

The implementation retains owned source-endpoint mute and captures the verified
music process tree before endpoint gain. A delayed local renderer uses a
different, unmuted endpoint. The controller checks all active source-endpoint
owners, including system sounds and ambiguous sessions. If music already uses
the selected Bluetooth output, or another application shares its source output,
it temporarily routes only the verified music application to an unoccupied
spare physical output. With no usable spare, it reports an error and starts no
sender. Occupancy and owned app policies are checked during playback; a conflict
stops the group and restores owned state. There is no virtual driver or
all-computer fallback. This check cannot make endpoint mute per-application;
an unrelated app arriving between worker checks may be briefly suppressed
before the conflict stops the group.

An application-route transaction now reads the checked Windows internal
AudioPolicyConfig ABI, preserves console/multimedia policies separately and
holds exact music-process identities. It journals write intent before changing
the app's preferred endpoint, then requires fresh Core Audio sessions to prove
the stream actually moved to an otherwise unoccupied spare output. It changes
neither the Windows default nor communications policy. Group capture remains
music-only. Unsupported routing, ambiguous owners, an occupied spare or missing
live migration must fail rather than broaden capture.

The first live route probe exposed a distinction between policy and playback:
clearing both persisted overrides returned successful empty policy readback,
while the active music stream remained on Realtek. Restoration therefore also
rebinds owned default-following roles to their original effective endpoint,
checks the actual music sessions there, then clears their original empty
policies. A separate live cleanup verified this sequence back to M6. The
journal must retain failed recovery rather than report success from policy
readback alone. Recovery on app restart and externally changed routes still
needs hardware verification.

## Available hardware and remaining gates

The user currently has one HomePod mini and an M6 Bluetooth speaker. This
permits real mixed-output listening and lifecycle tests. A second physical
HomePod is required for actual multi-HomePod listening/drift verification.
The two cryptographic fake receivers validate identical decoded PCM and its
presentation mapping, not acoustic synchronization or every receiver firmware.
On 2026-10-02, the shared host completed a 15.047-second check and a
90.078-second music-only HomePod mini + M6 check. The longer check automatically
moved NetEase's evidenced stream from M6 to Realtek, suppressed Realtek and
rendered the same shared history to M6. It logged no capture drops,
discontinuities or local history underruns, and 45 receiver feedback replies.
After helper shutdown, native mute was restored, the music session was observed
back on M6, both app policies again followed Windows defaults, and every
endpoint's default/mute/volume state matched its initial state. Evidence is in
ignored `logs/mixed-group-15s-realtek.json`, `logs/mixed-group-90s.json` and
`logs/music-m6-effective-restore.json`. These checks establish transport, local
rendering and restoration; the user's simultaneous listening assessment is
still pending. Endpoint-clock phase is not Bluetooth acoustic-delay proof.
Multi-hour drift, pause/resume, source replacement, topology changes, device
reconnection and stereo-pair behavior remain to be checked. A new audio child
process is not silently added to the fixed routing transaction; changed owners
stop the group and require a new device selection.

## Development package verification

The 6.407 source passed 2910 discovered Python tests in 72.055 seconds, Python
compilation and whitespace checks. All nine native CTest suites passed; the
group suite passed 14756 checks, including two decrypted fake receivers,
shared-history bounds, continuous source-clock correction and per-member
feedback blackholes. Thirteen actual helper lifecycle cases use only local
sockets and no authenticated receiver, leaving endpoint mute/volume unchanged.
An unresponsive member's feedback deadline stops the entire group; sending
packets and encrypted event traffic alone do not establish receiver health.

`release/GluSpot-6.407/GluSpot.exe --audio-package-check <report>` passed in the
frozen, windowless application. It imported the three new group/source/routing
modules without constructing audio resources, checked packaged group dispatch,
browsed devices and ran a no-receiver capture probe. External helper overrides
cannot certify a frozen bundle. Packaged helper hashes, provenance, version
resources and all supplied output SVGs match the build inputs. This acceptance
does not start a Qt window or prove physical synchronized playback. Group
graceful shutdown accounts for all receivers and owned routing restoration;
the controller's close wait is bounded to 17.5 seconds for a group.

Temporary references, generated test data and hardware logs stay in ignored
`build/` and `logs/`. No microphone capture or recorded PCM is used by the
diagnostic probes.
