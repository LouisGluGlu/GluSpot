"""Write offscreen shots of the lyric demo. Does not open a player."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PySide6.QtWidgets import QApplication  # noqa: E402

from demo import DemoWindow  # noqa: E402


SHOTS = (
    ("dark-apple-reading", "apple", "dark", 3.35),
    ("dark-apple-early", "apple", "dark", 3.72),
    ("white-apple-reading", "apple", "light", 3.35),
    ("white-apple-early", "apple", "light", 3.72),
    ("dark-current-same-moment", "current", "dark", 3.72),
    ("dark-apple-long-gap", "apple", "dark", 7.20),
)


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "shots"
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    window = DemoWindow()
    window.timer.stop()
    window.playing = False
    window.resize(1180, 760)
    window.show()
    app.processEvents()
    for name, style, theme, moment in SHOTS:
        window.set_style(style)
        window.set_theme(theme)
        window.seek(moment, play=False)
        window.playing = False
        app.processEvents()
        window.canvas.update()
        app.processEvents()
        image = window.grab()
        path = out / f"{name}.png"
        if not image.save(str(path)):
            raise SystemExit(f"failed to save {path}")
        print(path, image.width(), image.height())
    window.close()


if __name__ == "__main__":
    main()
