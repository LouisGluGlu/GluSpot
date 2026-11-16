"""Springs for width motion, and the magnet-dock latch.

Width, scroll and background springs have no fixed duration. Mass, stiffness
and damping ratio decide the path. Damping ratio 1 settles without wobble.
Retargeting keeps the current position and the current velocity.

The ticker follows the screen refresh rate and stops when nothing is moving.
The magnet latch, cover corner and playlist helpers are in this same module.
"""
from __future__ import annotations

import math
import os
import time

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPainterPath


def _finite(value, fallback=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if not math.isfinite(number):
        return float(fallback)
    return number


class SpringConfig:
    """Mass, stiffness and damping ratio for one spring."""

    def __init__(self, mass=1.0, stiffness=220.0, damping_ratio=1.0,
                 position_epsilon=0.002, velocity_epsilon=0.05):
        self.mass = _finite(mass, 1.0)
        self.stiffness = _finite(stiffness, 220.0)
        self.damping_ratio = _finite(damping_ratio, 1.0)
        self.position_epsilon = abs(_finite(position_epsilon, 0.002))
        self.velocity_epsilon = abs(_finite(velocity_epsilon, 0.05))
        if self.mass <= 0.0 or self.stiffness <= 0.0 or self.damping_ratio < 0.0:
            raise ValueError("spring mass and stiffness must be positive")

    @property
    def omega(self):
        return math.sqrt(self.stiffness / self.mass)

    @property
    def damping(self):
        return 2.0 * self.damping_ratio * math.sqrt(self.mass * self.stiffness)

    def replaced(self, **kwargs):
        values = dict(mass=self.mass, stiffness=self.stiffness, damping_ratio=self.damping_ratio,
                      position_epsilon=self.position_epsilon, velocity_epsilon=self.velocity_epsilon)
        values.update(kwargs)
        return SpringConfig(**values)


# Wide/narrow and expand/collapse. Critically damped, one place to retune.
WIDE_NARROW_SPRING = SpringConfig(mass=1.0, stiffness=220.0, damping_ratio=1.0)
# Edge bounce and dismiss-style gestures. One overshoot, then rest.
EDGE_SPRING = SpringConfig(mass=1.0, stiffness=260.0, damping_ratio=0.8)
# Flicks inside a list. No wobble.
SCROLL_SPRING = SpringConfig(mass=1.0, stiffness=180.0, damping_ratio=1.0)


class Spring:
    """Damped harmonic oscillator. `value` is the visible position."""

    def __init__(self, config=None, value=0.0, velocity=0.0, target=0.0):
        self.config = config or WIDE_NARROW_SPRING
        self.value = _finite(value)
        self.velocity = _finite(velocity)
        self.target = _finite(target)

    def retarget(self, target, velocity=None):
        """Aim at a new rest point without jumping the current pose."""
        self.target = _finite(target)
        if velocity is not None:
            self.velocity = _finite(velocity)
        return self

    @property
    def settled(self):
        config = self.config
        return (abs(self.value - self.target) <= config.position_epsilon
                and abs(self.velocity) <= config.velocity_epsilon)

    def step(self, dt):
        dt = _finite(dt)
        if dt <= 0.0:
            return self.value
        self.value, self.velocity = self._advance(dt)
        if self.settled:
            self.value = self.target
            self.velocity = 0.0
        return self.value

    def _advance(self, dt):
        config = self.config
        omega = config.omega
        zeta = config.damping_ratio
        x0 = self.value - self.target
        v0 = self.velocity
        if omega <= 0.0:
            return self.value, self.velocity
        if abs(zeta - 1.0) <= 1e-4:
            decay = math.exp(-omega * dt)
            slope = v0 + omega * x0
            displacement = (x0 + slope * dt) * decay
            velocity = (v0 - omega * slope * dt) * decay
        elif zeta < 1.0:
            damped = omega * math.sqrt(max(0.0, 1.0 - zeta * zeta))
            decay = math.exp(-zeta * omega * dt)
            if damped <= 1e-8:
                decay = math.exp(-omega * dt)
                slope = v0 + omega * x0
                displacement = (x0 + slope * dt) * decay
                velocity = (v0 - omega * slope * dt) * decay
            else:
                cosine = math.cos(damped * dt)
                sine = math.sin(damped * dt)
                swing = (v0 + zeta * omega * x0) / damped
                displacement = decay * (x0 * cosine + swing * sine)
                velocity = (-zeta * omega * displacement
                            + decay * (-x0 * damped * sine + swing * damped * cosine))
        else:
            spread = omega * math.sqrt(zeta * zeta - 1.0)
            root_fast = -zeta * omega + spread
            root_slow = -zeta * omega - spread
            # x = A e^(r1 t) + B e^(r2 t), with A + B = x0 and A r1 + B r2 = v0.
            amplitude = (v0 - root_slow * x0) / (root_fast - root_slow)
            other = x0 - amplitude
            fast = math.exp(root_fast * dt)
            slow = math.exp(root_slow * dt)
            displacement = amplitude * fast + other * slow
            velocity = amplitude * root_fast * fast + other * root_slow * slow
        return self.target + displacement, velocity


def rubber_band(offset, dimension, coefficient=0.55):
    """Apple-style edge resistance. The result stays inside the dimension."""
    offset = _finite(offset)
    dimension = abs(_finite(dimension))
    coefficient = abs(_finite(coefficient, 0.55))
    if offset == 0.0 or dimension <= 0.0 or coefficient <= 0.0:
        return 0.0
    sign = 1.0 if offset > 0.0 else -1.0
    stretched = abs(offset)
    resisted = (coefficient * stretched * dimension) / (dimension + coefficient * stretched)
    return sign * min(dimension, resisted)


def background_follow(fraction, duration_seconds, config=None):
    """How much of the destination background is visible at this point.

    Fraction 0 keeps the origin. The spring then carries the blend for the
    whole handoff, instead of cutting the origin image off early.
    """
    config = config or WIDE_NARROW_SPRING
    fraction = max(0.0, min(1.0, _finite(fraction)))
    duration = max(0.0, _finite(duration_seconds))
    spring = Spring(config, 0.0, 0.0, 1.0)
    spring.step(fraction * duration)
    return max(0.0, min(1.0, spring.value))


class FrameLog:
    """Recent ticker and paint samples. Printing is off unless asked."""

    def __init__(self):
        self.frames = []
        self.paints = []

    def note_frame(self, dt, dropped, subscribers):
        self.frames.append({"t": time.perf_counter(), "dt": dt, "dropped": bool(dropped),
                            "subscribers": int(subscribers)})
        del self.frames[:-240]
        if os.environ.get("GLUSPOT_FRAME_LOG") == "1":
            print(f"frame dt={dt * 1000.0:.2f}ms dropped={int(bool(dropped))} subs={subscribers}", flush=True)

    def note_paint(self, elapsed_ms, kind):
        self.paints.append({"t": time.perf_counter(), "ms": float(elapsed_ms), "kind": str(kind)})
        del self.paints[:-240]
        if os.environ.get("GLUSPOT_FRAME_LOG") == "1":
            print(f"paint kind={kind} ms={float(elapsed_ms):.2f}", flush=True)

    def summary(self):
        def pack(rows, key):
            values = [row[key] for row in rows]
            if not values:
                return None
            ordered = sorted(values)
            return {"count": len(values), "min": ordered[0], "median": ordered[len(ordered) // 2],
                    "max": ordered[-1]}
        dropped = sum(1 for row in self.frames if row["dropped"])
        return {"frames": pack(self.frames, "dt"), "dropped": dropped,
                "paints": pack(self.paints, "ms")}


FRAME_LOG = FrameLog()


class SpringTicker(QObject):
    """The only timer the new springs share."""

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = SpringTicker()
        return cls._instance

    @classmethod
    def reset_for_tests(cls):
        if cls._instance is not None:
            cls._instance._timer.stop()
            cls._instance._subs.clear()
            cls._instance._last = None
        cls._instance = None

    def __init__(self):
        super().__init__()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._subs = {}
        self._last = None

    def interval_ms(self):
        screen = QGuiApplication.primaryScreen()
        rate = 60.0
        if screen is not None:
            try:
                rate = float(screen.refreshRate())
            except (TypeError, ValueError, RuntimeError):
                rate = 60.0
        if not math.isfinite(rate) or rate < 10.0:
            rate = 60.0
        return max(1, int(round(1000.0 / rate)))

    @property
    def active(self):
        return self._timer.isActive()

    @property
    def subscriber_count(self):
        return len(self._subs)

    def add(self, token, callback):
        self._subs[token] = callback
        if not self._timer.isActive():
            self._last = time.perf_counter()
            self._timer.start(self.interval_ms())

    def remove(self, token):
        self._subs.pop(token, None)
        if not self._subs and self._timer.isActive():
            self._timer.stop()
            self._last = None

    def _tick(self):
        now = time.perf_counter()
        interval = self.interval_ms() / 1000.0
        raw = interval if self._last is None else max(0.0, now - self._last)
        self._last = now
        dropped = raw > interval * 1.5
        FRAME_LOG.note_frame(raw, dropped, len(self._subs))
        # A stalled frame is logged, then the springs take a bounded step
        # so one hitch cannot throw the pose to the end.
        dt = min(raw, 0.064)
        for callback in list(self._subs.values()):
            callback(dt, dropped)


def note_paint_frame(elapsed_ms, kind):
    FRAME_LOG.note_paint(elapsed_ms, kind)


class SpringProgressAnimation(QObject):
    """0..1 progress with the same start/stop/scrub calls the old animation had.

    Live playback integrates the spring. setCurrentTime still jumps to the
    pose the spring would have at that moment, so tests can finish a handoff
    without waiting for the screen.
    """

    valueChanged = Signal(float)
    finished = Signal()

    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.config = config or WIDE_NARROW_SPRING
        self._spring = Spring(self.config)
        self._duration = 1000
        self._start = 0.0
        self._end = 1.0
        self.release_velocity = 0.0
        self._current_time = 0
        self._state = QAbstractAnimation.State.Stopped
        self._curve = QEasingCurve(QEasingCurve.Type.Linear)
        self._token = object()

    @property
    def spring(self):
        return self._spring

    def setDuration(self, milliseconds):
        self._duration = max(0, int(milliseconds))

    def duration(self):
        return self._duration

    def setEasingCurve(self, curve):
        self._curve = QEasingCurve(curve)

    def easingCurve(self):
        return self._curve

    def setStartValue(self, value):
        self._start = _finite(value)

    def setEndValue(self, value):
        self._end = _finite(value)

    def state(self):
        return self._state

    def currentTime(self):
        return int(self._current_time)

    def stop(self):
        self._state = QAbstractAnimation.State.Stopped
        ticker = SpringTicker.instance()
        ticker.remove(self._token)

    def pause(self):
        if self._state != QAbstractAnimation.State.Running:
            return
        self._state = QAbstractAnimation.State.Paused
        SpringTicker.instance().remove(self._token)

    def resume(self):
        if self._state != QAbstractAnimation.State.Paused:
            return
        self._state = QAbstractAnimation.State.Running
        self._wall_clock = time.perf_counter()
        SpringTicker.instance().add(self._token, self._on_tick)

    def start(self):
        self._current_time = 0
        self._wall_clock = time.perf_counter()
        self._spring = Spring(self.config, self._start, self.release_velocity, self._end)
        self._state = QAbstractAnimation.State.Running
        SpringTicker.instance().add(self._token, self._on_tick)
        self.valueChanged.emit(float(self._spring.value))

    def retarget(self, target, velocity=None):
        """Keep the current pose and velocity, then aim at a new end."""
        self._end = _finite(target)
        self._spring.retarget(self._end, velocity)
        if self._state != QAbstractAnimation.State.Running:
            self._state = QAbstractAnimation.State.Running
            SpringTicker.instance().add(self._token, self._on_tick)

    def setCurrentTime(self, milliseconds):
        milliseconds = max(0, int(milliseconds))
        self._current_time = milliseconds
        if self._duration > 0 and milliseconds >= self._duration:
            value, velocity = self._end, 0.0
        else:
            sample = Spring(self.config, self._start, self.release_velocity, self._end)
            sample.step(milliseconds / 1000.0)
            value, velocity = sample.value, sample.velocity
        self._spring.value = value
        self._spring.velocity = velocity
        self._spring.target = self._end
        self.valueChanged.emit(float(value))
        if self._state == QAbstractAnimation.State.Running and self._duration > 0 and milliseconds >= self._duration:
            self.stop()
            self.finished.emit()

    def advance_by_wall_clock(self):
        """Step by elapsed time. A drag can call this when timers are not delivered."""
        if self._state != QAbstractAnimation.State.Running:
            return
        now = time.perf_counter()
        last = getattr(self, "_wall_clock", None)
        self._wall_clock = now
        if last is None:
            return
        dt = min(0.05, now - last)
        if dt >= 0.001:
            self._step(dt)

    def _on_tick(self, dt, _dropped):
        if self._state != QAbstractAnimation.State.Running:
            return
        self._wall_clock = time.perf_counter()
        if dt >= 0.001:
            self._step(dt)

    def _step(self, dt):
        self._current_time += dt * 1000.0
        self._spring.step(dt)
        self.valueChanged.emit(float(self._spring.value))
        if self._spring.settled:
            self._spring.value = self._spring.target
            self._spring.velocity = 0.0
            self.valueChanged.emit(float(self._spring.target))
            self.stop()
            self.finished.emit()


class FlickScroll:
    """Velocity carry inside the range, rubber-band only when already at the edge.

    A single jump that lands on the edge does not rubber-band. The finger or
    the wheel has to push again, from the edge, before the content stretches.
    """

    def __init__(self, scroll=None, edge=None):
        self.scroll_config = scroll or SCROLL_SPRING
        self.edge_config = edge or EDGE_SPRING
        self.overscroll = 0.0
        self.velocity = 0.0
        self._last_t = None
        self._spring = None
        self._mode = None
        self._lo = 0.0
        self._hi = 0.0
        self._span = 1.0
        self._token = object()
        self.on_step = None

    def cancel(self):
        self._spring = None
        self._mode = None
        self.velocity = 0.0
        if SpringTicker._instance is not None:
            SpringTicker._instance.remove(self._token)

    def observe(self, delta_px, now):
        now = _finite(now, time.monotonic())
        elapsed = 0.016 if self._last_t is None else max(1e-3, now - self._last_t)
        sample = _finite(delta_px) / elapsed
        if self._last_t is None:
            self.velocity = sample
        else:
            self.velocity = self.velocity * 0.35 + sample * 0.65
        self._last_t = now

    def absorb(self, previous, proposed, lo, hi, span):
        lo = _finite(lo)
        hi = _finite(hi)
        previous = _finite(previous)
        proposed = _finite(proposed)
        span = max(1.0, abs(_finite(span, 1.0)))
        self._lo, self._hi, self._span = lo, hi, span
        if hi <= lo:
            self.overscroll = 0.0
            return lo, 0.0
        if proposed < lo and previous <= lo + 0.5:
            self.overscroll = rubber_band(proposed - lo, span)
            self._arm_edge()
            return lo, self.overscroll
        if proposed > hi and previous >= hi - 0.5:
            self.overscroll = rubber_band(proposed - hi, span)
            self._arm_edge()
            return hi, self.overscroll
        self.overscroll = 0.0
        return max(lo, min(hi, proposed)), 0.0

    def arm_coast(self, position, lo, hi, span=None):
        """Continue from the release velocity. Small releases stay put."""
        if abs(self.velocity) < 80.0:
            return False
        lo = _finite(lo)
        hi = _finite(hi)
        position = _finite(position)
        span = max(1.0, abs(_finite(span if span is not None else hi - lo, 1.0)))
        self._lo, self._hi, self._span = lo, hi, span
        coast = position + self.velocity / max(1e-3, self.scroll_config.omega)
        outward = (coast < lo and self.velocity < 0.0) or (coast > hi and self.velocity > 0.0)
        if outward and (position <= lo + 0.5 or position >= hi - 0.5):
            edge = lo if coast < lo else hi
            self.overscroll = rubber_band(position + self.velocity * 0.08 - edge, span)
            self._arm_edge()
            return True
        self._mode = "coast"
        self._spring = Spring(self.scroll_config, position, self.velocity, max(lo, min(hi, coast)))
        self._subscribe()
        return True

    def _arm_edge(self):
        self._mode = "edge"
        self._spring = Spring(self.edge_config, self.overscroll, self.velocity, 0.0)
        self._subscribe()

    def _subscribe(self):
        if QGuiApplication.instance() is None:
            return
        SpringTicker.instance().add(self._token, self._on_tick)

    def step(self, dt):
        if self._spring is None:
            return None
        self._spring.step(dt)
        self.velocity = self._spring.velocity
        if self._mode == "edge":
            self.overscroll = self._spring.value
            if self._spring.settled:
                self.overscroll = 0.0
                self.velocity = 0.0
                self._spring = None
                self._mode = None
                if SpringTicker._instance is not None:
                    SpringTicker._instance.remove(self._token)
            return ("overscroll", self.overscroll)
        value = self._spring.value
        if value < self._lo or value > self._hi:
            edge = self._lo if value < self._lo else self._hi
            self.overscroll = rubber_band(value - edge, self._span)
            self._mode = "edge"
            self._spring = Spring(self.edge_config, self.overscroll, self.velocity, 0.0)
            return ("position", edge, self.overscroll)
        if self._spring.settled:
            resting = self._spring.target
            self.overscroll = 0.0
            self.velocity = 0.0
            self._spring = None
            self._mode = None
            if SpringTicker._instance is not None:
                SpringTicker._instance.remove(self._token)
            return ("position", resting, 0.0)
        return ("position", value, 0.0)

    def _on_tick(self, dt, _dropped):
        result = self.step(dt)
        if result is not None and self.on_step is not None:
            self.on_step(result)

COVER_CORNER_RATIO = 0.055
COVER_CORNER_CAP = 0.24

SNAP_SECONDS = 1.0           # holding still this long over the slot means "stay here"
OVERLAP_ARM = 0.50
OVERLAP_DROP = 0.40
DWELL_MOVE = 8.0
# After the dwell, the tile fades out instead of changing color or caption.
MAGNET_FADE_SECONDS = 0.22


def scaled_cover_corner_radius(side: float, *, ratio: float = COVER_CORNER_RATIO) -> float:
    """Corner radius that stays the same fraction of the cover at every size."""
    side = max(0.0, float(side))
    radius = side * float(ratio)
    return min(radius, side * COVER_CORNER_CAP)


def squircle_path(rect: QRectF, exponent: float = 4.0, steps: int = 64) -> QPainterPath:
    """Continuous-curvature cover. The exponent is independent of size."""
    path = QPainterPath()
    if rect.width() < 2 or rect.height() < 2:
        return path
    center_x, center_y = rect.center().x(), rect.center().y()
    radius_x, radius_y = rect.width() / 2.0, rect.height() / 2.0
    power = 2.0 / exponent
    points = []
    for index in range(steps):
        angle = math.tau * index / steps
        cosine, sine = math.cos(angle), math.sin(angle)
        points.append(QPointF(
            center_x + radius_x * math.copysign(abs(cosine) ** power, cosine),
            center_y + radius_y * math.copysign(abs(sine) ** power, sine),
        ))
    path.moveTo(points[0])
    for point in points[1:]:
        path.lineTo(point)
    path.closeSubpath()
    return path


SMOOTH_CORNER_EXTENT = 1.8   # a squircle corner this many radii long cuts like a circle of one radius


def smooth_rounded_rect_path(rect: QRectF, radius: float, steps: int = 40) -> QPainterPath:
    """A rounded rectangle with continuous-curvature (squircle) corners.

    Each corner is a quarter superellipse (exponent 4) spanning
    ``SMOOTH_CORNER_EXTENT * radius`` along both edges, so it eases into the
    straight edge instead of meeting it with a visible kink, while cutting
    off about as much as a circular corner of ``radius`` would.
    """
    path = QPainterPath()
    rect = QRectF(rect)
    if rect.width() <= 0 or rect.height() <= 0:
        return path
    extent = min(max(0.0, float(radius)) * SMOOTH_CORNER_EXTENT, rect.width() / 2.0, rect.height() / 2.0)
    if extent <= 0.01:
        path.addRect(rect)
        return path
    power = 0.5   # 2 / exponent 4

    def corner(cx, cy, sx, sy, start_angle):
        points = []
        for index in range(steps + 1):
            angle = start_angle + (math.pi / 2.0) * index / steps
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            points.append(QPointF(cx + sx * extent * math.copysign(abs(cos_a) ** power, cos_a),
                                  cy + sy * extent * math.copysign(abs(sin_a) ** power, sin_a)))
        return points

    left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
    outline = []
    # Clockwise from the top edge, each corner's superellipse centred inside it.
    outline += corner(right - extent, top + extent, 1, 1, -math.pi / 2.0)      # top right
    outline += corner(right - extent, bottom - extent, 1, 1, 0.0)              # bottom right
    outline += corner(left + extent, bottom - extent, 1, 1, math.pi / 2.0)     # bottom left
    outline += corner(left + extent, top + extent, 1, 1, math.pi)              # top left
    path.moveTo(outline[0])
    for point in outline[1:]:
        path.lineTo(point)
    path.closeSubpath()
    return path


def width_overlap(island_left: float, island_right: float, island_width: float,
                  tile_left: float, tile_right: float) -> float:
    """Fraction of the island width that lies across the tile."""
    if island_width <= 1.0:
        return 0.0
    if island_right <= tile_left or tile_right <= island_left:
        return 0.0
    span = min(island_right, tile_right) - max(island_left, tile_left)
    return max(0.0, span / float(island_width))


def magnet_overlap(island: QRectF, tile: QRectF) -> float:
    """Share of the island that sits on the slot, 0..1 (snap needs 0.5).

    Width overlap times height overlap. The height share is measured
    against the shorter of the two, because the slot is only as tall as its
    bar: an island resting on the bar counts fully, one pulled off it above
    or below counts nothing (width alone used to keep a pulled-out island
    "over" the slot, so it snapped back).
    """
    if island is None or tile is None or island.isNull() or tile.isNull():
        return 0.0
    vertical = max(0.0, min(island.bottom(), tile.bottom()) - max(island.top(), tile.top()))
    vertical /= max(1.0, min(island.height(), tile.height()))
    return rect_width_overlap(island, tile) * min(1.0, vertical)


def rect_width_overlap(island: QRectF, tile: QRectF) -> float:
    if island is None or tile is None:
        return 0.0
    return width_overlap(island.left(), island.right(), island.width(), tile.left(), tile.right())


class MagnetLatch:
    """Overlap hysteresis and a 2 second dwell. Paint never calls this."""

    def __init__(self):
        self.armed = False
        self.pinned = False
        self.anchor: tuple[float, float] | None = None
        self.anchor_time = 0.0
        self.elapsed = 0.0

    def observe(self, x: float, y: float, overlap: float, now: float) -> None:
        if self.anchor is None or math.hypot(x - self.anchor[0], y - self.anchor[1]) > DWELL_MOVE:
            self.anchor = (x, y)
            self.anchor_time = now
            self.pinned = False
            self.elapsed = 0.0
        else:
            self.elapsed = max(0.0, now - self.anchor_time)
            if self.elapsed > SNAP_SECONDS:
                self.pinned = True
        if overlap >= OVERLAP_ARM:
            self.armed = True
        elif overlap <= OVERLAP_DROP:
            self.armed = False

    def decision(self) -> str:
        if self.pinned:
            return "stay"
        if self.armed and self.elapsed <= SNAP_SECONDS:
            return "snap"
        return "stay"

    def caption(self) -> str:
        # A finished dwell removes the tile. It does not switch to a stay label.
        if self.pinned:
            return ""
        if self.armed:
            return "松手吸附"
        return "吸附"


def magnet_fade_opacity(elapsed: float, duration: float = MAGNET_FADE_SECONDS) -> float:
    """1 at the start of the dwell ending, 0 when the tile has faded away."""
    span = float(duration)
    if span <= 0.0:
        return 0.0
    amount = max(0.0, min(1.0, float(elapsed) / span))
    smooth = amount * amount * (3.0 - 2.0 * amount)
    return 1.0 - smooth


class CriticalSpring:
    """Scalar spring, damping ratio 1. A new target keeps position and velocity."""

    def __init__(self, omega: float = 11.0):
        self.omega = omega
        self.value = 0.0
        self.velocity = 0.0
        self.target = 0.0

    def set_target(self, on: bool) -> None:
        self.target = 1.0 if on else 0.0

    def advance(self, amount: float = 1.0) -> None:
        """Move the rest point further along. Position and velocity stay put."""
        self.target += float(amount)

    def step(self, dt: float) -> float:
        remaining = min(max(0.0, dt), 0.05)
        omega = self.omega
        while remaining > 0.0:
            step = min(0.008, remaining)
            acceleration = omega * omega * (self.target - self.value) - 2.0 * omega * self.velocity
            self.velocity += acceleration * step
            self.value += self.velocity * step
            remaining -= step
        if abs(self.target - self.value) < 0.001 and abs(self.velocity) < 0.02:
            self.value = self.target
            self.velocity = 0.0
        return self.value


class RectSpring:
    """Four-component spring, damping ratio 1. Idle while the pointer owns the island."""

    def __init__(self, omega: float = 12.0):
        self.omega = omega
        self.active = False
        self.value = [0.0, 0.0, 0.0, 0.0]
        self.velocity = [0.0, 0.0, 0.0, 0.0]
        self.target = [0.0, 0.0, 0.0, 0.0]

    def start(self, current: QRectF, target: QRectF) -> None:
        self.active = True
        self.value = [current.x(), current.y(), current.width(), current.height()]
        self.velocity = [0.0, 0.0, 0.0, 0.0]
        self.target = [target.x(), target.y(), target.width(), target.height()]

    def step(self, dt: float) -> QRectF:
        remaining = min(max(0.0, dt), 0.05)
        omega = self.omega
        while remaining > 0.0:
            step = min(0.008, remaining)
            for index in range(4):
                acceleration = (omega * omega * (self.target[index] - self.value[index])
                                - 2.0 * omega * self.velocity[index])
                self.velocity[index] += acceleration * step
                self.value[index] += self.velocity[index] * step
            remaining -= step
        if all(abs(self.target[index] - self.value[index]) < 0.4 and abs(self.velocity[index]) < 8.0
               for index in range(4)):
            self.value = list(self.target)
            self.velocity = [0.0, 0.0, 0.0, 0.0]
            self.active = False
        return QRectF(*self.value)

    def cancel(self) -> None:
        self.active = False


def magnet_tile_rect(screen: QRectF, *, mode: str, bar: QRectF | None = None) -> QRectF:
    """Finder: a slot inside the Finder bar. Taskbar: a slot on the taskbar.

    Free placement does not use this rect. The island itself is not the tile.
    """
    screen = QRectF(screen)
    if screen.isNull() or screen.width() < 8.0 or screen.height() < 8.0:
        return QRectF()
    if mode not in {"finder", "taskbar"}:
        return QRectF()
    bar = QRectF(bar) if bar is not None else QRectF()
    if not bar.isNull() and bar.height() >= 8.0 and bar.width() >= 8.0:
        height = min(bar.height(), screen.height() * 0.2)
        width = min(bar.width(), max(160.0, min(420.0, bar.width() * 0.42)))
        width = min(width, screen.width())
        x = bar.center().x() - width / 2.0
        y = bar.top() + (bar.height() - height) / 2.0
    else:
        width = min(340.0, max(160.0, screen.width() * 0.34))
        height = 48.0 if mode == "finder" else 40.0
        height = min(height, screen.height() * 0.2)
        x = screen.center().x() - width / 2.0
        y = screen.top() if mode == "finder" else screen.bottom() - height
    y = min(max(screen.top(), y), screen.bottom() - height)
    x = min(max(screen.left(), x), screen.right() - width)
    return QRectF(x, y, width, height)


def magnet_tile_radius(tile: QRectF, *, mode: str) -> float:
    """Finder slots are capsules the height of the bar. Taskbar slots stay softer."""
    if tile.isNull():
        return 0.0
    if mode == "finder":
        return min(tile.width(), tile.height()) / 2.0
    return max(6.0, min(12.0, tile.height() * 0.22))


def docked_island_rect(island: QRectF, tile: QRectF) -> QRectF:
    """Same island size, centered on the tile."""
    return QRectF(tile.center().x() - island.width() / 2.0,
                  tile.center().y() - island.height() / 2.0,
                  island.width(), island.height())


def song_already_in_playlist(playlist_id: str, *, contains_track: bool = False,
                             remembered: set[str] | frozenset[str] | tuple[str, ...] | None = None) -> bool:
    """The add path refuses a playlist that already holds this song."""
    if contains_track:
        return True
    return bool(remembered) and str(playlist_id) in remembered
