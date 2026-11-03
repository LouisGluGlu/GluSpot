"""Width-motion bitmap handoff for the shared music lyric column (t160)."""
from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QTransform


class LyricResizeProbeReady(Exception):
    """Stop the shared painter once it has resolved its final shaping inputs."""


class MusicLyricResizeMixin:
    LYRIC_RESIZE_SETTLE_MS = 150
    LYRIC_RESIZE_CROSSFADE_MS = 140

    def _init_lyric_resize(self):
        self._lyric_relayout_count = 0
        self._lyric_resize_frozen = False
        self._lyric_resize_capturing = False
        self._lyric_resize_probe = False
        self._lyric_resize_request = None
        self._lyric_resize_last_request = None
        self._lyric_resize_last_geometry = None
        self._lyric_resize_last_display = ([], [])
        self._lyric_resize_last_layers = ()
        self._lyric_resize_last_layouts = ((), ())
        self._lyric_resize_snapshot = QImage()
        self._lyric_resize_source_rect = QRectF()
        self._lyric_resize_wrap_signature = ()
        self._lyric_resize_reuse_scale = None
        self._lyric_resize_reposition_pending = False
        self._lyric_resize_crossfade_at = None
        self._lyric_resize_last_width = None
        self._lyric_resize_was_moving = False
        self._lyric_resize_settle_timer = QTimer(self)
        self._lyric_resize_settle_timer.setSingleShot(True)
        self._lyric_resize_settle_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._lyric_resize_settle_timer.timeout.connect(self._settle_lyric_resize)
        self._lyric_resize_motion_timer = QTimer(self)
        self._lyric_resize_motion_timer.setInterval(16)
        self._lyric_resize_motion_timer.timeout.connect(self._sync_lyric_resize_motion)
        self._lyric_resize_fade_timer = QTimer(self)
        self._lyric_resize_fade_timer.setInterval(16)
        self._lyric_resize_fade_timer.timeout.connect(self._advance_lyric_resize_fade)

    @property
    def lyric_relayout_count(self):
        """Actual full-song build starts, including cold builds; read only."""
        return self._lyric_relayout_count

    def _lyric_width_in_motion(self):
        transition = getattr(self, '_width_transition', None)
        return (getattr(self, '_width_resize_marks', None) is not None
                or getattr(transition, 'active', False)
                or getattr(self, '_width_chrome_active', False))

    def _note_lyric_width_event(self, event):
        if not hasattr(self, '_lyric_resize_last_width'):
            return
        width = event.size().width()
        if width == self._lyric_resize_last_width:
            return  # The narrow shell forwards this same event to the base.
        self._lyric_resize_last_width = width
        if (event.oldSize().width() <= 0 or self._lyric_resize_last_request is None
                or not self.isVisible() or self._content_layout_resize_suspended
                or self._pending_fullscreen is not None or self._island_returning):
            return
        self._enter_lyric_resize()
        self._lyric_resize_settle_timer.stop()
        self._lyric_resize_was_moving = self._lyric_width_in_motion()
        if not self._lyric_resize_was_moving:
            self._lyric_resize_settle_timer.start(self.LYRIC_RESIZE_SETTLE_MS)

    def _enter_lyric_resize(self):
        if (self._lyric_resize_frozen or self._lyric_resize_last_request is None
                or not self.isVisible() or self._content_layout_resize_suspended
                or self._pending_fullscreen is not None or self._island_returning):
            return
        self._lyric_resize_fade_timer.stop()
        self._lyric_resize_crossfade_at = None
        # Render only the previous column, using its last resolved pose and
        # existing layouts. This avoids QWidget.grab()'s recursive resize paint
        # and excludes chrome/background/cover from the snapshot.
        geometry = self._lyric_resize_last_geometry
        source = QRectF(geometry['column']).intersected(self._lyric_resize_last_request[0])
        if source.isEmpty():
            return
        ratio = self.devicePixelRatioF()
        image = QImage(max(1, round(source.width() * ratio)),
                       max(1, round(source.height() * ratio)),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        self._lyric_resize_capturing = True
        try:
            with QPainter(image) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.translate(-source.left(), -source.top())
                rect, kwargs = self._lyric_resize_last_request
                self._paint_live_lyric_column(painter, rect, **kwargs)
        finally:
            self._lyric_resize_capturing = False
        self._lyric_resize_snapshot = image
        self._lyric_resize_source_rect = source
        self._lyric_resize_w0 = source.width()
        playback_position = getattr(self, 'playback_position', None)
        self._lyric_resize_playback_anchor = float(
            playback_position() if callable(playback_position)
            else getattr(self.owner, 'media_position_seconds', 0.) or 0.)
        self._lyric_resize_track = self._visual_track_identity()
        self._lyric_resize_wrap_signature = self._current_lyric_wrap_signature()
        self._lyric_resize_song_key = self._song_layout_key
        self._lyric_resize_frozen = True
        pin = getattr(self, '_lyric_resize_backdrop_scene', None)
        if callable(pin):
            pin()
        self._disable_resize_lyric_hits()
        self._lyric_resize_motion_timer.start()

    def _sync_lyric_resize_motion(self):
        moving = self._lyric_width_in_motion()
        transition = getattr(self, '_width_transition', None)
        # Marks sustain a gesture only after its first real width event.
        # A split/merge moves the column even after the pointer is released.
        animating = getattr(transition, 'active', False) or getattr(self, '_width_chrome_active', False)
        if animating and not self._lyric_resize_frozen:
            self._enter_lyric_resize()
        if not self._lyric_resize_frozen:
            return
        if moving:
            self._lyric_resize_settle_timer.stop()
        elif self._lyric_resize_was_moving:
            self._lyric_resize_settle_timer.start(self.LYRIC_RESIZE_SETTLE_MS)
        self._lyric_resize_was_moving = moving

    def _cancel_lyric_resize(self):
        self._lyric_resize_settle_timer.stop()
        self._lyric_resize_motion_timer.stop()
        self._lyric_resize_fade_timer.stop()
        self._lyric_resize_frozen = False
        self._lyric_resize_was_moving = False
        self._lyric_resize_crossfade_at = None
        self._lyric_resize_snapshot = QImage()
        self._lyric_resize_reuse_scale = None
        self._lyric_resize_reposition_pending = False
        if hasattr(self, '_lyric_resize_background'):
            self._lyric_resize_background = None

    def _same_lyric_resize_content(self, args, kwargs):
        key = self._lyric_resize_song_key
        if key is None:
            return False
        def same_font(source, target):
            if source is None or target is None:
                return source is None and target is None
            before = QFont()
            before.fromString(source)
            after = QFont(target)
            before.setPixelSize(1)
            after.setPixelSize(1)
            return before.toString() == after.toString()

        return (same_font(key[1], args[1])
                and same_font(key[5], kwargs.get('secondary_font'))
                and same_font(key[8], kwargs.get('parenthetical_font'))
                and tuple(args[0]) == key[0]
                and tuple(kwargs.get('secondary_lines', ())) == key[4]
                and tuple(kwargs.get('parenthetical_lines', ())) == key[6]
                and tuple(kwargs.get('parenthetical_translation_lines', ())) == key[7]
                and kwargs.get('row_right_aligned', ()) == key[10]
                and kwargs.get('pair_starts', ()) == key[11])

    def _disable_resize_lyric_hits(self):
        self._lyric_hit_rects.clear()
        self._lyric_word_hit_rects.clear()
        self._pressed_lyric_index = self._pressed_lyric_word = None
        self._pressed_lyric_track_key = None

    @staticmethod
    def _lyric_layout_signature(layout):
        return (layout.lineCount(), tuple(
            (layout.lineAt(i).textStart(), layout.lineAt(i).textLength())
            for i in range(layout.lineCount())))

    def _current_lyric_wrap_signature(self):
        return tuple(tuple(self._lyric_layout_signature(item[0]) if item else None
                           for item in group) for group in (
            self._song_layouts, self._song_secondary_layouts,
            self._song_parenthetical_layouts, self._song_parenthetical_translation_layouts))

    def _probe_lyric_wrap_signature(self, args, kwargs):
        lines, font, width, _gap = args
        widths = kwargs.get('row_widths', ())
        right = kwargs.get('row_right_aligned', ())
        groups = []
        for texts, row_font in ((lines, font),
                (kwargs.get('secondary_lines', ()), kwargs.get('secondary_font')),
                (kwargs.get('parenthetical_lines', ()), kwargs.get('parenthetical_font')),
                (kwargs.get('parenthetical_translation_lines', ()), kwargs.get('secondary_font'))):
            signatures = []
            for index in range(len(lines)):
                text = texts[index] if index < len(texts) else ''
                if row_font is None or (not text and texts is not lines):
                    signatures.append(None)
                    continue
                layout, _height = self._create_wrapped_lyric_layout(
                    text, row_font, widths[index] if index < len(widths) else width,
                    align_right=bool(right[index]) if index < len(right) else False)
                signatures.append(self._lyric_layout_signature(layout))
            groups.append(tuple(signatures))
        return tuple(groups)

    def _settle_lyric_resize(self):
        if not self._lyric_resize_frozen or self._closing_for_shutdown:
            return
        if self._lyric_width_in_motion():
            self._lyric_resize_was_moving = True
            return
        request = self._lyric_resize_request or self._lyric_resize_last_request
        rect, kwargs = request
        rect = QRectF(self.rect())
        kwargs = dict(kwargs, paint_region=None, previous=None, transition=1.)
        self._lyric_resize_probe_inputs = None
        self._lyric_resize_probe = True
        # The ordinary painter resolves translations, duet columns and fonts;
        # stop before shaping, touching caches or painting live lyric ink.
        scratch = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
        try:
            with QPainter(scratch) as painter:
                try:
                    self._paint_live_lyric_column(painter, rect, **kwargs)
                except LyricResizeProbeReady:
                    pass
        finally:
            self._lyric_resize_probe = False
        inputs = self._lyric_resize_probe_inputs
        same = False
        if (inputs is not None and self._lyric_resize_track == self._visual_track_identity()
                and self._same_lyric_resize_content(*inputs)):
            same = self._probe_lyric_wrap_signature(*inputs) == self._lyric_resize_wrap_signature
        self._lyric_resize_frozen = False
        self._lyric_resize_motion_timer.stop()
        if same:
            # Font changes reuse the old shapes under a uniform font transform;
            # plain width changes just reposition the cached lines.
            final_font = inputs[0][1]
            old_font = self._song_layouts[0][0].font() if self._song_layouts else final_font
            if old_font != final_font:
                self._lyric_resize_reuse_scale = self._lyric_resize_last_geometry['scale']
            self._lyric_resize_reposition_pending = True
        else:
            self._lyric_resize_reuse_scale = None
        # Reuse the existing motion rebase hook (width-only keeps its scale).
        self._rebase_snapshot_lyric_motion()
        refresh = getattr(self, '_refresh_lyric_resize_backdrop', None)
        if callable(refresh):
            refresh()
        # Resolve the final layout synchronously on this same settle tick.
        # The wrapper suppresses deferred batching for this one commit.
        self._lyric_resize_committing = True
        try:
            if inputs is not None and self._lyric_resize_reuse_scale is None:
                # The probe already resolved the final inputs. Commit shapes
                # or positions directly; a discarded second lyric paint here
                # needlessly prepares all live ink before the visible frame.
                self._song_lyric_layouts(*inputs[0], **inputs[1])
            else:
                with QPainter(scratch) as painter:
                    self._paint_live_lyric_column(painter, rect, **kwargs)
        finally:
            self._lyric_resize_committing = False
        if not same:
            self._lyric_resize_crossfade_at = time.monotonic()
            self._lyric_resize_fade_timer.start()
        else:
            self._lyric_resize_snapshot = QImage()
        self.update()

    def _lyric_resize_target_rect(self, rect, kwargs):
        if self._effective_content_layout() in {'cover', 'playlist'}:
            return QRectF()
        if self._effective_content_layout() == 'cover_lyrics':
            return QRectF(kwargs.get('painted_cover') or self._current_cover_rect(time.monotonic()))
        return self._content_column_geometry(rect)[2]

    def _draw_lyric_resize_snapshot(self, painter, target, opacity=1.):
        if self._lyric_resize_snapshot.isNull() or target.isEmpty():
            return
        source = self._lyric_resize_source_rect
        transform = QTransform()
        transform.translate(target.left(), target.top())
        transform.scale(target.width() / source.width(), target.height() / source.height())
        painter.save()
        painter.setClipRect(target, Qt.ClipOperation.IntersectClip)
        painter.setWorldTransform(transform, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(painter.opacity() * opacity)
        painter.drawImage(QPointF(), self._lyric_resize_snapshot)
        painter.restore()

    def _advance_lyric_resize_fade(self):
        if (self._lyric_resize_crossfade_at is None or
                (time.monotonic() - self._lyric_resize_crossfade_at) * 1000 >= self.LYRIC_RESIZE_CROSSFADE_MS):
            self._lyric_resize_crossfade_at = None
            self._lyric_resize_snapshot = QImage()
            self._lyric_resize_fade_timer.stop()
        self.update()

    def _paint_lyric_column(self, painter, rect, **kwargs):
        self._lyric_resize_request = (QRectF(rect), dict(kwargs))
        self._sync_lyric_resize_motion()
        target = self._lyric_resize_target_rect(rect, kwargs)
        if self._effective_content_layout() == 'cover_lyrics' and not kwargs.get('navigation_capture', False):
            self._paint_lyric_cover_shade(painter, target)
        if self._lyric_resize_frozen:
            self._disable_resize_lyric_hits()
            self._draw_lyric_resize_snapshot(painter, target)
            return
        if self._lyric_resize_crossfade_at is not None:
            fraction = min(1., (time.monotonic() - self._lyric_resize_crossfade_at) * 1000 / self.LYRIC_RESIZE_CROSSFADE_MS)
            fraction = QEasingCurve(QEasingCurve.Type.InOutCubic).valueForProgress(fraction)
            if not target.isEmpty():
                # Weight premultiplied endpoints before a single SourceOver.
                # Two SourceOver draws would dim coincident ink at midpoint.
                ratio = self.devicePixelRatioF()
                live = QImage(max(1, round(target.width() * ratio)), max(1, round(target.height() * ratio)),
                              QImage.Format.Format_ARGB32_Premultiplied)
                live.setDevicePixelRatio(ratio)
                live.fill(Qt.GlobalColor.transparent)
                with QPainter(live) as ink:
                    ink.setRenderHint(QPainter.RenderHint.Antialiasing)
                    ink.translate(-target.left(), -target.top())
                    self._paint_live_lyric_column(ink, rect, **kwargs)
                with QPainter(live) as weight:
                    weight.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                    weight.fillRect(QRectF(0., 0., target.width(), target.height()),
                                    QColor(255, 255, 255, round(255 * fraction)))
                blend = QImage(live.size(), live.format())
                blend.setDevicePixelRatio(ratio)
                blend.fill(Qt.GlobalColor.transparent)
                with QPainter(blend) as mix:
                    mix.translate(-target.left(), -target.top())
                    self._draw_lyric_resize_snapshot(mix, target, 1. - fraction)
                    mix.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                    mix.setOpacity(1.)
                    mix.drawImage(target.topLeft(), live)
                painter.save()
                painter.setClipRect(target, Qt.ClipOperation.IntersectClip)
                painter.drawImage(target.topLeft(), blend)
                painter.restore()
        else:
            self._paint_live_lyric_column(painter, rect, **kwargs)
        self._lyric_resize_last_request = (QRectF(rect), dict(kwargs))
