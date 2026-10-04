# Multiple Windows outputs alongside AirPlay

This backend extension starts from 6.410 commit `4c4c8fd` on the isolated
`codex/multiple-windows-outputs` branch. It implements the requested topology:
two independent HomePods, one wired/local speaker and one Bluetooth output.
The total cap remains eight playback outputs, with at least one AirPlay
receiver. Local-only multi-output groups and HomePod stereo-pair topology
are outside this extension.

## Capture and playback

One verified music process tree is captured once. The coordinator conditions
each final PCM block once and appends it to bounded shared history. Network
receivers receive that block with their own encryption and a shared RTP/NTP
mapping. Each selected Windows endpoint has a separate `LocalAudioRenderer`,
device clock, playback cursor and bounded drift correction. They read the
same history without consuming it, and arm against the same presentation epoch
and delay. Windows clock scheduling is not a measured Bluetooth acoustic delay.

The existing source-endpoint mute prevents direct, early music playback.
Consequently, no playback output may be the source. If music is on any selected
Windows output, the existing application-route transaction moves it to an
available, unoccupied, unselected non-Bluetooth endpoint before casting.
Every selected Windows output is excluded from the spare candidates.

This topology needs an independent source endpoint. If a computer has only
the wired speaker and Bluetooth endpoint and both are selected, there is no
usable spare. An active HDMI or USB output can supply it when ownership and
actual music-stream migration are verified. No virtual driver is installed,
no output is implicitly unchecked, and no system-wide capture fallback is
used. Per-application session mute previously removed meaningful captured
music, so it is not reintroduced as an unverified workaround.

The existing application recovery journal still uses one playback endpoint as
its active witness. Routing and restoration operate on the exact music process,
original policies and spare output; they never write the witness output's
volume/default/mute. Controller and native validation fence every playback
endpoint before the source can be suppressed. No journal schema migration is
needed.

## Lifecycle and diagnostic contract

Repeated `--local-endpoint` arguments carry explicit endpoints. Source/output
collisions and duplicate local outputs use the existing ASCII comparison rule
without parsing or rewriting the opaque Windows ID. The native parser and
Python boundary both enforce the eight-output total.

All local renderers must prepare and arm. A failure stops the entire group;
all local stop signals are issued before network teardown or worker joins, and every
renderer is reaped before owned source mute and application routes are restored.
All local renderers must report ready, armed, playing and meaningful PCM before
streaming or a valid delay estimate can be reported. A silent or failed second
renderer cannot be certified by the first one's signal.

Native stats include `group.local_endpoints` and named `local_renders` entries.
Single-local aliases remain only when exactly one endpoint exists. Python
retains only the complete selected endpoint list with validated boolean,
counter and finite clock fields. Missing, duplicated or unknown local evidence
invalidates the delay sample. Stopped-session diagnostics are historical;
terminal state prevents their use as current latency.

The current chooser design is retained for the user's later UI reference.
Its existing checkbox/provider contract now adds a Windows member instead of
replacing another Windows member. Existing per-device volume setters and
readback remain independent, and multi-device capture stays music-only.

## Verification

Public controller regressions cover the four-member sequence, second-local
source conflicts, exclusion of every output from spare candidates, unavailable
spares, independent volumes, membership changes, teardown/restoration order,
either Windows output disconnecting and mixed eight/nine-output limits.

Native tests use two independently encrypted fake AirPlay receivers. Both
decoded streams and two overlapping Windows-history reads match the same final
PCM. A separate production renderer-collection fixture exercises initialization,
arm, runtime failure and stop fanout. Its two offline clocks drift in opposite
directions over 60 simulated seconds and maintain independent corrected
cursors. These tests open no physical output stream and establish algorithm
and lifecycle behavior, not audible four-device alignment.

The bundled-helper acceptance check passes two local endpoints with an
intentional second-output/source collision. The new parser accepts the repeated
flag and rejects that collision before any Windows work or receiver contact.
An older one-renderer helper fails this compatibility check even when ordinary
group dispatch works.

Physical two-HomePod plus wired/Bluetooth playback, actual spare migration,
speaker-specific acoustic offsets, restart recovery and long-session drift
remain acceptance gates. The preceding HomePod mini + AirPort Express + M6
listening pass is separate evidence and is not claimed as verification of
the new two-Windows-output topology.

Final source verification passed 3068 discovered Python tests in 100.804
seconds, dependency checking, Python compilation and whitespace checks. The
rebuilt native helper passed all 11 CTest suites in a final 49.92-second rerun, including
the new multi-renderer lifecycle suite. The 6.411 frozen package passed
dependency/device/capture/group dispatch and multiple-output parser acceptance.
Its native helper matches the source-build SHA-256
`bbd8419e7dd2c0cb9466a2446b9c542a6bd3687c14c099be79607a51facc0fac`.
These checks do not start a physical receiver session or prove four-device
audibility. Version metadata is aligned in branding, spec, Windows resources,
build script and README.
