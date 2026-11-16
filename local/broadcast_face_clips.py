"""Keyframe clips for broadcast style two (the round face).

Each clip is a few tracks of keyframes. Between two keys the value follows
a cubic bezier ease, the same curve as CSS ``cubic-bezier``, except that y
may leave [0, 1] so a hop can dip before it rises or overshoot as it lands.
That idea (and shuffling idle clips instead of one fixed loop) comes from
studying how character avatars are animated; the motions and numbers here
are our own.

The face painter reads one ``FacePose`` per frame. All functions are pure
functions of time and a seed, so the same moment always paints the same.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# --- easing ---------------------------------------------------------------

LINEAR = (0.0, 0.0, 1.0, 1.0)
EASE = (0.42, 0.0, 0.58, 1.0)
EASE_OUT = (0.22, 1.0, 0.36, 1.0)
EASE_IN = (0.55, 0.0, 0.85, 0.35)
DIP_POP = (0.6, -0.35, 0.3, 1.0)        # sinks a little before it rises
OVERSHOOT = (0.3, 0.0, 0.25, 1.35)      # lands past the target, settles back


def bezier_ease(x: float, curve) -> float:
    """y at x on a cubic bezier from (0,0) to (1,1); y may leave [0, 1]."""
    x1, y1, x2, y2 = curve
    x = max(0.0, min(1.0, float(x)))
    if (x1, y1, x2, y2) == LINEAR:
        return x

    def sample(a, b, s):
        return ((1.0 - 3.0 * b + 3.0 * a) * s + (3.0 * b - 6.0 * a)) * s * s + 3.0 * a * s

    def slope(a, b, s):
        return 3.0 * (1.0 - 3.0 * b + 3.0 * a) * s * s + 2.0 * (3.0 * b - 6.0 * a) * s + 3.0 * a

    s = x
    for _ in range(8):
        error = sample(x1, x2, s) - x
        if abs(error) < 1e-6:
            break
        d = slope(x1, x2, s)
        if abs(d) < 1e-6:
            break
        s -= error / d
    s = max(0.0, min(1.0, s))
    return sample(y1, y2, s)


def track_value(keys, t: float, default: float = 0.0) -> float:
    """``keys`` is ((time, value, ease_into_this_key), ...) sorted by time."""
    if not keys:
        return default
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0, _e0), (t1, v1, e1) in zip(keys, keys[1:]):
        if t <= t1:
            span = max(1e-6, t1 - t0)
            return v0 + (v1 - v0) * bezier_ease((t - t0) / span, e1)
    return keys[-1][1]


# --- pose -----------------------------------------------------------------

@dataclass
class FacePose:
    """Offsets the painter adds to the resting face. Angles are degrees."""
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    hop: float = 0.0            # upward, in body radii
    sway: float = 0.0           # sideways, in body radii
    eye_w: float = 1.0          # multipliers on the expression's eye size
    eye_h: float = 1.0
    lid_left: float = 1.0       # 1 open, 0 shut
    lid_right: float = 1.0
    shape: str | None = None    # None keeps the expression's eye shape
    wander: float = 1.0         # how much of the ambient gaze drift remains
    decor: str = ""             # "", "dots", "notes", "sparkles", "confetti", "bars", "question"
    decor_t: float = 0.0        # 0..1 progress of the decoration
    eyes_hidden: float = 0.0    # 0 shows eyes, 1 replaces them (logo bars)
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Clip:
    name: str
    duration: float
    loop: bool
    tracks: dict
    shape: str | None = None
    shape_window: tuple[float, float] | None = None
    decor: str = ""
    wander: float = 1.0
    eyes_hidden: tuple = ()     # keys for eyes_hidden


def _k(*pairs):
    """Shorthand: _k((t, v), (t, v, ease), ...)."""
    out = []
    for item in pairs:
        if len(item) == 2:
            out.append((item[0], item[1], EASE))
        else:
            out.append(item)
    return tuple(out)


CLIPS: dict[str, Clip] = {}


def _clip(clip: Clip) -> None:
    CLIPS[clip.name] = clip


# Speaking. Working: looks down at its desk and nods along the voice.
_clip(Clip("working", 2.4, True, {
    "pitch": _k((0, 9), (0.3, 13), (0.6, 9), (0.9, 13), (1.2, 9), (1.6, 6), (2.0, 10), (2.4, 9)),
    "yaw": _k((0, 6), (1.2, 10), (1.6, -4), (2.0, 4), (2.4, 6)),
    "eye_h": _k((0, 0.86), (2.4, 0.86)),
}, wander=0.35))

# Coding: reads a line left to right, then the eyes snap back for the next.
_clip(Clip("coding", 3.0, True, {
    "yaw": _k((0, -16), (0.45, -16), (0.7, -4, EASE_OUT), (1.1, -4), (1.35, 9, EASE_OUT), (1.8, 9),
              (2.05, 18, EASE_OUT), (2.55, 18), (2.8, -16, EASE_IN), (3.0, -16)),
    "pitch": _k((0, 4), (2.55, 7), (2.8, 3), (3.0, 4)),
    "roll": _k((0, -2), (1.5, 2), (3.0, -2)),
    "eye_h": _k((0, 0.78), (3.0, 0.78)),
    "eye_w": _k((0, 1.08), (3.0, 1.08)),
}, wander=0.15))

# Happy, once after a broadcast: a crouch, a hop with arc eyes, a soft landing.
_clip(Clip("happy", 1.7, False, {
    "hop": _k((0, 0.0), (0.14, -0.02), (0.42, 0.11, EASE_OUT), (0.66, 0.0, EASE_IN), (0.86, 0.025), (1.1, 0.0)),
    "pitch": _k((0, 4), (0.42, -8), (0.86, 6), (1.7, 4)),
}, shape="arc", shape_window=(0.12, 1.7), wander=0.2))

# Corner idle: the ordinary resting face (ambient drift and blinks).
_clip(Clip("idle", 6.0, True, {}, wander=1.0))

# Turntable: the head turns all the way round; the eyes pass behind.
_clip(Clip("turntable", 4.0, False, {
    "yaw": _k((0, 0), (0.3, -10, EASE), (3.4, 350, (0.45, 0.0, 0.4, 1.0)), (4.0, 360, EASE_OUT)),
    "hop": _k((0, 0), (1.0, 0.025), (2.0, 0), (3.0, 0.025), (4.0, 0)),
}, wander=0.0))

_clip(Clip("celebrate", 2.2, False, {
    "hop": _k((0, 0), (0.12, -0.02), (0.38, 0.11, EASE_OUT), (0.62, 0, EASE_IN), (0.74, -0.015),
              (1.0, 0.10, EASE_OUT), (1.24, 0, EASE_IN), (1.5, 0.02), (1.8, 0)),
    "roll": _k((0, 0), (0.38, -8), (1.0, 8), (1.6, 0)),
}, shape="arc", shape_window=(0.08, 1.9), wander=0.1))

_clip(Clip("approving", 1.5, False, {
    "pitch": _k((0, 0), (0.18, 16, EASE_OUT), (0.38, -2), (0.56, 14, EASE_OUT), (0.8, 0), (1.5, 0)),
}, shape="arc", shape_window=(0.78, 1.45), wander=0.2))

_clip(Clip("humming", 3.6, False, {
    "roll": _k((0, 0), (0.6, 8), (1.4, -8), (2.2, 8), (3.0, -4), (3.6, 0)),
    "sway": _k((0, 0), (0.6, 0.03), (1.4, -0.03), (2.2, 0.03), (3.0, -0.015), (3.6, 0)),
    "pitch": _k((0, 0), (0.5, -6), (3.1, -6), (3.6, 0)),
}, shape="closed", shape_window=(0.25, 3.3), wander=0.0))

_clip(Clip("showcase", 3.0, False, {
    "yaw": _k((0, 0), (0.55, 16, EASE_OUT), (1.25, 16), (1.85, -46, EASE), (2.5, -46), (3.0, 0, EASE_OUT)),
    "pitch": _k((0, 0), (0.55, -4), (2.5, -4), (3.0, 0)),
}, wander=0.0))

_clip(Clip("playful", 2.2, False, {
    "roll": _k((0, 0), (0.3, 14, EASE_OUT), (1.5, 14), (1.9, 0, EASE)),
    "sway": _k((0, 0), (0.3, -0.03), (0.5, 0.03), (0.7, -0.03), (0.9, 0.0), (2.2, 0)),
    "lid_right": _k((0, 1), (0.55, 1), (0.7, 0.0), (1.15, 0.0), (1.3, 1.0), (2.2, 1)),
    "yaw": _k((0, 0), (0.3, 10), (1.5, 10), (1.9, 0)),
}, wander=0.1))

_clip(Clip("excited", 1.9, False, {
    "hop": _k((0, 0), (0.12, 0.07, EASE_OUT), (0.24, 0, EASE_IN), (0.36, 0.07, EASE_OUT), (0.48, 0, EASE_IN),
              (0.6, 0.07, EASE_OUT), (0.72, 0, EASE_IN), (1.0, 0)),
    "eye_h": _k((0, 1), (0.1, 1.22, EASE_OUT), (1.5, 1.22), (1.9, 1)),
    "eye_w": _k((0, 1), (0.1, 1.12, EASE_OUT), (1.5, 1.12), (1.9, 1)),
}, wander=0.3))

_clip(Clip("proud", 2.4, False, {
    "pitch": _k((0, 0), (0.45, -14, EASE_OUT), (1.9, -14), (2.4, 0, EASE)),
    "roll": _k((0, 0), (0.45, -5), (1.9, -5), (2.4, 0)),
}, shape="arc", shape_window=(0.35, 2.0), wander=0.0))

# Loading the next broadcast.
_clip(Clip("thinking", 2.8, True, {
    "yaw": _k((0, -16), (1.4, -22), (2.8, -16)),
    "pitch": _k((0, -16), (1.4, -20), (2.8, -16)),
    "roll": _k((0, 6), (1.4, 9), (2.8, 6)),
}, wander=0.25))

_clip(Clip("suspicious", 3.2, True, {
    "yaw": _k((0, -20), (0.5, -20), (1.3, 20, EASE), (2.1, 20), (2.9, -20, EASE), (3.2, -20)),
    "roll": _k((0, -5), (1.3, 5), (2.9, -5), (3.2, -5)),
    "eye_h": _k((0, 0.46), (3.2, 0.46)),
    "eye_w": _k((0, 1.2), (3.2, 1.2)),
    "sway": _k((0, -0.02), (1.3, 0.02), (2.9, -0.02), (3.2, -0.02)),
}, wander=0.0))


SPEAKING_CLIPS = ("working", "coding")
FINISH_CLIP = "happy"
CORNER_BASE_CLIPS = ("idle", "turntable")
# Expressions and motion only: no dots, question marks, notes or sparkles.
CORNER_SPECIAL_CLIPS = ("celebrate", "approving", "humming", "showcase",
                        "turntable", "playful", "excited", "proud")
LOADING_CLIPS = ("suspicious", "thinking")
SPECIAL_EVERY_SECONDS = 15.0


def _rand(seed: int) -> float:
    """A small stable hash to [0, 1)."""
    value = (int(seed) * 0x9E3779B1 + 0x7F4A7C15) & 0xFFFFFFFF
    value ^= value >> 15
    value = (value * 0x2C1B3C6D) & 0xFFFFFFFF
    value ^= value >> 12
    value = (value * 0x297A2D39) & 0xFFFFFFFF
    value ^= value >> 15
    return (value & 0xFFFFFF) / float(0x1000000)


def pick(options, seed: int) -> str:
    return options[min(len(options) - 1, int(_rand(seed) * len(options)))]


def speaking_clip(seed: int) -> str:
    """One of the two speaking clips, fixed for one broadcast."""
    return pick(SPEAKING_CLIPS, seed)


def loading_clip(seed: int) -> str:
    return pick(LOADING_CLIPS, seed)


def corner_clip(t: float, seed: int = 0) -> tuple[str, float]:
    """(clip, time inside it) for the resting corner button at clock ``t``.

    Every 15 s one special clip plays once from the start of its window.
    The rest of the window shuffles the two base clips (idle, turntable),
    idle being the likelier, each starting from its own beginning.
    """
    t = max(0.0, float(t))
    window = int(t // SPECIAL_EVERY_SECONDS)
    inside = t - window * SPECIAL_EVERY_SECONDS
    if window > 0:
        special = pick(CORNER_SPECIAL_CLIPS, seed * 7919 + window)
        length = CLIPS[special].duration
        if inside < length:
            return special, inside
        inside -= length
    else:
        length = 0.0
    slot = 0
    while True:
        base = "turntable" if _rand(seed * 104729 + window * 97 + slot) < 0.22 else "idle"
        if window == 0 and slot == 0:
            base = "idle"
        duration = CLIPS[base].duration
        if inside < duration:
            return base, inside
        inside -= duration
        slot += 1


def clip_pose(name: str, t: float) -> FacePose:
    """The pose of clip ``name`` at ``t`` seconds into it."""
    clip = CLIPS.get(name) or CLIPS["idle"]
    local = float(t)
    if clip.loop and clip.duration > 0:
        local = local % clip.duration
    else:
        local = max(0.0, min(clip.duration, local))
    pose = FacePose(wander=clip.wander)
    for key, keys in clip.tracks.items():
        default = 1.0 if key in {"eye_w", "eye_h", "lid_left", "lid_right"} else 0.0
        setattr(pose, key, track_value(keys, local, default))
    if clip.shape and clip.shape_window and clip.shape_window[0] <= local <= clip.shape_window[1]:
        pose.shape = clip.shape
    if clip.shape and clip.shape_window:
        # The eye shape changes behind a quick blink, never in one frame.
        lid = _shape_switch_lid(local, clip)
        pose.lid_left *= lid
        pose.lid_right *= lid
    if clip.eyes_hidden:
        pose.eyes_hidden = max(0.0, min(1.0, track_value(clip.eyes_hidden, local, 0.0)))
    if clip.decor:
        pose.decor = clip.decor
        pose.decor_t = local / clip.duration if clip.duration else 0.0
        if clip.loop:
            pose.decor_t = local / clip.duration
    pose.extra["clip"] = clip.name
    pose.extra["local"] = local
    return pose


# --- blending between clips ------------------------------------------------

BLEND_SECONDS = 0.35
_NUMERIC = ("pitch", "roll", "hop", "sway", "eye_w", "eye_h", "lid_left", "lid_right",
            "wander", "eyes_hidden")


def mix_poses(a: FacePose, b: FacePose, amount: float) -> FacePose:
    """``a`` at 0, ``b`` at 1. Yaw takes the short way round (360 is 0)."""
    k = max(0.0, min(1.0, float(amount)))
    pose = FacePose()
    for key in _NUMERIC:
        x, y = getattr(a, key), getattr(b, key)
        setattr(pose, key, x + (y - x) * k)
    turn = ((b.yaw - a.yaw + 180.0) % 360.0) - 180.0
    pose.yaw = a.yaw + turn * k
    pose.shape = b.shape if k >= 0.5 else a.shape
    if a.shape != b.shape:
        # Blend into a different eye shape through a blink: shut at the swap.
        shut = abs(2.0 * k - 1.0)
        pose.lid_left *= shut
        pose.lid_right *= shut
    pose.decor, pose.decor_t = b.decor, b.decor_t
    pose.extra = dict(b.extra)
    return pose


class ClipBlender:
    """Eases from the pose on screen to a new clip instead of cutting.

    A new clip, or a one-shot clip starting over, used to jump straight to
    its first key: the head snapped, the gaze drift switched on or off and
    the bot twitched. The pose on screen at the switch fades into the new
    clip over ``BLEND_SECONDS``.
    """

    def __init__(self) -> None:
        self.name: str | None = None
        self.local = 0.0
        self.switched_at = -1e9
        self.start: FacePose | None = None
        self.shown: FacePose | None = None

    def pose(self, name: str, t: float, now: float) -> FacePose:
        target = clip_pose(name, t)
        local = float(target.extra.get("local", 0.0))
        restarted = name == self.name and local + 0.05 < self.local and not CLIPS[target.extra["clip"]].loop
        if name != self.name or restarted:
            self.start = self.shown
            self.switched_at = float(now)
            self.name = name
        self.local = local
        amount = (float(now) - self.switched_at) / BLEND_SECONDS
        if self.start is not None and amount < 1.0:
            target = mix_poses(self.start, target, bezier_ease(amount, EASE))
        else:
            self.start = None
        self.shown = target
        return target


SHAPE_BLINK_S = 0.09


def _shape_switch_lid(local: float, clip: Clip) -> float:
    """1 open, dipping to 0 exactly where the clip swaps the eye shape."""
    lid = 1.0
    for edge in clip.shape_window:
        if 0.0 < edge < clip.duration:
            lid = min(lid, abs(local - edge) / SHAPE_BLINK_S)
    return max(0.0, min(1.0, lid))


def finish_seconds() -> float:
    return CLIPS[FINISH_CLIP].duration


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def decor_alpha(pose: FacePose) -> float:
    """Decorations fade in over the first 12% and out over the last 15%."""
    if not pose.decor:
        return 0.0
    clip = CLIPS.get(pose.extra.get("clip", ""))
    if clip is not None and clip.loop:
        return 1.0
    p = pose.decor_t
    return _clamp(p / 0.12) * _clamp((1.0 - p) / 0.15)


def wave(t: float, period: float, phase: float = 0.0) -> float:
    return math.sin((t / period + phase) * math.tau)
