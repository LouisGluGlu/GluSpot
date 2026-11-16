"""Offscreen strips and short loops. No player."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from local.spring_motion import WIDE_NARROW_SPRING  # noqa: E402

from clips import CLIPS, write_all  # noqa: E402
from demo import DemoWindow, _prepare_quick_software  # noqa: E402


def _wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _settle(window: DemoWindow, on: bool) -> None:
    window.stage.set_broadcast(on)
    for _ in range(180):
        window.stage.step(1.0 / 60.0)
        QApplication.processEvents()
    window.stage._ensure_timer()


def _check_idle(window: DemoWindow) -> None:
    if WIDE_NARROW_SPRING.damping_ratio != 1.0:
        raise SystemExit("enter/exit spring is not damping ratio 1")
    window.show()
    QApplication.processEvents()
    _settle(window, False)
    if window.stage.timer_active or not window.stage.clip.hold:
        raise SystemExit("idle preview left a clock running")
    _settle(window, True)
    if window.stage.timer_active:
        raise SystemExit("settled broadcast still runs the demo timer")
    if window.stage.clip.hold:
        raise SystemExit("broadcast did not start the Lottie loop")


def _strip(window: DemoWindow) -> QImage:
    frames = []
    _settle(window, False)
    frames.append(window.grab().toImage())
    window.stage.set_broadcast(True)
    for _ in range(8):
        window.stage.step(1.0 / 60.0)
        QApplication.processEvents()
    frames.append(window.grab().toImage())
    _settle(window, True)
    for _ in range(4):
        _wait(280)
        frames.append(window.grab().toImage())
    window.stage.set_broadcast(False)
    for _ in range(8):
        window.stage.step(1.0 / 60.0)
        QApplication.processEvents()
    frames.append(window.grab().toImage())
    _settle(window, False)
    frames.append(window.grab().toImage())
    width = frames[0].width()
    height = frames[0].height()
    sheet = QImage(width * len(frames), height, QImage.Format.Format_RGB32)
    painter_mod = __import__("PySide6.QtGui", fromlist=["QPainter"]).QPainter
    with painter_mod(sheet) as painter:
        for index, frame in enumerate(frames):
            painter.drawImage(index * width, 0, frame)
    return sheet, frames


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "shots"
    out.mkdir(parents=True, exist_ok=True)
    write_all()
    _prepare_quick_software()
    QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)
    app = QApplication.instance() or QApplication([])
    window = DemoWindow()
    window.resize(880, 640)
    _check_idle(window)
    from PIL import Image
    for name, label in CLIPS:
        window.stage.set_candidate(name)
        sheet, frames = _strip(window)
        if not sheet.save(str(out / f"{name}-strip.png")):
            raise SystemExit(f"Could not save {name}")
        images = []
        for frame in frames:
            ptr = frame.constBits()
            raw = bytes(ptr) if not isinstance(ptr, (bytes, bytearray)) else bytes(ptr)
            # QImage is RGB32, little-endian BGRA in memory when format is RGB32? Format_RGB32 is 0xffRRGGBB.
            images.append(Image.frombytes("RGBA", (frame.width(), frame.height()), raw, "raw", "BGRA"))
        images[0].save(
            out / f"{name}.webp", save_all=True, append_images=images[1:],
            duration=180, loop=0, format="WEBP",
        )
        print(label, "frames", len(frames))
    _settle(window, False)
    if window.stage.timer_active or not window.stage.clip.hold:
        raise SystemExit("preview did not return to idle")
    print(f"saved to {out}")


if __name__ == "__main__":
    main()
