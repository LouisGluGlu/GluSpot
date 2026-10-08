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
| `speaker.fill.svg` | `音响_fill.svg` | Speaker |
| `output.link.svg` | `common-linear-link.svg` | Output chooser button |

Generic receiver/headphone/device kinds retain code-native fallbacks rather
than claiming a model. Device kind does not establish Bluetooth transport.
Unknown devices use a neutral peripheral glyph, visibly distinct from a
speaker, headphones and a generic AirPlay receiver.

The October 8, 2026 reference is implemented by the shared
`paint_audio_output_status` compositor inside the existing circular control.
One connected device uses its identified glyph. A homogeneous group uses one
glyph above the total member count; mixed groups use two overlapping distinct
representatives above that count (HomePod and HomePod mini when both are
present). The face and ring stay owned by the music surface. Empty membership
retains the original output-link size. Overlap separation preserves the source
assets' alpha, and status compositions and tints use bounded caches.

Displayed counts reflect confirmed route membership, including generic and
mixed device groups; they are not a claim of acoustically verified capacity.
