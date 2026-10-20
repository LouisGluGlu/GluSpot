"""Bounded snapshot handoffs with an independent, continuous album cover."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import weakref

from PySide6.QtCore import QEvent, QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from local.liquid_glass import synchronous_glass_capture
from local.music_navigation_native import NativeNavigationGeometryGuard


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
    # Only an interrupted overlay needs a fractional window pose and incoming
    # velocity. Ordinary page captures keep their original interface/raster.
    visible_geometry: QRectF = field(default_factory=QRectF)
    velocity: tuple[float, ...] = ()
    background_geometry: QRectF = field(default_factory=QRectF)
    freeze_owner: object = field(default=None, repr=False)

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
    # A capture must contain complete glass, including pages which do not
    # inherit GlassCaptureMixin. QWidget capture remains on Qt's GUI thread.
    with synchronous_glass_capture(window):
        return _capture_navigation_frame(window, separate_cover=separate_cover,
            cover_state=cover_state, freeze_cover=freeze_cover)


def _capture_navigation_frame(window, *, separate_cover, cover_state, freeze_cover):
    resume = None
    roundness = .055
    frozen_cover_state = None
    freeze = getattr(window, "freeze_navigation_cover", None) if freeze_cover else None
    if callable(freeze):
        resume = getattr(window, "resume_navigation_cover", None)
        try:
            frozen_cover_state = freeze()
        except RuntimeError:
            _resume_cover(resume)
            return None
    if separate_cover:
        if cover_state is None:
            getter = getattr(window, "navigation_cover_state", None)
            cover_state = frozen_cover_state if callable(freeze) else getter() if callable(getter) else None
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
        try:
            image, backdrop = window.grab().toImage(), QImage()
        except RuntimeError:
            _resume_cover(resume)
            return None
    image = _bounded_image(image)
    if image.isNull():
        _resume_cover(resume)
        return None
    return NavigationFrame(QRect(window.geometry()), image, QRectF(cover_rect),
        _bounded_image(cover_image), float(angle), _bounded_image(backdrop), resume, roundness,
        freeze_owner=weakref.ref(window) if callable(resume) else None)


class MusicNavigationTransition(QWidget):
    """Freeze each page once; move its cover independently of page controls."""
    SCENE_RASTER_LIMIT = 1600

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
        self._destination_geometry = QRect()
        self._pending_destination_geometry = QRect()
        self._restoring_geometry_owner = None
        self._restoring_geometry = QRect()
        self._restoring_geometry_generation = -1
        self._restoring_page_state = None
        self._geometry_applying = False
        self._native_geometry_handles = {}
        self._native_geometry_hwnds = {}
        self._native_geometry_guard = NativeNavigationGeometryGuard()
        self.destroyed.connect(self._native_geometry_guard.close)
        self._settling_destination = None
        self._settling_geometry = QRect()
        self._placement_settle_timer = QTimer(self)
        self._placement_settle_timer.setSingleShot(True)
        self._placement_settle_timer.setInterval(200)
        self._placement_settle_timer.timeout.connect(self._finish_settling_destination)
        self._destination_opacity = 1.
        self._destination_enabled = True
        self._fraction = 0.
        self._scene_canvas = QImage()
        self._scene_fraction = None
        self._scene_scale = 1.
        self._motion_start = self._motion_end = self._incoming_velocity = ()
        self._request_generation = 0
        self._pending_start = None
        self._prepare_scheduled = False
        self._waiting_initial = False
        self._origin_presented = False
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(260)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._advance)
        self._animation.finished.connect(self._finished)

    @staticmethod
    def _rect_between(a, b, fraction):
        return QRectF(*(x + (y - x) * fraction for x, y in zip(
            (a.x(), a.y(), a.width(), a.height()), (b.x(), b.y(), b.width(), b.height()))))

    def start(self, frame, destination):
        """Immediate compatibility path; production navigation uses request_start.

        QWidget capture remains GUI work. A replacement nevertheless starts
        from the actual visible overlay, rather than a hidden page's origin.
        """
        if not frame or not isinstance(destination, QWidget) or not destination.isVisible():
            if isinstance(frame, NavigationFrame):
                frame.release()
            return False
        if not isinstance(frame, NavigationFrame):
            frame = NavigationFrame(QRect(frame[0]), QImage(frame[1]))
        self._request_generation += 1
        self._release_settling_destination()
        self._drop_pending()
        # Release freeze callbacks before acquiring another capture of the
        # same page: boolean page freezes are not reference-counted leases.
        self._release_destination_freeze(destination)
        # Both handoff styles own an independent snapshot. A library target
        # must also pause invisible background work during a plain 260ms fade.
        target = capture_navigation_frame(destination, separate_cover=frame.has_cover, freeze_cover=True)
        if self.isVisible() and self._origin_frame is not None:
            visible = self._visible_frame(flatten=target is not None and not target.has_cover)
            frame.release()
            frame = visible
        return self._start_prepared(frame, destination, target)

    def origin_frame(self, window, **capture_options):
        """Take over a live overlay before controllers hide its former page."""
        if self.isVisible() and self._origin_frame is not None:
            self._restore_destination()
            self._drop_pending()
            # A cheap request descriptor only. At ready time we capture the
            # then-current composition/velocity, after the old animation has
            # continued normally while its new target was being prepared.
            return NavigationFrame(QRect(self.geometry()), QImage(self._origin),
                QRectF(self._cover_rect_at()) if self._cover_transition else QRectF(),
                QImage(self._origin_frame.cover_image) if self._cover_transition else QImage())
        return capture_navigation_frame(window, **capture_options)

    def request_start(self, frame, destination):
        """Show a valid origin now, prepare one latest target on Qt's thread.

        Queuing provides an opportunity to present/input before the capture;
        it does not make QWidget.grab asynchronous or remove its raster cost.
        """
        if not frame or not isinstance(destination, QWidget) or not destination.isVisible():
            if isinstance(frame, NavigationFrame):
                frame.release()
            return False
        if not isinstance(frame, NavigationFrame):
            frame = NavigationFrame(QRect(frame[0]), QImage(frame[1]))
        self._request_generation += 1
        self._release_settling_destination()
        self._drop_pending()
        self._restore_destination()
        saved = self._page_state(destination)
        self._pending_start = (self._request_generation, frame, destination, saved)
        request = self._pending_start
        self._pending_destination_geometry = QRect(destination.geometry())
        destination.installEventFilter(self)
        destination.destroyed.connect(self._pending_destination_destroyed)
        self._observe_native_geometry(destination)
        if self._pending_start is not request:
            return False
        destination.setEnabled(False)
        if self._pending_start is not request:
            return False
        destination.setWindowOpacity(0.)
        if self._pending_start is not request:
            # Native alpha/placement changes can synchronously invalidate
            # the handoff (screen/DPI/state) or start a replacement request.
            # Its cancellation already restored the real page; do not show
            # an orphaned hold layer after that newer decision.
            return False
        if self._origin_frame is None:
            self._waiting_initial = True
            self._origin_presented = False
            self._origin_frame = self._target_frame = frame
            self._origin = self._target = frame.image
            self._cover_transition = frame.has_cover
            self._fraction = 0.
            self._motion_start = self._motion_end = self._incoming_velocity = ()
            self.setGeometry(frame.geometry)
            self._prepare_scene_canvas()
        self.show()
        if self._pending_start is not request:
            return False
        self.raise_()
        self.update()
        self._schedule_prepare()
        return True

    def _schedule_prepare(self):
        if self._pending_start is not None and self._origin_presented and not self._prepare_scheduled:
            self._prepare_scheduled = True
            QTimer.singleShot(0, self._prepare_pending)

    def _prepare_pending(self):
        self._prepare_scheduled = False
        request = self._pending_start
        if request is None:
            return
        generation, frame, destination, saved = request
        self._release_destination_freeze(destination)
        target = None
        try:
            # Disabling native controls changes their rendered QStyle. Keep
            # input blocked between frames, but capture their original state
            # while the real page is still transparent. A reentrant request
            # owns its own enabled state and must never be changed here.
            destination.setEnabled(saved[1])
            if self._pending_start is request and generation == self._request_generation:
                self._pin_destination_geometry(destination)
                if self._pending_start is request and generation == self._request_generation:
                    target = capture_navigation_frame(destination,
                        separate_cover=self._cover_transition or frame.has_cover, freeze_cover=True)
        except RuntimeError:
            target = None
        finally:
            if self._pending_start is request and generation == self._request_generation:
                try:
                    destination.setEnabled(False)
                except RuntimeError:
                    pass
        if self._pending_start is not request or generation != self._request_generation:
            if target is not None:
                target.release()
            return
        if target is None:
            self._drop_pending()
            # A failed target must not leave input disabled or a permanent
            # frozen overlay in front of the surviving real page.
            self.cancel()
            return
        if self._cover_transition and not target.has_cover:
            visible = self._visible_frame(flatten=True)
        else:
            visible = self._origin_frame if self._waiting_initial else self._visible_frame()
        self._waiting_initial = False
        self._origin_presented = False
        self._drop_pending(restore=False)
        if frame is not self._origin_frame:
            frame.release()
        self._start_prepared(visible, destination, target, saved=saved)

    def _start_prepared(self, frame, destination, target, *, saved=None):
        if target is None or not frame.geometry.isValid() or not target.geometry.isValid() or frame.image.isNull():
            frame.release()
            if target is not None:
                target.release()
            return False
        self._animation.stop()
        self._restore_destination()
        self._release_frames(keep=(frame, target))
        if frame.has_cover and target.cover_rect.isValid() and target.cover_image.isNull():
            target.cover_image = QImage(frame.cover_image)
        self._origin_frame, self._target_frame = frame, target
        self._waiting_initial = False
        self._origin, self._target = frame.image, target.image
        self._cover_transition = frame.has_cover and target.has_cover
        self._destination = destination
        generation = self._request_generation
        self._destination_geometry = QRect(target.geometry)
        self._destination_opacity, self._destination_enabled = saved or self._page_state(destination)
        self._origin_geometry, self._target_geometry = QRect(frame.geometry), QRect(target.geometry)
        self._fraction = 0.
        self._motion_start = self._frame_pose(frame)
        end = list(self._frame_pose(target))
        end[8] = self._motion_start[8] + (end[8] - self._motion_start[8] + 180.) % 360. - 180.
        self._motion_end = tuple(end)
        self._incoming_velocity = frame.velocity if any(abs(value) > 1e-9 for value in frame.velocity) else ()
        if self._cover_transition:
            self.setGeometry(self._motion_bounds() if self._incoming_velocity else frame.geometry.united(target.geometry))
            self._prepare_scene_canvas()
            self._animation.setDuration(560)
            self._animation.setEasingCurve(QEasingCurve.Type.Linear)
        else:
            self.setGeometry(frame.geometry)
            self._animation.setDuration(260)
            self._animation.setEasingCurve(QEasingCurve.Type.Linear if self._incoming_velocity else QEasingCurve.Type.OutCubic)
        destination.installEventFilter(self)
        destination.destroyed.connect(self._destination_destroyed)
        self._observe_native_geometry(destination)
        if generation != self._request_generation or self._destination is not destination:
            return False
        destination.setEnabled(False)
        if generation != self._request_generation or self._destination is not destination:
            return False
        destination.setWindowOpacity(0.)
        if generation != self._request_generation or self._destination is not destination:
            return False
        self.show()
        if generation != self._request_generation or self._destination is not destination:
            return False
        self.raise_()
        self._animation.setStartValue(0.)
        self._animation.setEndValue(1.)
        self._animation.start()
        return True

    def _finished(self):
        if self._pending_start is None:
            self.cancel(settle_destination=True)
        else:
            # A new target is still preparing. Hold this valid final picture
            # instead of exposing an empty/transparent native window.
            self.update()

    @staticmethod
    def _frame_pose(frame):
        rect = frame.visible_geometry if frame.visible_geometry.isValid() else QRectF(frame.geometry)
        return tuple(float(value) for value in (rect.x(), rect.y(), rect.width(), rect.height(),
            frame.cover_rect.x(), frame.cover_rect.y(), frame.cover_rect.width(), frame.cover_rect.height(),
            frame.cover_angle, frame.cover_roundness))

    def _motion_values(self, fraction=None):
        f = self._fraction if fraction is None else fraction
        if not self._incoming_velocity:
            return ()
        duration = (560 if self._cover_transition else 260) / 1000.
        return tuple(a + v * duration * f + (3 * (b-a) - 2 * v * duration) * f*f
                     + (v * duration - 2 * (b-a)) * f*f*f
                     for a, b, v in zip(self._motion_start, self._motion_end, self._incoming_velocity))

    def _window_rect_at(self):
        values = self._motion_values()
        if values:
            return QRectF(values[0], values[1], max(1., values[2]), max(1., values[3]))
        p = self._cover_progress() if self._cover_transition else self._fraction
        a = self._origin_frame.visible_geometry if self._origin_frame.visible_geometry.isValid() else self._origin_frame.geometry
        b = self._target_frame.visible_geometry if self._target_frame.visible_geometry.isValid() else self._target_frame.geometry
        return self._rect_between(a, b, p)

    def _pose_velocity(self):
        f = self._fraction
        duration = (560 if self._cover_transition else 260) / 1000.
        if self._incoming_velocity:
            return tuple((v * duration + 2 * (3*(b-a)-2*v*duration)*f
                + 3*(v*duration-2*(b-a))*f*f) / duration
                for a, b, v in zip(self._motion_start, self._motion_end, self._incoming_velocity))
        if self._cover_transition:
            s = f*f*(3.-2.*f)
            rate = 6.*f*(1.-f) * (1.+.045*(2.*math.pi*math.cos(s*math.pi*2.)*math.sin(s*math.pi)
                    + math.pi*math.sin(s*math.pi*2.)*math.cos(s*math.pi))) / duration
        else:
            rate = 3.*max(0., 1.-f)**(2./3.) / duration
        a, b = self._frame_pose(self._origin_frame), list(self._frame_pose(self._target_frame))
        b[8] = a[8] + (b[8]-a[8]+180.) % 360. - 180.
        return tuple((end-start)*rate for start, end in zip(a, b))

    def _motion_bounds(self):
        # Cubic extrema, including right/bottom, cover incoming overshoot.
        phases = {0., 1.}
        duration = (560 if self._cover_transition else 260) / 1000.
        for indices in ((0,), (1,), (0, 2), (1, 3), (4,), (5,), (4, 6), (5, 7)):
            a = sum(self._motion_start[i] for i in indices)
            b = sum(self._motion_end[i] for i in indices)
            tangent = sum(self._incoming_velocity[i] for i in indices) * duration
            linear, quadratic, cubic = tangent, 3*(b-a)-2*tangent, tangent-2*(b-a)
            if abs(cubic) < 1e-12:
                roots = (-linear/(2*quadratic),) if abs(quadratic) > 1e-12 else ()
            else:
                discriminant = 4*quadratic*quadratic-12*cubic*linear
                roots = ((-2*quadratic+math.sqrt(discriminant))/(6*cubic),
                         (-2*quadratic-math.sqrt(discriminant))/(6*cubic)) if discriminant >= 0 else ()
            phases.update(root for root in roots if 0. < root < 1.)
        bounds = QRectF()
        for phase in phases:
            values = self._motion_values(phase)
            for offset in (0, 4):
                rect = QRectF(*values[offset:offset+4])
                bounds = bounds.united(rect) if bounds.isValid() else rect
        return bounds.adjusted(-3., -3., 3., 3.).toAlignedRect()

    def _cover_progress(self):
        p = self._fraction * self._fraction * (3. - 2. * self._fraction)
        return p + .045 * math.sin(p * math.pi * 2.) * math.sin(p * math.pi)

    def _cover_rect_at(self):
        values = self._motion_values()
        if values:
            return QRectF(values[4], values[5], max(1., values[6]), max(1., values[7]))
        return self._rect_between(self._origin_frame.cover_rect, self._target_frame.cover_rect, self._cover_progress())

    def _cover_angle_at(self):
        values = self._motion_values()
        if values:
            return values[8]
        a, b = self._origin_frame.cover_angle, self._target_frame.cover_angle
        delta = (b - a + 180.) % 360. - 180.
        return a + delta * self._cover_progress()

    def _advance(self, value):
        generation = self._request_generation
        fraction = self._fraction = max(0., min(1., float(value)))
        if self._destination is not None:
            self._pin_destination_geometry(self._destination)
        if generation != self._request_generation or self._origin_frame is None:
            return
        if not self._cover_transition:
            self.setGeometry(self._window_rect_at().toRect())
        self.update()

    def eventFilter(self, watched, event):
        if watched is self._settling_destination and event.type() in (
                QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress,
                QEvent.Type.NonClientAreaMouseButtonPress):
            # The live page takes input immediately. Its next user placement
            # owns the window, even before the native-map grace period ends.
            self._release_settling_destination()
        placement_changed = event.type() in (
            getattr(QEvent.Type, "DevicePixelRatioChange", None),
            getattr(QEvent.Type, "ScreenChangeInternal", None),
        ) or event.type() == QEvent.Type.WindowStateChange
        if placement_changed and self.destination_geometry(watched).isValid():
            # A changed screen/DPI/native window state invalidates snapshot
            # coordinates. Release the handoff rather than pinning an old
            # screen rectangle over the new fullscreen/work-area placement.
            self._pending_destination_geometry = QRect()
            self._destination_geometry = QRect()
            self.cancel()
            return super().eventFilter(watched, event)
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            # A newly mapped native page can receive late platform placement
            # events even after the first paint. The overlay already owns a
            # snapshot in global coordinates; keep its disabled destination
            # at that same rectangle until the real page takes over.
            self._pin_destination_geometry(watched)
        unavailable = event.type() in (QEvent.Type.Hide, QEvent.Type.Close) or (
            event.type() == QEvent.Type.WindowStateChange and isinstance(watched, QWidget) and watched.isMinimized())
        if (self._pending_start is not None and watched is self._pending_start[2]
                and unavailable):
            self.cancel()
        if watched is self._destination and unavailable:
            self.cancel()
        if watched is self._settling_destination and unavailable:
            self._release_settling_destination()
        return super().eventFilter(watched, event)

    def _destination_destroyed(self, _destination=None):
        self.cancel()

    def _pending_destination_destroyed(self, _destination=None):
        self.cancel()

    def _drop_pending(self, *, restore=True):
        pending, self._pending_start = self._pending_start, None
        geometry, self._pending_destination_geometry = self._pending_destination_geometry, QRect()
        if pending is None:
            return
        _generation, frame, destination, saved = pending
        self._unobserve_native_geometry(destination)
        try:
            destination.destroyed.disconnect(self._pending_destination_destroyed)
            destination.removeEventFilter(self)
            if restore:
                self._restore_page(destination, geometry, *saved)
        except RuntimeError:
            pass
        if frame is not self._origin_frame:
            frame.release()

    def hideEvent(self, event):
        # A native hide/close can bypass the controller's usual cancel path.
        # Release the frozen pages instead of ticking an invisible overlay.
        if self._destination is not None or self._origin_frame is not None:
            self.cancel()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.cancel()
        super().closeEvent(event)

    def _restore_destination(self, *, settle=False):
        destination, self._destination = self._destination, None
        geometry, self._destination_geometry = self._destination_geometry, QRect()
        if destination is not None:
            try:
                if settle and geometry.isValid() and destination.isVisible() and not destination.isMinimized():
                    # Restoring native alpha can enqueue a mapping Move
                    # after this call returns. Hold only placement briefly;
                    # snapshots, animation and page clocks release normally.
                    self._settling_destination = destination
                    self._settling_geometry = QRect(geometry)
                else:
                    self._unobserve_native_geometry(destination)
                    destination.destroyed.disconnect(self._destination_destroyed)
                    destination.removeEventFilter(self)
                self._restore_page(destination, geometry, self._destination_opacity, self._destination_enabled)
                if self._settling_destination is destination:
                    self._placement_settle_timer.start()
            except RuntimeError:
                self._release_settling_destination()

    def _release_settling_destination(self):
        destination, self._settling_destination = self._settling_destination, None
        self._settling_geometry = QRect()
        self._placement_settle_timer.stop()
        if destination is not None:
            self._unobserve_native_geometry(destination)
            try:
                destination.destroyed.disconnect(self._destination_destroyed)
                destination.removeEventFilter(self)
            except RuntimeError:
                pass

    def _finish_settling_destination(self):
        destination = self._settling_destination
        generation = self._request_generation
        if destination is not None:
            # A native move may already be applied while its Qt notification
            # is still queued behind this timeout. Check actual coordinates
            # before dropping the lease, not only the cached Move signals.
            self._pin_destination_geometry(destination)
        if generation == self._request_generation and self._settling_destination is destination:
            self._release_settling_destination()

    def destination_geometry(self, window):
        """Global placement owned by this handoff, or an invalid rectangle.

        Controllers must not save a transient native Move as a user resize
        while this overlay owns the page. Each result is a value copy.
        """
        if window is self._restoring_geometry_owner and self._restoring_geometry_generation == self._request_generation:
            return QRect(self._restoring_geometry)
        if window is self._destination:
            return QRect(self._destination_geometry)
        if self._pending_start is not None and window is self._pending_start[2]:
            return QRect(self._pending_destination_geometry)
        if window is self._settling_destination:
            return QRect(self._settling_geometry)
        return QRect()

    def _pin_destination_geometry(self, window):
        if self._geometry_applying:
            return
        geometry = self.destination_geometry(window)
        if not geometry.isValid():
            return
        actual = QRect(window.mapToGlobal(QPoint()), window.size())
        if window.geometry() == geometry and actual == geometry:
            return
        generation = self._request_generation
        self._geometry_applying = True
        try:
            window.setGeometry(geometry)
            if generation != self._request_generation:
                return
            actual = QRect(window.mapToGlobal(QPoint()), window.size())
            if (actual != geometry and self.destination_geometry(window) == geometry
                    and window.windowFlags() & Qt.WindowType.FramelessWindowHint):
                # Native placement may precede Qt's cached QWidget/QWindow
                # geometry update. A same-value setGeometry can then be a
                # no-op. Synchronize the QWindow position to the actual
                # frameless client origin before requesting the held pose.
                handle = window.windowHandle()
                if handle is not None:
                    handle.setPosition(actual.topLeft())
                    if generation == self._request_generation and self.destination_geometry(window) == geometry:
                        handle.setPosition(geometry.topLeft())
        finally:
            self._geometry_applying = False

    def _observe_native_geometry(self, destination):
        self._unobserve_native_geometry(destination)
        handle = destination.windowHandle()
        if handle is not None:
            self._native_geometry_handles[destination] = handle
            for signal in (handle.xChanged, handle.yChanged, handle.widthChanged, handle.heightChanged):
                signal.connect(self._native_geometry_changed)
            # Correct existing placement before preventing unsolicited native
            # moves. Qt's Move signals arrive after Windows applies a move,
            # which is too late to prevent a visible jump at alpha handoff.
            self._pin_destination_geometry(destination)
            if (self._native_geometry_handles.get(destination) is handle
                    and self.destination_geometry(destination).isValid()):
                owner = weakref.ref(self)
                def applying():
                    transition = owner()
                    return transition is None or transition._geometry_applying
                hwnd = int(handle.winId())
                if self._native_geometry_guard.register(hwnd, bypass=applying):
                    self._native_geometry_hwnds[destination] = hwnd

    def _unobserve_native_geometry(self, destination):
        hwnd = self._native_geometry_hwnds.pop(destination, None)
        if hwnd is not None:
            self._native_geometry_guard.unregister(hwnd)
        handle = self._native_geometry_handles.pop(destination, None)
        if handle is not None:
            try:
                for signal in (handle.xChanged, handle.yChanged, handle.widthChanged, handle.heightChanged):
                    signal.disconnect(self._native_geometry_changed)
            except RuntimeError:
                pass

    def _native_geometry_changed(self, _value):
        handle = self.sender()
        for destination, observed in tuple(self._native_geometry_handles.items()):
            if observed is handle:
                self._pin_destination_geometry(destination)

    def _restore_page(self, destination, geometry, opacity, enabled):
        generation = self._request_generation
        def interrupted():
            if generation == self._request_generation:
                return False
            def newer_owner():
                return (destination is self._destination or destination is self._settling_destination
                    or (self._pending_start is not None and destination is self._pending_start[2]))
            # A screen cancellation relinquishes coordinates, but must still
            # restore the surviving page's input/alpha. A replacement which
            # owns this same page is authoritative for those states instead.
            if not newer_owner():
                if destination.windowOpacity() != opacity:
                    destination.setWindowOpacity(opacity)
                if not newer_owner() and destination.isEnabled() != enabled:
                    destination.setEnabled(enabled)
            return True
        previous = (self._restoring_geometry_owner, self._restoring_geometry,
                    self._restoring_geometry_generation, self._restoring_page_state)
        self._restoring_geometry_owner, self._restoring_geometry = destination, geometry
        self._restoring_geometry_generation = generation
        self._restoring_page_state = (opacity, enabled)
        try:
            self._pin_destination_geometry(destination)
            if interrupted():
                return
            destination.setWindowOpacity(opacity)
            if interrupted():
                return
            # Changing native alpha can synchronously remap a window. Do not
            # reveal a real page at a different pose from its final snapshot.
            self._pin_destination_geometry(destination)
            if interrupted():
                return
            destination.setEnabled(enabled)
        finally:
            (self._restoring_geometry_owner, self._restoring_geometry,
             self._restoring_geometry_generation, self._restoring_page_state) = previous

    def _page_state(self, destination):
        # A request reentered from alpha restoration inherits the page's
        # intended input/alpha, not its temporary disabled halfway state.
        if destination is self._restoring_geometry_owner and self._restoring_page_state is not None:
            return self._restoring_page_state
        return destination.windowOpacity(), destination.isEnabled()

    def _release_destination_freeze(self, destination):
        # A page's freeze is a boolean, not a lease. Release only an old
        # freeze of the page being captured; unrelated source pages stay
        # frozen until their overlay hands off or is cancelled.
        for frame in (self._origin_frame, self._target_frame):
            if frame is not None and frame.freeze_owner is not None and frame.freeze_owner() is destination:
                frame.release()

    def _release_frames(self, *, keep=()):
        for frame in (self._origin_frame, self._target_frame):
            if frame is not None and all(frame is not retained for retained in keep):
                frame.release()

    def cancel(self, *, settle_destination=False):
        self._release_settling_destination()
        self._request_generation += 1
        generation = self._request_generation
        self._drop_pending()
        if generation != self._request_generation:
            return
        self._animation.stop()
        self._restore_destination(settle=settle_destination)
        if generation != self._request_generation:
            return
        self._release_frames()
        if generation != self._request_generation:
            return
        self._origin_frame = self._target_frame = None
        self._cover_transition = False
        self.hide()
        if generation != self._request_generation:
            return
        self._origin = self._target = QImage()
        self._scene_canvas = QImage()
        self._scene_fraction = None
        self._motion_start = self._motion_end = self._incoming_velocity = ()
        self._waiting_initial = False
        self._origin_presented = False

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            if self._cover_transition:
                self._paint_cover_handoff(painter)
            else:
                rect = QRectF(self.rect())
                path = QPainterPath(); path.addRoundedRect(rect, 26, 26)
                painter.setClipPath(path)
                painter.setOpacity(1. - self._fraction); painter.drawImage(rect, self._origin)
                painter.setOpacity(self._fraction); painter.drawImage(rect, self._target)
        # Only queue preparation after the valid origin has actually been
        # rasterized at least once. This is not a compositor/present fence.
        self._origin_presented = True
        self._schedule_prepare()

    def _paint_cover_handoff(self, painter):
        origin, target = self._origin_frame, self._target_frame
        f, p = self._fraction, self._cover_progress()
        offset = QPointF(self.geometry().topLeft())
        window = self._window_rect_at().translated(-offset)
        clip = QPainterPath(); clip.addRoundedRect(window, 24., 24.)
        painter.setClipPath(clip)
        # Resample/composite page scenery once on a bounded physical-pixel
        # canvas. The native DPR-sized stage then scales one finished scene,
        # while the moving album remains a separate full-resolution layer.
        # Repaints at the same animation time reuse the completed raster.
        if not self._scene_canvas.isNull():
            painter.drawImage(QRectF(self.rect()), self._composited_scene(window, offset, f, p))
        else:
            # Small ordinary-DPR windows are already cheap to paint directly;
            # an intermediate copy would only add work at their native size.
            self._paint_handoff_scene(painter, window, offset, f, p)
        self._paint_handoff_cover(painter, offset, p)

    def _prepare_scene_canvas(self):
        size = self.size()
        self._scene_scale = min(float(self.devicePixelRatioF()),
                                self.SCENE_RASTER_LIMIT / max(1, size.width(), size.height()))
        native_pixels = size.width() * size.height() * self.devicePixelRatioF() ** 2
        if native_pixels <= self.SCENE_RASTER_LIMIT ** 2:
            self._scene_canvas = QImage()
            self._scene_fraction = None
            return
        raster_size = QSize(max(1, round(size.width() * self._scene_scale)),
                            max(1, round(size.height() * self._scene_scale)))
        self._scene_canvas = QImage(raster_size, QImage.Format.Format_ARGB32_Premultiplied)
        self._scene_fraction = None

    def _composited_scene(self, window, offset, f, p):
        if self._scene_canvas.isNull():
            self._prepare_scene_canvas()
        if self._scene_fraction != f:
            self._scene_canvas.fill(Qt.GlobalColor.transparent)
            with QPainter(self._scene_canvas) as scene:
                scene.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                scene.scale(self._scene_scale, self._scene_scale)
                self._paint_handoff_scene(scene, window, offset, f, p)
            self._scene_fraction = f
        return self._scene_canvas

    def _paint_handoff_scene(self, painter, window, offset, f, p):
        self._paint_handoff_background(painter, window, f, p)
        self._paint_handoff_foreground(painter, offset, f)

    def _paint_handoff_background(self, painter, window, f, p):
        origin, target = self._origin_frame, self._target_frame
        # The scenery grows independently of the native window's bounds, so
        # entering a smaller playback window still feels like moving into the
        # album rather than shrinking the entire library into that window.
        source_background = origin.background if not origin.background.isNull() else target.background
        if source_background.isNull():
            source_background = origin.image
        target_background = target.background if not target.background.isNull() else source_background
        for frame, image, alpha, zoom in ((origin, source_background, 1., 1. + .18 * p),
                                   (target, target_background, p, 1. + .18 * (1. - p))):
            if alpha <= 0.:
                continue
            background_rect = QRectF(window)
            background_rect.setSize(window.size() * zoom)
            background_rect.moveCenter(window.center())
            if frame.background_geometry.isValid() and frame.visible_geometry.isValid():
                crop, visible = frame.background_geometry, frame.visible_geometry
                sx, sy = background_rect.width()/visible.width(), background_rect.height()/visible.height()
                background_rect = QRectF(background_rect.x() + (crop.x()-visible.x())*sx,
                    background_rect.y() + (crop.y()-visible.y())*sy, crop.width()*sx, crop.height()*sy)
            painter.setOpacity(alpha)
            painter.drawImage(background_rect, image)

    def _paint_handoff_foreground(self, painter, offset, f):
        origin, target = self._origin_frame, self._target_frame
        source_alpha = max(0., 1. - f / .38)
        if source_alpha > 0.:
            painter.setOpacity(source_alpha)
            painter.drawImage(QRectF(origin.geometry).translated(-offset), origin.image)
        target_alpha = max(0., min(1., (f - .28) / .72))
        if target_alpha > 0.:
            painter.setOpacity(target_alpha)
            painter.drawImage(QRectF(target.geometry).translated(-offset), target.image)

    def _paint_handoff_cover(self, painter, offset, p):
        origin, target = self._origin_frame, self._target_frame
        cover = self._cover_rect_at().translated(-offset)
        painter.setOpacity(1.)
        painter.translate(cover.center()); painter.rotate(self._cover_angle_at())
        cover.moveCenter(QPointF())
        rounding = origin.cover_roundness + (target.cover_roundness - origin.cover_roundness) * p
        values = self._motion_values()
        if values:
            rounding = min(.5, max(0., values[9]))
        cover_clip = QPainterPath(); cover_clip.addRoundedRect(cover, cover.width() * rounding, cover.height() * rounding)
        painter.setClipPath(cover_clip, Qt.ClipOperation.IntersectClip)
        def square(image):
            side = min(image.width(), image.height())
            return QRectF((image.width() - side) / 2., (image.height() - side) / 2., side, side)
        painter.drawImage(cover, origin.cover_image, square(origin.cover_image))
        painter.setOpacity(p)
        painter.drawImage(cover, target.cover_image, square(target.cover_image))

    def _visible_frame(self, *, flatten=False):
        """Rasterize existing layers once; never re-grab live page widgets."""
        window = self._window_rect_at()
        geometry = window.toAlignedRect()
        ratio = min(float(self.devicePixelRatioF()), self.SCENE_RASTER_LIMIT / max(1, geometry.width(), geometry.height()))
        size = QSize(max(1, math.ceil(geometry.width()*ratio)), max(1, math.ceil(geometry.height()*ratio)))
        def layer_image(draw):
            image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.scale(ratio, ratio)
                draw(painter)
            return image
        offset = QPointF(geometry.topLeft())
        if flatten and self._cover_transition:
            def complete(painter):
                painter.translate(QPointF(self.geometry().topLeft()) - offset)
                self._paint_cover_handoff(painter)
            return NavigationFrame(geometry, layer_image(complete), visible_geometry=window,
                                   velocity=self._pose_velocity())
        if not self._cover_transition:
            def plain(painter):
                bounds = QRectF(0, 0, geometry.width(), geometry.height())
                painter.setOpacity(1.-self._fraction)
                painter.drawImage(bounds, self._origin)
                painter.setOpacity(self._fraction)
                painter.drawImage(bounds, self._target)
            return NavigationFrame(geometry, layer_image(plain), visible_geometry=window,
                                   velocity=self._pose_velocity())
        f, p = self._fraction, self._cover_progress()
        background = layer_image(lambda painter: self._paint_handoff_background(painter, window.translated(-offset), f, p))
        foreground = layer_image(lambda painter: self._paint_handoff_foreground(painter, offset, f))
        origin, target = self._origin_frame, self._target_frame
        side = max(1, min(self.SCENE_RASTER_LIMIT, max(origin.cover_image.width(), target.cover_image.width())))
        cover = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        cover.fill(Qt.GlobalColor.transparent)
        with QPainter(cover) as painter:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            for image, alpha in ((origin.cover_image, 1.), (target.cover_image, p)):
                edge = min(image.width(), image.height())
                painter.setOpacity(alpha)
                painter.drawImage(QRectF(0, 0, side, side), image,
                    QRectF((image.width()-edge)/2., (image.height()-edge)/2., edge, edge))
        values = self._motion_values()
        rounding = values[9] if values else origin.cover_roundness + (target.cover_roundness-origin.cover_roundness)*p
        return NavigationFrame(geometry, foreground, self._cover_rect_at(), cover, self._cover_angle_at(),
            background, cover_roundness=rounding, visible_geometry=window,
            velocity=self._pose_velocity(), background_geometry=QRectF(geometry))
