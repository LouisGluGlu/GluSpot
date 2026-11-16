"""Cover-slot broadcast indicator: scrolling rings, or a round face.

Style one reuses the existing speech rings. Style two ports the measured
Grok-style avatar from Jérémy Perret's MIT-licensed bloub project: one
circle body, two slanted eye capsules on a sphere, gaze drift, blinking,
and exponential ease-outs. Eye expressions that squint, smile, and look
in different directions are original vector shapes. The ComposeAnimation
repository has no license, so none of its code is copied. See
``THIRD_PARTY_NOTICES.md``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient, QTransform

from local.music_surface_palette import relative_luminance


BROADCAST_STYLE_RINGS = "rings"
BROADCAST_STYLE_FACE = "face"
BROADCAST_STYLES = (BROADCAST_STYLE_RINGS, BROADCAST_STYLE_FACE)

# Style two's round face. "accent" is the adopted body: mostly the theme
# color, held dark enough that the white eyes stay readable.
BROADCAST_FACE_BODY_MODE = "accent"
BROADCAST_FACE_BODY_MODES = ("current", "accent", "warm", "album")

# preparing / speaking / paused / ended, plus idle when nothing is showing.
BROADCAST_VISUAL_STATES = ("preparing", "speaking", "paused", "ended", "idle")


def normalize_broadcast_style(value) -> str:
    """Unknown values stay on the existing scrolling rings."""
    text = str(value or "").strip().lower()
    if text in {BROADCAST_STYLE_FACE, "2", "style2", "样式二"}:
        return BROADCAST_STYLE_FACE
    return BROADCAST_STYLE_RINGS


def broadcast_visual_state(
    *,
    speaking: bool = False,
    presentation_state: str = "idle",
    cover_paused: bool = False,
    slot: float = 0.0,
    introduction_pending: bool = False,
) -> str:
    """Map a provider snapshot onto one indicator expression.

    The cover slot itself is only occupied while ``slot`` is above zero,
    which tracks active speech. Preparing is still named so the same map
    drives the preview and any in-flight blend.
    """
    raw = presentation_state if isinstance(presentation_state, str) else "idle"
    # ``cover_paused`` means the voice is speaking over ducked music, which is
    # the ordinary broadcast. It used to freeze the rings and squint the face
    # for the whole speech; speech always animates now.
    if speaking or raw == "speaking":
        return "speaking"
    if slot > 0.001:
        return "ended"
    if raw in {"loading", "preparing"} or introduction_pending:
        return "preparing"
    if raw in {"failed", "ended"}:
        return "ended"
    return "idle"


@dataclass(frozen=True)
class BroadcastMotion:
    state: str
    expression: str
    animate: bool
    use_bands: bool


_MOTIONS = {
    "preparing": BroadcastMotion("preparing", "attentive", True, False),
    "speaking": BroadcastMotion("speaking", "talking", True, True),
    "paused": BroadcastMotion("paused", "squint", False, False),
    "ended": BroadcastMotion("ended", "happy", True, False),
    "idle": BroadcastMotion("idle", "idle", False, False),
}


def broadcast_motion(state: str) -> BroadcastMotion:
    return _MOTIONS.get(state, _MOTIONS["idle"])


def broadcast_indicator_rect(cover: QRectF) -> QRectF:
    """The indicator uses the cover's rectangle, not a second slot."""
    return QRectF(cover)


def broadcast_ease(progress: float) -> float:
    """Exponential ease-out. The body does not overshoot."""
    t = max(0.0, min(1.0, float(progress)))
    return 1.0 - (1.0 - t) ** 5


def broadcast_slot_visible(slot: float) -> bool:
    return float(slot) > 0.001


# --- Face, ported from bloub's measured sphere model --------------------
# Constants are the published measurements (face.ts / expressions.ts), not
# rounded stand-ins. Screen axes: x right, y down, z toward the viewer.

EYE_SPLIT = 15.46
EYE_W = 0.186
EYE_H = 0.412
REST_GAZE = (28.49, 28.62, -13.0)
BLINK_DUR = 0.18


def _imul(left: int, right: int) -> int:
    def signed(value: int) -> int:
        value &= 0xFFFFFFFF
        return value - 0x100000000 if value >= 0x80000000 else value
    return (signed(left) * signed(right)) & 0xFFFFFFFF


def _mulberry32(seed: int):
    state = seed & 0xFFFFFFFF

    def rng() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        mixed = _imul(state ^ (state >> 15), 1 | state)
        mixed = (mixed + _imul(mixed ^ (mixed >> 7), 61 | mixed)) & 0xFFFFFFFF
        mixed ^= mixed
        return ((mixed ^ (mixed >> 14)) & 0xFFFFFFFF) / 4294967296

    return rng


def _blink_times() -> tuple[float, ...]:
    rng = _mulberry32(0x5EED)
    times = []
    moment = 1.4
    while moment < 900.0:
        times.append(moment)
        moment += 1.9 + rng() * 2.7
        if rng() < 0.18:
            times.append(moment)
            moment += 0.24
    return tuple(times)


_BLINKS = _blink_times()


def _loop_noise(t: float, period: float, seed: float = 0.0) -> float:
    phase = (t / period) * math.tau
    return (0.55 * math.sin(phase + seed)
            + 0.30 * math.sin(2.0 * phase + seed * 1.7 + 1.1)
            + 0.15 * math.sin(3.0 * phase + seed * 2.3 + 2.4))


def _blink_lid(t: float) -> float:
    for start in _BLINKS:
        if t < start:
            break
        k = (t - start) / BLINK_DUR
        if 0.0 <= k <= 1.0:
            return 1.0 - k / 0.45 if k < 0.45 else (k - 0.45) / 0.55
    return 1.0


def _blink_scale(lid: float) -> float:
    return 0.06 + 0.94 * max(0.0, min(1.0, lid))


def _spin(u, v, angle: float):
    c, s = math.cos(angle), math.sin(angle)
    return (
        (u[0] * c + v[0] * s, u[1] * c + v[1] * s, u[2] * c + v[2] * s),
        (v[0] * c - u[0] * s, v[1] * c - u[1] * s, v[2] * c - u[2] * s),
    )


def _eye_poses(yaw: float, pitch: float, roll: float, scale: float, split: float):
    """Orthographic eyes on a unit sphere. Index 0 is the inner eye."""
    forward, right, down = (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
    forward, right = _spin(forward, right, math.radians(yaw))
    down, forward = _spin(down, forward, math.radians(pitch))
    right, down = _spin(right, down, math.radians(roll))

    def build(side: float):
        eye_forward, eye_right = _spin(forward, right, math.radians(split * side))
        return {
            "x": eye_forward[0] * scale,
            "y": eye_forward[1] * scale,
            "a": eye_right[0],
            "b": eye_right[1],
            "c": down[0],
            "d": down[1],
            "depth": eye_forward[2],
        }

    return build(-1.0), build(1.0)


# Original expression targets. ``shape`` "arc" is the happy curved eye.
_EXPRESSIONS = {
    "idle": {"gaze": REST_GAZE, "split": EYE_SPLIT, "w": EYE_W, "h": EYE_H,
             "tilt": 0.0, "open": 1.0, "shape": "capsule", "wander": 1.0},
    "attentive": {"gaze": (-16.0, 6.0, -6.0), "split": 16.5, "w": 0.20, "h": 0.40,
                  "tilt": 0.0, "open": 1.0, "shape": "capsule", "wander": 0.55},
    "talking": {"gaze": (8.0, 4.0, -8.0), "split": 16.0, "w": 0.22, "h": 0.48,
                "tilt": -6.0, "open": 1.0, "shape": "capsule", "wander": 1.15},
    "squint": {"gaze": (12.0, -12.0, -4.0), "split": 15.2, "w": 0.32, "h": 0.22,
               "tilt": 8.0, "open": 0.78, "shape": "capsule", "wander": 0.12},
    "happy": {"gaze": (4.0, 12.0, 0.0), "split": 17.0, "w": 0.32, "h": 0.18,
              "tilt": 18.0, "open": 1.0, "shape": "arc", "wander": 0.35},
}


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _blend_expression(left: dict, right: dict, t: float) -> dict:
    if t >= 1.0:
        return right
    if t <= 0.0:
        return left
    gaze = tuple(_lerp(a, b, t) for a, b in zip(left["gaze"], right["gaze"]))
    shape = right["shape"] if t >= 0.5 else left["shape"]
    return {
        "gaze": gaze,
        "split": _lerp(left["split"], right["split"], t),
        "w": _lerp(left["w"], right["w"], t),
        "h": _lerp(left["h"], right["h"], t),
        "tilt": _lerp(left["tilt"], right["tilt"], t),
        "open": _lerp(left["open"], right["open"], t),
        "shape": shape,
        "wander": _lerp(left["wander"], right["wander"], t),
    }


def broadcast_expression(state: str, *, previous: str | None = None, blend: float = 1.0) -> dict:
    motion = broadcast_motion(state)
    target = _EXPRESSIONS[motion.expression]
    if not previous or blend >= 1.0:
        return target
    origin = _EXPRESSIONS.get(broadcast_motion(previous).expression, _EXPRESSIONS["idle"])
    return _blend_expression(origin, target, broadcast_ease(blend))


def broadcast_face_sample(t: float, expression: dict, *, animate: bool = True) -> dict:
    """Pure function of time. Frozen ``t`` holds gaze, blink, and breath."""
    moment = float(t) if animate else float(t)
    wander = expression["wander"] if animate else 0.0
    yaw, pitch, roll = expression["gaze"]
    if animate:
        yaw += (_loop_noise(moment, 11.3, 0.4) * 5.5 + _loop_noise(moment, 3.7, 2.1) * 1.6) * wander
        pitch += (_loop_noise(moment, 9.1, 1.3) * 4.2 + _loop_noise(moment, 4.3, 0.7) * 1.3) * wander
        roll += _loop_noise(moment, 13.7, 3.2) * 2.2 * wander
    lid = _blink_lid(moment) if animate else 1.0
    # A state change closes the eyes once, then eases them open.
    return {
        "yaw": yaw,
        "pitch": pitch,
        "roll": roll,
        "lid": lid,
        "breath": 1.0 + math.sin((moment / 3.4) * math.tau) * 0.005 if animate else 1.0,
        "drift_x": _loop_noise(moment, 7.9, 1.9) * 0.006 if animate else 0.0,
        "drift_y": _loop_noise(moment, 5.3, 0.3) * 0.007 if animate else 0.0,
        "open": expression["open"],
        "shape": expression["shape"],
        "w": expression["w"],
        "h": expression["h"],
        "tilt": expression["tilt"],
        "split": expression["split"],
    }


def face_eye_heights(state: str, t: float) -> tuple[float, float]:
    """Screen-space eye heights. Tests use this to tell expressions apart."""
    expression = broadcast_expression(state)
    sample = broadcast_face_sample(t, expression, animate=broadcast_motion(state).animate)
    poses = _eye_poses(sample["yaw"], sample["pitch"], sample["roll"], 1.0, sample["split"])
    k = _blink_scale(min(sample["lid"], sample["open"]))
    heights = []
    for pose in poses:
        heights.append(abs(pose["d"]) * sample["h"] * k)
    return tuple(heights)


def broadcast_face_colors(body: QColor, mode: str | None = None) -> tuple[QColor, QColor]:
    """Named body colors. The adopted default is the theme-colored accent.

    The eyes stay white. A bright theme is pulled down in linear light so
    the circle still reads on a white cover.
    """
    ink = (10, 10, 12)
    accent = QColor(body)
    if not accent.isValid():
        accent = QColor(*ink)
    mode = BROADCAST_FACE_BODY_MODE if mode is None else mode
    eyes = QColor(245, 246, 248)
    if mode == "album":
        # The cover's own colour, kept as it is: the caller already separated
        # it from what sits behind the face and kept room for white eyes.
        return QColor(accent), eyes
    if mode == "warm":
        return QColor(112, 100, 92), eyes
    if mode == "accent":
        mixed = QColor(*(
            round(channel * 0.28 + accent_channel * 0.72)
            for channel, accent_channel in zip(ink, accent.getRgb()[:3])
        ))
        luminance = relative_luminance(mixed)
        if luminance > 0.18:
            # Scale in linear light to retain the hue and white-eye contrast.
            # The 0.01 margin absorbs 8-bit rounding at the luminance ceiling.
            channels = []
            for channel in mixed.getRgb()[:3]:
                srgb = channel / 255.0
                linear = srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4
                linear *= 0.17 / luminance
                srgb = 12.92 * linear if linear <= 0.0031308 else 1.055 * linear ** (1.0 / 2.4) - 0.055
                channels.append(round(srgb * 255.0))
            mixed = QColor(*channels)
        return mixed, eyes
    if mode != "current":
        raise ValueError(f"Unknown broadcast face body mode: {mode!r}")
    mixed = QColor(*(
        round(channel * 0.72 + accent_channel * 0.28)
        for channel, accent_channel in zip(ink, accent.getRgb()[:3])
    ))
    if relative_luminance(mixed) > 0.22:
        mixed = QColor(*(round(channel * 0.84 + accent_channel * 0.16)
                         for channel, accent_channel in zip(ink, accent.getRgb()[:3])))
    return mixed, eyes


def _transition_lid(blend: float) -> float:
    if blend >= 1.0 or blend <= 0.0:
        return 1.0
    return abs(blend * 2.0 - 1.0)


def paint_broadcast_face(
    painter: QPainter,
    rect: QRectF,
    t: float,
    state: str,
    body: QColor,
    *,
    previous: str | None = None,
    blend: float = 1.0,
    body_mode: str | None = None,
    bands=None,
    clip: str | None = None,
    clip_t: float = 0.0,
    pose=None,
) -> None:
    from local.broadcast_face_clips import clip_pose
    expression = broadcast_expression(state, previous=previous, blend=blend)
    motion = broadcast_motion(state)
    clip_state = pose if pose is not None else (clip_pose(clip, clip_t) if clip else None)
    if clip_state is not None:
        expression = dict(expression)
        expression["wander"] = expression["wander"] * clip_state.wander
    animate = motion.animate or blend < 1.0 or clip_state is not None
    sample = broadcast_face_sample(t, expression, animate=animate)
    if clip_state is not None:
        sample["yaw"] += clip_state.yaw
        sample["pitch"] += clip_state.pitch
        sample["roll"] += clip_state.roll
        sample["w"] *= clip_state.eye_w
        sample["h"] *= clip_state.eye_h
        if clip_state.shape:
            if clip_state.shape in {"arc", "closed"} and sample["shape"] == "capsule":
                # Curved eyes are strokes: use the happy eye's proportions,
                # or a capsule-sized stroke turns into a blob.
                sample["w"] = _EXPRESSIONS["happy"]["w"] * clip_state.eye_w
                sample["h"] = _EXPRESSIONS["happy"]["h"] * clip_state.eye_h
            sample["shape"] = clip_state.shape
    ink, eyes = broadcast_face_colors(body, body_mode)
    # Nothing follows the raw voice level: the body used to swell with it
    # (zooming) and then the eyes squeezed with it every frame, which read as
    # the eyes twitching. Speaking is shown by the clip's motion alone.
    radius = min(rect.width(), rect.height()) * 0.46
    if clip_state is not None:
        # One size for every clip (resting, speaking and the hopping ones),
        # with room for a hop and a stretch inside the cover.
        radius *= CLIP_BODY_SCALE
    else:
        radius *= sample["breath"]
    center_x = rect.center().x() + sample["drift_x"] * radius
    center_y = rect.center().y() + sample["drift_y"] * radius
    if clip_state is not None:
        center_x += clip_state.sway * radius
        center_y -= clip_state.hop * radius
    painter.save()
    # No clip scales the body: squash and stretch read as the bot zooming
    # and pushed it past the cover edge. Motion is hops, turns and eyes.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_body_brush(ink, center_x, center_y, radius))
    painter.drawEllipse(QRectF(center_x - radius, center_y - radius, radius * 2.0, radius * 2.0))
    lid = min(sample["lid"], _transition_lid(blend))
    k = _blink_scale(min(lid, sample["open"]))
    hidden = clip_state.eyes_hidden if clip_state is not None else 0.0
    poses = _eye_poses(sample["yaw"], sample["pitch"], sample["roll"], radius, sample["split"])
    tilt = math.radians(sample["tilt"])
    cos_t, sin_t = math.cos(tilt), math.sin(tilt)
    for index, pose in enumerate(poses):
        if pose["depth"] <= 0.02:
            continue
        side = -1.0 if index == 0 else 1.0
        # Mirrored tilt: anger/smile lean inward, the shared head roll does not.
        local = side * tilt
        cos_l, sin_l = math.cos(local), math.sin(local)
        ax = pose["a"] * cos_l + pose["c"] * sin_l
        ay = pose["b"] * cos_l + pose["d"] * sin_l
        cx = -pose["a"] * sin_l + pose["c"] * cos_l
        cy = -pose["b"] * sin_l + pose["d"] * cos_l
        own = k
        if clip_state is not None:
            own_lid = clip_state.lid_left if index == 0 else clip_state.lid_right
            own = k * _blink_scale(own_lid)
        painter.save()
        painter.setOpacity(painter.opacity() * max(0.0, min(1.0, pose["depth"] / 0.12)) * (1.0 - hidden))
        painter.setTransform(QTransform(ax, ay * own, cx, cy * own,
                                        center_x + pose["x"], center_y + pose["y"]), True)
        _paint_eye(painter, sample["w"] * radius, sample["h"] * radius, sample["shape"], eyes)
        painter.restore()
    if clip_state is not None:
        _paint_decor(painter, clip_state, center_x, center_y, radius, eyes, ink)
    painter.restore()


# Every clip paints the body at this share of the plain face, so idle and the
# occasional expressions are the same size. 0.88 leaves room for the largest
# hop (happy, celebrate) inside the cover or the corner circle.
CLIP_BODY_SCALE = 0.88


def _mix(a: QColor, b: QColor, amount: float) -> QColor:
    amount = max(0.0, min(1.0, float(amount)))
    return QColor(*(round(x + (y - x) * amount) for x, y in zip(a.getRgb()[:3], b.getRgb()[:3])))


def _body_brush(ink: QColor, cx: float, cy: float, radius: float) -> QRadialGradient:
    """A lit ball rather than a flat disc: a soft highlight up-left, a deeper rim.

    The highlight stays a light mix (at most 22% white) so the white eyes
    near the centre keep their contrast.
    """
    focal_x, focal_y = cx - radius * 0.38, cy - radius * 0.46
    gradient = QRadialGradient(focal_x, focal_y, radius * 1.55, focal_x, focal_y)
    gradient.setColorAt(0.0, _mix(ink, QColor(255, 255, 255), 0.22))
    gradient.setColorAt(0.42, _mix(ink, QColor(255, 255, 255), 0.06))
    gradient.setColorAt(0.78, QColor(ink))
    gradient.setColorAt(1.0, _mix(ink, QColor(0, 0, 0), 0.30))
    return gradient


def _star(painter: QPainter, x: float, y: float, size: float) -> None:
    path = QPainterPath()
    path.moveTo(x, y - size)
    path.quadTo(x, y, x + size, y)
    path.quadTo(x, y, x, y + size)
    path.quadTo(x, y, x - size, y)
    path.quadTo(x, y, x, y - size)
    painter.drawPath(path)


def _paint_decor(painter: QPainter, pose, cx: float, cy: float, r: float, light: QColor, ink: QColor) -> None:
    """Small marks drawn inside the body, so the 30 px corner face keeps them."""
    from local.broadcast_face_clips import decor_alpha
    alpha = decor_alpha(pose)
    if alpha <= 0.01 and pose.decor != "bars":
        return
    local = float(pose.extra.get("local", 0.0))
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    white = QColor(light)
    if pose.decor == "dots":
        # Three dots over the raised gaze light up in turn.
        for index in range(3):
            phase = (local * 1.6 - index * 0.22) % 1.0
            level = 0.35 + 0.65 * max(0.0, math.sin(phase * math.pi))
            white.setAlphaF(alpha * level)
            painter.setBrush(white)
            x = cx + r * (0.10 + 0.20 * index)
            y = cy - r * (0.50 + 0.10 * index)
            size = r * (0.055 + 0.012 * index)
            painter.drawEllipse(QRectF(x - size, y - size, size * 2.0, size * 2.0))
    elif pose.decor == "question":
        # A drawn question mark: no font needed, crisp at 30 px.
        white.setAlphaF(alpha * (0.75 + 0.25 * math.sin(local * math.tau / 1.6)))
        bob = math.sin(local * math.tau / 1.6) * r * 0.03
        qx, qy, q = cx + r * 0.04, cy - r * 0.74 + bob, r * 0.10
        hook = QPainterPath()
        hook.moveTo(qx - q, qy - q * 0.6)
        hook.cubicTo(qx - q, qy - q * 1.9, qx + q * 1.1, qy - q * 1.9, qx + q * 1.05, qy - q * 0.5)
        hook.cubicTo(qx + q, qy + q * 0.3, qx, qy + q * 0.2, qx, qy + q * 1.0)
        painter.setPen(QPen(white, max(1.0, q * 0.55), Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(hook)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(white)
        dot = max(0.6, q * 0.34)
        painter.drawEllipse(QRectF(qx - dot, qy + q * 1.75 - dot, dot * 2.0, dot * 2.0))
    elif pose.decor == "notes":
        for index, start in enumerate((0.0, 0.45)):
            phase = ((local / 1.6) + start) % 1.0
            fade = max(0.0, math.sin(phase * math.pi))
            white.setAlphaF(alpha * fade)
            x = cx + r * (-0.42 if index == 0 else 0.36) + math.sin(phase * math.tau) * r * 0.05
            y = cy - r * (0.30 + 0.42 * phase)
            size = r * 0.075
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(white)
            painter.drawEllipse(QRectF(x - size * 1.2, y - size, size * 2.4, size * 2.0))
            pen = QPen(white, max(0.8, r * 0.035), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            stem_x = x + size * 1.1
            painter.drawLine(QPointF(stem_x, y), QPointF(stem_x, y - size * 3.4))
            painter.drawLine(QPointF(stem_x, y - size * 3.4), QPointF(stem_x + size * 1.3, y - size * 2.6))
    elif pose.decor == "sparkles":
        for dx, dy, delay in ((-0.52, -0.42, 0.0), (0.56, -0.30, 0.18), (0.40, 0.48, 0.34)):
            phase = max(0.0, min(1.0, (pose.decor_t - delay * 0.5) * 2.4))
            twinkle = math.sin(phase * math.pi)
            if twinkle <= 0.01:
                continue
            white.setAlphaF(alpha * twinkle)
            painter.setBrush(white)
            _star(painter, cx + dx * r, cy + dy * r, r * 0.11 * (0.6 + 0.4 * twinkle))
    elif pose.decor == "confetti":
        hue = ink.hsvHueF() if ink.hsvHue() >= 0 else 0.6
        for index in range(7):
            seed = index * 0.61803
            x = cx + r * (-0.62 + 1.24 * ((seed * 7.3) % 1.0))
            fall = (pose.decor_t * 1.4 + (seed % 1.0) * 0.4) % 1.0
            y = cy - r * 0.70 + r * 1.2 * fall
            if (x - cx) ** 2 + (y - cy) ** 2 > (r * 0.86) ** 2:
                continue
            color = QColor.fromHsvF((hue + 0.18 * (index % 3 + 1)) % 1.0, 0.45, 1.0)
            color.setAlphaF(alpha * (1.0 - fall * 0.6))
            painter.save()
            painter.translate(x, y)
            painter.rotate(fall * 540.0 + index * 40.0)
            painter.setBrush(color)
            painter.drawRect(QRectF(-r * 0.045, -r * 0.022, r * 0.09, r * 0.044))
            painter.restore()
    elif pose.decor == "bars":
        # The broadcast's waveform takes the eyes' place for a moment.
        level = max(0.0, min(1.0, pose.eyes_hidden))
        if level > 0.01:
            white.setAlphaF(level)
            painter.setBrush(white)
            width = r * 0.13
            for index, offset in enumerate((-0.26, 0.0, 0.26)):
                height = r * (0.22 + 0.30 * (0.5 + 0.5 * math.sin(local * math.tau * 1.7 + index * 1.9)))
                box = QRectF(cx + offset * r - width / 2.0, cy - height / 2.0 - r * 0.05, width, height)
                painter.drawRoundedRect(box, width / 2.0, width / 2.0)
    painter.restore()


def _paint_eye(painter: QPainter, width: float, height: float, shape: str, color: QColor) -> None:
    if shape == "closed":
        # Calm closed eyes: a soft downward curve.
        path = QPainterPath()
        path.moveTo(-width / 2.0, -height * 0.1)
        path.quadTo(0.0, height * 0.55, width / 2.0, -height * 0.1)
        pen = QPen(color, max(1.2, height * 0.5), Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        return
    if shape == "arc":
        path = QPainterPath()
        path.moveTo(-width / 2.0, height * 0.25)
        path.quadTo(0.0, -height * 0.95, width / 2.0, height * 0.25)
        pen = QPen(color, max(1.2, height * 0.72), Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        return
    hw, hh = max(width, 0.01) / 2.0, max(height, 0.01) / 2.0
    radius = min(hw, hh)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(QRectF(-hw, -hh, hw * 2.0, hh * 2.0), radius, radius)


def paint_broadcast_indicator(
    painter: QPainter,
    rect: QRectF,
    radius: float,
    *,
    style: str,
    state: str,
    elapsed: float,
    color: QColor,
    ring_painter,
    bands=None,
    neutral: QColor | None = None,
    backdrop=None,
    max_lightening: float = 0.60,
    slot: float = 1.0,
    previous: str | None = None,
    blend: float = 1.0,
    body_mode: str | None = None,
    clip: str | None = None,
    clip_t: float = 0.0,
    pose=None,
) -> None:
    """Clip to the cover's corner radius and paint one style inside it."""
    if rect.isEmpty() or not broadcast_slot_visible(slot):
        return
    motion = broadcast_motion(state)
    painter.save()
    clip_path = QPainterPath()
    clip_path.addRoundedRect(rect, max(0.0, float(radius)), max(0.0, float(radius)))
    painter.setClipPath(clip_path, Qt.ClipOperation.IntersectClip)
    painter.setOpacity(painter.opacity() * max(0.0, min(1.0, float(slot))))
    if normalize_broadcast_style(style) == BROADCAST_STYLE_FACE:
        paint_broadcast_face(painter, rect, elapsed, state, color,
                             previous=previous, blend=blend, body_mode=body_mode,
                             bands=bands if motion.use_bands else None,
                             clip=clip, clip_t=clip_t, pose=pose)
    elif ring_painter is not None:
        # Rings keep the caller's clock. A finder fade continues that phase;
        # pausing only drops the live frequency bands.
        ring_painter(
            painter, rect, color,
            bands=bands if motion.use_bands else None,
            elapsed=elapsed,
            neutral=neutral,
            max_lightening=max_lightening,
            backdrop=backdrop,
        )
    painter.restore()



def separated_face_color(album: QColor, behind: QColor) -> QColor:
    """The cover's colour for the face, pushed apart from the colour behind it.

    White eyes need a body no lighter than about 0.32 relative luminance. When
    the face would melt into the field (close hue and similar lightness), its
    value moves away from the field until the two read as separate shapes.
    """
    face = QColor(album)
    if not face.isValid():
        return QColor(40, 48, 60)
    hue = face.hsvHue()
    sat = max(0.55, face.hsvSaturationF()) if hue >= 0 else 0.0
    value = face.valueF()
    face.setHsvF(max(0.0, face.hsvHueF()), sat, value)

    def luminance(color):
        return relative_luminance(color)

    def contrast(a, b):
        la, lb = luminance(a), luminance(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    # Keep white eyes readable.
    while luminance(face) > 0.32 and value > 0.2:
        value -= 0.03
        face.setHsvF(max(0.0, face.hsvHueF()), sat, value)
    back = QColor(behind)
    if back.isValid():
        gap = 180.0
        if hue >= 0 and back.hsvHue() >= 0:
            raw = abs(hue - back.hsvHue()) % 360
            gap = min(raw, 360 - raw)
        if gap < 50 or back.hsvSaturationF() < 0.15:
            # Same family as the field: separate by lightness instead.
            darker = luminance(back) > 0.12
            for _ in range(30):
                if contrast(face, back) >= 1.9:
                    break
                value = value - 0.03 if darker else value + 0.03
                value = max(0.08, min(1.0, value))
                face.setHsvF(max(0.0, face.hsvHueF()), sat, value)
                if luminance(face) > 0.32:
                    break
    return face
