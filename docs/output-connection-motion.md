# Device arrival motion and Bluetooth feasibility

The October 8 follow-up is isolated on `codex/output-connection-motion`, based
on `23a2129`. It changes window/full-screen presentation and notification
dispatch. Audio transport, latency scheduling and the 6.411 package are unchanged.

## Window and full-screen motion

A confirmed selection or newly available device introduces its own glyph in
the existing output control. The old glyph shrinks; the new glyph grows from
small dark grey into blue, overshoots its normal size by at most 12%, and settles
inside the circular face. The control then expands left to reveal the device
name, holds, and collapses back to the confirmed group glyphs and count. The
other controls retain their positions. An available endpoint's arrival does
not count it as a playing member. Opening the chooser settles the notice so
the selected rows and normal blue open-state control remain immediately usable.

`OutputConnectionMotion` owns a bounded queue and reuses the existing bounded
simulation clock. Paint consumes an immutable pose without advancing time or
reading devices. New confirmations queue without resetting the visible motion.
Queued introductions begin from the last displayed group; removals blend back
from the current pose. Bitmap transitions pause the clock, and hiding settles
the animation. Direct reopening baselines the current route instead of replaying
events from the hidden interval. A visible inactive window can show a new
connection without activating or raising itself. Only the capsule region is
invalidated; per-frame poses do not fill the whole control-row raster cache.

The membership tracker retains confirmed connection order across device-list
sorting and volume updates. The earliest two distinct kinds represent a group;
same-kind additions increase count without a duplicate glyph. Later kinds do
not replace these representatives. Only a HomePod plus HomePod mini pair uses
the dedicated overlapping layout, irrespective of their attachment order.
Other two-kind combinations use ordered left/right slots. Mini, mini 2, then
HomePod therefore produces the special stack and count 3; headphones, HomePod,
then mini retains headphones/HomePod and count 3. Removing members rederives
the earliest remaining kinds. Pending sender restarts hold the previous
confirmed badge until success/error and never count targets as playing.

The route event contract now includes an optional `event_device_id`, appended
for positional compatibility. Arrival, local switch and single AirPlay
confirmation publish the exact endpoint; a group-wide event clears it. Legacy
name-only snapshots resolve a model only when the name is unique. The app
dispatches visible window/full-screen events into this control and closes the
old global toast. Minimized/temporarily hidden active music surfaces consume
events without opening a separate popup. Island mode keeps its existing notice
while the proposed island-specific presentation is discussed with the user.

## Bluetooth-only groups

Playing to multiple Bluetooth outputs without HomePod is technically feasible.
The existing shared music PCM, per-Windows-output renderer, device clock and
drift correction can be reused. Current controller, Python helper boundary and
native group all require AirPlay, and native presentation policy is read from
the first network sender. A proper extension needs a transport-independent
group identity/lifecycle, local presentation epoch, startup confirmation,
failure restoration and lyric-delay estimate; merely deleting the minimum
receiver check is insufficient.

The current source-mute design still requires an available unselected capture
endpoint, e.g. wired/HDMI/USB when two Bluetooth endpoints are selected. Selecting
every real Windows output leaves no source. A virtual driver would be a separate
and more involved capture route. Two active Bluetooth render endpoints also
depend on the adapter, drivers and headphones; the eight-sink software budget
is not a measured number of concurrent A2DP streams.

Simultaneous output is more achievable than tight acoustic synchronization.
Codec queues, wireless transport and device DSP each add different latency.
Windows clock alignment cannot establish actual acoustic alignment; the M6
early-playback issue remains open. A future Bluetooth-only extension should
start with two devices and verify startup, drift, reconnect and long playback.

Windows also offers [Shared audio for compatible LE Audio hardware](https://blogs.windows.com/windows-insider/2025/10/31/extending-bluetooth-le-audio-on-windows-11-with-shared-audio-preview/).
Ordinary BLE or Bluetooth 5.x support does not establish LE Audio compatibility;
see [Microsoft's hardware guidance](https://support.microsoft.com/en-gb/windows/hardware/bluetooth/check-if-a-windows-11-device-supports-bluetooth-low-energy-audio?nochrome=true).
This is a separate system route, not evidence that M6 or the user's AirPods
support it.

## Battery evidence and island proposal

Windows can read some headphones through HFP battery indicators or BLE Battery
Service, depending on device and driver support. See [Microsoft battery profile
guidance](https://learn.microsoft.com/en-us/windows-hardware/design/accessory-guidelines/bluetooth-accessory-guidelines-battery-profiles-services).
A read-only query on this computer found a driver-exposed M6 battery cache of
90%; present AirPods nodes did not expose the same property. The cache is not
proof of a fresh measurement, and no playback, microphone or scan was started.

AirPods have another possible path: [Windows BLE advertisement watcher](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.advertisement.bluetoothleadvertisementwatcher)
can receive Apple manufacturer advertisements. [AirPodsDesktop's inspected
parser](https://github.com/SpriteOvO/AirPodsDesktop/blob/56b04001f75044dbd15507303a9438c71e93c6e4/Source/Core/AppleCP.cpp#L103)
and [battery conversion](https://github.com/SpriteOvO/AirPodsDesktop/blob/56b04001f75044dbd15507303a9438c71e93c6e4/Source/Core/AirPodsAdvertisement.cpp#L61)
show left/right/case values in 10% increments for that broadcast path.
Values depend on broadcasts, model/state and correct identity matching;
randomized addresses and nearby other AirPods cannot be assigned to the active
output by guesswork. No code from that project is copied into GluSpot here.

The proposed island design uses a device glyph on the left and a battery ring
on the right only when a reliable, fresh reading exists; unavailable battery
falls back to device name and connection state. Mains-powered HomePods do not
get a battery ring. This proposal is awaiting the user's design choice; no
battery field, scanning loop or fabricated battery UI is added in this change.

## Verification

Pure motion tests cover phase order, bounded overshoot, delayed/backward ticks,
queued arrivals, interruptions, opacity and paint purity. Qt integration covers
pending confirmation, duplicate events, hidden/minimized lifecycle, entry
bitmaps, fixed neighboring controls, capsule hit targets, bounded cache use and
surface-specific dispatch. Controller tests verify exact identity for duplicate
names and clearing the prior identity on group events. Rendered phase sheets
and an animated fixture are kept under ignored `logs/` and visually inspected.

Final verification passed all 3169 Python tests in the isolated branch. The
scoped UI/event patch was applied to `E:/gpt-island` after `git apply --check`;
its 283 focused checks and full 3140-test baseline passed. No native audio code,
unfinished calibration source or background work was copied. No physical
reconnection, acoustic/battery capture or EXE rebuild is claimed by this QA.
