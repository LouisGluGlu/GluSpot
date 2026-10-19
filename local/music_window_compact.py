"""Window-edge overscroll into the existing Free island, without audio commands."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QRect, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath


class MusicWindowCompactMixin:
    WINDOW_MIN_WIDTH = 330
    WINDOW_HEIGHT = 400

    def _init_window_compact(self):
        self._compact_fraction = 0.
        self._compact_finish = 0.
        self._compact_committing = False
        self._compact_snapshot = QImage()
        self._compact_island = QImage()
        self._compact_target = QRect()
        self._compact_base_geometry = QRect()
        self._compact_canvas_geometry = QRect()
        self._compact_pressure = (0., 0.)
        self._compact_pressure_edges = frozenset()
        self._compact_animation = QVariantAnimation(self)
        self._compact_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._compact_animation.valueChanged.connect(self._advance_window_compact)
        self._compact_animation.finished.connect(self._finish_window_compact)

    def _bounded_window_geometry(self, geometry):
        available = self._available_geometry()
        width = min(available.width(), max(self.WINDOW_MIN_WIDTH, geometry.width()))
        height = min(available.height(), max(self.WINDOW_HEIGHT, geometry.height()))
        result = QRect(geometry.x(), geometry.y(), width, height)
        result.moveLeft(max(available.left(), min(result.left(), available.right() - width + 1)))
        result.moveTop(max(available.top(), min(result.top(), available.bottom() - height + 1)))
        return result

    def _reset_window_compact(self):
        self._compact_animation.stop()
        base = QRect(self._compact_base_geometry)
        canvas = QRect(self._compact_canvas_geometry)
        self._compact_base_geometry = QRect()
        self._compact_canvas_geometry = QRect()
        self._compact_fraction = self._compact_finish = 0.
        self._compact_committing = False
        self._compact_snapshot = QImage()
        self._compact_island = QImage()
        self._compact_target = QRect()
        self._compact_pressure = (0., 0.)
        self._compact_pressure_edges = frozenset()
        if base.isValid() and self.geometry() == canvas and self.geometry() != base:
            self.setGeometry(base)
        if base.isValid() and hasattr(self, '_content_layout'):
            self._content_layout_resize_suspended = False

    def _update_window_compact(self, requested_width, requested_height):
        if self._compact_committing:
            return
        self._compact_animation.stop()
        # The real window stops at its ordinary minimum. Only the virtual
        # edge continues with the pointer, so it never lays out a tiny page.
        minimum = self._minimum_window_size()
        base = self._compact_base_geometry if self._compact_base_geometry.isValid() else self.geometry()
        if (base.width() > minimum.width() or base.height() > minimum.height()
                or requested_width > minimum.width() or requested_height > minimum.height()):
            self._reset_window_compact()
            return
        horizontal = min(1., max(0., (minimum.width() - requested_width) / (minimum.width() * .5)))
        vertical = min(1., max(0., (minimum.height() - requested_height) / (minimum.height() * .5)))
        fraction = max(horizontal, vertical)
        if fraction <= 0.:
            self._reset_window_compact()
            return
        if self._compact_snapshot.isNull():
            preview = getattr(self.owner, '_preview_free_island_from_music_surface', None)
            if not callable(preview):
                return
            self._dismiss_audio_output_panel(immediate=True)
            self._dismiss_song_playlist_panel()
            self._close_dj_voice_selector()
            # Publish one complete frame pair. A failed owner paint must not
            # leave a snapshot that suppresses the ordinary player painter.
            try:
                snapshot = self._capture_transition_snapshot()
                target, image = preview(QPointF(self.geometry().center()), self.devicePixelRatioF())
                target, image = QRect(target), QImage(image)
            except Exception:
                self._reset_window_compact()
                self.update()
                return
            if snapshot.isNull() or image.isNull() or not target.isValid():
                self._reset_window_compact()
                self.update()
                return
            self._compact_snapshot = snapshot
            self._compact_target, self._compact_island = target, image
            self._compact_base_geometry = QRect(self.geometry())
            # A Free island may be wider than the narrow page. Extend only
            # the transparent native canvas, keeping the painted page fixed.
            canvas = self.geometry().united(self._compact_target.adjusted(-2, -2, 2, 2))
            self._compact_canvas_geometry = QRect(canvas)
            self._content_layout_resize_suspended = True
            self.setGeometry(canvas)
        self._compact_pressure = (horizontal, vertical)
        self._compact_pressure_edges = frozenset(self._resize_edges)
        self._compact_fraction = fraction
        self.update()
        if fraction >= 1.:
            self._compact_committing = True
            self._moving = False
            self._resize_edges = set()
            self._pressed_window_button = self._pressed_media_command = ''
            self._pressed_lyric_index = None
            self._queue_pressed = self._queue_pressed_token = None
            self._normal_geometry = QRect(self._compact_base_geometry)
            self._compact_animation.setDuration(180)
            self._compact_animation.setStartValue(0.)
            self._compact_animation.setEndValue(1.)
            self._compact_animation.start()

    def _release_window_compact(self):
        if self._compact_committing:
            return True
        if self._compact_fraction <= 0.:
            self._reset_window_compact()
            return False
        self._compact_animation.setDuration(220)
        self._compact_animation.setStartValue(self._compact_fraction)
        self._compact_animation.setEndValue(0.)
        self._compact_animation.start()
        return True

    def _advance_window_compact(self, value):
        if self._compact_committing:
            self._compact_finish = float(value)
        else:
            self._compact_fraction = float(value)
        self.update()

    def _finish_window_compact(self):
        if self._compact_committing:
            callback = getattr(self.owner, '_commit_free_island_from_music_surface', None)
            center = QPointF(self._compact_base_geometry.center())
            target = QRect(self._compact_target)
            self._compact_committing = False
            try:
                if callable(callback):
                    callback(center, target=target)
            finally:
                self._reset_window_compact()
        else:
            self._reset_window_compact()
            self.update()

    def _paint_window_compact(self):
        if self._compact_snapshot.isNull() or self._capturing_transition_snapshot:
            return False
        fraction = self._compact_fraction
        eased = fraction * fraction * (3. - 2. * fraction)
        with QPainter(self) as painter:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            base = QRectF(self._compact_base_geometry)
            base.translate(-self.x(), -self.y())
            clip = QPainterPath()
            radius = self._window_corner_radius(self._compact_base_geometry.size())
            clip.addRoundedRect(base, radius, radius)
            painter.save()
            painter.setClipPath(clip)
            painter.setOpacity(1. - self._compact_finish)
            painter.drawImage(base, self._compact_snapshot)
            self._paint_window_compression_glow(painter, base, fraction)
            painter.restore()
            painter.setOpacity(min(1., fraction * 2.5))
            target = QRectF(self._compact_target)
            target.translate(-self.x(), -self.y())
            center = target.center()
            scale = .72 + .28 * eased
            target.setSize(target.size() * scale)
            target.moveCenter(center)
            painter.drawImage(target, self._compact_island)
        return True

    def _paint_window_compression_glow(self, painter, base, fraction):
        """Advance a feathered white pressure front from the dragged edges.

        The untouched center retains the original scene pixels. Rebound
        scales these same fronts backwards, rather than fading a page veil.
        """
        horizontal, vertical = self._compact_pressure
        peak = max(horizontal, vertical)
        if peak <= 0. or fraction <= 0.:
            return
        rebound = min(1., fraction / peak)
        edges = self._compact_pressure_edges
        for axis, pressure, negative, positive in (
                ('x', horizontal, 'left', 'right'), ('y', vertical, 'top', 'bottom')):
            progress = pressure * rebound
            if progress <= 0.:
                continue
            # A direct (non-pointer) preview keeps a symmetrical pressure
            # front; actual drags follow only their pressed edge/corner.
            active = edges.intersection((negative, positive))
            if not active:
                active = (negative, positive)
            extent = base.width() if axis == 'x' else base.height()
            depth = extent * (.025 + .44 * progress)
            for edge in active:
                band = QRectF(base)
                if axis == 'x':
                    start = QPointF(base.left() if edge == negative else base.right(), base.center().y())
                    end = start + QPointF(depth if edge == negative else -depth, 0.)
                    band.setLeft(start.x() if edge == negative else end.x())
                    band.setWidth(depth)
                else:
                    start = QPointF(base.center().x(), base.top() if edge == negative else base.bottom())
                    end = start + QPointF(0., depth if edge == negative else -depth)
                    band.setTop(start.y() if edge == negative else end.y())
                    band.setHeight(depth)
                gradient = QLinearGradient(start, end)
                alpha = round(240 * progress ** .55)
                for stop, strength in ((0., 1.), (.22, .94), (.56, .48), (.82, .10), (1., 0.)):
                    gradient.setColorAt(stop, QColor(255, 255, 255, round(alpha * strength)))
                painter.fillRect(band, gradient)
