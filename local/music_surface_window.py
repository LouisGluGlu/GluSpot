from __future__ import annotations

from collections import OrderedDict, deque
from bisect import bisect_left, bisect_right
from functools import lru_cache
import math
import re
import time
import unicodedata

from PIL import Image, ImageFilter
from local.artwork_skeleton import paint_artwork_skeleton

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, QTextBoundaryFinder, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient, QRegion, QTextCharFormat, QTextLayout, QTextOption, QTransform
from PySide6.QtWidgets import QWidget

from api.lyric_timeline import active_index_for_position
from local.music_lyric_preparation import (
    coalesced_timed_lyrics, prepare_lyric_presentation, snapshot_lyric_input,
)
from local.lyric_unit_timing import (
    TimedHighlightGroup,
    resolve_display_row_timing,
    display_row_reading_front,
    duet_estimated_read_start,
    estimated_row_read_end,
    estimated_row_reading_progress,
    explicit_slow_unit_groups,
    genuinely_timed_unit_row,
    lyric_timing_quality_label,
    real_unit_row_finished_before_next_start,
    timed_highlight_wave_progress,
    timed_unit_reading_front,
)
from local.island_presentation import animation_duration
from local.music_lyric_reading import (
    apply_cjk_letter_spacing,
    is_cjk_cluster,
    read_wipe_image_at_front,
    reading_front,
    reading_unit_offsets,
    whole_block_reading_gradient,
)
from local.settings_store import (
    MUSIC_READING_GRADIENT_DEFAULT_STEPS,
    MUSIC_READING_LIFT_DEFAULT_LEVEL,
    MUSIC_READING_STRATEGY_DEFAULT,
    normalize_music_reading_gradient_steps,
    normalize_music_reading_lift_level,
    normalize_music_reading_strategy,
)
from local.music_lyric_motion import (
    GLYPH_FLOAT_LIFT,
    GLYPH_PULSE_SPAN,
    SPARSE_MIN_SECONDS_PER_UNIT,
    LogicalLineSpringChain,
    MotionSimulationClock,
    MusicLyricMotion,
    continuous_read_lift,
    lyric_emphasis_plan,
    lyric_glow_phase,
    SparseGlyphFrame,
    sparse_glyph_frame,
)
from local.music_lyric_presentation import (
    INTRO_COUNTDOWN_TEXT,
    DuetLyricPresentation,
    split_display_layers,
)
from local.music_surface_palette import (
    MusicSurfacePalette,
    artwork_surface_palette,
    cached_artwork_surface_palette,
    background_role_color,
    future_lyric_color as select_future_lyric_color,
)
from local.audio_output_panel import AudioOutputPanel
from local.audio_output_icons import paint_audio_output_status
from local.audio_output_status import OutputMember, OutputMembershipTracker
from local.audio_output_motion import OutputConnectionMotion, connection_capsule_rect, paint_connection_pose
from local.music_surface_background import AlbumFlowBackgroundRenderer
from local.music_mode_controls import PRESENTATION_GLYPH_STATES, paint_presentation_glyph
from local.music_source_icons import paint_music_source_icon
from local.personal_dj_icon import DJ_RING_CYCLE_SECONDS, paint_personal_dj_icon, personal_dj_status_color, personal_dj_status_opacity
from local.liquid_glass import GlassCaptureMixin, paint_liquid_glass, widget_glass_level, glass_material_tint, live_glass_paint
from local.ui_theme import app_dark_mode
from local.control_hover import HoverMotion, hover_scale
from local.music_surface_layout import MusicContentLayoutMixin
from local.music_song_collection import MusicSongCollectionMixin
from local.music_window_compact import MusicWindowCompactMixin


MUSIC_WINDOW_ASPECT_RATIO = 16.0 / 9.0


def constrained_window_size(
    width: float,
    height: float,
    minimum: QSize,
    maximum: QSize,
    *,
    aspect_ratio: float = MUSIC_WINDOW_ASPECT_RATIO,
    prefer_width: bool = True,
) -> QSize:
    """Return a bounded size while preserving the music surface aspect ratio."""
    ratio = max(0.1, float(aspect_ratio))
    min_width = max(int(minimum.width()), int(round(minimum.height() * ratio)))
    max_width = min(int(maximum.width()), int(round(maximum.height() * ratio)))
    max_width = max(min_width, max_width)
    requested_width = float(width) if prefer_width else float(height) * ratio
    bounded_width = max(min_width, min(max_width, int(round(requested_width))))
    return QSize(bounded_width, max(1, int(round(bounded_width / ratio))))


class MusicSurfaceWindow(MusicWindowCompactMixin, MusicSongCollectionMixin, MusicContentLayoutMixin, GlassCaptureMixin, QWidget):
    """Frameless full-screen/windowed music surface owned by the V2 island."""

    seekFinished = Signal(bool, float, str)
    artworkPaletteReady = Signal(int, object, object)
    RESIZE_MARGIN = 9
    TITLE_BAR_HEIGHT = 58
    MODE_TRANSITION_MS = 340
    AMBIENT_FRAME_SECONDS = AlbumFlowBackgroundRenderer.FRAME_SECONDS
    LYRIC_RASTER_CACHE_MAX_BYTES = 48 * 1024 * 1024
    LYRIC_RASTER_CACHE_MAX_ENTRIES = 128
    LYRIC_BLUR_CACHE_MAX_BYTES = 32 * 1024 * 1024
    LYRIC_BLUR_CACHE_MAX_ENTRIES = 96
    LYRIC_BLUR_RADII = (0.0, 3.0, 6.0)
    LYRIC_BLUR_PREWARM_LEAD_SECONDS = 1.5
    LYRIC_BLUR_PREWARM_INTERVAL_SECONDS = 0.035
    BLURRED_LYRIC_DPR_CAP = 1.25
    LYRIC_BROWSE_IDLE_SECONDS = 4.0
    LYRIC_BROWSE_RETURN_SECONDS = 0.48
    COVER_PAUSE_SCALE = 0.8
    COVER_PAUSE_ANIMATION_SECONDS = 0.28
    COVER_LAYOUT_ANIMATION_SECONDS = 0.56
    COVER_NO_LYRICS_SCALE = 1.2
    FULLSCREEN_CONTROL_IDLE_SECONDS = 5.0
    WINDOWED_CONTROL_IDLE_SECONDS = 2.0
    FULLSCREEN_CONTROL_TRANSITION_SECONDS = 0.52
    DJ_SPEECH_CONTRACTION_SECONDS = 0.32
    DJ_OUTRO_IDLE_SECONDS = 2 * DJ_RING_CYCLE_SECONDS
    LIBRARY_EXPANSION_SECONDS = 0.36
    LOW_PERCEPTION_BLOCK_WIPE_SECONDS = 0.5
    SURFACE_FONT_FAMILIES = ("Segoe UI", "Microsoft YaHei UI")
    REFERENCE_SURFACE_HEIGHT = 666.0
    MIN_SURFACE_SCALE = 0.5
    MAX_SURFACE_SCALE = 2.5
    LYRIC_PIXEL_SIZE = 38
    TITLE_PIXEL_SIZE = 34
    ARTIST_PIXEL_SIZE = 22
    METADATA_MARQUEE_SECONDS = 5.0
    METADATA_MARQUEE_HOLD_SECONDS = 15.0
    METADATA_MARQUEE_END_HOLD_SECONDS = 0.7
    METADATA_RASTER_CACHE_MAX_BYTES = 64 * 1024 * 1024
    METADATA_RASTER_CACHE_MAX_ENTRIES = 96
    TRANSPORT_PANEL_HEIGHT = 60.0
    TRANSPORT_FEEDBACK_SECONDS = 0.42
    TRANSPORT_LIGHT_EXPAND_SECONDS = 1.5
    TRANSPORT_LIGHT_FADE_SECONDS = 0.22
    TRANSPORT_LIGHT_CREST_FRACTION = 0.79
    TRANSPORT_LIGHT_SPRITE_SIZE = 384
    ARTWORK_SCALE_CACHE_LIMIT = 24
    ARTWORK_SCALE_CACHE_BYTES = 64 * 1024 * 1024
    OBO_REVEAL_SECONDS = 3.0
    ANIMATION_FRAME_MS = 16
    EMPHASIS_FRAME_MS = 4
    LYRIC_LAYOUT_BATCH_SECONDS = 0.0025
    CONTROL_BLUR_FRACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)
    CONTROL_LAYER_DPR_CAP = 1.25
    CONTROL_LAYER_CACHE_MAX_BYTES = 64 * 1024 * 1024
    CONTROL_BOTTOM_REGION_HEIGHT = 195.0
    LYRIC_GLOW_CACHE_MAX_BYTES = 64 * 1024 * 1024
    LYRIC_GLOW_CACHE_MAX_ENTRIES = 48
    GLOW_EDGE_FEATHER_STEPS = 4
    GLYPH_TINT_CACHE_MAX_BYTES = 16 * 1024 * 1024
    GLYPH_TINT_CACHE_MAX_ENTRIES = 2048
    SPARSE_GLYPH_HALO_CACHE_MAX_BYTES = 24 * 1024 * 1024
    SPARSE_GLYPH_HALO_CACHE_MAX_ENTRIES = 1024
    GLYPH_RUN_CACHE_MAX_ENTRIES = 128
    GLYPH_RUN_CACHE_MAX_BYTES = 16 * 1024 * 1024
    ISOLATED_GLYPH_CACHE_MAX_ENTRIES = 128
    ISOLATED_GLYPH_CACHE_MAX_BYTES = 24 * 1024 * 1024
    ISOLATED_GLYPH_BUILD_MAX_ENTRIES = 16
    ISOLATED_GLYPH_BUILD_MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, owner):
        super().__init__(None)
        self.owner = owner
        # The song page and its child panels use stationary click controls.
        # The painted top-left library return keeps its explicit hover reveal.
        self.setProperty("controlHoverDisabled", True)
        self.setWindowTitle("GluSpot Music")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._fullscreen = False
        self._normal_geometry = QRect()
        self._drag_origin = QPoint()
        self._drag_geometry = QRect()
        self._resize_edges: set[str] = set()
        self._moving = False
        self._closing_for_shutdown = False
        self._button_rects: dict[str, QRectF] = {}
        self._owned_playback_mode: str | None = None
        self._owned_playback_modes: tuple[str, ...] = ()
        self._owned_personal_fm = False
        self._owned_personal_dj_enabled = None
        self._owned_dj_speaking = False
        self._dj_activity_from = self._dj_activity_to = 0.0
        self._dj_activity_started_at = 0.0
        self._dj_last_reveal = 0.0
        self._dj_audio_frame = None
        self._dj_presentation_state = "idle"
        self._dj_next_title = ""
        self._dj_presentation_token = None
        self._dj_ring_started_at = time.monotonic()
        self._dj_failed_until = 0.0
        self._dj_outro_started_at: float | None = None
        self._dj_outro_track_id = ""
        self._dj_completed_token = 0
        self._dj_next_preload_state = "idle"
        self._dj_introduction_pending = False
        self._dj_introduction_failed = False
        self._dj_last_damage = QRect()
        self._mode_snapshot_dj_hit_path = QPainterPath()
        self._audio_output_panel: AudioOutputPanel | None = None
        self._audio_output_kinds: tuple[str, ...] = ()
        self._audio_output_membership = OutputMembershipTracker()
        self._audio_output_motion = OutputConnectionMotion()
        self._audio_output_event_serial = 0
        self._audio_output_recent_announced: dict[str, float] = {}
        self._media_button_rects: dict[str, QRectF] = {}
        self._media_control_panel_rect = QRectF()
        self._transport_feedback: dict[str, tuple[float, QPointF, float, float]] = {}
        self._transport_light_dirty_regions: dict[str, QRect] = {}
        self._transport_light_ink_regions: dict[str, QRegion] = {}
        self._transport_light_origins: dict[str, QPointF] = {}
        self._transport_light_sprite = QImage()
        self._obo_reveal_light_sprite = QImage()
        self._obo_reveal_track_key = None
        self._art_loading_track_key = None
        self._art_loading_waiting = False
        self._art_loading_preview_key = None
        self._hovered_window_button = ""
        self._hovered_media_command = ""
        self._pressed_media_command = ""
        self._pressed_window_button = ""
        self._pressed_dj_toggle_ready = False
        self._pressed_lyric_index: int | None = None
        self._control_last_motion_at = time.monotonic()
        self._control_transition_started_at = self._control_last_motion_at
        self._control_reveal_from = 1.0
        self._control_reveal_target = 1.0
        self._control_last_tick_reveal = 1.0
        self._control_hover_motions: dict[str, HoverMotion] = {}
        self._dj_voice_selector_active = False
        self._dj_voice_selector_pointer = False
        self._dj_voice_selector_widget = None
        self._dj_voice_selector_motion = HoverMotion()
        self._dj_voice_selector_close_timer = QTimer(self)
        self._dj_voice_selector_close_timer.setSingleShot(True)
        self._dj_voice_selector_close_timer.setInterval(2000)
        self._dj_voice_selector_close_timer.timeout.connect(self._check_dj_voice_selector_leave)
        self._library_expansion_from = 0.0
        self._library_expansion_target = 0.0
        self._library_expansion_started_at = self._control_last_motion_at
        self._library_expansion_paused_at: float | None = None
        self._library_last_tick_expansion = 0.0
        self._idle_cursor_hidden = False
        self._idle_cursor_restore_cursor: QCursor | None = None
        self._control_layer_cache: dict[tuple, QImage] = {}
        self._control_layer_cache_bytes = 0
        self._control_layer_next_prewarm_at = 0.0
        self._control_prewarm_library_available = self._library_back_available()
        self._control_prewarm_remaining = (2 + int(self._control_prewarm_library_available)) * (len(self.CONTROL_BLUR_FRACTIONS) - 1)
        self._control_prewarm_requested = False
        self._lyric_motion = MusicLyricMotion()
        self._logical_line_spring = LogicalLineSpringChain()
        self._visual_row_ids_key = None
        self._visual_row_ids = ()
        self._glyph_cache_key = None
        self._glyph_cache: tuple[QImage, tuple[float, ...]] | None = None
        self._glyph_run_cache: dict[tuple[str, str, str], tuple[QImage, tuple[float, ...]]] = {}
        self._glyph_run_cache_bytes = 0
        self._isolated_glyph_cache: dict[tuple[str, str, str], tuple[tuple[QImage, float], ...]] = {}
        self._isolated_glyph_cache_bytes = 0
        self._isolated_glyph_builds: dict[tuple[str, str, str], dict] = {}
        self._isolated_glyph_builds_bytes = 0
        self._isolated_glyph_build_last_key = None
        self._glyph_tint_cache: OrderedDict[tuple[int, int], QImage] = OrderedDict()
        self._glyph_tint_cache_bytes = 0
        self._reading_feather_cache: dict[tuple[str, str, int], float] = {}
        self._reading_line_width_cache: dict[tuple[str, str], int] = {}
        self._sparse_glyph_halo_cache: OrderedDict[int, tuple[QImage, float]] = OrderedDict()
        self._sparse_glyph_halo_cache_bytes = 0
        self._emphasis_protected_glyph_runs: frozenset[tuple[str, str, str]] = frozenset()
        self._emphasis_protected_source_keys: set[int] = set()
        self._emphasis_protected_token = None
        self._emphasis_protected_tint_color = None
        self._sparse_static_run_cache: OrderedDict[tuple, QImage] = OrderedDict()
        self._sparse_static_run_cache_bytes = 0
        self._sparse_timing_map_cache: dict[tuple, tuple[tuple[TimedHighlightGroup, int] | None, ...]] = {}
        self._future_tone_cache: dict[tuple, QColor] = {}
        self._future_tone_track_key = None
        self._future_tone_use_light = False
        self._future_tone_require_light = False
        self._future_tone_backdrop = QColor(12, 15, 22)
        self._art_palette_cache_key = None
        self._art_palette_cache: MusicSurfacePalette | None = None
        self._art_palette_worker_token = 0
        self._art_palette_active_token = None
        self._art_palette_worker_key = None
        self._art_palette_pending = None
        self._art_palette_failed_key = None
        self._lyric_layout_cache: dict[tuple[str, str, int], tuple[QTextLayout, float]] = {}
        self._font_height_cache: dict[tuple[str, float], float] = {}
        self._display_lyrics_cache_key = None
        self._display_lyrics_cache: tuple[list[str], list[tuple[float, str]]] | None = None
        self._display_timing_quality: str | None = None
        self._display_duet_presentation: DuetLyricPresentation | None = None
        self._visual_lyric_cache_key = None
        self._visual_lyric_layers = ()
        self._lyric_loading_track_key = None
        self._lyric_loading_started_at = 0.0
        self._lyric_loading_last_stage = None
        self._lyric_loading_waiting = False
        self._lyric_loading_hold_centered = False
        self._lyric_loading_has_shown = False
        self._lyric_loading_release_at = 0.0
        self._lyric_loading_exit_started_at = 0.0
        self._lyric_loading_cycle_end_at = 0.0
        self._display_timed_units: tuple[tuple[object, ...], ...] = ()
        self._display_row_timings: tuple = ()
        self._display_timed_kinds: tuple[str, ...] = ()
        self._display_timed_read_ends: tuple[float | None, ...] = ()
        self._display_raw_source_texts: tuple[str, ...] = ()
        self._display_units_source = None
        self._display_kinds_source = None
        self._song_layout_key = None
        self._song_layouts: tuple[tuple[QTextLayout, float], ...] = ()
        self._song_secondary_layouts: tuple[tuple[QTextLayout, float, float] | None, ...] = ()
        self._song_parenthetical_layouts: tuple[tuple[QTextLayout, float, float] | None, ...] = ()
        self._song_parenthetical_translation_layouts: tuple[tuple[QTextLayout, float, float] | None, ...] = ()
        self._song_parenthetical_extra_heights: tuple[float, ...] = ()
        self._song_translation_extra_heights: tuple[float, ...] = ()
        self._song_row_heights: tuple[float, ...] = ()
        self._song_row_tops: tuple[float, ...] = ()
        self._song_visual_tops: tuple[float, ...] = ()
        self._song_visual_heights: tuple[float, ...] = ()
        self._song_visual_ends: tuple[int, ...] = ()
        self._song_max_visual_height = 0.0
        self._song_visual_ranges: tuple[tuple[int, int], ...] = ()
        self._song_visual_counts: tuple[int, ...] = ()
        self._song_layout_build_key = None
        self._song_layout_build = None
        self._lyric_raster_cache: dict[tuple, tuple[QImage, float, int]] = {}
        self._lyric_raster_cache_bytes = 0
        self._lyric_blur_cache: dict[tuple, tuple[QImage, int]] = {}
        self._lyric_blur_cache_bytes = 0
        self._lyric_blur_prewarm_token = None
        self._lyric_blur_prewarm_queue: deque[tuple] = deque()
        self._lyric_blur_next_prewarm_at = 0.0
        self._lyric_glow_cache: dict[tuple, tuple[QImage, float, int]] = {}
        self._lyric_glow_cache_bytes = 0
        self._pending_glow_prewarm = None
        self._pending_reading_prewarm = None
        self._last_lyric_emphasis_mode = "none"
        self._has_timed_lyrics = False
        self._active_glow_stage = "done"
        self._recent_paint_costs: deque[float] = deque(maxlen=30)
        self._recent_priority_paint_costs: deque[float] = deque(maxlen=30)
        self._lyric_animation_rect = QRect()
        self._active_lyric_animation_rect = QRect()
        self._cover_animation_rect = QRect()
        self._lyric_spring_active = True
        self._parenthetical_focus_index = None
        self._parenthetical_track_key = None
        self._parenthetical_enter_at = 0.0
        self._parenthetical_visible_since: dict[int, float] = {}
        self._parenthetical_read_progress: dict[int, float] = {}
        self._parenthetical_exit_index = None
        self._parenthetical_exit_at = 0.0
        self._translation_setting = bool(getattr(owner, "enable_dual_line_lyrics", False))
        self._translation_track_key = None
        self._translation_exit_at: float | None = None
        self._translation_last_source = None
        self._translation_exit_source = None
        self._translation_animate_entries = False
        self._translation_entered_at: dict[tuple, float] = {}
        self._translation_known_tokens: set[tuple] = set()
        self._translation_entry_from: dict[tuple, tuple[float, float, float]] = {}
        self._translation_exit_poses: dict[tuple, tuple[float, float, float]] = {}
        self._obo_advanced_focus_index: int | None = None
        self._obo_advanced_focus_pose: tuple[float, float, float, float, bool, float] | None = None
        self._obo_advanced_handoff_last_at: float | None = None
        self._last_full_animation_update_at = -1.0
        self._cover_cache_key = None
        self._cover_cache = QImage()
        self._artwork_scale_cache: OrderedDict[tuple, QImage] = OrderedDict()
        self._artwork_scale_cache_bytes = 0
        self._preview_cover_cache_key = None
        self._preview_cover_cache = QImage()
        self._cover_scale_from = 1.0
        self._cover_scale_to = 1.0 if bool(getattr(owner, "media_is_playing", False)) else self.COVER_PAUSE_SCALE
        self._cover_scale_started_at = 0.0
        self._cover_scale_track_key = ""
        self._cover_layout_from = 0.0
        self._cover_layout_to = 0.0
        self._cover_layout_started_at = 0.0
        self._resolved_centered_track_key = None
        self._last_visible_lyric_snapshot = None
        self._last_visible_lyric_snapshot_track = None
        self._optimistic_playing: bool | None = None
        self._optimistic_playing_until = 0.0
        self._optimistic_track_key = ""
        self._background_cache_key = None
        self._background_cache_at = -1.0
        self._background_cache = QImage()
        self._background_rebuilt_this_paint = False
        self._glass_idle_backdrop = QImage()
        self._glass_idle_backdrop_key = None
        self._frame_glass_light = None
        self._scene_control_ink_key = None
        self._scene_control_ink_cache = QColor("white")
        self._control_color_transitions: dict[str, tuple[QColor, QColor, float]] = {}
        self._frame_control_color_at: float | None = None
        self._album_flow_background = AlbumFlowBackgroundRenderer()
        self._browse_index: float | None = None
        self._browse_until = 0.0
        self._browse_return_from: float | None = None
        self._browse_return_started_at = 0.0
        self._browse_track_key = ""
        self._timeline_signature = None
        self._timeline_signature_source = None
        self._owner_seek_marker = None
        self._lyric_source_was_playing: bool | None = None
        self._lyric_source_app: str | None = None
        self._owner_lyric_marker = None
        self._pending_backward_seek: tuple[float, float] | None = None
        self._pending_backward_seek_app: str | None = None
        self._seek_request_source_app: str | None = None
        self._pending_lyric_offset_reanchor = False
        self._lyric_hit_rects: dict[int, QRectF] = {}
        self._hovered_lyric_index: int | None = None
        self._lyric_clip_rect = QRectF()
        self._seek_pending = False
        self._track_visual = None
        self._previous_track_visual = None
        self._track_transition_at = 0.0
        self._previous_cover_cache_key = None
        self._previous_cover_cache = QImage()
        self._metadata_marquee_states: dict[str, tuple[object, str, float]] = {}
        self._metadata_hit_rects: dict[str, QRectF] = {}
        self._metadata_overflows: dict[str, float] = {}
        self._metadata_slot_widths: dict[str, float] = {}
        self._metadata_reveal_pending: dict[str, tuple[object, str]] = {}
        self._metadata_scroll_after_hide: dict[str, tuple[object, str]] = {}
        self._metadata_hover_field = ""
        self._metadata_hover_started_at = 0.0
        self._metadata_returns: dict[str, tuple[float, float]] = {}
        self._metadata_marquee_moving = False
        self._metadata_animation_rect = QRect()
        self._metadata_next_motion_at = float("inf")
        self._metadata_draw_pixel_sizes: dict[str, float] = {}
        self._metadata_raster_cache: OrderedDict[tuple, tuple[QImage, float, float]] = OrderedDict()
        self._metadata_raster_cache_bytes = 0
        self._metadata_idle_raster_pending: deque[tuple] = deque()
        self._metadata_idle_raster_token = None
        self._metadata_metric_cache: OrderedDict[tuple, tuple[float, float, float, float]] = OrderedDict()
        self._centered_text_layout_cache: OrderedDict[tuple, tuple[QTextLayout, float]] = OrderedDict()
        self._centered_album_plan_key = None
        self._centered_album_plan_cache = None
        self._metadata_prewarm_token = None
        self._metadata_prewarm_queue: deque[tuple[str, QFont, QColor, float, object]] = deque()
        self.seekFinished.connect(self._on_seek_finished)
        self.artworkPaletteReady.connect(self._on_artwork_palette_ready)
        self._geometry_transition_snapshot = QImage()
        self._island_origin_snapshot = QImage()
        self._island_origin_geometry = QRect()
        self._island_entering = False
        self._island_returning = False
        self._capturing_transition_snapshot = False
        self._mode_animation = QPropertyAnimation(self, b"geometry", self)
        self._mode_animation.setDuration(self.MODE_TRANSITION_MS)
        self._mode_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._mode_animation.finished.connect(self._finish_mode_transition)
        self._pending_fullscreen: bool | None = None
        self._mode_transition_started_at = 0.0
        self._mode_transition_clock = MotionSimulationClock()
        self._mode_transition_elapsed = 0.0
        self._mode_transition_start = QRect()
        self._mode_transition_end = QRect()
        self._mode_transition_velocity = (0.0, 0.0, 0.0, 0.0)
        self._mode_transition_hermite = False
        self._mode_corner_start = 0.0
        self._mode_corner_end = 0.0
        self._mode_glyph_start = PRESENTATION_GLYPH_STATES["island"]
        self._mode_glyph_end = "island"
        self._mode_snapshot_control_rects: dict[str, QRectF] = {}
        self._mode_snapshot_size = QSize()
        self._mode_snapshot_title_fraction = 0.0
        self._mode_snapshot_controls_clickable = False
        self._mode_lyric_snapshot_size = QSize()
        self._mode_lyric_snapshot_track = None
        self._hidden_lyric_pose_size = QSize()
        self._hidden_lyric_pose_track = None
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        # One display-sized request cadence; the existing measured-budget
        # branch enables higher rates on displays that can sustain them.
        self._timer.setInterval(self.ANIMATION_FRAME_MS)
        self._timer.timeout.connect(self._on_animation_tick)
        # Route snapshots are cached by the provider. Read them outside paint,
        # only while visible, without attaching discovery to the lyric clock.
        self._audio_output_status_timer = QTimer(self)
        self._audio_output_status_timer.setInterval(200)
        self._audio_output_status_timer.timeout.connect(self._sync_audio_output_status)
        self._init_content_layout()
        self._init_song_collection()
        self._init_window_compact()

    @property
    def presentation_mode(self) -> str:
        return "fullscreen" if self._fullscreen else "windowed"

    def screen(self):
        # QWidget.screen() parents the shared QScreen Python wrapper to this
        # transient surface in PySide6 6.11. Application queries preserve its
        # lifetime when the surface is destroyed and retain monitor selection.
        return (QGuiApplication.screenAt(self.frameGeometry().center())
                or QGuiApplication.primaryScreen())

    def _available_geometry(self) -> QRect:
        screen = self.screen()
        if screen is None and self.owner is not None:
            try:
                screen = self.owner.screen()
            except Exception:
                screen = None
        return QRect(0, 0, 1920, 1080) if screen is None else screen.availableGeometry()

    def _default_window_geometry(self) -> QRect:
        available = self._available_geometry()
        minimum = QSize(min(self.WINDOW_MIN_WIDTH, available.width()),
                        min(self.WINDOW_HEIGHT, available.height()))
        maximum = QSize(max(minimum.width(), int(available.width() * 0.94)),
                        max(minimum.height(), int(available.height() * 0.94)))
        # First use opens a proportionate 960x540 page. The independent
        # 330x400 resize boundary must not replace the chosen default height.
        ratio = MUSIC_WINDOW_ASPECT_RATIO
        if maximum.width() < max(minimum.width(), round(minimum.height() * ratio)):
            ratio = 4.0 / 3.0
        if maximum.width() >= max(minimum.width(), round(minimum.height() * ratio)):
            size = constrained_window_size(540 * ratio, 540, minimum, maximum,
                                           aspect_ratio=ratio)
        else:
            # A very small work area cannot hold either supported default
            # ratio and the minimum height; keeping the page on-screen wins.
            size = maximum
        # QRect.center()/moveCenter() truncate negative coordinates toward
        # zero. Center from the work-area origin so left-hand monitors do
        # not accumulate an extra pixel of displacement.
        return QRect(available.x() + (available.width() - size.width()) // 2,
                     available.y() + (available.height() - size.height()) // 2,
                     size.width(), size.height())

    def _minimum_window_size(self) -> QSize:
        return QSize(self.WINDOW_MIN_WIDTH, self.WINDOW_HEIGHT)

    def show_from_island(
        self,
        *,
        fullscreen: bool | None = None,
        origin_geometry: QRect | None = None,
        origin_snapshot: QImage | None = None,
    ):
        self._cancel_mode_transition()
        self._content_layout_resize_suspended = True
        self._reset_control_idle()
        if (not self.isVisible() and self._track_visual is not None
                and self._track_visual[0] != self._visual_track_identity()):
            # A hidden page has not observed intervening songs. Prepare the
            # current track through the normal artwork/lyric handoff, without
            # capturing its old page in the new presentation's destination.
            palette = self._art_palette()
            self._track_transition(time.monotonic(), QColor(palette.primary), QColor(palette.secondary))
            self._previous_track_visual = None
        if self._closing_for_shutdown:
            self._timer.timeout.connect(self._on_animation_tick)
            self._audio_output_status_timer.timeout.connect(self._sync_audio_output_status)
            self._mode_animation.finished.connect(self._finish_mode_transition)
            self.seekFinished.connect(self._on_seek_finished)
        self._closing_for_shutdown = False
        self._sync_queue_artwork_connection()
        # Hidden surfaces stop polling. Refresh before rendering the morph's
        # destination bitmap so it cannot carry an old device/count on entry.
        self._sync_audio_output_status(settle=True)
        self._timer.start()
        if fullscreen is None:
            fullscreen = not bool(getattr(self.owner, "_music_windowed_preference", False))
        shared_geometry = getattr(self.owner, "_music_page_geometry", QRect())
        if shared_geometry.isValid():
            self._normal_geometry = QRect(shared_geometry)
        if not self._normal_geometry.isValid():
            self._normal_geometry = self._default_window_geometry()
        self._normal_geometry = self._bounded_window_geometry(self._normal_geometry)
        self._island_origin_geometry = QRect(origin_geometry) if origin_geometry is not None else QRect()
        self._island_origin_snapshot = QImage(origin_snapshot) if origin_snapshot is not None else QImage()
        target = self._fullscreen_geometry() if fullscreen else QRect(self._normal_geometry)
        self._restore_hidden_lyric_pose(target.size())
        if self._island_origin_geometry.isValid() and target.isValid():
            # Render the destination once, then only scale bitmaps during the
            # morph. The island itself remains hidden for the entire handoff.
            self.setGeometry(target)
            self._fullscreen = bool(fullscreen)
            frame = QImage(target.size(), QImage.Format.Format_ARGB32_Premultiplied)
            frame.fill(Qt.GlobalColor.transparent)
            self._capturing_transition_snapshot = True
            try:
                self.render(frame, renderFlags=QWidget.RenderFlag.DrawWindowBackground
                            | QWidget.RenderFlag.DrawChildren | QWidget.RenderFlag.IgnoreMask)
            finally:
                self._capturing_transition_snapshot = False
            self._fullscreen = False
            self._geometry_transition_snapshot = frame
            self._mode_lyric_snapshot_size = QSize(target.size())
            self._mode_lyric_snapshot_track = self._visual_track_identity()
            self._remember_transition_controls(target.size(), bool(fullscreen))
            self._island_entering = True
            self._pending_fullscreen = bool(fullscreen)
            self._mode_corner_start = self._island_corner_radius(self._island_origin_geometry)
            self._mode_corner_end = 0.0 if fullscreen else self._window_corner_radius(target.size())
            self._mode_glyph_start = PRESENTATION_GLYPH_STATES["fullscreen" if fullscreen else "windowed"]
            self._mode_glyph_end = "windowed" if fullscreen else "island"
            self.showNormal()
            self.setGeometry(self._island_origin_geometry)
            self._mode_animation.setStartValue(QRect(self._island_origin_geometry))
            self._mode_animation.setEndValue(QRect(target))
            if self.owner is not None:
                self.owner._music_windowed_preference = not bool(fullscreen)
            self._start_mode_animation()
            self.raise_()
            self.activateWindow()
            self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            return
        if fullscreen:
            self._fullscreen = True
            self.showFullScreen()
        else:
            self._fullscreen = False
            self.setGeometry(self._normal_geometry)
            self.showNormal()
        self._sync_window_control_rects()
        self._sync_surface_shape()
        self._content_layout_resize_suspended = False
        self._reset_content_layout_resize_baseline()
        if self.owner is not None:
            self.owner._music_windowed_preference = not bool(fullscreen)
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def toggle_presentation_mode(self):
        self._dismiss_audio_output_panel(immediate=True)
        # The title/artist hit areas move with this geometry handoff. Return
        # an actively hovered marquee from its current pose before resetting
        # its idle wait; ordinary automatic marquees keep their own phase.
        self._update_metadata_hover(None, time.monotonic())
        self._restore_idle_cursor()
        was_transitioning = self._pending_fullscreen is not None
        was_returning = self._island_returning
        target_fullscreen = not self._pending_fullscreen if was_transitioning else not self._fullscreen
        start = QRect(self.geometry())
        start_corner = self._surface_corner_radius()
        start_glyph = self._mode_glyph_state()
        velocity = self._current_mode_velocity()
        controls = self._transition_control_rects()
        title_fraction = self._transition_title_fraction()
        controls_clickable = self._transition_controls_clickable()
        live_snapshot = self._geometry_transition_snapshot.isNull()
        snapshot = self._capture_transition_snapshot()
        self._restore_idle_cursor()
        self._mode_animation.stop()
        self._island_entering = False
        self._island_returning = False
        if not snapshot.isNull():
            self._geometry_transition_snapshot = snapshot
            if live_snapshot:
                self._mode_lyric_snapshot_size = QSize(start.size())
                self._mode_lyric_snapshot_track = self._visual_track_identity()
            self._mode_snapshot_control_rects = controls
            self._mode_snapshot_size = start.size()
            self._mode_snapshot_title_fraction = title_fraction
            self._mode_snapshot_controls_clickable = controls_clickable

        if target_fullscreen:
            if not was_transitioning and not was_returning and not self._fullscreen and start.isValid():
                self._normal_geometry = QRect(start)
            target = self._fullscreen_geometry()
            self._fullscreen = False
        else:
            if not self._normal_geometry.isValid():
                self._normal_geometry = self._default_window_geometry()
            self._normal_geometry = self._bounded_window_geometry(self._normal_geometry)
            target = QRect(self._normal_geometry)
            self._fullscreen = False

        self._pending_fullscreen = bool(target_fullscreen)
        self._mode_corner_start = start_corner
        self._mode_corner_end = 0.0 if target_fullscreen else self._window_corner_radius(target.size())
        self._mode_glyph_start = start_glyph
        self._mode_glyph_end = "windowed" if target_fullscreen else "island"
        if not target_fullscreen:
            # Switching out of Qt full-screen can restore an arbitrary old
            # normal geometry. Put the window back at the captured frame so
            # the snapshot animation has no visible jump.
            self.showNormal()
            self.setGeometry(start)

        self._mode_animation.setStartValue(QRect(start))
        self._mode_animation.setEndValue(QRect(target))
        if self.owner is not None:
            self.owner._music_windowed_preference = not bool(target_fullscreen)
        if not target.isValid() or target == start:
            if target.isValid():
                self.setGeometry(target)
            self._finish_mode_transition()
            return
        self._start_mode_animation(velocity)
        self.update()

    def _start_mode_animation(self, velocity=None):
        """Retarget the visible bitmap without resetting its incoming motion."""
        self._mode_transition_started_at = time.monotonic()
        self._mode_transition_clock.reset(self._mode_transition_started_at)
        self._mode_transition_elapsed = 0.0
        self._mode_transition_start = QRect(self._mode_animation.startValue())
        self._mode_transition_end = QRect(self._mode_animation.endValue())
        self._mode_transition_velocity = tuple(velocity or (0.0, 0.0, 0.0, 0.0))
        self._mode_transition_hermite = any(abs(value) > 0.01 for value in self._mode_transition_velocity)
        if self._mode_transition_hermite:
            # Qt retains ownership of geometry and completion. These keys
            # follow a Hermite curve with incoming velocity and a resting
            # endpoint, including a natural brake when the target reverses.
            self._mode_animation.setEasingCurve(QEasingCurve.Type.Linear)
            self._mode_animation.setKeyValues([
                (index / 68.0, self._mode_geometry_at(index / 68.0))
                for index in range(69)
            ])
        else:
            self._mode_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
            self._mode_animation.setKeyValues([
                (0.0, QRect(self._mode_transition_start)),
                (1.0, QRect(self._mode_transition_end)),
            ])
        self._mode_animation.start()
        self._mode_animation.pause()
        self._mode_animation.setCurrentTime(0)
        self._sync_surface_shape()
        self._timer.setInterval(self._transition_frame_interval())

    def _transition_frame_interval(self) -> int:
        screen = self.screen()
        try:
            refresh = float(screen.refreshRate()) if screen is not None else 60.0
        except (TypeError, ValueError, RuntimeError):
            refresh = 60.0
        if not math.isfinite(refresh) or refresh <= 0.0:
            refresh = 60.0
        return max(self.EMPHASIS_FRAME_MS, min(16, int(round(1000.0 / refresh))))

    def _advance_mode_transition(self, now: float):
        if self._mode_animation.state() != QAbstractAnimation.State.Paused:
            return
        self._mode_transition_elapsed += self._mode_transition_clock.advance(now)
        elapsed_ms = int(round(self._mode_transition_elapsed * 1000.0))
        self._mode_animation.setCurrentTime(min(self.MODE_TRANSITION_MS, elapsed_ms))
        self._sync_surface_shape()
        self.update()

    @staticmethod
    def _mode_rect_components(rect: QRect):
        return float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height())

    def _mode_geometry_at(self, fraction: float) -> QRect:
        phase = max(0.0, min(1.0, float(fraction)))
        duration = self.MODE_TRANSITION_MS / 1000.0
        values = []
        for start, end, velocity in zip(
            self._mode_rect_components(self._mode_transition_start),
            self._mode_rect_components(self._mode_transition_end),
            self._mode_transition_velocity,
        ):
            delta, tangent = end - start, velocity * duration
            values.append(int(round(start + tangent * phase
                                    + (3.0 * delta - 2.0 * tangent) * phase * phase
                                    + (tangent - 2.0 * delta) * phase * phase * phase)))
        return QRect(values[0], values[1], max(1, values[2]), max(1, values[3]))

    def _current_mode_velocity(self):
        if self._mode_animation.state() != QAbstractAnimation.State.Paused:
            return (0.0, 0.0, 0.0, 0.0)
        duration = self.MODE_TRANSITION_MS / 1000.0
        phase = self._mode_animation.currentTime() / max(1, self.MODE_TRANSITION_MS)
        velocities = []
        for start, end, velocity in zip(
            self._mode_rect_components(self._mode_transition_start),
            self._mode_rect_components(self._mode_transition_end),
            self._mode_transition_velocity,
        ):
            delta = end - start
            if self._mode_transition_hermite:
                tangent = velocity * duration
                value = (tangent + 2.0 * (3.0 * delta - 2.0 * tangent) * phase
                         + 3.0 * (tangent - 2.0 * delta) * phase * phase) / duration
            else:
                value = delta / duration * 12.0 * min(phase, 1.0 - phase) ** 2
            velocities.append(value)
        return tuple(velocities)

    def _remember_transition_controls(self, size: QSize, fullscreen: bool):
        self._mode_snapshot_control_rects = {
            key: QRectF(rect) for key, rect in self._button_rects.items()
        }
        self._mode_snapshot_size = QSize(size)
        self._mode_snapshot_title_fraction = 0.0 if fullscreen else self.TITLE_BAR_HEIGHT / max(1, size.height())
        self._mode_snapshot_controls_clickable = self._control_reveal_at(time.monotonic()) >= 0.75
        self._mode_snapshot_dj_hit_path = self._personal_dj_hit_path(QRectF(0, 0, size.width(), size.height()), time.monotonic())

    def _transition_control_rects(self):
        if self._geometry_transition_snapshot.isNull() or not self._mode_snapshot_size.isValid():
            return {key: QRectF(rect) for key, rect in self._button_rects.items()}
        scale_x = self.width() / max(1, self._mode_snapshot_size.width())
        scale_y = self.height() / max(1, self._mode_snapshot_size.height())
        return {
            key: QRectF(rect.x() * scale_x, rect.y() * scale_y,
                        rect.width() * scale_x, rect.height() * scale_y)
            for key, rect in self._mode_snapshot_control_rects.items()
        }

    def _transition_title_fraction(self):
        if not self._geometry_transition_snapshot.isNull():
            return self._mode_snapshot_title_fraction
        return 0.0 if self._fullscreen else self.TITLE_BAR_HEIGHT / max(1, self.height())

    def _transition_controls_clickable(self):
        if not self._geometry_transition_snapshot.isNull():
            return self._mode_snapshot_controls_clickable
        return self._control_reveal_at(time.monotonic()) >= 0.75

    def _transition_window_control_contains(self, key: str, rect: QRectF, point: QPointF) -> bool:
        if key in {"library", "personal_dj"} and not self._geometry_transition_snapshot.isNull() and self._mode_snapshot_size.isValid():
            captured = self._mode_snapshot_control_rects.get(key)
            if captured is None:
                return False
            # The captured circle/capsule may be stretched differently on
            # each axis during a mode transition. Test in snapshot space.
            point = QPointF(point.x() * self._mode_snapshot_size.width() / max(1, self.width()),
                            point.y() * self._mode_snapshot_size.height() / max(1, self.height()))
            rect = captured
            if key == "personal_dj" and not self._mode_snapshot_dj_hit_path.isEmpty():
                return self._mode_snapshot_dj_hit_path.contains(point)
        return self._window_control_contains(key, rect, point)

    def _capture_transition_snapshot(self) -> QImage:
        """Capture exactly one rendered frame before a geometry transition."""
        if self._capturing_transition_snapshot or self.width() <= 0 or self.height() <= 0:
            return QImage()
        self._capturing_transition_snapshot = True
        try:
            # Keep the backing bitmap rectangular. The live, interpolated
            # shell supplies clipping, so rounded source corners cannot grow
            # into large transparent holes on the way to full screen.
            dpr = max(1.0, self.devicePixelRatioF())
            frame = QImage(round(self.width()*dpr), round(self.height()*dpr),
                           QImage.Format.Format_ARGB32_Premultiplied)
            frame.setDevicePixelRatio(dpr)
            frame.fill(Qt.GlobalColor.transparent)
            self.render(frame, renderFlags=QWidget.RenderFlag.DrawWindowBackground
                        | QWidget.RenderFlag.DrawChildren | QWidget.RenderFlag.IgnoreMask)
            return frame
        finally:
            self._capturing_transition_snapshot = False

    def _cancel_mode_transition(self):
        self._reset_window_compact()
        self._mode_animation.stop()
        self._mode_transition_started_at = 0.0
        self._mode_transition_elapsed = 0.0
        self._mode_snapshot_control_rects.clear()
        self._mode_snapshot_size = QSize()
        self._mode_snapshot_title_fraction = 0.0
        self._mode_snapshot_controls_clickable = False
        self._mode_lyric_snapshot_size = QSize()
        self._mode_lyric_snapshot_track = None
        self._pending_fullscreen = None
        self._geometry_transition_snapshot = QImage()
        self._island_entering = False
        self._island_returning = False
        self._capturing_transition_snapshot = False
        self._content_layout_resize_suspended = False
        if hasattr(self, "_content_layout"):
            self._reset_content_layout_resize_baseline()

    def _fullscreen_geometry(self) -> QRect:
        screen = self.screen()
        return QRect(screen.geometry()) if screen is not None else QRect(self._available_geometry())

    def _finish_mode_transition(self):
        if self._island_returning:
            self._hidden_lyric_pose_size = QSize(self._mode_lyric_snapshot_size)
            self._hidden_lyric_pose_track = self._mode_lyric_snapshot_track
            self._cancel_mode_transition()
            callback = getattr(self.owner, "_restore_island_from_music_surface", None)
            if callable(callback):
                callback()
            else:
                self.hide()
            return
        if self._pending_fullscreen is None:
            return
        target = self._pending_fullscreen
        if target is True:
            self._fullscreen = True
            self._reset_control_idle()
            self.showFullScreen()
        elif target is False:
            self._fullscreen = False
            self._reset_control_idle()
            if self.geometry().isValid():
                self._normal_geometry = QRect(self.geometry())
        self._rebase_snapshot_lyric_motion()
        self._pending_fullscreen = None
        self._mode_transition_started_at = 0.0
        self._mode_transition_elapsed = 0.0
        self._mode_snapshot_control_rects.clear()
        self._mode_snapshot_size = QSize()
        self._mode_snapshot_title_fraction = 0.0
        self._mode_snapshot_controls_clickable = False
        self._mode_lyric_snapshot_size = QSize()
        self._mode_lyric_snapshot_track = None
        self._geometry_transition_snapshot = QImage()
        self._island_entering = False
        self._content_layout_resize_suspended = False
        self._reset_content_layout_resize_baseline()
        self._sync_window_control_rects()
        self._sync_surface_shape()
        self.update()

    def _finish_stale_track_transition(self):
        if (self._geometry_transition_snapshot.isNull() or self._mode_lyric_snapshot_track is None
                or self._mode_lyric_snapshot_track == self._visual_track_identity()):
            return
        # Finish the chosen destination when its frozen bitmap belongs to
        # the preceding song. Cancelling midway would strand its geometry.
        target = self._mode_animation.endValue()
        self._mode_animation.stop()
        if isinstance(target, QRect) and target.isValid():
            self.setGeometry(target)
        self._finish_mode_transition()

    def _rebase_snapshot_lyric_motion(self):
        """Make the live lyric pose inherit the bitmap's final local scale."""
        source = self._mode_lyric_snapshot_size
        spring = self._logical_line_spring
        if (not source.isValid() or not spring.positions
                or self._mode_lyric_snapshot_track != self._visual_track_identity()):
            return
        scale = self.height() / max(1, source.height())
        spring.rescale(scale, time.monotonic())

    def _restore_hidden_lyric_pose(self, target: QSize):
        source = self._hidden_lyric_pose_size
        track = self._hidden_lyric_pose_track
        self._hidden_lyric_pose_size = QSize()
        self._hidden_lyric_pose_track = None
        if (source.isValid() and target.isValid()
                and track == self._visual_track_identity()):
            self._logical_line_spring.rescale(target.height() / max(1, source.height()), time.monotonic())

    def return_to_island(self):
        self._dismiss_audio_output_panel(immediate=True)
        if self._island_returning:
            return
        self._restore_idle_cursor()
        # Do not remember an in-between animation rectangle as the next
        # windowed size when the surface is closed during a transition.
        if self._pending_fullscreen is None and not self._island_returning and not self._fullscreen and self.geometry().isValid():
            self._normal_geometry = QRect(self._compact_base_geometry if self._compact_base_geometry.isValid() else self.geometry())
        start = QRect(self.geometry())
        start_corner = self._surface_corner_radius()
        start_glyph = self._mode_glyph_state()
        velocity = self._current_mode_velocity()
        controls = self._transition_control_rects()
        title_fraction = self._transition_title_fraction()
        controls_clickable = self._transition_controls_clickable()
        lyric_snapshot_size = (
            QSize(start.size()) if self._geometry_transition_snapshot.isNull()
            else QSize(self._mode_lyric_snapshot_size)
        )
        lyric_snapshot_track = (
            self._visual_track_identity() if self._geometry_transition_snapshot.isNull()
            else self._mode_lyric_snapshot_track
        )
        snapshot = self._capture_transition_snapshot()
        self._restore_idle_cursor()
        self._cancel_mode_transition()
        target = QRect(self._island_origin_geometry)
        current_target = getattr(self.owner, "_current_main_target_global_rect", None)
        if callable(current_target):
            try:
                updated = current_target().toAlignedRect()
                if updated.isValid():
                    target = QRect(updated)
            except (AttributeError, RuntimeError, TypeError):
                pass
        if self.isVisible() and target.isValid() and start.isValid() and not snapshot.isNull():
            self._island_returning = True
            self._mode_corner_start = start_corner
            self._mode_corner_end = self._island_corner_radius(target)
            self._mode_glyph_start = start_glyph
            self._mode_glyph_end = "fullscreen" if not bool(getattr(self.owner, "_music_windowed_preference", False)) else "windowed"
            self._geometry_transition_snapshot = snapshot
            self._mode_lyric_snapshot_size = lyric_snapshot_size
            self._mode_lyric_snapshot_track = lyric_snapshot_track
            self._mode_snapshot_control_rects = controls
            self._mode_snapshot_size = start.size()
            self._mode_snapshot_title_fraction = title_fraction
            self._mode_snapshot_controls_clickable = controls_clickable
            self.showNormal()
            self.setGeometry(start)
            self._fullscreen = False
            self._mode_animation.setStartValue(start)
            self._mode_animation.setEndValue(target)
            self._start_mode_animation(velocity)
            return
        callback = getattr(self.owner, "_restore_island_from_music_surface", None)
        if callable(callback):
            callback()
        else:
            self.hide()

    def _library_back_available(self) -> bool:
        if self._library_login_required():
            return callable(getattr(self.owner, "_music_login_callback", None))
        callback = getattr(self.owner, "_music_library_back_callback", None)
        active = getattr(getattr(self.owner, "data_provider", None), "_owned_active", None)
        return callable(callback) and (getattr(self, "_library_loading", None) is not None
                                      or callable(active) and bool(active()))

    def _library_login_required(self) -> bool:
        getter = getattr(self.owner, "_music_login_required", None)
        return bool(getter()) if callable(getter) else False

    def return_to_library(self) -> bool:
        """Request account navigation without issuing a playback command."""
        if not self._library_back_available():
            return False
        self._dismiss_audio_output_panel(immediate=True)
        self._restore_idle_cursor()
        callback = self.owner._music_login_callback if self._library_login_required() else self.owner._music_library_back_callback
        callback()
        return True

    def hideEvent(self, event):
        self._reset_window_compact()
        self._dismiss_song_playlist_panel()
        self._disconnect_queue_artwork_signal()
        self._layout_animation.stop()
        self._layout_edge_animation.stop()
        self._layout_edge_fraction = 1.
        self._finish_content_layout()
        self._update_metadata_hover(None, time.monotonic())
        self._dismiss_audio_output_panel(immediate=True)
        self._audio_output_status_timer.stop()
        self._audio_output_motion.settle(self._audio_output_membership.current, time.monotonic())
        if not self._hidden_lyric_pose_size.isValid():
            if self._mode_lyric_snapshot_size.isValid():
                self._hidden_lyric_pose_size = QSize(self._mode_lyric_snapshot_size)
                self._hidden_lyric_pose_track = self._mode_lyric_snapshot_track
            else:
                self._hidden_lyric_pose_size = QSize(self.size())
                snapshot_track = self._last_visible_lyric_snapshot_track
                self._hidden_lyric_pose_track = None if snapshot_track is None else snapshot_track[0]
        if self._mode_animation.state() != QAbstractAnimation.State.Stopped:
            self._cancel_mode_transition()
        self._timer.stop()
        self._reset_control_idle()
        super().hideEvent(event)

    def resizeEvent(self, event):
        # The popup is anchored in the surface's current coordinate space.
        # Close it before resizing rather than leave hit areas off-screen.
        self._dismiss_audio_output_panel(immediate=True)
        self._sync_surface_shape()
        if hasattr(self, "_content_layout"):
            self._resize_content_layout(user_resize=self._content_layout_user_resizing)
        super().resizeEvent(event)

    @staticmethod
    def _album_art_radius(side: float) -> float:
        return max(0.0, float(side))*0.055

    def _window_corner_radius(self, size: QSize | None = None) -> float:
        size = self.size() if size is None else size
        scale = max(0.01, size.height()/self.REFERENCE_SURFACE_HEIGHT)
        artwork_side = max(1.0, min(404.0*scale, size.height()-238.0*scale))
        return self._album_art_radius(artwork_side)

    def _island_corner_radius(self, geometry: QRect) -> float:
        edge_notch = getattr(self.owner, "_edge_notch_active", None)
        if callable(edge_notch) and edge_notch():
            return 0.0  # The top-anchored island already supplies its own alpha silhouette.
        radius = getattr(self.owner, "_music_shell_radius", None)
        if callable(radius):
            return max(0.0, float(radius(QRectF(geometry), bool(getattr(self.owner, "expanded", False)))))
        return max(0.0, min(geometry.width(), geometry.height())*0.5)

    def _surface_corner_radius(self) -> float:
        if bool(getattr(self, "_capturing_transition_snapshot", False)):
            return 0.0
        if (getattr(self, "_pending_fullscreen", None) is not None
                or bool(getattr(self, "_island_returning", False))):
            progress = self._mode_animation.currentTime()/max(1, self.MODE_TRANSITION_MS)
            progress = progress*progress*(3.0-2.0*progress)
            return self._mode_corner_start+(self._mode_corner_end-self._mode_corner_start)*progress
        return 0.0 if bool(getattr(self, "_fullscreen", False)) else self._window_corner_radius()

    def _surface_shape_path(self) -> QPainterPath:
        path = QPainterPath()
        radius = min(self._surface_corner_radius(), self.width()*0.5, self.height()*0.5)
        if radius > 0.001:
            path.addRoundedRect(QRectF(self.rect()), radius, radius)
        else:
            path.addRect(QRectF(self.rect()))
        return path

    def _mode_glyph_state(self) -> tuple[float, float]:
        if self._pending_fullscreen is None and not self._island_returning:
            return PRESENTATION_GLYPH_STATES["windowed" if self._fullscreen else "island"]
        fraction = self._mode_animation.currentTime()/max(1, self.MODE_TRANSITION_MS)
        fraction = fraction*fraction*(3.0-2.0*fraction)
        return tuple(a+(b-a)*fraction for a, b in
                     zip(self._mode_glyph_start, PRESENTATION_GLYPH_STATES[self._mode_glyph_end]))

    def _paint_transition_mode_glyph(self, painter: QPainter) -> None:
        if self._capturing_transition_snapshot:
            return
        buttons = self._transition_control_rects()
        button = buttons.get("mode", buttons.get("return", QRectF()))
        if button.isEmpty():
            return
        side = button.width()*0.60
        glyph = QRectF(button.center().x()-side/2.0, button.center().y()-side/2.0, side, side)
        painter.save()
        painter.setOpacity(self._control_reveal_at(time.monotonic())**0.75)
        paint_presentation_glyph(painter, glyph, self._mode_glyph_end, self._scene_control_ink(),
                                 previous=self._mode_glyph_state(), progress=0.0)
        painter.restore()

    def _sync_surface_shape(self) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return
        if self._surface_corner_radius() <= 0.001:
            self.clearMask()
        else:
            # The native hit region must include the antialiased fringe. An
            # exact binary region cuts off partial-alpha pixels at the curve.
            path = QPainterPath()
            radius = self._surface_corner_radius()
            path.addRoundedRect(QRectF(self.rect()).adjusted(-2, -2, 2, 2), radius, radius)
            self.setMask(QRegion(path.toFillPolygon().toPolygon()).intersected(QRegion(self.rect())))

    def _apply_surface_edge_alpha(self, painter: QPainter) -> None:
        """Cache corner coverage and, when needed, one-pixel edge fringes."""
        radius = min(self._surface_corner_radius(), self.width()*0.5, self.height()*0.5)
        if radius <= 0.001:
            return
        dpr = max(1.0, float(painter.device().devicePixelRatioF()))
        key = (self.width(), self.height(), radius, dpr)
        cached = getattr(self, "_surface_edge_alpha_cache", None)
        if cached is None or cached[0] != key:
            side = int(math.ceil(radius*dpr)) + 2
            pixel_width = int(math.ceil(self.width()*dpr))
            pixel_height = int(math.ceil(self.height()*dpr))
            # Partition tiny transition surfaces at integer physical pixels so
            # the four patches never multiply coverage in overlapping areas.
            left = min(side, (pixel_width + 1)//2)
            right = min(side, pixel_width//2)
            top = min(side, (pixel_height + 1)//2)
            bottom = min(side, pixel_height//2)
            patches = [(0, 0, left, top), (pixel_width-right, 0, right, top),
                       (0, pixel_height-bottom, left, bottom),
                       (pixel_width-right, pixel_height-bottom, right, bottom)]
            # An odd logical extent at fractional DPR can end inside a pixel.
            # Preserve that same coverage along the straight edge between the
            # corners, rather than make it jump to opaque at each patch seam.
            if pixel_width - self.width()*dpr > 0.001:
                patches.append((pixel_width-1, top, 1, pixel_height-top-bottom))
            if pixel_height - self.height()*dpr > 0.001:
                patches.append((left, pixel_height-1, pixel_width-left-right, 1))
            images, origins = [], []
            for x, y, width, height in patches:
                if width <= 0 or height <= 0:
                    continue
                mask = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
                mask.fill(Qt.GlobalColor.transparent)
                with QPainter(mask) as edge:
                    edge.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    edge.translate(-x, -y)
                    edge.scale(dpr, dpr)
                    edge.setPen(Qt.PenStyle.NoPen)
                    edge.setBrush(Qt.GlobalColor.white)
                    edge.drawRoundedRect(QRectF(self.rect()), radius, radius)
                mask.setDevicePixelRatio(dpr)
                images.append(mask)
                origins.append(QPointF(x/dpr, y/dpr))
            # A fractional-DPR right/bottom corner has its own pixel phase.
            # Mirroring the top-left patch would shift its antialiased fringe.
            cached = (key, tuple(images), tuple(origins))
            self._surface_edge_alpha_cache = cached
        painter.save()
        painter.setClipping(False)
        painter.setOpacity(1.0)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        for image, origin in zip(cached[1], cached[2]):
            painter.drawImage(origin, image)
        painter.restore()

    def showEvent(self, event):
        self._sync_queue_artwork_connection()
        self._resize_content_layout()
        self._sync_surface_shape()
        self._reset_control_idle()
        self._sync_audio_output_status(settle=True)
        if not self.isMinimized():
            self._audio_output_status_timer.start()
            self._timer.start()
        super().showEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if self.isActiveWindow():
            self._note_pointer_moved()

    def focusOutEvent(self, event):
        self._restore_idle_cursor()
        super().focusOutEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowDeactivate:
            self._restore_idle_cursor()
            if hasattr(self, "_compact_animation") and not self._compact_snapshot.isNull() and not self._compact_committing:
                self._moving = False
                self._resize_edges = set()
                self._release_window_compact()
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self._timer.stop()
                self._audio_output_status_timer.stop()
            elif self.isVisible() and not self._closing_for_shutdown:
                self._timer.start()
                self._audio_output_status_timer.start()
            sync = getattr(self.owner, "_sync_minimum_surface", None)
            if callable(sync):
                # Qt can deliver this before the final show/hide state settles.
                QTimer.singleShot(0, sync)

    def _restore_idle_cursor(self):
        if not getattr(self, "_idle_cursor_hidden", False):
            return
        self._idle_cursor_hidden = False
        cursor = self._idle_cursor_restore_cursor
        self._idle_cursor_restore_cursor = None
        if cursor is None:
            self.unsetCursor()
        else:
            self.setCursor(cursor)

    def _sync_idle_cursor(self):
        hide = (
            self._fullscreen
            and self._control_reveal_target <= 0.0
            and self.isVisible()
            and self.isActiveWindow()
            and self.underMouse()
            and self._pending_fullscreen is None
            and not self._island_returning
            and not self._capturing_transition_snapshot
        )
        if not hide:
            self._restore_idle_cursor()
        elif not self._idle_cursor_hidden:
            # Keep the blank cursor local to this widget. An application-wide
            # override can leak into the island, settings, or another app's
            # activation path, and would require balancing a global stack.
            self._idle_cursor_restore_cursor = (
                QCursor(self.cursor())
                if self.testAttribute(Qt.WidgetAttribute.WA_SetCursor) else None
            )
            self._idle_cursor_hidden = True
            self.setCursor(Qt.CursorShape.BlankCursor)

    def _reset_control_idle(self, now: float | None = None):
        self._restore_idle_cursor()
        instant = time.monotonic() if now is None else float(now)
        self._transport_feedback.clear()
        self._transport_light_dirty_regions.clear()
        self._transport_light_ink_regions.clear()
        self._transport_light_origins.clear()
        self._control_last_motion_at = instant
        self._control_transition_started_at = instant
        self._control_reveal_from = 1.0
        self._control_reveal_target = 1.0
        self._control_last_tick_reveal = 1.0
        self._reset_control_hover(instant)
        self._reset_library_expansion(instant)
        if self._hovered_window_button == "library":
            self._hovered_window_button = ""
        self._control_layer_next_prewarm_at = 0.0
        self._control_prewarm_library_available = self._library_back_available()
        self._control_prewarm_remaining = (2 + int(self._control_prewarm_library_available)) * (len(self.CONTROL_BLUR_FRACTIONS) - 1)
        self._control_prewarm_requested = False
        self._metadata_reveal_pending.clear()
        self._metadata_scroll_after_hide.clear()

    def _reset_control_hover(self, now: float | None = None):
        self._control_hover_motions.clear()

    def _set_control_hover(self, window_key: str, media_key: str, now: float, *, arm_selector_leave: bool = True):
        """Only the top-left return grows; menu opening belongs to clicks."""
        targets = {"library"} if window_key == "library" else set()
        for key in targets | self._control_hover_motions.keys():
            if key not in self._control_hover_motions:
                self._control_hover_motions[key] = HoverMotion()
            self._control_hover_motions[key].set_hovered(key in targets, now)
        if window_key == "personal_dj" and arm_selector_leave:
            self._dj_voice_selector_close_timer.stop()
        elif arm_selector_leave and self._dj_voice_selector_active and not self._dj_voice_selector_pointer:
            if not self._dj_voice_selector_close_timer.isActive():
                self._dj_voice_selector_close_timer.start()

    def _hover_control_rect(self, key: str, button: QRectF, group: dict[str, QRectF], now: float) -> QRectF:
        if key != "library":
            return QRectF(button)
        # Retain one exact hover pose through shared blur/hide, avoiding a
        # newly rasterized blurred layer on every exit frame.
        instant = (self._control_transition_started_at if self._control_reveal_target < 1.0 else now)
        own = self._control_hover_motions.get(key)
        factor = hover_scale(own.value(instant) if own is not None else 0.0)
        if factor == 1.0:
            return QRectF(button)
        center = button.center()
        result = QRectF(center.x() - button.width() * factor / 2,
                        center.y() - button.height() * factor / 2,
                        button.width() * factor, button.height() * factor)
        return result

    @staticmethod
    def _control_rect_transform(base: QRectF, posed: QRectF) -> QTransform:
        transform = QTransform()
        transform.translate(posed.center().x(), posed.center().y())
        transform.scale(posed.width() / max(.001, base.width()), posed.height() / max(.001, base.height()))
        transform.translate(-base.center().x(), -base.center().y())
        return transform

    def _transport_hover_group(self, panel: QRectF) -> dict[str, QRectF]:
        slot = panel.width() / 3.0
        return {"media:" + command: QRectF(panel.left() + index * slot, panel.top(), slot, panel.height())
                for index, command in enumerate(("previous", "toggle_play_pause", "next"))}

    def _top_control_hover_group(self, rect: QRectF) -> dict[str, QRectF]:
        base, scale = self._window_control_base_rect(rect), self._surface_scale(rect)
        group = {"mode" if self._fullscreen else "return": base,
                 "translation": self._translation_button_rect(base, scale)}
        if self._owned_playback_mode is not None:
            group["playback_mode"] = self._playback_mode_button_rect(base, scale)
        group["content_layout"] = self._content_layout_button_rect(base, scale)
        if self._audio_output_available():
            group["audio_output"] = self._audio_output_button_rect(base, scale)
        return group

    def _control_hover_pose_key(self, group: dict[str, QRectF], now: float) -> tuple:
        return tuple((key, tuple(round(value, 4) for value in (pose.x(), pose.y(), pose.width(), pose.height())))
                     for key, button in group.items()
                     for pose in (self._hover_control_rect(key, button, group, now),) if pose != button)

    def _notify_dj_voice_selector(self, visible: bool):
        callback = getattr(self.owner, "_personal_dj_voice_hover_callback", None)
        if callable(callback):
            widget = callback(self, visible)
            if isinstance(widget, QWidget):
                self._dj_voice_selector_widget = widget

    def _open_dj_voice_selector(self, now: float):
        if self._owned_personal_dj_enabled is None or not callable(getattr(self.owner, "_personal_dj_voice_hover_callback", None)):
            return
        self._dismiss_audio_output_panel()
        self._dismiss_song_playlist_panel()
        self._dj_voice_selector_active = True
        self._dj_voice_selector_motion.set_hovered(True, now)
        self._dj_voice_selector_close_timer.stop()
        self._notify_dj_voice_selector(True)
        self.update(self._personal_dj_damage_rect(QRectF(self.rect())))

    def _set_dj_voice_selector_hover(self, hovered: bool):
        """Called by the child selector while the pointer traverses its caption."""
        self._dj_voice_selector_pointer = bool(hovered)
        if hovered:
            self._dj_voice_selector_close_timer.stop()
        elif self._dj_voice_selector_active and not self._dj_voice_selector_close_timer.isActive():
            self._dj_voice_selector_close_timer.start()

    def _dj_voice_selector_contains_pointer(self) -> bool:
        position = QCursor.pos()
        widget = self._dj_voice_selector_widget
        if widget is not None and widget.isVisible() and widget.rect().contains(widget.mapFromGlobal(position)):
            return True
        # The connecting corridor lets the pointer cross from the large icon
        # to the caption while keeping the same two-second leave deadline.
        bounds = self._personal_dj_button_rect(QRectF(self.rect()))
        bounds.translate(self._personal_dj_control_shift(QRectF(self.rect()), self._personal_dj_reveal_at(time.monotonic())))
        return bounds.adjusted(-6, -6, 6, 6).contains(QPointF(self.mapFromGlobal(position)))

    def _check_dj_voice_selector_leave(self):
        if (self._dj_voice_selector_active and not self._dj_voice_selector_pointer
                and not self._dj_voice_selector_contains_pointer()):
            # A child caption can retain the parent's last icon hover key.
            # The actual pointer, rather than that stale key, owns departure.
            if self._hovered_window_button == "personal_dj":
                self._hovered_window_button = ""
            self._close_dj_voice_selector()

    def _close_dj_voice_selector(self, *, immediate: bool = False):
        was_active = self._dj_voice_selector_active
        self._dj_voice_selector_active = self._dj_voice_selector_pointer = False
        self._dj_voice_selector_close_timer.stop()
        self._dj_voice_selector_motion.set_hovered(False, time.monotonic())
        if immediate:
            self._dj_voice_selector_motion = HoverMotion()
        if was_active:
            self._notify_dj_voice_selector(False)
            self.update(self._personal_dj_damage_rect(QRectF(self.rect())))

    @staticmethod
    def _control_spring_progress(progress: float) -> float:
        """A non-overshooting spring settle; positions move without squashing ink."""
        phase = max(0.0, min(1.0, float(progress)))
        if phase >= 1.0:
            return 1.0
        response = 1.0 - (1.0 + 3.2 * phase) * math.exp(-3.2 * phase)
        normalization = 1.0 - 4.2 * math.exp(-3.2)
        return response / normalization

    def _control_reveal_at(self, now: float) -> float:
        """Return 1 for visible controls and 0 after mode-specific pointer idling."""
        instant = float(now)
        panel = getattr(self, "_audio_output_panel", None)
        collection = getattr(self, "_song_playlist_panel", None)
        if ((panel is not None and panel.isVisible())
                or (collection is not None and collection.isVisible())):
            # The chooser owns the source control while unfolding or closing.
            # Keep its neighbours and metadata in that same visible layout;
            # dismissed re-arms the ordinary idle timer through its callback.
            self._control_last_motion_at = instant
            self._control_reveal_from = self._control_reveal_target = 1.0
            self._sync_library_motion_reveal(1.0, instant)
            self._sync_idle_cursor()
            return 1.0
        if self._dj_voice_selector_active:
            self._control_last_motion_at = instant
            self._control_reveal_from = self._control_reveal_target = 1.0
            self._sync_library_motion_reveal(1.0, instant)
            self._sync_idle_cursor()
            return 1.0
        idle_seconds = (
            self.FULLSCREEN_CONTROL_IDLE_SECONDS if self._fullscreen
            else self.WINDOWED_CONTROL_IDLE_SECONDS
        )
        idle_at = self._control_last_motion_at + idle_seconds
        if self._control_reveal_target > 0.0 and instant >= idle_at:
            self._control_reveal_from = 1.0
            self._control_reveal_target = 0.0
            self._control_transition_started_at = idle_at
            self._sync_library_motion_reveal(0.5, idle_at)
            self._set_library_expanded(False, idle_at)
            self._pressed_window_button = ""
            self._pressed_media_command = ""
            if self._hovered_window_button or self._hovered_media_command:
                self._hovered_window_button = ""
                self._hovered_media_command = ""
                self._set_control_hover("", "", idle_at)
                self.unsetCursor()
        self._sync_idle_cursor()
        duration = max(0.001, self.FULLSCREEN_CONTROL_TRANSITION_SECONDS)
        progress = (instant - self._control_transition_started_at) / duration
        eased = self._control_spring_progress(progress)
        reveal = self._control_reveal_target if progress >= 1.0 - 1e-9 else max(0.0, min(1.0, self._control_reveal_from + (
            self._control_reveal_target - self._control_reveal_from
        ) * eased))
        self._sync_library_motion_reveal(reveal, instant)
        return reveal

    def _note_pointer_moved(self, now: float | None = None):
        # An inactive window must not reveal its controls merely because the
        # global pointer crosses it. Focusing the surface re-enables reveal.
        if not self._fullscreen and not self.isActiveWindow():
            self._restore_idle_cursor()
            return
        instant = time.monotonic() if now is None else float(now)
        reveal = self._control_reveal_at(instant)
        self._control_last_motion_at = instant
        if self._control_reveal_target < 1.0:
            self._metadata_controls_revealing()
            self._control_reveal_from = reveal
            self._control_reveal_target = 1.0
            self._control_transition_started_at = instant
            self.update()
        self._restore_idle_cursor()

    def _controls_animating(self, now: float) -> bool:
        return (
            float(now) - self._control_transition_started_at
            <= self.FULLSCREEN_CONTROL_TRANSITION_SECONDS + self.ANIMATION_FRAME_MS * 0.002
        ) and self._control_reveal_from != self._control_reveal_target

    def _update_control_animation_regions(self, now: float, *, force: bool = False):
        reveal = self._control_reveal_at(now)
        if reveal <= .001 and self._control_hover_motions:
            self._reset_control_hover(now)
        self._set_control_hover(self._hovered_window_button, self._hovered_media_command, now, arm_selector_leave=False)
        hover_changed = any(motion.is_animating(now) for motion in self._control_hover_motions.values())
        if self._dj_voice_selector_active:
            self._notify_dj_voice_selector(True)
        self._update_metadata_control_state(now, reveal)
        changed = abs(reveal - self._control_last_tick_reveal) > 0.001
        self._control_last_tick_reveal = reveal
        expansion = self._library_expansion_at(now)
        library_changed = abs(expansion - self._library_last_tick_expansion) > 0.001
        self._library_last_tick_expansion = expansion
        collection_changed = self._update_song_collection_animation(now)
        shared_damage = force or self._controls_animating(now) or changed or hover_changed or collection_changed
        if not shared_damage and not library_changed:
            return
        rect = QRectF(self.rect())
        scale = self._surface_scale(rect)
        if self.isVisible() and self._library_back_available():
            self.update(self._library_damage_rect(rect))
        if not shared_damage:
            return
        self.update(QRectF(rect.right() - 420.0 * scale, 0.0, 420.0 * scale, 105.0 * scale).toAlignedRect())
        bottom_height = self.CONTROL_BOTTOM_REGION_HEIGHT * scale
        self.update(QRectF(0.0, rect.bottom() - bottom_height, 505.0 * scale, bottom_height).toAlignedRect())
        collection = self._song_collection_geometry(rect)
        if collection.isValid():
            self.update(collection.adjusted(-3., -3., 3., 3.).toAlignedRect())
        if self._cover_layout_from > 0.0 or self._cover_layout_to > 0.0:
            original, _centered = self._cover_layout_endpoints(now)
            plan = self._centered_album_plan(original)
            center_group = plan["idle_cover"].united(plan["shown_cover"])
            for cover in (plan["idle_cover"], plan["shown_cover"]):
                title_row, artist_row, transport_row = self._centered_group_rects(cover, plan)
                center_group = center_group.united(title_row).united(artist_row).united(transport_row)
            self.update(center_group.adjusted(-4.0, -4.0, 4.0, 4.0).intersected(rect).toAlignedRect())

    @classmethod
    def _transport_feedback_frame(cls, elapsed: float, duration: float) -> tuple[float, float, float] | None:
        """Return icon scale, ripple radius fraction and light opacity for a click."""
        if duration <= 0.0 or elapsed >= duration:
            return None
        total_seconds = cls.TRANSPORT_LIGHT_EXPAND_SECONDS + cls.TRANSPORT_LIGHT_FADE_SECONDS
        icon_seconds = duration * cls.TRANSPORT_FEEDBACK_SECONDS / total_seconds
        expand_seconds = duration * cls.TRANSPORT_LIGHT_EXPAND_SECONDS / total_seconds
        icon_progress = max(0.0, min(1.0, float(elapsed) / icon_seconds))
        if icon_progress < 0.28:
            phase = icon_progress / 0.28
            icon_scale = 1.0 + 0.17 * (1.0 - (1.0 - phase) ** 3)
        elif icon_progress < 0.66:
            phase = (icon_progress - 0.28) / 0.38
            icon_scale = 1.17 - 0.19 * (0.5 - 0.5 * math.cos(math.pi * phase))
        elif icon_progress < 1.0:
            phase = (icon_progress - 0.66) / 0.34
            icon_scale = 0.98 + 0.02 * (1.0 - (1.0 - phase) ** 3)
        else:
            icon_scale = 1.0
        # The crest starts behind the artwork and arrives at the right edge
        # after the full expansion interval. Linear acceleration of its speed
        # gives the wave a gentle start and an increasingly quick finish;
        # retaining some initial speed avoids a long invisible wait under art.
        progress = max(0.0, min(1.0, float(elapsed) / expand_seconds))
        radius_fraction = progress * (0.4 + 0.6 * progress)
        ingress = min(1.0, max(0.0, float(elapsed) / (expand_seconds * 0.09)))
        egress = min(1.0, max(0.0, (duration - float(elapsed)) / (duration - expand_seconds)))
        light_opacity = 0.78 * ingress * egress
        return icon_scale, radius_fraction, light_opacity

    def _transport_light_origin(self, now: float | None = None) -> QPointF:
        """Anchor the expanding light to the album, including while it scales."""
        return self._current_cover_rect(now).center()

    def _transport_light_max_radius(self, origin: QPointF, now: float | None = None) -> float:
        # The sprite crest, rather than its transparent outer pixels, reaches
        # the rightmost visible pixel at the end of the expansion interval.
        return max(1.0, (self.rect().right() - origin.x()) / self.TRANSPORT_LIGHT_CREST_FRACTION)

    def _transport_light_dirty_rect(self, radius_fraction: float, now: float | None = None) -> QRect:
        origin = self._transport_light_origin(now)
        radius = self._transport_light_max_radius(origin, now) * radius_fraction + 3.0
        return QRectF(
            origin.x() - radius, origin.y() - radius,
            2.0 * radius, 2.0 * radius,
        ).toAlignedRect().intersected(self.rect())

    def _transport_light_ink_region(
        self, command: str, radius_fraction: float, now: float | None = None,
    ) -> QRegion:
        """Conservatively bound the sprite's painted annulus, including sampling bleed."""
        origin = self._transport_light_origin(now)
        radius = self._transport_light_max_radius(origin, now) * radius_fraction
        # The actual cached sprites are transparent inside 0.53 (gold) or
        # 0.66 (ordinary) of their radius. Eight logical pixels protect the
        # antialiasing and bilinear edges at native/high display DPR.
        inner_fraction = 0.53 if command == "obo_reveal" else 0.66
        outer = radius + 8.0
        bounds = QRectF(
            origin.x() - outer, origin.y() - outer,
            2.0 * outer, 2.0 * outer,
        ).toAlignedRect()
        inner = max(0.0, radius * inner_fraction - 8.0)
        # An ellipse QRegion expands into over a thousand one-pixel runs on a
        # large window, making even update() expensive. Enclose its annulus
        # in 16-pixel horizontal bands. Every band's outer extent uses its
        # closest point to the center; the inner hole uses its farthest point.
        # This slightly overpaints but leaves at most two rectangles per band.
        region = QRegion()
        top = max(0, bounds.top())
        bottom = min(self.height(), bounds.bottom() + 1)
        band_height = 16
        for y0 in range(top, bottom, band_height):
            y1 = min(bottom, y0 + band_height)
            outer_dy = 0.0 if y0 <= origin.y() <= y1 else min(
                abs(y0 - origin.y()), abs(y1 - origin.y()),
            )
            outer_dx = math.sqrt(max(0.0, outer * outer - outer_dy * outer_dy))
            outer_left = max(0, math.floor(origin.x() - outer_dx) - 1)
            outer_right = min(self.width(), math.ceil(origin.x() + outer_dx) + 2)
            inner_dy = max(abs(y0 - origin.y()), abs(y1 - origin.y()))
            inner_dx = math.sqrt(max(0.0, inner * inner - inner_dy * inner_dy))
            inner_left = max(outer_left, min(outer_right, math.ceil(origin.x() - inner_dx)))
            inner_right = max(inner_left, min(outer_right, math.floor(origin.x() + inner_dx) + 1))
            if inner_dx <= 0.0:
                region = region.united(QRegion(QRect(outer_left, y0, outer_right - outer_left, y1 - y0)))
            else:
                if inner_left > outer_left:
                    region = region.united(QRegion(QRect(outer_left, y0, inner_left - outer_left, y1 - y0)))
                if outer_right > inner_right:
                    region = region.united(QRegion(QRect(inner_right, y0, outer_right - inner_right, y1 - y0)))
        return region

    def _transport_icon_dirty_rect(self, center: QPointF, scale: float) -> QRect:
        radius = 34.0 * scale
        return QRectF(center.x() - radius, center.y() - radius,
                      2.0 * radius, 2.0 * radius).toAlignedRect().intersected(self.rect())

    def _start_transport_feedback(self, command: str, now: float | None = None):
        if command not in {"previous", "next"}:
            return
        button = self._media_button_rects.get(command)
        if button is None or button.isEmpty():
            return
        instant = time.monotonic() if now is None else float(now)
        if self._control_reveal_at(instant) < 0.75:
            return
        # A manual skip takes over the current loading wave. If the new track
        # still has only a preview, the ring will begin a new cycle at its edge.
        loading_dirty = self._transport_light_dirty_regions.pop("art_loading", QRect())
        loading_ink = self._transport_light_ink_regions.pop("art_loading", QRegion(loading_dirty))
        self._transport_light_origins.pop("art_loading", None)
        self._transport_feedback.pop("art_loading", None)
        if not loading_ink.isEmpty():
            self.update(loading_ink)
        scale = self._surface_scale()
        duration = animation_duration(
            self.TRANSPORT_LIGHT_EXPAND_SECONDS + self.TRANSPORT_LIGHT_FADE_SECONDS,
            speed=getattr(self.owner, "animation_speed", 1.0),
            minimum=0.84,
        )
        center = QPointF(button.center())
        previous = self._transport_feedback.get(command)
        previous_dirty = self._transport_light_dirty_regions.get(command, QRect())
        previous_ink = QRegion(self._transport_light_ink_regions.get(command, QRegion(previous_dirty)))
        if previous is not None:
            old_frame = self._transport_feedback_frame(instant - previous[0], previous[3])
            if old_frame is not None:
                # A rapid second click restarts the wave; clear its former
                # extent as well as the new small opening ring.
                current_ink = self._transport_light_ink_region(command, old_frame[1], instant)
                old_origin = self._transport_light_origins.get(command)
                current_origin = self._transport_light_origin(instant)
                if old_origin is not None and old_origin != current_origin:
                    # The cover can relocate while the wave is in flight.
                    # Fall back to both extents on this uncommon transition.
                    previous_ink = QRegion(previous_dirty).united(
                        QRegion(self._transport_light_dirty_rect(old_frame[1], instant))
                    )
                else:
                    previous_ink = previous_ink.united(current_ink)
        if not previous_ink.isEmpty():
            self.update(previous_ink)
        self._transport_feedback[command] = (instant, center, scale, duration)
        dirty = self._transport_light_dirty_rect(0.0, instant)
        ink = self._transport_light_ink_region(command, 0.0, instant)
        self._transport_light_dirty_regions[command] = dirty
        self._transport_light_ink_regions[command] = ink
        self._transport_light_origins[command] = self._transport_light_origin(instant)
        self.update(ink)
        self.update(self._transport_icon_dirty_rect(center, scale))

    def _artwork_is_loading(self) -> bool:
        idle = getattr(self.owner, "_no_audio_visual_active", None)
        if callable(idle) and idle():
            return False
        lookup_state = str(getattr(self.owner, "album_art_lookup_state", "") or "").casefold()
        if lookup_state in {"failed", "miss"}:
            # A completed failed lookup keeps its best available cover. It is
            # no longer an in-flight download, and must not hold lyric feedback
            # behind a permanently repeating artwork ring.
            return False
        image = QImage(getattr(self.owner, "album_art", QImage()))
        source = str(getattr(self.owner, "album_art_source", "") or "")
        # Acquisition provenance belongs to the provider. Resizing the
        # surface cannot restart a completed download or change lyric layout.
        if image.isNull() or source == "thumbnail":
            return True
        if (source == "original" or source.endswith("-original")) and self._art_loading_preview_key is not None:
            # The source label can arrive before decoding updates the image.
            # Keep the actual displayed preview soft until it is replaced.
            # A same-object original can legitimately contain the exact same
            # pixels as its 320px preview. The provider's completed result,
            # rather than a changed QImage key, ends that upgrade. Tiny retained
            # previews and pending results still keep their existing guard.
            completed = lookup_state in {"original", "ready"} and min(image.width(), image.height()) >= 320
            return not completed and self._artwork_display_key(image) == self._art_loading_preview_key
        return False

    @staticmethod
    def _artwork_display_key(image: QImage) -> int:
        return image.cacheKey()

    def _should_blur_preview(self, image: QImage) -> bool:
        failed = str(getattr(self.owner, "album_art_lookup_state", "") or "").casefold() == "failed"
        if (not self._art_loading_waiting and not failed) or image.isNull():
            return False
        if self._track_visual is None or self._art_loading_track_key != self._track_visual[0]:
            return False
        source = str(getattr(self.owner, "album_art_source", "") or "")
        return source == "thumbnail" or (
            self._art_loading_preview_key is not None
            and self._artwork_display_key(image) == self._art_loading_preview_key
        )

    def _begin_art_loading_ring(self, now: float):
        if "art_loading" in self._transport_feedback:
            return
        duration = animation_duration(
            self.TRANSPORT_LIGHT_EXPAND_SECONDS + self.TRANSPORT_LIGHT_FADE_SECONDS,
            speed=getattr(self.owner, "animation_speed", 1.0),
            minimum=0.84,
        )
        self._transport_feedback["art_loading"] = (
            float(now), self._transport_light_origin(now), self._surface_scale(), duration,
        )
        dirty = self._transport_light_dirty_rect(0.0, now)
        self._transport_light_dirty_regions["art_loading"] = dirty
        ink = self._transport_light_ink_region("art_loading", 0.0, now)
        self._transport_light_ink_regions["art_loading"] = ink
        self._transport_light_origins["art_loading"] = self._transport_light_origin(now)
        self.update(ink)

    def invalidate_artwork_cache(self):
        """Reload artwork while retaining this song's layout and playhead."""
        self._cover_cache_key = None
        self._preview_cover_cache_key = None
        self._previous_cover_cache_key = None
        self._artwork_scale_cache.clear()
        self._artwork_scale_cache_bytes = 0
        self._art_palette_worker_token += 1
        self._art_palette_pending = None
        self._art_palette_failed_key = None
        self._art_palette_cache_key = None
        image = QImage(getattr(self.owner, "album_art", QImage()))
        self._art_loading_track_key = self._track_visual[0] if self._track_visual else (
            str(getattr(self.owner, "media_track_id", "") or "") or (
                str(getattr(self.owner, "track_title", "") or ""),
                str(getattr(self.owner, "artist", "") or ""),
            )
        )
        self._art_loading_preview_key = None if image.isNull() else self._artwork_display_key(image)
        self._art_loading_waiting = True
        self._begin_art_loading_ring(time.monotonic())
        self.update()

    def _on_artwork_track_changed(self, identity: object, now: float):
        old_dirty = self._transport_light_dirty_regions.pop("art_loading", QRect())
        old_ink = self._transport_light_ink_regions.pop("art_loading", QRegion(old_dirty))
        self._transport_light_origins.pop("art_loading", None)
        self._transport_feedback.pop("art_loading", None)
        if not old_ink.isEmpty():
            self.update(old_ink)
        old_gold_dirty = self._transport_light_dirty_regions.pop("obo_reveal", QRect())
        old_gold_ink = self._transport_light_ink_regions.pop("obo_reveal", QRegion(old_gold_dirty))
        self._transport_light_origins.pop("obo_reveal", None)
        self._transport_feedback.pop("obo_reveal", None)
        if not old_gold_ink.isEmpty():
            self.update(old_gold_ink)
        self._obo_reveal_track_key = None
        self._art_loading_track_key = identity
        image = QImage(getattr(self.owner, "album_art", QImage()))
        source = str(getattr(self.owner, "album_art_source", "") or "")
        self._art_loading_preview_key = self._artwork_display_key(image) if source == "thumbnail" and not image.isNull() else None
        self._art_loading_waiting = self._artwork_is_loading()
        # An already-running click wave remains visible across the song
        # change. Otherwise an automatic skip gets its own cover-origin ring.
        if not any(command in self._transport_feedback for command in ("previous", "next")):
            self._begin_art_loading_ring(now)

    def _update_transport_feedback_regions(self, now: float):
        if self._art_loading_waiting and self._track_visual is not None and self._art_loading_track_key == self._track_visual[0]:
            if not any(command in self._transport_feedback for command in ("previous", "next", "art_loading")):
                self._begin_art_loading_ring(now)
        for command, (started, center, scale, duration) in tuple(self._transport_feedback.items()):
            if self._art_loading_waiting and command == "art_loading":
                expand_seconds = duration * self.TRANSPORT_LIGHT_EXPAND_SECONDS / (
                    self.TRANSPORT_LIGHT_EXPAND_SECONDS + self.TRANSPORT_LIGHT_FADE_SECONDS
                )
                if now - started >= expand_seconds:
                    # Restart only after the crest reaches the right edge;
                    # after original art arrives the final ring finishes its
                    # ordinary fade rather than disappearing mid-flight.
                    cycles = max(1, int((float(now) - started) / expand_seconds))
                    started += cycles * expand_seconds
                    self._transport_feedback[command] = (started, center, scale, duration)
            frame = self._transport_feedback_frame(now - started, duration)
            if command == "obo_reveal" and self._reading_strategy() != "gold":
                frame = None
            if frame is None:
                self._transport_feedback.pop(command, None)
            old_dirty = self._transport_light_dirty_regions.get(command, QRect())
            old_ink = self._transport_light_ink_regions.get(command, QRegion(old_dirty))
            current_dirty = self._transport_light_dirty_rect(frame[1], now) if frame else QRect()
            current_ink = self._transport_light_ink_region(command, frame[1], now) if frame else QRegion()
            # Most frames change only the thin circular light. The old and
            # current annuli include the moving crest, tail and erase region.
            # A relocating cover uses both whole extents, so its old center
            # cannot leave a faint trail outside the new annulus.
            old_origin = self._transport_light_origins.get(command)
            current_origin = self._transport_light_origin(now)
            if old_origin is not None and old_origin != current_origin:
                dirty = QRegion(old_dirty).united(QRegion(current_dirty))
            else:
                dirty = old_ink.united(current_ink)
            if frame is None:
                self._transport_light_dirty_regions.pop(command, None)
                self._transport_light_ink_regions.pop(command, None)
                self._transport_light_origins.pop(command, None)
            else:
                self._transport_light_dirty_regions[command] = current_dirty
                self._transport_light_ink_regions[command] = current_ink
                self._transport_light_origins[command] = current_origin
            if not dirty.isEmpty():
                self.update(dirty)
            if command in {"previous", "next"}:
                self.update(self._transport_icon_dirty_rect(center, scale))
            if (
                command in {"previous", "next"} and self._art_loading_waiting
                and "art_loading" not in self._transport_feedback
            ):
                expand_seconds = duration * self.TRANSPORT_LIGHT_EXPAND_SECONDS / (
                    self.TRANSPORT_LIGHT_EXPAND_SECONDS + self.TRANSPORT_LIGHT_FADE_SECONDS
                )
                if now - started >= expand_seconds:
                    self._begin_art_loading_ring(now)
        self._maybe_start_obo_reveal(now)

    def _maybe_start_obo_reveal(self, now: float):
        """Follow the last artwork wave once genuinely timed lyrics are ready."""
        if self._track_visual is None or self._reading_strategy() != "gold":
            return
        identity = self._track_visual[0]
        if identity == self._obo_reveal_track_key or self._art_loading_waiting:
            return
        if any(command in self._transport_feedback for command in ("previous", "next", "art_loading")):
            return
        if self._artwork_is_loading():
            return
        lines, timed = self._display_lyrics()
        if self._lyrics_lookup_pending(lines, timed) or not self._has_genuine_obo_timing(lines, timed):
            return
        duration = animation_duration(
            self.OBO_REVEAL_SECONDS,
            speed=getattr(self.owner, "animation_speed", 1.0),
            minimum=2.0,
        )
        self._obo_reveal_track_key = identity
        self._transport_feedback["obo_reveal"] = (
            float(now), self._transport_light_origin(now), self._surface_scale(), duration,
        )
        dirty = self._transport_light_dirty_rect(0.0, now)
        self._transport_light_dirty_regions["obo_reveal"] = dirty
        ink = self._transport_light_ink_region("obo_reveal", 0.0, now)
        self._transport_light_ink_regions["obo_reveal"] = ink
        self._transport_light_origins["obo_reveal"] = self._transport_light_origin(now)
        self.update(ink)

    def _on_animation_tick(self):
        if getattr(self, "_library_loading", None) is not None:
            self.update()
            return
        if self.isVisible():
            self._sync_queue_artwork_connection()
        previous = getattr(self, "_frame_lyric_cache", None)
        self._frame_lyric_cache = {}
        try:
            self._advance_surface_animation_tick()
        finally:
            self._frame_lyric_cache = previous

    def _advance_surface_animation_tick(self):
        """Keep the moving lyric local while ambient/cover frames refresh slowly.

        A shorter timeout cannot force extra display frames.  Match a faster
        cadence to a high-refresh screen only after recent local paints have
        shown a safe frame budget; ordinary/expensive frames use 16 ms
        requests. These timer requests are never presented-FPS measurements.
        """
        if self._closing_for_shutdown:
            return
        if not self._compact_snapshot.isNull():
            self.update()
            return
        self._finish_stale_track_transition()
        self._advance_audio_output_motion(time.monotonic())
        if self._mode_animation.state() == QAbstractAnimation.State.Paused:
            if self._timer.interval() != self._transition_frame_interval():
                self._timer.setInterval(self._transition_frame_interval())
            self._advance_mode_transition(time.monotonic())
            return
        tick_started = time.perf_counter()
        now = time.monotonic()
        did_heavy_prewarm = False
        if self._song_layout_build is not None:
            # A fresh song's lyric ink is delayed by the existing track fade.
            # Shape a bounded part of its exact row geometry each tick rather
            # than all 180+ rows in the first visible transition frame.
            self._advance_song_layout_build(
                max_rows=24, max_seconds=self.LYRIC_LAYOUT_BATCH_SECONDS,
            )
            did_heavy_prewarm = True
        if (
            not did_heavy_prewarm and self._lyric_blur_prewarm_queue
            and now >= self._lyric_blur_next_prewarm_at
        ):
            self._advance_lyric_blur_prewarm(now)
            did_heavy_prewarm = True
        pending = self._pending_glow_prewarm if not did_heavy_prewarm else None
        if pending is not None:
            self._pending_glow_prewarm = None
        if pending is not None and pending[0][0] == self._song_layout_key:
            did_heavy_prewarm = True
            token, text, font, width, layout, mode, unread_color, line_index, unit_index = pending
            if 0 <= line_index < layout.lineCount():
                line = layout.lineAt(line_index)
                segment = self._utf16_slice(text, line.textStart(), line.textLength())
                units, complete = self._advance_isolated_glyph_units(
                    segment, font, "character", max_units=1,
                )
                next_unit = min(len(units), unit_index + 1)
                for source, _left in units[unit_index:next_unit]:
                    self._tinted_glyph_image(source, unread_color)
                    self._sparse_glyph_halo(source)
                if not complete or next_unit < len(units):
                    self._pending_glow_prewarm = (
                        token, text, font, width, layout, mode,
                        unread_color, line_index, next_unit,
                    )
                elif line_index + 1 < layout.lineCount():
                    self._pending_glow_prewarm = (
                        token, text, font, width, layout, mode,
                        unread_color, line_index + 1, 0,
                    )
        reading_pending = self._pending_reading_prewarm if not did_heavy_prewarm else None
        if reading_pending is not None:
            self._pending_reading_prewarm = None
        if (
            reading_pending is not None
            and reading_pending[0][0] == self._song_layout_key
            and reading_pending[0][1] == self._visual_track_identity()
        ):
            token, text, font, layout, unread_color, line_index, unit_index = reading_pending
            if 0 <= line_index < layout.lineCount():
                    did_heavy_prewarm = True
                    line = layout.lineAt(line_index)
                    segment = self._utf16_slice(text, line.textStart(), line.textLength())
                    self._glyph_run(segment, font, "reading")
                    units, complete = self._advance_isolated_glyph_units(
                        segment, font, "reading", max_units=1,
                    )
                    next_unit = min(len(units), unit_index + 1)
                    for source, _left in units[unit_index:next_unit]:
                        self._tinted_glyph_image(source, unread_color)
                    if not complete or next_unit < len(units):
                        self._pending_reading_prewarm = (
                            token, text, font, layout, unread_color, line_index, next_unit,
                        )
                    elif line_index + 1 < layout.lineCount():
                        self._pending_reading_prewarm = (
                            token, text, font, layout, unread_color, line_index + 1, 0,
                        )
        if not did_heavy_prewarm:
            idle_before = (
                self._metadata_idle_raster_token,
                len(self._metadata_idle_raster_pending),
                self._metadata_raster_cache_bytes,
            )
            self._prewarm_idle_metadata_raster()
            did_heavy_prewarm = idle_before != (
                self._metadata_idle_raster_token,
                len(self._metadata_idle_raster_pending),
                self._metadata_raster_cache_bytes,
            )
        now = time.monotonic()
        idle_elapsed = now - self._control_last_motion_at
        idle_seconds = (
            self.FULLSCREEN_CONTROL_IDLE_SECONDS if self._fullscreen
            else self.WINDOWED_CONTROL_IDLE_SECONDS
        )
        if (
            self._control_reveal_target >= 1.0
            and 0.4 <= idle_elapsed < idle_seconds - 0.2
            and not self._controls_animating(now)
            and not did_heavy_prewarm
        ):
            self._prewarm_metadata_text()
        self._update_control_animation_regions(now)
        self._update_personal_dj_control(now)
        if any(now - state[2] < .36 and state[0] != state[1]
               for state in self._control_color_transitions.values()):
            # Reuse the existing animation clock for one shared color handoff;
            # no per-button timer or full-scene repaint is required.
            self._update_control_animation_regions(now, force=True)
            self.update(self._personal_dj_damage_rect(QRectF(self.rect())))
            if self._dj_voice_selector_widget is not None and self._dj_voice_selector_widget.isVisible():
                self._dj_voice_selector_widget.update()
        if (self.isVisible() and self._owned_personal_fm
                and self._owned_playback_mode == "sequential"
                and self._control_last_tick_reveal >= 0.999
                and not self._capturing_transition_snapshot):
            # The existing FM sweep stays live; its second tone now follows
            # the same scene-wide black/white decision as its neighbours.
            base = self._window_control_base_rect(QRectF(self.rect()))
            self.update(self._playback_mode_button_rect(base, self._surface_scale())
                        .adjusted(-3, -3, 3, 3).toAlignedRect())
        self._update_transport_feedback_regions(now)
        # A paused track may have no other repaint during the two-second
        # windowed lead-in. Queue only the small control regions until
        # their cached blur levels have been prepared in separate paints.
        if (
            self._control_reveal_target >= 1.0
            and self._control_prewarm_remaining > 0
            and not self._control_prewarm_requested
            and max(0.3, idle_seconds - 2.0) <= idle_elapsed < idle_seconds
            and now >= self._control_layer_next_prewarm_at
        ):
            self._control_prewarm_requested = True
            self._update_control_animation_regions(now, force=True)
        if self._metadata_animation_rect.isValid() and (
            self._metadata_marquee_moving or now >= self._metadata_next_motion_at
        ):
            self.update(self._metadata_animation_rect)
        self._expire_lyric_browse(now)
        self._update_lyric_loading_state(now)
        self._advance_lyric_animation(now)
        if self._lyric_loading_visible(now) and self._lyric_animation_rect.isValid():
            cycle, phase = self._lyric_loading_phase(now)
            entrance, exit_phase = self._lyric_loading_reveal(now)
            stage = (cycle, round(phase * 80.0), round(entrance * 80.0), round(exit_phase * 80.0))
            if stage != self._lyric_loading_last_stage:
                self._lyric_loading_last_stage = stage
                self.update(self._lyric_animation_rect)
        elif self._lyric_loading_release_at and now >= self._lyric_loading_release_at:
            # A paused result still needs one paint to hand the column from
            # the completed skeleton cycle to the resolved lyric/layout.
            self._lyric_loading_release_at = 0.0
            self.update()
        cover_animating = self._cover_scale_animating(now)
        layout_animating = self._cover_layout_animating(now)
        cover_animating = layout_animating or cover_animating
        playing = self._display_playing_state(now)
        priority = (
            playing
            and self._browse_index is None
            and self._browse_return_from is None
            and self._last_lyric_emphasis_mode in {"scatter_glow", "character_lift"}
        )
        obo_pose_animating = (
            self._obo_advanced_focus_pose is not None
            and self._obo_advanced_focus_pose[-1] < 1.0
            and playing
        )
        companion_animating = (
            self._translation_exit_at is not None
            or any(0.0 <= now - began < 0.48 for began in self._translation_entered_at.values())
            or (self._translation_animate_entries and self._translation_setting and bool(
                self._translation_known_tokens - self._translation_entered_at.keys()
            ))
            or any(0.0 <= now - began < 0.48 for began in self._parenthetical_visible_since.values())
        )
        lyric_animation = companion_animating or priority or (
            self._browse_index is None and self._has_timed_lyrics and playing
        ) or self._lyric_spring_active or self._browse_return_from is not None or obo_pose_animating
        if (
            lyric_animation and self._lyric_animation_rect.isValid()
            and self._mode_animation.state() == QAbstractAnimation.State.Stopped
        ):
            screen = self.screen()
            refresh_rate = float(screen.refreshRate()) if screen is not None else 60.0
            local_costs = self._recent_priority_paint_costs
            costs = sorted(local_costs if len(local_costs) >= 12 else self._recent_paint_costs)
            p95 = costs[min(len(costs) - 1, int(len(costs) * 0.95))] if costs else float("inf")
            tick_signature = (
                self.width(), self.height(), round(self.devicePixelRatioF(), 2),
                round(refresh_rate, 1), self._visual_track_identity(),
            )
            if tick_signature != getattr(self, "_tick_profile_signature", None):
                self._tick_profile_signature = tick_signature
                self._recent_tick_costs = deque(maxlen=30)
            self._recent_tick_costs.append((time.perf_counter() - tick_started) * 1000.0)
            tick_costs = sorted(self._recent_tick_costs)
            tick_p95 = tick_costs[min(len(tick_costs) - 1, int(len(tick_costs) * 0.95))]
            # Match the monitor instead of flooding a 120 Hz event loop with
            # 250 timer requests.  144/165/240 Hz panels receive roughly
            # 7/6/4 ms requests when the measured paint budget can sustain it.
            display_interval = max(
                self.EMPHASIS_FRAME_MS,
                int(round(1000.0 / max(60.0, refresh_rate))),
            )
            fast = (
                refresh_rate >= 100.0
                and display_interval < self.ANIMATION_FRAME_MS
                and len(costs) >= 12
                and p95 + tick_p95 <= display_interval * 0.80
            )
            interval = min(self.ANIMATION_FRAME_MS, display_interval) if fast else self.ANIMATION_FRAME_MS
            if self._timer.interval() != interval:
                self._timer.setInterval(interval)
            identity = str(getattr(self.owner, "media_track_id", "") or "") or (
                str(getattr(self.owner, "track_title", "") or ""),
                str(getattr(self.owner, "artist", "") or ""),
            )
            if (
                self._previous_track_visual is not None
                or self._track_visual is None
                or identity != self._track_visual[0]
                or now - self._last_full_animation_update_at >= self.AMBIENT_FRAME_SECONDS
            ):
                self._last_full_animation_update_at = now
                self.update()
            else:
                dirty = (
                    self._active_lyric_animation_rect
                    if (
                        not companion_animating
                        and
                        not layout_animating
                        and not self._lyric_spring_active
                        and self._browse_return_from is None
                        and self._active_lyric_animation_rect.isValid()
                    )
                    else self._lyric_animation_rect
                )
                self.update(dirty)
                if cover_animating and self._cover_animation_rect.isValid():
                    self.update(self._cover_animation_rect)
            return
        if self._timer.interval() != self.ANIMATION_FRAME_MS:
            self._timer.setInterval(self.ANIMATION_FRAME_MS)
        identity = str(getattr(self.owner, "media_track_id", "") or "") or (
            str(getattr(self.owner, "track_title", "") or ""),
            str(getattr(self.owner, "artist", "") or ""),
        )
        if (
            self._previous_track_visual is not None
            or self._track_visual is None
            or identity != self._track_visual[0]
            or now - self._last_full_animation_update_at >= self.AMBIENT_FRAME_SECONDS
        ):
            self._last_full_animation_update_at = now
            self.update()
        elif cover_animating and self._cover_animation_rect.isValid():
            self.update(self._cover_animation_rect)
            if layout_animating and self._lyric_animation_rect.isValid():
                self.update(self._lyric_animation_rect)

    def shutdown(self):
        if self._closing_for_shutdown:
            return
        self._disconnect_queue_artwork_signal()
        self._layout_animation.stop()
        self._layout_edge_animation.stop()
        self._layout_edge_fraction = 1.
        self._finish_content_layout()
        self._dismiss_audio_output_panel(immediate=True)
        self._restore_idle_cursor()
        self._closing_for_shutdown = True
        self._timer.stop()
        self._audio_output_status_timer.stop()
        self._cancel_mode_transition()
        # Release bound receiver connections when this surface is closed,
        # before Qt/Python reclaim it and its child animation objects. An
        # explicit later show reattaches them through the existing entrypoint.
        self.close()
        self._timer.timeout.disconnect(self._on_animation_tick)
        self._audio_output_status_timer.timeout.disconnect(self._sync_audio_output_status)
        self._mode_animation.finished.disconnect(self._finish_mode_transition)
        self.seekFinished.disconnect(self._on_seek_finished)
        self._art_palette_worker_token += 1
        self._art_palette_pending = None
        # Keep the self-owned result connection until QObject destruction.
        # A reopened surface waits for this one worker to release its slot;
        # the generation check prevents its retired result changing the UI.

    def closeEvent(self, event):
        self._restore_idle_cursor()
        if not self._closing_for_shutdown:
            event.ignore()
            self.return_to_island()
            return
        super().closeEvent(event)

    def _owner_color(self, name: str, fallback: QColor) -> QColor:
        color = QColor(getattr(self.owner, name, QColor()))
        return color if color.isValid() else QColor(fallback)

    def _art_palette(self) -> MusicSurfacePalette:
        idle = getattr(self.owner, "_no_audio_visual_active", None)
        if callable(idle) and idle():
            primary, secondary = self.owner._no_audio_surface_colors()
            return artwork_surface_palette(QImage(), primary, secondary)
        image = QImage(getattr(self.owner, "album_art", QImage()))
        album = self._owner_color("album_color", QColor(27, 36, 60))
        secondary = self._owner_color("secondary_color", QColor(9, 12, 22))
        key = (image.cacheKey(), album.rgba(), secondary.rgba())
        if key != self._art_palette_cache_key or self._art_palette_cache is None:
            cached = cached_artwork_surface_palette(image, album, secondary)
            runner = getattr(self.owner, "_run_background", None)
            if cached is not None:
                self._art_palette_cache, self._art_palette_cache_key = cached, key
            elif image.isNull() or not callable(runner):
                self._art_palette_cache = artwork_surface_palette(image, album, secondary)
                self._art_palette_cache_key = key
            elif self._art_palette_failed_key != key and not self._closing_for_shutdown:
                if (self._art_palette_worker_key != key
                        or self._art_palette_active_token != self._art_palette_worker_token):
                    self._art_palette_pending = (key, image, album, secondary)
                self._start_artwork_palette_worker()
            if self._art_palette_cache is None:
                # Hold a real provider tone on first open; subsequent misses
                # retain the last complete displayed palette until ready.
                # The existing 1.2 s field retarget keeps the handoff smooth.
                self._art_palette_cache = artwork_surface_palette(QImage(), album, secondary)
        return self._art_palette_cache

    def _start_artwork_palette_worker(self):
        """One extraction in flight; rapid arrivals replace its pending successor."""
        if self._art_palette_worker_key is not None or self._art_palette_pending is None:
            return
        key, image, album, secondary = self._art_palette_pending
        self._art_palette_pending = None
        self._art_palette_worker_key = key
        self._art_palette_worker_token += 1
        token = self._art_palette_worker_token
        self._art_palette_active_token = token
        cached = cached_artwork_surface_palette(image, album, secondary)
        if cached is not None:
            self._on_artwork_palette_ready(token, key, cached)
            return
        deliver = self.artworkPaletteReady

        def prepare():
            # These immutable value snapshots are the complete worker payload.
            # Only signal emission is allowed; never inspect a QWidget or
            # paint from this worker thread.
            try:
                result = artwork_surface_palette(image, album, secondary)
            except Exception:
                result = None
            try:
                deliver.emit(token, key, result)
            except RuntimeError:
                pass  # Qt may already have disposed the hidden surface.

        try:
            self.owner._run_background("ArtworkPalette", prepare)
        except Exception:
            self._art_palette_worker_key = None
            self._art_palette_active_token = None
            self._art_palette_failed_key = key

    @Slot(int, object, object)
    def _on_artwork_palette_ready(self, token: int, key, result):
        if token != self._art_palette_active_token:
            return
        self._art_palette_worker_key = None
        self._art_palette_active_token = None
        if self._closing_for_shutdown:
            return
        image = QImage(getattr(self.owner, "album_art", QImage()))
        album = self._owner_color("album_color", QColor(27, 36, 60))
        secondary = self._owner_color("secondary_color", QColor(9, 12, 22))
        current = (image.cacheKey(), album.rgba(), secondary.rgba())
        if token == self._art_palette_worker_token and key == current:
            if result is not None:
                self._art_palette_cache, self._art_palette_cache_key = result, key
                self._art_palette_failed_key = None
                self.update()
            else:
                self._art_palette_failed_key = key
        if self._art_palette_pending is not None:
            if self._art_palette_pending[0] == current and current != self._art_palette_cache_key:
                self._start_artwork_palette_worker()
            else:
                self._art_palette_pending = None

    def _visual_track_identity(self) -> str:
        return str(getattr(self.owner, "media_track_id", "") or "") or (
            f"{getattr(self.owner, 'track_title', '')}\0{getattr(self.owner, 'artist', '')}"
        )

    def _display_playing_state(self, now: float | None = None) -> bool:
        current = bool(getattr(self.owner, "media_is_playing", False))
        if self._optimistic_playing is None:
            return current
        instant = time.monotonic() if now is None else float(now)
        if (
            self._optimistic_track_key != self._visual_track_identity()
            or current == self._optimistic_playing
            or instant >= self._optimistic_playing_until
        ):
            self._optimistic_playing = None
            return current
        return self._optimistic_playing

    def _cover_scale_at(self, now: float) -> float:
        instant = float(now)
        duration = self.COVER_PAUSE_ANIMATION_SECONDS
        progress = max(0.0, min(1.0, (instant - self._cover_scale_started_at) / duration))
        eased = progress * progress * (3.0 - 2.0 * progress)
        current = self._cover_scale_from + (self._cover_scale_to - self._cover_scale_from) * eased
        target = 1.0 if self._cover_visual_playing_state(instant) else self.COVER_PAUSE_SCALE
        if target != self._cover_scale_to:
            self._cover_scale_from = current
            self._cover_scale_to = target
            self._cover_scale_started_at = instant
            return current
        return current

    def _cover_visual_playing_state(self, now: float) -> bool:
        # The introduced music can play quietly underneath real DJ speech.
        # Retain its paused cover pose without misrepresenting the transport
        # state or reversing Play/Pause's actual command intent.
        provider = getattr(self.owner, "data_provider", None)
        getter = getattr(provider, "get_personal_dj_presentation", None)
        try:
            presentation = getter() if callable(getter) else {}
            presentation = presentation if isinstance(presentation, dict) else {}
            if "cover_paused" in presentation:
                cover_paused = bool(presentation["cover_paused"])
            else:
                speaking_getter = getattr(provider, "is_owned_personal_dj_speaking", None)
                cover_paused = bool(speaking_getter()) if callable(speaking_getter) else False
            if cover_paused:
                return False
        except Exception:
            pass
        return self._display_playing_state(now)

    def _cover_scale_animating(self, now: float) -> bool:
        self._cover_scale_at(now)
        return (
            self._cover_scale_from != self._cover_scale_to
            and float(now) - self._cover_scale_started_at < self.COVER_PAUSE_ANIMATION_SECONDS
        )

    def _no_lyrics_cover_target(self) -> bool:
        """Center artwork for missing lyrics or at most three sung display rows."""
        layout = self._effective_content_layout() if hasattr(self, "_content_layout") else "lyrics"
        if layout in {"cover", "cover_lyrics"}:
            return True
        if layout == "playlist":
            return False
        title = str(getattr(self.owner, "track_title", "") or "").strip()
        if title in {"", "暂无媒体", "暂无播放", "媒体初始化中"}:
            return False
        track_key = (
            str(getattr(self.owner, "media_track_id", "") or ""),
            title,
            str(getattr(self.owner, "artist", "") or ""),
        )
        lines, timed = self._display_lyrics()
        now = time.monotonic()
        self._update_lyric_loading_state(now, lines, timed)
        # Preserve the preceding layout until this song is resolved and the
        # visible loading cycle has finished. A centered song has no skeleton.
        if self._lyrics_lookup_pending(lines, timed) or self._lyric_loading_holds_result(now):
            return self._lyric_loading_hold_centered
        placeholder = str(getattr(self.owner, "no_lyrics_text", "暂无歌词") or "暂无歌词").strip()
        # Count semantic lyric rows only after display filtering. Translations
        # at the same timestamp remain one row; the synthetic intro countdown
        # is not a sung line. A sparse song uses the same centered-art layout
        # as a song with no usable lyrics.
        if timed:
            sung = 0
            for _start, text in timed:
                if text != INTRO_COUNTDOWN_TEXT:
                    sung += 1
                    if sung > 3:
                        break
            centered = sung <= 3
        else:
            centered = sum(str(line).strip() not in {"", "暂无歌词", placeholder} for line in lines) <= 3
        if centered:
            self._resolved_centered_track_key = track_key
        return centered

    def _lyrics_lookup_pending(
        self, lines: list[str] | None = None,
        timed: list[tuple[float, str]] | None = None,
    ) -> bool:
        title = str(getattr(self.owner, "track_title", "") or "").strip()
        if title in {"", "暂无媒体", "暂无播放", "媒体初始化中"}:
            return False
        if lines is None or timed is None:
            lines, timed = self._display_lyrics()
        if timed:
            return False
        waiting = getattr(self.owner, "_music_surface_waiting_for_loaded_lyrics", None)
        if callable(waiting) and waiting():
            return True
        return len(lines) == 1 and str(lines[0]).strip() in {
            "查找歌词中", "寻找歌词中", "加载歌词中",
        }

    def _update_lyric_loading_state(
        self, now: float, lines: list[str] | None = None,
        timed: list[tuple[float, str]] | None = None,
    ) -> None:
        """Keep visual loading local to a track; never hold its provider clock."""
        key = self._visual_track_identity()
        pending = self._lyrics_lookup_pending(lines, timed)
        if key != getattr(self, "_lyric_loading_track_key", None):
            self._lyric_loading_track_key = key
            self._lyric_loading_started_at = float(now)
            self._lyric_loading_last_stage = None
            self._lyric_loading_hold_centered = float(getattr(self, "_cover_layout_to", 0.0)) >= 0.5
            self._lyric_loading_has_shown = False
            self._lyric_loading_release_at = 0.0
            self._lyric_loading_exit_started_at = 0.0
            self._lyric_loading_cycle_end_at = 0.0
            self._lyric_loading_waiting = pending
        elif pending and not self._lyric_loading_waiting:
            self._lyric_loading_started_at = float(now)
            self._lyric_loading_hold_centered = self._cover_layout_to >= 0.5
            self._lyric_loading_has_shown = False
            self._lyric_loading_release_at = 0.0
            self._lyric_loading_exit_started_at = 0.0
            self._lyric_loading_cycle_end_at = 0.0
            self._lyric_loading_waiting = True
        elif not pending and self._lyric_loading_waiting:
            self._lyric_loading_waiting = False
            if self._lyric_loading_has_shown:
                visible_at = self._lyric_loading_started_at + 1.0
                cycle = max(1, math.ceil((float(now) - visible_at) / 3.0))
                self._lyric_loading_cycle_end_at = visible_at + cycle * 3.0
                self._lyric_loading_exit_started_at = max(
                    float(now), self._lyric_loading_cycle_end_at - 0.5,
                )
                # A result arriving at the cycle boundary still deserves the
                # full fade, instead of reducing it to one paint or 50 ms.
                self._lyric_loading_release_at = self._lyric_loading_exit_started_at + 0.5
        if (
            pending and not self._lyric_loading_hold_centered
            and float(now) >= self._lyric_loading_started_at + 1.0
        ):
            self._lyric_loading_has_shown = True

    def _lyric_loading_holds_result(self, now: float) -> bool:
        return bool(self._lyric_loading_release_at and float(now) < self._lyric_loading_release_at)

    def _lyric_loading_visible(self, now: float) -> bool:
        return bool(
            not self._lyric_loading_hold_centered
            and self._lyric_loading_has_shown
            and (self._lyric_loading_waiting or self._lyric_loading_holds_result(now))
        )

    def _lyric_loading_reveal(self, now: float) -> tuple[float, float]:
        entrance = max(0.0, min(1.0, (
            float(now) - self._lyric_loading_started_at - 1.0
        ) / 0.5))
        exit_phase = 0.0
        if self._lyric_loading_release_at:
            remaining = self._lyric_loading_release_at - self._lyric_loading_exit_started_at
            exit_phase = max(0.0, min(1.0, (
                float(now) - self._lyric_loading_exit_started_at
            ) / max(0.001, remaining)))
        return entrance, exit_phase

    def _lyric_loading_phase(self, now: float) -> tuple[int, float]:
        self._update_lyric_loading_state(now)
        instant = float(now)
        if self._lyric_loading_cycle_end_at:
            # Hold the final pose during a late result's extended fade; don't
            # wrap widths and row positions back to the next loading cycle.
            instant = min(instant, self._lyric_loading_cycle_end_at - 0.000001)
        elapsed = max(0.0, instant - self._lyric_loading_started_at - 1.0)
        cycle = int(elapsed // 3.0)
        within = elapsed - cycle * 3.0
        motion = max(0.0, min(1.0, (within - 2.48) / 0.52))
        return cycle, motion * motion * (3.0 - 2.0 * motion)

    def _paint_lyric_loading(self, painter: QPainter, column: QRectF, now: float, opacity: float) -> None:
        """Seven album-tinted rows move up once every three seconds."""
        if opacity <= 0.001 or column.isEmpty():
            return
        cycle, phase = self._lyric_loading_phase(now)
        if not self._lyric_loading_visible(now):
            return
        entrance, exit_phase = self._lyric_loading_reveal(now)
        scale = self._surface_scale()
        height = min(42.0 * scale, max(17.0 * scale, column.height() * 0.067))
        step = min(height * 1.75, column.height() / 7.45)
        top = column.center().y() - 3.0 * step - height * 0.5
        tone = self._future_lyric_color(column)
        widths = (0.96, 0.89, 0.84, 0.93, 0.78, 0.91, 0.86, 0.95, 0.81, 0.90, 0.87)
        painter.save()
        painter.setOpacity(opacity)
        band = QRectF(column.left(), top - (step - height) * 0.5, column.width(), step * 7.0)
        painter.setClipRect(column.intersected(band), Qt.ClipOperation.IntersectClip)
        painter.setPen(Qt.PenStyle.NoPen)
        for slot in range(-1, 8):
            visual = slot - phase
            distance = abs(visual - 3.0)
            if visual < -0.95 or visual > 7.05:
                continue
            brightness = max(0.36, 1.0 - 0.18 * distance)
            color = QColor(tone)
            hsv = color.getHsv()
            if hsv[0] >= 0 and hsv[1] > 28:
                color = QColor.fromHsv(hsv[0], hsv[1], max(1, round(hsv[2] * brightness)))
            else:
                level = max(1, round(color.value() * brightness))
                color = QColor(level, level, level)
            color.setAlpha(round(235 * max(0.25, 1.0 - 0.19 * distance)))
            width = column.width() * widths[(cycle + slot) % len(widths)]
            bar = QRectF(column.left(), top + visual * step, width, height)
            fade = whole_block_reading_gradient(
                band, exit_phase if exit_phase else entrance,
                color if exit_phase else QColor(0, 0, 0, 0),
                QColor(0, 0, 0, 0) if exit_phase else color,
            )
            # Mirror the shared diagonal so the lower right appears (and
            # disappears) first, without allocating a per-frame mask image.
            start, stop = fade.start(), fade.finalStop()
            fade.setStart(band.bottomRight())
            fade.setFinalStop(band.bottomRight() - (stop - start))
            painter.setBrush(QBrush(fade))
            painter.drawRoundedRect(bar, 7.0 * scale, 7.0 * scale)
        painter.restore()

    def _cover_layout_fraction_at(self, now: float) -> float:
        instant = float(now)
        duration = animation_duration(
            self.COVER_LAYOUT_ANIMATION_SECONDS,
            speed=getattr(self.owner, "animation_speed", 1.0),
            minimum=0.22,
        )
        progress = max(0.0, min(1.0, (instant - self._cover_layout_started_at) / duration))
        eased = progress * progress * (3.0 - 2.0 * progress)
        current = self._cover_layout_from + (self._cover_layout_to - self._cover_layout_from) * eased
        if progress >= 1.0:
            self._cover_layout_from = current
        target = 1.0 if self._no_lyrics_cover_target() else 0.0
        if target != self._cover_layout_to:
            self._cover_layout_from = current
            self._cover_layout_to = target
            self._cover_layout_started_at = instant
            # The cover and metadata cross both columns. An immediate full
            # repaint clears the old lyric/metadata ink at the decision edge;
            # subsequent animation frames use the swept group bounds.
            self.update()
        return current

    def _cover_layout_animating(self, now: float) -> bool:
        self._cover_layout_fraction_at(now)
        duration = animation_duration(
            self.COVER_LAYOUT_ANIMATION_SECONDS,
            speed=getattr(self.owner, "animation_speed", 1.0),
            minimum=0.22,
        )
        return (
            self._cover_layout_from != self._cover_layout_to
            and float(now) - self._cover_layout_started_at < duration
        )

    @staticmethod
    def _interpolate_rect(first: QRectF, second: QRectF, fraction: float) -> QRectF:
        amount = max(0.0, min(1.0, float(fraction)))
        return QRectF(
            first.x() + (second.x() - first.x()) * amount,
            first.y() + (second.y() - first.y()) * amount,
            first.width() + (second.width() - first.width()) * amount,
            first.height() + (second.height() - first.height()) * amount,
        )

    @staticmethod
    def _lyric_horizontal_slide(fraction: float, surface_right: float, text_left: float) -> float:
        """Send disappearing lyric ink rightward; bring new ink in from there."""
        progress = max(0.0, min(1.0, float(fraction)))
        eased = progress * progress * (3.0 - 2.0 * progress)
        return max(0.0, float(surface_right) - float(text_left) + 64.0) * eased

    def _centered_text_layout(self, text: str, font: QFont, width: float) -> tuple[QTextLayout, float]:
        """Shape centered, fully wrapped metadata once per text/size/width."""
        display = " ".join(str(text or "").split())
        key = (display, font.toString(), max(1, int(round(width))))
        cached = self._centered_text_layout_cache.get(key)
        if cached is not None:
            self._centered_text_layout_cache.move_to_end(key)
            return cached
        layout = QTextLayout(display, font)
        option = QTextOption()
        option.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        cursor = 0.0
        last_bottom = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1.0, float(width)))
            line.setPosition(QPointF(0.0, cursor))
            last_bottom = cursor + line.height()
            cursor += line.height() * 0.94
        layout.endLayout()
        result = (layout, max(self._font_height(font), last_bottom))
        self._centered_text_layout_cache[key] = result
        if len(self._centered_text_layout_cache) > 32:
            self._centered_text_layout_cache.popitem(last=False)
        return result

    def _centered_album_plan(self, original: QRectF) -> dict:
        """Fit the art, complete names and optional transport in either mode."""
        rect = QRectF(self.rect())
        scale = self._surface_scale(rect)
        footer_height = self._personal_dj_footer_height(rect)
        title = str(getattr(self.owner, "track_title", "") or "暂无播放")
        artist = str(getattr(self.owner, "artist", "") or "")
        key = (rect.width(), rect.height(), original.width(), title, artist, footer_height,
               self.SURFACE_FONT_FAMILIES)
        if key == self._centered_album_plan_key and self._centered_album_plan_cache is not None:
            return self._centered_album_plan_cache
        text_width = max(1.0, min(rect.width() - 24.0 * scale, rect.width() * 0.86))
        margin = 12.0 * scale
        cover_gap = 7.0 * scale
        artist_gap = 2.0 * scale
        control_gap = 8.0 * scale
        control_height = self.TRANSPORT_PANEL_HEIGHT * scale
        for step in range(15):
            fraction = 0.94 ** step
            title_font = self._surface_font(43.0 * scale * fraction, QFont.Weight.Bold)
            artist_font = self._surface_font(27.0 * scale * fraction, QFont.Weight.Medium)
            title_layout, title_height = self._centered_text_layout(title, title_font, text_width)
            artist_layout, artist_height = self._centered_text_layout(artist, artist_font, text_width)
            available = rect.height() - footer_height - 2.0 * margin - title_height - artist_height - (
                cover_gap + artist_gap + control_gap + control_height
            )
            if available >= original.width() * 0.53 or step == 14:
                break
        maximum_side = min(original.width() * 1.3, rect.width() - 2.0 * margin)
        if self._content_layout_is_narrow():
            maximum_side = min(rect.width() - 2.0 * margin, max(1., rect.height() - 142. * scale))
        idle_side = max(1.0, min(maximum_side, rect.height() - footer_height - 2.0 * margin -
                                 title_height - artist_height - cover_gap - artist_gap))
        shown_side = max(1.0, min(maximum_side, available))
        idle_height = idle_side + cover_gap + title_height + artist_gap + artist_height + footer_height
        shown_height = shown_side + cover_gap + title_height + artist_gap + artist_height + control_gap + control_height + footer_height
        idle_cover = QRectF(rect.center().x() - idle_side * 0.5,
                            rect.top() + (rect.height() - idle_height) * 0.5,
                            idle_side, idle_side)
        shown_cover = QRectF(rect.center().x() - shown_side * 0.5,
                             rect.top() + (rect.height() - shown_height) * 0.5,
                             shown_side, shown_side)
        plan = {
            "idle_cover": idle_cover, "shown_cover": shown_cover,
            "title_font": title_font, "title_layout": title_layout, "title_height": title_height,
            "artist_font": artist_font, "artist_layout": artist_layout, "artist_height": artist_height,
            "text_width": text_width, "cover_gap": cover_gap, "artist_gap": artist_gap,
            "control_gap": control_gap, "control_height": control_height,
            "control_width": min(350.0 * scale, max(175.0 * scale, shown_side * 0.82)),
        }
        self._centered_album_plan_key = key
        self._centered_album_plan_cache = plan
        return plan

    def _cover_layout_endpoints(self, now: float | None = None) -> tuple[QRectF, QRectF]:
        rect = QRectF(self.rect())
        scale = self._surface_scale(rect)
        content, original, _column = self._content_column_geometry(rect)
        if self._content_layout_is_narrow():
            side = max(1., min(rect.width() - 24. * scale, rect.height() - 142. * scale))
            original = QRectF(content.left(), content.top(), side, side)
            footer_height = self._personal_dj_footer_height(rect)
            if footer_height > 0.:
                # The source endpoint remains visible during a resize or
                # centering handoff. Fit its complete cover/name/transport
                # group above the same footer as the centered endpoint;
                # clipping only transport would put it onto the artist row.
                below_cover = max(
                    self._metadata_layout(original, content, scale, reveal)[3].bottom() - original.bottom()
                    for reveal in (0., .2, 1.)
                )
                below_cover += (14. + self.TRANSPORT_PANEL_HEIGHT) * scale
                bottom = rect.bottom() - footer_height - 18. * scale
                side = max(1., min(side, bottom - content.top() - below_cover))
                original.setWidth(side)
                original.setHeight(side)
            # The unused source endpoint can still participate during a
            # resize/center animation. Keep that endpoint within the surface.
            original.moveLeft(min(original.left(), rect.right()-12.*scale-side))
        if (
            self._cover_layout_from <= 0.0 and self._cover_layout_to <= 0.0
            and not self._no_lyrics_cover_target()
        ):
            return original, QRectF(original)
        instant = time.monotonic() if now is None else float(now)
        plan = self._centered_album_plan(original)
        centered = self._interpolate_rect(
            plan["idle_cover"], plan["shown_cover"], self._control_reveal_at(instant),
        )
        return original, centered

    def _centered_group_rects(self, cover: QRectF, plan: dict) -> tuple[QRectF, QRectF, QRectF]:
        rect = QRectF(self.rect())
        text_width = plan["text_width"]
        title = QRectF(rect.center().x() - text_width * 0.5,
                       cover.bottom() + plan["cover_gap"],
                       text_width, plan["title_height"])
        artist = QRectF(title.left(), title.bottom() + plan["artist_gap"],
                        text_width, plan["artist_height"])
        controls = QRectF(rect.center().x() - plan["control_width"] * 0.5,
                          artist.bottom() + plan["control_gap"],
                          plan["control_width"], plan["control_height"])
        return title, artist, controls

    def _current_cover_rect(self, now: float | None = None) -> QRectF:
        """Continuous unpaused artwork geometry, also shared by the spatial light."""
        instant = time.monotonic() if now is None else float(now)
        fraction = self._cover_layout_fraction_at(instant)
        original, centered = self._cover_layout_endpoints(instant)
        return self._interpolate_rect(original, centered, fraction)

    def navigation_cover_state(self):
        """Expose the actual unrotated cover pose for native page handoffs."""
        now = time.monotonic()
        cover = self._scaled_cover_rect(self._current_cover_rect(now), self._cover_scale_at(now)).toAlignedRect()
        pending = getattr(self, "_library_loading", None)
        image = pending[1] if pending is not None else getattr(self.owner, "album_art", QImage())
        return (QRect(self.mapToGlobal(cover.topLeft()), cover.size()), QImage(image), 0.)

    def set_library_loading(self, title=None, artwork=None):
        """Present a pending library selection without inventing player data."""
        self._library_loading = ((str(title), QImage(artwork if artwork is not None else QImage()))
                                 if title is not None else None)
        if title is not None:
            self._lyric_hit_rects.clear()
            self._queue_hit_rects.clear()
            self._media_button_rects.clear()
            self._metadata_hit_rects.clear()
            self._lyric_clip_rect = QRectF()
            self._queue_view_rect = QRectF()
            self._queue_pressed = self._queue_pressed_token = None
            self._pressed_window_button = self._hovered_window_button = ""
            self._dismiss_song_playlist_panel()
            self._pressed_media_command = ""
            self._pressed_lyric_index = None
        self._reset_control_idle()
        self.update()

    def _paint_library_loading(self, painter, rect, now):
        title, image = self._library_loading
        cover = self._scaled_cover_rect(self._current_cover_rect(now), self._cover_scale_at(now))
        ink = QColor("white")
        if not getattr(self, "_navigation_cover_capture", False):
            painter.save()
            path = QPainterPath(); path.addRoundedRect(cover, cover.width() * .055, cover.height() * .055)
            painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
            if not image.isNull():
                side = min(image.width(), image.height())
                painter.drawImage(cover, image, QRectF((image.width()-side)/2, (image.height()-side)/2, side, side))
            paint_artwork_skeleton(painter, cover, ink, now, radius=cover.width() * .055)
            painter.restore()
        label = QRectF(cover.left(), cover.bottom()+18, cover.width(), 30)
        painter.setPen(ink)
        painter.setFont(self._surface_font(17, QFont.Weight.DemiBold))
        painter.drawText(label, Qt.AlignmentFlag.AlignCenter,
                         QFontMetricsF(painter.font()).elidedText(title, Qt.TextElideMode.ElideRight, label.width()))
        for index, width in enumerate((.78, .56)):
            bar = QRectF(cover.left(), label.bottom()+12+index*18, cover.width()*width, 9)
            paint_artwork_skeleton(painter, bar, ink, now, radius=4.5)
        self._paint_library_back(painter, rect)
        self._paint_window_controls(painter, rect)

    def navigation_background_frame(self, excluded_cover_rect=None):
        """Capture UI once without the independently travelling album layer."""
        previous = getattr(self, "_navigation_cover_capture", False)
        self._navigation_cover_capture = True
        try:
            return self.grab().toImage()
        finally:
            self._navigation_cover_capture = previous

    def navigation_backdrop_frame(self):
        return QImage(self._background_cache)

    @staticmethod
    def _scaled_cover_rect(cover: QRectF, scale: float) -> QRectF:
        side = min(cover.width(), cover.height()) * max(0.0, float(scale))
        return QRectF(
            cover.center().x() - side * 0.5,
            cover.center().y() - side * 0.5,
            side, side,
        )

    def _send_media_command(self, command: str):
        sender = getattr(self.owner, "_send_playback_command_async", None)
        if not callable(sender):
            return
        if command == "toggle_play_pause":
            now = time.monotonic()
            self._optimistic_playing = not self._display_playing_state(now)
            self._optimistic_track_key = self._visual_track_identity()
            self._optimistic_playing_until = now + 1.8
            self._cover_scale_at(now)
        else:
            self._optimistic_playing = None
        sender(command)
        if hasattr(self.owner, "last_media_refresh_at"):
            self.owner.last_media_refresh_at = 0.0
        self.update()

    def _track_transition(self, now: float, primary: QColor, secondary: QColor) -> float:
        identity = str(getattr(self.owner, "media_track_id", "") or "") or (
            str(getattr(self.owner, "track_title", "") or ""),
            str(getattr(self.owner, "artist", "") or ""),
        )
        visual = (
            identity,
            str(getattr(self.owner, "track_title", "") or "暂无播放"),
            str(getattr(self.owner, "artist", "") or ""),
            QImage(getattr(self.owner, "album_art", QImage())),
            QColor(primary), QColor(secondary),
        )
        if self._track_visual is not None and visual[0] != self._track_visual[0]:
            self._previous_track_visual = self._track_visual
            self._track_transition_at = now
            # The center-layout exit may reuse lyric ink from an earlier
            # layout of this song, but never from the previous song while a
            # lyric lookup or its loading skeleton is being replaced.
            self._last_visible_lyric_snapshot = None
            self._last_visible_lyric_snapshot_track = None
            self._on_artwork_track_changed(identity, now)
        elif self._track_visual is None and visual[1] not in {"", "暂无媒体", "暂无播放", "媒体初始化中"}:
            # First-open thumbnails and missing images also get the loading
            # handoff. Remember final images without starting a loading ring.
            if self._artwork_is_loading():
                self._on_artwork_track_changed(identity, now)
            else:
                self._art_loading_track_key = identity
        elif self._art_loading_track_key == identity:
            source = str(getattr(self.owner, "album_art_source", "") or "")
            if source == "thumbnail" and not visual[3].isNull():
                # Includes a late first preview and a real same-song downgrade.
                self._art_loading_preview_key = self._artwork_display_key(visual[3])
            still_loading = self._artwork_is_loading()
            if self._art_loading_waiting and not still_loading:
                # An actual final image ends repetition; its last ring keeps
                # expanding and fades naturally instead of being cut off.
                self._art_loading_waiting = False
            elif still_loading and not self._art_loading_waiting:
                # A real thumbnail/null-image regression starts a new wait;
                # changes to geometry or device-pixel ratio do not.
                self._art_loading_waiting = True
                self._begin_art_loading_ring(now)
        self._track_visual = visual
        return max(0.0, min(1.0, (now - self._track_transition_at) / 0.36)) if self._previous_track_visual else 1.0

    def _blurred_preview_cover(self, source: QImage) -> QImage:
        if source.isNull():
            return source
        key = (source.cacheKey(), source.width(), source.height(), source.devicePixelRatio())
        if key == self._preview_cover_cache_key:
            return self._preview_cover_cache
        rgba = source.convertToFormat(QImage.Format.Format_RGBA8888)
        pixels = Image.frombytes("RGBA", (rgba.width(), rgba.height()), bytes(rgba.bits()))
        softened = pixels.filter(ImageFilter.GaussianBlur(max(2.0, 5.0 * source.devicePixelRatio())))
        image = QImage(
            softened.tobytes(), rgba.width(), rgba.height(), rgba.width() * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
        image.setDevicePixelRatio(source.devicePixelRatio())
        self._preview_cover_cache_key = key
        self._preview_cover_cache = image
        return image

    def _scaled_artwork_image(self, source: QImage, pixels: int, dpr: float) -> QImage:
        """Reuse full-quality current/previous covers with a bounded pixel budget."""
        key = (source.cacheKey(), int(pixels), round(float(dpr), 3))
        cached = self._artwork_scale_cache.get(key)
        if cached is not None:
            self._artwork_scale_cache.move_to_end(key)
            return QImage(cached)
        image = source.scaled(max(1, int(pixels)), max(1, int(pixels)),
                              Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                              Qt.TransformationMode.SmoothTransformation)
        image.setDevicePixelRatio(dpr)
        cost = image.sizeInBytes()
        if cost <= self.ARTWORK_SCALE_CACHE_BYTES:
            self._artwork_scale_cache[key] = QImage(image)
            self._artwork_scale_cache_bytes += cost
            while (len(self._artwork_scale_cache) > self.ARTWORK_SCALE_CACHE_LIMIT
                   or self._artwork_scale_cache_bytes > self.ARTWORK_SCALE_CACHE_BYTES):
                _old_key, old_image = self._artwork_scale_cache.popitem(last=False)
                self._artwork_scale_cache_bytes -= old_image.sizeInBytes()
        return image

    @staticmethod
    def _mix_color(previous: QColor, current: QColor, fraction: float) -> QColor:
        return QColor(*(
            round(a * (1.0 - fraction) + b * fraction)
            for a, b in zip(previous.getRgb()[:3], current.getRgb()[:3])
        ))

    @staticmethod
    def _relative_luminance(color: QColor) -> float:
        channels = []
        for value in QColor(color).getRgb()[:3]:
            component = value / 255.0
            channels.append(component / 12.92 if component <= 0.04045 else ((component + 0.055) / 1.055) ** 2.4)
        return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722

    @classmethod
    def _contrast_ratio(cls, first: QColor, second: QColor) -> float:
        first_luminance = cls._relative_luminance(first)
        second_luminance = cls._relative_luminance(second)
        lighter, darker = max(first_luminance, second_luminance), min(first_luminance, second_luminance)
        return (lighter + 0.05) / (darker + 0.05)

    @staticmethod
    def _composited_color(foreground: QColor, background: QColor) -> QColor:
        alpha = QColor(foreground).alphaF()
        return QColor(*(
            round(front * alpha + back * (1.0 - alpha))
            for front, back in zip(QColor(foreground).getRgb()[:3], QColor(background).getRgb()[:3])
        ))

    def _background_sample_colors(self, region: QRectF, *, image: QImage | None = None) -> tuple[QColor, ...]:
        image = self._background_cache if image is None else image
        if image.isNull() or image.width() <= 0 or image.height() <= 0:
            return (QColor(12, 15, 22),)
        bounds = QRectF(self.rect())
        sample_region = QRectF(region).intersected(bounds)
        if not sample_region.isValid() or sample_region.isEmpty():
            sample_region = bounds
        samples: list[QColor] = []
        for y_fraction in (0.12, 0.5, 0.88):
            surface_y = sample_region.top() + sample_region.height() * y_fraction
            image_y = round(surface_y / max(1.0, bounds.height()) * max(0, image.height() - 1))
            for x_fraction in (0.12, 0.5, 0.88):
                surface_x = sample_region.left() + sample_region.width() * x_fraction
                image_x = round(surface_x / max(1.0, bounds.width()) * max(0, image.width() - 1))
                samples.append(image.pixelColor(image_x, image_y))
        return tuple(samples)

    def _future_lyric_color(self, region: QRectF) -> QColor:
        """Choose one album-hued unread tone for the whole song."""
        palette = self._art_palette()
        album_source = QColor(palette.characteristic)
        flow_backdrop = self._album_flow_background.contrast_backdrop(self._background_cache)
        track_key = (
            self._visual_track_identity(), self._art_palette_cache_key,
            palette.characteristic.rgba(),
            None if flow_backdrop is None else flow_backdrop.rgba(),
        )
        cached = self._future_tone_cache.get(track_key)
        if cached is not None:
            return QColor(cached)
        if track_key != self._future_tone_track_key:
            backdrop = flow_backdrop
            if backdrop is not None:
                # The darkest moment's spatial median cannot protect fixed
                # lyric ink when a brighter album mass later drifts beneath it.
                self._future_tone_use_light = True
                self._future_tone_require_light = True
            else:
                overview = self._background_sample_colors(QRectF(self.rect()))
                backdrop = sorted(overview, key=self._relative_luminance)[len(overview) // 2]
                self._future_tone_use_light = self._relative_luminance(backdrop) < 0.11
                self._future_tone_require_light = False
            self._future_tone_backdrop = QColor(backdrop)
            self._future_tone_track_key = track_key
        tinted = select_future_lyric_color(
            album_source, self._future_tone_backdrop, use_light=self._future_tone_use_light,
            primary=palette.primary, require_light=self._future_tone_require_light,
        )
        self._future_tone_cache[track_key] = QColor(tinted)
        while len(self._future_tone_cache) > 256:
            self._future_tone_cache.pop(next(iter(self._future_tone_cache)))
        return tinted

    def _future_visual_line_colors(self, layout: QTextLayout, origin: QPointF) -> tuple[QColor, ...]:
        if layout.lineCount() == 0:
            return ()
        left = min(layout.lineAt(index).x() for index in range(layout.lineCount()))
        right = max(layout.lineAt(index).x() + layout.lineAt(index).naturalTextWidth() for index in range(layout.lineCount()))
        top = min(layout.lineAt(index).y() for index in range(layout.lineCount()))
        bottom = max(layout.lineAt(index).y() + layout.lineAt(index).height() for index in range(layout.lineCount()))
        ink = QRectF(origin.x() + left, origin.y() + top, max(1.0, right - left), max(1.0, bottom - top))
        color = self._future_lyric_color(ink)
        return (color,) * layout.lineCount()

    def _display_lyrics(self) -> tuple[list[str], list[tuple[float, str]]]:
        # Each GUI tick/paint sees one consistent provider snapshot. Keep the
        # complete value check between frames (including in-place edits), but
        # avoid rebuilding a long song's signature for every helper in one.
        frame = getattr(self, "_frame_lyric_cache", None)
        if frame is None:
            return self._resolve_display_lyrics()
        if "lyrics" not in frame:
            frame["lyrics"] = self._resolve_display_lyrics()
        return frame["lyrics"]

    def _resolve_display_lyrics(self) -> tuple[list[str], list[tuple[float, str]]]:
        translated = self._translation_render_enabled(time.monotonic())
        raw_timed = tuple((start, str(text)) for start, text in (getattr(self.owner, "timed_lyrics", ()) or ()))
        owner_units = getattr(self.owner, "timed_lyric_units", ()) or ()
        owner_kinds = getattr(self.owner, "timed_lyric_timing_kinds", ()) or ()
        if self._translation_exit_source is not None:
            raw_timed, owner_units, owner_kinds = self._translation_exit_source
        if bool(getattr(self.owner, "enable_dual_line_lyrics", False)):
            self._translation_last_source = (raw_timed, owner_units, owner_kinds)
        source = (raw_timed, owner_units, owner_kinds)
        store = getattr(self.owner, "_lyric_presentation_store", None)
        if store is not None:
            # The live owner submits on provider/settings updates. A miss here
            # only replaces the latest pending snapshot, never processes text.
            prepared = store.get(translated=translated,
                                 source=source if self._translation_exit_source is not None else None)
            if prepared is None:
                if (self._display_lyrics_cache is not None
                        and getattr(self, "_prepared_lyric_identity", None) == self._visual_track_identity()):
                    return self._display_lyrics_cache
                self._display_timed_units = self._display_timed_kinds = self._display_row_timings = ()
                self._display_timed_read_ends = self._display_raw_source_texts = ()
                self._display_duet_presentation = None
                return (["暂无歌词" if store.failed is not None else "加载歌词中"], [])
            key = (prepared.source.identity, id(prepared))
        else:
            # Standalone/offline callers retain a synchronous pure accessor.
            # Every live V2 owner installs the shared bounded store before its
            # first provider update; no QWidget/font/layout enters that worker.
            key = (self._visual_track_identity(), str(getattr(self.owner, "track_title", "")),
                   str(getattr(self.owner, "artist", "")), raw_timed,
                   id(owner_units), len(owner_units), id(owner_kinds), len(owner_kinds),
                   tuple(getattr(self.owner, "lyrics", ()) or ()), translated)
            if key == self._display_lyrics_cache_key and self._display_lyrics_cache is not None:
                return self._display_lyrics_cache
            prepared = prepare_lyric_presentation(snapshot_lyric_input(
                self.owner, translated=translated, source=source,
            ))
        if key == self._display_lyrics_cache_key and self._display_lyrics_cache is not None:
            return self._display_lyrics_cache
        self._display_duet_presentation = prepared.duet
        self._display_timed_units, self._display_timed_kinds = prepared.units, prepared.kinds
        self._display_row_timings = prepared.timings
        self._display_timed_read_ends = prepared.read_ends
        self._display_raw_source_texts = prepared.raw_source_texts
        self._display_units_source, self._display_kinds_source = owner_units, owner_kinds
        self._prepared_lyric_layers = prepared.layers
        self._prepared_lyric_result = prepared
        self._prepared_lyric_identity = self._visual_track_identity()
        self._display_lyrics_cache_key = key
        self._display_lyrics_cache = (list(prepared.lines), list(prepared.timed))
        self._display_timing_quality = None
        self._visual_lyric_cache_key = None
        return self._display_lyrics_cache

    def _reading_strategy(self) -> str:
        return normalize_music_reading_strategy(getattr(
            self.owner, "music_surface_reading_strategy", MUSIC_READING_STRATEGY_DEFAULT,
        ))

    def _visual_lyric_layers_for_rows(
        self, lines: list[str], timed: list[tuple[float, str]], *, outgoing: bool,
    ) -> tuple:
        duet = self._display_duet_presentation
        cached_display = self._display_lyrics_cache
        use_duet = (
            not outgoing and duet is not None and cached_display is not None
            and lines is cached_display[0] and timed is cached_display[1]
            and len(duet.rows) == len(lines)
        )
        show_translation = self._translation_render_enabled(time.monotonic())
        key = (
            self._visual_track_identity(), id(lines), id(timed),
            id(duet) if use_duet else None, show_translation,
        )
        if key != self._visual_lyric_cache_key:
            if (use_duet and getattr(self, "_prepared_lyric_layers", None) is not None
                    and len(self._prepared_lyric_layers) == len(lines)):
                self._visual_lyric_layers = self._prepared_lyric_layers
                self._visual_lyric_cache_key = key
                return self._visual_lyric_layers
            source = (row.body_text for row in duet.rows) if use_duet else lines
            self._visual_lyric_layers = tuple(
                split_display_layers(
                    text,
                    source_text=(
                        self._display_raw_source_texts[index]
                        if not outgoing and index < len(self._display_raw_source_texts)
                        else None
                    ),
                    show_translation=show_translation,
                )
                for index, text in enumerate(source)
            )
            self._visual_lyric_cache_key = key
        return self._visual_lyric_layers

    @staticmethod
    def _visible_primary_source_offsets(source_text: str, layers) -> tuple[int, ...] | None:
        """Map painted characters to the unchanged timed source's offsets."""
        primary = MusicSurfaceWindow._primary_timed_lyric_text(source_text)
        original = layers.original_primary_text
        start = primary.find(original) if original else -1
        if start < 0 or len(layers.primary_source_offsets) != len(layers.primary_text) + 1:
            return None
        return tuple(start + offset for offset in layers.primary_source_offsets)

    @staticmethod
    def _project_source_front(front: float, offsets: tuple[int, ...]) -> float:
        """Hold a reading front through hidden brackets/prefixes without retiming it."""
        if len(offsets) <= 1 or front <= offsets[0]:
            return 0.0
        count = len(offsets) - 1
        if front >= offsets[-1]:
            return float(count)
        index = max(0, min(count - 1, bisect_right(offsets[:-1], front) - 1))
        return min(float(index + 1), index + max(0.0, front - offsets[index]))

    @staticmethod
    def _project_highlight_groups(
        groups: tuple[TimedHighlightGroup, ...], offsets: tuple[int, ...] | None,
    ) -> tuple[TimedHighlightGroup, ...]:
        if offsets is None:
            return ()
        source_positions = offsets[:-1]
        projected = []
        for group in groups:
            start = bisect_left(source_positions, group.start_offset)
            end = bisect_left(source_positions, group.end_offset)
            if (
                end - start != group.end_offset - group.start_offset
                or tuple(source_positions[start:end])
                != tuple(range(group.start_offset, group.end_offset))
            ):
                continue
            projected.append(TimedHighlightGroup(
                start, end, group.start_seconds, group.end_seconds,
                group.unit_starts, group.unit_ends, group.source_text,
            ))
        return tuple(projected)

    def _parenthetical_reserve_factors(
        self, visual_active: int, now: float, *, pair_starts: tuple[int, ...] = (),
    ) -> tuple[float, ...]:
        """Open aside space only on focus, then keep it with completed lyrics."""
        extras = self._song_parenthetical_extra_heights
        paired = frozenset(pair_starts)
        group_start = visual_active - 1 if visual_active - 1 in paired else visual_active
        group_members = (
            (group_start, group_start + 1) if group_start in paired else (group_start,)
        )
        track = self._visual_track_identity()
        if getattr(self, "_parenthetical_track_key", None) != track or now < self._parenthetical_exit_at:
            self._parenthetical_track_key = track
            self._parenthetical_focus_index = group_start
            self._parenthetical_enter_at = now
            self._parenthetical_visible_since.clear()
            self._parenthetical_read_progress.clear()
            self._parenthetical_exit_index = None
        elif group_start != self._parenthetical_focus_index:
            self._parenthetical_focus_index = group_start
            self._parenthetical_enter_at = now
        factors = [0.0] * len(extras)
        for index in (*range(max(0, group_start)), *group_members):
            if 0 <= index < len(factors) and extras[index] > 0.0:
                factors[index] = 1.0
        return tuple(factors)

    def _parenthetical_reveal_pose(
        self, index: int, now: float, *, focused: bool,
    ) -> tuple[float, float]:
        """Share the aside's elastic entrance with the translation below it."""
        if not focused:
            return 1.0, 1.0
        entered_at = self._parenthetical_visible_since.setdefault(index, now)
        phase = min(1.0, max(0.0, (now - entered_at) / 0.48))
        back = 1.0 + 2.70158 * (phase - 1.0) ** 3 + 1.70158 * (phase - 1.0) ** 2
        opacity_phase = min(1.0, phase * 3.0)
        opacity = opacity_phase * opacity_phase * (3.0 - 2.0 * opacity_phase)
        return back, opacity

    def on_translation_visibility_changed(self, enabled: bool) -> None:
        """Retain the outgoing companion long enough for a visible collapse."""
        enabled = bool(enabled)
        now = time.monotonic()
        if self._visual_track_identity() != self._translation_track_key:
            # A provider can replace the song immediately before the click,
            # while no new paint has cleared the previous source yet.
            self._translation_render_enabled(now)
        if enabled == self._translation_setting:
            return
        poses = {
            token: self._translation_layer_pose(token[0], token[1], now)
            for token in tuple(self._translation_known_tokens)
        }
        self._translation_track_key = self._visual_track_identity()
        self._translation_setting = enabled
        if enabled:
            self._translation_exit_at = None
            self._translation_exit_source = None
            self._translation_animate_entries = True
            self._translation_entered_at.clear()
            self._translation_entry_from = poses
            self._translation_exit_poses.clear()
        else:
            self._translation_exit_poses = poses
            source = self._translation_last_source
            self._translation_exit_at = now if source is not None else None
            self._translation_exit_source = (
                (source[0], tuple(tuple(group or ()) for group in source[1]), tuple(source[2]))
                if source is not None else None
            )
        self._display_lyrics_cache_key = None
        self.update(self._lyric_animation_rect)

    def _translation_render_enabled(self, now: float) -> bool:
        track = self._visual_track_identity()
        if track != getattr(self, "_translation_track_key", None):
            self._translation_track_key = track
            self._translation_exit_at = None
            self._translation_exit_source = None
            self._translation_last_source = None
            self._translation_entered_at = {}
            self._translation_known_tokens = set()
            self._translation_entry_from = {}
            self._translation_exit_poses = {}
            self._translation_animate_entries = False
            self._translation_setting = bool(getattr(self.owner, "enable_dual_line_lyrics", False))
        enabled = bool(getattr(self.owner, "enable_dual_line_lyrics", False))
        if enabled != self._translation_setting:
            self.on_translation_visibility_changed(enabled)
        if self._translation_exit_at is not None and now - self._translation_exit_at >= 0.34:
            self._translation_exit_at = None
            self._translation_exit_source = None
        return enabled or self._translation_exit_at is not None

    def _translation_layer_pose(self, index: int, text: str, now: float) -> tuple[float, float, float]:
        """Return reserved height, opacity and one positional spring reveal."""
        if not text:
            return 0.0, 0.0, 0.0
        token = (index, text)
        self._translation_known_tokens.add(token)
        if self._translation_exit_at is not None:
            phase = max(0.0, min(1.0, (now - self._translation_exit_at) / 0.34))
            reveal = 1.0 - phase * phase * (3.0 - 2.0 * phase)
            start = self._translation_exit_poses.get(token, (1.0, 1.0, 1.0))
            # Keep the entrance lane until the outgoing ink has disappeared;
            # its following row then closes the space with the same spring.
            return start[0], start[1] * reveal, start[2] * reveal
        if not self._translation_setting:
            return 0.0, 0.0, 0.0
        if not self._translation_animate_entries:
            return 1.0, 1.0, 1.0
        start = self._translation_entry_from.get(token, (1.0, 0.0, 0.0))
        began = self._translation_entered_at.get(token)
        if began is None and start[1] > 0.001:
            began = self._translation_entered_at.setdefault(token, now)
        if began is None:
            # Reserve the full destination before showing ink. The actual
            # following spring grants clearance and starts the reveal below.
            return 1.0, start[1], start[2]
        phase = max(0.0, min(1.0, (now - began) / 0.48))
        smooth = phase * phase * (3.0 - 2.0 * phase)
        back = 1.0 + 2.70158 * (phase - 1.0) ** 3 + 1.70158 * (phase - 1.0) ** 2
        opacity_phase = min(1.0, phase * 3.0)
        opacity = opacity_phase * opacity_phase * (3.0 - 2.0 * opacity_phase)
        return tuple(value + (1.0 - value) * eased for value, eased in zip(start, (smooth, opacity, back)))

    def _start_translation_entries_with_clearance(
        self, texts: tuple[str, ...], layouts: tuple, positions: tuple[float, ...],
        ranges: tuple, factors: tuple[float, ...], pair_first_by_row: dict, now: float,
        visible_range: tuple[int, int],
    ) -> None:
        """Let following primary springs open the lane before elastic ink enters."""
        if not self._translation_setting or not self._translation_animate_entries:
            return
        for index, text in enumerate(texts[:len(layouts)]):
            token = (index, text)
            if not text or token in self._translation_entered_at:
                continue
            first, end = ranges[index]
            if end <= visible_range[0] or first >= visible_range[1]:
                # Offscreen springs intentionally do not integrate. They
                # acquire settled geometry when entering the viewport and
                # must not keep a paused page repainting for an unseen reveal.
                self._translation_entered_at[token] = now - 0.48
                continue
            next_row = pair_first_by_row[index] + 2 if index in pair_first_by_row else index + 1
            if next_row >= len(layouts):
                self._translation_entered_at[token] = now
                continue
            next_first = ranges[next_row][0]
            if end <= first or next_first >= len(positions):
                continue
            layout, height = layouts[index]
            bottom = (
                positions[first] + height if index in pair_first_by_row else
                positions[end - 1] + layout.lineAt(layout.lineCount() - 1).height()
            )
            bottom += (
                self._song_parenthetical_extra_heights[index] * factors[index]
                + self._song_translation_extra_heights[index]
            )
            # The small overshoot remains inside the normal sentence gap.
            secondary = self._song_secondary_layouts[index]
            small_height = secondary[1] if secondary is not None else 0.0
            margin = min(max(3.0, small_height * 0.06), max(3.0, self.height() * 0.012))
            if positions[next_first] >= bottom + margin:
                self._translation_entered_at[token] = now

    def _timing_quality_badge(self, lines: list[str], timed: list[tuple[float, str]]) -> str:
        """Describe the source timing, independent of the chosen paint style."""
        cached_display = self._display_lyrics_cache
        displayed = cached_display is not None and lines is cached_display[0] and timed is cached_display[1]
        if displayed and self._display_timing_quality is not None:
            return self._display_timing_quality
        if not timed:
            label = lyric_timing_quality_label((), (), has_lyrics=bool(lines and lines != ["暂无歌词"]))
        else:
            indexes = [
                index for index, (_, text) in enumerate(timed)
                if text != INTRO_COUNTDOWN_TEXT
            ]
            label = lyric_timing_quality_label(
                tuple(self._display_timed_kinds[index] if index < len(self._display_timed_kinds) else "line" for index in indexes),
                tuple(self._display_timed_units[index] if index < len(self._display_timed_units) else () for index in indexes),
                has_lyrics=bool(indexes),
            )
        if displayed:
            self._display_timing_quality = label
        return label

    def _has_genuine_obo_timing(self, lines: list[str], timed: list[tuple[float, str]]) -> bool:
        """True only when a surviving sung row has measured unit timing."""
        return self._timing_quality_badge(lines, timed) in {"真实逐字", "部分逐字"}

    def _has_only_unverified_unit_timing(self, timed: list[tuple[float, str]]) -> bool:
        """Identify enhanced-LRC rows whose unit markers lack measured timing.

        Plain line-timed songs retain the estimated sparse-highlight fallback.
        When a source claims unit timing but none of its surviving rows can
        substantiate that claim, estimating long-word highlights from row
        boundaries would misrepresent the entire song.
        """
        unverified = False
        for index, (_, text) in enumerate(timed):
            if text == INTRO_COUNTDOWN_TEXT or index >= len(self._display_timed_kinds):
                continue
            if self._display_timed_kinds[index] != "unit":
                continue
            units = self._display_timed_units[index] if index < len(self._display_timed_units) else ()
            if genuinely_timed_unit_row(units):
                return False
            unverified = True
        return unverified

    @staticmethod
    def _coalesced_timed_lyrics(timed_lyrics, *, clean_intro=True, singer_names=""):
        return coalesced_timed_lyrics(timed_lyrics, clean_intro=clean_intro, singer_names=singer_names)

    @staticmethod
    def _primary_timed_lyric_text(text: str) -> str:
        parts = str(text or "").replace("\u2028", "\n").splitlines()
        return parts[0].strip() if parts else ""

    @staticmethod
    def _presentation_lyric_text(text: str) -> str:
        """Apply conservative display-only clause breaks without rewriting lyrics.

        Providers sometimes concatenate an English original and a CJK
        translation without a separator.  Split that clear bilingual boundary
        only when the Latin side contains at least two words.  Original text
        and timestamps remain untouched in the provider model.
        """
        value = str(text or "")
        if "\n" in value:
            return value.replace("\n", "\u2028")
        cjk = r"\u2e80-\u9fff\uf900-\ufaff\u3040-\u30ff\u31f0-\u31ff\u1100-\u11ff\u3130-\u318f\uac00-\ud7af"
        match = re.search(rf"(?P<latin>[A-Za-z][A-Za-z0-9'’.,!?;:()\-\s]*\s+[A-Za-z][A-Za-z0-9'’.,!?;:()\-\s]*)(?P<cjk>[{cjk}]{{2,}})", value)
        if match is None:
            return value
        return value[:match.end("latin")].rstrip() + "\u2028" + value[match.start("cjk"):]

    def _clear_lyric_browse(self):
        self._browse_index = None
        self._browse_until = 0.0
        self._browse_return_from = None
        self._browse_return_started_at = 0.0

    def _expire_lyric_browse(self, now: float):
        if self._browse_index is None or not self._browse_until or now < self._browse_until:
            return
        # The listening clock never changes: only the viewport's visual
        # anchor travels back to it after manual wheel input goes idle.
        self._browse_return_from = float(self._browse_index)
        self._browse_return_started_at = float(self._browse_until)
        self._browse_index = None
        self._browse_until = 0.0
        self._hovered_lyric_index = None

    def _lyric_view_anchor(self, active: int, count: int, now: float) -> tuple[float, int, bool]:
        """Return the viewport anchor, crisp row and manual-return state."""
        self._expire_lyric_browse(now)
        active = max(0, min(max(0, count - 1), int(active)))
        returning = self._browse_return_from is not None
        if self._browse_index is not None:
            anchor = float(self._browse_index)
        elif returning:
            elapsed = float(now) - self._browse_return_started_at
            if elapsed < 0.0 or elapsed >= self.LYRIC_BROWSE_RETURN_SECONDS:
                self._browse_return_from = None
                self._browse_return_started_at = 0.0
                returning = False
                anchor = float(active)
            else:
                phase = elapsed / self.LYRIC_BROWSE_RETURN_SECONDS
                eased = phase * phase * (3.0 - 2.0 * phase)
                anchor = self._browse_return_from + (active - self._browse_return_from) * eased
        else:
            anchor = float(active)
        anchor = max(0.0, min(float(max(0, count - 1)), anchor))
        focus = max(0, min(max(0, count - 1), int(math.floor(anchor + 0.5))))
        return anchor, focus, self._browse_index is not None or returning

    def _primary_row_tokens(self, timed) -> tuple[tuple[str, int], ...]:
        """Identify repeated primary sentences independently of companions/time."""
        occurrences: dict[str, int] = {}
        tokens = []
        for _start, text in timed:
            primary = self._primary_timed_lyric_text(str(text))
            occurrence = occurrences.get(primary, 0)
            occurrences[primary] = occurrence + 1
            tokens.append((primary, occurrence))
        return tuple(tokens)

    def _advance_lyric_animation(self, now: float) -> None:
        """Advance existing motion on the frame timer, even if Qt coalesces paint."""
        if (
            not self._has_timed_lyrics
            or self._lyric_loading_waiting
            or self._lyric_loading_visible(now)
        ):
            return
        snapshot_track = self._last_visible_lyric_snapshot_track
        if snapshot_track is None or snapshot_track[0] != self._visual_track_identity():
            return
        _lines, timed = self._display_lyrics()
        previous_active = self._lyric_motion.active_index
        previous_timeline = self._timeline_signature
        frame = self._lyric_frame(timed, now)
        if frame.active_index != previous_active or self._timeline_signature != previous_timeline:
            # The next paint publishes layout targets. Motion integration
            # continues independently against the last published geometry.
            self.update(self._lyric_animation_rect)
        spring = self._logical_line_spring
        spring.advance(now)
        first, last = spring.visible_range or (0, 0)
        self._lyric_spring_active = any(
            abs(spring.positions[index] - spring.targets[index]) > 0.10
            or abs(spring.velocities[index]) > 0.10
            for index in range(first, last)
        )

    def _lyric_frame(self, timed: list[tuple[float, str]], now: float):
        try:
            media_position = float(self.owner._estimated_media_position(now))
        except (AttributeError, TypeError, ValueError):
            media_position = float(getattr(self.owner, "media_position_seconds", 0.0) or 0.0)
        offset = getattr(self.owner, "_effective_lyric_offset_seconds", None)
        lyric_offset = 0.0
        if callable(offset):
            lyric_offset = float(offset())
        track_key = self._visual_track_identity()
        timeline_source = tuple(timed)
        if timeline_source != self._timeline_signature_source:
            timeline_signature = tuple(
                (round(float(start), 4), str(text))
                for start, text in timed
            )
            self._timeline_signature_source = timeline_source
        else:
            timeline_signature = self._timeline_signature
        track_changed = track_key != self._browse_track_key
        first_visible_timeline = bool(timed) and not self._timeline_signature
        if track_changed or timeline_signature != self._timeline_signature:
            previous_timeline = self._timeline_signature or ()
            self._obo_advanced_focus_index = None
            self._obo_advanced_focus_pose = None
            self._obo_advanced_handoff_last_at = None
            self._clear_lyric_browse()
            self._hovered_lyric_index = None
            self._browse_track_key = track_key
            self._timeline_signature = timeline_signature
            self._owner_lyric_marker = None
            if track_changed or first_visible_timeline:
                self._owner_seek_marker = None
                self._lyric_source_was_playing = None
                self._pending_backward_seek = None
                self._lyric_motion.last_at = None
            elif self._lyric_motion.last_position is not None:
                # Translation/timing enrichment edits the row list, not the
                # player clock. Remap row indexes at the validated position
                # without accepting an unconfirmed source spike.
                remapped = active_index_for_position(
                    timed, self._lyric_motion.last_position, lead_seconds=0.0,
                ) if timed else 0
                self._lyric_motion.active_index = remapped
                # Reindex the same physical sentence, preserving any roll in
                # flight. A companion or timing update is not a new Seek.
                old_tokens = self._primary_row_tokens(previous_timeline)
                new_tokens = self._primary_row_tokens(timed)
                new_indexes = {token: index for index, token in enumerate(new_tokens)}
                scroll = self._lyric_motion.scroll_index
                lower = max(0, min(len(old_tokens) - 1, int(math.floor(scroll)))) if old_tokens else 0
                upper = min(len(old_tokens) - 1, lower + 1) if old_tokens else 0
                if old_tokens and old_tokens[lower] in new_indexes:
                    new_lower = new_indexes[old_tokens[lower]]
                    new_upper = new_indexes.get(old_tokens[upper], new_lower)
                    fraction = max(0.0, min(1.0, scroll - lower))
                    self._lyric_motion.scroll_index = new_lower + (new_upper - new_lower) * fraction
                elif not set(old_tokens).intersection(new_tokens):
                    # A completely replaced placeholder has no shared
                    # physical sentence; start its first view at source time.
                    self._lyric_motion.scroll_index = float(remapped)
                    self._lyric_motion.velocity = 0.0
                self._lyric_motion._clear_pending_forward()
                self._lyric_motion._clear_pending_backward()
        # The island labels *any* multi-row change "seek", including a stale
        # foreground clock spike. Verified seeks and a fresh raw SDK event
        # while already paused can bypass confirmation; row labels cannot.
        # One interpolated provider sample painted 80 times is still one sample.
        source = str(getattr(self.owner, "media_position_source", "") or "")
        source_app = str(getattr(self.owner, "media_source_app", "") or "")
        if source_app != self._lyric_source_app:
            # Identical track IDs can survive an app handoff. Clock evidence
            # and accepted seek targets belong to that app, while the same
            # sentence keeps its physical viewport pose.
            self._owner_seek_marker = None
            self._lyric_source_was_playing = None
            self._pending_backward_seek = None
            self._pending_backward_seek_app = None
        self._lyric_source_app = source_app
        if hasattr(self.owner, "media_position_observed_seconds"):
            # An explicit None is an unknown observation, not permission to
            # confirm a spike using the provider's already-projected clock.
            # A non-finite confirmation marker never replaces source time.
            # Owners predating this contract retain their raw fallback.
            observed_position = self.owner.media_position_observed_seconds
        else:
            observed_position = getattr(self.owner, "media_raw_position_seconds",
                                        getattr(self.owner, "media_position_seconds", media_position))
        observed_position = float("nan") if observed_position is None else float(observed_position)
        original_sample_at = float(getattr(self.owner, "media_position_sampled_at", 0.0) or 0.0)
        sample_id = (
            original_sample_at if original_sample_at > 0.0 else float(getattr(self.owner, "media_position_sync_at", now) or 0.0),
            0.0 if original_sample_at > 0.0 else float(getattr(self.owner, "media_raw_position_seconds", getattr(self.owner, "media_position_seconds", media_position)) or 0.0),
            source,
            source_app,
        )
        playing = bool(getattr(self.owner, "media_is_playing", False))
        paused_source_seek = (
            not playing and self._lyric_source_was_playing is False
            and source in {"windows-media", "interactive"}
            and math.isfinite(observed_position)
            and original_sample_at > 0.0
            and self._owner_seek_marker is not None
            and self._owner_seek_marker[2] == source
            and self._owner_seek_marker[3] == source_app
            and sample_id != self._owner_seek_marker
        )
        # A paused player may publish only one event after its slider moves.
        # That fresh source event can re-anchor an already-paused view; the
        # first playing->paused sample still passes the stale-clock filter.
        explicit_seek = paused_source_seek or (
            source == "local-seek"
            and (
                self._owner_seek_marker is None
                or self._owner_seek_marker[2] != "local-seek"
                or (original_sample_at > 0.0 and sample_id != self._owner_seek_marker)
            )
        )
        presentation_reanchor = self._pending_lyric_offset_reanchor
        self._pending_lyric_offset_reanchor = False
        self._owner_seek_marker = sample_id
        self._lyric_source_was_playing = playing
        if explicit_seek:
            self._clear_lyric_browse()
            self._hovered_lyric_index = None
        owner_lyric_marker = (
            int(getattr(self.owner, "lyric_index", 0) or 0),
            float(getattr(self.owner, "lyric_changed_at", 0.0) or 0.0),
        )
        self._owner_lyric_marker = owner_lyric_marker
        if self._pending_backward_seek is not None:
            target_seconds, expires_at = self._pending_backward_seek
            if now > expires_at or self._pending_backward_seek_app not in {None, source_app}:
                self._pending_backward_seek = None
            elif abs(media_position - target_seconds) <= 0.85:
                explicit_seek = True
                self._pending_backward_seek = None
        position_ceiling = None
        if sample_id[2].endswith("-uia-lyrics"):
            # The island caps this coarse row anchor before rendering. Repeat
            # its boundary here because the motion clock can extrapolate past
            # the capped input when paints arrive slowly.
            owner_timed = getattr(self.owner, "timed_lyrics", ()) or timed
            if owner_timed:
                anchor = max(0.0, float(getattr(self.owner, "media_position_seconds", 0.0) or 0.0))
                anchor_index = active_index_for_position(owner_timed, anchor, lead_seconds=0.0)
                if anchor_index + 1 < len(owner_timed):
                    next_start = float(owner_timed[anchor_index + 1][0])
                    position_ceiling = max(0.0, max(anchor, next_start - 0.08))
        previous_active = self._lyric_motion.active_index
        presentation_changed = presentation_reanchor or not math.isclose(
            lyric_offset, self._lyric_motion.presentation_offset, abs_tol=1e-7,
        )
        compensation_anchor = getattr(self.owner, "_lyric_output_compensation_anchor", None)
        previous_anchor = getattr(self, "_lyric_compensation_anchor", compensation_anchor)
        self._lyric_compensation_anchor = compensation_anchor
        presentation_retarget = presentation_changed and (
            presentation_reanchor or not playing or compensation_anchor is None
            or compensation_anchor != previous_anchor
        )
        if explicit_seek or presentation_retarget:
            self._obo_advanced_focus_index = None
            self._obo_advanced_focus_pose = None
            self._obo_advanced_handoff_last_at = None
        frame = self._lyric_motion.frame(
            timed, max(0.0, media_position), now, track_key,
            fallback_index=int(getattr(self.owner, "lyric_index", 0) or 0),
            duration=float(getattr(self.owner, "media_duration_seconds", 0.0) or 0.0),
            sample_id=sample_id,
            playing=playing,
            explicit_seek=explicit_seek,
            position_ceiling=position_ceiling,
            presentation_offset=lyric_offset,
            presentation_reanchor=presentation_reanchor,
            sample_position=observed_position,
            source_generation=(source_app, source),
        )
        if abs(frame.active_index - previous_active) > 1:
            self._clear_lyric_browse()
            self._hovered_lyric_index = None
        if frame.active_index < previous_active:
            self._obo_advanced_focus_index = None
            self._obo_advanced_focus_pose = None
            self._obo_advanced_handoff_last_at = None
        return frame

    def on_lyric_offset_changed(self) -> None:
        """Re-anchor the next lyric frame after an effective offset edit."""
        self._obo_advanced_focus_index = None
        self._obo_advanced_focus_pose = None
        self._obo_advanced_handoff_last_at = None
        self._pending_lyric_offset_reanchor = True
        self._pending_backward_seek = None
        self._clear_lyric_browse()
        self._hovered_lyric_index = None
        self.update()

    @staticmethod
    def _utf16_offset(text: str, python_offset: int) -> int:
        return len(text[:max(0, int(python_offset))].encode("utf-16-le")) // 2

    @staticmethod
    def _is_cjk_cluster(text: str) -> bool:
        return any(
            "\u2e80" <= character <= "\u9fff"
            or "\uf900" <= character <= "\ufaff"
            or "\u3040" <= character <= "\u30ff"
            or "\u31f0" <= character <= "\u31ff"
            or "\u1100" <= character <= "\u11ff"
            or "\u3130" <= character <= "\u318f"
            or "\uac00" <= character <= "\ud7af"
            for character in text
        )

    @staticmethod
    @lru_cache(maxsize=512)
    def _reading_unit_offsets(text: str) -> tuple[int, ...]:
        return reading_unit_offsets(text)

    @staticmethod
    @lru_cache(maxsize=512)
    def _character_unit_offsets(text: str) -> tuple[int, ...]:
        """Return grapheme boundaries while attaching spaces/punctuation left."""
        if not text:
            return (0,)
        finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
        boundaries = [0]
        clusters: list[tuple[int, int, str]] = []
        previous = 0
        encoded = text.encode("utf-16-le")
        while True:
            offset = finder.toNextBoundary()
            if offset < 0:
                break
            cluster = encoded[previous * 2 : int(offset) * 2].decode("utf-16-le")
            clusters.append((previous, int(offset), cluster))
            previous = int(offset)
        for _start, end, cluster in clusters:
            spoken = any(character.isalnum() for character in cluster)
            symbol = any(unicodedata.category(character).startswith("S") for character in cluster)
            if spoken or symbol or len(boundaries) == 1:
                boundaries.append(end)
            else:
                boundaries[-1] = end
        utf16_length = len(encoded) // 2
        if boundaries[-1] != utf16_length:
            boundaries[-1] = utf16_length
        return tuple(dict.fromkeys(boundaries))

    @staticmethod
    def _utf16_slice(text: str, start: int, length: int) -> str:
        encoded = str(text).encode("utf-16-le")
        return encoded[max(0, start) * 2 : max(0, start + length) * 2].decode("utf-16-le")

    @classmethod
    def _apply_cjk_letter_spacing(cls, layout: QTextLayout, text: str, font: QFont):
        apply_cjk_letter_spacing(layout, text, font, reference_pixel_size=cls.LYRIC_PIXEL_SIZE)

    def _glyph_run(self, text: str, font: QFont, unit_mode: str = "reading") -> tuple[QImage, tuple[float, ...]]:
        key = (text, font.toString(), str(unit_mode))
        cached = self._glyph_run_cache.get(key)
        if cached is not None:
            previous_working_key = self._glyph_cache_key
            self._glyph_cache_key = key
            self._glyph_cache = cached
            if previous_working_key != key and self._glyph_run_cache_bytes > self.GLYPH_RUN_CACHE_MAX_BYTES:
                self._trim_emphasis_glyph_caches()
            return cached
        if key == self._glyph_cache_key and self._glyph_cache is not None:
            return self._glyph_cache
        layout = QTextLayout(text, font)
        self._apply_cjk_letter_spacing(layout, text, font)
        layout.beginLayout()
        line = layout.createLine()
        layout.endLayout()
        if not line.isValid():
            return QImage(), (0.0,)
        line.setPosition(QPointF(2.0, 4.0))
        width = max(1, math.ceil(line.naturalTextWidth()) + 5)
        height = max(1, math.ceil(line.height()) + 9)
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as layer:
            layer.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            layer.setPen(Qt.GlobalColor.white)
            layout.draw(layer, QPointF())
        # cursorToX already includes the QTextLine's 2 px raster padding.
        # Remove that padding before positioning isolated words; adding it a
        # second time moved each later word right while the active lyric was
        # read, then snapped it left when the whole-line raster took over.
        boundaries = [
            max(0.0, min(float(width), float(line.cursorToX(offset)[0]) - line.x()))
            for offset in (
                self._character_unit_offsets(text)
                if unit_mode == "character"
                else self._reading_unit_offsets(text)
            )
        ]
        boundaries[0] = 0.0
        boundaries[-1] = float(width)
        self._glyph_cache_key = key
        self._glyph_cache = image, tuple(boundaries)
        self._glyph_run_cache[key] = self._glyph_cache
        self._glyph_run_cache_bytes += int(image.sizeInBytes())
        self._trim_emphasis_glyph_caches()
        return self._glyph_cache

    def _isolated_glyph_units(
        self,
        text: str,
        font: QFont,
        unit_mode: str = "reading",
    ) -> tuple[tuple[QImage, float], ...]:
        """Rasterize complete shaped words/graphemes with horizontal bleed.

        The full-line raster still supplies glyph advances, but moving units
        are drawn from their own images. A vertical slice through the full
        raster would cut an italic overhang or borrow ink from the next glyph.
        """
        units, _complete = self._advance_isolated_glyph_units(text, font, unit_mode)
        return units

    def _advance_isolated_glyph_units(
        self,
        text: str,
        font: QFont,
        unit_mode: str = "reading",
        *,
        max_units: int | None = None,
    ) -> tuple[tuple[tuple[QImage, float], ...], bool]:
        """Build a bounded part of a line so prewarming cannot block one frame."""
        key = (text, font.toString(), str(unit_mode))
        previous_working_key = self._isolated_glyph_build_last_key
        self._isolated_glyph_build_last_key = key
        cached = self._isolated_glyph_cache.get(key)
        if cached is not None:
            if (previous_working_key != key
                    and self._isolated_glyph_builds_bytes > self.ISOLATED_GLYPH_BUILD_MAX_BYTES):
                self._trim_emphasis_glyph_caches()
            return cached, True
        build = self._isolated_glyph_builds.get(key)
        if build is None:
            whole, boundaries = self._glyph_run(text, font, unit_mode)
            if whole.isNull():
                return (), True
            whole_layout = QTextLayout(text, font)
            self._apply_cjk_letter_spacing(whole_layout, text, font)
            whole_layout.beginLayout()
            whole_line = whole_layout.createLine()
            whole_layout.endLayout()
            offsets = (
                self._character_unit_offsets(text)
                if unit_mode == "character"
                else self._reading_unit_offsets(text)
            )
            build = {
                "boundaries": boundaries,
                "offsets": offsets,
                "baseline": whole_line.ascent() if whole_line.isValid() else QFontMetricsF(font).ascent(),
                "whole_height": whole.height(),
                "result": [],
                "next_index": 0,
                "bytes": 0,
            }
            self._isolated_glyph_builds[key] = build
            self._trim_emphasis_glyph_caches()
        offsets = build["offsets"]
        start_index = build["next_index"]
        end_index = len(offsets) - 1 if max_units is None else min(len(offsets) - 1, start_index + max(1, int(max_units)))
        for index in range(start_index, end_index):
            start, end = offsets[index], offsets[index + 1]
            segment = self._utf16_slice(text, start, end - start)
            layout = QTextLayout(segment, font)
            self._apply_cjk_letter_spacing(layout, segment, font)
            layout.beginLayout()
            line = layout.createLine()
            layout.endLayout()
            if not line.isValid():
                continue
            horizontal_bleed = max(6.0, float(max(1, font.pixelSize())) * 0.20)
            vertical_bleed = max(5.0, float(max(1, font.pixelSize())) * 0.12)
            line.setPosition(QPointF(horizontal_bleed, vertical_bleed + max(0.0, build["baseline"] - line.ascent())))
            image = QImage(
                max(1, math.ceil(line.naturalTextWidth() + horizontal_bleed * 2.0)),
                max(
                    build["whole_height"] + math.ceil(vertical_bleed * 2.0 + GLYPH_FLOAT_LIFT),
                    math.ceil(line.y() + line.height() + vertical_bleed + GLYPH_FLOAT_LIFT),
                ),
                QImage.Format.Format_ARGB32_Premultiplied,
            )
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as layer:
                layer.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
                layer.setPen(Qt.GlobalColor.white)
                layout.draw(layer, QPointF())
            build["result"].append((image, build["boundaries"][index] - horizontal_bleed))
            size = int(image.sizeInBytes())
            build["bytes"] += size
            self._isolated_glyph_builds_bytes += size
            if key in self._emphasis_protected_glyph_runs:
                self._emphasis_protected_source_keys.add(image.cacheKey())
        build["next_index"] = end_index
        output = tuple(build["result"])
        complete = end_index >= len(offsets) - 1
        if complete:
            self._isolated_glyph_builds.pop(key, None)
            self._isolated_glyph_builds_bytes -= build["bytes"]
            self._isolated_glyph_cache[key] = output
            self._isolated_glyph_cache_bytes += build["bytes"]
        self._trim_emphasis_glyph_caches()
        return output, complete

    def _trim_emphasis_glyph_caches(self):
        """Bound cold pixels, retaining the current row and one working run.

        Arbitrarily long visible lyrics are a separate measured working set;
        evicting them would make a ready highlight fall back on every frame.
        The single last run/build is also needed to finish bounded prewarm.
        """
        protected = self._emphasis_protected_glyph_runs
        for cache, byte_name, count_limit, byte_limit, working, size_of in (
            (self._glyph_run_cache, "_glyph_run_cache_bytes", self.GLYPH_RUN_CACHE_MAX_ENTRIES,
             self.GLYPH_RUN_CACHE_MAX_BYTES, self._glyph_cache_key, lambda value: int(value[0].sizeInBytes())),
            (self._isolated_glyph_cache, "_isolated_glyph_cache_bytes", self.ISOLATED_GLYPH_CACHE_MAX_ENTRIES,
             self.ISOLATED_GLYPH_CACHE_MAX_BYTES, None, lambda value: sum(int(image.sizeInBytes()) for image, _left in value)),
            (self._isolated_glyph_builds, "_isolated_glyph_builds_bytes", self.ISOLATED_GLYPH_BUILD_MAX_ENTRIES,
             self.ISOLATED_GLYPH_BUILD_MAX_BYTES, self._isolated_glyph_build_last_key, lambda value: value["bytes"]),
        ):
            while len(cache) > count_limit or getattr(self, byte_name) > byte_limit:
                oldest = next((key for key in cache if key not in protected and key != working), None)
                if oldest is None:
                    break
                setattr(self, byte_name, getattr(self, byte_name) - size_of(cache.pop(oldest)))

    def _tinted_glyph_image(self, source: QImage, color: QColor) -> QImage:
        opaque = QColor(color)
        opaque.setAlpha(255)
        key = (source.cacheKey(), opaque.rgba())
        if (self._emphasis_protected_tint_color is None
                and key[0] in self._emphasis_protected_source_keys):
            self._emphasis_protected_tint_color = key[1]
        cached = self._glyph_tint_cache.get(key)
        if cached is not None:
            return cached
        tinted = QImage(source.size(), QImage.Format.Format_ARGB32_Premultiplied)
        tinted.fill(opaque)
        with QPainter(tinted) as layer:
            layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            layer.drawImage(0, 0, source)
        self._glyph_tint_cache[key] = tinted
        self._glyph_tint_cache_bytes += tinted.sizeInBytes()
        self._trim_emphasis_image_caches()
        return tinted

    def _sparse_glyph_halo(self, source: QImage) -> tuple[QImage, float]:
        """Cache a soft white light field for one isolated grapheme."""
        key = source.cacheKey()
        cached = self._sparse_glyph_halo_cache.get(key)
        if cached is not None:
            return cached
        padding = max(12.0, source.height() * 0.28)
        logical_size = QSize(
            int(math.ceil(source.width() + 2.0 * padding)),
            int(math.ceil(source.height() + 2.0 * padding)),
        )
        reduction = 0.35
        small = QSize(
            max(1, int(math.ceil(logical_size.width() * reduction))),
            max(1, int(math.ceil(logical_size.height() * reduction))),
        )
        ink = QImage(small, QImage.Format.Format_ARGB32_Premultiplied)
        ink.fill(Qt.GlobalColor.transparent)
        with QPainter(ink) as shape:
            shape.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            shape.scale(reduction, reduction)
            shape.drawImage(QPointF(padding, padding), source)
        scattered = QImage(small, QImage.Format.Format_ARGB32_Premultiplied)
        scattered.fill(Qt.GlobalColor.transparent)
        with QPainter(scattered) as light:
            for stride, opacity in ((2, 0.012), (1, 0.037)):
                light.setOpacity(opacity)
                for y in range(-2, 3):
                    for x in range(-2, 3):
                        light.drawImage(QPointF(x * stride, y * stride), ink)
        sprite = scattered.scaled(
            logical_size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        result = sprite, padding
        self._sparse_glyph_halo_cache[key] = result
        self._sparse_glyph_halo_cache_bytes += sprite.sizeInBytes()
        self._trim_emphasis_image_caches()
        return result

    def _trim_emphasis_image_caches(self) -> None:
        """Keep cold image caches bounded without evicting the playing glow.

        The current lyric is a working set, retained until the row changes.
        Upcoming rows must not evict it and make a ready effect fall back to
        ordinary reading. A row's working set may exceed the cold-cache
        budget; changing rows releases that protection and trims it again.
        """
        while (self._glyph_tint_cache_bytes > self.GLYPH_TINT_CACHE_MAX_BYTES
               or len(self._glyph_tint_cache) > self.GLYPH_TINT_CACHE_MAX_ENTRIES):
            oldest = next((
                key for key in self._glyph_tint_cache
                if (key[0] not in self._emphasis_protected_source_keys
                    or key[1] != self._emphasis_protected_tint_color)
            ), None)
            if oldest is None:
                break
            self._glyph_tint_cache_bytes -= self._glyph_tint_cache.pop(oldest).sizeInBytes()
        while (self._sparse_glyph_halo_cache_bytes > self.SPARSE_GLYPH_HALO_CACHE_MAX_BYTES
               or len(self._sparse_glyph_halo_cache) > self.SPARSE_GLYPH_HALO_CACHE_MAX_ENTRIES):
            oldest = next((
                key for key in self._sparse_glyph_halo_cache
                if key not in self._emphasis_protected_source_keys
            ), None)
            if oldest is None:
                break
            old, _padding = self._sparse_glyph_halo_cache.pop(oldest)
            self._sparse_glyph_halo_cache_bytes -= old.sizeInBytes()

    def _protect_lyric_emphasis_assets(
        self, text: str, font: QFont, layout: QTextLayout | None, *, unread_color: QColor | None = None,
    ) -> None:
        """Pin exactly the current effect's shaped runs before future prewarm."""
        color = QColor(unread_color) if unread_color is not None else None
        if color is not None:
            color.setAlpha(255)
        token = (str(text), font.toString(), id(layout), color.rgba() if color is not None else None) if layout is not None else None
        if token == self._emphasis_protected_token:
            return
        self._emphasis_protected_token = token
        self._emphasis_protected_tint_color = color.rgba() if color is not None else None
        keys = frozenset(
            (
                self._utf16_slice(text, layout.lineAt(index).textStart(), layout.lineAt(index).textLength()),
                font.toString(), "character",
            )
            for index in range(layout.lineCount())
        ) if layout is not None else frozenset()
        self._emphasis_protected_glyph_runs = keys
        # Include a partially built current run when media time enters the
        # row before its bounded prewarm has reached the final character.
        self._emphasis_protected_source_keys = {
            source.cacheKey()
            for key in keys
            for source, _left in (
                self._isolated_glyph_cache.get(key)
                or self._isolated_glyph_builds.get(key, {}).get("result", ())
            )
        }
        if keys and all(key in self._isolated_glyph_cache for key in keys):
            self._emphasis_protected_source_keys.update(
                source.cacheKey()
                for key in keys
                for source, _left in self._isolated_glyph_cache[key]
            )
        self._trim_emphasis_glyph_caches()
        self._trim_emphasis_image_caches()

    @staticmethod
    def _read_wipe_image(
        source: QImage, fraction: float, *, start: float = 0.0, width: float | None = None,
        feather_width: float | None = None,
    ) -> QImage:
        """Mask a glyph once so fractional strip edges cannot leave vertical seams."""
        fraction = max(0.0, min(1.0, float(fraction)))
        if source.isNull() or fraction >= 1.0:
            return source
        dpr = max(1.0, float(source.devicePixelRatio()))
        logical_width = source.width() / dpr
        start = max(0.0, min(logical_width, float(start)))
        read_width = max(0.0, min(logical_width - start, float(logical_width - start if width is None else width)))
        masked = source.copy()
        if fraction <= 0.0 or read_width <= 0.0:
            masked.fill(Qt.GlobalColor.transparent)
            return masked
        # The reading front should span neighboring letters, leaving several
        # visible intermediate tones between the album tint and white.
        feather = (
            min(160.0, max(30.0, read_width * 0.42))
            if feather_width is None else max(1.0 / dpr, float(feather_width))
        )
        # The unfeathered reading position is the fully white boundary.
        # Widening the transition adds color *ahead* of that position; it must
        # never turn a previously read character back into a partial tint.
        core = read_width * fraction
        edge = core + feather
        if read_width - core <= 1.0 / dpr:
            return source
        gradient = QLinearGradient(0.0, 0.0, logical_width, 0.0)
        clear = QColor(0, 0, 0, 0)
        opaque = QColor(0, 0, 0, 255)
        left_alpha = round(255.0 * max(0.0, min(1.0, edge / feather)))
        right_alpha = round(255.0 * max(0.0, min(1.0, (edge - read_width) / feather)))
        if start > 0.0:
            gradient.setColorAt(0.0, clear)
            gradient.setColorAt(max(0.0, start - 1.0 / dpr) / logical_width, clear)
        gradient.setColorAt(start / logical_width, QColor(0, 0, 0, left_alpha))
        if 0.0 < core < read_width:
            gradient.setColorAt((start + core) / logical_width, opaque)
        if 0.0 < edge < read_width:
            gradient.setColorAt((start + edge) / logical_width, clear)
        gradient.setColorAt((start + read_width) / logical_width, QColor(0, 0, 0, right_alpha))
        if start + read_width < logical_width:
            gradient.setColorAt(min(logical_width, start + read_width + 1.0 / dpr) / logical_width, clear)
        with QPainter(masked) as mask:
            mask.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            mask.fillRect(QRectF(0.0, 0.0, logical_width, source.height() / dpr), gradient)
        return masked

    @staticmethod
    def _read_wipe_image_at_front(
        source: QImage, *, front: float, feather: float, x_offset: float,
    ) -> QImage:
        return read_wipe_image_at_front(source, front=front, feather=feather, x_offset=x_offset)

    @staticmethod
    def _character_span_feather(text: str, font: QFont, steps: int) -> float:
        """Bound the color transition by actual Latin/CJK ink advances."""
        steps = normalize_music_reading_gradient_steps(steps)
        if steps == 0:
            return 0.0
        metrics = QFontMetricsF(font)
        latin = "".join(character for character in text if character.isalnum() and not is_cjk_cluster(character))
        cjk = "".join(character for character in text if is_cjk_cluster(character))
        latin_average = metrics.horizontalAdvance(latin or "shine") / max(1, len(latin or "shine"))
        cjk_average = metrics.horizontalAdvance(cjk or "中文") / max(1, len(cjk or "中文"))
        latin_span = latin_average * 3.0
        cjk_span = cjk_average * 2.0
        latin_ink = metrics.horizontalAdvance(latin) if latin else 0.0
        cjk_ink = metrics.horizontalAdvance(cjk) if cjk else 0.0
        occupied = latin_ink + cjk_ink
        if occupied <= 0.0:
            maximum = latin_span
        else:
            maximum = (latin_ink * latin_span + cjk_ink * cjk_span) / occupied
        return max(0.0, maximum * steps / 30.0)

    def _reading_front(
        self, progress: float, line_width: float, font: QFont, text: str = "",
    ) -> tuple[float, float]:
        if not text:
            return reading_front(progress, line_width, font)
        steps = normalize_music_reading_gradient_steps(getattr(
            self.owner, "music_surface_reading_gradient_steps", MUSIC_READING_GRADIENT_DEFAULT_STEPS,
        ))
        if steps == 0:
            feather = 0.0
        else:
            # Cache the maximum three-Latin/two-CJK span once per shaped line.
            # Scaling that span is arithmetic, so dragging the settings slider
            # no longer creates 31 cache entries per line or evicts the first
            # songs' glyph metrics while they are playing.
            key = (str(text), font.toString(), 30)
            maximum = self._reading_feather_cache.get(key)
            if maximum is None:
                maximum = self._character_span_feather(text, font, 30)
                self._reading_feather_cache[key] = maximum
                if len(self._reading_feather_cache) > 128:
                    self._reading_feather_cache.pop(next(iter(self._reading_feather_cache)))
            feather = maximum * steps / 30.0
        fraction = max(0.0, min(1.0, float(progress)))
        # The hard-edge position stays fixed as the slider changes.  The
        # gradient reaches forward into unread text from that position.
        return fraction * max(0.0, float(line_width)) + feather, feather

    def _draw_glyph_read_wipe(
        self, painter: QPainter, source: QImage, origin: QPointF,
        fraction: float, opacity: float,
    ):
        if fraction <= 0.0 or source.isNull():
            return
        painter.setOpacity(opacity)
        painter.drawImage(origin, self._read_wipe_image(source, fraction))

    @staticmethod
    def _draw_subpixel_lyric_image(painter: QPainter, origin: QPointF, image: QImage):
        """Keep the small baseline lift visible between whole pixel positions.

        Qt's 1:1 image blit rounds a fractional QPointF to an integer pixel,
        even with SmoothPixmapTransform enabled. A 1/64 px taller destination
        selects its smooth sampler without visibly changing the glyph shape.
        """
        dpr = max(1.0, float(image.devicePixelRatio()))
        painter.drawImage(QRectF(
            origin.x(), origin.y(), image.width() / dpr,
            image.height() / dpr + 1.0 / 64.0,
        ), image)

    def _draw_continuous_read_unit(
        self, painter: QPainter, source: QImage, origin: QPointF,
        left: float, front: float, feather: float,
        unread_color: QColor, base_opacity: float, *, subpixel_lift: bool = False,
        stable_subpixel_x: bool = False,
        trailing_bleed: float = 0.0,
        read_color: QColor | None = None,
    ):
        """Compose album tint and white through one line-wide moving front."""
        # Keep the sampling mode stable before, during and after a word lift.
        smooth_blit = subpixel_lift or stable_subpixel_x
        # An isolated word image has transparent padding on both sides. The
        # reading front follows the shaped line, so the padding after the last
        # ink must not keep its unread layer alive at 100% line progress.
        unit_right = left + source.width() / max(1.0, float(source.devicePixelRatio())) - max(0.0, trailing_bleed)
        if front < unit_right + feather:
            painter.setOpacity(base_opacity * unread_color.alphaF())
            tinted = self._tinted_glyph_image(source, unread_color)
            if smooth_blit:
                self._draw_subpixel_lyric_image(painter, origin, tinted)
            else:
                painter.drawImage(origin, tinted)
        # A wrapped row with zero local progress still has a forward feather.
        # Only reveal white after its hard read boundary has entered the row;
        # otherwise every waiting visual row would start with a white prefix.
        if front > feather and front > left:
            painter.setOpacity(base_opacity * (read_color.alphaF() if read_color is not None else 1.0))
            read_source = self._tinted_glyph_image(source, read_color) if read_color is not None else source
            read = self._read_wipe_image_at_front(
                read_source, front=front, feather=feather, x_offset=left,
            )
            if smooth_blit:
                self._draw_subpixel_lyric_image(painter, origin, read)
            else:
                painter.drawImage(origin, read)

    def _reading_lift_height(self) -> float:
        level = normalize_music_reading_lift_level(getattr(
            self.owner, "music_surface_reading_lift_level", MUSIC_READING_LIFT_DEFAULT_LEVEL,
        ))
        return GLYPH_FLOAT_LIFT * level / 4.0

    def _paint_sparse_glyph_run(
        self, painter: QPainter, origin: QPointF, line_height: float,
        text: str, font: QFont, progress: float, unread_color: QColor,
        glyph_states: tuple[SparseGlyphFrame, ...] | None = None,
        *, wave_progress: float | None = None,
        wave_unit_indices: tuple[int, ...] = (),
        wave_unit_count: int | None = None,
    ):
        """Grow and illuminate each grapheme, then rest white on its lifted baseline."""
        image, _boundaries = self._glyph_run(text, font, "character")
        units = self._isolated_glyph_units(text, font, "character")
        if image.isNull() or not units:
            return
        lift_scale = max(0.5, float(max(1, font.pixelSize())) / float(self.LYRIC_PIXEL_SIZE))
        lift_height = self._reading_lift_height()
        lift_fraction = lift_height / GLYPH_FLOAT_LIFT
        trailing_bleed = max(6.0, float(max(1, font.pixelSize())) * 0.20)
        top = origin.y() + (line_height - image.height()) / 2.0 + lift_height * lift_scale
        base_opacity = painter.opacity()
        read_front, read_feather = self._reading_front(progress, image.width(), font, text)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        baseline_local = max(5.0, float(max(1, font.pixelSize())) * 0.12) + QFontMetricsF(font).ascent()
        explicit_states = glyph_states is not None and len(glyph_states) == len(units)
        # A genuinely timed slow word can share its row with dozens of quiet
        # characters. Only its growing/lifting glyphs and the reading edge need
        # individual blits. Keep the resting, fully read/unread runs together;
        # the edge itself still uses the existing line-wide gradient below.
        can_batch_static = (
            explicit_states and abs(base_opacity - 1.0) < 1e-9
            and painter.compositionMode() == QPainter.CompositionMode.CompositionMode_SourceOver
        )
        static_modes: list[str | None] = []
        if can_batch_static:
            for (source, left), state in zip(units, glyph_states):
                unit_front = read_front
                if state.highlighted and state.read_fraction > 0.0:
                    unit_front = max(
                        unit_front,
                        left + source.width() / max(1.0, source.devicePixelRatio())
                        * state.read_fraction + read_feather,
                    )
                if state.scale != 1.0 or state.glow > 0.001 or state.lift not in (0.0, GLYPH_FLOAT_LIFT):
                    static_modes.append(None)
                elif unit_front <= read_feather or unit_front <= left:
                    static_modes.append("unread")
                elif unit_front >= left + source.width() - trailing_bleed + read_feather:
                    static_modes.append("read")
                else:
                    static_modes.append(None)
        index = 0
        while index < len(units):
            source, left = units[index]
            if can_batch_static and static_modes[index] is not None:
                mode = static_modes[index]
                resting_lift = glyph_states[index].lift
                end = index + 1
                while (
                    end < len(units) and static_modes[end] == mode
                    and glyph_states[end].lift == resting_lift
                    and units[end][1] >= units[end - 1][1]
                ):
                    end += 1
                if end - index >= 3 and self._draw_sparse_static_run(
                    painter, units[index:end],
                    QPointF(origin.x(), top - resting_lift * lift_fraction * lift_scale),
                    unread_color if mode == "unread" else None,
                ):
                    index = end
                    continue
            state = (
                glyph_states[index]
                if explicit_states
                else sparse_glyph_frame(
                    progress if wave_progress is None else wave_progress,
                    wave_unit_indices[index] if index < len(wave_unit_indices) else index,
                    len(units) if wave_unit_count is None else wave_unit_count,
                )
            )
            unit_front = read_front
            if state.highlighted and state.read_fraction > 0.0:
                unit_front = max(
                    unit_front,
                    left + source.width() / max(1.0, source.devicePixelRatio())
                    * state.read_fraction + read_feather,
                )
            x = origin.x() + left
            # The sparse highlight has its own eight-stage motion clock.  The
            # white reading front below can change without retiming the wave
            # or moving a glyph back down after its glow has passed.
            y = top - state.lift * lift_fraction * lift_scale
            subpixel_lift = lift_height > 0.0 and 0.0 < state.lift < GLYPH_FLOAT_LIFT
            center = QPointF(x + source.width() * 0.5, y + baseline_local)
            painter.save()
            painter.translate(center)
            painter.scale(state.scale, state.scale)
            local = QPointF(-source.width() * 0.5, -baseline_local)
            if state.glow > 0.001 and (not state.highlighted or state.read_fraction >= 1.0 - 1e-9):
                halo, padding = self._sparse_glyph_halo_cache[source.cacheKey()]
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
                painter.setOpacity(base_opacity * state.glow)
                halo_origin = local - QPointF(padding, padding)
                if lift_height > 0.0:
                    self._draw_subpixel_lyric_image(painter, halo_origin, halo)
                else:
                    painter.drawImage(halo_origin, halo)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            self._draw_continuous_read_unit(
                painter, source, local, left, unit_front, read_feather,
                unread_color, base_opacity, subpixel_lift=subpixel_lift,
                stable_subpixel_x=lift_height > 0.0,
                trailing_bleed=trailing_bleed,
            )
            painter.restore()
            index += 1

    def _draw_sparse_static_run(
        self, painter: QPainter, units: tuple[tuple[QImage, float], ...],
        origin: QPointF, unread_color: QColor | None,
    ) -> bool:
        """Reuse quiet glyphs at their exact device-pixel phase.

        Build with the same smooth sampler and glyph coordinates as separate
        blits, then copy at native resolution. Rotation, scaling, fading and
        the reading feather remain on the original per-glyph path.
        """
        transform = painter.deviceTransform()
        dpr = transform.m11()
        if (
            dpr <= 0.0 or abs(transform.m22() - dpr) > 1e-9
            or abs(transform.m12()) > 1e-9 or abs(transform.m21()) > 1e-9
            or any(source.devicePixelRatio() != 1.0 for source, _left in units)
            or any(units[index][1] < units[index - 1][1] for index in range(1, len(units)))
        ):
            return False
        first = transform.map(origin + QPointF(units[0][1], 0.0))
        left_pixel = math.floor(first.x()) - 1
        top_pixel = math.floor(first.y()) - 1
        right = max(left + source.width() for source, left in units)
        end = transform.map(origin + QPointF(right, max(source.height() for source, _left in units)))
        width = max(1, math.ceil(end.x()) - left_pixel + 1)
        height = max(1, math.ceil(end.y()) - top_pixel + 1)
        local_x = (first.x() - left_pixel) / dpr
        local_y = (first.y() - top_pixel) / dpr
        key = (
            tuple((source.cacheKey(), left - units[0][1]) for source, left in units),
            unread_color.rgba() if unread_color is not None else None,
            self._reading_lift_height() > 0.0,
            round(dpr, 6), round(local_x, 6), round(local_y, 6), width, height,
        )
        image = self._sparse_static_run_cache.pop(key, None)
        if image is None:
            image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(dpr)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as shape:
                shape.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                if unread_color is not None:
                    shape.setOpacity(unread_color.alphaF())
                for source, left in units:
                    image_at = QPointF(local_x + left - units[0][1], local_y)
                    glyph = self._tinted_glyph_image(source, unread_color) if unread_color is not None else source
                    if self._reading_lift_height() > 0.0:
                        self._draw_subpixel_lyric_image(shape, image_at, glyph)
                    else:
                        shape.drawImage(image_at, glyph)
            self._sparse_static_run_cache_bytes += image.sizeInBytes()
        self._sparse_static_run_cache[key] = image
        while len(self._sparse_static_run_cache) > 64 or self._sparse_static_run_cache_bytes > 16 * 1024 * 1024:
            _old_key, old = self._sparse_static_run_cache.popitem(last=False)
            self._sparse_static_run_cache_bytes -= old.sizeInBytes()
        inverse, invertible = transform.inverted()
        if not invertible:
            return False
        painter.setOpacity(1.0)
        painter.drawImage(inverse.map(QPointF(left_pixel, top_pixel)), image)
        return True

    def _paint_lifted_run(
        self,
        painter: QPainter,
        origin: QPointF,
        line_height: float,
        text: str,
        font: QFont,
        progress: float,
        unread_color: QColor | None = None,
        unit_mode: str = "reading",
        *,
        lift_progress: float | None = None,
        lift_unit_indices: tuple[int, ...] = (),
        lift_unit_count: int | None = None,
        read_color: QColor | None = None,
    ):
        image, boundaries = self._glyph_run(text, font, unit_mode)
        if image.isNull() or len(boundaries) < 2:
            return
        units = self._isolated_glyph_units(text, font, unit_mode)
        if not units:
            return
        unread_color = QColor(unread_color or QColor(235, 237, 241, 178))
        lift_scale = max(0.5, float(max(1, font.pixelSize())) / float(self.LYRIC_PIXEL_SIZE))
        lift_height = self._reading_lift_height()
        trailing_bleed = max(6.0, float(max(1, font.pixelSize())) * 0.20)
        # Unread glyphs begin below the normal baseline. They rise once and
        # settle exactly on the normal layout baseline, so the completed line
        # never drops when it stops being active.
        top = origin.y() + (line_height - image.height()) / 2.0 + lift_height * lift_scale
        base_opacity = painter.opacity()
        read_front, read_feather = self._reading_front(progress, image.width(), font, text)
        wave_progress = progress if lift_progress is None else lift_progress
        wave_count = len(units) if lift_unit_count is None else max(1, lift_unit_count)

        # Long wrapped verses contain many units that have already landed or
        # have not started moving. Reusing the existing bounded, pixel-phase
        # composite for those quiet spans avoids a native-DPR image blit for
        # every word on every timer paint. Only a span with one resting lift
        # and one settled color may be combined; the actual moving front and
        # its neighboring followers remain separate layers.
        lifts = tuple(
            lift_height * continuous_read_lift(
                wave_progress,
                lift_unit_indices[index] if index < len(lift_unit_indices) else index,
                wave_count,
            )
            for index in range(len(units))
        )
        can_batch_quiet = (
            len(units) >= 2 and abs(base_opacity - 1.0) < 1e-9
            and painter.compositionMode() == QPainter.CompositionMode.CompositionMode_SourceOver
            # The composite cache keys include exact device-pixel phase.
            # Building a fresh large bitmap for every spring position would
            # turn one smooth group scroll into a costly allocation stream.
            and not self._lyric_spring_active
        )
        quiet_modes: list[str | None] = []
        if can_batch_quiet:
            for (source, left), lift in zip(units, lifts):
                if lift not in (0.0, lift_height):
                    quiet_modes.append(None)
                elif read_front <= read_feather or read_front <= left:
                    quiet_modes.append("unread")
                elif read_front >= left + source.width() / max(1.0, source.devicePixelRatio()) + read_feather:
                    quiet_modes.append("read")
                else:
                    quiet_modes.append(None)

        # Keep separate shaped layers for the vertical lift, but sample color
        # from a single horizontal front. A word/grapheme boundary never
        # restarts the color interpolation or inserts a pause.
        if lift_height > 0.0:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        index = 0
        while index < len(units):
            source, left = units[index]
            # Neighboring words follow one overlapping rise wave. Color
            # retains its source-clock front; the vertical wave can start a
            # nearby unread word while its predecessor is still rising.
            lift = lifts[index]
            if can_batch_quiet and quiet_modes[index] is not None:
                mode = quiet_modes[index]
                end = index + 1
                while (
                    end < len(units) and quiet_modes[end] == mode
                    and lifts[end] == lift and units[end][1] >= units[end - 1][1]
                ):
                    end += 1
                if end - index >= 2 and self._draw_sparse_static_run(
                    painter, units[index:end],
                    QPointF(origin.x(), top - lift * lift_scale),
                    unread_color if mode == "unread" else read_color,
                ):
                    index = end
                    continue
            origin_at = QPointF(origin.x() + left, top - lift * lift_scale)
            subpixel_lift = 0.0 < lift < lift_height
            self._draw_continuous_read_unit(
                painter, source, origin_at, left, read_front, read_feather,
                unread_color, base_opacity,
                subpixel_lift=subpixel_lift,
                stable_subpixel_x=lift_height > 0.0,
                trailing_bleed=trailing_bleed,
                read_color=read_color,
            )
            index += 1
        if lift_height > 0.0:
            painter.restore()
        else:
            painter.setOpacity(base_opacity)

    def _paint_lifted_lyric(self, painter: QPainter, row: QRectF, text: str, font: QFont, progress: float):
        self._paint_lifted_run(painter, row.topLeft(), row.height(), text, font, progress)

    @staticmethod
    def _intro_dot_radius(font: QFont) -> float:
        return max(4.0, font.pixelSize() * 0.17)

    @classmethod
    def _lyric_blur_radius(cls, level: int) -> float:
        return cls.LYRIC_BLUR_RADII[max(0, min(2, int(level)))]

    @classmethod
    def _lyric_blur_bleed(cls, level: float) -> float:
        # A fractional degree blends the two adjacent cached rasters. Keep
        # enough room for the wider one throughout the transition.
        return math.ceil(cls._lyric_blur_radius(math.ceil(level)) * 3.0)

    @staticmethod
    def _lyric_blur_layers(level: float) -> tuple[tuple[int, float], ...]:
        degree = max(0.0, min(2.0, float(level)))
        lower = int(math.floor(degree))
        fraction = degree - lower
        if fraction <= 0.000001:
            return ((lower, 1.0),)
        return ((lower, 1.0 - fraction), (lower + 1, fraction))

    @staticmethod
    def _lyric_group_blur_level(row: int, focus: int, pair_starts: tuple[int, ...] = ()) -> int:
        row_group = row - bisect_left(pair_starts, row)
        focus_group = focus - bisect_left(pair_starts, focus)
        distance = abs(row_group - focus_group)
        return 0 if distance == 0 else 1 if distance <= 2 else 2

    def _lyric_blur_follow_levels(
        self, token: object, focus: int, positions: tuple[float, ...],
        targets: tuple[float, ...], visual_ranges: tuple[tuple[int, int], ...],
        *, pair_starts: tuple[int, ...] = (),
    ) -> tuple[tuple[float, ...], bool]:
        """Fade cached focus degrees only after the position spring moves.

        Timing/focus changes describe the destination, not a reason to blur
        before text starts scrolling. Each sentence (or duet group) therefore
        derives its blend from actual travel rather than a separate timer.
        This also preserves an in-flight fade when translations are toggled.
        """
        state = getattr(self, "_lyric_blur_follow_state", None)
        resetting = state is None or state["token"] != token or len(state["positions"]) != len(visual_ranges)
        if resetting:
            pair_set = frozenset(pair_starts)
            groups = tuple(row - 1 if row - 1 in pair_set else row for row in range(len(visual_ranges)))
            visual_indices = tuple(visual_ranges[group][0] for group in groups)
        else:
            visual_indices = state["visual_indices"]
        row_positions = tuple(positions[index] for index in visual_indices)
        desired = (
            tuple(float(self._lyric_group_blur_level(row, focus, pair_starts)) for row in range(len(visual_ranges)))
            if resetting or state["focus"] != focus else state["desired"]
        )
        if self._browse_index is not None or self._browse_return_from is not None:
            # Wheel browsing makes every visible sentence readable, including
            # while the existing viewport animation returns to the real clock.
            # Keep current positions so leaving browse cannot revive an old
            # playback focus or restart a blur fade from stale geometry.
            clear = (0.0,) * len(visual_ranges)
            self._lyric_blur_follow_state = {
                "token": token, "focus": focus, "positions": row_positions,
                "origins": row_positions, "from": clear, "levels": clear,
                "progress": [1.0] * len(visual_ranges), "moving": True,
                "visual_indices": visual_indices, "desired": desired,
                "browse_clear": True,
            }
            return clear, True
        restoring_browse = not resetting and bool(state.pop("browse_clear", False))
        if restoring_browse:
            # A click or completed automatic return hands the column back to
            # the ordinary physical-travel blend, starting with its clear ink.
            state["focus"] = None
        if resetting:
            self._lyric_blur_follow_state = {
                "token": token, "focus": focus, "positions": row_positions,
                "origins": row_positions, "from": desired, "levels": desired,
                "progress": [1.0] * len(visual_ranges), "moving": True,
                "visual_indices": visual_indices, "desired": desired,
            }
            return desired, True
        if state["focus"] != focus:
            state["focus"] = focus
            state["desired"] = desired
            state["origins"] = state["positions"]
            state["from"] = state["levels"]
            state["progress"] = [0.0] * len(visual_ranges)
            state["moving"] = False
        elif all(progress >= 1.0 for progress in state["progress"]):
            state["positions"] = row_positions
            return state["levels"], state["moving"]
        row_targets = tuple(targets[index] for index in visual_indices)
        if any(abs(position - origin) > 0.025 for position, origin in zip(row_positions, state["origins"])):
            state["moving"] = True
        levels = []
        for row, (position, target, origin) in enumerate(zip(row_positions, row_targets, state["origins"])):
            travel = target - origin
            progress = 1.0 if abs(travel) < 0.025 else max(0.0, min(1.0, (position - origin) / travel))
            # Elastic overshoot or a growing annotation reservation must not
            # make already-clearing text become blurry for a second time.
            progress = max(state["progress"][row], progress)
            state["progress"][row] = progress
            blend = progress * progress * (3.0 - 2.0 * progress)
            levels.append(
                0.0 if desired[row] == 0.0 and state["moving"] else
                state["from"][row] * (1.0 - blend) + desired[row] * blend
            )
        state["levels"] = tuple(levels)
        state["positions"] = row_positions
        if restoring_browse and all(progress >= 1.0 for progress in state["progress"]):
            # The viewport may already be centred (or the player may be
            # paused), so no further spring movement is required for reading.
            state["moving"] = True
        return state["levels"], state["moving"]

    def _paint_intro_countdown(
        self, painter: QPainter, row: QRectF, font: QFont, progress: float,
        blur_level: int = 0,
        *, unread_color: QColor | None = None,
    ):
        """Keep the three countdown dots equally sized across clear and blurred rows."""
        layers = self._lyric_blur_layers(blur_level)
        if len(layers) > 1:
            opacity = painter.opacity()
            for degree, weight in layers:
                painter.save()
                painter.setOpacity(opacity * weight)
                self._paint_intro_countdown(
                    painter, row, font, progress, degree, unread_color=unread_color,
                )
                painter.restore()
            return
        radius = self._intro_dot_radius(font)
        degree = max(0, min(2, int(blur_level)))
        unread = QColor(unread_color) if unread_color is not None else self._future_lyric_color(row)
        if degree:
            dpr = max(1.0, float(self.devicePixelRatioF()))
            ink = unread if progress <= 0.0 else QColor(255, 255, 255)
            key = (("intro-countdown", font.pixelSize(), round(dpr, 3), int(ink.rgba())), degree)
            cached = self._lyric_blur_cache.get(key)
            if cached is None:
                dot_radius = radius * (1.15 if degree == 1 else 1.30)
                stride = radius * 3.35
                bleed = self._lyric_blur_bleed(degree)
                width = 2.0 * dot_radius + 2.0 * stride + 2.0 * bleed
                height = 2.0 * dot_radius + 2.0 * bleed
                source = QImage(
                    max(1, int(math.ceil(width * dpr))),
                    max(1, int(math.ceil(height * dpr))),
                    QImage.Format.Format_ARGB32_Premultiplied,
                )
                source.setDevicePixelRatio(dpr)
                source.fill(Qt.GlobalColor.transparent)
                with QPainter(source) as layer:
                    layer.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    layer.setPen(Qt.PenStyle.NoPen)
                    layer.setBrush(ink)
                    for index in range(3):
                        layer.drawEllipse(
                            QPointF(bleed + dot_radius + index * stride, bleed + dot_radius),
                            dot_radius, dot_radius,
                        )
                image = self._blurred_lyric_raster(
                    source, degree, ink, cache_key=key,
                )
            else:
                image = cached[0]
                dot_radius = radius * (1.15 if degree == 1 else 1.30)
                bleed = self._lyric_blur_bleed(degree)
            painter.drawImage(
                QPointF(
                    row.left() + radius * 1.2 - dot_radius - bleed,
                    row.top() + row.height() * 0.48 - dot_radius - bleed,
                ),
                image,
            )
            return
        stride = radius * 3.35
        center_y = row.top() + row.height() * 0.48
        origin_x = row.left() + radius * 1.2
        front = max(0.0, min(1.0, float(progress))) * 3.0
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(3):
            local = max(0.0, min(1.0, front - index))
            eased = local * local * (3.0 - 2.0 * local)
            painter.setBrush(QColor(
                round(unread.red() + (255 - unread.red()) * eased),
                round(unread.green() + (255 - unread.green()) * eased),
                round(unread.blue() + (255 - unread.blue()) * eased),
                round(unread.alpha() + (255 - unread.alpha()) * eased),
            ))
            painter.drawEllipse(QPointF(origin_x + index * stride, center_y), radius, radius)
        painter.restore()

    def _wrapped_lyric_runs(
        self,
        text: str,
        font: QFont,
        layout: QTextLayout,
        unit_mode: str = "reading",
    ) -> list[tuple[str, float, float, float, int]]:
        runs: list[tuple[str, float, float, float, int]] = []
        for line_index in range(layout.lineCount()):
            line = layout.lineAt(line_index)
            segment = self._utf16_slice(text, line.textStart(), line.textLength())
            _image, boundaries = self._glyph_run(segment, font, unit_mode)
            runs.append((
                segment,
                line.x(),
                line.y(),
                line.height(),
                max(0, len(boundaries) - 1),
            ))
        return runs

    @staticmethod
    def _has_reversed_lyric_unit_order(text: str) -> bool:
        """Keep bidi text on Qt's intact shaping path until units support it."""
        return any(unicodedata.bidirectional(character) in {"R", "AL", "AN"} for character in text)

    @staticmethod
    def _visual_line_progresses(progress: float, line_units: tuple[int, ...]) -> tuple[float, ...]:
        if not line_units:
            return ()
        units = tuple(max(1, int(value)) for value in line_units)
        front = max(0.0, min(1.0, float(progress))) * max(
            1.0,
            sum(units) - 1.0 + GLYPH_PULSE_SPAN,
        )
        progresses: list[float] = []
        consumed = 0.0
        for count in units:
            duration = max(1.0, count - 1.0 + GLYPH_PULSE_SPAN)
            progresses.append(max(0.0, min(1.0, (front - consumed) / duration)))
            consumed += count
        return tuple(progresses)

    def _real_visual_line_progresses(
        self, text: str, layout: QTextLayout, units: tuple[object, ...],
        position: float, *, line_end: float | None = None,
        source_text: str | None = None,
        visible_source_offsets: tuple[int, ...] | None = None,
    ) -> tuple[float, ...] | None:
        """Map genuine unit timestamps across wrapped original-text rows.

        The source units belong to the original lyric, never to a translation.
        Every QTextLayout line is mapped by its UTF-16 range to Python offsets
        before the pure timing helper samples the media clock.
        """
        primary = self._primary_timed_lyric_text(text)
        source_primary = self._primary_timed_lyric_text(source_text) if source_text is not None else primary
        front = timed_unit_reading_front(
            source_primary, units, position, tuple(range(len(source_primary) + 1)),
            line_end=line_end,
        )
        if front is None:
            return None
        if visible_source_offsets is not None:
            front = self._project_source_front(front, visible_source_offsets)
        progresses: list[float] = []
        for visual_index in range(layout.lineCount()):
            visual = layout.lineAt(visual_index)
            utf16_start = visual.textStart()
            segment = self._utf16_slice(text, utf16_start, visual.textLength())
            start = len(self._utf16_slice(text, 0, utf16_start))
            end = start + len(segment)
            if start >= len(primary):
                # Keep the optional translation stable during the source's
                # syllables; reveal it as a whole after the original completes.
                progresses.append(1.0 if front >= len(primary) else 0.0)
                continue
            end = min(end, len(primary))
            local_char_front = max(0.0, min(float(end - start), front - start))
            if local_char_front <= 0.0:
                progresses.append(0.0)
                continue
            if local_char_front >= end - start:
                progresses.append(1.0)
                continue
            char_index = int(local_char_front)
            fraction = local_char_front - char_index
            utf16_left = utf16_start + self._utf16_offset(segment, char_index)
            utf16_right = utf16_start + self._utf16_offset(segment, char_index + 1)
            x_left = float(visual.cursorToX(utf16_left)[0])
            x_right = float(visual.cursorToX(utf16_right)[0])
            x_origin = float(visual.cursorToX(utf16_start)[0])
            # A wrapped QTextLine omits trailing spaces from naturalTextWidth
            # and can cap cursorToX at its wrap width. Measure the standalone
            # segment just as _glyph_run does, without allocating its QImage.
            width_key = (segment, layout.font().toString())
            raster_width = self._reading_line_width_cache.get(width_key)
            if raster_width is None:
                width_layout = QTextLayout(segment, layout.font())
                self._apply_cjk_letter_spacing(width_layout, segment, layout.font())
                width_layout.beginLayout()
                width_line = width_layout.createLine()
                width_layout.endLayout()
                raster_width = max(1, math.ceil(width_line.naturalTextWidth()) + 5) if width_line.isValid() else 1
                self._reading_line_width_cache[width_key] = raster_width
                if len(self._reading_line_width_cache) > 128:
                    self._reading_line_width_cache.pop(next(iter(self._reading_line_width_cache)))
            pixel_front = max(0.0, x_left - x_origin + (x_right - x_left) * fraction)
            # _glyph_run places the ink at x=2; its hard reading boundary
            # includes that inset, while untouched rows remain exactly zero.
            progresses.append(max(0.0, min(1.0, (2.0 + pixel_front) / raster_width)))
        return tuple(progresses)

    def _real_sparse_glyph_states(
        self, segment: str, font: QFont, start_offset: int,
        groups: tuple[TimedHighlightGroup, ...], progress: float,
        position: float, *,
        wave_progress: float | None = None,
        wave_unit_indices: tuple[int, ...] = (),
        wave_unit_count: int | None = None,
    ) -> tuple[SparseGlyphFrame, ...]:
        """Preserve the eight-stage wave only on explicitly slow characters."""
        image, boundaries = self._glyph_run(segment, font, "character")
        if image.isNull():
            return ()
        mapping_key = (segment, start_offset, groups)
        mapping = self._sparse_timing_map_cache.get(mapping_key)
        if mapping is None:
            offsets = self._character_unit_offsets(segment)
            if len(offsets) != len(boundaries):
                return ()
            matches: list[tuple[TimedHighlightGroup, int, int] | None] = []
            group_boundaries: dict[TimedHighlightGroup, tuple[int, ...]] = {}
            for index, utf16_start in enumerate(offsets[:-1]):
                glyph_start = start_offset + len(self._utf16_slice(segment, 0, utf16_start))
                glyph_text = self._utf16_slice(segment, utf16_start, offsets[index + 1] - utf16_start)
                # The character painter attaches a following space to its
                # preceding letter. The timed highlight group deliberately
                # excludes that whitespace; it must not lose the last letter
                # of a word merely because its raster also owns the space.
                glyph_end = glyph_start + len(glyph_text.rstrip())
                matched = next((
                    group for group in groups
                    if group.start_offset <= glyph_start and glyph_end <= group.end_offset
                    and glyph_end > glyph_start
                ), None)
                if matched is None:
                    matches.append(None)
                    continue
                if matched not in group_boundaries:
                    group_text = matched.source_text
                    if group_text and len(group_text) == matched.end_offset - matched.start_offset:
                        group_boundaries[matched] = tuple(
                            len(self._utf16_slice(group_text, 0, utf16_offset))
                            for utf16_offset in self._character_unit_offsets(group_text)
                        )
                    else:
                        # Backward-compatible synthetic groups have no text.
                        # Their established codepoint map remains available.
                        group_boundaries[matched] = tuple(range(matched.end_offset - matched.start_offset + 1))
                cluster_boundaries = group_boundaries[matched]
                ordinal = max(0, bisect_right(cluster_boundaries, glyph_start - matched.start_offset) - 1)
                matches.append((matched, ordinal, max(1, len(cluster_boundaries) - 1)))
            mapping = tuple(matches)
            self._sparse_timing_map_cache[mapping_key] = mapping
            while len(self._sparse_timing_map_cache) > 64:
                self._sparse_timing_map_cache.pop(next(iter(self._sparse_timing_map_cache)))
        read_position = max(0.0, min(1.0, float(progress))) * image.width()
        states: list[SparseGlyphFrame] = []
        wave_progresses: dict[TimedHighlightGroup, float] = {}
        read_progresses: dict[TimedHighlightGroup, float] = {}
        for index, match in enumerate(mapping):
            if match is not None:
                matched, group_index, group_count = match
                if matched not in wave_progresses:
                    wave_progresses[matched] = timed_highlight_wave_progress(matched, position)
                    read_progresses[matched] = max(0.0, min(
                        1.0,
                        (position - matched.start_seconds)
                        / max(1e-9, matched.end_seconds - matched.start_seconds),
                    ))
                highlight_progress = wave_progresses[matched]
                states.append(sparse_glyph_frame(
                    highlight_progress, group_index,
                    group_count,
                    read_progress=read_progresses[matched],
                ))
                continue
            left, right = boundaries[index:index + 2]
            filled = max(0.0, min(1.0, (read_position - left) / max(0.001, right - left)))
            ordinary_index = wave_unit_indices[index] if index < len(wave_unit_indices) else index
            ordinary_count = len(mapping) if wave_unit_count is None else wave_unit_count
            ordinary_progress = progress if wave_progress is None else wave_progress
            states.append(SparseGlyphFrame(
                1.0, GLYPH_FLOAT_LIFT * continuous_read_lift(
                    ordinary_progress, ordinary_index, ordinary_count,
                ),
                filled, 0.0,
            ))
        return tuple(states)

    def _paint_cold_emphasis_reading(
        self, painter: QPainter, row: QRectF, text: str, font: QFont,
        layout: QTextLayout, progress: float, unread_color: QColor,
        line_offsets: tuple[float, ...],
        *, real_progresses: tuple[float, ...] | None = None,
        highlight_groups: tuple[TimedHighlightGroup, ...] = (),
        highlight_position: float | None = None,
    ) -> None:
        """Keep the reading front while bounded glyph prewarm is incomplete.

        QTextLine already owns the cold row's shape. Drawing its color ramp
        requires no isolated glyph or halo raster on the first visible frame.
        A whole white row here would flash unread text, then jump back into
        the reading colors when its character assets became ready.
        """
        segments = tuple(
            self._utf16_slice(text, layout.lineAt(index).textStart(), layout.lineAt(index).textLength())
            for index in range(layout.lineCount())
        )
        progresses = (
            real_progresses if real_progresses is not None else
            self._visual_line_progresses(
                progress, tuple(max(1, len(self._character_unit_offsets(segment)) - 1) for segment in segments),
            )
        )
        painter.save()
        for index, (segment, local) in enumerate(zip(segments, progresses)):
            line = layout.lineAt(index)
            width = max(1.0, line.naturalTextWidth())
            if local <= 0.0:
                painter.setPen(unread_color)
            elif local >= 1.0:
                painter.setPen(Qt.GlobalColor.white)
            else:
                front, feather = self._reading_front(local, width, font, segment)
                hard = max(0.0, min(width, front - feather))
                edge = min(width, front)
                gradient = QLinearGradient(row.left() + line.x(), 0.0, row.left() + line.x() + width, 0.0)
                gradient.setColorAt(0.0, QColor(255, 255, 255))
                gradient.setColorAt(hard / width, QColor(255, 255, 255))
                gradient.setColorAt(edge / width, unread_color)
                gradient.setColorAt(1.0, unread_color)
                painter.setPen(QPen(gradient, 1.0))
            offset = line_offsets[index] if index < len(line_offsets) else 0.0
            line.draw(painter, QPointF(row.left(), row.top() + offset))
            if highlight_groups and highlight_position is not None:
                # On a cold genuine-unit row, retain the original source
                # front for ordinary words. Draw only accelerated highlight
                # spans again in white, without allocating character images.
                # The warm glyph path uses the same group elapsed fraction.
                start_py = len(self._utf16_slice(text, 0, line.textStart()))
                segment = segments[index]
                end_py = start_py + len(segment)
                for group in highlight_groups:
                    overlap_start = max(start_py, group.start_offset)
                    overlap_end = min(end_py, group.end_offset)
                    if overlap_end <= overlap_start:
                        continue
                    duration = max(1e-9, group.end_seconds - group.start_seconds)
                    read = max(0.0, min(
                        1.0, 2.0 * (highlight_position - group.start_seconds) / duration,
                    ))
                    front_py = group.start_offset + read * (group.end_offset - group.start_offset)
                    if front_py <= overlap_start:
                        continue
                    x_left = row.left() + float(line.cursorToX(self._utf16_offset(text, overlap_start))[0])
                    x_right = row.left() + float(line.cursorToX(self._utf16_offset(text, overlap_end))[0])
                    whole = min(overlap_end, int(front_py))
                    x_front = row.left() + float(line.cursorToX(self._utf16_offset(text, whole))[0])
                    if whole < overlap_end:
                        next_x = row.left() + float(line.cursorToX(self._utf16_offset(text, whole + 1))[0])
                        x_front += (next_x - x_front) * (front_py - whole)
                    if x_right <= x_left:
                        continue
                    edge = min(
                        x_right,
                        x_front + max(1.0, self._reading_front(0.0, width, font, segment)[1]),
                    )
                    gradient = QLinearGradient(x_left, 0.0, x_right, 0.0)
                    gradient.setColorAt(0.0, QColor(255, 255, 255, 255))
                    if x_front < x_right:
                        gradient.setColorAt(max(0.0, (x_front - x_left) / (x_right - x_left)), QColor(255, 255, 255, 255))
                        gradient.setColorAt(max(0.0, (edge - x_left) / (x_right - x_left)), QColor(255, 255, 255, 0))
                        gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
                    painter.save()
                    painter.setClipRect(QRectF(
                        x_left, row.top() + line.y() + offset - 2.0,
                        x_right - x_left, line.height() + 4.0,
                    ), Qt.ClipOperation.IntersectClip)
                    painter.setPen(QPen(gradient, 1.0))
                    line.draw(painter, QPointF(row.left(), row.top() + offset))
                    painter.restore()
        painter.restore()

    def _wrapped_lift_wave(
        self, text: str, font: QFont, layout: QTextLayout,
        runs: list[tuple[str, float, float, float, int]], progress: float,
        unit_mode: str, real_progresses: tuple[float, ...] | None,
        *, ordinal_mode: str = "reading",
    ) -> tuple[float, tuple[tuple[int, ...], ...], int]:
        """Map local visual read fronts onto one sentence-wide rise clock."""
        # A highlighted sentence is rasterized by character, but only the
        # explicitly highlighted span uses that per-character motion. Its
        # remaining ordinary words must share one lift ordinal; otherwise a
        # later letter can visibly rise ahead of the preceding word.
        unit_offsets = (
            self._character_unit_offsets(text)
            if ordinal_mode == "character" else self._reading_unit_offsets(text)
        )
        wave_count = max(1, len(unit_offsets) - 1)
        wave_progress = progress
        wave_mappings: list[tuple[int, ...]] = []
        measured_units: list[float] = [0.0] * wave_count
        filled_units: list[float] = [0.0] * wave_count
        for line_index, (segment, _x, _y, _height, _glyphs) in enumerate(runs):
            segment_offsets = (
                self._character_unit_offsets(segment)
                if unit_mode == "character" else self._reading_unit_offsets(segment)
            )
            utf16_start = layout.lineAt(line_index).textStart()
            wave_indices = tuple(
                max(0, min(wave_count - 1, bisect_right(unit_offsets, utf16_start + offset) - 1))
                for offset in segment_offsets[:-1]
            )
            wave_mappings.append(wave_indices)
            if real_progresses is not None:
                image, boundaries = self._glyph_run(segment, font, unit_mode)
                local = real_progresses[line_index] if line_index < len(real_progresses) else 0.0
                local_progress = max(0.0, min(1.0, float(local)))
                # _real_visual_line_progresses includes the glyph run's 2 px
                # left inset for a partial row. Remove it only for the rise
                # clock; the white color edge keeps its exact source value.
                hard_front = local_progress * image.width()
                if 0.0 < local_progress < 1.0:
                    hard_front = max(0.0, hard_front - 2.0)
                for unit_index, left, right in zip(wave_indices, boundaries, boundaries[1:]):
                    span = max(0.0, right - left)
                    measured_units[unit_index] += span
                    filled_units[unit_index] += max(0.0, min(span, hard_front - left))
        if real_progresses is not None:
            for index, (length, filled) in enumerate(zip(measured_units, filled_units)):
                if length > 1e-9 and filled < length - 1e-9:
                    wave_progress = (index + filled / length) / wave_count
                    break
            else:
                wave_progress = 1.0
        return wave_progress, tuple(wave_mappings), wave_count

    def _paint_wrapped_lifted_lyric(
        self,
        painter: QPainter,
        row: QRectF,
        text: str,
        font: QFont,
        layout: QTextLayout,
        progress: float,
        runs: list[tuple[str, float, float, float, int]] | None = None,
        line_offsets: tuple[float, ...] = (),
        unit_mode: str = "reading",
        real_progresses: tuple[float, ...] | None = None,
        read_color: QColor | None = None,
    ):
        runs = runs if runs is not None else self._wrapped_lyric_runs(text, font, layout, unit_mode)
        progresses = (
            real_progresses if real_progresses is not None else
            self._visual_line_progresses(progress, tuple(run[4] for run in runs))
        )
        # Color keeps each visual row's actual front; lift shares one
        # sentence-wide clock, including long words split across rows.
        wave_progress, wave_mappings, wave_count = self._wrapped_lift_wave(
            text, font, layout, runs, progress, unit_mode, real_progresses,
        )
        unread_colors = self._future_visual_line_colors(layout, row.topLeft())
        for line_index, ((segment, x, y, height, _glyphs), local) in enumerate(zip(runs, progresses)):
            offset = line_offsets[line_index] if line_index < len(line_offsets) else 0.0
            origin = QPointF(row.left() + x, row.top() + y + offset)
            unread_color = unread_colors[line_index]
            self._paint_lifted_run(
                painter,
                origin,
                height,
                segment,
                font,
                local,
                unread_color,
                unit_mode,
                lift_progress=wave_progress,
                lift_unit_indices=wave_mappings[line_index],
                lift_unit_count=wave_count,
                **({"read_color": read_color} if read_color is not None else {}),
            )

    def _surface_font(self, size: float, weight: QFont.Weight = QFont.Weight.Bold) -> QFont:
        # The large music surface has its own restrained typography.  Do not
        # inherit a decorative island lyric style here: Segoe UI gives Latin
        # lyrics the reference proportions while Microsoft YaHei UI remains
        # the explicit CJK fallback.
        font = QFont()
        font.setFamilies(list(self.SURFACE_FONT_FAMILIES))
        font.setPixelSize(max(1, int(round(size))))
        font.setWeight(weight)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font

    def _surface_scale(self, rect: QRectF | None = None) -> float:
        surface = QRectF(self.rect()) if rect is None else QRectF(rect)
        raw_scale = surface.height() / self.REFERENCE_SURFACE_HEIGHT
        if surface.width() < 640:
            raw_scale = min(raw_scale, surface.width() / 470.)
        # Width has no product minimum. Below the former floor's 235px span,
        # let the existing composition keep shrinking so controls stay inside.
        floor = min(self.MIN_SURFACE_SCALE, max(.001, surface.width() / 470.))
        return max(floor, min(self.MAX_SURFACE_SCALE, raw_scale))

    def _font_height(self, font: QFont) -> float:
        # One long-song layout asks for the same lyric font once per row.
        # Share its measured height without changing geometry; first-use font
        # initialization remains a separate Windows cost.
        key = (font.toString(), round(float(self.devicePixelRatioF()), 3))
        cached = self._font_height_cache.get(key)
        if cached is not None:
            return cached
        height = max(1.0, float(QFontMetricsF(font).height()))
        self._font_height_cache[key] = height
        if len(self._font_height_cache) > 64:
            self._font_height_cache.pop(next(iter(self._font_height_cache)))
        return height

    @staticmethod
    def _metadata_spring_fraction(progress: float, *, delay: float = 0.0, overshoot: float = 0.5) -> float:
        """Stagger a text row's expansion without stretching its glyphs."""
        phase = max(0.0, min(1.0, (float(progress) - delay) / max(0.001, 1.0 - delay)))
        remaining = phase - 1.0
        return 1.0 + (overshoot + 1.0) * remaining ** 3 + overshoot * remaining ** 2

    def _metadata_layout(
        self, cover: QRectF, content: QRectF, surface_scale: float, control_reveal: float,
    ) -> tuple[QFont, QRectF, QFont, QRectF]:
        """Let title lead and artist follow into space freed by the controls."""
        expansion = 1.0 - max(0.0, min(1.0, float(control_reveal)))
        title_gain = self._metadata_spring_fraction(expansion, delay=0.05, overshoot=0.5)
        artist_gain = self._metadata_spring_fraction(expansion, delay=0.16, overshoot=0.38)
        width = cover.width() - 14.0
        metadata_top = cover.bottom() + max(14.0, 60.0 * surface_scale * 0.38)
        metadata_top += 8.0 * surface_scale * title_gain
        title_size = (self.TITLE_PIXEL_SIZE + 12.0 * title_gain) * surface_scale
        title_font = self._surface_font(title_size, QFont.Weight.Bold)
        title_base_font = self._surface_font(self.TITLE_PIXEL_SIZE * surface_scale, QFont.Weight.Bold)
        title_height = self._font_height(title_base_font) * title_size / title_base_font.pixelSize()
        title_rect = QRectF(
            content.left() + 7.0,
            metadata_top,
            width,
            max(40.0 * surface_scale, title_height + 2.0 * surface_scale),
        )
        # The negative half-sine briefly compresses the gap as the trailing
        # artist catches the title; neither row's glyphs are transformed.
        gap = -2.0 + 14.0 * artist_gain - 4.0 * math.sin(math.pi * min(1.0, max(0.0, artist_gain)))
        artist_size = (self.ARTIST_PIXEL_SIZE + 8.0 * artist_gain) * surface_scale
        artist_font = self._surface_font(artist_size, QFont.Weight.Medium)
        artist_base_font = self._surface_font(self.ARTIST_PIXEL_SIZE * surface_scale, QFont.Weight.Medium)
        artist_height = self._font_height(artist_base_font) * artist_size / artist_base_font.pixelSize()
        artist_rect = QRectF(
            content.left() + 7.0,
            title_rect.bottom() + gap * surface_scale,
            width,
            max(28.0 * surface_scale, artist_height + surface_scale),
        )
        self._metadata_draw_pixel_sizes = {"title": title_size, "artist": artist_size}
        return title_font, title_rect, artist_font, artist_rect

    def _wrapped_lyric_layout(
        self, text: str, font: QFont, width: float, *, align_right: bool = False,
    ) -> tuple[QTextLayout, float]:
        key = (str(text), font.toString(), max(1, int(round(width))), bool(align_right))
        cached = self._lyric_layout_cache.get(key)
        if cached is not None:
            return cached
        layout = QTextLayout(str(text), font)
        self._apply_cjk_letter_spacing(layout, str(text), font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        y = 0.0
        last_line_bottom = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1.0, float(width)))
            line.setPosition(QPointF(
                max(0.0, float(width) - line.naturalTextWidth())
                if align_right else 0.0,
                y,
            ))
            last_line_bottom = y + line.height()
            y += round(line.height() * 0.86)
        layout.endLayout()
        result = (layout, max(self._font_height(font), last_line_bottom))
        self._lyric_layout_cache[key] = result
        while len(self._lyric_layout_cache) > 96:
            self._lyric_layout_cache.pop(next(iter(self._lyric_layout_cache)))
        return result

    def _song_lyric_layouts(
        self, lines: list[str], font: QFont, width: float, row_gap: float,
        *, defer: bool = False, secondary_lines: tuple[str, ...] = (),
        secondary_font: QFont | None = None,
        parenthetical_lines: tuple[str, ...] = (),
        parenthetical_translation_lines: tuple[str, ...] = (),
        parenthetical_font: QFont | None = None,
        row_widths: tuple[float, ...] = (),
        row_right_aligned: tuple[bool, ...] = (),
        pair_starts: tuple[int, ...] = (),
    ) -> tuple[tuple[tuple[QTextLayout, float], ...], tuple[float, ...]]:
        # The short LRU above serves arbitrary individual rows.  A separate
        # one-song cache prevents a 100+ row track from evicting its first
        # layouts before the next animation frame even starts.
        secondary = tuple(secondary_lines) if secondary_font is not None else ()
        parenthetical = tuple(parenthetical_lines) if secondary_font is not None else ()
        parenthetical_translations = tuple(parenthetical_translation_lines) if secondary_font is not None else ()
        key = (
            tuple(lines), font.toString(), max(1, int(round(width))), round(row_gap, 3),
            secondary, secondary_font.toString() if secondary_font is not None else None,
            parenthetical, parenthetical_translations,
            parenthetical_font.toString() if parenthetical_font is not None else None,
            tuple(round(value, 3) for value in row_widths),
            row_right_aligned, pair_starts,
        )
        if key != self._song_layout_key:
            if key != self._song_layout_build_key:
                self._song_layout_build_key = key
                self._song_layout_build = {
                    "lines": tuple(lines), "font": QFont(font), "width": width,
                    "row_gap": row_gap, "layouts": [], "tops": [],
                    "secondary_lines": secondary,
                    "parenthetical_lines": parenthetical,
                    "parenthetical_translation_lines": parenthetical_translations,
                    "parenthetical_font": QFont(parenthetical_font) if parenthetical_font is not None else None,
                    "secondary_font": QFont(secondary_font) if secondary_font is not None else None,
                    "row_widths": row_widths, "row_right_aligned": row_right_aligned,
                    "pair_starts": frozenset(pair_starts),
                    "secondary_layouts": [], "row_heights": [],
                    "translation_extra_heights": [],
                    "parenthetical_layouts": [], "parenthetical_extra_heights": [],
                    "parenthetical_translation_layouts": [],
                    "visual_tops": [], "visual_heights": [],
                    "visual_ranges": [], "visual_counts": [], "cursor": 0.0,
                }
                self._pending_glow_prewarm = None
                self._pending_reading_prewarm = None
                self._lyric_blur_prewarm_queue.clear()
                self._lyric_blur_prewarm_token = None
            self._advance_song_layout_build(
                max_rows=12 if defer else None,
                max_seconds=self.LYRIC_LAYOUT_BATCH_SECONDS if defer else None,
            )
            if self._song_layout_key != key:
                return (), ()
        return self._song_layouts, self._song_row_tops

    def _advance_song_layout_build(
        self, *, max_rows: int | None, max_seconds: float | None = None,
    ) -> None:
        build = self._song_layout_build
        if build is None:
            return
        lines = build["lines"]
        deadline = (
            time.perf_counter() + max(0.0, float(max_seconds))
            if max_seconds is not None else None
        )
        stop = len(lines) if max_rows is None else min(len(lines), len(build["layouts"]) + max(1, max_rows))
        pair_starts = build["pair_starts"]
        if stop - 1 in pair_starts:
            stop = min(len(lines), stop + 1)

        def append_row(row_index: int, cursor: float, *, paired: bool) -> float:
            text = lines[row_index]
            widths = build["row_widths"]
            width = widths[row_index] if row_index < len(widths) else build["width"]
            right_sides = build["row_right_aligned"]
            align_right = bool(right_sides[row_index]) if row_index < len(right_sides) else False
            layout, height = self._wrapped_lyric_layout(
                text, build["font"], width, align_right=align_right,
            )
            build["layouts"].append((layout, height))
            build["tops"].append(cursor)
            visual_start = len(build["visual_tops"])
            if not paired:
                for visual_index in range(layout.lineCount()):
                    visual = layout.lineAt(visual_index)
                    build["visual_tops"].append(cursor + visual.y())
                    build["visual_heights"].append(visual.height())
            total_height = height
            secondary_lines = build["secondary_lines"]
            secondary_text = secondary_lines[row_index] if row_index < len(secondary_lines) else ""
            parens = build["parenthetical_lines"]
            parenthetical_text = parens[row_index] if row_index < len(parens) else ""
            translated_parens = build["parenthetical_translation_lines"]
            translated_text = translated_parens[row_index] if row_index < len(translated_parens) else ""
            if (parenthetical_text or translated_text) and build["parenthetical_font"] is not None:
                # A sung parenthesis belongs to this sentence's next visual
                # line. Font line metrics already leave space for its ink;
                # the separate inter-sentence gap must not enter this stack.
                annotation_top = total_height
                annotation_bottom = annotation_top
            else:
                annotation_top = annotation_bottom = 0.0
            if parenthetical_text and build["parenthetical_font"] is not None:
                annotation_layout, annotation_height = self._wrapped_lyric_layout(
                    parenthetical_text, build["parenthetical_font"], width,
                    align_right=align_right,
                )
                build["parenthetical_layouts"].append((
                    annotation_layout, annotation_height, annotation_top,
                ))
                annotation_bottom = annotation_top + annotation_height
            else:
                build["parenthetical_layouts"].append(None)
            if translated_text and build["secondary_font"] is not None:
                translation_top = annotation_bottom
                translation_layout, translation_height = self._wrapped_lyric_layout(
                    translated_text, build["secondary_font"], width,
                    align_right=align_right,
                )
                build["parenthetical_translation_layouts"].append((
                    translation_layout, translation_height, translation_top,
                ))
                annotation_bottom = translation_top + translation_height
            else:
                build["parenthetical_translation_layouts"].append(None)
            build["parenthetical_extra_heights"].append(
                max(0.0, annotation_bottom - total_height)
            )
            # Asides remain attached directly to the main sentence, including
            # when translation is visible. Translation is the final small row.
            # Small rows follow the final primary spring; adding/removing them
            # must never replace the main sentence's physical spring slots.
            if secondary_text and build["secondary_font"] is not None:
                small_layout, small_height = self._wrapped_lyric_layout(
                    secondary_text, build["secondary_font"], width, align_right=align_right,
                )
                small_gap = max(4.0, build["font"].pixelSize() * 0.12)
                small_top = max(total_height, annotation_bottom) + small_gap
                build["secondary_layouts"].append((small_layout, small_height, small_top))
                build["translation_extra_heights"].append(small_gap + small_height)
            else:
                build["secondary_layouts"].append(None)
                build["translation_extra_heights"].append(0.0)
            if paired:
                build["visual_tops"].append(cursor)
                build["visual_heights"].append(
                    total_height + build["parenthetical_extra_heights"][-1]
                )
            elif len(build["visual_tops"]) > visual_start:
                last = len(build["visual_tops"]) - 1
                build["visual_heights"][last] = max(
                    build["visual_heights"][last],
                    cursor + total_height + build["parenthetical_extra_heights"][-1]
                    - build["visual_tops"][last],
                )
            build["row_heights"].append(total_height)
            build["visual_ranges"].append((visual_start, len(build["visual_tops"])))
            build["visual_counts"].append(len(build["visual_tops"]) - visual_start)
            return total_height

        row_index = len(build["layouts"])
        while row_index < stop:
            cursor = build["cursor"]
            if row_index in pair_starts and row_index + 1 < len(lines):
                first_height = append_row(row_index, cursor, paired=True)
                second_height = append_row(row_index + 1, cursor, paired=True)
                build["cursor"] = cursor + max(first_height, second_height) + build["row_gap"]
                row_index += 2
            else:
                height = append_row(row_index, cursor, paired=False)
                build["cursor"] = cursor + height + build["row_gap"]
                row_index += 1
            # Finish at least one whole row/group, then yield. A single
            # expensive font shape can exceed the budget, but may not cause
            # the rest of a long song to shape on the same GUI frame.
            if deadline is not None and time.perf_counter() >= deadline:
                break
        if len(build["layouts"]) < len(lines):
            return
        self._song_layout_key = self._song_layout_build_key
        self._song_layouts = tuple(build["layouts"])
        self._song_secondary_layouts = tuple(build["secondary_layouts"])
        self._song_parenthetical_layouts = tuple(build["parenthetical_layouts"])
        self._song_parenthetical_translation_layouts = tuple(build["parenthetical_translation_layouts"])
        self._song_parenthetical_extra_heights = tuple(build["parenthetical_extra_heights"])
        self._song_translation_extra_heights = tuple(build["translation_extra_heights"])
        self._song_row_heights = tuple(build["row_heights"])
        self._song_row_tops = tuple(build["tops"])
        self._song_visual_tops = tuple(build["visual_tops"])
        self._song_visual_heights = tuple(build["visual_heights"])
        self._song_visual_ends = tuple(end for _start, end in build["visual_ranges"])
        self._song_max_visual_height = max(build["visual_heights"], default=0.0)
        self._song_visual_ranges = tuple(build["visual_ranges"])
        self._song_visual_counts = tuple(build["visual_counts"])
        self._song_layout_build = None
        self._song_layout_build_key = None

    def _visual_row_identities(self, lines: list[str]) -> tuple:
        key = (self._browse_track_key, tuple(lines), self._song_visual_counts)
        if key != self._visual_row_ids_key:
            tokens = self._primary_row_tokens((0.0, text) for text in lines)
            self._visual_row_ids = tuple(
                (self._browse_track_key, tokens[row], ordinal)
                for row, count in enumerate(self._song_visual_counts)
                for ordinal in range(count)
            )
            self._visual_row_ids_key = key
        return self._visual_row_ids

    def _visible_visual_spring_range(
        self, targets: tuple[float, ...], viewport: QRectF, bleed: float, key: tuple,
        row_ids: tuple | None = None,
    ) -> tuple[int, int]:
        """Keep springs for physical rows entering or leaving the whole window.

        The candidate range follows actual pixels rather than an integer lyric
        anchor.  Previous visible positions are included while a line catches
        its target, preventing an edge row from being dropped mid-transition.
        """
        top = viewport.top() - bleed
        bottom = viewport.bottom() + bleed
        previous = self._logical_line_spring
        prior_range = previous.visible_range or (0, 0)
        use_previous = previous.key == key and len(previous.positions) == len(targets)
        if row_ids is previous.row_ids and len(previous.positions) == len(targets):
            prior_visible = {index: index for index in range(*prior_range)}
        elif row_ids is not None and previous.row_ids:
            old_by_id = {identity: index for index, identity in enumerate(previous.row_ids)}
            prior_visible = {
                index: old_by_id[identity] for index, identity in enumerate(row_ids)
                if identity in old_by_id and prior_range[0] <= old_by_id[identity] < prior_range[1]
            }
        elif use_previous:
            prior_visible = {index: index for index in range(*prior_range)}
        else:
            prior_visible = {}
        offsets = sorted(previous.positions[old] - targets[index] for index, old in prior_visible.items())
        roll_offset = offsets[len(offsets) // 2] if offsets else 0.0
        # Follow the physical column as it rolls past intermediate lyrics.
        # A swept old-to-destination union would integrate an entire long
        # song during a seek, even though only a few rows can contribute ink.
        candidate_first = max(0, bisect_left(targets, top - self._song_max_visual_height - roll_offset) - 1)
        candidate_last = min(len(targets), bisect_right(targets, bottom - roll_offset) + 1)
        candidates = set(range(candidate_first, candidate_last))
        candidates.update(prior_visible)
        first, last = len(targets), 0
        for index in sorted(candidates):
            target = targets[index]
            old_index = prior_visible.get(index)
            position = previous.positions[old_index] if old_index is not None else target + roll_offset
            height = self._song_visual_heights[index]
            if position + height < top or position > bottom:
                continue
            first = min(first, index)
            last = index + 1
        return (first, last) if last else (0, 0)

    @staticmethod
    def _draw_wrapped_layout(painter: QPainter, layout: QTextLayout, origin: QPointF, color: QColor):
        painter.save()
        painter.setPen(color)
        layout.draw(painter, origin)
        painter.restore()

    @staticmethod
    def _lyric_glow_sprite_key(
        text: str, font: QFont, width: float, layout: QTextLayout, line_index: int,
    ) -> tuple:
        line = layout.lineAt(line_index)
        return (
            str(text), font.toString(), max(1, int(round(width))), line_index,
            round(line.naturalTextWidth(), 2), round(line.height(), 2),
        )

    def _cached_lyric_glow_sprite(
        self, text: str, font: QFont, width: float, layout: QTextLayout, line_index: int,
    ) -> tuple[QImage, float] | None:
        key = self._lyric_glow_sprite_key(text, font, width, layout, line_index)
        cached = self._lyric_glow_cache.pop(key, None)
        if cached is None:
            return None
        self._lyric_glow_cache[key] = cached
        return cached[0], cached[1]

    def _lyric_emphasis_assets_ready(
        self, text: str, font: QFont, width: float, layout: QTextLayout, mode: str,
        unread_color: QColor | None = None,
    ) -> bool:
        for line_index in range(layout.lineCount()):
            line = layout.lineAt(line_index)
            segment = self._utf16_slice(text, line.textStart(), line.textLength())
            glyph_key = (segment, font.toString(), "character")
            if glyph_key not in self._glyph_run_cache or glyph_key not in self._isolated_glyph_cache:
                return False
            opaque = QColor(unread_color) if unread_color is not None else None
            if opaque is not None:
                opaque.setAlpha(255)
            for source, _left in self._isolated_glyph_cache[glyph_key]:
                if source.cacheKey() not in self._sparse_glyph_halo_cache:
                    return False
                if opaque is not None and (source.cacheKey(), opaque.rgba()) not in self._glyph_tint_cache:
                    return False
        return True

    def _lyric_reading_assets_ready(
        self, text: str, font: QFont, layout: QTextLayout, unread_color: QColor,
    ) -> bool:
        """Check that the next ordinary row can lift without cold raster work."""
        opaque = QColor(unread_color)
        opaque.setAlpha(255)
        font_key = font.toString()
        for line_index in range(layout.lineCount()):
            line = layout.lineAt(line_index)
            segment = self._utf16_slice(text, line.textStart(), line.textLength())
            key = (segment, font_key, "reading")
            if key not in self._glyph_run_cache or key not in self._isolated_glyph_cache:
                return False
            for source, _left in self._isolated_glyph_cache[key]:
                if (source.cacheKey(), opaque.rgba()) not in self._glyph_tint_cache:
                    return False
        return True

    def _lyric_glow_sprite(
        self, text: str, font: QFont, width: float, layout: QTextLayout, line_index: int,
    ) -> tuple[QImage, float]:
        """Shape a visual line once, then cache a diffuse white glyph halo.

        The low-resolution tap composite is intentional: it scatters light
        from the actual letter shapes, not from unrelated circular spotlights.
        The glyphs themselves are painted at native resolution afterwards.
        """
        line = layout.lineAt(line_index)
        key = self._lyric_glow_sprite_key(text, font, width, layout, line_index)
        cached = self._cached_lyric_glow_sprite(text, font, width, layout, line_index)
        if cached is not None:
            return cached

        padding = max(18.0, line.height() * 1.05)
        logical_width = max(1, int(math.ceil(line.naturalTextWidth() + padding * 2.0)))
        logical_height = max(1, int(math.ceil(line.height() + padding * 2.0)))
        reduction = 0.25
        small_size = QSize(
            max(1, int(math.ceil(logical_width * reduction))),
            max(1, int(math.ceil(logical_height * reduction))),
        )
        ink = QImage(small_size, QImage.Format.Format_ARGB32_Premultiplied)
        ink.fill(Qt.GlobalColor.transparent)
        with QPainter(ink) as shape:
            shape.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            shape.scale(reduction, reduction)
            shape.setPen(Qt.GlobalColor.white)
            # QTextLine.draw adds the position assigned during wrapping.  A
            # sprite is local to one visual line, so cancel that position here;
            # the final compositor applies line.x()/line.y() exactly once.
            line.draw(shape, QPointF(padding - line.x(), padding - line.y()))

        scattered = QImage(small_size, QImage.Format.Format_ARGB32_Premultiplied)
        scattered.fill(Qt.GlobalColor.transparent)
        with QPainter(scattered) as light:
            light.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            for stride, opacity in ((2, 0.018), (1, 0.045)):
                light.setOpacity(opacity)
                for y in range(-2, 3):
                    for x in range(-2, 3):
                        light.drawImage(QPointF(x * stride, y * stride), ink)
        # The sprite is a soft light field; one logical pixel per texel is
        # plenty even on a high-DPI monitor and avoids a 4K blur allocation.
        sprite = scattered.scaled(
            logical_width, logical_height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        byte_count = int(sprite.sizeInBytes())
        if byte_count <= self.LYRIC_GLOW_CACHE_MAX_BYTES:
            self._lyric_glow_cache[key] = (sprite, padding, byte_count)
            self._lyric_glow_cache_bytes += byte_count
            while (
                len(self._lyric_glow_cache) > self.LYRIC_GLOW_CACHE_MAX_ENTRIES
                or self._lyric_glow_cache_bytes > self.LYRIC_GLOW_CACHE_MAX_BYTES
            ):
                oldest_key = next(iter(self._lyric_glow_cache))
                _old_image, _old_padding, old_bytes = self._lyric_glow_cache.pop(oldest_key)
                self._lyric_glow_cache_bytes -= old_bytes
        return sprite, padding

    def _paint_lyric_scatter_glow(
        self,
        painter: QPainter,
        text: str,
        font: QFont,
        width: float,
        layout: QTextLayout,
        origin: QPointF,
        line_offsets: tuple[float, ...],
        progress: float,
    ):
        phase = lyric_glow_phase(progress)
        if phase.intensity <= 0.001 or phase.visible_fraction <= 0.001:
            return
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
        for index in range(layout.lineCount()):
            line = layout.lineAt(index)
            cached = self._cached_lyric_glow_sprite(text, font, width, layout, index)
            if cached is None:
                continue
            sprite, padding = cached
            offset = line_offsets[index] if index < len(line_offsets) else 0.0
            target = QRectF(
                origin.x() + line.x() - padding,
                origin.y() + line.y() + offset - padding,
                float(sprite.width()), float(sprite.height()),
            )
            edge = target.left() + target.width() * phase.visible_fraction
            feather = min(24.0, max(8.0, line.height() * 0.38))
            opaque_right = min(target.right(), max(target.left(), edge - feather))
            painter.setOpacity(phase.intensity)
            if phase.stage == "hold":
                painter.drawImage(target.topLeft(), sprite)
                continue
            if opaque_right > target.left():
                source = QRectF(0.0, 0.0, opaque_right - target.left(), target.height())
                painter.drawImage(QRectF(target.topLeft(), source.size()), sprite, source)
            remaining = max(0.0, min(feather, edge - opaque_right))
            if remaining <= 0.0:
                continue
            step = remaining / self.GLOW_EDGE_FEATHER_STEPS
            for strip in range(self.GLOW_EDGE_FEATHER_STEPS):
                left = opaque_right + strip * step
                source = QRectF(left - target.left(), 0.0, step, target.height())
                painter.setOpacity(phase.intensity * (1.0 - (strip + 0.5) / self.GLOW_EDGE_FEATHER_STEPS))
                painter.drawImage(QRectF(left, target.top(), step, target.height()), sprite, source)
        painter.restore()

    def _lyric_raster(
        self,
        text: str,
        font: QFont,
        width: float,
        height: float,
        layout: QTextLayout,
        color: QColor,
        *,
        dpr_cap: float | None = None,
    ) -> tuple[QImage, float]:
        dpr = max(1.0, float(self.devicePixelRatioF()))
        if dpr_cap is not None:
            # Blurred, noncurrent rows have no visible high-frequency ink.
            # Render those layers at a lower device scale to keep new-row
            # blur work and cache use bounded on 2×/3× displays.
            dpr = min(dpr, max(1.0, float(dpr_cap)))
        key = (
            str(text),
            font.toString(),
            max(1, int(round(width))),
            max(1, int(round(height))),
            int(QColor(color).rgba()),
            round(dpr, 3),
        )
        cached = self._lyric_raster_cache.pop(key, None)
        if cached is not None:
            self._lyric_raster_cache[key] = cached
            return cached[0], cached[1]

        padding = max(3.0, math.ceil(self._font_height(font) * 0.18), self._lyric_blur_bleed(2))
        natural_width = max(
            (layout.lineAt(index).x() + layout.lineAt(index).naturalTextWidth() for index in range(layout.lineCount())),
            default=1.0,
        )
        logical_width = max(1.0, min(float(width), natural_width) + padding * 2.0)
        logical_height = max(1.0, float(height) + padding * 2.0)
        image = QImage(
            max(1, int(math.ceil(logical_width * dpr))),
            max(1, int(math.ceil(logical_height * dpr))),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as layer:
            layer.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            layer.setPen(QColor(color))
            layout.draw(layer, QPointF(padding, padding))

        byte_count = int(image.sizeInBytes())
        # One unusually large line should still render correctly without
        # forcing all useful cached rows out of the bounded working set.
        if byte_count <= self.LYRIC_RASTER_CACHE_MAX_BYTES:
            self._lyric_raster_cache[key] = (image, padding, byte_count)
            self._lyric_raster_cache_bytes += byte_count
            while (
                len(self._lyric_raster_cache) > self.LYRIC_RASTER_CACHE_MAX_ENTRIES
                or self._lyric_raster_cache_bytes > self.LYRIC_RASTER_CACHE_MAX_BYTES
            ):
                oldest_key = next(iter(self._lyric_raster_cache))
                _old_image, _old_padding, old_bytes = self._lyric_raster_cache.pop(oldest_key)
                self._lyric_raster_cache_bytes -= old_bytes
        return image, padding

    def _blurred_lyric_raster(
        self, source: QImage, level: int, color: QColor, *, cache_key: tuple | None = None,
    ) -> QImage:
        """Blur a stable text layer once; spring movement only moves the result."""
        degree = max(0, min(2, int(level)))
        if degree == 0 or source.isNull():
            return source
        key = cache_key if cache_key is not None else (int(source.cacheKey()), degree)
        cached = self._lyric_blur_cache.pop(key, None)
        if cached is not None:
            self._lyric_blur_cache[key] = cached
            return cached[0]
        rgba = source.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = rgba.width(), rgba.height()
        pixels = Image.frombytes("RGBA", (width, height), bytes(rgba.bits()))
        radius = self._lyric_blur_radius(degree) * max(1.0, source.devicePixelRatio())
        # Blur opacity, not straight-alpha RGB. Blurring transparent black with
        # white glyph pixels would produce dirty gray edges on a light cover.
        alpha = pixels.getchannel("A").filter(ImageFilter.GaussianBlur(radius))
        ink = QColor(color)
        blurred_pixels = Image.new("RGBA", (width, height), (ink.red(), ink.green(), ink.blue(), 0))
        blurred_pixels.putalpha(alpha)
        blurred_bytes = blurred_pixels.tobytes()
        blurred = QImage(
            blurred_bytes, width, height, width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
        blurred.setDevicePixelRatio(source.devicePixelRatio())
        byte_count = int(blurred.sizeInBytes())
        if byte_count <= self.LYRIC_BLUR_CACHE_MAX_BYTES:
            self._lyric_blur_cache[key] = (blurred, byte_count)
            self._lyric_blur_cache_bytes += byte_count
            while (
                len(self._lyric_blur_cache) > self.LYRIC_BLUR_CACHE_MAX_ENTRIES
                or self._lyric_blur_cache_bytes > self.LYRIC_BLUR_CACHE_MAX_BYTES
            ):
                oldest = next(iter(self._lyric_blur_cache))
                _image, old_size = self._lyric_blur_cache.pop(oldest)
                self._lyric_blur_cache_bytes -= old_size
        return blurred

    def _blurred_visual_line(
        self, source: QImage, layout: QTextLayout, index: int,
        level: int, color: QColor, padding: float,
    ) -> tuple[QImage, float]:
        """Render one visual line before blurring so tighter wrapping cannot leak adjacent ink."""
        line = layout.lineAt(index)
        dpr = max(1.0, float(source.devicePixelRatio()))
        bleed = self._lyric_blur_bleed(level)
        key = (int(source.cacheKey()), int(level), int(index), "visual-line")
        cached = self._lyric_blur_cache.get(key)
        if cached is not None:
            self._lyric_blur_cache.pop(key)
            self._lyric_blur_cache[key] = cached
            return cached[0], bleed
        isolated = QImage(
            source.width(), max(1, int(math.ceil((line.height() + bleed * 2.0) * dpr))),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        isolated.setDevicePixelRatio(dpr)
        isolated.fill(Qt.GlobalColor.transparent)
        with QPainter(isolated) as layer:
            layer.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            layer.setPen(QColor(color))
            line.draw(layer, QPointF(padding, bleed - line.y()))
        return self._blurred_lyric_raster(isolated, level, color, cache_key=key), bleed

    def _queue_next_lyric_blurs(
        self, lines: list[str], layouts: tuple[tuple[QTextLayout, float], ...],
        row_tops: tuple[float, ...], font: QFont, width: float,
        lyric_clip: QRectF, bleed: float, future_color: QColor,
        active: int, next_start: float | None, position: float, now: float,
        *, manual_view: bool, row_widths: tuple[float, ...] = (),
        secondary_lines: tuple[str, ...] = (), secondary_font: QFont | None = None,
        pair_starts: tuple[int, ...] = (),
    ) -> None:
        """Prepare the next focus's exact row rasters outside the paint event."""
        if manual_view or not self._display_playing_state(now):
            self._lyric_blur_prewarm_queue.clear()
            self._lyric_blur_prewarm_token = None
            return
        next_active = active + 2 if active in pair_starts else active + 1
        token = (
            self._song_layout_key, self._visual_track_identity(),
            self._timeline_signature, next_active, int(future_color.rgba()),
            round(float(self.devicePixelRatioF()), 3),
        )
        if self._lyric_blur_prewarm_token not in (None, token):
            self._lyric_blur_prewarm_queue.clear()
            self._lyric_blur_prewarm_token = None
        if self._lyric_blur_prewarm_token == token:
            return
        if (
            not layouts or next_start is None or next_active >= len(layouts)
            or not 0.0 < next_start - position <= self.LYRIC_BLUR_PREWARM_LEAD_SECONDS
        ):
            return

        next_center = row_tops[next_active] + layouts[next_active][1] * 0.5
        next_origin_y = lyric_clip.top() + lyric_clip.height() * 0.43 - next_center
        viewport = QRectF(self.rect())
        visible = [
            index for index, (_layout, height) in enumerate(layouts)
            if next_origin_y + row_tops[index] + height + bleed >= viewport.top()
            and next_origin_y + row_tops[index] - bleed <= viewport.bottom()
        ]
        if not visible:
            return
        # A spring may retain one row just outside the next target viewport.
        first = max(0, visible[0] - 1)
        last = min(len(layouts), visible[-1] + 2)
        jobs = deque()
        for index in sorted(range(first, last), key=lambda row: (abs(row - next_active), row)):
            level = self._lyric_group_blur_level(index, next_active, pair_starts)
            if not level:
                continue
            layout, height = layouts[index]
            raster_width = row_widths[index] if index < len(row_widths) else width
            color = QColor(255, 255, 255) if index < next_active else QColor(future_color)
            if lines[index] == INTRO_COUNTDOWN_TEXT:
                jobs.append((lines[index], font, raster_width, height, layout, color, level, "intro-countdown"))
                continue
            if layout.lineCount() > 1:
                for visual_index in range(layout.lineCount()):
                    jobs.append((lines[index], font, raster_width, height, layout, color, level, visual_index))
                # Past rows draw a whole raster after their spring settles.
                if index < next_active:
                    jobs.append((lines[index], font, raster_width, height, layout, color, level, None))
            else:
                jobs.append((lines[index], font, raster_width, height, layout, color, level, None))
            if secondary_font is not None and index < len(secondary_lines):
                secondary = self._song_secondary_layouts[index]
                if secondary is not None:
                    small_layout, small_height, _small_top = secondary
                    small_text = secondary_lines[index]
                    if small_layout.lineCount() > 1:
                        for visual_index in range(small_layout.lineCount()):
                            jobs.append((small_text, secondary_font, raster_width, small_height,
                                         small_layout, color, level, visual_index))
                    if small_layout.lineCount() == 1 or index < next_active:
                        jobs.append((small_text, secondary_font, raster_width, small_height,
                                     small_layout, color, level, None))
        self._lyric_blur_prewarm_token = token
        self._lyric_blur_prewarm_queue = jobs
        self._lyric_blur_next_prewarm_at = now

    def _advance_lyric_blur_prewarm(self, now: float) -> None:
        """Build at most one missing blur between animation paints."""
        if not self._lyric_blur_prewarm_queue:
            return
        token = self._lyric_blur_prewarm_token
        if (
            token is None or token[0] != self._song_layout_key
            or token[1] != self._visual_track_identity()
            or self._browse_index is not None or self._browse_return_from is not None
            or not self._display_playing_state(now)
        ):
            self._lyric_blur_prewarm_queue.clear()
            self._lyric_blur_prewarm_token = None
            return
        if now < self._lyric_blur_next_prewarm_at:
            return
        while self._lyric_blur_prewarm_queue:
            text, font, width, height, layout, color, level, visual_index = (
                self._lyric_blur_prewarm_queue.popleft()
            )
            if visual_index == "intro-countdown":
                key = (("intro-countdown", font.pixelSize(), round(max(1.0, float(self.devicePixelRatioF())), 3), int(color.rgba())), level)
                if key in self._lyric_blur_cache:
                    continue
                scratch = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
                scratch.fill(Qt.GlobalColor.transparent)
                with QPainter(scratch) as layer:
                    self._paint_intro_countdown(
                        layer, QRectF(0.0, 0.0, width, height), font, 1.0,
                        level, unread_color=color,
                    )
                self._lyric_blur_next_prewarm_at = now + self.LYRIC_BLUR_PREWARM_INTERVAL_SECONDS
                return
            source, padding = self._lyric_raster(
                text, font, width, height, layout, color,
                dpr_cap=self.BLURRED_LYRIC_DPR_CAP,
            )
            key = (
                (int(source.cacheKey()), level) if visual_index is None else
                (int(source.cacheKey()), level, visual_index, "visual-line")
            )
            if key in self._lyric_blur_cache:
                continue
            if visual_index is None:
                self._blurred_lyric_raster(source, level, color)
            else:
                self._blurred_visual_line(source, layout, visual_index, level, color, padding)
            self._lyric_blur_next_prewarm_at = now + self.LYRIC_BLUR_PREWARM_INTERVAL_SECONDS
            return

    def _draw_cached_wrapped_layout(
        self,
        painter: QPainter,
        layout: QTextLayout,
        origin: QPointF,
        text: str,
        font: QFont,
        width: float,
        height: float,
        color: QColor,
        blur_level: float = 0,
    ):
        layers = self._lyric_blur_layers(blur_level)
        if len(layers) > 1:
            opacity = painter.opacity()
            for degree, weight in layers:
                painter.save()
                painter.setOpacity(opacity * weight)
                self._draw_cached_wrapped_layout(
                    painter, layout, origin, text, font, width, height, color,
                    blur_level=degree,
                )
                painter.restore()
            return
        blur_level = layers[0][0]
        image, padding = self._lyric_raster(
            text, font, width, height, layout, color,
            dpr_cap=self.BLURRED_LYRIC_DPR_CAP if blur_level else None,
        )
        image = self._blurred_lyric_raster(image, blur_level, color)
        painter.drawImage(QPointF(origin.x() - padding, origin.y() - padding), image)

    def _draw_future_wrapped_layout(
        self,
        painter: QPainter,
        layout: QTextLayout,
        origin: QPointF,
        text: str,
        font: QFont,
        width: float,
        height: float,
        line_offsets: tuple[float, ...] = (),
        blur_level: float = 0,
    ):
        layers = self._lyric_blur_layers(blur_level)
        if len(layers) > 1:
            opacity = painter.opacity()
            for degree, weight in layers:
                painter.save()
                painter.setOpacity(opacity * weight)
                self._draw_future_wrapped_layout(
                    painter, layout, origin, text, font, width, height, line_offsets,
                    blur_level=degree,
                )
                painter.restore()
            return
        blur_level = layers[0][0]
        colors = self._future_visual_line_colors(layout, origin)
        if not colors:
            return
        if blur_level and layout.lineCount() > 1:
            for index, tone in enumerate(colors):
                image, padding = self._lyric_raster(
                    text, font, width, height, layout, tone,
                    dpr_cap=self.BLURRED_LYRIC_DPR_CAP,
                )
                isolated, bleed = self._blurred_visual_line(
                    image, layout, index, blur_level, tone, padding,
                )
                line = layout.lineAt(index)
                offset = line_offsets[index] if index < len(line_offsets) else 0.0
                painter.drawImage(
                    QPointF(origin.x() - padding, origin.y() + line.y() + offset - bleed),
                    isolated,
                )
            return
        if all(color.rgba() == colors[0].rgba() for color in colors):
            self._draw_sprung_cached_wrapped_layout(
                painter, layout, origin, text, font, width, height, colors[0], line_offsets,
                blur_level=blur_level,
            )
            return
        # A wrapped sentence can cross a bright bloom and a dark corner. Use
        # one cached glyph raster per distinct tone, clipping whole visual
        # lines so the sentence never develops a hard color seam within a word.
        for color in dict.fromkeys(color.rgba() for color in colors):
            tone = QColor.fromRgba(color)
            image, padding = self._lyric_raster(
                text, font, width, height, layout, tone,
                dpr_cap=self.BLURRED_LYRIC_DPR_CAP if blur_level else None,
            )
            image = self._blurred_lyric_raster(image, blur_level, tone)
            painter.save()
            clip = QPainterPath()
            for index, line_color in enumerate(colors):
                if line_color.rgba() != color:
                    continue
                line = layout.lineAt(index)
                offset = line_offsets[index] if index < len(line_offsets) else 0.0
                bleed = self._lyric_blur_bleed(blur_level)
                clip.addRect(QRectF(
                    origin.x() - padding, origin.y() + line.y() + offset - bleed,
                    width + padding * 2.0, line.height() + bleed * 2.0,
                ))
            painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
            for index, line_color in enumerate(colors):
                if line_color.rgba() != color:
                    continue
                line = layout.lineAt(index)
                offset = line_offsets[index] if index < len(line_offsets) else 0.0
                bleed = self._lyric_blur_bleed(blur_level)
                painter.save()
                painter.setClipRect(QRectF(
                    origin.x() - padding, origin.y() + line.y() + offset - bleed,
                    width + padding * 2.0, line.height() + bleed * 2.0,
                ), Qt.ClipOperation.IntersectClip)
                painter.drawImage(QPointF(origin.x() - padding, origin.y() - padding + offset), image)
                painter.restore()
            painter.restore()

    def _draw_sprung_cached_wrapped_layout(
        self,
        painter: QPainter,
        layout: QTextLayout,
        origin: QPointF,
        text: str,
        font: QFont,
        width: float,
        height: float,
        color: QColor,
        line_offsets: tuple[float, ...],
        blur_level: float = 0,
    ):
        layers = self._lyric_blur_layers(blur_level)
        if len(layers) > 1:
            opacity = painter.opacity()
            for degree, weight in layers:
                painter.save()
                painter.setOpacity(opacity * weight)
                self._draw_sprung_cached_wrapped_layout(
                    painter, layout, origin, text, font, width, height, color,
                    line_offsets, blur_level=degree,
                )
                painter.restore()
            return
        blur_level = layers[0][0]
        if not blur_level:
            # Keep the already-shaped text on the same native glyph grid
            # while its spring moves and after it settles. A cached bitmap
            # rounds a fractional origin differently from QTextLine.draw;
            # switching to it at rest shifted low-perception rows sideways.
            # This also shares the whole-block wipe's glyph placement.
            painter.save()
            painter.setPen(QColor(color))
            for line_index in range(layout.lineCount()):
                offset = line_offsets[line_index] if line_index < len(line_offsets) else 0.0
                layout.lineAt(line_index).draw(
                    painter, QPointF(origin.x(), origin.y() + offset),
                )
            painter.restore()
            return
        if not any(abs(value) >= 0.01 for value in line_offsets):
            self._draw_cached_wrapped_layout(
                painter, layout, origin, text, font, width, height, color,
                blur_level=blur_level,
            )
            return
        image, padding = self._lyric_raster(
            text, font, width, height, layout, color,
            dpr_cap=self.BLURRED_LYRIC_DPR_CAP if blur_level else None,
        )
        if blur_level and layout.lineCount() > 1:
            for line_index in range(layout.lineCount()):
                isolated, bleed = self._blurred_visual_line(
                    image, layout, line_index, blur_level, color, padding,
                )
                line = layout.lineAt(line_index)
                offset = line_offsets[line_index] if line_index < len(line_offsets) else 0.0
                painter.drawImage(
                    QPointF(origin.x() - padding, origin.y() + line.y() + offset - bleed),
                    isolated,
                )
            return
        image = self._blurred_lyric_raster(image, blur_level, color)
        for line_index in range(layout.lineCount()):
            line = layout.lineAt(line_index)
            offset = line_offsets[line_index] if line_index < len(line_offsets) else 0.0
            bleed = self._lyric_blur_bleed(blur_level)
            painter.save()
            painter.translate(0.0, offset)
            painter.setClipRect(
                QRectF(
                    origin.x() - padding,
                    origin.y() + line.y() - bleed,
                    width + padding * 2.0,
                    line.height() + bleed * 2.0,
                ),
                Qt.ClipOperation.IntersectClip,
            )
            painter.drawImage(QPointF(origin.x() - padding, origin.y() - padding), image)
            painter.restore()

    @classmethod
    def _low_perception_block_progress(cls, position: float, row_start: float) -> float:
        return max(0.0, min(
            1.0, (float(position) - float(row_start)) / cls.LOW_PERCEPTION_BLOCK_WIPE_SECONDS,
        ))

    def _draw_low_perception_block_wipe(
        self,
        painter: QPainter,
        layers: tuple[tuple[QTextLayout, QPointF, tuple[float, ...]], ...],
        progress: float,
        unread: QColor,
        *,
        layer_poses: tuple[tuple[float, float], ...] = (),
    ) -> None:
        """Draw all wrapped original/translation lines with one color clock.

        QTextLine uses its already-shaped glyphs, so the half-second effect
        does not allocate or recolor a new raster on every animation frame.
        """
        ink: QRectF | None = None
        for layout, origin, offsets in layers:
            for index in range(layout.lineCount()):
                line = layout.lineAt(index)
                # Anchor the shared color front to the song's logical block,
                # not to the moving spring. Individual visual rows have
                # different offsets while they catch up; including those in
                # the bounds makes already-read glyphs darken again mid-roll.
                line_ink = QRectF(
                    origin.x() + line.x(), origin.y() + line.y(),
                    max(1.0, line.naturalTextWidth()), max(1.0, line.height()),
                )
                ink = line_ink if ink is None else ink.united(line_ink)
        if ink is None:
            return
        painter.save()
        gradient = whole_block_reading_gradient(
            ink, progress, unread, QColor(255, 255, 255),
        )
        painter.setPen(QPen(QBrush(gradient), 1.0))
        for layer_index, (layout, origin, offsets) in enumerate(layers):
            opacity, slide = layer_poses[layer_index] if layer_index < len(layer_poses) else (1.0, 0.0)
            painter.save()
            painter.setOpacity(painter.opacity() * opacity)
            painter.translate(0.0, slide)
            for index in range(layout.lineCount()):
                offset = offsets[index] if index < len(offsets) else 0.0
                painter.save()
                painter.translate(0.0, offset)
                layout.lineAt(index).draw(
                    painter, origin,
                )
                painter.restore()
            painter.restore()
        painter.restore()

    def _record_paint_cost(self, started_at: float, event):
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        # Offscreen QImage renders do not need the native display cadence. Avoid
        # querying Qt's screen object while the widget is hidden.
        screen = self.screen() if self.isVisible() else None
        signature = (
            self.width(), self.height(), round(float(self.devicePixelRatioF()), 2),
            round(float(screen.refreshRate()), 1) if screen is not None else 60.0,
            self._track_visual[0] if self._track_visual else self._visual_track_identity(),
        )
        if signature != getattr(self, "_paint_profile_signature", None):
            self._recent_paint_costs.clear()
            self._recent_priority_paint_costs.clear()
            self._paint_profile_signature = signature
        self._recent_paint_costs.append(elapsed_ms)
        try:
            painted = event.region().boundingRect()
        except (AttributeError, RuntimeError):
            return
        surface_area = max(1, self.width() * self.height())
        if (
            painted.isValid()
            and painted.width() * painted.height() < surface_area * 0.90
            and self._active_lyric_animation_rect.isValid()
            and painted.intersects(self._active_lyric_animation_rect)
        ):
            self._recent_priority_paint_costs.append(elapsed_ms)

    @classmethod
    def _metadata_scroll_offset(cls, overflow: float, elapsed: float) -> tuple[float, bool]:
        """Start one smooth round trip every 15 s, including its travel time."""
        if overflow <= 0.0:
            return 0.0, False
        start_hold = cls.METADATA_MARQUEE_HOLD_SECONDS
        end_hold = cls.METADATA_MARQUEE_END_HOLD_SECONDS
        travel = cls.METADATA_MARQUEE_SECONDS * 0.5
        period = max(start_hold, travel * 2.0 + end_hold)
        if elapsed < start_hold:
            return 0.0, False
        phase = (elapsed - start_hold) % period
        if phase < travel:
            progress = phase / travel
            return overflow * (0.5 - 0.5 * math.cos(math.pi * progress)), True
        if phase < travel + end_hold:
            return overflow, False
        if phase < travel * 2.0 + end_hold - 1e-9:
            progress = (phase - travel - end_hold) / travel
            return overflow * (0.5 + 0.5 * math.cos(math.pi * progress)), True
        return 0.0, False

    @classmethod
    def _metadata_next_motion_delay(cls, elapsed: float) -> float:
        """Schedule one repaint at the end of a hold, even for paused songs."""
        start_hold = cls.METADATA_MARQUEE_HOLD_SECONDS
        travel = cls.METADATA_MARQUEE_SECONDS * 0.5
        end_hold = cls.METADATA_MARQUEE_END_HOLD_SECONDS
        period = max(start_hold, travel * 2.0 + end_hold)
        if elapsed < start_hold:
            return start_hold - max(0.0, elapsed)
        phase = (elapsed - start_hold) % period
        inbound_at = travel + end_hold
        if phase < inbound_at:
            return inbound_at - phase
        return period - phase

    def _metadata_marquee_pose(self, field: str, overflow: float, now: float):
        if overflow <= 0.0:
            return 0.0, False, float("inf")
        state = self._metadata_marquee_states[field]
        returning = self._metadata_returns.get(field)
        if returning is not None:
            started, fraction = returning
            progress = max(0.0, min(1.0, (now - started) / 0.45))
            if progress < 1.0:
                return overflow * fraction * (0.5 + 0.5 * math.cos(math.pi * progress)), True, 0.0
            self._metadata_returns.pop(field, None)
        if field == self._metadata_hover_field:
            cycle = self.METADATA_MARQUEE_SECONDS + self.METADATA_MARQUEE_END_HOLD_SECONDS
            elapsed = self.METADATA_MARQUEE_HOLD_SECONDS + max(0.0, now - self._metadata_hover_started_at) % cycle
        else:
            elapsed = now - state[2]
        offset, moving = self._metadata_scroll_offset(overflow, elapsed)
        return offset, moving, self._metadata_next_motion_delay(elapsed)

    def _metadata_control_overflow(self, field: str, reveal: float) -> float:
        """Measure a settled control layout using the same exact ink as paint.

        The slot keeps its width as controls hide, while its font grows. Do
        not restart a marquee on intermediate font or viewport changes.
        """
        state = self._metadata_marquee_states.get(field)
        width = self._metadata_slot_widths.get(field)
        if state is None or width is None:
            return self._metadata_overflows.get(field, 0.0)
        scale = self._surface_scale()
        base_size = self.TITLE_PIXEL_SIZE if field == "title" else self.ARTIST_PIXEL_SIZE
        extra_size = 12.0 if field == "title" else 8.0
        weight = QFont.Weight.Bold if field == "title" else QFont.Weight.Medium
        font = self._surface_font(base_size * scale, weight)
        text_width = self._metadata_text_metrics(
            state[1], font, max(1.0, float(self.devicePixelRatioF())),
        )[1]
        if reveal < 0.999:
            text_width *= (base_size + extra_size) * scale / font.pixelSize()
        return max(0.0, text_width - width)

    def _metadata_controls_revealing(self) -> None:
        """Capture one reveal event; ordinary pointer moves cannot retrigger it."""
        self._metadata_reveal_pending = {
            field: state[:2] for field, state in self._metadata_marquee_states.items()
            if field in self._metadata_hit_rects
            and self._metadata_control_overflow(field, 0.0) > 0.0
        }
        self._metadata_scroll_after_hide.clear()

    def _start_metadata_marquee_once(self, field: str, overflow: float, now: float) -> None:
        state = self._metadata_marquee_states.get(field)
        if state is None or overflow <= 0.0 or field == self._metadata_hover_field:
            return
        offset, moving, _delay = self._metadata_marquee_pose(field, overflow, now)
        # A reveal during an existing trip preserves that trip and its timing.
        if moving and field not in self._metadata_returns:
            return
        self._metadata_returns.pop(field, None)
        fraction = max(0.0, min(1.0, offset / max(1.0, overflow)))
        outbound = math.acos(1.0 - 2.0 * fraction) / math.pi * self.METADATA_MARQUEE_SECONDS * 0.5
        self._metadata_marquee_states[field] = (
            *state[:2], now - self.METADATA_MARQUEE_HOLD_SECONDS - outbound,
        )
        self._metadata_next_motion_at = min(self._metadata_next_motion_at, now)
        if self._metadata_animation_rect.isValid():
            self.update(self._metadata_animation_rect)

    def _update_metadata_control_state(self, now: float, reveal: float) -> None:
        """Resolve each field once after the control animation has settled."""
        if self._control_reveal_target > 0.0 and reveal >= 0.999:
            pending, self._metadata_reveal_pending = self._metadata_reveal_pending, {}
            for field, key in pending.items():
                state = self._metadata_marquee_states.get(field)
                if state is None or state[:2] != key:
                    continue
                overflow = self._metadata_control_overflow(field, 1.0)
                if overflow > 0.0:
                    self._start_metadata_marquee_once(field, overflow, now)
                else:
                    self._metadata_scroll_after_hide[field] = key
        elif self._control_reveal_target <= 0.0 and reveal <= 0.001:
            pending, self._metadata_scroll_after_hide = self._metadata_scroll_after_hide, {}
            for field, key in pending.items():
                state = self._metadata_marquee_states.get(field)
                if state is not None and state[:2] == key:
                    self._start_metadata_marquee_once(
                        field, self._metadata_control_overflow(field, 0.0), now,
                    )

    def _update_metadata_hover(self, position: QPointF | None, now: float) -> None:
        field = next((key for key, rect in self._metadata_hit_rects.items()
                      if position is not None and rect.contains(position)
                      and self._metadata_overflows.get(key, 0.0) > 0.0), "")
        if field == self._metadata_hover_field:
            return
        previous = self._metadata_hover_field
        if previous and previous in self._metadata_marquee_states:
            overflow = self._metadata_overflows.get(previous, 0.0)
            offset, _, _ = self._metadata_marquee_pose(previous, overflow, now)
            state = self._metadata_marquee_states[previous]
            # Return from the displayed position before beginning the new wait.
            self._metadata_returns[previous] = (now, offset / max(1.0, overflow))
            self._metadata_marquee_states[previous] = (*state[:2], now + 0.45)
        if field and field in self._metadata_marquee_states:
            overflow = self._metadata_overflows.get(field, 0.0)
            offset, _, _ = self._metadata_marquee_pose(field, overflow, now)
            self._metadata_returns.pop(field, None)
            fraction = max(0.0, min(1.0, offset / max(1.0, overflow)))
            outbound_elapsed = math.acos(1.0 - 2.0 * fraction) / math.pi * self.METADATA_MARQUEE_SECONDS * 0.5
            self._metadata_hover_started_at = now - outbound_elapsed
        self._metadata_hover_field = field
        if self._metadata_animation_rect.isValid():
            self.update(self._metadata_animation_rect)

    def _metadata_text_raster(
        self, text: str, font: QFont, color: QColor, dpr: float,
        identity: object, *, create: bool = False,
    ) -> tuple[QImage, float, float] | None:
        """Cache the shaped title/artist at its exact font size and device scale."""
        key = (identity, text, font.toString(), color.rgba(), round(dpr, 3))
        cached = self._metadata_raster_cache.get(key)
        if cached is not None:
            self._metadata_raster_cache.move_to_end(key)
            return cached
        if not create:
            return None
        metrics = QFontMetricsF(font)
        bounds = metrics.boundingRect(text)
        ink_left = min(0.0, float(bounds.left()))
        text_width = max(float(metrics.horizontalAdvance(text)), float(bounds.right())) - ink_left
        padding = 2.0
        logical_width = text_width + 2.0 * padding
        logical_height = max(
            float(metrics.height()) + 2.0 * padding,
            float(metrics.ascent() + bounds.bottom()) + 2.0 * padding,
        )
        width = max(1, int(math.ceil(logical_width * dpr)))
        height = max(1, int(math.ceil(logical_height * dpr)))
        # Pathological metadata remains drawable without retaining a huge image.
        if width * height * 4 > self.METADATA_RASTER_CACHE_MAX_BYTES // 4:
            return None
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as layer:
            layer.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            layer.setFont(font)
            layer.setPen(color)
            layer.drawText(QPointF(padding - ink_left, padding + metrics.ascent()), text)
        result = (image, text_width, float(metrics.height()))
        self._metadata_raster_cache[key] = result
        self._metadata_raster_cache_bytes += image.sizeInBytes()
        while (
            self._metadata_raster_cache_bytes > self.METADATA_RASTER_CACHE_MAX_BYTES
            or len(self._metadata_raster_cache) > self.METADATA_RASTER_CACHE_MAX_ENTRIES
        ):
            _, oldest = self._metadata_raster_cache.popitem(last=False)
            self._metadata_raster_cache_bytes -= oldest[0].sizeInBytes()
        return result

    def _metadata_text_metrics(
        self, text: str, font: QFont, dpr: float,
    ) -> tuple[float, float, float, float]:
        """Stable native-font geometry before and after a raster is prewarmed."""
        key = (text, font.toString(), round(dpr, 3))
        cached = self._metadata_metric_cache.get(key)
        if cached is not None:
            self._metadata_metric_cache.move_to_end(key)
            return cached
        metrics = QFontMetricsF(font)
        bounds = metrics.boundingRect(text)
        ink_left = min(0.0, float(bounds.left()))
        width = max(float(metrics.horizontalAdvance(text)), float(bounds.right())) - ink_left
        result = (ink_left, width, float(metrics.height()), float(metrics.ascent()))
        self._metadata_metric_cache[key] = result
        if len(self._metadata_metric_cache) > 96:
            self._metadata_metric_cache.popitem(last=False)
        return result

    def _metadata_reference_font(self, field: str) -> QFont:
        scale = self._surface_scale()
        if field == "title":
            return self._surface_font((self.TITLE_PIXEL_SIZE + 12.0 * 1.04) * scale, QFont.Weight.Bold)
        return self._surface_font((self.ARTIST_PIXEL_SIZE + 8.0 * 1.04) * scale, QFont.Weight.Medium)

    def _prewarm_metadata_text(self) -> None:
        """Prepare one raster per field during the quiet lead-in, not one per frame."""
        raw_title = str(getattr(self.owner, "track_title", "") or "")
        raw_artist = str(getattr(self.owner, "artist", "") or "")
        title = " ".join((raw_title or "暂无播放").split())
        artist = " ".join(raw_artist.split())
        media_id = str(getattr(self.owner, "media_track_id", "") or "")
        identity = media_id or (raw_title, raw_artist)
        scale = self._surface_scale()
        dpr = max(1.0, float(self.devicePixelRatioF()))
        token = (identity, title, artist, round(scale, 4), round(dpr, 3))
        if token != self._metadata_prewarm_token:
            self._metadata_prewarm_token = token
            self._metadata_prewarm_queue.clear()
            for field, text, color in (
                ("title", title, QColor(255, 255, 255, 242)),
                ("artist", artist, QColor(255, 255, 255, 142)),
            ):
                if not text:
                    continue
                self._metadata_prewarm_queue.append((
                    text, self._metadata_reference_font(field), color, dpr, identity,
                ))
        if self._metadata_prewarm_queue:
            text, font, color, dpr, identity = self._metadata_prewarm_queue.popleft()
            self._metadata_text_raster(text, font, color, dpr, identity, create=True)

    def _prewarm_idle_metadata_raster(self) -> None:
        """Shape one fully visible title/artist image away from paintEvent."""
        if self._controls_animating(time.monotonic()) or self._control_reveal_target < 1.0:
            self._metadata_idle_raster_pending.clear()
            self._metadata_idle_raster_token = None
            return
        raw_title = str(getattr(self.owner, "track_title", "") or "")
        raw_artist = str(getattr(self.owner, "artist", "") or "")
        identity = str(getattr(self.owner, "media_track_id", "") or "") or (raw_title, raw_artist)
        scale = self._surface_scale()
        dpr = max(1.0, float(self.devicePixelRatioF()))
        token = (identity, raw_title, raw_artist, round(scale, 4), round(dpr, 3))
        if token != self._metadata_idle_raster_token:
            self._metadata_idle_raster_token = token
            self._metadata_idle_raster_pending.clear()
            for field, value, color in (
                ("title", raw_title or "暂无播放", QColor(255, 255, 255, 242)),
                ("artist", raw_artist, QColor(255, 255, 255, 142)),
            ):
                display_text = " ".join(value.split())
                if not display_text:
                    continue
                base_size = self.TITLE_PIXEL_SIZE if field == "title" else self.ARTIST_PIXEL_SIZE
                weight = QFont.Weight.Bold if field == "title" else QFont.Weight.Medium
                font = self._surface_font(base_size * scale, weight)
                self._metadata_idle_raster_pending.append((display_text, font, color, dpr, identity))
        if self._metadata_idle_raster_pending:
            self._metadata_text_raster(*self._metadata_idle_raster_pending.popleft(), create=True)

    def _paint_scrolling_metadata(
        self, painter: QPainter, rect: QRectF, text: str, font: QFont,
        now: float, identity: object, *, field: str, animate: bool = True,
    ) -> None:
        """Keep long metadata on one line and reveal its ends inside its own slot."""
        if rect.width() <= 0.0 or rect.height() <= 0.0:
            return
        display_text = " ".join(str(text or "").split())
        if not display_text:
            return
        dpr = max(1.0, float(painter.device().devicePixelRatioF()))
        reference_font = self._metadata_reference_font(field)
        # Fully visible metadata keeps its native font and exact raster size.
        # Only the controls' expansion interpolates the reference raster; a
        # prewarm must not move a marquee or alter its text-width measurement.
        use_raster = self._control_reveal_at(now) < 0.999
        drawn_size = self._metadata_draw_pixel_sizes.get(field, float(font.pixelSize()))
        if use_raster and field in self._metadata_draw_pixel_sizes:
            base_size = self.TITLE_PIXEL_SIZE if field == "title" else self.ARTIST_PIXEL_SIZE
            weight = QFont.Weight.Bold if field == "title" else QFont.Weight.Medium
            base_font = self._surface_font(base_size * self._surface_scale(), weight)
            base_left, base_width, base_height, base_ascent = self._metadata_text_metrics(
                display_text, base_font, dpr,
            )
            continuous_scale = drawn_size / base_font.pixelSize()
            ink_left = base_left * continuous_scale
            text_width = base_width * continuous_scale
            text_height = base_height * continuous_scale
            ascent = base_ascent * continuous_scale
        else:
            ink_left, text_width, text_height, ascent = self._metadata_text_metrics(
                display_text, font, dpr,
            )
        raster = None
        if use_raster:
            raster = self._metadata_text_raster(
                display_text, reference_font, painter.pen().color(), dpr, identity,
            )
        elif self._metadata_idle_raster_token is not None:
            raster = self._metadata_text_raster(
                display_text, font, painter.pen().color(), dpr, identity,
            )
        overflow = max(0.0, text_width - rect.width())
        offset = 0.0
        if animate:
            key = (identity, display_text)
            state = self._metadata_marquee_states.get(field)
            if state is None or state[:2] != key:
                state = (identity, display_text, now)
                self._metadata_marquee_states[field] = state
                self._metadata_returns.pop(field, None)
                self._metadata_reveal_pending.pop(field, None)
                self._metadata_scroll_after_hide.pop(field, None)
                if field == self._metadata_hover_field:
                    self._metadata_hover_started_at = now
            self._metadata_hit_rects[field] = QRectF(rect)
            self._metadata_slot_widths[field] = rect.width()
            self._metadata_overflows[field] = overflow
            offset, moving, next_delay = self._metadata_marquee_pose(field, overflow, now)
            if overflow > 0.0:
                dirty = rect.adjusted(-2.0, -2.0, 2.0, 2.0).toAlignedRect()
                self._metadata_animation_rect = self._metadata_animation_rect.united(dirty)
                if moving:
                    self._metadata_marquee_moving = True
                else:
                    self._metadata_next_motion_at = min(
                        self._metadata_next_motion_at,
                        now + next_delay,
                    )
        painter.save()
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        if raster is None:
            baseline = rect.top() + (rect.height() - text_height) * 0.5 + ascent
            painter.setFont(font)
            painter.drawText(QPointF(rect.left() - ink_left - offset, baseline), display_text)
        else:
            vertical_scale = drawn_size / reference_font.pixelSize() if use_raster else 1.0
            horizontal_scale = text_width / max(1.0, raster[1]) if use_raster else 1.0
            padding_x = 2.0 * horizontal_scale
            padding_y = 2.0 * vertical_scale
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(QRectF(
                rect.left() - offset - padding_x,
                rect.top() + (rect.height() - text_height) * 0.5 - padding_y,
                raster[0].width() / dpr * horizontal_scale,
                raster[0].height() / dpr * vertical_scale,
            ), raster[0])
        painter.restore()

    def paintEvent(self, _event):
        previous = getattr(self, "_frame_lyric_cache", None)
        previous_light = self._frame_glass_light
        previous_color_at = self._frame_control_color_at
        self._frame_glass_light = not app_dark_mode(getattr(self.owner, "settings_getter", None))
        self._frame_control_color_at = time.monotonic()
        self._frame_lyric_cache = {}
        try:
            self._paint_surface_frame(_event)
        finally:
            self._frame_lyric_cache = previous
            self._frame_glass_light = previous_light
            self._frame_control_color_at = previous_color_at

    def _paint_surface_frame(self, _event):
        paint_started = time.perf_counter()
        if self._paint_window_compact():
            self._record_paint_cost(paint_started, _event)
            return
        navigation_capture = getattr(self, "_navigation_cover_capture", False)
        if (not navigation_capture and getattr(self, "_library_loading", None) is None
                and self._paint_cached_content_layout()):
            self._record_paint_cost(paint_started, _event)
            return
        if (not navigation_capture and (self._pending_fullscreen is not None or self._island_returning)
                and not self._geometry_transition_snapshot.isNull()):
            painter = QPainter(self)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setClipRect(self.rect())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(QRectF(self.rect()), self._geometry_transition_snapshot)
            if (self._island_entering or self._island_returning) and not self._island_origin_snapshot.isNull():
                fraction = self._mode_animation.currentTime() / max(1, self.MODE_TRANSITION_MS)
                if self._island_returning:
                    opacity = max(0.0, min(1.0, (fraction - 0.62) / 0.38))
                else:
                    opacity = max(0.0, 1.0 - fraction / 0.38)
                painter.setOpacity(opacity)
                painter.drawImage(QRectF(self.rect()), self._island_origin_snapshot)
            self._paint_transition_mode_glyph(painter)
            self._apply_surface_edge_alpha(painter)
            painter.end()
            self._record_paint_cost(paint_started, _event)
            return
        painter = QPainter(self)
        # Reset just Qt's exposed paint region before antialiased clipping.
        # Repainting a rounded edge must not accumulate its partial coverage
        # on an old backing frame or repeatedly rendered preview bitmap.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setClipRect(self.rect())
        try:
            paint_region = _event.region()
            if paint_region.isEmpty():
                paint_region = None
        except (AttributeError, RuntimeError):
            paint_region = None
        rect = QRectF(self.rect())
        palette = self._art_palette()
        primary, secondary = QColor(palette.primary), QColor(palette.secondary)
        if palette.uses_primary_fallback:
            secondary = QColor(primary)
        transition = self._track_transition(time.monotonic(), primary, secondary)
        previous = self._previous_track_visual if transition < 1.0 else None
        if previous:
            primary = self._mix_color(previous[4], primary, transition)
            secondary = self._mix_color(previous[5], secondary, transition)
        else:
            self._previous_track_visual = None
        self._paint_background(
            painter, rect, primary, secondary, time.monotonic(),
            ambient_colors=palette.ambient_colors,
            ambient_weights=palette.ambient_weights,
            advance_frame=paint_region is None or (
                self.mask() if not self.mask().isEmpty() else QRegion(self.rect())
            ).subtracted(paint_region).isEmpty(),
        )
        # Transport light is a native-resolution foreground layer of the
        # background. Its cached low-resolution composite was too faint after
        # upscale on bright artwork, while the cover/text below still need to
        # occlude the expanding ring.
        if getattr(self, "_library_loading", None) is not None:
            self._paint_library_loading(painter, rect, time.monotonic())
            self._apply_surface_edge_alpha(painter)
            painter.end()
            self._record_paint_cost(paint_started, _event)
            return
        self._paint_transport_spatial_light(painter, rect, time.monotonic())

        surface_scale = self._surface_scale(rect)
        # Match the selected 1190 x 666 composition: a 60 px outer inset,
        # 404 px artwork and a lyric column beginning around x=565.  The same
        # proportions scale with the locked 16:9 surface.
        content, _column_cover, side_column = self._content_column_geometry(rect)
        cover_now = time.monotonic()
        original_cover, centered_cover = self._cover_layout_endpoints(cover_now)
        cover = self._current_cover_rect(cover_now)
        cover_fraction = self._cover_layout_fraction_at(cover_now)
        center_plan = self._centered_album_plan(original_cover) if (
            self._cover_layout_to > 0.0 or cover_fraction > 0.0
        ) else None
        centered_title_rect, centered_artist_rect, centered_controls_rect = (
            self._centered_group_rects(centered_cover, center_plan)
            if center_plan is not None else (QRectF(), QRectF(), QRectF())
        )
        # Update the swept area, including the previous location.  The cover
        # can travel into the lyric column even during partial lyric paints.
        layout_animating = self._cover_layout_animating(cover_now)
        cover_dirty = original_cover.united(centered_cover) if layout_animating else cover
        if layout_animating and center_plan is not None:
            for endpoint in (center_plan["idle_cover"], center_plan["shown_cover"]):
                title_row, artist_row, controls_row = self._centered_group_rects(endpoint, center_plan)
                cover_dirty = cover_dirty.united(endpoint).united(title_row).united(artist_row).united(controls_row)
            # The former metadata and controls must be cleared as the central
            # group arrives, including on narrow windowed surfaces.
            cover_dirty = cover_dirty.united(QRectF(
                content.left(), original_cover.bottom(), original_cover.width(),
                max(0.0, rect.bottom() - original_cover.bottom()),
            ))
        self._cover_animation_rect = cover_dirty.adjusted(
            -2.0, -2.0, 2.0, 2.0,
        ).intersected(rect).toAlignedRect()
        cover_scale = self._cover_scale_at(cover_now)
        painted_cover = self._scaled_cover_rect(cover, cover_scale)
        cover_exposed = paint_region is None or paint_region.intersects(
            painted_cover.adjusted(-2.0, -2.0, 2.0, 2.0).toAlignedRect()
        )
        if cover_exposed and not navigation_capture:
            painted_side = painted_cover.width()
            image = QImage(getattr(self.owner, "album_art", QImage()))
            path = QPainterPath()
            path.addRoundedRect(painted_cover, self._album_art_radius(painted_side), self._album_art_radius(painted_side))
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.setClipPath(path)
            if image.isNull():
                painter.fillRect(painted_cover, QColor(255, 255, 255, 22))
            else:
                dpr = max(1.0, float(self.devicePixelRatioF()))
                cache_side = max(original_cover.width(), center_plan["idle_cover"].width(),
                                 center_plan["shown_cover"].width()) if center_plan else original_cover.width()
                preview_cover = self._should_blur_preview(image)
                cache_pixels = max(1, int(round(cache_side * dpr)))
                if preview_cover:
                    # The preview is intentionally soft. Upscaling a thumbnail
                    # to a 4K cover and then blurring millions of pixels would
                    # stall the first visible frame for no perceptual benefit.
                    cache_pixels = min(cache_pixels, 384)
                cover_key = (
                    image.cacheKey(),
                    cache_pixels,
                    cache_pixels,
                    round(dpr, 3),
                    preview_cover,
                )
                if cover_key != self._cover_cache_key:
                    self._cover_cache = self._scaled_artwork_image(image, cover_key[1], dpr)
                    self._cover_cache_key = cover_key
                cover_image = self._cover_cache
                if preview_cover:
                    cover_image = self._blurred_preview_cover(cover_image)
                painter.drawImage(painted_cover, cover_image)
            painter.restore()

            if previous and not previous[3].isNull():
                painter.save()
                painter.setOpacity(1.0 - transition)
                painter.setClipPath(path)
                dpr = max(1.0, float(self.devicePixelRatioF()))
                cache_side = max(original_cover.width(), center_plan["idle_cover"].width(),
                                 center_plan["shown_cover"].width()) if center_plan else original_cover.width()
                previous_key = (
                    previous[3].cacheKey(),
                    max(1, int(round(cache_side * dpr))),
                    max(1, int(round(cache_side * dpr))),
                    round(dpr, 3),
                )
                if previous_key != self._previous_cover_cache_key:
                    self._previous_cover_cache = self._scaled_artwork_image(previous[3], previous_key[1], dpr)
                    self._previous_cover_cache_key = previous_key
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawImage(painted_cover, self._previous_cover_cache)
                painter.restore()

        metadata_width = original_cover.width()
        available_metadata_width = max(
            14.0 * surface_scale, cover.left() - content.left() - 16.0 * surface_scale,
        )
        metadata_start_y = original_cover.bottom() + max(14.0, 60.0 * 0.38) * surface_scale
        cover_interference = max(0.0, min(1.0, (
            cover.bottom() - (metadata_start_y - 20.0 * surface_scale)
        ) / (48.0 * surface_scale)))
        available_fraction = max(0.0, min(1.0, (
            available_metadata_width - 150.0 * surface_scale
        ) / (120.0 * surface_scale)))
        available_fraction = available_fraction * available_fraction * (3.0 - 2.0 * available_fraction)
        metadata_opacity = (1.0 - cover_interference) ** 2 + (
            1.0 - (1.0 - cover_interference) ** 2
        ) * available_fraction
        metadata_cover = QRectF(original_cover)
        metadata_cover.setWidth(metadata_width + (
            min(metadata_width, available_metadata_width) - metadata_width
        ) * cover_interference)
        metadata_now = time.monotonic()
        title_font, title_rect, artist_font, artist_rect = self._metadata_layout(
            metadata_cover, content, surface_scale, self._control_reveal_at(metadata_now),
        )
        normal_metadata_exposed = paint_region is None or paint_region.intersects(
            QRectF(content.left(), original_cover.bottom(), metadata_width, rect.bottom() - original_cover.bottom()).toAlignedRect()
        )
        center_metadata_exposed = paint_region is None or paint_region.intersects(
            centered_title_rect.united(centered_artist_rect).adjusted(-3.0, -3.0, 3.0, 3.0).toAlignedRect()
        )
        # The two metadata layouts have different wrapping and sit far apart.
        # Let only one occupy the screen at a time while the cover travels;
        # overlapping cross-fades looked like duplicated song names mid-flight.
        center_phase = max(0.0, min(1.0, (cover_fraction - 0.5) / 0.3))
        center_opacity = center_phase * center_phase * (3.0 - 2.0 * center_phase)
        normal_phase = max(0.0, min(1.0, (0.5 - cover_fraction) / 0.3))
        normal_opacity = normal_phase * normal_phase * (3.0 - 2.0 * normal_phase) * metadata_opacity
        cover_mask = QPainterPath()
        painted_mask = QPainterPath()
        if cover_fraction > 0.0:
            cover_mask.addRect(rect)
            painted_mask.addRoundedRect(
                painted_cover, painted_cover.width() * 0.055, painted_cover.height() * 0.055,
            )
        unobscured_path = cover_mask.subtracted(painted_mask) if cover_fraction > 0.0 else None
        if normal_metadata_exposed or center_metadata_exposed:
            self._metadata_hit_rects.clear()
            if normal_opacity <= 0.001:
                self._metadata_slot_widths.clear()
            # During the centerward travel, the artwork crosses the former
            # title slot.  Fade that slot through the narrow crossing and
            # never let its glyphs paint over the cover itself.
            metadata_identity = self._track_visual[0] if self._track_visual else self._visual_track_identity()
            self._metadata_marquee_moving = False
            self._metadata_animation_rect = QRect()
            self._metadata_next_motion_at = float("inf")
            if normal_metadata_exposed and normal_opacity > 0.001:
                painter.save()
                if unobscured_path is not None:
                    painter.setClipPath(unobscured_path)
                metadata_transition = transition if previous and cover_fraction < 0.1 else 1.0
                painter.setOpacity(normal_opacity * metadata_transition)
                painter.setFont(title_font)
                painter.setPen(QColor(255, 255, 255, 242))
                self._paint_scrolling_metadata(
                    painter, title_rect, str(getattr(self.owner, "track_title", "") or "暂无播放"),
                    title_font, metadata_now, metadata_identity,
                    field="title",
                )
                if previous and cover_fraction < 0.1:
                    painter.save()
                    painter.setOpacity(normal_opacity * (1.0 - transition))
                    self._paint_scrolling_metadata(
                        painter, title_rect, previous[1], title_font,
                        metadata_now, previous[0], field="title", animate=False,
                    )
                    painter.restore()
                painter.setPen(QColor(255, 255, 255, 142))
                painter.setFont(artist_font)
                self._paint_scrolling_metadata(
                    painter, artist_rect, str(getattr(self.owner, "artist", "") or ""),
                    artist_font, metadata_now, metadata_identity,
                    field="artist",
                )
                if previous and cover_fraction < 0.1:
                    painter.save()
                    painter.setOpacity(normal_opacity * (1.0 - transition))
                    self._paint_scrolling_metadata(
                        painter, artist_rect, previous[2], artist_font,
                        metadata_now, previous[0], field="artist", animate=False,
                    )
                    painter.restore()
                painter.restore()
            if center_metadata_exposed and center_opacity > 0.001:
                painter.save()
                painter.setOpacity(center_opacity)
                # The text is shaped at a stable target width and translated
                # as the cover travels. There is no per-frame wrap or raster
                # rebuild when the controls slide into place.
                painter.setPen(QColor(255, 255, 255, 242))
                center_plan["title_layout"].draw(painter, centered_title_rect.topLeft())
                painter.setPen(QColor(255, 255, 255, 155))
                center_plan["artist_layout"].draw(painter, centered_artist_rect.topLeft())
                painter.restore()

        text_left = side_column.left()
        text_width = max(1.0, side_column.width())
        cover_lyrics_layout = self._effective_content_layout() == "cover_lyrics"
        lyrics_hidden_by_layout = (self._effective_content_layout() in {"cover", "playlist"}
                                   or navigation_capture and cover_lyrics_layout)
        lyric_transform = QTransform()
        lyric_cover = painted_cover
        if cover_lyrics_layout:
            # Reuse the real lyric timing/translation/duet rendering inside
            # one stable logical cover. Controls and pause animate the art
            # size; scaling its lyric canvas avoids restarting a long song's
            # deferred layout (and losing its translations) on every frame.
            lyric_cover = QRectF(center_plan["shown_cover"])
            lyric_transform.translate(painted_cover.left(), painted_cover.top())
            lyric_transform.scale(painted_cover.width() / max(1., lyric_cover.width()),
                                  painted_cover.height() / max(1., lyric_cover.height()))
            lyric_transform.translate(-lyric_cover.left(), -lyric_cover.top())
            text_left = lyric_cover.left() + 25. * surface_scale
            text_width = max(1., lyric_cover.width() - 50. * surface_scale)
            if not navigation_capture:
                painter.save()
                path = QPainterPath()
                radius = self._album_art_radius(painted_cover.width())
                path.addRoundedRect(painted_cover, radius, radius)
                painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
                shade = QLinearGradient(painted_cover.left(), painted_cover.top(), painted_cover.left(), painted_cover.bottom())
                shade.setColorAt(0., QColor(0, 0, 0, 0))
                shade.setColorAt(.42, QColor(0, 0, 0, 12))
                shade.setColorAt(.72, QColor(0, 0, 0, 135))
                shade.setColorAt(1., QColor(0, 0, 0, 180))
                painter.fillRect(painted_cover, shade)
                painter.restore()
        lines, timed = self._display_lyrics()
        loading_now = time.monotonic()
        self._update_lyric_loading_state(loading_now, lines, timed)
        lookup_pending = self._lyrics_lookup_pending(lines, timed)
        holding_loaded_lyrics = not lookup_pending and self._lyric_loading_holds_result(loading_now)
        loading_lyrics = lookup_pending or holding_loaded_lyrics
        if lookup_pending:
            # A search marker is state, not lyric ink. Its timed result can
            # arrive on the next provider poll without waiting for artwork.
            lines, timed = [], []
        low_perception = self._reading_strategy() == "low_perception"
        centered_target = self._no_lyrics_cover_target() and not cover_lyrics_layout
        outgoing_lyrics = None
        if centered_target:
            # Keep the last actually painted lyric sentence for the short exit
            # transition. The provider may already have replaced its rows with
            # a loading/empty result, but the old ink should still travel to
            # the right instead of vanishing at the decision edge.
            snapshot_track = (
                self._visual_track_identity(),
                str(getattr(self.owner, "track_title", "") or ""),
                str(getattr(self.owner, "artist", "") or ""),
            )
            if (
                self._cover_layout_to >= 1.0
                and cover_fraction < 0.999
                and self._last_visible_lyric_snapshot_track == snapshot_track
            ):
                outgoing_lyrics = self._last_visible_lyric_snapshot
            if outgoing_lyrics is None:
                lines, timed = [], []
            else:
                lines, timed = outgoing_lyrics[:2]
        visual_layers = self._visual_lyric_layers_for_rows(
            lines, timed, outgoing=outgoing_lyrics is not None,
        )
        # An all-parenthetical row has no persistent main text. Keep a space
        # for layout/seek geometry, but never paint its hidden source ink.
        paint_lines = [layer.primary_text or " " for layer in visual_layers]
        translation_lines = tuple(layer.translation_text or "" for layer in visual_layers)
        parenthetical_lines = tuple(layer.parenthetical_text or "" for layer in visual_layers)
        parenthetical_translation_lines = tuple(
            layer.parenthetical_translation_text or "" for layer in visual_layers
        )
        duet = self._display_duet_presentation
        cached_display = self._display_lyrics_cache
        use_duet = (
            outgoing_lyrics is None and duet is not None and cached_display is not None
            and lines is cached_display[0] and timed is cached_display[1]
            and len(duet.rows) == len(paint_lines)
        )
        pair_starts = tuple(
            group.first_index for group in duet.groups
        ) if use_duet else ()
        pair_first_by_row = {
            index: first for first in pair_starts for index in (first, first + 1)
        }
        duet_gutter = max(24.0 * surface_scale, text_width * 0.065)
        duet_column_width = max(80.0, (text_width - duet_gutter) * 0.5)
        duet_right_sides = [
            row.alignment == "right" for row in duet.rows
        ] if use_duet else [False] * len(paint_lines)
        for first in pair_starts:
            # A same-singer doublet still needs two non-overlapping columns.
            if duet_right_sides[first] == duet_right_sides[first + 1]:
                duet_right_sides[first], duet_right_sides[first + 1] = False, True
        row_widths = tuple(
            duet_column_width if index in pair_first_by_row else text_width
            for index in range(len(paint_lines))
        )
        row_lefts = tuple(
            text_left + (duet_column_width + duet_gutter)
            if index in pair_first_by_row and duet_right_sides[index]
            else text_left
            for index in range(len(paint_lines))
        )
        self._has_timed_lyrics = bool(timed and outgoing_lyrics is None and not loading_lyrics and not lyrics_hidden_by_layout)
        lyric_now = time.monotonic()
        # Cache preparation is not a displayed lyric timeline. Keep the
        # empty presentation clock until handoff, so its first visible frame
        # anchors to the player's current time rather than confirming a jump
        # from an earlier hidden result.
        lyric_frame = outgoing_lyrics[2] if outgoing_lyrics is not None else self._lyric_frame(
            [] if holding_loaded_lyrics else timed, lyric_now,
        )
        if not centered_target and timed and not loading_lyrics:
            self._last_visible_lyric_snapshot = (list(lines), list(timed), lyric_frame)
            self._last_visible_lyric_snapshot_track = (
                self._visual_track_identity(),
                str(getattr(self.owner, "track_title", "") or ""),
                str(getattr(self.owner, "artist", "") or ""),
            )
        active = min(lyric_frame.active_index, len(lines) - 1)
        current_position = float(self._lyric_motion.last_position or 0.0)
        if holding_loaded_lyrics and timed:
            active = active_index_for_position(timed, current_position)
        next_start = float(timed[active + 1][0]) if timed and active + 1 < len(timed) else None
        active_pair_first = pair_first_by_row.get(active)
        paired_end = (
            float(timed[active_pair_first + 2][0])
            if active_pair_first is not None and active_pair_first + 2 < len(timed)
            else None
        )
        if active_pair_first is not None and paired_end is None:
            # The final duet has no following row. Its first voice must not
            # finish merely because the second voice starts singing.
            paired_end = max(
                float(getattr(self.owner, "media_duration_seconds", 0.0) or 0.0),
                float(timed[active_pair_first + 1][0]) + 2.45,
            )
        source_read_ends = getattr(self, "_display_timed_read_ends", ()) if outgoing_lyrics is None else ()
        attached_read_end = source_read_ends[active] if 0 <= active < len(source_read_ends) else None
        active_reading_end = (
            paired_end if paired_end is not None else
            attached_read_end if attached_read_end is not None else next_start
        )
        active_reading_progress = lyric_frame.reading_progress
        if active_pair_first is not None and paired_end is not None:
            started_at = float(timed[active][0])
            active_reading_progress = max(
                0.0, min(1.0,
                    (current_position - started_at) / max(0.001, paired_end - started_at)
                ),
            )
        elif attached_read_end is not None and timed and 0 <= active < len(timed):
            started_at = float(timed[active][0])
            active_reading_progress = max(0.0, min(1.0,
                (current_position - started_at) / max(0.001, attached_read_end - started_at),
            ))
        active_kind = (
            self._display_timed_kinds[active]
            if outgoing_lyrics is None and 0 <= active < len(self._display_timed_kinds)
            else "line"
        )
        active_units = (
            self._display_timed_units[active]
            if outgoing_lyrics is None and 0 <= active < len(self._display_timed_units)
            else ()
        )
        source_active_units = active_units
        unverified_unit_row = active_kind == "unit" and not genuinely_timed_unit_row(active_units)
        suppress_estimated_highlight = (
            outgoing_lyrics is None and self._has_only_unverified_unit_timing(timed)
        )
        active_primary = self._primary_timed_lyric_text(str(timed[active][1])) if timed and 0 <= active < len(timed) else ""
        active_visible_primary = paint_lines[active] if 0 <= active < len(paint_lines) else ""
        active_source_offsets = (
            self._visible_primary_source_offsets(timed[active][1], visual_layers[active])
            if timed and 0 <= active < len(visual_layers) else None
        )
        active_plan = (
            self._display_row_timings[active]
            if outgoing_lyrics is None and 0 <= active < len(self._display_row_timings)
            else resolve_display_row_timing(active_primary, active_units, active_kind,
                start=float(timed[active][0]) if timed and 0 <= active < len(timed) else 0.,
                next_start=active_reading_end)
        )
        active_units = active_plan.display_units
        active_real_front = display_row_reading_front(
            active_plan, current_position, range(len(active_primary) + 1),
        )
        self._last_lyric_timing_quality = active_plan.quality
        self._last_lyric_render_branch = (
            "word-reading" if active_plan.has_reading_timing else "whole-row"
        )
        if active_real_front is not None:
            active_reading_progress = active_plan.reading_progress(current_position)
        if active_kind == "unit" and not active_plan.has_reading_timing:
            active_kind = "line"
        song_has_real_units = bool(
            timed and outgoing_lyrics is None and self._has_genuine_obo_timing(lines, timed)
        )
        song_reads_internally = bool(active_plan.has_reading_timing) or not low_perception
        estimated_end = None
        if (
            active_plan.quality == "estimated" and active_pair_first is None
            and timed and 0 <= active < len(timed)
            and timed[active][1] != INTRO_COUNTDOWN_TEXT
        ):
            estimated_end = active_plan.end_seconds
            estimated_progress = active_plan.reading_progress(current_position)
            if estimated_progress is not None:
                active_reading_progress = estimated_progress
        # Genuine word timing may finish before the next row's start.  Move
        # only the viewport focus at that point; keep the media-clock active
        # row unchanged so the next sentence remains unread until its time.
        visual_active = active
        if not song_has_real_units or not timed or outgoing_lyrics is not None:
            self._obo_advanced_focus_index = None
            self._obo_advanced_focus_pose = None
            self._obo_advanced_handoff_last_at = None
        handoff_active = (
            self._obo_advanced_focus_index is not None
            and active == self._obo_advanced_focus_index
            and self._obo_advanced_focus_pose is not None
        )
        if self._obo_advanced_focus_index is not None and active >= self._obo_advanced_focus_index:
            self._obo_advanced_focus_index = None
            if not handoff_active:
                self._obo_advanced_focus_pose = None
                self._obo_advanced_handoff_last_at = None
        real_unit_focus_ready = (
            active_kind == "unit"
            and real_unit_row_finished_before_next_start(
                active_primary, active_units, current_position, next_start,
            )
        )
        estimated_focus_ready = (
            active_kind == "line" and estimated_end is not None
            and next_start is not None and estimated_end < next_start
            and estimated_end <= current_position < next_start
        )
        if (
            self._obo_advanced_focus_index == active + 1
            and next_start is not None and current_position < next_start
            and not (real_unit_focus_ready or estimated_focus_ready)
        ):
            # A confirmed same-row seek can make the spoken row unfinished
            # again without changing its active index. Retain early focus only
            # while its existing unit/row completion evidence still holds.
            # Beyond next_start, keep the pose through boundary confirmation.
            self._obo_advanced_focus_index = None
            self._obo_advanced_focus_pose = None
            self._obo_advanced_handoff_last_at = None
        if (
            song_has_real_units and (real_unit_focus_ready or estimated_focus_ready)
            and active_pair_first is None
            and next_start is not None and active + 1 < len(timed)
        ):
            if self._obo_advanced_focus_index != active + 1:
                # The waiting row rests on the ordinary layout baseline. Its
                # unread reading pose is lower, so approach that pose during
                # the early roll instead of dropping at the source boundary.
                self._obo_advanced_focus_pose = (
                    current_position, lyric_now, 0.0,
                    max(0.001, min(0.22, next_start - current_position)),
                    bool(getattr(self.owner, "media_is_playing", False)), 0.0,
                )
                self._obo_advanced_handoff_last_at = None
            self._obo_advanced_focus_index = active + 1
        if self._obo_advanced_focus_index == active + 1:
            # The media clock confirms a row boundary a few frames after its
            # timestamp. An already completed OBO row must not pull the view
            # back while that confirmation catches up.
            visual_active = active + 1
        obo_unread_pose = 0.0
        obo_reading_handoff = 0.0
        if (visual_active != active or handoff_active) and self._obo_advanced_focus_pose is not None:
            started_position, last_at, playing_elapsed, prepare_seconds, was_playing, previous_phase = self._obo_advanced_focus_pose
            playing = bool(getattr(self.owner, "media_is_playing", False))
            if playing and was_playing:
                playing_elapsed += max(0.0, lyric_now - last_at)
            candidate = max(0.0, min(1.0, max(
                current_position - started_position, playing_elapsed,
            ) / prepare_seconds))
            if handoff_active:
                # The source clock can skip straight across a short gap. Let
                # the first reading frame start at the last painted position,
                # then catch up over a bounded visible interval.
                if self._obo_advanced_handoff_last_at is None:
                    self._obo_advanced_handoff_last_at = lyric_now
                    candidate = previous_phase
                elif playing and was_playing:
                    candidate = min(1.0, previous_phase + max(
                        0.0, lyric_now - self._obo_advanced_handoff_last_at,
                    ) / prepare_seconds)
                    self._obo_advanced_handoff_last_at = lyric_now
            phase = max(previous_phase, candidate)
            self._obo_advanced_focus_pose = (
                started_position, lyric_now, playing_elapsed, prepare_seconds, playing, phase,
            )
            obo_unread_pose = phase * phase * (3.0 - 2.0 * phase)
            if handoff_active:
                obo_reading_handoff = 1.0 - obo_unread_pose
                if phase >= 1.0:
                    self._obo_advanced_focus_pose = None
                    self._obo_advanced_handoff_last_at = None
        visual_focus_row = pair_first_by_row.get(visual_active, visual_active)
        real_groups = self._project_highlight_groups(
            explicit_slow_unit_groups(active_primary, source_active_units)
            if active_kind == "unit" else (),
            active_source_offsets,
        )
        emphasis = lyric_emphasis_plan(
            active_visible_primary,
            float(timed[active][0]),
            active_reading_end,
            min_seconds_per_unit=getattr(
                self.owner, "music_sparse_highlight_seconds_per_unit", SPARSE_MIN_SECONDS_PER_UNIT
            ),
        ) if active_kind == "line" and not low_perception and not unverified_unit_row and not suppress_estimated_highlight and timed and 0 <= active < len(timed) else None
        active_priority_mode = (
            "scatter_glow" if real_groups else
            emphasis.mode if emphasis is not None else "none"
        )
        self._last_lyric_emphasis_mode = active_priority_mode
        if real_groups:
            current_group = next((
                group for group in real_groups
                if group.start_seconds <= current_position < group.end_seconds
            ), None)
            self._active_glow_stage = (
                lyric_glow_phase(
                    (current_position - current_group.start_seconds)
                    / max(1e-9, current_group.end_seconds - current_group.start_seconds)
                ).stage if current_group is not None else "done"
            )
        else:
            self._active_glow_stage = (
                lyric_glow_phase(active_reading_progress).stage
                if emphasis is not None and emphasis.mode != "none" else "done"
            )
        lyric_top = content.top() + max(8.0, rect.height() * 0.018)
        lyric_height = max(1.0, content.height() - max(12.0, rect.height() * 0.035))
        if cover_lyrics_layout:
            lyric_top = lyric_cover.top() + lyric_cover.height() * .42
            lyric_height = max(1., lyric_cover.height() * .55)
        lyric_font = self._surface_font(self.LYRIC_PIXEL_SIZE * surface_scale * (.74 if cover_lyrics_layout else 1.), QFont.Weight.Bold)
        secondary_font = self._surface_font(
            max(1.0, (self.LYRIC_PIXEL_SIZE - 6.0) * surface_scale * 0.5), QFont.Weight.Normal,
        )
        parenthetical_font = self._surface_font(
            max(1.0, (self.LYRIC_PIXEL_SIZE - 6.0) * surface_scale), QFont.Weight.Bold,
        )
        painter.setFont(lyric_font)
        lyric_clip = QRectF(text_left, lyric_top, text_width, lyric_height)
        self._lyric_clip_rect = QRectF() if lyrics_hidden_by_layout else lyric_transform.mapRect(lyric_clip)
        glow_bleed = max(60.0 * surface_scale, self._font_height(lyric_font) * 1.7)
        # The inset viewport anchors wrapping, follow and hit targets, not
        # ink. Blur and the highlight halo must cross either horizontal inset
        # naturally instead of ending at a visible vertical cut. The existing
        # cover mask and real window edge still bound the composed surface.
        lyric_ink_clip = QRectF(rect)
        self._lyric_animation_rect = QRectF(
            lyric_clip.left() - glow_bleed, rect.top(),
            lyric_clip.width() + 2.0 * glow_bleed, rect.height(),
        ).intersected(rect).toAlignedRect()
        if lyrics_hidden_by_layout:
            self._lyric_animation_rect = QRect()
        elif cover_lyrics_layout:
            self._lyric_animation_rect = painted_cover.toAlignedRect()
        # The target layout changes discretely at a lyric boundary.  Each
        # visual line below keeps its own physical position and springs toward
        # that new target, which creates the staggered Apple-style group roll.
        anchor, focus_index, manual_view = self._lyric_view_anchor(
            visual_focus_row, len(lines), lyric_now,
        )
        focus_index = pair_first_by_row.get(focus_index, focus_index)
        self._lyric_hit_rects = {}
        # Sentence spacing leaves a clear entrance lane for annotations;
        # wrapping within a sentence retains its tighter visual-line rhythm.
        row_gap = max(18.0 * surface_scale, rect.height() * 0.035)
        layouts, row_tops = self._song_lyric_layouts(
            paint_lines, lyric_font, text_width, row_gap,
            defer=holding_loaded_lyrics or bool(previous and transition < 0.35) or len(paint_lines) > 48,
            secondary_lines=translation_lines, secondary_font=secondary_font,
            parenthetical_lines=parenthetical_lines,
            parenthetical_translation_lines=parenthetical_translation_lines,
            parenthetical_font=parenthetical_font,
            row_widths=row_widths,
            row_right_aligned=tuple(duet_right_sides),
            pair_starts=pair_starts,
        )
        parenthetical_factors = self._parenthetical_reserve_factors(
            visual_active, lyric_now, pair_starts=pair_starts,
        )
        parenthetical_open_poses = tuple(
            self._parenthetical_reveal_pose(
                index, lyric_now,
                focused=pair_first_by_row.get(index, index) == focus_index,
            )[0] if factor > 0.0 else 0.0
            for index, factor in enumerate(parenthetical_factors)
        )
        translation_texts = tuple(
            "\n".join(filter(None, (translation_lines[index], parenthetical_translation_lines[index])))
            for index in range(len(layouts))
        )
        self._translation_known_tokens = {
            (index, text) for index, text in enumerate(translation_texts) if text
        }
        self._translation_entered_at = {
            token: began for token, began in self._translation_entered_at.items()
            if token in self._translation_known_tokens
        }
        translation_poses = tuple(
            self._translation_layer_pose(
                index, translation_texts[index],
                lyric_now,
            )
            for index in range(len(layouts))
        )
        annotation_extras = tuple(
            self._song_parenthetical_extra_heights[index]
            - (self._song_parenthetical_translation_layouts[index][1]
               if self._song_parenthetical_translation_layouts[index] is not None else 0.0)
            * (1.0 - translation_poses[index][0])
            for index in range(len(layouts))
        )
        if layouts and (any(parenthetical_factors) or any(self._song_translation_extra_heights)):
            adjusted_tops = []
            adjusted_visual_tops = list(self._song_visual_tops)
            reserve_before = 0.0
            row_index = 0
            while row_index < len(row_tops):
                group = (row_index, row_index + 1) if row_index in pair_starts else (row_index,)
                for member in group:
                    adjusted_tops.append(row_tops[member] + reserve_before)
                    start, end = self._song_visual_ranges[member]
                    for visual_index in range(start, end):
                        adjusted_visual_tops[visual_index] += reserve_before
                reserve_before += max(
                    annotation_extras[member]
                    * parenthetical_factors[member]
                    + self._song_translation_extra_heights[member] * translation_poses[member][0]
                    for member in group
                )
                row_index += len(group)
            row_tops = tuple(adjusted_tops)
            visual_tops = tuple(adjusted_visual_tops)
        else:
            visual_tops = self._song_visual_tops
        active_emphasis_layout = (
            layouts[active][0]
            if active_priority_mode != "none" and focus_index == pair_first_by_row.get(active, active) and 0 <= active < len(layouts)
            else None
        )
        emphasis_unread_color = self._future_lyric_color(lyric_clip)
        self._protect_lyric_emphasis_assets(
            paint_lines[active] if active_emphasis_layout is not None else "", lyric_font,
            active_emphasis_layout, unread_color=emphasis_unread_color,
        )
        obo_active_translation_lift = (
            obo_reading_handoff * self._reading_lift_height()
            * max(0.5, float(max(1, secondary_font.pixelSize())) / self.LYRIC_PIXEL_SIZE)
        )
        obo_active_primary_lift = (
            obo_reading_handoff * self._reading_lift_height()
            * max(0.5, float(max(1, lyric_font.pixelSize())) / self.LYRIC_PIXEL_SIZE)
        )
        obo_active_parenthetical_lift = (
            obo_reading_handoff * self._reading_lift_height()
            * max(0.5, float(max(1, parenthetical_font.pixelSize())) / self.LYRIC_PIXEL_SIZE)
        )
        if timed and outgoing_lyrics is None:
            focus_change_time = next_start
            if active_kind == "unit" and song_has_real_units and active_pair_first is None:
                ends = [
                    float(unit.end_seconds) for unit in active_units
                    if getattr(unit, "end_seconds", None) is not None
                ]
                finished_at = max(ends, default=0.0)
                if real_unit_row_finished_before_next_start(
                    active_primary, active_units, finished_at, next_start,
                ):
                    focus_change_time = finished_at
            elif estimated_end is not None and estimated_end < next_start:
                focus_change_time = estimated_end
            self._queue_next_lyric_blurs(
                paint_lines, layouts, row_tops, lyric_font, text_width,
                lyric_clip, glow_bleed, emphasis_unread_color,
                active, focus_change_time, current_position, lyric_now,
                manual_view=manual_view, row_widths=row_widths,
                secondary_lines=translation_lines, secondary_font=secondary_font,
                pair_starts=pair_starts,
            )
        else:
            self._lyric_blur_prewarm_queue.clear()
            self._lyric_blur_prewarm_token = None
        # Prepare an eligible glow and the next ordinary reading row between
        # frames. Neither effect should shape a whole cold row at its boundary.
        reading_candidate = None
        glow_queued = False
        for candidate in range(max(0, active), min(len(timed) - 1, active + 3) + 1) if layouts else ():
            if candidate == active:
                candidate_kind = active_kind
                candidate_mode = active_priority_mode
            else:
                candidate_kind = (
                    self._display_timed_kinds[candidate]
                    if candidate < len(self._display_timed_kinds) else "line"
                )
                candidate_units = (
                    self._display_timed_units[candidate]
                    if candidate < len(self._display_timed_units) else ()
                )
                unverified_candidate = candidate_kind == "unit" and not genuinely_timed_unit_row(candidate_units)
                if unverified_candidate:
                    candidate_kind = "line"
                if candidate_kind == "unit":
                    candidate_groups = explicit_slow_unit_groups(
                        self._primary_timed_lyric_text(str(timed[candidate][1])), candidate_units,
                    )
                    candidate_mode = "scatter_glow" if candidate_groups else "none"
                elif candidate_kind == "synthetic":
                    candidate_mode = "none"
                elif low_perception or unverified_candidate or suppress_estimated_highlight:
                    candidate_mode = "none"
                else:
                    candidate_read_end = (
                        source_read_ends[candidate]
                        if candidate < len(source_read_ends) else None
                    )
                    candidate_mode = lyric_emphasis_plan(
                        paint_lines[candidate],
                        float(timed[candidate][0]),
                        candidate_read_end if candidate_read_end is not None else
                        float(timed[candidate + 1][0]) if candidate + 1 < len(timed) else None,
                        min_seconds_per_unit=getattr(
                            self.owner, "music_sparse_highlight_seconds_per_unit", SPARSE_MIN_SECONDS_PER_UNIT
                        ),
                    ).mode
            if candidate_mode == "none":
                if (
                    reading_candidate is None
                    and timed and outgoing_lyrics is None
                    and candidate == active + 1
                    and candidate_kind != "synthetic"
                    and song_reads_internally
                    and not self._has_reversed_lyric_unit_order(paint_lines[candidate])
                ):
                    candidate_layout = layouts[candidate][0]
                    if not self._lyric_reading_assets_ready(
                        paint_lines[candidate], lyric_font, candidate_layout, emphasis_unread_color,
                    ):
                        reading_candidate = (candidate, candidate_layout)
                continue
            if glow_queued:
                continue
            token = (self._song_layout_key, candidate)
            candidate_layout = layouts[candidate][0]
            if self._lyric_emphasis_assets_ready(
                paint_lines[candidate], lyric_font, row_widths[candidate], candidate_layout,
                candidate_mode, emphasis_unread_color,
            ):
                continue
            if self._pending_glow_prewarm is None or self._pending_glow_prewarm[0] != token:
                self._pending_glow_prewarm = (
                    token, paint_lines[candidate], lyric_font, row_widths[candidate],
                    candidate_layout, candidate_mode,
                    emphasis_unread_color, 0, 0,
                )
            glow_queued = True
        if reading_candidate is None:
            candidate = active + 1
            if (
                timed and outgoing_lyrics is None and 0 <= candidate < len(layouts)
                and song_reads_internally
            ):
                # These layers appear at the same boundary as the main row.
                # Prepare their existing unit/tint assets incrementally too.
                for layer, layer_text, layer_font in (
                    (self._song_secondary_layouts[candidate], translation_lines[candidate], secondary_font),
                    (self._song_parenthetical_layouts[candidate], parenthetical_lines[candidate], parenthetical_font),
                    (self._song_parenthetical_translation_layouts[candidate], parenthetical_translation_lines[candidate], secondary_font),
                ):
                    if layer is not None and not self._lyric_reading_assets_ready(
                        layer_text, layer_font, layer[0], emphasis_unread_color,
                    ):
                        reading_candidate = (candidate, layer_text, layer_font, layer[0])
                        break
        elif len(reading_candidate) == 2:
            candidate, candidate_layout = reading_candidate
            reading_candidate = (candidate, paint_lines[candidate], lyric_font, candidate_layout)
        if reading_candidate is None:
            self._pending_reading_prewarm = None
        else:
            candidate, reading_text, reading_font, candidate_layout = reading_candidate
            token = (
                self._song_layout_key, self._visual_track_identity(),
                candidate, int(emphasis_unread_color.rgba()), reading_text, reading_font.toString(),
            )
            if self._pending_reading_prewarm is None or self._pending_reading_prewarm[0] != token:
                self._pending_reading_prewarm = (
                    token, reading_text, reading_font, candidate_layout,
                    emphasis_unread_color, 0, 0,
                )
        if layouts:
            lower = max(0, min(len(layouts) - 1, int(math.floor(anchor))))
            upper = min(len(layouts) - 1, lower + 1)
            fraction = max(0.0, min(1.0, anchor - lower))
            def row_center(index: int) -> float:
                first = pair_first_by_row.get(index)
                def block_height(row: int) -> float:
                    height = layouts[row][1]
                    if cover_lyrics_layout:
                        height += annotation_extras[row] * parenthetical_factors[row]
                        height += self._song_translation_extra_heights[row] * translation_poses[row][0]
                    return height
                height = max(block_height(first), block_height(first + 1)) if first is not None else block_height(index)
                return row_tops[index] + height * 0.5
            lower_center = row_center(lower)
            upper_center = row_center(upper)
            anchor_center = lower_center * (1.0 - fraction) + upper_center * fraction
        else:
            anchor_center = 0.0
        origin_y = lyric_clip.top() + lyric_clip.height() * 0.43 - anchor_center
        visual_targets = tuple(origin_y + top for top in visual_tops) if layouts else ()
        visual_ranges = self._song_visual_ranges if layouts else ()
        active_visual_index = visual_ranges[visual_focus_row][0] if visual_ranges and 0 <= visual_focus_row < len(visual_ranges) else 0
        logical_spring_key = (
            self._browse_track_key,
            tuple((start, paint_lines[index]) for index, (start, _text) in enumerate(timed))
            if timed else tuple(paint_lines),
            lyric_font.toString(),
            max(1, int(round(text_width))),
            self._song_visual_counts,
        )
        visual_row_ids = self._visual_row_identities(paint_lines) if layouts else ()
        spring_visible_range = self._visible_visual_spring_range(
            visual_targets, rect, glow_bleed, logical_spring_key, visual_row_ids,
        )
        logical_spring = self._logical_line_spring.frame(
            logical_spring_key,
            visual_targets,
            active_visual_index,
            lyric_now,
            animate=not manual_view and not previous,
            sequence_index=visual_focus_row - sum(first < visual_focus_row for first in pair_starts),
            visible_range=spring_visible_range,
            row_ids=visual_row_ids,
        )
        self._start_translation_entries_with_clearance(
            translation_texts, layouts, logical_spring.positions,
            visual_ranges, parenthetical_factors, pair_first_by_row, lyric_now,
            spring_visible_range,
        )
        translation_poses = tuple(
            self._translation_layer_pose(index, text, lyric_now)
            for index, text in enumerate(translation_texts)
        )
        focus_target_shift = anchor_center - row_center(focus_index) if layouts else 0.0
        blur_levels, blur_focus_moving = self._lyric_blur_follow_levels(
            logical_spring_key, focus_index, logical_spring.positions,
            tuple(target + focus_target_shift for target in visual_targets),
            visual_ranges, pair_starts=pair_starts,
        )
        self._lyric_spring_active = any(
            abs(logical_spring.positions[index] - visual_targets[index]) > 0.10
            or abs(logical_spring.velocities[index]) > 0.10
            for index in range(*spring_visible_range)
        )
        active_animation_rect = QRectF()
        painter.save()
        lyric_opacity = 0. if lyrics_hidden_by_layout else (1. if cover_lyrics_layout else 1.0 - cover_fraction)
        if previous and outgoing_lyrics is None and not loading_lyrics:
            lyric_opacity *= max(0.0, min(1.0, (transition - 0.35) / 0.65))
        painter.setOpacity(lyric_opacity)
        # Keep the surrounding blurred rows behind the visible song title.
        # Follow its existing cover-travel/blur fades without dimming album color,
        # the focused lyric/duet or deliberately browsed lyric text.
        surrounding_lyric_opacity = 1.0 - 0.28 * max(normal_opacity, center_opacity) if (
            str(getattr(self.owner, "track_title", "") or "").strip()
        ) else 1.0
        if cover_lyrics_layout:
            path = QPainterPath(); path.addRoundedRect(painted_cover, self._album_art_radius(painted_cover.width()), self._album_art_radius(painted_cover.width()))
            painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
            painter.setWorldTransform(lyric_transform, True)
        else:
            painter.setClipRect(lyric_ink_clip, Qt.ClipOperation.IntersectClip)
            if unobscured_path is not None:
                painter.setClipPath(unobscured_path, Qt.ClipOperation.IntersectClip)
        lyric_slide = 0. if cover_lyrics_layout else self._lyric_horizontal_slide(cover_fraction, rect.right(), text_left)
        painter.translate(lyric_slide, 0.0)
        visual_ends = self._song_visual_ends if layouts else ()
        first_row = bisect_right(visual_ends, spring_visible_range[0]) if layouts else 0
        last_row = bisect_right(visual_ends, max(0, spring_visible_range[1] - 1)) + 1 if layouts else 0
        first_row = pair_first_by_row.get(first_row, first_row)
        if last_row - 1 in pair_first_by_row:
            last_row = max(last_row, pair_first_by_row[last_row - 1] + 2)
        if loading_lyrics:
            # Use the remaining loader cycle to build the actual result via
            # the existing bounded layout/glyph prewarm. Hidden rows never
            # paint, become seek targets, or replace the visible snapshot.
            first_row = last_row = 0
        for index in range(first_row, min(len(lines), last_row)):
            if not 0 <= index < len(lines):
                continue
            layout, layout_height = layouts[index]
            row_height = (
                self._song_row_heights[index]
                + annotation_extras[index] * parenthetical_factors[index]
                + self._song_translation_extra_heights[index] * translation_poses[index][0]
            )
            row_width = row_widths[index]
            row = QRectF(row_lefts[index], origin_y + row_tops[index], row_width, row_height)
            visual_start, visual_end = visual_ranges[index]
            line_offsets = tuple(
                logical_spring.positions[flat_index] - visual_targets[flat_index]
                for flat_index in range(visual_start, visual_end)
            )
            if index in pair_first_by_row and line_offsets:
                group_visual = visual_ranges[pair_first_by_row[index]][0]
                group_offset = logical_spring.positions[group_visual] - visual_targets[group_visual]
                secondary_layout = self._song_secondary_layouts[index]
                line_offsets = (group_offset,) * max(1, layout.lineCount())
            painted_top = min(
                logical_spring.positions[flat_index]
                for flat_index in range(visual_start, visual_end)
            ) if visual_end > visual_start else row.top()
            painted_bottom = max(
                logical_spring.positions[flat_index] + self._song_visual_heights[flat_index]
                for flat_index in range(visual_start, visual_end)
            ) if visual_end > visual_start else row.bottom()
            if index in pair_first_by_row and line_offsets:
                painted_top = row.top() + line_offsets[0]
                painted_bottom = painted_top + row_height
            if parenthetical_factors[index] > 0.0:
                painted_bottom = max(painted_bottom, row.bottom())
            if self._song_secondary_layouts[index] is not None:
                painted_bottom = max(painted_bottom, row.bottom() + (line_offsets[-1] if line_offsets else 0.0))
            paired_continuation = (
                active_pair_first is not None and paired_end is not None
                and index == active_pair_first and index < active
                and current_position < paired_end
            )
            if index in (active, visual_active) or paired_continuation:
                ink_left = min(
                    (row.left() + layout.lineAt(line_index).x() for line_index in range(layout.lineCount())),
                    default=row.left(),
                )
                ink_right = max(
                    (
                        row.left() + layout.lineAt(line_index).x()
                        + layout.lineAt(line_index).naturalTextWidth()
                        for line_index in range(layout.lineCount())
                    ),
                    default=row.right(),
                )
                if self._song_secondary_layouts[index] is not None:
                    secondary_layout = self._song_secondary_layouts[index][0]
                    ink_left = min(ink_left, *(
                        row.left() + secondary_layout.lineAt(line_index).x()
                        for line_index in range(secondary_layout.lineCount())
                    ))
                    ink_right = max(ink_right, *(
                        row.left() + secondary_layout.lineAt(line_index).x()
                        + secondary_layout.lineAt(line_index).naturalTextWidth()
                        for line_index in range(secondary_layout.lineCount())
                    ))
                if (self._song_parenthetical_layouts[index] is not None
                        or self._song_parenthetical_translation_layouts[index] is not None):
                    # The annotation occupies only its shaped text width.
                    # Expanding to row.right() turns a short sung aside into
                    # a full-column repaint on every reading tick, including
                    # the background and any neighboring blurred rows.
                    for annotation in (
                        self._song_parenthetical_layouts[index],
                        self._song_parenthetical_translation_layouts[index],
                    ):
                        if annotation is None:
                            continue
                        annotation_layout = annotation[0]
                        for line_index in range(annotation_layout.lineCount()):
                            line = annotation_layout.lineAt(line_index)
                            ink_left = min(ink_left, row.left() + line.x())
                            ink_right = max(
                                ink_right,
                                row.left() + line.x() + line.naturalTextWidth(),
                            )
                animation_part = QRectF(
                    ink_left,
                    painted_top,
                    max(1.0, ink_right - ink_left),
                    max(1.0, painted_bottom - painted_top),
                ).adjusted(-glow_bleed, -glow_bleed, glow_bleed, glow_bleed)
                active_animation_rect = (
                    active_animation_rect.united(animation_part)
                    if active_animation_rect.isValid() else animation_part
                )
            blur_level = blur_levels[index]
            row_bleed = glow_bleed if index == active else max(
                self._lyric_blur_bleed(blur_level), self._font_height(lyric_font) * 0.55,
            )
            if painted_bottom + row_bleed < rect.top() or painted_top - row_bleed > rect.bottom():
                continue
            if timed and outgoing_lyrics is None and not lyrics_hidden_by_layout and (cover_fraction < 0.02 or cover_lyrics_layout):
                hit_row = QRectF(row.left(), painted_top, row.width(), max(1.0, painted_bottom - painted_top))
                self._lyric_hit_rects[index] = lyric_transform.mapRect(hit_row.intersected(lyric_clip))
            # QWidget clips pixels, but the Python lyric shaping and halo work
            # would still run for every visible row on a controls-only frame.
            # Keep hit geometry current while skipping ink outside the exposed
            # region. The generous bleed includes moving glyphs and blur.
            if paint_region is not None:
                ink_bounds = QRectF(
                    row.left() - glow_bleed, painted_top - glow_bleed,
                    row.width() + 2.0 * glow_bleed,
                    max(1.0, painted_bottom - painted_top) + 2.0 * glow_bleed,
                )
                if cover_lyrics_layout:
                    ink_bounds = lyric_transform.mapRect(ink_bounds)
                if not paint_region.intersects(ink_bounds.toAlignedRect()):
                    continue
            if self._hovered_lyric_index == index and timed and self._browse_index is not None:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 255, 24))
                painter.drawRoundedRect(row.adjusted(-4, 2, 4, -2), 12, 12)
            text = paint_lines[index]
            painter.save()
            if (not manual_view and blur_level > 0.0
                    and pair_first_by_row.get(index, index) != focus_index):
                painter.setOpacity(painter.opacity() * (
                    1.0 - (1.0 - surrounding_lyric_opacity) * min(1.0, blur_level)
                ))
            if handoff_active and index == active and obo_reading_handoff > 0.0:
                painter.translate(0.0, -obo_active_primary_lift)
            if blur_level and self.devicePixelRatioF() > self.BLURRED_LYRIC_DPR_CAP:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            reading_focused = (
                (index == active or paired_continuation)
                and pair_first_by_row.get(index, index) == focus_index
                and blur_focus_moving
            )
            obo_waiting = (
                song_has_real_units and visual_active != active
                and index == visual_active and index == focus_index
                and index not in pair_first_by_row
                and outgoing_lyrics is None
                and index < len(self._display_timed_kinds)
                and self._display_timed_kinds[index] != "synthetic"
                and not self._has_reversed_lyric_unit_order(text)
                and blur_focus_moving
            )
            row_progress = active_reading_progress
            row_kind = active_kind
            row_units = active_units
            row_plan = active_plan
            row_end = active_reading_end
            row_source_offsets = active_source_offsets
            row_real_groups = real_groups
            row_priority_mode = active_priority_mode
            if paired_continuation:
                row_progress = max(0.0, min(
                    1.0,
                    (current_position - float(timed[index][0]))
                    / max(0.001, paired_end - float(timed[index][0])),
                ))
                row_end = paired_end
                row_source_units = self._display_timed_units[index] if index < len(self._display_timed_units) else ()
                row_kind = self._display_timed_kinds[index] if index < len(self._display_timed_kinds) else "line"
                row_plan = self._display_row_timings[index]
                row_units = row_plan.display_units
                planned_progress = row_plan.reading_progress(current_position)
                if planned_progress is not None:
                    row_progress = planned_progress
                row_source_offsets = self._visible_primary_source_offsets(
                    timed[index][1], visual_layers[index],
                )
                row_real_groups = self._project_highlight_groups(
                    explicit_slow_unit_groups(
                        self._primary_timed_lyric_text(timed[index][1]), row_source_units,
                    ) if row_kind == "unit" else (),
                    row_source_offsets,
                )
                row_priority_mode = "scatter_glow" if row_real_groups else "none"
            if reading_focused and index in pair_first_by_row and row_plan.has_reading_timing:
                row_progress = row_plan.reading_progress(current_position)
            reversed_unit_order = reading_focused and self._has_reversed_lyric_unit_order(text)
            whole_row_reading = (
                reversed_unit_order or row_kind == "synthetic"
                or (low_perception and not row_plan.has_reading_timing)
            )
            # Low perception retains its restrained emphasis/typography, but
            # no longer forces line-timed songs into a half-second block wipe.
            low_whole_block = False
            priority_mode = row_priority_mode if reading_focused and not reversed_unit_order else "none"
            intro_countdown = bool(timed and text == INTRO_COUNTDOWN_TEXT and index + 1 < len(timed))
            unit_mode = "character" if priority_mode != "none" else "reading"
            priority_assets_ready = (
                priority_mode == "none"
                or self._lyric_emphasis_assets_ready(
                    text, lyric_font, row_width, layout,
                    priority_mode, emphasis_unread_color,
                )
            )
            active_runs = (
                self._wrapped_lyric_runs(text, lyric_font, layout, unit_mode)
                if (
                    reading_focused and timed and layout.lineCount() > 1
                    and (
                        priority_mode == "none"
                        or (
                            priority_mode in {"scatter_glow", "character_lift"}
                            and priority_assets_ready
                        )
                    )
                )
                else []
            )
            real_progresses = (
                self._real_visual_line_progresses(
                    text, layout, row_units, current_position,
                    line_end=row_end,
                    source_text=timed[index][1],
                    visible_source_offsets=row_source_offsets,
                ) if reading_focused and row_plan.has_reading_timing else None
            )
            # The group-roll spring owns visual-line position.  The reading
            # effect below only adds its small per-unit baseline lift; the two
            # motions stay independent and text is never scaled.
            combined_offsets = line_offsets
            low_block_painted = False
            if intro_countdown:
                dot_row = QRectF(row)
                dot_row.translate(0.0, combined_offsets[0] if combined_offsets else 0.0)
                self._paint_intro_countdown(
                    painter, dot_row, lyric_font,
                    max(0.0, min(1.0, (current_position - float(timed[index][0]))
                        / max(0.001, float(timed[index + 1][0]) - float(timed[index][0])))),
                    blur_level,
                    unread_color=emphasis_unread_color,
                )
            elif priority_mode in {"scatter_glow", "character_lift"}:
                if priority_assets_ready:
                    runs = active_runs or self._wrapped_lyric_runs(text, lyric_font, layout, "character")
                    progresses = (
                        real_progresses if real_progresses is not None else
                        self._visual_line_progresses(
                            row_progress, tuple(run[4] for run in runs),
                        )
                    )
                    if row_real_groups:
                        ordinary_wave_progress, ordinary_wave_indices, ordinary_wave_count = self._wrapped_lift_wave(
                            text, lyric_font, layout, runs, row_progress,
                            "character", real_progresses,
                        )
                    else:
                        highlight_wave_progress, highlight_wave_indices, highlight_wave_count = self._wrapped_lift_wave(
                            text, lyric_font, layout, runs, row_progress,
                            "character", None, ordinal_mode="character",
                        )
                    for visual_index, ((segment, x, y, height, _count), local_progress) in enumerate(zip(runs, progresses)):
                        offset = combined_offsets[visual_index] if visual_index < len(combined_offsets) else 0.0
                        visual_start = len(self._utf16_slice(text, 0, layout.lineAt(visual_index).textStart()))
                        states = (
                            self._real_sparse_glyph_states(
                                segment, lyric_font, visual_start, row_real_groups,
                                local_progress, current_position,
                                wave_progress=ordinary_wave_progress,
                                wave_unit_indices=ordinary_wave_indices[visual_index],
                                wave_unit_count=ordinary_wave_count,
                            ) if row_real_groups else None
                        )
                        glyph_args = {"glyph_states": states} if states is not None else {}
                        self._paint_sparse_glyph_run(
                            painter,
                            QPointF(row.left() + x, row.top() + y + offset),
                            height, segment, lyric_font, local_progress,
                            emphasis_unread_color,
                            **({
                                "wave_progress": highlight_wave_progress,
                                "wave_unit_indices": highlight_wave_indices[visual_index],
                                "wave_unit_count": highlight_wave_count,
                            } if not row_real_groups else {}),
                            **glyph_args,
                        )
                else:
                    # Preserve the real reading front on a cold highlighter
                    # frame while its per-glyph halo layers prewarm. A white
                    # full-row fallback would flash words before their time.
                    self._paint_cold_emphasis_reading(
                        painter, row, text, lyric_font, layout,
                        row_progress,
                        emphasis_unread_color, combined_offsets,
                        real_progresses=real_progresses,
                        highlight_groups=row_real_groups,
                        highlight_position=current_position,
                    )
            elif obo_waiting:
                waiting_kind = self._display_timed_kinds[index]
                waiting_units = self._display_timed_units[index]
                waiting_primary = self._primary_timed_lyric_text(timed[index][1])
                waiting_end = float(timed[index + 1][0]) if index + 1 < len(timed) else None
                if index < len(source_read_ends) and source_read_ends[index] is not None:
                    waiting_end = source_read_ends[index]
                waiting_unverified = waiting_kind == "unit" and not genuinely_timed_unit_row(waiting_units)
                waiting_emphasis = lyric_emphasis_plan(
                    text, float(timed[index][0]), waiting_end,
                    min_seconds_per_unit=getattr(
                        self.owner, "music_sparse_highlight_seconds_per_unit", SPARSE_MIN_SECONDS_PER_UNIT,
                    ),
                ) if waiting_kind == "line" and not suppress_estimated_highlight else None
                waiting_character_mode = (
                    bool(explicit_slow_unit_groups(waiting_primary, waiting_units))
                    if waiting_kind == "unit" and not waiting_unverified else
                    bool(waiting_emphasis is not None and waiting_emphasis.mode != "none")
                )
                waiting_scale = max(0.5, float(max(1, lyric_font.pixelSize())) / self.LYRIC_PIXEL_SIZE)
                painter.save()
                painter.translate(0.0, -(1.0 - obo_unread_pose) * self._reading_lift_height() * waiting_scale)
                self._paint_wrapped_lifted_lyric(
                    painter, row, text, lyric_font, layout, 0.0,
                    line_offsets=combined_offsets,
                    unit_mode="character" if waiting_character_mode else "reading",
                )
                painter.restore()
            elif reading_focused and whole_row_reading:
                # No safe clock (or reversed source order): retain the normal
                # whole-row fallback without inventing a timed word wave.
                block_progress = self._low_perception_block_progress(
                    current_position, timed[index][0],
                ) if low_whole_block and timed else 1.0
                if low_whole_block and block_progress < 1.0:
                    block_layers = [(layout, row.topLeft(), combined_offsets)]
                    block_poses = [(1.0, 0.0)]
                    block_secondary = self._song_secondary_layouts[index]
                    if block_secondary is not None:
                        secondary_layout, secondary_height, secondary_top = block_secondary
                        secondary_top -= self._song_parenthetical_extra_heights[index] * (
                            1.0 - parenthetical_open_poses[index]
                        )
                        block_layers.append((
                            secondary_layout,
                            QPointF(row.left(), row.top() + secondary_top),
                            (combined_offsets[-1] if combined_offsets else 0.0,) * secondary_layout.lineCount(),
                        ))
                        block_poses.append((
                            translation_poses[index][1],
                            -(1.0 - translation_poses[index][2]) * secondary_height * 0.55,
                        ))
                    self._draw_low_perception_block_wipe(
                        painter, tuple(block_layers), block_progress,
                        emphasis_unread_color,
                        layer_poses=tuple(block_poses),
                    )
                    low_block_painted = True
                else:
                    self._draw_sprung_cached_wrapped_layout(
                        painter, layout, row.topLeft(), text, lyric_font,
                        row_width, layout_height, QColor(255, 255, 255), combined_offsets,
                    )
            elif reading_focused and timed:
                self._paint_wrapped_lifted_lyric(
                    painter,
                    row,
                    text,
                    lyric_font,
                    layout,
                    row_progress,
                    runs=active_runs or None,
                    line_offsets=combined_offsets,
                    real_progresses=real_progresses,
                )
            else:
                if not timed or index < active or (index == active and not reading_focused and blur_focus_moving):
                    base_color = QColor(255, 255, 255, 255)
                    self._draw_sprung_cached_wrapped_layout(
                        painter, layout, row.topLeft(), text, lyric_font,
                        row_width, layout_height, base_color, combined_offsets,
                        blur_level=blur_level,
                    )
                else:
                    self._draw_future_wrapped_layout(
                        painter, layout, row.topLeft(), text, lyric_font,
                        row_width, layout_height, combined_offsets,
                        blur_level=blur_level,
                    )
            secondary = self._song_secondary_layouts[index]
            if secondary is not None and translation_poses[index][1] > 0.001:
                secondary_layout, secondary_height, secondary_top = secondary
                secondary_text = translation_lines[index]
                # Keep the translated aside and the sentence translation in
                # one ordered stack while both pop out. Collapsing their
                # internal distance with the outer reservation overlaps ink.
                secondary_top -= self._song_parenthetical_extra_heights[index] * (
                    1.0 - parenthetical_open_poses[index]
                )
                companion_slide = -(1.0 - translation_poses[index][2]) * secondary_height * 0.55
                secondary_origin = QPointF(row.left(), row.top() + secondary_top + companion_slide)
                secondary_offsets = (combined_offsets[-1] if combined_offsets else 0.0,) * secondary_layout.lineCount()
                painter.save()
                painter.setOpacity(painter.opacity() * translation_poses[index][1])
                if obo_waiting:
                    waiting_scale = max(0.5, float(max(1, secondary_font.pixelSize())) / self.LYRIC_PIXEL_SIZE)
                    painter.save()
                    painter.translate(0.0, -(1.0 - obo_unread_pose) * self._reading_lift_height() * waiting_scale)
                    self._paint_wrapped_lifted_lyric(
                        painter,
                        QRectF(secondary_origin.x(), secondary_origin.y(), row_width, secondary_height),
                        secondary_text, secondary_font, secondary_layout,
                        0.0, line_offsets=secondary_offsets,
                    )
                    painter.restore()
                elif low_block_painted:
                    # The original branch already drew this translation as
                    # part of its one shared diagonal wipe.
                    pass
                elif reading_focused and timed and low_perception and whole_row_reading:
                    self._draw_sprung_cached_wrapped_layout(
                        painter, secondary_layout, secondary_origin,
                        secondary_text, secondary_font, row_width,
                        secondary_height, QColor(255, 255, 255), secondary_offsets,
                    )
                elif reading_focused and timed:
                    if obo_active_translation_lift:
                        painter.translate(0.0, obo_active_primary_lift - obo_active_translation_lift)
                    row_real_front = (
                        display_row_reading_front(
                            row_plan, current_position, range(len(row_plan.text) + 1),
                        ) if paired_continuation else active_real_front
                    )
                    if row_real_front is not None and row_source_offsets is not None:
                        visible_front = self._project_source_front(
                            row_real_front, row_source_offsets,
                        )
                        translated_progress = min(
                            1.0, visible_front / max(1, len(text)),
                        )
                    else:
                        translated_progress = row_progress
                    self._paint_wrapped_lifted_lyric(
                        painter,
                        QRectF(secondary_origin.x(), secondary_origin.y(), row_width, secondary_height),
                        secondary_text, secondary_font, secondary_layout,
                        translated_progress, line_offsets=secondary_offsets,
                    )
                    if obo_active_translation_lift:
                        painter.translate(0.0, obo_active_translation_lift - obo_active_primary_lift)
                elif not timed or index < active or (index == active and not reading_focused and blur_focus_moving):
                    self._draw_sprung_cached_wrapped_layout(
                        painter, secondary_layout, secondary_origin,
                        secondary_text, secondary_font, row_width,
                        secondary_height, QColor(255, 255, 255), secondary_offsets,
                        blur_level=blur_level,
                    )
                else:
                    self._draw_future_wrapped_layout(
                        painter, secondary_layout, secondary_origin,
                        secondary_text, secondary_font, row_width,
                        secondary_height, secondary_offsets,
                        blur_level=blur_level,
                    )
                painter.restore()
            parenthetical = self._song_parenthetical_layouts[index]
            parenthetical_translation = self._song_parenthetical_translation_layouts[index]
            if (parenthetical is not None or parenthetical_translation is not None) and parenthetical_factors[index] > 0.0:
                annotation_height = annotation_extras[index]
                annotation_top = (
                    parenthetical[2] if parenthetical is not None
                    else parenthetical_translation[2]
                )
                # OBO can focus the next visual sentence before its source
                # clock starts. Its annotation enters with that focus, while
                # the reading front remains fully unread until its own time.
                annotation_waiting = bool(index == visual_active and index > active)
                showing = bool(
                    timed
                    and pair_first_by_row.get(index, index) == focus_index
                )
                completed = bool(
                    timed and index <= active
                    and pair_first_by_row.get(index, index) < focus_index
                )
                if showing or completed:
                    if showing:
                        # Start when the annotation becomes visible, rather
                        # than at the audio timestamp: a late provider result
                        # or delayed focus must still show the entrance.
                        back, opacity = self._parenthetical_reveal_pose(
                            index, lyric_now, focused=True,
                        )
                        slide = -(1.0 - back) * max(
                            annotation_height * 0.75,
                            annotation_top - layout_height,
                        )
                    else:
                        opacity, slide = 1.0, 0.0
                    if opacity > 0.001:
                        painter.save()
                        painter.setOpacity(painter.opacity() * opacity)
                        bracket_progress = 0.0 if (annotation_waiting or not reading_focused) else row_progress
                        if showing and reading_focused and not annotation_waiting and row_plan.has_reading_timing and row_source_offsets is not None:
                            source_front = display_row_reading_front(
                                row_plan, current_position, range(len(row_plan.text) + 1),
                            )
                            if source_front is not None:
                                bracket_progress = self._project_source_front(
                                    source_front, row_source_offsets,
                                ) / max(1, len(text))
                        # The annotation keeps its independent entrance, but
                        # its reading front runs only during the last fifth
                        # of the visible primary sentence. Both annotation
                        # layers share this exact fraction.
                        bracket_progress = max(
                            0.0, min(1.0, (bracket_progress - 0.8) / 0.2),
                        )
                        if showing:
                            self._parenthetical_read_progress[index] = bracket_progress
                        else:
                            bracket_progress = 1.0
                        for layer, layer_text, layer_font in (
                            (parenthetical, parenthetical_lines[index], parenthetical_font),
                            (parenthetical_translation, parenthetical_translation_lines[index], secondary_font),
                        ):
                            if layer is None:
                                continue
                            annotation_layout, layer_height, layer_top = layer
                            is_translation = layer_font is secondary_font
                            if is_translation and translation_poses[index][1] <= 0.001:
                                continue
                            painter.save()
                            if is_translation:
                                painter.setOpacity(painter.opacity() * translation_poses[index][1])
                                painter.translate(0.0, -(1.0 - translation_poses[index][2]) * layer_height * 0.55)
                            layer_lift = (
                                obo_active_parenthetical_lift if layer_font is parenthetical_font
                                else obo_active_translation_lift
                            )
                            if layer_lift:
                                painter.translate(0.0, obo_active_primary_lift - layer_lift)
                            layer_origin = QPointF(row.left(), row.top() + layer_top + slide)
                            layer_offsets = (combined_offsets[-1] if combined_offsets else 0.0,) * annotation_layout.lineCount()
                            if completed:
                                self._draw_sprung_cached_wrapped_layout(
                                    painter, annotation_layout, layer_origin, layer_text,
                                    layer_font, row_width, layer_height,
                                    QColor(255, 255, 255) if is_translation else QColor(220, 220, 220),
                                    layer_offsets, blur_level=blur_level,
                                )
                            else:
                                self._paint_wrapped_lifted_lyric(
                                    painter,
                                    QRectF(layer_origin.x(), layer_origin.y(), row_width, layer_height),
                                    layer_text, layer_font, annotation_layout, bracket_progress,
                                    line_offsets=layer_offsets,
                                    **({"read_color": QColor(220, 220, 220)} if not is_translation else {}),
                                )
                            if layer_lift:
                                painter.translate(0.0, layer_lift - obo_active_primary_lift)
                            painter.restore()
                        painter.restore()
            painter.restore()

        painter.restore()
        self._active_lyric_animation_rect = lyric_transform.mapRect(active_animation_rect).intersected(rect).toAlignedRect()

        if loading_lyrics and self._lyric_loading_visible(lyric_now) and not lyrics_hidden_by_layout:
            painter.save()
            if cover_lyrics_layout:
                painter.setClipRect(painted_cover, Qt.ClipOperation.IntersectClip)
                painter.setWorldTransform(lyric_transform, True)
            else:
                painter.setClipRect(lyric_ink_clip, Qt.ClipOperation.IntersectClip)
                if unobscured_path is not None:
                    painter.setClipPath(unobscured_path, Qt.ClipOperation.IntersectClip)
            painter.translate(lyric_slide, 0.0)
            self._paint_lyric_loading(
                painter, lyric_clip, lyric_now, 1. if cover_lyrics_layout else max(0.0, 1.0 - cover_fraction),
            )
            painter.restore()

        if self._effective_content_layout() == "playlist":
            self._paint_playback_queue(painter, side_column.adjusted(0.,8.*surface_scale,0.,-4.*surface_scale))
        else:
            self._queue_view_rect = QRectF(); self._queue_hit_rects = {}

        window_control_exposed = paint_region is None or paint_region.intersects(
            QRectF(rect.right() - 420.0 * surface_scale, 0.0, 420.0 * surface_scale, 100.0 * surface_scale).toAlignedRect()
        )
        content_snapshot = self._capturing_content_snapshot
        if not content_snapshot and (paint_region is None or paint_region.intersects(self._library_damage_rect(rect))):
            self._paint_library_back(painter, rect)
        self._paint_window_controls(painter, rect, paint_ink=window_control_exposed and not content_snapshot)
        controls_height = self.TRANSPORT_PANEL_HEIGHT * surface_scale
        normal_controls_bottom = rect.bottom() - self._personal_dj_footer_height(rect) - 18.0 * surface_scale
        controls_top = min(
            normal_controls_bottom - controls_height,
            artist_rect.bottom() + 12.0 * surface_scale,
        )
        if cover_fraction > 0.0:
            # Keep transport below the central artwork while information
            # remains in its established lower-left home.
            controls_top = min(
                normal_controls_bottom - controls_height,
                max(controls_top, cover.bottom() + 6.0 * surface_scale * cover_fraction),
            )
        panel_width = metadata_width - 50.0
        if cover.bottom() > controls_top:
            panel_width = min(
                panel_width,
                max(145.0 * surface_scale, cover.left() - (content.left() + 7.0) - 8.0 * surface_scale),
            )
        normal_controls_rect = QRectF(content.left() + 7.0, controls_top, panel_width, controls_height)
        transport_rect = self._interpolate_rect(normal_controls_rect, centered_controls_rect, cover_fraction)
        # DJ avoidance must see this frame's transport geometry, including
        # the first compact paint after a resize or content-mode switch.
        self._media_control_panel_rect = QRectF(transport_rect)
        if not content_snapshot and (paint_region is None or paint_region.intersects(self._personal_dj_damage_rect(rect))):
            self._paint_personal_dj_control(painter, rect)
        collection_damage = self._song_collection_geometry(rect).adjusted(-4., -4., 4., 4.)
        if not content_snapshot and (paint_region is None or paint_region.intersects(collection_damage.toAlignedRect())):
            self._paint_song_collection_control(painter, rect)
        normal_control_region = QRectF(
            0.0, rect.bottom() - self.CONTROL_BOTTOM_REGION_HEIGHT * surface_scale,
            505.0 * surface_scale, self.CONTROL_BOTTOM_REGION_HEIGHT * surface_scale,
        )
        media_control_exposed = paint_region is None or (
            paint_region.intersects(normal_control_region.toAlignedRect())
            or paint_region.intersects(centered_controls_rect.adjusted(-5.0, -5.0, 5.0, 5.0).toAlignedRect())
            or paint_region.intersects(transport_rect.adjusted(-5.0, -5.0, 5.0, 5.0).toAlignedRect())
        )
        self._paint_media_controls(
            painter,
            transport_rect,
            primary,
            secondary,
            paint_ink=media_control_exposed and not content_snapshot,
        )
        if self._control_prewarm_requested and (window_control_exposed or media_control_exposed):
            self._control_prewarm_requested = False
            self._control_prewarm_remaining = max(0, self._control_prewarm_remaining - 1)
        if not content_snapshot and not navigation_capture:
            self._paint_content_layout_transition(painter)
        self._apply_surface_edge_alpha(painter)
        painter.end()
        self._record_paint_cost(paint_started, _event)

    @staticmethod
    def _background_role_color(source: QColor) -> QColor:
        return background_role_color(source)

    def _paint_background(
        self, painter: QPainter, rect: QRectF, primary: QColor, secondary: QColor,
        now: float, *, ambient_colors: tuple[QColor, ...] = (),
        ambient_weights: tuple[float, ...] = (), advance_frame: bool = True,
    ):
        # The album-flow renderer owns the small blurred raster and smooth
        # palette retargeting. Lyric-only paints reuse this complete image,
        # including its contrast samples, until the next ambient frame.
        colors = ambient_colors or (primary, secondary)
        width, height = max(1, int(rect.width())), max(1, int(rect.height()))
        intensity_value = getattr(self.owner, "music_flow_intensity", 0.74)
        intensity = max(0.0, min(1.0, float(0.74 if intensity_value is None else intensity_value)))
        identity = self._track_visual[0] if self._track_visual else None
        key = (width, height, tuple(color.rgba() for color in colors), ambient_weights, intensity, identity)
        # Advancing during a lyric-only dirty paint would leave a newer
        # background patch surrounded by old pixels. Hold the displayed
        # frame and its contrast samples until a whole-surface paint.
        self._background_rebuilt_this_paint = False
        if self._background_cache.isNull() or (advance_frame and (
            key != self._background_cache_key
            or self._album_flow_background._frame.cacheKey() != self._background_cache.cacheKey()
            or now < self._background_cache_at
            or now - self._background_cache_at >= self.AMBIENT_FRAME_SECONDS
        )):
            previous_frame_key = self._background_cache.cacheKey()
            self._background_cache = self._album_flow_background.render(
                QSize(width, height), colors, now, intensity, weights=ambient_weights,
                material_owner=self if live_glass_paint(painter, self) else None,
                material_scene=self._visual_track_identity,
            )
            self._background_cache_key = key
            self._background_cache_at = now
            self._background_rebuilt_this_paint = self._background_cache.cacheKey() != previous_frame_key
        if not getattr(self, "_navigation_cover_capture", False):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            # Expanding to a full native-DPR image costs more on ambient
            # frames than direct smooth sampling. Keep the small source and
            # let Qt sample only the actual dirty region of this paint.
            painter.drawImage(rect, self._background_cache)
            painter.restore()

    def _window_control_base_rect(self, rect: QRectF) -> QRectF:
        scale = self._surface_scale(rect)
        width, height, top = 44.0 * scale, 44.0 * scale, 18.0 * scale
        return QRectF(rect.right() - 22.0 * scale - width, top, width, height)

    def _reset_library_expansion(self, now: float):
        self._library_expansion_from = self._library_expansion_target = 0.0
        self._library_expansion_started_at = float(now)
        self._library_expansion_paused_at = None
        self._library_last_tick_expansion = 0.0

    def _library_expansion_at(self, now: float) -> float:
        instant = self._library_expansion_paused_at
        if instant is None:
            instant = float(now)
        phase = (instant - self._library_expansion_started_at) / self.LIBRARY_EXPANSION_SECONDS
        if phase >= 1.0 - 1e-9:
            return self._library_expansion_target
        return self._library_expansion_from + (self._library_expansion_target - self._library_expansion_from) * self._control_spring_progress(phase)

    def _sync_library_motion_reveal(self, reveal: float, now: float):
        # A moving capsule must not make a new blur raster every fade frame.
        # Hold its exact pose while the shared layer moves; fully hidden
        # controls restart as a circle on the next reveal.
        if reveal <= 0.001:
            self._reset_library_expansion(now)
        elif reveal < 0.999 or self._control_reveal_target < 1.0:
            if self._library_expansion_paused_at is None:
                self._library_expansion_paused_at = float(now)
        elif self._library_expansion_paused_at is not None:
            self._library_expansion_started_at += float(now) - self._library_expansion_paused_at
            self._library_expansion_paused_at = None

    def _set_library_expanded(self, expanded: bool, now: float):
        target = 1.0 if expanded else 0.0
        if target == self._library_expansion_target:
            return
        self._library_expansion_from = self._library_expansion_at(now)
        self._library_expansion_target = target
        self._library_expansion_started_at = float(now) if self._library_expansion_paused_at is None else self._library_expansion_paused_at
        if self.isVisible() and self._library_back_available():
            self.update(self._library_damage_rect(QRectF(self.rect())))

    def _library_button_rect(self, rect: QRectF, expansion: float | None = None) -> QRectF:
        scale = self._surface_scale(rect)
        expansion = self._library_expansion_at(time.monotonic()) if expansion is None else expansion
        diameter = min(max(44.0, 44.0 * scale), max(1., rect.width() * .14))
        expanded = min(max(104.0, 118.0 * scale), max(diameter, rect.width()-36.*scale))
        width = diameter + (expanded - diameter) * expansion
        return QRectF(18.0 * scale, max(4.0, 6.0 * scale),
                      width, diameter)

    def _library_button_face(self, rect: QRectF, expansion: float | None = None) -> QRectF:
        inset = max(3.0, 4.0 * self._surface_scale(rect))
        return self._library_button_rect(rect, expansion).adjusted(inset, inset, -inset, -inset)

    def _library_control_shift(self, rect: QRectF, reveal: float) -> QPointF:
        button = self._library_button_rect(rect, 0.0)
        return QPointF(-(button.right()) * (1.0 - reveal),
                       -(button.bottom()) * (1.0 - reveal))

    def _library_damage_rect(self, rect: QRectF) -> QRect:
        padding = math.ceil(20.0 * self._surface_scale(rect))
        return self._library_button_rect(rect, 1.0).adjusted(-padding, -padding, padding, padding).intersected(rect).toAlignedRect()

    def _window_control_contains(self, key: str, rect: QRectF, point: QPointF) -> bool:
        if key == "personal_dj":
            path = self._personal_dj_hit_path(QRectF(self.rect()), time.monotonic(), shifted=False)
            bounds = path.boundingRect()
            if bounds.width() <= 0.0 or bounds.height() <= 0.0:
                return False
            point = QPointF(bounds.left() + (point.x() - rect.left()) * bounds.width() / max(.001, rect.width()),
                            bounds.top() + (point.y() - rect.top()) * bounds.height() / max(.001, rect.height()))
            return path.contains(point)
        if key not in {"library", "personal_dj"}:
            return rect.contains(point)
        shape = QPainterPath()
        radius = min(rect.width(), rect.height()) / 2.0
        shape.addRoundedRect(rect, radius, radius)
        return shape.contains(point)

    def _surface_glass_backdrop(self) -> QImage:
        """Keep the same scene through control prewarm and its idle exit."""
        now = time.monotonic()
        reveal = self._control_reveal_at(now)
        if .001 < reveal < .999 and not self._glass_idle_backdrop.isNull():
            return self._glass_idle_backdrop
        idle = self.FULLSCREEN_CONTROL_IDLE_SECONDS if self._fullscreen else self.WINDOWED_CONTROL_IDLE_SECONDS
        if now - self._control_last_motion_at < max(.3, idle - 2.):
            return self._background_cache
        key = (self._control_last_motion_at, self.width(), self.height(),
               self._background_cache_key, self._surface_glass_light())
        if key != self._glass_idle_backdrop_key:
            self._glass_idle_backdrop = QImage(self._background_cache)
            self._glass_idle_backdrop_key = key
        return self._glass_idle_backdrop

    def _surface_glass_source_rect(self, target: QRectF, image: QImage) -> QRectF:
        sx, sy = image.width() / max(1, self.width()), image.height() / max(1, self.height())
        return QRectF(target.x() * sx, target.y() * sy, target.width() * sx, target.height() * sy)

    def _surface_glass_light(self) -> bool:
        if self._frame_glass_light is not None:
            return self._frame_glass_light
        return not app_dark_mode(getattr(self.owner, "settings_getter", None))

    def _surface_glass_level(self) -> int:
        return widget_glass_level(self)

    def refresh_glass_material(self):
        """Invalidate control exit snapshots and any already open child panels."""
        self._control_layer_cache.clear()
        self._control_layer_cache_bytes = 0
        self._control_prewarm_remaining = (2 + int(self._control_prewarm_library_available)) * (len(self.CONTROL_BLUR_FRACTIONS) - 1)
        self._control_prewarm_requested = False
        self._control_layer_next_prewarm_at = 0.
        panel = getattr(self, "_audio_output_panel", None)
        if panel is not None:
            panel._sync_surface_style()
            panel._capture_material(force=True)
        self.update()
        for child in self.findChildren(QWidget):
            child.update()

    def _interpolated_control_color(self, role: str, target: QColor, now: float | None = None) -> QColor:
        """Retarget a color from its visible value over the track's 0.36 s handoff."""
        instant = (self._frame_control_color_at if self._frame_control_color_at is not None
                   else time.monotonic()) if now is None else float(now)
        target = QColor(target)
        state = self._control_color_transitions.get(role)
        if state is None:
            self._control_color_transitions[role] = (target, target, instant)
            return QColor(target)
        source, previous_target, started = state
        progress = max(0.0, min(1.0, (instant - started) / .36))
        eased = progress * progress * (3.0 - 2.0 * progress)
        current = QColor(*(
            round(a * (1.0 - eased) + b * eased)
            for a, b in zip(source.getRgb(), previous_target.getRgb())
        ))
        if target != previous_target:
            self._control_color_transitions[role] = (QColor(current), target, instant)
        return current

    def _scene_control_ink(self) -> QColor:
        """One black/white foreground from the whole displayed scene's area.

        Every equal-area sample votes for whichever of pure black and white
        has higher contrast. A minority patch beneath a particular button
        cannot choose a different foreground. Cache the bounded grid by the
        actual scene raster and the shared theme veil/strength. Active states
        never choose their own ink, so all controls still agree.
        """
        image = self._background_cache
        if image.isNull():
            return self._interpolated_control_color("scene", QColor("black" if self._surface_glass_light() else "white"))
        veil = glass_material_tint(light=self._surface_glass_light(), material_level=self._surface_glass_level())
        key = (image.cacheKey(), veil.rgba())
        if key != self._scene_control_ink_key:
            columns, rows = min(32, image.width()), min(18, image.height())
            black_area = white_area = 0.0
            # Equal black/white contrast occurs at this linear luminance.
            crossover = math.sqrt(.05 * 1.05) - .05
            for row in range(rows):
                y = min(image.height() - 1, int((row + .5) * image.height() / rows))
                for column in range(columns):
                    x = min(image.width() - 1, int((column + .5) * image.width() / columns))
                    color = image.pixelColor(x, y)
                    color = QColor(*(round(a * (1. - veil.alphaF()) + b * veil.alphaF())
                                     for a, b in zip(color.getRgb()[:3], veil.getRgb()[:3])), color.alpha())
                    if self._relative_luminance(color) > crossover:
                        black_area += color.alphaF()
                    else:
                        white_area += color.alphaF()
            # White is the deterministic tie-breaker, so crossing an exact
            # half split cannot depend on the caller or on a control's bounds.
            self._scene_control_ink_cache = QColor("black" if black_area > white_area else "white")
            self._scene_control_ink_key = key
        return self._interpolated_control_color("scene", self._scene_control_ink_cache)

    def _surface_glass_ink(self, target: QRectF, *, active=False, live=False) -> QColor:
        # Preserve the existing shared caller contract, including the voice
        # slider, while all buttons now use the same whole-scene decision.
        return self._scene_control_ink()

    def _paint_surface_glass(self, painter: QPainter, target: QRectF, *, hovered=False,
                             pressed=False, active=False, live=False, role=None):
        image = self._background_cache if live else self._surface_glass_backdrop()
        paint_liquid_glass(painter, target, hovered=hovered, pressed=pressed, active=active,
                           light=self._surface_glass_light(), refractive=True,
                           material_level=self._surface_glass_level(),
                           backdrop=image, backdrop_rect=self._surface_glass_source_rect(target, image),
                           cache_token=id(self), material_owner=self, material_role=role,
                           material_scene=self._visual_track_identity,
                           material_dirty=painter.worldTransform().mapRect(target))

    def _paint_library_back(self, painter: QPainter, rect: QRectF):
        if not self._library_back_available():
            return
        now = time.monotonic()
        reveal = self._control_reveal_at(now)
        if reveal <= 0.001:
            return
        expansion = self._library_expansion_at(now)
        scale = max(0.9, self._surface_scale(rect))
        hovered = self._hovered_window_button == "library"
        pressed = self._pressed_window_button == "library"
        def draw(layer, *, fraction=expansion, is_hovered=hovered, is_pressed=pressed):
            face = self._library_button_face(rect, fraction)
            original_height = face.height()
            face = self._hover_control_rect("library", face, {"library": face}, now)
            growth = face.height() / max(.001, original_height)
            layer.save()
            self._paint_surface_glass(layer, face, hovered=is_hovered, pressed=is_pressed, role="library")
            ink = self._surface_glass_ink(face)
            icon_side = 20.0 * scale * growth
            center_x = face.left() + face.height() / 2
            paint_music_source_icon(layer, QRectF(center_x - icon_side / 2, face.center().y() - icon_side / 2, icon_side, icon_side), "login" if self._library_login_required() else "library_back", tint=ink)
            if fraction > 0.001:
                label = QRectF(face.left() + face.height(), face.top(), max(0.0, face.width() - face.height() - 6.0 * scale), face.height())
                layer.setClipRect(label, Qt.ClipOperation.IntersectClip)
                layer.setOpacity(fraction)
                layer.setPen(ink)
                layer.setFont(self._surface_font(max(12.0, 14.0 * self._surface_scale(rect)) * growth, QFont.Weight.DemiBold))
                layer.drawText(label, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "登录" if self._library_login_required() else "音乐库")
            layer.restore()
        idle_seconds = self.FULLSCREEN_CONTROL_IDLE_SECONDS if self._fullscreen else self.WINDOWED_CONTROL_IDLE_SECONDS
        idle_expansion = self._library_expansion_at(self._control_last_motion_at + idle_seconds)
        hover_key = self._control_hover_pose_key({"library": self._library_button_face(rect, expansion)}, now)
        key = lambda fraction, over, down: ("library", self._library_login_required(), round(self._surface_scale(rect), 3), round(fraction, 5), over, down, hover_key)
        self._paint_control_layer(painter, self._library_button_rect(rect, 1.0), reveal,
                                  self._library_control_shift(rect, reveal), key(expansion, hovered, pressed), draw,
                                  prewarm_default=(key(idle_expansion, False, False),
                                      lambda layer: draw(layer, fraction=idle_expansion, is_hovered=False, is_pressed=False)))

    @staticmethod
    def _preceding_control_rect(button: QRectF, scale: float) -> QRectF:
        return QRectF(button.left() - button.width() - 12.0 * scale,
                      button.top(), button.width(), button.height())

    def _translation_button_rect(self, button: QRectF, scale: float) -> QRectF:
        return self._preceding_control_rect(button, scale)

    def _playback_mode_button_rect(self, button: QRectF, scale: float) -> QRectF:
        return self._preceding_control_rect(self._translation_button_rect(button, scale), scale)

    def _sync_owned_playback_mode(self):
        provider = getattr(self.owner, "data_provider", None)
        getter = getattr(provider, "get_owned_playback_mode", None)
        setter = getattr(provider, "set_owned_playback_mode", None)
        modes_getter = getattr(provider, "get_owned_playback_modes", None)
        fm_getter = getattr(provider, "is_owned_personal_fm", None)
        try:
            mode = getter() if callable(getter) and callable(setter) else None
            modes = tuple(modes_getter()) if callable(modes_getter) else ("sequential", "shuffle", "repeat_one")
            personal_fm = bool(fm_getter()) if callable(fm_getter) else False
        except Exception:
            mode = None
            modes, personal_fm = (), False
        modes = tuple(value for value in modes if value in {"sequential", "shuffle", "repeat_one"})
        mode = mode if mode in modes else None
        if mode is None:
            modes, personal_fm = (), False
        if (mode, modes, personal_fm) != (self._owned_playback_mode, self._owned_playback_modes, self._owned_personal_fm):
            self._owned_playback_mode = mode
            self._owned_playback_modes = modes
            self._owned_personal_fm = personal_fm
            if self._hovered_window_button == "playback_mode":
                self.setToolTip(self._playback_mode_tooltip())
            self._sync_window_control_rects()
            self.update()

        self._sync_personal_dj_state()

    def _playback_mode_tooltip(self) -> str:
        if self._owned_personal_fm:
            return ("私人漫游 · ∞ 续播 · 点击切换为单曲循环" if self._owned_playback_mode == "sequential"
                    else "单曲循环 · 点击切换为∞ 漫游续播")
        labels = {"sequential": ("顺序播放 · 歌单循环", "随机播放"),
                  "shuffle": ("随机播放", "单曲循环"),
                  "repeat_one": ("单曲循环", "顺序播放")}
        mode = labels.get(self._owned_playback_mode)
        return f"{mode[0]} · 点击切换为{mode[1]}" if mode else ""

    def _cycle_owned_playback_mode(self) -> bool:
        # Recheck the provider at release so a source switch cannot send this
        # owned-player action to the system track displayed after the press.
        self._sync_owned_playback_mode()
        modes = self._owned_playback_modes
        following = modes[(modes.index(self._owned_playback_mode) + 1) % len(modes)] if self._owned_playback_mode in modes else None
        setter = getattr(getattr(self.owner, "data_provider", None), "set_owned_playback_mode", None)
        if following is None or not callable(setter):
            return False
        try:
            changed = bool(setter(following))
        except Exception:
            changed = False
        self._sync_owned_playback_mode()
        self.setToolTip(self._playback_mode_tooltip())
        return changed

    def _personal_fm_stops(self, region: QRectF) -> tuple[tuple[float, QColor], ...]:
        accent = self._interpolated_control_color("fm-accent", self._future_lyric_color(region))
        ink = self._scene_control_ink()
        # Keep the original moving infinity bands and their seamless sweep,
        # using the extracted accent plus the shared decided black/white tone.
        return ((0.0, accent), (.5, ink), (1.0, accent))

    def _personal_dj_footer_height(self, rect: QRectF) -> float:
        # Disabled DJ still owns its enable button while controls are shown.
        # Reserve one stable row for every available DJ, independently of
        # status/control animation, so the album group never jumps with it.
        if not self._content_layout_is_narrow():
            return 0.
        collection = self._song_collection_footer(rect)
        if self._owned_personal_dj_enabled is None:
            return collection
        scale = self._surface_scale(rect)
        return max(collection, max(36., 44. * scale) + 2. * max(3., 4. * scale)
            + max(10., 18. * scale) + max(6., 8. * scale))

    def _personal_dj_measured_widths(self, rect: QRectF):
        """Measure each real prompt once; geometry and footer share these widths."""
        scale = self._surface_scale(rect)
        has_collection = bool(self._song_collection_state().track_id)
        side = max(36. if has_collection else 32., 44. * scale)
        font = self._surface_font(max(10., 14. * scale), QFont.Weight.DemiBold)
        key = (self._dj_next_title, font.toString(), round(scale, 3))
        if getattr(self, "_dj_caption_width_key", None) != key:
            self._dj_caption_width_key = key
            self._dj_caption_width = min(250. * scale, max(side * 4.4,
                QFontMetricsF(font).horizontalAdvance(self._dj_next_title) + 28. * scale))
        status_font = self._surface_font(max(14., 16. * scale), QFont.Weight.DemiBold)
        status_key = status_font.toString()
        if getattr(self, "_dj_status_width_key", None) != status_key:
            self._dj_status_width_key = status_key
            texts = ("下一首歌播报已准备好", "下一首歌播报加载中", "下一首歌播报加载失败",
                     "等待下一首歌播报", "本首歌播报已准备好", "本首歌播报加载失败")
            self._dj_status_text_width = max(QFontMetricsF(status_font).horizontalAdvance(text) for text in texts)
        return side, max(side * 2., self._dj_caption_width), self._dj_status_text_width + side + 32. * scale

    def _footer_dj_lane_width(self, rect: QRectF, now: float) -> float:
        """Retarget the shared footer from its visible width, including instant statuses."""
        if self._owned_personal_dj_enabled is None:
            self._footer_lane_from = self._footer_lane_target = 0.
            self._footer_lane_started = now
            return 0.
        side, caption_width, status_width = self._personal_dj_measured_widths(rect)
        scale = self._surface_scale(rect)
        has_collection = bool(self._song_collection_state().track_id)
        margin = max(10., 18. * scale)
        reserve = (max(36., 44. * scale) * 2.5 + max(8., 10. * scale)) if has_collection else 0.
        maximum = max(side, rect.width() - 2. * margin - reserve)
        expanded = self._dj_activity_to > .001 or self._dj_voice_selector_active
        target = caption_width if expanded else side
        if not expanded and self._personal_dj_status_reveal_at(now) > .001:
            target = max(target, status_width)
        target = min(maximum, target)
        if not hasattr(self, "_footer_lane_target"):
            self._footer_lane_from = self._footer_lane_target = target
            self._footer_lane_started = now
        phase = (now - self._footer_lane_started) / self.DJ_SPEECH_CONTRACTION_SECONDS
        width = self._footer_lane_from + (self._footer_lane_target - self._footer_lane_from) * self._control_spring_progress(phase)
        if abs(target - self._footer_lane_target) > .001:
            self._footer_lane_from = min(maximum, max(side, width))
            self._footer_lane_target, self._footer_lane_started = target, now
            width = self._footer_lane_from
        return min(maximum, max(side, width))

    def _personal_dj_button_rect(self, rect: QRectF) -> QRectF:
        now = time.monotonic()
        icon, caption = self._personal_dj_geometry(rect, now)
        bounds = icon.united(caption)
        if self._personal_dj_status_reveal_at(now) > .001:
            bounds = bounds.united(self._personal_dj_outro_status_rect(rect, now))
        return bounds

    def _personal_dj_geometry(self, rect: QRectF, now: float, *, expansion: float | None = None):
        if self._owned_personal_dj_enabled is None and self._song_collection_state().track_id:
            return QRectF(), QRectF()
        scale = self._surface_scale(rect)
        side, width, _status_width = self._personal_dj_measured_widths(rect)
        margin = max(10.0, 18.0 * scale)
        expansion = max(self._personal_dj_activity_at(now), self._dj_voice_selector_motion.value(now)) if expansion is None else max(0.0, min(1.0, expansion))
        base = QRectF(rect.right() - margin - side, rect.bottom() - margin - side, side, side)
        lane = self._footer_dj_lane_width(rect, now) if self._song_collection_state().track_id else width
        width = min(width, lane)
        pill_height = side
        pill = QRectF(rect.right() - margin - width, rect.bottom() - margin - pill_height, width, pill_height)
        large_side = min(side * 2, lane)
        large = QRectF(pill.center().x() - large_side / 2, pill.top() - 44.0 * scale - large_side, large_side, large_side)
        if self._content_layout_is_narrow():
            # Short/narrow album pages cannot place a second-height icon above
            # the footer without crossing their metadata/transport. Keep its
            # stable right target inside the expanding caption instead.
            large = QRectF(base)
        def blend(start, end):
            return QRectF(*(a + (b - a) * expansion for a, b in zip(
                (start.x(), start.y(), start.width(), start.height()),
                (end.x(), end.y(), end.width(), end.height()))))
        icon, caption = blend(base, large), blend(base, pill)
        whole = icon.united(caption)
        posed = self._hover_control_rect("personal_dj", whole, {"personal_dj": whole}, now)
        transform = self._control_rect_transform(whole, posed)
        return transform.mapRect(icon), transform.mapRect(caption)

    def _personal_dj_backdrop_rect(self, rect: QRectF) -> QRectF:
        return QRectF(rect.right() - rect.width() * .32, rect.bottom() - rect.height() * .56,
                      rect.width() * .32, rect.height() * .56)

    def _personal_dj_damage_rect(self, rect: QRectF) -> QRect:
        icon, caption = self._personal_dj_geometry(rect, time.monotonic(), expansion=1.0)
        padding = max(8.0, 12.0 * self._surface_scale(rect))
        damage = icon.united(caption).adjusted(-padding, -padding, padding, padding)
        if (self._personal_dj_outro_reveal_at(time.monotonic()) > .001
                or self._dj_introduction_pending or self._dj_introduction_failed):
            damage = damage.united(self._personal_dj_outro_status_rect(rect, time.monotonic()))
        if self._owned_dj_speaking or self._personal_dj_activity_at(time.monotonic()) > .001:
            damage = damage.united(self._personal_dj_backdrop_rect(rect))
        # The caller also unites the previous damage, erasing the dark corner
        # when speech finishes without repainting it during ordinary idle.
        return damage.intersected(rect).toAlignedRect()

    def _paint_personal_dj_backdrop(self, painter: QPainter, rect: QRectF, expansion: float):
        if expansion <= .001:
            return
        bounds = self._personal_dj_backdrop_rect(rect)
        painter.save()
        painter.setOpacity(painter.opacity() * expansion)
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        painter.translate(rect.bottomRight())
        painter.scale(bounds.width(), bounds.height())
        # One shared soft mask keeps album/lyric detail visible through the
        # rings. Native vector feathering needs no per-frame background copy.
        gradient = QRadialGradient(QPointF(0, 0), 1.0)
        for stop, alpha in ((0.0, 177), (.35, 164), (.68, 104), (.88, 31), (1.0, 0)):
            gradient.setColorAt(stop, QColor(9, 11, 16, alpha))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(QRectF(-1, -1, 2, 2))
        painter.restore()

    def _personal_dj_control_shift(self, rect: QRectF, reveal: float) -> QPointF:
        bounds = self._personal_dj_button_rect(rect)
        return QPointF(14.0 * self._surface_scale(rect) * (1.0 - reveal),
                       (rect.bottom() - bounds.top() + 4.0) * (1.0 - reveal))

    def _personal_dj_hit_path(self, rect: QRectF, now: float, *, shifted: bool = True):
        icon, caption = self._personal_dj_geometry(rect, now)
        shape = QPainterPath()
        shape.setFillRule(Qt.FillRule.WindingFill)
        shape.addEllipse(icon)
        shape.addRoundedRect(caption, caption.height() / 2, caption.height() / 2)
        if self._personal_dj_status_reveal_at(now) > .001:
            status = self._personal_dj_outro_status_rect(rect, now)
            shape.addRoundedRect(status, status.height() / 2, status.height() / 2)
        if shifted:
            shift = self._personal_dj_control_shift(rect, self._personal_dj_reveal_at(now))
            shape.translate(shift)
        return shape

    def _personal_dj_activity_at(self, now: float) -> float:
        progress = max(0.0, min(1.0, (now - self._dj_activity_started_at) / self.DJ_SPEECH_CONTRACTION_SECONDS))
        return self._dj_activity_from + (self._dj_activity_to - self._dj_activity_from) * self._control_spring_progress(progress)

    def _personal_dj_outro_reveal_at(self, now: float) -> float:
        if self._dj_outro_started_at is None or self._owned_personal_dj_enabled is not True:
            return 0.0
        hide_at = self._dj_outro_started_at + self.DJ_SPEECH_CONTRACTION_SECONDS + self.DJ_OUTRO_IDLE_SECONDS
        progress = (now - hide_at) / self.FULLSCREEN_CONTROL_TRANSITION_SECONDS
        return max(0.0, min(1.0, 1.0 - self._control_spring_progress(progress)))

    def _personal_dj_outro_status_text(self) -> str:
        return {"ready": "下一首歌播报已准备好", "loading": "下一首歌播报加载中",
                "failed": "下一首歌播报加载失败"}.get(self._dj_next_preload_state, "等待下一首歌播报")

    def _personal_dj_pending_status_text(self) -> str:
        if self._dj_introduction_failed:
            return "本首歌播报加载失败"
        if self._dj_presentation_state == "ready":
            return "本首歌播报已准备好"
        return "本首歌播报加载中"

    def _personal_dj_outro_status_rect(self, rect: QRectF, now: float) -> QRectF:
        """One glass prompt contains the message and its existing right icon."""
        icon, _caption = self._personal_dj_geometry(rect, now)
        scale = self._surface_scale(rect)
        margin = max(10.0, 18.0 * scale)
        inset = max(3.0, 4.0 * scale)
        height = icon.height() + 2 * inset
        self._personal_dj_measured_widths(rect)
        text_width = self._dj_status_text_width
        right = min(rect.right() - margin + inset, icon.right() + inset)
        width = min(max(0.0, right - rect.left() - margin), text_width + icon.width() + 32.0 * scale)
        if self._song_collection_state().track_id:
            width = min(width, self._footer_dj_lane_width(rect, now))
        return QRectF(right - width, icon.center().y() - height / 2, width, height)

    def _personal_dj_status_reveal_at(self, now: float) -> float:
        if self._dj_voice_selector_active or self._owned_dj_speaking:
            return 0.0
        waiting = self._dj_introduction_pending or self._dj_introduction_failed
        return (1.0 if waiting else self._personal_dj_outro_reveal_at(now)) * (1.0 - self._personal_dj_activity_at(now))

    def _personal_dj_reveal_at(self, now: float) -> float:
        if self._owned_personal_dj_enabled is None:
            return 0.0
        return max(self._control_reveal_at(now), self._personal_dj_activity_at(now),
                   self._personal_dj_outro_reveal_at(now),
                   float(self._owned_personal_dj_enabled is True
                       and (self._dj_introduction_pending or self._dj_introduction_failed)))

    def _sync_personal_dj_state(self):
        provider = getattr(self.owner, "data_provider", None)
        getter = getattr(provider, "get_owned_personal_dj_enabled", None)
        speaking_getter = getattr(provider, "is_owned_personal_dj_speaking", None)
        try:
            enabled = getter() if self._owned_personal_fm and callable(getter) else None
            enabled = enabled if isinstance(enabled, bool) else None
            speaking = enabled is True and callable(speaking_getter) and bool(speaking_getter())
            presentation_getter = getattr(provider, "get_personal_dj_presentation", None)
            presentation = presentation_getter() if enabled is True and callable(presentation_getter) else {}
            presentation = presentation if isinstance(presentation, dict) else {}
            state = presentation.get("state", "speaking" if speaking else "idle")
            state = state if state in {"idle", "loading", "ready", "speaking", "failed"} else "idle"
            title = presentation.get("next_title", "")
            title = title[:256] if isinstance(title, str) else ""
            token = presentation.get("token")
            token = token if isinstance(token, (str, int, tuple)) else None
        except Exception:
            enabled, speaking = None, False
            state, title, token = "idle", "", None
            presentation = {}
        now = time.monotonic()
        preload_state = presentation.get("next_preload_state", "idle")
        preload_state = preload_state if isinstance(preload_state, str) and preload_state in {"idle", "loading", "ready", "failed"} else "idle"
        preload_changed = preload_state != self._dj_next_preload_state
        self._dj_next_preload_state = preload_state
        introduction_pending = (enabled is True and presentation.get("introduction_pending") is True
            and presentation.get("is_preview") is not True)
        introduction_failed = (enabled is True and presentation.get("introduction_failed") is True
            and presentation.get("is_preview") is not True)
        introduction_changed = ((introduction_pending, introduction_failed)
            != (self._dj_introduction_pending, self._dj_introduction_failed))
        self._dj_introduction_pending = introduction_pending
        self._dj_introduction_failed = introduction_failed
        completion = presentation.get("completion_id", 0)
        completion = completion if type(completion) is int and completion > 0 else 0
        completed_track_id = presentation.get("completed_track_id", "")
        completed_at = presentation.get("completed_at")
        current_track_id = self._visual_track_identity()
        previous_outro = self._dj_outro_started_at
        new_outro = False
        if (enabled is not True or speaking or presentation.get("is_preview") is True
                or self._dj_outro_track_id and self._dj_outro_track_id != current_track_id
                or not completion or self._dj_outro_started_at is not None and now >= self._dj_outro_started_at
                    + self.DJ_SPEECH_CONTRACTION_SECONDS + self.DJ_OUTRO_IDLE_SECONDS
                    + self.FULLSCREEN_CONTROL_TRANSITION_SECONDS):
            self._dj_outro_started_at = None
            self._dj_outro_track_id = ""
        elif (completion != self._dj_completed_token and isinstance(completed_track_id, str)
                and completed_track_id and completed_track_id == current_track_id
                and type(completed_at) in (int, float) and math.isfinite(completed_at)
                and 0 <= now - completed_at < self.DJ_SPEECH_CONTRACTION_SECONDS
                    + self.DJ_OUTRO_IDLE_SECONDS + self.FULLSCREEN_CONTROL_TRANSITION_SECONDS):
            # The provider's successful end marker excludes pause, failed
            # speech, previews and manual navigation. Its actual clock also
            # prevents a hidden page from replaying an old completion.
            self._dj_completed_token = completion
            self._dj_outro_started_at = float(completed_at)
            self._dj_outro_track_id = completed_track_id
            self._dj_ring_started_at = float(completed_at) + self.DJ_SPEECH_CONTRACTION_SECONDS
            new_outro = True
        if enabled is None:
            self._close_dj_voice_selector(immediate=True)
        if not speaking and not title and self._personal_dj_activity_at(now) > .001:
            # Keep the real outgoing caption's width through its contraction;
            # clearing provider text must not snap a wide pill to a short one.
            title = self._dj_next_title
        if state == "failed":
            self._dj_failed_until = now + 1.2
        elif state == "idle" and now < self._dj_failed_until and enabled is True:
            state = "failed"
        if (not new_outro and previous_outro == self._dj_outro_started_at and not preload_changed and not introduction_changed
                and (enabled, speaking, state, title, token) == (self._owned_personal_dj_enabled, self._owned_dj_speaking,
                self._dj_presentation_state, self._dj_next_title, self._dj_presentation_token)):
            return
        if token != self._dj_presentation_token and self._dj_outro_started_at is None:
            self._dj_ring_started_at = now
        activity_to = 1.0 if speaking else 0.0
        if activity_to != self._dj_activity_to:
            self._dj_activity_from = self._personal_dj_activity_at(now)
            self._dj_activity_to = activity_to
            self._dj_activity_started_at = now
        self._owned_personal_dj_enabled, self._owned_dj_speaking = enabled, speaking
        self._dj_presentation_state, self._dj_next_title, self._dj_presentation_token = state, title, token
        self._dj_audio_frame = None
        self._sync_window_control_rects()
        damage = self._personal_dj_damage_rect(QRectF(self.rect()))
        self.update(damage.united(self._dj_last_damage))
        self._dj_last_damage = damage

    def _update_personal_dj_control(self, now: float):
        # This provider presentation is an immutable in-memory player read,
        # independent of the slower device-enumeration/status cadence.
        self._sync_personal_dj_state()
        reveal = self._personal_dj_reveal_at(now)
        changed = abs(reveal - self._dj_last_reveal) > .001
        self._dj_last_reveal = reveal
        self._dj_audio_frame = None
        if self._owned_dj_speaking:
            getter = getattr(getattr(self.owner, "data_provider", None), "get_owned_personal_dj_audio_frame", None)
            try:
                self._dj_audio_frame = getter() if callable(getter) else None
            except Exception:
                pass
        if self.isVisible() and not self._capturing_transition_snapshot and (changed or reveal > .001):
            damage = self._personal_dj_damage_rect(QRectF(self.rect()))
            self.update(damage.united(self._dj_last_damage))
            self._dj_last_damage = damage

    def _personal_dj_tooltip(self):
        if not self._owned_personal_dj_enabled:
            return "私人漫游 · 私人DJ已关闭 · 点击开启"
        if self._dj_voice_selector_active:
            return "私人漫游 · 私人DJ已开启 · 点击光环关闭，下方选择音色"
        return "私人漫游 · 私人DJ已开启 · 点击展开"

    def _personal_dj_icon_contains(self, position, now: float) -> bool:
        rect = QRectF(self.rect())
        icon, _caption = self._personal_dj_geometry(rect, now)
        icon.translate(self._personal_dj_control_shift(rect, self._personal_dj_reveal_at(now)))
        shape = QPainterPath()
        shape.addEllipse(icon)
        return shape.contains(position)

    def _activate_personal_dj(self, position, *, toggle_ready: bool):
        """Compact clicks reveal choices; only an already expanded icon toggles."""
        self._sync_owned_playback_mode()
        if self._owned_personal_dj_enabled is None:
            return
        now = time.monotonic()
        icon_hit = self._personal_dj_icon_contains(position, now)
        if self._owned_personal_dj_enabled is False and icon_hit:
            self._toggle_personal_dj()
        elif (toggle_ready and self._dj_voice_selector_active
                and icon_hit):
            self._toggle_personal_dj()
        self._open_dj_voice_selector(now)
        self.setToolTip(self._personal_dj_tooltip())

    def _toggle_personal_dj(self):
        self._sync_owned_playback_mode()
        setter = getattr(getattr(self.owner, "data_provider", None), "set_owned_personal_dj_enabled", None)
        if self._owned_personal_dj_enabled is None or not callable(setter):
            return False
        try:
            changed = bool(setter(not self._owned_personal_dj_enabled))
        except Exception:
            changed = False
        self._sync_personal_dj_state()
        self.setToolTip(self._personal_dj_tooltip())
        return changed

    def _window_button_clickable(self, key: str, shared: bool) -> bool:
        return shared or key == "personal_dj" and self._personal_dj_reveal_at(time.monotonic()) >= .75

    def _personal_dj_ring_status(self):
        """The actual preload status outlives its short text notification."""
        if self._owned_personal_dj_enabled is not True or self._owned_dj_speaking:
            return "idle"
        if self._dj_introduction_pending or self._dj_introduction_failed:
            return self._dj_presentation_state
        if self._dj_next_preload_state in {"loading", "ready", "failed"}:
            return self._dj_next_preload_state
        return self._dj_presentation_state

    def _paint_personal_dj_control(self, painter: QPainter, rect: QRectF):
        now = time.monotonic()
        reveal = self._personal_dj_reveal_at(now)
        if reveal <= .001:
            return
        icon, caption = self._personal_dj_geometry(rect, now)
        button = icon.united(caption)
        speech_expansion = self._personal_dj_activity_at(now)
        expansion = max(speech_expansion, self._dj_voice_selector_motion.value(now))
        scale = self._surface_scale(rect)
        elapsed = max(0.0, now - self._dj_ring_started_at)
        painter.save()
        painter.setOpacity(reveal)
        self._paint_personal_dj_backdrop(painter, rect, speech_expansion)
        painter.translate(self._personal_dj_control_shift(rect, reveal))
        hovered = self._hovered_window_button == "personal_dj"
        frame = self._dj_audio_frame if self._owned_dj_speaking else None
        energy = min(1.0, max(frame.low, frame.mid, frame.high) * 4) if frame is not None else 0.0
        status_reveal = self._personal_dj_status_reveal_at(now)
        status_rect = self._personal_dj_outro_status_rect(rect, now) if status_reveal > .001 else QRectF()
        if status_reveal > .001:
            painter.save()
            painter.setOpacity(reveal * status_reveal)
            self._paint_surface_glass(painter, status_rect, hovered=hovered,
                                     pressed=self._pressed_window_button == "personal_dj", live=True, role="dj-status")
            painter.restore()
        if status_reveal < .999:
            painter.save()
            painter.setOpacity(reveal * (1.0 - status_reveal))
            self._paint_surface_glass(painter, caption, hovered=hovered,
                                     pressed=self._pressed_window_button == "personal_dj", live=True, role="dj-caption")
            painter.setBrush(Qt.BrushStyle.NoBrush)
            border = QColor(170, 172, 175, round(150 + 80 * energy))
            painter.setPen(QPen(border, max(1.0, scale) + energy * scale * 1.5))
            painter.drawRoundedRect(caption, caption.height() / 2, caption.height() / 2)
            if expansion < .999:
                painter.save()
                painter.setOpacity(reveal * (1.0 - status_reveal) * (1.0 - expansion))
                self._paint_surface_glass(painter, icon, hovered=hovered,
                                         pressed=self._pressed_window_button == "personal_dj", live=True, role="dj-icon")
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(icon)
                painter.restore()
            painter.restore()
        status_state = self._personal_dj_ring_status()
        status_color = personal_dj_status_color(status_state)
        if status_color is None and self._owned_personal_dj_enabled is True and not self._owned_dj_speaking:
            # Enabled remains visible when no brief status text is open. This
            # is an outline only: glass keeps the same neutral scene material
            # as its neighboring controls, and ordinary idle hiding is shared.
            status_color = self._interpolated_control_color("dj-accent", self._future_lyric_color(button))
        if status_color is not None and status_reveal < .999:
            painter.save()
            painter.setOpacity(reveal * (1.0 - status_reveal))
            opacity = personal_dj_status_opacity(status_state, elapsed)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for spread, alpha in ((3.5, 23), (2.0, 45), (1.0, 230)):
                color = QColor(status_color)
                color.setAlpha(round(170 + 60 * opacity if spread == 1.0 else alpha * opacity))
                painter.setPen(QPen(color, max(1.2, 1.8 * scale) * spread))
                painter.drawEllipse(icon.adjusted(-1.2 * scale, -1.2 * scale, 1.2 * scale, 1.2 * scale))
            painter.restore()
        control_ink = self._surface_glass_ink(status_rect if status_reveal > .001 else button, live=True)
        color = (self._interpolated_control_color("dj-accent", self._future_lyric_color(button)) if self._owned_dj_speaking
                 and self._owned_personal_dj_enabled and status_reveal < .999 else control_ink)
        inner = icon.adjusted(icon.width() * .18, icon.height() * .14, -icon.width() * .18, -icon.height() * .14)
        neutral = control_ink
        paint_personal_dj_icon(painter, inner, color, bands=frame, elapsed=elapsed, neutral=neutral)
        if expansion > .25 and self._dj_next_title and not self._dj_voice_selector_active:
            painter.save()
            painter.setOpacity(reveal * min(1.0, (expansion - .25) / .75))
            painter.setClipRect(caption)
            font = self._surface_font(max(10.0, 14.0 * scale), QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(self._surface_glass_ink(caption, live=True))
            text_rect = caption.adjusted(12.0 * scale, 0, -12.0 * scale, 0)
            if self._content_layout_is_narrow():
                text_rect.setRight(max(text_rect.left(), icon.left() - 8. * scale))
            title = QFontMetricsF(font).elidedText(self._dj_next_title, Qt.TextElideMode.ElideRight, max(1, round(text_rect.width())))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter, title)
            painter.restore()
        waiting = (not self._owned_dj_speaking
            and (self._dj_introduction_pending or self._dj_introduction_failed))
        if status_reveal > .001:
            painter.save()
            painter.setOpacity(reveal * status_reveal)
            font = self._surface_font(max(14.0, 16.0 * scale), QFont.Weight.DemiBold)
            painter.setFont(font)
            status_text = self._personal_dj_pending_status_text() if waiting else self._personal_dj_outro_status_text()
            text_rect = QRectF(status_rect.left() + 16.0 * scale, status_rect.top(),
                               max(0.0, icon.left() - status_rect.left() - 24.0 * scale), status_rect.height())
            text = QFontMetricsF(font).elidedText(status_text,
                Qt.TextElideMode.ElideRight, max(1, round(text_rect.width())))
            painter.setPen(self._surface_glass_ink(status_rect, live=True))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
            painter.restore()
        painter.restore()

    def _audio_output_available(self) -> bool:
        return callable(getattr(getattr(self.owner, "data_provider", None), "get_audio_route_snapshot", None))

    def _sync_audio_output_status(self, snapshot=None, *, settle: bool = False):
        self._sync_owned_playback_mode()
        getter = getattr(getattr(self.owner, "data_provider", None), "get_audio_route_snapshot", None)
        try:
            snapshot = snapshot if snapshot is not None else (getter() if callable(getter) else None)
        except Exception:
            snapshot = None
        update = self._audio_output_membership.update(snapshot)
        kinds = update.current.kinds
        now = time.monotonic()
        for member in update.removed:
            self._audio_output_recent_announced.pop(member.id, None)
        if settle or not self.isVisible() or self.isMinimized() or update.baseline:
            self._audio_output_motion.settle(update.current, now)
        elif not update.held_pending:
            self._audio_output_motion.reconcile(update.current, now,
                removed=tuple(member.id for member in update.removed))
            for member in update.added:
                if now - self._audio_output_recent_announced.get(member.id, -100.0) < 4.0:
                    continue
                self._audio_output_motion.announce(member, update.previous, update.current, now)
                self._audio_output_recent_announced[member.id] = now
        if kinds == self._audio_output_kinds and not update.changed:
            return
        self._audio_output_kinds = kinds
        self._update_audio_output_region()

    def on_audio_device_event(self, snapshot, device) -> None:
        """Window/fullscreen device arrivals replace the island's global toast."""
        self._sync_audio_output_status(snapshot)
        if snapshot.event_serial <= self._audio_output_event_serial:
            return
        self._audio_output_event_serial = snapshot.event_serial
        if device is None or not self.isVisible() or self.isMinimized():
            return
        now = time.monotonic()
        if now - self._audio_output_recent_announced.get(device.id, -100.0) < 4.0:
            return
        # An available endpoint may arrive without becoming playback output.
        # Animate its name/glyph, then return to the confirmed route/count.
        state = self._audio_output_membership.current
        self._audio_output_motion.announce(OutputMember.from_device(device), state, state, now)
        self._audio_output_recent_announced[device.id] = now
        self._update_audio_output_region()

    def _advance_audio_output_motion(self, now: float):
        if not self._geometry_transition_snapshot.isNull():
            self._audio_output_motion.pause(now)
            return
        if self._audio_output_motion.advance(now):
            # Keep the control strip revealed for the entire connection
            # sequence; animation frames repaint only this small region.
            reveal = self._control_reveal_at(now)
            self._control_last_motion_at = now
            if self._control_reveal_target < 1.0:
                self._control_reveal_from = reveal
                self._control_reveal_target = 1.0
                self._control_transition_started_at = now
            self._update_audio_output_region()
        # Identity dedup is bounded and expires; discovery/volume snapshots
        # cannot restart the sequence or accumulate an unbounded event history.
        self._audio_output_recent_announced = {identity: at for identity, at in
            self._audio_output_recent_announced.items() if now - at < 4.0}

    def _update_audio_output_region(self):
        button = self._audio_output_button_rect(
            self._window_control_base_rect(QRectF(self.rect())), self._surface_scale())
        # Include the whole name capsule throughout its collapse so no pixels
        # remain behind it. Other controls keep their normal geometry/hitboxes.
        margin = round(button.height() * 0.5) + 3
        self.update(button.toAlignedRect().adjusted(-round(button.width() * 5.0) - margin,
                                                   -margin, margin, margin))

    def _audio_output_button_rect(self, button: QRectF, scale: float) -> QRectF:
        return self._preceding_control_rect(self._content_layout_button_rect(button, scale), scale)

    def _dismiss_audio_output_panel(self, *, immediate: bool = False):
        if immediate:
            self._close_dj_voice_selector(immediate=True)
        panel = getattr(self, "_audio_output_panel", None)
        if panel is not None:
            panel.dismiss(immediate=immediate)

    def _toggle_audio_output_panel(self):
        if self._audio_output_panel is not None and self._audio_output_panel.isVisible():
            self._dismiss_audio_output_panel()
            return
        self._open_audio_output_panel()

    def _open_audio_output_panel(self):
        if self._audio_output_panel is not None and self._audio_output_panel.isVisible():
            return
        if not self._audio_output_available():
            return
        self._dismiss_song_playlist_panel()
        self._close_dj_voice_selector()
        self._audio_output_motion.settle(self._audio_output_membership.current, time.monotonic())
        if self._audio_output_panel is None:
            self._audio_output_panel = AudioOutputPanel(self.owner.data_provider, self)
            self._audio_output_panel.dismissed.connect(self._note_pointer_moved)
        now, rect = time.monotonic(), QRectF(self.rect())
        base = self._window_control_base_rect(rect)
        button = self._audio_output_button_rect(base, self._surface_scale())
        button = self._hover_control_rect("audio_output", button, self._top_control_hover_group(rect), now)
        button.translate(self._window_control_shift(button, self._control_reveal_at(time.monotonic())))
        self._note_pointer_moved(now)
        self._audio_output_panel.open_at(QPoint(round(button.right()), round(button.top())), source_rect=button)
        self._update_audio_output_region()

    def _toggle_translation(self):
        setter = getattr(self.owner, "set_enable_dual_line_lyrics", None)
        if callable(setter):
            enabled = not bool(getattr(self.owner, "enable_dual_line_lyrics", False))
            self.on_translation_visibility_changed(enabled)
            setter(enabled)
        self._display_lyrics_cache_key = None
        refresh = getattr(getattr(self.owner, "data_provider", None), "_request_immediate_media_refresh", None)
        if callable(refresh):
            refresh()
        self.update()

    def _window_control_shift(self, button: QRectF, reveal: float) -> QPointF:
        scale = self._surface_scale()
        hidden = 1.0 - reveal
        return QPointF((button.width() + 22.0 * scale) * hidden,
                       -(button.height() + 18.0 * scale) * hidden)

    def _transport_control_shift(self, panel: QRectF, reveal: float) -> QPointF:
        scale = self._surface_scale()
        hidden = 1.0 - reveal
        centered = self._cover_layout_fraction_at(time.monotonic())
        return QPointF(-36.0 * scale * hidden * (1.0 - centered),
                       (panel.height() + 20.0 * scale) * hidden)

    def _remember_control_layer(self, key: tuple, image: QImage):
        previous = self._control_layer_cache.pop(key, None)
        if previous is not None:
            self._control_layer_cache_bytes -= int(previous.sizeInBytes())
        self._control_layer_cache[key] = image
        self._control_layer_cache_bytes += int(image.sizeInBytes())
        while (
            len(self._control_layer_cache) > 48
            or self._control_layer_cache_bytes > self.CONTROL_LAYER_CACHE_MAX_BYTES
        ):
            oldest = next(iter(self._control_layer_cache))
            self._control_layer_cache_bytes -= int(self._control_layer_cache.pop(oldest).sizeInBytes())

    def _cached_control_layer(self, key: tuple) -> QImage | None:
        image = self._control_layer_cache.pop(key, None)
        if image is not None:
            self._control_layer_cache[key] = image
        return image

    def _control_layer_source(self, image_key: tuple, layer_bounds: QRectF, dpr: float, draw) -> QImage:
        source_key = (image_key, 0.0)
        source = self._cached_control_layer(source_key)
        if source is not None:
            return source
        width, height = image_key[1:3]
        source = QImage(width, height, QImage.Format.Format_RGBA8888_Premultiplied)
        source.fill(Qt.GlobalColor.transparent)
        with QPainter(source) as layer:
            layer.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            layer.scale(dpr, dpr)
            layer.translate(-layer_bounds.left(), -layer_bounds.top())
            draw(layer)
        source.setDevicePixelRatio(dpr)
        self._remember_control_layer(source_key, source)
        return source

    @classmethod
    def _control_blur_radii(cls, scale: float, dpr: float) -> tuple[float, ...]:
        maximum = 6.0 * scale * dpr
        return tuple(dict.fromkeys(
            round(maximum * fraction * 2.0) / 2.0
            for fraction in cls.CONTROL_BLUR_FRACTIONS
        ))

    def _build_control_blur(self, image_key: tuple, radius: float, source: QImage) -> QImage:
        key = (image_key, radius)
        cached = self._cached_control_layer(key)
        if cached is not None:
            return cached
        width, height = image_key[1:3]
        pixels = Image.frombytes("RGBA", (width, height), bytes(source.bits()))
        blurred_bytes = pixels.filter(ImageFilter.GaussianBlur(radius)).tobytes()
        blurred = QImage(
            blurred_bytes, width, height, width * 4,
            QImage.Format.Format_RGBA8888_Premultiplied,
        ).copy()
        blurred.setDevicePixelRatio(source.devicePixelRatio())
        self._remember_control_layer(key, blurred)
        return blurred

    def _maybe_prewarm_control_blur(
        self, bounds: QRectF, scale: float, dpr: float,
        default_key: tuple, default_draw,
    ):
        """Prepare one stable control blur level per quiet frame before idle exit."""
        if self._control_reveal_target < 1.0:
            return
        now = time.monotonic()
        if self._previous_track_visual is not None and now - self._track_transition_at < .36:
            # Album changes continually retint the transport glyphs. Blurring
            # those transient colors evicts useful levels and competes with
            # the visible transition; resume in its existing quiet lead-in.
            return
        idle_elapsed = now - self._control_last_motion_at
        idle_seconds = (
            self.FULLSCREEN_CONTROL_IDLE_SECONDS if self._fullscreen
            else self.WINDOWED_CONTROL_IDLE_SECONDS
        )
        if not (max(0.3, idle_seconds - 2.0) <= idle_elapsed < idle_seconds):
            return
        if now < self._control_layer_next_prewarm_at:
            return
        padding = math.ceil(20.0 * scale)
        layer_bounds = QRectF(bounds).adjusted(-padding, -padding, padding, padding)
        width = max(1, int(math.ceil(layer_bounds.width() * dpr)))
        height = max(1, int(math.ceil(layer_bounds.height() * dpr)))
        image_key = (default_key, width, height, round(dpr, 3))
        for radius in self._control_blur_radii(scale, dpr)[1:]:
            if (image_key, radius) in self._control_layer_cache:
                continue
            source = self._control_layer_source(image_key, layer_bounds, dpr, default_draw)
            self._build_control_blur(image_key, radius, source)
            # All control layers share this gate; blur only one per paint.
            self._control_layer_next_prewarm_at = now + 0.035
            break

    def _paint_control_layer(self, painter: QPainter, bounds: QRectF, reveal: float,
                             shift: QPointF, cache_key: tuple, draw,
                             *, prewarm_default: tuple[tuple, object] | None = None):
        """Move a blended, already blurred control layer without frame-time PIL work."""
        if reveal <= 0.001:
            return
        # Glyph bodies also carry glass; all exiting layers must use the same
        # setting and frozen scene as their fully visible counterparts.
        scene = self._surface_glass_backdrop()
        # Transport's key already contains its one shared ink decision; reuse
        # it rather than asking the interpolator again within the same paint.
        ink_key = cache_key[2] if cache_key[0] == "transport" else self._scene_control_ink().rgba()
        material_key = (self._surface_glass_light(), scene.cacheKey(), self.width(), self.height(),
                        ink_key, self._surface_glass_level())
        cache_key = cache_key + (material_key,)
        if prewarm_default is not None:
            prewarm_default = (prewarm_default[0] + (material_key,), prewarm_default[1])
        painter.save()
        scale = self._surface_scale()
        transform = painter.deviceTransform()
        native_dpr = max(1., math.hypot(transform.m11(), transform.m12()),
                         math.hypot(transform.m21(), transform.m22()))
        dpr = min(native_dpr, self.CONTROL_LAYER_DPR_CAP)
        if reveal >= 0.999:
            # The background can rebuild on every paint at low cadence. Its
            # presence must not starve the existing one-level/35 ms prewarm
            # budget or the controls would lose their idle-exit blur.
            if prewarm_default is not None:
                self._maybe_prewarm_control_blur(
                    bounds, scale, dpr, prewarm_default[0], prewarm_default[1],
                )
            painter.translate(shift)
            draw(painter)
            painter.restore()
            return
        padding = math.ceil(20.0 * scale)
        layer_bounds = QRectF(bounds).adjusted(-padding, -padding, padding, padding)
        width = max(1, int(math.ceil(layer_bounds.width() * dpr)))
        height = max(1, int(math.ceil(layer_bounds.height() * dpr)))
        image_key = (cache_key, width, height, round(dpr, 3))
        source = self._control_layer_source(image_key, layer_bounds, dpr, draw)
        wanted = (1.0 - reveal) * 6.0 * scale * dpr
        available = [(0.0, source)]
        for radius in self._control_blur_radii(scale, dpr)[1:]:
            cached = self._cached_control_layer((image_key, radius))
            if cached is not None:
                available.append((radius, cached))
        lower_radius, lower_image = available[0]
        upper_radius, upper_image = available[-1]
        for radius, image in available[1:]:
            if radius >= wanted:
                upper_radius, upper_image = radius, image
                break
            lower_radius, lower_image = radius, image
        if upper_radius <= lower_radius or lower_image is upper_image:
            layer_image = lower_image if wanted <= lower_radius else upper_image
        else:
            blend = max(0.0, min(1.0, (wanted - lower_radius) / (upper_radius - lower_radius)))
            layer_image = QImage(width, height, QImage.Format.Format_RGBA8888_Premultiplied)
            layer_image.fill(Qt.GlobalColor.transparent)
            with QPainter(layer_image) as mixed:
                mixed.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                target = QRectF(0.0, 0.0, width, height)
                mixed.setOpacity(1.0 - blend)
                mixed.drawImage(target, lower_image)
                mixed.setOpacity(blend)
                mixed.drawImage(target, upper_image)
            layer_image.setDevicePixelRatio(dpr)
        painter.setOpacity(reveal ** 0.75)
        if native_dpr > dpr:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(layer_bounds.translated(shift), layer_image)
        painter.restore()

    def _sync_window_control_rects(self, rect: QRectF | None = None):
        rect = QRectF(self.rect()) if rect is None else QRectF(rect)
        library_available = self._library_back_available()
        if library_available != self._control_prewarm_library_available:
            # A source can change in place. Re-arm only the bounded local
            # prewarm work; source changes must not reveal idle controls.
            self._control_prewarm_library_available = library_available
            self._control_prewarm_remaining = (2 + int(library_available)) * (len(self.CONTROL_BLUR_FRACTIONS) - 1)
            self._control_prewarm_requested = False
        button = self._window_control_base_rect(rect)
        now = time.monotonic()
        reveal = self._control_reveal_at(now)
        group = self._top_control_hover_group(rect)
        self._button_rects = ({key: self._hover_control_rect(key, target, group, now).translated(
            self._window_control_shift(button, reveal)) for key, target in group.items()}
            if reveal >= .75 else {})
        if reveal >= 0.75 and library_available:
            library = self._library_button_face(rect)
            self._button_rects["library"] = self._hover_control_rect("library", library, {"library": library}, now).translated(self._library_control_shift(rect, reveal))
        if reveal >= .75 and getattr(self, "_library_loading", None) is None:
            self._button_rects.update(self._song_collection_rects(rect))
        dj_reveal = self._personal_dj_reveal_at(now)
        if dj_reveal >= .75 and getattr(self, "_library_loading", None) is None:
            dj_button = self._personal_dj_button_rect(rect)
            self._button_rects["personal_dj"] = dj_button.translated(self._personal_dj_control_shift(rect, dj_reveal))
        if reveal >= .75 and "audio_output" in group:
            audio = self._audio_output_button_rect(button, self._surface_scale(rect))
            pose = self._audio_output_motion.pose
            if pose is not None:
                audio = connection_capsule_rect(audio, pose, self._surface_font(
                    16.0 * self._surface_scale(rect), QFont.Weight.DemiBold), 16.0 * self._surface_scale(rect))
            self._button_rects["audio_output"] = self._hover_control_rect("audio_output", audio, group, now).translated(self._window_control_shift(button, reveal))
        if self._hovered_window_button not in self._button_rects:
            self._hovered_window_button = ""

    def _translation_symbol_path(self, rect: QRectF) -> QPainterPath:
        """The compact 文/A symbol also supplies the active face's cutout."""
        scale = rect.width() / 24.0
        path = QPainterPath()
        path.addText(QPointF(0.0, 17.0 * scale),
                     self._surface_font(19.0 * scale, QFont.Weight.Bold), "文")
        path.addText(QPointF(16.0 * scale, 26.0 * scale),
                     self._surface_font(13.0 * scale, QFont.Weight.Bold), "A")
        return path.translated(rect.center() - path.boundingRect().center())

    @staticmethod
    def _paint_window_mode_icon(painter: QPainter, rect: QRectF, fullscreen: bool, color: QColor):
        """Show the next destination in the existing three-mode route."""
        paint_presentation_glyph(painter, rect, "windowed" if fullscreen else "island", color)

    def _paint_window_controls(self, painter: QPainter, rect: QRectF, *, paint_ink: bool = True):
        self._sync_window_control_rects(rect)
        if not paint_ink:
            return
        scale = self._surface_scale(rect)
        key = "mode" if self._fullscreen else "return"
        button = self._window_control_base_rect(rect)
        translation = self._translation_button_rect(button, scale)
        bounds = button.united(translation)
        playback_mode = self._owned_playback_mode
        playback_button = self._playback_mode_button_rect(button, scale)
        layout_button = self._content_layout_button_rect(button, scale)
        bounds = bounds.united(layout_button)
        layout_hovered = self._hovered_window_button == "content_layout"
        layout_pressed = self._pressed_window_button == "content_layout"
        playback_hovered = self._hovered_window_button == "playback_mode"
        playback_pressed = self._pressed_window_button == "playback_mode"
        fm_continuous = self._owned_personal_fm and playback_mode == "sequential"
        playback_asset = "personal_fm" if fm_continuous else ("sequence" if playback_mode == "sequential" else playback_mode)
        playback_active = playback_mode in {"shuffle", "repeat_one"}
        now = time.monotonic()
        reveal = self._control_reveal_at(now)
        if reveal <= 0.001:
            return
        playback_ink = self._surface_glass_ink(playback_button, active=playback_active)
        fm_stops = None
        fm_phase = default_fm_phase = 0.0
        if fm_continuous:
            ink_time = now if reveal >= .999 else self._control_transition_started_at
            fm_stops = self._personal_fm_stops(playback_button)
            fm_phase = (ink_time / 4.0) % 1.0
        default_playback_ink = QColor(playback_ink)
        if fm_continuous:
            idle_seconds = self.FULLSCREEN_CONTROL_IDLE_SECONDS if self._fullscreen else self.WINDOWED_CONTROL_IDLE_SECONDS
            default_fm_phase = ((self._control_last_motion_at + idle_seconds) / 4.0) % 1.0
        if playback_mode is not None:
            bounds = bounds.united(playback_button)
        audio_available = self._audio_output_available()
        audio_button = self._audio_output_button_rect(button, scale)
        audio_hovered = self._hovered_window_button == "audio_output"
        audio_pressed = self._pressed_window_button == "audio_output"
        audio_active = self._audio_output_panel is not None and self._audio_output_panel.isVisible()
        audio_kinds = self._audio_output_kinds
        audio_count = len(audio_kinds)
        audio_pose = self._audio_output_motion.pose
        if audio_available and not audio_active and audio_pose is None:
            bounds = bounds.united(audio_button)
        translated = bool(getattr(self.owner, "enable_dual_line_lyrics", False))
        translation_hovered = self._hovered_window_button == "translation"
        translation_pressed = self._pressed_window_button == "translation"
        hovered = self._hovered_window_button == key
        pressed = self._pressed_window_button == key
        hover_group = self._top_control_hover_group(rect)
        hover_pose_key = self._control_hover_pose_key(hover_group, now)
        posed_button = self._hover_control_rect(key, button, hover_group, now)
        posed_translation = self._hover_control_rect("translation", translation, hover_group, now)
        posed_playback = self._hover_control_rect("playback_mode", playback_button, hover_group, now)
        posed_audio = self._hover_control_rect("audio_output", audio_button, hover_group, now)
        posed_layout = self._hover_control_rect("content_layout", layout_button, hover_group, now)

        def draw(layer: QPainter, *, hovered_state: bool = hovered, pressed_state: bool = pressed,
                 mode_ink: QColor = playback_ink, gradient_phase: float = fm_phase,
                 use_hover_pose: bool = True):
            button, translation, playback_button, audio_button = (
                (posed_button, posed_translation, posed_playback, posed_audio) if use_hover_pose else
                (self._window_control_base_rect(rect), self._translation_button_rect(self._window_control_base_rect(rect), scale),
                 self._playback_mode_button_rect(self._window_control_base_rect(rect), scale),
                 self._audio_output_button_rect(self._window_control_base_rect(rect), scale)))
            layer.save()
            def face(target: QRectF, is_hovered: bool = False, is_pressed: bool = False,
                     active: bool = False, *, role=None):
                self._paint_surface_glass(layer, target, hovered=is_hovered,
                                         pressed=is_pressed, active=active, role=role)

            def icon_rect(target: QRectF, fraction: float = 0.55):
                side = target.width() * fraction
                return QRectF(target.center().x() - side / 2.0,
                              target.center().y() - side / 2.0, side, side)

            ink = self._surface_glass_ink(button)
            current_layout_button = posed_layout if use_hover_pose else self._content_layout_button_rect(button, scale)
            face(current_layout_button, layout_hovered, layout_pressed, False, role="content-layout")
            self._paint_content_layout_icon(layer, icon_rect(current_layout_button, .62))
            if audio_available and not audio_active and audio_pose is None:
                face(audio_button, audio_hovered, audio_pressed, audio_active or bool(audio_kinds), role="audio-output")
                audio_ink = self._surface_glass_ink(audio_button, active=bool(audio_kinds))
                paint_audio_output_status(layer, audio_button, audio_kinds, audio_ink, count=audio_count)
            if playback_mode is not None:
                face(playback_button, playback_hovered, playback_pressed, playback_active, role="playback-mode")
                paint_music_source_icon(layer, icon_rect(playback_button, 0.55), playback_asset,
                                        tint=mode_ink, gradient_stops=fm_stops, gradient_phase=gradient_phase)
            translation_symbol = self._translation_symbol_path(icon_rect(translation, 0.58))
            face(translation, translation_hovered, translation_pressed, translated, role="translation")
            layer.setPen(Qt.PenStyle.NoPen)
            layer.setBrush(self._surface_glass_ink(translation, active=translated))
            layer.drawPath(translation_symbol)
            face(button, hovered_state, pressed_state, role="window-mode")
            if not self._capturing_transition_snapshot:
                self._paint_window_mode_icon(layer, icon_rect(button, 0.60), self._fullscreen, ink)
            layer.restore()

        def draw_default(layer: QPainter):
            draw(layer, hovered_state=False, pressed_state=False, mode_ink=default_playback_ink,
                 gradient_phase=default_fm_phase, use_hover_pose=False)

        gradient_key = tuple((position, color.rgba()) for position, color in fm_stops) if fm_stops else ()

        self._paint_control_layer(
            painter, bounds, reveal, self._window_control_shift(button, reveal),
            ("window", key, round(scale, 3), translated,
             translation_hovered, translation_pressed, playback_mode, playback_asset, playback_ink.rgba(), playback_hovered, playback_pressed,
             hovered, pressed, audio_available, audio_hovered, audio_pressed, audio_active, audio_kinds, audio_pose is not None, self._capturing_transition_snapshot,
             gradient_key, fm_phase if fm_continuous else None, hover_pose_key,
             self._content_layout, round(self._layout_transition_fraction, 3), layout_hovered, layout_pressed, False), draw,
            prewarm_default=(("window", key, round(scale, 3), translated,
                               translation_hovered, translation_pressed, playback_mode, playback_asset, default_playback_ink.rgba(), playback_hovered, playback_pressed,
                               False, False, audio_available, audio_hovered, audio_pressed, audio_active, audio_kinds, audio_pose is not None, self._capturing_transition_snapshot,
                               gradient_key, default_fm_phase if fm_continuous else None, (),
                               self._content_layout, 1., False, False, False), draw_default),
            # The live FM gradient changes every frame. Caching the entire
            # group here would only churn native rasters and evict its idle
            # blur levels; its existing direct vector path stays continuous.
        )
        if audio_available and not audio_active and audio_pose is not None:
            painter.save()
            painter.setOpacity(reveal ** .75)
            painter.translate(self._window_control_shift(button, reveal))
            connection = connection_capsule_rect(audio_button, audio_pose, self._surface_font(
                16.0 * scale, QFont.Weight.DemiBold), 16.0 * scale)
            posed_connection = self._hover_control_rect("audio_output", connection, hover_group, now)
            painter.setTransform(self._control_rect_transform(connection, posed_connection), True)
            paint_connection_pose(painter, audio_button, audio_pose,
                                  self._surface_font(16.0 * scale, QFont.Weight.DemiBold),
                                  available_left=16.0 * scale, refractive=True,
                                  material_level=self._surface_glass_level(),
                                  light=self._surface_glass_light(), ink=self._surface_glass_ink(posed_connection),
                                  backdrop=self._surface_glass_backdrop(),
                                  backdrop_rect=self._surface_glass_source_rect(posed_connection, self._surface_glass_backdrop()),
                                  material_owner=self, material_role="audio-connection",
                                  material_scene=self._visual_track_identity,
                                  material_dirty=painter.worldTransform().mapRect(posed_connection))
            painter.restore()

    @staticmethod
    def _rounded_polygon_path(points: tuple[QPointF, ...], radius: float) -> QPainterPath:
        """Build a softly rounded filled-symbol outline from polygon points."""
        path = QPainterPath()
        if len(points) < 3:
            return path
        before: list[QPointF] = []
        after: list[QPointF] = []
        for index, point in enumerate(points):
            previous = points[index - 1]
            following = points[(index + 1) % len(points)]
            previous_dx, previous_dy = previous.x() - point.x(), previous.y() - point.y()
            following_dx, following_dy = following.x() - point.x(), following.y() - point.y()
            previous_length = max(0.001, math.hypot(previous_dx, previous_dy))
            following_length = max(0.001, math.hypot(following_dx, following_dy))
            inset = min(max(0.0, radius), previous_length * 0.28, following_length * 0.28)
            before.append(QPointF(
                point.x() + previous_dx / previous_length * inset,
                point.y() + previous_dy / previous_length * inset,
            ))
            after.append(QPointF(
                point.x() + following_dx / following_length * inset,
                point.y() + following_dy / following_length * inset,
            ))
        path.moveTo(after[0])
        for index in range(1, len(points)):
            path.lineTo(before[index])
            path.quadTo(points[index], after[index])
        path.lineTo(before[0])
        path.quadTo(points[0], after[0])
        path.closeSubpath()
        return path

    def _paint_transport_icon(self, painter: QPainter, rect: QRectF, command: str, color: QColor,
                              *, hovered: bool = False, pressed: bool = False):
        painter.save()
        shape = QPainterPath()
        cx, cy = rect.center().x(), rect.center().y()
        size = min(rect.width(), rect.height()) * 0.78
        half_height = size * 0.60
        if command == "toggle_play_pause" and self._display_playing_state():
            bar = size * 0.27
            for left in (cx - size * 0.48, cx + size * 0.21):
                shape.addRoundedRect(
                    QRectF(left, cy - half_height, bar, half_height * 2.0),
                    bar * 0.30, bar * 0.30,
                )
        else:
            direction = -1.0 if command == "previous" else 1.0
            optical_x = cx + (size * 0.04 if command == "toggle_play_pause" else 0.0)
            tip = size * (0.48 if command in {"previous", "next"} else 0.58)
            base = size * (0.42 if command in {"previous", "next"} else 0.46)
            triangle = self._rounded_polygon_path((
                QPointF(optical_x + direction * tip, cy),
                QPointF(optical_x - direction * base, cy - half_height),
                QPointF(optical_x - direction * base, cy + half_height),
            ), size * 0.20)
            shape.addPath(triangle)
            if command in {"previous", "next"}:
                line_x = cx + direction * size * 0.57
                stem = size * 0.22
                shape.addRoundedRect(
                    QRectF(line_x - stem / 2.0, cy - half_height, stem, half_height * 2.0),
                    stem * 0.30, stem * 0.30,
                )
        # Use the window-mode button's neutral glass inside the existing
        # silhouette. Foreground ink must not add a dark/white face over it.
        # Pause bars, triangles, stems and broad pointer targets stay intact.
        image = self._surface_glass_backdrop()
        level = self._surface_glass_level()
        paint_liquid_glass(painter, shape.boundingRect(), path=shape,
                           light=self._surface_glass_light(), refractive=True,
                           hovered=hovered, pressed=pressed,
                           material_level=level, backdrop=image,
                           backdrop_rect=self._surface_glass_source_rect(shape.boundingRect(), image),
                           cache_token=(id(self), "transport", command),
                           material_owner=self, material_role=("transport", command),
                           material_scene=self._visual_track_identity,
                           material_dirty=painter.worldTransform().mapRect(shape.boundingRect()))
        painter.restore()

    @staticmethod
    @lru_cache(maxsize=2)
    def _transport_light_gradient(gold: bool = False) -> QRadialGradient:
        """One normalized profile, rasterized at the actual display resolution."""
        light = QRadialGradient(QPointF(0.0, 0.0), 1.0)
        if gold:
            # This one-off confirmation wave is roughly twice as thick
            # as the ordinary artwork ripple, with a warm radial blend.
            for stop, color in (
                (0.0, QColor(190, 128, 20, 0)),
                (0.53, QColor(190, 128, 20, 0)),
                (0.58, QColor(204, 139, 27, 10)),
                (0.62, QColor(218, 158, 36, 25)),
                (0.66, QColor(232, 181, 49, 48)),
                (0.71, QColor(245, 209, 87, 68)),
                (0.77, QColor(255, 234, 150, 80)),
                (0.80, QColor(255, 246, 187, 84)),
                (0.84, QColor(253, 223, 110, 77)),
                (0.89, QColor(234, 183, 55, 57)),
                (0.94, QColor(210, 146, 27, 37)),
                (0.98, QColor(188, 122, 18, 12)),
                (1.0, QColor(188, 122, 18, 0)),
            ):
                light.setColorAt(stop, color)
        else:
            # Keep a readable thick crest without washing across half of
            # the music surface. The shoulders stay translucent.
            light.setColorAt(0.0, QColor(255, 255, 255, 0))
            light.setColorAt(0.66, QColor(255, 255, 255, 0))
            light.setColorAt(0.69, QColor(239, 225, 255, 10))
            light.setColorAt(0.73, QColor(246, 234, 255, 32))
            light.setColorAt(0.77, QColor(250, 244, 255, 65))
            light.setColorAt(0.80, QColor(255, 252, 255, 78))
            light.setColorAt(0.83, QColor(255, 255, 255, 70))
            light.setColorAt(0.87, QColor(250, 251, 255, 45))
            light.setColorAt(0.91, QColor(236, 240, 255, 16))
            light.setColorAt(0.94, QColor(218, 226, 255, 0))
            light.setColorAt(1.0, QColor(218, 226, 255, 0))
        return light

    def _cached_transport_light_sprite(self, *, gold: bool = False) -> QImage:
        """Small reference raster for previews; live waves use the native profile."""
        cached = self._obo_reveal_light_sprite if gold else self._transport_light_sprite
        if not cached.isNull():
            return cached
        side = self.TRANSPORT_LIGHT_SPRITE_SIZE
        image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as layer:
            layer.translate(side * 0.5, side * 0.5)
            layer.scale(side * 0.5, side * 0.5)
            layer.fillRect(QRectF(-1.0, -1.0, 2.0, 2.0), self._transport_light_gradient(gold))
        if gold:
            self._obo_reveal_light_sprite = image
        else:
            self._transport_light_sprite = image
        return image

    def _paint_transport_spatial_light(self, painter: QPainter, rect: QRectF, now: float):
        if not self._transport_feedback:
            return
        origin = self._transport_light_origin(now)
        max_radius = self._transport_light_max_radius(origin, now)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        space = QPainterPath()
        space.addRect(rect)
        cover = self._scaled_cover_rect(self._current_cover_rect(now), self._cover_scale_at(now))
        occluder = QPainterPath()
        occluder.addRoundedRect(cover, cover.width() * 0.055, cover.height() * 0.055)
        reveal = self._control_reveal_at(now)
        if self._library_back_available() and reveal > 0.001:
            face = self._library_button_face(rect, self._library_expansion_at(now))
            face = self._hover_control_rect("library", face, {"library": face}, now).translated(self._library_control_shift(rect, reveal))
            occluder.addRoundedRect(face, face.height() / 2, face.height() / 2)
        dj_reveal = self._personal_dj_reveal_at(now)
        if dj_reveal > .001:
            occluder.addPath(self._personal_dj_hit_path(rect, now))
        # Transparent/absent artwork still owns its foreground slot. Rings
        # illuminate only the space behind it, never the thumbnail or plate.
        painter.setClipPath(space.subtracted(occluder), Qt.ClipOperation.IntersectClip)
        for command, (started, _button_center, _scale, duration) in self._transport_feedback.items():
            if command == "obo_reveal" and self._reading_strategy() != "gold":
                continue
            frame = self._transport_feedback_frame(now - started, duration)
            if frame is None or frame[1] <= 0.0 or frame[2] <= 0.0:
                continue
            radius = max_radius * frame[1]
            painter.setOpacity(frame[2])
            # Never enlarge a fixed 384 px circle into thousands of display
            # pixels. Native radial sampling keeps the crest round at every
            # radius/DPR and avoids a cold, full-screen sprite allocation.
            painter.save()
            painter.translate(origin)
            painter.scale(radius, radius)
            painter.fillRect(QRectF(-1.0, -1.0, 2.0, 2.0),
                             self._transport_light_gradient(command == "obo_reveal"))
            painter.restore()
        painter.restore()

    def _paint_media_controls(
        self, painter: QPainter, rect: QRectF, primary: QColor, secondary: QColor,
        *, paint_ink: bool = True,
    ):
        # The panel remains a layout/hit-test region, but has no visible plate.
        panel = QRectF(rect)
        scale = self._surface_scale()
        panel.setHeight(min(self.TRANSPORT_PANEL_HEIGHT * scale, panel.height()))
        panel.setWidth(min(350.0 * scale, panel.width()))
        self._media_control_panel_rect = QRectF(panel)
        slot = panel.width() / 3.0
        commands = ("previous", "toggle_play_pause", "next")
        now = time.monotonic()
        reveal = self._control_reveal_at(now)
        shift = self._transport_control_shift(panel, reveal)
        hover_group = self._transport_hover_group(panel)
        self._media_button_rects = {}
        for index, command in enumerate(commands):
            button = QRectF(panel.left() + index * slot, panel.top(), slot, panel.height())
            if reveal >= 0.75:
                self._media_button_rects[command] = self._hover_control_rect("media:" + command, button, hover_group, now).translated(shift)
        if not paint_ink:
            return
        accent = self._scene_control_ink()
        feedback = {}
        if reveal > 0.001:
            for command in ("previous", "next"):
                state = self._transport_feedback.get(command)
                if state is None:
                    continue
                # At the exact initial pose the glyph has not moved. Keep
                # its native cached compositing path until motion begins,
                # avoiding a one-frame premultiplied-alpha rounding change.
                if now <= state[0]:
                    continue
                icon_seconds = state[3] * self.TRANSPORT_FEEDBACK_SECONDS / (
                    self.TRANSPORT_LIGHT_EXPAND_SECONDS + self.TRANSPORT_LIGHT_FADE_SECONDS
                )
                if now - state[0] >= icon_seconds:
                    continue
                frame = self._transport_feedback_frame(now - state[0], state[3])
                if frame is not None:
                    feedback[command] = (frame, state)
        active_commands = tuple(command for command in commands if command in feedback)
        def draw(
            layer: QPainter, *, hovered_command: str = self._hovered_media_command,
            pressed_command: str = self._pressed_media_command,
            animated_commands: tuple[str, ...] = active_commands,
            use_hover_pose: bool = True,
        ):
            for index, command in enumerate(commands):
                if command in animated_commands:
                    continue
                button = QRectF(panel.left() + index * slot, panel.top(), slot, panel.height())
                if use_hover_pose:
                    button = self._hover_control_rect("media:" + command, button, hover_group, now)
                icon_color = QColor(accent)
                if pressed_command == command:
                    icon_color.setAlpha(round(icon_color.alpha() * 0.70))
                elif hovered_command == command:
                    icon_color.setAlpha(255)
                growth = button.height() / max(.001, panel.height())
                icon_side = min(44.0 * scale, panel.height() - 12.0 * scale) * growth
                icon_rect = QRectF(
                    button.center().x() - icon_side / 2.0,
                    button.center().y() - icon_side / 2.0,
                    icon_side,
                    icon_side,
                )
                self._paint_transport_icon(layer, icon_rect, command, icon_color,
                                           hovered=hovered_command == command,
                                           pressed=pressed_command == command)

        def draw_default(layer: QPainter):
            draw(layer, hovered_command="", pressed_command="", animated_commands=(), use_hover_pose=False)

        self._paint_control_layer(
            painter, panel, reveal, shift,
            (
                "transport", round(scale, 3), accent.rgba(),
                self._display_playing_state(), self._hovered_media_command,
                self._pressed_media_command, active_commands, self._control_hover_pose_key(hover_group, now),
            ), draw,
            prewarm_default=(
                ("transport", round(scale, 3), accent.rgba(), self._display_playing_state(), "", "", (), ()),
                draw_default,
            ),
        )
        for command in active_commands:
            frame, _state = feedback[command]
            index = commands.index(command)
            button = QRectF(panel.left() + index * slot, panel.top(), slot, panel.height())
            button = self._hover_control_rect("media:" + command, button, hover_group, now)
            growth = button.height() / max(.001, panel.height())
            icon_side = min(44.0 * scale, panel.height() - 12.0 * scale) * growth
            icon_rect = QRectF(
                button.center().x() - icon_side / 2.0,
                button.center().y() - icon_side / 2.0,
                icon_side, icon_side,
            )
            icon_color = QColor(accent)
            if self._hovered_media_command == command:
                icon_color.setAlpha(255)
            painter.save()
            painter.setOpacity(reveal)
            painter.translate(shift)
            painter.translate(icon_rect.center())
            painter.scale(frame[0], frame[0])
            painter.translate(-icon_rect.center())
            self._paint_transport_icon(painter, icon_rect, command, icon_color,
                                       hovered=self._hovered_media_command == command,
                                       pressed=self._pressed_media_command == command)
            painter.restore()

    def _edge_set(self, point: QPointF) -> set[str]:
        if self._fullscreen:
            return set()
        margin = float(self.RESIZE_MARGIN)
        edges: set[str] = set()
        if point.x() <= margin:
            edges.add("left")
        elif point.x() >= self.width() - margin:
            edges.add("right")
        if point.y() <= margin:
            edges.add("top")
        elif point.y() >= self.height() - margin:
            edges.add("bottom")
        return edges

    def mousePressEvent(self, event):
        if self._compact_committing:
            event.accept()
            return
        self._reset_window_compact()
        self._song_collection_press_track = self._visual_track_identity()
        self._song_collection_press_authority = self._capture_song_collection_authority(self._song_collection_press_track)
        panel = self._song_playlist_panel
        if panel is not None and panel.isVisible() and not self._song_collection_geometry(QRectF(self.rect())).contains(event.position()):
            self._dismiss_song_playlist_panel()
        self._queue_pressed = None
        self._queue_pressed_token = None
        self._pressed_dj_toggle_ready = False
        if self._audio_output_panel is not None and self._audio_output_panel.isVisible():
            output_button = self._button_rects.get("audio_output", QRectF())
            if not output_button.contains(event.position()):
                self._dismiss_audio_output_panel()
        controls_clickable = self._control_reveal_at(time.monotonic()) >= 0.75
        self._note_pointer_moved()
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        if (self._pending_fullscreen is not None or self._island_returning) and not self._geometry_transition_snapshot.isNull():
            self._moving = False
            self._resize_edges = set()
            self._pressed_media_command = ""
            self._pressed_window_button = next((
                key for key, rect in self._transition_control_rects().items()
                if (self._transition_controls_clickable() and key in {"mode", "return", "library"})
                and self._transition_window_control_contains(key, rect, event.position())
            ), "")
            self._pressed_lyric_index = None
            event.accept()
            return
        if self._effective_content_layout() == "playlist" and self._queue_snapshot is not None:
            self._queue_pressed = next((index for index, rect in self._queue_hit_rects.items()
                                       if rect.contains(event.position())), None)
            if self._queue_pressed is not None:
                # Retain the generation the listener actually pressed. A
                # repaint while held may already show a replacement queue.
                self._queue_pressed_token = self._queue_snapshot[0]
        self._resize_edges = self._edge_set(event.position())
        self._drag_origin = event.globalPosition().toPoint()
        self._drag_geometry = QRect(self.geometry())
        self._pressed_window_button = next((key for key, rect in self._button_rects.items()
                                            if self._window_button_clickable(key, controls_clickable) and self._window_control_contains(key, rect, event.position())), "")
        if self._pressed_window_button == "personal_dj":
            self._resize_edges = set()
            now = time.monotonic()
            self._pressed_dj_toggle_ready = (self._dj_voice_selector_active
                and self._dj_voice_selector_motion.value(now) >= .999
                and self._personal_dj_icon_contains(event.position(), now))
        if self._pressed_window_button == "library":
            self._resize_edges = set()
        self._pressed_media_command = next((command for command, rect in self._media_button_rects.items() if rect.contains(event.position())), "") if controls_clickable else ""
        self._pressed_lyric_index = next((index for index, rect in self._lyric_hit_rects.items() if rect.contains(event.position())), None)
        over_button = bool(self._pressed_window_button or self._pressed_media_command or self._pressed_lyric_index is not None or self._queue_pressed is not None)
        self._moving = not self._fullscreen and not self._resize_edges and not over_button and event.position().y() <= self.TITLE_BAR_HEIGHT
        event.accept()

    def mouseMoveEvent(self, event):
        if self._compact_committing:
            event.accept()
            return
        self._note_pointer_moved()
        self._update_metadata_hover(
            event.position() if not event.buttons() else None, time.monotonic(),
        )
        if (self._pending_fullscreen is not None or self._island_returning) and not self._geometry_transition_snapshot.isNull():
            self._hovered_lyric_index = None
            self._hovered_window_button = ""
            self._set_library_expanded(False, time.monotonic())
            self._hovered_media_command = ""
            self._set_control_hover("", "", time.monotonic())
            self._pressed_media_command = ""
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                self._pressed_window_button = ""
            self._pressed_lyric_index = None
            self._moving = False
            self._resize_edges = set()
            self.unsetCursor()
            event.accept()
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            hover = next((index for index, rect in self._lyric_hit_rects.items() if rect.contains(event.position())), None)
            if hover != self._hovered_lyric_index:
                self._hovered_lyric_index = hover
                self.update()
            if hover is not None:
                self._set_control_hover("", "", time.monotonic())
                if self._hovered_window_button or self._hovered_media_command:
                    self._hovered_window_button = self._hovered_media_command = ""
                    self._set_library_expanded(False, time.monotonic())
                    self.update()
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                return
            controls_hoverable = self._control_reveal_at(time.monotonic()) >= 0.75
            window_button = next((key for key, rect in self._button_rects.items()
                                  if self._window_button_clickable(key, controls_hoverable) and self._window_control_contains(key, rect, event.position())), "")
            media_button = next((key for key, rect in self._media_button_rects.items() if rect.contains(event.position())), "") if controls_hoverable else ""
            self._set_control_hover(window_button, media_button, time.monotonic())
            if window_button != self._hovered_window_button or media_button != self._hovered_media_command:
                self._hovered_window_button = window_button
                self._hovered_media_command = media_button
                self._set_library_expanded(window_button == "library", time.monotonic())
                self.setToolTip({"mode": "切换到窗口", "return": "返回岛模式", "library": "登录" if self._library_login_required() else "返回音乐库",
                                 "playback_mode": self._playback_mode_tooltip(), "personal_dj": self._personal_dj_tooltip(),
                                 "content_layout": self._content_layout_tooltip(),
                                 "favorite": self._song_collection_tooltip("favorite"),
                                 "playlist_add": self._song_collection_tooltip("playlist_add")}.get(window_button, ""))
                self.update()
            if window_button or media_button:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                return
            edges = self._edge_set(event.position())
            if edges in ({"left", "top"}, {"right", "bottom"}):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif edges in ({"right", "top"}, {"left", "bottom"}):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            elif edges & {"left", "right"}:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif edges & {"top", "bottom"}:
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            else:
                self.unsetCursor()
            return super().mouseMoveEvent(event)
        delta = event.globalPosition().toPoint() - self._drag_origin
        if self._moving:
            self.move(self._drag_geometry.topLeft() + delta)
            return
        if not self._resize_edges:
            return
        original = QRect(self._drag_geometry)
        requested_width = original.width() + (delta.x() if "right" in self._resize_edges else -delta.x() if "left" in self._resize_edges else 0)
        requested_height = original.height() + (delta.y() if "bottom" in self._resize_edges else -delta.y() if "top" in self._resize_edges else 0)
        available = self._available_geometry()
        # Each edge preserves its opposite anchor. Overscroll only becomes
        # a presentation gesture after both real dimensions reach minimum.
        minimum = self._minimum_window_size()
        if not self._compact_snapshot.isNull():
            self._update_window_compact(requested_width, requested_height)
            if not self._compact_snapshot.isNull():
                event.accept()
                return
        size = QSize(max(minimum.width(), min(available.width(), requested_width)),
                     max(minimum.height(), min(available.height(), requested_height)))
        x = original.right() - size.width() + 1 if "left" in self._resize_edges else original.left()
        y = original.bottom() - size.height() + 1 if "top" in self._resize_edges else original.top()
        self._content_layout_user_resizing = True
        try:
            self.setGeometry(x, y, size.width(), size.height())
        finally:
            self._content_layout_user_resizing = False
        self._update_window_compact(requested_width, requested_height)

    def mouseReleaseEvent(self, event):
        if self._compact_committing:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._release_window_compact()
        controls_clickable = self._control_reveal_at(time.monotonic()) >= 0.75
        self._note_pointer_moved()
        if (self._pending_fullscreen is not None or self._island_returning) and not self._geometry_transition_snapshot.isNull():
            released = next((
                key for key, rect in self._transition_control_rects().items()
                if (self._transition_controls_clickable() and key in {"mode", "return", "library"})
                and self._transition_window_control_contains(key, rect, event.position())
            ), "")
            pressed = self._pressed_window_button
            self._moving = False
            self._resize_edges = set()
            self._pressed_media_command = ""
            self._pressed_window_button = ""
            self._pressed_lyric_index = None
            self._queue_pressed = None
            self._queue_pressed_token = None
            if event.button() == Qt.MouseButton.LeftButton and pressed == released:
                if released == "mode":
                    self.toggle_presentation_mode()
                elif released == "return":
                    self.return_to_island()
                elif released == "library":
                    self.return_to_library()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and not self._moving and not self._resize_edges:
            point = event.position()
            release_window_button = next((key for key, rect in self._button_rects.items()
                                          if self._window_button_clickable(key, controls_clickable) and self._window_control_contains(key, rect, point)), "")
            release_media_command = next((command for command, rect in self._media_button_rects.items() if rect.contains(point)), "") if controls_clickable else ""
            release_lyric_index = next((index for index, rect in self._lyric_hit_rects.items() if rect.contains(point)), None)
            pressed_window_button = str(getattr(self, "_pressed_window_button", "") or "")
            pressed_media_command = str(getattr(self, "_pressed_media_command", "") or "")
            pressed_lyric_index = getattr(self, "_pressed_lyric_index", None)
            if pressed_window_button == "library" and release_window_button == "library":
                self.return_to_library()
            elif pressed_window_button == "return" and release_window_button == "return":
                self.return_to_island()
            elif pressed_window_button == "mode" and release_window_button == "mode":
                self.toggle_presentation_mode()
            elif pressed_window_button == "translation" and release_window_button == "translation":
                self._toggle_translation()
            elif pressed_window_button == "playback_mode" and release_window_button == "playback_mode":
                self._cycle_owned_playback_mode()
            elif pressed_window_button == "content_layout" and release_window_button == "content_layout":
                self._cycle_content_layout()
            elif pressed_window_button in {"favorite", "playlist_add"} and release_window_button == pressed_window_button:
                self._activate_song_collection(pressed_window_button, self._song_collection_press_track,
                                               self._song_collection_press_authority)
                self._song_collection_press_authority = None
            elif pressed_window_button == "personal_dj" and release_window_button == "personal_dj":
                self._activate_personal_dj(event.position(), toggle_ready=self._pressed_dj_toggle_ready)
            elif pressed_window_button == "audio_output" and release_window_button == "audio_output":
                self._toggle_audio_output_panel()
            elif pressed_lyric_index is not None and release_lyric_index == pressed_lyric_index:
                self._seek_to_lyric(pressed_lyric_index)
            elif pressed_media_command and release_media_command == pressed_media_command:
                self._start_transport_feedback(pressed_media_command)
                self._send_media_command(pressed_media_command)
            elif (self._queue_pressed is not None and self._queue_pressed_token is not None
                    and self._effective_content_layout() == "playlist"
                    and self._queue_hit_rects.get(self._queue_pressed, QRectF()).contains(point)):
                self._select_playback_queue(self._queue_pressed, self._queue_pressed_token)
        self._moving = False
        self._resize_edges = set()
        self._pressed_media_command = ""
        self._pressed_window_button = ""
        self._pressed_dj_toggle_ready = False
        self._pressed_lyric_index = None
        self._queue_pressed = None
        self._queue_pressed_token = None
        if self._pending_fullscreen is None and not self._island_returning and not self._fullscreen and self.geometry().isValid():
            self._normal_geometry = QRect(self._compact_base_geometry if self._compact_base_geometry.isValid() else self.geometry())
        event.accept()

    def wheelEvent(self, event):
        if not self._compact_snapshot.isNull():
            event.accept()
            return
        self._note_pointer_moved()
        if self._effective_content_layout() == "playlist" and self._queue_view_rect.contains(event.position()):
            self._scroll_playback_queue(event)
            event.accept(); return
        _lines, timed = self._display_lyrics()
        if not timed or not self._lyric_clip_rect.contains(event.position()):
            return super().wheelEvent(event)
        now = time.monotonic()
        frame = self._lyric_frame(timed, now)
        anchor, _focus, manual_view = self._lyric_view_anchor(frame.active_index, len(timed), now)
        origin = anchor if manual_view else frame.scroll_index
        delta = event.angleDelta().y() / 120.0
        if delta:
            self._browse_index = max(0.0, min(float(len(timed) - 1), origin - delta))
            self._browse_until = now + self.LYRIC_BROWSE_IDLE_SECONDS
            self._browse_return_from = None
            self._browse_return_started_at = 0.0
            self.update()
        event.accept()

    def _seek_to_lyric(self, index: int):
        _lines, timed = self._display_lyrics()
        if self._seek_pending or not 0 <= index < len(timed):
            return
        offset = getattr(self.owner, "_effective_lyric_offset_seconds", None)
        lyric_offset = 0.0
        if callable(offset):
            try:
                lyric_offset = float(offset(include_output_latency=False))
            except TypeError:
                # Older owners expose the original no-argument offset API.
                lyric_offset = float(offset())
        seconds = max(0.0, float(timed[index][0]) - lyric_offset)
        track_key = self._browse_track_key
        provider = getattr(self.owner, "data_provider", None)
        seek = getattr(provider, "seek_playback_position", None)
        if not callable(seek):
            self._clear_lyric_browse()
            self._hovered_lyric_index = None
            self.update()
            return
        # Selecting a line ends the temporary clear browsing presentation
        # immediately. The fenced asynchronous command still controls whether
        # a seek target is accepted; the viewport follows the real clock.
        self._clear_lyric_browse()
        self._hovered_lyric_index = None
        self.update()
        self._seek_pending = True
        self._seek_request_source_app = str(getattr(self.owner, "media_source_app", "") or "")

        def request():
            try:
                try:
                    result = bool(seek(seconds, expected_track_id=track_key))
                except TypeError:
                    # Third-party/fake providers may still implement the
                    # original one-argument contract.
                    result = bool(seek(seconds))
            except Exception:
                result = False
            self.seekFinished.emit(result, seconds, track_key)

        background = getattr(self.owner, "_run_background", None)
        if callable(background):
            background("LyricSeek", request)
        else:
            request()

    @Slot(bool, float, str)
    def _on_seek_finished(self, succeeded: bool, seconds: float, track_key: str):
        if self._closing_for_shutdown:
            return
        self._seek_pending = False
        request_app = self._seek_request_source_app
        self._seek_request_source_app = None
        # Seek completion is asynchronous and must not change normal Windows
        # stacking. Any temporary player reveal restores the prior foreground
        # window inside the provider before this callback is delivered.
        source_app = str(getattr(self.owner, "media_source_app", "") or "")
        if track_key != self._browse_track_key or request_app not in {None, source_app}:
            return
        # A failed player seek must not leave the viewport permanently frozen
        # at its manual browse anchor. Resume from the actual media clock; only
        # a verified provider success is allowed to publish the target time.
        self._clear_lyric_browse()
        self._hovered_lyric_index = None
        if succeeded and self.owner is not None:
            # Provider success only confirms that the command was accepted.
            # Keep following the real clock, but remember the target long
            # enough for an asynchronous player readback to arrive.
            self._pending_backward_seek = (float(seconds), time.monotonic() + 5.0)
            self._pending_backward_seek_app = source_app
            self.owner.last_media_refresh_at = 0.0
        self.update()

    def enterEvent(self, event):
        self._note_pointer_moved()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._update_metadata_hover(None, time.monotonic())
        self._restore_idle_cursor()
        self._hovered_window_button = ""
        self._set_library_expanded(False, time.monotonic())
        self._hovered_media_command = ""
        self._set_control_hover("", "", time.monotonic())
        self._pressed_media_command = ""
        self._pressed_window_button = ""
        self._pressed_lyric_index = None
        self.unsetCursor()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event):
        if not self._compact_snapshot.isNull():
            event.accept()
            return
        controls_clickable = self._control_reveal_at(time.monotonic()) >= 0.75
        self._note_pointer_moved()
        if (self._pending_fullscreen is not None or self._island_returning) and not self._geometry_transition_snapshot.isNull():
            over_button = any(
                self._transition_controls_clickable() and self._transition_window_control_contains(key, rect, event.position())
                for key, rect in self._transition_control_rects().items()
            )
            if (event.button() == Qt.MouseButton.LeftButton and not over_button
                    and 0.0 < event.position().y() <= self._transition_title_fraction() * self.height()):
                self.toggle_presentation_mode()
            event.accept()
            return
        over_button = controls_clickable and any(self._window_control_contains(key, rect, event.position()) for key, rect in self._button_rects.items())
        if event.button() == Qt.MouseButton.LeftButton and over_button:
            event.accept()
            return
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._fullscreen
            and event.position().y() <= self.TITLE_BAR_HEIGHT
        ):
            self.toggle_presentation_mode()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        self._note_pointer_moved()
        if self._compact_committing:
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and not self._compact_snapshot.isNull():
            self._moving = False
            self._resize_edges = set()
            self._release_window_compact()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            if self._song_playlist_panel is not None and self._song_playlist_panel.isVisible():
                self._dismiss_song_playlist_panel()
                return
            if self._dj_voice_selector_active:
                self._close_dj_voice_selector()
                return
            if self._audio_output_panel is not None and self._audio_output_panel.isVisible():
                self._dismiss_audio_output_panel()
                return
            if not self.return_to_library():
                self.return_to_island()
            return
        command = {Qt.Key.Key_Space: "toggle_play_pause", Qt.Key.Key_Left: "previous", Qt.Key.Key_Right: "next"}.get(event.key())
        if command:
            self._send_media_command(command)
            return
        super().keyPressEvent(event)
