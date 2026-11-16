"""Recreate the Chinese decision sheets with real Qt widgets, without a player.

QT_QPA_PLATFORM=offscreen python _demos/par_decisions/render.py
--defaults-only --source-root PATH captures the adopted defaults. The official
window now grows its side-lyric cover when controls hide. The preview subclass
still shows the other cover targets.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
HANDOFF = Path("/workspace/gluspot/handoff/par-decisions")


def render_all(root: Path, output: Path, defaults_only: bool) -> None:
    sys.path[:0] = [str(root), str(root / "tests"), str(root / "_demos/apple_lyrics")]
    from PySide6.QtCore import QRectF, Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath
    from PySide6.QtWidgets import QApplication, QWidget

    from _demos.narrow3.narrow_window import make_owner
    from local.broadcast_indicator import broadcast_face_colors, paint_broadcast_indicator
    from local.music_surface_layout import layout_g_targets
    from local.music_surface_palette import artwork_surface_palette
    from local.music_surface_window import MusicSurfaceWindow
    from demo import DemoWindow
    from timeline import AppleKnobs

    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Noto Sans CJK SC", 11))
    cover_path = root / "_demos/narrow3/covers/night_sea.jpg"
    art = QImage(str(cover_path))
    if art.isNull():
        raise RuntimeError(f"Missing repository sample cover: {cover_path}")
    accent = QColor(92, 158, 232)
    output.mkdir(parents=True, exist_ok=True)
    proofs = {}

    def capture(widget):
        widget.ensurePolished()
        image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        widget.render(image)
        return image

    def save(name, image):
        if not image.save(str(output / name)):
            raise RuntimeError(f"Could not save {name}")

    def proof(name, image):
        proofs[name] = dict(width=image.width(), height=image.height(),
                            sha256=hashlib.sha256(bytes(image.constBits())).hexdigest())
        if defaults_only:
            save(name + ".png", image)

    def text(painter, x, y, width, caption, size=24, color="#edf1f8"):
        font = QFont("Noto Sans CJK SC")
        font.setPixelSize(size)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(QRectF(x, y, width, size * 1.8), Qt.AlignmentFlag.AlignLeft, caption)

    def sheet(width, height):
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(QColor("#171c24"))
        return image

    class CoverTargetPreview(MusicSurfaceWindow):
        """Change only the pose inputs; retain the production QWidget painter."""
        def __init__(self, owner, mode, reveal):
            self.decision_mode, self.decision_reveal = mode, reveal
            super().__init__(owner)

        def target(self):
            kwargs = {} if defaults_only else {"cover_growth_mode": self.decision_mode}
            return layout_g_targets(self.width(), self.height(),
                                    control_reveal=self.decision_reveal,
                                    **self._layout_g_font_measures(QRectF(self.rect())), **kwargs)

        def _cover_layout_endpoints(self, now=None):
            cover = self.target().cover.as_rect()
            return cover, QRectF(cover)

        def _metadata_layout(self, cover, content, surface_scale, control_reveal):
            target = self.target()
            self._metadata_artist_visible = True
            self._metadata_draw_pixel_sizes = {"title": target.title_size, "artist": target.artist_size}
            return (self._metadata_title_font(target.title_size), target.title.as_rect(),
                    self._surface_font(target.artist_size, QFont.Weight.Medium), target.artist.as_rect())

    def music_frame(mode="current", reveal=0.0, preview=True):
        # Freeze all existing scene/motion clocks, including construction.
        with patch("time.monotonic", return_value=100.0):
            owner = make_owner()
            owner.album_art = art
            palette = artwork_surface_palette(art, QColor(44, 44, 44), QColor(14, 14, 14))
            owner.album_color, owner.secondary_color = palette.primary, palette.secondary
            owner.media_duration_seconds = 192.0
            window = CoverTargetPreview(owner, mode, reveal) if preview else MusicSurfaceWindow(owner)
            try:
                window.resize(960, 540)
                window.set_content_layout("lyrics", animate=False)
                window._finish_content_layout()
                window._control_reveal_from = window._control_reveal_target = reveal
                window._control_reveal_at = lambda _now: reveal
                for timer in window.findChildren(QTimer):
                    timer.stop()
                image = capture(window)
                cover = window._current_cover_rect(100.0)
                # The preview must never push the real lyric lane to the right.
                lane = window._content_column_geometry()[2]
                assert cover.right() < lane.left()
                return image, dict(width=cover.width(), x=cover.x(), right=cover.right(), lyric_left=lane.left())
            finally:
                window.shutdown()
                window.close()
                owner.close()

    class FacePreview(QWidget):
        def __init__(self, mode, pale):
            super().__init__()
            self.mode, self.pale = mode, pale
            self.setFixedSize(480, 310)

        def paintEvent(self, _event):
            with QPainter(self) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.fillRect(self.rect(), QColor("#171c24"))
                for index, (state, label) in enumerate((("speaking", "播报中"), ("idle", "待机"))):
                    left = 24 + index * 236
                    rect = QRectF(left, 50, 192, 192)
                    text(painter, left, 8, 200, label, 22)
                    painter.save()
                    clip = QPainterPath()
                    clip.addRoundedRect(rect, 11, 11)
                    painter.setClipPath(clip)
                    if self.pale:
                        painter.fillRect(rect, Qt.GlobalColor.white)
                    else:
                        side = min(art.width(), art.height())
                        painter.drawImage(rect, art, QRectF((art.width()-side)/2, (art.height()-side)/2, side, side))
                    kwargs = {} if defaults_only else {"body_mode": self.mode}
                    paint_broadcast_indicator(
                        painter, rect, 11, style="face", state=state, elapsed=1.2,
                        color=accent, ring_painter=None, **kwargs,
                    )
                    painter.restore()
                text(painter, 24, 260, 440, "纯白封面" if self.pale else "夜海封面", 22, "#b4bfce")

    visible, _ = music_frame(reveal=1.0, preview=False)
    live_hidden, _ = music_frame(preview=False)
    current, current_geometry = music_frame()
    proof("default-live-visible", visible)
    proof("default-live-hidden", live_hidden)
    proof("default-hidden-target", current)
    proof("default-face-dark", capture(FacePreview("current", False)))
    proof("default-face-white", capture(FacePreview("current", True)))

    apple = DemoWindow()
    apple.timer.stop()
    apple.playing = False
    apple.canvas.setFixedSize(720, 800)
    apple.set_cover(1)  # Same night-sea photograph as the other decisions.

    def apple_frame(preset, theme):
        apple.knobs = AppleKnobs() if defaults_only else AppleKnobs.from_preset(preset)
        apple.set_theme(theme)
        apple.seek(3.35, play=False)
        return capture(apple.canvas)

    proof("default-apple-dark", apple_frame("current", "dark"))
    proof("default-apple-white", apple_frame("current", "light"))
    proofs["numbers"] = dict(apple=asdict(AppleKnobs()), cover=current_geometry,
                             face=broadcast_face_colors(accent)[0].getRgb())

    save("adopted-cover-visible.png", visible)
    save("adopted-cover-hidden.png", live_hidden)
    save("adopted-face-dark.png", capture(FacePreview("accent", False)))
    save("adopted-face-white.png", capture(FacePreview("accent", True)))
    save("adopted-apple-dark.png", apple_frame("current", "dark"))
    save("adopted-apple-white.png", apple_frame("current", "light"))

    if not defaults_only:
        image = sheet(2992, 1088)
        with QPainter(image) as painter:
            text(painter, 32, 18, 2900, "① 隐藏控件后，封面放大约 11%", 34)
            text(painter, 32, 70, 2900, "真实播放器控件 · 同一首歌、同一张封面 · 每格 960×540 · 上排为布局目标预览", 24, "#b4bfce")
            for index, (mode, label) in enumerate((("none", "原大小"), ("current", "约 11%（已采用）"), ("large", "大封面"))):
                frame, geometry = music_frame(mode)
                left = 32 + index * 984
                text(painter, left, 130, 950, label, 30)
                text(painter, left, 176, 950, f"控件隐藏 · 封面宽 {geometry['width']:.1f} 像素", 23, "#b4bfce")
                painter.drawImage(left, 224, frame)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            text(painter, 32, 786, 700, "正式页面：控件显示", 24)
            text(painter, 536, 786, 700, "正式页面：控件隐藏，封面约大 11%", 24)
            painter.drawImage(QRectF(32, 834, 432, 243), visible)
            painter.drawImage(QRectF(536, 834, 432, 243), live_hidden)
            text(painter, 1036, 836, 1900, "正式页面已接上「约 11%」。标题栏宽度不变，跟着封面下移。", 26)
            text(painter, 1036, 888, 1900, "30% 是请求值：受歌词边界限制，实际约增大 11%。", 26, "#b4bfce")
            text(painter, 1036, 940, 1900, "大封面减少左留白，实际约增大 26%；右侧歌词位置不变。", 26, "#b4bfce")
        save("item1-cover-growth.png", image)

        image = sheet(1536, 858)
        with QPainter(image) as painter:
            text(painter, 24, 16, 1480, "② 圆脸主体改用更鲜艳的主题色", 34)
            text(painter, 24, 70, 1480, "同一主题蓝、同一尺寸、同一时刻 · 眼睛保持白色 · 深色与白色封面对比", 23, "#b4bfce")
            for index, (mode, label) in enumerate((("current", "近黑染色"), ("accent", "主题色（已采用）"), ("warm", "暖炭灰"))):
                left = 24 + index * 504
                text(painter, left, 128, 480, label, 28)
                for row, pale in enumerate((False, True)):
                    painter.drawImage(left, 188 + row * 326, capture(FacePreview(mode, pale)))
        save("item2-face-body.png", image)

        image = sheet(2272, 1840)
        with QPainter(image) as painter:
            text(painter, 32, 16, 2200, "③ 仿 Apple 歌词默认 88%、2.2、350 毫秒", 34)
            text(painter, 32, 70, 2200, "独立演示 · 同为 3.35 秒，「慢」字正在读亮 · 上排夜海背景，下排白底", 25, "#b4bfce")
            for index, (preset, label) in enumerate((("current", "88% · 2.2 · 350 毫秒（已采用）"), ("soft_close", "柔和紧凑"), ("dramatic", "深度聚焦"))):
                left = 32 + index * 744
                knobs = AppleKnobs.from_preset(preset)
                text(painter, left, 126, 720, label, 30)
                text(painter, left, 174, 720, f"模糊 {knobs.blur_px:g} · 邻行 {knobs.other_scale:.0%} · 滚动 {knobs.scroll_ms:g} 毫秒", 23, "#b4bfce")
                for row, theme in enumerate(("dark", "light")):
                    painter.drawImage(left, 224 + row * 816, apple_frame(preset, theme))
        save("item3-apple-knobs.png", image)
    apple.close()
    (output / "default-proofs.json").write_text(json.dumps(proofs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HANDOFF)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--defaults-only", action="store_true")
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.dont_write_bytecode = True
    with tempfile.TemporaryDirectory(prefix="gluspot-decisions-") as runtime:
        for name in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
            os.environ[name] = runtime
        render_all(args.source_root.resolve(), args.out.resolve(), args.defaults_only)
    print(f"Saved GluSpot decision evidence to {args.out.resolve()}")


if __name__ == "__main__":
    main()
