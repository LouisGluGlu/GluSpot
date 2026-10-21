"""One bounded pixel worker; QWidget lifetime and result caches stay on Qt's thread.

QImage is reentrant, not a license to share one mutable wrapper. Callers pass
their own QImage value wrapper and immutable numeric options to a pure renderer.
No painter, widget or provider crosses the worker boundary.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import time
import weakref

from PIL import Image
from PySide6.QtCore import QEvent, QObject, QRectF, Slot
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from local.music_tasks import MusicTasks


@dataclass
class _Owner:
    reference: object
    scene: object
    getter: object = None
    generation: int = 0


@dataclass(frozen=True)
class _Request:
    slot: tuple
    generation: int
    domain: object
    stamp: object
    serial: int
    payload: object
    byte_count: int
    render: object
    dirty: tuple
    interval: float
    transition: float = 0.
    ready_callback: object = None


@dataclass(frozen=True)
class _Ready:
    generation: int
    domain: object
    stamp: object
    serial: int
    image: QImage
    at: float
    previous: QImage | None = None
    transition: float = 0.


@dataclass(frozen=True)
class _Desired:
    generation: int
    domain: object
    stamp: object
    serial: int


class GlassPixelTasks(QObject):
    """One in-flight job and a latest-per-role bounded pending set.

    Backdrop frames may advance while a job runs. Accept a completed frame of
    the current scene/geometry monotonically, rather than starving forever by
    demanding the newest 30 Hz frame. Geometry/settings/scene changes remain
    strict, and a newer accepted result can never be replaced by an older one.
    """
    MAX_OWNERS = 64
    MAX_SLOTS = 64
    MAX_PENDING = 16
    MAX_INPUT_BYTES = 16 * 1024 * 1024
    MAX_CACHE_BYTES = 4 * 1024 * 1024

    def __init__(self, parent=None, *, tasks=None):
        super().__init__(parent)
        self.tasks = tasks if tasks is not None else MusicTasks(self, worker_count=1)
        self.tasks.completed.connect(self._completed)
        self.owners = OrderedDict()
        self.owner_ids = weakref.WeakKeyDictionary()
        self.latest = OrderedDict()
        self.pending = OrderedDict()
        self.failed = {}
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.active = None
        self.closed = False
        self._next_owner = self._serial = 0
        self.submitted = self.discarded = self.hits = self.misses = self.rejected = 0
        self.prepare_seconds = 0.

    @staticmethod
    def _live(owner):
        try:
            return owner is not None and owner.isVisible() and not owner.window().isMinimized()
        except RuntimeError:
            return False

    def _bind(self, owner, scene):
        getter = None
        if callable(scene):
            try:
                getter = weakref.WeakMethod(scene)
            except TypeError:
                # Unbound closures may retain a QWidget. Require a weak bound
                # method or an immutable value, never retain such a closure.
                return None
            scene = scene()
        try:
            hash(scene)
        except TypeError:
            return None
        uid = self.owner_ids.get(owner)
        if uid is None:
            if len(self.owners) >= self.MAX_OWNERS:
                protected = {request.slot[0] for request in self.pending.values()}
                if self.active is not None:
                    protected.add(self.active.slot[0])
                retired = next((key for key in self.owners if key not in protected), None)
                if retired is None:
                    return None
                self._forget(retired)
            self._next_owner += 1
            uid = self._next_owner
            self.owner_ids[owner] = uid
            self.owners[uid] = _Owner(weakref.ref(owner), scene, getter)
            owner.installEventFilter(self)
            owner.window().installEventFilter(self)
            hooked = getattr(owner, '_glass_pixel_service_hook', None)
            if hooked is None or hooked() is not self:
                # A scrolled-away live row may leave and reenter the bounded
                # LRU. Connect once for its lifetime, rather than retaining
                # another obsolete uid callback on every re-registration.
                owner._glass_pixel_service_hook = weakref.ref(self)
                service_ref, owner_ref = weakref.ref(self), weakref.ref(owner)
                def destroyed(*_args):
                    service = service_ref()
                    if service is not None:
                        service._forget_reference(owner_ref)
                owner.destroyed.connect(destroyed)
        state = self.owners[uid]
        self.owners.move_to_end(uid)
        if state.scene != scene:
            self._invalidate(uid)
            state.scene = scene
        state.getter = getter
        return uid, state

    def request(self, owner, role, scene, domain, stamp, payload, render, dirty,
                *, byte_count, interval=.1, ready_callback=None, transition=0.):
        """GUI-only request; return compatible presentation pixels or None.

        A nonzero transition requires the owner's existing animation clock;
        no timer is created here. Callbacks still receive the raw ready frame.
        """
        if self.closed or not self._live(owner):
            return None
        bound = self._bind(owner, scene)
        if bound is None:
            self.rejected += 1
            return None
        uid, state = bound
        try:
            hash(role)
            hash(domain)
            hash(stamp)
        except TypeError:
            self.rejected += 1
            return None
        slot = (uid, role)
        ready = self.cache.pop(slot, None)
        if ready is not None:
            self.cache[slot] = ready
        compatible = ready is not None and (ready.generation, ready.domain) == (state.generation, domain)
        now = time.monotonic()
        if compatible and (ready.stamp == stamp or now - ready.at < max(0., interval)):
            self.hits += 1
            return self._present(ready, now)
        self.misses += 1
        previous = self.latest.get(slot)
        desired = (state.generation, domain, stamp)
        if self.failed.get(slot) == desired:
            return self._present(ready, now) if compatible else None
        if previous is not None and (previous.generation, previous.domain, previous.stamp) == desired:
            if slot in self.pending or (self.active is not None and self.active.serial == previous.serial):
                return self._present(ready, now) if compatible else None
        self._serial += 1
        rect = QRectF(dirty)
        callback = weakref.WeakMethod(ready_callback) if ready_callback is not None else None
        request = _Request(slot, state.generation, domain, stamp, self._serial, payload,
                           max(0, int(byte_count)), render,
                           (rect.x(), rect.y(), rect.width(), rect.height()),
                           max(0., interval), min(.25, max(0., float(transition))), callback)
        self.failed.pop(slot, None)
        self.latest[slot] = _Desired(request.generation, domain, stamp, request.serial)
        self.latest.move_to_end(slot)
        self.pending.pop(slot, None)
        self.pending[slot] = request
        while len(self.latest) > self.MAX_SLOTS:
            retired, _request = self.latest.popitem(last=False)
            self.pending.pop(retired, None)
            self.failed.pop(retired, None)
            self._drop_ready(retired)
        while (len(self.pending) > self.MAX_PENDING
               or self.input_bytes > self.MAX_INPUT_BYTES):
            retired, _request = self.pending.popitem(last=False)
            self.latest.pop(retired, None)
            self.rejected += 1
            if not self.pending:
                break
        self._start()
        return self._present(ready, now) if compatible else None

    @staticmethod
    def _present(ready, now):
        """Blend only the bounded material; the owner supplies its paint clock."""
        if ready.previous is None or ready.transition <= 0.:
            return ready.image
        fraction = min(1., max(0., (now - ready.at) / ready.transition))
        if fraction >= 1.:
            return ready.image
        if fraction <= 0.:
            return ready.previous
        fraction = fraction * fraction * (3. - 2. * fraction)
        source = ready.previous.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        target = ready.image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        size = (source.width(), source.height())
        # Blend premultiplied bytes, including alpha, with the same weights.
        # Applying QPainter opacity to Plus composition has different opaque
        # fast paths and can reduce coverage even for two opaque sources.
        first = Image.frombytes('RGBA', size, source.constBits().tobytes(),
                                'raw', 'RGBA', source.bytesPerLine())
        second = Image.frombytes('RGBA', size, target.constBits().tobytes(),
                                 'raw', 'RGBA', target.bytesPerLine())
        pixels = Image.blend(first, second, fraction).tobytes()
        return QImage(pixels, *size, size[0] * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()

    @staticmethod
    def _ready_bytes(ready):
        return int(ready.image.sizeInBytes()) + (
            int(ready.previous.sizeInBytes()) if ready.previous is not None else 0)

    @property
    def input_bytes(self):
        return (self.active.byte_count if self.active is not None else 0) + sum(
            request.byte_count for request in self.pending.values())

    def _start(self):
        if self.closed or self.active is not None:
            return
        while self.pending:
            _slot, request = self.pending.popitem(last=False)
            state = self.owners.get(request.slot[0])
            if state is None or not self._live(state.reference()) or request.generation != state.generation:
                continue
            ready = self.cache.get(request.slot)
            if (ready is not None and (ready.generation, ready.domain) == (
                    request.generation, request.domain)
                    and (ready.stamp == request.stamp or time.monotonic() - ready.at < request.interval)):
                # A newer backdrop can queue while this role has no ready
                # material. Once the in-flight result is accepted, the same
                # freshness rule as request() applies before another render.
                # Keep the latest desire, so the existing animation clock
                # can retry after its interval without an extra timer.
                continue
            self.active = request
            # Only payload and a module-level pure renderer enter this closure.
            payload, render = request.payload, request.render
            def work():
                nonlocal payload
                started = time.perf_counter()
                try:
                    result = render(payload)
                    return result, time.perf_counter() - started
                finally:
                    # MusicTasks keeps its last action local while waiting
                    # for the next job. Do not let that idle closure retain
                    # an already-completed input outside this byte budget.
                    payload = None
            if self.tasks.submit(request.serial, work):
                self.submitted += 1
            else:
                self.active = None
                self.latest.pop(request.slot, None)
            break

    @Slot(object, object, object)
    def _completed(self, token, value, error):
        request = self.active
        if self.closed or request is None or token != request.serial:
            return
        self.active = None
        try:
            self._deliver(request, value, error)
        finally:
            # A receiver may delete its widget or fail while accepting a
            # result. Never strand unrelated bounded work behind that slot.
            self._start()

    def _deliver(self, request, value, error):
        state = self.owners.get(request.slot[0])
        owner = state.reference() if state is not None else None
        latest = self.latest.get(request.slot)
        valid = state is not None and self._live(owner) and request.generation == state.generation
        if valid and state.getter is not None:
            getter = state.getter()
            try:
                valid = getter is not None and getter() == state.scene
            except RuntimeError:
                valid = False
            if not valid:
                self._invalidate(request.slot[0])
        valid = valid and latest is not None and (latest.generation, latest.domain) == (
            request.generation, request.domain)
        image = value[0] if error is None and value is not None else None
        previous = self.cache.get(request.slot)
        if (valid and isinstance(image, QImage) and not image.isNull()
                and image.sizeInBytes() <= self.MAX_CACHE_BYTES
                and (previous is None or previous.serial < request.serial)):
            self.prepare_seconds = value[1]
            accepted_at = time.monotonic()
            source = None
            if (request.transition > 0. and previous is not None
                    and (previous.generation, previous.domain) == (request.generation, request.domain)
                    and previous.image.size() == image.size()):
                source = self._present(previous, accepted_at)
                if source.sizeInBytes() + image.sizeInBytes() > self.MAX_CACHE_BYTES:
                    source = None
            self._drop_ready(request.slot)
            self.cache[request.slot] = _Ready(request.generation, request.domain, request.stamp,
                                             request.serial, image, accepted_at, source, request.transition)
            self.cache_bytes += self._ready_bytes(self.cache[request.slot])
            while len(self.cache) > self.MAX_SLOTS or self.cache_bytes > self.MAX_CACHE_BYTES:
                self._drop_ready(next(iter(self.cache)))
            callback = request.ready_callback() if request.ready_callback is not None else None
            if callback is not None:
                callback(image, request.domain, request.stamp)
            try:
                owner.update(QRectF(*request.dirty).adjusted(-2., -2., 2., 2.).toAlignedRect())
            except RuntimeError:
                self._forget(request.slot[0])
        else:
            self.discarded += 1
            if valid and latest.serial == request.serial:
                self.failed[request.slot] = (request.generation, request.domain, request.stamp)

    def _drop_ready(self, slot):
        ready = self.cache.pop(slot, None)
        if ready is not None:
            self.cache_bytes -= self._ready_bytes(ready)

    def _invalidate(self, uid):
        state = self.owners.get(uid)
        if state is not None:
            state.generation += 1
        for slot in tuple(self.latest):
            if slot[0] == uid:
                self.latest.pop(slot, None)
                self.pending.pop(slot, None)
                self.failed.pop(slot, None)
                self._drop_ready(slot)

    def _forget(self, uid):
        state = self.owners.get(uid)
        owner = state.reference() if state is not None else None
        self._invalidate(uid)
        self.owners.pop(uid, None)
        if owner is not None:
            self.owner_ids.pop(owner, None)

    def _forget_reference(self, reference):
        owner = reference()
        uid = self.owner_ids.get(owner) if owner is not None else None
        if uid is not None:
            self._forget(uid)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Hide, QEvent.Type.Close, QEvent.Type.WindowStateChange):
            for uid, state in tuple(self.owners.items()):
                owner = state.reference()
                try:
                    affected = owner is watched or (owner is not None and owner.window() is watched)
                except RuntimeError:
                    affected = True
                if affected and (event.type() != QEvent.Type.WindowStateChange or not self._live(owner)):
                    self._invalidate(uid)
        return super().eventFilter(watched, event)

    def clear(self):
        for uid in tuple(self.owners):
            self._invalidate(uid)
        self.cache.clear()
        self.cache_bytes = 0

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.clear()
        self.tasks.close()
        self.active = None


_service = None


def glass_pixel_tasks(*, create=True):
    global _service
    app = QApplication.instance()
    if app is None:
        return None
    if _service is None and create:
        _service = GlassPixelTasks(app)
        app.aboutToQuit.connect(_service.close)
    return _service
