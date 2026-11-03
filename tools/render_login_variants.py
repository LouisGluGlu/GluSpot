"""Render the real entry window with local artwork, never live login tokens.

Run with /workspace/gluspot/qtvenv/bin/python. All Qt/settings locations are
private temporary directories; no provider or playback controller is created.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("/workspace/gluspot/wt-login-variants"))
    parser.add_argument("--output", type=Path, default=Path("/workspace/gluspot/handoff/login-variants"))
    parser.add_argument("--a2", action="store_true", help="Render the shipping sidebar, three sizes and pulse proof")
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = "1"
    if args.a2:
        os.environ["GLUSPOT_LOGIN_VARIANT"] = "sidebar"
    with tempfile.TemporaryDirectory(prefix="gluspot-login-variants-") as private:
        for key in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                    "XDG_RUNTIME_DIR", "GLUSPOT_LYRICS_CACHE_DIR", "GLUSPOT_ALBUM_ART_CACHE_DIR"):
            folder = Path(private, key)
            folder.mkdir(mode=0o700)
            os.environ[key] = str(folder)
        from PySide6.QtCore import QByteArray, QBuffer, QCoreApplication, QEvent, QIODevice, QPointF, QRect, QRectF, Qt
        from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QRadialGradient
        from PySide6.QtWidgets import QApplication
        from api.online_music import AccountSnapshot
        from local.playback_entry_window import ENTRY_SOURCES, PlaybackEntryWindow

        app = QApplication.instance() or QApplication([])
        shots = args.output / ("shots-a2" if args.a2 else "shots")
        shots.mkdir(parents=True, exist_ok=True)
        qr = QImage(132, 132, QImage.Format.Format_RGB32)
        qr.fill(Qt.GlobalColor.white)
        randomizer = random.Random(168)
        with QPainter(qr) as painter:
            for y in range(29):
                for x in range(29):
                    if randomizer.randrange(2):
                        painter.fillRect(8 + x * 4, 8 + y * 4, 4, 4, Qt.GlobalColor.black)
            for x, y in ((8, 8), (96, 8), (8, 96)):
                painter.fillRect(x, y, 28, 28, Qt.GlobalColor.black)
                painter.fillRect(x + 4, y + 4, 20, 20, Qt.GlobalColor.white)
                painter.fillRect(x + 8, y + 8, 12, 12, Qt.GlobalColor.black)
        output = QByteArray()
        buffer = QBuffer(output)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        qr.save(buffer, "PNG")
        buffer.close()

        avatar = QImage(320, 320, QImage.Format.Format_ARGB32_Premultiplied)
        avatar.fill(Qt.GlobalColor.transparent)
        with QPainter(avatar) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            clip = QPainterPath()
            clip.addEllipse(QRectF(0, 0, 320, 320))
            painter.setClipPath(clip)
            sky = QLinearGradient(0, 0, 320, 320)
            sky.setColorAt(0, QColor("#a5c5cc"))
            sky.setColorAt(.5, QColor("#c9a899"))
            sky.setColorAt(1, QColor("#38495f"))
            painter.fillRect(avatar.rect(), sky)
            sun = QRadialGradient(QPointF(240, 66), 130)
            sun.setColorAt(0, QColor(255, 223, 172, 220))
            sun.setColorAt(1, QColor(255, 223, 172, 0))
            painter.fillRect(avatar.rect(), sun)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#6c7f82"))
            painter.drawEllipse(QRectF(-70, 178, 320, 200))
            painter.setBrush(QColor("#445a68"))
            painter.drawEllipse(QRectF(102, 205, 340, 210))
            painter.setBrush(QColor("#263443"))
            painter.drawEllipse(QRectF(85, 218, 158, 208))
            painter.setBrush(QColor("#d6af96"))
            painter.drawRoundedRect(QRectF(147, 188, 35, 57), 16, 16)
            painter.drawEllipse(QRectF(125, 99, 80, 111))
            painter.setBrush(QColor("#30313b"))
            painter.drawEllipse(QRectF(119, 82, 90, 67))
            painter.drawEllipse(QRectF(116, 112, 22, 65))

        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1920, 1080),
                                 geometry=lambda: QRect(0, 0, 1920, 1080))
        states = (("connected", "已连接"), ("waiting", "等待扫码"), ("error", "连接错误"))
        sizes = ((960, 540), (433, 550), (391, 508)) if args.a2 else ((960, 540), (433, 550))
        variants = (("A2", "sidebar"),) if args.a2 else zip("ABC", ("sidebar", "focus", "hero"))
        with patch.object(QGuiApplication, "screenAt", return_value=screen):
            for letter, variant in variants:
                images = {}
                for width, height in sizes:
                    for state, _label in states:
                        window = PlaybackEntryWindow()
                        window.set_login_variant(variant)
                        window.show_sources()
                        window.set_sources(tuple(SimpleNamespace(source_id=s.source_id, available=s.login_supported)
                                                 for s in ENTRY_SOURCES))
                        window.select_source("netease")
                        window._expansion.stop()
                        window._expanded = 1.
                        window.setMinimumSize(0, 0)
                        window.resize(width, height)
                        if state == "connected":
                            window.set_account(AccountSnapshot("netease", True, "u1", "LouisHerm", "", "verified", ""))
                            window.set_account_artwork(avatar)
                        else:
                            window.set_login_snapshot(SimpleNamespace(source_id="netease", state=state,
                                qr_image_bytes=bytes(output) if state == "waiting" else b"",
                                message="等待扫码" if state == "waiting" else "连接暂不可用，请重试"))
                        window.show()
                        window._layout_children()
                        app.processEvents()
                        if args.a2 and width == 960 and state == "connected":
                            # Advance the real dot-only pulse callback to both extrema.
                            window._status_timer.stop()
                            button = window.source_buttons["netease"]
                            crop = button.status_rect().translated(button.pos()).adjusted(-28, -18, 12, 18)
                            frames = []
                            for offset in (0., 1.):
                                window._advance_status_pulse(window._status_pulse_started_at + offset)
                                frames.append(window.grab().toImage().copy(crop))
                            assert frames[0] != frames[1], "Pulse extrema must produce different dot pixels"
                            proof = QImage(520, 210, QImage.Format.Format_RGB32)
                            proof.fill(QColor("#0d0f13"))
                            with QPainter(proof) as painter:
                                font = QFont("sans-serif")
                                font.setPixelSize(14)
                                painter.setFont(font)
                                painter.setPen(Qt.GlobalColor.white)
                                for column, (frame, label) in enumerate(zip(frames, ("0.55 · dimmest", "1.00 · brightest"))):
                                    x = 16 + column * 260
                                    painter.drawText(QRectF(x, 10, 244, 26), Qt.AlignmentFlag.AlignVCenter, label)
                                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                                    painter.drawImage(QRectF(x, 46, frame.width() * 3, frame.height() * 3), frame)
                            pulse_path = args.output / "variant-A2-pulse.png"
                            if not proof.save(str(pulse_path)) or pulse_path.stat().st_size < 1_000:
                                raise RuntimeError(f"Missing or unexpectedly small pulse proof: {pulse_path}")
                            print(f"{pulse_path} ({pulse_path.stat().st_size:,} bytes)")
                        image = window.grab().toImage()
                        assert image.size() == window.size() and (image.width(), image.height()) == (width, height)
                        path = shots / f"{variant}-{width}x{height}-{state}.png"
                        if not image.save(str(path)):
                            raise RuntimeError(f"Cannot save {path}")
                        images[width, state] = image
                        window.close()
                        window.deleteLater()
                        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                        app.processEvents()
                row_height = max(height for _width, height in sizes) + 46
                sheet = QImage(sum(width for width, _height in sizes) + 16 * (len(sizes) + 1),
                               16 + len(states) * row_height, QImage.Format.Format_RGB32)
                sheet.fill(QColor("#0d0f13"))
                with QPainter(sheet) as painter:
                    font = QFont("sans-serif")
                    font.setPixelSize(13)
                    painter.setFont(font)
                    painter.setPen(Qt.GlobalColor.white)
                    for row, (state, label) in enumerate(states):
                        for column, (width, height) in enumerate(sizes):
                            x = 16 + sum(size[0] + 16 for size in sizes[:column])
                            y = 16 + row * row_height
                            painter.drawText(QRectF(x, y, width, 24), Qt.AlignmentFlag.AlignVCenter,
                                             f"{letter} · {variant} · {width} × {height} · {label}")
                            painter.drawImage(x, y + 28, images[width, state])
                path = args.output / f"variant-{letter}.png"
                if not sheet.save(str(path)) or path.stat().st_size < 20_000:
                    raise RuntimeError(f"Missing or unexpectedly small contact sheet: {path}")
                print(f"{path} ({path.stat().st_size:,} bytes)")
        if args.a2:
            return
        (args.output / "README.md").write_text(
            "# 音乐来源登录页对比\n\n"
            "- A / sidebar：宽屏左侧平台栏，窄屏左侧图标栏，右侧呈现账号与扫码详情。\n"
            "- B / focus：紧凑平台切换胶囊下方，居中突出账号或扫码卡片。\n"
            "- C / hero：柔和头像色彩背景、平台状态列表与内嵌详情卡片。\n\n"
            "默认 sidebar；classic 可选。设置环境变量 `GLUSPOT_LOGIN_VARIANT=sidebar|focus|hero|classic`，"
            "或调用 `window.set_login_variant('sidebar')`；`window.login_variant` 可读取当前布局。\n\n"
            "对比图：`variant-A.png`、`variant-B.png`、`variant-C.png`。每张两列（960 / 433），"
            "三行（已连接 / 等待扫码 / 连接错误），单图在 `shots/`。头像与二维码均为本地测试绘图，无登录凭证。\n\n"
            "重现：`/workspace/gluspot/qtvenv/bin/python /workspace/gluspot/wt-login-variants/tools/render_login_variants.py`。"
            "此目录的 `render_variants.py` 是同一脚本的副本，使用隔离的临时配置目录。\n",
            encoding="utf-8")


if __name__ == "__main__":
    main()
