# Output selection and connected device status

The subsequent [connection-motion change](output-connection-motion.md) refines
the representative ordering and pairing rules below: earliest two distinct
confirmed kinds, with overlap only for the HomePod/HomePod mini pair.

This source change is isolated on `codex/output-device-status`, starting from
`544aba8`. It follows the user's October 8 circular-button reference without
changing audio routing, native scheduling or the 6.411 release package.

## Selection and glyphs

The chooser places provider-confirmed members or pending replacement targets
before unselected devices. Both buckets retain provider order. A capture or
default endpoint is never implicitly added to a group. Existing row objects,
keyboard focus and volume requests survive sorting; a held volume slider delays
row movement until release. Keyboard traversal follows the resulting row order.

The full-screen and windowed surfaces share the same circular output control.
One confirmed device uses its explicit HomePod, HomePod mini or AirPods Pro
glyph, or a distinct generic headphones, speaker, AirPlay or unknown-device
glyph. A homogeneous group shows one glyph and its total count. A mixed group
shows up to two canonical representative glyphs above the total count; HomePod
and HomePod mini overlap when both are present. AirPort Express is a generic
AirPlay receiver, not a HomePod. A count includes all playback kinds, not just
the two representatives, and excludes the capture source and duplicate IDs.

The active control has a pale face and dark glyph; the open chooser tints it
blue. An unconfirmed route keeps the original output link. Pending targets do
not claim connected playback. Original SVG opacity and aspect ratio are
preserved. Raster compositions and tints use bounded caches, including at
high DPI. No paint path reads providers or infers a device model from a name.

A visible-only 200 ms timer reads the provider's immutable cached snapshot and
updates just the control region when kinds or count change. It stops while
hidden and at shutdown, reconnects on explicit reopen, and refreshes before
the floating-island entry bitmap is captured. Unrelated background and lyric
animation code is unchanged.

## Capacity and evidence

| Playback topology | Current software limit |
| --- | --- |
| Bluetooth only | One selected output; pure Bluetooth multi-output sync is unsupported |
| Independent HomePod / HomePod mini receivers | Eight AirPlay outputs |
| AirPlay mixed with wired / Bluetooth outputs | Eight playback outputs total, at least one AirPlay |
| Windows / Bluetooth members in an AirPlay group | At most seven, within the same total cap |

The Python `AirPlayGroupProcess.MAX_SINKS` and native group validation both
enforce eight total playback sinks. A local wired speaker counts toward that
limit. When Windows playback endpoints are selected, the group also requires
an unselected, available and unoccupied source endpoint, normally non-Bluetooth;
that capture endpoint does not count as a playback sink. No virtual endpoint
is installed automatically.

Seven Bluetooth outputs is a software endpoint budget, not an assurance that
a Windows adapter can maintain seven concurrent A2DP streams. Each must be
exposed as an independent active render endpoint. Multiple Bluetooth devices,
eight HomePods, HomePod stereo pairs and the complete two-HomePod plus wired
plus Bluetooth topology have not been verified on hardware. Previous listening
checks covered HomePod mini + AirPort Express + M6; later M6 early playback
remains unresolved. This UI change does not claim to fix acoustic synchronization.

## Verification

Focused Qt tests cover provider-confirmed/pending sorting, capture-source
exclusion, preserved focus/volume dragging, tab order, model/generic glyphs,
count accuracy, overlap alpha, bounded caches, high DPI, visibility polling,
explicit reopen and refreshed entry snapshots. Actual-size control strips and
larger glyph fixtures were rendered and visually inspected. Fixtures and test
logs remain under ignored `logs/`; no hardware output is opened by this UI QA.

The final isolated source passed all 3124 discovered Python tests. Only the
reviewed output UI, associated tests and documentation were integrated into
`E:/gpt-island` after verifying those target files still matched the baseline;
unrelated background/lyrics work and unfinished calibration changes were not
copied. The integrated directory passed 101 focused checks and its full 3095
test baseline. No new executable was built and the existing 6.411 package
remains unchanged.
