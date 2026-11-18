"""An expanded V2 island embedded in the library's bottom row.

The child owns only its presentation clock. Track changes and controls use the
library controller, and audio is read through the existing island provider.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt, Signal, Slot
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from api.models import IslandVisualData
from api.providers import empty_visual_data
from local.island_stack_visual import IslandStackVisual
from local.music_lyric_preparation_store import LyricPresentationStore


class LibraryNowPlayingIsland(IslandStackVisual):
    """The library's now-playing bar in one of three poses, chosen by the
    window's shape: "tall" (the expanded island: title row over the lyric
    band), "row" (one line: cover, title and artist, the lyric, controls)
    and "pill" (the floating island's free compact pose)."""

    openRequested = Signal()
    commandRequested = Signal(str)
    HEIGHT = 132
    ROW_HEIGHT = 64
    PILL_HEIGHT = 52
    PILL_WIDTH = 440
    BAR_MODES = ("tall", "row", "pill")
    _PRESENTATION_SETTINGS = (
        "lyric_font_size", "lyric_offset_seconds", "lyric_word_spacing",
        "wave_font_weight", "enable_dual_line_lyrics", "dual_line_font_size",
        "dual_line_secondary_weight", "dual_line_secondary_spacing",
        "music_intro_cleanup_enabled", "music_flow_intensity",
        "music_reactive_background_enabled", "music_reactive_background_intensity",
        "sea_wave_intensity", "solid_album_background_fallback",
        "compact_duotone_balance", "expanded_duotone_balance",
        "music_surface_reading_gradient_steps", "music_sparse_highlight_seconds_per_unit",
    )

    def __init__(self, parent):
        super().__init__()
        self.timer.stop()
        self.timer.timeout.disconnect(self._tick)
        self.timer.timeout.connect(self._advance_embedded)
        self.setParent(parent, Qt.WindowType.Widget)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAccessibleName("正在播放；点击打开播放窗口")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expanded = True
        self.location_mode = "free"
        self.music_expanded_layout_style = "standard"
        self.bar_mode = "tall"
        self.merged_bottom_radius = 0.0     # > 0: the bar is the window's bottom edge
        self._source_island = None
        self._has_track = False
        self._pressed_command = None
        self.hide()
        # A newly constructed library can remain hidden or use another layout.
        # Its embedded island must not retain a worker until actually mounted.
        self.suspend()
        parent.destroyed.connect(self.suspend)

    @Slot()
    def suspend(self):
        """Release child workers even when closing a hidden parent emits no hide."""
        self.timer.stop()
        self._clear_island_track_transition()
        if not self._solid_album_palette_closed:
            self._close_solid_album_palette_worker()
        preparation = self._lyric_presentation_store
        if not preparation.closed:
            preparation.close()

    def _resume_embedded(self):
        previous = self._lyric_presentation_store
        if previous.closed:
            previous.changed.disconnect(self._on_prepared_lyrics_ready)
            self._lyric_presentation_store = LyricPresentationStore(self)
            self._lyric_presentation_store.changed.connect(self._on_prepared_lyrics_ready)
            previous.deleteLater()
        self._resume_solid_album_palette_worker()
        self._lyric_presentation_store.get()

    # This is an ordinary child, not another floating/native island. None of
    # the standalone position, topmost, masking or drag lifecycle applies.
    def _center_on_screen(self):
        pass

    def moveEvent(self, event):
        QWidget.moveEvent(self, event)

    def _empty_music_text_active(self):
        return False

    def _expanded_no_lyrics_cover_active(self):
        return False

    def set_context(self, settings_getter=None, island=None):
        self.settings_getter = settings_getter
        if island is not None:
            self._source_island = island
            provider = getattr(island, "data_provider", None)
            if provider is not None:
                self.data_provider = provider
        self.sync_settings()

    def sync_settings(self):
        source = self._source_island
        getter = self.settings_getter
        for name in self._PRESENTATION_SETTINGS:
            current = getattr(self, name, None)
            if source is not None and hasattr(source, name):
                setattr(self, name, getattr(source, name))
            elif callable(getter) and current is not None:
                setattr(self, name, getter("v2_" + name, current))
        style = getter("v2_music_ambience_style", "sea_level") if callable(getter) else getattr(source, "music_ambience_style", "sea_level")
        self.set_music_ambience_style(style, notify=False)
        self.update()

    def set_bar_mode(self, mode: str) -> None:
        mode = mode if mode in self.BAR_MODES else "tall"
        if mode != self.bar_mode:
            self.bar_mode = mode
            self.music_button_rects = {}
            self.update()

    def bar_height(self, mode: str | None = None) -> int:
        mode = mode or self.bar_mode
        return {"row": self.ROW_HEIGHT, "pill": self.PILL_HEIGHT}.get(mode, self.HEIGHT)

    def _paint_music_expanded(self, painter, rect):
        if self.bar_mode != "row":
            return super()._paint_music_expanded(painter, rect)
        try:
            dj = self._personal_dj_island_presenting() or self._personal_dj_island_outro_progress() < 1.0
        except Exception:
            dj = False
        if dj:
            return super()._paint_music_expanded(painter, rect)
        self._paint_music_row(painter, rect)

    # A one-line bar has no lyric band: the light wave would run through the
    # title and the bottom glow blurs its corners.
    ROW_SKIPPED_STEPS = frozenset({"bottom_ambience"})
    ROW_WATER_LINE = 0.22             # the water line sits above the words, which lie in the sea as in the tall bar
    ROW_LYRIC_HEIGHT = 40.0           # under the dual-line threshold: one centred line
    ROW_RADIUS = 18.0
    ROW_MARGIN = 10.0

    def _execute_music_surface_paint_step(self, step, *args, **kwargs):
        if self.bar_mode == "row" and step in self.ROW_SKIPPED_STEPS:
            return None
        if self.bar_mode == "row" and step == "light_wave" and len(args) >= 2 and isinstance(args[1], QRectF):
            painter, rect = args[0], args[1]
            return self._paint_row_light_wave(painter, rect, step, *args[2:], **kwargs)
        return super()._execute_music_surface_paint_step(step, *args, **kwargs)

    def _paint_row_light_wave(self, painter, rect, step, *rest, **kwargs):
        """Sea level's ripple at the same scale as in the tall bar: the wave is
        laid out on a tall bar's height with its water line a little under the
        row's middle, so the crest moves and the sea light shows beneath it.
        Every clip the renderer asks for is the row's own shell (its corners
        are the window's corners), never the taller layout's."""
        tall = rect.height() * self.HEIGHT / max(1.0, float(self.ROW_HEIGHT))
        water = rect.top() + rect.height() * self.ROW_WATER_LINE
        layout = QRectF(rect.left(), water - tall * .495, rect.width(), tall)
        self._row_wave_shell = self._music_shell_path(rect, self._music_shell_radius(rect, True), True)
        try:
            return super()._execute_music_surface_paint_step(step, painter, layout, *rest, **kwargs)
        finally:
            self._row_wave_shell = None

    def _music_shell_path(self, rect, radius, expanded=False):
        shell = getattr(self, "_row_wave_shell", None)
        if shell is not None:
            return QPainterPath(shell)
        bottom = float(getattr(self, "merged_bottom_radius", 0.0) or 0.0)
        if bottom <= 0.0 or self.bar_mode == "pill":
            return super()._music_shell_path(rect, radius, expanded)
        # Flat top (a divider under the content), rounded bottom corners
        # following the window's frame.
        r = min(bottom, rect.height() / 2.0, rect.width() / 2.0)
        path = QPainterPath()
        path.moveTo(rect.topLeft())
        path.lineTo(rect.topRight())
        path.lineTo(rect.right(), rect.bottom() - r)
        path.arcTo(QRectF(rect.right() - 2 * r, rect.bottom() - 2 * r, 2 * r, 2 * r), 0.0, -90.0)
        path.lineTo(rect.left() + r, rect.bottom())
        path.arcTo(QRectF(rect.left(), rect.bottom() - 2 * r, 2 * r, 2 * r), -90.0, -90.0)
        path.closeSubpath()
        return path

    def _music_shell_radius(self, rect, expanded):
        if self.bar_mode == "row" and expanded:
            return min(self._u(self.ROW_RADIUS), rect.height() / 2.0)
        return super()._music_shell_radius(rect, expanded)

    def _paint_music_row(self, painter, rect):
        """One line: the cover inset equally from top, bottom and left; title
        over artist; the current lyric line centred between them and the
        transport; previous, play and next in fixed places (a key that does
        not apply right now, such as previous on personal FM, is dimmed)."""
        u = self._u
        margin = u(self.ROW_MARGIN)
        side = max(1., rect.height() - 2 * margin)
        # Merged into the window's bottom edge, the cover lines up with the rail.
        # Window pixels, not the island's visual scale: the bar sits at x = 1
        # and paints inside a 1 px inset, so 14 lands the cover on x = 16.
        left = 14.0 if getattr(self, "merged_bottom_radius", 0.0) else margin
        cover = QRectF(rect.left() + left, rect.top() + margin, side, side)
        self.music_cover_rect = QRectF(cover)
        self._paint_interactive_music_cover(painter, cover, u(10))
        order = ["music:previous", "music:toggle_play_pause", "music:next"]
        supported = set(self._supported_music_transport_commands(list(order)))
        size, step = u(34), u(42)
        controls_x = rect.right() - margin - (size + 2 * step)
        self.music_button_rects = {}
        for index, command in enumerate(order):
            button = QRectF(controls_x + index * step, rect.center().y() - size / 2, size, size)
            if command in supported:
                self.music_button_rects[command] = QRectF(button)
                self._paint_music_control_button(painter, button, command,
                                                 32 if command == "music:toggle_play_pause" else 22, radius=u(12))
            else:
                # Drawn dim in its place, but not a target: the painter
                # registers every key it draws, so take it back out.
                painter.save()
                painter.setOpacity(painter.opacity() * .32)
                self._paint_music_control_button(painter, button, command, 22, radius=u(12))
                painter.restore()
                self.music_button_rects.pop(command, None)
        left = cover.right() + u(12)
        room = max(1., controls_x - u(16) - left)
        info_w = max(min(u(96), room), min(u(200), room * .30))
        self._paint_island_metadata_line(painter, QRectF(left, rect.center().y() - u(19), info_w, u(22)),
                                         self.track_title, 13, self._music_meta_text_color(240), True, "title")
        self._paint_island_metadata_line(painter, QRectF(left, rect.center().y() + u(3), info_w, u(18)),
                                         self.artist, 9, self._music_meta_text_color(135), False, "artist")
        lane = QRectF(left + info_w + u(14), rect.top(), controls_x - u(14) - (left + info_w + u(14)), rect.height())
        if lane.width() < u(60):
            return
        if not self.lyrics:
            return
        # The island's own one-line lyric: word-by-word reading, centred, and
        # a line too long for the lane scrolls with faded edges.
        height = min(lane.height(), u(self.ROW_LYRIC_HEIGHT))
        self._paint_compact_lyric_viewport(
            painter, QRectF(lane.left(), lane.center().y() - height / 2, lane.width(), height))

    def set_track(self, track):
        self._has_track = track is not None
        data = IslandVisualData(track, (), ()) if track is not None else empty_visual_data("未在播放", "")
        self._apply_visual_data(data)
        self._sync_clock()
        self.update()

    def _embedded_interval_ms(self, now: float) -> int:
        """Keep the 33 ms footer clock until the expanded ripple is quiet."""
        target = self._low_rate_interval_ms(now) if self._expanded_idle_ripple() else 33
        if self._expanded_idle_ripple() or getattr(self, "_clock_interval_spring", None) is not None:
            return self._eased_clock_interval(target, now)
        return 33

    def _sync_clock(self):
        if (self.isVisible() and self._has_track and not self.window().isMinimized()
                and not getattr(self.parentWidget(), "_navigation_cover_frozen", False)):
            interval = self._embedded_interval_ms(time.monotonic())
            if not self.timer.isActive():
                self.last_tick_at = time.monotonic()
                self.timer.start(interval)
            elif self.timer.interval() != interval:
                self.timer.setInterval(interval)
        else:
            self.timer.stop()

    def _advance_embedded(self):
        if not self.isVisible() or self.window().isMinimized():
            self._sync_clock()
            return
        now = time.monotonic()
        self.ambient_phase += max(0., min(.1, now - self.last_tick_at))
        self.last_tick_at = now
        self._update_wave_values()
        self._handle_music_lyric_tick(now, self._animation_speed_factor())
        self._advance_island_music_control_hover(now)
        self.update()
        self._sync_clock()

    def showEvent(self, event):
        QWidget.showEvent(self, event)
        self._resume_embedded()
        self.sync_settings()
        self._sync_clock()

    def _observe_motion_cover(self):
        # The main island owns the lookup. This player only paints that frame.
        return

    def hideEvent(self, event):
        from local.motion_cover_playback import note_cover_hidden
        note_cover_hidden(self)
        self.suspend()
        QWidget.hideEvent(self, event)

    def paintEvent(self, event):
        self._island_lyric_frame_at = time.monotonic()
        self._island_lyric_frame_phase = None
        try:
            with QPainter(self) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
                pill = self.bar_mode == "pill"
                if pill:
                    self.music_button_rects = {}
                self._paint_music(painter, QRectF(self.rect()).adjusted(1, 1, -1, -1), expanded=not pill)
        finally:
            self._island_lyric_frame_at = None
            self._island_lyric_frame_phase = None

    def _paint_interactive_music_cover(self, painter, rect, radius, image=None, cache_key=None):
        if not getattr(self.parentWidget(), "_navigation_capture_hide_cover", False):
            super()._paint_interactive_music_cover(painter, rect, radius, image, cache_key)

    def _command_at(self, position):
        return next((key.split(":", 1)[1] for key, rect in self.music_button_rects.items()
                     if rect.contains(position)), "")

    def mouseMoveEvent(self, event):
        command = self._command_at(event.position())
        self.hovered_key = "music:" + command if command else ""
        self.update()

    def leaveEvent(self, event):
        self.hovered_key = ""
        self.update()
        QWidget.leaveEvent(self, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed_command = self._command_at(event.position())
            self.drag_candidate = True
            self.drag_press_key = "music:" + self._pressed_command if self._pressed_command else ""
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            command = self._command_at(event.position())
            if self._pressed_command is not None and command == self._pressed_command and self._has_track:
                if command:
                    self.music_control_press_started_at["music:" + command] = time.monotonic()
                    self.commandRequested.emit(command)
                else:
                    self.openRequested.emit()
            self._pressed_command = None
            self.drag_candidate = False
            self.drag_press_key = ""
            self.update()
            event.accept()

    def mouseDoubleClickEvent(self, event):
        # The floating island collapses on double-click. This mounted player
        # has one fixed expanded pose; its first click already owns the action.
        self._pressed_command = None
        self.drag_candidate = False
        event.accept()

    def keyPressEvent(self, event):
        if self._has_track and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.openRequested.emit()
            event.accept()
        elif self._has_track and event.key() == Qt.Key.Key_Space:
            self.commandRequested.emit("toggle_play_pause")
            event.accept()
        else:
            QWidget.keyPressEvent(self, event)
