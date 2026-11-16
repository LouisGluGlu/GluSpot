"""Render the real music surface with original fixture lyrics, without players.

QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 python tools/render_apple_lyrics.py OUT_DIR
Output lives outside the checkout; no user settings, artwork or network access.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from tests.apple_lyric_fixture import make_surface, render


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error("Write render artifacts outside the source checkout")
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    records = []
    for moment, position in (("sustained-3.350s", 3.35), ("early-scroll-3.625s", 3.625), ("long-gap-7.200s", 7.2)):
        for enabled in (False, True):
            owner, surface = make_surface(enabled, position)
            try:
                image = render(surface)
                name = f'{"after-on" if enabled else "before-off"}-{moment}.png'
                assert image.save(str(output / name))
                records.append(dict(file=name, audio_position=surface._lyric_motion.last_position,
                                    apple_focus=getattr(surface, "_apple_focus", None),
                                    row_hits=len(surface._lyric_hit_rects), word_hits=len(surface._lyric_word_hit_rects)))
            finally:
                surface.shutdown()
                owner.close()
    (output / "render-metadata.json").write_text(json.dumps(records, indent=2) + "\n")
    print(f"Rendered {len(records)} product PNGs to {output}")
    del app


if __name__ == "__main__":
    main()
