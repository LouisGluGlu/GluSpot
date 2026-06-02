# -*- coding: utf-8 -*-
"""
Local visual renderer for Island Stack V2.

Controls:
  M       Toggle music island
  P       Toggle persistent plugin island
  N       Push/update a fake notification
  C       Clear notifications
  F       Toggle Finder simulation / standalone mode
  Space   Expand / collapse island stack
  1/2/3   Focus plugin: time / weather / schedule
  Esc     Quit
"""

from __future__ import annotations

import json
import math
import random
import re
import sys
import threading
import time
import ctypes
import unicodedata
from datetime import datetime, timedelta
from ctypes import wintypes
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPalette,
    QRadialGradient,
    QRegion,
)
try:
    from PySide6.QtSvg import QSvgRenderer
except Exception:
    QSvgRenderer = None
from PySide6.QtWidgets import QApplication, QWidget

from api.lyric_timeline import active_index_for_position, lyric_phase_for_index
from api.models import IslandVisualData
from api.providers import EmptyIslandDataProvider
from local.mydockfinder import FinderGlassMaterial, read_finder_material
from local.ui_theme import app_dark_mode, is_theme_change_event


class IslandKind(Enum):
    MUSIC = "music"
    PLUGIN = "plugin"
    PLUGIN_PANEL = "plugin_panel"
    PLUGIN_ALERT = "plugin_alert"
    MESSAGE = "message"
    THIN_BAR = "thin_bar"


@dataclass
class Message:
    app: str
    sender: str
    text: str
    name_color: QColor
    avatar_color: QColor
    avatar_image: QImage = field(default_factory=QImage)
    serial: int = 0
    created_at: float = field(default_factory=time.monotonic)
    popup_until: float = field(default_factory=lambda: time.monotonic() + 5.0)
    barrage_started_at: float = 0.0


@dataclass
class StackVisual:
    key: str
    kind: IslandKind
    rect: QRectF
    folded: bool = False
    message: Message | None = None
    z_index: int = 0


@dataclass(frozen=True)
class LyricLayout:
    text: str
    chunks: tuple[str, ...]
    widths: tuple[float, ...]
    total_width: float
    font_key: tuple[object, ...]


class SpringRect:
    def __init__(self, rect: QRectF):
        self.x = rect.x()
        self.y = rect.y()
        self.w = rect.width()
        self.h = rect.height()
        self.vx = 0.0
        self.vy = 0.0
        self.vw = 0.0
        self.vh = 0.0
        self.opacity = 0.0

    @property
    def rect(self) -> QRectF:
        return QRectF(self.x, self.y, self.w, self.h)

    def step(self, target: QRectF, target_opacity: float, dt: float, response: float = 0.34, damping: float = 0.76):
        dt = max(0.001, min(0.033, dt))
        omega = 2.0 * math.pi / response

        self.x, self.vx = self._spring(self.x, self.vx, target.x(), omega, damping, dt)
        self.y, self.vy = self._spring(self.y, self.vy, target.y(), omega, damping, dt)
        self.w, self.vw = self._spring(self.w, self.vw, target.width(), omega, damping, dt)
        self.h, self.vh = self._spring(self.h, self.vh, target.height(), omega, damping, dt)
        self.opacity += (target_opacity - self.opacity) * min(1.0, dt * 14.0)

    @staticmethod
    def _spring(value: float, velocity: float, target: float, omega: float, damping: float, dt: float) -> tuple[float, float]:
        acceleration = omega * omega * (target - value) - 2.0 * damping * omega * velocity
        velocity += acceleration * dt
        value += velocity * dt
        return value, velocity


class IslandStackVisual(QWidget):
    VISUAL_SCALE = 0.75
    STACK_WIDTH = 560
    DEFAULT_FONT_FAMILY = "PingFang SC"
    GELV_FONT_FAMILY = "Source Han Serif SC"
    GELV_FONT_CHOICES = ("Source Han Serif SC", "PingFang SC")
    _source_han_serif_loaded = False
    CHINESE_FONT_FALLBACKS = (
        "PingFang SC",
        "Source Han Serif SC",
    )
    MUSIC_AMBIENCE_STYLES = ("sea_level", "duotone_full", "pure_black", "bilateral_diffraction")
    COLOR_FILL_AMBIENCE_STYLES = ("pure_black", "bilateral_diffraction")
    LYRIC_FLOW_STYLES = ("wave_stream", "gelv_songscatter", "album_island")
    ALBUM_ISLAND_SECONDARY_STYLES = ("shrink_dim", "perspective_dim")

    def __init__(self, data_provider=None):
        super().__init__()
        self.data_provider = data_provider or EmptyIslandDataProvider()
        visual_data = self.data_provider.get_visual_data()
        self.finder_material: FinderGlassMaterial = read_finder_material(include_center=True)
        self.finder_mode = bool(self.finder_material.available)
        self.attach_to_real_finder = self.finder_mode
        self.setWindowTitle("Island Stack V2")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.resize(int(round(self._standalone_canvas_width())), int(round(self._u(650))))
        self.stack_center_ratio = 0.5
        self.saved_window_pos: tuple[int, int] | None = None
        self.drag_press_pos = QPointF()
        self.drag_press_global = QPoint()
        self.drag_press_window_pos = QPoint()
        self.drag_press_center_ratio = 0.5
        self.drag_press_key = ""
        self.drag_press_main_offset = QPointF()
        self.drag_candidate = False
        self.dragging = False
        self.drag_threshold = 5.0
        self.super_topmost_enabled = True
        self.topmost_reinforced_at = 0.0
        self.scene_suppressed = False
        self.scene_reason = ""
        self.scene_checked_at = 0.0
        self.scene_transition_mode = ""
        self.scene_transition_started_at = 0.0
        self.scene_transition_duration = 0.18
        self.settings_window_active = False
        self.last_tick_at = time.monotonic()
        self.input_passthrough_enabled = False
        self.barrage_clock = 0.0
        self.barrage_rects: dict[str, QRectF] = {}
        self.barrage_hovered = False
        self.barrage_dark_background = False
        self.barrage_style_checked_at = 0.0
        self.last_pointer_press_at = 0.0
        self.last_pointer_move_at = 0.0
        self.last_mask_update_at = 0.0
        self.interaction_mask_active = False
        self.surface_transition_pixmap = QPixmap()
        self.surface_transition_global_rect = QRectF()
        self.surface_transition_started_at = 0.0
        self.surface_transition_duration = 0.22
        self.surface_transition_reason = ""
        self.mask_deferred_until = 0.0
        self.drag_detach_global_rects: dict[str, QRectF] = {}
        self._center_on_screen()

        self.music_playing = True
        self.music_auto_detect = False
        self.media_is_playing = True
        self.media_duration_seconds = 0.0
        self.media_position_seconds = 0.0
        self.media_track_id = ""
        self.media_source_app = ""
        self.media_position_source = "windows-media"
        self.media_position_confidence = 1.0
        self.listened_seconds_total = 0.0
        self.album_history: list[dict[str, object]] = []
        self.album_queue: list[dict[str, object]] = []
        self.album_island_transition_started_at = 0.0
        self.album_island_transition_reason = ""
        self.album_island_transition_direction = 1
        self.lyrics_source = ""
        self.timed_lyrics: list[tuple[float, str]] = []
        self.album_art = QImage()
        self.last_media_refresh_at = 0.0
        self.media_refresh_interval = 0.65
        self.music_button_rects: dict[str, QRectF] = {}
        self.music_cover_rect = QRectF()
        self.persistent_plugin = True
        self.enabled_plugin_count = 3
        self.show_seconds = True
        self.expanded = False
        self.plugin_panel_open = False
        self.plugin_focus = 0
        self.plugin_focus_from = 0
        self.plugin_focus_target = 0
        self.plugin_focus_changed_at = time.monotonic()
        self.plugin_alert_width = 168.0
        self.side_island_style = "standard"
        self.plugin_island_style = "standard"
        self.music_island_style = "standard"
        self.time_alarm_side_display = "note"
        self.pointer_pos = QPointF(0, 0)
        self.hovered_key = ""
        self.hover_started_at = 0.0
        self.hover_bounce_until = 0.0
        self.hover_armed_key = ""
        self.left_stack_at = 0.0
        self.opened_source_app = ""
        self.opened_source_until = 0.0
        self.stack_bounce_until = 0.0
        self.settings_changed_callback = None
        self.settings_getter = None
        self.settings_setter = None
        self.plugin_manager_windows: dict[str, QWidget] = {}
        self.plugin_action_rects: dict[str, QRectF] = {}
        self.plugin_time_mode = "dashboard"
        self.plugin_time_visible_module = "countdown"
        self.plugin_time_module_animation: dict[str, object] = {}
        self.plugin_panel_last_width = 0.0
        self.plugin_timer_paused_remaining_seconds = 0
        self.plugin_timer_paused_progress = 0.0
        self.plugin_countdown_entries: list[dict[str, object]] = []
        self.plugin_alarm_rows = self._default_time_alarm_rows()
        self.plugin_alarm_list_scroll_index = 0
        self.plugin_alarm_list_bottom_spacer_rows = 0.0
        self.plugin_alarm_list_max_scroll = 0.0
        self.plugin_alarm_list_rect = QRectF()
        self.plugin_alarm_visible_range = (0, 0)
        self.plugin_alarm_delete_index: int | None = None
        self.plugin_alarm_editor = self._default_alarm_editor_state()
        self.plugin_alarm_editor_focus = ""
        self.plugin_alarm_editor_input_buffers: dict[str, str] = {}
        self.plugin_alarm_editor_spin_animations: dict[str, dict[str, float | int]] = {}
        self.plugin_alarm_editor_page_animation: dict[str, object] = {}
        self.plugin_alarm_editor_reflow_animation: dict[str, object] = {}
        self.plugin_alarm_editor_rects: dict[str, QRectF] = {}
        self.plugin_alarm_editor_note_hint = ""
        self.plugin_alarm_editor_note_hint_until = 0.0
        self.plugin_alarm_due_pending: dict[str, object] = {}
        self.plugin_alarm_due_acknowledged_key = ""
        self.show_debug_overlay = False
        self.finder_material_refreshed_at = time.monotonic()
        self.manual_finder_available = False
        self.ui_font_family = self.DEFAULT_FONT_FAMILY
        self.high_quality_font_rendering = False
        self.animation_fps = 60
        self.animation_speed = 1.0
        self.empty_bar_palette = "aurora"
        self.empty_bar_width = 92
        self.empty_bar_height = 14
        self.empty_bar_y_offset = 0
        self.empty_bar_opacity = 100
        self.empty_bar_custom_colors = ("#46D6FF", "#73FFA6", "#FF5A86")
        self.barrage_y_offset = 0
        self.last_barrage_preview_at = 0.0
        self.barrage_font_size = 10
        self.barrage_speed = 100
        self.barrage_use_backdrop = True
        self.barrage_show_icon = True
        self.auto_expand_bounds = True
        self.expand_direction = "auto"
        self.no_lyrics_text = "暂无歌词"
        self.lyric_offset_seconds = 0.0
        self.lyric_font_size = 18
        self.lyric_elastic_strength = 100
        self.lyric_elastic_mode = "both"
        self.lyric_word_spacing = 100
        self.wave_color_fill_enabled = False
        self.wave_color_fill_fallback = "#00D7FF"
        self.enable_dual_line_lyrics = False
        self.dual_line_secondary_weight = 72
        self.dual_line_secondary_spacing = 100
        self.compact_lyric_show_cover = True
        self.lyric_flow_style = "wave_stream"
        self.gelv_font_family = self.GELV_FONT_FAMILY
        self.gelv_primary_font_size = 22
        self.gelv_secondary_font_size = 12
        self.gelv_dual_line_font_size = 17
        self.gelv_initial_word_spacing = 220
        self.gelv_word_spacing = 320
        self.gelv_size_shift_enabled = True
        self.gelv_color_fill_enabled = False
        self.gelv_color_fill_fallback = "#00D7FF"
        self.no_lyrics_display_mode = "text"
        self.album_island_cover_count = 5
        self.album_island_cover_size = 30
        self.album_island_spacing = 112
        self.album_island_secondary_style = "perspective_dim"
        self.album_island_perspective_amount = 100
        self.lyric_transition_kind = ""
        self.lyric_transition_distance = 0
        self.lyric_last_sync_position = 0.0
        self.lyric_last_phase_index = -1
        self.lyric_last_phase_progress = 0.0
        self.lyric_last_phase_at = 0.0
        self._lyric_layout_cache: dict[tuple[object, ...], LyricLayout] = {}
        self._representative_lyric_width_cache: dict[tuple[object, ...], float] = {}
        self._scaled_album_art_cache: dict[tuple[str, int, int], QImage] = {}
        self.font_shrink_scale = 1.0
        self.stack_direction = 1
        self.messages: list[Message] = []
        self.barrage_preview_messages: list[Message] = []
        self._seen_provider_message_keys: set[str] = set()

        self._apply_visual_data(visual_data, initial=True)
        self.current_audio_bands = (0.44, 0.48, 0.36)
        self.live_audio_available = False
        self.wave_values = [0.0] * 36
        self.wave_targets = [random.uniform(0.12, 0.72) for _ in self.wave_values]
        self.scroll_phase = random.random() * 10.0
        self.ambient_phase = random.random() * 10.0
        self.ambient_intensity = 0.0
        self.music_ambience_style = "sea_level"
        self.music_flow_intensity = 0.74
        self.compact_duotone_balance = 50
        self.expanded_duotone_balance = 50

        self.message_samples = [
            (
                message.app,
                message.sender,
                message.text,
                QColor(*message.name_color.rgba()),
                QColor(*message.avatar_color.rgba()),
            )
            for message in visual_data.messages
        ]
        self.plugin_samples = [
            (
                plugin.label,
                plugin.detail,
                plugin.summary,
                QColor(*plugin.color.rgba()),
                plugin.icon,
                plugin.plugin_id,
                plugin.status,
                plugin.alert_title,
                plugin.alert_body,
                plugin.alert_priority,
                plugin.progress,
            )
            for plugin in visual_data.plugins
        ]
        self.sample_index = 0

        self.motions: dict[str, SpringRect] = {}
        self.group_frame_motion: SpringRect | None = None
        self.group_frame_mask_path: QPainterPath | None = None
        self.finder_hover_rect = QRectF()
        self.last_mask_rect = QRectF()
        self.last_frame_at = time.monotonic()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(self._timer_interval_ms())

        if self.message_samples:
            self.add_message()
            QTimer.singleShot(650, self.add_message)
            QTimer.singleShot(1300, self.add_message)

    def _apply_visual_data(self, visual_data: IslandVisualData, initial: bool = False):
        now = time.monotonic()
        track = visual_data.track
        previous_track_id = getattr(self, "media_track_id", "")
        previous_title = str(getattr(self, "track_title", "") or "")
        previous_artist = str(getattr(self, "artist", "") or "")
        previous_lyrics = tuple(getattr(self, "lyrics", ()))
        previous_album_island = bool(self._should_paint_album_island()) if hasattr(self, "lyrics") else False
        incoming_track_id = track.track_id or f"{track.source_app}:{track.title}:{track.artist}"
        track_changed = initial or incoming_track_id != previous_track_id
        same_visible_track = previous_title == str(track.title or "") and previous_artist == str(track.artist or "")
        message_refresh = bool(getattr(visual_data, "messages", ()))
        flow_direction = 1
        if track_changed and not initial:
            flow_direction = self._prepare_album_flow_for_track_change(incoming_track_id)
        self.track_title = track.title
        self.artist = track.artist
        self.album_color = QColor(*track.album_color.rgba())
        self.secondary_color = QColor(*track.secondary_color.rgba())
        self.audio_frames = list(track.audio_frames)
        incoming_is_playing = bool(track.is_playing)
        incoming_position = max(0.0, float(track.position_seconds))
        incoming_position_source = str(getattr(track, "position_source", "windows-media") or "windows-media")
        incoming_position_confidence = max(0.0, min(1.0, float(getattr(track, "position_confidence", 1.0) or 0.0)))
        incoming_position_interactive = bool(getattr(track, "position_interactive", False))
        frontstage_position = bool(getattr(track, "source_app_foreground", False) or incoming_position_interactive or incoming_position_source == "interactive")
        previous_position = self._estimated_media_position(now) if hasattr(self, "media_position_sync_at") else 0.0
        previous_sync_at = float(getattr(self, "media_position_sync_at", now))
        elapsed_since_sync = max(0.0, now - previous_sync_at)
        raw_position = incoming_position
        previous_raw_position = getattr(self, "media_raw_position_seconds", None)
        previous_raw_source = str(getattr(self, "media_raw_position_source", "") or "")
        same_position_source = previous_raw_source == incoming_position_source
        raw_delta = None
        if previous_raw_position is not None and same_position_source:
            raw_delta = raw_position - float(previous_raw_position)
        trusted_external_position = incoming_position_confidence >= 0.75 and (incoming_position_source != "windows-media" or frontstage_position)
        repeated_stale_position = (
            not track_changed
            and incoming_is_playing
            and previous_raw_position is not None
            and same_position_source
            and raw_position > 0.0
            and abs(float(previous_raw_position) - raw_position) <= 0.08
            and previous_position > raw_position + 0.35
        )
        stale_windows_fallback_position = (
            not track_changed
            and incoming_is_playing
            and incoming_position_source == "windows-media"
            and previous_raw_source not in ("", "windows-media")
            and raw_position > 0.0
            and previous_position > raw_position + 0.35
        )
        seek_like_position = (
            not track_changed
            and incoming_is_playing
            and previous_raw_position is not None
            and same_position_source
            and raw_position > 0.0
            and raw_delta is not None
            and (
                raw_delta <= -0.35
                or abs(raw_position - previous_position) >= 2.8
                or raw_delta >= max(1.6, elapsed_since_sync + 0.9)
            )
        )
        if not track_changed and incoming_is_playing and frontstage_position:
            if raw_position > 0.0 or self._looks_like_seek_to_start(raw_position, previous_position, previous_raw_position):
                incoming_position = raw_position
            elif previous_position > 0.0:
                incoming_position = previous_position
        elif not track_changed and incoming_is_playing and (trusted_external_position or seek_like_position):
            if repeated_stale_position:
                incoming_position = previous_position
            else:
                incoming_position = raw_position
        elif not track_changed and incoming_is_playing:
            if repeated_stale_position or stale_windows_fallback_position:
                incoming_position = previous_position
            elif incoming_position <= 0.0 and previous_position > 0.5:
                incoming_position = previous_position
            elif 0.0 < previous_position - incoming_position <= 2.5:
                incoming_position = previous_position
            elif incoming_position == 0.0 and previous_position > 0.0:
                incoming_position = previous_position
            else:
                drift = incoming_position - previous_position
                if abs(drift) <= 0.95:
                    incoming_position = previous_position + drift * 0.24
        self.media_is_playing = incoming_is_playing
        self.media_duration_seconds = max(0.0, float(track.duration_seconds))
        self.media_position_seconds = incoming_position
        if not track_changed and incoming_is_playing:
            listened_delta = min(max(0.0, incoming_position - previous_position), max(0.0, elapsed_since_sync) + 1.0)
            self.listened_seconds_total = float(getattr(self, "listened_seconds_total", 0.0)) + listened_delta
        self.media_position_sync_at = now
        self.media_raw_position_seconds = raw_position
        self.media_raw_position_source = incoming_position_source
        self.media_track_id = incoming_track_id
        self.media_source_app = track.source_app
        self.media_position_source = incoming_position_source
        self.media_position_confidence = incoming_position_confidence
        self.media_position_interactive = incoming_position_interactive
        self.media_source_foreground = bool(getattr(track, "source_app_foreground", False))
        self.media_refresh_interval = 0.18 if frontstage_position else 0.65
        self.lyrics_source = track.lyrics_source
        had_timed_lyrics = bool(getattr(self, "timed_lyrics", ()))
        self.timed_lyrics = self._coalesced_timed_lyrics([(line.time_seconds, line.text) for line in track.timed_lyrics])
        timed_lyrics_appeared = bool(self.timed_lyrics) and not had_timed_lyrics and not track_changed
        self.plugin_samples = [
            (
                plugin.label,
                plugin.detail,
                plugin.summary,
                QColor(*plugin.color.rgba()),
                plugin.icon,
                plugin.plugin_id,
                plugin.status,
                plugin.alert_title,
                plugin.alert_body,
                plugin.alert_priority,
                plugin.progress,
            )
            for plugin in visual_data.plugins
        ]
        self._ingest_provider_messages(visual_data)
        raw_lyrics = [text for _time, text in self.timed_lyrics] if self.timed_lyrics else list(track.lyrics)
        if not self.timed_lyrics and (not raw_lyrics or raw_lyrics == ["暂无歌词"]):
            raw_lyrics = [getattr(self, "no_lyrics_text", "暂无歌词")]
        self.lyrics = raw_lyrics or [track.title or "正在播放"]
        if not self.lyrics:
            self.lyrics = ["正在播放"]
        if tuple(self.lyrics) != previous_lyrics:
            self._clear_lyric_metrics_cache()
        if flow_direction >= 0 or not getattr(self, "album_queue", None):
            self.album_queue = self._album_queue_from_track(getattr(track, "album_queue", ()))
        current_album_island = bool(self._should_paint_album_island())
        if current_album_island and (track_changed or current_album_island != previous_album_island):
            self.album_island_transition_started_at = now
            self.album_island_transition_reason = "track" if track_changed else "mode"
            self.album_island_transition_direction = flow_direction
        image = QImage()
        cover_loaded = bool(track.album_art_bytes) and image.loadFromData(track.album_art_bytes) and not image.isNull()
        black_placeholder_loaded = cover_loaded and self._looks_like_black_album_placeholder(image)
        has_existing_cover = not getattr(self, "album_art", QImage()).isNull()
        preserve_existing_cover = (
            cover_loaded
            and black_placeholder_loaded
            and has_existing_cover
            and (not track_changed or same_visible_track or message_refresh)
        )
        preserve_missing_cover = (
            not cover_loaded
            and has_existing_cover
            and message_refresh
            and (not track_changed or same_visible_track)
        )
        if preserve_existing_cover:
            cover_loaded = False
        if cover_loaded:
            self.album_art = image
            self._scaled_album_art_cache.clear()
        elif track_changed and not preserve_existing_cover and not preserve_missing_cover:
            self.album_art = QImage()
            self._scaled_album_art_cache.clear()
        if track_changed:
            self.lyric_index = 0 if self.timed_lyrics else min(1, len(self.lyrics) - 1)
            self.prev_lyric_index = self.lyric_index
            self.lyric_changed_at = now
            self.audio_started_at = now
        self._sync_timed_lyrics(now, allow_backward=timed_lyrics_appeared)

    def _looks_like_black_album_placeholder(self, image: QImage) -> bool:
        if image.isNull():
            return False
        sample = image.convertToFormat(QImage.Format.Format_ARGB32)
        if sample.width() > 12 or sample.height() > 12:
            sample = sample.scaled(12, 12, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
        total_luma = 0.0
        max_channel = 0
        min_luma = 255
        max_luma = 0
        opaque_pixels = 0
        pixel_count = max(1, sample.width() * sample.height())
        for y in range(sample.height()):
            for x in range(sample.width()):
                color = sample.pixelColor(x, y)
                if color.alpha() < 220:
                    continue
                opaque_pixels += 1
                luma = int(color.red() * 0.2126 + color.green() * 0.7152 + color.blue() * 0.0722)
                total_luma += luma
                min_luma = min(min_luma, luma)
                max_luma = max(max_luma, luma)
                max_channel = max(max_channel, color.red(), color.green(), color.blue())
        if opaque_pixels < pixel_count * 0.70:
            return False
        average_luma = total_luma / max(1, opaque_pixels)
        return average_luma <= 8.0 and max_channel <= 24 and (max_luma - min_luma) <= 10

    def _ingest_provider_messages(self, visual_data: IslandVisualData):
        latest_by_app: dict[str, tuple[str, object]] = {}
        app_order: list[str] = []
        for message in visual_data.messages:
            app_name = str(getattr(message, "app", "") or "").strip()
            if not app_name:
                continue
            app_key = app_name.casefold()
            if app_key in latest_by_app:
                try:
                    app_order.remove(app_key)
                except ValueError:
                    pass
            latest_by_app[app_key] = (app_name, message)
            app_order.append(app_key)

        for app_key in app_order:
            app_name, message = latest_by_app[app_key]
            key = f"{app_name}\0{message.sender}\0{message.text}"
            if key in self._seen_provider_message_keys:
                continue
            self._seen_provider_message_keys.add(key)
            avatar_image = QImage()
            avatar_bytes = getattr(message, "avatar_image_bytes", b"") or b""
            if avatar_bytes:
                avatar_image.loadFromData(avatar_bytes)
            msg = Message(
                app_name,
                message.sender,
                message.text,
                QColor(*message.name_color.rgba()),
                QColor(*message.avatar_color.rgba()),
                avatar_image=avatar_image,
                serial=len(self._seen_provider_message_keys),
            )
            self.messages = [
                existing for existing in self.messages
                if str(getattr(existing, "app", "") or "").strip().casefold() != app_key
            ]
            msg.barrage_started_at = float(getattr(self, "barrage_clock", 0.0)) + 0.05 * len(self.messages)
            self.messages.insert(0, msg)
        self.messages = self.messages[:8]

    def _coalesced_timed_lyrics(self, timed_lyrics: list[tuple[float, str]]) -> list[tuple[float, str]]:
        if not timed_lyrics:
            return []
        merged: list[tuple[float, str]] = []
        current_time: float | None = None
        current_parts: list[str] = []

        def flush():
            if current_time is None or not current_parts:
                return
            if len(current_parts) == 1:
                text = current_parts[0]
            else:
                text = f"{current_parts[0]}\n{' / '.join(current_parts[1:])}"
            merged.append((current_time, text))

        for raw_time, raw_text in timed_lyrics:
            parts = [" ".join(part.split()) for part in str(raw_text or "").splitlines()]
            parts = [part for part in parts if part]
            if not parts:
                continue
            text = "\n".join(parts[:2])
            line_time = max(0.0, float(raw_time))
            if current_time is None:
                current_time = line_time
                current_parts = [text]
                continue
            if abs(line_time - current_time) <= 0.08:
                if text not in current_parts:
                    current_parts.append(text)
                continue
            flush()
            current_time = line_time
            current_parts = [text]
        flush()
        return merged

    def _refresh_media_data(self, now: float):
        if now - getattr(self, "last_media_refresh_at", 0.0) < getattr(self, "media_refresh_interval", 0.65):
            return
        self.last_media_refresh_at = now
        try:
            visual_data = self.data_provider.get_visual_data()
        except Exception:
            return
        self._apply_visual_data(visual_data)

    def _estimated_media_position(self, now: float) -> float:
        base = max(0.0, float(getattr(self, "media_position_seconds", 0.0)))
        sync_at = float(getattr(self, "media_position_sync_at", now))
        duration = max(0.0, float(getattr(self, "media_duration_seconds", 0.0)))
        if self.media_is_playing:
            base += max(0.0, now - sync_at)
        if duration > 0.0:
            return min(duration, base)
        return base

    def _looks_like_seek_to_start(self, raw_position: float, previous_position: float, previous_raw_position: float | None) -> bool:
        if previous_raw_position is None:
            return False
        raw_position = max(0.0, float(raw_position))
        previous_position = max(0.0, float(previous_position))
        previous_raw = max(0.0, float(previous_raw_position))
        if raw_position > 0.12 or previous_position <= 3.0 or previous_raw <= 3.0:
            return False
        return abs(previous_raw - previous_position) <= max(5.0, previous_position * 0.12)

    def _sync_timed_lyrics(self, now: float, allow_backward: bool = False):
        if not self.timed_lyrics:
            return
        position = max(0.0, self._estimated_media_position(now) + float(getattr(self, "lyric_offset_seconds", 0.0)))
        index = active_index_for_position(self.timed_lyrics, position)
        previous_index = int(getattr(self, "lyric_index", 0))
        if index < previous_index and bool(getattr(self, "media_is_playing", False)) and not allow_backward:
            current_start = float(self.timed_lyrics[min(previous_index, len(self.timed_lyrics) - 1)][0])
            last_position = float(getattr(self, "lyric_last_sync_position", 0.0))
            small_backstep = position >= max(0.0, current_start - 1.05) or 0.0 < last_position - position < 2.4
            if small_backstep:
                index = previous_index
        self.lyric_last_sync_position = position
        if index != self.lyric_index and index < len(self.lyrics):
            distance = abs(index - self.lyric_index)
            self.prev_lyric_index = self.lyric_index
            self.lyric_index = index
            self.lyric_changed_at = now
            self.lyric_transition_distance = distance
            self.lyric_transition_kind = "seek" if distance > 1 else "line"
            self.lyric_last_phase_index = -1
            self.lyric_last_phase_progress = 0.0
            self.lyric_last_phase_at = now

    def _u(self, value: float) -> float:
        return value * self.VISUAL_SCALE

    def _font_size(self, size: float, shrink: bool = True) -> int:
        shrink_scale = getattr(self, "font_shrink_scale", 1.0) if shrink else 1.0
        return max(1, int(round(size * self.VISUAL_SCALE * shrink_scale)))

    def _font(self, size: float, weight: QFont.Weight = QFont.Weight.Normal, shrink: bool = True) -> QFont:
        font = QFont()
        font.setFamilies(self._font_families())
        font.setPointSize(self._font_size(size, shrink=shrink))
        font.setWeight(weight)
        if getattr(self, "high_quality_font_rendering", False):
            font.setStyleStrategy(QFont.StyleStrategy.PreferQuality)
        else:
            font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font

    def _font_families(self) -> list[str]:
        family = str(getattr(self, "ui_font_family", self.DEFAULT_FONT_FAMILY)).strip() or self.DEFAULT_FONT_FAMILY
        families = [family]
        for fallback in self.CHINESE_FONT_FALLBACKS:
            if fallback not in families:
                families.append(fallback)
        return families

    def _gelv_font_families(self) -> list[str]:
        selected = str(getattr(self, "gelv_font_family", self.GELV_FONT_FAMILY) or self.GELV_FONT_FAMILY).strip()
        if selected not in self.GELV_FONT_CHOICES:
            selected = self.GELV_FONT_FAMILY
        families = [selected]
        for family in self.GELV_FONT_CHOICES:
            if family not in families:
                families.append(family)
        return families

    def _gelv_font(self, size: float, weight: QFont.Weight = QFont.Weight.Normal, shrink: bool = True) -> QFont:
        font = QFont()
        font.setFamilies(self._gelv_font_families())
        font.setPointSize(self._font_size(size, shrink=shrink))
        font.setWeight(max(weight, QFont.Weight.Bold))
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font

    @classmethod
    def _source_han_serif_asset_paths(cls) -> list[Path]:
        roots = [ROOT_DIR / "assets" / "fonts" / "SourceHanSerifSC"]
        paths: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            paths.extend(sorted(root.rglob("SourceHanSerifSC-*.otf")))
            paths.extend(sorted(root.rglob("SourceHanSerifSC-*.otc")))
        return paths

    @classmethod
    def load_source_han_serif_fonts(cls):
        if cls._source_han_serif_loaded:
            return
        for path in cls._source_han_serif_asset_paths():
            try:
                QFontDatabase.addApplicationFont(str(path))
            except Exception:
                continue
        cls._source_han_serif_loaded = True

    def _clear_lyric_metrics_cache(self):
        cache = getattr(self, "_lyric_layout_cache", None)
        if cache is not None:
            cache.clear()
        width_cache = getattr(self, "_representative_lyric_width_cache", None)
        if width_cache is not None:
            width_cache.clear()

    @classmethod
    def best_available_font_family(cls, preferred: str | None = None) -> str:
        families = set(QFontDatabase.families())
        if preferred:
            selected = str(preferred).strip()
            if selected in families:
                return selected
        candidates = list(cls.CHINESE_FONT_FALLBACKS)
        for candidate in candidates:
            if candidate in families and cls._font_supports_simplified_chinese(candidate):
                return candidate
        for candidate in candidates:
            if candidate in families:
                return candidate
        return cls.DEFAULT_FONT_FAMILY

    @staticmethod
    def _font_supports_simplified_chinese(family: str) -> bool:
        try:
            return QFontDatabase.WritingSystem.SimplifiedChinese in QFontDatabase.writingSystems(family)
        except Exception:
            return True

    def _timer_interval_ms(self) -> int:
        fps = max(24, min(90, int(getattr(self, "animation_fps", 60))))
        return max(11, int(round(1000 / fps)))

    def set_animation_fps(self, fps: int, notify: bool = True):
        self.animation_fps = max(24, min(90, int(fps)))
        if hasattr(self, "timer"):
            self.timer.start(self._timer_interval_ms())
        if notify:
            self._notify_settings_changed()

    def set_animation_speed(self, speed: float, notify: bool = True):
        allowed = (0.5, 0.75, 1.0, 2.0)
        try:
            value = float(speed)
        except (TypeError, ValueError):
            value = 1.0
        self.animation_speed = min(allowed, key=lambda item: abs(item - value))
        if notify:
            self._notify_settings_changed()

    def set_music_ambience_style(self, style: str, notify: bool = True):
        style = str(style or "sea_level").lower()
        if style in ("album_mist", "xiaomi_duotone"):
            style = "sea_level"
        if style not in self.MUSIC_AMBIENCE_STYLES:
            style = "sea_level"
        if self._active_lyric_color_fill_enabled() and style not in self.COLOR_FILL_AMBIENCE_STYLES:
            style = "pure_black"
        self.music_ambience_style = style
        self.update()
        if notify:
            self._notify_settings_changed()

    def _coerce_music_ambience_for_color_fill(self):
        if str(getattr(self, "music_ambience_style", "sea_level")) not in self.COLOR_FILL_AMBIENCE_STYLES:
            self.music_ambience_style = "pure_black"

    def set_music_flow_intensity(self, value: float, notify: bool = True):
        try:
            amount = float(value)
        except (TypeError, ValueError):
            amount = 0.74
        self.music_flow_intensity = max(0.0, min(1.0, amount))
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_duotone_balance(self, compact: int | None = None, expanded: int | None = None, notify: bool = True):
        if compact is not None:
            self.compact_duotone_balance = max(0, min(100, int(compact)))
        if expanded is not None:
            self.expanded_duotone_balance = max(0, min(100, int(expanded)))
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_high_quality_font_rendering(self, enabled: bool, notify: bool = True):
        self.high_quality_font_rendering = bool(enabled)
        self._clear_lyric_metrics_cache()
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_ui_font(self, family: str, notify: bool = True):
        family = self.best_available_font_family(family or self.DEFAULT_FONT_FAMILY)
        self.ui_font_family = family
        self._clear_lyric_metrics_cache()
        self.setFont(self._font(10))
        self._pulse_stack()
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_empty_bar_palette(self, palette: str, notify: bool = True):
        self.empty_bar_palette = str(palette or "aurora")
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_empty_bar_geometry(
        self,
        width: int | None = None,
        height: int | None = None,
        y_offset: int | None = None,
        opacity: int | None = None,
        notify: bool = True,
    ):
        if width is not None:
            self.empty_bar_width = max(70, min(260, int(width)))
        if height is not None:
            self.empty_bar_height = max(8, min(28, int(height)))
        if y_offset is not None:
            self.empty_bar_y_offset = max(-80, min(160, int(y_offset)))
        if opacity is not None:
            self.empty_bar_opacity = max(10, min(100, int(opacity)))
        self._pulse_stack()
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_empty_bar_custom_colors(self, colors, notify: bool = True):
        normalized = []
        for value in list(colors or [])[:3]:
            text = str(value).strip()
            color = QColor(text)
            normalized.append(text.upper() if color.isValid() else "#46D6FF")
        while len(normalized) < 3:
            normalized.append(("#46D6FF", "#73FFA6", "#FF5A86")[len(normalized)])
        self.empty_bar_custom_colors = tuple(normalized[:3])
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_lyric_offset_seconds(self, seconds: float, notify: bool = True):
        self.lyric_offset_seconds = max(-12.0, min(12.0, float(seconds)))
        now = time.monotonic()
        self.lyric_changed_at = now
        self._sync_timed_lyrics(now)
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_lyric_font_size(self, size: int, notify: bool = True):
        self.lyric_font_size = max(14, min(24, int(size)))
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_lyric_elastic_strength(self, strength: int, notify: bool = True):
        self.lyric_elastic_strength = max(0, min(160, int(strength)))
        self._clear_lyric_metrics_cache()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_lyric_elastic_mode(self, mode: str, notify: bool = True):
        mode = str(mode or "both").lower()
        if mode not in ("both", "left"):
            mode = "both"
        self.lyric_elastic_mode = mode
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_lyric_word_spacing(self, spacing: int, notify: bool = True):
        self.lyric_word_spacing = max(60, min(150, int(spacing)))
        self._clear_lyric_metrics_cache()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_enable_dual_line_lyrics(self, enabled: bool, notify: bool = True):
        self.enable_dual_line_lyrics = bool(enabled)
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_dual_line_secondary_weight(self, weight: int, notify: bool = True):
        self.dual_line_secondary_weight = max(40, min(100, int(weight)))
        self._clear_lyric_metrics_cache()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_dual_line_secondary_spacing(self, spacing: int, notify: bool = True):
        self.dual_line_secondary_spacing = max(60, min(150, int(spacing)))
        self._clear_lyric_metrics_cache()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_compact_lyric_show_cover(self, enabled: bool, notify: bool = True):
        self.compact_lyric_show_cover = bool(enabled)
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_lyric_flow_style(self, style: str, notify: bool = True):
        style = str(style or "wave_stream").strip()
        if style not in self.LYRIC_FLOW_STYLES:
            style = "wave_stream"
        self.lyric_flow_style = style
        if self._active_lyric_color_fill_enabled():
            self._coerce_music_ambience_for_color_fill()
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_font_family(self, family: str, notify: bool = True):
        family = str(family or self.GELV_FONT_FAMILY).strip()
        if family not in self.GELV_FONT_CHOICES:
            family = self.GELV_FONT_FAMILY
        self.gelv_font_family = family
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_primary_font_size(self, size: int, notify: bool = True):
        self.gelv_primary_font_size = max(14, min(30, int(size)))
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_secondary_font_size(self, size: int, notify: bool = True):
        self.gelv_secondary_font_size = max(8, min(24, int(size)))
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_dual_line_font_size(self, size: int, notify: bool = True):
        self.gelv_dual_line_font_size = max(8, min(30, int(size)))
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_initial_word_spacing(self, spacing: int, notify: bool = True):
        self.gelv_initial_word_spacing = max(100, min(320, int(spacing)))
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_word_spacing(self, spacing: int, notify: bool = True):
        self.gelv_word_spacing = max(100, min(420, int(spacing)))
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_size_shift_enabled(self, enabled: bool, notify: bool = True):
        self.gelv_size_shift_enabled = bool(enabled)
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_color_fill_enabled(self, enabled: bool, notify: bool = True):
        self.gelv_color_fill_enabled = bool(enabled)
        if self._gelv_enabled() and self.gelv_color_fill_enabled:
            self._coerce_music_ambience_for_color_fill()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_color_fill_fallback(self, color: str, notify: bool = True):
        qcolor = QColor(str(color or "#00D7FF"))
        self.gelv_color_fill_fallback = qcolor.name().upper() if qcolor.isValid() else "#00D7FF"
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_wave_color_fill_enabled(self, enabled: bool, notify: bool = True):
        self.wave_color_fill_enabled = bool(enabled)
        if not self._gelv_enabled() and self.wave_color_fill_enabled:
            self._coerce_music_ambience_for_color_fill()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_wave_color_fill_fallback(self, color: str, notify: bool = True):
        qcolor = QColor(str(color or "#00D7FF"))
        self.wave_color_fill_fallback = qcolor.name().upper() if qcolor.isValid() else "#00D7FF"
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_no_lyrics_display_mode(self, mode: str, notify: bool = True):
        mode = str(mode or "text").strip()
        self.no_lyrics_display_mode = mode if mode in {"text", "album_island"} else "text"
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_album_island_cover_count(self, count: int, notify: bool = True):
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 5
        if count not in (3, 5, 7):
            count = 5
        self.album_island_cover_count = count
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_album_island_cover_size(self, size: int, notify: bool = True):
        self.album_island_cover_size = max(18, min(44, int(size)))
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_album_island_spacing(self, spacing: int, notify: bool = True):
        self.album_island_spacing = max(48, min(170, int(spacing)))
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_album_island_secondary_style(self, style: str, notify: bool = True):
        style = str(style or "perspective_dim").strip()
        if style not in self.ALBUM_ISLAND_SECONDARY_STYLES:
            style = "perspective_dim"
        self.album_island_secondary_style = style
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_album_island_perspective_amount(self, amount: int, notify: bool = True):
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            amount = 100
        self.album_island_perspective_amount = max(0, min(200, amount))
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_no_lyrics_text(self, text: str, notify: bool = True):
        old_text = getattr(self, "no_lyrics_text", "暂无歌词")
        text = " ".join(str(text or "暂无歌词").split())[:16] or "暂无歌词"
        self.no_lyrics_text = text
        if self.lyrics == ["暂无歌词"] or self.lyrics == [old_text]:
            self.lyrics = [text]
            self.lyric_index = 0
            self.prev_lyric_index = 0
            self.lyric_changed_at = time.monotonic()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_barrage_settings(
        self,
        *,
        y_offset: int | None = None,
        font_size: int | None = None,
        speed: int | None = None,
        use_backdrop: bool | None = None,
        show_icon: bool | None = None,
        notify: bool = True,
    ):
        if y_offset is not None:
            self.barrage_y_offset = max(0, min(240, int(y_offset)))
        if font_size is not None:
            self.barrage_font_size = max(8, min(20, int(font_size)))
        if speed is not None:
            self.barrage_speed = max(40, min(220, int(speed)))
        if use_backdrop is not None:
            self.barrage_use_backdrop = bool(use_backdrop)
        if show_icon is not None:
            self.barrage_show_icon = bool(show_icon)
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_auto_expand_bounds(self, enabled: bool, notify: bool = True):
        self.auto_expand_bounds = bool(enabled)
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_expand_direction(self, direction: str, notify: bool = True):
        direction = str(direction or "auto").lower()
        if direction not in ("auto", "up", "down"):
            direction = "auto"
        self.expand_direction = direction
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_settings_window_active(self, active: bool):
        self.settings_window_active = bool(active)
        self.scene_checked_at = 0.0
        self._update_scene_state(time.monotonic())
        self.update()

    def _center_on_screen(self):
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        geo = screen.geometry()
        if getattr(self, "attach_to_real_finder", False):
            self.resize(geo.width(), self.height())
            self.move(geo.left(), geo.top())
            self._force_super_topmost()
        else:
            standalone_width = int(round(self._standalone_canvas_width()))
            if self.width() != standalone_width:
                self.resize(standalone_width, self.height())
            if self.saved_window_pos is not None:
                x, y = self.saved_window_pos
                self.move(int(x), int(y))
            else:
                self.move(geo.center().x() - self.width() // 2, geo.top() + int(round(self._u(14))))

    def _standalone_canvas_width(self) -> float:
        return self._u(1180.0)

    def set_saved_placement(self, center_ratio=None, window_pos=None, notify: bool = True, stack_direction=None):
        try:
            ratio = float(center_ratio)
        except (TypeError, ValueError):
            ratio = 0.5
        self.stack_center_ratio = max(0.06, min(0.94, ratio))
        try:
            if stack_direction is not None:
                self.stack_direction = -1 if int(stack_direction) < 0 else 1
        except (TypeError, ValueError):
            self.stack_direction = 1

        parsed_pos = None
        if isinstance(window_pos, (list, tuple)) and len(window_pos) >= 2:
            try:
                parsed_pos = (int(window_pos[0]), int(window_pos[1]))
            except (TypeError, ValueError):
                parsed_pos = None
        self.saved_window_pos = parsed_pos
        self._center_on_screen()
        if notify:
            self._notify_settings_changed()

    def set_super_topmost(self, enabled: bool, notify: bool = True):
        self.super_topmost_enabled = bool(enabled)
        if self.super_topmost_enabled:
            self._force_super_topmost()
        else:
            self._release_super_topmost()
        if notify:
            self._notify_settings_changed()
        self.update()

    def _force_super_topmost(self):
        if sys.platform != "win32" or not getattr(self, "super_topmost_enabled", True):
            return
        try:
            hwnd = int(self.winId())
            if hwnd <= 0:
                return
            user32 = ctypes.windll.user32
            hwnd_topmost = ctypes.c_void_p(-1)
            flags = 0x0001 | 0x0002 | 0x0010 | 0x0040  # NOSIZE, NOMOVE, NOACTIVATE, SHOWWINDOW
            user32.SetWindowPos(ctypes.c_void_p(hwnd), hwnd_topmost, 0, 0, 0, 0, flags)
            self.topmost_reinforced_at = time.monotonic()
        except Exception:
            pass

    def _release_super_topmost(self):
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            if hwnd <= 0:
                return
            user32 = ctypes.windll.user32
            hwnd_notopmost = ctypes.c_void_p(-2)
            flags = 0x0001 | 0x0002 | 0x0010 | 0x0040  # NOSIZE, NOMOVE, NOACTIVATE, SHOWWINDOW
            user32.SetWindowPos(ctypes.c_void_p(hwnd), hwnd_notopmost, 0, 0, 0, 0, flags)
            self.topmost_reinforced_at = 0.0
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self._center_on_screen()
        self._force_super_topmost()

    def _tick(self):
        now = time.monotonic()
        tick_dt = max(0.001, min(0.2, now - self.last_tick_at))
        self.last_tick_at = now
        speed = max(0.1, min(4.0, float(getattr(self, "animation_speed", 1.0))))
        anim_dt = tick_dt * speed
        self._refresh_media_data(now)
        self._update_scene_state(now)
        if self.scene_suppressed:
            self.barrage_hovered = False
        else:
            self.barrage_hovered = self._point_in_barrage(self.pointer_pos)
        if not self.barrage_hovered:
            self.barrage_clock += anim_dt
        if getattr(self, "super_topmost_enabled", True) and now - self.topmost_reinforced_at > 1.6:
            self._force_super_topmost()

        if self.media_is_playing:
            if self.timed_lyrics:
                self._sync_timed_lyrics(now)
            elif now - self.lyric_changed_at > 2.45 / speed and len(self.lyrics) > 1:
                self.prev_lyric_index = self.lyric_index
                self.lyric_index = (self.lyric_index + 1) % len(self.lyrics)
                self.lyric_changed_at = now

        if self.scene_suppressed:
            self.hovered_key = ""
            self.hover_armed_key = ""
            self.left_stack_at = 0.0
            self.update()
            return

        if self.hovered_key and not self.expanded and self.hover_armed_key == self.hovered_key and now - self.hover_started_at >= 1.0:
            self._activate_key(self.hovered_key)
            self.hover_armed_key = ""
        if (
            self.expanded
            and self.hovered_key == "plugin"
            and not getattr(self, "plugin_panel_open", False)
            and now - self.hover_started_at >= 0.5
        ):
            self.plugin_panel_open = True
            self._pulse_stack()
        if self.expanded and self.left_stack_at and now - self.left_stack_at >= 0.5:
            self._set_expanded(False)
            self.left_stack_at = 0.0
            self._pulse_stack()
        if self._maybe_trigger_alarm_row_long_press(now):
            self.update()
            return

        self.scroll_phase += 0.017 * speed
        self.ambient_phase += 0.024 * speed
        self._update_wave_values()
        self.update()

    def _update_wave_values(self):
        low_band, mid_band, high_band = self._sample_audio_bands(time.monotonic())
        has_live_audio = False
        try:
            has_live_audio = bool(getattr(self.data_provider, "has_live_audio", lambda: False)())
        except Exception:
            has_live_audio = False
        self.live_audio_available = has_live_audio
        if not self.media_is_playing and not has_live_audio:
            low_band *= 0.24
            mid_band *= 0.18
            high_band *= 0.12
        self.current_audio_bands = (low_band, mid_band, high_band)

        for i, value in enumerate(self.wave_values):
            pos = i / max(1, len(self.wave_values) - 1)
            low_shape = max(0.0, 1.0 - abs(pos - 0.14) / 0.28)
            mid_shape = max(0.0, 1.0 - abs(pos - 0.50) / 0.24)
            high_shape = max(0.0, 1.0 - abs(pos - 0.84) / 0.18)
            drift = math.sin(self.ambient_phase * 0.9 + pos * math.pi * 2.1) * 0.5 + 0.5
            target = (
                0.16
                + 0.78 * low_band * low_shape
                + 0.68 * mid_band * mid_shape
                + 0.56 * high_band * high_shape
                + 0.06 * drift
            )
            if random.random() < 0.012:
                target = min(1.0, target + random.uniform(0.08, 0.18) * high_band)
            target = max(0.04, min(1.0, target))
            self.wave_targets[i] = target
            self.wave_values[i] = value + (target - value) * 0.38

    def _sample_audio_bands(self, now: float) -> tuple[float, float, float]:
        live_frame = None
        try:
            live_frame = self.data_provider.get_audio_frame()
        except Exception:
            live_frame = None
        if live_frame is not None:
            return (
                max(0.0, min(1.0, live_frame.low)),
                max(0.0, min(1.0, live_frame.mid)),
                max(0.0, min(1.0, live_frame.high)),
            )
        if not self.audio_frames:
            has_live_audio = False
            try:
                has_live_audio = bool(getattr(self.data_provider, "has_live_audio", lambda: False)())
            except Exception:
                has_live_audio = False
            if not has_live_audio:
                return (0.10, 0.08, 0.05) if self.media_is_playing else (0.02, 0.015, 0.01)
            phase = self.ambient_phase
            return (
                math.sin(phase * 0.8) * 0.24 + 0.48,
                math.sin(phase * 1.3 + 1.6) * 0.22 + 0.50,
                math.sin(phase * 2.2 + 0.8) * 0.18 + 0.42,
            )

        pos = ((now - self.audio_started_at) * 3.35) % len(self.audio_frames)
        index = int(pos)
        next_index = (index + 1) % len(self.audio_frames)
        mix = pos - index
        mix = mix * mix * (3.0 - 2.0 * mix)
        current = self.audio_frames[index]
        upcoming = self.audio_frames[next_index]

        def blend(a: float, b: float) -> float:
            return max(0.0, min(1.0, a + (b - a) * mix))

        return (
            blend(current.low, upcoming.low),
            blend(current.mid, upcoming.mid),
            blend(current.high, upcoming.high),
        )

    def _update_scene_state(self, now: float):
        if now - self.scene_checked_at < 0.4:
            return
        self.scene_checked_at = now
        suppressed, reason = self._detect_scene_suppression()
        new_state = bool(suppressed)
        if new_state == self.scene_suppressed:
            if not new_state and self.scene_transition_mode == "hide":
                self.scene_transition_mode = ""
            return
        self.scene_suppressed = new_state
        self.scene_reason = reason if new_state else ""
        self._set_input_passthrough(new_state)
        self.scene_transition_mode = "hide" if new_state else "show"
        self.scene_transition_started_at = now
        self.scene_transition_duration = 0.18 if new_state else 0.14
        self.hovered_key = ""
        self.hover_armed_key = ""
        self.left_stack_at = 0.0
        if self.scene_suppressed:
            self._set_expanded(False)
        self.group_frame_mask_path = None
        self.clearMask()
        self.update()

    def _set_input_passthrough(self, enabled: bool):
        enabled = bool(enabled)
        if getattr(self, "input_passthrough_enabled", False) == enabled:
            return
        self.input_passthrough_enabled = enabled
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            if hwnd <= 0:
                return
            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_NOACTIVATE = 0x08000000
            style = user32.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
            if enabled:
                style |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
            else:
                style &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
            user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE, style)
        except Exception:
            pass

    def _scene_transition_params(self, now: float) -> tuple[float, float, bool, float]:
        mode = getattr(self, "scene_transition_mode", "")
        if mode not in ("hide", "show"):
            return 1.0, 1.0, False, 0.0
        elapsed = max(0.0, now - self.scene_transition_started_at)
        duration = max(0.05, self.scene_transition_duration)
        if elapsed >= duration:
            if mode == "hide":
                return 0.0, 0.0, True, 1.0
            self.scene_transition_mode = ""
            return 1.0, 1.0, False, 0.0
        t = max(0.0, min(1.0, elapsed / duration))
        eased = 1.0 - pow(1.0 - t, 3.0)
        if mode == "hide":
            return 1.0 - 0.72 * eased, 1.0 - 0.96 * eased, False, eased
        return 0.28 + 0.72 * eased, 0.24 + 0.76 * eased, False, 1.0 - eased

    def _scene_transition_anchor(self, visuals: list[StackVisual]) -> QPointF:
        main_rect = next((visual.rect for visual in visuals if visual.z_index == 10), None)
        if main_rect:
            return main_rect.center()
        return QPointF(self.width() / 2.0, self.height() / 3.0)

    def _scene_transition_offset(self, visuals: list[StackVisual], progress: float) -> QPointF:
        progress = max(0.0, min(1.0, progress))
        bounds = self._visuals_bounds(visuals)
        if progress <= 0.001 or bounds is None:
            return QPointF(0.0, 0.0)
        edge, screen_geo, global_rect = self._nearest_scene_edge(bounds)
        margin = self._u(28.0)
        if edge == "left":
            dx = screen_geo.left() - global_rect.right() - margin if screen_geo else -bounds.right() - margin
            return QPointF(dx * progress, 0.0)
        if edge == "right":
            dx = screen_geo.right() - global_rect.left() + 1.0 + margin if screen_geo else self.width() - bounds.left() + margin
            return QPointF(dx * progress, 0.0)
        if edge == "bottom":
            dy = screen_geo.bottom() - global_rect.top() + 1.0 + margin if screen_geo else self.height() - bounds.top() + margin
            return QPointF(0.0, dy * progress)
        dy = screen_geo.top() - global_rect.bottom() - margin if screen_geo else -bounds.bottom() - margin
        return QPointF(0.0, dy * progress)

    def _visuals_bounds(self, visuals: list[StackVisual]) -> QRectF | None:
        rects = [visual.rect for visual in visuals if visual.rect.width() > 1 and visual.rect.height() > 1]
        if not rects:
            return None
        left = min(rect.left() for rect in rects)
        top = min(rect.top() for rect in rects)
        right = max(rect.right() for rect in rects)
        bottom = max(rect.bottom() for rect in rects)
        return QRectF(left, top, right - left, bottom - top)

    def _nearest_scene_edge(self, bounds: QRectF):
        top_left = self.mapToGlobal(QPoint(int(round(bounds.left())), int(round(bounds.top()))))
        bottom_right = self.mapToGlobal(QPoint(int(round(bounds.right())), int(round(bounds.bottom()))))
        global_rect = QRectF(
            float(top_left.x()),
            float(top_left.y()),
            float(bottom_right.x() - top_left.x()),
            float(bottom_right.y() - top_left.y()),
        )
        screen = QGuiApplication.screenAt(QPoint(int(round(global_rect.center().x())), int(round(global_rect.center().y())))) or QGuiApplication.primaryScreen()
        screen_geo = screen.geometry() if screen else None
        if self.finder_mode:
            return "top", screen_geo, global_rect
        if not screen_geo:
            distances = {
                "left": max(0.0, bounds.left()),
                "right": max(0.0, self.width() - bounds.right()),
                "top": max(0.0, bounds.top()),
                "bottom": max(0.0, self.height() - bounds.bottom()),
            }
            return min(distances, key=distances.get), screen_geo, global_rect
        distances = {
            "left": max(0.0, global_rect.left() - screen_geo.left()),
            "right": max(0.0, screen_geo.right() - global_rect.right()),
            "top": max(0.0, global_rect.top() - screen_geo.top()),
            "bottom": max(0.0, screen_geo.bottom() - global_rect.bottom()),
        }
        return min(distances, key=distances.get), screen_geo, global_rect

    def _detect_scene_suppression(self) -> tuple[bool, str]:
        if getattr(self, "settings_window_active", False):
            return True, "settings"
        if sys.platform != "win32":
            return False, ""
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False, ""
            self_hwnd = int(self.winId()) if self.winId() else 0
            if self_hwnd and hwnd == self_hwnd:
                return False, ""

            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return False, ""

            width = max(0, rect.right - rect.left)
            height = max(0, rect.bottom - rect.top)
            if width <= 0 or height <= 0:
                return False, ""

            title = self._window_title(hwnd, user32)
            class_name = self._window_class(hwnd, user32)
            process_name = self._window_process_name(hwnd, user32)
            if self._is_shell_desktop_window(title, class_name, process_name):
                return False, ""

            screen = QGuiApplication.screenAt(QPoint(rect.left + width // 2, rect.top + height // 2)) or QGuiApplication.primaryScreen()
            if screen:
                geo = screen.geometry()
                if width >= geo.width() - 4 and height >= geo.height() - 4 and abs(rect.left - geo.left()) <= 4 and abs(rect.top - geo.top()) <= 4:
                    return True, "fullscreen"
                available = screen.availableGeometry()
                if user32.IsZoomed(hwnd) or (
                    width >= available.width() - 10
                    and height >= available.height() - 10
                    and abs(rect.left - available.left()) <= 8
                    and abs(rect.top - available.top()) <= 8
                ):
                    return True, "maximized"

            title = self._window_title(hwnd, user32)
            class_name = self._window_class(hwnd, user32)
            process_name = self._window_process_name(hwnd, user32)
            lowered = " ".join(part for part in (title, class_name, process_name) if part).lower()
            if any(token in lowered for token in ("video", "player", "播放器", "bilibili", "youtube", "netflix", "vlc", "mpv", "potplayer")):
                if width >= 0.45 * (screen.geometry().width() if screen else width):
                    return True, "video"
        except Exception:
            return False, ""
        return False, ""

    def _is_shell_desktop_window(self, title: str, class_name: str, process_name: str) -> bool:
        cls = (class_name or "").lower()
        proc = Path(process_name or "").name.lower()
        if cls in {"progman", "workerw", "shell_traywnd", "shell_secondarytraywnd"}:
            return True
        if proc == "explorer.exe" and cls in {"progman", "workerw", "shelldll_defview"}:
            return True
        if proc == "explorer.exe" and not (title or "").strip() and cls in {"workerw", "progman"}:
            return True
        return False

    def _window_title(self, hwnd, user32) -> str:
        try:
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return ""
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            return buffer.value
        except Exception:
            return ""

    def _window_class(self, hwnd, user32) -> str:
        try:
            buffer = ctypes.create_unicode_buffer(256)
            if user32.GetClassNameW(hwnd, buffer, len(buffer)):
                return buffer.value
        except Exception:
            return ""
        return ""

    def _window_process_name(self, hwnd, user32) -> str:
        if sys.platform != "win32":
            return ""
        try:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process_id = int(pid.value)
            if not process_id:
                return ""
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
            if not handle:
                return ""
            try:
                buffer = ctypes.create_unicode_buffer(260)
                size = wintypes.DWORD(len(buffer))
                if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    return Path(buffer.value).name
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return ""
        return ""

    def _point_in_barrage(self, point: QPointF) -> bool:
        for rect in self.barrage_rects.values():
            if rect.contains(point):
                return True
        return False

    def add_message(self):
        if not self.message_samples:
            return
        app, sender, text, name_color, avatar_color = self.message_samples[self.sample_index % len(self.message_samples)]
        self.sample_index += 1
        message = Message(app, sender, text, name_color, avatar_color, serial=self.sample_index)
        message.barrage_started_at = self.barrage_clock
        self.messages.insert(0, message)
        self._enforce_stack_limit()
        self._pulse_stack()
        self.update()

    def show_barrage_preview(self):
        now = time.monotonic()
        if now - getattr(self, "last_barrage_preview_at", 0.0) < 0.18:
            return
        self.last_barrage_preview_at = now
        self.sample_index += 1
        message = Message(
            "预览",
            "弹幕",
            f"位置 {int(getattr(self, 'barrage_y_offset', 0))}px",
            QColor(96, 186, 255),
            QColor(52, 118, 240),
            serial=self.sample_index,
        )
        message.popup_until = now + 1.0
        message.barrage_started_at = self.barrage_clock
        self.barrage_preview_messages.insert(0, message)
        self.barrage_preview_messages = self.barrage_preview_messages[:3]
        if getattr(self, "settings_window_active", False):
            self.scene_checked_at = 0.0
            self._update_scene_state(now)
        self.update()

    def _enforce_stack_limit(self):
        self.messages = self.messages[:6]

    def _pulse_stack(self):
        self.stack_bounce_until = time.monotonic() + 0.46

    def _set_expanded(self, expanded: bool):
        expanded = bool(expanded)
        if self.expanded == expanded:
            if not expanded:
                self.plugin_panel_open = False
            return
        self.expanded = expanded
        if not expanded:
            self.plugin_panel_open = False
        if self.finder_mode:
            self.clearMask()
            self.interaction_mask_active = False
            self.last_mask_rect = QRectF()
            self.mask_deferred_until = time.monotonic() + (0.34 if expanded else 0.18)

    def keyPressEvent(self, event):
        key = event.key()
        settings_changed = False
        if self._handle_alarm_editor_key_event(event):
            self.update()
            return
        if key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_M:
            self.music_playing = not self.music_playing
            self._enforce_stack_limit()
            self._pulse_stack()
            settings_changed = True
        elif key == Qt.Key.Key_P:
            self.persistent_plugin = not self.persistent_plugin
            self._enforce_stack_limit()
            self._pulse_stack()
            settings_changed = True
        elif key == Qt.Key.Key_N:
            self.add_message()
        elif key == Qt.Key.Key_C:
            self.messages.clear()
            self._pulse_stack()
        elif key == Qt.Key.Key_F:
            self.set_finder_mode(not self.finder_mode, notify=False)
            settings_changed = True
        elif key == Qt.Key.Key_Space:
            self._set_expanded(not self.expanded)
            self._pulse_stack()
            settings_changed = True
        elif key in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3):
            self._set_plugin_focus(key - Qt.Key.Key_1)
            self._set_expanded(True)
            self._pulse_stack()
            settings_changed = True
        elif key == Qt.Key.Key_0:
            self.enabled_plugin_count = (self.enabled_plugin_count + 1) % 4
            self._set_plugin_focus(min(self.plugin_focus_target, max(0, self.enabled_plugin_count - 1)), immediate=True)
            self._pulse_stack()
            settings_changed = True
        elif key == Qt.Key.Key_T:
            self.show_seconds = not self.show_seconds
            self._pulse_stack()
            settings_changed = True
        if settings_changed:
            self._notify_settings_changed()
        self.update()

    def inputMethodEvent(self, event):
        try:
            commit = event.commitString()
        except Exception:
            commit = ""
        if commit and self._handle_alarm_editor_key(commit):
            try:
                event.accept()
            except Exception:
                pass
            return
        super().inputMethodEvent(event)

    def settings_snapshot(self) -> dict[str, object]:
        return {
            "v2_music_playing": self.music_playing,
            "v2_music_auto_detect": bool(getattr(self, "music_auto_detect", False)),
            "v2_persistent_plugin": self.persistent_plugin,
            "v2_finder_mode": self.finder_mode,
            "v2_manual_mydockfinder": self.manual_finder_available,
            "v2_expanded": self.expanded,
            "v2_show_seconds": self.show_seconds,
            "v2_plugin_count": self.enabled_plugin_count,
            "v2_side_island_style": str(getattr(self, "side_island_style", "standard")),
            "v2_plugin_island_style": str(getattr(self, "plugin_island_style", "standard")),
            "v2_music_island_style": str(getattr(self, "music_island_style", "standard")),
            "v2_time_alarm_side_display": str(getattr(self, "time_alarm_side_display", "note")),
            "v2_ambient_intensity": self.ambient_intensity,
            "v2_music_ambience_style": getattr(self, "music_ambience_style", "sea_level"),
            "v2_music_flow_intensity": round(float(getattr(self, "music_flow_intensity", 0.74)), 3),
            "v2_compact_duotone_balance": int(getattr(self, "compact_duotone_balance", 50)),
            "v2_expanded_duotone_balance": int(getattr(self, "expanded_duotone_balance", 50)),
            "v2_font_family": self.ui_font_family,
            "v2_high_quality_font_rendering": bool(getattr(self, "high_quality_font_rendering", False)),
            "v2_animation_fps": self.animation_fps,
            "v2_animation_speed": self.animation_speed,
            "v2_empty_bar_palette": self.empty_bar_palette,
            "v2_empty_bar_width": int(getattr(self, "empty_bar_width", 92)),
            "v2_empty_bar_height": int(getattr(self, "empty_bar_height", 14)),
            "v2_empty_bar_y_offset": int(getattr(self, "empty_bar_y_offset", 0)),
            "v2_empty_bar_opacity": int(getattr(self, "empty_bar_opacity", 100)),
            "v2_empty_bar_custom_colors": list(getattr(self, "empty_bar_custom_colors", ("#46D6FF", "#73FFA6", "#FF5A86"))),
            "v2_stack_center_ratio": round(float(self.stack_center_ratio), 5),
            "v2_window_pos": list(self.saved_window_pos) if self.saved_window_pos is not None else None,
            "v2_stack_direction": int(getattr(self, "stack_direction", 1)),
            "v2_barrage_y_offset": int(self.barrage_y_offset),
            "v2_barrage_font_size": int(self.barrage_font_size),
            "v2_barrage_speed": int(getattr(self, "barrage_speed", 100)),
            "v2_barrage_use_backdrop": self.barrage_use_backdrop,
            "v2_barrage_show_icon": self.barrage_show_icon,
            "v2_auto_expand_bounds": self.auto_expand_bounds,
            "v2_expand_direction": self.expand_direction,
            "v2_lyric_offset_seconds": round(float(getattr(self, "lyric_offset_seconds", 0.0)), 3),
            "v2_lyric_font_size": int(getattr(self, "lyric_font_size", 18)),
            "v2_lyric_elastic_strength": int(getattr(self, "lyric_elastic_strength", 100)),
            "v2_lyric_elastic_mode": str(getattr(self, "lyric_elastic_mode", "both")),
            "v2_lyric_word_spacing": int(getattr(self, "lyric_word_spacing", 100)),
            "v2_enable_dual_line_lyrics": bool(getattr(self, "enable_dual_line_lyrics", False)),
            "v2_dual_line_secondary_weight": int(getattr(self, "dual_line_secondary_weight", 72)),
            "v2_dual_line_secondary_spacing": int(getattr(self, "dual_line_secondary_spacing", 100)),
            "v2_compact_lyric_show_cover": bool(getattr(self, "compact_lyric_show_cover", True)),
            "v2_wave_color_fill_enabled": bool(getattr(self, "wave_color_fill_enabled", False)),
            "v2_wave_color_fill_fallback": str(getattr(self, "wave_color_fill_fallback", "#00D7FF")),
            "v2_lyric_flow_style": str(getattr(self, "lyric_flow_style", "wave_stream")),
            "v2_gelv_font_family": str(getattr(self, "gelv_font_family", self.GELV_FONT_FAMILY)),
            "v2_gelv_primary_font_size": int(getattr(self, "gelv_primary_font_size", 22)),
            "v2_gelv_secondary_font_size": int(getattr(self, "gelv_secondary_font_size", 12)),
            "v2_gelv_dual_line_font_size": int(getattr(self, "gelv_dual_line_font_size", 17)),
            "v2_gelv_initial_word_spacing": int(getattr(self, "gelv_initial_word_spacing", 220)),
            "v2_gelv_word_spacing": int(getattr(self, "gelv_word_spacing", 320)),
            "v2_gelv_size_shift_enabled": bool(getattr(self, "gelv_size_shift_enabled", True)),
            "v2_gelv_color_fill_enabled": bool(getattr(self, "gelv_color_fill_enabled", False)),
            "v2_gelv_color_fill_fallback": str(getattr(self, "gelv_color_fill_fallback", "#00D7FF")),
            "v2_no_lyrics_display_mode": str(getattr(self, "no_lyrics_display_mode", "text")),
            "v2_album_island_cover_count": int(getattr(self, "album_island_cover_count", 5)),
            "v2_album_island_cover_size": int(getattr(self, "album_island_cover_size", 30)),
            "v2_album_island_spacing": int(getattr(self, "album_island_spacing", 112)),
            "v2_album_island_secondary_style": str(getattr(self, "album_island_secondary_style", "perspective_dim")),
            "v2_album_island_perspective_amount": int(getattr(self, "album_island_perspective_amount", 100)),
            "v2_no_lyrics_text": getattr(self, "no_lyrics_text", "暂无歌词"),
            "v2_super_topmost": bool(getattr(self, "super_topmost_enabled", True)),
        }

    def _begin_surface_transition(self, reason: str = "layout"):
        if not self.isVisible() or self.width() <= 2 or self.height() <= 2:
            return
        try:
            pixmap = self.grab()
        except Exception:
            return
        if pixmap.isNull():
            return
        top_left = self.mapToGlobal(QPoint(0, 0))
        self.surface_transition_pixmap = pixmap
        self.surface_transition_global_rect = QRectF(
            float(top_left.x()),
            float(top_left.y()),
            float(self.width()),
            float(self.height()),
        )
        self.surface_transition_started_at = time.monotonic()
        self.surface_transition_duration = 0.24 if reason in ("finder", "standalone") else 0.20
        self.surface_transition_reason = reason

    def _paint_surface_transition(self, painter: QPainter, now: float):
        pixmap = getattr(self, "surface_transition_pixmap", QPixmap())
        if pixmap.isNull():
            return
        duration = max(0.05, float(getattr(self, "surface_transition_duration", 0.22)))
        elapsed = now - float(getattr(self, "surface_transition_started_at", now))
        if elapsed >= duration:
            self.surface_transition_pixmap = QPixmap()
            self.surface_transition_global_rect = QRectF()
            self.surface_transition_reason = ""
            return
        progress = max(0.0, min(1.0, elapsed / duration))
        eased = progress * progress * (3.0 - 2.0 * progress)
        current_top_left = self.mapToGlobal(QPoint(0, 0))
        old_global = getattr(self, "surface_transition_global_rect", QRectF())
        if old_global.isNull():
            target = QRectF(0.0, 0.0, float(pixmap.width()), float(pixmap.height()))
        else:
            target = QRectF(
                old_global.left() - float(current_top_left.x()),
                old_global.top() - float(current_top_left.y()),
                old_global.width(),
                old_global.height(),
            )
        reason = str(getattr(self, "surface_transition_reason", "") or "")
        drift_y = self._u(-8.0 if reason == "finder" else 8.0) * eased
        target.translate(0.0, drift_y)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setOpacity(max(0.0, min(0.92, 0.92 * (1.0 - eased))))
        painter.drawPixmap(target, pixmap, QRectF(0.0, 0.0, float(pixmap.width()), float(pixmap.height())))
        painter.restore()

    def set_manual_finder_available(self, enabled: bool, notify: bool = True):
        self.manual_finder_available = bool(enabled)
        if notify:
            self._notify_settings_changed()

    def set_finder_mode(self, enabled: bool, notify: bool = True):
        enabled = bool(enabled)
        if enabled != bool(getattr(self, "finder_mode", False)):
            self._begin_surface_transition("finder" if enabled else "standalone")
        self.finder_mode = enabled
        self.attach_to_real_finder = self.finder_mode
        if self.finder_mode:
            self.finder_material = read_finder_material(force_screen_sample=True, include_center=not self.isVisible())
            self.finder_material_refreshed_at = time.monotonic()
        self._center_on_screen()
        if self.finder_mode:
            self._force_super_topmost()
        else:
            self.scene_suppressed = False
            self.scene_reason = ""
            self.scene_transition_mode = ""
            self.group_frame_mask_path = None
            self.clearMask()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def _notify_settings_changed(self):
        if self.settings_changed_callback:
            self.settings_changed_callback(self.settings_snapshot())

    def provider_diagnostics(self) -> dict[str, object]:
        diagnostics = {}
        try:
            provider_diag = getattr(self.data_provider, "diagnostics", None)
            if callable(provider_diag):
                diagnostics.update(provider_diag())
        except Exception as exc:
            diagnostics["provider_error"] = str(exc)
        now = time.monotonic()
        estimated_position = self._estimated_media_position(now) if hasattr(self, "media_position_sync_at") else 0.0
        lyric_phase = self._lyric_phase(now) if hasattr(self, "lyric_changed_at") else {}
        lyric_index = int(getattr(self, "lyric_index", 0))
        lyrics = list(getattr(self, "lyrics", ()))
        current_lyric = lyrics[lyric_index % len(lyrics)] if lyrics else ""
        diagnostics.update(
            {
                "visible_title": getattr(self, "track_title", ""),
                "visible_artist": getattr(self, "artist", ""),
                "visible_track_id": getattr(self, "media_track_id", ""),
                "visible_source_app": getattr(self, "media_source_app", ""),
                "estimated_position_seconds": estimated_position,
                "visible_position_source": getattr(self, "media_position_source", ""),
                "visible_position_confidence": getattr(self, "media_position_confidence", 0.0),
                "visible_position_interactive": bool(getattr(self, "media_position_interactive", False)),
                "visible_position_frontstage": bool(
                    getattr(self, "media_source_foreground", False)
                    or getattr(self, "media_position_interactive", False)
                    or getattr(self, "media_position_source", "") == "interactive"
                ),
                "lyric_offset_seconds": getattr(self, "lyric_offset_seconds", 0.0),
                "visible_lyric_index": lyric_index,
                "visible_current_lyric": current_lyric,
                "visible_lyrics_count": len(lyrics),
                "lyric_phase_progress": lyric_phase.get("progress", 0.0) if isinstance(lyric_phase, dict) else 0.0,
                "lyric_phase_span": lyric_phase.get("span", 0.0) if isinstance(lyric_phase, dict) else 0.0,
                "lyric_phase_source": lyric_phase.get("source", "") if isinstance(lyric_phase, dict) else "",
            }
        )
        return diagnostics

    def mouseMoveEvent(self, event):
        now = time.monotonic()
        move_interval = 0.012 if getattr(self, "finder_mode", False) else 0.025
        if now - getattr(self, "last_pointer_move_at", 0.0) < move_interval and not self.drag_candidate:
            return
        self.last_pointer_move_at = now
        self.pointer_pos = event.position()
        if self.scene_suppressed:
            self.update()
            return
        if self.drag_candidate and event.buttons() & Qt.MouseButton.LeftButton:
            self._update_drag(event)
            self.update()
            return
        key = self._hit_test(event.position())
        in_finder_frame = self._point_in_finder_frame(event.position())
        if key or in_finder_frame:
            self.left_stack_at = 0.0
        elif self.expanded and not self.left_stack_at:
            self.left_stack_at = time.monotonic()
        if self.expanded and key == "plugin":
            self._focus_plugin_at(event.position().x())
        if self._should_close_plugin_panel_for_hover(key):
            self.plugin_panel_open = False
            self._pulse_stack()
        if self._main_kind() == IslandKind.MESSAGE and key and self._is_message_hit(key) and not self.expanded:
            self._set_expanded(True)
            self.hover_armed_key = ""
            self._pulse_stack()
        if key != self.hovered_key:
            self.hovered_key = key
            self.hover_started_at = now
            self.hover_armed_key = key if key and not self.expanded and not (self._main_kind() == IslandKind.MESSAGE and self._is_message_hit(key)) else ""
            if key:
                self.hover_bounce_until = now + 1.0
            self.update()

    def _open_message_source(self, msg_key: str):
        msg = next((m for m in self.messages if self._message_key(m) == msg_key), None)
        if not msg:
            return
        self._focus_app_window_async(msg.app)
        self.opened_source_app = f"Opened {msg.app} · {msg.sender}"
        self.opened_source_until = time.monotonic() + 1.4
        self._clear_messages_for_app(msg.app)
        if self.expanded:
            self._set_expanded(False)

    def _message_key_from_hit(self, key: str) -> str:
        if key.startswith("message-dot:") or key.startswith("message:"):
            return key.split(":", 1)[1]
        return ""

    def _is_message_hit(self, key: str) -> bool:
        return key.startswith("message:") or key.startswith("message-dot:")

    def _should_close_plugin_panel_for_hover(self, key: str) -> bool:
        if not getattr(self, "plugin_panel_open", False):
            return False
        if not self.expanded or self._main_kind() != IslandKind.MUSIC:
            return False
        return key == "music" or key.startswith("music:")

    def _clear_messages_for_app(self, app_name: str):
        target = str(app_name or "").strip().casefold()
        if not target:
            return
        self.messages = [message for message in self.messages if str(getattr(message, "app", "") or "").strip().casefold() != target]
        self.barrage_preview_messages = [
            message for message in self.barrage_preview_messages if str(getattr(message, "app", "") or "").strip().casefold() != target
        ]
        self.update()

    def _focus_app_window(self, app_name: str) -> bool:
        if sys.platform != "win32" or not app_name:
            return False
        try:
            user32 = ctypes.windll.user32
            matches: list[int] = []
            needle = app_name.lower()
            needles = [needle]
            aliases = {
                "网易云音乐": ["cloudmusic", "orpheus", "netease"],
                "qq 音乐": ["qqmusic", "qq 音乐"],
                "spotify": ["spotify"],
                "微信": ["wechat", "weixin", "微信"],
                "telegram": ["telegram"],
                "飞书": ["feishu", "lark", "飞书"],
                "钉钉": ["dingtalk", "钉钉"],
            }
            for key, values in aliases.items():
                if key.lower() in needle or needle in key.lower():
                    needles.extend(values)
            enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def enum_proc(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                info = buffer.value.lower()
                try:
                    class_buf = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, class_buf, 256)
                    info += " " + class_buf.value.lower()
                except Exception:
                    pass
                if any(item and item in info for item in needles):
                    matches.append(hwnd)
                    return False
                return True

            user32.EnumWindows(enum_proc_type(enum_proc), 0)
            if not matches:
                return False
            hwnd = matches[0]
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False

    def _run_background(self, name: str, callback):
        def runner():
            try:
                callback()
            except Exception:
                pass

        thread = threading.Thread(target=runner, name=f"Island{name}", daemon=True)
        thread.start()

    def _focus_app_window_async(self, app_name: str):
        if app_name:
            self._run_background("FocusApp", lambda: self._focus_app_window(app_name))

    def _send_playback_command_async(self, command: str):
        self._run_background("MediaCommand", lambda: self.data_provider.send_playback_command(command))

    def _activate_key(self, key: str):
        if key.startswith("music:"):
            command = key.split(":", 1)[1]
            if command == "open_source":
                self._open_music_source()
            else:
                self._send_playback_command_async(command)
                if command == "toggle_play_pause":
                    self.media_is_playing = not self.media_is_playing
                self.last_media_refresh_at = 0.0
        elif key.startswith("plugin-alert:"):
            plugin_id = key.split(":", 1)[1]
            index = self._plugin_index_for_id(plugin_id)
            if index >= 0:
                self.persistent_plugin = True
                self.enabled_plugin_count = max(self.enabled_plugin_count, index + 1)
                self._set_plugin_focus(index, immediate=False)
            self._set_expanded(True)
            self.plugin_panel_open = True
        elif key.startswith("plugin-action:"):
            self._activate_plugin_action(key.split(":", 1)[1])
        elif key == "plugin":
            self.plugin_panel_open = True
            if self.expanded:
                self._focus_plugin_at(self.pointer_pos.x(), immediate=True)
            else:
                self._set_expanded(True)
        elif key == "plugin-panel":
            if not self.expanded:
                self._set_expanded(True)
            self.plugin_panel_open = True
        elif self._is_message_hit(key):
            self._open_message_source(self._message_key_from_hit(key))
        elif key and not self.expanded:
            self._set_expanded(True)
        elif key and self.expanded:
            self._set_expanded(False)
        self._pulse_stack()

    def _open_plugin_manager(self):
        if not self.plugin_samples:
            return
        index = max(0, min(self._active_plugin_focus(time.monotonic()), len(self.plugin_samples) - 1))
        label, detail, summary, _color, _icon, plugin_id, status, _alert_title, _alert_body, _alert_priority, _progress = self._plugin_tuple(self.plugin_samples[index])
        try:
            from local.plugin_manager_window import PluginManagerWindow
            existing = self.plugin_manager_windows.get(label)
            if existing is not None and existing.isVisible():
                existing.raise_()
                existing.activateWindow()
                return
            window = PluginManagerWindow(
                label,
                detail,
                summary,
                self,
                settings_getter=getattr(self, "settings_getter", None),
                settings_setter=getattr(self, "settings_setter", None),
                refresh_callback=self.refresh_plugin_data,
                diagnostics_getter=self.provider_diagnostics,
            )
            self.plugin_manager_windows[label] = window
            window.show()
            window.raise_()
            window.activateWindow()
        except Exception:
            self.opened_source_app = f"{label} 管理面板暂不可用"
            self.opened_source_until = time.monotonic() + 1.4

    def _set_plugin_setting(self, key: str, value):
        setter = getattr(self, "settings_setter", None)
        if callable(setter):
            setter(key, value)

    def _plugin_setting(self, key: str, default=None):
        getter = getattr(self, "settings_getter", None)
        if callable(getter):
            try:
                return getter(key, default)
            except Exception:
                return default
        return default

    def _format_timer_seconds(self, seconds: int) -> str:
        seconds = max(0, int(seconds))
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    def _timer_paused_remaining_seconds(self) -> int:
        try:
            return max(0, int(getattr(self, "plugin_timer_paused_remaining_seconds", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _set_timer_paused_state(self, remaining_seconds: int, progress: float = 0.0):
        self.plugin_timer_paused_remaining_seconds = max(0, int(remaining_seconds))
        self.plugin_timer_paused_progress = max(0.0, min(1.0, float(progress or 0.0)))

    def _clear_timer_paused_state(self):
        self.plugin_timer_paused_remaining_seconds = 0
        self.plugin_timer_paused_progress = 0.0

    def _append_countdown_entry(self, minutes: int, started_at: datetime, end_at: datetime):
        entries = getattr(self, "plugin_countdown_entries", None)
        if not isinstance(entries, list):
            entries = []
        entries.append(
            {
                "minutes": int(minutes),
                "started_at": started_at.isoformat(timespec="seconds"),
                "end_at": end_at.isoformat(timespec="seconds"),
                "status": "running",
            }
        )
        self.plugin_countdown_entries = entries[-2:]

    def _pause_latest_countdown_entry(self, remaining_seconds: int, progress: float):
        entries = getattr(self, "plugin_countdown_entries", None)
        if not isinstance(entries, list) or not entries:
            self.plugin_countdown_entries = [
                {
                    "text": self._format_timer_seconds(remaining_seconds),
                    "progress": max(0.0, min(1.0, float(progress or 0.0))),
                    "status": "paused",
                }
            ]
            return
        updated = [dict(entry) for entry in entries[-2:] if isinstance(entry, dict)]
        if not updated:
            self.plugin_countdown_entries = []
            return
        latest = dict(updated[-1])
        latest["text"] = self._format_timer_seconds(remaining_seconds)
        latest["progress"] = max(0.0, min(1.0, float(progress or 0.0)))
        latest["status"] = "paused"
        latest.pop("end_at", None)
        updated[-1] = latest
        self.plugin_countdown_entries = updated[-2:]

    def _resume_latest_countdown_entry(self, remaining_seconds: int, started_at: datetime, end_at: datetime):
        entries = getattr(self, "plugin_countdown_entries", None)
        if not isinstance(entries, list) or not entries:
            self._append_countdown_entry(max(1, int(math.ceil(max(1, remaining_seconds) / 60.0))), started_at, end_at)
            return
        updated = [dict(entry) for entry in entries[-2:] if isinstance(entry, dict)]
        if not updated:
            self.plugin_countdown_entries = []
            return
        latest = dict(updated[-1])
        latest["started_at"] = started_at.isoformat(timespec="seconds")
        latest["end_at"] = end_at.isoformat(timespec="seconds")
        latest["status"] = "running"
        latest.pop("text", None)
        updated[-1] = latest
        self.plugin_countdown_entries = updated[-2:]

    def _timer_setting_datetime(self, key: str, now: datetime) -> datetime | None:
        raw = str(self._plugin_setting(key, "") or "").strip()
        if not raw:
            return None
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=now.tzinfo)
        return value.astimezone(now.tzinfo)

    def _timer_remaining_seconds_from_settings(self, now: datetime | None = None) -> int:
        now = now or datetime.now().astimezone()
        target = self._timer_setting_datetime("v2_timer_end_at", now)
        if target is None:
            return 0
        return max(0, int((target - now).total_seconds()))

    def _timer_progress_from_settings(self, now: datetime | None = None) -> float:
        now = now or datetime.now().astimezone()
        started = self._timer_setting_datetime("v2_timer_started_at", now)
        target = self._timer_setting_datetime("v2_timer_end_at", now)
        if started is None or target is None or started >= target:
            return 0.0
        total = max(1.0, (target - started).total_seconds())
        elapsed = max(0.0, (now - started).total_seconds())
        return max(0.0, min(1.0, elapsed / total))

    def _request_visual_update(self):
        try:
            self.update()
        except RuntimeError:
            pass

    def _clear_alarm_delete_mode(self):
        self.plugin_alarm_delete_index = None

    def _handle_alarm_row_long_press(self, command: str) -> bool:
        match = re.fullmatch(r"alarm-edit-(\d+)", str(command or "").strip().lower())
        if not match:
            return False
        rows = self._time_alarm_rows()
        index = int(match.group(1))
        if not (0 <= index < len(rows)):
            return False
        self.plugin_alarm_delete_index = index
        self._request_visual_update()
        return True

    def _maybe_trigger_alarm_row_long_press(self, now: float) -> bool:
        if not getattr(self, "drag_candidate", False) or getattr(self, "dragging", False):
            return False
        command = str(getattr(self, "drag_press_key", "") or "")
        if command.startswith("plugin-action:"):
            command = command.split(":", 1)[1]
        if not re.fullmatch(r"alarm-edit-\d+", command):
            return False
        if float(now) - float(getattr(self, "last_pointer_press_at", 0.0) or 0.0) < 0.48:
            return False
        if self._handle_alarm_row_long_press(command):
            self.drag_candidate = False
            self.drag_press_key = ""
            return True
        return False

    def _begin_time_module_animation(self, from_module: str, to_module: str, steps: int):
        if from_module == to_module:
            self.plugin_time_module_animation = {}
            return
        direction = 1 if int(steps or 0) >= 0 else -1
        self.plugin_time_module_animation = {
            "from": str(from_module or "countdown"),
            "to": str(to_module or "countdown"),
            "direction": direction,
            "started_at": time.monotonic(),
        }

    def _time_module_transition_offsets(self, visible: str) -> tuple[str, float, float] | None:
        animation = getattr(self, "plugin_time_module_animation", {})
        if not isinstance(animation, dict) or not animation:
            return None
        if str(animation.get("to") or "") != str(visible or ""):
            return None
        try:
            started_at = float(animation.get("started_at", 0.0) or 0.0)
            direction = 1 if int(animation.get("direction", 1) or 1) >= 0 else -1
        except (TypeError, ValueError):
            return None
        elapsed = max(0.0, time.monotonic() - started_at)
        duration = 0.24
        if elapsed >= duration:
            self.plugin_time_module_animation = {}
            return None
        progress = max(0.0, min(1.0, elapsed / duration))
        eased = 1.0 - pow(1.0 - progress, 3)
        from_offset = -direction * eased
        to_offset = direction * (1.0 - eased)
        return str(animation.get("from") or ""), from_offset, to_offset

    def _activate_plugin_action(self, command: str):
        command = str(command or "").strip().lower()
        focus_match = re.fullmatch(r"alarm-editor-focus-(hour|minute|note|frequency|weekdays|frequency_unit|month_day|year_month|year_day)", command)
        if focus_match:
            self.plugin_alarm_editor_focus = focus_match.group(1)
            if self.plugin_alarm_editor_focus in {"hour", "minute", "month_day", "year_month", "year_day"}:
                self.plugin_alarm_editor_input_buffers = {}
            self._request_visual_update()
            return
        if command == "alarm-editor-note":
            self.plugin_alarm_editor_focus = "note"
            self._request_visual_update()
            return
        if command in {"alarm-editor-page-main", "alarm-editor-page-note"}:
            editor = self._time_alarm_editor_state()
            previous_page = str(editor.get("editor_page") or "main")
            next_page = "note" if command.endswith("note") else "main"
            editor["editor_page"] = next_page
            editor["detail_mode"] = ""
            self.plugin_alarm_editor = editor
            if previous_page != next_page:
                self._begin_alarm_editor_page_animation(previous_page, next_page, 1 if next_page == "note" else -1)
            self._request_visual_update()
            return
        if command == "alarm-editor-detail-confirm":
            editor = self._time_alarm_editor_state()
            editor["detail_mode"] = ""
            editor["editor_page"] = "main"
            self.plugin_alarm_editor = editor
            self.plugin_alarm_editor_focus = ""
            self._request_visual_update()
            return
        detail_commands = {
            "alarm-editor-open-weekdays": "weekly",
            "alarm-editor-open-month-day": "monthly",
            "alarm-editor-open-year-date": "yearly",
        }
        if command in detail_commands:
            editor = self._time_alarm_editor_state()
            editor["detail_mode"] = detail_commands[command]
            editor["editor_page"] = "main"
            self._normalize_alarm_editor_recurrence(editor)
            self.plugin_alarm_editor = editor
            self._request_visual_update()
            return
        weekday_match = re.fullmatch(r"alarm-editor-toggle-weekday-(\d)", command)
        if weekday_match:
            editor = self._time_alarm_editor_state()
            day = max(1, min(7, int(weekday_match.group(1))))
            weekdays = self._alarm_weekdays_from_value(editor.get("weekdays", []))
            if day in weekdays:
                weekdays = [item for item in weekdays if item != day]
            else:
                weekdays.append(day)
            editor["frequency_unit"] = "weekly"
            editor["frequency"] = "每周"
            editor["weekdays"] = sorted(weekdays) or [day]
            editor["detail_mode"] = "weekly"
            self.plugin_alarm_editor = editor
            self._begin_alarm_editor_spin(f"weekday_{day}", 1)
            self._request_visual_update()
            return
        timer_match = re.fullmatch(r"timer-start-(\d+)", command)
        if timer_match:
            minutes = max(1, min(180, int(timer_match.group(1))))
            now = datetime.now().astimezone()
            end_at = now + timedelta(minutes=minutes)
            self._clear_timer_paused_state()
            self._append_countdown_entry(minutes, now, end_at)
            self._set_plugin_setting("v2_timer_started_at", now.isoformat(timespec="seconds"))
            self._set_plugin_setting("v2_timer_end_at", end_at.isoformat(timespec="seconds"))
            self.plugin_time_mode = "dashboard"
            self.refresh_plugin_data()
            return
        if command == "timer-pause":
            now = datetime.now().astimezone()
            remaining = self._timer_remaining_seconds_from_settings(now)
            if remaining > 0:
                progress = self._timer_progress_from_settings(now)
                self._set_timer_paused_state(remaining, progress)
                self._pause_latest_countdown_entry(remaining, progress)
                self._set_plugin_setting("v2_timer_started_at", "")
                self._set_plugin_setting("v2_timer_end_at", "")
                self.refresh_plugin_data()
            return
        if command == "timer-resume":
            remaining = self._timer_paused_remaining_seconds()
            if remaining > 0:
                now = datetime.now().astimezone()
                end_at = now + timedelta(seconds=remaining)
                self._set_plugin_setting("v2_timer_started_at", now.isoformat(timespec="seconds"))
                self._set_plugin_setting("v2_timer_end_at", end_at.isoformat(timespec="seconds"))
                self._resume_latest_countdown_entry(remaining, now, end_at)
                self._clear_timer_paused_state()
                self.plugin_time_mode = "dashboard"
                self.refresh_plugin_data()
            return
        if command == "timer-clear":
            self._clear_timer_paused_state()
            self.plugin_countdown_entries = []
            self._set_plugin_setting("v2_timer_started_at", "")
            self._set_plugin_setting("v2_timer_end_at", "")
            self.refresh_plugin_data()
            return
        if command == "alarm-due-confirm":
            pending = self._time_alarm_due_pending()
            if pending:
                label = str(pending.get("label") or "闹钟").strip() or "闹钟"
                time_text = str(pending.get("time") or "").strip()
                self.plugin_alarm_due_acknowledged_key = str(pending.get("key") or f"{time_text}|{label}")
            self.plugin_alarm_due_pending = {}
            self._request_visual_update()
            return
        alarm_match = re.fullmatch(r"alarm-toggle-(\d+)", command)
        if alarm_match:
            self._clear_alarm_delete_mode()
            rows = [dict(row) for row in self._time_alarm_rows()]
            index = int(alarm_match.group(1))
            if 0 <= index < len(rows):
                rows[index]["enabled"] = not bool(rows[index].get("enabled", False))
                self._persist_alarm_rows(rows)
                self._request_visual_update()
            return
        alarm_delete_match = re.fullmatch(r"alarm-delete-(\d+)", command)
        if alarm_delete_match:
            rows = [dict(row) for row in self._time_alarm_rows()]
            index = int(alarm_delete_match.group(1))
            if 0 <= index < len(rows):
                rows.pop(index)
                self._persist_alarm_rows(rows)
            self._clear_alarm_delete_mode()
            self._request_visual_update()
            return
        alarm_edit_match = re.fullmatch(r"alarm-edit-(\d+)", command)
        if alarm_edit_match:
            self._clear_alarm_delete_mode()
            rows = [dict(row) for row in self._time_alarm_rows()]
            index = int(alarm_edit_match.group(1))
            if 0 <= index < len(rows):
                self.plugin_time_mode = "alarm_editor"
                self.plugin_alarm_editor = self._alarm_editor_state_from_row(index, rows[index])
                self.plugin_alarm_editor_focus = "note"
                self._request_visual_update()
            return
        if command == "alarm-add-open":
            self.plugin_time_mode = "alarm_editor"
            self.plugin_alarm_editor = self._default_alarm_editor_state()
            self.plugin_alarm_editor_focus = "note"
            self._request_visual_update()
            return
        if command == "alarm-editor-cancel":
            self.plugin_time_mode = "dashboard"
            self.plugin_alarm_editor_focus = ""
            self._request_visual_update()
            return
        if command == "alarm-editor-save":
            editor = self._time_alarm_editor_state()
            rows = [dict(row) for row in self._time_alarm_rows()]
            normalized_editor = dict(editor)
            self._normalize_alarm_editor_recurrence(normalized_editor)
            new_row = {
                "time": f"{int(normalized_editor['hour']):02d}:{int(normalized_editor['minute']):02d}",
                "label": str(normalized_editor.get("note") or "闹钟"),
                "enabled": True,
                "frequency": self._alarm_recurrence_label(normalized_editor),
                "frequency_unit": str(normalized_editor.get("frequency_unit") or "daily"),
                "weekdays": normalized_editor.get("weekdays", ""),
                "month_day": normalized_editor.get("month_day", 28),
                "year_month": normalized_editor.get("year_month", 4),
                "year_day": normalized_editor.get("year_day", 28),
            }
            edit_index = editor.get("edit_index")
            try:
                edit_index = int(edit_index)
            except (TypeError, ValueError):
                edit_index = None
            if edit_index is not None and 0 <= edit_index < len(rows):
                new_row["enabled"] = bool(rows[edit_index].get("enabled", True))
                rows[edit_index].update(new_row)
            else:
                rows.insert(0, new_row)
            self._persist_alarm_rows(rows)
            self.plugin_time_mode = "dashboard"
            self.plugin_alarm_editor_focus = ""
            self._request_visual_update()
            return
        if command == "weather-refresh":
            self._set_plugin_setting("v2_weather_refresh_nonce", str(time.monotonic()))
            self.refresh_plugin_data()
            return
        if command == "agenda-open":
            self._open_plugin_manager()
            return
        if command == "timer-open":
            self._open_plugin_manager()

    def refresh_plugin_data(self):
        self.last_media_refresh_at = -999.0
        self._refresh_media_data(time.monotonic())
        self.update()

    def _plugin_tuple(self, plugin):
        if len(plugin) >= 11:
            label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, progress = plugin[:11]
            return label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, progress
        if len(plugin) >= 10:
            label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority = plugin[:10]
            return label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, 0.0
        if len(plugin) >= 7:
            label, detail, summary, color, icon, plugin_id, status = plugin[:7]
            return label, detail, summary, color, icon, plugin_id, status, "", "", 50, 0.0
        label, detail, summary, color, icon = plugin[:5]
        return label, detail, summary, color, icon, "", "normal", "", "", 50, 0.0

    def _plugin_index_for_id(self, plugin_id: str) -> int:
        target = str(plugin_id or "").strip().casefold()
        if not target:
            return -1
        if target.startswith("time:"):
            target = "time"
        for index, plugin in enumerate(self.plugin_samples[: self.enabled_plugin_count]):
            _label, _detail, _summary, _color, _icon, current_id, _status, _alert_title, _alert_body, _alert_priority, _progress = self._plugin_tuple(plugin)
            if str(current_id or "").strip().casefold() == target:
                return index
        return -1

    def _time_alert_side_suppressed(self, include_expanded: bool) -> bool:
        if include_expanded:
            return False
        return bool(
            self.expanded
            and getattr(self, "plugin_panel_open", False)
            and self._main_kind() == IslandKind.PLUGIN
        )

    def _time_alarm_side_display_mode(self) -> str:
        value = str(getattr(self, "time_alarm_side_display", "note") or "note").strip().lower()
        setting = self._plugin_setting("v2_time_alarm_side_display", value)
        value = str(setting or value or "note").strip().lower()
        return "time" if value in {"time", "deadline", "due", "clock"} else "note"

    def _time_alarm_side_segment_text(self, segment: dict[str, object]) -> str:
        if self._time_alarm_side_display_mode() == "time":
            due_at = segment.get("due_at")
            if isinstance(due_at, datetime):
                return due_at.strftime("%H:%M")
            match = re.search(r"\d{1,2}:\d{2}", str(segment.get("text") or ""))
            if match:
                return match.group(0)
        return str(segment.get("text") or "").strip() or "闹钟"

    def _time_alarm_due_pending(self) -> dict[str, object]:
        pending = getattr(self, "plugin_alarm_due_pending", {})
        return dict(pending) if isinstance(pending, dict) and pending else {}

    def _time_alarm_due_acknowledged_matches(self, time_text: str, label: str) -> bool:
        acknowledged = str(getattr(self, "plugin_alarm_due_acknowledged_key", "") or "").strip()
        if not acknowledged:
            return False
        label = str(label or "闹钟").strip() or "闹钟"
        time_text = str(time_text or "").strip()
        key = f"{time_text}|{label}" if time_text else ""
        return bool((key and acknowledged == key) or acknowledged.endswith(f"|{label}"))

    def _capture_time_alarm_due_pending(self, model: dict[str, object]) -> dict[str, object]:
        if str(model.get("status") or "").strip().lower() != "alarm":
            if not self._time_alarm_due_pending():
                self.plugin_alarm_due_acknowledged_key = ""
            return {}
        summary = str(model.get("summary") or "")
        alert_body = str(model.get("alert_body") or "")
        countdown_source = f"{alert_body} {summary}".lower()
        has_countdown_context = any(token in countdown_source for token in ("倒计时", "计时", "时间到", "countdown", "timer", "time up", "done"))
        label_parts = [alert_body, str(model.get("alert_title") or "")]
        if not has_countdown_context:
            label_parts.append(summary)
        label = self._time_alarm_alert_text_from_parts(*label_parts)
        existing = self._time_alarm_due_pending()
        if label == "闹钟" and has_countdown_context and existing:
            return existing
        if label == "闹钟" and has_countdown_context and self._time_text_looks_like_countdown(alert_body):
            return {}
        time_source = summary if not has_countdown_context else ""
        time_text = self._strip_timer_prefix(str(time_source or model.get("detail") or ""))
        if not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", time_text):
            match = re.search(r"\d{1,2}:\d{2}(?::\d{2})?", str(time_source or model.get("detail") or ""))
            time_text = match.group(0) if match else ""
        if self._time_alarm_due_acknowledged_matches(time_text, label):
            return {}
        pending = {
            "time": time_text,
            "label": label or "闹钟",
            "key": f"{time_text}|{label or '闹钟'}",
            "captured_at": time.monotonic(),
        }
        self.plugin_alarm_due_pending = pending
        return dict(pending)

    def _time_side_alert_candidates(
        self,
        index: int,
        label: str,
        detail: str,
        summary: str,
        color,
        icon: str,
        plugin_id: str,
        status: str,
        alert_title: str,
        alert_body: str,
        alert_priority: int,
    ) -> list[tuple[int, int, int, str, str, str, object, str, str, str, str, int]]:
        status_key = str(status or "").strip().lower()
        candidates: list[tuple[int, int, int, str, str, str, object, str, str, str, str, int]] = []
        status_priority = {"due": 0, "severe": 1, "alarm": 2, "done": 3, "soon": 4, "rain": 5, "error": 6, "running": 7, "loading": 8}
        base_id = str(plugin_id or "time").strip() or "time"

        def add(kind: str, text: str, candidate_status: str, title: str, priority: int):
            body = str(text or "").strip()
            if not body:
                return
            virtual_id = f"{base_id}:alarm" if kind == "alarm" else base_id
            candidate_summary = "闹钟" if kind == "alarm" else body
            candidates.append(
                (
                    int(priority),
                    status_priority.get(str(candidate_status or "").lower(), 99),
                    int(index),
                    label,
                    body,
                    candidate_summary,
                    color,
                    icon,
                    virtual_id,
                    candidate_status,
                    title,
                    body,
                    int(priority),
                )
            )

        def add_countdown_from_text(text: str, priority: int, allow_bare_time: bool = True) -> bool:
            source = str(text or "").strip()
            if not self._time_text_looks_like_countdown(source):
                return False
            lowered = source.lower()
            has_countdown_context = any(token in lowered for token in ("倒计时", "计时", "时间到", "countdown", "timer", "time up", "done"))
            if not allow_bare_time and not has_countdown_context:
                return False
            done_countdown = any(token in lowered for token in ("计时结束", "时间到", "time up", "timer done", "done"))
            value = self._strip_timer_prefix(source)
            if done_countdown or value in {"", "结束"}:
                add("countdown", "时间到", "done", "倒计时", priority)
            else:
                add("countdown", value, "running", "倒计时", max(priority, 30))
            return True

        if status_key == "alarm":
            countdown_context = any(token in f"{alert_body} {summary}".lower() for token in ("倒计时", "计时", "时间到", "countdown", "timer", "time up", "done"))
            alarm_label = self._time_alarm_alert_text_from_parts(alert_body, alert_title)
            pending_alarm = self._time_alarm_due_pending()
            if alarm_label == "闹钟" and countdown_context:
                if pending_alarm:
                    pending_label = str(pending_alarm.get("label") or "闹钟").strip() or "闹钟"
                    add("alarm", pending_label, "alarm", "闹钟", min(int(alert_priority), 3))
                elif not self._time_text_looks_like_countdown(alert_body):
                    time_text = self._strip_timer_prefix(str(detail or ""))
                    if not self._time_alarm_due_acknowledged_matches(time_text, alarm_label):
                        add("alarm", alarm_label, "alarm", "闹钟", min(int(alert_priority), 3))
            else:
                time_source = "" if countdown_context else summary
                time_text = self._strip_timer_prefix(str(time_source or detail or ""))
                if not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", time_text):
                    match = re.search(r"\d{1,2}:\d{2}(?::\d{2})?", str(time_source or detail or ""))
                    time_text = match.group(0) if match else ""
                if not self._time_alarm_due_acknowledged_matches(time_text, alarm_label):
                    add("alarm", alarm_label, "alarm", "闹钟", min(int(alert_priority), 3))
            add_countdown_from_text(summary, max(min(int(alert_priority) + 2, 5), 4), allow_bare_time=False)
            return candidates

        pending_alarm = self._time_alarm_due_pending()
        if pending_alarm:
            pending_label = str(pending_alarm.get("label") or "闹钟").strip() or "闹钟"
            add("alarm", pending_label, "alarm", "闹钟", min(int(alert_priority), 3))

        if status_key in {"done", "due"}:
            note = self._time_alarm_alert_text(alert_body)
            if note != "闹钟" and not self._time_text_looks_like_countdown(alert_body):
                candidates.clear()
                add("alarm", note, "alarm", "闹钟", min(int(alert_priority), 3))
            else:
                countdown_source = next(
                    (
                        str(candidate or "")
                        for candidate in (alert_body, summary, detail)
                        if self._time_text_looks_like_countdown(str(candidate or ""))
                    ),
                    "时间到",
                )
                add_countdown_from_text(countdown_source, int(alert_priority))
            return candidates

        model_segments = self._time_compact_segments(detail, summary, status, alert_body)
        for segment in model_segments[1:]:
            kind = str(segment.get("kind") or "").strip().lower()
            text = str(segment.get("text") or "").strip()
            if kind == "alarm":
                add("alarm", self._time_alarm_side_segment_text(segment), "running", "闹钟", 29)
            elif kind == "countdown":
                segment_status = str(segment.get("status") or status_key or "running").strip().lower()
                candidate_status = "done" if segment_status in {"done", "due"} or text == "时间到" else "running"
                add("countdown", text or self._strip_timer_prefix(alert_body or summary or detail), candidate_status, "倒计时", int(alert_priority))
        return candidates

    def _active_plugin_alerts(self, limit: int = 2, include_expanded: bool = False):
        if not self._plugin_enabled() or (self.expanded and not include_expanded):
            return []
        alert_statuses = {"done", "due", "soon", "running", "loading", "error", "alarm", "severe", "rain"}
        status_priority = {"due": 0, "severe": 1, "alarm": 2, "done": 3, "soon": 4, "rain": 5, "error": 6, "running": 7, "loading": 8}
        candidates = []
        for index, plugin in enumerate(self.plugin_samples[: self.enabled_plugin_count]):
            label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, _progress = self._plugin_tuple(plugin)
            is_time = str(plugin_id or "").strip().casefold() == "time" or str(label or "").strip().upper() == "TIME"
            if is_time and self._time_alert_side_suppressed(include_expanded):
                continue
            if is_time:
                self._capture_time_alarm_due_pending(
                    {
                        "label": label,
                        "detail": detail,
                        "summary": summary,
                        "status": status,
                        "alert_title": alert_title,
                        "alert_body": alert_body,
                    }
                )
                candidates.extend(self._time_side_alert_candidates(index, label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority))
                continue
            normalized_status = str(status or "").lower()
            if normalized_status in alert_statuses or alert_title or alert_body:
                candidates.append((
                    int(alert_priority),
                    status_priority.get(normalized_status, 99),
                    index,
                    label,
                    detail,
                    summary,
                    color,
                    icon,
                    plugin_id,
                    status,
                    alert_title,
                    alert_body,
                    alert_priority,
                ))
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        return [
            (index, label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority)
            for _alert_priority, _status_priority, index, label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority in candidates[: max(0, int(limit))]
        ]

    def _active_plugin_alert(self):
        alerts = self._active_plugin_alerts(1)
        return alerts[0] if alerts else None

    def _open_music_source(self):
        app_name = str(getattr(self, "media_source_app", "") or "").strip()
        self._focus_app_window_async(app_name)
        self.opened_source_app = f"Opening {app_name or 'music app'}"
        self.opened_source_until = time.monotonic() + 1.4

    def _update_drag(self, event):
        if not self.drag_candidate:
            return
        global_pos = event.globalPosition().toPoint()
        delta = global_pos - self.drag_press_global
        if not self.dragging:
            if abs(delta.x()) + abs(delta.y()) < self.drag_threshold:
                return
            self.dragging = True
        if self.finder_mode:
            screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
            if screen:
                geo = screen.geometry()
                if delta.y() > max(self._u(22.0), 18.0):
                    self._detach_from_finder_for_drag(global_pos)
                    self._move_standalone_drag(global_pos)
                    self._pulse_stack()
                    return
                self.resize(geo.width(), self.height())
                self.move(geo.left(), geo.top())
                usable = max(1.0, float(geo.width()))
                ratio = (global_pos.x() - geo.left()) / usable
                self.stack_center_ratio = max(0.06, min(0.94, ratio))
                self._force_super_topmost()
        else:
            if self._should_attach_to_finder(global_pos, delta):
                self._attach_to_finder_for_drag(global_pos)
            else:
                self._move_standalone_drag(global_pos)
        self._pulse_stack()

    def _finder_attach_allowed(self) -> bool:
        return bool(getattr(self.finder_material, "available", False) or getattr(self, "manual_finder_available", False))

    def _should_attach_to_finder(self, global_pos: QPoint, delta: QPoint) -> bool:
        if not self._finder_attach_allowed() or delta.y() >= 0:
            return False
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        if not screen:
            return False
        geo = screen.geometry()
        threshold = max(self._u(42.0), float(getattr(self.finder_material, "bar_height", 28.0)) + self._u(18.0))
        return global_pos.y() <= geo.top() + threshold

    def _attach_to_finder_for_drag(self, global_pos: QPoint):
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        if not screen:
            return
        geo = screen.geometry()
        self._begin_surface_transition("finder")
        self.finder_mode = True
        self.attach_to_real_finder = True
        self.finder_material = read_finder_material(force_screen_sample=True, include_center=True)
        self.finder_material_refreshed_at = time.monotonic()
        self.resize(geo.width(), self.height())
        self.move(geo.left(), geo.top())
        self.saved_window_pos = None
        self.stack_center_ratio = max(0.06, min(0.94, (global_pos.x() - geo.left()) / max(1.0, float(geo.width()))))
        self._force_super_topmost()
        self._notify_settings_changed()

    def _remember_motion_global_rects(self):
        origin = self.mapToGlobal(QPoint(0, 0))
        ox = float(origin.x())
        oy = float(origin.y())
        self.drag_detach_global_rects = {
            key: QRectF(motion.x + ox, motion.y + oy, motion.w, motion.h)
            for key, motion in self.motions.items()
            if motion.opacity > 0.025
        }

    def _restore_motion_global_rects_after_move(self):
        saved = getattr(self, "drag_detach_global_rects", {})
        if not saved:
            return
        origin = self.mapToGlobal(QPoint(0, 0))
        ox = float(origin.x())
        oy = float(origin.y())
        for key, global_rect in saved.items():
            motion = self.motions.get(key)
            if motion is None:
                continue
            motion.x = global_rect.left() - ox
            motion.y = global_rect.top() - oy
            motion.w = global_rect.width()
            motion.h = global_rect.height()
            motion.vx = motion.vy = motion.vw = motion.vh = 0.0
        self.drag_detach_global_rects = {}

    def _detach_from_finder_for_drag(self, global_pos: QPoint):
        self._remember_motion_global_rects()
        self.finder_mode = False
        self.attach_to_real_finder = False
        self.group_frame_motion = None
        self.group_frame_mask_path = None
        self.clearMask()
        self.interaction_mask_active = False
        self.last_mask_rect = QRectF()
        standalone_width = int(round(self._u(940)))
        if self.width() != standalone_width:
            self.resize(standalone_width, self.height())
        self._notify_settings_changed()

    def _move_standalone_drag(self, global_pos: QPoint):
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        screen_geo = screen.geometry() if screen else None
        main_kind = self._main_kind()
        target_stack_w = self._stack_target_width(main_kind)
        target_main_w, _target_main_h = self._main_size_for_kind(main_kind, target_stack_w)
        desired_center_x = float(global_pos.x()) + target_main_w / 2.0 - float(self.drag_press_main_offset.x())
        stack_w = min(target_stack_w, self._standalone_width_limit_for_global_center(desired_center_x, screen_geo, target_stack_w))
        stack_w = max(target_stack_w * 0.5, stack_w)
        main_w, main_h = self._main_size_for_kind(main_kind, stack_w)
        old_direction = getattr(self, "stack_direction", 1)
        old_main_top = self._main_top_for_direction(main_kind, main_h, old_direction)
        direction = self._standalone_drag_direction(global_pos)
        self.stack_direction = direction
        main_left = self.width() / 2.0 - main_w / 2.0
        main_top = self._main_top_for_direction(main_kind, main_h, direction)
        if direction != old_direction:
            local_delta_y = main_top - old_main_top
            for motion in self.motions.values():
                motion.y += local_delta_y
                motion.vy = 0.0
        new_x = int(round(global_pos.x() - main_left - self.drag_press_main_offset.x()))
        new_y = int(round(global_pos.y() - main_top - self.drag_press_main_offset.y()))
        if screen_geo:
            new_pos = self._clamp_standalone_window_pos(new_x, new_y, main_left, main_top, main_w, main_h, screen_geo)
        else:
            new_pos = QPoint(new_x, new_y)
        self.move(new_pos)
        self._restore_motion_global_rects_after_move()
        self.saved_window_pos = (int(new_pos.x()), int(new_pos.y()))

    def _clamp_standalone_window_pos(
        self,
        new_x: float,
        new_y: float,
        main_left: float,
        main_top: float,
        main_w: float,
        main_h: float,
        screen_geo,
    ) -> QPoint:
        margin = self._u(6.0)
        x = float(new_x)
        y = float(new_y)
        min_x = float(screen_geo.left()) + margin - float(main_left)
        max_x = float(screen_geo.right()) - margin - float(main_left) - float(main_w)
        min_y = float(screen_geo.top()) + margin - float(main_top)
        max_y = float(screen_geo.bottom()) - margin - float(main_top) - float(main_h)
        if min_x <= max_x:
            x = max(min_x, min(max_x, x))
        if min_y <= max_y:
            y = max(min_y, min(max_y, y))
        return QPoint(int(round(x)), int(round(y)))

    def _drag_main_offset_for_press(self, press_pos: QPointF, main_rect: QRectF, key: str = "") -> QPointF:
        if str(key or "").startswith("plugin-alert:"):
            return QPointF(main_rect.width() / 2.0, main_rect.height() / 2.0)
        if main_rect.contains(press_pos):
            return press_pos - main_rect.topLeft()
        return QPointF(main_rect.width() / 2.0, main_rect.height() / 2.0)

    def _save_placement_state(self):
        if self.finder_mode:
            self.saved_window_pos = None
        elif self.saved_window_pos is None:
            self.saved_window_pos = (int(self.x()), int(self.y()))
        self._notify_settings_changed()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.last_pointer_press_at = time.monotonic()
        self.pointer_pos = event.position()
        if self.scene_suppressed:
            self.drag_candidate = False
            self.dragging = False
            return
        self.drag_press_pos = QPointF(event.position())
        self.drag_press_global = event.globalPosition().toPoint()
        self.drag_press_window_pos = self.pos()
        self.drag_press_center_ratio = self.stack_center_ratio
        self.drag_press_key = self._hit_test(event.position())
        self.drag_press_main_offset = QPointF()
        try:
            visuals = self._build_visuals()
            main_visual = next((visual for visual in visuals if visual.z_index == 10), None)
            if main_visual:
                motion = self.motions.get(main_visual.key)
                main_rect = QRectF(motion.rect) if motion and motion.opacity > 0.08 else QRectF(main_visual.rect)
                self.drag_press_main_offset = self._drag_main_offset_for_press(event.position(), main_rect, self.drag_press_key)
        except Exception:
            self.drag_press_main_offset = QPointF()
        self.drag_candidate = True
        self.dragging = False
        self.hover_armed_key = ""
        self.hovered_key = ""
        self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.scene_suppressed:
                return
            if self._main_kind() != IslandKind.MUSIC:
                return
            self._set_expanded(False)
            self._pulse_stack()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.scene_suppressed:
            self.drag_candidate = False
            self.dragging = False
            return
        if self.drag_candidate and not self.dragging:
            key = self.drag_press_key or self._hit_test(event.position())
            if key:
                self._activate_key(key)
            self.update()
        self.drag_candidate = False
        self.dragging = False
        self._save_placement_state()

    def wheelEvent(self, event):
        if self._handle_time_alarm_editor_wheel(event):
            return
        if self._handle_time_panel_module_wheel(event):
            return
        super().wheelEvent(event)

    def leaveEvent(self, event):
        self.hovered_key = ""
        self.hover_armed_key = ""
        self.barrage_hovered = False
        self.pointer_pos = QPointF(-1, -1)
        if self.expanded:
            self.left_stack_at = time.monotonic()
        super().leaveEvent(event)

    def changeEvent(self, event):
        if is_theme_change_event(event):
            self.update()
        super().changeEvent(event)

    def _point_in_finder_frame(self, point: QPointF) -> bool:
        if not self.finder_mode:
            return False
        rect = getattr(self, "finder_hover_rect", QRectF())
        return not rect.isNull() and rect.adjusted(-self._u(2.0), -self._u(2.0), self._u(2.0), self._u(2.0)).contains(point)

    def _hit_test(self, point: QPointF) -> str:
        if self.expanded and self._main_kind() == IslandKind.MUSIC:
            if getattr(self, "music_cover_rect", QRectF()).contains(point):
                return "music:open_source"
            for key, rect in self.music_button_rects.items():
                if rect.contains(point):
                    return key
        if self.expanded:
            if str(getattr(self, "plugin_time_mode", "dashboard")) == "alarm_editor":
                action_rects = getattr(self, "plugin_action_rects", {})
                for command, rect in action_rects.items():
                    if str(command).startswith("alarm-editor-open-") and rect.contains(point):
                        return f"plugin-action:{command}"
                for command, rect in action_rects.items():
                    if rect.contains(point):
                        return f"plugin-action:{command}"
                for field, rect in getattr(self, "plugin_alarm_editor_rects", {}).items():
                    if rect.contains(point):
                        return f"plugin-action:alarm-editor-focus-{field}"
            else:
                for command, rect in getattr(self, "plugin_action_rects", {}).items():
                    if rect.contains(point):
                        return f"plugin-action:{command}"
        for key, motion in sorted(self.motions.items(), key=lambda item: item[1].rect.y(), reverse=True):
            if motion.opacity > 0.22 and motion.rect.contains(point):
                return key
        try:
            for visual in sorted(self._build_visuals(), key=lambda item: item.z_index, reverse=True):
                if visual.rect.contains(point):
                    return visual.key
        except Exception:
            pass
        return ""

    def _set_plugin_focus(self, index: int, immediate: bool = False):
        if self.enabled_plugin_count <= 0:
            self.plugin_focus = 0
            self.plugin_focus_from = 0
            self.plugin_focus_target = 0
            return
        index = max(0, min(index, self.enabled_plugin_count - 1))
        now = time.monotonic()
        if immediate:
            self.plugin_focus = index
            self.plugin_focus_from = index
            self.plugin_focus_target = index
            self.plugin_focus_changed_at = now - 0.26
            return
        if index == self.plugin_focus_target:
            return
        self.plugin_focus_from = self._active_plugin_focus(now)
        self.plugin_focus = index
        self.plugin_focus_target = index
        self.plugin_focus_changed_at = now

    def _focus_plugin_at(self, x: float, immediate: bool = False):
        if self.enabled_plugin_count <= 0:
            return
        motion = self.motions.get("plugin")
        if not motion:
            return
        now = time.monotonic()
        cells = self._plugin_item_rects(motion.rect, now, expand_active=False)
        if not cells:
            return
        for index, cell in cells:
            if cell.left() <= x <= cell.right():
                self._set_plugin_focus(index, immediate=immediate)
                return
        nearest = min(cells, key=lambda item: abs(item[1].center().x() - x))
        self._set_plugin_focus(nearest[0], immediate=immediate)

    def _plugin_focus_progress(self, now: float) -> float:
        elapsed = now - self.plugin_focus_changed_at
        progress = max(0.0, min(1.0, elapsed / 0.26))
        return progress * progress * (3.0 - 2.0 * progress)

    def _active_plugin_focus(self, now: float) -> int:
        if self.enabled_plugin_count <= 0:
            return 0
        if self.expanded:
            progress = self._plugin_focus_progress(now)
            if progress >= 1.0:
                return self.plugin_focus_target
            return self.plugin_focus_from if progress < 0.5 else self.plugin_focus_target
        return min(self.plugin_focus, self.enabled_plugin_count - 1)

    def _plugin_focus_weights(self, count: int, now: float, expand_active: bool = True) -> list[float]:
        if count <= 0:
            return []
        if not self.expanded or not expand_active:
            return [1.0] * count
        from_index = max(0, min(self.plugin_focus_from, count - 1))
        target_index = max(0, min(self.plugin_focus_target, count - 1))
        progress = self._plugin_focus_progress(now)
        weights = [0.0] * count
        if from_index == target_index:
            weights[target_index] = 1.0
        else:
            weights[from_index] = 1.0 - progress
            weights[target_index] = progress
        return weights

    def _plugin_widths(self, count: int, total_width: float, now: float, expand_active: bool = True) -> list[float]:
        if count <= 0:
            return []
        if not self.expanded or count == 1 or not expand_active:
            return [total_width / count] * count
        focus_cap = min(total_width * 0.40, self._u(220.0))
        focus_width = max(total_width / count, focus_cap)
        compact_slot = (total_width - focus_width) / max(1, count - 1)
        from_index = max(0, min(self.plugin_focus_from, count - 1))
        target_index = max(0, min(self.plugin_focus_target, count - 1))
        progress = self._plugin_focus_progress(now)
        from_widths = [compact_slot] * count
        target_widths = [compact_slot] * count
        from_widths[from_index] = focus_width
        target_widths[target_index] = focus_width
        return [from_widths[i] + (target_widths[i] - from_widths[i]) * progress for i in range(count)]

    def _plugin_item_rects(self, rect: QRectF, now: float, expand_active: bool = True) -> list[tuple[int, QRectF]]:
        count = max(0, min(self.enabled_plugin_count, 3))
        widths = self._plugin_widths(count, rect.width(), now, expand_active=expand_active)
        cells = []
        x = rect.left()
        for index, width in enumerate(widths):
            right = rect.right() if index == count - 1 else x + width
            cells.append((index, QRectF(x, rect.top(), right - x, rect.height())))
            x = right
        return cells

    def _music_target_width(self, expanded: bool | None = None) -> float:
        expanded = self.expanded if expanded is None else expanded
        instrumental = self._is_instrumental_track()
        cleaned = [self._clean_lyric_for_width(line) for line in self.lyrics]
        lyric_lines = [line for line in cleaned if line]
        active_lyric_size = max(14, min(24, int(getattr(self, "lyric_font_size", 18))))
        compact_lyric_size = max(12, min(20, active_lyric_size - 2))

        if expanded:
            title_w = QFontMetrics(self._font(13, QFont.Weight.Bold, shrink=False)).horizontalAdvance(self.track_title)
            artist_w = QFontMetrics(self._font(9, shrink=False)).horizontalAdvance(self.artist)
            lyric_w = self._representative_lyric_width(lyric_lines, active_lyric_size, True)
            identity_w = max(title_w, artist_w * 0.88)
            content_w = identity_w if instrumental else max(identity_w, lyric_w * 0.82)
            required = self._u(58 + 16 + 15 + 132 + 48) + content_w
            return max(self._u(470.0), min(self._u(760.0), required))

        if self._should_paint_album_island():
            stage_w = self._album_island_preferred_stage_width()
            required = self._u(9 + 30 + 12 + 25) + stage_w
            return max(self._u(300.0), min(self._u(760.0), required))

        compact_text = self._compact_music_text()
        compact_metric = QFontMetrics(self._font(compact_lyric_size, QFont.Weight.Bold, shrink=False))
        title_metric = QFontMetrics(self._font(11, QFont.Weight.Bold, shrink=False))
        content_w = compact_metric.horizontalAdvance(compact_text)
        title_w = title_metric.horizontalAdvance(self.track_title)
        artist_w = title_metric.horizontalAdvance(self.artist)
        lyric_w = self._representative_lyric_width(lyric_lines, compact_lyric_size, True)
        current_dual_w = self._current_compact_dual_line_width(compact_lyric_size)
        if self._gelv_enabled():
            active_size, next_size = self._gelv_line_sizes(taskbar=False)
            dual_size = self._gelv_dual_line_size(taskbar=False)
            second_size = dual_size if (self._gelv_current_dual_line() or self._gelv_current_mixed_language_line()) else next_size
            gelv_lines = self._gelv_display_lines()
            gelv_w = max(
                (
                    self._gelv_layout_width(line, active_size if index == 0 else second_size)
                    for index, line in enumerate(gelv_lines)
                    if line
                ),
                default=0.0,
            )
            current_dual_w = max(current_dual_w, gelv_w)
        return self._compact_music_target_width_from_content(content_w, title_w, artist_w, lyric_w, current_dual_w, instrumental)

    def _compact_music_target_width_from_content(
        self,
        content_w: float,
        title_w: float,
        artist_w: float,
        lyric_w: float,
        current_dual_w: float,
        instrumental: bool,
    ) -> float:
        if instrumental:
            content_w = max(content_w, title_w, artist_w * 0.72)
        elif current_dual_w > 0:
            content_w = max(content_w, current_dual_w, title_w * 0.64)
        else:
            content_w = max(content_w, lyric_w * 0.76, title_w * 0.64)
        show_cover = bool(getattr(self, "compact_lyric_show_cover", True))
        cover_slot = 50.0 if current_dual_w > 0 else 30.0
        media_lead = cover_slot + 15 if show_cover else 0.0
        required = self._u(9 + media_lead + 32) + content_w
        min_w = self._u(470.0) if current_dual_w > 0 else self._u(430.0)
        max_w = self._u(760.0) if current_dual_w > 0 else self._u(610.0)
        return max(min_w, min(max_w, required))

    def _current_compact_dual_line_width(self, compact_size: float) -> float:
        lines = self._current_lyric_display_lines()
        if len(lines) <= 1:
            return 0.0
        secondary_bold = int(getattr(self, "dual_line_secondary_weight", 72)) >= 78
        secondary_spacing = int(getattr(self, "dual_line_secondary_spacing", 100))
        primary_w = self._lyric_layout(lines[0], max(12, compact_size - 1), True).total_width
        secondary_w = self._lyric_layout(lines[1], max(10, compact_size - 4), secondary_bold, secondary_spacing).total_width
        return max(primary_w, secondary_w)

    def _representative_text_width(self, lines: list[str], metrics: QFontMetrics) -> float:
        if not lines:
            return 0.0
        widths = sorted(metrics.horizontalAdvance(line) for line in lines if line)
        if not widths:
            return 0.0
        index = min(len(widths) - 1, max(0, int(round((len(widths) - 1) * 0.72))))
        return float(widths[index])

    def _representative_lyric_width(self, lines: list[str], size: float, bold: bool) -> float:
        if not lines:
            return 0.0
        key = (
            tuple(lines),
            self._font_size(size),
            bool(bold),
            tuple(self._font_families()),
            bool(getattr(self, "high_quality_font_rendering", False)),
            int(getattr(self, "lyric_elastic_strength", 100)),
            int(getattr(self, "lyric_word_spacing", 100)),
        )
        cache = getattr(self, "_representative_lyric_width_cache", None)
        if cache is None:
            cache = {}
            self._representative_lyric_width_cache = cache
        cached = cache.get(key)
        if cached is not None:
            return cached
        widths = sorted(self._lyric_layout(line, size, bold).total_width for line in lines if line)
        if not widths:
            return 0.0
        index = min(len(widths) - 1, max(0, int(round((len(widths) - 1) * 0.72))))
        value = float(widths[index])
        cache[key] = value
        if len(cache) > 16:
            cache.pop(next(iter(cache)))
        return value

    def _is_instrumental_track(self) -> bool:
        if len(self.lyrics) > 1:
            return False
        lines = [self._clean_lyric_for_width(line).lower() for line in self.lyrics]
        lines = [line for line in lines if line]
        if not lines:
            return True
        marker_text = " ".join(lines[:4])
        markers = ("纯音乐", "instrumental", "伴奏", "无人声", "无歌词", "暂无歌词", "暂无")
        return any(marker in marker_text for marker in markers)

    def _compact_music_text(self) -> str:
        if self._is_instrumental_track():
            title = self.track_title.strip()
            artist = self.artist.strip()
            if title and artist:
                return f"{title} · {artist}"
            return title or artist or "纯音乐"
        current = self._clean_lyric_for_width(self.lyrics[self.lyric_index]) if self.lyrics else ""
        return current or self.track_title or self.artist or "正在播放"

    def _lyric_display_lines(self, lyric: str) -> list[str]:
        text = str(lyric or "").strip()
        if not text:
            return []
        allow_dual_line = bool(getattr(self, "enable_dual_line_lyrics", False))
        if "\n" in text:
            lines = [" ".join(part.split()) for part in text.splitlines()]
            lines = [line for line in lines if line]
            return lines[:2] if allow_dual_line else lines[:1]
        if not allow_dual_line:
            return [" ".join(text.split())]
        for pattern in (r"\s[|｜]\s", r"\s/{1,2}\s"):
            parts = [" ".join(part.split()) for part in re.split(pattern, text, maxsplit=1)]
            if len(parts) == 2 and self._looks_like_two_line_lyric(parts[0], parts[1]):
                return parts
        return [" ".join(text.split())]

    def _looks_like_two_line_lyric(self, first: str, second: str) -> bool:
        if len(first) < 4 or len(second) < 4:
            return False
        if len(first) > 48 or len(second) > 48:
            return False
        has_cjk = any(self._is_cjk_lyric_char(char) for char in first + second)
        if has_cjk:
            return True
        return len(first.split()) >= 3 and len(second.split()) >= 3

    def _gelv_enabled(self) -> bool:
        return str(getattr(self, "lyric_flow_style", "wave_stream")) == "gelv_songscatter"

    def _active_lyric_color_fill_enabled(self) -> bool:
        style = str(getattr(self, "lyric_flow_style", "wave_stream"))
        if style == "gelv_songscatter":
            return bool(getattr(self, "gelv_color_fill_enabled", False))
        return bool(getattr(self, "wave_color_fill_enabled", False))

    def _active_lyric_fill_color(self) -> QColor:
        if self._gelv_enabled():
            return self._gelv_fill_color()
        return self._wave_fill_color()

    def _gelv_line_sizes(self, taskbar: bool = False) -> tuple[int, int]:
        active = max(14, min(30, int(getattr(self, "gelv_primary_font_size", 22))))
        secondary = max(8, min(24, int(getattr(self, "gelv_secondary_font_size", 12))))
        secondary = min(active, secondary)
        if taskbar:
            active = max(12, active - 4)
            secondary = max(8, min(active, secondary - 3))
        return active, secondary

    def _gelv_dual_line_size(self, taskbar: bool = False) -> int:
        active, _secondary = self._gelv_line_sizes(taskbar=taskbar)
        size = max(8, min(30, int(getattr(self, "gelv_dual_line_font_size", 17))))
        if taskbar:
            size = max(8, size - 3)
        return min(active, size)

    def _gelv_line_pixel_height(self, size: float, expanded: bool = False) -> float:
        metrics = QFontMetrics(self._gelv_font(size, QFont.Weight.Normal))
        padding = self._u(3.0 if expanded else 2.0)
        fallback = self._u(max(float(size) + (10.0 if expanded else 4.0), 14.0))
        return max(fallback, float(metrics.height()) + padding)

    def _gelv_compact_music_height(self) -> float:
        active_size, secondary_size = self._gelv_line_sizes(taskbar=False)
        line_count = len(self._gelv_display_lines())
        if line_count <= 1:
            content_px = self._gelv_line_pixel_height(active_size, expanded=False)
            return max(46.0, min(64.0, content_px / self.VISUAL_SCALE + 9.0))
        second_size = self._gelv_dual_line_size(taskbar=False) if (
            self._gelv_current_dual_line() or self._gelv_current_mixed_language_line()
        ) else secondary_size
        line_heights = [
            self._gelv_line_pixel_height(size, expanded=False)
            for size in (active_size, second_size)[:line_count]
        ]
        content_px = sum(line_heights) + self._u(1.0) * max(0, line_count - 1)
        return max(46.0, min(82.0, content_px / self.VISUAL_SCALE + 9.0))

    def _album_island_compact_music_height(self) -> float:
        cover = max(18, min(44, int(getattr(self, "album_island_cover_size", 30))))
        return max(46.0, min(72.0, float(cover) + 16.0))

    def _gelv_display_lines(self) -> list[str]:
        lyrics = getattr(self, "lyrics", None) or []
        if not lyrics:
            return []
        index = int(getattr(self, "lyric_index", 0)) % len(lyrics)
        active = str(lyrics[index] or "").strip()
        active_lines = [line.strip() for line in active.splitlines() if line.strip()]
        dual_enabled = bool(getattr(self, "enable_dual_line_lyrics", False))
        active_primary = active_lines[0] if active_lines else active
        if dual_enabled and len(active_lines) > 1:
            return active_lines[:2]
        mixed = self._gelv_split_mixed_language_line(active) if dual_enabled else None
        if mixed is not None:
            return [mixed[0], mixed[1]]
        if len(lyrics) <= 1:
            return [active_primary] if active_primary else []
        next_line = str(lyrics[(index + 1) % len(lyrics)] or "").strip()
        next_primary = next((line.strip() for line in next_line.splitlines() if line.strip()), next_line)
        return [line for line in (active_primary, next_primary) if line]

    def _gelv_current_dual_line(self) -> bool:
        lyrics = getattr(self, "lyrics", None) or []
        if not lyrics or not bool(getattr(self, "enable_dual_line_lyrics", False)):
            return False
        index = int(getattr(self, "lyric_index", 0)) % len(lyrics)
        active = str(lyrics[index] or "")
        return len([line for line in active.splitlines() if line.strip()]) > 1

    def _gelv_current_mixed_language_line(self) -> bool:
        lyrics = getattr(self, "lyrics", None) or []
        if not lyrics or not bool(getattr(self, "enable_dual_line_lyrics", False)):
            return False
        index = int(getattr(self, "lyric_index", 0)) % len(lyrics)
        return self._gelv_split_mixed_language_line(str(lyrics[index] or "")) is not None

    def _gelv_split_mixed_language_line(self, text: str) -> tuple[str, str] | None:
        text = " ".join(str(text or "").split())
        if not text:
            return None
        has_cjk = any(self._is_cjk_lyric_char(char) for char in text)
        has_latin = any(("A" <= char <= "Z") or ("a" <= char <= "z") for char in text)
        if not has_cjk or not has_latin:
            return None
        matches = list(re.finditer(r"\s+", text))
        for match in reversed(matches):
            left = text[: match.start()].strip()
            right = text[match.end() :].strip()
            if not left or not right:
                continue
            left_cjk = any(self._is_cjk_lyric_char(char) for char in left)
            right_cjk = any(self._is_cjk_lyric_char(char) for char in right)
            left_latin = any(("A" <= char <= "Z") or ("a" <= char <= "z") for char in left)
            right_latin = any(("A" <= char <= "Z") or ("a" <= char <= "z") for char in right)
            if left_latin and not left_cjk and right_cjk:
                return left, right
            if left_cjk and right_latin and not right_cjk:
                return left, right
        return None

    def _gelv_spacing_percent(self, progress: float = 1.0) -> int:
        initial = max(100, min(320, int(getattr(self, "gelv_initial_word_spacing", 220))))
        target = max(initial, min(420, int(getattr(self, "gelv_word_spacing", 320))))
        progress = max(0.0, min(1.0, float(progress)))
        return int(round(initial + (target - initial) * progress))

    def _gelv_layout_width(self, text: str, size: float, spacing_progress: float = 1.0) -> float:
        spacing = self._gelv_spacing_percent(spacing_progress)
        layout = self._gelv_layout(text, size, spacing_percent=spacing)
        return float(layout.total_width)

    def _gelv_transition_state(self, now: float | None = None) -> dict[str, float]:
        now = time.monotonic() if now is None else float(now)
        current_index = int(getattr(self, "lyric_index", 0))
        previous_index = int(getattr(self, "prev_lyric_index", current_index))
        changed_at = float(getattr(self, "lyric_changed_at", now))
        duration = 0.42
        elapsed = max(0.0, min(1.0, (now - changed_at) / duration))
        transitioning = previous_index != current_index and elapsed < 1.0
        if transitioning:
            eased = 1.0 - (1.0 - elapsed) ** 4
            size_mix = eased if bool(getattr(self, "gelv_size_shift_enabled", True)) else 1.0
            return {
                "progress": elapsed,
                "eased": eased,
                "out_alpha": 1.0 - eased,
                "in_alpha": max(0.18, eased),
                "out_y_offset": 0.0,
                "current_y_offset": 0.0,
                "current_size_mix": size_mix,
            }
        return {
            "progress": 1.0,
            "eased": 1.0,
            "out_alpha": 0.0,
            "in_alpha": 1.0,
            "out_y_offset": 0.0,
            "current_y_offset": 0.0,
            "current_size_mix": 1.0,
        }

    def _gelv_line_x(self, rect: QRectF, text: str, size: float, progress: float, spacing_percent: int | None = None) -> float:
        spacing = int(spacing_percent) if spacing_percent is not None else self._gelv_spacing_percent(progress)
        layout = self._gelv_layout(text, size, spacing_percent=spacing)
        text_w = float(layout.total_width)
        if text_w <= rect.width() - self._u(8.0):
            return rect.center().x() - text_w / 2.0
        travel = max(0.0, text_w - rect.width() + self._u(18.0))
        eased = 1.0 - (1.0 - max(0.0, min(1.0, float(progress)))) ** 3
        return rect.left() - travel * eased

    def _gelv_primary_text_for_index(self, index: int) -> str:
        lyrics = getattr(self, "lyrics", None) or []
        if not lyrics:
            return ""
        text = str(lyrics[index % len(lyrics)] or "").strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            return lines[0]
        mixed = self._gelv_split_mixed_language_line(text) if bool(getattr(self, "enable_dual_line_lyrics", False)) else None
        return mixed[0] if mixed is not None else text

    def _gelv_line_rects(self, rect: QRectF, expanded: bool = False, now: float | None = None) -> list[dict[str, object]]:
        lines = self._gelv_display_lines()
        if not lines:
            return []
        active_size, secondary_size = self._gelv_line_sizes(taskbar=False)
        dual = self._gelv_current_dual_line() or self._gelv_current_mixed_language_line()
        dual_line_size = self._gelv_dual_line_size(taskbar=False)
        state = self._gelv_transition_state(now)
        size_mix = max(0.0, min(1.0, float(state.get("current_size_mix", 1.0))))
        incoming_size = secondary_size + (active_size - secondary_size) * size_mix
        if dual:
            sizes = [incoming_size, dual_line_size]
            actives = [True, True]
            stable_sizes = [active_size, dual_line_size]
        else:
            sizes = [incoming_size, secondary_size]
            actives = [True, False]
            stable_sizes = [active_size, secondary_size]
        gap = self._u(8.0 if expanded else 1.0)
        line_heights = [self._gelv_line_pixel_height(size, expanded=expanded) for size in stable_sizes[: len(lines)]]
        total_h = sum(line_heights) + gap * max(0, len(line_heights) - 1)
        y = rect.center().y() - total_h / 2.0
        rows: list[dict[str, object]] = []
        for index, text in enumerate(lines[:2]):
            row = QRectF(rect.left(), y, rect.width(), line_heights[index])
            rows.append({
                "text": text,
                "rect": row,
                "size": sizes[index],
                "active": actives[index],
                "opacity": 1.0,
                "color_fill": index == 0,
                "progress_fill": dual and index == 1,
                "spacing_percent": None if index == 0 or (dual and index == 1) else 100,
                "transition_out": False,
            })
            y = row.bottom() + gap
        out_alpha = max(0.0, min(1.0, float(state.get("out_alpha", 0.0))))
        previous_index = int(getattr(self, "prev_lyric_index", int(getattr(self, "lyric_index", 0))))
        current_index = int(getattr(self, "lyric_index", 0))
        previous_text = self._gelv_primary_text_for_index(previous_index)
        moving_switch = out_alpha > 0.001 and previous_index != current_index and not dual and len(rows) >= 2
        if moving_switch:
            eased = max(0.0, min(1.0, float(state.get("eased", 1.0))))
            upper_rect = QRectF(rows[0]["rect"])
            lower_rect = QRectF(rows[1]["rect"])
            incoming = QRectF(upper_rect)
            incoming.moveTop(lower_rect.top() + (upper_rect.top() - lower_rect.top()) * eased)
            rows[0]["rect"] = incoming
            enter_offset = (lower_rect.height() + gap) * (1.0 - eased)
            entering_next = QRectF(lower_rect)
            entering_next.moveTop(lower_rect.top() + enter_offset)
            rows[1]["rect"] = entering_next
            rows[1]["opacity"] = max(0.42, eased)
        if out_alpha > 0.001 and previous_index != current_index and previous_text and rows:
            out_size_lift = 1.6 * (1.0 - float(state.get("eased", 1.0))) if bool(getattr(self, "gelv_size_shift_enabled", True)) else 0.0
            out_rect = QRectF(rows[0]["rect"])
            if moving_switch:
                eased = max(0.0, min(1.0, float(state.get("eased", 1.0))))
                upper_rect = QRectF(rect.left(), rect.center().y() - total_h / 2.0, rect.width(), line_heights[0])
                out_rect = QRectF(upper_rect)
                out_rect.moveTop(upper_rect.top() - (upper_rect.height() + gap) * eased)
            out_row = dict(rows[0])
            out_row.update({
                "text": previous_text,
                "rect": out_rect,
                "size": active_size + out_size_lift,
                "opacity": out_alpha,
                "color_fill": True,
                "spacing_percent": None,
                "transition_out": True,
            })
            rows.insert(0, out_row)
        return rows

    def _gelv_spacing_progress(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else float(now)
        phase = self._lyric_phase(now)
        if "progress" in phase:
            return max(0.0, min(1.0, float(phase.get("progress", 0.0))))
        timed = getattr(self, "timed_lyrics", []) or []
        index = int(getattr(self, "lyric_index", 0))
        if timed and 0 <= index < len(timed):
            position = max(0.0, self._estimated_media_position(now) + float(getattr(self, "lyric_offset_seconds", 0.0)))
            start = float(timed[index][0])
            if index + 1 < len(timed):
                end = float(timed[index + 1][0])
            else:
                end = max(start + 1.0, float(getattr(self, "media_duration_seconds", 0.0) or 0.0))
            if end > start:
                return max(0.0, min(1.0, (position - start) / (end - start)))
        changed_at = float(getattr(self, "lyric_changed_at", now))
        return max(0.0, min(1.0, (now - changed_at) / 3.0))

    def _lyric_fill_color(self, fallback_attr: str = "gelv_color_fill_fallback") -> QColor:
        album = QColor(getattr(self, "album_color", QColor(0, 215, 255)))
        if album.saturation() < 28 or album.value() < 28 or album.value() > 238:
            fallback = QColor(str(getattr(self, fallback_attr, "#00D7FF")))
            return fallback if fallback.isValid() else QColor("#00D7FF")
        hsv = album.toHsv()
        hsv.setHsv(hsv.hue(), max(190, hsv.saturation()), max(210, hsv.value()), 255)
        return hsv.toRgb()

    def _gelv_fill_color(self) -> QColor:
        return self._lyric_fill_color("gelv_color_fill_fallback")

    def _wave_fill_color(self) -> QColor:
        return self._lyric_fill_color("wave_color_fill_fallback")

    def _should_paint_album_island(self) -> bool:
        if str(getattr(self, "lyric_flow_style", "wave_stream")) == "album_island":
            return True
        if str(getattr(self, "no_lyrics_display_mode", "text")) != "album_island":
            return False
        if self._is_instrumental_track():
            return True
        lyrics = [str(line).strip() for line in getattr(self, "lyrics", []) if str(line).strip()]
        if not lyrics:
            return True
        no_text = str(getattr(self, "no_lyrics_text", "暂无歌词")).strip()
        return len(lyrics) == 1 and lyrics[0] == no_text

    def _album_island_slots(self, rect: QRectF, reserve_text: bool = False) -> list[dict[str, object]]:
        count = int(getattr(self, "album_island_cover_count", 5))
        if count not in (3, 5, 7):
            count = 5
        center = count // 2
        stage = QRectF(rect)
        transition = self._album_island_transition_progress()
        eased_transition = 1.0 - (1.0 - transition) ** 4
        history_entries = self._album_island_entries_with_art(getattr(self, "album_history", []) or [])
        queue_entries = self._album_island_entries_with_art(
            getattr(self, "album_queue", []) or getattr(self, "album_recommendations", []) or []
        )
        left_count = min(center, len(history_entries))
        right_count = min(center, len(queue_entries))
        if reserve_text and right_count <= 0:
            text_reserve = min(stage.width() * 0.42, self._u(126.0 if stage.height() < self._u(82.0) else 184.0))
            stage.setRight(max(stage.left() + self._u(76.0), stage.right() - text_reserve))
        cover = self._album_island_cover_px(stage)
        step = self._album_island_step_px(cover, stage, center)
        center_x = stage.center().x()
        center_y = stage.center().y()
        secondary_style = str(getattr(self, "album_island_secondary_style", "perspective_dim") or "perspective_dim")
        if secondary_style not in self.ALBUM_ISLAND_SECONDARY_STYLES:
            secondary_style = "perspective_dim"
        perspective_amount = max(0, min(200, int(getattr(self, "album_island_perspective_amount", 100)))) / 100.0
        sides = list(range(-left_count, 0)) + [0] + list(range(1, right_count + 1))
        slots: list[dict[str, object]] = []
        for side in sides:
            distance = abs(side)
            perspective = None
            if secondary_style == "perspective_dim":
                scale = max(0.78, 1.0 - 0.08 * distance)
                alpha = max(92, 255 - 48 * distance)
                squeeze = min(0.42, (0.10 + 0.055 * distance) * perspective_amount)
                if side < 0:
                    perspective = {"left_height": 1.0, "right_height": 1.0 - squeeze}
                elif side > 0:
                    perspective = {"left_height": 1.0 - squeeze, "right_height": 1.0}
                tilt = 0.0
            else:
                scale = max(0.48, 1.0 - 0.16 * distance)
                alpha = max(76, 255 - 56 * distance)
                tilt = 0.0
            size = cover * scale
            animated_side = float(side)
            if eased_transition < 1.0:
                animated_side += float(getattr(self, "album_island_transition_direction", 1) or 1) * (1.0 - eased_transition)
            slot_center_x = center_x + animated_side * step
            x = slot_center_x - size / 2.0
            y = center_y - size / 2.0 + distance * stage.height() * 0.020
            if side == 0:
                album_art = QImage(getattr(self, "album_art", QImage()))
            elif side < 0:
                album_art = history_entries[abs(side) - 1]
            else:
                album_art = queue_entries[side - 1]
            slots.append(
                {
                    "index": side + center,
                    "side": side,
                    "rect": QRectF(x, y, size, size),
                    "alpha": alpha,
                    "center": side == 0,
                    "z": 100 - distance * 12,
                    "tilt": tilt,
                    "perspective": perspective,
                    "album_art": album_art,
                }
            )
        if slots:
            leftmost = min(QRectF(slot["rect"]).left() for slot in slots)
            if leftmost < stage.left():
                center_slot = next((slot for slot in slots if bool(slot.get("center"))), None)
                current_center_x = QRectF(center_slot["rect"]).center().x() if center_slot is not None else center_x
                max_shift = max(0.0, rect.center().x() - current_center_x)
                shift = min(stage.left() - leftmost, max_shift)
                if shift > 0.0:
                    for slot in slots:
                        moved = QRectF(slot["rect"])
                        moved.translate(shift, 0.0)
                        slot["rect"] = moved
        return slots

    def _album_island_entries_with_art(self, entries) -> list[QImage]:
        images: list[QImage] = []
        for entry in list(entries or [])[:6]:
            image = self._album_art_from_entry(entry)
            if not image.isNull():
                images.append(image)
        return images

    def _album_island_cover_px(self, stage: QRectF) -> float:
        configured = max(18, min(44, int(getattr(self, "album_island_cover_size", 30))))
        return min(self._u(float(configured)), stage.height() * 0.84)

    def _album_island_step_px(self, cover: float, stage: QRectF, side_capacity: int) -> float:
        spacing = max(48, min(170, int(getattr(self, "album_island_spacing", 112))))
        desired = cover * (spacing / 100.0)
        max_step = stage.width() / max(1.0, side_capacity * 2.0 + 1.0)
        return max(cover * 0.72, min(desired, max_step * 1.18))

    def _album_island_transition_progress(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else float(now)
        started_at = float(getattr(self, "album_island_transition_started_at", 0.0) or 0.0)
        if started_at <= 0.0:
            started_at = float(getattr(self, "audio_started_at", 0.0) or 0.0)
        if started_at <= 0.0:
            return 1.0
        return max(0.0, min(1.0, (now - started_at) / 0.62))

    def _album_island_preferred_stage_width(self) -> float:
        count = int(getattr(self, "album_island_cover_count", 5))
        if count not in (3, 5, 7):
            count = 5
        side_capacity = count // 2
        cover = self._u(float(max(18, min(44, int(getattr(self, "album_island_cover_size", 30))))))
        step = cover * (max(48, min(170, int(getattr(self, "album_island_spacing", 112)))) / 100.0)
        history_count = min(side_capacity, len(self._album_island_entries_with_art(getattr(self, "album_history", []) or [])))
        queue_count = min(side_capacity, len(self._album_island_entries_with_art(getattr(self, "album_queue", []) or [])))
        text_w = self._u(112.0) if queue_count <= 0 else 0.0
        span = cover + step * (history_count + queue_count) + text_w + self._u(24.0)
        minimum = self._u(164.0 if queue_count <= 0 else 132.0)
        return max(minimum, span)

    def _album_island_art_for_side(self, side: int, center: int) -> QImage:
        if side == 0:
            return QImage(getattr(self, "album_art", QImage()))
        history = list(getattr(self, "album_history", []) or [])
        queue = list(getattr(self, "album_queue", []) or getattr(self, "album_recommendations", []) or [])
        if side < 0:
            return self._album_art_from_entry(history[abs(side) - 1] if abs(side) - 1 < len(history) else None)
        return self._album_art_from_entry(queue[side - 1] if side - 1 < len(queue) else None)

    def _album_queue_from_track(self, entries) -> list[dict[str, object]]:
        queue: list[dict[str, object]] = []
        for entry in list(entries or [])[:6]:
            image = self._album_art_from_entry(entry)
            if image.isNull():
                break
            queue.append(
                {
                    "track_id": str(getattr(entry, "track_id", "") or ""),
                    "title": str(getattr(entry, "title", "") or ""),
                    "artist": str(getattr(entry, "artist", "") or ""),
                    "album_title": str(getattr(entry, "album_title", "") or ""),
                    "album_art": image,
                }
            )
        return queue

    def _album_art_from_entry(self, entry) -> QImage:
        if isinstance(entry, QImage):
            return QImage(entry)
        if isinstance(entry, dict):
            image = entry.get("album_art") or entry.get("image")
            if isinstance(image, QImage):
                return QImage(image)
            data = entry.get("album_art_bytes") or entry.get("image_bytes")
            if isinstance(data, bytes):
                loaded = QImage()
                if loaded.loadFromData(data) and not loaded.isNull():
                    return loaded
        image = getattr(entry, "album_art", None)
        if isinstance(image, QImage):
            return QImage(image)
        data = getattr(entry, "album_art_bytes", b"") or getattr(entry, "image_bytes", b"") or b""
        if isinstance(data, bytes) and data:
            loaded = QImage()
            if loaded.loadFromData(data) and not loaded.isNull():
                return loaded
        return QImage()

    def _album_island_listened_text(self) -> str:
        seconds = max(0.0, float(getattr(self, "listened_seconds_total", 0.0)))
        hours = int(seconds / 3600.0)
        return f"您已听歌 {hours} 小时"

    def _current_album_history_entry(self) -> dict[str, object]:
        return {
            "track_id": str(getattr(self, "media_track_id", "") or ""),
            "title": str(getattr(self, "track_title", "") or ""),
            "artist": str(getattr(self, "artist", "") or ""),
            "album_art": getattr(self, "album_art", QImage()),
        }

    def _entry_matches_track_id(self, entry, track_id: str) -> bool:
        candidate = ""
        if isinstance(entry, dict):
            candidate = str(entry.get("track_id", "") or "")
        else:
            candidate = str(getattr(entry, "track_id", "") or "")
        return bool(candidate) and candidate == str(track_id or "")

    def _prepare_album_flow_for_track_change(self, incoming_track_id: str) -> int:
        incoming_track_id = str(incoming_track_id or "")
        history = list(getattr(self, "album_history", []) or [])
        queue = list(getattr(self, "album_queue", []) or [])
        previous_id = str(getattr(self, "media_track_id", "") or "")
        if previous_id and incoming_track_id and previous_id != incoming_track_id:
            history_index = next(
                (index for index, entry in enumerate(history) if self._entry_matches_track_id(entry, incoming_track_id)),
                -1,
            )
            current_entry = self._current_album_history_entry()
            if history_index >= 0:
                history.pop(history_index)
                queue = [current_entry] + [entry for entry in queue if not self._entry_matches_track_id(entry, previous_id)]
                self.album_history = history[:6]
                self.album_queue = queue[:6]
                return -1
            history.insert(0, current_entry)
        self.album_history = history[:6]
        return 1

    def _remember_album_history(self, incoming_track_id: str):
        history = list(getattr(self, "album_history", []))
        previous_id = str(getattr(self, "media_track_id", "") or "")
        if previous_id and previous_id != str(incoming_track_id or ""):
            history.insert(
                0,
                {
                    "track_id": previous_id,
                    "title": str(getattr(self, "track_title", "") or ""),
                    "album_art": getattr(self, "album_art", QImage()),
                },
            )
        self.album_history = history[:6]

    def _current_lyric_display_lines(self) -> list[str]:
        if not self.lyrics:
            return []
        index = self.lyric_index % len(self.lyrics)
        return self._lyric_display_lines(self.lyrics[index])

    def _has_current_two_line_lyric(self) -> bool:
        return len(self._current_lyric_display_lines()) > 1

    def _clean_lyric_for_width(self, lyric: str) -> str:
        noise_tokens = ["作词", "作曲", "编曲", "制作", "混音", "母带", "吉他", "贝斯", "鼓", "小提琴", "和声", "录音"]
        for token in noise_tokens:
            if token in lyric:
                lyric = lyric.split(token, 1)[0]
        return "\n".join(" ".join(part.strip(" -—·:：[]【】()（）").split()) for part in str(lyric).splitlines()).strip()

    def _stack_target_width(self, main_kind: IslandKind) -> float:
        if main_kind == IslandKind.MUSIC:
            return self._music_target_width(self.expanded)
        return self._u(float(self.STACK_WIDTH))

    def _current_screen_geometry(self):
        screen = QGuiApplication.screenAt(self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2))) or QGuiApplication.primaryScreen()
        return screen.geometry() if screen else None

    def _expanded_stack_height(self, main_kind: IslandKind, main_h: float, include_popups: bool) -> float:
        gap = self._u(10.0)
        height = main_h
        if self.expanded and self._plugin_enabled():
            panel_visible = self._plugin_panel_visible()
            if main_kind == IslandKind.MUSIC:
                height += gap + self._u(48.0)
                if panel_visible:
                    height += gap + self._plugin_panel_height(self._stack_target_width(main_kind))
            elif main_kind == IslandKind.PLUGIN and panel_visible:
                height += gap + self._plugin_panel_height(self._stack_target_width(main_kind))

        if self.expanded:
            if main_kind == IslandKind.MESSAGE:
                message_count = max(0, len(self.messages[1 : 1 + self._folded_message_limit(main_kind)]))
            else:
                message_count = len(self.messages)
            if message_count:
                height += gap + self._u(48.0) * message_count + gap * max(0, message_count - 1)
        elif include_popups:
            popup_count = 0 if main_kind == IslandKind.MESSAGE else min(1, len([m for m in self.messages if time.monotonic() < m.popup_until]))
            if popup_count:
                height += gap + self._u(46.0) * popup_count
        return height

    def _standalone_width_limit(self, center_x: float, screen_geo) -> float:
        target_width = self._stack_target_width(self._main_kind())
        if not screen_geo:
            return target_width
        global_center_x = self.mapToGlobal(QPoint(int(round(center_x)), 0)).x()
        return self._standalone_width_limit_for_global_center(global_center_x, screen_geo, target_width)

    def _standalone_width_limit_for_global_center(self, global_center_x: float, screen_geo, target_width: float | None = None) -> float:
        if target_width is None:
            target_width = self._stack_target_width(self._main_kind())
        if not screen_geo:
            return target_width
        left_space = max(0.0, float(global_center_x - screen_geo.left()))
        right_space = max(0.0, float(screen_geo.right() - global_center_x))
        limit = min(left_space, right_space) * 2.0 - self._u(28.0)
        min_width = max(self._u(120.0), target_width * 0.5)
        return max(min_width, min(target_width, limit if limit > 0 else min_width))

    def _main_size_for_kind(self, main_kind: IslandKind, stack_w: float) -> tuple[float, float]:
        if main_kind == IslandKind.MUSIC:
            if self.expanded:
                return stack_w, self._u(176.0)
            if self._should_paint_album_island():
                height = self._album_island_compact_music_height()
            elif self._gelv_enabled():
                height = self._gelv_compact_music_height()
            else:
                height = 80.0 if self._has_current_two_line_lyric() else 46.0
            return stack_w, self._u(height)
        if main_kind == IslandKind.PLUGIN:
            if self.expanded:
                return stack_w, self._u(48.0)
            base_plugin_width = self._u(420.0)
            return base_plugin_width / 3.0 * max(1, self.enabled_plugin_count), self._u(48.0)
        if main_kind == IslandKind.MESSAGE:
            return (stack_w if self.expanded else min(self._u(430.0), stack_w)), self._u(48.0)
        return float(getattr(self, "empty_bar_width", 92)), float(getattr(self, "empty_bar_height", 14))

    def _time_panel_breakpoint(self) -> float:
        return self._u(560.0)

    def _time_panel_stretch_breakpoint(self) -> float:
        return self._u(960.0)

    def _time_panel_modules(self) -> list[str]:
        return ["countdown", "alarm"]

    def _time_visible_module_for_width(self, width: float) -> str:
        if float(width or 0.0) >= self._time_panel_breakpoint():
            return "all"
        module = str(getattr(self, "plugin_time_visible_module", "countdown") or "countdown")
        return module if module in self._time_panel_modules() else "countdown"

    def _plugin_panel_height(self, width: float | None = None) -> float:
        mode = str(getattr(self, "plugin_time_mode", "dashboard") or "dashboard")
        if mode == "alarm_editor":
            return self._u(190.0)
        panel_w = float(width if width is not None else getattr(self, "plugin_panel_last_width", 0.0) or 0.0)
        if panel_w > 0.0 and panel_w < self._time_panel_breakpoint():
            return self._u(156.0)
        return self._u(236.0)

    def _plugin_panel_visible(self) -> bool:
        return bool(self.expanded and self._plugin_enabled() and getattr(self, "plugin_panel_open", False))

    def _main_top_for_direction(self, main_kind: IslandKind, main_h: float, direction: int) -> float:
        top_y = self._finder_stack_top_y()
        if main_kind == IslandKind.THIN_BAR:
            top_y = max(0.0, top_y - self._u(2.0) - 2.0 + float(getattr(self, "empty_bar_y_offset", 0)))
        if self.finder_mode or direction >= 0:
            return top_y
        return max(0.0, self.height() - top_y - main_h)

    def _standalone_drag_direction(self, global_pos: QPoint) -> int:
        mode = str(getattr(self, "expand_direction", "auto") or "auto").lower()
        if mode == "down":
            return 1
        if mode == "up":
            return -1
        if not getattr(self, "auto_expand_bounds", True):
            return 1
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        if not screen:
            return 1
        geo = screen.geometry()
        cutoff = geo.top() + geo.height() * (2.0 / 3.0)
        return -1 if global_pos.y() >= cutoff else 1

    def _vertical_stack_direction(self, main_kind: IslandKind, main_h: float, screen_geo) -> int:
        if self.finder_mode:
            return 1
        mode = str(getattr(self, "expand_direction", "auto") or "auto").lower()
        if mode == "down":
            return 1
        if mode == "up":
            return -1
        if not getattr(self, "auto_expand_bounds", True) or not screen_geo:
            return 1
        local_top = self._main_top_for_direction(main_kind, main_h, getattr(self, "stack_direction", 1))
        global_main_y = self.mapToGlobal(QPoint(0, 0)).y() + local_top + main_h / 2.0
        screen_cutoff = screen_geo.top() + screen_geo.height() * (2.0 / 3.0)
        if global_main_y >= screen_cutoff:
            return -1
        if not self.expanded:
            return 1
        down_height = self._expanded_stack_height(main_kind, main_h, include_popups=False)
        global_main_top = self.mapToGlobal(QPoint(0, 0)).y() + self._main_top_for_direction(main_kind, main_h, 1)
        if global_main_top + down_height > screen_geo.bottom() - self._u(14.0):
            return -1
        return 1

    def _stack_rect_for_direction(self, center_x: float, cursor_edge: float, width: float, height: float, direction: int, gap: float) -> tuple[QRectF, float]:
        if direction >= 0:
            rect = QRectF(center_x - width / 2.0, cursor_edge, width, height)
            return rect, rect.bottom() + gap
        rect = QRectF(center_x - width / 2.0, cursor_edge - height, width, height)
        return rect, rect.top() - gap

    def _finder_content_gap(self) -> float:
        return max(self._u(8.0), min(self._u(14.0), self.finder_material.bar_height * 0.38 * self.VISUAL_SCALE))

    def _finder_stack_top_y(self) -> float:
        if not self.finder_mode:
            return self._u(72.0)
        return self._finder_content_gap()

    def _stack_center_x(self, content_width: float) -> float:
        if not self.finder_mode:
            return self.width() / 2.0
        width = float(self.width())
        margin = max(content_width / 2.0 + self._u(24.0), self._u(68.0))
        ratio = max(0.06, min(0.94, float(getattr(self, "stack_center_ratio", 0.5))))
        return max(margin, min(width - margin, width * ratio))

    def _build_visuals(self) -> list[StackVisual]:
        now = time.monotonic()
        main_kind = self._main_kind()
        screen_geo = self._current_screen_geometry()
        target_stack_w = self._stack_target_width(main_kind)
        stack_w = target_stack_w
        center_x = self._stack_center_x(stack_w)
        if not self.finder_mode:
            stack_w = min(stack_w, self._standalone_width_limit(center_x, screen_geo))
            stack_w = max(target_stack_w * 0.5, stack_w)
        self.font_shrink_scale = 1.0
        gap = self._u(10)
        visuals: list[StackVisual] = []
        _, direction_probe_h = self._main_size_for_kind(main_kind, stack_w)
        direction = self._vertical_stack_direction(main_kind, direction_probe_h, screen_geo)
        self.stack_direction = direction
        main_w, main_h = self._main_size_for_kind(main_kind, stack_w)

        if main_kind == IslandKind.MUSIC:
            main_key = "music"
            message = None
        elif main_kind == IslandKind.PLUGIN:
            main_key = "plugin"
            message = None
        elif main_kind == IslandKind.MESSAGE:
            message = self.messages[0]
            main_key = f"message:{self._message_key(message)}"
        else:
            main_key = "thin"
            message = None

        main_top = self._main_top_for_direction(main_kind, main_h, direction)
        main_rect = QRectF(center_x - main_w / 2, main_top, main_w, main_h)
        visuals.append(StackVisual(main_key, main_kind, main_rect, False, message, 10))
        cursor_edge = main_rect.bottom() + gap if direction >= 0 else main_rect.top() - gap

        plugin_alerts = self._active_plugin_alerts(2)
        if plugin_alerts:
            for alert_slot, plugin_alert in enumerate(plugin_alerts):
                _index, _label, _detail, _summary, _color, _icon, plugin_id, _status, _alert_title, _alert_body, _alert_priority = plugin_alert
                alert_rect = self._plugin_alert_rect(main_rect, alert_slot, len(plugin_alerts))
                visuals.append(StackVisual(f"plugin-alert:{plugin_id}", IslandKind.PLUGIN_ALERT, alert_rect, False, None, 8))

        panel_visible = self._plugin_panel_visible()
        if self.expanded and self._plugin_enabled() and main_kind == IslandKind.MUSIC:
            rect, cursor_edge = self._stack_rect_for_direction(center_x, cursor_edge, stack_w, self._u(48), direction, gap)
            visuals.append(StackVisual("plugin", IslandKind.PLUGIN, rect, False, None, 6))
            if panel_visible:
                panel_edge = rect.bottom() if direction >= 0 else rect.top()
                rect, cursor_edge = self._stack_rect_for_direction(center_x, panel_edge, stack_w, self._plugin_panel_height(stack_w), direction, gap)
                visuals.append(StackVisual("plugin-panel", IslandKind.PLUGIN_PANEL, rect, False, None, 5))
        elif self.expanded and self._plugin_enabled() and main_kind == IslandKind.PLUGIN and panel_visible:
            strip_rect = main_rect
            panel_edge = strip_rect.bottom() if direction >= 0 else strip_rect.top()
            rect, cursor_edge = self._stack_rect_for_direction(center_x, panel_edge, stack_w, self._plugin_panel_height(stack_w), direction, gap)
            visuals.append(StackVisual("plugin-panel", IslandKind.PLUGIN_PANEL, rect, False, None, 5))

        if self.expanded:
            expanded_messages = self.messages
            if main_kind == IslandKind.MESSAGE:
                expanded_messages = self.messages[1 : 1 + self._folded_message_limit(main_kind)]
            for index, msg in enumerate(expanded_messages):
                key = f"message:{self._message_key(msg)}"
                rect, cursor_edge = self._stack_rect_for_direction(center_x, cursor_edge, stack_w, self._u(48), direction, gap)
                visuals.append(StackVisual(key, IslandKind.MESSAGE, rect, False, msg, max(1, 6 - index)))
            return visuals

        if main_kind == IslandKind.MESSAGE:
            dot_messages = self.messages[1 : 1 + self._folded_message_limit(main_kind)]
        else:
            dot_messages = self.messages[: self._folded_message_limit(main_kind)]
        for dot_index, msg in enumerate(dot_messages):
            key = f"message-dot:{self._message_key(msg)}"
            rect = self._message_dot_rect(main_rect, dot_index, len(dot_messages), direction)
            visuals.append(StackVisual(key, IslandKind.MESSAGE, rect, True, msg, 5 - dot_index))

        dot_tray_edge = main_rect.bottom() if direction >= 0 else main_rect.top()
        if dot_messages:
            dot_rects = [self._message_dot_rect(main_rect, i, len(dot_messages), direction) for i in range(len(dot_messages))]
            dot_tray = self._dot_tray_rect(dot_rects)
            if dot_tray:
                dot_tray_edge = dot_tray.bottom() if direction >= 0 else dot_tray.top()

        popup_messages = [m for m in self.messages if now < m.popup_until]
        if not self.expanded:
            popup_start_edge = cursor_edge
            if dot_messages:
                popup_start_edge = max(popup_start_edge, dot_tray_edge + self._u(12.0)) if direction >= 0 else min(popup_start_edge, dot_tray_edge - self._u(12.0))
            popup_candidates = []
            for msg in popup_messages:
                if main_kind == IslandKind.MESSAGE and self.messages and msg is self.messages[0]:
                    continue
                popup_candidates.append(msg)
            popup_limit = 0 if main_kind == IslandKind.MESSAGE else 1
            for index, msg in enumerate(popup_candidates[:popup_limit]):
                key = f"message:{self._message_key(msg)}"
                pop_width = main_w if main_kind != IslandKind.THIN_BAR else self._u(430)
                if direction >= 0:
                    y = popup_start_edge + index * self._u(54.0)
                else:
                    y = popup_start_edge - self._u(46.0) - index * self._u(54.0)
                rect = QRectF(center_x - pop_width / 2, y, pop_width, self._u(46))
                visuals.append(StackVisual(key, IslandKind.MESSAGE, rect, False, msg, 3 - index))

        return visuals

    def _plugin_alert_rect(self, main_rect: QRectF, slot: int = 0, total: int = 1) -> QRectF:
        width = self._u(float(getattr(self, "plugin_alert_width", 168.0)))
        try:
            alerts = self._active_plugin_alerts(total)
            if 0 <= int(slot) < len(alerts):
                _index, label, detail, summary, _color, _icon, _plugin_id, _status, _alert_title, alert_body, _alert_priority = alerts[int(slot)]
                content_width = self._plugin_alert_width_for(label, detail, alert_body or summary)
                if str(label or "").strip().upper() == "TIME":
                    width = content_width
                else:
                    width = max(width, content_width)
        except Exception:
            pass
        height = self._u(42.0)
        gap = self._u(10.0)
        screen_left, screen_right = self._screen_bounds_in_local()
        min_width = self._u(128.0)
        right_x = main_rect.right() + gap
        left_x = main_rect.left() - gap - width
        right_space = max(0.0, screen_right - right_x)
        left_space = max(0.0, main_rect.left() - gap - screen_left)
        pressure = self._main_rect_screen_pressure(main_rect, screen_left, screen_right)
        total = max(1, int(total))
        slot = max(0, min(int(slot), total - 1))
        row_gap = self._u(7.0)
        side = "right"
        if pressure:
            width = self._plugin_alert_circle_size(height)
            preferred = "left" if pressure == "right" else "right"
            side = preferred
            x = self._plugin_alert_x_for_side(main_rect, width, gap, preferred)
            if not self._rect_x_fits_screen(x, width, screen_left, screen_right):
                fallback_side = "right" if preferred == "left" else "left"
                side = fallback_side
                x = self._plugin_alert_x_for_side(main_rect, width, gap, fallback_side)
            x = self._clamp_rect_x_to_screen(x, width, screen_left, screen_right)
        elif self._rect_x_fits_screen(right_x, width, screen_left, screen_right):
            side = "right"
            x = right_x
        elif self._rect_x_fits_screen(left_x, width, screen_left, screen_right):
            side = "left"
            x = left_x
        elif right_space >= left_space and right_space >= min_width:
            side = "right"
            width = max(min_width, min(width, right_space))
            x = right_x
        elif left_space >= min_width:
            side = "left"
            width = max(min_width, min(width, left_space))
            x = main_rect.left() - gap - width
        else:
            side = "right" if right_space >= left_space else "left"
            usable = max(min_width, screen_right - screen_left)
            width = min(width, usable)
            x = self._clamp_rect_x_to_screen(main_rect.left() - gap - width, width, screen_left, screen_right)
        if total > 1:
            try:
                alerts_for_row = self._active_plugin_alerts(total)
            except Exception:
                alerts_for_row = []
            same_time_row = len(alerts_for_row) >= total and all(
                str(alert[1] or "").strip().upper() == "TIME" or str(alert[6] or "").strip().casefold().startswith("time")
                for alert in alerts_for_row[:total]
            )
            row_width = self._plugin_alert_row_desired_width(total, width)
            side = self._plugin_alert_side_for_row(main_rect, row_width, gap, row_gap, total, side, screen_left, screen_right)
            split_sides = False
            alternate = "left" if side == "right" else "right"
            row_fits_side = self._plugin_alert_row_fits(main_rect, row_width, gap, row_gap, total, side, screen_left, screen_right)
            row_fits_alternate = self._plugin_alert_row_fits(main_rect, row_width, gap, row_gap, total, alternate, screen_left, screen_right)
            right_single = self._rect_x_fits_screen(self._plugin_alert_x_for_side(main_rect, row_width, gap, "right"), row_width, screen_left, screen_right)
            left_single = self._rect_x_fits_screen(self._plugin_alert_x_for_side(main_rect, row_width, gap, "left"), row_width, screen_left, screen_right)
            if total == 2 and same_time_row and not row_fits_side and not row_fits_alternate:
                width = self._plugin_alert_circle_size(height)
                side = "right" if right_space >= left_space else "left"
                if not self._plugin_alert_row_fits(main_rect, width, gap, row_gap, total, side, screen_left, screen_right):
                    alternate = "left" if side == "right" else "right"
                    if self._plugin_alert_row_fits(main_rect, width, gap, row_gap, total, alternate, screen_left, screen_right):
                        side = alternate
            elif total == 2 and not pressure and not row_fits_side and not row_fits_alternate and right_single and left_single:
                width = row_width
                side = "right" if slot == 0 else "left"
                x = self._plugin_alert_x_for_side(main_rect, width, gap, side)
                split_sides = True
            else:
                width = self._plugin_alert_width_for_row(main_rect, row_width, gap, row_gap, total, side, screen_left, screen_right)
            if not split_sides and self._plugin_alert_uses_ring(QRectF(0.0, 0.0, width, height)):
                alternate = "left" if side == "right" else "right"
                row_fits_side = self._plugin_alert_row_fits(main_rect, width, gap, row_gap, total, side, screen_left, screen_right)
                row_fits_alternate = self._plugin_alert_row_fits(main_rect, width, gap, row_gap, total, alternate, screen_left, screen_right)
                right_single = self._rect_x_fits_screen(self._plugin_alert_x_for_side(main_rect, width, gap, "right"), width, screen_left, screen_right)
                left_single = self._rect_x_fits_screen(self._plugin_alert_x_for_side(main_rect, width, gap, "left"), width, screen_left, screen_right)
                if total == 2 and not same_time_row and not row_fits_side and not row_fits_alternate and right_single and left_single:
                    side = "right" if slot == 0 else "left"
                    x = self._plugin_alert_x_for_side(main_rect, width, gap, side)
                    split_sides = True
                else:
                    side = self._plugin_alert_side_for_row(main_rect, width, gap, row_gap, total, side, screen_left, screen_right)
            if not split_sides:
                x = self._plugin_alert_x_for_slot(main_rect, width, gap, row_gap, side, slot)
            x = self._clamp_rect_x_to_screen(x, width, screen_left, screen_right)
        y = main_rect.center().y() - height / 2.0
        y = max(self._u(8.0), min(float(self.height()) - height - self._u(8.0), y))
        return QRectF(x, y, width, height)

    def _screen_bounds_in_local(self) -> tuple[float, float]:
        margin = self._u(8.0)
        fallback = (margin, max(margin, float(self.width()) - margin))
        try:
            screen_geo = self._current_screen_geometry()
        except Exception:
            screen_geo = None
        if not screen_geo:
            return fallback
        try:
            origin = self.mapToGlobal(QPoint(0, 0))
            left = float(screen_geo.left() - origin.x()) + margin
            right = float(screen_geo.right() - origin.x()) - margin
        except Exception:
            return fallback
        left = max(left, margin)
        right = min(right, float(self.width()) - margin)
        if right - left < self._u(80.0):
            return fallback
        return left, right

    def _main_rect_screen_pressure(self, main_rect: QRectF, screen_left: float, screen_right: float) -> str:
        try:
            main_kind = self._main_kind()
            target_width = self._main_size_for_kind(main_kind, self._stack_target_width(main_kind))[0]
        except Exception:
            try:
                target_width = self._stack_target_width(self._main_kind())
            except Exception:
                target_width = main_rect.width()
        is_shrunk = main_rect.width() < target_width - self._u(2.0)
        if not is_shrunk:
            return ""
        if main_rect.right() > screen_right + self._u(1.0):
            return "right"
        if main_rect.left() < screen_left - self._u(1.0):
            return "left"
        return ""

    def _plugin_alert_circle_size(self, height: float) -> float:
        return max(self._u(34.0), float(height))

    def _plugin_alert_x_for_side(self, main_rect: QRectF, width: float, gap: float, side: str) -> float:
        if side == "left":
            return main_rect.left() - gap - width
        return main_rect.right() + gap

    def _plugin_alert_side_for_row(
        self,
        main_rect: QRectF,
        width: float,
        gap: float,
        row_gap: float,
        total: int,
        preferred_side: str,
        screen_left: float,
        screen_right: float,
    ) -> str:
        preferred = "left" if preferred_side == "left" else "right"
        alternate = "right" if preferred == "left" else "left"
        if self._plugin_alert_row_fits(main_rect, width, gap, row_gap, total, preferred, screen_left, screen_right):
            return preferred
        if self._plugin_alert_row_fits(main_rect, width, gap, row_gap, total, alternate, screen_left, screen_right):
            return alternate
        preferred_space = self._plugin_alert_side_space(main_rect, gap, preferred, screen_left, screen_right)
        alternate_space = self._plugin_alert_side_space(main_rect, gap, alternate, screen_left, screen_right)
        return preferred if preferred_space >= alternate_space else alternate

    def _plugin_alert_row_desired_width(self, total: int, fallback_width: float) -> float:
        row_width = float(fallback_width)
        try:
            alerts = self._active_plugin_alerts(max(1, int(total)))
        except Exception:
            alerts = []
        base_width = self._u(float(getattr(self, "plugin_alert_width", 168.0)))
        for alert in alerts[: max(1, int(total))]:
            try:
                _index, label, detail, summary, _color, _icon, _plugin_id, _status, _alert_title, alert_body, _alert_priority = alert
            except (TypeError, ValueError):
                continue
            content_width = self._plugin_alert_width_for(label, detail, alert_body or summary)
            if str(label or "").strip().upper() == "TIME":
                desired = content_width
            else:
                desired = max(base_width, content_width)
            row_width = max(row_width, desired)
        return row_width

    def _plugin_alert_width_for_row(
        self,
        main_rect: QRectF,
        width: float,
        gap: float,
        row_gap: float,
        total: int,
        side: str,
        screen_left: float,
        screen_right: float,
    ) -> float:
        total = max(1, int(total))
        if total <= 1:
            return width
        space = self._plugin_alert_side_space(main_rect, gap, side, screen_left, screen_right)
        available = max(0.0, space - row_gap * (total - 1))
        compact_min = self._plugin_alert_circle_size(self._u(42.0))
        if available <= 0.0:
            return compact_min
        item_width = min(width, available / total)
        if item_width < width - self._u(1.0):
            return compact_min
        return item_width

    def _plugin_alert_x_for_slot(self, main_rect: QRectF, width: float, gap: float, row_gap: float, side: str, slot: int) -> float:
        slot = max(0, int(slot))
        if side == "left":
            return main_rect.left() - gap - width - slot * (width + row_gap)
        return main_rect.right() + gap + slot * (width + row_gap)

    def _plugin_alert_row_fits(
        self,
        main_rect: QRectF,
        width: float,
        gap: float,
        row_gap: float,
        total: int,
        side: str,
        screen_left: float,
        screen_right: float,
    ) -> bool:
        row_width = width * max(1, int(total)) + row_gap * max(0, int(total) - 1)
        if side == "left":
            return main_rect.left() - gap - row_width >= screen_left
        return main_rect.right() + gap + row_width <= screen_right

    def _plugin_alert_side_space(self, main_rect: QRectF, gap: float, side: str, screen_left: float, screen_right: float) -> float:
        if side == "left":
            return max(0.0, main_rect.left() - gap - screen_left)
        return max(0.0, screen_right - main_rect.right() - gap)

    def _plugin_alert_uses_ring(self, rect: QRectF) -> bool:
        return rect.width() <= self._plugin_alert_circle_size(rect.height()) + self._u(0.5)

    def _rect_x_fits_screen(self, x: float, width: float, screen_left: float, screen_right: float) -> bool:
        return x >= screen_left and x + width <= screen_right

    def _clamp_rect_x_to_screen(self, x: float, width: float, screen_left: float, screen_right: float) -> float:
        return max(screen_left, min(x, screen_right - width))

    def _plugin_alert_width_for(self, label: str, detail: str, summary: str) -> float:
        if str(label or "").strip().upper() == "TIME":
            return self._time_plugin_alert_width_for(detail, summary)
        longest = max(len(str(label or "")), len(str(detail or "")), len(str(summary or "")))
        summary_weight = min(10, len(str(summary or "")))
        width_units = 112.0 + min(10, longest) * 4.0 + summary_weight * 2.0
        return self._u(max(126.0, min(226.0, width_units)))

    def _time_plugin_alert_width_for(self, detail: str, summary: str) -> float:
        source = str(summary or detail or "")
        if self._time_reminder_kind("", source) == "alarm" or not self._strip_timer_prefix(source):
            note = self._time_alarm_alert_text(source)
            char_units = max(5, min(10, len(note)))
            return max(self._u(150.0), min(self._u(168.0), self._u(92.0 + char_units * 10.0)))
        value = self._strip_timer_prefix(source)
        value_units = max(46.0, min(72.0, len(value) * 6.8 + 22.0)) if value else 48.0
        target_units = 84.0 + value_units
        return max(self._u(150.0), min(self._u(168.0), self._u(target_units)))

    def _plugin_alert_text_parts(self, label: str, detail: str, summary: str, status: str, alert_title: str = "", alert_body: str = "") -> tuple[str, str]:
        normalized_status = str(status or "").strip().lower()
        status_text = {
            "done": "完成",
            "due": "现在",
            "soon": "将近",
            "running": "计时中",
            "loading": "刷新中",
            "error": "需要注意",
            "alarm": "闹钟",
            "severe": "严重天气",
            "rain": "降雨",
        }.get(normalized_status, "提醒")
        if str(alert_title or "").strip() or str(alert_body or "").strip():
            title = str(alert_title or status_text).strip()
            body = str(alert_body or detail or summary or "").strip()
            return f"{label} · {title}", body
        body = str(detail or summary or "")
        if normalized_status == "running" and str(summary or "").strip():
            body = str(summary)
        return f"{label} · {status_text}", body

    def _main_kind(self) -> IslandKind:
        if self._music_enabled_for_stack():
            return IslandKind.MUSIC
        if self._plugin_enabled():
            return IslandKind.PLUGIN
        if self.messages:
            return IslandKind.MESSAGE
        return IslandKind.THIN_BAR

    def _music_enabled_for_stack(self) -> bool:
        if not self.music_playing:
            return False
        if not bool(getattr(self, "music_auto_detect", False)):
            return True
        has_audio = bool(getattr(self, "live_audio_available", False))
        estimated = self._estimated_media_position(time.monotonic()) if hasattr(self, "media_position_sync_at") else 0.0
        has_position = bool(getattr(self, "media_is_playing", False)) and estimated > 0.25
        source = str(getattr(self, "media_source_app", "") or "")
        title = str(getattr(self, "track_title", "") or "").strip()
        has_real_track = bool(title) and source not in ("", "system", "dependency", "windows-media")
        return has_audio or has_position or (bool(getattr(self, "media_is_playing", False)) and has_real_track)

    def _plugin_enabled(self) -> bool:
        return self.persistent_plugin and self.enabled_plugin_count > 0

    def _folded_message_limit(self, main_kind: IslandKind) -> int:
        if main_kind == IslandKind.MESSAGE and not self._music_enabled_for_stack() and not self._plugin_enabled():
            return 5
        return 3

    def _message_dot_rect(self, main_rect: QRectF, index: int, count: int, direction: int = 1) -> QRectF:
        dot_size = self._u(7.0)
        gap = self._u(8.0)
        total_w = count * dot_size + max(0, count - 1) * gap
        x = main_rect.center().x() - total_w / 2.0 + index * (dot_size + gap)
        if direction >= 0:
            y = main_rect.bottom() + self._u(1.0)
        else:
            y = main_rect.top() - self._u(1.0) - dot_size
        return QRectF(x, y, dot_size, dot_size)

    def _message_card_progress(self, rect: QRectF) -> float:
        return max(0.0, min(1.0, (rect.width() - self._u(10.0)) / self._u(180.0)))

    def _message_key(self, msg: Message) -> str:
        if msg.serial:
            return f"{msg.serial}-{msg.app}-{msg.sender}"
        return f"{int(msg.created_at * 1000)}-{msg.app}-{msg.sender}"

    def _initial_motion_rect_for_visual(self, visual: StackVisual, visuals: list[StackVisual]) -> QRectF:
        start = QRectF(visual.rect)
        if visual.kind == IslandKind.PLUGIN_PANEL:
            strip = next((item for item in visuals if item.key == "plugin"), None)
            if strip is not None:
                height = min(self._u(10.0), max(self._u(2.0), visual.rect.height()))
                if getattr(self, "stack_direction", 1) < 0:
                    return QRectF(visual.rect.left(), strip.rect.top() - height, visual.rect.width(), height)
                return QRectF(visual.rect.left(), strip.rect.bottom(), visual.rect.width(), height)
        if visual.kind == IslandKind.MESSAGE:
            message_motions = [motion.rect for key, motion in self.motions.items() if key.startswith("message:") and motion.opacity > 0.1]
            main_motion = next((self.motions.get(item.key) for item in visuals if item.z_index == 10 and item.key in self.motions), None)
            if message_motions and visual.message and time.monotonic() < visual.message.popup_until:
                source = message_motions[0]
                if getattr(self, "stack_direction", 1) < 0:
                    return QRectF(source.center().x() - self._u(8), source.top() - self._u(14), self._u(16), self._u(10))
                return QRectF(source.center().x() - self._u(8), source.bottom() + self._u(4), self._u(16), self._u(10))
            if main_motion:
                if getattr(self, "stack_direction", 1) < 0:
                    return QRectF(main_motion.rect.center().x() - self._u(3), main_motion.rect.top() - self._u(17), self._u(7), self._u(7))
                return QRectF(main_motion.rect.center().x() - self._u(3), main_motion.rect.bottom() + self._u(10), self._u(7), self._u(7))
            if visual.message and self.expanded and time.monotonic() >= visual.message.popup_until:
                if getattr(self, "stack_direction", 1) < 0:
                    return QRectF(visual.rect.center().x() - self._u(3), visual.rect.bottom() + self._u(7), self._u(7), self._u(7))
                return QRectF(visual.rect.center().x() - self._u(3), visual.rect.top() - self._u(14), self._u(7), self._u(7))
        start.moveTop(start.top() - self._u(12) if getattr(self, "stack_direction", 1) >= 0 else start.top() + self._u(12))
        return start

    def _plugin_alert_exit_rect_towards_main(self, alert_rect: QRectF, visuals: list[StackVisual]) -> QRectF:
        main = next((item for item in visuals if item.z_index == 10), None)
        if main is None:
            size = min(self._u(10.0), max(self._u(4.0), alert_rect.height()))
            return QRectF(alert_rect.center().x() - size / 2.0, alert_rect.center().y() - size / 2.0, size, size)
        size = min(self._u(10.0), max(self._u(4.0), alert_rect.height() * 0.24))
        y = alert_rect.center().y() - size / 2.0
        if alert_rect.center().x() >= main.rect.center().x():
            x = main.rect.right() + self._u(4.0)
        else:
            x = main.rect.left() - self._u(4.0) - size
        return QRectF(x, y, size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        if getattr(self, "high_quality_font_rendering", False):
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        now = time.monotonic()
        dt = (now - self.last_frame_at) * max(0.1, min(4.0, float(getattr(self, "animation_speed", 1.0))))
        self.last_frame_at = now

        self._paint_stage(painter)
        visuals = self._build_visuals()
        transition_scale, transition_opacity, scene_hidden, transition_edge_progress = self._scene_transition_params(now)
        if scene_hidden:
            self.group_frame_mask_path = None
            barrage_rects = self._paint_barrage_scene(painter)
            self.barrage_rects = barrage_rects
            self.clearMask()
            if self.show_debug_overlay:
                self._paint_help(painter)
            painter.end()
            return

        targets = {visual.key for visual in visuals}
        painter_transform_applied = False
        if transition_scale != 1.0 or transition_opacity != 1.0:
            painter.save()
            anchor = self._scene_transition_anchor(visuals)
            offset = self._scene_transition_offset(visuals, transition_edge_progress)
            painter.translate(offset)
            painter.translate(anchor.x(), anchor.y())
            painter.scale(transition_scale, transition_scale)
            painter.translate(-anchor.x(), -anchor.y())
            painter.setOpacity(transition_opacity)
            painter_transform_applied = True

        for visual in visuals:
            if visual.key not in self.motions:
                start = self._initial_motion_rect_for_visual(visual, visuals)
                self.motions[visual.key] = SpringRect(start)
            if self.finder_mode:
                response = 0.19 if now < self.stack_bounce_until else 0.24
                damping = 0.84
            else:
                response = 0.28 if now < self.stack_bounce_until else 0.34
                damping = 0.76
            self.motions[visual.key].step(visual.rect, 1.0, dt, response=response, damping=damping)

        for key in list(self.motions):
            if key not in targets:
                if key.startswith("plugin-alert:") and getattr(self, "expanded", False):
                    rect = self._plugin_alert_exit_rect_towards_main(self.motions[key].rect, visuals)
                    response = 0.18 if self.finder_mode else 0.22
                    damping = 0.68 if self.finder_mode else 0.62
                else:
                    rect = self.motions[key].rect.adjusted(0, -self._u(8), 0, -self.motions[key].rect.height() + self._u(8))
                    response = 0.18 if self.finder_mode else 0.24
                    damping = 0.84 if self.finder_mode else 0.76
                self.motions[key].step(
                    rect,
                    0.0,
                    dt,
                    response=response,
                    damping=damping,
                )
                if self.motions[key].opacity < 0.025:
                    del self.motions[key]

        items = [(visual, self.motions[visual.key]) for visual in visuals if visual.key in self.motions and self.motions[visual.key].opacity > 0.06]
        self._paint_myfinder_group_frame(painter, items, dt)

        self._paint_dot_group_backing(painter, items)
        self.plugin_action_rects = {}
        for visual, motion in sorted(items, key=lambda item: item[0].z_index):
            painter.save()
            painter.setOpacity(max(0.0, min(1.0, motion.opacity)))
            rect = QRectF(motion.rect)
            if visual.key == self.hovered_key and now < self.hover_bounce_until:
                rect = self._hover_rect(rect, now)
            if visual.kind == IslandKind.MUSIC:
                self._paint_music(painter, rect, self.expanded)
            elif visual.kind == IslandKind.PLUGIN:
                self._paint_plugin(painter, rect, visual.folded)
            elif visual.kind == IslandKind.PLUGIN_PANEL:
                self._paint_plugin_panel(painter, rect)
            elif visual.kind == IslandKind.PLUGIN_ALERT:
                self._paint_plugin_alert(painter, rect, visual.key)
            elif visual.kind == IslandKind.MESSAGE and visual.message:
                self._paint_message(painter, rect, visual.message, visual.folded)
            else:
                self._paint_thin_bar(painter, rect)
            painter.restore()

        self.barrage_rects = {}
        mask_rects = [motion.rect for _, motion in items]
        self._update_interaction_mask(mask_rects)
        if painter_transform_applied:
            painter.restore()
        self._paint_surface_transition(painter, now)
        if self.show_debug_overlay:
            self._paint_help(painter)
        painter.end()

    def _hover_rect(self, rect: QRectF, now: float) -> QRectF:
        if self.expanded or not self.hover_started_at:
            return rect
        progress = max(0.0, min(1.0, now - self.hover_started_at))
        scale = 1.0 + 0.026 * (1.0 - math.cos(progress * math.pi)) / 2.0
        out = QRectF(rect)
        center = out.center()
        out.setWidth(rect.width() * scale)
        out.setHeight(rect.height() * scale)
        out.moveCenter(center)
        return out

    def _paint_stage(self, painter: QPainter):
        return
        painter.fillRect(QRectF(0, 0, self.width(), self.height()), QColor(3, 3, 4, 238))

        if self.finder_mode:
            finder = QRectF(44, 24, self.width() - 88, 34)
            path = self._rounded_path(finder, 14)
            painter.fillPath(path, QColor(22, 24, 28, 224))
            painter.setPen(QPen(QColor(255, 255, 255, 24), 1.0))
            painter.drawPath(path)
            painter.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
            painter.drawLine(QPointF(finder.left() + 22, finder.top() + 1), QPointF(finder.right() - 22, finder.top() + 1))
            painter.setFont(self._font(9, QFont.Weight.Medium))
            painter.setPen(QColor(255, 255, 255, 138))
            painter.drawText(finder.adjusted(18, 0, -18, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "MyFinder simulation · V2 shell")
            painter.setPen(QColor(255, 255, 255, 92))
            painter.drawText(finder.adjusted(18, 0, -18, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "upper edge reserved for embedded fusion")
        else:
            painter.setFont(self._font(9, QFont.Weight.Medium))
            painter.setPen(QColor(255, 255, 255, 105))
            painter.drawText(QRectF(42, 29, self.width() - 84, 24), Qt.AlignmentFlag.AlignLeft, "Standalone floating mode · V2 island stack")

    def _paint_barrage_scene(self, painter: QPainter) -> dict[str, QRectF]:
        now = time.monotonic()
        previews = list(getattr(self, "barrage_preview_messages", []))
        if getattr(self, "settings_window_active", False) and previews:
            active = previews
        else:
            active = list(self.messages) + previews
        active.sort(key=lambda message: (message.barrage_started_at, message.serial))
        if not active:
            return {}

        font_size = max(8, min(20, int(getattr(self, "barrage_font_size", 10))))
        font = self._font(font_size, QFont.Weight.Bold)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        rects: dict[str, QRectF] = {}
        lane_count = max(1, min(6, len(active)))
        lane_height = max(self._u(26.0), float(metrics.height()) + self._u(12.0))
        start_y = float(getattr(self, "barrage_y_offset", 0))
        lane_available_at = [0.0] * lane_count
        speed_scale = max(0.4, min(2.2, float(getattr(self, "barrage_speed", 100)) / 100.0))

        def barrage_metrics(text: str) -> tuple[float, float, float, float, float]:
            text_w = float(metrics.horizontalAdvance(text))
            icon_space = self._u(24.0) if getattr(self, "barrage_show_icon", True) else 0.0
            content_w = text_w + self._u(26.0) + icon_space
            content_h = max(self._u(24.0), float(metrics.height()) + self._u(10.0))
            travel = self.width() + content_w + self._u(140.0)
            base_speed = max(self._u(160.0), 150.0 + content_w * 0.14) * speed_scale
            duration = max(2.8 / speed_scale, travel / base_speed)
            return content_w, content_h, travel, duration, travel / duration

        for message in active:
            text = self._barrage_text(message)
            content_w, content_h, travel, duration, speed = barrage_metrics(text)
            lane = min(range(lane_count), key=lambda i: max(message.barrage_started_at, lane_available_at[i]))
            effective_start = max(message.barrage_started_at, lane_available_at[lane])
            progress = max(0.0, min(1.4, (self.barrage_clock - effective_start) / duration))
            if self.barrage_clock < effective_start:
                continue
            x = self.width() + self._u(22.0) - progress * travel
            if x < -content_w - self._u(120.0):
                continue

            lane_available_at[lane] = effective_start + (content_w + self._u(92.0)) / max(1.0, speed)
            y = start_y + lane * lane_height
            text_rect = QRectF(x, y, content_w, content_h)
            rects[self._message_key(message)] = text_rect.adjusted(-self._u(10.0), -self._u(8.0), self._u(12.0), self._u(8.0))
            self._paint_barrage_text(painter, text_rect, text, message, font, metrics)

        def preview_alive(message: Message) -> bool:
            content_w, _content_h, travel, duration, _speed = barrage_metrics(self._barrage_text(message))
            progress = max(0.0, (self.barrage_clock - message.barrage_started_at) / max(0.01, duration))
            x = self.width() + self._u(22.0) - progress * travel
            return x >= -content_w - self._u(120.0)

        self.barrage_preview_messages = [
            message for message in getattr(self, "barrage_preview_messages", []) if preview_alive(message)
        ][:3]
        return rects

    def _barrage_text(self, message: Message) -> str:
        sender = message.sender.strip()
        text = message.text.strip()
        if sender and text:
            return f"{sender}: {text}"
        return sender or text or message.app

    def _barrage_background_is_dark(self, rect: QRectF) -> bool:
        now = time.monotonic()
        if self.drag_candidate or now - getattr(self, "last_pointer_press_at", 0.0) < 1.2:
            return self.barrage_dark_background
        if now - self.barrage_style_checked_at > 0.55:
            luma = self._sample_background_luma(rect)
            if luma < 0.46:
                self.barrage_dark_background = True
            elif luma > 0.58:
                self.barrage_dark_background = False
            self.barrage_style_checked_at = now
        return self.barrage_dark_background

    def _paint_barrage_text(self, painter: QPainter, rect: QRectF, text: str, message: Message, font: QFont, metrics: QFontMetrics):
        dark_bg = self._barrage_background_is_dark(rect)
        ink = QColor(255, 255, 255, 232) if dark_bg else QColor(22, 28, 40, 232)
        glow = QColor(0, 0, 0, 86) if dark_bg else QColor(255, 255, 255, 122)
        accent = QColor(message.name_color.red(), message.name_color.green(), message.name_color.blue(), 198 if dark_bg else 216)

        painter.save()
        painter.setFont(font)
        painter.setOpacity(1.0)
        radius = min(rect.height() / 2.0, self._u(12.0))
        if getattr(self, "barrage_use_backdrop", True):
            backdrop = QColor(12, 16, 24, 104) if dark_bg else QColor(255, 255, 255, 116)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(backdrop)
            painter.drawRoundedRect(rect, radius, radius)
            edge = QColor(255, 255, 255, 22) if dark_bg else QColor(0, 0, 0, 18)
            painter.setPen(QPen(edge, self._u(1.0)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(self._u(0.5), self._u(0.5), -self._u(0.5), -self._u(0.5)), max(1.0, radius - self._u(0.5)), max(1.0, radius - self._u(0.5)))

        icon_x = rect.left() + self._u(9.0)
        text_x = icon_x
        if getattr(self, "barrage_show_icon", True):
            icon_size = max(self._u(14.0), min(self._u(18.0), rect.height() - self._u(8.0)))
            icon_rect = QRectF(rect.left() + self._u(7.0), rect.center().y() - icon_size / 2.0, icon_size, icon_size)
            painter.setPen(QPen(accent, self._u(0.9)))
            painter.setBrush(QColor(message.avatar_color.red(), message.avatar_color.green(), message.avatar_color.blue(), 220))
            painter.drawEllipse(icon_rect)
            painter.setFont(self._font(max(8, min(20, int(getattr(self, "barrage_font_size", 10)))), QFont.Weight.Bold))
            painter.setPen(QColor(255, 255, 255, 228))
            painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, (message.sender[:1] or message.app[:1] or "·"))
            text_x = icon_rect.right() + self._u(8.0)
        else:
            text_x = rect.left() + self._u(12.0)

        text_rect = QRectF(text_x, rect.top(), rect.right() - text_x - self._u(10.0), rect.height())
        painter.setFont(font)
        painter.setPen(QPen(glow, self._u(1.35)))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            painter.drawText(text_rect.translated(dx, dy), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.setPen(ink)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()

    def _sample_background_luma(self, rect: QRectF) -> float:
        try:
            center = self.mapToGlobal(QPoint(int(round(rect.center().x())), int(round(rect.center().y()))))
            screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
            if not screen:
                return 0.5
            sample_w = max(8, min(96, int(round(rect.width()))))
            sample_h = max(8, min(36, int(round(rect.height()))))
            left = center.x() - sample_w // 2
            top = center.y() - sample_h // 2
            geo = screen.geometry()
            left = max(geo.left(), min(left, geo.right() - sample_w + 1))
            top = max(geo.top(), min(top, geo.bottom() - sample_h + 1))
            pixmap = screen.grabWindow(0, left, top, sample_w, sample_h)
            if pixmap.isNull():
                return 0.5
            image = pixmap.toImage()
            if image.isNull():
                return 0.5
        except Exception:
            return 0.5
        total = 0.0
        count = 0
        step_x = max(1, image.width() // 10)
        step_y = max(1, image.height() // 6)
        for y in range(0, image.height(), step_y):
            for x in range(0, image.width(), step_x):
                color = image.pixelColor(x, y)
                total += color.red() * 0.2126 + color.green() * 0.7152 + color.blue() * 0.0722
                count += 1
        if not count:
            return 0.5
        return max(0.0, min(1.0, (total / count) / 255.0))

    def _update_interaction_mask(self, rects: list[QRectF]):
        if sys.platform != "win32":
            return
        if not self.finder_mode and not self.scene_suppressed:
            if getattr(self, "interaction_mask_active", False):
                self.clearMask()
                self.interaction_mask_active = False
                self.last_mask_rect = QRectF()
            return
        now = time.monotonic()
        if now < float(getattr(self, "mask_deferred_until", 0.0)):
            return
        pointer_quiet_seconds = 0.06 if self.finder_mode else 0.12
        if now - getattr(self, "last_pointer_move_at", 0.0) < pointer_quiet_seconds:
            return
        mask_interval = 0.24 if self.finder_mode else 0.18
        if now - getattr(self, "last_mask_update_at", 0.0) < mask_interval:
            return
        self.last_mask_update_at = now
        mask_rect = QRectF()
        if self.finder_mode and not getattr(self, "finder_hover_rect", QRectF()).isNull():
            mask_rect = QRectF(self.finder_hover_rect)
        for rect in rects:
            grown = rect.adjusted(-self._u(8.0), -self._u(6.0), self._u(8.0), self._u(6.0))
            mask_rect = grown if mask_rect.isNull() else mask_rect.united(grown)
        if self.finder_mode and not mask_rect.isNull():
            mask_rect = mask_rect.adjusted(-self._u(4.0), -self._u(2.0), self._u(4.0), self._u(4.0))
        if mask_rect.isNull() or mask_rect.width() <= 0 or mask_rect.height() <= 0:
            if getattr(self, "interaction_mask_active", False):
                self.clearMask()
                self.interaction_mask_active = False
                self.last_mask_rect = QRectF()
            return
        aligned = mask_rect.toAlignedRect()
        previous = getattr(self, "last_mask_rect", QRectF())
        if getattr(self, "interaction_mask_active", False) and not previous.isNull():
            delta = max(
                abs(previous.left() - mask_rect.left()),
                abs(previous.top() - mask_rect.top()),
                abs(previous.right() - mask_rect.right()),
                abs(previous.bottom() - mask_rect.bottom()),
            )
            threshold = 6.0 if self.finder_mode else 1.5
            if delta < threshold:
                return
        self.setMask(QRegion(aligned))
        self.last_mask_rect = QRectF(mask_rect)
        self.interaction_mask_active = True

    def _paint_help(self, painter: QPainter):
        painter.setFont(self._font(9))
        painter.setPen(QColor(255, 255, 255, 118))
        mode = "Finder" if self.finder_mode else "Standalone"
        music = "Music" if self.music_playing else "NoMusic"
        plugin = "Plugin" if self.persistent_plugin else "NoPlugin"
        stack = "Expanded" if self.expanded else "Folded"
        width = int(self._stack_target_width(self._main_kind())) if self.expanded else int(self._u(float(self.STACK_WIDTH)))
        text = f"M music  P plugin  N message  C clear  F finder  Space expand  0 plugins  T seconds  1/2/3 focus  Esc quit    [{mode} | {music} | {plugin}:{self.enabled_plugin_count} | {stack} | width={width}px]"
        painter.drawText(QRectF(self._u(34), self.height() - self._u(36), self.width() - self._u(68), self._u(24)), Qt.AlignmentFlag.AlignCenter, text)
        if self.opened_source_app and time.monotonic() < self.opened_source_until:
            painter.setFont(self._font(9, QFont.Weight.Bold))
            painter.setPen(QColor(132, 255, 176, 210))
            painter.drawText(QRectF(self._u(34), self.height() - self._u(62), self.width() - self._u(68), self._u(22)), Qt.AlignmentFlag.AlignCenter, self.opened_source_app)

    def _rounded_path(self, rect: QRectF, radius: float) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        return path

    def _myfinder_cradle_path(self, rect: QRectF, top_radius: float, bottom_radius: float) -> QPainterPath:
        bottom_r = max(1.0, min(bottom_radius, rect.height() * 0.5, rect.width() * 0.5))
        path = QPainterPath()
        path.moveTo(rect.left(), rect.top())
        path.lineTo(rect.right(), rect.top())
        path.lineTo(rect.right(), rect.bottom() - bottom_r)
        path.quadTo(rect.right(), rect.bottom(), rect.right() - bottom_r, rect.bottom())
        path.lineTo(rect.left() + bottom_r, rect.bottom())
        path.quadTo(rect.left(), rect.bottom(), rect.left(), rect.bottom() - bottom_r)
        path.lineTo(rect.left(), rect.top())
        path.closeSubpath()
        return path

    def _finder_texture_source_rect(self, target: QRectF, texture: QImage) -> QRectF:
        glass = self.finder_material
        if getattr(glass, "texture_is_full_screen", False):
            global_top_left = self.mapToGlobal(QPoint(int(round(target.left())), int(round(target.top()))))
            source_left = float(global_top_left.x() - int(getattr(glass, "texture_screen_left", 0)))
            source_top = float(global_top_left.y() - int(getattr(glass, "texture_screen_top", 0)))
            source_left = max(0.0, min(float(texture.width() - 1), source_left))
            source_top = max(0.0, min(float(texture.height() - 1), source_top))
            source_width = max(1.0, min(float(texture.width()) - source_left, target.width()))
            source_height = max(1.0, min(float(texture.height()) - source_top, target.height()))
            return QRectF(source_left, source_top, source_width, source_height)
        if texture.width() > 512:
            global_left = self.mapToGlobal(QPoint(int(round(target.left())), 0)).x()
            source_left = max(0.0, min(float(texture.width() - 1), float(global_left - int(getattr(glass, "texture_screen_left", 0)))))
            source_width = max(1.0, min(float(texture.width()) - source_left, target.width()))
            return QRectF(source_left, 0.0, source_width, float(texture.height()))
        return QRectF(0.0, 0.0, float(texture.width()), float(texture.height()))

    def _paint_myfinder_group_frame(self, painter: QPainter, items: list[tuple[StackVisual, SpringRect]], dt: float):
        if not self.finder_mode:
            self.group_frame_motion = None
            self.group_frame_mask_path = None
            self.finder_hover_rect = QRectF()
            return
        visible_rects = []
        main_rect = None
        main_kind = None
        for visual, motion in items:
            if motion.opacity <= 0.16 or motion.rect.width() <= 4 or motion.rect.height() <= 4:
                continue
            if visual.z_index == 10:
                main_rect = QRectF(visual.rect)
                main_kind = visual.kind
            if self.expanded:
                if visual.folded:
                    continue
            elif (
                visual.z_index != 10
                and visual.kind != IslandKind.PLUGIN_ALERT
                and not (visual.kind == IslandKind.MESSAGE and not visual.folded)
            ):
                continue
            rect = QRectF(visual.rect)
            visible_rects.append(rect)
        if not visible_rects:
            self.group_frame_motion = None
            self.group_frame_mask_path = None
            self.finder_hover_rect = QRectF()
            return
        if main_kind == IslandKind.THIN_BAR:
            self.group_frame_motion = None
            self.group_frame_mask_path = None
            self.finder_hover_rect = QRectF()
            return

        left = min(rect.left() for rect in visible_rects)
        right = max(rect.right() for rect in visible_rects)
        top = min(rect.top() for rect in visible_rects)
        bottom = max(rect.bottom() for rect in visible_rects)
        dot_rects = [visual.rect for visual, motion in items if visual.kind == IslandKind.MESSAGE and visual.folded and motion.opacity > 0.08]
        dot_tray = self._dot_tray_rect(dot_rects)
        if dot_tray and not self.expanded:
            bottom = max(bottom, dot_tray.bottom())
            if main_rect:
                bottom = max(bottom, main_rect.bottom() + self._u(15.0))

        finder_bar = QRectF(0.0, 0.0, self.width(), self.finder_material.bar_height)
        glass = self.finder_material
        effective_border_width = 0.0
        pad_x = (
            max(self._u(6.0), min(self._u(12.0), self._u(5.6) + effective_border_width * self._u(1.8)))
            if not self.expanded
            else max(self._u(9.0), min(self._u(15.0), self._u(8.2) + effective_border_width * self._u(2.0)))
        )
        frame_top = finder_bar.bottom() - 1.0
        balance_gap = max(self._u(8.0), min(self._u(48.0), top))
        if dot_tray and not self.expanded:
            bottom = max(bottom, dot_tray.bottom())
        target_bottom = bottom + balance_gap
        target = QRectF(left - pad_x, frame_top, right - left + pad_x * 2.0, target_bottom - frame_top)
        stable_dual_compact_music = main_kind == IslandKind.MUSIC and not self.expanded and self._has_current_two_line_lyric()
        if self.group_frame_motion is None:
            start = QRectF(target)
            start.setHeight(self._u(22.0))
            start.moveBottom(finder_bar.bottom() + self._u(10.0))
            self.group_frame_motion = SpringRect(start)
        if stable_dual_compact_music:
            self.group_frame_motion = SpringRect(target)
            self.group_frame_motion.opacity = 1.0
        else:
            self.group_frame_motion.step(target, 1.0, dt, response=0.16, damping=0.9)

        frame = self.group_frame_motion.rect
        side_flare = max(
            self._u(18.0),
            min(self._u(32.0), glass.corner_radius * self.VISUAL_SCALE + effective_border_width * self._u(5.0)),
        )
        cradle = frame.adjusted(-side_flare, 0.0, side_flare, 0.0)
        cradle.setTop(finder_bar.bottom() - 2.0)

        finder_bottom = finder_bar.bottom() - self._u(3.0)
        body_bottom = cradle.bottom()
        body_h = max(1.0, body_bottom - finder_bottom)
        base_corner = max(self._u(18.0), glass.corner_radius * self.VISUAL_SCALE + effective_border_width * self._u(2.2))
        bottom_r = max(self._u(14.0), min(base_corner, body_h * 0.48, cradle.width() * 0.12))
        top_r = max(bottom_r, min(self._u(42.0), body_h * 0.52, side_flare * 1.55))
        shell_rect = QRectF(cradle.left(), finder_bottom - self._u(1.0), cradle.width(), body_h + self._u(1.0))
        shell = self._myfinder_cradle_path(shell_rect, top_r, bottom_r)
        self.group_frame_mask_path = QPainterPath(shell)
        hover_pad_x = max(self._u(10.0), side_flare * 0.36)
        self.finder_hover_rect = shell_rect.adjusted(-hover_pad_x, -self._u(7.0), hover_pad_x, self._u(12.0))
        left_join = shell_rect.left()
        right_join = shell_rect.right()

        painter.setPen(Qt.PenStyle.NoPen)
        used_texture = False
        if glass.texture_image and not glass.texture_image.isNull():
            painter.save()
            painter.setClipPath(shell)
            target = QRectF(shell_rect)
            painter.drawImage(target, glass.texture_image, self._finder_texture_source_rect(target, glass.texture_image))
            painter.restore()
            used_texture = True
        else:
            material = QLinearGradient(finder_bar.left(), finder_bottom, finder_bar.right(), cradle.bottom())
            material.setColorAt(0.00, glass.bottom_color)
            material.setColorAt(0.12, glass.bottom_color)
            material.setColorAt(0.42, glass.bottom_color)
            material.setColorAt(1.00, glass.mid_color)
            painter.fillPath(shell, material)

        if not used_texture:
            shade = QLinearGradient(0, finder_bottom, 0, cradle.bottom())
            shade.setColorAt(0.00, QColor(255, 255, 255, 0))
            shade.setColorAt(0.34, QColor(255, 255, 255, 0))
            shade.setColorAt(0.72, QColor(0, 0, 0, max(0, int(glass.shadow_alpha * 0.10))))
            shade.setColorAt(1.00, QColor(0, 0, 0, max(8, int(glass.shadow_alpha * 0.28))))
            painter.fillPath(shell, shade)

        join_wash = QLinearGradient(0, finder_bottom, 0, finder_bottom + self._u(8.0))
        join_wash.setColorAt(0.00, QColor(glass.bottom_color.red(), glass.bottom_color.green(), glass.bottom_color.blue(), 0))
        join_wash.setColorAt(0.45, QColor(glass.bottom_color.red(), glass.bottom_color.green(), glass.bottom_color.blue(), max(10, int(glass.bottom_color.alpha() * 0.18))))
        join_wash.setColorAt(1.00, QColor(glass.bottom_color.red(), glass.bottom_color.green(), glass.bottom_color.blue(), 0))
        painter.fillPath(shell, join_wash)

    def _dot_tray_rect(self, dot_rects: list[QRectF]) -> QRectF | None:
        if not dot_rects:
            return None
        left = min(rect.left() for rect in dot_rects)
        right = max(rect.right() for rect in dot_rects)
        top = min(rect.top() for rect in dot_rects)
        bottom = max(rect.bottom() for rect in dot_rects)
        return QRectF(left - self._u(8.0), top - self._u(5.0), right - left + self._u(16.0), bottom - top + self._u(9.0))

    def _paint_dot_group_backing(self, painter: QPainter, items: list[tuple[StackVisual, SpringRect]]):
        dots = [(visual, motion.rect) for visual, motion in items if visual.kind == IslandKind.MESSAGE and visual.folded and motion.opacity > 0.08]
        if not dots:
            return
        bg = self._dot_tray_rect([rect for _, rect in dots])
        if not bg:
            return
        main_rect = next((motion.rect for visual, motion in items if visual.z_index == 10 and motion.opacity > 0.12), None)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 218))
        if main_rect:
            bg = QRectF(bg)
            direction = -1 if bg.center().y() < main_rect.center().y() else 1
            if direction >= 0:
                bg.setTop(main_rect.bottom() - self._u(2.0))
                bg.setBottom(max(bg.bottom(), main_rect.bottom() + self._u(15.0)))
            else:
                bg.setBottom(main_rect.top() + self._u(2.0))
                bg.setTop(min(bg.top(), main_rect.top() - self._u(15.0)))
            corner_radius = min(self._u(8.0), bg.height() * 0.45, bg.width() * 0.26)
            path = QPainterPath()
            if direction >= 0:
                path.moveTo(bg.left(), bg.top())
                path.lineTo(bg.right(), bg.top())
                path.lineTo(bg.right(), bg.bottom() - corner_radius)
                path.quadTo(bg.right(), bg.bottom(), bg.right() - corner_radius, bg.bottom())
                path.lineTo(bg.left() + corner_radius, bg.bottom())
                path.quadTo(bg.left(), bg.bottom(), bg.left(), bg.bottom() - corner_radius)
                path.lineTo(bg.left(), bg.top())
            else:
                path.moveTo(bg.left(), bg.bottom())
                path.lineTo(bg.right(), bg.bottom())
                path.lineTo(bg.right(), bg.top() + corner_radius)
                path.quadTo(bg.right(), bg.top(), bg.right() - corner_radius, bg.top())
                path.lineTo(bg.left() + corner_radius, bg.top())
                path.quadTo(bg.left(), bg.top(), bg.left(), bg.top() + corner_radius)
                path.lineTo(bg.left(), bg.bottom())
            path.closeSubpath()
            tray_gradient = QLinearGradient(0, bg.top(), 0, bg.bottom())
            if direction >= 0:
                tray_gradient.setColorAt(0.00, QColor(0, 0, 0, 210))
                tray_gradient.setColorAt(0.35, QColor(0, 0, 0, 228))
                tray_gradient.setColorAt(1.00, QColor(0, 0, 0, 238))
            else:
                tray_gradient.setColorAt(0.00, QColor(0, 0, 0, 238))
                tray_gradient.setColorAt(0.65, QColor(0, 0, 0, 228))
                tray_gradient.setColorAt(1.00, QColor(0, 0, 0, 210))
            painter.fillPath(path, tray_gradient)
            return

        radius = min(self._u(8.0), bg.height() * 0.45, bg.width() * 0.26)
        path = QPainterPath()
        path.moveTo(bg.left(), bg.top())
        path.lineTo(bg.right(), bg.top())
        path.lineTo(bg.right(), bg.bottom() - radius)
        path.quadTo(bg.right(), bg.bottom(), bg.right() - radius, bg.bottom())
        path.lineTo(bg.left() + radius, bg.bottom())
        path.quadTo(bg.left(), bg.bottom(), bg.left(), bg.bottom() - radius)
        path.lineTo(bg.left(), bg.top())
        path.closeSubpath()
        painter.fillPath(path, QColor(0, 0, 0, 226))

    def _paint_black_glass(self, painter: QPainter, rect: QRectF, radius: float, folded: bool = False):
        path = self._rounded_path(rect, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, QColor(0, 0, 0, 255))

        stroke = self._u(1.0)
        inner = rect.adjusted(stroke, stroke, -stroke, -stroke)
        painter.setPen(QPen(QColor(255, 255, 255, 28 if not folded else 15), stroke))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(inner, max(1.0, radius - stroke), max(1.0, radius - stroke))

        if rect.height() > self._u(26) and not folded:
            painter.setPen(QPen(QColor(255, 255, 255, 16), stroke))
            painter.drawLine(QPointF(rect.left() + radius * 0.85, rect.top() + self._u(1.2)), QPointF(rect.right() - radius * 0.85, rect.top() + self._u(1.2)))

    def _paint_connected_backing(self, painter: QPainter, items: list[tuple[StackVisual, SpringRect]]):
        visible = [(v, m.rect) for v, m in items if m.opacity > 0.2 and not v.folded and m.rect.height() > self._u(22)]
        visible.sort(key=lambda item: item[1].top())
        painter.setPen(Qt.PenStyle.NoPen)
        for (_, upper), (_, lower) in zip(visible, visible[1:]):
            if lower.top() - upper.bottom() > self._u(22):
                continue
            cx = (upper.center().x() + lower.center().x()) / 2.0
            width = min(upper.width(), lower.width()) - self._u(28)
            bridge = QRectF(cx - width / 2.0, upper.bottom() - self._u(1.0), width, lower.top() - upper.bottom() + self._u(2.0))
            path = self._rounded_path(bridge.adjusted(0, -self._u(7), 0, self._u(7)), self._u(19))
            painter.fillPath(path, QColor(0, 0, 0, 178))
            painter.setPen(QPen(QColor(255, 255, 255, 16), self._u(1.0)))
            painter.drawPath(path)
            painter.setPen(Qt.PenStyle.NoPen)

    def _paint_music(self, painter: QPainter, rect: QRectF, expanded: bool):
        radius = self._u(30) if expanded else rect.height() / 2
        ambience_style = getattr(self, "music_ambience_style", "sea_level")
        black_background = self._active_lyric_color_fill_enabled() or ambience_style in {"pure_black", "bilateral_diffraction"}
        if black_background:
            self._paint_pure_black_music_background(painter, rect, radius)
            if ambience_style == "bilateral_diffraction":
                self._paint_bilateral_diffraction_ambience(painter, rect, radius, expanded)
        else:
            self._paint_music_glass_body(painter, rect, radius)
            self._paint_bottom_ambience(painter, rect, radius, expanded)
            if ambience_style != "duotone_full":
                self._paint_music_blur_mask(painter, rect, radius, expanded)
            self._paint_music_light_wave(painter, rect, radius, expanded)
        if expanded:
            self._paint_music_expanded(painter, rect)
        else:
            self._paint_music_compact(painter, rect)
        self._paint_music_border(painter, rect, radius)

    def _paint_pure_black_music_background(self, painter: QPainter, rect: QRectF, radius: float):
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 255))
        painter.drawRoundedRect(rect, radius, radius)
        painter.restore()

    def _compact_cover_rect(self, rect: QRectF) -> QRectF:
        if self._gelv_enabled():
            cover_size = min(rect.height() - self._u(12.0), self._u(38.0))
            return QRectF(rect.left() + self._u(9), rect.center().y() - cover_size / 2.0, cover_size, cover_size)
        two_line = self._has_current_two_line_lyric()
        if two_line:
            cover_size = min(rect.height() - self._u(14.0), self._u(50.0))
            y_offset = self._u(1.0)
        else:
            cover_size = self._u(30.0)
            y_offset = 0.0
        return QRectF(rect.left() + self._u(9), rect.center().y() - cover_size / 2.0 + y_offset, cover_size, cover_size)

    def _paint_music_glass_body(self, painter: QPainter, rect: QRectF, radius: float):
        path = self._rounded_path(rect, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        body = QLinearGradient(0, rect.top(), 0, rect.bottom())
        body.setColorAt(0.00, QColor(4, 8, 18, 255))
        body.setColorAt(0.30, QColor(6, 11, 24, 255))
        body.setColorAt(0.66, QColor(8, 16, 32, 255))
        body.setColorAt(1.00, QColor(12, 24, 44, 255))
        painter.fillPath(path, body)

        shade = QLinearGradient(0, rect.top(), 0, rect.bottom())
        shade.setColorAt(0.0, QColor(255, 255, 255, 18))
        shade.setColorAt(0.26, QColor(255, 255, 255, 6))
        shade.setColorAt(0.70, QColor(0, 0, 0, 0))
        shade.setColorAt(1.0, QColor(0, 0, 0, 30))
        painter.fillPath(path, shade)

    def _paint_music_border(self, painter: QPainter, rect: QRectF, radius: float):
        stroke = self._u(1.0)
        inner = rect.adjusted(stroke, stroke, -stroke, -stroke)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 30), stroke))
        painter.drawRoundedRect(inner, max(1.0, radius - stroke), max(1.0, radius - stroke))

        painter.setPen(QPen(QColor(255, 255, 255, 13), stroke))
        painter.drawLine(QPointF(rect.left() + radius * 0.9, rect.top() + self._u(1.4)), QPointF(rect.right() - radius * 0.9, rect.top() + self._u(1.4)))

    def _paint_music_blur_mask(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        painter.save()
        stroke = self._u(1.0)
        bleed = self._u(1.0)
        paint_rect = rect.adjusted(-bleed, -bleed, bleed, bleed)
        painter.setClipPath(self._rounded_path(rect.adjusted(stroke - bleed, stroke - bleed, -stroke + bleed, -stroke + bleed), max(1.0, radius - stroke + bleed)))

        mask = QLinearGradient(0, paint_rect.top(), 0, paint_rect.bottom())
        mask.setColorAt(0.00, QColor(0, 0, 0, 232 if expanded else 218))
        mask.setColorAt(0.24, QColor(0, 0, 0, 218 if expanded else 198))
        mask.setColorAt(0.40, QColor(0, 0, 0, 164 if expanded else 142))
        mask.setColorAt(0.55, QColor(0, 0, 0, 76 if expanded else 62))
        mask.setColorAt(0.72, QColor(0, 0, 0, 18 if expanded else 14))
        mask.setColorAt(0.88, QColor(0, 0, 0, 8))
        mask.setColorAt(1.00, QColor(0, 0, 0, 0))
        painter.fillRect(paint_rect, mask)
        painter.restore()

    def _dominant_album_color(self) -> QColor:
        return self._safe_wave_color(self.album_color)

    def _safe_wave_color(self, color: QColor) -> QColor:
        hue, sat, val, _alpha = color.getHsvF()
        if hue < 0 or sat < 0.10:
            gray = max(40, min(224, int(val * 255)))
            return QColor(gray, gray, gray)
        sat = max(0.42, min(0.94, sat))
        val = max(0.42, min(0.90, val))
        return QColor.fromHsvF(hue, sat, val, 1.0)

    def _pure_plugin_color(self, color: QColor) -> QColor:
        hue, sat, val, _alpha = color.getHsvF()
        if hue < 0:
            return color
        sat = max(0.92, min(1.0, sat + 0.22))
        val = max(0.88, min(1.0, val + 0.12))
        return QColor.fromHsvF(hue, sat, val, 1.0)

    def _plugin_display_color(self, label: str, color: QColor) -> QColor:
        if str(label or "").strip().upper() == "TIME":
            return QColor(255, 204, 0)
        return QColor(color)

    def _system_dark_mode(self) -> bool:
        return app_dark_mode()

    def _plugin_theme_colors(self) -> dict[str, QColor]:
        if self._system_dark_mode():
            return {
                "shell": QColor(30, 30, 32, 255),
                "shell_expanded": QColor(28, 28, 30, 255),
                "cell": QColor(46, 46, 49, 255),
                "cell_active": QColor(58, 58, 62, 255),
                "stroke": QColor(255, 255, 255, 42),
                "inner_stroke": QColor(0, 0, 0, 45),
                "text": QColor(245, 245, 247, 238),
                "muted": QColor(188, 188, 194, 224),
                "alert": QColor(38, 38, 41, 255),
            }
        return {
            "shell": QColor(248, 248, 252, 255),
            "shell_expanded": QColor(248, 248, 252, 255),
            "cell": QColor(255, 255, 255, 255),
            "cell_active": QColor(255, 255, 255, 255),
            "stroke": QColor(255, 255, 255, 166),
            "inner_stroke": QColor(0, 0, 0, 18),
            "text": QColor(28, 28, 30, 238),
            "muted": QColor(72, 72, 74, 226),
            "alert": QColor(248, 248, 252, 255),
        }

    def _time_reference_palette(self, theme: dict[str, QColor], color: QColor) -> dict[str, QColor]:
        if not self._system_dark_mode():
            return {
                "surface": QColor(246, 246, 248, 255),
                "surface_lowest": QColor(238, 239, 242, 255),
                "module": QColor(255, 255, 255, 255),
                "module_active": QColor(248, 250, 255, 255),
                "module_stroke": QColor(214, 216, 222, 220),
                "handle": QColor(226, 229, 235, 255),
                "divider": QColor(196, 200, 210, 190),
                "accent": QColor(255, 184, 0, 255),
                "accent_soft": QColor(255, 184, 0, 20),
                "accent_track": QColor(255, 184, 0, 54),
                "control_stroke": QColor(218, 154, 18, 170),
                "active": QColor(2, 170, 74, 255),
                "text": QColor(24, 24, 27, 242),
                "muted": QColor(92, 92, 100, 220),
                "muted_dim": QColor(128, 128, 136, 170),
                "thumb": QColor(255, 255, 255, 246),
                "off_track": QColor(232, 234, 238, 230),
                "note_stroke": QColor(142, 150, 164, 185),
            }
        return {
            "surface": QColor(19, 19, 19, 255),
            "surface_lowest": QColor(14, 14, 14, 255),
            "module": QColor(28, 27, 27, 255),
            "module_active": QColor(32, 32, 31, 255),
            "module_stroke": QColor(255, 184, 0, 255),
            "handle": QColor(42, 42, 42, 255),
            "divider": QColor(68, 68, 68, 190),
            "accent": QColor(255, 184, 0, 255),
            "accent_soft": QColor(255, 184, 0, 16),
            "accent_track": QColor(255, 184, 0, 46),
            "control_stroke": QColor(255, 184, 0, 255),
            "active": QColor(2, 201, 83, 255),
            "text": QColor(229, 226, 225, 242),
            "muted": QColor(213, 196, 171, 225),
            "muted_dim": QColor(158, 143, 120, 170),
            "thumb": QColor(245, 244, 241, 246),
            "off_track": QColor(19, 19, 19, 230),
            "note_stroke": QColor(142, 150, 164, 185),
        }

    def _accent_pair(self) -> tuple[QColor, QColor]:
        base = self._dominant_album_color()
        companion = self._safe_wave_color(self.secondary_color)
        base_hue, base_sat, _base_val, _base_alpha = base.getHsvF()
        companion_hue, companion_sat, _companion_val, _companion_alpha = companion.getHsvF()
        if base_hue < 0 or base_sat < 0.12:
            return self._gray_pair(base)
        if companion_hue < 0 or companion_sat < 0.12 or self._hue_distance(base_hue, companion_hue) < 0.08:
            companion = QColor.fromHsvF((base_hue + 0.16) % 1.0, 0.72, 0.58, 1.0)
        return base, companion

    def _gray_pair(self, color: QColor) -> tuple[QColor, QColor]:
        gray = color.red() if color.red() == color.green() == color.blue() else int(color.valueF() * 255)
        gray = max(56, min(220, gray))
        base = QColor(gray, gray, gray)
        companion = QColor(max(28, gray - 40), max(28, gray - 40), max(28, gray - 40))
        return base, companion

    def _hue_distance(self, a: float, b: float) -> float:
        delta = abs(a - b) % 1.0
        return min(delta, 1.0 - delta)

    def _gray_step(self, color: QColor, amount: float) -> QColor:
        amount = max(0.0, min(1.0, amount))
        base = color.redF() * 255.0
        value = max(28.0, min(234.0, base + amount * 84.0))
        gray = int(value)
        return QColor(gray, gray, gray)

    def _reactive_color(self, color: QColor, hue_shift: float, sat_boost: float, val_boost: float) -> QColor:
        hue, sat, val, _alpha = color.getHsvF()
        if hue < 0:
            hue = 0.58
        return QColor.fromHsvF(
            (hue + hue_shift) % 1.0,
            max(0.0, min(1.0, sat + sat_boost)),
            max(0.0, min(1.0, val + val_boost)),
            1.0,
        )

    def _mix_color(self, a: QColor, b: QColor, amount: float) -> QColor:
        amount = max(0.0, min(1.0, amount))
        return QColor(
            int(a.red() + (b.red() - a.red()) * amount),
            int(a.green() + (b.green() - a.green()) * amount),
            int(a.blue() + (b.blue() - a.blue()) * amount),
        )

    def _duotone_balance_amount(self, expanded: bool) -> float:
        key = "expanded_duotone_balance" if expanded else "compact_duotone_balance"
        return max(0.0, min(1.0, float(getattr(self, key, 50)) / 100.0))

    def _balanced_pair(self, primary: QColor, secondary: QColor, expanded: bool) -> tuple[QColor, QColor]:
        balance = self._duotone_balance_amount(expanded)
        delta = balance - 0.5
        if abs(delta) <= 0.001:
            return primary, secondary
        primary_mix = max(0.0, delta) * 0.44
        secondary_mix = max(0.0, -delta) * 0.44
        return self._mix_color(primary, secondary, primary_mix), self._mix_color(secondary, primary, secondary_mix)

    def _audio_bands(self) -> tuple[float, float, float]:
        return getattr(self, "current_audio_bands", (0.0, 0.0, 0.0))

    def _ambient_mist_color(self, color: QColor, saturation_boost: float = 0.0, lightness_boost: float = 0.0) -> QColor:
        hue, saturation, lightness, _alpha = color.getHslF()
        if hue < 0:
            hue = 0.58
            saturation = 0.52
            lightness = 0.47
        saturation = max(0.44, min(0.86, saturation + saturation_boost))
        lightness = max(0.30, min(0.66, lightness + lightness_boost))
        return QColor.fromHslF(hue, saturation, lightness, 1.0)

    def _album_mist_palette(self) -> tuple[QColor, QColor, QColor]:
        primary = self._ambient_mist_color(self.album_color, 0.18, 0.03)
        secondary = self._ambient_mist_color(self.secondary_color, 0.20, 0.04)
        bridge = self._mix_color(primary, secondary, 0.50)
        return primary, self._ambient_mist_color(bridge, 0.16, 0.03), secondary

    def _album_mist_palette_for_state(self, expanded: bool) -> tuple[QColor, QColor, QColor]:
        primary, bridge, secondary = self._album_mist_palette()
        primary, secondary = self._balanced_pair(primary, secondary, expanded)
        balance = self._duotone_balance_amount(expanded)
        bridge = self._mix_color(primary, secondary, balance)
        return primary, self._ambient_mist_color(bridge, 0.16, 0.03), secondary

    def _music_flow_amount(self) -> float:
        return max(0.0, min(1.0, float(getattr(self, "music_flow_intensity", 0.74))))

    def _xiaomi_duotone_palette(self) -> tuple[QColor, QColor, QColor]:
        base = self._safe_wave_color(self.album_color)
        hue, sat, val, _alpha = base.getHsvF()
        if hue < 0:
            hue = 0.78
            sat = 0.72
            val = 0.62
        main = QColor.fromHsvF(hue, max(0.78, min(1.0, sat + 0.26)), max(0.72, min(0.98, val + 0.18)), 1.0)
        reverse_hue = (hue + 0.50) % 1.0
        accent = QColor.fromHsvF(reverse_hue, max(0.58, min(0.90, sat + 0.08)), max(0.30, min(0.48, val * 0.58)), 1.0)
        bridge = self._mix_color(main, accent, 0.42)
        bridge_h, bridge_s, bridge_v, _ = bridge.getHsvF()
        if bridge_h >= 0:
            bridge = QColor.fromHsvF(bridge_h, max(0.64, bridge_s), max(0.54, bridge_v), 1.0)
        return main, accent, bridge

    def _full_duotone_palette(self) -> tuple[QColor, QColor, QColor]:
        return self._full_duotone_palette_for_state(bool(getattr(self, "expanded", False)))

    def _full_duotone_palette_for_state(self, expanded: bool) -> tuple[QColor, QColor, QColor]:
        base = self._safe_wave_color(self.album_color)
        companion = self._safe_wave_color(self.secondary_color)
        hue, sat, val, _alpha = base.getHsvF()
        comp_hue, comp_sat, comp_val, _comp_alpha = companion.getHsvF()
        if hue < 0:
            hue = 0.78
            sat = 0.72
            val = 0.62
        if comp_hue < 0 or comp_sat < 0.14 or self._hue_distance(hue, comp_hue) < 0.06:
            comp_hue = (hue + 0.50) % 1.0
            comp_sat = max(0.48, min(0.78, sat + 0.02))
            comp_val = max(0.30, min(0.58, val * 0.72))
        main = QColor.fromHsvF(hue, max(0.84, min(1.0, sat + 0.28)), max(0.72, min(0.98, val + 0.18)), 1.0)
        accent = QColor.fromHsvF(comp_hue, max(0.48, min(0.80, comp_sat + 0.06)), max(0.22, min(0.42, comp_val * 0.62)), 1.0)
        main, accent = self._balanced_pair(main, accent, expanded)
        bridge = self._mix_color(main, accent, 0.40)
        bridge_h, bridge_s, bridge_v, _ = bridge.getHsvF()
        if bridge_h >= 0:
            bridge = QColor.fromHsvF(bridge_h, max(0.70, bridge_s), max(0.46, min(0.72, bridge_v + 0.08)), 1.0)
        return main, accent, bridge

    def _mist_alpha(self, opacity: float, intensity: float) -> int:
        return int(max(0, min(196, opacity * 255.0 * intensity)))

    def _ambient_boost(self) -> tuple[float, float, float, float]:
        value = max(0.0, min(1.0, float(getattr(self, "ambient_intensity", 0.0))))
        eased = value * value * (3.0 - 2.0 * value)
        return 1.04 + eased * 1.04, eased, 1.0 + eased * 0.32, 1.0 + eased * 0.56

    def _boost_mist_color(self, color: QColor, boost: float) -> QColor:
        hue, saturation, lightness, _alpha = color.getHslF()
        if hue < 0:
            return color
        saturation = max(0.0, min(0.92, saturation + boost * 0.36))
        lightness = max(0.0, min(0.84, lightness + boost * 0.15))
        return QColor.fromHslF(hue, saturation, lightness, 1.0)

    def _paint_mist_ellipse(self, painter: QPainter, center: QPointF, radius_x: float, radius_y: float, color: QColor, alpha: int):
        if alpha <= 0:
            return
        painter.save()
        painter.translate(center)
        painter.scale(max(1.0, radius_x), max(1.0, radius_y))
        glow = QRadialGradient(QPointF(0.0, 0.0), 1.0)
        glow.setColorAt(0.00, QColor(min(255, int(color.red() * 1.05 + 14)), min(255, int(color.green() * 1.05 + 14)), min(255, int(color.blue() * 1.05 + 14)), int(alpha * 0.78)))
        glow.setColorAt(0.20, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.58)))
        glow.setColorAt(0.46, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.28)))
        glow.setColorAt(0.70, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.10)))
        glow.setColorAt(0.88, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.025)))
        glow.setColorAt(1.00, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(-1.0, -1.0, 2.0, 2.0))
        painter.restore()

    def _paint_bilateral_diffraction_ambience(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        painter.save()
        inset = self._u(1.2)
        bleed = self._u(1.0)
        paint_rect = rect.adjusted(-bleed, -bleed, bleed, bleed)
        clip_rect = rect.adjusted(inset - bleed, inset - bleed, -inset + bleed, -inset + bleed)
        painter.setClipPath(self._rounded_path(clip_rect, max(1.0, radius - inset + bleed)))
        painter.setPen(Qt.PenStyle.NoPen)

        fill = self._active_lyric_fill_color()
        secondary = self._safe_wave_color(QColor(getattr(self, "secondary_color", fill)))
        bridge = self._mix_color(fill, secondary, 0.46)
        low, mid, high = self._audio_bands()
        intensity, boost, size_boost, opacity_boost = self._ambient_boost()
        flow = 0.42 + self._music_flow_amount() * 0.58
        if not bool(getattr(self, "media_is_playing", True)):
            intensity *= 0.76
            flow *= 0.42
        fill = self._boost_mist_color(fill, boost * 0.52)
        secondary = self._boost_mist_color(secondary, boost * 0.34)
        bridge = self._boost_mist_color(bridge, boost * 0.28)

        now = time.monotonic()
        phase = float(getattr(self, "ambient_phase", 0.0)) * (0.92 + flow * 0.48) + now * (0.28 + flow * 0.36)
        values = list(getattr(self, "wave_values", []) or [0.25] * 36)

        center = rect.center() + QPointF(self._u(8.0) * flow * math.sin(phase * 0.56), self._u(2.8) * math.sin(phase * 0.41 + 1.7))
        center_radius = rect.width() * (0.56 if expanded else 0.46)
        center_glow = QRadialGradient(center, center_radius)
        center_alpha = self._mist_alpha((0.020 if expanded else 0.014) * opacity_boost + mid * 0.006, intensity)
        center_glow.setColorAt(0.00, QColor(bridge.red(), bridge.green(), bridge.blue(), center_alpha))
        center_glow.setColorAt(0.34, QColor(bridge.red(), bridge.green(), bridge.blue(), int(center_alpha * 0.42)))
        center_glow.setColorAt(0.68, QColor(0, 0, 0, 0))
        center_glow.setColorAt(1.00, QColor(0, 0, 0, 0))
        painter.fillRect(paint_rect, center_glow)

        side_specs = (
            (-1, fill, bridge, low, 0.0),
            (1, secondary, bridge, high, 2.1),
        )
        for side, side_color, halo_color, band, seed in side_specs:
            side_x = rect.left() - rect.width() * (0.055 if expanded else 0.115) if side < 0 else rect.right() + rect.width() * (0.055 if expanded else 0.115)
            side_y = rect.center().y() + self._u(7.0) * flow * math.sin(phase * 0.72 + seed)
            wash_alpha = self._mist_alpha((0.115 if expanded else 0.094) * opacity_boost + band * 0.028, intensity)
            if expanded:
                wash_rx = rect.width() * (0.28 * size_boost + band * 0.026)
                wash_ry = rect.height() * (1.26 * size_boost + mid * 0.044)
                halo_rx = rect.width() * (0.18 + band * 0.018)
                halo_ry = rect.height() * (0.72 + mid * 0.036)
            else:
                wash_rx = wash_ry = rect.height() * (1.18 * size_boost + band * 0.12 + mid * 0.06)
                halo_rx = halo_ry = rect.height() * (0.82 + band * 0.08 + mid * 0.04)
            self._paint_mist_ellipse(
                painter,
                QPointF(side_x, side_y),
                wash_rx,
                wash_ry,
                side_color,
                wash_alpha,
            )
            self._paint_mist_ellipse(
                painter,
                QPointF(side_x + side * self._u(10.0), side_y),
                halo_rx,
                halo_ry,
                halo_color,
                int(wash_alpha * 0.44),
            )

        ring_count = 7
        for side, side_color, _halo_color, band, seed in side_specs:
            center_x = rect.left() - rect.width() * (0.030 if expanded else 0.130) if side < 0 else rect.right() + rect.width() * (0.030 if expanded else 0.130)
            center_y = rect.center().y() + self._u(5.5) * flow * math.sin(phase * 0.60 + seed)
            for index in range(ring_count):
                wave_sample = float(values[(index * 5 + (0 if side < 0 else 2)) % len(values)])
                travel = (now * (0.075 + flow * 0.080) + index / ring_count + seed * 0.061) % 1.0
                falloff = (1.0 - travel) ** 1.36
                if expanded:
                    rx = rect.width() * (0.105 + travel * 0.42)
                    ry = rect.height() * (0.48 + travel * 1.04)
                    rx += self._u(8.0) * flow * wave_sample
                    ry += self._u(7.0) * flow * (0.34 + band)
                else:
                    ring_radius = rect.height() * (0.62 + travel * 1.42)
                    ring_radius += self._u(10.0) * flow * (0.34 + band + wave_sample * 0.55)
                    rx = ry = ring_radius
                alpha = int(max(0, min(132, falloff * (0.36 + band * 0.22 + wave_sample * 0.10) * 255.0 * intensity * opacity_boost)))
                if not expanded and alpha <= 2:
                    alpha = 3
                if alpha <= 2:
                    continue
                tint = self._mix_color(side_color, bridge, 0.28 + 0.22 * travel)
                pen_color = QColor(tint.red(), tint.green(), tint.blue(), alpha)
                pen_width = self._u(1.0 + (0.9 if expanded else 0.55) * (1.0 - travel) + wave_sample * 0.36)
                painter.setPen(QPen(pen_color, pen_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                arc_rect = QRectF(center_x - rx, center_y - ry, rx * 2.0, ry * 2.0)
                if side < 0:
                    painter.drawArc(arc_rect, -90 * 16, 180 * 16)
                else:
                    painter.drawArc(arc_rect, 90 * 16, 180 * 16)

        edge_shadow = QLinearGradient(rect.left(), 0, rect.right(), 0)
        edge_shadow.setColorAt(0.00, QColor(0, 0, 0, 50))
        edge_shadow.setColorAt(0.18, QColor(0, 0, 0, 0))
        edge_shadow.setColorAt(0.50, QColor(0, 0, 0, 0))
        edge_shadow.setColorAt(0.82, QColor(0, 0, 0, 0))
        edge_shadow.setColorAt(1.00, QColor(0, 0, 0, 50))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(edge_shadow)
        painter.fillRect(paint_rect, edge_shadow)
        painter.restore()

    def _paint_bottom_ambience(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        style = getattr(self, "music_ambience_style", "sea_level")
        if style in {"pure_black", "bilateral_diffraction"}:
            return
        if style == "duotone_full":
            self._paint_full_duotone_ambience(painter, rect, radius, expanded)
            return
        if style == "sea_level":
            self._paint_xiaomi_duotone_ambience(painter, rect, radius, expanded)
            return

        painter.save()
        inset = self._u(1.4)
        bleed = self._u(1.0)
        paint_rect = rect.adjusted(-bleed, -bleed, bleed, bleed)
        clip_rect = rect.adjusted(inset - bleed, inset - bleed, -inset + bleed, -inset + bleed)
        painter.setClipPath(self._rounded_path(clip_rect, max(1.0, radius - inset + bleed)))
        painter.setPen(Qt.PenStyle.NoPen)

        primary, bridge, secondary = self._album_mist_palette()
        low, mid, high = self._audio_bands()
        now = time.monotonic()
        intensity, boost, size_boost, opacity_boost = self._ambient_boost()
        if not self.media_is_playing:
            intensity *= 0.80
        primary = self._boost_mist_color(primary, boost)
        bridge = self._boost_mist_color(bridge, boost)
        secondary = self._boost_mist_color(secondary, boost)

        layer_top = paint_rect.top() + paint_rect.height() * (0.24 if expanded else 0.30)
        layer_rect = QRectF(paint_rect.left(), layer_top, paint_rect.width(), paint_rect.bottom() - layer_top)
        field = QLinearGradient(0, layer_rect.top(), 0, layer_rect.bottom())
        field.setColorAt(0.00, QColor(bridge.red(), bridge.green(), bridge.blue(), 0))
        field.setColorAt(0.16, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.060 + mid * 0.022, intensity)))
        field.setColorAt(0.39, QColor(primary.red(), primary.green(), primary.blue(), self._mist_alpha(0.125 + low * 0.034, intensity)))
        field.setColorAt(0.66, QColor(secondary.red(), secondary.green(), secondary.blue(), self._mist_alpha(0.165 + high * 0.038, intensity)))
        field.setColorAt(1.00, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.145 + mid * 0.032, intensity)))
        painter.fillRect(layer_rect, field)

        def wave(duration: float, delay: float, offset: float = 0.0) -> float:
            return math.sin(((now + delay) / duration) * math.tau + offset)

        def breathe(duration: float, delay: float, offset: float = 0.0) -> float:
            return 0.5 + 0.5 * wave(duration, delay, offset)

        color_blocks = (
            (primary, 14.0, 0.0, 0.18, 0.56, 28.0, 11.0, 0.96 * size_boost, 0.38 * size_boost, 0.24 * opacity_boost, 0.118 * opacity_boost, low, 0.0),
            (bridge, 17.0, -4.0, 0.50, 0.60, 32.0, 13.0, 1.18 * size_boost, 0.47 * size_boost, 0.23 * opacity_boost, 0.110 * opacity_boost, mid, 1.3),
            (secondary, 20.0, -8.0, 0.82, 0.55, 26.0, 12.0, 0.94 * size_boost, 0.38 * size_boost, 0.24 * opacity_boost, 0.118 * opacity_boost, high, 2.4),
            (self._mix_color(primary, bridge, 0.38), 23.0, -11.0, 0.35, 0.38, 20.0, 9.0, 0.78 * size_boost, 0.28 * size_boost, 0.16 * opacity_boost, 0.074 * opacity_boost, mid, 3.1),
            (self._mix_color(secondary, bridge, 0.42), 26.0, -15.0, 0.66, 0.41, 22.0, 9.0, 0.74 * size_boost, 0.27 * size_boost, 0.15 * opacity_boost, 0.070 * opacity_boost, high, 4.2),
        )
        for glow_color, duration, delay, x_ratio, y_ratio, drift_x, drift_y, width_ratio, height_ratio, base_opacity, opacity_span, band, offset in color_blocks:
            breath = breathe(duration, delay, offset)
            shimmer = breathe(duration * 1.73, delay * 0.61, offset + 2.2)
            center = QPointF(
                rect.left() + rect.width() * x_ratio + self._u(drift_x) * wave(duration, delay, offset + 0.4),
                layer_rect.top() + layer_rect.height() * y_ratio + self._u(drift_y) * wave(duration, delay, offset + 1.8),
            )
            scale = 0.96 + breath * 0.14
            alpha = self._mist_alpha(base_opacity + opacity_span * shimmer + band * 0.034, intensity)
            rx = rect.width() * (width_ratio + breath * 0.10 + band * 0.018)
            ry = rect.height() * (height_ratio + breath * 0.055 + band * 0.014)
            self._paint_mist_ellipse(painter, center, rx * scale * 1.40, ry * scale * 1.34, glow_color, int(alpha * 0.38))
            self._paint_mist_ellipse(painter, center, rx * scale, ry * scale, glow_color, alpha)

        highlight_breath = breathe(18.0, -5.0, 2.8)
        highlight_center = QPointF(rect.left() + rect.width() * 0.50 + self._u(15.0) * wave(18.0, -5.0, 2.4), layer_rect.top() + layer_rect.height() * 0.46 + self._u(6.0) * wave(18.0, -5.0, 0.8))
        highlight = self._mix_color(QColor(255, 255, 255), bridge, 0.76)
        self._paint_mist_ellipse(
            painter,
            highlight_center,
            rect.width() * (0.20 + highlight_breath * 0.035),
            rect.height() * (0.12 + highlight_breath * 0.024),
            highlight,
            self._mist_alpha((0.045 + highlight_breath * 0.030) * opacity_boost, intensity),
        )

        black_blocks = (
            (0.30, 0.46, 19.0, -2.0, 16.0, 8.0, 0.34 * size_boost, 0.16 * size_boost, 0.055, 0.050, 0.0),
            (0.58, 0.54, 24.0, -9.0, 22.0, 9.0, 0.42 * size_boost, 0.18 * size_boost, 0.060, 0.055, 1.6),
            (0.74, 0.40, 28.0, -14.0, 18.0, 7.0, 0.30 * size_boost, 0.14 * size_boost, 0.045, 0.040, 3.0),
        )
        for x_ratio, y_ratio, duration, delay, drift_x, drift_y, width_ratio, height_ratio, base_opacity, opacity_span, offset in black_blocks:
            breath = breathe(duration, delay, offset)
            center = QPointF(
                rect.left() + rect.width() * x_ratio + self._u(drift_x) * wave(duration, delay, offset + 0.7),
                layer_rect.top() + layer_rect.height() * y_ratio + self._u(drift_y) * wave(duration, delay, offset + 2.1),
            )
            alpha = int(max(0, min(70, (base_opacity + opacity_span * breath) * 255.0 * intensity)))
            self._paint_mist_ellipse(
                painter,
                center,
                rect.width() * (width_ratio + breath * 0.055),
                rect.height() * (height_ratio + breath * 0.035),
                QColor(0, 0, 0),
                alpha,
            )

        top_fade = QLinearGradient(0, paint_rect.top(), 0, paint_rect.bottom())
        top_fade.setColorAt(0.00, QColor(0, 0, 0, 236 if expanded else 226))
        top_fade.setColorAt(0.18, QColor(0, 0, 0, 192 if expanded else 178))
        top_fade.setColorAt(0.30, QColor(0, 0, 0, 50 if expanded else 54))
        top_fade.setColorAt(0.42, QColor(0, 0, 0, 6))
        top_fade.setColorAt(0.56, QColor(0, 0, 0, 0))
        top_fade.setColorAt(1.00, QColor(0, 0, 0, 0))
        painter.fillRect(paint_rect, top_fade)

        painter.restore()

    def _paint_full_duotone_ambience(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        painter.save()
        inset = self._u(1.4)
        bleed = self._u(1.0)
        paint_rect = rect.adjusted(-bleed, -bleed, bleed, bleed)
        clip_rect = rect.adjusted(inset - bleed, inset - bleed, -inset + bleed, -inset + bleed)
        painter.setClipPath(self._rounded_path(clip_rect, max(1.0, radius - inset + bleed)))
        painter.setPen(Qt.PenStyle.NoPen)

        main, accent, bridge = self._full_duotone_palette_for_state(expanded)
        low, mid, high = self._audio_bands()
        now = time.monotonic()
        intensity, boost, size_boost, opacity_boost = self._ambient_boost()
        flow = self._music_flow_amount()
        if not self.media_is_playing:
            intensity *= 0.84
            flow *= 0.50
        main = self._boost_mist_color(main, boost * 0.58)
        bridge = self._boost_mist_color(bridge, boost * 0.34)

        def solid_alpha(opacity: float) -> int:
            return int(max(0, min(236, opacity * 255.0 * intensity)))

        base = QLinearGradient(0, paint_rect.top(), 0, paint_rect.bottom())
        base.setColorAt(0.00, QColor(accent.red(), accent.green(), accent.blue(), solid_alpha(0.90 + high * 0.024)))
        base.setColorAt(0.42, QColor(accent.red(), accent.green(), accent.blue(), solid_alpha(0.97 + high * 0.026)))
        base.setColorAt(0.48, QColor(bridge.red(), bridge.green(), bridge.blue(), solid_alpha(0.98 + mid * 0.024)))
        base.setColorAt(0.54, QColor(bridge.red(), bridge.green(), bridge.blue(), solid_alpha(0.98 + mid * 0.024)))
        base.setColorAt(0.62, QColor(main.red(), main.green(), main.blue(), solid_alpha(0.98 + low * 0.026)))
        base.setColorAt(1.00, QColor(main.red(), main.green(), main.blue(), solid_alpha(1.00)))
        painter.fillRect(paint_rect, base)

        def wave(duration: float, delay: float, offset: float = 0.0) -> float:
            return math.sin(((now + delay) / duration) * math.tau + offset)

        def breathe(duration: float, delay: float, offset: float = 0.0) -> float:
            return 0.5 + 0.5 * wave(duration, delay, offset)

        transition_top = paint_rect.top() + paint_rect.height() * 0.40
        transition_height = paint_rect.height() * 0.24
        transition = QLinearGradient(paint_rect.left(), transition_top, paint_rect.right(), transition_top + transition_height)
        transition.setColorAt(0.00, QColor(accent.red(), accent.green(), accent.blue(), 0))
        transition.setColorAt(0.30, QColor(accent.red(), accent.green(), accent.blue(), solid_alpha(0.036 + high * 0.008)))
        transition.setColorAt(0.50, QColor(bridge.red(), bridge.green(), bridge.blue(), solid_alpha(0.092 + mid * 0.016)))
        transition.setColorAt(0.70, QColor(main.red(), main.green(), main.blue(), solid_alpha(0.060 + low * 0.012)))
        transition.setColorAt(1.00, QColor(main.red(), main.green(), main.blue(), 0))
        painter.fillRect(QRectF(paint_rect.left(), transition_top, paint_rect.width(), transition_height), transition)

        shadow = QLinearGradient(0, paint_rect.top(), 0, paint_rect.bottom())
        shadow.setColorAt(0.00, QColor(0, 0, 0, 48 if expanded else 42))
        shadow.setColorAt(0.12, QColor(0, 0, 0, 32 if expanded else 28))
        shadow.setColorAt(0.32, QColor(0, 0, 0, 10))
        shadow.setColorAt(0.62, QColor(0, 0, 0, 4))
        shadow.setColorAt(1.00, QColor(0, 0, 0, 16))
        painter.fillRect(paint_rect, shadow)

        sheen = QLinearGradient(paint_rect.left(), paint_rect.top(), paint_rect.right(), paint_rect.bottom())
        sheen.setColorAt(0.00, QColor(255, 255, 255, 22))
        sheen.setColorAt(0.28, QColor(255, 255, 255, 5))
        sheen.setColorAt(0.54, QColor(255, 255, 255, 0))
        sheen.setColorAt(1.00, QColor(255, 255, 255, 12))
        painter.fillRect(paint_rect, sheen)

        self._paint_full_duotone_interference(painter, rect, main, accent, bridge, intensity, opacity_boost, flow, now, low, mid, high)

        painter.restore()

    def _paint_full_duotone_interference(
        self,
        painter: QPainter,
        rect: QRectF,
        main: QColor,
        accent: QColor,
        bridge: QColor,
        intensity: float,
        opacity_boost: float,
        flow: float,
        now: float,
        low: float,
        mid: float,
        high: float,
    ):
        if flow <= 0.01:
            return

        def drift(seed: float, x: float, y: float, dx_period: float, dy_period: float, amount: float) -> QPointF:
            px = x + amount * flow * (0.20 * math.sin(now / dx_period * math.tau + seed) + 0.08 * math.sin(now / (dx_period * 0.43) * math.tau + seed * 2.1))
            py = y + amount * flow * (0.16 * math.sin(now / dy_period * math.tau + seed * 1.7) + 0.07 * math.sin(now / (dy_period * 0.51) * math.tau + seed * 2.9))
            return QPointF(rect.left() + rect.width() * px, rect.top() + rect.height() * py)

        accent_pulse = 0.5 + 0.5 * math.sin(now / 9.5 * math.tau + 0.4)
        main_pulse = 0.5 + 0.5 * math.sin(now / 11.5 * math.tau + 3.0)
        highlight_pulse = 0.5 + 0.5 * math.sin(now / 15.5 * math.tau + 1.8)

        accent_center = drift(0.3, 0.24, 0.38, 18.0, 24.0, 1.0)
        accent_rx = rect.width() * (0.38 + accent_pulse * 0.24 + high * 0.040)
        accent_ry = rect.height() * (0.48 + accent_pulse * 0.32 + mid * 0.036)
        accent_alpha = int(max(0, min(220, (0.36 + accent_pulse * 0.165 + high * 0.044) * 255.0 * intensity * opacity_boost)))
        self._paint_interference_blob(painter, accent_center, accent_rx, accent_ry, accent, accent_alpha)

        main_center = drift(5.7, 0.76, 0.74, 22.0, 27.0, 0.78)
        main_rx = rect.width() * (0.38 + main_pulse * 0.22 + low * 0.040)
        main_ry = rect.height() * (0.36 + main_pulse * 0.22 + mid * 0.030)
        main_alpha = int(max(0, min(205, (0.30 + main_pulse * 0.135 + low * 0.038) * 255.0 * intensity * opacity_boost)))
        self._paint_interference_blob(painter, main_center, main_rx, main_ry, main, main_alpha)

        mix_progress = 0.5 + 0.5 * math.sin(now / 13.0 * math.tau + 1.2)
        mix_center = QPointF(
            accent_center.x() + (main_center.x() - accent_center.x()) * (0.45 + 0.10 * math.sin(now / 17.0 * math.tau + 0.8)),
            accent_center.y() + (main_center.y() - accent_center.y()) * (0.48 + 0.08 * math.sin(now / 15.0 * math.tau + 2.6)),
        )
        mix_color = self._mix_color(accent, main, 0.50 + 0.12 * math.sin(now / 18.0 * math.tau + 1.7))
        mix_color = self._mix_color(mix_color, bridge, 0.42)
        mix_rx = rect.width() * (0.34 + mix_progress * 0.14 + mid * 0.032)
        mix_ry = rect.height() * (0.36 + mix_progress * 0.18 + mid * 0.030)
        mix_alpha = int(max(0, min(190, (0.20 + mix_progress * 0.105 + mid * 0.034) * 255.0 * intensity * opacity_boost)))
        self._paint_interference_blob(painter, mix_center, mix_rx, mix_ry, mix_color, mix_alpha)

        highlight_center = drift(11.2, 0.50, 0.50, 31.0, 21.0, 0.72)
        highlight = self._mix_color(QColor(255, 255, 255), bridge, 0.72)
        highlight_rx = rect.width() * (0.22 + highlight_pulse * 0.10)
        highlight_ry = rect.height() * (0.26 + highlight_pulse * 0.13)
        highlight_alpha = int(max(0, min(74, (0.050 + highlight_pulse * 0.034 + mid * 0.010) * 255.0 * intensity * opacity_boost)))
        self._paint_interference_blob(painter, highlight_center, highlight_rx, highlight_ry, highlight, highlight_alpha)

    def _paint_interference_blob(self, painter: QPainter, center: QPointF, radius_x: float, radius_y: float, color: QColor, alpha: int):
        if alpha <= 0:
            return
        painter.save()
        painter.translate(center)
        painter.scale(max(1.0, radius_x), max(1.0, radius_y))
        glow = QRadialGradient(QPointF(0.0, 0.0), 1.0)
        glow.setColorAt(0.00, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.58)))
        glow.setColorAt(0.24, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.50)))
        glow.setColorAt(0.52, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.30)))
        glow.setColorAt(0.78, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.115)))
        glow.setColorAt(1.00, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(-1.0, -1.0, 2.0, 2.0))
        painter.restore()

    def _paint_full_duotone_orbs(
        self,
        painter: QPainter,
        rect: QRectF,
        main: QColor,
        accent: QColor,
        bridge: QColor,
        intensity: float,
        opacity_boost: float,
        flow: float,
        now: float,
        low: float,
        mid: float,
        high: float,
    ):
        if flow <= 0.01:
            return
        specs = (
            (0.0, 19.0, 27.0, 35.0, 0.28, 0.40, 0.28, 0.30, low, accent, main, 0.10),
            (6.4, 24.0, 31.0, 41.0, 0.70, 0.60, 0.32, 0.34, high, main, accent, 0.08),
            (13.2, 30.0, 23.0, 47.0, 0.50, 0.50, 0.24, 0.28, mid, bridge, main, 0.06),
        )
        for seed, dx_period, dy_period, pulse_period, base_x, base_y, width_ratio, height_ratio, band, base_color, tint_color, tint_amount in specs:
            x = base_x
            x += flow * (0.23 * math.sin(now / dx_period * math.tau + seed) + 0.11 * math.sin(now / (dx_period * 0.47) * math.tau + seed * 2.4))
            y = base_y
            y += flow * (0.18 * math.sin(now / dy_period * math.tau + seed * 1.6) + 0.10 * math.sin(now / (dy_period * 0.53) * math.tau + seed * 3.1))
            pulse = 0.5 + 0.5 * math.sin(now / pulse_period * math.tau + seed * 0.9)
            slow_tint = tint_amount * (0.5 + 0.5 * math.sin(now / (pulse_period * 1.85) * math.tau + seed * 0.37))
            orb_color = self._mix_color(base_color, tint_color, slow_tint)
            mixed_color = self._mix_color(orb_color, bridge, 0.12 + 0.16 * pulse)
            center = QPointF(
                rect.left() + rect.width() * max(-0.08, min(1.08, x)),
                rect.top() + rect.height() * max(-0.10, min(1.10, y)),
            )
            rx = rect.width() * (width_ratio + pulse * 0.060 + band * 0.018)
            ry = rect.height() * (height_ratio + pulse * 0.054 + mid * 0.012)
            alpha = int(max(0, min(118, (0.155 + pulse * 0.042 + band * 0.026) * 255.0 * intensity * opacity_boost)))
            self._paint_background_blob(painter, center, rx, ry, mixed_color, alpha)

    def _paint_background_blob(self, painter: QPainter, center: QPointF, radius_x: float, radius_y: float, color: QColor, alpha: int):
        if alpha <= 0:
            return
        painter.save()
        painter.translate(center)
        painter.scale(max(1.0, radius_x), max(1.0, radius_y))
        glow = QRadialGradient(QPointF(0.0, 0.0), 1.0)
        glow.setColorAt(0.00, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.46)))
        glow.setColorAt(0.30, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.34)))
        glow.setColorAt(0.62, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.16)))
        glow.setColorAt(0.84, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.045)))
        glow.setColorAt(1.00, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(-1.0, -1.0, 2.0, 2.0))
        painter.restore()

    def _paint_xiaomi_duotone_ambience(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        painter.save()
        inset = self._u(1.4)
        bleed = self._u(1.0)
        paint_rect = rect.adjusted(-bleed, -bleed, bleed, bleed)
        clip_rect = rect.adjusted(inset - bleed, inset - bleed, -inset + bleed, -inset + bleed)
        painter.setClipPath(self._rounded_path(clip_rect, max(1.0, radius - inset + bleed)))
        painter.setPen(Qt.PenStyle.NoPen)

        main, accent, bridge = self._xiaomi_duotone_palette()
        low, mid, high = self._audio_bands()
        now = time.monotonic()
        intensity, boost, size_boost, opacity_boost = self._ambient_boost()
        flow = self._music_flow_amount()
        if not self.media_is_playing:
            intensity *= 0.80
            flow *= 0.45
        main = self._boost_mist_color(main, boost * 0.72)
        bridge = self._boost_mist_color(bridge, boost * 0.42)

        split = paint_rect.top() + paint_rect.height() * (0.34 if expanded else 0.38)
        body = QLinearGradient(0, paint_rect.top(), 0, paint_rect.bottom())
        body.setColorAt(0.00, QColor(0, 0, 0, 0))
        body.setColorAt(0.22, QColor(accent.red(), accent.green(), accent.blue(), self._mist_alpha(0.044 * opacity_boost, intensity)))
        body.setColorAt(0.36, QColor(accent.red(), accent.green(), accent.blue(), self._mist_alpha(0.096 * opacity_boost + mid * 0.020, intensity)))
        body.setColorAt(0.52, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.150 * opacity_boost + high * 0.026, intensity)))
        body.setColorAt(0.76, QColor(main.red(), main.green(), main.blue(), self._mist_alpha(0.245 * opacity_boost + low * 0.040, intensity)))
        body.setColorAt(1.00, QColor(main.red(), main.green(), main.blue(), self._mist_alpha(0.225 * opacity_boost + mid * 0.034, intensity)))
        painter.fillRect(paint_rect, body)

        def wave(duration: float, delay: float, offset: float = 0.0) -> float:
            return math.sin(((now + delay) / duration) * math.tau + offset)

        def breathe(duration: float, delay: float, offset: float = 0.0) -> float:
            return 0.5 + 0.5 * wave(duration, delay, offset)

        flows = (
            (accent, 19.0, 0.0, 0.30, 0.34, 34.0, 13.0, 0.82, 0.28, 0.115, 0.074, mid, 0.0),
            (main, 15.5, -3.0, 0.70, 0.72, 42.0, 16.0, 1.08, 0.44, 0.230, 0.126, low, 1.1),
            (bridge, 22.0, -7.0, 0.48, 0.58, 38.0, 15.0, 1.12, 0.38, 0.172, 0.104, high, 2.4),
            (main, 28.0, -11.0, 0.22, 0.82, 26.0, 10.0, 0.72, 0.26, 0.140, 0.082, mid, 3.5),
            (accent, 31.0, -14.0, 0.78, 0.42, 24.0, 9.0, 0.58, 0.20, 0.070, 0.050, high, 4.4),
        )
        for color, duration, delay, x_ratio, y_ratio, drift_x, drift_y, width_ratio, height_ratio, base_opacity, opacity_span, band, offset in flows:
            breath = breathe(duration, delay, offset)
            shimmer = breathe(duration * 1.6, delay * 0.7, offset + 1.8)
            center = QPointF(
                rect.left() + rect.width() * x_ratio + self._u(drift_x) * flow * wave(duration, delay, offset + 0.6),
                rect.top() + rect.height() * y_ratio + self._u(drift_y) * flow * wave(duration, delay, offset + 2.0),
            )
            alpha = self._mist_alpha(base_opacity + opacity_span * shimmer + band * 0.030, intensity)
            rx = rect.width() * (width_ratio * size_boost + breath * 0.08 + band * 0.016)
            ry = rect.height() * (height_ratio * size_boost + breath * 0.042 + band * 0.012)
            self._paint_mist_ellipse(painter, center, rx * 1.35, ry * 1.30, color, int(alpha * 0.34))
            self._paint_mist_ellipse(painter, center, rx, ry, color, alpha)

        join = QLinearGradient(0, split - rect.height() * 0.18, 0, split + rect.height() * 0.34)
        join.setColorAt(0.00, QColor(accent.red(), accent.green(), accent.blue(), 0))
        join.setColorAt(0.38, QColor(accent.red(), accent.green(), accent.blue(), self._mist_alpha(0.054 * opacity_boost, intensity)))
        join.setColorAt(0.56, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.092 * opacity_boost + high * 0.018, intensity)))
        join.setColorAt(1.00, QColor(main.red(), main.green(), main.blue(), 0))
        painter.fillRect(QRectF(paint_rect.left(), split - paint_rect.height() * 0.18, paint_rect.width(), paint_rect.height() * 0.56), join)

        top_fade = QLinearGradient(0, paint_rect.top(), 0, paint_rect.bottom())
        top_fade.setColorAt(0.00, QColor(0, 0, 0, 238 if expanded else 230))
        top_fade.setColorAt(0.17, QColor(0, 0, 0, 206 if expanded else 190))
        top_fade.setColorAt(0.30, QColor(0, 0, 0, 116 if expanded else 104))
        top_fade.setColorAt(0.43, QColor(0, 0, 0, 30))
        top_fade.setColorAt(0.62, QColor(0, 0, 0, 0))
        top_fade.setColorAt(1.00, QColor(0, 0, 0, 0))
        painter.fillRect(paint_rect, top_fade)

        painter.restore()

    def _music_wave_path(self, rect: QRectF, center_y: float, amplitude: float, thickness: float, phase: float) -> QPainterPath:
        values = self.wave_values or [0.25] * 36
        points_top: list[QPointF] = []
        points_bottom: list[QPointF] = []
        count = max(2, len(values))
        for i, value in enumerate(values):
            pos = i / (count - 1)
            x = rect.left() + rect.width() * pos
            soft_peak = math.sin(pos * math.pi)
            ripple = math.sin(pos * math.tau * 2.0 + phase) * 0.30 + math.sin(pos * math.tau * 4.2 - phase * 0.72) * 0.16
            level = (value - 0.42) * 1.24 + ripple
            y = center_y - amplitude * level * (0.25 + soft_peak * 0.75)
            band = thickness * (0.72 + soft_peak * 0.40 + max(0.0, value - 0.25) * 0.34)
            points_top.append(QPointF(x, y - band * 0.5))
            points_bottom.append(QPointF(x, y + band * 0.5))

        path = QPainterPath(points_top[0])
        for point in points_top[1:]:
            path.lineTo(point)
        for point in reversed(points_bottom):
            path.lineTo(point)
        path.closeSubpath()
        return path

    def _paint_music_light_wave(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        style = getattr(self, "music_ambience_style", "sea_level")
        if style in {"pure_black", "bilateral_diffraction"}:
            return
        if style == "duotone_full":
            return
        if style == "sea_level":
            self._paint_xiaomi_duotone_wave(painter, rect, radius, expanded)
            return

        painter.save()
        inset = self._u(1.4)
        painter.setClipPath(self._rounded_path(rect.adjusted(inset, inset, -inset, -inset), max(1.0, radius - inset)))
        painter.setPen(Qt.PenStyle.NoPen)

        primary, bridge, secondary = self._album_mist_palette_for_state(expanded)
        low, mid, high = self._audio_bands()
        intensity, boost, _size_boost, opacity_boost = self._ambient_boost()
        if not self.media_is_playing:
            intensity *= 0.72
        primary = self._boost_mist_color(primary, boost)
        bridge = self._boost_mist_color(bridge, boost)
        secondary = self._boost_mist_color(secondary, boost)

        now = time.monotonic()
        phase = self.ambient_phase * 1.8 + now * 0.55
        center_y = rect.top() + rect.height() * (0.69 if expanded else 0.63)
        center_y += self._u(4.5) * math.sin(phase * 0.65) + self._u(3.0) * (low - high)
        glow_h = rect.height() * (0.22 if expanded else 0.30) + self._u(12.0) * mid
        glow_rect = QRectF(rect.left(), center_y - glow_h, rect.width(), glow_h * 2.05)

        vertical = QLinearGradient(0, glow_rect.top(), 0, glow_rect.bottom())
        vertical.setColorAt(0.00, QColor(bridge.red(), bridge.green(), bridge.blue(), 0))
        vertical.setColorAt(0.22, QColor(primary.red(), primary.green(), primary.blue(), self._mist_alpha(0.038 * opacity_boost + low * 0.012, intensity)))
        vertical.setColorAt(0.50, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.112 * opacity_boost + mid * 0.024, intensity)))
        vertical.setColorAt(0.74, QColor(secondary.red(), secondary.green(), secondary.blue(), self._mist_alpha(0.082 * opacity_boost + high * 0.018, intensity)))
        vertical.setColorAt(1.00, QColor(primary.red(), primary.green(), primary.blue(), 0))
        painter.fillRect(glow_rect, vertical)

        horizontal = QLinearGradient(rect.left(), 0, rect.right(), 0)
        horizontal.setColorAt(0.00, QColor(primary.red(), primary.green(), primary.blue(), 0))
        horizontal.setColorAt(0.18, QColor(primary.red(), primary.green(), primary.blue(), self._mist_alpha(0.092 * opacity_boost + low * 0.024, intensity)))
        horizontal.setColorAt(0.50, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.132 * opacity_boost + mid * 0.032, intensity)))
        horizontal.setColorAt(0.82, QColor(secondary.red(), secondary.green(), secondary.blue(), self._mist_alpha(0.100 * opacity_boost + high * 0.026, intensity)))
        horizontal.setColorAt(1.00, QColor(secondary.red(), secondary.green(), secondary.blue(), 0))

        amplitude = self._u(9.0 if expanded else 4.0) + self._u(9.0 if expanded else 5.0) * (low * 0.58 + mid * 0.34 + high * 0.20)
        thickness = self._u(13.0 if expanded else 6.0) + self._u(8.0 if expanded else 4.5) * mid
        main_path = self._music_wave_path(rect.adjusted(self._u(5.0), 0, -self._u(5.0), 0), center_y, amplitude, thickness, phase)
        painter.fillPath(main_path, QBrush(horizontal))

        upper_path = self._music_wave_path(rect.adjusted(self._u(18.0), 0, -self._u(18.0), 0), center_y - self._u(10.0), amplitude * 0.56, thickness * 0.42, phase + 1.7)
        upper_color = self._mix_color(QColor(255, 255, 255), bridge, 0.55)
        painter.fillPath(upper_path, QColor(upper_color.red(), upper_color.green(), upper_color.blue(), self._mist_alpha(0.024 * opacity_boost + mid * 0.010, intensity)))

        crest = QPainterPath()
        values = self.wave_values or [0.25] * 36
        count = max(2, len(values))
        for i, value in enumerate(values):
            pos = i / (count - 1)
            x = rect.left() + self._u(10.0) + (rect.width() - self._u(20.0)) * pos
            soft_peak = math.sin(pos * math.pi)
            ripple = math.sin(pos * math.tau * 2.0 + phase) * 0.28 + math.sin(pos * math.tau * 3.6 - phase * 0.68) * 0.12
            y = center_y - amplitude * ((value - 0.40) * 0.72 + ripple) * (0.20 + soft_peak * 0.80)
            if i == 0:
                crest.moveTo(x, y)
            else:
                crest.lineTo(x, y)

        crest_gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        crest_gradient.setColorAt(0.00, QColor(primary.red(), primary.green(), primary.blue(), 0))
        crest_gradient.setColorAt(0.22, QColor(255, 255, 255, self._mist_alpha(0.030 * opacity_boost + low * 0.008, intensity)))
        crest_gradient.setColorAt(0.50, QColor(255, 255, 255, self._mist_alpha(0.058 * opacity_boost + mid * 0.012, intensity)))
        crest_gradient.setColorAt(0.78, QColor(255, 255, 255, self._mist_alpha(0.034 * opacity_boost + high * 0.010, intensity)))
        crest_gradient.setColorAt(1.00, QColor(secondary.red(), secondary.green(), secondary.blue(), 0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QBrush(crest_gradient), self._u(1.6 if expanded else 1.1), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(crest)

        painter.restore()

    def _paint_xiaomi_duotone_wave(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        painter.save()
        inset = self._u(1.4)
        painter.setClipPath(self._rounded_path(rect.adjusted(inset, inset, -inset, -inset), max(1.0, radius - inset)))
        painter.setPen(Qt.PenStyle.NoPen)

        main, accent, bridge = self._xiaomi_duotone_palette()
        low, mid, high = self._audio_bands()
        intensity, boost, _size_boost, opacity_boost = self._ambient_boost()
        flow = self._music_flow_amount()
        if not self.media_is_playing:
            intensity *= 0.70
            flow *= 0.42
        main = self._boost_mist_color(main, boost * 0.60)
        bridge = self._boost_mist_color(bridge, boost * 0.38)

        now = time.monotonic()
        phase = self.ambient_phase * (1.15 + flow * 1.30) + now * (0.38 + flow * 0.45)
        center_y = rect.top() + rect.height() * (0.64 if expanded else 0.60)
        center_y += self._u(8.0) * flow * math.sin(phase * 0.68) + self._u(4.0) * flow * (low - high)

        glow_h = rect.height() * (0.26 if expanded else 0.31) + self._u(18.0) * flow * (0.30 + mid)
        glow_rect = QRectF(rect.left(), center_y - glow_h * 0.74, rect.width(), glow_h * 1.72)
        wash = QLinearGradient(0, glow_rect.top(), 0, glow_rect.bottom())
        wash.setColorAt(0.00, QColor(accent.red(), accent.green(), accent.blue(), 0))
        wash.setColorAt(0.24, QColor(accent.red(), accent.green(), accent.blue(), self._mist_alpha(0.050 * opacity_boost + high * 0.014, intensity)))
        wash.setColorAt(0.48, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.118 * opacity_boost + mid * 0.026, intensity)))
        wash.setColorAt(0.76, QColor(main.red(), main.green(), main.blue(), self._mist_alpha(0.170 * opacity_boost + low * 0.034, intensity)))
        wash.setColorAt(1.00, QColor(main.red(), main.green(), main.blue(), 0))
        painter.fillRect(glow_rect, wash)

        horizontal = QLinearGradient(rect.left(), 0, rect.right(), 0)
        horizontal.setColorAt(0.00, QColor(accent.red(), accent.green(), accent.blue(), 0))
        horizontal.setColorAt(0.18, QColor(accent.red(), accent.green(), accent.blue(), self._mist_alpha(0.076 * opacity_boost + high * 0.018, intensity)))
        horizontal.setColorAt(0.45, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.132 * opacity_boost + mid * 0.028, intensity)))
        horizontal.setColorAt(0.74, QColor(main.red(), main.green(), main.blue(), self._mist_alpha(0.188 * opacity_boost + low * 0.036, intensity)))
        horizontal.setColorAt(1.00, QColor(main.red(), main.green(), main.blue(), 0))

        amplitude = self._u(7.0 if expanded else 3.8) + self._u(15.0 if expanded else 8.0) * flow * (low * 0.58 + mid * 0.30 + high * 0.22)
        thickness = self._u(15.0 if expanded else 7.0) + self._u(14.0 if expanded else 7.0) * flow * (0.28 + mid)
        main_path = self._music_wave_path(rect.adjusted(self._u(5.0), 0, -self._u(5.0), 0), center_y, amplitude, thickness, phase)
        painter.fillPath(main_path, QBrush(horizontal))

        counter = QLinearGradient(rect.left(), 0, rect.right(), 0)
        counter.setColorAt(0.00, QColor(main.red(), main.green(), main.blue(), 0))
        counter.setColorAt(0.32, QColor(main.red(), main.green(), main.blue(), self._mist_alpha(0.060 * opacity_boost + low * 0.014, intensity)))
        counter.setColorAt(0.58, QColor(accent.red(), accent.green(), accent.blue(), self._mist_alpha(0.066 * opacity_boost + high * 0.016, intensity)))
        counter.setColorAt(1.00, QColor(accent.red(), accent.green(), accent.blue(), 0))
        counter_path = self._music_wave_path(rect.adjusted(self._u(16.0), 0, -self._u(16.0), 0), center_y - self._u(12.0), amplitude * 0.62, thickness * 0.42, phase + 2.2)
        painter.fillPath(counter_path, QBrush(counter))

        crest = QPainterPath()
        values = self.wave_values or [0.25] * 36
        count = max(2, len(values))
        for i, value in enumerate(values):
            pos = i / (count - 1)
            x = rect.left() + self._u(10.0) + (rect.width() - self._u(20.0)) * pos
            soft_peak = math.sin(pos * math.pi)
            ripple = math.sin(pos * math.tau * 1.7 + phase) * 0.34 + math.sin(pos * math.tau * 3.2 - phase * 0.60) * 0.16
            y = center_y - amplitude * flow * ((value - 0.38) * 0.74 + ripple) * (0.18 + soft_peak * 0.82)
            if i == 0:
                crest.moveTo(x, y)
            else:
                crest.lineTo(x, y)

        crest_gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        crest_gradient.setColorAt(0.00, QColor(accent.red(), accent.green(), accent.blue(), 0))
        crest_gradient.setColorAt(0.26, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha((0.026 + flow * 0.018) * opacity_boost, intensity)))
        crest_gradient.setColorAt(0.55, QColor(main.red(), main.green(), main.blue(), self._mist_alpha((0.034 + flow * 0.026) * opacity_boost + mid * 0.008, intensity)))
        crest_gradient.setColorAt(0.84, QColor(accent.red(), accent.green(), accent.blue(), self._mist_alpha((0.022 + flow * 0.016) * opacity_boost, intensity)))
        crest_gradient.setColorAt(1.00, QColor(main.red(), main.green(), main.blue(), 0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QBrush(crest_gradient), self._u(1.7 if expanded else 1.1), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(crest)

        painter.restore()

    def _paint_cover(self, painter: QPainter, rect: QRectF, radius: float, image: QImage | None = None, cache_key: str | None = None):
        path = self._rounded_path(rect, radius)
        painter.save()
        painter.setClipPath(path)
        album_art = image if isinstance(image, QImage) else getattr(self, "album_art", QImage())
        if not album_art.isNull():
            scaled_width = int(max(1, rect.width()))
            scaled_height = int(max(1, rect.height()))
            cache_id = cache_key or str(album_art.cacheKey() or getattr(self, "media_track_id", ""))
            scaled_key = (cache_id, scaled_width, scaled_height)
            scaled_cache = getattr(self, "_scaled_album_art_cache", None)
            if scaled_cache is None:
                scaled_cache = {}
                self._scaled_album_art_cache = scaled_cache
            scaled = scaled_cache.get(scaled_key)
            if scaled is None or scaled.isNull():
                scaled = album_art.scaled(
                    scaled_width,
                    scaled_height,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                scaled_cache[scaled_key] = scaled
                if len(scaled_cache) > 8:
                    scaled_cache.pop(next(iter(scaled_cache)))
            source = QRectF(
                (scaled.width() - rect.width()) / 2.0,
                (scaled.height() - rect.height()) / 2.0,
                rect.width(),
                rect.height(),
            )
            painter.drawImage(rect, scaled, source)
            shade = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
            shade.setColorAt(0.00, QColor(255, 255, 255, 42))
            shade.setColorAt(0.36, QColor(255, 255, 255, 6))
            shade.setColorAt(1.00, QColor(0, 0, 0, 42))
            painter.fillPath(path, QBrush(shade))
            painter.setPen(QPen(QColor(255, 255, 255, 48), self._u(1.0)))
            painter.drawPath(path)
            painter.restore()
            return
        album = self._dominant_album_color().lighter(156)
        secondary = self.secondary_color.lighter(148)
        focus = QColor(240, 250, 255)
        fallback = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
        fallback.setColorAt(0.0, QColor(224, 238, 255, 255))
        fallback.setColorAt(0.52, QColor(album.red(), album.green(), album.blue(), 255))
        fallback.setColorAt(1.0, QColor(218, 190, 238, 255))
        painter.fillPath(path, QBrush(fallback))
        gradient = QRadialGradient(rect.center() + QPointF(rect.width() * 0.10, -rect.height() * 0.16), rect.width() * 0.86)
        gradient.setColorAt(0.0, QColor(focus.red(), focus.green(), focus.blue(), 255))
        gradient.setColorAt(0.24, QColor(album.red(), album.green(), album.blue(), 255))
        gradient.setColorAt(0.66, QColor(secondary.red(), secondary.green(), secondary.blue(), 255))
        gradient.setColorAt(1.0, QColor(max(18, album.red() // 4), max(18, album.green() // 4), max(18, album.blue() // 4), 255))
        painter.fillPath(path, QBrush(gradient))
        sheen = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
        sheen.setColorAt(0.00, QColor(255, 255, 255, 58))
        sheen.setColorAt(0.34, QColor(255, 255, 255, 10))
        sheen.setColorAt(1.00, QColor(0, 0, 0, 26))
        painter.fillPath(path, QBrush(sheen))
        highlight = QRadialGradient(rect.center() + QPointF(-rect.width() * 0.12, -rect.height() * 0.18), rect.width() * 0.48)
        highlight.setColorAt(0.0, QColor(234, 250, 255, 232))
        highlight.setColorAt(0.62, QColor(148, 210, 255, 82))
        highlight.setColorAt(1.0, QColor(148, 210, 255, 0))
        painter.fillPath(path, QBrush(highlight))
        painter.setPen(QPen(QColor(255, 255, 255, 48), self._u(1.0)))
        painter.drawPath(path)
        painter.restore()

    def _paint_music_compact(self, painter: QPainter, rect: QRectF):
        if self._should_paint_album_island():
            self.music_cover_rect = QRectF()
            self._paint_album_island_compact(painter, rect)
            return
        two_line = self._has_current_two_line_lyric() and not self._gelv_enabled()
        top_padding = self._u(7 if two_line else 3)
        bottom_padding = self._u(6 if two_line else 6)
        if bool(getattr(self, "compact_lyric_show_cover", True)):
            cover = self._compact_cover_rect(rect)
            self.music_cover_rect = QRectF(cover)
            self._paint_cover(painter, cover, self._u(10 if two_line else 8))
            text_left = cover.right() + self._u(15 if two_line else 12)
        else:
            self.music_cover_rect = QRectF()
            text_left = rect.left() + self._u(10.0)
        text_rect = QRectF(text_left, rect.top() + top_padding, rect.right() - text_left - self._u(25), rect.height() - top_padding - bottom_padding)
        self._paint_compact_lyric_viewport(painter, text_rect)

    def _paint_compact_lyric_viewport(self, painter: QPainter, rect: QRectF):
        painter.save()
        painter.setClipRect(rect)
        if self._should_paint_album_island():
            self._paint_album_island(painter, rect)
            painter.restore()
            return
        if self._gelv_enabled():
            self._paint_gelv_compact_lyrics(painter, rect)
            painter.restore()
            return
        lines = self._compact_lyric_lines()
        if not lines:
            painter.restore()
            return
        index = self.lyric_index % len(lines)
        phase = self._lyric_phase(time.monotonic())
        compact_size = max(12, min(20, int(getattr(self, "lyric_font_size", 18)) - 2))
        display_lines = self._lyric_display_lines(lines[index])
        if len(display_lines) > 1 and rect.height() >= self._u(48.0):
            secondary_bold = int(getattr(self, "dual_line_secondary_weight", 72)) >= 78
            secondary_spacing = int(getattr(self, "dual_line_secondary_spacing", 100))
            first_h = rect.height() * 0.54
            first = QRectF(rect.left(), rect.top() + self._u(2.0), rect.width(), first_h)
            second = QRectF(rect.left(), first.bottom() - self._u(1.0), rect.width(), rect.bottom() - first.bottom() - self._u(1.0))
            self._paint_live_lyric_text(painter, first, display_lines[0], max(12, compact_size - 1), QColor(255, 255, 255, 132), QColor(255, 255, 255, 246), True, phase, centered=True, centered_scroll=True)
            self._paint_live_lyric_text(painter, second, display_lines[1], max(10, compact_size - 4), QColor(255, 255, 255, 96), QColor(255, 255, 255, 204), secondary_bold, phase, centered=True, centered_scroll=True, spacing_percent=secondary_spacing)
        else:
            self._paint_live_lyric_text(painter, rect, display_lines[0] if display_lines else lines[index], compact_size, QColor(255, 255, 255, 132), QColor(255, 255, 255, 246), True, phase, centered=True, centered_scroll=True)
        painter.restore()

    def _paint_gelv_compact_lyrics(self, painter: QPainter, rect: QRectF):
        now = time.monotonic()
        progress = self._gelv_spacing_progress(now)
        for row in self._gelv_line_rects(rect, expanded=False, now=now):
            opacity = max(0.0, min(1.0, float(row.get("opacity", 1.0))))
            if opacity <= 0.01:
                continue
            painter.save()
            painter.setOpacity(opacity)
            self._paint_gelv_line(
                painter,
                row["rect"],
                str(row["text"]),
                float(row["size"]),
                progress,
                active=bool(row["active"]),
                color_fill=bool(row.get("color_fill", False)),
                progress_fill=bool(row.get("progress_fill", False)),
                spacing_percent=row.get("spacing_percent"),
            )
            painter.restore()

    def _paint_gelv_expanded_lyrics(self, painter: QPainter, rect: QRectF):
        now = time.monotonic()
        progress = self._gelv_spacing_progress(now)
        self._paint_lyric_phase_pulse(painter, rect, self._lyric_phase(now))
        inner = rect.adjusted(self._u(18.0), self._u(16.0), -self._u(18.0), -self._u(16.0))
        for row in self._gelv_line_rects(inner, expanded=True, now=now):
            opacity = max(0.0, min(1.0, float(row.get("opacity", 1.0))))
            if opacity <= 0.01:
                continue
            painter.save()
            painter.setOpacity(opacity)
            self._paint_gelv_line(
                painter,
                row["rect"],
                str(row["text"]),
                float(row["size"]),
                progress,
                active=bool(row["active"]),
                color_fill=bool(row.get("color_fill", False)),
                progress_fill=bool(row.get("progress_fill", False)),
                spacing_percent=row.get("spacing_percent"),
            )
            painter.restore()

    def _paint_gelv_line(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        size: float,
        progress: float,
        active: bool,
        color_fill: bool = True,
        progress_fill: bool = False,
        spacing_percent: int | None = None,
    ):
        if not text:
            return
        spacing = int(spacing_percent) if spacing_percent is not None else self._gelv_spacing_percent(progress)
        layout = self._gelv_layout(text, size, spacing_percent=spacing)
        x = self._gelv_line_x(rect, text, size, progress, spacing_percent=spacing)
        if color_fill and bool(getattr(self, "gelv_color_fill_enabled", False)):
            base = QColor(255, 255, 255, 120 if active else 82)
            live = self._gelv_fill_color()
            live.setAlpha(250 if active else 178)
        elif progress_fill:
            base = QColor(255, 255, 255, 128 if active else 82)
            live = QColor(255, 255, 255, 245 if active else 156)
        else:
            base = QColor(255, 255, 255, 245 if active else 156)
            live = QColor(base)
        self._paint_gelv_songscatter_chunks(painter, rect, x, layout, size, base, live, progress, active)

    def _paint_gelv_songscatter_chunks(
        self,
        painter: QPainter,
        rect: QRectF,
        x: float,
        layout: LyricLayout,
        size: float,
        base_color: QColor,
        live_color: QColor,
        progress: float,
        active: bool,
    ):
        chunks = layout.chunks
        if not chunks:
            return
        progress = max(0.0, min(1.0, float(progress)))
        live_width = max(0.0, min(layout.total_width, self._lyric_progress_width(layout, progress)))
        font = self._gelv_font(size, QFont.Weight.Normal)
        painter.setFont(font)
        cursor = x
        for index, chunk in enumerate(chunks):
            width = layout.widths[index]
            if width <= 0.0:
                cursor += width
                continue
            sung_amount = max(0.0, min(1.0, (live_width - (cursor - x)) / max(1.0, width)))
            chunk_color = self._mix_alpha_color(base_color, live_color, sung_amount)
            token_rect = QRectF(cursor, rect.top(), width, rect.height())
            painter.setPen(chunk_color)
            painter.drawText(
                token_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
                chunk,
            )
            cursor += width

    def _paint_album_island(self, painter: QPainter, rect: QRectF):
        painter.save()
        painter.setClipRect(rect)
        raw_slots = self._album_island_slots(rect)
        self._paint_album_island_slots(painter, raw_slots)
        painter.setOpacity(1.0)
        if not any(int(slot.get("side", 0)) > 0 for slot in raw_slots):
            self._paint_album_island_listened_text(painter, rect, raw_slots)
        painter.restore()

    def _paint_album_island_slots(self, painter: QPainter, raw_slots: list[dict[str, object]]):
        slots = sorted(raw_slots, key=lambda slot: int(slot.get("z", 0)))
        for slot in slots:
            painter.setOpacity(float(slot["alpha"]) / 255.0)
            radius = self._u(8.0 if bool(slot["center"]) else 6.0)
            self._paint_album_island_slot(painter, slot, radius)

    def _paint_album_island_compact(self, painter: QPainter, rect: QRectF):
        painter.save()
        painter.setClipRect(rect)
        stage = rect.adjusted(self._u(8.0), self._u(4.0), -self._u(8.0), -self._u(4.0))
        raw_slots = self._album_island_slots(stage, reserve_text=True)
        self._paint_album_island_slots(painter, raw_slots)
        painter.setOpacity(1.0)
        if not any(int(slot.get("side", 0)) > 0 for slot in raw_slots):
            self._paint_album_island_listened_text(painter, rect, raw_slots)
        painter.restore()

    def _paint_album_island_slot(self, painter: QPainter, slot: dict[str, object], radius: float):
        rect = QRectF(slot["rect"])
        image = slot.get("album_art")
        if not bool(slot.get("center")) and (not isinstance(image, QImage) or image.isNull()):
            return
        tilt = float(slot.get("tilt", 0.0) or 0.0)
        perspective = slot.get("perspective")
        painter.save()
        if isinstance(perspective, dict) and not bool(slot.get("center")):
            left_h = max(0.45, min(1.0, float(perspective.get("left_height", 1.0) or 1.0)))
            right_h = max(0.45, min(1.0, float(perspective.get("right_height", 1.0) or 1.0)))
            left_top = rect.center().y() - rect.height() * left_h / 2.0
            left_bottom = rect.center().y() + rect.height() * left_h / 2.0
            right_top = rect.center().y() - rect.height() * right_h / 2.0
            right_bottom = rect.center().y() + rect.height() * right_h / 2.0
            path = QPainterPath(QPointF(rect.left(), left_top))
            path.lineTo(QPointF(rect.right(), right_top))
            path.lineTo(QPointF(rect.right(), right_bottom))
            path.lineTo(QPointF(rect.left(), left_bottom))
            path.closeSubpath()
            painter.setClipPath(path)
            painter.setOpacity(float(slot["alpha"]) / 255.0)
            cache_key = f"album-island:{slot.get('side', 0)}:{image.cacheKey() if isinstance(image, QImage) else 'empty'}"
            self._paint_cover(painter, rect, radius, image if isinstance(image, QImage) else None, cache_key)
            painter.restore()
            return
        if abs(tilt) > 0.01:
            center = rect.center()
            painter.translate(center)
            painter.shear(math.tan(math.radians(tilt)) * 0.34, 0.0)
            painter.translate(-center)
        cache_key = f"album-island:{slot.get('side', 0)}:{image.cacheKey() if isinstance(image, QImage) else 'empty'}"
        self._paint_cover(painter, rect, radius, image if isinstance(image, QImage) else None, cache_key)
        painter.restore()

    def _paint_album_island_listened_text(self, painter: QPainter, rect: QRectF, slots: list[dict[str, object]]):
        center_slot = next((slot for slot in slots if bool(slot.get("center"))), None)
        if center_slot is None:
            return
        cover_rect = QRectF(center_slot["rect"])
        text_rect = QRectF(
            cover_rect.right() + self._u(10.0),
            rect.top(),
            max(0.0, rect.right() - cover_rect.right() - self._u(12.0)),
            rect.height(),
        )
        if text_rect.width() < self._u(46.0):
            return
        painter.save()
        progress = self._album_island_transition_progress()
        painter.setOpacity(max(0.0, min(1.0, progress)))
        expanded = rect.height() >= self._u(92.0)
        painter.setFont(self._font(17 if expanded else 9, QFont.Weight.Bold if expanded else QFont.Weight.DemiBold, shrink=False))
        painter.setPen(QColor(255, 255, 255, 218 if expanded else 152))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._album_island_listened_text())
        painter.restore()

    def _compact_lyric_lines(self) -> list[str]:
        if self._is_instrumental_track():
            return [self._compact_music_text()]
        return [self._clean_lyric_for_width(line) or line for line in self.lyrics] or [self.track_title or self.artist or "正在播放"]

    def _paint_music_expanded(self, painter: QPainter, rect: QRectF):
        if self._should_paint_album_island():
            self.music_cover_rect = QRectF()
            self._paint_album_island_expanded(painter, rect)
            return
        margin = self._u(16)
        cover_size = self._u(58)
        cover = QRectF(rect.left() + margin, rect.top() + self._u(14), cover_size, cover_size)
        self.music_cover_rect = QRectF(cover)
        self._paint_cover(painter, cover, self._u(17))

        controls_w = self._u(132)
        info = QRectF(cover.right() + self._u(15), cover.top() + self._u(2), rect.right() - cover.right() - controls_w - self._u(48), self._u(68))
        self._paint_scrolling_text(painter, QRectF(info.left(), info.top(), info.width(), self._u(34)), self.track_title, 13, QColor(255, 255, 255, 240), True)
        self._paint_scrolling_text(painter, QRectF(info.left(), info.top() + self._u(35), info.width(), self._u(24)), self.artist, 9, QColor(255, 255, 255, 135), False)

        controls_x = rect.right() - margin - controls_w
        labels = ["⏮", "⏸" if self.media_is_playing else "▶", "⏭"]
        commands = ["music:previous", "music:toggle_play_pause", "music:next"]
        self.music_button_rects = {}
        for i, label in enumerate(labels):
            button_size = self._u(36)
            br = QRectF(controls_x + i * self._u(45), cover.center().y() - button_size / 2.0, button_size, button_size)
            self.music_button_rects[commands[i]] = QRectF(br)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 24 if i != 1 else 36))
            painter.drawRoundedRect(br, self._u(13), self._u(13))
            painter.setFont(QFont("Segoe UI Symbol", self._font_size(14), QFont.Weight.Medium))
            painter.setPen(QColor(255, 255, 255, 232))
            painter.drawText(br, Qt.AlignmentFlag.AlignCenter, label)

        viewport = QRectF(rect.left() + self._u(22), cover.bottom() + self._u(10), rect.width() - self._u(44), rect.bottom() - cover.bottom() - self._u(28))
        self._paint_lyric_viewport(painter, viewport)

    def _paint_album_island_expanded(self, painter: QPainter, rect: QRectF):
        painter.save()
        painter.setClipRect(rect)
        margin = self._u(18.0)
        controls_w = self._u(132.0)
        self.music_button_rects = {}

        info = QRectF(rect.left() + margin, rect.top() + self._u(13.0), rect.width() - controls_w - margin * 2.0 - self._u(18.0), self._u(42.0))
        self._paint_scrolling_text(painter, QRectF(info.left(), info.top(), info.width(), self._u(24.0)), self.track_title, 12, QColor(255, 255, 255, 238), True)
        self._paint_scrolling_text(painter, QRectF(info.left(), info.top() + self._u(24.0), info.width(), self._u(18.0)), self.artist, 8, QColor(255, 255, 255, 132), False)

        controls_x = rect.right() - margin - controls_w
        labels = ["\u23ee", "\u23f8" if self.media_is_playing else "\u25b6", "\u23ed"]
        commands = ["music:previous", "music:toggle_play_pause", "music:next"]
        for i, label in enumerate(labels):
            button_size = self._u(34.0)
            br = QRectF(controls_x + i * self._u(44.0), rect.top() + self._u(19.0), button_size, button_size)
            self.music_button_rects[commands[i]] = QRectF(br)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 22 if i != 1 else 34))
            painter.drawRoundedRect(br, self._u(12.0), self._u(12.0))
            painter.setFont(QFont("Segoe UI Symbol", self._font_size(14), QFont.Weight.Medium))
            painter.setPen(QColor(255, 255, 255, 232))
            painter.drawText(br, Qt.AlignmentFlag.AlignCenter, label)

        stage = QRectF(rect.left() + margin, rect.top() + self._u(58.0), rect.width() - margin * 2.0, rect.height() - self._u(76.0))
        raw_slots = self._album_island_slots(stage, reserve_text=True)
        self._paint_album_island_slots(painter, raw_slots)
        painter.setOpacity(1.0)
        if not any(int(slot.get("side", 0)) > 0 for slot in raw_slots):
            self._paint_album_island_listened_text(painter, stage, raw_slots)
        painter.restore()

    def _lyric_transition_progress(self, duration: float = 0.52) -> tuple[float, float]:
        if getattr(self, "lyric_transition_kind", "") == "seek":
            duration = max(duration, 0.46)
        elapsed = max(0.0, min(1.0, (time.monotonic() - self.lyric_changed_at) / max(0.001, duration)))
        eased = elapsed * elapsed * (3.0 - 2.0 * elapsed)
        if elapsed >= 1.0 and getattr(self, "lyric_transition_kind", ""):
            self.lyric_transition_kind = ""
            self.lyric_transition_distance = 0
        return elapsed, eased

    def _lyric_phase(self, now: float) -> dict[str, float]:
        position = self._estimated_media_position(now) + float(getattr(self, "lyric_offset_seconds", 0.0))
        phase = lyric_phase_for_index(
            self.timed_lyrics,
            self.lyric_index,
            position_seconds=position,
            changed_elapsed_seconds=now - getattr(self, "lyric_changed_at", now),
        )
        state = phase.as_paint_state()
        index = int(getattr(self, "lyric_index", 0))
        progress = max(0.0, min(1.0, float(state.get("progress", 0.0))))
        last_index = int(getattr(self, "lyric_last_phase_index", -1))
        last_progress = float(getattr(self, "lyric_last_phase_progress", 0.0))
        last_at = float(getattr(self, "lyric_last_phase_at", now))
        if (
            bool(getattr(self, "media_is_playing", False))
            and state.get("source") == "timed"
            and index == last_index
            and getattr(self, "lyric_transition_kind", "") != "seek"
            and progress + 0.018 < last_progress
        ):
            span = max(0.35, float(state.get("span", 0.35) or 0.35))
            progress = min(1.0, last_progress + max(0.0, now - last_at) / span)
            state["progress"] = progress
            elapsed = max(0.0, span * progress)
            state["elapsed"] = elapsed
            state["remaining"] = max(0.0, span - elapsed)
        self.lyric_last_phase_index = index
        self.lyric_last_phase_progress = progress
        self.lyric_last_phase_at = now
        return state

    def _paint_lyric_phase_pulse(self, painter: QPainter, rect: QRectF, phase: dict[str, float]):
        progress = max(0.0, min(1.0, float(phase.get("progress", 0.0))))
        confidence = max(0.0, min(1.0, float(phase.get("confidence", 0.0))))
        if confidence <= 0.05:
            return
        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 2.2 + progress * math.tau)
        color, companion = self._accent_pair()
        center = QPointF(
            rect.left() + rect.width() * (0.20 + 0.60 * progress),
            rect.center().y() + self._u(math.sin(progress * math.tau) * 5.0),
        )
        radius = max(rect.width() * (0.22 + 0.08 * pulse), self._u(76))
        glow = QRadialGradient(center, radius)
        glow.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), int(34 + 26 * confidence)))
        glow.setColorAt(0.45, QColor(companion.red(), companion.green(), companion.blue(), int(18 + 16 * pulse)))
        glow.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, radius, radius * 0.42)
        painter.restore()

    def _paint_expanded_lyric_row(
        self,
        painter: QPainter,
        row: QRectF,
        text: str,
        size: float,
        alpha: int,
        bold: bool,
        active: bool,
        phase: dict[str, float],
    ):
        lines = self._lyric_display_lines(text)
        if not lines:
            return
        if active and len(lines) > 1:
            secondary_bold = int(getattr(self, "dual_line_secondary_weight", 72)) >= 78
            secondary_spacing = int(getattr(self, "dual_line_secondary_spacing", 100))
            first_h = row.height() * 0.56
            first = QRectF(row.left(), row.top(), row.width(), first_h)
            second = QRectF(row.left(), first.bottom() - self._u(1.0), row.width(), row.bottom() - first.bottom())
            self._paint_live_lyric_text(painter, first, lines[0], max(13, size - 1), QColor(255, 255, 255, 126), QColor(255, 255, 255, alpha), True, phase, centered=True, centered_scroll=True)
            self._paint_live_lyric_text(painter, second, lines[1], max(10, size - 5), QColor(255, 255, 255, 92), QColor(255, 255, 255, max(84, int(alpha * 0.74))), secondary_bold, phase, centered=True, centered_scroll=True, spacing_percent=secondary_spacing)
            return
        text = lines[0] if len(lines) == 1 else "  ".join(lines[:2])
        if active:
            self._paint_live_lyric_text(painter, row, text, size, QColor(255, 255, 255, 126), QColor(255, 255, 255, alpha), bold, phase, centered=True, centered_scroll=True)
        else:
            self._paint_scrolling_text(painter, row, text, size, QColor(255, 255, 255, max(0, min(255, alpha))), bold, centered=True, centered_scroll=True)

    def _paint_lyric_viewport(self, painter: QPainter, rect: QRectF):
        painter.save()
        painter.setClipRect(rect)
        if not self.lyrics:
            painter.restore()
            return
        if self._should_paint_album_island():
            self._paint_album_island(painter, rect)
            painter.restore()
            return
        if self._gelv_enabled():
            self._paint_gelv_expanded_lyrics(painter, rect)
            painter.restore()
            return
        if len(self.lyrics) <= 1 and self._is_instrumental_track():
            now = time.monotonic()
            phase = self._lyric_phase(now)
            self._paint_lyric_phase_pulse(painter, rect, phase)
            row = QRectF(rect.left(), rect.center().y() - self._u(17.0), rect.width(), self._u(34.0))
            text = self.lyrics[0] if self.lyrics else getattr(self, "no_lyrics_text", "暂无歌词")
            self._paint_live_lyric_text(
                painter,
                row,
                text,
                max(14, min(24, int(getattr(self, "lyric_font_size", 18)))),
                QColor(255, 255, 255, 110),
                QColor(255, 255, 255, 218),
                True,
                phase,
                centered=True,
                centered_scroll=True,
            )
            painter.restore()
            return
        active_size = max(14, min(24, int(getattr(self, "lyric_font_size", 18))))
        side_size = max(10, active_size - 7)
        active_lines = self._current_lyric_display_lines()
        active_line_h = self._u(max(30.0, active_size + 12.0))
        active_h = active_line_h * (1.55 if len(active_lines) > 1 else 1.0)
        side_h = self._u(max(22.0, side_size + 11.0))
        gap = self._u(7.0 if len(active_lines) > 1 else 9.0)
        center_row = QRectF(rect.left(), rect.center().y() - active_h / 2.0, rect.width(), active_h)
        now = time.monotonic()
        active_phase = self._lyric_phase(now)
        self._paint_lyric_phase_pulse(painter, rect, active_phase)
        is_seek_transition = (
            getattr(self, "lyric_transition_kind", "") == "seek"
            and self.prev_lyric_index != self.lyric_index
        )
        elapsed, eased = self._lyric_transition_progress(0.48 if is_seek_transition else 0.42)
        slide_distance = self._u(9.0) if is_seek_transition else active_line_h * 0.82
        slide = (1.0 - eased) * slide_distance
        live_progress = max(0.0, min(1.0, float(active_phase.get("progress", 0.0))))
        lyric_bloom = math.sin(live_progress * math.pi)
        live_lift = lyric_bloom * self._u(4.2)
        active_y = slide - live_lift if self.prev_lyric_index != self.lyric_index else -live_lift
        next_parallax = self._u(3.0 * lyric_bloom)
        rows: list[tuple[int, float, float, float, bool, int, bool]] = []
        if len(self.lyrics) > 1:
            rows.append((self.lyric_index - 1, -side_h - gap + active_y * 0.18, side_h, side_size, False, 70, False))
        if is_seek_transition and elapsed < 0.48:
            rows.append((self.prev_lyric_index, -self._u(8.0) * eased, side_h, side_size, False, int(52 * (1.0 - eased)), False))
        elif self.prev_lyric_index != self.lyric_index and elapsed < 0.30:
            out = min(1.0, elapsed / 0.30)
            rows.append((self.prev_lyric_index, -out * active_line_h * 0.96, active_line_h, active_size, True, int(78 * (1.0 - out)), False))
        rows.append((self.lyric_index, active_y, active_h, active_size, True, 246, True))
        if len(self.lyrics) > 1:
            next_slide = self._u(4.0) * (1.0 - eased) if is_seek_transition else slide
            rows.append((self.lyric_index + 1, active_h + gap + next_slide + next_parallax, side_h, side_size, False, 76, False))

        painted: set[tuple[int, bool]] = set()
        for raw_index, y_offset, row_h, size, bold, base_alpha, is_active in rows:
            idx = raw_index % len(self.lyrics)
            key = (idx, is_active)
            if key in painted:
                continue
            painted.add(key)
            if not is_active and raw_index == self.prev_lyric_index and self.prev_lyric_index != self.lyric_index:
                alpha = base_alpha
            else:
                alpha = int(base_alpha * ((0.18 + 0.82 * eased) if is_seek_transition else (0.30 + 0.70 * elapsed)))
            if alpha <= 3:
                continue
            row = QRectF(center_row)
            row.moveTop(center_row.top() + y_offset)
            row.setHeight(row_h)
            if row.bottom() < rect.top() + self._u(2) or row.top() > rect.bottom() - self._u(2):
                continue
            painter.save()
            painter.setOpacity(max(0.0, min(1.0, alpha / 255.0)))
            self._paint_expanded_lyric_row(painter, row, self.lyrics[idx], size, alpha, bold, is_active, active_phase)
            painter.restore()
        painter.restore()

    def _paint_plugin_solid_shell(self, painter: QPainter, rect: QRectF, radius: float, folded: bool):
        theme = self._plugin_theme_colors()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme["shell"] if folded else theme["shell_expanded"])
        painter.drawRoundedRect(rect, radius, radius)
        inset = self._u(0.8)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        stroke = QColor(theme["stroke"])
        stroke.setAlpha(min(stroke.alpha(), 166 if folded else 118))
        painter.setPen(QPen(stroke, self._u(1.0)))
        painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), max(1.0, radius - inset), max(1.0, radius - inset))
        inner_stroke = QColor(theme["inner_stroke"])
        inner_stroke.setAlpha(min(inner_stroke.alpha(), 18 if folded else 14) if not self._system_dark_mode() else inner_stroke.alpha())
        painter.setPen(QPen(inner_stroke, self._u(0.8)))
        painter.drawRoundedRect(rect.adjusted(inset * 1.8, inset * 1.8, -inset * 1.8, -inset * 1.8), max(1.0, radius - inset * 1.8), max(1.0, radius - inset * 1.8))
        painter.restore()

    def _paint_plugin(self, painter: QPainter, rect: QRectF, folded: bool):
        radius = rect.height() / 2 if folded else min(self._u(24), rect.height() / 2)
        joined_panel = bool(not folded and self._plugin_panel_visible())
        if not joined_panel:
            self._paint_plugin_solid_shell(painter, rect, radius, folded=folded)
        if folded or rect.height() < self._u(22):
            return

        plugins = [
            (
                label,
                detail,
                summary,
                color,
                icon,
                plugin_id,
                status,
                alert_title,
                alert_body,
                alert_priority,
                progress,
            )
            for label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, progress in (self._plugin_tuple(plugin) for plugin in self.plugin_samples)
        ][:self.enabled_plugin_count]
        if not plugins:
            return

        now = time.monotonic()
        count = len(plugins)
        active_focus = self._active_plugin_focus(now)
        strip_expand_active = bool(self.hovered_key == "plugin" or self._plugin_panel_visible())
        weights = self._plugin_focus_weights(count, now, expand_active=strip_expand_active)
        cells = self._plugin_item_rects(rect, now, expand_active=strip_expand_active)

        for i, (label, detail, summary, color, icon, _plugin_id, status, _alert_title, _alert_body, _alert_priority, progress) in enumerate(plugins):
            cell = cells[i][1]
            sr = cell.adjusted(self._u(5), self._u(6), -self._u(5), -self._u(6))
            focus_weight = weights[i] if i < len(weights) else 0.0
            if self.expanded and strip_expand_active and i == active_focus:
                self._paint_plugin_focus_cell(painter, sr, label, detail, color, icon, focus_weight, False, status, summary=summary, progress=progress)
            else:
                self._paint_plugin_compact_cell(painter, sr, label, detail, summary, color, i == active_focus, status, progress)

    def _plugin_panel_shell_rect(self, panel_rect: QRectF, strip_rect: QRectF | None = None) -> QRectF:
        if strip_rect is None or strip_rect.isNull():
            motion = self.motions.get("plugin") if hasattr(self, "motions") else None
            strip_rect = QRectF(motion.rect) if motion and motion.opacity > 0.02 else QRectF()
        if strip_rect.isNull():
            return QRectF(panel_rect)
        return QRectF(
            min(panel_rect.left(), strip_rect.left()),
            min(panel_rect.top(), strip_rect.top()),
            max(panel_rect.right(), strip_rect.right()) - min(panel_rect.left(), strip_rect.left()),
            max(panel_rect.bottom(), strip_rect.bottom()) - min(panel_rect.top(), strip_rect.top()),
        )

    def _paint_plugin_panel(self, painter: QPainter, rect: QRectF):
        shell_rect = self._plugin_panel_shell_rect(rect)
        radius = min(self._u(26.0), shell_rect.height() / 4.0)
        self._paint_plugin_solid_shell(painter, shell_rect, radius, folded=False)
        plugins = [
            (
                label,
                detail,
                summary,
                color,
                icon,
                plugin_id,
                status,
                alert_title,
                alert_body,
                alert_priority,
                progress,
            )
            for label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, progress in (self._plugin_tuple(plugin) for plugin in self.plugin_samples)
        ][:self.enabled_plugin_count]
        if not plugins:
            theme = self._plugin_theme_colors()
            muted = QColor(theme["muted"])
            muted.setAlpha(170)
            self._paint_scrolling_text(painter, rect.adjusted(self._u(18.0), 0.0, -self._u(18.0), 0.0), "功能待定", 14, muted, False)
            return
        self._paint_plugin_function_panel(painter, rect, self._plugin_panel_focus_model() or {})

    def _plugin_panel_focus_model(self) -> dict[str, object] | None:
        plugins = [
            (
                label,
                detail,
                summary,
                color,
                icon,
                plugin_id,
                status,
                alert_title,
                alert_body,
                alert_priority,
                progress,
            )
            for label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, progress in (self._plugin_tuple(plugin) for plugin in self.plugin_samples)
        ][:self.enabled_plugin_count]
        if not plugins:
            return None
        index = max(0, min(self._active_plugin_focus(time.monotonic()), len(plugins) - 1))
        label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, progress = plugins[index]
        return {
            "index": index,
            "label": label,
            "detail": detail,
            "summary": summary,
            "color": color,
            "icon": icon,
            "plugin_id": plugin_id,
            "status": status,
            "alert_title": alert_title,
            "alert_body": alert_body,
            "alert_priority": alert_priority,
            "progress": progress,
            "action": self._plugin_action_model(label, detail, summary, status),
        }

    def _plugin_compact_value(self, label: str, detail: str, summary: str, status: str = "normal") -> str:
        text = str(detail or "").strip()
        if str(label or "").upper() == "TIME" and str(status or "").lower() == "running":
            text = re.sub(r"^\s*倒计时\s*", "", str(summary or "").strip())
        return text or str(summary or "").strip()

    def _strip_timer_prefix(self, text: str) -> str:
        text = str(text or "").strip()
        match = re.search(r"\d{1,2}:\d{2}(?::\d{2})?", text)
        if match:
            return match.group(0)
        return re.sub(r"^\s*(?:倒计时|Countdown|Timer|timer|计时)\s*", "", text, flags=re.I).strip()

    def _time_reminder_kind(self, status: str = "", text: str = "") -> str:
        source = f"{status} {text}".lower()
        if "alarm" in source or "\u95f9\u949f" in source:
            return "alarm"
        return "countdown"

    def _time_alert_kind_from_plugin_id(self, plugin_id: str) -> str:
        value = str(plugin_id or "").strip().casefold()
        if value.endswith(":alarm"):
            return "alarm"
        return "countdown"

    def _time_reminder_icon_path(self, status: str = "running", kind: str = "countdown") -> str:
        status_key = str(status or "").strip().lower()
        kind_key = str(kind or "").strip().lower()
        base = "\u95f9\u949f" if kind_key == "alarm" else "\u5012\u8ba1\u65f6"
        tone = "\u9ed1" if status_key in {"done", "due", "alarm"} else "\u9ec4"
        return str(ROOT_DIR / "logos" / f"{base}{tone}.svg")

    def _time_reminder_display_text(self, text: str, max_chars: int = 18) -> str:
        value = str(text or "").strip()
        max_chars = max(4, int(max_chars))
        if len(value) <= max_chars:
            return value
        return value[: max_chars - 3].rstrip() + "..."

    def _time_alarm_note_display_text(self, text: str) -> str:
        return str(text or "").strip()

    def _time_alarm_alert_text(self, note: str) -> str:
        value = str(note or "").strip()
        if not value:
            return "闹钟"
        value = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", "", value).strip()
        value = re.sub(r"^\s*(?:闹钟提醒?|提醒)[:：\s]*", "", value).strip()
        lowered = value.strip().lower()
        if lowered in {"alarm", "timer", "countdown"} or any(token in lowered for token in ("倒计时", "计时器", "时间到", "计时结束", "countdown", "timer", "time up", "done")):
            return "闹钟"
        return value or "闹钟"

    def _time_alarm_alert_text_from_parts(self, *parts: str) -> str:
        for part in parts:
            value = self._time_alarm_alert_text(part)
            if value != "闹钟":
                return value
        return "闹钟"

    def _time_text_looks_like_countdown(self, text: str) -> bool:
        value = str(text or "").strip().lower()
        if not value:
            return False
        if re.search(r"\d{1,2}:\d{2}(?::\d{2})?", value):
            return True
        return any(token in value for token in ("倒计时", "计时", "计时器", "时间到", "计时结束", "countdown", "timer"))

    def _time_reminder_due_from_alarm(self, alarm: dict[str, object], now: datetime) -> datetime | None:
        parsed = self._alarm_row_time_parts(alarm)
        if parsed is None:
            return None
        hour, minute = parsed
        for offset in range(0, 366 * 5):
            day = now + timedelta(days=offset)
            due = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if due < now:
                continue
            if self._alarm_row_matches_date(alarm, due):
                return due
        return None

    def _time_display_segment_text(self, detail: str, now: datetime) -> str:
        match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(detail or ""))
        if match:
            hour = int(match.group(1)) % 24
            minute = int(match.group(2)) % 60
            second = int(match.group(3) or now.second) % 60
        else:
            hour, minute, second = now.hour, now.minute, now.second
        if bool(getattr(self, "show_seconds", True)):
            return f"{hour:02d}:{minute:02d}:{second:02d}"
        return f"{hour:02d}:{minute:02d}"

    def _time_compact_segments(self, detail: str, summary: str, status: str, alert_body: str = "", now: datetime | None = None) -> list[dict[str, object]]:
        now = now or datetime.now().astimezone()
        segments: list[dict[str, object]] = [{"kind": "time", "text": self._time_display_segment_text(detail, now), "due_at": now}]
        reminders: list[dict[str, object]] = []
        status_key = str(status or "").strip().lower()
        raw_entries = getattr(self, "plugin_countdown_entries", None)
        countdown_candidates: list[dict[str, object]] = []
        if isinstance(raw_entries, list):
            for raw in raw_entries:
                if not isinstance(raw, dict):
                    continue
                due_at = None
                raw_end = str(raw.get("end_at") or "").strip()
                if raw_end:
                    try:
                        due_at = datetime.fromisoformat(raw_end)
                    except ValueError:
                        due_at = None
                    if due_at and due_at.tzinfo is None:
                        due_at = due_at.replace(tzinfo=now.tzinfo)
                    if due_at:
                        due_at = due_at.astimezone(now.tzinfo)
                text = str(raw.get("text") or "").strip()
                if due_at and not text:
                    text = self._format_timer_seconds(max(0, int((due_at - now).total_seconds())))
                if text or due_at:
                    countdown_candidates.append({"kind": "countdown", "text": text or "0:00", "due_at": due_at or now, "status": str(raw.get("status") or status_key)})
        fallback_text = self._strip_timer_prefix(str(alert_body or summary or ""))
        if not countdown_candidates and status_key in {"running", "done", "due"}:
            countdown_candidates.append(
                {
                    "kind": "countdown",
                    "text": "\u65f6\u95f4\u5230" if status_key == "done" else (fallback_text or self._plugin_compact_value("TIME", detail, summary, status)),
                    "due_at": now if status_key == "done" else now + timedelta(days=1),
                    "status": status_key or "running",
                }
            )
        if countdown_candidates:
            countdown_candidates.sort(key=lambda item: item["due_at"])
            reminders.append(countdown_candidates[0])

        alarm_candidates: list[dict[str, object]] = []
        raw_alarm_rows = self._time_alarm_rows()
        for alarm in raw_alarm_rows if isinstance(raw_alarm_rows, list) else []:
            if not bool(alarm.get("enabled", False)):
                continue
            due_at = self._time_reminder_due_from_alarm(alarm, now)
            if due_at is None:
                continue
            alarm_candidates.append({"kind": "alarm", "text": str(alarm.get("label") or alarm.get("time") or "\u5907\u6ce8"), "due_at": due_at, "status": "running"})
        if alarm_candidates:
            alarm_candidates.sort(key=lambda item: item["due_at"])
            reminders.append(alarm_candidates[0])
        reminders.sort(key=lambda item: item["due_at"])
        segments.extend(reminders[:2])
        return segments

    def _time_compact_cell_layout(self, rect: QRectF, segments: list[dict[str, object]]) -> list[dict[str, object]]:
        if not segments:
            return []
        gap = self._u(4.0)
        pad_x = self._u(5.0)
        inner_top = rect.top() + self._u(3.0)
        inner_h = max(self._u(24.0), rect.height() - self._u(6.0))
        cursor = rect.left() + pad_x
        right_limit = rect.right() - pad_x
        time_text = str(segments[0].get("text") or "")
        has_seconds = bool(re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", time_text))
        time_floor = self._u(86.0 if has_seconds else 66.0)
        time_target = max(time_floor, min(self._u(96.0), len(time_text) * self._u(7.4) + self._u(17.0)))
        reminders = list(segments[1:3])
        min_reminder_w = 0.0
        if reminders:
            circle = inner_h
            min_reminder_w = circle * len(reminders) + gap * max(0, len(reminders) - 1)
        max_time_w = max(self._u(48.0), right_limit - cursor - (gap if reminders else 0.0) - min_reminder_w)
        time_w = max(self._u(48.0), min(time_target, max_time_w))
        time_rect = QRectF(cursor, inner_top, max(0.0, time_w), inner_h)
        layout = [{**segments[0], "kind": "time", "rect": time_rect, "icon_only": False}]
        cursor = time_rect.right() + gap
        available = right_limit - cursor
        if available <= self._u(20.0):
            return layout

        full_widths = []
        for segment in reminders:
            kind = str(segment.get("kind") or "countdown")
            if kind == "countdown":
                base_width = self._u(66.0)
                display_text = self._time_reminder_display_text(str(segment.get("text") or ""), 10)
                if len(display_text) <= 5:
                    full_widths.append(base_width)
                    continue
                text_width = float(QFontMetrics(self._font(10, QFont.Weight.Bold)).horizontalAdvance(display_text))
                icon_size = min(self._u(20.0), inner_h - self._u(5.0))
                required_width = self._u(6.0) + icon_size + self._u(4.0) + text_width + self._u(7.0) + self._u(8.0)
                full_widths.append(max(base_width, min(self._u(112.0), required_width)))
            else:
                full_widths.append(self._u(74.0))
        full_total = sum(full_widths) + gap * max(0, len(full_widths) - 1)
        use_circles = bool(reminders) and full_total > available
        for index, segment in enumerate(reminders):
            target_w = inner_h if use_circles else full_widths[index]
            if cursor + target_w > right_limit + self._u(0.5):
                break
            layout.append({**segment, "rect": QRectF(cursor, inner_top, target_w, inner_h), "icon_only": use_circles})
            cursor += target_w + gap
        return layout

    def _paint_svg_icon(self, painter: QPainter, path: str, rect: QRectF) -> bool:
        if QSvgRenderer is None:
            return False
        try:
            renderer = QSvgRenderer(str(path))
            if not renderer.isValid():
                return False
            renderer.render(painter, rect)
            return True
        except Exception:
            return False

    def _plugin_alert_display_value(self, label: str, detail: str, summary: str, status: str, alert_title: str = "", alert_body: str = "") -> str:
        label_key = str(label or "").strip().upper()
        status_key = str(status or "").strip().lower()
        body = str(alert_body or summary or detail or "").strip()
        if label_key == "TIME" and status_key in {"running", "done"}:
            value = self._strip_timer_prefix(body) or self._strip_timer_prefix(detail)
            if status_key == "done" and not value:
                value = "\u65f6\u95f4\u5230"
            return self._time_reminder_display_text(value or "TIME")
        return self._plugin_compact_value(label, detail, body, status)

    def _default_time_alarm_rows(self) -> list[dict[str, object]]:
        return []

    def _default_alarm_editor_state(self) -> dict[str, object]:
        now = datetime.now().astimezone()
        return {
            "hour": now.hour,
            "minute": now.minute,
            "note": "",
            "frequency": "每周",
            "frequency_unit": "weekly",
            "weekdays": [2, 4],
            "month_day": 28,
            "year_month": 4,
            "year_day": 28,
            "editor_page": "main",
            "detail_mode": "",
            "edit_index": None,
        }

    def _alarm_editor_state_from_row(self, index: int, row: dict[str, object]) -> dict[str, object]:
        raw_time = str(row.get("time") or "").strip()
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw_time)
        if match:
            hour = max(0, min(23, int(match.group(1))))
            minute = max(0, min(59, int(match.group(2))))
        else:
            now = datetime.now().astimezone()
            hour, minute = now.hour, now.minute
        return {
            "hour": hour,
            "minute": minute,
            "note": str(row.get("label") or ""),
            "frequency": str(row.get("frequency") or "每周"),
            "weekdays": str(row.get("weekdays") or "二、四"),
            "frequency_unit": str(row.get("frequency_unit") or self._alarm_unit_from_frequency(str(row.get("frequency") or "每周"))),
            "month_day": int(row.get("month_day", 28) or 28),
            "year_month": int(row.get("year_month", 4) or 4),
            "year_day": int(row.get("year_day", 28) or 28),
            "editor_page": "main",
            "detail_mode": "",
            "edit_index": int(index),
        }

    def _alarm_row_time_parts(self, row: dict[str, object]) -> tuple[int, int] | None:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(row.get("time") or ""))
        if not match:
            return None
        hour = int(match.group(1))
        minute = int(match.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return hour, minute

    def _alarm_int(self, value: object, lower: int, upper: int, default: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = int(default)
        return max(int(lower), min(int(upper), number))

    def _alarm_row_matches_date(self, row: dict[str, object], due: datetime) -> bool:
        unit = str(row.get("frequency_unit") or self._alarm_unit_from_frequency(str(row.get("frequency") or ""))).strip().lower()
        if unit == "weekly":
            weekdays = self._alarm_weekdays_from_value(row.get("weekdays", []))
            return due.isoweekday() in weekdays if weekdays else False
        if unit == "monthly":
            day = self._alarm_int(row.get("month_day", 1), 1, 31, 1)
            return day <= self._days_in_month(due.year, due.month) and due.day == day
        if unit == "yearly":
            month = self._alarm_int(row.get("year_month", 1), 1, 12, 1)
            day = self._alarm_int(row.get("year_day", 1), 1, 31, 1)
            return due.month == month and due.day == day and day <= self._days_in_month(due.year, month)
        return True

    def _coerce_alarm_rows(self, rows: object) -> list[dict[str, object]]:
        if isinstance(rows, dict):
            rows = rows.get("alarms") or rows.get("rows") or []
        if not isinstance(rows, list):
            return []
        result: list[dict[str, object]] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            parsed = self._alarm_row_time_parts(item)
            if parsed is None:
                continue
            hour, minute = parsed
            unit = str(item.get("frequency_unit") or "").strip().lower()
            if unit not in {"daily", "weekly", "monthly", "yearly"}:
                unit = self._alarm_unit_from_frequency(str(item.get("frequency") or ""))
            row = {
                "time": f"{hour:02d}:{minute:02d}",
                "label": str(item.get("label") or item.get("note") or "闹钟").strip()[:10] or "闹钟",
                "enabled": bool(item.get("enabled", True)),
                "frequency_unit": unit,
                "weekdays": self._alarm_weekdays_from_value(item.get("weekdays", [])),
                "month_day": self._alarm_int(item.get("month_day", 28), 1, 31, 28),
                "year_month": self._alarm_int(item.get("year_month", 4), 1, 12, 4),
                "year_day": self._alarm_int(item.get("year_day", 28), 1, 31, 28),
            }
            if unit == "weekly" and not row["weekdays"]:
                row["weekdays"] = [1]
            if unit == "yearly":
                row["year_day"] = min(int(row["year_day"]), self._yearly_alarm_max_day(int(row["year_month"])))
            row["frequency"] = str(item.get("frequency") or self._alarm_recurrence_label(row))
            result.append(row)
            if len(result) >= 8:
                break
        return result

    def _settings_alarm_rows(self) -> list[dict[str, object]] | None:
        getter = getattr(self, "settings_getter", None)
        if not callable(getter):
            return None
        raw = self._plugin_setting("v2_alarm_rows", None)
        if raw is None:
            return None
        if raw == "":
            return []
        payload = raw
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                return []
        return self._coerce_alarm_rows(payload)

    def _persist_alarm_rows(self, rows: object) -> list[dict[str, object]]:
        normalized = self._coerce_alarm_rows(rows)
        self.plugin_alarm_rows = normalized
        self._set_plugin_setting("v2_alarm_rows", json.dumps(normalized, ensure_ascii=False, separators=(",", ":")))
        return normalized

    def _time_alarm_rows(self) -> list[dict[str, object]]:
        settings_rows = self._settings_alarm_rows()
        if settings_rows is not None:
            self.plugin_alarm_rows = settings_rows[:8]
            return self.plugin_alarm_rows
        rows = getattr(self, "plugin_alarm_rows", None)
        if not isinstance(rows, list):
            rows = self._default_time_alarm_rows()
        capped = [dict(row) for row in rows[:8] if isinstance(row, dict)]
        self.plugin_alarm_rows = capped
        return capped

    def _time_alarm_editor_state(self) -> dict[str, object]:
        editor = getattr(self, "plugin_alarm_editor", None)
        if not isinstance(editor, dict):
            editor = self._default_alarm_editor_state()
            self.plugin_alarm_editor = editor
        editor.setdefault("hour", datetime.now().astimezone().hour)
        editor.setdefault("minute", datetime.now().astimezone().minute)
        editor.setdefault("note", "")
        editor.setdefault("frequency", "每周")
        editor.setdefault("frequency_unit", self._alarm_unit_from_frequency(str(editor.get("frequency") or "每周")))
        editor.setdefault("weekdays", "二、四")
        editor.setdefault("month_day", 28)
        editor.setdefault("year_month", 4)
        editor.setdefault("year_day", 28)
        editor.setdefault("editor_page", "main")
        editor.setdefault("detail_mode", "")
        editor.setdefault("edit_index", None)
        return editor

    def _alarm_frequency_units(self) -> list[tuple[str, str]]:
        return [("daily", "每天"), ("weekly", "每周"), ("monthly", "每月"), ("yearly", "每年")]

    def _alarm_unit_from_frequency(self, label: str) -> str:
        text = str(label or "").strip()
        if text in {"每周", "周", "weekly"}:
            return "weekly"
        if text in {"每月", "每闰月", "每非明年二月", "每大月", "月", "monthly"}:
            return "monthly"
        if text in {"每年", "每闰年", "闰年", "年", "yearly"}:
            return "yearly"
        return "daily"

    def _alarm_weekdays_from_value(self, value) -> list[int]:
        if isinstance(value, (list, tuple, set)):
            result = []
            for item in value:
                try:
                    day = int(item)
                except (TypeError, ValueError):
                    continue
                if 1 <= day <= 7 and day not in result:
                    result.append(day)
            return sorted(result)
        text = str(value or "").strip()
        if not text:
            return []
        if text in {"每天", "每日"}:
            return [1, 2, 3, 4, 5, 6, 7]
        if text in {"周末", "末"}:
            return [6, 7]
        mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "日": 7, "天": 7}
        result = []
        for token in re.findall(r"[1-7一二三四五六七日天]", text):
            day = int(token) if token.isdigit() else mapping.get(token, 0)
            if 1 <= day <= 7 and day not in result:
                result.append(day)
        return sorted(result)

    def _days_in_month(self, year: int, month: int) -> int:
        year = max(1, int(year))
        month = max(1, min(12, int(month)))
        if month == 12:
            next_month = datetime(year + 1, 1, 1)
        else:
            next_month = datetime(year, month + 1, 1)
        return (next_month - timedelta(days=1)).day

    def _is_leap_year(self, year: int) -> bool:
        year = int(year)
        return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)

    def _alarm_year_boundary_year(self) -> int:
        return datetime.now().astimezone().year + 1

    def _yearly_alarm_max_day(self, month: int) -> int:
        month = max(1, min(12, int(month)))
        if month == 2:
            return 29
        return self._days_in_month(self._alarm_year_boundary_year(), month)

    def _normalize_alarm_editor_recurrence(self, editor: dict[str, object]) -> dict[str, object]:
        unit = str(editor.get("frequency_unit") or "").strip().lower()
        if not unit:
            unit = self._alarm_unit_from_frequency(str(editor.get("frequency") or "每周"))
        if unit not in {"daily", "weekly", "monthly", "yearly"}:
            unit = self._alarm_unit_from_frequency(unit)
        editor["frequency_unit"] = unit
        if unit == "weekly":
            weekdays = self._alarm_weekdays_from_value(editor.get("weekdays", [2, 4]))
            editor["weekdays"] = weekdays or [1]
        elif unit == "monthly":
            try:
                month_day = int(editor.get("month_day", 28) or 28)
            except (TypeError, ValueError):
                month_day = 28
            editor["month_day"] = ((month_day - 1) % 31) + 1
        elif unit == "yearly":
            try:
                month = int(editor.get("year_month", 4) or 4)
            except (TypeError, ValueError):
                month = 4
            month = ((month - 1) % 12) + 1
            try:
                day = int(editor.get("year_day", 28) or 28)
            except (TypeError, ValueError):
                day = 28
            max_day = self._yearly_alarm_max_day(month)
            editor["year_month"] = month
            editor["year_day"] = max(1, min(max_day, day))
        editor["frequency"] = self._alarm_recurrence_label(editor)
        return editor

    def _alarm_recurrence_label(self, editor: dict[str, object]) -> str:
        unit = str(editor.get("frequency_unit") or self._alarm_unit_from_frequency(str(editor.get("frequency") or ""))).lower()
        if unit == "weekly":
            return "每周"
        if unit == "monthly":
            try:
                day = int(editor.get("month_day", 28) or 28)
            except (TypeError, ValueError):
                day = 28
            if day == 29:
                return "每非明年二月"
            if day == 31:
                return "每大月"
            return "每月"
        if unit == "yearly":
            try:
                month = int(editor.get("year_month", 4) or 4)
                day = int(editor.get("year_day", 28) or 28)
            except (TypeError, ValueError):
                month, day = 4, 28
            if month == 2 and day == 29:
                return "每闰年"
            return "每年"
        return "每天"

    def _alarm_recurrence_value_label(self, editor: dict[str, object]) -> str:
        unit = str(editor.get("frequency_unit") or self._alarm_unit_from_frequency(str(editor.get("frequency") or ""))).lower()
        if unit == "weekly":
            days = self._alarm_weekdays_from_value(editor.get("weekdays", []))
            if set(days) == {1, 2, 3, 4, 5, 6, 7}:
                return "整周"
            return " ".join(str(day) for day in days)
        if unit == "monthly":
            try:
                day = int(editor.get("month_day", 28) or 28)
            except (TypeError, ValueError):
                day = 28
            return f"{day}号"
        if unit == "yearly":
            try:
                month = int(editor.get("year_month", 4) or 4)
                day = int(editor.get("year_day", 28) or 28)
            except (TypeError, ValueError):
                month, day = 4, 28
            return f"{month}月{day}日"
        return ""

    def _time_countdown_active_model(self, model: dict[str, object]) -> dict[str, object]:
        paused_remaining = self._timer_paused_remaining_seconds()
        if paused_remaining > 0:
            return {
                "active": True,
                "text": self._format_timer_seconds(paused_remaining),
                "progress": max(0.0, min(1.0, float(getattr(self, "plugin_timer_paused_progress", 0.0) or 0.0))),
                "status": "paused",
            }
        status = str(model.get("status", "") or "").strip().lower()
        text_source = " ".join(
            str(model.get(key, "") or "")
            for key in ("alert_body", "summary", "detail")
            if str(model.get(key, "") or "").strip()
        )
        text = self._strip_timer_prefix(text_source)
        active = status in {"running", "done"} and bool(text)
        if status == "done":
            text = "完成"
        return {
            "active": active,
            "text": text if active else "",
            "progress": max(0.0, min(1.0, float(model.get("progress", 0.0) or 0.0))),
            "status": status,
        }

    def _time_countdown_entries(self, model: dict[str, object]) -> list[dict[str, object]]:
        raw_entries = getattr(self, "plugin_countdown_entries", None)
        if isinstance(raw_entries, list) and raw_entries:
            entries = []
            now = datetime.now().astimezone()
            for raw in raw_entries[-2:]:
                if not isinstance(raw, dict):
                    continue
                text = str(raw.get("text") or "").strip()
                started = None
                target = None
                for key, holder in (("started_at", "started"), ("end_at", "target")):
                    raw_time = str(raw.get(key) or "").strip()
                    if not raw_time:
                        continue
                    try:
                        value = datetime.fromisoformat(raw_time)
                    except ValueError:
                        continue
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=now.tzinfo)
                    if holder == "started":
                        started = value.astimezone(now.tzinfo)
                    else:
                        target = value.astimezone(now.tzinfo)
                progress = max(0.0, min(1.0, float(raw.get("progress", 0.0) or 0.0)))
                status = str(raw.get("status") or "running").strip().lower()
                if status == "paused":
                    if not text:
                        remaining = self._timer_paused_remaining_seconds()
                        text = self._format_timer_seconds(remaining) if remaining > 0 else "0:00"
                    entries.append(
                        {
                            "active": True,
                            "text": text,
                            "progress": progress,
                            "status": "paused",
                        }
                    )
                    continue
                if target is not None:
                    remaining = max(0, int((target - now).total_seconds()))
                    text = self._format_timer_seconds(remaining)
                    status = "done" if remaining <= 0 and status != "paused" else status
                    if started is not None and target > started:
                        total = max(1.0, (target - started).total_seconds())
                        elapsed = max(0.0, (now - started).total_seconds())
                        progress = max(0.0, min(1.0, elapsed / total))
                if not text:
                    continue
                entries.append(
                    {
                        "active": True,
                        "text": text,
                        "progress": progress,
                        "status": status,
                    }
                )
            return list(reversed(entries))
        active = dict(model.get("countdown_active") or {})
        return [active] if active.get("active") else []

    def _time_plugin_panel_model(self, model: dict[str, object]) -> dict[str, object]:
        mode = str(getattr(self, "plugin_time_mode", "dashboard") or "dashboard")
        clock_text = str(model.get("detail", "") or "").strip() or datetime.now().astimezone().strftime("%H:%M")
        dashboard = {
            "mode": mode if mode == "alarm_editor" else "dashboard",
            "clock": {
                "time": clock_text,
                "date": str(model.get("summary", "") or "").strip(),
                "location": "中国 北京",
            },
            "countdown_presets": [
                {"minutes": 5, "label": "5"},
                {"minutes": 15, "label": "15"},
                {"minutes": 10, "label": "10"},
                {"minutes": 20, "label": "20"},
            ],
            "countdown_active": self._time_countdown_active_model(model),
            "alarms": [dict(row) for row in self._time_alarm_rows()[:8]],
        }
        dashboard["countdown_entries"] = self._time_countdown_entries(dashboard)
        pending_alarm = self._capture_time_alarm_due_pending(model) or self._time_alarm_due_pending()
        if pending_alarm:
            dashboard["alarm_due_pending"] = pending_alarm
        if dashboard["mode"] == "alarm_editor":
            dashboard["editor"] = dict(self._time_alarm_editor_state())
        return dashboard

    def _adjust_alarm_editor_field(self, field: str, steps: int):
        editor = self._time_alarm_editor_state()
        steps = int(steps)
        adjusted = True
        if field == "hour":
            editor["hour"] = (int(editor.get("hour", 0)) + steps) % 24
        elif field == "minute":
            editor["minute"] = (int(editor.get("minute", 0)) + steps) % 60
        elif field == "frequency":
            options = ["每周", "每天", "工作日", "仅一次"]
            current = str(editor.get("frequency", options[0]))
            index = options.index(current) if current in options else 0
            editor["frequency"] = options[(index + steps) % len(options)]
            editor["frequency_unit"] = self._alarm_unit_from_frequency(str(editor["frequency"]))
        elif field == "frequency_unit":
            units = self._alarm_frequency_units()
            current = str(editor.get("frequency_unit") or self._alarm_unit_from_frequency(str(editor.get("frequency") or "每周")))
            keys = [key for key, _label in units]
            index = keys.index(current) if current in keys else 0
            next_unit = keys[(index + steps) % len(keys)]
            editor["frequency_unit"] = next_unit
            self._normalize_alarm_editor_recurrence(editor)
            if next_unit != current:
                self._begin_alarm_editor_reflow_animation(current, next_unit, steps)
        elif field == "weekdays":
            options = [[1, 3, 5], [2, 4], [6, 7], [1, 2, 3, 4, 5, 6, 7]]
            current = self._alarm_weekdays_from_value(editor.get("weekdays", options[0]))
            index = options.index(current) if current in options else 0
            editor["weekdays"] = options[(index + steps) % len(options)]
            editor["frequency_unit"] = "weekly"
            editor["frequency"] = "每周"
        elif field == "month_day":
            current = int(editor.get("month_day", 28) or 28)
            editor["frequency_unit"] = "monthly"
            editor["month_day"] = ((current + steps - 1) % 31) + 1
            self._normalize_alarm_editor_recurrence(editor)
        elif field == "year_month":
            current = int(editor.get("year_month", 4) or 4)
            editor["frequency_unit"] = "yearly"
            editor["year_month"] = ((current + steps - 1) % 12) + 1
            self._normalize_alarm_editor_recurrence(editor)
        elif field == "year_day":
            editor["frequency_unit"] = "yearly"
            month = int(editor.get("year_month", 4) or 4)
            max_day = self._yearly_alarm_max_day(month)
            current = int(editor.get("year_day", 28) or 28)
            editor["year_day"] = ((current + steps - 1) % max_day) + 1
            self._normalize_alarm_editor_recurrence(editor)
        else:
            adjusted = False
        self.plugin_alarm_editor = editor
        if adjusted:
            self._begin_alarm_editor_spin(field, steps)
        self._request_visual_update()

    def _begin_alarm_editor_spin(self, field: str, steps: int):
        animations = getattr(self, "plugin_alarm_editor_spin_animations", None)
        if not isinstance(animations, dict):
            animations = {}
        direction = 1 if int(steps or 0) >= 0 else -1
        animations[str(field)] = {"direction": direction, "started_at": time.monotonic()}
        self.plugin_alarm_editor_spin_animations = animations

    def _begin_alarm_editor_page_animation(self, from_page: str, to_page: str, steps: int):
        direction = 1 if int(steps or 0) >= 0 else -1
        self.plugin_alarm_editor_page_animation = {
            "from": str(from_page or "main"),
            "to": str(to_page or "main"),
            "direction": direction,
            "started_at": time.monotonic(),
        }

    def _begin_alarm_editor_reflow_animation(self, from_unit: str, to_unit: str, steps: int):
        direction = 1 if int(steps or 0) >= 0 else -1
        self.plugin_alarm_editor_reflow_animation = {
            "from": str(from_unit or ""),
            "to": str(to_unit or ""),
            "direction": direction,
            "started_at": time.monotonic(),
        }

    def _alarm_editor_reflow_offset(self, unit: str) -> float:
        animation = getattr(self, "plugin_alarm_editor_reflow_animation", {})
        if not isinstance(animation, dict) or not animation:
            return 0.0
        if str(animation.get("to") or "") != str(unit or ""):
            return 0.0
        try:
            started_at = float(animation.get("started_at", 0.0) or 0.0)
            direction = 1 if int(animation.get("direction", 1) or 1) >= 0 else -1
        except (TypeError, ValueError):
            return 0.0
        elapsed = max(0.0, time.monotonic() - started_at)
        duration = 0.24
        if elapsed >= duration:
            self.plugin_alarm_editor_reflow_animation = {}
            return 0.0
        progress = max(0.0, min(1.0, elapsed / duration))
        eased = 1.0 - pow(1.0 - progress, 3)
        return -direction * (1.0 - eased)

    def _alarm_editor_page_transition_offset(self, page: str) -> float:
        animation = getattr(self, "plugin_alarm_editor_page_animation", {})
        if not isinstance(animation, dict) or not animation:
            return 0.0
        if str(animation.get("to") or "") != str(page or ""):
            return 0.0
        try:
            started_at = float(animation.get("started_at", 0.0) or 0.0)
            direction = 1 if int(animation.get("direction", 1) or 1) >= 0 else -1
        except (TypeError, ValueError):
            return 0.0
        elapsed = max(0.0, time.monotonic() - started_at)
        duration = 0.26
        if elapsed >= duration:
            self.plugin_alarm_editor_page_animation = {}
            return 0.0
        progress = max(0.0, min(1.0, elapsed / duration))
        eased = 1.0 - pow(1.0 - progress, 3)
        return direction * (1.0 - eased)

    def _alarm_editor_spin_offset(self, field: str) -> float:
        animations = getattr(self, "plugin_alarm_editor_spin_animations", {})
        if not isinstance(animations, dict):
            return 0.0
        animation = animations.get(str(field))
        if not isinstance(animation, dict):
            return 0.0
        try:
            started_at = float(animation.get("started_at", 0.0) or 0.0)
            direction = 1 if int(animation.get("direction", 1) or 1) >= 0 else -1
        except (TypeError, ValueError):
            return 0.0
        elapsed = max(0.0, time.monotonic() - started_at)
        duration = 0.22
        if elapsed >= duration:
            animations.pop(str(field), None)
            return 0.0
        progress = max(0.0, min(1.0, elapsed / duration))
        eased = 1.0 - pow(1.0 - progress, 3)
        return direction * (1.0 - eased)

    def _with_alarm_picker_offsets(self, field: str, values: list[dict[str, object]]) -> list[dict[str, object]]:
        offset = self._alarm_editor_spin_offset(field)
        if abs(offset) <= 0.001:
            return values
        adjusted = []
        for item in values:
            clone = dict(item)
            clone["offset"] = offset
            adjusted.append(clone)
        return adjusted

    def _alarm_picker_window_values(self, field: str, options: list[object], current: object, formatter=None, visible_count: int = 7) -> list[dict[str, object]]:
        if not options:
            return self._with_alarm_picker_offsets(field, [{"text": str(current), "current": True, "opacity": 1.0}])
        formatter = formatter or (lambda value: str(value))
        visible_count = max(3, int(visible_count))
        if visible_count % 2 == 0:
            visible_count += 1
        center = visible_count // 2
        try:
            current_index = options.index(current)
        except ValueError:
            current_index = 0
        values = []
        for slot in range(visible_count):
            distance = abs(slot - center)
            if slot == center:
                value = current
                current_row = True
            else:
                value = options[(current_index + slot - center) % len(options)]
                current_row = False
            values.append(
                {
                    "text": formatter(value),
                    "current": current_row,
                    "opacity": 1.0 if current_row else max(0.18, 0.54 - distance * 0.1),
                }
            )
        return self._with_alarm_picker_offsets(field, values)

    def _alarm_picker_display_values(self, field: str) -> list[dict[str, object]]:
        editor = self._time_alarm_editor_state()
        field = str(field or "")
        if field == "hour":
            current = int(editor.get("hour", 0)) % 24
            return self._alarm_picker_window_values(field, list(range(24)), current, lambda value: f"{int(value):02d}")
        if field == "minute":
            current = int(editor.get("minute", 0)) % 60
            return self._alarm_picker_window_values(field, list(range(60)), current, lambda value: f"{int(value):02d}")
        options_by_field = {
            "frequency": ["每周", "每天", "工作日", "仅一次"],
        }
        if field == "frequency_unit":
            options_by_field[field] = [label for _key, label in self._alarm_frequency_units()]
            unit = str(editor.get("frequency_unit") or self._alarm_unit_from_frequency(str(editor.get("frequency") or "每周")))
            editor = dict(editor)
            editor[field] = dict(self._alarm_frequency_units()).get(unit, "每周")
        elif field == "weekdays":
            options_by_field[field] = ["1 3 5", "2 4", "6 7", "整周"]
            editor = dict(editor)
            editor[field] = self._alarm_recurrence_value_label({"frequency_unit": "weekly", "weekdays": editor.get("weekdays", [])})
        elif field == "month_day":
            current = int(editor.get("month_day", 28) or 28)
            return self._alarm_picker_window_values(field, list(range(1, 32)), current, lambda value: f"{int(value)}")
        elif field == "year_month":
            current = int(editor.get("year_month", 4) or 4)
            return self._alarm_picker_window_values(field, list(range(1, 13)), current, lambda value: f"{int(value)}")
        elif field == "year_day":
            month = int(editor.get("year_month", 4) or 4)
            max_day = self._yearly_alarm_max_day(month)
            current = max(1, min(max_day, int(editor.get("year_day", 28) or 28)))
            return self._alarm_picker_window_values(field, list(range(1, max_day + 1)), current, lambda value: f"{int(value)}")
        options = options_by_field.get(field)
        if options:
            current = str(editor.get(field, options[0]))
            return self._alarm_picker_window_values(field, list(options), current, lambda value: str(value))
        current = str(editor.get(field, ""))
        return self._with_alarm_picker_offsets(field, [{"text": current, "current": True, "opacity": 1.0}])

    def _handle_alarm_editor_key(self, text: str, key: int | None = None) -> bool:
        if str(getattr(self, "plugin_time_mode", "dashboard")) != "alarm_editor":
            return False
        focus = str(getattr(self, "plugin_alarm_editor_focus", "") or "")
        numeric_limits = {
            "hour": (0, 23),
            "minute": (0, 59),
            "month_day": (1, 31),
            "year_month": (1, 12),
        }
        if focus in {"year_day"}:
            editor = self._time_alarm_editor_state()
            month = int(editor.get("year_month", 1) or 1)
            numeric_limits["year_day"] = (1, self._yearly_alarm_max_day(month))
        if focus in numeric_limits:
            editor = self._time_alarm_editor_state()
            buffers = getattr(self, "plugin_alarm_editor_input_buffers", None)
            if not isinstance(buffers, dict):
                buffers = {}
            current = str(buffers.get(focus, ""))
            if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) or text == "\b":
                current = current[:-1]
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._activate_plugin_action("alarm-editor-save")
                return True
            elif text and text.isdigit():
                current = text if len(current) >= 2 else current + text
            else:
                return False
            lower, upper = numeric_limits[focus]
            value = max(lower, min(upper, int(current or str(lower))))
            editor[focus] = value
            if focus == "month_day":
                editor["frequency_unit"] = "monthly"
            elif focus in {"year_month", "year_day"}:
                editor["frequency_unit"] = "yearly"
            if focus in {"month_day", "year_month", "year_day"}:
                self._normalize_alarm_editor_recurrence(editor)
            buffers[focus] = current
            self.plugin_alarm_editor = editor
            self.plugin_alarm_editor_input_buffers = buffers
            self._begin_alarm_editor_spin(focus, 1)
            self._request_visual_update()
            return True
        if focus != "note":
            return False
        editor = self._time_alarm_editor_state()
        note = str(editor.get("note") or "")
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) or text == "\b":
            editor["note"] = note[:-1]
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._activate_plugin_action("alarm-editor-save")
            return True
        elif text and text.isprintable():
            if len(note) >= 10:
                self.plugin_alarm_editor_note_hint = "最多10字"
                self.plugin_alarm_editor_note_hint_until = time.monotonic() + 1.4
            else:
                editor["note"] = (note + text)[:10]
                self.plugin_alarm_editor_note_hint = ""
                self.plugin_alarm_editor_note_hint_until = 0.0
        else:
            return False
        self.plugin_alarm_editor = editor
        self._request_visual_update()
        return True

    def _handle_alarm_editor_key_event(self, event) -> bool:
        if str(getattr(self, "plugin_time_mode", "dashboard")) != "alarm_editor":
            return False
        try:
            key = event.key()
        except Exception:
            key = None
        if key == Qt.Key.Key_Escape:
            self.plugin_time_mode = "dashboard"
            self._request_visual_update()
            return True
        try:
            text = event.text()
        except Exception:
            text = ""
        return self._handle_alarm_editor_key(text, key)

    def _handle_time_alarm_editor_wheel(self, event) -> bool:
        if str(getattr(self, "plugin_time_mode", "dashboard")) != "alarm_editor":
            return False
        rects = getattr(self, "plugin_alarm_editor_rects", {})
        try:
            pos = event.position()
        except Exception:
            return False
        try:
            delta = event.angleDelta().y()
        except Exception:
            delta = 0
        field = next((name for name, rect in rects.items() if rect.contains(pos)), "") if isinstance(rects, dict) else ""
        if not field:
            editor = self._time_alarm_editor_state()
            detail_mode = str(editor.get("detail_mode") or "").strip()
            width = float(getattr(self, "plugin_panel_last_width", 0.0) or 0.0)
            if detail_mode or width <= 0.0 or width >= self._u(600.0):
                return False
            pages = ["main", "note"]
            current = str(editor.get("editor_page") or "main")
            index = pages.index(current) if current in pages else 0
            step = 1 if delta < 0 else -1
            target = pages[(index + step) % len(pages)]
            editor["editor_page"] = target
            editor["detail_mode"] = ""
            self.plugin_alarm_editor = editor
            self._begin_alarm_editor_page_animation(current, target, step)
            try:
                event.accept()
            except Exception:
                pass
            self._request_visual_update()
            return True
        steps = -1 if delta > 0 else 1
        self._adjust_alarm_editor_field(field, steps)
        try:
            event.accept()
        except Exception:
            pass
        return True

    def _handle_time_panel_module_wheel(self, event) -> bool:
        if str(getattr(self, "plugin_time_mode", "dashboard")) == "alarm_editor":
            return False
        width = float(getattr(self, "plugin_panel_last_width", 0.0) or 0.0)
        if self._handle_time_alarm_list_wheel(event):
            return True
        if width <= 0.0 or width >= self._time_panel_breakpoint():
            return False
        modules = self._time_panel_modules()
        current = self._time_visible_module_for_width(width)
        index = modules.index(current) if current in modules else 0
        try:
            delta = event.angleDelta().y()
        except Exception:
            delta = 0
        step = 1 if delta < 0 else -1
        target = modules[(index + step) % len(modules)]
        self.plugin_time_visible_module = target
        self._begin_time_module_animation(current, target, step)
        try:
            event.accept()
        except Exception:
            pass
        self._request_visual_update()
        return True

    def _handle_time_alarm_list_wheel(self, event) -> bool:
        visible = self._time_visible_module_for_width(float(getattr(self, "plugin_panel_last_width", 0.0) or 0.0))
        if visible not in {"alarm", "all"}:
            return False
        list_rect = getattr(self, "plugin_alarm_list_rect", QRectF())
        if not isinstance(list_rect, QRectF) or list_rect.isNull():
            return False
        try:
            pos = event.position()
        except Exception:
            return False
        if not list_rect.contains(pos):
            return False
        alarms = self._time_alarm_rows()
        start, count = getattr(self, "plugin_alarm_visible_range", (0, 0))
        try:
            start = int(start)
            count = int(count)
        except (TypeError, ValueError):
            start, count = 0, 0
        if count <= 0:
            return False
        try:
            spacer_rows = float(getattr(self, "plugin_alarm_list_bottom_spacer_rows", 0.0) or 0.0)
        except (TypeError, ValueError):
            spacer_rows = 0.0
        max_start = float(max(0.0, len(alarms) - count + max(0.0, spacer_rows)))
        if max_start <= 0.0:
            return False
        try:
            delta = event.angleDelta().y()
        except Exception:
            delta = 0
        if delta == 0:
            return False
        step = -float(delta) / 120.0
        try:
            current = float(getattr(self, "plugin_alarm_list_scroll_index", start) or 0.0)
        except (TypeError, ValueError):
            current = float(start)
        current = max(0.0, min(max_start, current))
        target = max(0.0, min(max_start, current + step))
        if math.isclose(target, current, abs_tol=0.001):
            return False
        self.plugin_alarm_list_scroll_index = target
        self._clear_alarm_delete_mode()
        try:
            event.accept()
        except Exception:
            pass
        self._request_visual_update()
        return True

    def _plugin_panel_alert_progress(self, index: int) -> float:
        alerts = self._active_plugin_alerts(3, include_expanded=True)
        if not alerts:
            return 0.0
        for alert in alerts:
            alert_index = int(alert[0])
            if alert_index == int(index):
                return 1.0
        return 0.0

    def _plugin_ring_style(self, status: str = "normal") -> dict[str, float]:
        status_key = str(status or "").strip().lower()
        animated = status_key in {"running", "loading", "soon", "due", "done", "error", "alarm", "severe", "rain"}
        return {
            "width": self._u(3.8 if animated else 2.2),
            "track_width": self._u(3.2 if animated else 1.8),
            "pulse": 1.0 if animated else 0.0,
        }

    def _plugin_action_model(self, label: str, detail: str, summary: str, status: str) -> dict[str, str]:
        label_key = str(label or "").strip().upper()
        status_key = str(status or "").strip().lower()
        if label_key == "TIME":
            if status_key in {"running", "done", "alarm"}:
                return {"command": "timer-clear", "label": "清除计时", "hint": "停止当前提醒"}
            return {"command": "timer-open", "label": "设置计时", "hint": "打开时间插件"}
        if label_key == "WEATHER":
            return {"command": "weather-refresh", "label": "刷新天气", "hint": "立即更新"}
        if label_key == "AGENDA":
            return {"command": "agenda-open", "label": "管理日程", "hint": "添加或查看"}
        return {"command": "plugin-open", "label": "打开插件", "hint": "查看详情"}

    def _plugin_panel_layout(self, rect: QRectF) -> dict[str, QRectF]:
        outer_pad = self._u(10.0)
        nav_h = max(30.0, self._u(34.0))
        nav = QRectF(rect.left() + outer_pad, rect.top() + self._u(8.0), rect.width() - outer_pad * 2.0, nav_h)
        body = QRectF(
            rect.left() + self._u(18.0),
            nav.bottom() + self._u(12.0),
            rect.width() - self._u(36.0),
            max(self._u(88.0), rect.bottom() - nav.bottom() - self._u(30.0)),
        )
        reminders_h = max(26.0, self._u(30.0))
        reminders = QRectF(body.left(), rect.bottom() - reminders_h - self._u(12.0), body.width(), reminders_h)
        action_w = min(self._u(104.0), max(self._u(82.0), body.width() * 0.28))
        action = QRectF(body.right() - action_w, reminders.top() - self._u(34.0), action_w, max(24.0, self._u(26.0)))
        icon_size = min(self._u(82.0), max(self._u(58.0), body.height() - self._u(26.0)))
        icon = QRectF(body.left() + self._u(4.0), body.top() + self._u(6.0), icon_size, icon_size)
        text = QRectF(icon.right() + self._u(18.0), body.top() + self._u(2.0), max(self._u(120.0), body.right() - icon.right() - self._u(24.0)), max(self._u(56.0), action.top() - body.top() - self._u(4.0)))
        module_top = body.top() + self._u(5.0)
        module_bottom = max(module_top + self._u(82.0), body.bottom() - self._u(15.0))
        module_h = min(self._u(116.0), module_bottom - module_top)
        gap = self._u(9.0)
        clock_w = min(self._u(170.0), max(self._u(94.0), body.width() * 0.21))
        countdown_w = min(self._u(420.0), max(self._u(150.0), body.width() * 0.32))
        alarm_w = max(self._u(76.0), body.width() - clock_w - countdown_w - gap * 2.0)
        time_clock = QRectF(body.left(), module_top, clock_w, module_h)
        time_countdown = QRectF(time_clock.right() + gap, module_top, countdown_w, module_h)
        time_alarm = QRectF(time_countdown.right() + gap, module_top, alarm_w, module_h)
        time_layout_mode = "single" if rect.width() < self._time_panel_breakpoint() else "triple"
        time_visible_module = self._time_visible_module_for_width(rect.width())
        if time_layout_mode == "single":
            single_rect = QRectF(body.left(), module_top, body.width(), module_h)
            hidden = QRectF(single_rect.left(), single_rect.top(), 0.0, single_rect.height())
            time_clock = QRectF(single_rect if time_visible_module == "clock" else hidden)
            time_countdown = QRectF(single_rect if time_visible_module == "countdown" else hidden)
            time_alarm = QRectF(single_rect if time_visible_module == "alarm" else hidden)
        return {
            "nav": nav,
            "body": body,
            "icon": icon,
            "text": text,
            "action": action,
            "reminders": reminders,
            "time_clock": time_clock,
            "time_countdown": time_countdown,
            "time_alarm": time_alarm,
            "time_layout_mode": time_layout_mode,
            "time_visible_module": time_visible_module,
        }

    def _time_plugin_panel_layout(self, rect: QRectF) -> dict[str, QRectF]:
        title = QRectF(rect.left() + self._u(12.0), rect.top() + self._u(6.0), rect.width() - self._u(24.0), self._u(18.0))
        body_top = title.bottom() + self._u(4.0)
        body = QRectF(
            rect.left() + self._u(18.0),
            body_top,
            rect.width() - self._u(36.0),
            max(self._u(88.0), rect.bottom() - body_top - self._u(5.0)),
        )
        module_top = body.top() + self._u(2.0)
        module_h = min(self._u(205.0), max(self._u(92.0), body.bottom() - module_top - self._u(1.0)))
        gap = self._u(12.0)
        pager = QRectF()
        visible = self._time_visible_module_for_width(rect.width())
        if rect.width() < self._time_panel_breakpoint():
            pager_w = self._u(14.0)
            usable_w = max(self._u(260.0), body.width() - pager_w - self._u(8.0))
            clock_w = min(self._u(182.0), max(self._u(132.0), usable_w * 0.47))
            right_w = max(self._u(118.0), usable_w - clock_w - gap)
            time_clock = QRectF(body.left(), module_top, clock_w, module_h)
            right_slot = QRectF(time_clock.right() + gap, module_top, right_w, module_h)
            hidden = QRectF(right_slot.left(), right_slot.top(), 0.0, right_slot.height())
            time_countdown = QRectF(right_slot if visible == "countdown" else hidden)
            time_alarm = QRectF(right_slot if visible == "alarm" else hidden)
            pager = QRectF(body.right() - pager_w, module_top, pager_w, module_h)
            mode = "double"
        else:
            stretch = rect.width() >= self._time_panel_stretch_breakpoint()
            if stretch:
                clock_w = min(self._u(190.0), max(self._u(142.0), body.width() * 0.20))
                countdown_w = min(self._u(400.0), max(self._u(240.0), body.width() * 0.34))
            else:
                clock_w = min(self._u(180.0), max(self._u(126.0), body.width() * 0.24))
                countdown_w = min(self._u(205.0), max(self._u(138.0), body.width() * 0.28))
            alarm_w = max(self._u(176.0), body.width() - clock_w - countdown_w - gap * 2.0)
            time_clock = QRectF(body.left(), module_top, clock_w, module_h)
            time_countdown = QRectF(time_clock.right() + gap, module_top, countdown_w, module_h)
            time_alarm = QRectF(time_countdown.right() + gap, module_top, alarm_w, module_h)
            mode = "stretch" if stretch else "triple"
            visible = "all"
        return {
            "nav": title,
            "time_title": title,
            "body": body,
            "icon": QRectF(),
            "text": QRectF(),
            "action": QRectF(),
            "reminders": QRectF(),
            "time_clock": time_clock,
            "time_countdown": time_countdown,
            "time_alarm": time_alarm,
            "time_pager": pager,
            "time_layout_mode": mode,
            "time_visible_module": visible,
        }

    def _plugin_panel_reminder_models(self, limit: int = 3) -> list[dict[str, object]]:
        reminders = []
        for alert in self._active_plugin_alerts(limit, include_expanded=True):
            index, label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, _alert_priority = alert
            title, body = self._plugin_alert_text_parts(label, detail, summary, status, alert_title, alert_body)
            try:
                progress = self._plugin_tuple(self.plugin_samples[index])[10]
            except Exception:
                progress = 0.0
            reminders.append(
                {
                    "index": index,
                    "label": label,
                    "detail": detail,
                    "summary": summary,
                    "color": color,
                    "icon": icon,
                    "plugin_id": plugin_id,
                    "status": status,
                    "title": title,
                    "body": body,
                    "progress": max(0.0, min(1.0, float(progress or 0.0))),
                }
            )
        return reminders

    def _plugin_function_lines(self, label: str, detail: str, summary: str, status: str, alert_title: str = "", alert_body: str = "") -> tuple[str, tuple[str, ...]]:
        label_key = str(label or "").strip().upper()
        detail = str(detail or "").strip()
        summary = str(summary or "").strip()
        status_key = str(status or "").strip().lower()
        status_copy = {
            "running": "进行中",
            "done": "已完成",
            "due": "现在",
            "soon": "即将开始",
            "loading": "刷新中",
            "error": "需要注意",
        }.get(status_key, "实时状态")
        alert_line = str(alert_body or alert_title or "").strip()
        if label_key == "TIME":
            value = self._plugin_compact_value(label, detail, summary, status)
            second = alert_line or (summary if summary and summary != value else status_copy)
            return "时间 TIME", tuple(line for line in (value, second, "时钟、倒计时与闹钟在这里运行") if line)
        if label_key == "WEATHER":
            return "天气 WEATHER", tuple(line for line in (detail or "天气刷新中", alert_line or summary or status_copy, "公共天气源 · 定位优先") if line)
        if label_key == "AGENDA":
            return "日程 AGENDA", tuple(line for line in (detail or "暂无日程", alert_line or summary or "JSON / ICS", "课表读取录入 · 课表提醒功能") if line)
        return str(label or "PLUGIN"), tuple(line for line in (detail, summary, status_copy) if line)

    def _plugin_panel_sections(self, model: dict[str, object]) -> list[dict[str, str]]:
        label = str(model.get("label", "")).strip()
        label_key = label.upper()
        detail = str(model.get("detail", "") or "").strip()
        summary = str(model.get("summary", "") or "").strip()
        status = str(model.get("status", "") or "").strip().lower()
        alert_title = str(model.get("alert_title", "") or "").strip()
        alert_body = str(model.get("alert_body", "") or "").strip()
        alert = alert_body or alert_title
        if label_key == "TIME":
            clock = detail or "本地时间"
            if status in {"running", "done", "alarm"}:
                countdown = alert or summary or "计时提醒"
            else:
                countdown = "5 / 15 / 30 分钟"
            alarm = alert if status == "alarm" else (summary if re.search(r"\d{1,2}:\d{2}", summary) else "未设置")
            return [
                {"id": "clock", "title": "本地时钟", "body": clock},
                {"id": "countdown", "title": "倒计时", "body": countdown},
                {"id": "alarm", "title": "闹钟", "body": alarm or "未设置"},
            ]
        if label_key == "WEATHER":
            metrics = summary or alert or "等待天气数据"
            return [
                {"id": "current", "title": "当前天气", "body": detail or "刷新中"},
                {"id": "metrics", "title": "风与湿度", "body": metrics},
                {"id": "source", "title": "数据源", "body": alert or "定位优先 · 公共天气源"},
            ]
        if label_key == "AGENDA":
            next_item = alert or summary or detail or "暂无日程"
            return [
                {"id": "next", "title": "下一项", "body": next_item},
                {"id": "todo", "title": "代办清单", "body": "课表读取录入 · 课表提醒功能"},
                {"id": "reminder", "title": "提醒", "body": "15 / 30 / 5 分钟"},
            ]
        return [
            {"id": "primary", "title": label or "PLUGIN", "body": detail},
            {"id": "secondary", "title": "摘要", "body": summary},
            {"id": "state", "title": "状态", "body": status or "normal"},
        ]

    def _paint_plugin_panel_module(
        self,
        painter: QPainter,
        rect: QRectF,
        title: str,
        body: str,
        theme: dict[str, QColor],
        color: QColor,
        emphasized: bool = False,
        reserve_action: bool = False,
    ):
        radius = min(self._u(15.0), rect.height() * 0.28)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme["cell_active"] if emphasized else theme["cell"])
        painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(QPen(theme["stroke"], self._u(1.0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(self._u(0.8), self._u(0.8), -self._u(0.8), -self._u(0.8)), radius, radius)

        accent = QRectF(rect.left() + self._u(10.0), rect.top() + self._u(9.0), self._u(22.0), self._u(3.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 235))
        painter.drawRoundedRect(accent, self._u(1.5), self._u(1.5))

        title_rect = QRectF(rect.left() + self._u(10.0), rect.top() + self._u(14.0), rect.width() - self._u(20.0), self._u(18.0))
        painter.setFont(self._font(8, QFont.Weight.DemiBold))
        painter.setPen(theme["muted"])
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)

        body_bottom = rect.bottom() - (self._u(28.0) if reserve_action else self._u(8.0))
        body_rect = QRectF(rect.left() + self._u(10.0), title_rect.bottom() + self._u(2.0), rect.width() - self._u(20.0), max(self._u(18.0), body_bottom - title_rect.bottom()))
        self._paint_scrolling_text(painter, body_rect, body, 12 if emphasized else 10, theme["text"], emphasized)

    def _paint_plugin_panel_action_button(self, painter: QPainter, rect: QRectF, action: dict[str, str], color: QColor):
        command = str(action.get("command", ""))
        if not command:
            return
        button_h = min(self._u(24.0), max(self._u(20.0), rect.height() * 0.26))
        button = QRectF(rect.left() + self._u(10.0), rect.bottom() - button_h - self._u(8.0), rect.width() - self._u(20.0), button_h)
        self.plugin_action_rects[command] = QRectF(button)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 232))
        painter.drawRoundedRect(button, self._u(8.0), self._u(8.0))
        painter.setFont(self._font(8, QFont.Weight.DemiBold))
        painter.setPen(QColor(255, 255, 255, 246))
        painter.drawText(button.adjusted(self._u(8.0), 0.0, -self._u(8.0), 0.0), Qt.AlignmentFlag.AlignCenter, str(action.get("label") or "操作"))

    def _paint_time_clock_module(self, painter: QPainter, rect: QRectF, section: dict[str, str], theme: dict[str, QColor], color: QColor, progress: float, status: str):
        self._paint_plugin_panel_module(painter, rect, section["title"], "", theme, color, emphasized=True)
        face_size = min(rect.width() - self._u(22.0), rect.height() - self._u(34.0))
        face = QRectF(rect.center().x() - face_size / 2.0, rect.top() + self._u(26.0), face_size, face_size)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 238), self._u(3.2), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(face)
        self._paint_plugin_progress_ring(painter, face.adjusted(self._u(4.0), self._u(4.0), -self._u(4.0), -self._u(4.0)), color, progress, status)
        center = face.center()
        painter.setPen(QPen(theme["text"], self._u(2.5), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, QPointF(center.x() + face.width() * 0.22, center.y() + face.height() * 0.13))
        painter.setPen(QPen(theme["muted"], self._u(1.8), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, QPointF(center.x() - face.width() * 0.16, center.y() - face.height() * 0.18))

    def _paint_time_module_shell(self, painter: QPainter, rect: QRectF, theme: dict[str, QColor], active: bool = False):
        ref = self._time_reference_palette(theme, QColor(255, 184, 0))
        radius = min(self._u(16.0), rect.height() * 0.26)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ref["module"])
        painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(QPen(ref.get("module_stroke", ref["accent"]), self._u(1.2 if not self._system_dark_mode() else 1.4)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(self._u(0.8), self._u(0.8), -self._u(0.8), -self._u(0.8)), radius, radius)

    def _time_clock_hand_angles(self, clock_text: str = "") -> dict[str, float]:
        match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(clock_text or ""))
        if match:
            hour = int(match.group(1)) % 24
            minute = max(0, min(59, int(match.group(2))))
            second = max(0, min(59, int(match.group(3) or 0)))
        else:
            now = datetime.now().astimezone()
            hour, minute, second = now.hour, now.minute, now.second
        return {
            "hour": (hour % 12) * 30.0 + minute * 0.5 + second / 120.0,
            "minute": minute * 6.0 + second * 0.1,
            "second": second * 6.0,
        }

    def _clock_hand_end(self, center: QPointF, length: float, angle_degrees: float) -> QPointF:
        radians = math.radians(float(angle_degrees) - 90.0)
        return QPointF(center.x() + math.cos(radians) * length, center.y() + math.sin(radians) * length)

    def _draw_time_clock_face(self, painter: QPainter, face: QRectF, color: QColor, theme: dict[str, QColor], clock_text: str = ""):
        ref = self._time_reference_palette(theme, color)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ref["accent_track"], self._u(5.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(face.adjusted(-self._u(1.0), -self._u(1.0), self._u(1.0), self._u(1.0)))
        painter.setPen(QPen(ref["accent"], self._u(1.5), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(face)
        center = face.center()
        angles = self._time_clock_hand_angles(clock_text)
        radius = face.width() / 2.0
        painter.setPen(QPen(ref["accent"], self._u(2.4), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, self._clock_hand_end(center, radius * 0.48, angles["hour"]))
        painter.setPen(QPen(ref["accent"], self._u(1.7), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, self._clock_hand_end(center, radius * 0.70, angles["minute"]))
        second_pen = QColor(ref["accent"])
        second_pen.setAlpha(155)
        painter.setPen(QPen(second_pen, self._u(0.9), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, self._clock_hand_end(center, radius * 0.76, angles["second"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ref["accent"])
        painter.drawEllipse(QRectF(center.x() - self._u(2.3), center.y() - self._u(2.3), self._u(4.6), self._u(4.6)))

    def _paint_time_dashboard_clock(self, painter: QPainter, rect: QRectF, model: dict[str, object], color: QColor, theme: dict[str, QColor]):
        ref = self._time_reference_palette(theme, color)
        self._paint_time_module_shell(painter, rect, theme, active=True)
        clock = model["clock"]
        face_size = min(rect.width() - self._u(24.0), rect.height() - self._u(35.0))
        face = QRectF(rect.center().x() - face_size / 2.0, rect.top() + self._u(10.0), face_size, face_size)
        self._draw_time_clock_face(painter, face, color, theme, str(clock.get("time") or ""))
        painter.setFont(self._font(8, QFont.Weight.DemiBold))
        painter.setPen(ref["accent"])
        painter.drawText(QRectF(rect.left() + self._u(8.0), face.bottom() + self._u(2.0), rect.width() - self._u(16.0), self._u(14.0)), Qt.AlignmentFlag.AlignCenter, str(clock["location"]))

    def _time_countdown_ring_rect(self, rect: QRectF) -> QRectF:
        ring_size = min(rect.width() - self._u(28.0), rect.height() - self._u(18.0))
        ring_size = max(self._u(42.0), ring_size)
        center = rect.center() + QPointF(0.0, self._u(4.0))
        return QRectF(center.x() - ring_size / 2.0, center.y() - ring_size / 2.0, ring_size, ring_size)

    def _time_countdown_ring_widths(self) -> dict[str, float]:
        return {"width": self._u(7.8), "track_width": self._u(6.6)}

    def _paint_time_countdown_text_button(self, painter: QPainter, rect: QRectF, label: str, fill: QColor, stroke: QColor, text: QColor):
        painter.setPen(QPen(stroke, self._u(1.3)))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        painter.setFont(self._font(12, QFont.Weight.Bold))
        painter.setPen(text)
        painter.drawText(rect.adjusted(self._u(5.0), 0.0, -self._u(5.0), 0.0), Qt.AlignmentFlag.AlignCenter, label)

    def _paint_time_countdown_icon_button(self, painter: QPainter, rect: QRectF, kind: str, fill: QColor, stroke: QColor, icon: QColor):
        painter.setPen(QPen(stroke, self._u(1.0)))
        painter.setBrush(fill)
        painter.drawEllipse(rect)
        painter.setPen(QPen(icon, self._u(2.2), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if kind == "pause":
            bar_w = rect.width() * 0.13
            gap = rect.width() * 0.12
            top = rect.top() + rect.height() * 0.25
            bottom = rect.bottom() - rect.height() * 0.25
            x1 = rect.center().x() - gap - bar_w / 2.0
            x2 = rect.center().x() + gap + bar_w / 2.0
            painter.drawLine(QPointF(x1, top), QPointF(x1, bottom))
            painter.drawLine(QPointF(x2, top), QPointF(x2, bottom))
            return
        if kind == "play":
            path = QPainterPath()
            path.moveTo(rect.left() + rect.width() * 0.38, rect.top() + rect.height() * 0.28)
            path.lineTo(rect.left() + rect.width() * 0.38, rect.bottom() - rect.height() * 0.28)
            path.lineTo(rect.right() - rect.width() * 0.27, rect.center().y())
            path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(icon)
            painter.drawPath(path)
            return
        inset = rect.width() * 0.30
        painter.drawLine(QPointF(rect.left() + inset, rect.top() + inset), QPointF(rect.right() - inset, rect.bottom() - inset))
        painter.drawLine(QPointF(rect.right() - inset, rect.top() + inset), QPointF(rect.left() + inset, rect.bottom() - inset))

    def _paint_static_elided_text(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        size: int,
        color: QColor,
        bold: bool,
        alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
    ):
        font = self._font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(color)
        fm = QFontMetrics(font)
        available = max(1, int(rect.width() - self._u(4.0)))
        clipped = fm.elidedText(str(text or ""), Qt.TextElideMode.ElideRight, available)
        painter.save()
        painter.setClipRect(rect)
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | alignment, clipped)
        painter.restore()

    def _time_alarm_note_should_scroll(self, text: str, rect: QRectF, size: int, bold: bool, max_chars: int) -> bool:
        value = str(text or "").strip()
        threshold = 5 if int(max_chars) <= 5 else max(4, int(max_chars))
        if len(value) > threshold:
            return True
        font = self._font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        return QFontMetrics(font).horizontalAdvance(value) > rect.width() - self._u(8.0)

    def _paint_time_symbol(self, painter: QPainter, rect: QRectF, kind: str, color: QColor, stroke_width: float | None = None):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(color, stroke_width if stroke_width is not None else self._u(1.8), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if str(kind or "").lower() == "alarm":
            painter.drawEllipse(rect.adjusted(self._u(1.0), self._u(1.0), -self._u(1.0), -self._u(1.0)))
            center = rect.center()
            painter.drawLine(center, QPointF(center.x(), rect.top() + rect.height() * 0.28))
            painter.drawLine(center, QPointF(rect.right() - rect.width() * 0.28, rect.bottom() - rect.height() * 0.30))
        else:
            top = rect.top() + rect.height() * 0.20
            bottom = rect.bottom() - rect.height() * 0.20
            left = rect.left() + rect.width() * 0.30
            right = rect.right() - rect.width() * 0.30
            mid = rect.center().y()
            painter.drawLine(QPointF(left, top), QPointF(right, top))
            painter.drawLine(QPointF(left, bottom), QPointF(right, bottom))
            painter.drawLine(QPointF(left, top), QPointF(rect.center().x(), mid))
            painter.drawLine(QPointF(right, top), QPointF(rect.center().x(), mid))
            painter.drawLine(QPointF(rect.center().x(), mid), QPointF(left, bottom))
            painter.drawLine(QPointF(rect.center().x(), mid), QPointF(right, bottom))
        painter.restore()

    def _paint_time_countdown_ring(self, painter: QPainter, ring: QRectF, entry: dict[str, object], ref: dict[str, QColor]):
        widths = self._time_countdown_ring_widths()
        status = str(entry.get("status") or "running").lower()
        progress = 1.0 if status in {"done", "due", "alarm"} else max(0.0, min(1.0, float(entry.get("progress", 0.0) or 0.0)))
        ring_line = ring.adjusted(self._u(4.0), self._u(4.0), -self._u(4.0), -self._u(4.0))
        painter.setPen(QPen(ref["accent_track"], widths["track_width"], Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(ring_line)
        painter.setPen(QPen(ref["accent"], widths["width"], Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(ring_line, 90 * 16, int(-360 * 16 * progress))
        painter.setFont(self._font(16, QFont.Weight.Bold))
        painter.setPen(ref["text"])
        painter.drawText(ring, Qt.AlignmentFlag.AlignCenter, str(entry.get("text") or ""))

    def _paint_time_countdown_pause_overlay(self, painter: QPainter, rect: QRectF, ring: QRectF, ref: dict[str, QColor]):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 116))
        painter.drawRoundedRect(rect.adjusted(self._u(2.0), self._u(2.0), -self._u(2.0), -self._u(2.0)), min(self._u(15.0), rect.height() * 0.24), min(self._u(15.0), rect.height() * 0.24))
        icon_size = min(self._u(38.0), ring.height() * 0.34)
        play = QRectF(ring.center().x() - icon_size / 2.0, ring.center().y() - icon_size * 1.22, icon_size, icon_size)
        clear = QRectF(ring.center().x() - icon_size / 2.0, ring.center().y() + icon_size * 0.38, icon_size, icon_size)
        red = QColor(223, 0, 0, 245)
        self._paint_time_countdown_icon_button(painter, play, "play", ref["active"], ref["active"], QColor(255, 255, 255, 246))
        self._paint_time_countdown_icon_button(painter, clear, "clear", red, red, QColor(255, 255, 255, 246))
        self.plugin_action_rects["timer-resume"] = QRectF(play.adjusted(-self._u(2.0), -self._u(2.0), self._u(2.0), self._u(2.0)))
        self.plugin_action_rects["timer-clear"] = QRectF(clear.adjusted(-self._u(2.0), -self._u(2.0), self._u(2.0), self._u(2.0)))

    def _paint_time_countdown_module(self, painter: QPainter, rect: QRectF, model: dict[str, object], color: QColor, theme: dict[str, QColor]):
        active = model["countdown_active"]
        ref = self._time_reference_palette(theme, color)
        self._paint_time_module_shell(painter, rect, theme, active=bool(active.get("active")))
        title_rect = QRectF(rect.left() + self._u(12.0), rect.top() + self._u(8.0), rect.width() - self._u(24.0), self._u(18.0))
        painter.setFont(self._font(9, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "倒计时")
        entries = self._time_countdown_entries(model)
        if entries:
            layout_mode = str(model.get("time_layout_mode") or "").lower()
            stretch_controls = layout_mode == "stretch" or (not layout_mode and rect.width() >= self._u(250.0))
            show_two = len(entries) > 1 and stretch_controls
            terminal_statuses = {"done", "due", "alarm"}
            if show_two:
                ring_size = min(rect.height() - self._u(16.0), (rect.width() - self._u(34.0)) / 2.0)
                ring_size = max(self._u(52.0), ring_size)
                total_w = ring_size * 2.0 + self._u(8.0)
                x = rect.center().x() - total_w / 2.0
                y = rect.center().y() - ring_size / 2.0 + self._u(4.0)
                for index, entry in enumerate(entries[:2]):
                    ring = QRectF(x + index * (ring_size + self._u(8.0)), y, ring_size, ring_size)
                    self._paint_time_countdown_ring(painter, ring, entry, ref)
                    entry_status = str(entry.get("status") or "").lower()
                    if entry_status == "paused":
                        self._paint_time_countdown_pause_overlay(painter, QRectF(ring.adjusted(-self._u(4.0), -self._u(4.0), self._u(4.0), self._u(4.0))), ring, ref)
                    elif index == 0 and entry_status in terminal_statuses:
                        self.plugin_action_rects["timer-clear"] = QRectF(ring.adjusted(-self._u(5.0), -self._u(5.0), self._u(5.0), self._u(5.0)))
                    elif index == 0:
                        self.plugin_action_rects["timer-pause"] = QRectF(ring.adjusted(-self._u(5.0), -self._u(5.0), self._u(5.0), self._u(5.0)))
                return

            entry = entries[0]
            status = str(entry.get("status") or "running").lower()
            paused = status == "paused"
            terminal = status in terminal_statuses
            if terminal:
                ring = self._time_countdown_ring_rect(rect)
                self._paint_time_countdown_ring(painter, ring, entry, ref)
                self.plugin_action_rects["timer-clear"] = QRectF(ring.adjusted(-self._u(5.0), -self._u(5.0), self._u(5.0), self._u(5.0)))
                return
            if stretch_controls and not paused:
                ring_size = min(rect.height() - self._u(24.0), rect.width() * 0.36)
                ring_size = max(self._u(48.0), ring_size)
                ring = QRectF(rect.left() + self._u(18.0), rect.center().y() - ring_size / 2.0 + self._u(5.0), ring_size, ring_size)
                self._paint_time_countdown_ring(painter, ring, entry, ref)
                ring_hit = QRectF(ring.adjusted(-self._u(5.0), -self._u(5.0), self._u(5.0), self._u(5.0)))
                button_w = min(self._u(82.0), max(self._u(56.0), rect.right() - ring.right() - self._u(24.0)))
                button_h = self._u(28.0)
                button_x = rect.right() - button_w - self._u(14.0)
                pause = QRectF(button_x, rect.center().y() - button_h - self._u(3.0), button_w, button_h)
                clear = QRectF(button_x, rect.center().y() + self._u(5.0), button_w, button_h)
                red = QColor(223, 0, 0, 245)
                self._paint_time_countdown_text_button(painter, pause, "暂停", QColor(0, 0, 0, 0), ref.get("control_stroke", ref["accent"]), ref["text"])
                self._paint_time_countdown_text_button(painter, clear, "取消", red, ref.get("control_stroke", ref["accent"]), ref["text"])
                self.plugin_action_rects["timer-pause"] = ring_hit.united(pause.adjusted(-self._u(2.0), -self._u(2.0), self._u(2.0), self._u(2.0)))
                self.plugin_action_rects["timer-clear"] = QRectF(clear.adjusted(-self._u(2.0), -self._u(2.0), self._u(2.0), self._u(2.0)))
                return

            ring = self._time_countdown_ring_rect(rect)
            self._paint_time_countdown_ring(painter, ring, entry, ref)
            if paused:
                self._paint_time_countdown_pause_overlay(painter, rect, ring, ref)
            else:
                self.plugin_action_rects["timer-pause"] = QRectF(ring.adjusted(-self._u(5.0), -self._u(5.0), self._u(5.0), self._u(5.0)))
            return

        presets = model["countdown_presets"]
        grid_top = title_rect.bottom() + self._u(3.0)
        grid_h = max(self._u(46.0), rect.bottom() - grid_top - self._u(10.0))
        layout_mode = str(model.get("time_layout_mode") or "").lower()
        if layout_mode == "stretch":
            side_pad = self._u(15.0)
            gap = self._u(10.0)
            available_w = max(self._u(120.0), rect.width() - side_pad * 2.0)
            ring_size = min(grid_h - self._u(4.0), (available_w - gap * 3.0) / 4.0)
            ring_size = max(self._u(60.0), ring_size)
            total_w = ring_size * 4.0 + gap * 3.0
            start_x = rect.left() + (rect.width() - total_w) / 2.0
            y = grid_top + (grid_h - ring_size) / 2.0
            for index, item in enumerate(presets[:4]):
                ring = QRectF(start_x + index * (ring_size + gap), y, ring_size, ring_size)
                command = f"timer-start-{int(item['minutes'])}"
                self.plugin_action_rects[command] = QRectF(ring.adjusted(-self._u(3.0), -self._u(3.0), self._u(3.0), self._u(3.0)))
                ring_line = ring.adjusted(self._u(5.0), self._u(5.0), -self._u(5.0), -self._u(5.0))
                painter.setPen(QPen(ref["accent_track"], self._u(5.8), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(ring_line)
                painter.setPen(QPen(ref["accent"], self._u(7.2), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawArc(ring_line, 90 * 16, -360 * 16)
                self._paint_static_elided_text(
                    painter,
                    ring.adjusted(self._u(5.0), 0.0, -self._u(5.0), 0.0),
                    f"{int(item['minutes'])}分钟",
                    12,
                    ref["accent"],
                    True,
                    Qt.AlignmentFlag.AlignCenter,
                )
            return
        use_pills = True
        gap_x = self._u(10.0 if layout_mode == "double" else 12.0)
        gap_y = self._u(10.0)
        button_h = min(self._u(30.0), max(self._u(22.0), (grid_h - gap_y) / 2.0))
        button_w = min(self._u(108.0), max(button_h * 2.15, (rect.width() - self._u(30.0) - gap_x) / 2.0))
        grid_w = button_w * 2.0 + gap_x
        grid_h_used = button_h * 2.0 + gap_y
        grid_x = rect.left() + (rect.width() - grid_w) / 2.0
        grid_y = grid_top + (grid_h - grid_h_used) / 2.0
        for index, item in enumerate(presets):
            col = index % 2
            row = index // 2
            button = QRectF(grid_x + col * (button_w + gap_x), grid_y + row * (button_h + gap_y), button_w, button_h)
            command = f"timer-start-{int(item['minutes'])}"
            self.plugin_action_rects[command] = QRectF(button.adjusted(-self._u(3.0), -self._u(3.0), self._u(3.0), self._u(3.0)))
            painter.setPen(QPen(ref.get("control_stroke", ref["accent"]), self._u(1.2)))
            painter.setBrush(ref["module"])
            painter.drawRoundedRect(button, button.height() / 2.0, button.height() / 2.0)
            self._paint_static_elided_text(painter, button.adjusted(self._u(5.0), 0.0, -self._u(5.0), 0.0), f"{int(item['minutes'])}分钟", 10, ref["accent"], True, Qt.AlignmentFlag.AlignCenter)

    def _paint_time_alarm_switch(self, painter: QPainter, rect: QRectF, enabled: bool, color: QColor, theme: dict[str, QColor]):
        ref = self._time_reference_palette(theme, color)
        radius = rect.height() / 2.0
        painter.setPen(QPen(ref["active"] if enabled else ref["muted_dim"], self._u(1.0)))
        painter.setBrush(ref["active"] if enabled else ref["off_track"])
        painter.drawRoundedRect(rect, radius, radius)
        knob = rect.height() - self._u(4.0)
        knob_x = rect.right() - knob - self._u(2.0) if enabled else rect.left() + self._u(2.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ref["thumb"])
        painter.drawEllipse(QRectF(knob_x, rect.top() + self._u(2.0), knob, knob))

    def _paint_time_page_dots(self, painter: QPainter, rect: QRectF, visible: str, theme: dict[str, QColor], color: QColor):
        if rect.isNull() or rect.width() <= 0.0:
            return
        ref = self._time_reference_palette(theme, color)
        dot = self._u(9.0)
        gap = self._u(7.0)
        total_h = dot * 2.0 + gap
        x = rect.center().x() - dot / 2.0
        y = rect.center().y() - total_h / 2.0
        modules = self._time_panel_modules()
        for index, module in enumerate(modules):
            active = module == visible
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ref["text"] if active else ref["muted_dim"])
            painter.drawEllipse(QRectF(x, y + index * (dot + gap), dot, dot))

    def _paint_time_alarm_module(self, painter: QPainter, rect: QRectF, model: dict[str, object], color: QColor, theme: dict[str, QColor]):
        ref = self._time_reference_palette(theme, color)
        self._paint_time_module_shell(painter, rect, theme, active=False)
        alarms = [dict(row) for row in list(model.get("alarms", []))[:8]]
        pending_alarm = dict(model.get("alarm_due_pending") or self._time_alarm_due_pending())
        if pending_alarm:
            confirm_rect = QRectF(rect)
            self.plugin_alarm_list_rect = QRectF()
            self.plugin_alarm_visible_range = (0, 0)
            self.plugin_alarm_list_scroll_index = 0.0
            self.plugin_alarm_list_bottom_spacer_rows = 0.0
            self.plugin_alarm_list_max_scroll = 0.0
            self.plugin_action_rects["alarm-due-confirm"] = QRectF(rect.adjusted(-self._u(4.0), -self._u(4.0), self._u(4.0), self._u(4.0)))
            radius = min(self._u(16.0), rect.height() * 0.26)
            fill = QLinearGradient(confirm_rect.topLeft(), confirm_rect.bottomLeft())
            fill.setColorAt(0.0, QColor(255, 211, 46, 255))
            fill.setColorAt(1.0, ref["accent"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(fill))
            painter.drawRoundedRect(confirm_rect, radius, radius)
            painter.setPen(QPen(QColor(255, 236, 142, 120), self._u(1.0)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(confirm_rect.adjusted(self._u(0.7), self._u(0.7), -self._u(0.7), -self._u(0.7)), radius, radius)
            text_color = QColor(32, 23, 0, 248)
            label_text = str(pending_alarm.get("label") or "闹钟").strip() or "闹钟"
            time_text = str(pending_alarm.get("time") or "").strip()
            content = confirm_rect.adjusted(self._u(14.0), self._u(10.0), -self._u(14.0), -self._u(10.0))
            painter.setFont(self._font(8, QFont.Weight.DemiBold))
            painter.setPen(QColor(68, 47, 0, 210))
            painter.drawText(QRectF(content.left(), content.top(), content.width(), self._u(15.0)), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "闹钟提醒")
            time_w = self._u(66.0) if time_text else 0.0
            label_rect = QRectF(content.left(), content.center().y() - self._u(17.0), max(self._u(70.0), content.width() - time_w - self._u(8.0)), self._u(34.0))
            self._paint_static_elided_text(painter, label_rect, label_text, 16, text_color, True, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            if time_text:
                time_rect = QRectF(content.right() - time_w, label_rect.top(), time_w, label_rect.height())
                self._paint_static_elided_text(painter, time_rect, time_text, 14, text_color, True, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            hint_rect = QRectF(content.left(), content.bottom() - self._u(16.0), content.width(), self._u(16.0))
            self._paint_static_elided_text(painter, hint_rect, "点击确认", 9, QColor(77, 54, 0, 215), True, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return
        painter.setFont(self._font(9, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        painter.drawText(QRectF(rect.left() + self._u(10.0), rect.top() + self._u(8.0), rect.width() - self._u(20.0), self._u(16.0)), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "闹钟")
        row_top = rect.top() + self._u(27.0)
        add_h = self._u(27.0)
        add_rect = QRectF(rect.left() + self._u(8.0), rect.bottom() - self._u(5.0) - add_h, rect.width() - self._u(16.0), add_h)
        list_bottom = add_rect.top() - self._u(6.0)
        list_rect = QRectF(rect.left() + self._u(8.0), row_top, rect.width() - self._u(16.0), max(0.0, list_bottom - row_top))
        slot_count = 4
        row_h = max(self._u(18.0), list_rect.height() / float(slot_count))
        visible_count = min(slot_count, len(alarms)) if alarms else 0
        bottom_spacer_rows = max(1.0, add_h / max(1.0, row_h))
        max_start = max(0.0, float(len(alarms) - visible_count) + (bottom_spacer_rows if alarms else 0.0))
        try:
            scroll_index = float(getattr(self, "plugin_alarm_list_scroll_index", 0.0) or 0.0)
        except (TypeError, ValueError):
            scroll_index = 0.0
        scroll_index = max(0.0, min(max_start, scroll_index)) if visible_count > 0 else 0.0
        start = int(math.floor(scroll_index))
        fraction = scroll_index - float(start)
        self.plugin_alarm_list_scroll_index = scroll_index
        self.plugin_alarm_list_bottom_spacer_rows = bottom_spacer_rows if alarms else 0.0
        self.plugin_alarm_list_max_scroll = max_start
        self.plugin_alarm_list_rect = QRectF(list_rect)
        self.plugin_alarm_visible_range = (start, visible_count)
        self.plugin_action_rects["alarm-add-open"] = QRectF(add_rect.adjusted(-self._u(2.0), -self._u(2.0), self._u(2.0), self._u(2.0)))
        painter.save()
        painter.setClipRect(list_rect)
        extra_row = 1 if fraction > 0.001 else 0
        end = min(len(alarms), start + visible_count + extra_row)
        for index in range(start, end):
            local_index = index - start
            alarm = alarms[index]
            row = QRectF(list_rect.left(), list_rect.top() + (local_index - fraction) * row_h, list_rect.width(), row_h)
            if row.bottom() < list_rect.top() or row.top() > list_rect.bottom():
                continue
            switch = QRectF(row.right() - self._u(26.0), row.center().y() - self._u(7.0), self._u(25.0), self._u(14.0))
            delete_active = int(getattr(self, "plugin_alarm_delete_index", -1) if getattr(self, "plugin_alarm_delete_index", None) is not None else -1) == index
            delete_rect = QRectF(row.right() - self._u(57.0), row.center().y() - self._u(8.5), self._u(54.0), self._u(17.0))
            control_rect = delete_rect if delete_active else switch
            edit_hit = QRectF(row.left(), row.top(), max(self._u(32.0), control_rect.left() - row.left() - self._u(5.0)), row.height())
            self.plugin_action_rects[f"alarm-edit-{index}"] = QRectF(edit_hit)
            if delete_active:
                self.plugin_action_rects[f"alarm-delete-{index}"] = QRectF(delete_rect.adjusted(-self._u(3.0), -self._u(4.0), self._u(3.0), self._u(4.0)))
            else:
                self.plugin_action_rects[f"alarm-toggle-{index}"] = QRectF(switch.adjusted(-self._u(4.0), -self._u(5.0), self._u(4.0), self._u(5.0)))
            time_rect = QRectF(row.left(), row.top(), self._u(42.0), row.height())
            label_rect = QRectF(time_rect.right() + self._u(3.0), row.top(), max(self._u(20.0), control_rect.left() - time_rect.right() - self._u(8.0)), row.height())
            enabled = bool(alarm.get("enabled"))
            time_text = str(alarm.get("time", "") or "")
            label_text = str(alarm.get("label", "") or "")
            time_color = ref["text"] if enabled else ref["muted_dim"]
            label_color = ref["text"] if enabled else ref["muted_dim"]
            self._paint_static_elided_text(painter, time_rect, time_text, 10, time_color, False, Qt.AlignmentFlag.AlignLeft)
            if label_text:
                if self._time_alarm_note_should_scroll(label_text, label_rect, 10, True, 5):
                    self._paint_scrolling_text(painter, label_rect, self._time_alarm_note_display_text(label_text), 10, label_color, True)
                else:
                    self._paint_static_elided_text(painter, label_rect, label_text, 10, label_color, True, Qt.AlignmentFlag.AlignLeft)
            if delete_active:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(210, 35, 39, 245))
                painter.drawRoundedRect(delete_rect, delete_rect.height() / 2.0, delete_rect.height() / 2.0)
                self._paint_static_elided_text(painter, delete_rect.adjusted(self._u(5.0), 0.0, -self._u(5.0), 0.0), "删除", 8, QColor(255, 248, 245, 248), True, Qt.AlignmentFlag.AlignCenter)
            else:
                self._paint_time_alarm_switch(painter, switch, enabled, color, theme)
            if index < end - 1 and row.bottom() < list_rect.bottom():
                painter.setPen(QPen(ref["divider"], self._u(0.8)))
                painter.drawLine(QPointF(row.left(), row.bottom()), QPointF(row.right(), row.bottom()))
        painter.restore()
        self.plugin_action_rects["alarm-list"] = QRectF(list_rect)
        if visible_count > 0 and len(alarms) > visible_count:
            track = QRectF(rect.right() - self._u(5.0), list_rect.top() + self._u(2.0), self._u(2.0), list_rect.height() - self._u(4.0))
            total_rows = max(1.0, len(alarms) + bottom_spacer_rows)
            thumb_h = max(self._u(12.0), track.height() * visible_count / total_rows)
            travel = max(0.0, track.height() - thumb_h)
            thumb_y = track.top() + travel * (scroll_index / max(1.0, max_start))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ref["muted_dim"])
            painter.drawRoundedRect(QRectF(track.left(), thumb_y, track.width(), thumb_h), track.width() / 2.0, track.width() / 2.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ref["accent"])
        painter.drawRoundedRect(add_rect, add_rect.height() / 2.0, add_rect.height() / 2.0)
        self._paint_static_elided_text(painter, add_rect.adjusted(self._u(8.0), 0.0, -self._u(8.0), 0.0), "添 加 闹 钟", 10, QColor(26, 21, 0, 245), True, Qt.AlignmentFlag.AlignCenter)

    def _paint_alarm_picker_field(self, painter: QPainter, rect: QRectF, field: str, value: str, title: str, theme: dict[str, QColor], color: QColor):
        ref = self._time_reference_palette(theme, color)
        self.plugin_alarm_editor_rects[field] = QRectF(rect)
        painter.setPen(QPen(ref["accent"], self._u(1.2)))
        painter.setBrush(ref["module_active"])
        painter.drawRoundedRect(rect, self._u(11.0), self._u(11.0))
        painter.setFont(self._font(10, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        painter.drawText(QRectF(rect.left(), rect.top() + self._u(4.0), rect.width(), self._u(15.0)), Qt.AlignmentFlag.AlignCenter, title)
        painter.setPen(QPen(ref["accent_track"], self._u(1.0)))
        painter.drawLine(QPointF(rect.left() + self._u(9.0), rect.center().y()), QPointF(rect.right() - self._u(9.0), rect.center().y()))
        values = self._alarm_picker_display_values(field)
        row_h = max(self._u(14.0), (rect.height() - self._u(19.0)) / 3.0)
        value_clip = rect.adjusted(self._u(3.0), self._u(18.0), -self._u(3.0), -self._u(3.0))
        center_index = next((index for index, item in enumerate(values) if bool(item.get("current"))), len(values) // 2)
        center_y = value_clip.center().y() - row_h / 2.0
        for index, item in enumerate(values):
            current = bool(item.get("current"))
            text = str(item.get("text") or value)
            painter.save()
            painter.setClipRect(value_clip)
            painter.setOpacity(float(item.get("opacity", 1.0)))
            value_size = 20 if current and field in {"hour", "minute"} else (14 if current else 11)
            painter.setFont(self._font(value_size, QFont.Weight.Bold if current else QFont.Weight.DemiBold))
            painter.setPen(ref["text"] if current else ref["muted_dim"])
            offset = float(item.get("offset", 0.0) or 0.0)
            row = QRectF(rect.left() + self._u(4.0), center_y + (index - center_index + offset) * row_h, rect.width() - self._u(8.0), row_h)
            painter.drawText(row, Qt.AlignmentFlag.AlignCenter, text)
            painter.restore()

    def _time_alarm_editor_layout(self, rect: QRectF) -> dict[str, QRectF]:
        if rect.width() < self._u(600.0):
            shell = QRectF(rect)
            title = QRectF(rect.left() + self._u(18.0), rect.top() + self._u(7.0), rect.width() - self._u(36.0), self._u(19.0))
            label_gap = self._u(10.0)
            label_h = self._u(20.0)
            top_floor = self._u(38.0)
            action_h = self._u(32.0)
            action_gap = self._u(8.0)
            bottom_pad = self._u(10.0) + action_h + action_gap
            max_field_h = max(self._u(44.0), rect.height() - top_floor - label_gap - label_h - bottom_pad)
            field_h = min(self._u(82.0), max(self._u(68.0), rect.height() * 0.38), max_field_h)
            field_y = rect.top() + max(top_floor, rect.height() * 0.30)
            label_y = field_y + field_h + label_gap
            label_bottom = label_y + label_h
            max_label_bottom = rect.bottom() - bottom_pad
            if label_bottom > max_label_bottom:
                field_y = max(rect.top() + self._u(32.0), max_label_bottom - label_h - label_gap - field_h)
                label_y = field_y + field_h + label_gap
            gap = self._u(16.0)
            dot_w = self._u(18.0)
            left_pad = self._u(20.0)
            right_pad = self._u(30.0)
            usable_w = max(self._u(210.0), rect.width() - left_pad - right_pad - dot_w)
            time_w = min(self._u(138.0), max(self._u(104.0), usable_w * 0.28))
            value_w = min(self._u(112.0), max(self._u(72.0), usable_w * 0.22))
            freq_w = usable_w - time_w - value_w - gap * 2.0
            if freq_w < self._u(88.0):
                value_w = 0.0
                freq_w = usable_w - time_w - gap
            time_rect = QRectF(rect.left() + left_pad, field_y, time_w, field_h)
            frequency_rect = QRectF(time_rect.right() + gap, field_y, max(self._u(88.0), freq_w), field_h)
            value_rect = QRectF(frequency_rect.right() + gap, field_y, value_w, field_h) if value_w > 0.0 else QRectF()
            if not value_rect.isNull() and value_rect.right() > rect.right() - dot_w - self._u(10.0):
                value_rect = QRectF()
            note_right = rect.right() - dot_w - right_pad
            note_rect = QRectF(rect.left() + left_pad, field_y, max(self._u(170.0), note_right - rect.left() - left_pad), field_h)
            action_y = rect.bottom() - self._u(10.0) - action_h
            action_gap_x = self._u(14.0)
            action_w = min(self._u(156.0), max(self._u(118.0), (rect.width() - self._u(44.0) - action_gap_x) / 2.0))
            actions_total = action_w * 2.0 + action_gap_x
            action_x = rect.center().x() - actions_total / 2.0
            cancel = QRectF(action_x, action_y, action_w, action_h)
            save = QRectF(cancel.right() + action_gap_x, action_y, action_w, action_h)
            main_dot = QRectF(rect.right() - self._u(26.0), rect.center().y() - self._u(13.0), self._u(18.0), self._u(26.0))
            note_dot = QRectF(main_dot)
            return {
                "mode": "narrow",
                "shell": shell,
                "title": title,
                "main_page": QRectF(rect),
                "note_page": QRectF(rect),
                "time": time_rect,
                "main_time": time_rect,
                "main_frequency": frequency_rect,
                "main_frequency_value": value_rect,
                "frequency": frequency_rect,
                "frequency_value": value_rect,
                "note": note_rect,
                "time_label": QRectF(time_rect.left(), label_y, time_rect.width(), label_h),
                "frequency_label": QRectF(frequency_rect.left(), label_y, max(frequency_rect.width(), value_rect.right() - frequency_rect.left() if not value_rect.isNull() else frequency_rect.width()), label_h),
                "note_label": QRectF(note_rect.left(), label_y, note_rect.width(), label_h),
                "main_note_dot": main_dot,
                "note_main_dot": note_dot,
                "save": save,
                "cancel": cancel,
            }
        shell = QRectF(rect)
        title = QRectF(rect.left() + self._u(16.0), rect.top() + self._u(7.0), rect.width() - self._u(32.0), self._u(19.0))
        field_h = min(self._u(43.0), max(self._u(34.0), rect.height() * 0.31))
        label_h = self._u(15.0)
        row_gap = self._u(16.0)
        action_h = self._u(32.0)
        action_gap = self._u(8.0)
        action_y = rect.bottom() - self._u(10.0) - action_h
        field_y = title.bottom() + self._u(12.0)
        max_label_bottom = action_y - action_gap
        field_y = min(field_y, max(rect.top() + self._u(28.0), max_label_bottom - label_h - self._u(6.0) - field_h))
        label_y = field_y + field_h + self._u(6.0)
        time_w = self._u(112.0)
        note_w = self._u(106.0)
        frequency_w = self._u(58.0)
        weekdays_w = self._u(78.0)
        total_w = time_w + note_w + frequency_w + weekdays_w + row_gap * 3.0
        if total_w > rect.width() - self._u(24.0):
            scale = max(0.72, (rect.width() - self._u(24.0) - row_gap * 3.0) / max(1.0, total_w - row_gap * 3.0))
            time_w *= scale
            note_w *= scale
            frequency_w *= scale
            weekdays_w *= scale
            total_w = time_w + note_w + frequency_w + weekdays_w + row_gap * 3.0
        x = rect.center().x() - total_w / 2.0
        time_rect = QRectF(x, field_y, time_w, field_h)
        note_rect = QRectF(time_rect.right() + row_gap, field_y, note_w, field_h)
        frequency_rect = QRectF(note_rect.right() + row_gap, field_y, frequency_w, field_h)
        weekdays_rect = QRectF(frequency_rect.right() + row_gap, field_y, weekdays_w, field_h)
        action_gap_x = self._u(16.0)
        action_w = min(self._u(150.0), max(self._u(118.0), (rect.width() - self._u(64.0) - action_gap_x) / 2.0))
        actions_total = action_w * 2.0 + action_gap_x
        action_x = rect.center().x() - actions_total / 2.0
        cancel = QRectF(action_x, action_y, action_w, action_h)
        save = QRectF(cancel.right() + action_gap_x, action_y, action_w, action_h)
        return {
            "shell": shell,
            "title": title,
            "time": time_rect,
            "note": note_rect,
            "frequency": frequency_rect,
            "weekdays": weekdays_rect,
            "time_label": QRectF(time_rect.left(), label_y, time_rect.width(), label_h),
            "note_label": QRectF(note_rect.left(), label_y, note_rect.width(), label_h),
            "frequency_label": QRectF(frequency_rect.left(), label_y, frequency_rect.width(), label_h),
            "weekdays_label": QRectF(weekdays_rect.left(), label_y, weekdays_rect.width(), label_h),
            "save": save,
            "cancel": cancel,
        }

    def _paint_alarm_editor_caret(self, painter: QPainter, rect: QRectF, field: str, ref: dict[str, QColor]):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(ref["text"], self._u(1.6), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        x = rect.center().x() + rect.width() * 0.22
        top = rect.top() + rect.height() * 0.26
        bottom = rect.bottom() - rect.height() * 0.26
        painter.drawLine(QPointF(x, top), QPointF(x, bottom))
        painter.restore()

    def _paint_time_alarm_editor_box(self, painter: QPainter, rect: QRectF, text: str, placeholder: bool, ref: dict[str, QColor], size: int = 14, bold: bool = True, focused: bool = False, field: str = ""):
        painter.setPen(QPen(ref["accent"], self._u(1.9 if focused else 1.5)))
        painter.setBrush(ref["module_active"])
        painter.drawRoundedRect(rect, self._u(7.0), self._u(7.0))
        color = ref["muted_dim"] if placeholder else ref["text"]
        text_rect = rect.adjusted(self._u(7.0), 0.0, -self._u(7.0), 0.0)
        if field == "note" and not placeholder and self._time_alarm_note_should_scroll(text, text_rect, size, bold, 5):
            self._paint_scrolling_text(painter, text_rect, self._time_alarm_note_display_text(text), size, color, bold, centered=True)
        else:
            self._paint_static_elided_text(painter, text_rect, text, size, color, bold, Qt.AlignmentFlag.AlignCenter)
        if focused and field:
            self._paint_alarm_editor_caret(painter, rect, field, ref)

    def _paint_alarm_detail_input_box(self, painter: QPainter, rect: QRectF, field: str, text: str, ref: dict[str, QColor], focused: bool):
        self.plugin_alarm_editor_rects[field] = QRectF(rect)
        self.plugin_action_rects[f"alarm-editor-focus-{field}"] = QRectF(rect)
        painter.setPen(QPen(ref["accent"], self._u(2.0)))
        painter.setBrush(QColor(28, 28, 27, 238))
        painter.drawRoundedRect(rect, self._u(10.0), self._u(10.0))
        values = self._alarm_picker_display_values(field)
        if len(values) <= 1:
            painter.setFont(self._font(30, QFont.Weight.Bold))
            painter.setPen(ref["accent"])
            painter.drawText(rect.adjusted(self._u(8.0), 0.0, -self._u(8.0), 0.0), Qt.AlignmentFlag.AlignCenter, text)
        else:
            clip = rect.adjusted(self._u(4.0), self._u(4.0), -self._u(4.0), -self._u(4.0))
            row_h = max(self._u(23.0), rect.height() * 0.44)
            center_index = next((index for index, item in enumerate(values) if bool(item.get("current"))), len(values) // 2)
            center_y = clip.center().y() - row_h / 2.0
            for index, item in enumerate(values):
                current = bool(item.get("current"))
                painter.save()
                painter.setClipRect(clip)
                painter.setOpacity(float(item.get("opacity", 1.0)))
                painter.setFont(self._font(30 if current else 18, QFont.Weight.Bold if current else QFont.Weight.DemiBold))
                painter.setPen(ref["accent"] if current else ref["muted_dim"])
                offset = float(item.get("offset", 0.0) or 0.0)
                row = QRectF(rect.left() + self._u(8.0), center_y + (index - center_index + offset) * row_h, rect.width() - self._u(16.0), row_h)
                painter.drawText(row, Qt.AlignmentFlag.AlignCenter, str(item.get("text") or text))
                painter.restore()
        if focused:
            self._paint_alarm_editor_caret(painter, rect, field, ref)

    def _paint_time_alarm_editor_action(self, painter: QPainter, rect: QRectF, text: str, ref: dict[str, QColor], primary: bool):
        painter.setPen(QPen(ref["accent"], self._u(1.1)))
        painter.setBrush(ref["accent"] if primary else QColor(24, 24, 23, 245))
        painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        painter.setFont(self._font(12 if rect.height() >= self._u(30.0) else 10, QFont.Weight.Bold))
        painter.setPen(QColor(35, 24, 0, 245) if primary else ref["accent"])
        painter.drawText(rect.adjusted(self._u(5.0), 0.0, -self._u(5.0), 0.0), Qt.AlignmentFlag.AlignCenter, text)

    def _paint_time_alarm_editor_label(self, painter: QPainter, rect: QRectF, text: str, ref: dict[str, QColor]):
        painter.setFont(self._font(10, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_alarm_editor_page_dots(self, painter: QPainter, rect: QRectF, active_page: str, ref: dict[str, QColor]):
        if rect.isNull() or rect.width() <= 0.0:
            return
        dot = self._u(7.0)
        gap = self._u(7.0)
        total_h = dot * 2.0 + gap
        x = rect.center().x() - dot / 2.0
        y = rect.center().y() - total_h / 2.0
        for index, page in enumerate(("main", "note")):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ref["text"] if page == active_page else ref["muted_dim"])
            painter.drawEllipse(QRectF(x, y + index * (dot + gap), dot, dot))

    def _alarm_editor_frequency_value(self, editor: dict[str, object]) -> tuple[str, str, str]:
        unit = str(editor.get("frequency_unit") or self._alarm_unit_from_frequency(str(editor.get("frequency") or "每周"))).lower()
        if unit == "weekly":
            return "weekdays", self._alarm_recurrence_value_label(editor) or "1", "alarm-editor-open-weekdays"
        if unit == "monthly":
            return "month_day", self._alarm_recurrence_value_label(editor), "alarm-editor-open-month-day"
        if unit == "yearly":
            return "year_date", self._alarm_recurrence_value_label(editor), "alarm-editor-open-year-date"
        return "", "", ""

    def _alarm_editor_narrow_frequency_rect(self, layout: dict[str, QRectF], value_field: str) -> QRectF:
        frequency = QRectF(layout["main_frequency"])
        if value_field:
            return frequency
        slot_left = layout["main_time"].right() + self._u(16.0)
        slot_right = layout["main_note_dot"].left() - self._u(8.0)
        slot_w = max(frequency.width(), slot_right - slot_left)
        target_w = min(slot_w, max(frequency.width() * 1.24, self._u(132.0)))
        rect = QRectF(slot_left + (slot_w - target_w) / 2.0, frequency.top(), target_w, frequency.height())
        unit = str(self._time_alarm_editor_state().get("frequency_unit") or "")
        offset = self._alarm_editor_reflow_offset(unit)
        if abs(offset) > 0.001:
            rect.translate(offset * self._u(18.0), 0.0)
        return rect

    def _paint_time_alarm_editor_detail(self, painter: QPainter, rect: QRectF, editor: dict[str, object], color: QColor, theme: dict[str, QColor], ref: dict[str, QColor]):
        mode = str(editor.get("detail_mode") or "").strip().lower()
        confirm = QRectF(rect.right() - self._u(40.0), rect.top() + self._u(10.0), self._u(30.0), self._u(24.0))
        self.plugin_action_rects["alarm-editor-detail-confirm"] = QRectF(confirm.adjusted(-self._u(4.0), -self._u(4.0), self._u(4.0), self._u(4.0)))
        painter.setPen(QPen(ref["accent"], self._u(1.2)))
        painter.setBrush(QColor(ref["accent"].red(), ref["accent"].green(), ref["accent"].blue(), 34))
        painter.drawRoundedRect(confirm, self._u(8.0), self._u(8.0))
        painter.setPen(QPen(ref["accent"], self._u(2.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QPointF(confirm.left() + confirm.width() * 0.28, confirm.center().y()), QPointF(confirm.left() + confirm.width() * 0.44, confirm.bottom() - confirm.height() * 0.30))
        painter.drawLine(QPointF(confirm.left() + confirm.width() * 0.44, confirm.bottom() - confirm.height() * 0.30), QPointF(confirm.right() - confirm.width() * 0.24, confirm.top() + confirm.height() * 0.30))
        title_rect = QRectF(rect.left() + self._u(40.0), rect.top() + self._u(24.0), rect.width() - self._u(100.0), self._u(46.0))
        content = QRectF(rect.left() + self._u(40.0), title_rect.bottom() + self._u(18.0), rect.width() - self._u(80.0), max(self._u(54.0), rect.bottom() - title_rect.bottom() - self._u(32.0)))
        painter.setFont(self._font(30, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        title = {"weekly": "每周", "monthly": self._alarm_recurrence_label(editor), "yearly": self._alarm_recurrence_label(editor)}.get(mode, "频次")
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        if mode == "weekly":
            weekdays = set(self._alarm_weekdays_from_value(editor.get("weekdays", [])))
            gap = self._u(12.0)
            chip_w = min(self._u(48.0), max(self._u(34.0), (content.width() - gap * 6.0) / 7.0))
            x = content.left()
            y = content.top() + self._u(2.0)
            for day in range(1, 8):
                chip = QRectF(x + (day - 1) * (chip_w + gap), y, chip_w, self._u(58.0))
                selected = day in weekdays
                painter.setFont(self._font(34, QFont.Weight.Bold))
                painter.setPen(ref["accent"] if selected else QColor(118, 118, 118, 235))
                painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, str(day))
                self.plugin_action_rects[f"alarm-editor-toggle-weekday-{day}"] = QRectF(chip.adjusted(-self._u(2.0), -self._u(2.0), self._u(2.0), self._u(2.0)))
            return
        if mode == "monthly":
            field_w = min(self._u(142.0), max(self._u(104.0), content.width() * 0.34))
            field_h = self._u(74.0)
            field = QRectF(content.center().x() - field_w / 2.0 - self._u(18.0), content.top(), field_w, field_h)
            self._paint_alarm_detail_input_box(painter, field, "month_day", str(editor.get("month_day", 28)), ref, str(getattr(self, "plugin_alarm_editor_focus", "") or "") == "month_day")
            painter.setFont(self._font(30, QFont.Weight.Bold))
            painter.setPen(ref["text"])
            painter.drawText(QRectF(field.right() + self._u(18.0), field.top(), self._u(58.0), field.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "号")
            return
        if mode == "yearly":
            gap = self._u(26.0)
            field_w = min(self._u(96.0), max(self._u(80.0), (content.width() - gap - self._u(92.0)) / 2.0))
            field_h = self._u(74.0)
            total_w = field_w * 2.0 + gap + self._u(84.0)
            x = content.center().x() - total_w / 2.0
            y = content.top()
            month = QRectF(x, y, field_w, field_h)
            day = QRectF(month.right() + gap + self._u(42.0), y, field_w, field_h)
            focus = str(getattr(self, "plugin_alarm_editor_focus", "") or "")
            self._paint_alarm_detail_input_box(painter, month, "year_month", str(editor.get("year_month", 4)), ref, focus == "year_month")
            self._paint_alarm_detail_input_box(painter, day, "year_day", str(editor.get("year_day", 28)), ref, focus == "year_day")
            painter.setFont(self._font(30, QFont.Weight.Bold))
            painter.setPen(ref["text"])
            painter.drawText(QRectF(month.right() + self._u(12.0), month.top(), self._u(48.0), month.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "月")
            painter.drawText(QRectF(day.right() + self._u(12.0), day.top(), self._u(48.0), day.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "日")

    def _paint_time_alarm_editor_narrow(self, painter: QPainter, layout: dict[str, QRectF], editor: dict[str, object], color: QColor, theme: dict[str, QColor], ref: dict[str, QColor]):
        self._normalize_alarm_editor_recurrence(editor)
        focus = str(getattr(self, "plugin_alarm_editor_focus", "") or "")
        if str(editor.get("detail_mode") or "").strip():
            self._paint_time_alarm_editor_detail(painter, layout["shell"], editor, color, theme, ref)
            return

        page = str(editor.get("editor_page") or "main")
        self._paint_alarm_editor_page_dots(painter, layout["main_note_dot"], "note" if page == "note" else "main", ref)
        page_offset = self._alarm_editor_page_transition_offset("note" if page == "note" else "main")
        page_translated = abs(page_offset) > 0.001
        if page_translated:
            painter.save()
            painter.translate(0.0, page_offset * layout["shell"].height() * 0.18)
        if page == "note":
            note_text = str(editor.get("note") or "")
            self._paint_time_alarm_editor_box(painter, layout["note"], note_text or "备注...", not bool(note_text), ref, size=15, bold=True, focused=focus == "note", field="note")
            hint = str(getattr(self, "plugin_alarm_editor_note_hint", "") or "")
            if hint and time.monotonic() < float(getattr(self, "plugin_alarm_editor_note_hint_until", 0.0) or 0.0):
                self._paint_time_alarm_editor_label(painter, layout["note_label"], hint, ref)
            else:
                self._paint_time_alarm_editor_label(painter, layout["note_label"], "备注（最多10字）", ref)
            self.plugin_action_rects["alarm-editor-note"] = QRectF(layout["note"])
            self.plugin_action_rects["alarm-editor-focus-note"] = QRectF(layout["note"])
            self.plugin_action_rects["alarm-editor-page-main"] = QRectF(layout["note_main_dot"])
            if page_translated:
                painter.restore()
            return

        time_rect = layout["main_time"]
        hour_rect = QRectF(time_rect.left(), time_rect.top(), time_rect.width() * 0.46, time_rect.height())
        minute_rect = QRectF(time_rect.left() + time_rect.width() * 0.54, time_rect.top(), time_rect.width() * 0.46, time_rect.height())
        self.plugin_alarm_editor_rects["hour"] = hour_rect
        self.plugin_alarm_editor_rects["minute"] = minute_rect
        self.plugin_action_rects["alarm-editor-focus-hour"] = QRectF(hour_rect)
        self.plugin_action_rects["alarm-editor-focus-minute"] = QRectF(minute_rect)
        time_text = f"{int(editor.get('hour', 0)):02d}:{int(editor.get('minute', 0)):02d}"
        self._paint_time_alarm_editor_box(painter, time_rect, time_text, False, ref, size=24, bold=True, focused=focus in {"hour", "minute"})
        unit_label = dict(self._alarm_frequency_units()).get(str(editor.get("frequency_unit") or "weekly"), self._alarm_recurrence_label(editor))
        value_field, value_text, open_command = self._alarm_editor_frequency_value(editor)
        frequency_rect = self._alarm_editor_narrow_frequency_rect(layout, value_field)
        self._paint_alarm_picker_field(painter, frequency_rect, "frequency_unit", unit_label, "频次", theme, color)
        self.plugin_action_rects["alarm-editor-focus-frequency_unit"] = QRectF(frequency_rect)
        if value_field:
            open_rect = QRectF(layout["main_frequency_value"]) if not layout["main_frequency_value"].isNull() else QRectF(
                frequency_rect.center().x(),
                frequency_rect.top(),
                frequency_rect.width() * 0.5,
                frequency_rect.height(),
            )
            self.plugin_action_rects[open_command] = QRectF(open_rect)
        if value_field and not layout["main_frequency_value"].isNull():
            value_rect = layout["main_frequency_value"]
            self._paint_time_alarm_editor_box(painter, value_rect, value_text, False, ref, size=13, bold=True, focused=focus in {value_field, "weekdays"})
            if value_field == "year_date":
                self.plugin_alarm_editor_rects["year_month"] = QRectF(value_rect.left(), value_rect.top(), value_rect.width() * 0.48, value_rect.height())
                self.plugin_alarm_editor_rects["year_day"] = QRectF(value_rect.left() + value_rect.width() * 0.52, value_rect.top(), value_rect.width() * 0.48, value_rect.height())
            else:
                self.plugin_alarm_editor_rects[value_field] = QRectF(value_rect)
        self._paint_time_alarm_editor_label(painter, layout["time_label"], "时间", ref)
        frequency_label = QRectF(frequency_rect.left(), layout["frequency_label"].top(), frequency_rect.width(), layout["frequency_label"].height())
        if value_field and not layout["main_frequency_value"].isNull():
            frequency_label = QRectF(frequency_rect.left(), layout["frequency_label"].top(), layout["main_frequency_value"].right() - frequency_rect.left(), layout["frequency_label"].height())
        self._paint_time_alarm_editor_label(painter, frequency_label, "频次", ref)
        self.plugin_action_rects["alarm-editor-page-note"] = QRectF(layout["main_note_dot"])
        if page_translated:
            painter.restore()

    def _paint_time_alarm_editor(self, painter: QPainter, rect: QRectF, model: dict[str, object], color: QColor, theme: dict[str, QColor]):
        ref = self._time_reference_palette(theme, color)
        self.plugin_alarm_editor_rects = {}
        editor = model["editor"]
        self._normalize_alarm_editor_recurrence(editor)
        layout = self._time_alarm_editor_layout(rect)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(35, 36, 38, 248))
        painter.drawRoundedRect(layout["shell"], self._u(14.0), self._u(14.0))
        painter.setPen(QPen(ref["divider"], self._u(0.8)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(layout["shell"].adjusted(self._u(0.8), self._u(0.8), -self._u(0.8), -self._u(0.8)), self._u(13.0), self._u(13.0))
        if str(editor.get("detail_mode") or "").strip():
            self._paint_time_alarm_editor_detail(painter, layout["shell"], editor, color, theme, ref)
            return
        painter.setFont(self._font(13, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        painter.drawText(layout["title"], Qt.AlignmentFlag.AlignCenter, "闹钟设置")

        if layout.get("mode") == "narrow":
            self._paint_time_alarm_editor_narrow(painter, layout, editor, color, theme, ref)
            self.plugin_action_rects["alarm-editor-save"] = QRectF(layout["save"])
            self.plugin_action_rects["alarm-editor-cancel"] = QRectF(layout["cancel"])
            self._paint_time_alarm_editor_action(painter, layout["cancel"], "取消", ref, False)
            self._paint_time_alarm_editor_action(painter, layout["save"], "确定", ref, True)
            return

        time_rect = layout["time"]
        hour_rect = QRectF(time_rect.left(), time_rect.top(), time_rect.width() * 0.46, time_rect.height())
        minute_rect = QRectF(time_rect.left() + time_rect.width() * 0.54, time_rect.top(), time_rect.width() * 0.46, time_rect.height())
        self.plugin_alarm_editor_rects["hour"] = hour_rect
        self.plugin_alarm_editor_rects["minute"] = minute_rect

        time_text = f"{int(editor['hour']):02d}:{int(editor['minute']):02d}"
        focus = str(getattr(self, "plugin_alarm_editor_focus", "") or "")
        self._paint_time_alarm_editor_box(painter, time_rect, time_text, False, ref, size=24, bold=True, focused=focus in {"hour", "minute"})
        note_text = str(editor.get("note") or "")
        self._paint_time_alarm_editor_box(painter, layout["note"], note_text or "备注...", not bool(note_text), ref, size=12, bold=True, focused=focus == "note", field="note")
        unit_label = dict(self._alarm_frequency_units()).get(
            str(editor.get("frequency_unit") or self._alarm_unit_from_frequency(str(editor.get("frequency") or "每周"))),
            self._alarm_recurrence_label(editor),
        )
        value_field, value_text, open_command = self._alarm_editor_frequency_value(editor)
        frequency_rect = QRectF(layout["frequency"])
        value_rect = QRectF(layout["weekdays"])
        self._paint_alarm_picker_field(painter, frequency_rect, "frequency_unit", unit_label, "频次", theme, color)
        self.plugin_alarm_editor_rects["frequency"] = QRectF(frequency_rect)
        if value_field:
            self._paint_time_alarm_editor_box(painter, value_rect, value_text, False, ref, size=12, bold=True, focused=focus in {value_field, "weekdays", "year_month", "year_day"})
            if value_field == "year_date":
                self.plugin_alarm_editor_rects["year_month"] = QRectF(value_rect.left(), value_rect.top(), value_rect.width() * 0.48, value_rect.height())
                self.plugin_alarm_editor_rects["year_day"] = QRectF(value_rect.left() + value_rect.width() * 0.52, value_rect.top(), value_rect.width() * 0.48, value_rect.height())
                self.plugin_action_rects["alarm-editor-focus-year_month"] = QRectF(self.plugin_alarm_editor_rects["year_month"])
                self.plugin_action_rects["alarm-editor-focus-year_day"] = QRectF(self.plugin_alarm_editor_rects["year_day"])
            else:
                self.plugin_alarm_editor_rects[value_field] = QRectF(value_rect)
                self.plugin_action_rects[f"alarm-editor-focus-{value_field}"] = QRectF(value_rect)
            self.plugin_alarm_editor_rects["weekdays"] = QRectF(value_rect)
        else:
            self._paint_time_alarm_editor_box(painter, value_rect, "默认", False, ref, size=12, bold=True, focused=False)
            self.plugin_alarm_editor_rects["weekdays"] = QRectF(value_rect)

        self._paint_time_alarm_editor_label(painter, layout["time_label"], "时间", ref)
        hint = str(getattr(self, "plugin_alarm_editor_note_hint", "") or "")
        if hint and time.monotonic() < float(getattr(self, "plugin_alarm_editor_note_hint_until", 0.0) or 0.0):
            self._paint_time_alarm_editor_label(painter, layout["note_label"], hint, ref)
        else:
            self._paint_time_alarm_editor_label(painter, layout["note_label"], "备注", ref)
        self._paint_time_alarm_editor_label(painter, layout["frequency_label"], "频次", ref)
        self._paint_time_alarm_editor_label(painter, layout["weekdays_label"], "日期", ref)

        self.plugin_action_rects["alarm-editor-note"] = QRectF(layout["note"])
        self.plugin_action_rects["alarm-editor-focus-hour"] = QRectF(hour_rect)
        self.plugin_action_rects["alarm-editor-focus-minute"] = QRectF(minute_rect)
        self.plugin_action_rects["alarm-editor-focus-frequency_unit"] = QRectF(frequency_rect)
        self.plugin_action_rects["alarm-editor-focus-frequency"] = QRectF(frequency_rect)
        self.plugin_action_rects["alarm-editor-focus-weekdays"] = QRectF(value_rect)
        if open_command:
            self.plugin_action_rects[open_command] = QRectF(value_rect)
        self.plugin_action_rects["alarm-editor-save"] = QRectF(layout["save"])
        self.plugin_action_rects["alarm-editor-cancel"] = QRectF(layout["cancel"])
        self._paint_time_alarm_editor_action(painter, layout["cancel"], "取消", ref, False)
        self._paint_time_alarm_editor_action(painter, layout["save"], "确定", ref, True)

    def _paint_time_module_by_name(self, painter: QPainter, module: str, rect: QRectF, panel: dict[str, object], color: QColor, theme: dict[str, QColor]):
        if module == "alarm":
            self._paint_time_alarm_module(painter, rect, panel, color, theme)
        else:
            self._paint_time_countdown_module(painter, rect, panel, color, theme)

    def _paint_time_plugin_panel(self, painter: QPainter, rect: QRectF, model: dict[str, object], color: QColor, theme: dict[str, QColor]):
        self.plugin_panel_last_width = float(rect.width())
        panel = self._time_plugin_panel_model(model)
        if panel["mode"] == "alarm_editor":
            self._paint_time_alarm_editor(painter, rect, panel, color, theme)
            return
        ref = self._time_reference_palette(theme, color)
        layout = self._time_plugin_panel_layout(rect)
        panel_for_paint = dict(panel)
        panel_for_paint["time_layout_mode"] = layout["time_layout_mode"]
        title = layout["time_title"]
        painter.setFont(self._font(10, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        painter.drawText(title, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "TIME")
        if layout["time_layout_mode"] == "double":
            visible = str(layout.get("time_visible_module") or "countdown")
            self._paint_time_dashboard_clock(painter, layout["time_clock"], panel, color, theme)
            slot_rect = QRectF(layout["time_alarm"] if visible == "alarm" else layout["time_countdown"])
            transition = self._time_module_transition_offsets(visible)
            if transition and not slot_rect.isNull():
                from_module, from_offset, to_offset = transition
                painter.save()
                painter.setClipRect(slot_rect)
                action_snapshot = dict(self.plugin_action_rects)
                if from_module in self._time_panel_modules():
                    painter.save()
                    painter.translate(0.0, from_offset * slot_rect.height())
                    self._paint_time_module_by_name(painter, from_module, slot_rect, panel_for_paint, color, theme)
                    painter.restore()
                self.plugin_action_rects = action_snapshot
                painter.save()
                painter.translate(0.0, to_offset * slot_rect.height())
                self._paint_time_module_by_name(painter, visible, slot_rect, panel_for_paint, color, theme)
                painter.restore()
                painter.restore()
            else:
                self._paint_time_module_by_name(painter, visible, slot_rect, panel_for_paint, color, theme)
            self._paint_time_page_dots(painter, layout["time_pager"], visible, theme, color)
            return
        self._paint_time_dashboard_clock(painter, layout["time_clock"], panel, color, theme)
        self._paint_time_countdown_module(painter, layout["time_countdown"], panel_for_paint, color, theme)
        self._paint_time_alarm_module(painter, layout["time_alarm"], panel, color, theme)

    def _paint_structured_plugin_panel(self, painter: QPainter, rect: QRectF, model: dict[str, object], color: QColor, theme: dict[str, QColor]):
        label = str(model.get("label", ""))
        if label.strip().upper() == "TIME":
            self._paint_time_plugin_panel(painter, rect, model, color, theme)
            return
        detail = str(model.get("detail", ""))
        summary = str(model.get("summary", ""))
        status = str(model.get("status", "normal"))
        alert_title = str(model.get("alert_title", ""))
        alert_body = str(model.get("alert_body", ""))
        progress = float(model.get("progress", 0.0) or 0.0)
        layout = self._plugin_panel_layout(rect)
        title, _lines = self._plugin_function_lines(label, detail, summary, status, alert_title, alert_body)
        nav = layout["nav"]
        painter.setFont(self._font(10, QFont.Weight.Bold))
        painter.setPen(theme["text"])
        painter.drawText(nav.adjusted(self._u(4.0), 0.0, 0.0, 0.0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        status_text = {"running": "运行中", "done": "完成", "alarm": "提醒", "loading": "刷新中", "soon": "即将", "due": "现在", "rain": "降雨", "severe": "预警"}.get(status.lower(), "就绪")
        painter.setFont(self._font(8, QFont.Weight.DemiBold))
        painter.setPen(theme["muted"])
        painter.drawText(nav.adjusted(0.0, 0.0, -self._u(4.0), 0.0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, status_text)

        sections = self._plugin_panel_sections(model)
        module_rects = (layout["time_clock"], layout["time_countdown"], layout["time_alarm"])
        action = self._plugin_action_model(label, detail, summary, status)
        for index, section in enumerate(sections[:3]):
            module = module_rects[index]
            reserve_action = index == 2
            if str(section["id"]) == "clock":
                self._paint_time_clock_module(painter, module, section, theme, color, progress, status)
            else:
                self._paint_plugin_panel_module(
                    painter,
                    module,
                    section["title"],
                    section["body"],
                    theme,
                    color,
                    emphasized=index == 0 and str(label).upper() != "TIME",
                    reserve_action=reserve_action,
                )
            if reserve_action:
                self._paint_plugin_panel_action_button(painter, module, action, color)
        self._paint_plugin_panel_reminders(painter, layout["reminders"])

    def _paint_plugin_compact_cell(self, painter: QPainter, rect: QRectF, label: str, detail: str, summary: str, color: QColor, active: bool, status: str = "normal", progress: float = 0.0):
        if str(label or "").strip().upper() == "TIME":
            self._paint_time_compact_cell(painter, rect, detail, summary, color, active, status, progress)
            return
        theme = self._plugin_theme_colors()
        color = self._pure_plugin_color(self._plugin_display_color(label, color))
        radius = min(self._u(15), rect.height() / 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme["cell_active"] if active else theme["cell"])
        painter.drawRoundedRect(rect, radius, radius)

        accent_size = self._u(20)
        accent = QRectF(rect.left() + self._u(8), rect.center().y() - accent_size / 2.0, accent_size, accent_size)
        label_key = str(label or "").strip().upper()
        status_key = str(status or "").strip().lower()
        is_time = label_key == "TIME"
        is_time_done = is_time and status_key in {"done", "due", "alarm"}
        accent_fill = QColor(color.red(), color.green(), color.blue(), 238 if active else 218)
        if is_time:
            accent_fill = QColor(255, 184, 0, 238) if is_time_done else QColor(30, 26, 18, 232)
        painter.setBrush(accent_fill)
        painter.drawRoundedRect(accent, self._u(6), self._u(6))
        painted_icon = False
        if is_time:
            painted_icon = self._paint_svg_icon(painter, self._time_reminder_icon_path(status, self._time_reminder_kind(status, summary)), accent.adjusted(self._u(3.0), self._u(3.0), -self._u(3.0), -self._u(3.0)))
        if not painted_icon:
            painter.setPen(QColor(39, 25, 0, 245) if is_time_done else QColor(255, 255, 255, 242))
            painter.setFont(self._font(7, QFont.Weight.Bold))
            painter.drawText(accent, Qt.AlignmentFlag.AlignCenter, label[:1])
        if not is_time_done:
            self._paint_plugin_progress_ring(painter, accent.adjusted(-self._u(3.0), -self._u(3.0), self._u(3.0), self._u(3.0)), color, progress, status)

        painter.setPen(QPen(theme["stroke"], self._u(1.0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = self._u(0.8)
        painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), max(1.0, radius - inset), max(1.0, radius - inset))
        painter.setPen(QPen(theme["inner_stroke"], self._u(0.8)))
        painter.drawRoundedRect(rect.adjusted(inset * 1.7, inset * 1.7, -inset * 1.7, -inset * 1.7), max(1.0, radius - inset * 1.7), max(1.0, radius - inset * 1.7))
        detail_rect = QRectF(accent.right() + self._u(8), rect.top(), rect.right() - accent.right() - self._u(16), rect.height())
        detail_color = QColor(theme["text"])
        detail_color.setAlpha(232 if active else 188)
        self._paint_scrolling_text(painter, detail_rect, self._plugin_compact_value(label, detail, summary, status), 10, detail_color, False)
        self._paint_plugin_status_dot(painter, rect, status)

    def _paint_plugin_progress_ring(self, painter: QPainter, rect: QRectF, color: QColor, progress: float, status: str = "normal"):
        status = str(status or "").lower()
        if status == "loading":
            progress = (time.monotonic() * 0.82) % 1.0
        elif status == "error":
            progress = 1.0
        if status not in {"running", "done", "due", "soon", "loading", "error", "alarm", "severe", "rain"} and float(progress or 0.0) <= 0.0:
            return
        progress = 1.0 if status in {"done", "due", "alarm", "severe"} else max(0.0, min(1.0, float(progress or 0.0)))
        style = self._plugin_ring_style(status)
        base = QColor(255, 255, 255, 54)
        accent = QColor(color.red(), color.green(), color.blue(), 245)
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(base, style["track_width"], Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(rect)
        painter.setPen(QPen(accent, style["width"], Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 90 * 16, int(-360 * 16 * progress))
        painter.restore()

    def _paint_plugin_panel_reminders(self, painter: QPainter, rect: QRectF):
        reminders = self._plugin_panel_reminder_models(3)
        if not reminders:
            theme = self._plugin_theme_colors()
            idle = QRectF(rect.left(), rect.top(), min(rect.width(), self._u(132.0)), rect.height())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme["cell"])
            painter.drawRoundedRect(idle, self._u(12.0), self._u(12.0))
            muted = QColor(theme["muted"])
            muted.setAlpha(160)
            self._paint_scrolling_text(painter, idle.adjusted(self._u(10.0), 0.0, -self._u(10.0), 0.0), "暂无提醒", 9, muted, False)
            return

        gap = self._u(7.0)
        count = len(reminders)
        chip_w = max(self._u(78.0), min(self._u(132.0), (rect.width() - gap * max(0, count - 1)) / max(1, count)))
        theme = self._plugin_theme_colors()
        for i, item in enumerate(reminders):
            chip = QRectF(rect.left() + i * (chip_w + gap), rect.top(), chip_w, rect.height())
            label = str(item["label"])
            plugin_id = str(item.get("plugin_id") or "")
            color = self._pure_plugin_color(self._plugin_display_color(label, item["color"]))
            status = str(item["status"] or "normal")
            status_key = status.lower()
            is_time = label.strip().upper() == "TIME" or plugin_id.strip().casefold() == "time"
            is_time_done = is_time and status_key in {"done", "due", "alarm"}
            text_color = QColor(39, 25, 0, 245) if is_time_done else theme["text"]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 184, 0, 240) if is_time_done else (theme["cell_active"] if i == 0 else theme["cell"]))
            painter.drawRoundedRect(chip, self._u(12.0), self._u(12.0))
            icon_size = self._u(18.0)
            icon_rect = QRectF(chip.left() + self._u(7.0), chip.center().y() - icon_size / 2.0, icon_size, icon_size)
            painter.setBrush(QColor(30, 26, 18, 226) if is_time and not is_time_done else QColor(color.red(), color.green(), color.blue(), 226 if not is_time_done else 0))
            painter.drawRoundedRect(icon_rect, self._u(6.0), self._u(6.0))
            progress = float(item.get("progress", 0.0) or 0.0)
            if not is_time_done:
                self._paint_plugin_progress_ring(painter, icon_rect.adjusted(-self._u(2.6), -self._u(2.6), self._u(2.6), self._u(2.6)), color, progress, status)
            painted_icon = False
            if is_time:
                kind = self._time_reminder_kind(status, f"{item.get('body')} {item.get('detail')}")
                painted_icon = self._paint_svg_icon(painter, self._time_reminder_icon_path(status, kind), icon_rect.adjusted(self._u(2.8), self._u(2.8), -self._u(2.8), -self._u(2.8)))
            if not painted_icon:
                painter.setFont(QFont("Segoe UI Symbol", self._font_size(8), QFont.Weight.Bold))
                painter.setPen(text_color if is_time_done else QColor(255, 255, 255, 244))
                painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, str(item["icon"] or item["label"])[:1])
            text_rect = QRectF(icon_rect.right() + self._u(6.0), chip.top(), chip.right() - icon_rect.right() - self._u(10.0), chip.height())
            self._paint_scrolling_text(painter, text_rect, str(item["body"] or item["detail"] or item["label"]), 8, text_color, False)

    def _paint_time_compact_cell(self, painter: QPainter, rect: QRectF, detail: str, summary: str, color: QColor, active: bool, status: str = "normal", progress: float = 0.0):
        theme = self._plugin_theme_colors()
        ref = self._time_reference_palette(theme, color)
        radius = min(self._u(16.0), rect.height() / 2.0)
        painter.setPen(QPen(QColor(170, 170, 170, 150 if active else 92), self._u(1.1)))
        painter.setBrush(QColor(52, 52, 52, 238 if active else 214))
        painter.drawRoundedRect(rect, radius, radius)
        segments = self._time_compact_segments(detail, summary, status)
        if not segments:
            return
        layout = self._time_compact_cell_layout(rect, segments)
        if not layout:
            return
        time_text = str(layout[0].get("text") or "")
        time_rect = QRectF(layout[0]["rect"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(26, 26, 26, 232))
        painter.drawRoundedRect(time_rect, time_rect.height() / 2.0, time_rect.height() / 2.0)
        painter.setFont(self._font(12 if len(time_text) <= 5 else 9, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        painter.drawText(time_rect.adjusted(self._u(4.0), 0.0, -self._u(4.0), 0.0), Qt.AlignmentFlag.AlignCenter, time_text)
        for segment in layout[1:]:
            chip = QRectF(segment["rect"])
            seg_status = str(segment.get("status") or status).lower()
            done = seg_status in {"done", "due", "alarm-done"} or str(segment.get("text") or "") == "\u65f6\u95f4\u5230"
            fill = ref["accent"] if done else QColor(24, 24, 24, 235)
            text_color = QColor(34, 24, 0, 245) if done else ref["accent"]
            painter.setPen(QPen(ref["accent"] if not done else ref["accent"], self._u(1.0)))
            painter.setBrush(fill)
            painter.drawRoundedRect(chip, chip.height() / 2.0, chip.height() / 2.0)
            icon_size = min(self._u(20.0), chip.height() - self._u(5.0))
            segment_kind = str(segment.get("kind") or "countdown")
            if bool(segment.get("icon_only")):
                icon_size = min(chip.height() - self._u(7.0), self._u(24.0))
                icon_rect = QRectF(chip.center().x() - icon_size / 2.0, chip.center().y() - icon_size / 2.0, icon_size, icon_size)
                self._paint_time_symbol(painter, icon_rect, segment_kind, text_color, self._u(1.8))
                continue
            icon_rect = QRectF(chip.left() + self._u(6.0), chip.center().y() - icon_size / 2.0, icon_size, icon_size)
            self._paint_time_symbol(painter, icon_rect, segment_kind, text_color, self._u(1.6))
            text_rect = QRectF(icon_rect.right() + self._u(4.0), chip.top(), chip.right() - icon_rect.right() - self._u(7.0), chip.height())
            segment_text = str(segment.get("text") or "")
            display_text = self._time_alarm_note_display_text(segment_text) if segment_kind == "alarm" else self._time_reminder_display_text(segment_text, 10)
            if segment_kind == "alarm" and self._time_alarm_note_should_scroll(segment_text, text_rect, 10, True, 5):
                self._paint_scrolling_text(painter, text_rect, display_text, 10, text_color, True)
            else:
                self._paint_static_elided_text(painter, text_rect, display_text, 10, text_color, True)

    def _paint_plugin_function_panel(self, painter: QPainter, rect: QRectF, model: dict[str, object]):
        if not model:
            return
        index = int(model.get("index", 0))
        label = str(model.get("label", ""))
        detail = str(model.get("detail", ""))
        summary = str(model.get("summary", ""))
        color = model.get("color")
        icon = str(model.get("icon", ""))
        status = str(model.get("status", "normal"))
        alert_title = str(model.get("alert_title", ""))
        alert_body = str(model.get("alert_body", ""))
        progress = float(model.get("progress", 0.0) or 0.0)
        theme = self._plugin_theme_colors()
        color = self._pure_plugin_color(self._plugin_display_color(label, color))

        layout = self._plugin_panel_layout(rect)
        if str(label).upper() in {"TIME", "WEATHER", "AGENDA"}:
            self._paint_structured_plugin_panel(painter, rect, model, color, theme)
            return

        body = layout["body"]
        title, lines = self._plugin_function_lines(label, detail, summary, status, alert_title, alert_body)
        action = self._plugin_action_model(label, detail, summary, status)
        accent = layout["icon"]
        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 3.0)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), int(172 + pulse * 46)))
        painter.drawRoundedRect(accent, self._u(22.0), self._u(22.0))
        self._paint_plugin_progress_ring(painter, accent.adjusted(self._u(5.0), self._u(5.0), -self._u(5.0), -self._u(5.0)), color, progress, status)
        if str(label).upper() == "TIME":
            center = accent.center()
            painter.setPen(QPen(QColor(255, 255, 255, 236), self._u(3.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(center, QPointF(center.x() + accent.width() * 0.24, center.y() + accent.height() * 0.16))
            painter.setPen(QPen(QColor(255, 255, 255, 215), self._u(2.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(center, QPointF(center.x() - accent.width() * 0.18, center.y() - accent.height() * 0.12))
        else:
            painter.setFont(QFont("Segoe UI Symbol", self._font_size(22), QFont.Weight.Bold))
            painter.setPen(QColor(255, 255, 255, 244))
            painter.drawText(accent, Qt.AlignmentFlag.AlignCenter, icon or str(label)[:1])

        text_rect = layout["text"]
        text_left = text_rect.left()
        title_rect = QRectF(text_left, text_rect.top(), text_rect.width(), self._u(22.0))
        painter.setFont(self._font(12, QFont.Weight.Bold))
        painter.setPen(theme["muted"])
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        if lines:
            self._paint_scrolling_text(painter, QRectF(text_left, title_rect.bottom() + self._u(3.0), text_rect.width(), self._u(30.0)), lines[0], 18, theme["text"], False)
        if len(lines) > 1:
            self._paint_scrolling_text(painter, QRectF(text_left, title_rect.bottom() + self._u(34.0), text_rect.width(), self._u(18.0)), lines[1], 10, theme["muted"], False)
        if len(lines) > 2:
            third_color = QColor(theme["muted"])
            third_color.setAlpha(170)
            self._paint_scrolling_text(painter, QRectF(text_left, title_rect.bottom() + self._u(52.0), text_rect.width(), self._u(16.0)), lines[2], 9, third_color, False)

        action_rect = layout["action"]
        command = str(action.get("command", ""))
        if command:
            self.plugin_action_rects[command] = QRectF(action_rect)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 218))
        painter.drawRoundedRect(action_rect, self._u(9.0), self._u(9.0))
        painter.setFont(self._font(9, QFont.Weight.DemiBold))
        painter.setPen(QColor(255, 255, 255, 244))
        painter.drawText(action_rect.adjusted(self._u(8.0), 0.0, -self._u(8.0), 0.0), Qt.AlignmentFlag.AlignCenter, str(action.get("label") or "操作"))
        self._paint_plugin_panel_reminders(painter, layout["reminders"])

    def _paint_plugin_focus_cell(
        self,
        painter: QPainter,
        rect: QRectF,
        label: str,
        detail: str,
        color: QColor,
        icon: str,
        focus_weight: float,
        icon_mode: bool,
        status: str = "normal",
        summary: str = "",
        progress: float = 0.0,
    ):
        if str(label or "").strip().upper() == "TIME":
            self._paint_time_compact_cell(painter, rect, detail, summary or detail, color, True, status, progress)
            return
        theme = self._plugin_theme_colors()
        color = self._pure_plugin_color(self._plugin_display_color(label, color))
        if icon_mode or rect.width() < self._u(138):
            circle_size = min(self._u(32.0), max(self._u(28.0), rect.height() - self._u(4.0)))
            circle = QRectF(rect.center().x() - circle_size / 2.0, rect.center().y() - circle_size / 2.0, circle_size, circle_size)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme["cell_active"])
            painter.drawRoundedRect(circle, self._u(12), self._u(12))
            inner = circle.adjusted(self._u(4), self._u(4), -self._u(4), -self._u(4))
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 232))
            painter.drawRoundedRect(inner, self._u(8), self._u(8))
            painter.setFont(QFont("Segoe UI Symbol", self._font_size(13), QFont.Weight.Bold))
            painter.setPen(QColor(255, 255, 255, 242))
            painter.drawText(inner, Qt.AlignmentFlag.AlignCenter, icon)
            self._paint_plugin_status_dot(painter, circle, status)
            return

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._mix_color(theme["cell"], theme["cell_active"], focus_weight))
        radius = min(self._u(16), rect.height() / 2)
        painter.drawRoundedRect(rect, radius, radius)
        stroke = self._u(1.0)
        painter.setPen(QPen(theme["stroke"], stroke))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = self._u(0.8)
        painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), max(1.0, radius - inset), max(1.0, radius - inset))
        painter.setPen(QPen(theme["inner_stroke"], self._u(0.8)))
        painter.drawRoundedRect(rect.adjusted(inset * 1.8, inset * 1.8, -inset * 1.8, -inset * 1.8), max(1.0, radius - inset * 1.8), max(1.0, radius - inset * 1.8))

        icon_size = self._u(26)
        icon_box = QRectF(rect.left() + self._u(11), rect.center().y() - icon_size / 2.0, icon_size, icon_size)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 212))
        painter.drawRoundedRect(icon_box, self._u(9), self._u(9))
        painter.setFont(QFont("Segoe UI Symbol", self._font_size(11), QFont.Weight.Bold))
        painter.setPen(QColor(255, 255, 255, 242))
        painter.drawText(icon_box, Qt.AlignmentFlag.AlignCenter, icon)

        label_rect = QRectF(icon_box.right() + self._u(9), rect.top(), self._u(46), rect.height())
        painter.setFont(self._font(8, QFont.Weight.Bold))
        painter.setPen(theme["text"])
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
        detail_rect = QRectF(label_rect.right() + self._u(5), rect.top(), rect.right() - label_rect.right() - self._u(16), rect.height())
        self._paint_scrolling_text(painter, detail_rect, detail, 10, theme["muted"], False)
        self._paint_plugin_status_dot(painter, rect, status)

    def _paint_time_plugin_alert(
        self,
        painter: QPainter,
        rect: QRectF,
        label: str,
        detail: str,
        summary: str,
        color: QColor,
        status: str,
        alert_title: str,
        alert_body: str,
        progress: float,
        reminder_kind: str = "",
    ):
        ref = self._time_reference_palette(self._plugin_theme_colors(), color)
        status_key = str(status or "").strip().lower()
        done = status_key in {"done", "due", "alarm"}
        kind = str(reminder_kind or "").strip().lower()
        if kind not in {"alarm", "countdown"}:
            kind = self._time_reminder_kind(status, f"{summary} {alert_title} {alert_body}")
        alert_note = self._time_alarm_alert_text(alert_body)
        if status_key in {"done", "due"} and kind == "countdown" and alert_note != "闹钟" and not self._time_text_looks_like_countdown(alert_body):
            kind = "alarm"
        radius = rect.height() / 2.0
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ref["accent"] if done else QColor(21, 21, 20, 244))
        painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(QPen(QColor(46, 46, 44, 210) if not done else QColor(255, 184, 0, 245), self._u(1.0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(self._u(0.8), self._u(0.8), -self._u(0.8), -self._u(0.8)), radius - self._u(0.8), radius - self._u(0.8))
        text_color = QColor(35, 24, 0, 245) if done else ref["accent"]
        icon_size = min(rect.height() - self._u(5.0), max(self._u(30.0), rect.height() * 0.84))
        icon_rect = QRectF(rect.left() + self._u(8.0), rect.center().y() - icon_size / 2.0, icon_size, icon_size)
        use_ring = self._plugin_alert_uses_ring(rect)
        if done:
            if use_ring:
                icon_color = QColor(28, 21, 0, 248)
                icon_inner = rect.adjusted(self._u(8.0), self._u(8.0), -self._u(8.0), -self._u(8.0))
                self._paint_time_symbol(painter, icon_inner, kind, icon_color, self._u(2.2))
                painter.restore()
                return
            body_source = self._time_alarm_alert_text_from_parts(alert_body, alert_title, summary)
            if kind == "alarm" and body_source:
                body_text = self._time_alarm_note_display_text(body_source)
            else:
                body_source = "\u65f6\u95f4\u5230"
                body_text = "\u65f6\u95f4\u5230"
            self._paint_time_symbol(painter, icon_rect.adjusted(self._u(3.6), self._u(3.6), -self._u(3.6), -self._u(3.6)), kind, text_color, self._u(2.1))
            text_left = icon_rect.right() + self._u(8.0)
            text_rect = QRectF(text_left, rect.top(), max(self._u(20.0), rect.right() - text_left - self._u(12.0)), rect.height())
            self._paint_static_elided_text(painter, text_rect, body_text, 20, text_color, True, Qt.AlignmentFlag.AlignLeft)
            painter.restore()
            return

        if use_ring:
            icon_inner = rect.adjusted(self._u(8.0), self._u(8.0), -self._u(8.0), -self._u(8.0))
            self._paint_time_symbol(painter, icon_inner, kind, ref["accent"], self._u(2.1))
            self._paint_plugin_progress_ring(painter, rect.adjusted(self._u(4.0), self._u(4.0), -self._u(4.0), -self._u(4.0)), ref["accent"], progress, status)
            painter.restore()
            return

        painter.setPen(QPen(ref["accent"], self._u(2.0)))
        painter.setBrush(QColor(255, 184, 0, 18) if kind == "alarm" else Qt.BrushStyle.NoBrush)
        if kind == "alarm":
            painter.drawEllipse(icon_rect)
        self._paint_time_symbol(painter, icon_rect.adjusted(self._u(3.6), self._u(3.6), -self._u(3.6), -self._u(3.6)), kind, ref["accent"], self._u(2.1))
        if use_ring:
            self._paint_plugin_progress_ring(painter, icon_rect.adjusted(-self._u(2.4), -self._u(2.4), self._u(2.4), self._u(2.4)), ref["accent"], progress, status)

        text_left = icon_rect.right() + self._u(8.0)
        text_rect = QRectF(text_left, rect.top(), max(self._u(20.0), rect.right() - text_left - self._u(12.0)), rect.height())
        value = self._strip_timer_prefix(str(alert_body or summary or detail)) or self._plugin_compact_value(label, detail, summary, status)
        if kind == "countdown":
            self._paint_static_elided_text(painter, text_rect, self._time_reminder_display_text(value, 8), 20, text_color, True, Qt.AlignmentFlag.AlignLeft)
        else:
            note_source = self._time_alarm_alert_text_from_parts(alert_body, alert_title)
            note = self._time_alarm_note_display_text(note_source)
            self._paint_static_elided_text(painter, text_rect, note, 20, text_color, True, Qt.AlignmentFlag.AlignLeft)
        painter.restore()

    def _paint_plugin_alert(self, painter: QPainter, rect: QRectF, key: str):
        plugin_id = key.split(":", 1)[-1] if ":" in key else ""
        alert = None
        if hasattr(self, "persistent_plugin"):
            alert = next((item for item in self._active_plugin_alerts(4, include_expanded=True) if str(item[6] or "").strip().casefold() == str(plugin_id or "").strip().casefold()), None)
        if alert is not None:
            index, label, detail, summary, color, icon, alert_plugin_id, status, alert_title, alert_body, _alert_priority = alert
            progress = 0.0
            if 0 <= int(index) < len(self.plugin_samples):
                _label, _detail, _summary, _color, _icon, _source_id, _status, _title, _body, _priority, progress = self._plugin_tuple(self.plugin_samples[int(index)])
        else:
            index = self._plugin_index_for_id(plugin_id)
            if index < 0:
                return
            label, detail, summary, color, icon, alert_plugin_id, status, alert_title, alert_body, _alert_priority, progress = self._plugin_tuple(self.plugin_samples[index])
        theme = self._plugin_theme_colors()
        color = self._pure_plugin_color(self._plugin_display_color(label, color))
        label_key = str(label or "").strip().upper()
        is_time = label_key == "TIME" or str(plugin_id or "").strip().casefold() == "time"
        if is_time:
            time_kind = self._time_alert_kind_from_plugin_id(str(plugin_id))
            if time_kind != "alarm":
                time_kind = self._time_alert_kind_from_plugin_id(str(alert_plugin_id))
            self._paint_time_plugin_alert(painter, rect, label, detail, summary, color, status, alert_title, alert_body, progress, time_kind)
            return
        icon_only = rect.width() <= rect.height() * 1.28
        radius = rect.height() / 2.0 if icon_only else min(self._u(17.0), rect.height() / 2.0)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        text_color = theme["text"]
        painter.setBrush(theme["alert"])
        painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(QPen(theme["stroke"], self._u(1.0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(self._u(0.8), self._u(0.8), -self._u(0.8), -self._u(0.8)), radius - self._u(0.8), radius - self._u(0.8))

        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 4.0)
        icon_size = self._u(28.0)
        icon_rect = QRectF(rect.left() + self._u(8.0), rect.center().y() - icon_size / 2.0, icon_size, icon_size)
        painter.setPen(Qt.PenStyle.NoPen)
        icon_fill = QColor(color.red(), color.green(), color.blue(), int(214 + pulse * 32))
        painter.setBrush(icon_fill)
        painter.drawRoundedRect(icon_rect, self._u(10.0), self._u(10.0))
        glow_rect = icon_rect.adjusted(-self._u(4.0), -self._u(4.0), self._u(4.0), self._u(4.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        glow_color = QColor(color.red(), color.green(), color.blue(), int(44 + pulse * 48))
        painter.setPen(QPen(glow_color, self._u(1.2), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawRoundedRect(glow_rect, self._u(12.0), self._u(12.0))
        if self._plugin_alert_uses_ring(rect):
            self._paint_plugin_progress_ring(painter, icon_rect.adjusted(-self._u(3.0), -self._u(3.0), self._u(3.0), self._u(3.0)), color, progress, status)
        painter.setFont(QFont("Segoe UI Symbol", self._font_size(11), QFont.Weight.Bold))
        painter.setPen(QColor(255, 255, 255, 245))
        painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, icon or label[:1])
        if icon_only:
            self._paint_plugin_status_dot(painter, rect, status)
            painter.restore()
            return

        value = self._plugin_alert_display_value(label, detail, summary, status, alert_title, alert_body)
        text_left = icon_rect.right() + self._u(8.0)
        value_rect = QRectF(text_left, rect.top(), rect.right() - text_left - self._u(12.0), rect.height())
        painter.setFont(self._font(12, QFont.Weight.DemiBold))
        painter.setPen(text_color)
        self._paint_scrolling_text(painter, value_rect, value, 12, text_color, False)
        self._paint_plugin_status_dot(painter, rect, status)
        painter.restore()

    def _paint_plugin_status_dot(self, painter: QPainter, rect: QRectF, status: str):
        status = str(status or "normal").lower()
        colors = {
            "running": QColor(52, 199, 89, 235),
            "done": QColor(255, 59, 48, 235),
            "due": QColor(255, 59, 48, 235),
            "soon": QColor(255, 149, 0, 235),
            "loading": QColor(0, 122, 255, 225),
            "error": QColor(255, 149, 0, 235),
            "alarm": QColor(255, 59, 48, 235),
            "severe": QColor(255, 59, 48, 235),
            "rain": QColor(90, 200, 250, 235),
        }
        color = colors.get(status)
        if color is None:
            return
        size = self._u(6.0)
        dot = QRectF(rect.right() - self._u(9.0), rect.top() + self._u(7.0), size, size)
        painter.setPen(QPen(QColor(255, 255, 255, 215), self._u(1.0)))
        painter.setBrush(color)
        painter.drawEllipse(dot)

    def _paint_message(self, painter: QPainter, rect: QRectF, msg: Message, folded: bool):
        progress = self._message_card_progress(rect)
        if progress < 0.08:
            color = msg.name_color
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 225))
            painter.drawEllipse(rect)
            return

        radius = rect.height() / 2 if progress < 0.55 else min(self._u(23), rect.height() / 2)
        if progress < 0.92:
            color = msg.name_color
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), int(80 + progress * 78)))
            painter.drawRoundedRect(rect, radius, radius)
        else:
            self._paint_black_glass(painter, rect, radius, folded=False)

        painter.save()
        painter.setOpacity(progress)
        avatar_size = self._u(28)
        avatar = QRectF(rect.left() + self._u(10), rect.center().y() - avatar_size / 2.0, avatar_size, avatar_size)
        painter.setPen(Qt.PenStyle.NoPen)
        if not msg.avatar_image.isNull():
            painter.save()
            clip = QPainterPath()
            clip.addEllipse(avatar)
            painter.setClipPath(clip)
            painter.drawImage(avatar, msg.avatar_image)
            painter.restore()
        else:
            painter.setBrush(msg.avatar_color)
            painter.drawEllipse(avatar)
            painter.setFont(self._font(9, QFont.Weight.Bold))
            painter.setPen(QColor(255, 255, 255, 235))
            painter.drawText(avatar, Qt.AlignmentFlag.AlignCenter, msg.sender[:1])

        x = avatar.right() + self._u(11)
        painter.setFont(self._font(10, QFont.Weight.Bold))
        name_w = QFontMetrics(painter.font()).horizontalAdvance(msg.sender) + self._u(7)
        painter.setPen(msg.name_color)
        painter.drawText(QRectF(x, rect.top() + self._u(6), name_w, rect.height() - self._u(12)), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, msg.sender)
        x += name_w + self._u(9)
        text_rect = QRectF(x, rect.top() + self._u(6), rect.right() - x - self._u(16), rect.height() - self._u(12))
        self._paint_scrolling_text(painter, text_rect, msg.text, 10, QColor(255, 255, 255, 218), False)
        painter.restore()

    def _empty_bar_colors(self) -> tuple[QColor, QColor, QColor]:
        palettes = {
            "cyan": (QColor(50, 224, 255), QColor(70, 154, 255), QColor(105, 255, 207)),
            "violet": (QColor(144, 102, 255), QColor(66, 120, 255), QColor(236, 92, 255)),
            "amber": (QColor(255, 205, 92), QColor(255, 126, 82), QColor(110, 224, 255)),
            "rainbow": (QColor(255, 90, 134), QColor(78, 214, 255), QColor(112, 255, 166)),
            "aurora": (QColor(80, 172, 255), QColor(118, 104, 255), QColor(76, 229, 255)),
        }
        if getattr(self, "empty_bar_palette", "aurora") == "custom":
            colors = [QColor(value) for value in getattr(self, "empty_bar_custom_colors", ("#46D6FF", "#73FFA6", "#FF5A86"))]
            if all(color.isValid() for color in colors):
                return colors[0], colors[1], colors[2]
        return palettes.get(getattr(self, "empty_bar_palette", "aurora"), palettes["aurora"])

    def _paint_thin_bar(self, painter: QPainter, rect: QRectF):
        painter.save()
        painter.setOpacity(painter.opacity() * max(0.1, min(1.0, float(getattr(self, "empty_bar_opacity", 100)) / 100.0)))
        bar = rect.adjusted(self._u(0.5), self._u(0.5), -self._u(0.5), -self._u(0.5))
        radius = bar.height() / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        colors = self._empty_bar_colors()
        path = self._rounded_path(bar, radius)

        glow_rect = bar.adjusted(-self._u(1.2), -self._u(1.2), self._u(1.2), self._u(1.2))
        glow_path = self._rounded_path(glow_rect, glow_rect.height() / 2.0)
        glow = QLinearGradient(glow_rect.left(), glow_rect.top(), glow_rect.right(), glow_rect.top())
        glow.setColorAt(0.00, QColor(colors[0].red(), colors[0].green(), colors[0].blue(), 42))
        glow.setColorAt(0.34, QColor(colors[2].red(), colors[2].green(), colors[2].blue(), 62))
        glow.setColorAt(0.68, QColor(colors[1].red(), colors[1].green(), colors[1].blue(), 56))
        glow.setColorAt(1.00, QColor(colors[0].red(), colors[0].green(), colors[0].blue(), 42))
        painter.fillPath(glow_path, glow)

        base = QLinearGradient(bar.left(), bar.top(), bar.right(), bar.bottom())
        base.setColorAt(0.00, QColor(colors[0].red(), colors[0].green(), colors[0].blue(), 88))
        base.setColorAt(0.30, QColor(colors[2].red(), colors[2].green(), colors[2].blue(), 124))
        base.setColorAt(0.58, QColor(255, 255, 255, 108))
        base.setColorAt(0.82, QColor(colors[1].red(), colors[1].green(), colors[1].blue(), 118))
        base.setColorAt(1.00, QColor(colors[0].red(), colors[0].green(), colors[0].blue(), 74))
        painter.fillPath(path, base)

        painter.save()
        painter.setClipPath(path)
        phase = (time.monotonic() * 0.46 * max(0.1, min(4.0, float(getattr(self, "animation_speed", 1.0))))) % 1.0
        span = bar.width() * 0.78
        center = bar.left() - span + phase * (bar.width() + span * 2.0)
        stream = QLinearGradient(center - span, bar.top(), center + span, bar.top())
        stream.setColorAt(0.00, QColor(colors[0].red(), colors[0].green(), colors[0].blue(), 0))
        stream.setColorAt(0.24, QColor(colors[2].red(), colors[2].green(), colors[2].blue(), 106))
        stream.setColorAt(0.50, QColor(255, 255, 255, 138))
        stream.setColorAt(0.72, QColor(colors[1].red(), colors[1].green(), colors[1].blue(), 96))
        stream.setColorAt(1.00, QColor(colors[0].red(), colors[0].green(), colors[0].blue(), 0))
        painter.fillRect(bar.adjusted(-span, 0.0, span, 0.0), stream)
        painter.restore()

        sheen = QLinearGradient(0, bar.top(), 0, bar.bottom())
        sheen.setColorAt(0.00, QColor(255, 255, 255, 48))
        sheen.setColorAt(0.46, QColor(255, 255, 255, 10))
        sheen.setColorAt(1.00, QColor(255, 255, 255, 20))
        painter.fillPath(path, sheen)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 50), self._u(1.0)))
        painter.drawPath(path)
        painter.restore()

    def _scrolling_text_x(self, rect: QRectF, text_w: float, centered: bool, centered_scroll: bool, focus_px: float | None = None) -> float:
        if text_w <= rect.width() - self._u(10):
            return rect.center().x() - text_w / 2.0 if centered else rect.left()
        if focus_px is not None:
            min_x = rect.right() - text_w
            max_x = rect.left()
            safe_left = rect.left() + rect.width() * 0.40
            safe_right = rect.left() + rect.width() * 0.68
            focus_x = max(0.0, min(text_w, focus_px))
            desired = rect.left() if focus_x <= rect.width() * 0.42 else safe_right - focus_x
            if desired + focus_x < safe_left:
                desired = safe_left - focus_x
            return max(min_x, min(max_x, desired))
        travel = text_w - rect.width() + self._u(42)
        if centered_scroll:
            offset = math.sin(self.scroll_phase * 0.55) * travel / 2.0
            return rect.center().x() - text_w / 2.0 - offset
        offset = (math.sin(self.scroll_phase * 0.55) + 1.0) / 2.0 * travel
        return rect.left() - offset

    def _is_combining_lyric_char(self, char: str) -> bool:
        codepoint = ord(char)
        return unicodedata.combining(char) != 0 or 0xFE00 <= codepoint <= 0xFE0F or codepoint == 0x200D

    def _is_cjk_lyric_char(self, char: str) -> bool:
        codepoint = ord(char)
        return (
            0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0x3040 <= codepoint <= 0x30FF
            or 0xAC00 <= codepoint <= 0xD7AF
        )

    def _lyric_chunks(self, text: str) -> list[str]:
        chunks: list[str] = []
        buffer = ""
        buffer_kind = ""
        for char in text:
            if self._is_combining_lyric_char(char):
                if chunks and not buffer:
                    chunks[-1] += char
                else:
                    buffer += char
                continue
            if char.isspace():
                if buffer:
                    chunks.append(buffer + char)
                    buffer = ""
                    buffer_kind = ""
                elif chunks:
                    chunks[-1] += char
                else:
                    chunks.append(char)
                continue
            if self._is_cjk_lyric_char(char):
                if buffer:
                    chunks.append(buffer)
                    buffer = ""
                    buffer_kind = ""
                chunks.append(char)
                continue
            kind = "word" if char.isalnum() else "punct"
            if kind == "punct":
                if buffer:
                    chunks.append(buffer + char)
                    buffer = ""
                    buffer_kind = ""
                elif chunks:
                    chunks[-1] += char
                else:
                    chunks.append(char)
                continue
            if buffer_kind == kind:
                buffer += char
            else:
                if buffer:
                    chunks.append(buffer)
                buffer = char
                buffer_kind = kind
        if buffer:
            chunks.append(buffer)
        return chunks or ([text] if text else [])

    def _gelv_chunks(self, text: str) -> list[str]:
        return [str(chunk).strip() for chunk in self._lyric_chunks(text) if str(chunk).strip()]

    def _gelv_join_chunks(self, chunks: list[str]) -> str:
        output = ""
        for chunk in chunks:
            if not output:
                output = chunk
                continue
            joins_as_word = (
                output[-1:].isascii()
                and output[-1:].isalnum()
                and chunk[:1].isascii()
                and chunk[:1].isalnum()
            )
            output += (" " if joins_as_word else "") + chunk
        return output.strip()

    def _gelv_split_line(self, text: str, threshold: int | None = None) -> tuple[str, str] | None:
        chunks = self._gelv_chunks(text)
        limit = max(1, int(threshold if threshold is not None else 18))
        if len(chunks) <= limit:
            return None
        upper_count = (len(chunks) + 1) // 2
        upper = self._gelv_join_chunks(chunks[:upper_count])
        lower = self._gelv_join_chunks(chunks[upper_count:])
        if not upper or not lower:
            return None
        return upper, lower

    def _lyric_layout_key(self, text: str, size: float, bold: bool, spacing_percent: int | None = None) -> tuple[object, ...]:
        weight = QFont.Weight.Bold if bold else QFont.Weight.Normal
        weight_value = getattr(weight, "value", None)
        if weight_value is None:
            try:
                weight_value = int(weight)
            except Exception:
                weight_value = str(weight)
        return (
            text,
            self._font_size(size),
            weight_value,
            tuple(self._font_families()),
            bool(getattr(self, "high_quality_font_rendering", False)),
            int(getattr(self, "lyric_elastic_strength", 100)),
            int(spacing_percent if spacing_percent is not None else getattr(self, "lyric_word_spacing", 100)),
        )

    def _lyric_chunk_spacing(self, chunk: str, base_width: float, reference_width: float | None = None, spacing_percent: int | None = None) -> float:
        if not chunk or not chunk.strip():
            return 0.0
        strength = max(0.0, min(1.6, float(getattr(self, "lyric_elastic_strength", 100)) / 100.0))
        spacing = max(0.6, min(1.5, float(spacing_percent if spacing_percent is not None else getattr(self, "lyric_word_spacing", 100)) / 100.0))
        if strength <= 0.01:
            return 0.0
        has_cjk = any(self._is_cjk_lyric_char(char) for char in chunk)
        minimum = self._u(1.2 if has_cjk else 1.8)
        selected_width = max(0.0, float(base_width))
        original_width = max(0.0, float(reference_width if reference_width is not None else base_width))
        effective_width = (selected_width + original_width) * 0.5
        proportional = effective_width * (0.046 + 0.038 * strength)
        return max(minimum, proportional) * spacing

    def _lyric_layout(self, text: str, size: float, bold: bool, spacing_percent: int | None = None) -> LyricLayout:
        key = self._lyric_layout_key(text, size, bold, spacing_percent)
        cache = getattr(self, "_lyric_layout_cache", None)
        if cache is None:
            cache = {}
            self._lyric_layout_cache = cache
        cached = cache.get(key)
        if cached is not None:
            return cached
        chunks = tuple(self._lyric_chunks(text))
        font = self._font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        metrics = QFontMetrics(font)
        reference_size = 18 if size >= 16 else 15
        reference_font = self._font(reference_size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        reference_metrics = QFontMetrics(reference_font)
        widths = []
        for chunk in chunks:
            width = float(metrics.horizontalAdvance(chunk))
            reference_width = float(reference_metrics.horizontalAdvance(chunk))
            widths.append(width + self._lyric_chunk_spacing(chunk, width, reference_width, spacing_percent))
        widths = tuple(widths)
        layout = LyricLayout(text=text, chunks=chunks, widths=widths, total_width=sum(widths), font_key=key)
        cache[key] = layout
        if len(cache) > 96:
            cache.pop(next(iter(cache)))
        return layout

    def _gelv_layout_key(self, text: str, size: float, spacing_percent: int | None = None) -> tuple[object, ...]:
        return (
            "gelv",
            text,
            self._font_size(size),
            tuple(self._gelv_font_families()),
            int(spacing_percent if spacing_percent is not None else self._gelv_spacing_percent(1.0)),
        )

    def _gelv_chunk_spacing(self, chunk: str, base_width: float, reference_width: float | None = None, spacing_percent: int | None = None) -> float:
        if not chunk or not chunk.strip():
            return 0.0
        spacing = max(100, min(420, int(spacing_percent if spacing_percent is not None else self._gelv_spacing_percent(1.0))))
        spread = max(1.0, min(4.2, spacing / 100.0))
        has_cjk = any(self._is_cjk_lyric_char(char) for char in chunk)
        minimum = self._u(8.0 if has_cjk else 10.0)
        selected_width = max(0.0, float(base_width))
        original_width = max(0.0, float(reference_width if reference_width is not None else base_width))
        effective_width = (selected_width + original_width) * 0.5
        proportional = effective_width * (0.30 if has_cjk else 0.28)
        return max(minimum, proportional) * spread

    def _gelv_layout(self, text: str, size: float, spacing_percent: int | None = None) -> LyricLayout:
        key = self._gelv_layout_key(text, size, spacing_percent)
        cache = getattr(self, "_lyric_layout_cache", None)
        if cache is None:
            cache = {}
            self._lyric_layout_cache = cache
        cached = cache.get(key)
        if cached is not None:
            return cached
        chunks = tuple(self._gelv_chunks(text))
        font = self._gelv_font(size, QFont.Weight.Normal)
        metrics = QFontMetrics(font)
        reference_font = self._gelv_font(18 if size >= 16 else 15, QFont.Weight.Normal)
        reference_metrics = QFontMetrics(reference_font)
        widths = []
        for chunk in chunks:
            width = float(metrics.horizontalAdvance(chunk))
            reference_width = float(reference_metrics.horizontalAdvance(chunk))
            widths.append(width + self._gelv_chunk_spacing(chunk, width, reference_width, spacing_percent))
        layout = LyricLayout(text=text, chunks=chunks, widths=tuple(widths), total_width=sum(widths), font_key=key)
        cache[key] = layout
        if len(cache) > 96:
            cache.pop(next(iter(cache)))
        return layout

    def _lyric_progress_width(self, layout: LyricLayout, progress: float) -> float:
        chunks = layout.chunks
        if not chunks:
            return 0.0
        target = max(0.0, min(1.0, progress)) * len(chunks)
        full_count = max(0, min(len(chunks), int(target)))
        partial = max(0.0, min(1.0, target - full_count))
        width = sum(layout.widths[:full_count])
        if full_count < len(chunks):
            width += layout.widths[full_count] * partial
        return width

    def _lyric_elastic_scales(self, chunk_count: int, progress: float, confidence: float) -> list[float]:
        if chunk_count <= 0:
            return []
        strength = max(0.0, min(1.6, float(getattr(self, "lyric_elastic_strength", 100)) / 100.0))
        if strength <= 0.01 or confidence <= 0.01:
            return [1.0] * chunk_count
        mode = str(getattr(self, "lyric_elastic_mode", "both") or "both").lower()
        if chunk_count == 1:
            bloom = math.sin(max(0.0, min(1.0, progress)) * math.pi)
            return [1.0 + 0.055 * bloom * confidence * strength]
        focus = max(0.0, min(1.0, progress)) * (chunk_count - 1)
        now = time.monotonic()
        scales: list[float] = []
        for index in range(chunk_count):
            distance = abs(index - focus)
            core = math.exp(-(distance * distance) / 0.18)
            near = math.exp(-(distance * distance) / 1.35)
            sung_side = max(0.0, min(1.0, focus - index + 0.65))
            leading = 0.60 + 0.40 * sung_side
            spring = 0.94 + 0.06 * math.sin(now * 5.8 - index * 0.72)
            if mode == "left":
                right_fade = max(0.0, min(1.0, 1.0 - max(0.0, index - focus) / 0.55))
                left_tail = math.exp(-(max(0.0, focus - index) ** 2) / 2.1)
                amount = 0.078 * core * right_fade + 0.030 * left_tail * leading
            else:
                amount = 0.100 * core + 0.044 * near * leading
            scale = 1.0 + confidence * strength * spring * amount
            scales.append(max(1.0, min(1.24, scale)))
        return scales

    def _mix_alpha_color(self, a: QColor, b: QColor, amount: float, alpha: int | None = None) -> QColor:
        amount = max(0.0, min(1.0, amount))
        return QColor(
            int(a.red() + (b.red() - a.red()) * amount),
            int(a.green() + (b.green() - a.green()) * amount),
            int(a.blue() + (b.blue() - a.blue()) * amount),
            max(0, min(255, int(a.alpha() + (b.alpha() - a.alpha()) * amount) if alpha is None else alpha)),
        )

    def _paint_wave_stream_lyric_chunks(
        self,
        painter: QPainter,
        rect: QRectF,
        x: float,
        layout: LyricLayout,
        size: float,
        bold: bool,
        base_color: QColor,
        live_color: QColor,
        progress: float,
        confidence: float,
        glow: bool = False,
    ):
        chunks = layout.chunks
        if not chunks:
            return
        base_font = self._font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        widths = layout.widths
        total_w = layout.total_width
        live_width = max(0.0, min(total_w, self._lyric_progress_width(layout, progress)))
        focus = max(0.0, min(1.0, progress)) * max(1, len(chunks) - 1)
        elastic_confidence = confidence
        if len(chunks) > 48:
            elastic_confidence *= max(0.38, 48.0 / float(len(chunks)))
        scales = self._lyric_elastic_scales(len(chunks), progress, elastic_confidence)
        cursor = x
        white = QColor(255, 255, 255, live_color.alpha())
        painter.setFont(base_font)
        for index, chunk in enumerate(chunks):
            width = widths[index]
            if width <= 0.0:
                cursor += width
                continue
            sung_amount = max(0.0, min(1.0, (live_width - (cursor - x)) / max(1.0, width)))
            focus_strength = max(0.0, 1.0 - abs(index - focus) / 1.75) * elastic_confidence
            chunk_color = self._mix_alpha_color(base_color, live_color, sung_amount)
            chunk_color = self._mix_alpha_color(chunk_color, white, 0.22 * focus_strength, alpha=chunk_color.alpha())
            if glow:
                chunk_color = QColor(255, 255, 255, max(0, min(255, int(24 + 38 * focus_strength))))

            scale = scales[index]
            token_rect = QRectF(cursor, rect.top(), width, rect.height())
            painter.save()
            painter.translate(token_rect.center())
            painter.scale(scale, scale)
            painter.setPen(chunk_color)
            painter.drawText(
                QRectF(-width / 2.0, -rect.height() / 2.0, width, rect.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
                chunk,
            )
            painter.restore()
            cursor += width

    def _paint_live_lyric_text(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        size: float,
        base_color: QColor,
        live_color: QColor,
        bold: bool,
        phase: dict[str, float],
        centered: bool = False,
        centered_scroll: bool = False,
        spacing_percent: int | None = None,
    ):
        progress = max(0.0, min(1.0, float(phase.get("progress", 0.0))))
        confidence = max(0.0, min(1.0, float(phase.get("confidence", 0.0))))
        bloom = math.sin(progress * math.pi)
        breath = 0.5 + 0.5 * math.sin(time.monotonic() * 3.0 + progress * math.tau)
        layout = self._lyric_layout(text, size, bold, spacing_percent)
        text_w = layout.total_width
        live_width = self._lyric_progress_width(layout, progress)
        x = self._scrolling_text_x(rect, text_w, centered, centered_scroll, focus_px=live_width)
        color, companion = self._accent_pair()
        wave_fill_enabled = bool(getattr(self, "wave_color_fill_enabled", False))
        if wave_fill_enabled:
            fill = self._wave_fill_color()
            color = QColor(fill)
            companion = QColor(fill).lighter(128)
            base_color = self._mix_alpha_color(base_color, fill, 0.34, alpha=base_color.alpha())
            live_color = QColor(fill.red(), fill.green(), fill.blue(), live_color.alpha())

        painter.save()
        painter.setClipRect(rect)
        if text and confidence > 0.05:
            focus_x = x + max(0.0, min(text_w, live_width))
            focus_radius = self._u(42 + 22 * bloom)
            focus = QRadialGradient(QPointF(focus_x, rect.center().y()), focus_radius)
            focus.setColorAt(0.0, QColor(255, 255, 255, int(38 + 30 * breath)))
            focus.setColorAt(0.26, QColor(color.red(), color.green(), color.blue(), int(28 + 24 * confidence)))
            focus.setColorAt(0.58, QColor(companion.red(), companion.green(), companion.blue(), int(18 + 12 * bloom)))
            focus.setColorAt(1.0, QColor(companion.red(), companion.green(), companion.blue(), 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(focus)
            painter.drawEllipse(QPointF(focus_x, rect.center().y()), focus_radius, focus_radius * 0.48)

        glow_alpha = int(30 + 28 * confidence + 18 * bloom)
        self._paint_wave_stream_lyric_chunks(
            painter,
            rect.adjusted(-self._u(0.6), -self._u(0.4), self._u(0.6), self._u(0.4)),
            x,
            layout,
            size,
            bold,
            QColor(255, 255, 255, glow_alpha),
            QColor(255, 255, 255, glow_alpha),
            progress,
            confidence * 0.52,
            glow=True,
        )

        elastic_live = QColor(live_color.red(), live_color.green(), live_color.blue(), max(0, min(255, int(live_color.alpha() * (0.76 + 0.16 * bloom)))))
        self._paint_wave_stream_lyric_chunks(
            painter,
            rect,
            x,
            layout,
            size,
            bold,
            base_color,
            elastic_live,
            progress,
            confidence,
        )
        painter.restore()

    def _paint_scrolling_text(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        size: int,
        color: QColor,
        bold: bool,
        centered: bool = False,
        centered_scroll: bool = False,
    ):
        font = self._font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(color)
        fm = QFontMetrics(font)
        text_w = fm.horizontalAdvance(text)
        painter.save()
        painter.setClipRect(rect)
        if text_w <= rect.width() - self._u(10):
            flags = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignHCenter if centered else Qt.AlignmentFlag.AlignLeft)
            painter.drawText(rect, flags, text)
        else:
            x = self._scrolling_text_x(rect, text_w, centered, centered_scroll)
            painter.drawText(QRectF(x, rect.top(), text_w + self._u(20), rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Island Stack V2")
    app.setFont(QFont(IslandStackVisual.best_available_font_family(IslandStackVisual.DEFAULT_FONT_FAMILY), 9))
    from api.fake_provider import FakeIslandDataProvider

    widget = IslandStackVisual(data_provider=FakeIslandDataProvider())
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
