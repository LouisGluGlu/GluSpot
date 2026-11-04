"""An expanded V2 island embedded in the library's bottom row.

The child owns only its presentation clock. Track changes and controls use the
library controller, and audio is read through the existing island provider.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt, Signal, Slot
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from api.models import IslandVisualData
from api.providers import empty_visual_data
from local.island_stack_visual import IslandStackVisual
from local.music_lyric_preparation_store import LyricPresentationStore


class LibraryNowPlayingIsland(IslandStackVisual):
    openRequested = Signal()
    commandRequested = Signal(str)
    HEIGHT = 132
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

    def _should_paint_album_island(self):
        # The library mounts the standard cover/title/lyric instance, keeping
        # its transport reachable even for an instrumental/lyricless track.
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

    def set_track(self, track):
        self._has_track = track is not None
        data = IslandVisualData(track, (), ()) if track is not None else empty_visual_data("未在播放", "")
        self._apply_visual_data(data)
        self._sync_clock()
        self.update()

    def _sync_clock(self):
        if (self.isVisible() and self._has_track and not self.window().isMinimized()
                and not getattr(self.parentWidget(), "_navigation_cover_frozen", False)):
            if not self.timer.isActive():
                self.last_tick_at = time.monotonic()
                self.timer.start(33)
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

    def showEvent(self, event):
        QWidget.showEvent(self, event)
        self._resume_embedded()
        self.sync_settings()
        self._sync_clock()

    def hideEvent(self, event):
        self.suspend()
        QWidget.hideEvent(self, event)

    def paintEvent(self, event):
        self._island_lyric_frame_at = time.monotonic()
        self._island_lyric_frame_phase = None
        try:
            with QPainter(self) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
                self._paint_music(painter, QRectF(self.rect()).adjusted(1, 1, -1, -1), expanded=True)
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
