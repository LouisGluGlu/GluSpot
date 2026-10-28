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

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QTextLayout
from PySide6.QtWidgets import QApplication

from api.models import ColorToken, TimedLyricLine, TimedLyricUnit
from api.online_music import OnlineSong
from api.providers import WindowsMediaProvider
from local.audio_output_icons import paint_audio_output_status
from local.music_source_icons import paint_music_source_icon
from local.music_surface_window import MusicSurfaceWindow
from local.music_lyric_reading import LyricReadingRenderer
from test_music_surface_transition import _TransitionOwner
from test_owned_playback_surface import _Provider
from test_song_collection_surface import Collection
from layout import NarrowLayout
from motion import EaseOutFade, Visibility
from veil import VeilOverlay
from local.personal_dj_icon import paint_personal_dj_icon

SONGS = [("夜航的信", "林间电台"), ("晚风经过", "陈小雨"), ("云与海之间", "南方来信"), ("星光落下来", "木子"), ("慢慢的旅程", "夏日回声"), ("下一站黎明", "白昼乐队"), ("你听见了吗", "无名旅人"), ("再见之前", "海边房间")]
LYRICS = ["在夜色中慢慢走", "把心事交给晚风", "那些没有说完的话", "会在星光里重逢", "让时间轻轻流过", "我们还在这条路上", "等天边亮起微光", "再一起迎接远方", "城市把灯光放低", "远处有汽笛回响", "我把这首歌留给你", "天亮以前不要散场"]
# A middle-of-song fixture keeps real rows above and below both control bands.
# Repetition belongs only to this synthetic study, never to a provider.
DEMO_POSITION = len(LYRICS) * 4.0 + 5.6
# Explicit timings belong to the synthetic demo lyrics only.
TIMED_LYRICS = tuple(
    TimedLyricLine(
        index * 4.0, text,
        tuple(TimedLyricUnit(index * 4.0 + offset * 3.6 / len(text),
                             index * 4.0 + (offset + 1) * 3.6 / len(text), character)
              for offset, character in enumerate(text)),
        "word",
    )
    for index, text in enumerate(LYRICS * 4)
)
COVERS = ("night_sea.jpg", "sunset_city.jpg", "pink_forest.jpg", "clip.jpg")


class NarrowStudyWindow(MusicSurfaceWindow):
    def __init__(self, owner, variant, lyrics_alignment):
        super().__init__(owner)
        self.variant = variant
        self.lyrics_alignment = lyrics_alignment
        self.narrow = True
        self.mode = "lyrics"
        self.cover_index = 0
        self.song_index = 0
        self.liked_songs = set()
        self.playlist_songs = set()
        self.shelf = False
        self.footer_hover = False
        self.dj_available = True
        self.dj_enabled = False
        self.dj_speaking = False
        self.dj_motion = EaseOutFade(1.0)
        self.loop_mode = 0
        self.visibility = Visibility(variant == "B")
        self.veil = VeilOverlay()
        self.veil_key = None
        self._lyric_static_key = None
        self._lyric_static_image = None
        self._content_frame_key = None
        self._content_frame = None
        self._controls_key = None
        self._controls_bands = None
        self._controls_at = -float("inf")
        self._list_layer_key = None
        self._list_layer = None
        self._list_layer_at = -float("inf")
        self._fade_background = None
        self._fade_background_key = None
        self.touch_active = False
        self.hovered_glass = ""
        self.hints = True
        self.freeze_clock = False
        self.cover_images = [QImage(str(Path(__file__).parent / "covers" / name)) for name in COVERS]
        self.reading_renderer = LyricReadingRenderer()
        self.samples = []
        self.bench_phase = None
        self.bench_samples = {name: [] for name in ("steady", "press", "visibility")}
        self.cold_paint = None
        self.last_tick = time.monotonic()
        self.frame_times = []
        self.setMinimumSize(320, 560)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.move(80, 80)
        self.resize(360, 640)
        self.set_cover(0)
        self._sync_owned_playback_mode()
        self.set_content_layout("lyrics", animate=False)
        self._layout_animation.stop()
        self._finish_content_layout()
        self._reset_control_idle()
        self.refresh_veil()
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
            loop = len(TIMED_LYRICS) * 4.0
            self.owner.media_position_seconds = (self.owner.media_position_seconds + now - self.last_tick) % loop
        self.last_tick = now
        self.visibility.advance(now, self.shelf or self.touch_active, self.footer_hover,
                                self.mode == "playlist")
        self.dj_motion.retarget(float(self.dj_available), now, .18)
        feedback = self._glass_feedback
        if (self.hovered_glass or any(feedback.hover.values())
                or any(state.active(now) for state in feedback.controls.values())):
            feedback.advance(self.glass_groups(), self.hovered_glass, now)
        else:
            # Resting springs have no work. Keep their clock current so the
            # next real input starts with the same elapsed time as before.
            feedback.last_tick = now
        if self.narrow:
            self.refresh_fade_background(now)
            self.refresh_veil()
            self.refresh_controls(now)
        self.update()
        names = {"A": "静态", "B": "悬停"}
        fps = len([t for t in self.frame_times if now - t < 1.0])
        self.setWindowTitle(f"GluSpot 窄窗v3 · 方案{self.variant} {names[self.variant]} · {fps} fps")

    def glass_groups(self):
        layout = NarrowLayout(self.width(), self.height())
        top_keys = ("content_layout", "audio_output", "playback_mode", "translation", "return")
        top = {key: rect for key, rect in zip(top_keys, layout.top)}
        now = time.monotonic()
        top = {key: rect for index, (key, rect) in enumerate(top.items())
               if (self.visibility.footer if index == 0 else self.visibility.top[index - 1]).value(now) > .001}
        loop, mode = layout.utility(self.dj_motion.value(now))
        footer = {"media:previous": layout.previous, "media:toggle_play_pause": layout.play,
                  "media:next": layout.next, "collection": layout.favorite,
                  "footer_loop": loop, "footer_layout": mode}
        if self.dj_available:
            footer["personal_dj"] = layout.dj
        if self.visibility.footer.value(now) <= .001:
            footer = {}
        return [top, {"library": layout.back} if self.visibility.footer.value(now) > .001 else {}, footer]

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
        if self.narrow and hasattr(self, "veil"):
            self.refresh_veil()
            self.refresh_controls(time.monotonic())

    def paintEvent(self, event):
        start = time.perf_counter()
        if not self.narrow:
            super().paintEvent(event)
        else:
            self.paint_narrow()
        now = time.monotonic()
        self.frame_times = [t for t in self.frame_times if now - t < 1.0]
        self.frame_times.append(now)
        elapsed = (time.perf_counter() - start) * 1000
        if self.cold_paint is None:
            self.cold_paint = elapsed
        elif self.bench_phase is not None:
            self.bench_samples[self.bench_phase].append(elapsed)
        self.samples.append(elapsed)
        self.samples = self.samples[-2000:]

    def paint_narrow(self):
        now = time.monotonic()
        self._frame_control_color_at = now
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        palette = self._art_palette()
        fading = not self.visibility.settled(now)
        background_key = (self._background_cache.cacheKey(), self.width(), self.height(),
                          self.devicePixelRatioF())
        if fading and background_key == self._fade_background_key:
            painter.drawImage(QPointF(0, 0), self._fade_background)
        else:
            self._paint_background(painter, QRectF(self.rect()), QColor(palette.primary), QColor(palette.secondary), now,
                                   ambient_colors=palette.ambient_colors, ambient_weights=palette.ambient_weights,
                                   advance_frame=not fading)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        visibility = self.visibility.footer.value(now)
        layout = NarrowLayout(self.width(), self.height())
        content = self.content_image(layout, include_current=False)
        self.veil.paint(painter, content, self.veil_key, visibility,
                        scroll_delta=self.veil_scroll_delta)
        if self.mode == "lyrics":
            # The timed reading run always stays in the clear middle. Paint
            # only that run live; surrounding, stationary rows reuse pixels.
            self.paint_lyrics(painter, layout, current_only=True)
        if visibility > .001 and self._controls_bands is not None:
            painter.save()
            painter.setOpacity(visibility)
            painter.drawImage(QPointF(0, 0), self._controls_bands[0])
            painter.drawImage(QPointF(0, self.height() - VeilOverlay.BOTTOM_HEIGHT), self._controls_bands[1])
            painter.restore()
        if self.hints:
            painter.setFont(self._surface_font(12, QFont.Weight.Medium))
            painter.setPen(QColor(255, 255, 255, 120))
            painter.drawText(QRectF(4, self.height() - 13, self.width() - 8, 13), Qt.AlignmentFlag.AlignCenter,
                             "L 歌词  P 列表  N/W 窄/宽  1–4 封面  H 隐藏")
        painter.end()

    def content_image(self, layout, visual_focus=None, *, veil_source=False,
                      include_current=True, refresh=False):
        if self.mode == "playlist":
            # Queue data is static between demo actions. Its selected glass
            # face is prepared by refresh_veil at a bounded 10Hz refresh.
            # Paint consumes those pixels without rasterizing glass again.
            key = (self.width(), self.height(), self.devicePixelRatioF(),
                   self.song_index, self._scene_control_ink().rgba())
            now = time.monotonic()
            if (self._list_layer is not None
                    and (not refresh or (key == self._list_layer_key
                         and (not self.visibility.settled(now)
                              or now - self._list_layer_at < .1)))):
                return self._list_layer
        else:
            focus, visual = self.lyric_focus()
            if visual_focus is not None:
                visual = visual_focus
            frame_key = (self.width(), self.height(), self.devicePixelRatioF(),
                         layout.lyric_font, focus, visual, self.lyrics_alignment,
                         self._future_lyric_color(layout.viewport).rgba(),
                         self.owner.media_position_seconds if include_current else None,
                         include_current, veil_source)
            if frame_key == self._content_frame_key:
                return self._content_frame
        ratio = self.devicePixelRatioF()
        image = QImage(round(self.width() * ratio), round(self.height() * ratio),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        layer = QPainter(image)
        layer.setRenderHint(QPainter.RenderHint.Antialiasing)
        layer.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.mode == "lyrics":
            layer.save()
            layer.setClipRect(layout.viewport.adjusted(-18, 0, 18, 0))
            base_key = (self.width(), self.height(), ratio, layout.lyric_font,
                        focus, self.lyrics_alignment, veil_source,
                        self._future_lyric_color(layout.viewport).rgba())
            if base_key != self._lyric_static_key:
                guard = layout.lyric_step
                base = QImage(image.width(), round((self.height() + guard * 2) * ratio),
                              image.format())
                base.setDevicePixelRatio(ratio)
                base.fill(Qt.GlobalColor.transparent)
                with QPainter(base) as static:
                    static.setRenderHint(QPainter.RenderHint.Antialiasing)
                    static.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    static.translate(0, guard)
                    self.paint_lyrics(static, layout, visual_focus=focus,
                                      static_only=True, overscan=guard,
                                      veil_source=veil_source)
                self._lyric_static_key, self._lyric_static_image = base_key, base
            layer.drawImage(QPointF(0, (focus - visual) * layout.lyric_step - layout.lyric_step),
                            self._lyric_static_image)
            if include_current:
                self.paint_lyrics(layer, layout, visual_focus=visual, current_only=True)
            layer.restore()
            area = layout.viewport
            fade = min(88, area.height() * .16)
        else:
            self.paint_list(layer, layout)
            area = QRectF(0, layout.list_top, self.width(), max(1, layout.viewport.bottom() - layout.list_top))
            fade = min(72, area.height() * .14)
        layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        gradient = QLinearGradient(0, area.top(), 0, area.bottom())
        edge = min(.5, fade / max(1, area.height()))
        gradient.setColorAt(0, QColor(255, 255, 255, 0))
        gradient.setColorAt(edge * .45, QColor(255, 255, 255, 150))
        gradient.setColorAt(edge, QColor(255, 255, 255, 255))
        gradient.setColorAt(1 - edge, QColor(255, 255, 255, 255))
        gradient.setColorAt(1 - edge * .45, QColor(255, 255, 255, 150))
        gradient.setColorAt(1, QColor(255, 255, 255, 0))
        if self.mode == "playlist":
            # Preserve the heading above the list's faded viewport.
            layer.fillRect(QRectF(0, 0, self.width(), area.top()), QColor("white"))
        layer.fillRect(area, gradient)
        layer.end()
        if self.mode == "playlist":
            self._list_layer_key, self._list_layer = key, image
            self._list_layer_at = now
        else:
            self._content_frame_key, self._content_frame = frame_key, image
        return image

    def refresh_controls(self, now):
        """Capture production control pixels outside paint; fade only blends.

        Keep the same sampled scene for the 300ms reveal. Resting controls
        refresh at 10Hz; input/feedback/layout changes invalidate immediately.
        """
        if self.variant == "A":
            return
        key = (self.width(), self.height(), self.devicePixelRatioF(),
               self._glass_feedback.pose_key(), self.dj_motion.value(now),
               self.dj_available, self.dj_enabled,
               int(now * 30) if self.dj_speaking else 0,
               self.loop_mode, self.mode, self.song_index, self.cover_index,
               self.owner.album_art.cacheKey(),
               self.song_index in self.liked_songs, self.owner.media_is_playing,
               self._scene_control_ink().rgba())
        hidden = (self.visibility.opacity.target <= 0
                  and self.visibility.opacity.value(now) <= .001)
        if hidden and key == self._controls_key:
            return
        fading = not self.visibility.settled(now)
        if (key == self._controls_key and self._controls_bands is not None
                and (fading or now - self._controls_at < .1)):
            return
        ratio = self.devicePixelRatioF()
        image = QImage(round(self.width() * ratio), round(self.height() * ratio),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        self._clear_control_renderer.begin_frame(now)
        with QPainter(image) as layer:
            layer.setRenderHint(QPainter.RenderHint.Antialiasing)
            layer.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            layout = NarrowLayout(self.width(), self.height())
            self.paint_header(layer, layout)
            self.paint_controls(layer, layout, 1.0)
        self._clear_control_renderer.end_frame()
        self._controls_bands = (image.copy(0, 0, image.width(), round(VeilOverlay.TOP_END * ratio)),
                                image.copy(0, round((self.height() - VeilOverlay.BOTTOM_HEIGHT) * ratio),
                                           image.width(), round(VeilOverlay.BOTTOM_HEIGHT * ratio)))
        self._controls_key, self._controls_at = key, now

    def refresh_fade_background(self, now):
        """Reuse the production background at native size for one 300ms fade.

        This is the same raster, expanded once outside paint rather than
        bilinearly sampling it on every blend frame. Steady A/B use the normal
        production background path; no background filter is replaced.
        """
        if self.visibility.settled(now):
            self._fade_background = self._fade_background_key = None
            return
        if self._background_cache.isNull():
            return
        key = (self._background_cache.cacheKey(), self.width(), self.height(),
               self.devicePixelRatioF())
        if key == self._fade_background_key:
            return
        ratio = self.devicePixelRatioF()
        image = QImage(round(self.width() * ratio), round(self.height() * ratio),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        palette = self._art_palette()
        with QPainter(image) as layer:
            layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            layer.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            self._paint_background(layer, QRectF(self.rect()), QColor(palette.primary),
                                   QColor(palette.secondary), now,
                                   ambient_colors=palette.ambient_colors,
                                   ambient_weights=palette.ambient_weights, advance_frame=False)
        self._fade_background_key, self._fade_background = key, image

    def lyric_focus(self):
        position = self.owner.media_position_seconds
        focus = min(len(TIMED_LYRICS) - 1, max(0, int(position // 4)))
        transition = min(1.0, max(0.0, (position - focus * 4.0) / .36))
        eased = 1.0 - (1.0 - transition) ** 3
        return focus, focus - (1.0 - eased) if focus else 0.0

    @property
    def veil_scroll_delta(self):
        visual = self.lyric_focus()[1] if self.mode == "lyrics" else 0.0
        step = NarrowLayout(self.width(), self.height()).lyric_step
        return -(visual - round(visual)) * step

    def refresh_veil(self):
        """Prepare only when layout/theme/DPR/current line/scroll bucket changes.

        Called from the timer/input/capture preparation, never paintEvent.
        Word highlighting is confined to the clear middle and is not a cache key.
        """
        layout = NarrowLayout(self.width(), self.height())
        if self.mode == "playlist":
            self.content_image(layout, refresh=True)
        focus, visual = self.lyric_focus()
        bucket = round(visual) if self.mode == "lyrics" else 0
        self.veil_key = (
            self.mode, self.width(), self.height(), layout.lyric_font,
            layout.lyric_step, self.lyrics_alignment, tuple(LYRICS), tuple(SONGS),
            self.song_index, self.cover_index, self.devicePixelRatioF(),
            self.owner.album_color.rgba(), self.owner.secondary_color.rgba(),
            self._future_lyric_color(layout.viewport).rgba(),
            self._scene_control_ink().rgba(), focus if self.mode == "lyrics" else 0, bucket,
        )
        # prepare accepts a source factory: a hit must not rasterize full content.
        self.veil.prepare(self.veil_key,
                          lambda: self.content_image(layout, bucket, veil_source=True,
                                                     include_current=False))

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
        painter.drawText(QRectF(column.x(), column.center().y() - 25, column.width(), 26), Qt.AlignmentFlag.AlignVCenter, title)
        painter.setFont(self._surface_font(13, QFont.Weight.Medium))
        painter.setPen(QColor(255, 255, 255, 142))
        artist = QFontMetricsF(painter.font()).elidedText(self.owner.artist, Qt.TextElideMode.ElideRight, int(column.width()))
        painter.drawText(QRectF(column.x(), column.center().y() + 5, column.width(), 20), Qt.AlignmentFlag.AlignVCenter, artist)

    def paint_lyrics(self, painter, layout, visual_focus=None, *,
                     static_only=False, current_only=False, overscan=0,
                     veil_source=False):
        viewport = layout.viewport
        position = self.owner.media_position_seconds
        focus, actual_visual_focus = self.lyric_focus()
        if visual_focus is None:
            visual_focus = actual_visual_focus
        anchor = layout.lyric_anchor
        step = layout.lyric_step
        row = layout.lyric_row
        font = self._surface_font(layout.lyric_font, QFont.Weight.Bold)
        metrics_layout = self.reading_renderer.whole_line(TIMED_LYRICS[focus].text, font)
        ascent = metrics_layout.lineAt(0).ascent()
        painter.save()
        painter.setClipRect(viewport.adjusted(-18, -overscan, 18, overscan))
        for index in ((focus,) if current_only else range(len(TIMED_LYRICS))):
            if (static_only and index == focus) or (current_only and index != focus):
                continue
            line = TIMED_LYRICS[index]
            y = anchor + (index - visual_focus) * step - ascent
            if y + row < viewport.top() - 16 - overscan or y > viewport.bottom() + 16 + overscan:
                continue
            x = viewport.left()
            if self.lyrics_alignment == "center":
                width = QFontMetricsF(font).horizontalAdvance(line.text)
                x += (viewport.width() - width) / 2
            if index != focus:
                text_layout = QTextLayout(line.text, font)
                text_layout.beginLayout()
                text_layout.createLine().setLineWidth(viewport.width())
                text_layout.endLayout()
                color = QColor(255, 255, 255) if index < focus else self._future_lyric_color(viewport)
                image, padding = self._lyric_raster(line.text, font, viewport.width(), row, text_layout, color)
                # Filter the same resting ink that the clear side displays.
                # A sharp-only veil source would revive distant rows midway
                # through the feather and expose a horizontal change in focus.
                level = 0 if abs(index - focus) <= 2 else self._lyric_group_blur_level(index, focus)
                image = self._blurred_lyric_raster(image, level, color)
                painter.drawImage(QPointF(x - padding, y - padding), image)
                continue
            self.reading_renderer.paint(
                painter, QRectF(x, y, viewport.width(), row), x, line.text, font,
                max(0.0, min(1.0, (position - line.time_seconds) / 4.0)),
                self._future_lyric_color(viewport), QColor(255, 255, 255),
                timed_units=line.units, timed_position=position,
                timed_line_end=line.time_seconds + 4.0,
            )
        painter.restore()

    def paint_list(self, painter, layout):
        viewport = layout.viewport
        step = layout.list_step(len(SONGS))
        top = layout.list_top
        ink = self._scene_control_ink()
        if layout.list_heading > 1:
            painter.setPen(ink)
            painter.setFont(self._surface_font(12, QFont.Weight.DemiBold))
            painter.drawText(QRectF(20, viewport.top(), self.width() - 40, 28), Qt.AlignmentFlag.AlignVCenter, "播放列表")
        painter.save()
        painter.setClipRect(QRectF(0, top, self.width(), max(0, viewport.bottom() - top)))
        for index, (title, artist) in enumerate(SONGS):
            y = top + index * step
            row_height = min(52.0, step - 4)
            if index == self.song_index:
                row = QRectF(12, y + 2, self.width() - 24, row_height)
                self._clear_control_renderer.paint(painter, row, scene=self._surface_glass_backdrop(),
                    scene_bounds=QRectF(self.rect()), ink=ink, now=time.monotonic(), role="queue-selection",
                    kind="capsule", selected=True, level=self._surface_glass_level(),
                    state=self._glass_feedback.state("queue-selection"),
                    hover=self._glass_feedback.hover.get("queue-selection", 0.0))
                row_ink = QColor("#0b0c0e")
            else:
                row_ink = QColor(ink)
            art_side = min(44.0, row_height - 8)
            art = QRectF(20, y + (row_height - art_side) / 2 + 2, art_side, art_side)
            painter.save()
            clip = QPainterPath()
            clip.addRoundedRect(art, 5, 5)
            painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
            painter.drawImage(art, self.cover_images[index % len(COVERS)])
            painter.restore()
            painter.setPen(row_ink)
            painter.setFont(self._surface_font(15, QFont.Weight.DemiBold))
            painter.drawText(QRectF(72, y + 6, self.width() - 92, 24), Qt.AlignmentFlag.AlignVCenter, title)
            row_ink.setAlpha(round(row_ink.alpha() * .66))
            painter.setPen(row_ink)
            painter.setFont(self._surface_font(12, QFont.Weight.Medium))
            painter.drawText(QRectF(72, y + 29, self.width() - 92, 20), Qt.AlignmentFlag.AlignVCenter, artist)
        painter.restore()

    def paint_controls(self, painter, layout, opacity):
        if opacity <= .001:
            return
        painter.save()
        painter.setOpacity(opacity)
        back = self.posed_rect("library", layout.back)
        self._paint_surface_glass(painter, back, role="library")
        ink = self._scene_control_ink()
        paint_music_source_icon(painter, back.adjusted(8, 8, -8, -8), "library_back", tint=ink)
        content = self.posed_rect("content_layout", layout.top[0])
        self._paint_surface_glass(painter, content, role="content-layout")
        self._paint_content_layout_icon(painter, content.adjusted(7, 7, -7, -7))
        painter.restore()
        roles = ("audio-output", "playback-mode", "translation", "window-mode")
        keys = ("audio_output", "playback_mode", "translation", "return")
        for index, (rect, role, key) in enumerate(zip(layout.top[1:], roles, keys)):
            if opacity <= .001:
                continue
            painter.save()
            painter.setOpacity(opacity)
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
            painter.restore()
        footer = opacity
        if footer <= .001:
            return
        painter.save()
        painter.setOpacity(footer)
        for command, rect in (("previous", layout.previous),
                              ("toggle_play_pause", layout.play), ("next", layout.next)):
            rect = self.posed_rect("media:" + command, rect)
            self._paint_transport_icon(painter, layout.transport_icon(rect), command, ink)
        favorite = self.posed_rect("collection", layout.favorite)
        self._clear_control_renderer.paint(
            painter, favorite, scene=self._surface_glass_backdrop(),
            scene_bounds=QRectF(self.rect()), ink=ink, now=time.monotonic(),
            role="collection", state=self._glass_feedback.state("collection"),
            kind="capsule", level=self._surface_glass_level())
        heart = "heart_filled" if self.song_index in self.liked_songs else "heart_outline"
        for index, icon in enumerate((heart, "playlist_add")):
            side = favorite.height() * (.63 if index == 0 else .61)
            center_x = favorite.left() + favorite.width() * (.25 + .5 * index)
            glyph = QRectF(center_x - side / 2, favorite.center().y() - side / 2, side, side)
            icon_ink = QColor("#ff3e4c") if index == 0 and self.song_index in self.liked_songs else ink
            paint_music_source_icon(painter, glyph, icon, tint=icon_ink)
        loop, mode = layout.utility(self.dj_motion.value(time.monotonic()))
        loop = self.posed_rect("footer_loop", loop)
        mode = self.posed_rect("footer_layout", mode)
        self._paint_surface_glass(painter, loop, role="playback-mode")
        paint_music_source_icon(painter, loop.adjusted(9, 9, -9, -9),
                                ("sequence", "shuffle", "repeat_one")[self.loop_mode], tint=ink)
        self._paint_surface_glass(painter, mode, role="content-layout")
        self._paint_content_layout_icon(painter, mode.adjusted(9, 9, -9, -9))
        if self.dj_available:
            dj = self.posed_rect("personal_dj", layout.dj)
            self._paint_surface_glass(painter, dj, role="dj-icon")
            if self.dj_enabled:
                painter.save()
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(52, 196, 112), 1.8))
                ring = dj.adjusted(1, 1, -1, -1)
                if self.dj_speaking:
                    painter.drawArc(ring, int(-time.monotonic() * 150 * 16), 80 * 16)
                else:
                    painter.drawEllipse(ring)
                painter.restore()
            paint_personal_dj_icon(painter, dj.adjusted(dj.width() * .18, dj.height() * .14,
                                   -dj.width() * .18, -dj.height() * .14), ink,
                                   elapsed=time.monotonic() if self.dj_speaking else 0, neutral=ink)
        painter.restore()

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
        elif key == Qt.Key.Key_D:
            self.dj_available = not self.dj_available
        elif key == Qt.Key.Key_S:
            self.dj_enabled = True
            self.dj_speaking = not self.dj_speaking
        elif key == Qt.Key.Key_H:
            self.hints = not self.hints
        else:
            super().keyPressEvent(event)
        if self.narrow:
            self.refresh_veil()
            self.refresh_controls(time.monotonic())
        self.update()

    def mouseMoveEvent(self, event):
        if not self.narrow:
            super().mouseMoveEvent(event)
            return
        self._glass_feedback.move(event.position())
        self.shelf = True
        self.hovered_glass = ""
        for group in self.glass_groups():
            for key, rect in group.items():
                if rect.contains(event.position()):
                    self.hovered_glass = key
        self.footer_hover = True
        self.update()

    def enterEvent(self, event):
        if self.narrow:
            self.shelf = True
            self.visibility.advance(time.monotonic(), True, True, self.mode == "playlist")
        super().enterEvent(event)

    def event(self, event):
        if getattr(self, "narrow", False):
            if event.type() == QEvent.Type.TouchBegin:
                self.touch_active = True
                if event.points():
                    self._press_at(event.points()[0].position())
                self.visibility.advance(time.monotonic(), True, True, self.mode == "playlist")
                self.update()
                event.accept()
                return True
            elif event.type() == QEvent.Type.TouchUpdate:
                event.accept()
                return True
            elif event.type() in (QEvent.Type.TouchEnd, QEvent.Type.TouchCancel):
                self.touch_active = False
                self._glass_feedback.release()
                self.visibility.advance(time.monotonic(), self.shelf, self.footer_hover,
                                        self.mode == "playlist")
                self.update()
                event.accept()
                return True
        return super().event(event)

    def mousePressEvent(self, event):
        if not self.narrow:
            super().mousePressEvent(event)
            return
        self._press_at(event.position())

    def _press_at(self, position):
        visibility = self.visibility.footer.value(time.monotonic())
        self.visibility.advance(time.monotonic(), True, True, self.mode == "playlist")
        layout = NarrowLayout(self.width(), self.height(), visibility)
        footer_visible = visibility > .001
        for group in self.glass_groups():
            for key, rect in group.items():
                if rect.contains(position):
                    self._glass_feedback.press(key, position, time.monotonic())
        if footer_visible and layout.back.contains(position):
            self.resize(960, 540)
        elif footer_visible and layout.top[0].contains(position):
            self.mode = "playlist" if self.mode == "lyrics" else "lyrics"
            self.set_content_layout(self.mode, animate=False)
            self._finish_content_layout()
        elif footer_visible and layout.previous.contains(position):
            self.select_song(self.song_index - 1)
        elif footer_visible and layout.next.contains(position):
            self.select_song(self.song_index + 1)
        elif footer_visible and layout.play.contains(position):
            self.owner.media_is_playing = not self.owner.media_is_playing
        elif footer_visible and layout.favorite.contains(position):
            if position.x() < layout.favorite.center().x():
                if self.song_index in self.liked_songs:
                    self.liked_songs.remove(self.song_index)
                else:
                    self.liked_songs.add(self.song_index)
            else:
                self.playlist_songs.add(self.song_index)
        elif footer_visible and layout.utility(self.dj_motion.value(time.monotonic()))[0].contains(position):
            self.loop_mode = (self.loop_mode + 1) % 3
        elif footer_visible and layout.utility(self.dj_motion.value(time.monotonic()))[1].contains(position):
            self.mode = "playlist" if self.mode == "lyrics" else "lyrics"
            self.set_content_layout(self.mode, animate=False)
            self._finish_content_layout()
        elif footer_visible and self.dj_available and layout.dj.contains(position):
            self.dj_enabled = not self.dj_enabled
            if not self.dj_enabled:
                self.dj_speaking = False
        elif self.mode == "playlist" and layout.list_top <= position.y() < layout.viewport.bottom():
            index = int((position.y() - layout.list_top) // layout.list_step(len(SONGS)))
            if index < len(SONGS):
                self.select_song(index)
        if self.narrow:
            self.refresh_veil()
            self.refresh_controls(time.monotonic())
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
    owner.timed_lyrics = [(line.time_seconds, line.text) for line in TIMED_LYRICS]
    owner.lyrics = [line.text for line in TIMED_LYRICS]
    owner.media_position_seconds = DEMO_POSITION
    return owner


def main(variant, lyrics_alignment="left"):
    parser = argparse.ArgumentParser()
    parser.add_argument("--grab", type=Path)
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--bench", type=int)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    owner = make_owner()
    window = NarrowStudyWindow(owner, variant, lyrics_alignment)
    window.show()
    window._timer.stop()
    if args.grab:
        args.grab.mkdir(parents=True, exist_ok=True)
        window.hints = False
        window.freeze_clock = True
        window.owner.media_position_seconds = DEMO_POSITION
        jobs = [("narrow_lyrics", "lyrics", variant == "B", 0, False),
                ("narrow_list", "playlist", variant == "B", 0, False),
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
            started = time.monotonic()

            def save():
                name = f"narrow3_{variant}_{label}.png"
                window.grab().save(str(args.grab / name))
                count = len(window._clear_control_renderer.seen_roles)
                print(f"saved {name} mode={mode} narrow={window.narrow} cover={cover + 1} glass_count={count}", flush=True)
                state["index"] += 1
                QTimer.singleShot(10, grab_next)

            def wait_until_settled():
                now = time.monotonic()
                ready = window.visibility.settled(now)
                if shelf and now - started < .4:
                    ready = False
                if not shelf:
                    if window.visibility.footer.value(now) > .001:
                        ready = False
                if not ready:
                    QTimer.singleShot(33, wait_until_settled)
                    return
                window.refresh_veil()
                window.refresh_controls(now)
                window.repaint()
                window.repaint()
                save()

            QTimer.singleShot(33, wait_until_settled)
        QTimer.singleShot(100, grab_next)
    if args.seconds and not args.bench:
        QTimer.singleShot(round(args.seconds * 1000), lambda: app.exit(0))
    if args.bench:
        phases = ("steady", "press", "visibility")
        bench = {"index": 0, "started": time.monotonic(), "pressed": False}
        window.study_timer.setInterval(10)

        def drive_bench():
            index = bench["index"]
            if index >= len(phases):
                app.exit(0)
                return
            phase = phases[index]
            if window.bench_phase != phase:
                window.bench_phase = phase
                bench["started"] = time.monotonic()
                window.shelf = False
                window.footer_hover = False
                window._glass_feedback.release()
            elapsed = time.monotonic() - bench["started"]
            if phase == "press":
                held = elapsed % .8 < .35
                if held and not bench["pressed"]:
                    rect = NarrowLayout(window.width(), window.height()).top[0]
                    window._glass_feedback.press("content_layout", rect.center(), time.monotonic())
                elif not held and bench["pressed"]:
                    window._glass_feedback.release()
                bench["pressed"] = held
            elif phase == "visibility":
                window.shelf = elapsed % 2.2 < 1.0
                window.footer_hover = window.shelf
            window.tick()
            window.repaint()
            if len(window.bench_samples[phase]) >= args.bench:
                bench["index"] += 1
            QTimer.singleShot(0, drive_bench)

        def report():
            print(f"cold first paint={window.cold_paint:.2f} ms", flush=True)
            for phase in phases:
                samples = sorted(window.bench_samples[phase])
                if samples:
                    print(f"{phase}: median={samples[len(samples)//2]:.2f} ms "
                          f"p95={samples[int((len(samples) - 1) * .95)]:.2f} ms n={len(samples)}", flush=True)
            print("wide/narrow animation: unavailable; no samples", flush=True)
            print("bench overrides --seconds until sample collection finishes", flush=True)

        app.aboutToQuit.connect(report)
        QTimer.singleShot(0, drive_bench)
    app.exec()
    window.shutdown()
    owner.close()
