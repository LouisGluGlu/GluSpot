"""Native song-page content choices, glyphs and fenced queue presentation."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath

from local.liquid_glass import paint_liquid_glass
from local.music_lyric_motion import LogicalLineSpringChain
from local.artwork_skeleton import paint_artwork_skeleton


CONTENT_LAYOUTS = ("cover", "cover_lyrics", "lyrics", "playlist")
CONTENT_LAYOUT_LABELS = ("仅专辑", "封面歌词", "专辑与歌词", "专辑与播放列表")


def normalize_content_layout(value):
    return value if isinstance(value, str) and value in CONTENT_LAYOUTS else "lyrics"


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
        self._queue_scroll = 0.
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
        # A tall, independently resized window can be wider than the former
        # 640px cutoff yet have no room beside its height-scaled artwork. Use
        # the exact cover, gutters and font that the two-column layout paints.
        _content, _cover, column = self._content_column_geometry()
        scale = self._surface_scale()
        font = self._surface_font(self.LYRIC_PIXEL_SIZE * scale, QFont.Weight.Bold)
        six_glyphs = QFontMetricsF(font).horizontalAdvance("音乐正在播放")
        required = max(180., six_glyphs, (43. + 61. + 14. + 4. * 18.) * scale)
        return column.width() < required

    def _content_column_geometry(self, rect=None):
        """One bounded source of artwork/side-column geometry for fit and ink."""
        surface = QRectF(self.rect()) if rect is None else QRectF(rect)
        scale = self._surface_scale(surface)
        margin = 60. * scale
        content = surface.adjusted(margin, margin, -margin, -margin)
        side = max(1., min(404. * scale, content.height() - 118. * scale,
                           content.width()))
        cover = QRectF(content.left(), content.top(), side, side)
        left = cover.right() + 100. * scale
        column = QRectF(left, content.top(), max(0., content.right() - left), content.height())
        return content, cover, column

    def _effective_content_layout(self):
        layout = self._content_layout
        if self._content_layout_is_narrow():
            return "cover_lyrics" if layout in {"lyrics", "cover_lyrics"} else "cover"
        return layout

    def _cycle_content_layout(self):
        effective = self._effective_content_layout()
        if self._content_layout_is_narrow():
            following = "cover" if effective == "cover_lyrics" else "cover_lyrics"
        else:
            cycle = ("lyrics", "playlist", "cover", "cover_lyrics")
            following = cycle[(cycle.index(effective) + 1) % len(cycle)]
        return self.set_content_layout(following)

    def _content_layout_tooltip(self):
        return "单击切换专辑 / 封面歌词" if self._content_layout_is_narrow() else "单击切换：歌词 → 播放列表 → 专辑 → 封面歌词"

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
        if user_resize and self._last_content_layout_narrow is True and not narrow:
            # Only an actual edge drag crossing out of the compact family
            # promotes its two forms. Presentation changes retain selection.
            compact = self._last_effective_content_layout
            self._content_layout = "playlist" if compact == "cover" else "lyrics"
            setter = getattr(self.owner, "settings_setter", None)
            if callable(setter):
                setter("v2_music_content_layout", self._content_layout)
        self._last_content_layout_narrow = narrow
        effective = self._effective_content_layout()
        if effective != self._last_effective_content_layout:
            self._last_effective_content_layout = effective
            if effective != "playlist":
                self._clear_playback_queue_artwork_hint()
            self._clear_lyric_browse()
            self._logical_line_spring = LogicalLineSpringChain()
            if self._content_layout_is_narrow() and self._content_layout in {"lyrics", "playlist"}:
                self._pulse_content_layout_edges()
            self.update()

    def set_content_layout(self, layout, *, animate=True):
        layout = normalize_content_layout(layout)
        if layout == self._content_layout:
            return False
        cover_from = self._content_layout_cover_pose()
        snapshot = self._capture_content_layout() if animate and self.isVisible() else QImage()
        transport_from = QRectF(self._media_control_panel_rect)
        self._layout_animation.stop()
        self._layout_target_snapshot = QImage()
        self._layout_composite = QImage()
        self._layout_snapshot = QImage()
        self._layout_old, self._content_layout = self._content_layout, layout
        if self._effective_content_layout() != "playlist":
            self._clear_playback_queue_artwork_hint()
        self._last_effective_content_layout = self._effective_content_layout()
        self._last_content_layout_narrow = self._content_layout_is_narrow()
        if self._content_layout_is_narrow() and layout in {"lyrics", "playlist"}:
            self._pulse_content_layout_edges()
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
        if callable(setter):
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

    def _paint_cached_content_layout(self):
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
            if not self._capturing_content_snapshot:
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
        painter.save(); painter.setClipRect(rect)
        painter.setFont(self._surface_font(24 * scale, QFont.Weight.Bold)); painter.setPen(ink)
        painter.drawText(rect.adjusted(12 * scale, 0, -12 * scale, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, "播放列表")
        if snapshot is None or snapshot[0] is None:
            self._queue_focus_motion.stop()
            self._queue_skeleton_timer.stop()
            self._queue_scroll = 0.
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
        painter.setClipRect(viewport)
        if self._queue_selection_position >= 0.:
            selected = QRectF(rect.left(), top + self._queue_selection_position * row_height - self._queue_scroll,
                              rect.width(), row_height - 5 * scale)
            if selected.intersects(viewport):
                backdrop = self._surface_glass_backdrop()
                paint_liquid_glass(painter, selected, radius=15. * scale, active=True,
                    light=True, refractive=True, backdrop=backdrop,
                    backdrop_rect=self._surface_glass_source_rect(selected, backdrop), cache_token=id(self))
        first = max(0, int(self._queue_scroll // row_height))
        last = min(len(entries), first + int(viewport.height() / row_height) + 2)
        visible_indices = []
        missing_artwork = False
        for position in range(first, last):
            entry = entries[position]
            row = QRectF(rect.left(), top + position * row_height - self._queue_scroll, rect.width(), row_height - 5 * scale)
            self._queue_hit_rects[position] = row.intersected(viewport)
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
                paint_artwork_skeleton(painter, art_rect, ink, time.monotonic(), radius=6 * scale)
                missing_artwork = missing_artwork or bool(entry.artwork_url)
            text_left = 61 * scale
            painter.setPen(ink)
            painter.setFont(self._surface_font(18 * scale, QFont.Weight.DemiBold))
            title = QFontMetricsF(painter.font()).elidedText(entry.title, Qt.TextElideMode.ElideRight, max(1., row.width() - text_left - 14 * scale))
            painter.drawText(row.adjusted(text_left, 7 * scale, -14 * scale, -24 * scale), Qt.AlignmentFlag.AlignVCenter, title)
            soft = QColor(ink); soft.setAlpha(round(soft.alpha() * .66)); painter.setPen(soft)
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
        scroll = max(0., min(self._queue_scroll_limit, self._queue_scroll - delta))
        if scroll != self._queue_scroll:
            self._queue_scroll = scroll
            self._queue_artwork_hint_timer.stop()
            self._queue_artwork_hint_pending = None
            # The old painted rows are no longer valid click destinations.
            self._queue_hit_rects.clear()
            self.update(self._queue_view_rect.toAlignedRect())

    def _select_playback_queue(self, index, expected_queue_token=None):
        snapshot = self._queue_snapshot
        selector = getattr(getattr(self.owner, "data_provider", None), "select_owned_queue_index", None)
        if (snapshot is not None and snapshot[0] is not None and callable(selector)
                and (expected_queue_token is None or expected_queue_token == snapshot[0])):
            selector(index, snapshot[0] if expected_queue_token is None else expected_queue_token)
            self.update()
