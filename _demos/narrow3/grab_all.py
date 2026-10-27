"""Capture all three narrow studies and a production reference into one sheet."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication


HERE = Path(__file__).resolve().parent


def run_step(label, command):
    start = time.monotonic()
    print(f"start {label}", flush=True)
    subprocess.run(command, check=True, env=os.environ.copy())
    print(f"finished {label}: {time.monotonic() - start:.2f}s", flush=True)


def make_overview(output):
    app = QApplication.instance() or QApplication([])
    font = QFont("Microsoft YaHei UI") if "Microsoft YaHei UI" in QFontDatabase.families() else app.font()
    font.setPixelSize(20)
    font.setBold(True)
    sheet = QImage(1800, 1140, QImage.Format.Format_RGB32)
    sheet.fill(QColor("#20242a"))
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setFont(font)
    painter.setPen(Qt.GlobalColor.white)
    for column, (variant, label) in enumerate((("A", "方案 A · 安静"), ("B", "方案 B · 一行"), ("C", "方案 C · 居中"))):
        left = column * 600
        painter.drawText(QRectF(left, 10, 600, 35), Qt.AlignmentFlag.AlignCenter, label)
        for row, name in enumerate(("narrow_lyrics", "narrow_list")):
            image = QImage(str(output / f"narrow3_{variant}_{name}.png"))
            if image.isNull():
                raise RuntimeError(f"missing image: {variant} {name}")
            painter.drawImage(QRectF(left + 187.5, 50 + row * 420, 225, 400), image)
        painter.drawText(QRectF(left, 885, 600, 30), Qt.AlignmentFlag.AlignCenter, "生产宽窗 · 夜海")
        reference = QImage(str(output / "real_night_sea_960x540_lyrics.png"))
        if reference.isNull():
            raise RuntimeError("missing production reference")
        painter.drawImage(QRectF(left + 108, 920, 384, 216), reference)
    painter.end()
    if not sheet.save(str(output / "narrow3_overview.png")):
        raise RuntimeError("could not save overview")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for variant, script in (("A", "demo_quiet.py"), ("B", "demo_row.py"), ("C", "demo_center.py")):
        run_step(variant, [sys.executable, "-X", "utf8", str(HERE / script), "--grab", str(args.out)])
    run_step("production reference", [sys.executable, "-X", "utf8", str(HERE / "ref_real_window.py"),
             "--cover", str(HERE / "covers" / "night_sea.jpg"), "--out", str(args.out),
             "--sizes", "960x540", "--tag", "real_night_sea"])
    start = time.monotonic()
    make_overview(args.out)
    print(f"saved narrow3_overview.png: {time.monotonic() - start:.2f}s", flush=True)


if __name__ == "__main__":
    main()
