"""Experimental narrow composition using the production music surface renderer."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
SANDBOX = tempfile.TemporaryDirectory(prefix="gluspot-narrow3-")
for name in ("HOME", "APPDATA", "LOCALAPPDATA"):
    os.environ[name] = SANDBOX.name

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QTextLayout
from PySide6.QtWidgets import QApplication

from api.models import ColorToken
from api.online_music import OnlineSong
from api.providers import WindowsMediaProvider
from local.audio_output_icons import paint_audio_output_status
from local.music_source_icons import paint_music_source_icon
from local.music_surface_window import MusicSurfaceWindow
from test_music_surface_transition import _TransitionOwner
from test_owned_playback_surface import _Provider
from test_song_collection_surface import Collection
from layout import NarrowLayout

SONGS = [("夜航的信", "林间电台"), ("晚风经过", "陈小雨"), ("云与海之间", "南方来信"), ("星光落下来", "木子"), ("慢慢的旅程", "夏日回声"), ("下一站黎明", "白昼乐队"), ("你听见了吗", "无名旅人"), ("再见之前", "海边房间")]
LYRICS = ["在夜色中慢慢走", "把心事交给晚风", "那些没有说完的话", "会在星光里重逢", "让时间轻轻流过", "我们还在这条路上", "等天边亮起微光", "再一起迎接远方"]
COVERS = ("night_sea.jpg", "sunset_city.jpg", "pink_forest.jpg", "clip.jpg")


class NarrowStudyWindow(MusicSurfaceWindow):
    def __init__(self, owner, variant, footer_visibility, lyrics_alignment):
        super().__init__(owner)
        self.variant = variant
        self.footer_visibility = footer_visibility
        self.lyrics_alignment = lyrics_alignment
        self.narrow = True
        self.mode = "lyrics"
        self.cover_index = 0
        self.song_index = 0
        self.liked_songs = set()
        self.shelf = False
        self.footer_hover = False
        self.hovered_glass = ""
        self.hints = True
        self.freeze_clock = False
        self.samples = []
        self.last_tick = time.monotonic()
        self.frame_times = []
        self.setMinimumSize(320, 400)
        self.setMouseTracking(True)
        self.move(80, 80)
        self.resize(360, 640)
        self.set_cover(0)
        self._sync_owned_playback_mode()
        self.set_content_layout("lyrics", animate=False)
        self._layout_animation.stop()
        self._finish_content_layout()
        self._reset_control_idle()
        self.study_timer = QTimer(self)
        self.study_timer.timeout.connect(self.tick)
        self.study_timer.start(33)

    def set_cover(self, index):
        self.cover_index = index
        path = Path(__file__).parent / "covers" / COVERS[index]
        image = QImage(str(path)).convertToFormat(QImage.Format.Format_RGB32)
        fallback = ColorToken(40, 90, 170)
        secondary = ColorToken(20, 30, 70)
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        primary, other = provider._colors_from_art(path.read_bytes(), fallback, secondary)
        self.owner.album_color = QColor(*primary.rgba())
        self.owner.secondary_color = QColor(*other.rgba())
        self.owner.album_art = image
        self.update()

    def select_song(self, index):
        self.song_index = index % len(SONGS)
        self.owner.track_title, self.owner.artist = SONGS[self.song_index]
        self.owner.media_track_id = f"owned:netease:{self.song_index + 1}"
        self.owner.media_position_seconds = 0
        self.set_cover(self.song_index % len(COVERS))

    def tick(self):
        now = time.monotonic()
        if self.owner.media_is_playing and not self.freeze_clock:
            self.owner.media_position_seconds = (self.owner.media_position_seconds + now - self.last_tick) % 32
        self.last_tick = now
        self._glass_feedback.advance(self.glass_groups(), self.hovered_glass, now)
        self.update()
        names = {"A": "安静", "B": "一行", "C": "居中"}
        fps = len([t for t in self.frame_times if now - t < 1.0])
        self.setWindowTitle(f"GluSpot 窄窗v3 · 方案{self.variant} {names[self.variant]} · {fps} fps")

    def glass_groups(self):
        layout = NarrowLayout(self.width(), self.height())
        top_keys = ("content_layout", "audio_output", "playback_mode", "translation", "return")
        top = {key: rect for key, rect in zip(top_keys, layout.top)}
        if not self.shelf:
            top = {"content_layout": layout.top[0]}
        return [top, {"library": layout.back},
                {"media:toggle_play_pause": layout.play, "collection": layout.favorite}]

    def posed_rect(self, key, rect):
        state = self._glass_feedback.state(key)
        return rect.translated(state.offset) if state else rect

    def resizeEvent(self, event):
        ratio = self.width() / max(1, self.height())
        if ratio < .9:
            self.narrow = True
        elif ratio > 1.1:
            self.narrow = False
        super().resizeEvent(event)

    def paintEvent(self, event):
        start = time.perf_counter()
        if not self.narrow:
            super().paintEvent(event)
        else:
            self.paint_narrow()
        now = time.monotonic()
        self.frame_times = [t for t in self.frame_times if now - t < 1.0]
        self.frame_times.append(now)
        self.samples.append((time.perf_counter() - start) * 1000)
        self.samples = self.samples[-2000:]

    def paint_narrow(self):
        now = time.monotonic()
        self._frame_control_color_at = now
        self._clear_control_renderer.begin_frame(now)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        palette = self._art_palette()
        self._paint_background(painter, QRectF(self.rect()), QColor(palette.primary), QColor(palette.secondary), now,
                               ambient_colors=palette.ambient_colors, ambient_weights=palette.ambient_weights)
        layout = NarrowLayout(self.width(), self.height())
        self.paint_header(painter, layout)
        self.paint_fading_content(painter, layout)
        self.paint_controls(painter, layout)
        if self.hints:
            painter.setFont(self._surface_font(12, QFont.Weight.Medium))
            painter.setPen(QColor(255, 255, 255, 120))
            painter.drawText(QRectF(4, self.height() - 13, self.width() - 8, 13), Qt.AlignmentFlag.AlignCenter,
                             "L 歌词  P 列表  N/W 窄/宽  1–4 封面  H 隐藏")
        painter.end()
        self._clear_control_renderer.end_frame()

    def paint_fading_content(self, painter, layout):
        ratio = self.devicePixelRatioF()
        image = QImage(round(self.width() * ratio), round(self.height() * ratio),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        layer = QPainter(image)
        layer.setRenderHint(QPainter.RenderHint.Antialiasing)
        layer.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.mode == "lyrics":
            self.paint_lyrics(layer, layout)
            area = layout.viewport
            fade = 32
        else:
            self.paint_list(layer, layout)
            area = QRectF(0, 218, self.width(), self.height() - 314)
            fade = 28
        layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        gradient = QLinearGradient(0, area.top(), 0, area.bottom())
        edge = min(.5, fade / area.height())
        gradient.setColorAt(0, QColor(255, 255, 255, 0))
        gradient.setColorAt(edge, QColor(255, 255, 255, 255))
        gradient.setColorAt(1 - edge, QColor(255, 255, 255, 255))
        gradient.setColorAt(1, QColor(255, 255, 255, 0))
        if self.mode == "playlist":
            # Preserve the heading above the list's faded viewport.
            layer.fillRect(QRectF(0, 0, self.width(), area.top()), QColor("white"))
        layer.fillRect(area, gradient)
        layer.end()
        painter.drawImage(QPointF(0, 0), image)

    def paint_header(self, painter, layout):
        cover = layout.cover
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(cover, 10, 10)
        painter.setClipPath(clip)
        painter.drawImage(cover, self.owner.album_art)
        painter.restore()
        column = layout.text
        painter.setFont(self._surface_font(20))
        painter.setPen(QColor(255, 255, 255, 242))
        title = QFontMetricsF(painter.font()).elidedText(self.owner.track_title, Qt.TextElideMode.ElideRight, int(column.width()))
        painter.drawText(QRectF(column.x(), column.center().y() - 25, column.width(), 26), Qt.AlignmentFlag.AlignLeft, title)
        painter.setFont(self._surface_font(13, QFont.Weight.Medium))
        painter.setPen(QColor(255, 255, 255, 142))
        artist = QFontMetricsF(painter.font()).elidedText(self.owner.artist, Qt.TextElideMode.ElideRight, int(column.width()))
        painter.drawText(QRectF(column.x(), column.center().y() + 5, column.width(), 20), Qt.AlignmentFlag.AlignLeft, artist)

    def paint_lyrics(self, painter, layout):
        viewport = layout.viewport
        focus = min(len(LYRICS) - 1, int(self.owner.media_position_seconds // 4))
        anchor = viewport.top() + viewport.height() * .38
        font = self._surface_font(22)
        painter.save()
        painter.setClipRect(viewport.adjusted(-18, 0, 18, 0))
        for index in range(max(0, focus - 3), min(len(LYRICS), focus + 4)):
            text = LYRICS[index]
            y = anchor + (index - focus) * 44 - 22
            color = QColor(255, 255, 255) if index <= focus else self._future_lyric_color(viewport)
            text_layout = QTextLayout(text, font)
            text_layout.beginLayout()
            line = text_layout.createLine()
            line.setLineWidth(viewport.width())
            text_layout.endLayout()
            width = line.naturalTextWidth()
            x = viewport.left()
            if self.lyrics_alignment == "center":
                x += (viewport.width() - width) / 2
            image, padding = self._lyric_raster(text, font, viewport.width(), 30, text_layout, color)
            level = self._lyric_group_blur_level(index, focus)
            image = self._blurred_lyric_raster(image, level, color)
            painter.drawImage(QPointF(x - padding, y - padding), image)
        painter.restore()

    def paint_list(self, painter, layout):
        viewport = layout.viewport
        ink = self._scene_control_ink()
        painter.setPen(ink)
        painter.setFont(self._surface_font(12, QFont.Weight.DemiBold))
        painter.drawText(QRectF(20, 190, self.width() - 40, 28), Qt.AlignmentFlag.AlignVCenter, "播放列表")
        painter.save()
        painter.setClipRect(QRectF(0, 218, self.width(), self.height() - 314))
        for index, (title, artist) in enumerate(SONGS):
            y = 218 + index * 56
            if index == self.song_index:
                row = QRectF(12, y + 2, self.width() - 24, 52)
                self._clear_control_renderer.paint(painter, row, scene=self._surface_glass_backdrop(),
                    scene_bounds=QRectF(self.rect()), ink=ink, now=time.monotonic(), role="queue-selection",
                    kind="capsule", selected=True, level=self._surface_glass_level())
                row_ink = QColor("#0b0c0e")
            else:
                row_ink = QColor(ink)
            art = QRectF(20, y + 10, 36, 36)
            painter.save()
            clip = QPainterPath()
            clip.addRoundedRect(art, 5, 5)
            painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
            painter.drawImage(art, self.owner.album_art)
            painter.restore()
            painter.setPen(row_ink)
            painter.setFont(self._surface_font(15, QFont.Weight.DemiBold))
            painter.drawText(QRectF(72, y + 6, self.width() - 92, 24), Qt.AlignmentFlag.AlignVCenter, title)
            row_ink.setAlpha(round(row_ink.alpha() * .66))
            painter.setPen(row_ink)
            painter.setFont(self._surface_font(12, QFont.Weight.Medium))
            painter.drawText(QRectF(72, y + 29, self.width() - 92, 20), Qt.AlignmentFlag.AlignVCenter, artist)
        painter.restore()

    def paint_controls(self, painter, layout):
        back = self.posed_rect("library", layout.back)
        self._paint_surface_glass(painter, back, role="library")
        ink = self._scene_control_ink()
        paint_music_source_icon(painter, back.adjusted(8, 8, -8, -8), "library_back", tint=ink)
        content = self.posed_rect("content_layout", layout.top[0])
        self._paint_surface_glass(painter, content, role="content-layout")
        self._paint_content_layout_icon(painter, content.adjusted(7, 7, -7, -7))
        if self.shelf:
            roles = ("audio-output", "playback-mode", "translation", "window-mode")
            keys = ("audio_output", "playback_mode", "translation", "return")
            for rect, role, key in zip(layout.top[1:], roles, keys):
                rect = self.posed_rect(key, rect)
                self._paint_surface_glass(painter, rect, role=role)
                icon = rect.adjusted(8, 8, -8, -8)
                if role == "audio-output":
                    paint_audio_output_status(painter, rect, (), ink, count=0)
                elif role == "playback-mode":
                    paint_music_source_icon(painter, icon, "sequence", tint=ink)
                elif role == "translation":
                    painter.save()
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(ink)
                    painter.drawPath(self._translation_symbol_path(icon))
                    painter.restore()
                else:
                    self._paint_window_mode_icon(painter, icon, self._fullscreen, ink)
        footer = (self.footer_visibility == "always" or self.footer_hover) and not (self.shelf and self.mode == "playlist")
        if not footer:
            return
        y = self.height() - 88
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 31))
        painter.drawRoundedRect(QRectF(20, y, self.width() - 40, 2), 1, 1)
        painter.setBrush(QColor("white"))
        painter.drawRoundedRect(QRectF(20, y, (self.width() - 40) * self.owner.media_position_seconds / 32, 2), 1, 1)
        painter.setFont(self._surface_font(12, QFont.Weight.Medium))
        painter.setPen(QColor(255, 255, 255, 115))
        position = int(self.owner.media_position_seconds)
        elapsed = f"{position // 60}:{position % 60:02d}"
        painter.drawText(QRectF(20, y - 20, 80, 18), Qt.AlignmentFlag.AlignLeft, elapsed)
        painter.drawText(QRectF(self.width() - 100, y - 20, 80, 18), Qt.AlignmentFlag.AlignRight, "0:32")
        play = self.posed_rect("media:toggle_play_pause", layout.play)
        favorite = self.posed_rect("collection", layout.favorite)
        self._paint_transport_icon(painter, play, "toggle_play_pause", ink)
        self._paint_surface_glass(painter, favorite, role="collection")
        heart = "heart_filled" if self.song_index in self.liked_songs else "heart_outline"
        paint_music_source_icon(painter, QRectF(favorite.x() + 8, favorite.y() + 5, 20, 20), heart, tint=ink)
        paint_music_source_icon(painter, QRectF(favorite.x() + 43, favorite.y() + 5, 20, 20), "playlist_add", tint=ink)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_N:
            self.resize(360, 640)
        elif key == Qt.Key.Key_W:
            self.resize(960, 540)
        elif key in (Qt.Key.Key_L, Qt.Key.Key_P, Qt.Key.Key_Tab):
            if key == Qt.Key.Key_Tab:
                self.mode = "playlist" if self.mode == "lyrics" else "lyrics"
            else:
                self.mode = "lyrics" if key == Qt.Key.Key_L else "playlist"
            self.set_content_layout(self.mode, animate=False)
            self._finish_content_layout()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.select_song(self.song_index + (1 if key == Qt.Key.Key_Right else -1))
        elif Qt.Key.Key_1 <= key <= Qt.Key.Key_4:
            self.set_cover(key - Qt.Key.Key_1)
        elif key == Qt.Key.Key_Space:
            self.owner.media_is_playing = not self.owner.media_is_playing
        elif key == Qt.Key.Key_H:
            self.hints = not self.hints
        else:
            super().keyPressEvent(event)
        self.update()

    def mouseMoveEvent(self, event):
        if not self.narrow:
            super().mouseMoveEvent(event)
            return
        self._glass_feedback.move(event.position())
        self.shelf = event.position().y() < 56
        self.hovered_glass = ""
        for group in self.glass_groups():
            for key, rect in group.items():
                if rect.contains(event.position()):
                    self.hovered_glass = key
        self.footer_hover = event.position().y() >= self.height() - 120
        self.update()

    def mousePressEvent(self, event):
        if not self.narrow:
            super().mousePressEvent(event)
            return
        layout = NarrowLayout(self.width(), self.height())
        footer_visible = (self.footer_visibility == "always" or self.footer_hover) and not (
            self.shelf and self.mode == "playlist"
        )
        for group in self.glass_groups():
            for key, rect in group.items():
                if rect.contains(event.position()):
                    self._glass_feedback.press(key, event.position(), time.monotonic())
        if layout.back.contains(event.position()):
            self.resize(960, 540)
        elif layout.top[0].contains(event.position()):
            self.mode = "playlist" if self.mode == "lyrics" else "lyrics"
            self.set_content_layout(self.mode, animate=False)
            self._finish_content_layout()
        elif footer_visible and layout.play.contains(event.position()):
            self.owner.media_is_playing = not self.owner.media_is_playing
        elif (footer_visible and layout.favorite.contains(event.position())
              and event.position().x() < layout.favorite.center().x()):
            if self.song_index in self.liked_songs:
                self.liked_songs.remove(self.song_index)
            else:
                self.liked_songs.add(self.song_index)
        elif self.mode == "playlist" and 218 <= event.position().y() < self.height() - 96:
            index = int((event.position().y() - 218) // 56)
            if index < len(SONGS):
                self.select_song(index)
        self.update()

    def mouseReleaseEvent(self, event):
        if not self.narrow:
            super().mouseReleaseEvent(event)
            return
        self._glass_feedback.release()
        self.update()

    def leaveEvent(self, event):
        self.hovered_glass = ""
        self.shelf = False
        self.footer_hover = False
        super().leaveEvent(event)


def make_owner():
    owner = _TransitionOwner()
    owner.media_track_id = "owned:netease:1"
    owner.track_title, owner.artist = SONGS[0]
    owner._song_collection_controller = Collection()
    owner._music_library_back_callback = Mock()
    provider = _Provider()
    provider.get_owned_playback_queue = lambda: (0, 0, tuple(
        OnlineSong("netease", str(i), title, artist=artist)
        for i, (title, artist) in enumerate(SONGS)
    ))
    owner.data_provider = provider
    owner.settings_getter = lambda key, default=None: {"v2_music_glass_level": 50}.get(key, default)
    owner.timed_lyrics = [(i * 4.0, text) for i, text in enumerate(LYRICS)]
    owner.lyrics = LYRICS
    owner.media_position_seconds = 5.6
    return owner


def main(variant, footer_visibility="always", lyrics_alignment="left"):
    parser = argparse.ArgumentParser()
    parser.add_argument("--grab", type=Path)
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--bench", type=int)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    owner = make_owner()
    window = NarrowStudyWindow(owner, variant, footer_visibility, lyrics_alignment)
    window.show()
    window._timer.stop()
    if args.grab:
        args.grab.mkdir(parents=True, exist_ok=True)
        window.hints = False
        window.freeze_clock = True
        window.owner.media_position_seconds = 5.6
        jobs = [("narrow_lyrics", "lyrics", False, 0, False),
                ("narrow_list", "playlist", False, 0, False),
                ("narrow_lyrics_hover", "lyrics", True, 0, False),
                ("wide_lyrics", "lyrics", False, 0, True)]
        jobs.extend((f"cover{i + 1}", "lyrics", False, i, False) for i in range(4))
        state = {"index": 0}

        def grab_next():
            index = state["index"]
            if index >= len(jobs):
                if not args.seconds:
                    app.exit(0)
                return
            label, mode, shelf, cover, wide = jobs[index]
            window.mode = mode
            window.shelf = shelf
            window.footer_hover = shelf
            window.set_cover(cover)
            window.resize(960, 540) if wide else window.resize(360, 640)
            window.set_content_layout(mode, animate=False)
            window._finish_content_layout()
            window.update()

            def save():
                name = f"narrow3_{variant}_{label}.png"
                window.grab().save(str(args.grab / name))
                count = 2 + 4 * int(shelf) + int(mode == "playlist")
                count += 2 * int((footer_visibility == "always" or shelf) and not (shelf and mode == "playlist"))
                print(f"saved {name} mode={mode} narrow={window.narrow} cover={cover + 1} glass_count={count}", flush=True)
                state["index"] += 1
                QTimer.singleShot(10, grab_next)

            QTimer.singleShot(1000, save)
        QTimer.singleShot(100, grab_next)
    if args.seconds:
        QTimer.singleShot(round(args.seconds * 1000), lambda: app.exit(0))
    if args.bench:
        def report():
            samples = sorted(window.samples[-args.bench:])
            if samples:
                print(f"paint (mixed; incomplete benchmark): median={samples[len(samples)//2]:.2f} ms "
                      f"p95={samples[int((len(samples) - 1) * .95)]:.2f} ms n={len(samples)} "
                      f"first={window.samples[0]:.2f} ms", flush=True)
        app.aboutToQuit.connect(report)
    app.exec()
    window.shutdown()
    owner.close()
