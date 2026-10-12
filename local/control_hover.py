"""Shared control hover motion, without changing native layout or effects.

The mathematical helpers also serve the custom-painted music/island surfaces.
Widget controls retain their real geometry, keyboard/accessibility targets and
original paint implementation. A transparent sibling only composites their
native pixels while they move; pointer events are mapped back to those targets.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
import weakref

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QCursor, QImage, QMouseEvent, QPainter, QRegion, QWheelEvent
from PySide6.QtWidgets import (QAbstractButton, QAbstractScrollArea, QAbstractSpinBox, QApplication,
                              QComboBox, QLineEdit, QSlider, QStyledItemDelegate, QStyleOptionViewItem, QWidget)
from shiboken6 import isValid

HOVER_SECONDS = .5
HOVER_SCALE = 1.10
MENU_HOVER_DELAY_MS = 500


def _control_hover_allowed(widget: QWidget) -> bool:
    """Settings opt out before child construction, including later parenting."""
    current = widget
    while current is not None:
        if current.property("controlHoverDisabled"):
            return False
        current = current.parentWidget()
    return True


def _unit(value: float) -> float:
    return min(1., max(0., float(value)))


@dataclass
class HoverMotion:
    """Half-second fast-to-slow growth, reversible from the actual pose."""
    initial: float = 0.
    _from: float = field(init=False)
    _target: float = field(init=False)
    _started: float = field(default=0., init=False)

    def __post_init__(self):
        self._from = self._target = _unit(self.initial)

    def set_hovered(self, hovered: bool, now: float) -> bool:
        target = float(bool(hovered))
        if target == self._target:
            return False
        self._from = self.value(now)
        self._target, self._started = target, float(now)
        return True

    def value(self, now: float) -> float:
        phase = _unit((float(now) - self._started) / HOVER_SECONDS)
        eased = 1. - (1. - phase) ** 5
        return self._from + (self._target - self._from) * eased

    def is_animating(self, now: float) -> bool:
        return abs(self._target - self.value(now)) > .00001

    def reset(self):
        self._from = self._target = self._started = 0.


def hover_scale(progress: float) -> float:
    return 1. + (HOVER_SCALE - 1.) * _unit(progress)


def neighbor_displacement(rect: QRectF, active_rect: QRectF, progress: float) -> QPointF:
    """Nearby edges move further than distant ones; opposing sides move away.

    Only peers in the same control group participate. Edge distance, rather
    than center distance, keeps large cards and compact buttons consistent.
    The smooth bounded response is the target for the widget's damped spring.
    """
    progress = _unit(progress)
    if not progress or rect.isEmpty() or active_rect.isEmpty() or rect == active_rect:
        return QPointF()
    dx, dy = rect.center().x() - active_rect.center().x(), rect.center().y() - active_rect.center().y()
    distance = math.hypot(dx, dy)
    if distance < .001:
        return QPointF()
    gap_x = max(0., abs(dx) - (rect.width() + active_rect.width()) / 2.)
    gap_y = max(0., abs(dy) - (rect.height() + active_rect.height()) / 2.)
    gap = math.hypot(gap_x, gap_y)
    if gap > 320.:
        return QPointF()
    magnitude = min(12., max(3., min(active_rect.width(), active_rect.height()) * .18))
    magnitude *= math.exp(-gap / 90.) * progress
    return QPointF(dx / distance * magnitude, dy / distance * magnitude)


def advance_neighbor_spring(offset: QPointF, velocity: QPointF, target: QPointF,
                            elapsed: float) -> tuple[QPointF, QPointF]:
    """Bounded substeps keep the same elastic response at slow/fast cadence."""
    offset, velocity = QPointF(offset), QPointF(velocity)
    elapsed = min(.08, max(0., float(elapsed)))
    steps = max(1, math.ceil(elapsed / .008))
    step = elapsed / steps
    for _ in range(steps):
        velocity += ((target - offset) * 784. - velocity * 42.) * step
        offset += velocity * step
    if (abs(target.x() - offset.x()) + abs(target.y() - offset.y()) < .001 and
            abs(velocity.x()) + abs(velocity.y()) < .01):
        offset, velocity = QPointF(target), QPointF()
    return offset, velocity


@dataclass
class _Control:
    widget: weakref.ReferenceType
    motion: HoverMotion = field(default_factory=HoverMotion)
    offset: QPointF = field(default_factory=QPointF)
    velocity: QPointF = field(default_factory=QPointF)
    menu_callback: object = None
    hovered_at: float | None = None
    menu_fired: bool = False
    max_scale: float = HOVER_SCALE
    enabled_when: object = None
    bounds_provider: object = None
    image: QImage | None = None
    image_key: tuple | None = None


def _native_visual_key(widget):
    key = [widget.size().width(), widget.size().height(), round(widget.devicePixelRatioF(), 3),
           widget.font().toString(), widget.palette().cacheKey(), widget.styleSheet(),
           widget.isEnabled(), widget.underMouse(), widget.hasFocus()]
    if isinstance(widget, QAbstractButton):
        key.extend((widget.text(), widget.isChecked(), widget.isDown(), widget.icon().cacheKey()))
    elif isinstance(widget, QComboBox):
        key.extend((widget.currentIndex(), widget.currentText(), widget.iconSize().width(), widget.iconSize().height()))
    elif isinstance(widget, QSlider):
        key.extend((widget.value(), widget.minimum(), widget.maximum(), widget.isSliderDown()))
    elif isinstance(widget, QAbstractSpinBox):
        key.append(widget.text())
    elif isinstance(widget, QLineEdit):
        key.extend((widget.text(), widget.cursorPosition(), widget.selectionStart(), widget.selectedText(), widget.isReadOnly()))
        if widget.hasFocus() and not widget.isReadOnly():
            # A native caret keeps its ordinary blink while pointer motion is
            # settled. It requires at most two image refreshes per second.
            key.append(int(time.monotonic() * 2))
    return tuple(key)


class _HoverOverlay(QWidget):
    def __init__(self, group, parent):
        super().__init__(parent)
        self.group = group
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName("")
        self.hide()

    def paintEvent(self, event):
        self.group.paint_overlay(self)


class WidgetHoverGroup(QObject):
    """One bounded animation/compositing timer for direct sibling controls."""
    def __init__(self, parent):
        # QObject parenting can invoke Python event filters synchronously.
        # Ignore those construction events until every field is available.
        self._capturing = True
        super().__init__(parent)
        self.host = parent
        self.controls: list[_Control] = []
        self.active: _Control | None = None
        self.pressed: _Control | None = None
        self.pressed_widget = None
        self.pressed_visual = None
        self._forwarding = False
        self._last_tick = time.monotonic()
        self._dirty = QRectF()
        self.overlay = _HoverOverlay(self, parent.window())
        parent.destroyed.connect(self.overlay.deleteLater)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        parent.setMouseTracking(True)
        parent.installEventFilter(self)
        self._capturing = False

    def add(self, widget):
        record = next((item for item in self.controls if item.widget() is widget), None)
        if record is None:
            record = _Control(weakref.ref(widget))
            self.controls.append(record)
            widget.setMouseTracking(True)
            widget.installEventFilter(self)
            # The parent's paint must clear beneath moved translucent pixels.
            widget.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
            widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
            for child in widget.findChildren(QWidget):
                child.installEventFilter(self)
                child.setMouseTracking(True)
        return record

    def _valid(self, record):
        widget = record.widget()
        if widget is None or not isValid(widget) or widget.parentWidget() is not self.host or not widget.isVisible() or not _control_hover_allowed(widget):
            return False
        if record.enabled_when is not None and not record.enabled_when():
            return False
        ancestor = widget.parentWidget()
        while ancestor is not None:
            if getattr(ancestor, "_control_hover_registration", None) is not None:
                return False
            ancestor = ancestor.parentWidget()
        return True

    def _base(self, record):
        return QRectF(record.widget().geometry())

    def visual_rect(self, record, now=None):
        if record is self.pressed and self.pressed_visual is not None:
            return QRectF(self.pressed_visual)
        base = self._base(record)
        scale = 1. + (record.max_scale - 1.) * record.motion.value(time.monotonic() if now is None else now)
        center = base.center() + record.offset
        bounds = self._bounds(record)
        if bounds is not None:
            scale = min(scale, bounds.width() / max(1., base.width()),
                        bounds.height() / max(1., base.height()))
        visual = QRectF(center.x() - base.width() * scale / 2., center.y() - base.height() * scale / 2.,
                        base.width() * scale, base.height() * scale)
        if bounds is not None:
            visual.moveLeft(max(bounds.left(), min(visual.left(), bounds.right() - visual.width())))
            visual.moveTop(max(bounds.top(), min(visual.top(), bounds.bottom() - visual.height())))
        return visual

    def _bounds(self, record):
        """Optional host-local bounds share the visible transform and hit map."""
        if record.bounds_provider is None:
            return None
        bounds = QRectF(record.bounds_provider())
        return bounds if not bounds.isEmpty() else None

    def _moving(self, record, now):
        return (record.motion.value(now) > .00001 or
                abs(record.offset.x()) + abs(record.offset.y()) > .005)

    def _at(self, global_position):
        point = QPointF(self.host.mapFromGlobal(global_position.toPoint()))
        candidates = [item for item in self.controls if self._valid(item) and item.widget().isEnabled()]
        if self.active in candidates:
            candidates.remove(self.active)
            candidates.append(self.active)
        for item in reversed(candidates):
            bounds = self._bounds(item)
            if bounds is not None and not bounds.contains(point):
                continue
            # Keep the original target usable throughout the visual movement.
            if self.visual_rect(item).contains(point) or self._base(item).contains(point):
                return item
        return None

    def _set_active(self, record, now):
        if self.active is record:
            return
        previous, self.active = self.active, record
        if previous is not None:
            previous.motion.set_hovered(False, now)
            previous.hovered_at, previous.menu_fired = None, False
            self._boundary(previous, False)
        if record is not None:
            record.motion.set_hovered(True, now)
            record.hovered_at, record.menu_fired = now, False
            self._boundary(record, True)
        self._last_tick = now
        if not self.timer.isActive():
            self.timer.start()
        self._repaint()

    def _boundary(self, record, entering):
        widget = record.widget()
        if widget is None or not isValid(widget):
            return
        widget.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, entering)
        widget.update()

    def _repaint(self):
        now = time.monotonic()
        visible = any(self._valid(item) and self._moving(item, now) for item in self.controls)
        if self.overlay.parentWidget() is not self.host.window():
            self.overlay.setParent(self.host.window())
        self.overlay.setGeometry(self.overlay.parentWidget().rect())
        self.overlay.setVisible(visible)
        origin = QPointF(self.host.mapTo(self.overlay.parentWidget(), QPoint()))
        dirty = QRectF()
        for item in self.controls:
            if self._valid(item) and self._moving(item, now):
                region = self._base(item).united(self.visual_rect(item, now)).translated(origin).adjusted(-3, -3, 3, 3)
                dirty = region if dirty.isEmpty() else dirty.united(region)
        repaint = dirty.united(self._dirty) if not self._dirty.isEmpty() else dirty
        self._dirty = dirty
        if visible:
            self.overlay.raise_()
            self.overlay.update(repaint.toAlignedRect())
        if not repaint.isEmpty():
            self.overlay.parentWidget().update(repaint.toAlignedRect())

    def _tick(self):
        now = time.monotonic()
        elapsed = min(.05, max(0., now - self._last_tick))
        self._last_tick = now
        self.controls = [item for item in self.controls if item.widget() is not None and isValid(item.widget())]
        if self.active is not None and (not self._valid(self.active) or not self.active.widget().isEnabled()):
            self._set_active(None, now)
        active = self.active
        progress = active.motion.value(now) if active else 0.
        active_rect = self._base(active) if active else QRectF()
        moving = False
        for item in self.controls:
            if not self._valid(item):
                item.motion.reset()
                item.offset, item.velocity = QPointF(), QPointF()
                item.image, item.image_key = None, None
                continue
            target = neighbor_displacement(self._base(item), active_rect, progress) if item is not active else QPointF()
            item.offset, item.velocity = advance_neighbor_spring(item.offset, item.velocity, target, elapsed)
            moving |= (item.motion.is_animating(now) or
                       abs(target.x() - item.offset.x()) + abs(target.y() - item.offset.y()) > .001 or
                       abs(item.velocity.x()) + abs(item.velocity.y()) > .01)
            if not self._moving(item, now):
                item.image, item.image_key = None, None
        if active and active.menu_callback and not active.menu_fired and active.hovered_at is not None:
            if now - active.hovered_at >= MENU_HOVER_DELAY_MS / 1000.:
                active.menu_fired = True
                active.menu_callback()
            else:
                moving = True
        self._repaint()
        if not moving:
            self.timer.stop()

    def paint_overlay(self, overlay):
        now = time.monotonic()
        with QPainter(overlay) as painter:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            root = overlay.parentWidget()
            origin = QPointF(self.host.mapTo(root, QPoint()))
            ancestor = self.host
            while ancestor is not None and ancestor is not root:
                parent = ancestor.parentWidget()
                if isinstance(parent, QAbstractScrollArea) and ancestor is parent.viewport():
                    viewport_origin = QPointF(ancestor.mapTo(root, QPoint()))
                    painter.setClipRect(QRectF(viewport_origin, ancestor.size()), Qt.ClipOperation.IntersectClip)
                ancestor = parent
            for record in self.controls:
                if not self._valid(record) or not self._moving(record, now):
                    continue
                painter.save()
                bounds = self._bounds(record)
                if bounds is not None:
                    painter.setClipRect(bounds.translated(origin), Qt.ClipOperation.IntersectClip)
                painter.drawImage(self.visual_rect(record, now).translated(origin), self._control_image(record))
                painter.restore()

    def _control_image(self, record):
        widget = record.widget()
        key = _native_visual_key(widget)
        if record.image is not None and record.image_key == key:
            return record.image
        dpr = max(1., widget.devicePixelRatioF())
        image = QImage(math.ceil(widget.width() * dpr), math.ceil(widget.height() * dpr),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        self._capturing = True
        try:
            widget.render(image, QPoint(), QRegion(widget.rect()), QWidget.RenderFlag.DrawChildren)
        finally:
            self._capturing = False
        record.image, record.image_key = image, key
        return image

    def _route_pointer(self, watched, event):
        kind = event.type()
        record = self.pressed or self._at(event.globalPosition())
        if record is not None and not self._valid(record):
            self.pressed = record = None
            self.pressed_widget = None
            self.pressed_visual = None
        if kind == QEvent.Type.MouseMove:
            self._set_active(self.pressed or self._at(event.globalPosition()), time.monotonic())
        if record is None:
            return False
        widget = record.widget()
        local_host = QPointF(self.host.mapFromGlobal(event.globalPosition().toPoint()))
        visual = self.visual_rect(record)
        base = self._base(record)
        # The retained original hit area uses ordinary coordinates; expanded
        # overflow and moved pixels use inverse scale/translation coordinates.
        if not visual.contains(local_host) and base.contains(local_host):
            local = local_host - base.topLeft()
        else:
            scale = visual.width() / max(1., base.width())
            local = (local_host - visual.topLeft()) / scale
        held = self.pressed_widget() if self.pressed_widget is not None else None
        destination = held if held is not None and isValid(held) else widget.childAt(local.toPoint()) or widget
        if destination.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
            destination = widget
        if destination is not widget:
            local = QPointF(destination.mapFrom(widget, local.toPoint()))
        if kind == QEvent.Type.Wheel:
            mapped = QWheelEvent(local, event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                                 event.buttons(), event.modifiers(), event.phase(), event.inverted(), event.source())
        else:
            mapped = QMouseEvent(kind, local, event.globalPosition(), event.button(), event.buttons(), event.modifiers())
        if kind == QEvent.Type.MouseButtonPress:
            self.pressed_visual = QRectF(visual)
            self.pressed = record
            self.pressed_widget = weakref.ref(destination)
        self._forwarding = True
        try:
            QApplication.sendEvent(destination, mapped)
        finally:
            self._forwarding = False
            if kind == QEvent.Type.MouseButtonRelease:
                self.pressed = None
                self.pressed_widget = None
                self.pressed_visual = None
        event.setAccepted(mapped.isAccepted())
        return True

    def eventFilter(self, watched, event):
        # Qt can call an existing filter while its Python state is being
        # constructed or released. Leave native events untouched in that gap.
        if getattr(self, "_capturing", True) or getattr(self, "_forwarding", True):
            return False
        kind = event.type()
        record = next((item for item in self.controls if item.widget() is watched), None)
        if record is not None and not _control_hover_allowed(watched):
            return False
        if kind == QEvent.Type.Paint and record and self._moving(record, time.monotonic()):
            origin = QPointF(self.host.mapTo(self.overlay.parentWidget(), QPoint()))
            self.overlay.update(self.visual_rect(record).translated(origin).adjusted(-2, -2, 2, 2).toAlignedRect())
            return True
        if kind in {QEvent.Type.Enter, QEvent.Type.Leave}:
            position = event.globalPosition() if hasattr(event, "globalPosition") else QPointF(QCursor.pos())
            self._set_active(self._at(position), time.monotonic())
        elif kind in {QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                      QEvent.Type.MouseButtonDblClick, QEvent.Type.Wheel}:
            return self._route_pointer(watched, event)
        elif kind in {QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show, QEvent.Type.EnabledChange,
                      QEvent.Type.LayoutRequest, QEvent.Type.StyleChange, QEvent.Type.FontChange, QEvent.Type.PaletteChange}:
            if record:
                record.image, record.image_key = None, None
            if self.overlay.isVisible():
                self._repaint()
        elif kind in {QEvent.Type.Hide, QEvent.Type.ParentChange}:
            if record:
                record.motion.reset()
                record.offset, record.velocity = QPointF(), QPointF()
                record.image, record.image_key = None, None
                if record is self.active:
                    self._set_active(None, time.monotonic())
                if record is self.pressed:
                    self.pressed = None
                    self.pressed_widget = None
                    self.pressed_visual = None
            if watched is self.host:
                self.timer.stop()
                self.overlay.hide()
                self.active = self.pressed = None
                self.pressed_widget = None
                self.pressed_visual = None
        return False


class _HoverRegistration(QObject):
    def __init__(self, widget):
        super().__init__(widget)
        self.widget, self.group, self.record = widget, None, None
        self.menu_callback = None
        self.max_scale = HOVER_SCALE
        self.enabled_when = None
        self.bounds_provider = None
        # Explicit offscreen callers request original native pixels. The C++
        # screen compositor still goes through eventFilter; these two existing
        # Qt APIs keep their original overloads and do not capture our overlay.
        self._native_render, self._native_grab, self._native_update = widget.render, widget.grab, widget.update
        widget.render = self.render_native
        widget.grab = self.grab_native
        widget.update = self.update_native
        widget.installEventFilter(self)
        self.attach()

    def _native_call(self, callback, *args, **kwargs):
        group = self.group
        previous = group._capturing if group is not None else False
        if group is not None:
            group._capturing = True
        try:
            return callback(*args, **kwargs)
        finally:
            if group is not None:
                group._capturing = previous

    def render_native(self, *args, **kwargs):
        return self._native_call(self._native_render, *args, **kwargs)

    def grab_native(self, *args, **kwargs):
        return self._native_call(self._native_grab, *args, **kwargs)

    def update_native(self, *args, **kwargs):
        if self.record is not None:
            self.record.image, self.record.image_key = None, None
        return self._native_update(*args, **kwargs)

    def attach(self):
        parent = self.widget.parentWidget()
        if parent is None or not _control_hover_allowed(self.widget):
            self.group, self.record = None, None
            return
        group = getattr(parent, "_control_hover_group", None)
        if group is None:
            group = parent._control_hover_group = WidgetHoverGroup(parent)
        self.group, self.record = group, group.add(self.widget)
        self.record.menu_callback = self.menu_callback
        self.record.max_scale = self.max_scale
        self.record.enabled_when = self.enabled_when
        self.record.bounds_provider = self.bounds_provider

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Type.ParentChange, QEvent.Type.Show}:
            self.attach()
        return False


def install_control_hover(widget: QWidget, *, menu_callback=None, max_scale: float | None = None,
                          enabled_when=None, bounds_provider=None):
    """Register an existing control; repeated installation remains harmless."""
    if not _control_hover_allowed(widget):
        return None
    registration = getattr(widget, "_control_hover_registration", None)
    if registration is None:
        registration = widget._control_hover_registration = _HoverRegistration(widget)
    if bounds_provider is not None:
        registration.bounds_provider = bounds_provider
        if registration.record is not None:
            registration.record.bounds_provider = bounds_provider
    if menu_callback is not None:
        registration.menu_callback = menu_callback
        if registration.record is not None:
            registration.record.menu_callback = menu_callback
    if max_scale is not None:
        registration.max_scale = min(1.12, max(1., float(max_scale)))
        if registration.record is not None:
            registration.record.max_scale = registration.max_scale
    if enabled_when is not None:
        registration.enabled_when = enabled_when
        if registration.record is not None:
            registration.record.enabled_when = enabled_when
    return registration


def install_container_hover(container: QWidget):
    """Cover existing native interactive controls, excluding internal editors.

    Composite controls keep one visual transform so a spin box's editor and
    buttons cannot drift apart, and an embedded search action retains its hit.
    """
    if not _control_hover_allowed(container):
        return
    kinds = (QAbstractButton, QComboBox, QSlider, QAbstractSpinBox, QLineEdit)
    for widget in container.findChildren(QWidget):
        if not isinstance(widget, kinds):
            continue
        ancestor = widget.parentWidget()
        nested = False
        while ancestor is not None and ancestor is not container:
            if isinstance(ancestor, kinds):
                nested = True
                break
            ancestor = ancestor.parentWidget()
        if nested:
            continue
        if isinstance(widget, QComboBox):
            install_control_hover(widget, menu_callback=widget.showPopup)
        else:
            install_control_hover(widget)


def control_local_position(widget: QWidget, global_position: QPointF) -> QPointF:
    """Map a surface's explicit native hit test to the same composed pose."""
    registration = getattr(widget, "_control_hover_registration", None)
    if registration is None or registration.group is None or not registration.group._valid(registration.record):
        return QPointF(widget.mapFromGlobal(global_position.toPoint()))
    group, record = registration.group, registration.record
    local_host = QPointF(group.host.mapFromGlobal(global_position.toPoint()))
    base, visual = group._base(record), group.visual_rect(record)
    if visual.contains(local_host):
        return (local_host - visual.topLeft()) / (visual.width() / max(1., base.width()))
    return local_host - base.topLeft()


class _HoverItemDelegate(QStyledItemDelegate):
    """Preserve native sidebar selection/model semantics with row-local motion."""
    def __init__(self, view):
        super().__init__(view)
        self.view, self.base = view, view.itemDelegate()
        self.active = -1
        self.motion: dict[int, HoverMotion] = {}
        self.offsets: dict[int, QPointF] = {}
        self.velocities: dict[int, QPointF] = {}
        self._forwarding = False
        self._pressed_row = -1
        self._last_tick = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        view.viewport().setMouseTracking(True)
        view.viewport().installEventFilter(self)

    def _retarget(self, row):
        if self.active == row:
            return
        now = time.monotonic()
        if self.active >= 0:
            self.motion.setdefault(self.active, HoverMotion()).set_hovered(False, now)
        self.active = row
        if row >= 0:
            self.motion.setdefault(row, HoverMotion()).set_hovered(True, now)
        self._last_tick = now
        self.timer.start()
        self.view.viewport().update()

    def _tick(self):
        now = time.monotonic()
        elapsed = min(.05, max(0., now - self._last_tick))
        self._last_tick = now
        active = self.view.model().index(self.active, 0)
        active_rect = QRectF(self.view.visualRect(active)) if active.isValid() else QRectF()
        progress = self.motion[self.active].value(now) if self.active >= 0 else 0.
        moving = False
        for row in range(self.view.model().rowCount()):
            rect = QRectF(self.view.visualRect(self.view.model().index(row, 0)))
            target = neighbor_displacement(rect, active_rect, progress) if row != self.active else QPointF()
            offset = self.offsets.get(row, QPointF())
            velocity = self.velocities.get(row, QPointF())
            offset, velocity = advance_neighbor_spring(offset, velocity, target, elapsed)
            self.offsets[row], self.velocities[row] = offset, velocity
            moving |= abs(target.y() - offset.y()) >= .001 or abs(velocity.y()) >= .01
        moving |= any(motion.is_animating(now) for motion in self.motion.values())
        self.view.viewport().update()
        if not moving:
            self.timer.stop()

    def paint(self, painter, option, index):
        row = index.row()
        motion = self.motion.get(row)
        progress = motion.value(time.monotonic()) if motion else 0.
        center = QRectF(option.rect).center()
        painter.save()
        painter.translate(center + self.offsets.get(row, QPointF()))
        scale = 1. + .06 * progress
        painter.scale(scale, scale)
        painter.translate(-center)
        self.base.paint(painter, QStyleOptionViewItem(option), index)
        painter.restore()

    def sizeHint(self, option, index):
        return self.base.sizeHint(option, index)

    def _visual_rect(self, row):
        rect = QRectF(self.view.visualRect(self.view.model().index(row, 0)))
        motion = self.motion.get(row)
        scale = 1. + .06 * motion.value(time.monotonic()) if motion else 1.
        center = rect.center() + self.offsets.get(row, QPointF())
        return QRectF(center.x() - rect.width() * scale / 2., center.y() - rect.height() * scale / 2.,
                      rect.width() * scale, rect.height() * scale)

    def _row_at(self, position):
        rows = list(range(self.view.model().rowCount()))
        if self.active in rows:
            rows.remove(self.active)
            rows.append(self.active)
        for row in reversed(rows):
            base = QRectF(self.view.visualRect(self.view.model().index(row, 0)))
            if self._visual_rect(row).contains(position) or base.contains(position):
                return row
        return -1

    def eventFilter(self, watched, event):
        if self._forwarding:
            return False
        kind = event.type()
        if kind in {QEvent.Type.MouseMove, QEvent.Type.Enter}:
            local = event.position() if hasattr(event, "position") else event.localPosition()
            self._retarget(self._row_at(local) if self.view.isEnabled() else -1)
        elif kind in {QEvent.Type.Leave, QEvent.Type.Hide}:
            self._retarget(-1)
        if kind in {QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick}:
            row = self._pressed_row if self._pressed_row >= 0 else self._row_at(event.position())
            if row < 0 or not self.view.isEnabled():
                return False
            base = QRectF(self.view.visualRect(self.view.model().index(row, 0)))
            visual = self._visual_rect(row)
            position = event.position()
            if visual.contains(position):
                scale = visual.width() / max(1., base.width())
                position = base.topLeft() + (position - visual.topLeft()) / scale
            mapped = QMouseEvent(kind, position, event.globalPosition(), event.button(), event.buttons(), event.modifiers())
            if kind == QEvent.Type.MouseButtonPress:
                self._pressed_row = row
            self._forwarding = True
            try:
                QApplication.sendEvent(watched, mapped)
            finally:
                self._forwarding = False
                if kind == QEvent.Type.MouseButtonRelease:
                    self._pressed_row = -1
            event.setAccepted(mapped.isAccepted())
            return True
        return False


def install_item_hover(view):
    if not _control_hover_allowed(view):
        return None
    if getattr(view, "_control_hover_delegate", None) is None:
        delegate = view._control_hover_delegate = _HoverItemDelegate(view)
        view.setItemDelegate(delegate)
    return view._control_hover_delegate
