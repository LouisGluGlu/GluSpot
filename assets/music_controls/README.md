# Music playback and library controls

The four controls below were supplied directly by the user from their local
Downloads folder on 2026-10-10. Their copies preserve the source bytes exactly.
No new license or trademark ownership is claimed by GluSpot.

| Asset | User-supplied filename | SHA-256 |
| --- | --- | --- |
| `library.svg` | `音乐库.svg` | `1f30a8b8d6598219b2db0a2e496f493ff1b4cbc063fc60c70ebdc017737e184c` |
| `shuffle.svg` | `随机.svg` | `a2784902611c85b1b3d433297ae270ba5f34cd34248e70b5bc47cb7de185eaa0` |
| `sequence.svg` | `循环播放.svg` | `11c76facd25e99de9703b085fbcf31ace5547767bf55a745e0c2de37896210ce` |
| `repeat_one.svg` | `单曲循环.svg` | `45485b17ef9b28b87a34401eacd12db798dc6d7ced84bb6fd6d85f96fe0789da` |

XML inspection found only SVG/path geometry, with no scripts, event handlers,
external image/style references, internal DTD subsets or entity definitions.
The standard SVG 1.1 public DOCTYPE and original white fills are retained.
The music surface uses the existing bounded Qt SVG cache and render-time tint
to match its control ink without changing asset bytes or aspect ratios.
Sequential mode uses the ordinary gray face and white icon. Shuffle and
single-track repeat use the existing pale enabled face with a live background
tint, darkened in the same hue when needed to retain readable contrast.

The older Chinese-named playback transport assets already existed in this
folder. This record describes only the four additions above.

On 2026-10-11, the song-page library return action uses `library_back.svg`,
an original 24 px rounded-stroke return arrow paired with the existing
“音乐库” label. It remains readable at the same scale as adjacent controls.
The original supplied `library.svg` stays byte-identical for provenance.

On 2026-10-11, the user supplied `@无限.svg`, copied byte-identically as
`personal_fm.svg` (SHA-256
`9134eea47cbb4b776f08b58c7f03e51d851fccd8b521c176ba907ab62c0415e1`).
It contains the same local SVG/path-only geometry and standard public SVG 1.1
DOCTYPE, without scripts, event handlers, references or entity definitions.
Only personal FM continuation uses this icon. Its exact existing lyric accent
travels smoothly to black for a light occupied album tone or white for a dark
one, then back, over four seconds. A thin neutral edge preserves readability.
The render cache retains one mask per size rather than every animation tint;
the existing control fade freezes its color and reuses the shared blur layer.
Single-track repeat and all ordinary playlist icons retain their existing ink.
