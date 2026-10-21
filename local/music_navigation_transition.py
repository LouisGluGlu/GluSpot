"""Bounded snapshot handoffs with an independent, continuous album cover."""
from __future__ import annotations

from dataclasses import dataclass, field
import math

from PySide6.QtCore import QEvent, QEasingCurve, QPointF, QRect, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget


def _bounded_image(image):
    image = QImage(image)
    if max(image.width(), image.height()) > 1600:
        image = image.scaled(1600, 1600, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return image


def _resume_cover(resume):
    if callable(resume):
        try:
            resume()
        except RuntimeError:
            pass


@dataclass
class NavigationFrame:
    geometry: QRect
    image: QImage
    cover_rect: QRectF = field(default_factory=QRectF)
    cover_image: QImage = field(default_factory=QImage)
    cover_angle: float = 0.
    background: QImage = field(default_factory=QImage)
    resume: object = None
    cover_roundness: float = .055

    def __iter__(self):
        # Preserve the original two-value capture interface for simple pages.
        return iter((self.geometry, self.image))

    @property
    def has_cover(self):
        return self.cover_rect.isValid() and not self.cover_image.isNull()

    def release(self):
        resume, self.resume = self.resume, None
        _resume_cover(resume)


def capture_navigation_frame(window, *, separate_cover=False, cover_state=None, freeze_cover=False):
    if not isinstance(window, QWidget) or not window.isVisible():
        return None
    resume = None
    roundness = .055
    if separate_cover:
        if cover_state is None:
            freeze = getattr(window, "freeze_navigation_cover", None) if freeze_cover else None
            getter = freeze if callable(freeze) else getattr(window, "navigation_cover_state", None)
            cover_state = getter() if callable(getter) else None
            if callable(freeze):
                resume = getattr(window, "resume_navigation_cover", None)
            if callable(getattr(window, "freeze_navigation_cover", None)):
                roundness = .5
        cover_rect, cover_image, angle = cover_state or (QRect(), QImage(), 0.)
        try:
            getter = getattr(window, "navigation_background_frame", None)
            image = getter(excluded_cover_rect=QRect(cover_rect)) if callable(getter) else window.grab().toImage()
            getter = getattr(window, "navigation_backdrop_frame", None)
            backdrop = getter() if callable(getter) else QImage()
            if backdrop.isNull():
                # A first visible frame can precede its scene cache. Keep the
                # foreground transparent and use Qt's existing window color
                # until a genuine cached backdrop is available.
                backdrop = QImage(1, 1, QImage.Format.Format_RGB32)
                backdrop.fill(window.palette().window().color())
        except RuntimeError:
            _resume_cover(resume)
            return None
    else:
        cover_rect, cover_image, angle = QRect(), QImage(), 0.
        image, backdrop = window.grab().toImage(), QImage()
    image = _bounded_image(image)
    if image.isNull():
        _resume_cover(resume)
        return None
    return NavigationFrame(QRect(window.geometry()), image, QRectF(cover_rect),
        _bounded_image(cover_image), float(angle), _bounded_image(backdrop), resume, roundness)


class MusicNavigationTransition(QWidget):
    """Freeze each page once; move its cover independently of page controls."""
    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
                         | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._origin = QImage()
        self._target = QImage()
        self._origin_frame = self._target_frame = None
        self._cover_transition = False
        self._destination = None
        self._destination_opacity = 1.
        self._destination_enabled = True
        self._fraction = 0.
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(260)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._advance)
        self._animation.finished.connect(self.cancel)

    @staticmethod
    def _rect_between(a, b, fraction):
        return QRectF(*(x + (y - x) * fraction for x, y in zip(
            (a.x(), a.y(), a.width(), a.height()), (b.x(), b.y(), b.width(), b.height()))))

    def start(self, frame, destination):
        self.cancel()
        if not frame or not isinstance(destination, QWidget) or not destination.isVisible():
            if isinstance(frame, NavigationFrame):
                frame.release()
            return False
        if not isinstance(frame, NavigationFrame):
            frame = NavigationFrame(QRect(frame[0]), QImage(frame[1]))
        target = capture_navigation_frame(destination, separate_cover=frame.has_cover, freeze_cover=frame.has_cover)
        if target is None or not frame.geometry.isValid() or not target.geometry.isValid() or frame.image.isNull():
            frame.release()
            if target is not None:
                target.release()
            return False
        if frame.has_cover and target.cover_rect.isValid() and target.cover_image.isNull():
            target.cover_image = QImage(frame.cover_image)
        self._origin_frame, self._target_frame = frame, target
        self._origin, self._target = frame.image, target.image
        self._cover_transition = frame.has_cover and target.has_cover
        self._destination = destination
        self._destination_opacity = destination.windowOpacity()
        self._destination_enabled = destination.isEnabled()
        self._origin_geometry, self._target_geometry = QRect(frame.geometry), QRect(target.geometry)
        self._fraction = 0.
        if self._cover_transition:
            self.setGeometry(frame.geometry.united(target.geometry))
            self._animation.setDuration(560)
            self._animation.setEasingCurve(QEasingCurve.Type.Linear)
        else:
            self.setGeometry(frame.geometry)
            self._animation.setDuration(260)
            self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        destination.installEventFilter(self)
        destination.setEnabled(False)
        destination.setWindowOpacity(0.)
        self.show()
        self.raise_()
        self._animation.setStartValue(0.)
        self._animation.setEndValue(1.)
        self._animation.start()
        return True

    def _cover_progress(self):
        p = self._fraction * self._fraction * (3. - 2. * self._fraction)
        return p + .045 * math.sin(p * math.pi * 2.) * math.sin(p * math.pi)

    def _cover_rect_at(self):
        return self._rect_between(self._origin_frame.cover_rect, self._target_frame.cover_rect, self._cover_progress())

    def _cover_angle_at(self):
        a, b = self._origin_frame.cover_angle, self._target_frame.cover_angle
        delta = (b - a + 180.) % 360. - 180.
        return a + delta * self._cover_progress()

    def _advance(self, value):
        fraction = self._fraction = max(0., min(1., float(value)))
        if not self._cover_transition:
            self.setGeometry(self._rect_between(self._origin_geometry, self._target_geometry, fraction).toRect())
        self.update()

    def eventFilter(self, watched, event):
        if watched is self._destination and event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
            self.cancel()
        return super().eventFilter(watched, event)

    def cancel(self):
        self._animation.stop()
        destination, self._destination = self._destination, None
        if destination is not None:
            try:
                destination.removeEventFilter(self)
                destination.setWindowOpacity(self._destination_opacity)
                destination.setEnabled(self._destination_enabled)
            except RuntimeError:
                pass
        for frame in (self._origin_frame, self._target_frame):
            if frame is not None:
                frame.release()
        self._origin_frame = self._target_frame = None
        self._cover_transition = False
        self.hide()
        self._origin = self._target = QImage()

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            if self._cover_transition:
                self._paint_cover_handoff(painter)
                return
            rect = QRectF(self.rect())
            path = QPainterPath(); path.addRoundedRect(rect, 26, 26)
            painter.setClipPath(path)
            painter.setOpacity(1. - self._fraction); painter.drawImage(rect, self._origin)
            painter.setOpacity(self._fraction); painter.drawImage(rect, self._target)

    def _paint_cover_handoff(self, painter):
        origin, target = self._origin_frame, self._target_frame
        f, p = self._fraction, self._cover_progress()
        offset = QPointF(self.geometry().topLeft())
        window = self._rect_between(origin.geometry, target.geometry, p).translated(-offset)
        clip = QPainterPath(); clip.addRoundedRect(window, 24., 24.)
        painter.setClipPath(clip)
        # The scenery grows independently of the native window's bounds, so
        # entering a smaller playback window still feels like moving into the
        # album rather than shrinking the entire library into that window.
        source_background = origin.background if not origin.background.isNull() else target.background
        if source_background.isNull():
            source_background = origin.image
        target_background = target.background if not target.background.isNull() else source_background
        for image, alpha, zoom in ((source_background, 1., 1. + .18 * p),
                                   (target_background, p, 1. + .18 * (1. - p))):
            background_rect = QRectF(window)
            background_rect.setSize(window.size() * zoom)
            background_rect.moveCenter(window.center())
            painter.setOpacity(alpha)
            painter.drawImage(background_rect, image)
        painter.setOpacity(max(0., 1. - f / .38))
        painter.drawImage(QRectF(origin.geometry).translated(-offset), origin.image)
        painter.setOpacity(max(0., min(1., (f - .28) / .72)))
        painter.drawImage(QRectF(target.geometry).translated(-offset), target.image)
        cover = self._cover_rect_at().translated(-offset)
        painter.setOpacity(1.)
        painter.translate(cover.center()); painter.rotate(self._cover_angle_at())
        cover.moveCenter(QPointF())
        rounding = origin.cover_roundness + (target.cover_roundness - origin.cover_roundness) * p
        cover_clip = QPainterPath(); cover_clip.addRoundedRect(cover, cover.width() * rounding, cover.height() * rounding)
        painter.setClipPath(cover_clip, Qt.ClipOperation.IntersectClip)
        def square(image):
            side = min(image.width(), image.height())
            return QRectF((image.width() - side) / 2., (image.height() - side) / 2., side, side)
        painter.drawImage(cover, origin.cover_image, square(origin.cover_image))
        painter.setOpacity(p)
        painter.drawImage(cover, target.cover_image, square(target.cover_image))
