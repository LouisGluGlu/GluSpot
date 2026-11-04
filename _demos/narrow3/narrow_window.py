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

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRegion, QTextLayout, QTransform
from PySide6.QtWidgets import QApplication, QAbstractButton

from api.models import ColorToken, TimedLyricLine, TimedLyricUnit
from api.online_music import OnlineSong
from api.providers import WindowsMediaProvider
from local.audio_output_icons import paint_audio_output_status
from local.music_source_icons import paint_music_source_icon
from local.music_surface_window import MusicSurfaceWindow, MUSIC_WINDOW_ASPECT_RATIO, constrained_window_size
from local.music_surface_layout import normalize_content_layout
from local.music_lyric_reading import LyricReadingRenderer
from local.lyric_unit_timing import timed_unit_reading_front
from local.music_lyric_presentation import (
    split_display_layers, INTRO_COUNTDOWN_TEXT, intro_dot_radius, paint_intro_countdown,
    LYRIC_PREVIOUS_ALPHA, LYRIC_NEXT_ALPHA,
)
if __package__:
    from .layout import NarrowLayout
    from .motion import EaseOutFade, Visibility
    from .veil import VeilOverlay, BackgroundEdgeVeil
else:
    from layout import NarrowLayout
    from motion import EaseOutFade, Visibility
    from veil import VeilOverlay, BackgroundEdgeVeil
from local.personal_dj_icon import paint_personal_dj_icon
from local.music_control_glass import fused_control_path
from local.glass_style import GLASS_CLEAR

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


class ChromeFocusButton(QAbstractButton):
    """Accessible slot for a shared painted face; mouse geometry stays owned."""
    def __init__(self, surface, key, name):
        super().__init__(surface)
        self.surface, self.key = surface, key
        self.setAccessibleName(name)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.clicked.connect(self.activate)

    def activate(self):
        point = QPointF(self.geometry().center())
        if self.surface.live:
            self.surface._begin_live_press(point)
            self.surface._finish_live_press(point)
        else:
            self.surface._press_at(point)

    def focusInEvent(self, event):
        self.surface.pointer_activity(QPointF(), time.monotonic(), focus=True)
        self.surface.hovered_glass = self.key
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        self.surface._focus_reveal = False
        self.surface.hovered_glass = ""
        super().focusOutEvent(event)
        self.update()

    def paintEvent(self, event):
        if self.hasFocus() and not self.surface._capturing_content_snapshot:
            with QPainter(self) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(self.surface._scene_control_ink(), 1., Qt.PenStyle.DotLine))
                painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 8, 8)


class NarrowStudyWindow(MusicSurfaceWindow):
    LYRIC_SCROLL_SHARP_SECONDS = .35

    @property
    def mode(self):
        return self._effective_content_layout()

    @mode.setter
    def mode(self, value):
        self.set_content_layout(value, animate=False)

    def set_content_layout(self, layout, *, animate=True):
        # One selection is shared by the narrow and wide painters and inputs.
        # Narrow keeps its established immediate content switch.
        if hasattr(self, "_width_chrome_animation"):
            self._width_chrome_animation.stop()
            self._finish_width_chrome()
        changed = super().set_content_layout(
            normalize_content_layout(layout), animate=animate and not getattr(self, "narrow", False))
        if changed and hasattr(self, "_width_chrome_animation"):
            self._sync_narrow_mode()
            # Selecting content is immediate; only resizing owns the width
            # handoff, including a switch back from a cover-only layout.
            self._width_chrome_animation.stop()
            self._finish_width_chrome()
        if changed and hasattr(self, "_controls_key"):
            self._controls_key = None
            self._painted_live_model = self._painted_live_hit_pose = None
        return changed

    @property
    def _narrow_lyrics_mount(self):
        return getattr(self, "narrow", False) and getattr(self, "mode", None) == "lyrics"

    def _content_column_geometry(self, rect=None):
        if (getattr(self, "_width_chrome_active", False)
                and self.mode == "lyrics"):
            target = self._width_lyric_geometry(self.narrow, rect)
            phase = self._width_chrome_progress
            content, cover, column = (self._interpolate_rect(first, last, phase)
                                     for first, last in zip(self._width_content_from, target))
            compact = phase if self.narrow else 1. - phase
            cover.translate(0., -VeilOverlay.TOP_END * (1. - self.visibility.progress) * compact)
            return content, cover, column
        if not self._narrow_lyrics_mount:
            content, cover, column = super()._content_column_geometry(rect)
            if (getattr(self, "narrow", False) and self.mode == "playlist"
                    and hasattr(self, "visibility")):
                cover.translate(0., -VeilOverlay.TOP_END * (1. - self.visibility.progress))
            return content, cover, column
        surface = QRectF(self.rect()) if rect is None else QRectF(rect)
        viewport = NarrowLayout(surface.width(), surface.height()).lyric_viewport
        viewport.translate(surface.topLeft())
        return QRectF(viewport), QRectF(-10000., 0., 0., 0.), viewport

    def _width_lyric_geometry(self, narrow, rect=None):
        if not narrow:
            return self._wide_content_column_geometry(rect)
        surface = QRectF(self.rect()) if rect is None else QRectF(rect)
        layout = NarrowLayout(surface.width(), surface.height())
        return surface, layout.cover.translated(surface.topLeft()), layout.lyric_viewport.translated(surface.topLeft())

    def _paint_wide_lyrics_mounted(self, painter):
        self._paint_lyric_column(painter, QRectF(self.rect()))

    def _content_layout_cover_pose(self):
        if (self._narrow_lyrics_mount and not self._width_chrome_active
                and self._layout_snapshot.isNull()):
            cover = NarrowLayout(self.width(), self.height()).cover
            return cover.translated(0., -VeilOverlay.TOP_END * (1. - self.visibility.progress))
        return super()._content_layout_cover_pose()

    def __init__(self, owner, variant, lyrics_alignment, *, live=False):
        self._width_chrome_active = False
        self._width_chrome_progress = 1.
        self._width_chrome_from = {}
        self._width_content_from = ()
        self._width_glass_backdrop = None
        self._chrome_backdrop = None
        self._chrome_backdrop_key = None
        self._chrome_backdrop_at = -math.inf
        self._width_phase_from = (0., 0., 1.)
        self._chrome_focus_buttons = {}
        self.live = live
        self._wide_lyrics_dirty = True
        self._source_lines_key = None
        self._source_lines = ()
        self._real_layout_key = None
        self._real_blocks = ()
        self._pointer_active_until = 0.0
        self._lyric_scroll_sharp_until = 0.0
        self._live_press = None
        self._narrow_drag = False
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
        self._live_position_route = ""
        self._live_scroll_index = None
        self._live_scroll_at = None
        self._live_lyric_diagnostics = {}
        self._diagnostics.update(settle_commits=0, first_sample_settle_outlier=0)
        self._diagnostics.update(stale_frames=0)
        self._painted_track = None
        if live:
            # This compositor presents whole ambient frames. One 30 Hz
            # surface clock drives preparation and presentation together.
            self.ANIMATION_FRAME_MS = self.EMPHASIS_FRAME_MS = 33
        super().__init__(owner)
        self.variant = variant
        self.lyrics_alignment = lyrics_alignment
        self._sync_narrow_mode()
        if not live:
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
        self.background_veil = BackgroundEdgeVeil()
        self._background_veil_key = None
        self._lyric_resize_background = None
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
        self._palette_sync_key = None
        self._palette_resync_at = -math.inf
        self._controls_palette_key = None
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
        self.hints = not live
        self.freeze_clock = False
        self.cover_images = ([] if live else
                             [QImage(str(Path(__file__).parent / "covers" / name)) for name in COVERS])
        self.reading_renderer = LyricReadingRenderer()
        self.samples = []
        self.bench_phase = None
        self.bench_samples = {name: [] for name in ("steady", "press", "visibility")}
        self.cold_paint = None
        self.last_tick = time.monotonic()
        self.frame_times = []
        self.setMinimumSize(320, self.WINDOW_HEIGHT if live else 560)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.move(80, 80)
        self.toggle_wide(False)
        self._width_chrome_animation = QVariantAnimation(self)
        self._width_chrome_animation.setDuration(1000)
        self._width_chrome_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._width_chrome_animation.valueChanged.connect(self._advance_width_chrome)
        self._width_chrome_animation.finished.connect(self._complete_width_chrome)
        if not live:
            self.set_cover(0)
        if live:
            self._sync_live_capabilities()
        else:
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
        self._sync_chrome_accessibility()
        if not self.live:
            self.study_timer.start(40)

    def diagnostic_snapshot(self):
        result = dict(self._diagnostics)
        result.update(self._live_lyric_diagnostics)
        result['visible_windows'] = sum(
            widget.isVisible() and not widget.isMinimized()
            for widget in QApplication.topLevelWidgets()
            if isinstance(widget, MusicSurfaceWindow) or widget is self.owner)
        result['lyric_bounds'] = NarrowLayout(self.width(), self.height()).viewport.getRect()
        result['painted_track'] = self._painted_track
        result['capture_epoch_id'] = getattr(self.owner, 'media_capture_epoch_id', 0)
        result['is_first_sample'] = getattr(self.owner, 'media_is_first_sample', False)
        result['reveal_progress'] = self.visibility.progress
        result['dim'] = .04 + .14 * self.visibility.progress
        result['blur'] = VeilOverlay.MAX_BLUR
        result['veil_prepares'] = self.background_veil.veil_prepares
        result['veil_seam_max_step'] = self.background_veil.veil_seam_max_step
        result['veil_seam_metric'] = 'profile-only; physical centerline measured separately'
        result['resize_chrome_hold'] = self._resize_chrome_hold
        result['lyric_relayout_count'] = self.lyric_relayout_count
        result['lyric_resize_frozen'] = self._lyric_resize_frozen
        result['lyric_resize_source_width'] = getattr(self, '_lyric_resize_w0', None)
        result['lyric_resize_playback_anchor'] = getattr(self, '_lyric_resize_playback_anchor', None)
        result['veil_cache_hit_ratio'] = self.veil.hits / max(1, self.veil.hits + self.veil.misses)
        result['controls_translate'] = -VeilOverlay.TOP_END * (1. - self.visibility.progress)
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
            self._timer.setInterval(self.ANIMATION_FRAME_MS)

    def hideEvent(self, event):
        if hasattr(self, "_width_chrome_animation"):
            self._width_chrome_animation.stop()
            self._finish_width_chrome()
        if self.live and hasattr(self, "study_timer"):
            self.study_timer.stop()
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if getattr(self, "live", False) and hasattr(self, "study_timer"):
            if not self.isVisible() or self.isMinimized():
                self.study_timer.stop()

    def closeEvent(self, event):
        if self.live:
            self.study_timer.stop()
        super().closeEvent(event)

    def update(self, *args):
        # Production material completions and layout animations request their
        # own (sometimes partial) paints. The narrow compositor advances the
        # whole background, so those paints must share the study's full-frame
        # cadence rather than interleave patches from different frames.
        timer = getattr(self, "study_timer", None)
        if self.live and timer is not None and (timer.isActive() or self._timer.isActive()):
            self._wide_lyrics_dirty = True
            return
        return super().update(*args)

    def _on_animation_tick(self):
        super()._on_animation_tick()
        if self.live and self.isVisible() and not self._closing_for_shutdown:
            self.tick()

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
        self._update_lyric_loading_state(now)
        self._sync_no_lyrics_content_layout(now)
        if self._pointer_exit_at is not None and now - self._pointer_exit_at >= .2:
            self._pointer_hover = False
            self._pointer_exit_at = None
        if self.live:
            self._prepare_live_commit()
            self._refresh_live_position_route()
            self._sync_live_capabilities()
            self.shelf = self.footer_hover = (
                self._pointer_hover or self._focus_reveal or now < self._pointer_active_until)
        self.last_tick = now
        if not self._width_chrome_active:
            self.visibility.advance(now, self.shelf or self.touch_active, self.footer_hover,
                                    self.mode == "playlist")
        target = self.visibility.footer.target
        if target != self._diagnostic_target:
            self._diagnostics['controls_retargets'] += 1
            self._diagnostic_target = target
        self._sync_artwork_palette(now)
        self._refresh_chrome_backdrop(now)
        self._sync_chrome_accessibility()
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
               bool(self.owner.enable_dual_line_lyrics),
               self._library_back_available(),
               self.owner.album_art.cacheKey(), self.owner.album_color.rgba(),
               self.owner.secondary_color.rgba(), self.owner.track_title, self.owner.artist,
               self._art_palette_cache_key,
               self.width(), self.height(), self.devicePixelRatioF(), self._width_chrome_progress,
               getattr(self.owner, 'media_position_source', ''), self.hovered_glass)
        if self.live and key == self._commit_key and not self._wide_lyrics_dirty:
            self._diagnostics['noop_paints'] += 1
            return
        self._commit_key = key
        self._wide_lyrics_dirty = False
        self._diagnostics['commits'] += 1
        assert self._diagnostics['commits'] <= self._diagnostics['samples']
        if self.narrow or self._width_chrome_active:
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

    def _sync_live_capabilities(self):
        # The provider owns these gates. Synchronize before the first release
        # frame as well as subsequent shared-owner presentation ticks.
        self._sync_owned_playback_mode()
        self.dj_available = self._owned_personal_dj_enabled is not None
        self.dj_enabled = self._owned_personal_dj_enabled is True
        self.dj_speaking = self._owned_dj_speaking
        modes = ("sequential", "shuffle", "repeat_one")
        self.loop_mode = modes.index(self._owned_playback_mode) if self._owned_playback_mode in modes else 0

    def _sync_artwork_palette(self, now):
        # Palette acquisition belongs to the presentation clock, including
        # fully hidden chrome. Extraction retains the shared single worker.
        palette = self._art_palette()
        self._track_transition(now, QColor(palette.primary), QColor(palette.secondary))
        if self._track_cover_animating(now):
            self._wide_lyrics_dirty = True
        identity = (self.owner.album_art.cacheKey(), self.owner.album_color.rgba(),
                    self.owner.secondary_color.rgba(), self._art_palette_cache_key)
        changed = identity != self._palette_sync_key
        self._palette_sync_key = identity
        if changed:
            self._chrome_backdrop = self._chrome_backdrop_key = None
            self._controls_key = None
            self._scene_control_ink_key = None
        if now - self._palette_resync_at < 10.:
            return
        self._palette_resync_at = now
        # Compare identities only: a healthy hidden scene does no raster work.
        # Invalidating on drift still obeys the normal veil throttle and resize
        # freeze; never force a veil preparation on each presentation tick.
        veil_drift = ((self.narrow or self._width_chrome_active)
                      and not self._background_cache.isNull()
                      and self._background_veil_key != self._background_veil_identity())
        controls_drift = ((self.narrow or self._width_chrome_active)
                          and self.variant != "A" and self._controls_palette_key != identity)
        chrome_drift = (self._chrome_backdrop_key is not None
                        and self._chrome_backdrop_key[5] != self._background_cache.cacheKey())
        if veil_drift or controls_drift or chrome_drift:
            self._chrome_backdrop = self._chrome_backdrop_key = None
            self._controls_key = self._commit_key = None
            self._scene_control_ink_key = None
            if veil_drift:
                self._background_veil_key = None

    def _sync_chrome_accessibility(self):
        if not hasattr(self, "visibility"):
            return
        names = {"library": "返回音乐库", "favorite": "喜欢", "playlist_add": "收藏到歌单",
                 "personal_dj": "私人DJ", "audio_output": "音频设备", "content_layout": "内容布局",
                 "playback_mode": "播放顺序", "translation": "中文翻译",
                 "return": "返回岛模式" if self.live else "窗口大小", "mode": "窗口大小",
                 "media:previous": "上一首", "media:toggle_play_pause": "播放或暂停", "media:next": "下一首"}
        slots = {key: rect for group in self.glass_groups() for key, rect in group.items()}
        collection = slots.pop("collection", None)
        if collection is not None:
            layout = NarrowLayout(self.width(), self.height(), dj_available=self.dj_available)
            count = len(layout.collection_slots) if self._chrome_phases()[1] > 0. else 2
            for index, key in enumerate(("favorite", "playlist_add")[:count]):
                slots[key] = QRectF(collection.left() + index * collection.width() / count,
                                   collection.top(), collection.width() / count, collection.height())
        for key, name in names.items():
            button = self._chrome_focus_buttons.get(key)
            if button is None:
                button = self._chrome_focus_buttons[key] = ChromeFocusButton(self, key, name)
            rect = slots.get(key)
            button.setVisible(rect is not None and rect.isValid())
            if rect is not None and rect.isValid():
                button.setGeometry(rect.toAlignedRect())

    def _top_control_hover_group(self, rect):
        # Five utility entries remain reachable for capture and owned playback.
        base, scale = self._window_control_base_rect(rect), self._surface_scale(rect)
        return {"audio_output": self._audio_output_button_rect(base, scale),
                "content_layout": self._content_layout_button_rect(base, scale),
                "playback_mode": self._playback_mode_button_rect(base, scale),
                "translation": self._translation_button_rect(base, scale),
                "mode" if self._fullscreen else "return": base}

    def _content_layout_button_rect(self, button, scale):
        return self._preceding_control_rect(self._playback_mode_button_rect(button, scale), scale)

    def _paint_window_controls(self, painter, rect, *, paint_ink=True):
        super()._paint_window_controls(painter, rect, paint_ink=paint_ink)
        if not paint_ink:
            return
        reveal = self._control_reveal_at(time.monotonic())
        if reveal <= .001:
            return
        base, scale = self._window_control_base_rect(rect), self._surface_scale(rect)
        painter.save()
        painter.setOpacity(min(1., reveal / .2))
        painter.translate(self._window_control_shift(base, reveal))
        ink = self._scene_control_ink()
        if self._owned_playback_mode is None:
            button = self._playback_mode_button_rect(base, scale)
            self._paint_surface_glass(painter, button, role="playback-mode")
            side = button.width() * .55
            paint_music_source_icon(painter, NarrowLayout.centered(button.center().x(), button.center().y(), side),
                                    "sequence", tint=ink)
        if not self._audio_output_available():
            button = self._audio_output_button_rect(base, scale)
            self._paint_surface_glass(painter, button, role="audio-output")
            paint_audio_output_status(painter, button, (), ink, count=0)
        painter.restore()

    def _song_collection_geometry_uncached(self, rect, now=None, *, shifted=True):
        result = super()._song_collection_geometry_uncached(rect, now, shifted=shifted)
        if result.isValid():
            return result
        # Capture has no collection authority; retain the same disabled face.
        scale = self._surface_scale(rect)
        side, margin = max(36., 44. * scale), max(10., 18. * scale)
        result = QRectF(rect.right() - margin - side * 2.5,
                        rect.bottom() - margin - side, side * 2.5, side)
        if shifted:
            reveal = self._control_reveal_at(time.monotonic() if now is None else now)
            result.translate(0.,
                             (rect.bottom() - result.top() + 4.) * (1. - reveal))
        return result

    def _personal_dj_geometry_uncached(self, rect, now, *, expansion=None, posed=True):
        if not getattr(self, "dj_available", False):
            return QRectF(), QRectF()
        return super()._personal_dj_geometry_uncached(rect, now, expansion=expansion, posed=posed)

    @staticmethod
    def _chrome_phase(value, start, end):
        return min(1., max(0., (value - start) / (end - start)))

    def _chrome_phases(self):
        if not self._width_chrome_active:
            return (1., 1., 0.) if self.narrow else (0., 0., 1.)
        phase = self._width_chrome_progress
        from_b, from_a, from_bottom = self._width_phase_from
        if self.narrow:
            return (from_b + (1. - from_b) * self._chrome_phase(phase, .30, .55),
                    from_a + (1. - from_a) * self._chrome_phase(phase, .55, 1.),
                    from_bottom * (1. - self._chrome_phase(phase, 0., .30)))
        # A fully withdraws before B splits. The bottom lane enters last.
        return (from_b * (1. - self._chrome_phase(phase, .45, .70)),
                from_a * (1. - self._chrome_phase(phase, 0., .45)),
                from_bottom + (1. - from_bottom) * self._chrome_phase(phase, .70, 1.))

    def _chrome_collection_reveal(self):
        if getattr(self, '_resize_chrome_hold', False):
            return 1.
        _fusion, top, bottom = self._chrome_phases()
        return min(1., (top if top > 0. else bottom) / .2)

    def glass_groups(self):
        if not self.narrow and not self._width_chrome_active:
            self._sync_window_control_rects()
            groups = super()._glass_control_groups(time.monotonic())
            groups[0] = dict(self._button_rects)
            return groups
        layout = NarrowLayout(self.width(), self.height(),
                              library_available=self._library_back_available(), dj_available=self.dj_available)
        top = {key: rect for key, rect in layout._top_rects.items() if key != "library"}
        if getattr(self, '_resize_chrome_hold', False) and self.dj_available:
            top['personal_dj'] = self._width_control_rects(True)['personal_dj']
        _fusion, top_phase, bottom_phase = self._chrome_phases()
        if self._width_chrome_active and top_phase <= 0. and bottom_phase > 0. and self.dj_available:
            # A narrow target may yield DJ, but its wide endpoint must still
            # finish the bottom exit/entry before that disclosure applies.
            top["personal_dj"] = self._width_control_rects(False).get("personal_dj", QRectF())
        if self._chrome_collection_reveal() < .75:
            top.pop("collection", None)
            top.pop("personal_dj", None)
        footer = {"media:previous": layout.previous, "media:toggle_play_pause": layout.play,
                  "media:next": layout.next}
        if self.visibility.progress < .75 and not getattr(self, "_capturing_chrome", False):
            return [{}, {}, {}]
        groups = [top, {"library": layout.back} if self._library_back_available() else {}, footer]
        return [{key: self.posed_rect(key, rect) for key, rect in group.items() if rect.isValid()}
                for group in groups]

    def posed_rect(self, key, rect):
        fusion, top, bottom = self._chrome_phases()
        if self._width_chrome_active:
            targets = self._width_control_rects(self.narrow)
            target = targets.get(key, rect)
            source = self._width_chrome_from.get(key, target)
            if key in {"collection", "personal_dj"} and getattr(self, '_resize_chrome_hold', False):
                rect = self._interpolate_rect(source, target, self._width_chrome_progress)
                rect.moveLeft(min(max(0., rect.left()), max(0., self.width() - rect.width())))
                rect.moveTop(min(max(0., rect.top()), max(0., self.height() - rect.height())))
            elif key in {"collection", "personal_dj"}:
                if top > 0.:
                    layout = NarrowLayout(self.width(), self.height(),
                                          library_available=self._library_back_available(), dj_available=self.dj_available)
                    slot = layout._top_rects.get(key, QRectF())
                    anchor = layout.capsule_b.left() - 8.
                    # Right anchored emergence, then settle between Back and B.
                    rect = QRectF(anchor + (slot.left() - anchor) * top, slot.top(),
                                  max(.001, slot.width() * top), slot.height())
                else:
                    _from_b, from_top, from_bottom = self._width_phase_from
                    travel = self.width() + 22.
                    if self.narrow:
                        # Reversal sources are already posed: add only the
                        # remaining displacement, never a second full slide.
                        rect = QRectF(source).translated(travel * (from_bottom - bottom), 0.)
                    else:
                        start = (QRectF(source) if from_top <= 0. else
                                 QRectF(target).translated(travel * (1. - from_bottom), 0.))
                        progress = (bottom - from_bottom) / max(.001, 1. - from_bottom)
                        rect = self._interpolate_rect(start, target, progress)
            else:
                progress = self._chrome_phase(self._width_chrome_progress, * ((.30, .55) if self.narrow else (.45, .70)))
                rect = self._interpolate_rect(source, target, progress)
        else:
            rect = QRectF(rect)
        if not getattr(self, "_capturing_chrome", False):
            hidden = 1. - self.visibility.progress
            lower = key.startswith("media:") or (key in {"collection", "personal_dj"} and top <= 0.)
            rect.translate(0.,
                           (VeilOverlay.BOTTOM_HEIGHT if lower else -VeilOverlay.TOP_END) * hidden)
        return rect

    def _width_control_rects(self, narrow):
        if narrow:
            layout = NarrowLayout(self.width(), self.height(),
                                  library_available=self._library_back_available(), dj_available=self.dj_available)
            rects = {**layout._top_rects, "media:previous": layout.previous,
                     "media:toggle_play_pause": layout.play, "media:next": layout.next}
            if getattr(self, '_resize_chrome_hold', False) and self.dj_available and 'personal_dj' not in rects:
                rects['personal_dj'] = QRectF(self.width() - 16. - layout.button_size,
                                              62., layout.button_size, layout.button_size)
            return rects
        return {key: rect for group in super()._glass_control_groups(time.monotonic())
                for key, rect in group.items()}

    def _refresh_chrome_backdrop(self, now, *, force=False):
        # Rasterize shared content into a cached, unfiltered backdrop without
        # grabbing the widget or scheduling another visible paint. Width
        # merges freeze this endpoint; blur remains owned by the renderer.
        if (not self.isVisible() or self._width_chrome_active
                or getattr(self, '_lyric_resize_frozen', False)
                or self._capturing_content_snapshot or self._capturing_transition_snapshot
                or getattr(self, "_capturing_chrome", False)
                or not hasattr(self, "visibility")
                or (self.visibility.progress <= .001 and self.visibility.opacity.target <= 0.)):
            return
        if force:
            # Artwork publication precedes the next widget paint. Prepare its
            # background here so cover, ambient field and glass share that
            # publication rather than waiting for playback/progress to repaint.
            palette = self._art_palette()
            target = QImage(1, 1, QImage.Format.Format_RGB32)
            with QPainter(target) as layer:
                super()._paint_background(layer, QRectF(self.rect()), QColor(palette.primary),
                                          QColor(palette.secondary), now,
                                          ambient_colors=palette.ambient_colors,
                                          ambient_weights=palette.ambient_weights)
        raw = self._lyric_resize_backdrop_scene()
        key = (self.width(), self.height(), self.devicePixelRatioF(), self.mode,
               self._visual_track_identity(), raw.cacheKey(), self.owner.album_art.cacheKey(),
               self.source_lines(), self.ink_position(), self._queue_scroll,
               self.visibility.progress, self.lyric_focus(),
               now if self._track_cover_animating(now) else 0.,
               bool(self.owner.enable_dual_line_lyrics))
        if not force and (key == self._chrome_backdrop_key or now - self._chrome_backdrop_at < .1):
            return
        ratio = self.devicePixelRatioF()
        image = QImage(round(self.width() * ratio), round(self.height() * ratio),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        self._capturing_content_snapshot = True
        try:
            with QPainter(image) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                # Only these padded bands can contribute to a glass sample.
                # Middle-of-page lyric/queue damage cannot dirty the chrome.
                region = QRegion()
                material = GLASS_CLEAR.at_level(self._surface_glass_level())
                padding = math.ceil(material.lens * (1. + material.chroma) + material.blur * 3. + 2.)
                for group in self.glass_groups():
                    for control in group.values():
                        region |= QRegion(control.adjusted(-padding, -padding, padding, padding).toAlignedRect())
                painter.setClipRegion(region)
                painter.drawImage(QRectF(self.rect()), raw)
                _content, cover, column = self._content_column_geometry()
                if self.narrow and self.mode == "lyrics":
                    self._paint_wide_lyrics_mounted(painter)
                    self.paint_header(painter, NarrowLayout(self.width(), self.height()))
                else:
                    if cover.isValid():
                        painter.save()
                        path = QPainterPath()
                        painted_cover = self._current_cover_rect(now)
                        path.addRoundedRect(painted_cover, painted_cover.width() * .055, painted_cover.height() * .055)
                        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
                        self._paint_track_cover_image(painter, painted_cover, painted_cover.width() * .055, now)
                        painter.restore()
                    center_plan = self._centered_album_plan(cover) if self.mode == "cover_lyrics" else None
                    self._paint_lyric_column(painter, QRectF(self.rect()), center_plan=center_plan)
                    if self.mode == "playlist":
                        scale = self._surface_scale()
                        self._paint_playback_queue(painter, column.adjusted(0., 8. * scale, 0., -4. * scale))
                # Content painters may replace their local clip. Discard any
                # resulting pixels outside the glass footprint before testing
                # dirtiness, including queue rows in the middle of the page.
                painter.setClipRegion(QRegion(self.rect()) - region)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        finally:
            self._capturing_content_snapshot = False
        self._chrome_backdrop_key, self._chrome_backdrop_at = key, now
        self._diagnostics['chrome_backdrop_prepares'] = self._diagnostics.get('chrome_backdrop_prepares', 0) + 1
        if force or self._chrome_backdrop is None or image != self._chrome_backdrop:
            self._chrome_backdrop = image
            self._diagnostics['chrome_backdrop_changes'] = self._diagnostics.get('chrome_backdrop_changes', 0) + 1
            self._controls_key = None
            self._wide_lyrics_dirty = True
            if force:
                # A new artwork event replaces every visible material face
                # together, including the renderer's staggered resting slots.
                self._clear_control_renderer.clear()
        self._clear_control_renderer.live_tint_scene = self._chrome_backdrop

    def _surface_glass_backdrop(self):
        scene = self._lyric_resize_backdrop_scene()
        frozen = getattr(self, "_width_glass_backdrop", None)
        if frozen is not None:
            self._clear_control_renderer.live_tint_scene = frozen
            return frozen
        backdrop = getattr(self, "_chrome_backdrop", None)
        if backdrop is not None and not self._capturing_content_snapshot:
            self._clear_control_renderer.live_tint_scene = backdrop
            return backdrop
        if getattr(self, '_lyric_resize_frozen', False):
            self._clear_control_renderer.live_tint_scene = scene
            return scene
        return super()._surface_glass_backdrop()

    def _lyric_resize_backdrop_scene(self):
        """Keep sharp pixels and chrome probes on the same frozen source."""
        if getattr(self, '_lyric_resize_frozen', False):
            scene = getattr(self, '_lyric_resize_background', None)
            if scene is None and not self._background_cache.isNull():
                scene = self._lyric_resize_background = QImage(self._background_cache)
            if scene is not None:
                return scene
        return self._background_cache

    def _refresh_lyric_resize_backdrop(self):
        """Refresh the veil once in the shared lyric-settle callback."""
        self._lyric_resize_background = None
        self.refresh_background_veil(time.monotonic(), force=True)
        self.refresh_veil()
        self._chrome_backdrop_key = None
        self._chrome_backdrop_at = -math.inf

    def _advance_width_chrome(self, value):
        # Animation frames only repaint the posed geometry. Material/controls
        # targets remain pinned for this complete handoff.
        self._width_chrome_progress = float(value)
        self.update()

    def _complete_width_chrome(self):
        self._clear_width_chrome()
        if self._width_transition is not None:
            self._width_transition.complete()
            self._content_layout_fit_narrow = self._width_transition.narrow
            self._content_layout_fit_key = None
            self._record_width_transition()
        self._sync_narrow_mode()
        if hasattr(self, '_sync_lyric_resize_motion'):
            self._sync_lyric_resize_motion()

    def _clear_width_chrome(self):
        self._width_chrome_active = False
        self._width_chrome_progress = 1.
        self._width_chrome_from.clear()
        self._width_content_from = ()
        self._width_glass_backdrop = None
        self._controls_key = None
        self.update()

    def _finish_width_chrome(self):
        if hasattr(self, '_width_chrome_animation'):
            self._width_chrome_animation.stop()
        self._clear_width_chrome()
        if self._width_transition is not None:
            self._width_transition.settle(getattr(self, "narrow", True))

    def _width_breakpoint_animated_externally(self):
        return True

    def _width_breakpoint_narrow(self):
        explicit = getattr(self, "_explicit_width_target", None)
        if explicit is not None and (self.width(), self.height()) == explicit[:2]:
            narrow = explicit[2]
            previous = getattr(self, "_content_layout_fit_narrow", None)
            self._content_layout_fit_key = None
            self._content_layout_fit_narrow = narrow
            if previous is None or previous != narrow:
                self._retarget_width_layout(narrow)
            return narrow
        return super()._width_breakpoint_narrow()

    def _sync_narrow_mode(self):
        previous = getattr(self, "narrow", None)
        explicit = getattr(self, "_explicit_width_target", None)
        if explicit is not None and (self.width(), self.height()) != explicit[:2]:
            self._explicit_width_target = None
        narrow = self._width_breakpoint_narrow()
        if narrow == previous or not hasattr(self, "_width_chrome_animation"):
            self.narrow = narrow
            return
        old_phases = self._chrome_phases()
        old_rects = self._width_control_rects(previous)
        if self._width_chrome_active:
            self._capturing_chrome = True
            try:
                old_rects = {key: self.posed_rect(key, rect) for key, rect in old_rects.items()}
            finally:
                self._capturing_chrome = False
        old_content = (self._content_column_geometry() if self._width_chrome_active
                       else self._width_lyric_geometry(previous))
        self.narrow = narrow
        self._width_chrome_animation.stop()
        if (self.isVisible() and self.mode in {"lyrics", "playlist"}
                and not getattr(self, "_explicit_width_toggle", False)
                and not self._content_layout_resize_suspended
                and self._pending_fullscreen is None
                and not self._island_entering and not self._island_returning
                and not self._capturing_transition_snapshot
                and not getattr(self, "_capturing_chrome", False)
                and not getattr(self, "_glass_capture_depth", 0)):
            self._width_phase_from = old_phases
            self._width_glass_backdrop = QImage(self._surface_glass_backdrop())
            self._width_chrome_from = old_rects
            self._width_content_from = old_content
            self._width_content_to = self._width_lyric_geometry(narrow)
            self._width_shaping_height = self.height()
            wide_scale = self._surface_scale()
            narrow_scale = NarrowLayout(self.width(), self.height()).lyric_font / self.LYRIC_PIXEL_SIZE
            self._width_font_scales = (wide_scale, narrow_scale) if narrow else (narrow_scale, wide_scale)
            self._width_chrome_progress = 0.
            self._width_chrome_active = True
            self._width_chrome_animation.setDuration(round(self._width_transition.duration * 1000.))
            self._record_width_transition()
            self._width_chrome_animation.setStartValue(0.)
            self._width_chrome_animation.setEndValue(1.)
            self._width_chrome_animation.start()
        else:
            self._finish_width_chrome()

    def resizeEvent(self, event):
        if hasattr(self, '_note_lyric_width_event'):
            self._note_lyric_width_event(event)
        self._sync_narrow_mode()
        super().resizeEvent(event)
        if self.narrow and hasattr(self, "veil"):
            self.refresh_veil()
            self.refresh_controls(time.monotonic())

    def paintEvent(self, event):
        self._diagnostics['paints'] += 1
        start = time.perf_counter()
        if self._geometry_snapshot_active():
            # The shared surface owns island/fullscreen morphs and compact
            # previews. Their bitmap must also win over mounted narrow ink.
            super().paintEvent(event)
        elif self.narrow and not self._layout_snapshot.isNull():
            self._paint_shared_narrow_content()
        elif self._width_chrome_active:
            self._paint_shared_narrow_content()
        elif not self.narrow:
            self._painted_live_hit_pose = None
            super().paintEvent(event)
            if self.live and self.mode == "cover_lyrics":
                self._painted_live_model = self.live_model_key()
                self._painted_track = self._visual_track_identity()
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

    def _geometry_snapshot_active(self):
        return (not self._compact_snapshot.isNull()
                or ((self._pending_fullscreen is not None or self._island_returning)
                    and not self._geometry_transition_snapshot.isNull()))

    def paint_narrow(self):
        now = time.monotonic()
        if self.live:
            self._prepare_live_commit()
            focus, visual = self.lyric_focus()
            if (self._snap_change == (self._visual_track_identity(), self.owner.lyric_changed_at)
                    and abs(focus - visual) > .000001):
                self._diagnostics['snap_frames'] += 1
        if self.mode != "lyrics":
            self._paint_shared_narrow_content()
            return
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
        layout = NarrowLayout(self.width(), self.height(),
                              library_available=self._library_back_available(),
                              dj_available=self.dj_available)
        focus, visual = self.lyric_focus()
        scrolling = self.mode == "lyrics" and abs(focus - visual) > .001
        self._diagnostics['paint_source'] = ("SCROLL" if scrolling else
                                             "LIVE" if self.owner.media_is_playing else "IDLE_CACHE")
        mounted = self._narrow_lyrics_mount
        content = None if mounted else self.content_image(layout, include_current=False)
        # Resting paused content can blend opaque endpoints. Moving content
        # always uses the same stanza compositor as ordinary playback.
        if (not mounted and fading and not scrolling and self._fade_anchor_key is not None
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
                self.background_veil.paint(painter, QRectF(self.rect()), visibility)
            else:
                self._paint_background(painter, QRectF(self.rect()), QColor(palette.primary), QColor(palette.secondary), now,
                                       ambient_colors=palette.ambient_colors, ambient_weights=palette.ambient_weights,
                                       advance_frame=not fading)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            if mounted:
                self._paint_wide_lyrics_mounted(painter)
            else:
                veil_key = self._fade_veil_key if fading and self._fade_veil_key is not None else self.veil_key
                self.veil.paint(painter, content, veil_key, visibility,
                                scroll_delta=self.veil_scroll_delta)
        if self._capturing_content_snapshot:
            # Album/title ink belongs to the content transition. Utility and
            # transport chrome continue to use their independent live groups.
            self.paint_header(painter, layout)
        if visibility > .001 and self._controls_bands is not None and not self._capturing_content_snapshot:
            self._paint_chrome_bands(painter)
            painter.save()
            painter.setOpacity(min(1., visibility / .2))
            self.paint_header(painter, layout)
            painter.restore()
        if self.hints and not self._capturing_content_snapshot:
            painter.setFont(self._surface_font(12, QFont.Weight.Medium))
            painter.setPen(QColor(255, 255, 255, 120))
            painter.drawText(QRectF(4, self.height() - 13, self.width() - 8, 13), Qt.AlignmentFlag.AlignCenter,
                             "L 歌词  P 列表  N/W 窄/宽  1–4 封面  H 隐藏")
        painter.end()
        if self.live and self.mode == "lyrics":
            # The shared painter publishes the geometry the listener saw.
            self._painted_live_model = self.live_model_key()
            self._painted_track = self._visual_track_identity()
            source_track = self._source_lines_key[0] if self._source_lines_key else None
            if source_track != self._painted_track:
                self._diagnostics['stale_frames'] += 1

    def _paint_surface_frame(self, event):
        super()._paint_surface_frame(event, paint_chrome=not (self.narrow or self._width_chrome_active))

    def _background_veil_identity(self):
        return (self._visual_track_identity(), self.width(), self.height(),
                (self.owner.album_art.cacheKey(), self.owner.album_color.rgba(),
                 self.owner.secondary_color.rgba(), self._art_palette_cache_key),
                self.visibility.opacity.generation, self._background_cache.cacheKey())

    def refresh_background_veil(self, now, *, force=False):
        if self._background_cache.isNull():
            return
        # Pin both endpoints for the whole reveal/hide generation. The source
        # is the raw AlbumFlow raster; it cannot contain lyric/control ink.
        key = self._background_veil_identity()
        if (not force and (getattr(self, '_lyric_resize_frozen', False)
                or ((getattr(self, '_resize_chrome_hold', False) or self._width_chrome_active)
                    and self.background_veil.ready))):
            self._lyric_resize_backdrop_scene()
            if key != self._background_veil_key:
                self._diagnostics['veil_stale_frames'] = self._diagnostics.get('veil_stale_frames', 0) + 1
            return
        same_palette = self._background_veil_key is not None and key[3] == self._background_veil_key[3]
        if (not force and same_palette and self.background_veil.ready
                and now - getattr(self, '_background_veil_at', -math.inf) < .1):
            return
        if force or key != self._background_veil_key or not self.background_veil.ready:
            self.background_veil.prepare(self._background_cache, self.width(), self.height(), force=force)
            self._background_veil_key = key
            self._background_veil_at = now
            self._diagnostics['veil_prepares'] = self.background_veil.prepare_count

    def _paint_background(self, painter, rect, primary, secondary, now, **kwargs):
        narrow = (getattr(self, "narrow", False) or self._width_chrome_active) and hasattr(self, "background_veil")
        previous_basis = self._background_cache_key
        if narrow and (not self.visibility.settled(now) or self._width_chrome_active
                       or getattr(self, '_resize_chrome_hold', False)):
            kwargs['advance_frame'] = False
        scene = self._lyric_resize_backdrop_scene()
        if getattr(self, '_lyric_resize_frozen', False) and not scene.isNull():
            self._background_rebuilt_this_paint = False
            if not getattr(self, '_navigation_cover_capture', False):
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(rect, scene)
                painter.restore()
        else:
            super()._paint_background(painter, rect, primary, secondary, now, **kwargs)
        if self._background_cache_key != previous_basis:
            self._background_veil_key = self._controls_key = None
            self._chrome_backdrop = self._chrome_backdrop_key = None
            self._scene_control_ink_key = None
            self._wide_lyrics_dirty = True
        if narrow:
            # Sample after the shared painter publishes its new raw raster.
            # A palette change may bypass the cadence once; moving frames keep
            # the existing 100ms preparation limit and frozen resize endpoint.
            self.refresh_background_veil(now)
            self.background_veil.paint(painter, rect, self.visibility.progress)

    def _paint_chrome_bands(self, painter):
        hidden = 1. - self.visibility.progress
        painter.save()
        painter.setOpacity(min(1., self.visibility.progress / .2))
        painter.drawImage(QPointF(0., -VeilOverlay.TOP_END * hidden), self._controls_bands[0])
        painter.drawImage(QPointF(0, self.height() - VeilOverlay.BOTTOM_HEIGHT
                                 + VeilOverlay.BOTTOM_HEIGHT * hidden), self._controls_bands[1])
        painter.restore()

    def refresh_glass_material(self):
        super().refresh_glass_material()
        # Cached narrow bands must change level together with the shared faces.
        self._controls_key = None
        self._controls_bands = None
        if hasattr(self, "visibility"):
            self.refresh_controls(time.monotonic())

    def _paint_shared_narrow_content(self):
        # Keep shared content live (including queue artwork/focus), then draw
        # the same narrow chrome for every selected mode.
        super().paintEvent(None)
        if not self._capturing_content_snapshot and self.visibility.progress > .001:
            with QPainter(self) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                layout = NarrowLayout(self.width(), self.height(),
                                      library_available=self._library_back_available(),
                                      dj_available=self.dj_available)
                if self._width_chrome_active:
                    self.paint_controls(painter, layout, self.visibility.progress)
                elif self._controls_bands is not None:
                    self._paint_chrome_bands(painter)
        if self.live and self.mode == "cover_lyrics":
            self._painted_live_model = self.live_model_key()
            self._painted_track = self._visual_track_identity()

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

        Resting controls refresh only after their sampled scene changes;
        input/feedback/layout changes invalidate immediately.
        """
        if self.variant == "A" or self._width_chrome_active:
            return
        palette_identity = (self.owner.album_art.cacheKey(), self.owner.album_color.rgba(),
                            self.owner.secondary_color.rgba(), self._art_palette_cache_key)
        key = (self.width(), self.height(), self.devicePixelRatioF(),
               self._glass_feedback.pose_key(), self.dj_motion.value(now),
               self.dj_available, self.dj_enabled, self._library_back_available(),
               int(now * 30) if self.dj_speaking else 0,
               self.loop_mode, self.mode, self.song_index, self.cover_index,
               palette_identity,
               self.owner.track_title, self.owner.artist,
               self._song_collection_state() if self.live else self.song_index in self.liked_songs,
               self.owner.media_is_playing, bool(self.owner.enable_dual_line_lyrics),
               not self._layout_snapshot.isNull(),
               self._scene_control_ink().rgba())
        hidden = (self.visibility.opacity.target <= 0
                  and self.visibility.progress <= .001)
        if hidden and key == self._controls_key:
            return
        fading = not self.visibility.settled(now)
        if key == self._controls_key and self._controls_bands is not None:
            scene = self._surface_glass_backdrop()
            pending = any(self._clear_control_renderer.would_refresh(role, scene, now, pose, state)
                          for role, pose, state in getattr(self, '_controls_roles', ()))
            if not pending or fading or now - self._controls_at < .1:
                return
        ratio = self.devicePixelRatioF()
        image = QImage(round(self.width() * ratio), round(self.height() * ratio),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        self._clear_control_renderer.begin_frame(now)
        self._clear_control_renderer.record_roles = []
        try:
            with QPainter(image) as layer:
                layer.setRenderHint(QPainter.RenderHint.Antialiasing)
                layer.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                layout = NarrowLayout(self.width(), self.height(),
                                      library_available=self._library_back_available(),
                                      dj_available=self.dj_available)
                self._capturing_chrome = True
                try:
                    self.paint_controls(layer, layout, 1.0)
                finally:
                    self._capturing_chrome = False
        finally:
            self._controls_roles = self._clear_control_renderer.record_roles
            self._clear_control_renderer.record_roles = None
            self._clear_control_renderer.end_frame()
        self._controls_bands = (image.copy(0, 0, image.width(), round(VeilOverlay.TOP_END * ratio)),
                                image.copy(0, round((self.height() - VeilOverlay.BOTTOM_HEIGHT) * ratio),
                                           image.width(), round(VeilOverlay.BOTTOM_HEIGHT * ratio)))
        self._controls_key, self._controls_at = key, now
        self._controls_palette_key = palette_identity

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
            super()._paint_background(layer, QRectF(self.rect()), QColor(palette.primary),
                                   QColor(palette.secondary), now,
                                   ambient_colors=palette.ambient_colors,
                                   ambient_weights=palette.ambient_weights, advance_frame=False)
        self._fade_background_key, self._fade_background = key, image
        self._fade_anchor_key = generation
        # Playlist fades retain this prepared veil basis. Lyrics own one
        # canonical stanza source and invalidate it only on a line/model change.
        self._fade_veil_key = self.veil_key
        self.refresh_background_veil(now)

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
        if self._narrow_lyrics_mount or self.mode != "lyrics":
            self._fade_content_key = self._fade_content_frames = None
            return
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
        super()._advance_lyric_animation(now)

    def _refresh_live_position_route(self):
        """Cache the provider's published route; never poll a physical clock."""
        self._live_position_route = ""
        if getattr(self.owner, "media_position_source", "") != "capture-model":
            return
        diagnostics = getattr(self.owner.data_provider, "diagnostics", None)
        if not callable(diagnostics):
            return
        try:
            snapshot = diagnostics()
            self._live_position_route = str(snapshot.get("capture", {}).get("position_route", ""))
        except Exception:
            # Ancillary diagnostics must not stop lyric following. The owner
            # remains the focus authority when a route cannot be identified.
            pass

    def _lyric_frame(self, timed, now):
        return super()._lyric_frame(timed, now)

    def _prepare_live_commit(self):
        """Invalidate a track/epoch in one UI transaction, before any painting."""
        track = self._visual_track_identity()
        epoch = getattr(self.owner, 'media_capture_epoch_id', 0)
        swapped = self._committed_track is not None and track != self._committed_track
        settled = bool(epoch) and (track, epoch) != self._settled_epoch
        if track == self._committed_track and not settled:
            return
        self._live_scroll_index = self._live_scroll_at = None
        self._live_lyric_diagnostics = {}
        if swapped:
            self._live_position_route = ""
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
        if self._narrow_lyrics_mount:
            _lines, timed = self._display_lyrics()
            frame = self._lyric_frame(timed, time.monotonic())
            return frame.active_index, frame.scroll_index
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
        if (getattr(self, '_lyric_resize_frozen', False)
                or ((getattr(self, '_resize_chrome_hold', False) or self._width_chrome_active)
                    and self.veil_key is not None)):
            return
        # Wide and mounted narrow lyrics use the shared column renderer.
        # Preparing the unused stanza atlas here delays width settlement.
        if not self.narrow or self._narrow_lyrics_mount or self.mode != "lyrics":
            return
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
        shift = 0. if getattr(self, "_capturing_chrome", False) else -VeilOverlay.TOP_END * (1. - self.visibility.progress)
        cover = layout.cover.translated(0., shift)
        palette = self._art_palette()
        now = time.monotonic()
        self._track_transition(now, QColor(palette.primary), QColor(palette.secondary))
        self._paint_track_cover_image(painter, cover, 10., now)
        column = layout.text.translated(0., shift)
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
        metrics_layout = self.reading_renderer.whole_line(split_display_layers(lines[focus].text).primary_text, font)
        ascent = metrics_layout.lineAt(0).ascent()
        painter.save()
        painter.setClipRect(viewport.adjusted(-18, -overscan, 18, overscan))
        for index in range(len(lines)):
            dots = lines[index].text == INTRO_COUNTDOWN_TEXT
            if (static_only and (index == focus or dots)) or (current_only and index != focus and not dots):
                continue
            line = lines[index]
            layers = split_display_layers(line.text)
            text = layers.primary_text
            y = anchor + (index - visual_focus) * step - ascent
            if y + row < viewport.top() - 16 - overscan or y > viewport.bottom() + 16 + overscan:
                continue
            if line.text == INTRO_COUNTDOWN_TEXT:
                end = (lines[index + 1].time_seconds if index + 1 < len(lines)
                       else line.time_seconds)
                if not line.time_seconds <= position < end:
                    continue
                self.paint_intro_dots(painter, QPointF(
                    viewport.left() + intro_dot_radius(font.pixelSize()) * (1.2 + 3.35),
                    layout.lyric_anchor + (index - visual_focus) * step), font)
                continue
            x = viewport.left()
            if self.lyrics_alignment == "center":
                width = QFontMetricsF(font).horizontalAdvance(text)
                x += (viewport.width() - width) / 2
            if layers.role_tag:
                self._paint_lyric_role_tag(painter, layers.role_tag, font, QPointF(x, y))
            if index != focus:
                text_layout = QTextLayout(text, font)
                text_layout.beginLayout()
                text_layout.createLine().setLineWidth(viewport.width())
                text_layout.endLayout()
                color = QColor(255, 255, 255) if index < focus else self._future_lyric_color(viewport)
                image, padding = self._lyric_raster(text, font, viewport.width(), row, text_layout, color)
                # Filter the same resting ink that the clear side displays.
                # A sharp-only veil source would revive distant rows midway
                # through the feather and expose a horizontal change in focus.
                level = 0 if abs(index - focus) <= 2 else self._lyric_group_blur_level(index, focus)
                image = self._blurred_lyric_raster(image, level, color)
                painter.drawImage(QPointF(x - padding, y - padding), image)
                continue
            end = (lines[index + 1].time_seconds if index + 1 < len(lines)
                   else max(line.time_seconds + 1.0, getattr(self.owner, "media_duration_seconds", 0.0)))
            source_primary = self._primary_timed_lyric_text(line.text)
            source_front = timed_unit_reading_front(
                source_primary, line.units, position, tuple(range(len(source_primary) + 1)),
                line_end=end,
            ) if layers.role_tag and line.units else None
            primary_front = (self._project_source_front(source_front, layers.primary_source_offsets)
                             if source_front is not None else None)
            self.reading_renderer.paint(
                painter, QRectF(x, y, viewport.width(), row), x, text, font,
                max(0.0, min(1.0, (position - line.time_seconds) / max(.001, end - line.time_seconds))),
                self._future_lyric_color(viewport), QColor(255, 255, 255),
                timed_units=line.units, timed_position=position,
                timed_line_end=end,
                timed_character_front=primary_front,
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
                if not line.time_seconds <= position < end:
                    continue
                font = self._surface_font(layout.lyric_font, QFont.Weight.Bold)
                metrics = QFontMetricsF(font)
                baseline_offset = metrics.ascent() - metrics.capHeight() / 2
                self.paint_intro_dots(painter, QPointF(
                    row.left() + intro_dot_radius(font.pixelSize()) * (1.2 + 3.35),
                    row.top() + baseline_offset), font)
                continue
            for part_index, (text, font, shaped, offset, height) in enumerate(runs):
                target = QRectF(row.left(), row.top() + offset, row.width(), height)
                if part_index == 0 and layers.role_tag:
                    self._paint_lyric_role_tag(painter, layers.role_tag, font, target.topLeft())
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

    def _paint_chrome_capsule(self, painter, slots, *, role, fusion=1., padding=0.):
        """One refractive face; feedback stays clipped to each action's slot."""
        valid = {key: slot for key, slot in slots.items() if slot.isValid()}
        if not valid:
            return
        ordered = sorted(valid.values(), key=lambda slot: slot.left())
        face = QRectF(ordered[0])
        for slot in ordered[1:]:
            face = face.united(slot)
        face.adjust(-padding, 0., padding, 0.)
        merging = self._width_chrome_active and fusion < .999
        path_members = [QRectF(slot) for slot in ordered]
        if len(path_members) > 1:
            path_members[0].translate(-padding, 0.)
            path_members[-1].translate(padding, 0.)
        path = fused_control_path(path_members, fusion) if merging else None
        if path is not None:
            face = path.boundingRect()
        states = [self._glass_feedback.state(key) for key in valid]
        motion = max((state for state in states if state is not None),
                     key=lambda state: (state.held, abs(state.lift), state.glow), default=None)
        self._clear_control_renderer.paint(
            painter, face, scene=self._surface_glass_backdrop(), scene_bounds=QRectF(self.rect()),
            ink=self._scene_control_ink(), now=time.monotonic(), role=role,
            state=None if self._width_chrome_active else motion, kind="path" if merging else "capsule", path=path,
            fusing=self._width_chrome_active,
            hover_segments=tuple((self._glass_feedback.hover.get(key, 0.), slot)
                                 for key, slot in valid.items()), level=self._surface_glass_level())

    def paint_controls(self, painter, layout, opacity):
        if opacity <= .001:
            return
        painter.save()
        painter.setOpacity(min(1., opacity / .2))
        ink = self._scene_control_ink()
        fusion, top, bottom = self._chrome_phases()
        if self._library_back_available():
            back = self.posed_rect("library", layout.back)
            self._paint_surface_glass(painter, back, role="library")
            paint_music_source_icon(painter, back.adjusted(8, 8, -8, -8),
                                    "login" if self._library_login_required() else "library_back", tint=ink)
        keys = ("audio_output", "content_layout", "playback_mode", "translation", "return")
        slots = {key: self.posed_rect(key, layout._top_rects[key]) for key in keys}
        if fusion > .001:
            self._paint_chrome_capsule(painter, slots, role="chrome-utilities", fusion=fusion,
                                       padding=layout._capsule_metrics[1] * fusion)
        else:
            for key, rect in slots.items():
                role = {"audio_output": "audio-output", "content_layout": "content-layout",
                        "playback_mode": "playback-mode", "translation": "translation", "return": "window-mode"}[key]
                self._paint_surface_glass(painter, rect, role=role)
        for key, rect in slots.items():
            icon = rect.adjusted(8, 8, -8, -8)
            if key == "content_layout":
                self._paint_content_layout_icon(painter, rect.adjusted(7, 7, -7, -7))
            elif key == "audio_output":
                paint_audio_output_status(painter, rect, self._audio_output_kinds, ink,
                                          count=len(self._audio_output_kinds))
            elif key == "playback_mode":
                mode = self._owned_playback_mode if self.live else ("sequence", "shuffle", "repeat_one")[self.loop_mode]
                asset = "personal_fm" if self._owned_personal_fm and mode == "sequential" else "sequence" if mode in {None, "sequential"} else mode
                paint_music_source_icon(painter, icon, asset, tint=ink)
            elif key == "translation":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._surface_glass_ink(rect, active=bool(self.owner.enable_dual_line_lyrics)))
                painter.drawPath(self._translation_symbol_path(QRectF(
                    rect.center().x() - rect.width() * .29, rect.center().y() - rect.height() * .29,
                    rect.width() * .58, rect.height() * .58)))
            else:
                self._paint_window_mode_icon(painter, icon, self._fullscreen, ink)
        for command, rect in (("previous", layout.previous),
                              ("toggle_play_pause", layout.play), ("next", layout.next)):
            rect = self.posed_rect("media:" + command, rect)
            self._paint_transport_icon(painter, layout.transport_icon(rect), command, ink)
        collection_reveal = self._chrome_collection_reveal()
        if collection_reveal <= .001:
            painter.restore()
            return
        painter.setOpacity(painter.opacity() * collection_reveal)
        favorite = self.posed_rect("collection", layout.favorite)
        count = len(layout.collection_slots) if top > 0. and not getattr(self, '_resize_chrome_hold', False) else 2
        collection_slots = {key: QRectF(favorite.left() + index * favorite.width() / count,
                                       favorite.top(), favorite.width() / count, favorite.height())
                            for index, key in enumerate(("favorite", "playlist_add")[:count])}
        dj_slot = layout.dj
        if getattr(self, '_resize_chrome_hold', False) and self.dj_available:
            dj_slot = self._width_control_rects(True)['personal_dj']
        if self._width_chrome_active and top <= 0. and bottom > 0. and self.dj_available:
            dj_slot = self._width_control_rects(False).get("personal_dj", QRectF())
        dj = self.posed_rect("personal_dj", dj_slot) if dj_slot.isValid() else QRectF()
        separate_dj = getattr(self, '_resize_chrome_hold', False) and not layout.dj.isValid()
        if top > 0.:
            self._paint_chrome_capsule(painter, {**collection_slots, **({"personal_dj": dj} if dj.isValid() and not separate_dj else {})},
                                       role="chrome-collection", fusion=top,
                                       padding=layout._capsule_metrics[1] * top)
            if separate_dj and dj.isValid():
                self._paint_surface_glass(painter, dj, role="dj-icon")
        else:
            self._paint_chrome_capsule(painter, collection_slots, role="collection")
            if dj.isValid():
                self._paint_surface_glass(painter, dj, role="dj-icon")
        collection_state = self._song_collection_state() if self.live else None
        liked = collection_state.liked is True if self.live else self.song_index in self.liked_songs
        for key, slot in collection_slots.items():
            side = min(slot.width(), slot.height()) * (.63 if key == "favorite" else .61)
            glyph = NarrowLayout.centered(slot.center().x(), slot.center().y(), side)
            icon_ink = QColor("#ff3e4c") if key == "favorite" and liked else ink
            painter.save()
            if self.live and (not collection_state.available or collection_state.busy):
                painter.setOpacity(painter.opacity() * .38)
            paint_music_source_icon(painter, glyph, ("heart_filled" if liked else "heart_outline")
                                    if key == "favorite" else "playlist_add", tint=icon_ink)
            painter.restore()
        if dj.isValid():
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

    def toggle_wide(self, wide=None):
        wide = self.narrow if wide is None else wide
        width, height = (960, 540) if wide else (360, 640)
        self._explicit_width_target = (width, height, not wide)
        if wide:
            # Let Qt apply both target dimensions without clamping to 560.
            self.setMinimumHeight(540)
        if (self.width(), self.height()) != (width, height):
            self._explicit_width_toggle = True
            try:
                self.resize(width, height)
            finally:
                self._explicit_width_toggle = False
            if hasattr(self, "_width_chrome_animation"):
                self._width_chrome_animation.stop()
                self._finish_width_chrome()
        if not wide and not self.live:
            self.setMinimumHeight(560)
        self._sync_narrow_mode()

    def return_to_island(self):
        if self.live:
            return super().return_to_island()
        self.toggle_wide()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if self.live:
                return super().keyPressEvent(event)
            self.close()
        elif key == Qt.Key.Key_N:
            self.toggle_wide(False)
        elif key == Qt.Key.Key_W:
            self.toggle_wide(True)
        elif key in (Qt.Key.Key_L, Qt.Key.Key_P, Qt.Key.Key_Tab):
            if key == Qt.Key.Key_Tab:
                self._cycle_content_layout()
            else:
                self.set_content_layout("lyrics" if key == Qt.Key.Key_L else "playlist", animate=False)
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
        # Keep the press path until release if resizing switches to wide mode.
        if (self._geometry_snapshot_active()
                or (not (self.narrow or self._width_chrome_active) and not self._narrow_drag)):
            super().mouseMoveEvent(event)
            return
        if (self.live and event.buttons() & Qt.MouseButton.LeftButton
                and (self._moving or self._resize_edges)):
            delta = event.globalPosition().toPoint() - self._drag_origin
            original = self._drag_geometry
            if self._moving:
                self.move(original.topLeft() + delta)
            else:
                edges = self._resize_edges
                requested_width = original.width() + (delta.x() if "right" in edges else -delta.x() if "left" in edges else 0)
                requested_height = original.height() + (delta.y() if "bottom" in edges else -delta.y() if "top" in edges else 0)
                available = self._available_geometry()
                # Clamp before anchoring: Qt's minimum-size correction alone
                # would move the opposite edge on top/left inward drags.
                width = max(self.minimumWidth(), min(available.width(), requested_width))
                height = max(self.minimumHeight(), min(available.height(), requested_height))
                if width > round(height * MUSIC_WINDOW_ASPECT_RATIO):
                    size = constrained_window_size(width, height, self.minimumSize(), available.size())
                    width, height = size.width(), size.height()
                x = original.right() - width + 1 if "left" in edges else original.left()
                y = original.bottom() - height + 1 if "top" in edges else original.top()
                self._set_resize_chrome_hold(True)
                self._content_layout_user_resizing = True
                try:
                    self.setGeometry(x, y, width, height)
                finally:
                    self._content_layout_user_resizing = False
            event.accept()
            return
        self.pointer_activity(event.position(), time.monotonic())
        self._glass_feedback.move(event.position())
        self.hovered_glass = ""
        if not event.buttons() and self._update_resize_cursor(event.position()):
            self.update()
            return
        self.unsetCursor()
        for group in self.glass_groups():
            for key, rect in group.items():
                if rect.contains(event.position()):
                    self.hovered_glass = (self._live_action_at(event.position())
                                          if key == "collection" else key)
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
        # Keep the existing surface Tab shortcut before QWidget consumes it
        # for child traversal. Tab delivered to a focused slot still traverses.
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Tab:
            self.keyPressEvent(event)
            event.accept()
            return True
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
        if self._geometry_snapshot_active() or not (self.narrow or self._width_chrome_active):
            super().mousePressEvent(event)
            return
        if self.live:
            if event.button() == Qt.MouseButton.LeftButton:
                action = (None if self._edge_set(event.position())
                          else self._live_action_at(event.position()))
                if action is not None:
                    self._begin_live_press(event.position())
                else:
                    self._resize_edges = self._edge_set(event.position())
                    self._moving = not self._fullscreen and not self._resize_edges
                    self._narrow_drag = bool(self._moving or self._resize_edges)
                    self._drag_origin = event.globalPosition().toPoint()
                    self._drag_geometry = self.geometry()
                    if self._resize_edges:
                        self._set_resize_chrome_hold(True)
                event.accept()
            return
        self._press_at(event.position())

    def _live_action_at(self, position):
        if self.visibility.progress >= .75:
            groups = self.glass_groups()
            # Navigation owns its painted slot even if another control's
            # target overlaps it during a handoff.
            if self._library_back_available():
                for group in groups:
                    back = group.get("library")
                    if back is not None and back.contains(position):
                        return "library"
            for group in groups:
                for key, rect in group.items():
                    if rect.contains(position):
                        if key == "collection":
                            layout = NarrowLayout(self.width(), self.height(), dj_available=self.dj_available)
                            single = (self._chrome_phases()[1] > 0. and len(layout.collection_slots) == 1
                                      and not getattr(self, '_resize_chrome_hold', False))
                            return "favorite" if single or position.x() < rect.center().x() else "playlist_add"
                        return key
        if self.mode == "playlist":
            for index, rect in self._queue_hit_rects.items():
                if rect.contains(position):
                    token = self._queue_snapshot[0] if self._queue_snapshot is not None else None
                    return ("queue", index, token)
        if self.mode in {"lyrics", "cover_lyrics"}:
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
            self._cycle_content_layout()
            self._finish_content_layout()
        elif action in {"playback_mode", "footer_loop"}:
            self._cycle_owned_playback_mode()
        elif action in {"favorite", "playlist_add"}:
            self._activate_song_collection(action, track, authority)
        elif action == "translation":
            self._toggle_translation()
        elif action == "personal_dj":
            self._toggle_personal_dj()
        elif action == "audio_output":
            self._toggle_audio_output_panel()
        elif action == "library":
            self.return_to_library()
        elif action == "mode":
            self.toggle_presentation_mode()
        elif action == "return":
            self.return_to_island()
        self.refresh_veil()
        self.refresh_controls(time.monotonic())
        self.update()

    def _press_at(self, position):
        if self.live:
            self._begin_live_press(position)
            return
        visibility = self.visibility.progress
        self.visibility.advance(time.monotonic(), True, True, self.mode == "playlist")
        library_available = self._library_back_available()
        layout = NarrowLayout(self.width(), self.height(), visibility,
                              library_available=library_available, dj_available=self.dj_available)
        footer_visible = visibility > .001
        action = None
        for group in self.glass_groups():
            for key, rect in group.items():
                if rect.contains(position):
                    action = key
                    self._glass_feedback.press(key, position, time.monotonic())
        if footer_visible and library_available and action == 'library':
            self.return_to_library()
        elif footer_visible and action == 'return':
            self.toggle_wide()
        elif footer_visible and action == 'content_layout':
            self._cycle_content_layout()
            self._finish_content_layout()
        elif footer_visible and action == 'media:previous':
            self.select_song(self.song_index - 1)
        elif footer_visible and action == 'media:next':
            self.select_song(self.song_index + 1)
        elif footer_visible and action == 'media:toggle_play_pause':
            self.owner.media_is_playing = not self.owner.media_is_playing
        elif footer_visible and action == 'collection':
            if position.x() < layout.favorite.center().x():
                if self.song_index in self.liked_songs:
                    self.liked_songs.remove(self.song_index)
                else:
                    self.liked_songs.add(self.song_index)
            else:
                self.playlist_songs.add(self.song_index)
        elif footer_visible and action == 'translation':
            self._toggle_translation()
        elif footer_visible and action == 'playback_mode':
            self.loop_mode = (self.loop_mode + 1) % 3
        elif footer_visible and action == 'content_layout':
            self._cycle_content_layout()
            self._finish_content_layout()
        elif footer_visible and self.dj_available and action == 'personal_dj':
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
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_resize_chrome_hold(False)
        if (self._geometry_snapshot_active()
                or (not (self.narrow or self._width_chrome_active) and not self._narrow_drag)):
            super().mouseReleaseEvent(event)
            return
        if self.live:
            if event.button() == Qt.MouseButton.LeftButton:
                self._finish_live_press(event.position())
                if self._moving or self._resize_edges:
                    self._normal_geometry = self.geometry()
                self._moving = False
                self._resize_edges = set()
                self._narrow_drag = False
            event.accept()
            return
        self._glass_feedback.release()
        self.update()

    def _lyric_browse_blur_clear(self):
        if not self.narrow:
            return super()._lyric_browse_blur_clear()
        return time.monotonic() < self._lyric_scroll_sharp_until

    def wheelEvent(self, event):
        if self.live and self.narrow and self.mode == "playlist":
            self._scroll_playback_queue(event)
            self._list_layer_key = None
            self._list_layer = None
            self.refresh_veil()
            event.accept()
            return
        previous = self._browse_until
        super().wheelEvent(event)
        if self.narrow and self.mode in {"lyrics", "cover_lyrics"} and self._browse_until != previous:
            # Row ink alone becomes sharp while browsing. The independently
            # prepared chrome veil/control glass keeps its normal material.
            self._lyric_scroll_sharp_until = time.monotonic() + self.LYRIC_SCROLL_SHARP_SECONDS

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
