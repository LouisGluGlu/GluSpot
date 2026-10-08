# Audio output symbols

These SVGs were supplied by the user for the audio-output chooser and circular
controls on October 2, 2026. Original vector bytes are retained; the renderer
preserves aspect ratio and source opacity and applies the surface tint through
bounded Qt SVG/mask caches.

| Repository filename | Supplied filename | Intended kind |
| --- | --- | --- |
| `homepod.fill.svg` | `homepod.fill.svg` | Explicit HomePod model |
| `homepod.mini.fill.svg` | `homepod.mini.fill.svg` | Explicit HomePod mini model |
| `airpods.pro.svg` | `airpods.pro.svg` | Explicit AirPods Pro name on a headphone endpoint |
| `airpods.fill.svg` | `Airpods.svg` (October 8, 2026) | Explicit regular AirPods name on a headphone endpoint |
| `speaker.fill.svg` | `音响_fill.svg` | Speaker |
| `output.link.svg` | `common-linear-link.svg` | Output chooser button |

Generic receiver/headphone/device kinds retain code-native fallbacks rather
than claiming a model. Device kind does not establish Bluetooth transport.
Unknown devices use a neutral peripheral glyph, visibly distinct from a
speaker, headphones and a generic AirPlay receiver.

Regular AirPods and AirPods Pro retain separate model kinds and supplied
assets. The ASCII model-token matcher permits a Chinese owner's name directly
before `AirPods`; Windows must still identify the active render endpoint as
headphones/headset. AirPods Max retains the generic headphone glyph because
neither earbud asset represents it. A present Find My/AVRCP PnP entry alone is
not an active playback endpoint.

The October 8, 2026 reference is implemented by the shared
`paint_audio_output_status` compositor inside the existing circular control.
One connected device uses its identified glyph. A homogeneous group uses one
glyph above the total member count. The first two distinct confirmed kinds in
connection order retain the group's representative slots; later members only
change the count. Only the HomePod/HomePod mini pair uses the dedicated overlap.
Every other pair uses smaller ordered left/right glyphs, without duplicates.
Removing a member rederives the earliest remaining distinct kinds. The face and
ring stay owned by the music surface. Empty membership
retains the original output-link size. Overlap separation preserves the source
assets' alpha, and status compositions and tints use bounded caches.

The pure membership tracker preserves order across provider sorting and volume
updates. Sender restarts temporarily retain the last confirmed badge while
pending targets remain uncounted; a confirmed replacement or error resolves
that hold. A surface's first sample is a baseline and does not replay an old
connection animation.

Displayed counts reflect confirmed route membership, including generic and
mixed device groups; they are not a claim of acoustically verified capacity.
