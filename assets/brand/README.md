# GluSpot default GLU icon

The default app, tray, settings preview and next Windows build use
`gluspot-glu.ico` / `gluspot-glu-icon-256.png`. The mark contains only **GLU**,
using the actual Impact letter outlines from the installation animation in
`tools/preview_install_animation.py`: final 37 px glyphs, 1.10 horizontal scale,
centres 66 / 85 / 104. The transparent square export preserves that letter
placement and adds a narrow white keyline for visibility on light and dark
Windows surfaces. No capsule, `spot`, or earlier card logo is part of the mark.

`gluspot-glu.svg` is the reviewable outline source. Its description records the
SHA256 of the local `impact.ttf` used for extraction. No font file, clipboard
path, GIF frame, account data or reference-image metadata is embedded in the
assets. The checked-in SVG renders without an installed font.

Regenerate PNG and all seven native-size ICO frames with this checkout's Qt:

```powershell
& '.venv/Scripts/python.exe' tools/generate_glu_icon.py
```

To deliberately re-extract the same font's outlines:

```powershell
& '.venv/Scripts/python.exe' tools/generate_glu_icon.py --update-outline --font C:/Windows/Fonts/impact.ttf
```

The generator validates Impact, uses Qt's actual font curves and SVG renderer,
and separately renders 16, 24, 32, 48, 64, 128 and 256 px frames at 8x sampling.
ICO packing uses those explicit frames; smaller frames are not produced by
shrinking a pre-existing bitmap. PNG/ICO frames contain pixels only.

Older brand files are historical assets. Default resolution and packaging no
longer select them. User-selected custom icons retain their existing behavior.
