"""Native song-page content choices, glyphs and fenced queue presentation."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QTransform

from local.liquid_glass import paint_liquid_glass
from local.glass_style import GLASS_SELECTED_INK
from local.music_lyric_motion import LogicalLineSpringChain
from local.music_lyric_presentation import INTRO_COUNTDOWN_TEXT
from local.music_lyric_fit import fits as lyric_column_fits
from local.artwork_skeleton import paint_artwork_skeleton
from local.music_width_transition import WidthMarks, WidthTransition
from local.spring_motion import FlickScroll


CONTENT_LAYOUTS = ("cover", "cover_lyrics", "lyrics", "playlist")

# Hidden wide controls use this gain. "current" asks for 30% and the lyric
# lane caps the artwork at about 11%. "large" and "none" stay comparable.
LAYOUT_G_COVER_GROWTH_MODE = "current"
LAYOUT_G_COVER_GROWTH_FACTORS = {"none": 0.0, "current": 0.30, "large": 0.50}
# Resting wide cover before layout G shrank it with a 0.44 height cap.
# 37.8u is the side the window corner and the artwork shared.
LAYOUT_G_COVER_SIDE_UNITS = 37.8


def resting_cover_side(width: float, height: float) -> float:
    """Previous wide cover side. Narrow and wide windows share this corner ratio."""
    width = max(1.0, float(width))
    height = max(1.0, float(height))
    unit = min(width / 100.0, height / 75.8)
    return max(1.0, min(unit * LAYOUT_G_COVER_SIDE_UNITS, width * 0.42, height * 0.58))


def queue_selection_radius(height: float) -> float:
    """A clearly rounded card (20% of the row), not a capsule end cap.

    5.5% read as a square box on a 52 px row.
    """
    height = max(0.0, float(height))
    return max(2.0, min(height * 0.5, height * 0.20))


NARROW_HEADER_HIDDEN_SIDE = 40.0


def narrow_header_cover(stacked_content: QRectF, side: float, margin: float,
                        hidden: float) -> QRectF:
    """The stacked header cover: full size below the tools, small at the top.

    ``hidden`` is 0 with the controls shown and 1 once they have left. The
    cover keeps its left edge, shrinks to about 40 px and rises into the band
    the top tools free, the way the narrow demo's option A pins it.
    """
    hidden = max(0.0, min(1.0, float(hidden)))
    small = min(float(side), NARROW_HEADER_HIDDEN_SIDE)
    now_side = float(side) + (small - float(side)) * hidden
    top = stacked_content.top() + (max(12.0, margin * 0.8) - stacked_content.top()) * hidden
    return QRectF(stacked_content.left(), top, now_side, now_side)


def playlist_column_for_cover(surface: QRectF, cover: QRectF, column: QRectF, *, scale: float) -> QRectF:
    """Fill the surface when the album cover is gone.

    A small inset keeps the panel's rounded corners off the window edge,
    where the corner mask would otherwise clip them flat.
    """
    inset = max(8.0, 10.0 * float(scale))
    full = QRectF(surface).adjusted(inset, inset, -inset, -inset)
    cover_gone = (
        not cover.isValid() or cover.width() <= 1.0 or cover.height() <= 1.0
        or not cover.intersects(surface) or cover.bottom() <= surface.top() + 1.0
    )
    if cover_gone and full.width() > 1.0 and full.height() > 1.0:
        return full
    column = QRectF(column)
    gap = 24.0 * float(scale)
    if cover.isValid() and cover.bottom() + gap < column.top() - 0.5:
        new_top = max(surface.top() + inset, cover.bottom() + gap)
        if new_top < column.top():
            column.setTop(new_top)
    return column
CONTENT_LAYOUT_LABELS = ("仅专辑", "封面歌词", "专辑与歌词", "专辑与播放列表")


def normalize_content_layout(value):
    return value if isinstance(value, str) and value in CONTENT_LAYOUTS else "lyrics"


def wide_narrow_easing():
    """cubic-bezier(0.28, 0, 0.72, 1). QVariantAnimation stays on Qt's 60 Hz clock."""
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.28, 0.0), QPointF(0.72, 1.0), QPointF(1.0, 1.0))
    return curve


@dataclass(frozen=True)
class LayoutGBox:
    """One axis-aligned box. A spring can lerp x, y, w and h on their own."""

    x: float
    y: float
    w: float
    h: float

    def as_rect(self) -> QRectF:
        return QRectF(self.x, self.y, self.w, self.h)


@dataclass(frozen=True)
class LayoutGTargets:
    """Resting wide layout G. Numbers only: cover, names and the glass card share one axis.

    The card holds the progress track and the transport buttons on one plate.
    Pass the same font measurements the window used, and the boxes are the
    animation targets for a wide/narrow spring.
    """

    unit: float
    cover: LayoutGBox
    title: LayoutGBox
    artist: LayoutGBox
    card: LayoutGBox
    track: LayoutGBox
    time_row: LayoutGBox
    buttons: LayoutGBox
    axis: float
    play_side: float
    skip_side: float
    title_size: float
    artist_size: float


# The card's 8 px rhythm is specified on a 960 x 540 window (unit 540/75.8)
# and scales with the window like everything else in layout G.
LAYOUT_G_REFERENCE_UNIT = 540.0 / 75.8
LAYOUT_G_CARD_PAD = 16.0
LAYOUT_G_TRACK_BOX = 6.0         # the bar is 3 px, 6 px under the pointer
LAYOUT_G_TRACK_TO_TIME = 6.0
LAYOUT_G_TIME_PX = 11.0
LAYOUT_G_TIME_TO_KEYS = 12.0
LAYOUT_G_PLAY_ICON = 30.0        # a ~28 px play glyph (the glyph fills ~94%)
LAYOUT_G_SKIP_ICON = 21.4        # ~20 px previous/next glyphs
LAYOUT_G_KEY_GAP = 1.5           # gap between keys, in play-icon widths
LAYOUT_G_ARTIST_RATIO = 0.6      # artist size against the title size


def layout_g_scale(unit: float) -> float:
    return max(0.01, float(unit)) / LAYOUT_G_REFERENCE_UNIT


def layout_g_time_px(unit: float) -> float:
    return LAYOUT_G_TIME_PX * layout_g_scale(unit)


def layout_g_targets(
    width: float,
    height: float,
    *,
    title_font_height: float,
    title_font_pixel_size: float,
    artist_font_height: float,
    artist_font_pixel_size: float,
    time_font_height: float,
    title_emphasis: float = 1.15,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    control_reveal: float = 1.0,
    cover_growth_mode: str | None = None,
) -> LayoutGTargets:
    """Place layout G from the window size and three measured font heights.

    u = min(width/100, height/75.8). The group is centered, then clamped
    inside max(18, 5u). In the resting pose the cover's left inset equals
    its top inset, so a wide or fullscreen sleeve is not stuck to the window
    edge. ``control_reveal`` 1 is the resting pose a width spring should aim
    at. 0 uses the existing 18% title gain and lets the cover take space the
    transport card has left, without crossing the lyric lane. Large mode
    trades some left padding for more artwork. Nothing here reads a widget,
    a cache, or the clock. Omitted mode uses the constant.
    """
    width = max(1.0, float(width))
    height = max(1.0, float(height))
    unit = min(width / 100.0, height / 75.8)
    margin = max(18.0, unit * 5.0)
    expansion = 1.0 - max(0.0, min(1.0, float(control_reveal)))
    mode = LAYOUT_G_COVER_GROWTH_MODE if cover_growth_mode is None else cover_growth_mode
    growth = LAYOUT_G_COVER_GROWTH_FACTORS[mode]
    side = resting_cover_side(width, height)
    title_gain = 1.0 + 0.18 * expansion
    title_size = 3.1 * unit * float(title_emphasis) * title_gain
    layout_title_size = 3.1 * unit * title_gain
    title_text = float(title_font_height) * layout_title_size / max(1.0, float(title_font_pixel_size))
    title_h = max(3.4 * unit, title_text + 2.0 * unit)
    artist_size = LAYOUT_G_ARTIST_RATIO * title_size
    artist_text = float(artist_font_height) * artist_size / max(1.0, float(artist_font_pixel_size))
    artist_h = max(2.2 * unit, artist_text + unit)
    artist_gap = 0.1 * artist_size
    meta_h = title_h + artist_gap + artist_h
    k = layout_g_scale(unit)
    pad_top = pad_x = pad_bot = LAYOUT_G_CARD_PAD * k
    progress_h = LAYOUT_G_TRACK_BOX * k
    time_gap = LAYOUT_G_TRACK_TO_TIME * k
    time_h = max(LAYOUT_G_TIME_PX * k, float(time_font_height))
    button_gap = LAYOUT_G_TIME_TO_KEYS * k
    play_side = LAYOUT_G_PLAY_ICON * k
    card_h = pad_top + progress_h + time_gap + time_h + button_gap + play_side + pad_bot
    gap_cover, gap_card = unit * 1.35, unit * 1.55
    resting_total = side + gap_cover + meta_h + gap_card + card_h
    inset = (height - resting_total) / 2.0
    inset = max(margin, min(inset, height - resting_total - margin))
    # Match the top inset unless that would push the sleeve into the lyric lane.
    lane = (6.8 + 37.8) * unit
    left = min(inset, max(0.0, lane - side))
    resting_side = side
    if expansion > 0.0:
        width_cap, height_cap = 0.42, 0.58
        # The restored cover already meets the lyric lane. Reclaim a little
        # left padding so hiding the controls can still grow the art.
        hidden_left = min(left, max(8.0, unit * 1.15))
        if mode == "large":
            hidden_left = min(left, max(8.0, unit * 1.2))
            width_cap, height_cap = 0.46, 0.66
        left += (hidden_left - left) * expansion
        # The old lane cap landed near 11% for every font. Growing into the
        # font-driven left inset instead made the same window about 11% on
        # Linux and about 15% on Windows. Keep that 11% cap, and still stop
        # at the lyric lane.
        gain = 0.0 if growth <= 0.0 else min(growth, 0.11)
        room = max(resting_side, lane - left)
        side = min(
            resting_side * (1.0 + gain * expansion),
            room,
            width * width_cap,
            max(resting_side, height * height_cap),
        )
    total = side + gap_cover + meta_h + gap_card + card_h
    top = (height - total) / 2.0
    top = max(margin, min(top, height - total - margin))
    if expansion > 0.0 and side > resting_side:
        # The left inset always matches the top inset. A grown sleeve that
        # would then reach the lyric lane gives up size, not left padding.
        # Solve lane - side = (height - side - rest) / 2 for the side where the
        # left inset (to the lane) equals the centred top inset.
        rest = gap_cover + meta_h + gap_card + card_h
        if lane - side < top:
            side = max(resting_side, min(side, 2.0 * lane - height + rest))
            total = side + rest
            top = max(margin, min((height - total) / 2.0, height - total - margin))
            if lane - side < top:
                # The top inset sits on its margin floor; size to that instead.
                side = max(resting_side, lane - top)
                total = side + rest
                top = max(margin, min((height - total) / 2.0, height - total - margin))
    left = min(top, max(0.0, lane - side))
    axis_top = top
    if expansion > 0.0:
        # With the card hidden, cover + title + artist sit centred in the
        # window. The left inset keeps the rule above (it was set from the
        # card-including top); only the vertical position moves, on the
        # same reveal spring.
        group = side + gap_cover + meta_h
        centred = max(margin, min((height - group) / 2.0, height - group - margin))
        top += (centred - top) * expansion
    cover = LayoutGBox(origin_x + left, origin_y + top, side, side)
    title = LayoutGBox(cover.x, cover.y + cover.h + gap_cover, side, title_h)
    artist = LayoutGBox(title.x, title.y + title.h + artist_gap, side, artist_h)
    meta_bottom = artist.y + artist.h if artist_h > 0.0 else title.y + title.h
    card = LayoutGBox(cover.x, meta_bottom + gap_card, side, card_h)
    inner_left = card.x + pad_x
    inner_w = max(1.0, card.w - 2.0 * pad_x)
    track = LayoutGBox(inner_left, card.y + pad_top, inner_w, progress_h)
    time_row = LayoutGBox(inner_left, track.y + track.h + time_gap, inner_w, time_h)
    buttons = LayoutGBox(inner_left, time_row.y + time_row.h + button_gap, inner_w, play_side)
    return LayoutGTargets(
        unit=unit, cover=cover, title=title, artist=artist, card=card,
        track=track, time_row=time_row, buttons=buttons,
        axis=origin_y + axis_top + total / 2.0,
        play_side=play_side, skip_side=LAYOUT_G_SKIP_ICON * k,
        title_size=title_size, artist_size=artist_size,
    )


def paint_content_layout_glyph(painter, rect, layout, ink):
    """The user's four supplied silhouettes, scaled from one 100-unit grid."""
    painter.save()
    painter.translate(rect.topLeft())
    painter.scale(rect.width() / 100., rect.height() / 100.)
    painter.setPen(Qt.PenStyle.NoPen)
    bright = QColor(ink)
    soft = QColor(ink); soft.setAlpha(round(soft.alpha() * .52))
    def block(x, y, width, height, color=bright, radius=5):
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(x, y, width, height), radius, radius)
    if layout == "cover":
        block(23, 12, 54, 54, radius=9)
        block(20, 76, 60, 10, soft)
    elif layout == "cover_lyrics":
        block(22, 8, 56, 59, radius=9)
        dark = QColor(0, 0, 0, 125) if ink.lightnessF() > .5 else QColor(255, 255, 255, 140)
        block(28, 18, 34, 9, dark, 3)
        block(28, 33, 44, 9, dark, 3)
        block(28, 48, 34, 9, dark, 3)
        block(19, 77, 62, 10, soft)
    elif layout == "playlist":
        block(10, 28, 50, 50, radius=9)
        block(68, 10, 24, 22, soft)
        block(68, 42, 24, 22)
        block(68, 74, 24, 22, soft)
    else:
        block(6, 20, 47, 48, radius=8)
        block(60, 24, 32, 10)
        block(60, 40, 39, 10, soft)
        block(60, 56, 32, 10, soft)
        block(8, 79, 82, 10, soft)
    painter.restore()


class MusicContentLayoutMixin:
    def _init_content_layout(self):
        getter = getattr(self.owner, "settings_getter", None)
        self._content_layout = normalize_content_layout(getter("v2_music_content_layout", "lyrics") if callable(getter) else "lyrics")
        self._temporary_content_layout = None
        self._no_lyrics_layout_track = None
        self._no_lyrics_layout_active = False
        self._no_lyrics_manual_layout = None
        self._content_layout_cross_pair_next = False
        self._layout_snapshot = QImage()
        self._layout_target_snapshot = QImage()
        self._layout_composite = QImage()
        self._capturing_content_snapshot = False
        self._layout_snapshot_size = self.size()
        self._layout_transport_from = self._layout_transport_to = QRectF()
        self._layout_cover_from = self._layout_cover_to = QRectF()
        self._layout_transition_fraction = 1.
        self._layout_old = self._content_layout
        self._layout_transition_track = None
        self._layout_animation = QVariantAnimation(self)
        self._layout_animation.setDuration(560)
        self._layout_animation.setEasingCurve(QEasingCurve.Type.Linear)
        self._layout_animation.valueChanged.connect(self._advance_content_layout)
        self._layout_animation.finished.connect(self._finish_content_layout)
        self._width_layout_fraction = None
        self._width_layout_animation = QVariantAnimation(self)
        self._width_layout_animation.setDuration(1000)
        self._width_layout_animation.setEasingCurve(wide_narrow_easing())
        self._width_layout_animation.valueChanged.connect(self._advance_width_layout)
        self._width_layout_animation.finished.connect(self._width_layout_completed)
        self._width_transition = None
        self._width_resize_marks = None
        self._width_transition_history = []
        self._queue_scroll = 0.
        self._queue_overscroll = 0.
        self._queue_flick = FlickScroll()
        self._queue_hit_rects = {}
        self._queue_pressed = None
        self._queue_pressed_token = None
        self._queue_view_rect = QRectF()
        self._queue_scroll_limit = 0.
        self._queue_scrollbar_rect = QRectF()
        self._queue_snapshot = None
        self._queue_last_identity = None
        self._queue_selection_position = -1.
        self._queue_focus_from = self._queue_focus_to = 0.
        self._queue_selection_from = self._queue_selection_to = -1.
        self._queue_focus_motion = QVariantAnimation(self)
        self._queue_focus_motion.setDuration(440)
        self._queue_focus_motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._queue_focus_motion.valueChanged.connect(self._advance_queue_focus)
        self._queue_skeleton_timer = QTimer(self)
        self._queue_skeleton_timer.setInterval(33)
        self._queue_skeleton_timer.timeout.connect(self._pulse_queue_skeleton)
        self._queue_art_cache = {}
        self._queue_artwork_provider = None
        self._queue_artwork_signal = None
        self._queue_artwork_hint_pending = None
        self._queue_artwork_hint_sent = None
        self._queue_artwork_hint_timer = QTimer(self)
        self._queue_artwork_hint_timer.setSingleShot(True)
        self._queue_artwork_hint_timer.timeout.connect(self._flush_queue_artwork_hint)
        self._last_effective_content_layout = self._effective_content_layout()
        # Qt's constructor geometry is not a user-selected compact window.
        self._last_content_layout_narrow = None
        self._content_layout_resize_suspended = False
        self._content_layout_user_resizing = False
        self._layout_edge_fraction = 1.
        self._layout_edge_animation = QVariantAnimation(self)
        self._layout_edge_animation.setDuration(720)
        self._layout_edge_animation.valueChanged.connect(self._advance_content_edge)
        self._sync_queue_artwork_connection()

    def _advance_content_edge(self, value):
        self._layout_edge_fraction = float(value)
        self.update()

    def _content_layout_button_rect(self, button, scale):
        anchor = self._playback_mode_button_rect(button, scale) if self._owned_playback_mode is not None else self._translation_button_rect(button, scale)
        return self._preceding_control_rect(anchor, scale)

    def _content_layout_is_narrow(self):
        return self._width_breakpoint_narrow()

    def _width_breakpoint_width(self, height=None):
        """Invert the existing shaped wide-column fit; never persist this width."""
        height = float(self.height() if height is None else height)
        key = (height, self.LYRIC_PIXEL_SIZE, tuple(self.SURFACE_FONT_FAMILIES),
               self.devicePixelRatioF(), self.logicalDpiX(), self.logicalDpiY())
        if key == getattr(self, "_width_breakpoint_inverse_key", None):
            return self._width_breakpoint_inverse
        def fits(width):
            rect = QRectF(0., 0., width, height)
            column = self._wide_content_column_geometry(rect, measure_fit=True)[2]
            font = self._surface_font(self.LYRIC_PIXEL_SIZE * self._surface_scale(rect), QFont.Weight.Bold)
            return lyric_column_fits(column.width(), font, previous=None, dpr=self.devicePixelRatioF())
        low, high = 1., max(640., height * 2.)
        while not fits(high) and high < 65536.:
            high *= 2.
        for _ in range(24):
            middle = (low + high) * .5
            if fits(middle):
                high = middle
            else:
                low = middle
        self._width_breakpoint_inverse_key = key
        self._width_breakpoint_inverse = high
        return high

    def _begin_width_resize(self):
        if self._width_resize_marks is not None:
            return
        from local.music_surface_window import MUSIC_WINDOW_ASPECT_RATIO
        height = self.height()
        breakpoint = self._width_breakpoint_width(height)
        minimum = float(self.WINDOW_MIN_WIDTH)
        if breakpoint - minimum < 60.:
            minimum = breakpoint - 60.
        maximum = min(self._available_geometry().width(), round(height * MUSIC_WINDOW_ASPECT_RATIO))
        self._width_resize_marks = WidthMarks(minimum, breakpoint, maximum, height)
        if self._width_transition is None:
            self._width_transition = WidthTransition(bool(self._content_layout_fit_narrow))
        self._width_transition._sample = None
        self._width_transition.drag_speed = 0.
        self._width_transition.sample(self._width_resize_marks.mark(self.width()), time.monotonic())
        self._content_layout_fit_key = None
        self._record_width_transition()

    def _end_width_resize(self):
        # Adopt the gesture's requested endpoint as the independent font-fit
        # hysteresis baseline. Releasing exactly at 25 must not queue a merge
        # just because widening originally needed another half CJK of room.
        if self._width_resize_marks is not None and self._width_transition is not None:
            self._content_layout_measured_narrow = self._width_transition.want_narrow
        self._width_resize_marks = None
        self._content_layout_fit_key = None
        self._sync_lyric_resize_motion()

    def _record_width_transition(self):
        transition = self._width_transition
        if transition is None:
            return
        event = dict(state=transition.state, at=time.monotonic(), duration=transition.duration,
                     want_narrow=transition.want_narrow)
        self._width_transition_history.append(event)
        del self._width_transition_history[:-64]
        diagnostics = getattr(self, "_diagnostics", None)
        if diagnostics is not None:
            diagnostics.update(width_state=transition.state, width_duration=transition.duration,
                               width_want_narrow=transition.want_narrow, width_speed=transition.drag_speed)
            if self._width_resize_marks is not None:
                marks = self._width_resize_marks
                diagnostics.update(width_breakpoint=marks.breakpoint,
                                   width_mark=marks.mark(self.width()), split_unreachable=marks.split_unreachable)

    def _width_breakpoint_narrow(self):
        # The hypothetical wide font remains the measurement yardstick. The
        # rendered column and its four shaping poses move independently.
        scale = self._surface_scale()
        assert math.isfinite(scale) and scale > 0., "Invalid lyric surface scale"
        key = (self.width(), self.height(), scale, self.LYRIC_PIXEL_SIZE,
               tuple(self.SURFACE_FONT_FAMILIES), self.devicePixelRatioF(),
               self.logicalDpiX(), self.logicalDpiY(), self._width_resize_marks)
        if key == getattr(self, "_content_layout_fit_key", None):
            return self._content_layout_fit_narrow
        _content, _cover, column = self._wide_content_column_geometry(measure_fit=True)
        font = self._surface_font(self.LYRIC_PIXEL_SIZE * scale, QFont.Weight.Bold)
        previous = getattr(self, "_content_layout_measured_narrow", None)
        narrow = not lyric_column_fits(column.width(), font, previous=previous, dpr=self.devicePixelRatioF())
        self._content_layout_measured_narrow = narrow
        marks = self._width_resize_marks
        if marks is not None:
            if self._width_transition is None:
                self._width_transition = WidthTransition(narrow)
            self._width_transition.request_mark(marks.mark(self.width()), time.monotonic(),
                                                unreachable=marks.split_unreachable)
            narrow = self._width_transition.want_narrow
        self._retarget_width_layout(narrow)
        self._content_layout_fit_key = key
        self._content_layout_fit_narrow = self._width_transition.narrow
        self._record_width_transition()
        return self._content_layout_fit_narrow

    def _width_breakpoint_animated_externally(self):
        return False

    def _retarget_width_layout(self, narrow):
        target = float(narrow)
        animation = self._width_layout_animation
        fraction = self._width_layout_fraction
        if self._width_transition is None:
            self._width_transition = WidthTransition(narrow)
        transition = self._width_transition
        snap = (fraction is None or not self.isVisible()
                or self._content_layout not in {"lyrics", "playlist"}
                or self._content_layout_resize_suspended
                or self._pending_fullscreen is not None
                or self._island_entering or self._island_returning
                or self._capturing_transition_snapshot)
        if snap:
            animation.stop()
            transition.settle(narrow)
            self._width_layout_fraction = target
            return
        transition.request(narrow)
        if self._width_breakpoint_animated_externally():
            self._width_layout_fraction = float(transition.narrow)
            return
        if not transition.active or animation.state() == QVariantAnimation.State.Running:
            return
        animation.setDuration(round(transition.duration * 1000.))
        animation.setEasingCurve(wide_narrow_easing())
        animation.setStartValue(fraction)
        animation.setEndValue(float(transition.narrow))
        animation.start()

    def _advance_width_layout(self, value):
        self._width_layout_fraction = float(value)
        self.update()

    def _width_layout_completed(self):
        transition = self._width_transition
        if transition is None:
            return
        again = transition.complete()
        self._content_layout_fit_narrow = transition.narrow
        self._content_layout_fit_key = None
        self._record_width_transition()
        if again:
            self._retarget_width_layout(transition.want_narrow)
        self._sync_lyric_resize_motion()
        self.update()

    def _finish_width_layout(self):
        self._width_layout_animation.stop()
        if hasattr(self, "_content_layout_fit_narrow"):
            if self._width_transition is not None:
                self._content_layout_fit_narrow = self._width_transition.want_narrow
                self._width_transition.settle(self._content_layout_fit_narrow)
            self._width_layout_fraction = float(self._content_layout_fit_narrow)
            self._content_layout_fit_key = None

    def _width_lyric_render_pose(self, rect, column, scale):
        """Four shaped canvases; all intermediate frames transform cached ink."""
        if not getattr(self, "_width_chrome_active", False):
            return None
        phase = self._width_chrome_progress
        key_phase = round(max(0., min(1., phase)) * 3.) / 3.
        source = self._width_content_from[2]
        target = self._width_content_to[2]
        logical = self._interpolate_rect(source, target, key_phase)
        logical.setWidth(max(1., logical.width()))
        logical.setHeight(max(1., logical.height()))
        start_scale, end_scale = self._width_font_scales
        font_scale = start_scale + (end_scale - start_scale) * key_phase
        transform = QTransform()
        transform.translate(column.left(), column.top())
        transform.scale(column.width() / logical.width(), column.height() / logical.height())
        transform.translate(-logical.left(), -logical.top())
        diagnostics = getattr(self, "_diagnostics", None)
        if diagnostics is not None:
            diagnostics['width_shaping_key'] = round(key_phase * 3.)
        return QRectF(logical), logical, font_scale, transform

    def _content_column_geometry(self, rect=None):
        """Width changes the external column arrangement, never its mode.

        Inside one paint the same answer is asked for about seven times;
        the per-frame geometry cache returns copies of the first.
        """
        cache = getattr(self, "_frame_geometry_cache", None)
        if cache is not None:
            surface = QRectF(self.rect()) if rect is None else QRectF(rect)
            key = ("content_column", surface.x(), surface.y(), surface.width(), surface.height(),
                   self._effective_content_layout(), self._width_layout_fraction)
            hit = cache.get(key)
            if hit is None:
                hit = self._content_column_geometry_uncached(rect)
                cache[key] = tuple(QRectF(r) for r in hit)
            return tuple(QRectF(r) for r in hit)
        return self._content_column_geometry_uncached(rect)

    def _content_column_geometry_uncached(self, rect=None):
        content, cover, column = self._wide_content_column_geometry(rect)
        layout = self._effective_content_layout()
        if layout in {"lyrics", "playlist"}:
            narrow = self._content_layout_is_narrow()
            surface = QRectF(self.rect()) if rect is None else QRectF(rect)
            scale = self._surface_scale(surface)
            margin = min(20., 20. * scale)
            top = min(70., surface.height() * .16)
            stacked_content = surface.adjusted(margin, top, -margin, -margin)
            side = max(1., min(96., surface.width() * .267, stacked_content.height() * .28))
            hidden = 0.0
            reader = getattr(self, "_layout_g_control_reveal", None)
            if narrow and callable(reader):
                hidden = 1.0 - reader()
            stacked_cover = narrow_header_cover(stacked_content, side, margin, hidden)
            column_top = surface.top() if layout == "lyrics" else stacked_cover.bottom() + 24. * scale
            # Clear the transport card while it shows; take the whole height once it leaves.
            card = getattr(self, "_narrow_transport_card", None)
            shown_bottom = (card(surface).top() - 12. * scale if callable(card)
                            else surface.bottom() - max(76. * scale, 6.2 * self._control_unit(surface) + 2.4 * self._control_unit(surface) + 8.))
            bottom_now = shown_bottom + (surface.bottom() - margin - shown_bottom) * hidden
            bottom = surface.bottom() if layout == "lyrics" else max(column_top + 1., bottom_now)
            stacked_column = QRectF(stacked_content.left(), column_top, stacked_content.width(), bottom - column_top)
            # Content, artwork and the reading viewport share one pose.
            fraction = self._width_layout_fraction
            content = self._interpolate_rect(content, stacked_content, fraction)
            cover = self._interpolate_rect(cover, stacked_cover, fraction)
            column = self._interpolate_rect(column, stacked_column, fraction)
            if layout != "lyrics":
                column = self._separate_width_column(cover, column)
                column = playlist_column_for_cover(surface, cover, column, scale=scale)
        return content, cover, column

    @staticmethod
    def _separate_width_column(cover, column):
        if cover.isValid() and column.intersects(cover):
            column = QRectF(column)
            if column.center().x() >= cover.right():
                column.setLeft(max(column.left(), cover.right() + 1.))
            else:
                column.setTop(max(column.top(), cover.bottom() + 1.))
        return column

    def _wide_content_column_geometry(self, rect=None, *, measure_fit=False):
        """Unadapted two-column geometry also supplies the fit measurement."""
        surface = QRectF(self.rect()) if rect is None else QRectF(rect)
        scale = self._surface_scale(surface)
        margin = 60. * scale
        content = surface.adjusted(margin, margin, -margin, -margin)
        side = max(1., min(404. * scale, content.height() - 118. * scale,
                           content.width()))
        # Keep the existing shaped breakpoint measurement independent of the
        # responsive composition. Both visible columns share the width budget.
        gap = 100. * scale
        if not measure_fit:
            # Layout G places the cover. Hidden controls use the adopted gain;
            # the reading lane keeps the previous 6.8u + 37.8u anchor so width
            # handoff and reused lyric shapes stay on the same column.
            reveal = 1.0
            reader = getattr(self, "_layout_g_control_reveal", None)
            if callable(reader):
                reveal = reader()
            self._layout_g_painted_reveal = reveal
            cover = self._layout_g_targets(surface, control_reveal=reveal).cover.as_rect()
            unit = self._control_unit(surface)
            lane_right = (6.8 + 37.8) * unit
            gap = min(gap, max(0., content.width() * .10))
            left = max(lane_right, cover.right()) + gap
        else:
            cover = QRectF(content.left(), content.top(), side, side)
            left = cover.right() + gap
        # Follow/wrapping use the full reading lane. Bottom chrome feathers
        # lyric ink separately; its visibility must never move the anchor.
        column = QRectF(left, content.top(), max(0., content.right() - left), content.height())
        if not measure_fit and self._content_layout == "lyrics":
            column.setTop(surface.top())
            column.setBottom(surface.bottom())
        elif not measure_fit and self._content_layout == "playlist":
            # Fill down to the bottom-right controls instead of the old content margin.
            unit = self._control_unit(surface)
            footer = max(40., 6.2 * unit + 2.4 * unit + 8.)
            column.setBottom(max(column.top() + 1., surface.bottom() - footer))
        return content, cover, column

    def _effective_content_layout(self):
        return self._temporary_content_layout or self._content_layout

    def _sync_no_lyrics_content_layout(self, now):
        """Use the existing cover mode for a contiguous run without lyrics.

        The override is presentation state, separate from the saved choice.
        A button click within this run is remembered until usable lyrics
        arrive, including across instrumental track changes and quiet retries.
        """
        lines, timed = self._display_lyrics()
        track = self._visual_track_identity()
        self._no_lyrics_layout_track = track
        state = self._lyric_content_state(now, lines, timed)
        if state == "loading":
            if self._no_lyrics_layout_active:
                # A terminal miss says nothing about an explicitly reopened
                # lookup, even on this song. Restore without saving a choice.
                target = "playlist" if self._no_lyrics_manual_layout == "playlist" else "lyrics"
                self._no_lyrics_layout_active = False
                self._no_lyrics_manual_layout = None
                self._change_content_layout(target, temporary=True)
            return
        if state == "no_lyrics":
            self._no_lyrics_layout_active = True
            target = self._no_lyrics_manual_layout or "cover"
            if (self._layout_old in {"lyrics", "cover_lyrics"}
                    and not self._layout_snapshot.isNull()):
                self._layout_animation.stop()
                self._finish_content_layout()
            # Discard the old lyric bitmap at this boundary. A terminal
            # instrumental result must not crossfade stale ink over artwork.
            self._change_content_layout(target, temporary=True, animate=False)
            return
        if self._no_lyrics_layout_active:
            if state == "loaded":
                target = "playlist" if self._no_lyrics_manual_layout == "playlist" else "lyrics"
            else:
                target = self._content_layout
            self._no_lyrics_layout_active = False
            self._no_lyrics_manual_layout = None
            self._change_content_layout(target, temporary=True)

    def _cycle_content_layout(self):
        self._sync_no_lyrics_content_layout(time.monotonic())
        effective = self._effective_content_layout()
        if self._no_lyrics_layout_active:
            following = "cover" if effective == "playlist" else "playlist"
            self._no_lyrics_manual_layout = following
            # A deliberate no-lyrics choice is a user preference; unlike
            # automatic degradation it may use the existing settings path.
            return self.set_content_layout(following)
        # Preserve the established click order at every width: first the
        # current pair, then its corresponding external/cover form.
        same_pair = {"lyrics": "playlist", "playlist": "lyrics",
                     "cover": "cover_lyrics", "cover_lyrics": "cover"}
        other_pair = {"lyrics": "cover_lyrics", "playlist": "cover",
                      "cover": "playlist", "cover_lyrics": "lyrics"}
        following = (other_pair if self._content_layout_cross_pair_next else same_pair)[effective]
        cross_pair_next = not self._content_layout_cross_pair_next
        changed = self.set_content_layout(following)
        if changed:
            self._content_layout_cross_pair_next = cross_pair_next
        return changed

    def _content_layout_tooltip(self):
        if self._no_lyrics_layout_active:
            return "无歌词：单击切换仅专辑 / 播放列表"
        return "单击切换：先切换同组布局，再切换另一组"

    def _pulse_content_layout_edges(self):
        self._layout_edge_started_at = time.monotonic()
        self._layout_edge_animation.stop()
        self._layout_edge_animation.setStartValue(0.)
        self._layout_edge_animation.setEndValue(1.)
        self._layout_edge_animation.start()

    def _reset_content_layout_resize_baseline(self):
        # Native presentation owns the lyric pose handoff. Updating the fit
        # baseline must not discard the spring it has just rebased.
        self._last_content_layout_narrow = self._content_layout_is_narrow()
        self._last_effective_content_layout = self._effective_content_layout()
        self._content_layout_cross_pair_next = False

    def _resize_content_layout(self, *, user_resize=False):
        self._queue_artwork_hint_timer.stop()
        self._queue_artwork_hint_pending = None
        if (self._content_layout_resize_suspended or self._pending_fullscreen is not None
                or self._island_entering or self._island_returning
                or self._capturing_transition_snapshot):
            # Morph rectangles, including the tiny island origin, never
            # become the baseline for a listener's next resize gesture.
            return
        narrow = self._content_layout_is_narrow()
        if narrow != self._last_content_layout_narrow:
            self._content_layout_cross_pair_next = False
        self._last_content_layout_narrow = narrow
        effective = self._effective_content_layout()
        if effective != self._last_effective_content_layout:
            self._last_effective_content_layout = effective
            if effective != "playlist":
                self._clear_playback_queue_artwork_hint()
            self._clear_lyric_browse()
            self._logical_line_spring = LogicalLineSpringChain()
            self.update()

    def set_content_layout(self, layout, *, animate=True):
        return self._change_content_layout(layout, animate=animate)

    def _change_content_layout(self, layout, *, animate=True, temporary=False):
        layout = normalize_content_layout(layout)
        if temporary:
            if layout == self._effective_content_layout():
                return False
        elif layout == self._content_layout and self._temporary_content_layout is None:
            return False
        self._content_layout_cross_pair_next = False
        # An explicit content choice supersedes a pending width bitmap.
        self._cancel_lyric_resize()
        cover_from = self._content_layout_cover_pose()
        snapshot = self._capture_content_layout() if animate and self.isVisible() else QImage()
        transport_from = QRectF(self._media_control_panel_rect)
        self._finish_width_layout()
        self._layout_animation.stop()
        self._layout_target_snapshot = QImage()
        self._layout_composite = QImage()
        self._layout_snapshot = QImage()
        self._layout_old = self._effective_content_layout()
        if temporary:
            self._temporary_content_layout = layout if layout != self._content_layout else None
        else:
            self._content_layout = layout
            self._temporary_content_layout = None
        if self._effective_content_layout() != "playlist":
            self._clear_playback_queue_artwork_hint()
        self._last_effective_content_layout = self._effective_content_layout()
        self._last_content_layout_narrow = self._content_layout_is_narrow()
        self._layout_transition_track = self._visual_track_identity()
        self._layout_transition_fraction = 1.
        self._queue_scroll = 0.
        self._queue_focus_motion.stop()
        self._queue_skeleton_timer.stop()
        self._queue_last_identity = None
        self._queue_selection_position = -1.
        self._queue_pressed = None
        self._queue_pressed_token = None
        self._queue_hit_rects = {}
        self._queue_snapshot = None
        self._lyric_hit_rects.clear()
        self._clear_lyric_browse()
        self._logical_line_spring = LogicalLineSpringChain()
        self._control_layer_cache.clear()
        self._control_layer_cache_bytes = 0
        setter = getattr(self.owner, "settings_setter", None)
        if not temporary and callable(setter):
            setter("v2_music_content_layout", layout)
        self._reset_control_idle()
        if not snapshot.isNull():
            # The short handoff keeps two bounded pictures instead of shaping
            # lyrics, queue artwork and blurred glass again on every frame.
            # Actual controls remain live above these content-only snapshots.
            self._cover_layout_from = self._cover_layout_to = float(self._no_lyrics_cover_target())
            self._layout_target_snapshot = self._capture_content_layout()
            self._layout_cover_from = cover_from
            self._layout_cover_to = self._content_layout_cover_pose()
            self._layout_transport_from = transport_from
            self._layout_transport_to = QRectF(self._media_control_panel_rect)
            self._layout_snapshot_size = self.size()
            self._layout_snapshot = snapshot
            self._layout_transition_fraction = 0.
            self._lyric_hit_rects.clear()
            self._queue_hit_rects.clear()
            self._metadata_hit_rects.clear()
        if animate:
            self._layout_animation.setStartValue(0.)
            self._layout_animation.setEndValue(1.)
            self._layout_animation.start()
        else:
            self._finish_content_layout()
        self.update()
        return True

    def _advance_content_layout(self, value):
        self._layout_transition_fraction = float(value)
        self.update()

    def _finish_content_layout(self):
        self._layout_transition_fraction = 1.
        self._layout_snapshot = QImage()
        self._layout_target_snapshot = QImage()
        self._layout_composite = QImage()
        self.update()

    def _capture_content_layout(self):
        self._capturing_content_snapshot = True
        try:
            image = self.grab().toImage()
            if self._song_layout_build is not None:
                # A long song normally shapes over several live ticks. The
                # frozen target must contain its lyrics before the handoff.
                self._advance_song_layout_build(max_rows=None, max_seconds=None)
                image = self.grab().toImage()
        finally:
            self._capturing_content_snapshot = False
        if max(image.width(), image.height()) > 1600:
            image = image.scaled(1600, 1600, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        return image

    def _content_layout_cover_pose(self):
        if not self._layout_snapshot.isNull() and not self._layout_target_snapshot.isNull():
            fraction = self._layout_transition_fraction
            eased = fraction * fraction * (3. - 2. * fraction)
            return self._interpolate_rect(self._layout_cover_from,self._layout_cover_to,eased)
        now = time.monotonic()
        return self._scaled_cover_rect(self._current_cover_rect(now),self._cover_scale_at(now))

    def _paint_content_layout_images(self, painter):
        rect = QRectF(self.rect())
        fraction = self._layout_transition_fraction
        eased = fraction * fraction * (3. - 2. * fraction)
        painter.drawImage(rect,self._background_cache)
        # A single cover travels between the two positions. Staging the other
        # ink avoids duplicated titles and queue/lyric text in the handoff.
        for image, cover, opacity in (
                (self._layout_snapshot,self._layout_cover_from,max(0.,1.-fraction/.48)),
                (self._layout_target_snapshot,self._layout_cover_to,max(0.,(fraction-.48)/.52))):
            if opacity <= 0.:
                continue
            opacity = opacity * opacity * (3. - 2. * opacity)
            outer=QPainterPath();outer.addRect(rect)
            hole=QPainterPath();hole.addRect(cover.adjusted(-1.,-1.,1.,1.))
            painter.save();painter.setClipPath(outer.subtracted(hole))
            painter.setOpacity(opacity);painter.drawImage(rect,image);painter.restore()
        cover = self._interpolate_rect(self._layout_cover_from,self._layout_cover_to,eased)
        path=QPainterPath();path.addRoundedRect(cover,self._album_art_radius(cover.width()),self._album_art_radius(cover.width()))
        painter.save();painter.setClipPath(path)
        for image, source, opacity in (
                (self._layout_snapshot,self._layout_cover_from,1.),
                (self._layout_target_snapshot,self._layout_cover_to,eased)):
            sx,sy=image.width()/self.width(),image.height()/self.height()
            pixels=QRectF(source.x()*sx,source.y()*sy,source.width()*sx,source.height()*sy)
            painter.setOpacity(opacity);painter.drawImage(cover,image,pixels)
        painter.restore()

    def _paint_cached_content_layout(self, *, paint_chrome=True):
        if self._layout_snapshot.isNull() or self._layout_target_snapshot.isNull():
            return False
        if (self._layout_transition_track != self._visual_track_identity()
                or self._layout_snapshot_size != self.size()):
            self._layout_animation.stop()
            self._finish_content_layout()
            return False
        rect = QRectF(self.rect())
        fraction = self._layout_transition_fraction
        eased = fraction * fraction * (3. - 2. * fraction)
        composite = self.width() * self.devicePixelRatioF() > self._layout_target_snapshot.width() * 1.2
        if composite:
            # Blend at the bounded snapshot size, then upscale once. Two
            # separate full-display alpha/scaling passes are costly at 4K.
            size = self._layout_target_snapshot.size()
            if self._layout_composite.size() != size:
                self._layout_composite = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
            self._layout_composite.fill(Qt.GlobalColor.transparent)
            with QPainter(self._layout_composite) as cached:
                cached.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                cached.scale(size.width() / self.width(), size.height() / self.height())
                self._paint_content_layout_images(cached)
        with QPainter(self) as painter:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setClipRect(rect)
            if composite:
                painter.drawImage(rect, self._layout_composite)
            else:
                self._paint_content_layout_images(painter)
            if paint_chrome and not self._capturing_content_snapshot:
                self._paint_library_back(painter, rect)
                self._paint_window_controls(painter, rect)
                self._paint_personal_dj_control(painter, rect)
                transport = self._interpolate_rect(self._layout_transport_from, self._layout_transport_to, eased)
                palette = self._art_palette()
                self._paint_media_controls(painter, transport, palette.primary, palette.secondary)
            self._apply_surface_edge_alpha(painter)
        self._lyric_hit_rects.clear()
        self._queue_hit_rects.clear()
        self._metadata_hit_rects.clear()
        return True

    def _paint_content_layout_icon(self, painter, rect):
        fraction = self._layout_transition_fraction
        ink = self._surface_glass_ink(rect)
        if fraction >= 1.:
            paint_content_layout_glyph(painter, rect, self._effective_content_layout(), ink); return
        # A small damped overshoot lives only on deliberate layout selection.
        elastic = 1. + .16 * math.sin(fraction * math.pi * 3.) * (1. - fraction)
        painter.save(); painter.translate(rect.center()); painter.scale(elastic, elastic); painter.translate(-rect.center())
        painter.setOpacity(max(0., 1. - fraction * 2.))
        paint_content_layout_glyph(painter, rect, self._layout_old, ink)
        painter.setOpacity(min(1., fraction * 2.))
        paint_content_layout_glyph(painter, rect, self._effective_content_layout(), ink)
        painter.restore()

    def _paint_content_layout_transition(self, painter):
        if not self._layout_snapshot.isNull():
            if self._layout_transition_track != self._visual_track_identity():
                self._finish_content_layout()
            else:
                fraction = self._layout_transition_fraction
                eased = fraction * fraction * (3. - 2. * fraction)
                painter.save()
                painter.setOpacity(1. - eased)
                shrink = .025 * eased
                target = QRectF(self.rect()).adjusted(self.width() * shrink, self.height() * shrink,
                                                     -self.width() * shrink, -self.height() * shrink)
                painter.drawImage(target, self._layout_snapshot)
                painter.restore()
        phase = self._layout_edge_fraction
        if phase < 1.:
            opacity = math.sin(phase * math.pi * 2.) ** 2 * .68
            ink = self._scene_control_ink()
            for right in (False, True):
                x = self.width() if right else 0.
                stop = x - 18. if right else 18.
                gradient = QLinearGradient(x, 0., stop, 0.)
                strong = QColor(ink); strong.setAlpha(round(255 * opacity))
                gradient.setColorAt(0., strong); gradient.setColorAt(1., QColor(ink.red(), ink.green(), ink.blue(), 0))
                painter.fillRect(QRectF(self.width() - 18. if right else 0., 0., 18., self.height()), gradient)

    def _advance_queue_focus(self, value):
        fraction = float(value)
        self._queue_scroll = self._queue_focus_from + (self._queue_focus_to - self._queue_focus_from) * fraction
        self._queue_selection_position = self._queue_selection_from + (
            self._queue_selection_to - self._queue_selection_from) * fraction
        # Moving rows invalidate a held positional gesture just as wheel
        # scrolling does; release cannot activate a different moving row.
        self._queue_pressed = self._queue_pressed_token = None
        self._queue_hit_rects.clear()
        self.update(self._queue_view_rect.toAlignedRect())

    def _pulse_queue_skeleton(self):
        if (self._closing_for_shutdown or not self.isVisible() or self.isMinimized()
                or self._effective_content_layout() != "playlist"):
            self._queue_skeleton_timer.stop()
            return
        self.update(self._queue_view_rect.toAlignedRect())

    def _paint_playback_queue(self, painter, rect):
        self._queue_view_rect = QRectF(rect)
        self._queue_scroll_limit = 0.
        self._queue_scrollbar_rect = QRectF()
        provider = getattr(self.owner, "data_provider", None)
        getter = getattr(provider, "get_owned_playback_queue", None)
        try:
            snapshot = getter() if callable(getter) else None
        except Exception:
            snapshot = None
        self._queue_snapshot = snapshot
        self._queue_hit_rects = {}
        scale = self._surface_scale()
        ink = self._surface_glass_ink(rect)
        panel_radius = max(14.0, min(22.0, min(rect.width(), rect.height()) * 0.06))
        plate = getattr(self, "_paint_large_plate", None) or getattr(self, "_paint_flat_glass", None)
        if callable(plate):
            # One glass layer under the whole list: its edge is the list's edge.
            plate(painter, rect, panel_radius)
        painter.save()
        panel = QPainterPath()
        panel.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), panel_radius, panel_radius)
        painter.setClipPath(panel)
        # The list reads inside the plate's padding; scrolling and centring use that area.
        rect = rect.adjusted(6 * scale, 10 * scale, -6 * scale, -6 * scale)
        self._queue_view_rect = QRectF(rect)
        painter.setFont(self._surface_font(22 * scale, QFont.Weight.Bold)); painter.setPen(ink)
        painter.drawText(rect.adjusted(12 * scale, 0, -12 * scale, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, "播放列表")
        if snapshot is not None and snapshot[0] is not None and snapshot[2]:
            count = QColor(ink); count.setAlpha(round(count.alpha() * .55)); painter.setPen(count)
            painter.setFont(self._surface_font(13 * scale))
            painter.drawText(rect.adjusted(12 * scale, 0, -14 * scale, 0).adjusted(0, 0, 0, 0).intersected(
                QRectF(rect.left(), rect.top(), rect.width() - 14 * scale, 30 * scale)),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{len(snapshot[2])} 首")
            painter.setPen(ink)
        if snapshot is None or snapshot[0] is None:
            self._queue_focus_motion.stop()
            self._queue_skeleton_timer.stop()
            self._queue_scroll = 0.
            self._queue_overscroll = 0.
            self._queue_last_identity = None
            self._queue_selection_position = -1.
            self._remember_queue_artwork_hint(provider, None, ())
            painter.setFont(self._surface_font(16 * scale))
            painter.drawText(rect.adjusted(12 * scale, 48 * scale, -12 * scale, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, "当前来源未提供可选择的播放列表")
            painter.restore(); return
        # Snapshot interface is immutable and includes real indices plus a
        # generation token; click dispatch never infers indices from titles.
        token, current_index, entries = snapshot
        row_height = 64 * scale
        top = rect.top() + 46 * scale
        viewport = QRectF(rect.left(), top, rect.width(), max(0., rect.bottom() - top))
        max_scroll = max(0., len(entries) * row_height - viewport.height())
        self._queue_scroll_limit = max_scroll
        identity = (token, current_index)
        if identity != self._queue_last_identity:
            previous = self._queue_last_identity
            self._queue_last_identity = identity
            self._queue_overscroll = 0.0
            flick = getattr(self, "_queue_flick", None)
            if flick is not None:
                flick.cancel()
            self._queue_focus_motion.stop()
            if current_index >= 0:
                row_top = current_index * row_height
                target = max(0., min(max_scroll, row_top + row_height * .5 - viewport.height() * .5))
                if (previous is not None and previous[0] == token and self.isVisible()
                        and not self._capturing_content_snapshot and not self._capturing_transition_snapshot):
                    self._queue_focus_from, self._queue_focus_to = self._queue_scroll, target
                    self._queue_selection_from, self._queue_selection_to = self._queue_selection_position, float(current_index)
                    self._queue_focus_motion.setStartValue(0.)
                    self._queue_focus_motion.setEndValue(1.)
                    self._queue_focus_motion.start()
                else:
                    self._queue_scroll = target
                    self._queue_selection_position = float(current_index)
            else:
                self._queue_selection_position = -1.
        self._queue_scroll = max(0., min(max_scroll, self._queue_scroll))
        overscroll = float(getattr(self, "_queue_overscroll", 0.0) or 0.0)
        painter.setClipRect(viewport, Qt.ClipOperation.IntersectClip)
        if self._queue_selection_position >= 0.:
            inset = 6. * scale
            selected = QRectF(rect.left(), top + self._queue_selection_position * row_height - self._queue_scroll + overscroll,
                              rect.width(), row_height).adjusted(inset, inset, -inset, -inset)
            # Keep the rounded corners of a row that is crossing the clip.
            # A square viewport cut was shaving them off.
            selected = selected.intersected(viewport)
            if selected.width() > 1. and selected.height() > 1.:
                radius = queue_selection_radius(selected.height())
                highlight = QPainterPath()
                highlight.addRoundedRect(selected, radius, radius)
                backdrop = self._surface_glass_backdrop()
                self._clear_control_renderer.paint(painter, selected, scene=backdrop, scene_bounds=QRectF(self.rect()),
                    ink=ink, now=time.monotonic(), role="queue-selection", kind="capsule", path=highlight,
                    level=self._surface_glass_level(), selected=True, light=self._surface_glass_light())
        first = max(0, int(self._queue_scroll // row_height))
        last = min(len(entries), first + int(viewport.height() / row_height) + 2)
        visible_indices = []
        missing_artwork = False
        for position in range(first, last):
            entry = entries[position]
            row = QRectF(rect.left(), top + position * row_height - self._queue_scroll + overscroll, rect.width(), row_height - 5 * scale)
            row_ink = QColor(GLASS_SELECTED_INK) if self._queue_selection_position >= 0. and selected.contains(row.center()) else ink
            self._queue_hit_rects[position] = row.intersected(viewport)
            if position == getattr(self, "_queue_hover_index", None) and row_ink is ink:
                from local.row_states import paint_row_state
                hover = QRectF(rect.left(), row.top(), rect.width(), row_height).adjusted(
                    6. * scale, 6. * scale, -6. * scale, -6. * scale)
                paint_row_state(painter, hover, queue_selection_radius(hover.height()),
                                dark=ink.lightness() >= 128, hovered=True)
            if row.intersects(viewport) and len(visible_indices) < 24:
                visible_indices.append(position)
            art_side = 43. * scale
            art_rect = QRectF(row.left() + 9 * scale, row.center().y() - art_side / 2., art_side, art_side)
            getter = getattr(provider, "get_owned_queue_artwork", None)
            data = getter(entry) if callable(getter) else b""
            image = QImage()
            if isinstance(data, bytes) and data:
                cache_key = (entry.source_id, entry.song_id, hash(data))
                image = self._queue_art_cache.get(cache_key)
                if image is None:
                    image = QImage.fromData(data)
                    self._queue_art_cache[cache_key] = image
                    while len(self._queue_art_cache) > 48:
                        self._queue_art_cache.pop(next(iter(self._queue_art_cache)))
            if image.isNull() and position == current_index:
                image = QImage(getattr(self.owner, "album_art", QImage()))
            if not image.isNull():
                painter.save()
                path = QPainterPath(); path.addRoundedRect(art_rect, 6 * scale, 6 * scale)
                painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
                side = min(image.width(), image.height())
                painter.drawImage(art_rect, image, QRectF((image.width() - side)/2., (image.height() - side)/2., side, side))
                painter.restore()
            else:
                paint_artwork_skeleton(painter, art_rect, row_ink, time.monotonic(), radius=6 * scale)
                missing_artwork = missing_artwork or bool(entry.artwork_url)
            text_left = 61 * scale
            painter.setPen(row_ink)
            painter.setFont(self._surface_font(18 * scale, QFont.Weight.DemiBold))
            title = QFontMetricsF(painter.font()).elidedText(entry.title, Qt.TextElideMode.ElideRight, max(1., row.width() - text_left - 14 * scale))
            painter.drawText(row.adjusted(text_left, 7 * scale, -14 * scale, -24 * scale), Qt.AlignmentFlag.AlignVCenter, title)
            soft = QColor(row_ink); soft.setAlpha(round(soft.alpha() * .66)); painter.setPen(soft)
            painter.setFont(self._surface_font(13 * scale))
            artist = QFontMetricsF(painter.font()).elidedText(entry.artist, Qt.TextElideMode.ElideRight, max(1., row.width() - text_left - 14 * scale))
            painter.drawText(row.adjusted(text_left, 30 * scale, -14 * scale, -5 * scale), Qt.AlignmentFlag.AlignVCenter, artist)
        self._remember_queue_artwork_hint(provider, token, tuple(visible_indices))
        if missing_artwork and self.isVisible() and not self.isMinimized():
            if not self._queue_skeleton_timer.isActive():
                self._queue_skeleton_timer.start()
        else:
            self._queue_skeleton_timer.stop()
        if max_scroll > 0. and viewport.height() > 0.:
            thumb_height = min(viewport.height(), max(20. * scale,
                viewport.height() ** 2 / (len(entries) * row_height)))
            thumb_top = viewport.top() + (viewport.height() - thumb_height) * self._queue_scroll / max_scroll
            self._queue_scrollbar_rect = QRectF(viewport.right() - 4. * scale,
                thumb_top, 3. * scale, thumb_height)
            thumb = QColor(ink); thumb.setAlpha(round(ink.alpha() * .38))
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(thumb)
            painter.drawRoundedRect(self._queue_scrollbar_rect, 1.5 * scale, 1.5 * scale)
        painter.restore()

    def _sync_queue_artwork_connection(self):
        if self._closing_for_shutdown:
            return
        provider = getattr(self.owner, "data_provider", None)
        if provider is self._queue_artwork_provider:
            return
        self._disconnect_queue_artwork_signal()
        self._queue_artwork_provider = provider
        getter = getattr(provider, "get_owned_queue_artwork_changed_signal", None)
        signal = getter() if callable(getter) else None
        if signal is not None:
            signal.connect(self._on_queue_artwork_changed)
            self._queue_artwork_signal = signal

    def _disconnect_queue_artwork_signal(self):
        self._queue_focus_motion.stop()
        self._queue_skeleton_timer.stop()
        self._clear_playback_queue_artwork_hint()
        signal, self._queue_artwork_signal = self._queue_artwork_signal, None
        if signal is not None:
            try:
                signal.disconnect(self._on_queue_artwork_changed)
            except (RuntimeError, TypeError):
                pass
        self._queue_artwork_provider = None

    def _on_queue_artwork_changed(self):
        if self._queue_artwork_provider is not getattr(self.owner, "data_provider", None):
            self._sync_queue_artwork_connection()
            return
        if (not self._closing_for_shutdown and self.isVisible()
                and self._effective_content_layout() == "playlist" and self._queue_view_rect.isValid()):
            self.update(self._queue_view_rect.toAlignedRect())

    def _remember_queue_artwork_hint(self, provider, token, indices):
        # Painting only publishes an immutable visibility hint. The existing
        # provider owns all download/cache work after this paint has ended.
        if self._capturing_content_snapshot or self._capturing_transition_snapshot:
            return
        hint = (provider, token, indices)
        if hint == self._queue_artwork_hint_pending or hint == self._queue_artwork_hint_sent:
            return
        self._queue_artwork_hint_pending = hint
        self._queue_artwork_hint_timer.start(0)

    def _flush_queue_artwork_hint(self):
        hint, self._queue_artwork_hint_pending = self._queue_artwork_hint_pending, None
        if hint is None:
            return
        provider, token, indices = hint
        if (self._closing_for_shutdown or not self.isVisible()
                or provider is not getattr(self.owner, "data_provider", None)
                or self._effective_content_layout() != "playlist"):
            return
        self._sync_queue_artwork_connection()
        if token is None:
            self._clear_playback_queue_artwork_hint()
            return
        if (self._queue_snapshot is None or self._queue_snapshot[0] != token
                or self._pending_fullscreen is not None or self._island_entering
                or self._island_returning or self._layout_transition_fraction < 1.):
            return
        prefetch = getattr(provider, "prefetch_owned_queue_artwork", None)
        if callable(prefetch):
            prefetch(indices, expected_queue_token=token)
            self._queue_artwork_hint_sent = hint

    def _clear_playback_queue_artwork_hint(self):
        self._queue_artwork_hint_timer.stop()
        self._queue_artwork_hint_pending = None
        hint, self._queue_artwork_hint_sent = self._queue_artwork_hint_sent, None
        if hint is not None:
            provider, token, _indices = hint
            prefetch = getattr(provider, "prefetch_owned_queue_artwork", None)
            if callable(prefetch):
                prefetch((), expected_queue_token=token)

    def _scroll_playback_queue(self, event):
        # A transition bitmap is frozen. Only the settled, visible queue may
        # change its row offsets and hit targets in response to the wheel.
        if (self._pending_fullscreen is not None or self._island_entering
                or self._island_returning or self._layout_transition_fraction < 1.):
            return
        pixels = event.pixelDelta().y()
        delta = float(pixels) if pixels else event.angleDelta().y() / 120. * 64. * self._surface_scale()
        if not delta:
            return
        self._queue_pressed = None
        self._queue_pressed_token = None
        self._queue_focus_motion.stop()
        if self._queue_last_identity is not None:
            self._queue_selection_position = float(self._queue_last_identity[1])
        flick = self._queue_flick_controller()
        flick.on_step = self._on_queue_flick_step
        flick.cancel()
        flick.observe(-delta, time.monotonic())
        previous = self._queue_scroll
        committed, overscroll = flick.absorb(
            previous, previous - delta, 0.0, self._queue_scroll_limit, self._queue_view_rect.height())
        self._queue_overscroll = overscroll
        pixels = event.pixelDelta().y()
        # A notch lands on the same row as before. A trackpad flick keeps its
        # speed and coasts on the shared ticker after this event returns.
        if pixels and committed == previous - delta:
            flick.arm_coast(committed, 0.0, self._queue_scroll_limit, span=self._queue_view_rect.height())
        if committed != self._queue_scroll or overscroll:
            self._queue_scroll = committed
            self._queue_artwork_hint_timer.stop()
            self._queue_artwork_hint_pending = None
            # The old painted rows are no longer valid click destinations.
            self._queue_hit_rects.clear()
            self.update(self._queue_view_rect.toAlignedRect())

    def _queue_flick_controller(self):
        flick = getattr(self, "_queue_flick", None)
        if flick is None:
            flick = self._queue_flick = FlickScroll()
            self._queue_overscroll = 0.0
        return flick

    def _on_queue_flick_step(self, result):
        if result[0] == "overscroll":
            self._queue_overscroll = result[1]
        else:
            self._queue_scroll = max(0.0, min(self._queue_scroll_limit, result[1]))
            self._queue_overscroll = result[2]
        self._queue_hit_rects.clear()
        self.update(self._queue_view_rect.toAlignedRect())

    def _select_playback_queue(self, index, expected_queue_token=None):
        snapshot = self._queue_snapshot
        selector = getattr(getattr(self.owner, "data_provider", None), "select_owned_queue_index", None)
        if (snapshot is not None and snapshot[0] is not None and callable(selector)
                and (expected_queue_token is None or expected_queue_token == snapshot[0])):
            selector(index, snapshot[0] if expected_queue_token is None else expected_queue_token)
            self.update()
