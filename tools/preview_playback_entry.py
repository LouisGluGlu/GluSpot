"""Render UI fixtures without network access, accounts or settings writes.

The login fixture uses a labelled checker image to test raster placement; it
is not a login QR, an account session, or proof of provider authentication.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.playback_entry_window import ENTRY_SOURCES, PlaybackEntryWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".runtime/playback-entry-preview"))
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    window = PlaybackEntryWindow()
    window.show()
    app.processEvents()
    window._advance()
    window.grab().save(str(options.output / "choices.png"))
    window.show_login()
    window.set_sources(tuple(SimpleNamespace(source_id=s.source_id, available=s.source_id != "qishui", reason="暂无经过验证的登录接口") for s in ENTRY_SOURCES))
    app.processEvents()
    window.grab().save(str(options.output / "sources.png"))
    window.select_source("netease")
    window._expansion.stop()
    window._set_expanded(1.0)
    tile = QImage(160, 160, QImage.Format.Format_RGB32)
    tile.fill(QColor("white"))
    with QPainter(tile) as painter:
        painter.setPen(QColor("#444444"))
        painter.setFont(QFont("Arial", 12))
        painter.drawText(QRectF(0, 0, 160, 160), 0x84, "UI FIXTURE\nNO LOGIN QR")
    payload = QByteArray()
    buffer = QBuffer(payload)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    tile.save(buffer, "PNG")
    window.set_login_snapshot(SimpleNamespace(source_id="netease", state="waiting", qr_image_bytes=bytes(payload), message="布局测试图片 · 不是账号登录二维码"))
    app.processEvents()
    window.grab().save(str(options.output / "login-fixture.png"))
    window.close()
    print(options.output.resolve())


if __name__ == "__main__":
    main()
