"""Experimental narrow composition using the production music surface renderer."""
from __future__ import annotations

import argparse
import math
import logging
from bisect import bisect_right
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QTextLayout, QTransform
from PySide6.QtWidgets import QApplication

from api.models import ColorToken, TimedLyricLine, TimedLyricUnit
from api.online_music import OnlineSong
from api.providers import WindowsMediaProvider
from local.audio_output_icons import paint_audio_output_status
from local.music_source_icons import paint_music_source_icon
from local.music_surface_window import MusicSurfaceWindow
from local.music_lyric_reading import LyricReadingRenderer
from local.music_lyric_motion import MusicLyricFrame
from local.music_lyric_presentation import (
    split_display_layers, INTRO_COUNTDOWN_TEXT, intro_dot_radius, paint_intro_countdown,
    LYRIC_PREVIOUS_ALPHA, LYRIC_NEXT_ALPHA,
)
if __package__:
    from .layout import NarrowLayout
    from .motion import EaseOutFade, Visibility
    from .veil import VeilOverlay
else:
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
# Active-word ink is the only frame content that reads the live clock.  Key it
# on a 50ms bucket so a paused source rebuilds nothing and advancing lyrics
# still refresh the reading front well inside the 120ms seek budget.
INK_BUCKET_SECONDS = .05


class NarrowStudyWindow(MusicSurfaceWindow):
    def __init__(self, owner, variant, lyrics_alignment, *, live=False):
        self.live = live
        self._source_lines_key = None
        self._source_lines = ()
        self._real_layout_key = None
        self._real_blocks = ()
        self._pointer_active_until = 0.0
        self._live_press = None
        self._painted_live_model = None
        self._painted_live_hit_pose = None
        self._diagnostics = dict(samples=0, commits=0, swap_commits=0,
                                 snap_frames=0, noop_paints=0, visible_windows=0,
                                 hidden_owner_paints=0, controls_retargets=0,
                                 lyric_layer_builds=0, paints=0)
        self._diagnostic_target = 0.0
        self._commit_key = None
        self._pointer_anchor = None
        self._pointer_hover = False
        self._pointer_exit_at = None
        self._focus_reveal = False
        self._committed_track = None
        self._settled_epoch = None
        self._snap_change = None
        self._diagnostics.update(settle_commits=0, first_sample_settle_outlier=0)
        self._diagnostics.update(stale_frames=0)
        self._painted_track = None
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
        self.dj_available = not live
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
        self._content_frames = {}
        self._scroll_content_key = None
        self._scroll_endpoints = None
        self._scroll_surface_key = None
        self.scroll_surface = None
        self._scroll_guard_key = None
        self._scroll_guard = 0
        self._scroll_frame = None
        self._scroll_fade_generation = None
        self._intro_dot_key = None
        self._intro_dot_image = None
        self._controls_key = None
        self._controls_bands = None
        self._controls_at = -float("inf")
        self._list_layer_key = None
        self._list_layer = None
        self._list_layer_at = -float("inf")
        self._fade_background = None
        self._fade_background_key = None
        self._fade_anchor_key = None
        self._fade_veil_key = None
        self._fade_content_key = None
        self._fade_content_frames = None
        self._current_ink_key = None
        self._current_ink_image = None
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
        if not live:
            self.set_cover(0)
        self._sync_owned_playback_mode()
        self.set_content_layout("lyrics", animate=False)
        self._finish_content_layout()
        self._reset_control_idle()
        self.refresh_veil()
        self.study_timer = QTimer(self)
        self.study_timer.timeout.connect(self.tick)
        class OwnerPaintObserver(QObject):
            def eventFilter(observer, watched, event):
                if event.type() == QEvent.Type.Paint and not watched.isVisible():
                    self._diagnostics['hidden_owner_paints'] += 1
                return False
        self._owner_paint_observer = OwnerPaintObserver(self)
        self.owner.installEventFilter(self._owner_paint_observer)
        if not self.live:
            self.study_timer.start(40)

    def diagnostic_snapshot(self):
        result = dict(self._diagnostics)
        result['visible_windows'] = sum(
            widget.isVisible() and not widget.isMinimized()
            for widget in QApplication.topLevelWidgets()
            if isinstance(widget, MusicSurfaceWindow) or widget is self.owner)
        result['lyric_bounds'] = NarrowLayout(self.width(), self.height()).viewport.getRect()
        result['painted_track'] = self._painted_track
        result['capture_epoch_id'] = getattr(self.owner, 'media_capture_epoch_id', 0)
        result['is_first_sample'] = getattr(self.owner, 'media_is_first_sample', False)
        result['reveal_progress'] = self.visibility.progress
        result['dim'], result['blur'] = self.veil.progress(self.visibility.progress)
        result['veil_cache_hit_ratio'] = self.veil.hits / max(1, self.veil.hits + self.veil.misses)
        result['controls_translate'] = 0.
        result['controls_scale'] = 1.
        return result

    def playback_position(self):
        if not self.live:
            return self.owner.media_position_seconds
        # The owner publishes this offset-applied position from its shared
        # lyric tick. Rendering must never project or validate another clock.
        return self.owner.lyric_last_sync_position

    def ink_position(self):
        """Clock granularity the active-word ink actually reads.

        The frame key must not carry the exact live clock: the layer then
        rebuilt on every tick even when its content fields were identical.
        An idle row (scroll stopped, reading front complete) paints the same
        pixels at every clock value, so it keys on the line start. A row still
        being read keys on a nearest 50ms bucket, which stays well inside the
        120ms seek budget and keeps the bucket value itself stable.
        """
        position = self.playback_position()
        if not self.live:
            return position
        lines = self.source_lines()
        if not lines:
            return 0.0
        focus, visual = self.lyric_focus()
        focus = min(len(lines) - 1, max(0, focus))
        start = float(lines[focus].time_seconds)
        end = (float(lines[focus + 1].time_seconds) if focus + 1 < len(lines)
               else max(start + 1.0, self.owner.media_duration_seconds))
        if math.floor(visual) == focus and (position <= start or position >= end):
            return start
        # Nearest bucket keeps the ink within half the bucket of the clock;
        # the key compares exactly, so the bucket value itself must be stable.
        # Step in integer milliseconds: position/bucket lands just below an
        # integer for ordinary clock values and would round one bucket early.
        return round(position * 1000.0 / (INK_BUCKET_SECONDS * 1000)) / (1000.0 / (INK_BUCKET_SECONDS * 1000))

    def source_lines(self):
        if not self.live:
            return TIMED_LYRICS
        track_key = self._visual_track_identity()
        if track_key != self._browse_track_key:
            self._browse_track_key = track_key
            self._clear_lyric_browse()
            self._hovered_lyric_index = None
        _text, timed = self._display_lyrics()
        key = (track_key, tuple(timed), self._display_timed_units,
               self._display_timed_kinds)
        if key != self._source_lines_key:
            units = self._display_timed_units
            kinds = self._display_timed_kinds
            self._source_lines = tuple(
                TimedLyricLine(start, text, units[index] if index < len(units) else (),
                               kinds[index] if index < len(kinds) else "line")
                for index, (start, text) in enumerate(timed)
            )
            self._source_lines_key = key
        return self._source_lines

    def queue_snapshot(self):
        if not self.live:
            return (0, self.song_index, tuple(
                OnlineSong("netease", str(i), title, artist=artist)
                for i, (title, artist) in enumerate(SONGS)))
        getter = getattr(self.owner.data_provider, "get_owned_playback_queue", None)
        return getter() if callable(getter) else None

    def queue_rows(self):
        snapshot = self.queue_snapshot()
        return snapshot[2] if snapshot is not None else ()

    def showEvent(self, event):
        super().showEvent(event)
        if self.live:
            if hasattr(self, "study_timer") and not self.isMinimized():
                self.study_timer.start(40)

    def hideEvent(self, event):
        if self.live and hasattr(self, "study_timer"):
            self.study_timer.stop()
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if getattr(self, "live", False) and hasattr(self, "study_timer"):
            if self.isVisible() and not self.isMinimized():
                self.study_timer.start(40)
            else:
                self.study_timer.stop()

    def closeEvent(self, event):
        if self.live:
            self.study_timer.stop()
            self.shutdown()
            event.accept()
            return
        super().closeEvent(event)

    def update(self, *args):
        # Production material completions and layout animations request their
        # own (sometimes partial) paints. The narrow compositor advances the
        # whole background, so those paints must share the study's full-frame
        # cadence rather than interleave patches from different frames.
        timer = getattr(self, "study_timer", None)
        if self.live and timer is not None and timer.isActive():
            return
        return super().update(*args)

    def set_cover(self, index):
        if self.live:
            return
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
        if self.live:
            snapshot = self.queue_snapshot()
            if snapshot is not None and 0 <= index < len(snapshot[2]):
                self._select_playback_queue(index, snapshot[0])
            return
        self.song_index = index % len(SONGS)
        self.owner.track_title, self.owner.artist = SONGS[self.song_index]
        self.owner.media_track_id = f"owned:netease:{self.song_index + 1}"
        self.owner.media_position_seconds = 0
        self.set_cover(self.song_index % len(COVERS))

    def tick(self):
        now = time.monotonic()
        self._diagnostics['samples'] += 1
        if self._pointer_exit_at is not None and now - self._pointer_exit_at >= .2:
            self._pointer_hover = False
            self._pointer_exit_at = None
        if self.live:
            self._prepare_live_commit()
            self._sync_owned_playback_mode()
            self.dj_available = self._owned_personal_dj_enabled is not None
            self.dj_enabled = self._owned_personal_dj_enabled is True
            self.dj_speaking = self._owned_dj_speaking
            modes = ("sequential", "shuffle", "repeat_one")
            self.loop_mode = modes.index(self._owned_playback_mode) if self._owned_playback_mode in modes else 0
            self.shelf = self.footer_hover = (
                self._pointer_hover or self._focus_reveal or now < self._pointer_active_until)
        self.last_tick = now
        self.visibility.advance(now, self.shelf or self.touch_active, self.footer_hover,
                                self.mode == "playlist")
        target = self.visibility.footer.target
        if target != self._diagnostic_target:
            self._diagnostics['controls_retargets'] += 1
            self._diagnostic_target = target
        self.dj_motion.retarget(float(self.dj_available), now, .18)
        feedback = self._glass_feedback
        if (self.hovered_glass or any(feedback.hover.values())
                or any(state.active(now) for state in feedback.controls.values())):
            feedback.advance(self.glass_groups(), self.hovered_glass, now)
        else:
            # Resting springs have no work. Keep their clock current so the
            # next real input starts with the same elapsed time as before.
            feedback.last_tick = now
        focus, visual = self.lyric_focus()
        # Only changes that can alter pixels authorize another present. The
        # shared clock itself is deliberately absent from this frame key.
        frame_key = (focus, round(visual, 3), self.ink_position())
        key = (frame_key, self._visual_track_identity(),
               self._settled_epoch,
               self.source_lines(), self.visibility.progress,
               feedback.pose_key(), self.dj_motion.value(now), self.mode,
               self._queue_scroll, self.queue_snapshot(), self.dj_available,
               self.dj_enabled, self.loop_mode, self.owner.media_is_playing,
               self.owner.album_art.cacheKey(), self.owner.album_color.rgba(),
               self.owner.secondary_color.rgba(), self.owner.track_title, self.owner.artist,
               self.width(), self.height(), self.devicePixelRatioF(),
               getattr(self.owner, 'media_position_source', ''), self.hovered_glass)
        if self.live and key == self._commit_key:
            self._diagnostics['noop_paints'] += 1
            return
        self._commit_key = key
        self._diagnostics['commits'] += 1
        assert self._diagnostics['commits'] <= self._diagnostics['samples']
        if self.narrow:
            self.refresh_fade_background(now)
            self.refresh_veil()
            self.refresh_controls(now)
            self.refresh_fade_content(now)
        super().update()
        names = {"A": "静态", "B": "悬停"}
        fps = len([t for t in self.frame_times if now - t < 1.0])
        if self.live:
            title = f"GluSpot · {self.owner.track_title}"
            if title != self.windowTitle():
                self.setWindowTitle(title)
        else:
            self.setWindowTitle(f"GluSpot 窄窗v3 · 方案{self.variant} {names[self.variant]} · {fps} fps")

    def glass_groups(self):
        layout = NarrowLayout(self.width(), self.height())
        top_keys = ("content_layout", "audio_output", "playback_mode", "translation", "return")
        top = {key: rect for key, rect in zip(top_keys, layout.top)}
        now = time.monotonic()
        top = {key: rect for index, (key, rect) in enumerate(top.items())
               if self.visibility.progress > .001}
        loop, mode = layout.utility(self.dj_motion.value(now))
        footer = {"media:previous": layout.previous, "media:toggle_play_pause": layout.play,
                  "media:next": layout.next, "collection": layout.favorite,
                  "footer_loop": loop, "footer_layout": mode}
        if self.dj_available:
            footer["personal_dj"] = layout.dj
        if self.visibility.progress <= .001:
            footer = {}
        return [top, {"library": layout.back} if self.visibility.progress > .001 else {}, footer]

    def posed_rect(self, key, rect):
        return QRectF(rect)

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
        self._diagnostics['paints'] += 1
        start = time.perf_counter()
        if not self.narrow:
            self._painted_live_hit_pose = None
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
        if self.live:
            self._prepare_live_commit()
            focus, visual = self.lyric_focus()
            if (self._snap_change == (self._visual_track_identity(), self.owner.lyric_changed_at)
                    and abs(focus - visual) > .000001):
                self._diagnostics['snap_frames'] += 1
        self._frame_control_color_at = now
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        palette = self._art_palette()
        fading = not self.visibility.settled(now)
        background_key = (self._background_cache.cacheKey(), self.width(), self.height(),
                          self.devicePixelRatioF())
        visibility = self.visibility.progress
        layout = NarrowLayout(self.width(), self.height())
        # Veil / neighbor layer never carries live reading ink. Ink advances in
        # a sharp overlay after veil so the active line is never covered by a
        # re-blurred copy of itself (GIF active_band ~23Hz flicker).
        focus, visual = self.lyric_focus()
        scrolling = self.mode == "lyrics" and abs(focus - visual) > .001
        self._diagnostics['paint_source'] = ("SCROLL" if scrolling else
                                             "LIVE" if self.owner.media_is_playing else "IDLE_CACHE")
        content = (self.content_image(layout, focus, include_current=False, overscan=self._scroll_guard)
                   if self.mode == "lyrics" else self.content_image(layout, include_current=False))
        # Resting paused content can blend opaque endpoints. Moving content
        # always uses the same stanza compositor as ordinary playback.
        if (fading and not scrolling and self._fade_anchor_key is not None
                and self._fade_content_frames is not None
                and self._fade_content_key == self.fade_content_key()):
            # Paused source rows are part of these exact endpoint pixels.
            # The advancing fixture run is painted separately below.
            painter.drawImage(QPointF(0, 0), self._fade_content_frames[0])
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setOpacity(visibility)
            painter.drawImage(QPointF(0, 0), self._fade_content_frames[1])
            painter.setOpacity(1.0)
        else:
            if fading and background_key == self._fade_background_key:
                painter.drawImage(QPointF(0, 0), self._fade_background)
            else:
                self._paint_background(painter, QRectF(self.rect()), QColor(palette.primary), QColor(palette.secondary), now,
                                       ambient_colors=palette.ambient_colors, ambient_weights=palette.ambient_weights,
                                       advance_frame=not fading)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            # Blend from the frozen veil basis during a fade. Outside a fade
            # (or before the first anchor) the live key is authoritative.
            veil_key = self._fade_veil_key if fading and self._fade_veil_key is not None else self.veil_key
            if self.mode == "lyrics":
                self.paint_scroll_surface(painter, layout, content, visibility)
            else:
                self.veil.paint(painter, content, veil_key, visibility,
                                scroll_delta=self.veil_scroll_delta)
        if self.mode == "lyrics" and self.live:
            # Crisp active line + reading ink above veil (never under row blur).
            self.paint_live_lyrics(painter, layout, current_only=True)
        elif self.mode == "lyrics" and not self.live:
            self.paint_current_lyrics(painter, layout)
        if visibility > .001 and self._controls_bands is not None:
            painter.save()
            painter.setOpacity(visibility)
            painter.drawImage(QPointF(0, 0), self._controls_bands[0])
            painter.drawImage(QPointF(0, self.height() - VeilOverlay.BOTTOM_HEIGHT), self._controls_bands[1])
            painter.restore()
        if self.live and self.owner.media_position_source == "capture-unavailable":
            painter.setFont(self._surface_font(12, QFont.Weight.Medium))
            painter.setPen(QColor(255, 255, 255, 220))
            painter.drawText(QRectF(12, 8, self.width() - 24, 32), Qt.AlignmentFlag.AlignCenter,
                             "播放器进度不可用，歌词跟随已暂停")
        if self.hints:
            painter.setFont(self._surface_font(12, QFont.Weight.Medium))
            painter.setPen(QColor(255, 255, 255, 120))
            painter.drawText(QRectF(4, self.height() - 13, self.width() - 8, 13), Qt.AlignmentFlag.AlignCenter,
                             "L 歌词  P 列表  N/W 窄/宽  1–4 封面  H 隐藏")
        painter.end()
        if self.live and self.mode == "lyrics":
            self.publish_live_lyric_hits(layout)
            self._painted_track = self._visual_track_identity()
            source_track = self._source_lines_key[0] if self._source_lines_key else None
            if source_track != self._painted_track:
                self._diagnostics['stale_frames'] += 1

    def content_image(self, layout, visual_focus=None, *, veil_source=False,
                      include_current=True, refresh=False, overscan=0):
        assert not (veil_source and include_current), "veil must never sample live lyric ink"
        if self.mode == "playlist":
            # Queue data is static between demo actions. Its selected glass
            # face is prepared by refresh_veil at a bounded 10Hz refresh.
            # Paint consumes those pixels without rasterizing glass again.
            key = (self.width(), self.height(), self.devicePixelRatioF(),
                   self.song_index, self.queue_snapshot(), self._queue_scroll, self._scene_control_ink().rgba())
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
            # Static/veil layers key on a quantized scroll pose so sub-pixel
            # spring motion does not rebuild neighbor rasters every tick.
            # Reading ink is a separate overlay; only include_current sees the bucket.
            visual = round(visual, 3) if include_current else round(visual)
            scroll_key = visual
            frame_key = (self.width(), self.height(), self.devicePixelRatioF(),
                         layout.lyric_font, focus, scroll_key, self.lyrics_alignment,
                         self._future_lyric_color(layout.viewport).rgba(),
                         self.ink_position() if include_current else None,
                         self.source_lines(),
                         include_current, overscan)
            # Preparation and presentation share the same frozen neighbor
            # image. A veil lookup must not evict the live-ink cache (or vice
            # versa), which used to force alternating full-layer rebuilds.
            cache_slot = (include_current, bool(overscan))
            cached = self._content_frames.get(cache_slot)
            if cached is not None and frame_key == cached[0]:
                self._content_frame_key, self._content_frame = cached
                return self._content_frame
        ratio = self.devicePixelRatioF()
        self._diagnostics['lyric_layer_builds'] += int(self.mode == 'lyrics')
        image = QImage(round(self.width() * ratio), round((self.height() + 2 * overscan) * ratio),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        layer = QPainter(image)
        layer.setRenderHint(QPainter.RenderHint.Antialiasing)
        layer.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        layer.translate(0, overscan)
        if self.mode == "lyrics" and self.live:
            self.paint_live_lyrics(layer, layout, visual_focus=visual,
                                   static_only=not include_current, overscan=overscan)
            area = layout.viewport
            fade = min(88, area.height() * .16)
        elif self.mode == "lyrics":
            layer.save()
            layer.setClipRect(layout.viewport.adjusted(-18, -overscan, 18, overscan))
            base_key = (self.width(), self.height(), ratio, layout.lyric_font,
                        focus, self.lyrics_alignment, self.source_lines(), overscan,
                        self._future_lyric_color(layout.viewport).rgba())
            if base_key != self._lyric_static_key:
                guard = max(layout.lyric_step, overscan)
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
            layer.drawImage(QPointF(0, (focus - visual) * layout.lyric_step - max(layout.lyric_step, overscan)),
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
        if not overscan:
            self.mask_content_edges(layer, layout, area, fade)
        layer.end()
        if self.mode == "playlist":
            self._list_layer_key, self._list_layer = key, image
            self._list_layer_at = now
        else:
            self._content_frame_key, self._content_frame = frame_key, image
            self._content_frames[cache_slot] = (frame_key, image)
        return image

    def mask_content_edges(self, layer, layout, area, fade):
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

    def paint_scroll_surface(self, painter, layout, content, opacity):
        """Move one frozen neighbor composite; active ink is painted afterward.

        Both veil endpoints read the same static source. A scroll changes only
        the transform, never text, its active-row exclusion, or a blur input.
        The premultiplied fade is additive so partially opaque neighbors do
        not acquire a second copy during reveal/hide.
        """
        key = (self.veil_key, content.cacheKey(), layout.viewport.bottom())
        if key != self._scroll_content_key:
            ratio = content.devicePixelRatio()
            center = content.copy(0, round(self._scroll_guard * ratio), content.width(),
                                  round(self.height() * ratio))
            center.setDevicePixelRatio(ratio)
            band = QImage(center.size(), center.format())
            band.setDevicePixelRatio(ratio)
            band.fill(Qt.GlobalColor.transparent)
            with QPainter(band) as layer:
                self.veil.paint(layer, center, self.veil_key, 1.0,
                                bottom_start=layout.viewport.bottom() + 6, include_dim=False)
            revealed = content.copy()
            with QPainter(revealed) as layer:
                layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                layer.drawImage(QPointF(0, self._scroll_guard), band)
            self._scroll_content_key = key
            self._scroll_endpoints = (content, revealed)
            self._scroll_surface_key = None
        alpha = round(max(0., min(1., opacity)), 3)
        frame_key = (key, alpha)
        if frame_key != self._scroll_surface_key:
            clear, revealed = self._scroll_endpoints
            if alpha == 0:
                self.scroll_surface = clear
            elif alpha == 1:
                self.scroll_surface = revealed
            else:
                # Qt applies global opacity as coverage for CompositionMode_Plus.
                # Scale both sources first with complementary 8-bit weights;
                # otherwise even identical endpoint ink loses alpha mid-fade.
                surface, weighted = clear.copy(), revealed.copy()
                weight = round(alpha * 255)
                for image, strength in ((surface, 255 - weight), (weighted, weight)):
                    with QPainter(image) as layer:
                        layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                        layer.fillRect(QRectF(0, 0, image.width() / image.devicePixelRatio(),
                                              image.height() / image.devicePixelRatio()),
                                       QColor(0, 0, 0, strength))
                with QPainter(surface) as layer:
                    layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                    layer.drawImage(QPointF(), weighted)
                self.scroll_surface = surface
            self._scroll_surface_key = frame_key
        focus, visual = self.lyric_focus()
        delta = (self.real_scroll_offset(focus) - self.real_scroll_offset(visual)
                 if self.live else (focus - visual) * layout.lyric_step)
        ratio = self.devicePixelRatioF()
        if (self._scroll_frame is None or self._scroll_frame.width() != round(self.width() * ratio)
                or self._scroll_frame.height() != round(self.height() * ratio)
                or self._scroll_frame.devicePixelRatio() != ratio):
            self._scroll_frame = QImage(round(self.width() * ratio), round(self.height() * ratio),
                                       QImage.Format.Format_ARGB32_Premultiplied)
            self._scroll_frame.setDevicePixelRatio(ratio)
        self._scroll_frame.fill(Qt.GlobalColor.transparent)
        with QPainter(self._scroll_frame) as layer:
            layer.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            layer.setClipRect(layout.viewport.adjusted(-18, 0, 18, 0))
            layer.drawImage(QPointF(0, delta - self._scroll_guard), self.scroll_surface)
            self.mask_content_edges(layer, layout, layout.viewport, min(88, layout.viewport.height() * .16))
        painter.save()
        painter.setClipRect(layout.viewport.adjusted(-18, 0, 18, 0), Qt.ClipOperation.IntersectClip)
        painter.drawImage(QPointF(), self._scroll_frame)
        painter.restore()
        self.veil.paint_dim(painter, self.width(), self.height(), alpha,
                            bottom_start=layout.viewport.bottom() + 6)

    def paint_intro_dots(self, painter, center, font):
        """One opaque dot raster, owned by the sharp layer above the veil."""
        ratio = self.devicePixelRatioF()
        key = (font.pixelSize(), ratio)
        if key != self._intro_dot_key:
            radius = intro_dot_radius(font.pixelSize())
            half_width = math.ceil(radius * (3.35 + 1) + 2)
            half_height = math.ceil(radius + 2)
            image = QImage(round(2 * half_width * ratio), round(2 * half_height * ratio),
                           QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(ratio)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as layer:
                layer.setRenderHint(QPainter.RenderHint.Antialiasing)
                paint_intro_countdown(layer, center=QPointF(half_width, half_height),
                                      pixel_size=font.pixelSize(), color=QColor('white'),
                                      opacity=1., blur_layers=((0, 1.),), scale=ratio)
            self._intro_dot_key, self._intro_dot_image = key, image
        image = self._intro_dot_image
        painter.drawImage(QPointF(center.x() - image.width() / ratio / 2,
                                 center.y() - image.height() / ratio / 2), image)

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
               self.owner.album_art.cacheKey(), self.owner.track_title, self.owner.artist,
               self._song_collection_state() if self.live else self.song_index in self.liked_songs,
               self.owner.media_is_playing,
               self._scene_control_ink().rgba())
        hidden = (self.visibility.opacity.target <= 0
                  and self.visibility.progress <= .001)
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
        """Reuse the production background at native size for one 320ms fade.

        This is the same raster, expanded once outside paint rather than
        bilinearly sampling it on every blend frame. Steady A/B use the normal
        production background path; no background filter is replaced.

        The anchor is frozen for the whole fade generation: the animated
        production background keeps advancing underneath, but a reveal or hide
        must not swap the raster mid-blend. A retarget (reversal) starts a new
        generation, so a new fade basis is captured from whatever is current.
        """
        generation = (self.visibility.opacity.generation, self.width(),
                      self.height(), self.devicePixelRatioF(),
                      self.mode, self.owner.media_track_id)
        if self.visibility.settled(now):
            self._fade_background = self._fade_background_key = None
            self._fade_anchor_key = self._fade_veil_key = None
            return
        if self._background_cache.isNull():
            return
        if generation == self._fade_anchor_key:
            return
        key = (self._background_cache.cacheKey(), self.width(), self.height(),
               self.devicePixelRatioF())
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
        self._fade_anchor_key = generation
        # Playlist fades retain this prepared veil basis. Lyrics own one
        # canonical stanza source and invalidate it only on a line/model change.
        self._fade_veil_key = self.veil_key

    def paint_current_lyrics(self, painter, layout):
        if self.owner.media_is_playing and not self.freeze_clock:
            self.paint_lyrics(painter, layout, current_only=True)
            return
        # Paused source timing has no changing reading front. Retain just the
        # current row's ink rather than rebuilding its word masks every tick.
        focus, visual = self.lyric_focus()
        ratio = self.devicePixelRatioF()
        key = (self.source_lines(), self.playback_position(), focus, visual,
               self.width(), self.height(), ratio,
               self._future_lyric_color(layout.viewport).rgba())
        if key != self._current_ink_key:
            font = self._surface_font(layout.lyric_font, QFont.Weight.Bold)
            shaped = self.reading_renderer.whole_line(self.source_lines()[focus].text, font)
            ascent = shaped.lineAt(0).ascent()
            top = math.floor(layout.lyric_anchor + QFontMetricsF(font).capHeight() / 2
                             + (focus - visual) * layout.lyric_step - ascent - 16)
            height = math.ceil(layout.lyric_row + 32)
            if any(line.text == INTRO_COUNTDOWN_TEXT for line in self.source_lines()):
                # The sharp owner also draws visible neighboring gap markers.
                # Keep them in the paused cache instead of cropping to focus.
                top, height = 0, self.height()
            image = QImage(round(self.width() * ratio), round(height * ratio),
                           QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(ratio)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as layer:
                layer.translate(0, -top)
                self.paint_lyrics(layer, layout, current_only=True)
            self._current_ink_key, self._current_ink_image = key, (image, top)
        image, top = self._current_ink_image
        painter.drawImage(QPointF(0, top), image)

    def refresh_fade_content(self, now):
        """Prepare fixed A/B content once per fade; frames only blend pixels.

        Paused live lyrics can reuse their exact source-timed content too.
        Advancing live lyrics retain the ordinary compositor rather than
        freezing their variable-height reading run.
        At 360x640/DPR2 the two opaque endpoints occupy 7,372,800 bytes total.
        They are released as soon as the transition settles.

        Endpoints use the ordinary stanza compositor, including its fixed
        viewport mask and dim. They are prepared only at a settled scroll pose;
        a new source/focus invalidates them before another present.
        """
        advancing_live_lyrics = self.live and self.mode == "lyrics" and self.owner.media_is_playing
        focus, visual = self.lyric_focus()
        scrolling = self.mode == "lyrics" and abs(focus - visual) > .001
        if scrolling and not self.visibility.settled(now):
            self._scroll_fade_generation = self.visibility.opacity.generation
        # Keep one compositor for the whole fade after scroll has used it.
        # Switching to opaque endpoints at scroll end changes dim ordering.
        scroll_fade = (self.mode == "lyrics"
                       and self._scroll_fade_generation == self.visibility.opacity.generation)
        if (advancing_live_lyrics or scrolling or scroll_fade or self.visibility.settled(now)
                or self._fade_background is None or self._fade_veil_key is None):
            self._fade_content_key = self._fade_content_frames = None
            return
        layout = NarrowLayout(self.width(), self.height())
        content = self.content_image(layout, self.lyric_focus()[0] if self.mode == "lyrics" else None,
                                     include_current=False,
                                     overscan=self._scroll_guard if self.mode == "lyrics" else 0)
        # The ink clock never enters this identity. Only a model, theme or
        # settled focus change can authorize new paused endpoints.
        key = self.fade_content_key()
        if self._fade_content_frames is not None and key == self._fade_content_key:
            return
        endpoints = []
        for opacity in (0.0, 1.0):
            image = self._fade_background.copy()
            with QPainter(image) as painter:
                if self.mode == "lyrics":
                    self.paint_scroll_surface(painter, layout, content, opacity)
                else:
                    self.veil.paint(painter, content, self._fade_veil_key, opacity)
            endpoints.append(image)
        self._fade_content_key, self._fade_content_frames = key, tuple(endpoints)

    def fade_content_key(self):
        layout = NarrowLayout(self.width(), self.height())
        return (self._fade_background_key,
                self.veil_key if self.mode == "lyrics" else self._fade_veil_key,
                self.mode, self.source_lines(), self.queue_snapshot(),
                layout.lyric_font, self.lyrics_alignment,
                self._future_lyric_color(layout.viewport).rgba(),
                self.owner.album_color.rgba(), self.owner.secondary_color.rgba(),
                self._scene_control_ink().rgba(),
                self.lyric_focus()[0] if self.mode == "lyrics" else None)

    def _advance_lyric_animation(self, now):
        if self.live:
            # Keep the production surface timer available for its visual
            # controls/material work; shared owner state supplies lyric focus.
            return
        super()._advance_lyric_animation(now)

    def _lyric_frame(self, timed, now):
        if not self.live:
            return super()._lyric_frame(timed, now)
        self._prepare_live_commit()
        # Wheel input and the inherited wide painter also request frames.
        # Adapt the owner's published state without advancing surface motion.
        track_key = self._visual_track_identity()
        if track_key != self._browse_track_key:
            self._browse_track_key = track_key
            self._clear_lyric_browse()
            self._hovered_lyric_index = None
        position = self.playback_position()
        # The inherited painter reads this field for word ink; it is only a
        # mirror of the shared snapshot, never a separately projected clock.
        self._lyric_motion.last_position = position
        if not timed:
            return MusicLyricFrame(0, 0.0, 0.0, 0.0)
        focus = min(len(timed) - 1, max(0, self.owner.lyric_index))
        previous = min(len(timed) - 1, max(0, self.owner.prev_lyric_index))
        if self._snap_change == (track_key, self.owner.lyric_changed_at):
            previous = focus
        transition = min(1.0, max(0.0, (now - self.owner.lyric_changed_at) / .36))
        eased = 1.0 - (1.0 - transition) ** 3
        start = float(timed[focus][0])
        end = (float(timed[focus + 1][0]) if focus + 1 < len(timed)
               else max(start + 1.0, self.owner.media_duration_seconds))
        progress = min(1.0, max(0.0, (position - start) / max(.001, end - start)))
        return MusicLyricFrame(focus, previous + (focus - previous) * eased, progress, 0.0)

    def _prepare_live_commit(self):
        """Invalidate a track/epoch in one UI transaction, before any painting."""
        track = self._visual_track_identity()
        epoch = getattr(self.owner, 'media_capture_epoch_id', 0)
        swapped = self._committed_track is not None and track != self._committed_track
        settled = bool(epoch) and (track, epoch) != self._settled_epoch
        if track == self._committed_track and not settled:
            return
        if swapped or settled:
            for name in ('_source_lines_key', '_real_layout_key', '_content_frame_key',
                         '_lyric_static_key', '_current_ink_key', '_controls_key',
                         '_fade_content_key', '_painted_live_model', '_painted_live_hit_pose'):
                setattr(self, name, None)
            self._lyric_word_hit_rects.clear()
            self._lyric_hit_rects.clear()
            self._clear_lyric_browse()
            self.veil.clear()
            self._content_frames.clear()
            self._scroll_content_key = self._scroll_surface_key = None
            self._scroll_endpoints = self.scroll_surface = None
            self._scroll_guard_key = None
        if swapped:
            self._display_lyrics_cache_key = self._display_lyrics_cache = None
            self._frame_lyric_cache = None
            self._diagnostics['swap_commits'] += 1
        self._committed_track = track
        self._snap_change = (track, self.owner.lyric_changed_at)
        if settled:
            self._settled_epoch = (track, epoch)
            self._diagnostics['settle_commits'] += 1
            if self.playback_position() > 30 and self.owner.lyric_index > 20:
                self._diagnostics['first_sample_settle_outlier'] += 1
                logging.getLogger('GluSpot.NarrowLive').info(
                    'first_sample_settle_outlier epoch=%s position=%.3f index=%s sampled_at=%s',
                    epoch, self.playback_position(), self.owner.lyric_index,
                    getattr(self.owner, 'media_position_sampled_at', 0))

    def lyric_focus(self):
        lines = self.source_lines()
        if not lines:
            return 0, 0.0
        if self.live:
            frame = self._lyric_frame([(line.time_seconds, line.text) for line in lines], time.monotonic())
            return frame.active_index, frame.scroll_index
        position = self.playback_position()
        focus = min(len(lines) - 1, max(0, bisect_right([line.time_seconds for line in lines], position) - 1))
        transition = min(1.0, max(0.0, (position - lines[focus].time_seconds) / .36))
        eased = 1.0 - (1.0 - transition) ** 3
        return focus, focus - (1.0 - eased) if focus else 0.0

    @property
    def veil_scroll_delta(self):
        visual = self.lyric_focus()[1] if self.mode == "lyrics" else 0.0
        step = NarrowLayout(self.width(), self.height()).lyric_step
        if self.live and self.mode == "lyrics":
            offset = self.real_scroll_offset(visual)
            canonical = self.real_visual_for_offset(round(offset / 32.0) * 32.0)
            return self.real_scroll_offset(canonical) - offset
        return -(visual - round(visual)) * step

    def refresh_veil(self):
        """Prepare only when layout/theme/DPR/current line/scroll bucket changes.

        Called from the timer/input/capture preparation, never paintEvent.
        Reading ink advances in the sharp lyric layer. The blurred overlay is
        prepared only for a changed row/scroll bucket, never for word progress.
        """
        layout = NarrowLayout(self.width(), self.height())
        if self.mode == "playlist":
            self.content_image(layout, refresh=True)
        focus, visual = self.lyric_focus()
        # One stanza source for an entire line change. Fractional scroll is
        # applied to the composed surface, never fed back into blur prepare.
        bucket = focus if self.mode == "lyrics" else 0
        canonical_visual = bucket
        self.veil_key = (
            self.mode, self.width(), self.height(), layout.lyric_font,
            self._queue_scroll if self.live and self.mode == "playlist" else 0,
            layout.lyric_step, self.lyrics_alignment, self.source_lines(), self.queue_snapshot(),
            self._visual_track_identity(),
            self.song_index, self.cover_index, self.devicePixelRatioF(),
            self.owner.album_color.rgba(), self.owner.secondary_color.rgba(),
            self._future_lyric_color(layout.viewport).rgba(),
            self._scene_control_ink().rgba() if self.mode == "playlist" else None,
            focus if self.mode == "lyrics" else 0, bucket,
        )
        if self.mode == "lyrics" and self.veil_key != self._scroll_guard_key:
            delta = (self.real_scroll_offset(focus) - self.real_scroll_offset(visual)
                     if self.live else (focus - visual) * layout.lyric_step)
            self._scroll_guard = math.ceil(abs(delta) + layout.lyric_step)
            self._scroll_guard_key = self.veil_key
        # prepare accepts a source factory: a hit must not rasterize full content.
        # Veil blur atlas samples static rows only. Live ink must not enter
        # prepare(), or the active line is re-blurred into the chrome bands.
        def frozen_source():
            if self.mode != "lyrics":
                return self.content_image(layout, veil_source=True, include_current=False)
            strip = self.content_image(layout, canonical_visual, veil_source=True,
                                       include_current=False, overscan=self._scroll_guard)
            ratio = strip.devicePixelRatio()
            tile = strip.copy(0, round(self._scroll_guard * ratio), strip.width(),
                              round(self.height() * ratio))
            tile.setDevicePixelRatio(ratio)
            return tile
        self.veil.prepare(self.veil_key, frozen_source)

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
        if self.live:
            self.paint_live_lyrics(painter, layout, visual_focus=visual_focus,
                                   static_only=static_only, current_only=current_only,
                                   overscan=overscan)
            return
        viewport = layout.viewport
        position = self.playback_position()
        lines = self.source_lines()
        if not lines:
            if not static_only:
                painter.setFont(self._surface_font(20, QFont.Weight.Bold))
                painter.setPen(self._scene_control_ink())
                painter.drawText(viewport, Qt.AlignmentFlag.AlignCenter,
                                 "\n".join(self._display_lyrics()[0]))
            return
        focus, actual_visual_focus = self.lyric_focus()
        if visual_focus is None:
            visual_focus = actual_visual_focus
        anchor = layout.lyric_anchor
        step = layout.lyric_step
        row = layout.lyric_row
        font = self._surface_font(layout.lyric_font, QFont.Weight.Bold)
        anchor += QFontMetricsF(font).capHeight() / 2
        metrics_layout = self.reading_renderer.whole_line(lines[focus].text, font)
        ascent = metrics_layout.lineAt(0).ascent()
        painter.save()
        painter.setClipRect(viewport.adjusted(-18, -overscan, 18, overscan))
        for index in range(len(lines)):
            dots = lines[index].text == INTRO_COUNTDOWN_TEXT
            if (static_only and (index == focus or dots)) or (current_only and index != focus and not dots):
                continue
            line = lines[index]
            y = anchor + (index - visual_focus) * step - ascent
            if y + row < viewport.top() - 16 - overscan or y > viewport.bottom() + 16 + overscan:
                continue
            if line.text == INTRO_COUNTDOWN_TEXT:
                self.paint_intro_dots(painter, QPointF(
                    viewport.left() + intro_dot_radius(font.pixelSize()) * (1.2 + 3.35),
                    layout.lyric_anchor + (index - visual_focus) * step), font)
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
            end = (lines[index + 1].time_seconds if index + 1 < len(lines)
                   else max(line.time_seconds + 1.0, getattr(self.owner, "media_duration_seconds", 0.0)))
            self.reading_renderer.paint(
                painter, QRectF(x, y, viewport.width(), row), x, line.text, font,
                max(0.0, min(1.0, (position - line.time_seconds) / max(.001, end - line.time_seconds))),
                self._future_lyric_color(viewport), QColor(255, 255, 255),
                timed_units=line.units, timed_position=position,
                timed_line_end=end,
            )
        painter.restore()

    def real_lyric_blocks(self, layout):
        lines = self.source_lines()
        key = (lines, layout.viewport.width(), layout.lyric_font)
        if key == self._real_layout_key:
            return self._real_blocks
        font = self._surface_font(layout.lyric_font, QFont.Weight.Bold)
        secondary = self._surface_font(max(12, layout.lyric_font * .55), QFont.Weight.Medium)
        blocks, cursor = [], 0.0
        for line in lines:
            layers = split_display_layers(line.text)
            if line.text == INTRO_COUNTDOWN_TEXT:
                blocks.append((cursor, layout.lyric_row, layers, ()))
                cursor += layout.lyric_step
                continue
            parts = [(layers.primary_text, font)]
            parts.extend((text, secondary) for text in
                         (layers.translation_text, layers.parenthetical_text,
                          layers.parenthetical_translation_text) if text)
            runs, height = [], 0.0
            for text, part_font in parts:
                shaped, part_height = self._wrapped_lyric_layout(text, part_font, layout.viewport.width())
                runs.append((text, part_font, shaped, height, part_height))
                height += part_height + 8.0
            blocks.append((cursor, max(layout.lyric_row, height - 8.0), layers, tuple(runs)))
            cursor += max(layout.lyric_step, height + 8.0)
        self._real_layout_key, self._real_blocks = key, tuple(blocks)
        return self._real_blocks

    def real_scroll_offset(self, visual):
        blocks = self.real_lyric_blocks(NarrowLayout(self.width(), self.height()))
        if not blocks:
            return 0.0
        index = max(0, min(len(blocks) - 1, int(visual)))
        following = min(len(blocks) - 1, index + 1)
        return blocks[index][0] + (blocks[following][0] - blocks[index][0]) * (visual - index)

    def real_visual_for_offset(self, offset):
        blocks = self.real_lyric_blocks(NarrowLayout(self.width(), self.height()))
        if not blocks:
            return 0.0
        starts = [block[0] for block in blocks]
        index = max(0, min(len(starts) - 1, bisect_right(starts, offset) - 1))
        if index + 1 >= len(starts):
            return float(index)
        return index + (offset - starts[index]) / (starts[index + 1] - starts[index])

    def live_lyric_rows(self, layout, visual_focus=None):
        blocks = self.real_lyric_blocks(layout)
        focus, visual = self.lyric_focus()
        if visual_focus is not None:
            visual = visual_focus
        if not blocks:
            return ()
        first_runs = blocks[focus][3]
        first_layout = first_runs[0][2] if first_runs else None
        ascent = (first_layout.lineAt(0).ascent() if first_layout and first_layout.lineCount()
                  else QFontMetricsF(self._surface_font(layout.lyric_font)).ascent())
        cap_height = QFontMetricsF(self._surface_font(layout.lyric_font)).capHeight()
        origin = layout.lyric_anchor + cap_height / 2 - ascent - self.real_scroll_offset(visual)
        return tuple((index, QRectF(layout.viewport.left(), origin + start,
                                    layout.viewport.width(), height), layers, runs)
                     for index, (start, height, layers, runs) in enumerate(blocks))

    def paint_live_lyrics(self, painter, layout, visual_focus=None, *,
                          static_only=False, current_only=False, overscan=0):
        lines = self.source_lines()
        if not lines:
            if not static_only:
                painter.setFont(self._surface_font(20, QFont.Weight.Bold))
                painter.setPen(self._scene_control_ink())
                painter.drawText(layout.viewport, Qt.AlignmentFlag.AlignCenter,
                                 "\n".join(self._display_lyrics()[0]))
            return
        focus = self.lyric_focus()[0]
        # Match the frame key: live ink reads the quantized bucket, never the
        # exact clock the key deliberately ignores.
        position = self.ink_position()
        painter.save()
        clip = layout.viewport.adjusted(-18, -overscan, 18, overscan)
        painter.setClipRect(clip)
        for index, row, layers, runs in self.live_lyric_rows(layout, visual_focus):
            dots = lines[index].text == INTRO_COUNTDOWN_TEXT
            if (static_only and (index == focus or dots)) or (current_only and index != focus and not dots):
                continue
            if row.bottom() < clip.top() - 18 or row.top() > clip.bottom() + 18:
                continue
            line = lines[index]
            end = (lines[index + 1].time_seconds if index + 1 < len(lines)
                   else max(line.time_seconds + 1.0, self.owner.media_duration_seconds))
            progress = max(0.0, min(1.0, (position - line.time_seconds) / max(.001, end - line.time_seconds)))
            if line.text == INTRO_COUNTDOWN_TEXT:
                font = self._surface_font(layout.lyric_font, QFont.Weight.Bold)
                metrics = QFontMetricsF(font)
                baseline_offset = metrics.ascent() - metrics.capHeight() / 2
                self.paint_intro_dots(painter, QPointF(
                    row.left() + intro_dot_radius(font.pixelSize()) * (1.2 + 3.35),
                    row.top() + baseline_offset), font)
                continue
            for part_index, (text, font, shaped, offset, height) in enumerate(runs):
                target = QRectF(row.left(), row.top() + offset, row.width(), height)
                if index == focus:
                    real = (self._real_visual_line_progresses(
                        text, shaped, line.units, position, line_end=end,
                        source_text=line.text, visible_source_offsets=layers.primary_source_offsets)
                            if part_index == 0 and line.units else None)
                    self._paint_wrapped_lifted_lyric(painter, target, text, font, shaped,
                                                     progress, real_progresses=real)
                else:
                    color = QColor("white") if index < focus else self._future_lyric_color(target)
                    level = 0
                    painter.save()
                    painter.setOpacity(painter.opacity() * (LYRIC_PREVIOUS_ALPHA if index < focus else LYRIC_NEXT_ALPHA))
                    self._draw_cached_wrapped_layout(painter, shaped, target.topLeft(), text,
                                                     font, target.width(), height, color, level)
                    painter.restore()
        painter.restore()

    def live_model_key(self):
        return (self._visual_track_identity(), str(getattr(self.owner, "media_source_app", "")),
                self.source_lines(), self.width(), self.height(), self.mode)

    def publish_live_lyric_hits(self, layout):
        # Reading ink changes without moving the shaped source geometry. Keep
        # that painted hit map until its model or scroll pose actually changes.
        pose = (self.live_model_key(), self.lyric_focus())
        if (pose == self._painted_live_hit_pose
                and self._lyric_word_hit_track_key == pose[0][:2]):
            return
        self._lyric_word_hit_rects.clear()
        self._lyric_hit_rects.clear()
        self._lyric_word_hit_track_key = (self._visual_track_identity(),
                                         str(getattr(self.owner, "media_source_app", "")))
        lines = self.source_lines()
        for index, row, layers, runs in self.live_lyric_rows(layout):
            if not runs:
                continue
            if not row.intersects(layout.viewport):
                continue
            self._lyric_hit_rects[index] = row.intersected(layout.viewport)
            text, _font, shaped, _offset, height = runs[0]
            primary = QRectF(row.left(), row.top(), row.width(), height)
            self._populate_lyric_word_hit_rects(
                index, text, lines[index].text, layers, shaped, tuple(lines[index].units),
                primary, (), QTransform(), layout.viewport, 0.0,
                visible_source_offsets=layers.primary_source_offsets)
        self._painted_live_model = pose[0]
        self._painted_live_hit_pose = pose

    def live_lyric_action_at(self, point):
        # Input may only use a layout the listener has actually seen. A same-
        # track timing/translation enrichment must wait for the next paint.
        if self._painted_live_model != self.live_model_key():
            return None
        word = self._lyric_word_target_at(point)
        if word is not None:
            return ("lyric", word[0], word[2], self._painted_live_model)
        for index, rect in self._lyric_hit_rects.items():
            if rect.contains(point):
                return ("lyric", index, None, self._painted_live_model)
        return None

    def paint_list(self, painter, layout):
        viewport = layout.viewport
        if self.live:
            self._paint_playback_queue(painter, viewport)
            return
        snapshot = self.queue_snapshot()
        songs = self.queue_rows()
        step = layout.list_step(len(songs))
        top = layout.list_top
        current = snapshot[1] if snapshot is not None else self.song_index
        first = 0
        ink = self._scene_control_ink()
        if layout.list_heading > 1:
            painter.setPen(ink)
            painter.setFont(self._surface_font(12, QFont.Weight.DemiBold))
            painter.drawText(QRectF(20, viewport.top(), self.width() - 40, 28), Qt.AlignmentFlag.AlignVCenter, "播放列表")
        painter.save()
        painter.setClipRect(QRectF(0, top, self.width(), max(0, viewport.bottom() - top)))
        for index in range(first, len(songs)):
            song = songs[index]
            title, artist = song.title, song.artist
            y = top + (index - first) * step
            if y > viewport.bottom():
                break
            row_height = min(52.0, step - 4)
            if index == current:
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
            if not self.live:
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
        collection_state = self._song_collection_state() if self.live else None
        liked = collection_state.liked is True if self.live else self.song_index in self.liked_songs
        heart = "heart_filled" if liked else "heart_outline"
        for index, icon in enumerate((heart, "playlist_add")):
            side = favorite.height() * (.63 if index == 0 else .61)
            center_x = favorite.left() + favorite.width() * (.25 + .5 * index)
            glyph = QRectF(center_x - side / 2, favorite.center().y() - side / 2, side, side)
            icon_ink = QColor("#ff3e4c") if index == 0 and liked else ink
            painter.save()
            if self.live and (not collection_state.available or collection_state.busy):
                painter.setOpacity(painter.opacity() * .38)
            paint_music_source_icon(painter, glyph, icon, tint=icon_ink)
            painter.restore()
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
        elif self.live and key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Space):
            command = {Qt.Key.Key_Left: "previous", Qt.Key.Key_Right: "next",
                       Qt.Key.Key_Space: "toggle_play_pause"}[key]
            self._send_media_command(command)
        elif self.live and key in (Qt.Key.Key_D, Qt.Key.Key_S):
            return
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
        self.pointer_activity(event.position(), time.monotonic())
        self._glass_feedback.move(event.position())
        self.hovered_glass = ""
        for group in self.glass_groups():
            for key, rect in group.items():
                if rect.contains(event.position()):
                    self.hovered_glass = key
        self.update()

    def pointer_activity(self, point, now, *, focus=False):
        """Input hysteresis, advanced by the existing visual tick only."""
        if focus:
            self._focus_reveal = True
            self.visibility.advance(now, True, True, self.mode == 'playlist')
            return
        layout = NarrowLayout(self.width(), self.height())
        zones = (QRectF(0, 0, self.width(), VeilOverlay.TOP_END),
                 QRectF(0, self.height() - VeilOverlay.BOTTOM_HEIGHT,
                        self.width(), VeilOverlay.BOTTOM_HEIGHT))
        entered = any(rect.adjusted(8, 8, -8, -8).contains(point) for rect in zones)
        retained = any(rect.adjusted(-24, -24, 24, 24).contains(point) for rect in zones)
        if entered:
            self._pointer_hover = True
            self._pointer_exit_at = None
        elif self._pointer_hover and not retained:
            if self._pointer_exit_at is None:
                self._pointer_exit_at = now
        elif retained:
            self._pointer_exit_at = None
        if self._pointer_anchor is None or (point - self._pointer_anchor).manhattanLength() >= 8:
            self._pointer_anchor = QPointF(point)
            self._pointer_active_until = now + .2

    def focusInEvent(self, event):
        if event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                              Qt.FocusReason.ShortcutFocusReason):
            self.pointer_activity(QPointF(), time.monotonic(), focus=True)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._focus_reveal = False
        super().focusOutEvent(event)

    def enterEvent(self, event):
        if self.narrow:
            self._pointer_anchor = None
            self._pointer_active_until = time.monotonic() + .2
            if not self.live:
                self.shelf = self.footer_hover = True
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
                if self.live:
                    if event.type() == QEvent.Type.TouchEnd and event.points():
                        self._finish_live_press(event.points()[0].position())
                    else:
                        self._live_press = None
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
        if self.live:
            if event.button() == Qt.MouseButton.LeftButton:
                self._begin_live_press(event.position())
                event.accept()
            return
        self._press_at(event.position())

    def _live_action_at(self, position):
        if self.visibility.progress >= .75:
            for group in self.glass_groups():
                for key, rect in group.items():
                    if rect.contains(position):
                        if key == "collection":
                            return "favorite" if position.x() < rect.center().x() else "playlist_add"
                        return key
        if self.mode == "playlist":
            for index, rect in self._queue_hit_rects.items():
                if rect.contains(position):
                    token = self._queue_snapshot[0] if self._queue_snapshot is not None else None
                    return ("queue", index, token)
        if self.mode == "lyrics":
            return self.live_lyric_action_at(position)
        return None

    def _begin_live_press(self, position):
        # Keep the source and controller authority the listener actually pressed.
        action = self._live_action_at(position)
        track = self._visual_track_identity()
        source = str(getattr(self.owner, "media_source_app", ""))
        authority = self._capture_song_collection_authority(track)
        self._live_press = (action, track, source, authority)
        self._pointer_active_until = time.monotonic() + .15
        self.visibility.advance(time.monotonic(), True, True, self.mode == "playlist")
        if isinstance(action, str):
            key = "collection" if action in {"favorite", "playlist_add"} else action
            self._glass_feedback.press(key, position, time.monotonic())
        self.update()

    def _finish_live_press(self, position):
        pressed, self._live_press = self._live_press, None
        self._glass_feedback.release()
        if pressed is None:
            return
        action, track, source, authority = pressed
        if (action is None or action != self._live_action_at(position)
                or track != self._visual_track_identity()
                or source != str(getattr(self.owner, "media_source_app", ""))):
            return
        if isinstance(action, tuple):
            if action[0] == "lyric":
                self._seek_to_lyric(action[1], action[2])
            else:
                self._select_playback_queue(action[1], action[2])
        elif action.startswith("media:"):
            self._send_media_command(action.removeprefix("media:"))
        elif action in {"content_layout", "footer_layout"}:
            self.mode = "playlist" if self.mode == "lyrics" else "lyrics"
            self.set_content_layout(self.mode, animate=False)
            self._finish_content_layout()
        elif action in {"playback_mode", "footer_loop"}:
            self._cycle_owned_playback_mode()
        elif action in {"favorite", "playlist_add"}:
            self._activate_song_collection(action, track, authority)
        elif action == "personal_dj":
            self._toggle_personal_dj()
        elif action == "translation":
            self._toggle_translation()
        elif action == "audio_output":
            self._toggle_audio_output_panel()
        elif action in {"library", "return"}:
            self.resize(960, 540)
        self.refresh_veil()
        self.refresh_controls(time.monotonic())
        self.update()

    def _press_at(self, position):
        if self.live:
            self._begin_live_press(position)
            return
        visibility = self.visibility.progress
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
        if self.live:
            if event.button() == Qt.MouseButton.LeftButton:
                self._finish_live_press(event.position())
            event.accept()
            return
        self._glass_feedback.release()
        self.update()

    def wheelEvent(self, event):
        if self.live and self.narrow and self.mode == "playlist":
            self._scroll_playback_queue(event)
            self._list_layer_key = None
            self._list_layer = None
            self.refresh_veil()
            event.accept()
            return
        super().wheelEvent(event)

    def leaveEvent(self, event):
        self.hovered_glass = ""
        self.shelf = False
        self.footer_hover = False
        self._pointer_active_until = 0.0
        self._pointer_exit_at = time.monotonic()
        super().leaveEvent(event)


def make_owner():
    from unittest.mock import Mock
    from test_music_surface_transition import _TransitionOwner
    from test_owned_playback_surface import _Provider
    from test_song_collection_surface import Collection

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
