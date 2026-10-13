# Settings window controls

These three SVGs were supplied by the user on October 13, 2026 and are retained without edits:

- `close.svg`: `关闭 (1).svg`
- `minimize.svg`: `最小化 (3).svg`
- `maximize.svg`: `最大化1.svg`

`local/mac_style.py` preserves their alpha masks and aspect ratios. The actual occupied SVG area is measured from a cached 256-pixel mask, including transparency and holes. Icon ink uses its button's hue darkened by that area: larger shapes retain more brightness. Source white/gray fills are never used as the final control ink. This rule applies only to these three settings window buttons.

The existing whole-assets packaging rule includes this directory. Runtime screenshots and previews remain ignored.
