"""Current-song collection capsule and a bounded click-only playlist chooser."""
from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from local.liquid_glass import GlassPushButton, paint_liquid_glass, widget_glass_level
from local.music_source_icons import paint_music_source_icon
from local.song_collection_controller import SongCollectionState


class CollectionGlassButton(GlassPushButton):
    """Stable native target; shared click spring and live scene-colored ink."""

    def __init__(self, surface, text, parent=None, *, playlist=None):
        super().__init__(text, parent)
        self.surface, self.playlist = surface, playlist
        self.artwork = QImage()
        self.setMinimumHeight(76 if playlist else 44)
        self.setProperty("glassRadius", 16.)
        self._click = QVariantAnimation(self)
        self._click.setDuration(round(surface.TRANSPORT_FEEDBACK_SECONDS * 1000))
        self._click.setStartValue(0.)
        self._click.setEndValue(1.)
        self._click.valueChanged.connect(lambda _value: self.update())
        self.clicked.connect(self._start_click)

    def _start_click(self):
        self._click.stop()
        self._click.start()

    def _click_scale(self):
        progress = self._click.currentValue()
        if progress is None:
            return 1.
        frame = self.surface._transport_feedback_frame(float(progress) * self.surface.TRANSPORT_FEEDBACK_SECONDS,
            self.surface.TRANSPORT_LIGHT_EXPAND_SECONDS + self.surface.TRANSPORT_LIGHT_FADE_SECONDS)
        return frame[0] if frame is not None else 1.

    def paintEvent(self, event):
        # Pixels are assigned by the panel's async completion/scroll handlers.
        # Painting never asks a provider for artwork or schedules a request.
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            face = QRectF(self.rect()).adjusted(2, 2, -2, -2)
            image = self.surface._surface_glass_backdrop()
            target = face.translated(QPointF(self.mapTo(self.surface, QPoint())))
            paint_liquid_glass(painter, face, radius=16., light=self.surface._surface_glass_light(),
                material_level=widget_glass_level(self.surface),
                refractive=True, backdrop=image,
                backdrop_rect=self.surface._surface_glass_source_rect(target, image),
                hovered=self.underMouse(), pressed=self.isDown(), cache_token=id(self))
            ink = self.surface._scene_control_ink()
            if not self.isEnabled():
                painter.setOpacity(.5)
            painter.setPen(ink)
            scale = self._click_scale()
            painter.translate(face.center())
            painter.scale(scale, scale)
            painter.translate(-face.center())
            if self.playlist is None:
                painter.setFont(self.surface._surface_font(14))
                painter.drawText(face, Qt.AlignmentFlag.AlignCenter, self.text())
            else:
                cover = QRectF(face.left() + 12, face.center().y() - 24, 48, 48)
                path = QPainterPath()
                path.addRoundedRect(cover, 10, 10)
                painter.save()
                painter.setClipPath(path)
                if not self.artwork.isNull():
                    painter.drawImage(cover, self.artwork)
                else:
                    base = QColor(ink)
                    base.setAlpha(24)
                    painter.fillRect(cover, base)
                    if self.playlist.artwork_url:
                        phase = (time.monotonic() * .6) % 1.4 - .2
                        sheen = QLinearGradient(cover.left() + (phase - .6) * cover.width(), 0,
                                                cover.left() + (phase + .6) * cover.width(), 0)
                        for stop, alpha in ((0., 0), (.5, 48), (1., 0)):
                            color = QColor(ink)
                            color.setAlpha(alpha)
                            sheen.setColorAt(stop, color)
                        painter.fillRect(cover, sheen)
                    else:
                        paint_music_source_icon(painter, cover.adjusted(12, 12, -12, -12), "playlist_add", tint=ink)
                painter.restore()
                text = QRectF(cover.right() + 14, face.top() + 14,
                              max(1., face.right() - cover.right() - 28), 25)
                painter.setFont(self.surface._surface_font(15))
                title = painter.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, int(text.width()))
                painter.drawText(text, Qt.AlignmentFlag.AlignVCenter, title)
                detail = QRectF(text.left(), text.bottom() + 2, text.width(), 20)
                painter.setFont(self.surface._surface_font(12))
                subtitle = f"{self.playlist.track_count} 首歌曲" if self.playlist.track_count else "添加当前歌曲"
                painter.setOpacity(painter.opacity() * .65)
                painter.drawText(detail, Qt.AlignmentFlag.AlignVCenter, subtitle)
            if self.hasFocus():
                painter.setOpacity(1.)
                painter.setPen(QPen(ink, 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(face.adjusted(2, 2, -2, -2), 14, 14)


class SongPlaylistPanel(QWidget):
    """Native scroll/focus targets; no account I/O in widget callbacks."""

    def __init__(self, surface, controller, track_id, authority):
        super().__init__(surface)
        self.surface, self.controller, self.track_id = surface, controller, track_id
        self.authority = authority
        self._initial_load_attempted = False
        self.setAccessibleName("添加当前歌曲到歌单")
        self._key = None
        self._row_buttons = {}
        self._ink_key = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        header = QWidget(self)
        from PySide6.QtWidgets import QHBoxLayout
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("添加到歌单", header)
        self.title.setFont(surface._surface_font(16))
        row.addWidget(self.title, 1)
        self.close_button = CollectionGlassButton(surface, "×", header)
        self.close_button.setAccessibleName("关闭歌单选择")
        self.close_button.setMinimumHeight(40)
        self.close_button.setFixedSize(40, 40)
        self.close_button.clicked.connect(self.hide)
        row.addWidget(self.close_button)
        layout.addWidget(header)
        self.message = QLabel(self)
        self.message.setWordWrap(True)
        self.message.setMinimumHeight(36)
        layout.addWidget(self.message)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; } QScrollBar:horizontal {height:0;}")
        layout.addWidget(self.scroll, 1)
        self.more = CollectionGlassButton(surface, "刷新歌单", self)
        self.more.clicked.connect(self._load)
        layout.addWidget(self.more)
        self._motion = QVariantAnimation(self)
        self._motion.setDuration(220)
        self._motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._motion.valueChanged.connect(self._animate)
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self.scroll.verticalScrollBar().valueChanged.connect(self._visible_artwork)
        self._pulse = QTimer(self)
        self._pulse.setInterval(32)
        self._pulse.timeout.connect(self._tick)
        self.refresh()

    def _bound_state(self):
        state = self.surface._song_collection_state()
        if (self.surface._song_collection_connection is not self.controller
                or state.track_id != self.track_id
                or not self.controller.authority_matches(self.track_id, self.authority)):
            return SongCollectionState()
        return state

    def _load(self):
        state = self._bound_state()
        if not state.available or state.busy or state.playlists_pending:
            return False
        self._initial_load_attempted = True
        return self.controller.load_playlists(self.track_id, more=state.playlist_has_more,
                                             expected_authority=self.authority)

    def _add(self, playlist_id):
        state = self._bound_state()
        if not state.available or state.busy:
            return False
        return self.controller.add_to_playlist(playlist_id, self.track_id,
                                              expected_authority=self.authority)

    def _animate(self, value):
        self._opacity.setOpacity(float(value))

    def open(self):
        if not self._bound_state().track_id:
            return
        self.place()
        self.show()
        self.raise_()
        self.close_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._motion.setStartValue(0.)
        self._motion.setEndValue(1.)
        self._motion.start()
        self._pulse.start()
        self.refresh()

    def place(self):
        surface = self.surface
        margin = 10
        anchor = surface._song_collection_geometry(QRectF(surface.rect()), shifted=False)
        width = max(1, min(380, surface.width() - 2 * margin))
        height = max(1, min(520, int(anchor.top()) - 2 * margin))
        self.setGeometry(max(margin, surface.width() - margin - width),
                         max(margin, int(anchor.top()) - 8 - height), width, height)

    def refresh(self):
        state = self._bound_state()
        if not state.track_id:
            self.more.setEnabled(False)
            self.hide()
            return
        self._sync_ink()
        self.message.setText(state.message or ("选择自己创建的歌单" if state.playlists else "尚无可添加的自建歌单"))
        key = state.playlists
        if key != self._key:
            self._key = key
            body = QWidget()
            rows = QVBoxLayout(body)
            rows.setContentsMargins(0, 0, 2, 0)
            rows.setSpacing(6)
            self._row_buttons = {}
            for playlist in state.playlists:
                button = CollectionGlassButton(self.surface, playlist.title, body, playlist=playlist)
                button.setToolTip(playlist.title)
                button.clicked.connect(lambda _checked=False, playlist_id=playlist.playlist_id:
                    self._add(playlist_id))
                rows.addWidget(button)
                self._row_buttons[playlist.playlist_id] = button
            rows.addStretch(1)
            old = self.scroll.takeWidget()
            self.scroll.setWidget(body)
            if old is not None:
                old.deleteLater()
        for button in self._row_buttons.values():
            button.setEnabled(not state.busy and state.available)
        self._visible_artwork()
        self.more.setText("正在读取…" if state.playlists_pending else "加载更多" if state.playlist_has_more else "刷新歌单")
        self.more.setEnabled(state.available and not state.playlists_pending and not state.busy)
        self.update()
        # The panel may open before the asynchronous favorite-status read is
        # ready. Observe that transition once, without turning errors or later
        # changed signals into automatic retries.
        if state.playlists_pending or state.playlists_loaded:
            self._initial_load_attempted = True
        if (self.isVisible() and state.available and not state.busy
                and not self._initial_load_attempted):
            self._load()

    def _sync_ink(self):
        ink = self.surface._scene_control_ink()
        if ink.rgba() != self._ink_key:
            self._ink_key = ink.rgba()
            for label in (self.title, self.message):
                label.setStyleSheet(f"color:{ink.name()};background:transparent;")
            self.scroll.setStyleSheet(
                "QScrollArea, QScrollArea > QWidget > QWidget {background:transparent;}"
                "QScrollBar:vertical {background:transparent;width:6px;margin:2px 0;}"
                f"QScrollBar::handle:vertical {{background:rgba({ink.red()},{ink.green()},{ink.blue()},65);"
                "border-radius:3px;min-height:24px;}"
                "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}"
                "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:transparent;}"
                "QScrollBar:horizontal {height:0;}")

    def _visible_artwork(self, _value=None):
        if not self.isVisible() or not self._bound_state().track_id:
            return
        getter = getattr(self.controller, "playlist_artwork", None)
        request = getattr(self.controller, "request_playlist_artwork", None)
        viewport = self.scroll.viewport()
        for button in self._row_buttons.values():
            area = QRectF(button.rect()).translated(QPointF(button.mapTo(viewport, QPoint())))
            if not area.intersects(QRectF(viewport.rect())):
                continue
            if callable(getter):
                button.artwork = getter(button.playlist, self.track_id, expected_authority=self.authority)
            if button.artwork.isNull() and callable(request):
                request(button.playlist, self.track_id, expected_authority=self.authority)
            button.update()

    def _tick(self):
        self._sync_ink()
        for button in (self.more, self.close_button, *self._row_buttons.values()):
            if button.isVisible():
                button.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_row_buttons"):
            self._visible_artwork()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
        else:
            super().keyPressEvent(event)

    def hideEvent(self, event):
        self._motion.stop()
        self._pulse.stop()
        self.surface._note_pointer_moved()
        super().hideEvent(event)

    def paintEvent(self, event):
        self._sync_ink()
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            image = self.surface._surface_glass_backdrop()
            target = rect.translated(self.x(), self.y())
            paint_liquid_glass(painter, rect, radius=22, light=self.surface._surface_glass_light(),
                              material_level=widget_glass_level(self.surface),
                              refractive=True, backdrop=image,
                              backdrop_rect=self.surface._surface_glass_source_rect(target, image),
                              cache_token=id(self))


class MusicSongCollectionMixin:
    def _init_song_collection(self):
        self._song_collection_connection = None
        self._song_playlist_panel = None
        self._song_collection_press_track = ""
        self._song_collection_press_authority = None
        self._song_collection_clicks = {}
        self._song_collection_last_damage = QRectF()
        self._sync_song_collection_connection()

    def _sync_song_collection_connection(self):
        controller = getattr(self.owner, "_song_collection_controller", None)
        if controller is self._song_collection_connection:
            return controller
        if self._song_collection_connection is not None:
            self._song_collection_connection.changed.disconnect(self._song_collection_changed)
        self._song_collection_connection = controller
        if controller is not None:
            controller.changed.connect(self._song_collection_changed)
        return controller

    def _song_collection_state(self):
        controller = self._sync_song_collection_connection()
        track_id = self._visual_track_identity()
        provider = getattr(self.owner, "data_provider", None)
        active = getattr(provider, "_owned_active", None)
        if controller is None or not track_id.startswith("owned:") or callable(active) and not active():
            return SongCollectionState()
        return controller.context(track_id)

    def _song_collection_changed(self):
        panel = self._song_playlist_panel
        if panel is not None:
            panel.refresh()
        self.update()

    def _song_collection_geometry(self, rect, now=None, *, shifted=True):
        if not self._song_collection_state().track_id:
            return QRectF()
        now = time.monotonic() if now is None else now
        scale = self._surface_scale(rect)
        side = max(36., 44. * scale)
        margin = max(10., 18. * scale)
        lane = self._footer_dj_lane_width(rect, now)
        gap = max(8., 10. * scale) if lane > 0 else 0.
        face = QRectF(rect.right() - margin - lane - gap - side * 2.5,
                      rect.bottom() - margin - side, side * 2.5, side)
        if shifted:
            reveal = self._control_reveal_at(now)
            # DJ can remain independently visible during speech/status. This
            # capsule always uses ordinary controls' reveal and edge slide.
            if self._owned_personal_dj_enabled is not None:
                shift = self._personal_dj_control_shift(rect, reveal)
            else:
                shift = QPointF(14. * scale * (1. - reveal),
                               (rect.bottom() - face.top() + 4.) * (1. - reveal))
            face.translate(shift)
        return face

    def _song_collection_footer(self, rect):
        face = self._song_collection_geometry(rect, shifted=False)
        return face.height() + max(8., 10. * self._surface_scale(rect)) if face.isValid() else 0.

    def _song_collection_rects(self, rect, now=None):
        face = self._song_collection_geometry(rect, now)
        if not face.isValid():
            return {}
        return {"favorite": QRectF(face.left(), face.top(), face.width() / 2., face.height()),
                "playlist_add": QRectF(face.center().x(), face.top(), face.width() / 2., face.height())}

    def _update_song_collection_animation(self, now):
        rect = QRectF(self.rect())
        face = self._song_collection_geometry(rect, now)
        damage = face.adjusted(-4., -4., 4., 4.).intersected(rect) if face.isValid() else QRectF()
        previous = self._song_collection_last_damage
        moving = damage != previous
        active = bool(self._song_collection_clicks)
        for key, started in tuple(self._song_collection_clicks.items()):
            if now - started >= self.TRANSPORT_FEEDBACK_SECONDS:
                self._song_collection_clicks.pop(key, None)
        lane_active = (abs(getattr(self, "_footer_lane_from", 0.) - getattr(self, "_footer_lane_target", 0.)) > .001
                       and now - getattr(self, "_footer_lane_started", now) < self.DJ_SPEECH_CONTRACTION_SECONDS)
        if moving or active or lane_active:
            union = previous.united(damage) if previous.isValid() else damage
            if union.isValid():
                self.update(union.toAlignedRect())
        self._song_collection_last_damage = QRectF(damage)
        return moving or active or lane_active

    def _paint_song_collection_control(self, painter, rect):
        state = self._song_collection_state()
        now = time.monotonic()
        reveal = self._control_reveal_at(now)
        face = self._song_collection_geometry(rect, now)
        if not face.isValid() or reveal <= .001:
            return
        painter.save()
        painter.setOpacity(reveal)
        image = self._surface_glass_backdrop()
        paint_liquid_glass(painter, face, radius=face.height()/2., light=self._surface_glass_light(),
                          material_level=widget_glass_level(self),
                          refractive=True, backdrop=image,
                          backdrop_rect=self._surface_glass_source_rect(face, image), cache_token=(id(self), "collection"))
        for key, target in self._song_collection_rects(rect, now).items():
            painter.save()
            started = self._song_collection_clicks.get(key)
            if started is not None:
                frame = self._transport_feedback_frame(max(0., now - started),
                    self.TRANSPORT_LIGHT_EXPAND_SECONDS + self.TRANSPORT_LIGHT_FADE_SECONDS)
                if frame is not None:
                    painter.translate(target.center())
                    painter.scale(frame[0], frame[0])
                    painter.translate(-target.center())
            hovered = self._hovered_window_button == key
            pressed = self._pressed_window_button == key
            if hovered or pressed:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 255, 30 if pressed else 15))
                painter.drawEllipse(target.center(), target.height()*.43, target.height()*.43)
            enabled = state.available and not state.busy and (key != "favorite" or state.liked is not None)
            ink = QColor("#ff3e4c") if key == "favorite" and state.liked is True else self._scene_control_ink()
            if not enabled:
                painter.setOpacity(painter.opacity() * .5)
            side = target.height() * (.63 if key == "favorite" else .61)
            glyph = QRectF(target.center().x()-side/2., target.center().y()-side/2., side, side)
            paint_music_source_icon(painter, glyph, "heart_filled" if key == "favorite" and state.liked else "heart_outline" if key == "favorite" else "playlist_add", tint=ink)
            painter.restore()
        painter.restore()
        if self._song_playlist_panel is not None and self._song_playlist_panel.isVisible():
            self._song_playlist_panel._sync_ink()
            self._song_playlist_panel.place()

    def _song_collection_tooltip(self, key):
        state = self._song_collection_state()
        if state.message and (not state.available or state.busy or state.liked is None):
            return state.message
        return ("取消收藏" if state.liked else "收藏歌曲") if key == "favorite" else "添加到歌单"

    def _capture_song_collection_authority(self, expected_track_id):
        state = self._song_collection_state()
        controller = self._song_collection_connection
        if not state.track_id or state.track_id != expected_track_id or controller is None:
            return None
        return controller.capture_authority(expected_track_id)

    def _activate_song_collection(self, key, expected_track_id, expected_authority):
        state = self._song_collection_state()
        controller = self._song_collection_connection
        if (not state.track_id or expected_track_id != state.track_id or controller is None
                or not controller.authority_matches(expected_track_id, expected_authority)):
            return False
        self._song_collection_clicks[key] = time.monotonic()
        self._update_song_collection_animation(time.monotonic())
        if key == "favorite":
            if state.liked is None and not state.busy:
                controller.refresh()
            else:
                controller.toggle_favorite(state.track_id, expected_authority=expected_authority)
            self.setToolTip(self._song_collection_tooltip(key))
            return True
        self._dismiss_audio_output_panel()
        self._close_dj_voice_selector(immediate=True)
        panel = self._song_playlist_panel
        if (panel is not None and panel.isVisible() and panel.track_id == state.track_id
                and panel.authority is expected_authority):
            panel.hide()
            return True
        if panel is not None:
            panel.deleteLater()
        panel = SongPlaylistPanel(self, controller, state.track_id, expected_authority)
        self._song_playlist_panel = panel
        panel.open()
        return True

    def _dismiss_song_playlist_panel(self):
        panel = getattr(self, "_song_playlist_panel", None)
        if panel is not None:
            panel.hide()
