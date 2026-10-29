"""Clear glass and press motion for the song page only.

Geometry is independent of scene pixels, tint, translation and press growth.
Only bounded crops of the owner's existing background image are sampled; no
screen capture, new timers, provider access or external reference imports.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
import math
import sys

import numpy as np
from PIL import Image
from PIL.ImageFilter import GaussianBlur
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QRadialGradient, QTransform

from local import glass_style
from local.control_hover import HoverMotion, advance_neighbor_spring, neighbor_displacement
from local.glass_style import (CONTROL_COMPOSITE_DPR, DJ_TINT, GLASS_CLEAR, GLASS_DJ,
                               GLASS_GLYPH, GLASS_SELECTED_ALPHA, PRESS_DAMPING,
                               PRESS_GROWTH, PRESS_HOLD_SECONDS, PRESS_STIFFNESS)
from local.liquid_glass import (GLASS_CAPSULE_MATERIAL_MAX_EDGE,
                                GLASS_MATERIAL_FRAME_SECONDS, GLASS_MATERIAL_MAX_EDGE,
                                ChromeBackdropTint)


def paint_control_overlay(painter, bounds, draw):
    """Render unchanged foreground glyphs and control faces at >=2x."""
    dpr = max(2., min(3., painter.device().devicePixelRatioF()))
    bounds = QRectF(bounds)
    image = QImage(max(1, math.ceil(bounds.width() * dpr)), max(1, math.ceil(bounds.height() * dpr)),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    image.fill(Qt.GlobalColor.transparent)
    with QPainter(image) as p:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.translate(-bounds.topLeft())
        draw(p)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(bounds.topLeft(), image)
    painter.restore()


def control_key(key):
    return "collection" if key in {"favorite", "playlist_add"} else key


@dataclass
class ControlMotion:
    press_motion: HoverMotion = field(default_factory=HoverMotion)
    started: float = -math.inf
    held: bool = False
    lift: float = 0.
    speed: float = 0.
    glow: float = 0.
    offset: QPointF = field(default_factory=QPointF)
    velocity: QPointF = field(default_factory=QPointF)
    contact: QPointF = field(default_factory=QPointF)

    def active(self, now):
        return (self.held or now < self.started + PRESS_HOLD_SECONDS or
                self.press_motion.is_animating(now) or abs(self.lift) > .0001 or
                abs(self.speed) > .001 or self.glow > .001 or
                abs(self.offset.x()) + abs(self.offset.y()) > .001 or
                abs(self.velocity.x()) + abs(self.velocity.y()) > .01)


class ControlFeedback:
    """Timer-driven state; painting and hit testing never advance springs."""
    def __init__(self):
        self.controls: dict[str, ControlMotion] = {}
        self.hover: dict[str, float] = {}
        self.last_tick = None

    def reset(self):
        self.controls.clear()
        self.hover.clear()
        self.last_tick = None

    def state(self, key):
        return self.controls.get(control_key(key))

    def press(self, key, point, now):
        self.release()
        state = self.controls.setdefault(control_key(key), ControlMotion())
        state.held, state.started, state.contact = True, now, QPointF(point)
        state.press_motion.set_hovered(True, now)
        if self.last_tick is None:
            self.last_tick = now

    def release(self):
        for state in self.controls.values():
            state.held = False

    def move(self, point):
        for state in self.controls.values():
            delta = point - state.contact
            if state.held and delta.x() * delta.x() + delta.y() * delta.y() >= 9.:
                state.contact = QPointF(point)

    def advance(self, groups, hovered, now):
        elapsed = max(0., now - self.last_tick) if self.last_tick is not None else 0.
        self.last_tick = now
        # Time-bounded integration also handles a slow frame or suspend/resume.
        dt = min(.08, elapsed)
        changed = False
        for key in set(self.hover) | ({hovered} if hovered else set()):
            old = self.hover.get(key, 0.)
            goal = float(key == hovered)
            value = old + (goal - old) * min(1., dt * 12.)
            if abs(value - goal) < .0001:
                value = goal
            changed |= value != old
            self.hover[key] = value
        for group in groups:
            for key in group:
                self.controls.setdefault(key, ControlMotion())
        for key, state in self.controls.items():
            # Use the actual .3s boundary even when a tick straddles it.
            holding = state.held or now < state.started + PRESS_HOLD_SECONDS
            release_at = now if state.held else min(now, state.started + PRESS_HOLD_SECONDS)
            state.press_motion.set_hovered(holding, release_at)
            changed |= state.active(now)
            steps = max(1, math.ceil(dt / .008))
            for _ in range(steps):
                step = dt / steps
                state.speed += (PRESS_STIFFNESS * (float(state.held) - state.lift) - PRESS_DAMPING * state.speed) * step
                state.lift += state.speed * step
            if not state.held and abs(state.lift) < .0001 and abs(state.speed) < .001:
                state.lift = state.speed = 0.
            state.glow = (state.glow + (1. - state.glow) * min(1., dt * 14.) if state.held
                          else max(0., state.glow - dt * 3.2))
        for group in groups:
            for key, rect in group.items():
                state = self.controls[key]
                target = QPointF()
                for other, active_rect in group.items():
                    if other != key:
                        target += neighbor_displacement(rect, active_rect, self.controls[other].press_motion.value(now))
                state.offset, state.velocity = advance_neighbor_spring(state.offset, state.velocity, target, dt)
        return changed

    def pose_key(self, keys=None):
        return (tuple((key, s.lift, s.glow, s.offset.x(), s.offset.y(),
                       s.contact.x(), s.contact.y())
                      for key, s in self.controls.items()
                      if (keys is None or key in keys) and (s.lift or s.glow or not s.offset.isNull())),
                tuple((key, value) for key, value in self.hover.items()
                      if (keys is None or key in keys) and value))


def _material_maps(dist, nx, ny, xx, yy, dpr, spec):
    depth = np.maximum(-dist, 0.)
    x = np.clip(depth / max(1., spec.bezel * dpr), 0., 1.)
    mag = spec.lens * dpr * (1. - x) ** 2.2
    samples = tuple((xx + nx * mag * k, yy + ny * mag * k)
                    for k in (1. - spec.chroma, 1., 1. + spec.chroma))
    facing = nx * -.5 + ny * -.866
    rim = np.clip(1. - depth / (1.4 * dpr), 0., 1.) ** 1.5 * (.28 + .72 * np.abs(facing) ** 1.6)
    glow = (1. - x) ** 3 * (.18 + .55 * np.maximum(facing, 0.) + .25 * np.maximum(-facing, 0.))
    sheen = np.clip(1. - yy / dist.shape[0], 0., 1.) ** 1.6
    light = np.clip(spec.rim * rim + spec.glow * glow * .45 + spec.sheen * sheen, 0., 1.)
    return samples, light[..., None], np.clip(.5 - dist, 0., 1.)


def _path_signature(path):
    return (int(path.fillRule().value), tuple((e.type.value, round(e.x, 6), round(e.y, 6))
                                            for e in (path.elementAt(i) for i in range(path.elementCount()))))


def fused_control_path(rects, fusion):
    """Union round members with smooth, growing metaball necks.

    At zero only the original circles remain. At one, aligned equal-height
    slots form a capsule. Intermediate necks use tangential cubic curves,
    rather than a rounded bounding rectangle or overlapping glass layers.
    """
    circles = [(QRectF(rect).center(), min(rect.width(), rect.height()) / 2.)
               for rect in rects if not rect.isEmpty()]
    path = QPainterPath()
    for center, radius in circles:
        circle = QPainterPath()
        circle.addEllipse(center, radius, radius)
        path = path.united(circle)
    amount = min(1., max(0., float(fusion)))
    if amount <= 0.:
        return path
    for (first, r1), (second, r2) in zip(circles, circles[1:]):
        dx, dy = second.x() - first.x(), second.y() - first.y()
        distance = math.hypot(dx, dy)
        if distance <= abs(r1 - r2) or min(r1, r2) <= 0.:
            continue
        direction = math.atan2(dy, dx)
        tangent = math.acos(min(1., max(-1., (r1 - r2) / distance)))
        if distance < r1 + r2:
            u1 = math.acos(min(1., max(-1., (r1*r1 + distance*distance - r2*r2) / (2.*r1*distance))))
            u2 = math.acos(min(1., max(-1., (r2*r2 + distance*distance - r1*r1) / (2.*r2*distance))))
        else:
            u1 = u2 = 0.
        angles = (direction + u1 + (tangent - u1) * amount,
                  direction - u1 - (tangent - u1) * amount,
                  direction + math.pi - u2 - (math.pi - u2 - tangent) * amount,
                  direction - math.pi + u2 + (math.pi - u2 - tangent) * amount)
        def point(center, radius, angle):
            return center + QPointF(math.cos(angle) * radius, math.sin(angle) * radius)
        p1a, p1b = (point(first, r1, angle) for angle in angles[:2])
        p2a, p2b = (point(second, r2, angle) for angle in angles[2:])
        span = math.hypot(p2a.x() - p1a.x(), p2a.y() - p1a.y())
        handle = min(amount * 2.4, span / (r1 + r2)) * min(1., 2.*distance / (r1 + r2))
        bridge = QPainterPath(p1a)
        bridge.cubicTo(point(p1a, r1 * handle, angles[0] - math.pi / 2.),
                       point(p2a, r2 * handle, angles[2] + math.pi / 2.), p2a)
        bridge.lineTo(p2b)
        bridge.cubicTo(point(p2b, r2 * handle, angles[3] - math.pi / 2.),
                       point(p1b, r1 * handle, angles[1] + math.pi / 2.), p1b)
        bridge.closeSubpath()
        path = path.united(bridge)
    return path


def _restore_path(signature):
    path = QPainterPath()
    path.setFillRule(Qt.FillRule(signature[0]))
    elements = signature[1]
    i = 0
    while i < len(elements):
        kind, x, y = elements[i]
        if kind == 0:
            path.moveTo(x, y)
        elif kind == 1:
            path.lineTo(x, y)
        elif kind == 2:
            _, x2, y2 = elements[i + 1]
            _, x3, y3 = elements[i + 2]
            path.cubicTo(x, y, x2, y2, x3, y3)
            i += 2
        i += 1
    return path


@lru_cache(maxsize=64)
def control_geometry(w, h, radius, dpr, spec, signature=None):
    """One rest-size distance field, including the original transport path."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32) + .5
    if signature is None:
        dx, dy = xx - w / 2., yy - h / 2.
        r = min(radius * dpr, w / 2., h / 2.)
        qx, qy = np.abs(dx) - (w / 2. - r), np.abs(dy) - (h / 2. - r)
        mx, my = np.maximum(qx, 0.), np.maximum(qy, 0.)
        length = np.hypot(mx, my)
        dist = length + np.minimum(np.maximum(qx, qy), 0.) - r
        safe = np.maximum(length, 1e-4)
        nx = np.where(length > 1e-4, mx / safe, (qx >= qy)) * np.sign(dx)
        ny = np.where(length > 1e-4, my / safe, (qy > qx)) * np.sign(dy)
    else:
        path = _restore_path(signature)
        # Exact distance to flattened line segments avoids the prototype's
        # pixel x dense-boundary-point allocation and preserves disjoint bars.
        nearest = np.full((h, w), np.inf, np.float32)
        # Fused chrome has hundreds of boundary segments. Compare squared
        # distances and take one root per pixel instead of one per segment;
        # retain the existing transport material's distance calculation.
        squared = spec == GLASS_CLEAR
        for polygon in path.toSubpathPolygons(QTransform().scale(dpr, dpr)):
            points = list(polygon)
            for a, b in zip(points, points[1:] + points[:1]):
                vx, vy = b.x() - a.x(), b.y() - a.y()
                t = np.clip(((xx - a.x()) * vx + (yy - a.y()) * vy) / max(1e-10, vx * vx + vy * vy), 0., 1.)
                dx, dy = xx - a.x() - t * vx, yy - a.y() - t * vy
                nearest = np.minimum(nearest, dx * dx + dy * dy if squared else np.hypot(dx, dy))
        if squared:
            np.sqrt(nearest, out=nearest)
        mask = QImage(w, h, QImage.Format.Format_Grayscale8)
        mask.fill(0)
        with QPainter(mask) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.scale(dpr, dpr)
            p.fillPath(path, QColor("white"))
        cover = np.frombuffer(mask.constBits(), np.uint8).reshape(h, mask.bytesPerLine())[:, :w]
        dist = np.where(cover >= 128, -nearest, nearest)
        ny, nx = np.gradient(dist)
        norm = np.maximum(np.hypot(nx, ny), 1e-4)
        nx, ny = nx / norm, ny / norm
    return _material_maps(dist, nx, ny, xx, yy, dpr, spec)


def _sample(channel, x, y):
    x, y = np.clip(x, 0., channel.shape[1] - 1.001), np.clip(y, 0., channel.shape[0] - 1.001)
    ix, iy = x.astype(np.int32), y.astype(np.int32)
    fx, fy = x - ix, y - iy
    top = channel[iy, ix] * (1. - fx) + channel[iy, ix + 1] * fx
    bottom = channel[iy + 1, ix] * (1. - fx) + channel[iy + 1, ix + 1] * fx
    return top * (1. - fy) + bottom * fy


@lru_cache(maxsize=32)
def _sampling_maps(geometry_key, pad, sw, sh):
    """Cache the scene-independent bilinear indices and weights."""
    samples, _, alpha = control_geometry(*geometry_key)
    h, w = alpha.shape
    full_w, full_h = w + 2 * pad, h + 2 * pad
    maps = []
    for x, y in samples:
        x = np.clip((x + pad) * sw / full_w - .5, 0., sw - 1.001)
        y = np.clip((y + pad) * sh / full_h - .5, 0., sh - 1.001)
        ix, iy = x.astype(np.int32), y.astype(np.int32)
        maps.append((iy * sw + ix, sw, x - ix, y - iy))
    return tuple(maps)


@lru_cache(maxsize=32)
def _sampling_lookup(geometry_key, pad, sw, sh):
    """Interleaved RGB corner indices and bilinear weights for one take."""
    mappings = _sampling_maps(geometry_key, pad, sw, sh)
    indices, weights = [], []
    for channel, (index, width, fx, fy) in enumerate(mappings):
        fx, fy = fx.astype(np.float32), fy.astype(np.float32)
        index = index * 3 + channel
        indices.extend((index, index + 3, index + width * 3, index + width * 3 + 3))
        weights.extend(((1. - fx) * (1. - fy), fx * (1. - fy),
                        (1. - fx) * fy, fx * fy))
    return np.stack(indices), np.stack(weights)


def _sample_cached(channel, mapping):
    index, width, fx, fy = mapping
    flat = np.ascontiguousarray(channel).reshape(-1)
    top = flat[index] * (1. - fx) + flat[index + 1] * fx
    bottom = flat[index + width] * (1. - fx) + flat[index + width + 1] * fx
    return top * (1. - fy) + bottom * fy


def _shade(scene, scene_bounds, rect, dpr, spec, geometry_key, tint,
           material_max_edge=GLASS_MATERIAL_MAX_EDGE, mask=True):
    geometry = control_geometry(*geometry_key)
    _, light, alpha = geometry
    h, w = alpha.shape
    pad = math.ceil((spec.lens * (1. + spec.chroma) + spec.blur * 3.) * dpr + 3.)
    full_w, full_h = w + 2 * pad, h + 2 * pad
    ratio = min(1., material_max_edge / max(full_w, full_h))
    sw, sh = max(2, round(full_w * ratio)), max(2, round(full_h * ratio))
    area = rect.adjusted(-pad / dpr, -pad / dpr, pad / dpr, pad / dpr)
    crop = QImage(sw, sh, QImage.Format.Format_RGB32)
    crop.fill(QColor(112, 112, 112))
    if not scene.isNull() and scene_bounds.contains(area):
        source = QRectF((area.x() - scene_bounds.x()) * scene.width() / scene_bounds.width(),
                        (area.y() - scene_bounds.y()) * scene.height() / scene_bounds.height(),
                        area.width() * scene.width() / scene_bounds.width(),
                        area.height() * scene.height() / scene_bounds.height())
        with QPainter(crop) as p:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(QRectF(0, 0, sw, sh), scene, source)
        # RGB32 has native BGRA bytes on little-endian Qt platforms. Pillow
        # converts directly while copying, avoiding a second QImage raster.
        raw_format = "BGRX" if sys.byteorder == "little" else "XRGB"
        pixels = Image.frombuffer("RGB", (sw, sh), crop.constBits(), "raw", raw_format, 0, 1)
    elif not scene.isNull():
        # Clamp with edge replication, including controls near window edges.
        sx = (area.x() - scene_bounds.x()) * scene.width() / scene_bounds.width()
        sy = (area.y() - scene_bounds.y()) * scene.height() / scene_bounds.height()
        rgba = scene.convertToFormat(QImage.Format.Format_RGBA8888)
        source = np.frombuffer(rgba.constBits(), np.uint8).reshape(rgba.height(), rgba.bytesPerLine())[:, :rgba.width() * 4].reshape(rgba.height(), rgba.width(), 4)
        if min(source.shape[:2]) < 2:
            source = np.pad(source, ((0, int(source.shape[0] < 2)), (0, int(source.shape[1] < 2)), (0, 0)), mode="edge")
        xs = sx + (np.arange(sw, dtype=np.float32) + .5) / sw * area.width() * scene.width() / scene_bounds.width() - .5
        ys = sy + (np.arange(sh, dtype=np.float32) + .5) / sh * area.height() * scene.height() / scene_bounds.height() - .5
        xx, yy = np.meshgrid(xs, ys)
        pixels = np.stack([_sample(source[..., c].astype(np.float32), xx, yy) for c in range(3)], axis=-1).astype(np.uint8)
    else:
        pixels = np.full((sh, sw, 3), 112, np.uint8)
    if not isinstance(pixels, Image.Image):
        pixels = Image.fromarray(pixels)
    pixels = np.asarray(pixels.filter(GaussianBlur(spec.blur * dpr * ratio)), dtype=np.float32)
    flat = pixels.reshape(-1)
    indices, weights = _sampling_lookup(geometry_key, pad, sw, sh)
    sampled = np.take(flat, indices)
    np.multiply(sampled, weights, out=sampled)
    rgb = np.empty((h, w, 3), np.float32)
    for c in range(3):
        np.add(sampled[4*c], sampled[4*c+1], out=rgb[..., c])
        np.add(rgb[..., c], sampled[4*c+2], out=rgb[..., c])
        np.add(rgb[..., c], sampled[4*c+3], out=rgb[..., c])
    lum = rgb @ np.array([.2126, .7152, .0722], np.float32)
    np.subtract(rgb, lum[..., None], out=rgb)
    np.multiply(rgb, spec.sat, out=rgb)
    np.add(rgb, lum[..., None], out=rgb)
    if spec.veil:
        dark = float(lum.mean()) / 255. < .55
        amount = spec.veil * (1. if dark else 1.6)
        np.multiply(rgb, 1. - amount, out=rgb)
        np.add(rgb, (255. if dark else 0.) * amount, out=rgb)
    if spec.tint_alpha:
        np.multiply(rgb, 1. - spec.tint_alpha, out=rgb)
        np.add(rgb, np.array(tint, np.float32) * spec.tint_alpha, out=rgb)
    np.clip(rgb, 0., 255., out=rgb)
    rgb += (255. - rgb) * light
    rgba = np.empty((h, w, 4), np.uint8)
    rgba[..., :3], rgba[..., 3] = rgb, alpha * 255. if mask else 255
    return QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()


class GlassControlRenderer:
    """Bounded per-surface shaded patches; scene refresh is limited to 10 Hz."""
    def __init__(self):
        self.patches = OrderedDict()
        self.bytes = 0
        self.refresh_roles = None
        self.seen_roles = set()
        self.frame_roles = None
        self.versions = {}
        self.record_roles = None
        self.backdrop_tint = ChromeBackdropTint()
        self.live_tint_scene = None

    def begin_frame(self, now):
        # Reserve refreshes for the oldest eligible patches, independent of paint order.
        eligible = ((entry[2], role) for role, entry in self.patches.items()
                    if role in self.seen_roles and now - entry[2] >= GLASS_MATERIAL_FRAME_SECONDS)
        self.refresh_roles = {role for _, role in sorted(eligible, key=lambda item: item[0])[:2]}
        self.frame_roles = set()

    def end_frame(self):
        self.seen_roles = self.frame_roles or set()
        self.frame_roles = None
        self.refresh_roles = None

    def clear(self):
        self.patches.clear()
        self.bytes = 0
        self.refresh_roles = None
        self.seen_roles.clear()
        self.frame_roles = None
        self.versions.clear()
        self.backdrop_tint.clear()
        self.live_tint_scene = None

    def would_refresh(self, role, scene, now, pose, state):
        previous = self.patches.get(role)
        moving = state is not None and state.active(now)
        # Capsule/morph geometry changes share the scene's 100 ms budget.
        # A material-level change must still replace every face atomically.
        bounded = len(pose) > 8 and pose[8]
        interval = (GLASS_MATERIAL_FRAME_SECONDS if bounded and not moving else
                    glass_style.GLASS_MOVING_REFRESH_SECONDS if moving else 0.)
        material_changed = previous is not None and previous[0][3] != pose[3]
        # Exact circular/shared endpoints cannot stretch the previous neck's
        # baked rim into the capsule, even inside the shape refresh interval.
        path_changed = (previous is not None and
                        (previous[0][0][5] is None) != (pose[0][5] is None))
        throttled = (interval > 0. and previous is not None and not material_changed and not path_changed and
                     0. <= now - previous[2] < interval)
        return not throttled and (previous is None or previous[0] != pose or
            (previous[1] != scene.cacheKey() and (moving or
             (now - previous[2] >= GLASS_MATERIAL_FRAME_SECONDS and
              (self.refresh_roles is None or role in self.refresh_roles)))))

    def touch(self, role, scene, now, state):
        if self.frame_roles is not None:
            self.frame_roles.add(role)
        previous = self.patches.get(role)
        moving = state is not None and state.active(now)
        interval = glass_style.GLASS_MOVING_REFRESH_SECONDS
        throttled = (moving and interval > 0. and previous is not None and
                     0. <= now - previous[2] < interval)
        if (previous is not None and not throttled and self.refresh_roles is not None
                and role in self.refresh_roles and previous[1] == scene.cacheKey()):
            self.patches[role] = (previous[0], previous[1], now, previous[3])

    def paint(self, painter, rect, *, scene, scene_bounds, ink, now, role,
              state=None, hover=0., path=None, kind="circle", level=50,
              selected=False, hover_clip=None, hover_segments=None, fusing=False):
        if self.frame_roles is not None:
            self.frame_roles.add(role)
        # Chrome paths retain the circular controls' Clear material; original
        # transport silhouettes continue to use their existing glyph token.
        base_spec = GLASS_CLEAR if fusing else GLASS_GLYPH if kind == "path" else GLASS_DJ if kind == "dj" else GLASS_CLEAR
        spec = base_spec.at_level(level)
        if fusing:
            state = None
        dpr = max(CONTROL_COMPOSITE_DPR, min(3., painter.device().devicePixelRatioF()))
        rect = QRectF(rect)
        rect.moveCenter(QPointF(round(rect.center().x() * dpr) / dpr, round(rect.center().y() * dpr) / dpr))
        signature = None
        if path is not None:
            # Translation is excluded from the cache key, including fractional
            # origins caused by neighbor springs or layout changes.
            box = path.boundingRect()
            local = path.translated(2. - box.left(), 2. - box.top())
            signature = _path_signature(local)
            rect = QRectF(rect.center().x() - (box.width() + 4.) / 2.,
                          rect.center().y() - (box.height() + 4.) / 2., box.width() + 4., box.height() + 4.)
        w, h = max(2, round(rect.width() * dpr)), max(2, round(rect.height() * dpr))
        # Blur/veil and tint cannot affect the geometry cache.
        geometry_key = (w, h, round(min(rect.width(), rect.height()) / 2., 6), dpr, base_spec, signature)
        tint = DJ_TINT if kind == "dj" else (ink.red(), ink.green(), ink.blue())
        material_max_edge = GLASS_CAPSULE_MATERIAL_MAX_EDGE if kind in {"capsule", "path"} else GLASS_MATERIAL_MAX_EDGE
        pose = (geometry_key, round(rect.x() * dpr), round(rect.y() * dpr), level, tint,
                scene_bounds.width(), scene_bounds.height(), material_max_edge,
                kind == "capsule" or fusing)
        previous = self.patches.get(role)
        if self.record_roles is not None:
            self.record_roles.append((role, pose, state))
        if self.would_refresh(role, scene, now, pose, state):
            image = _shade(scene, scene_bounds, rect, dpr, spec, geometry_key, tint,
                           material_max_edge, mask=not (kind == "capsule" or fusing))
            if previous is not None:
                self.bytes -= previous[3].sizeInBytes()
            self.patches[role] = (pose, scene.cacheKey(), now, image)
            self.versions[role] = self.versions.get(role, 0) + 1
            self.bytes += image.sizeInBytes()
            self.patches.move_to_end(role)
            while len(self.patches) > 32 or self.bytes > 8 * 1024 * 1024:
                self.bytes -= self.patches.popitem(last=False)[1][3].sizeInBytes()
        else:
            image = previous[3]
            self.touch(role, scene, now, state)
        lift = state.lift if state is not None else 0.
        grown = QRectF(rect)
        if kind == "capsule" or kind == "dj" and rect.width() > rect.height() * 1.2:
            grown.adjust(-4. * lift, -2.5 * lift, 4. * lift, 2.5 * lift)
        else:
            gx, gy = rect.width() * PRESS_GROWTH * lift / 2., rect.height() * PRESS_GROWTH * lift / 2.
            grown.adjust(-gx, -gy, gx, gy)
        shape = QPainterPath()
        if path is not None:
            shape = path.translated(rect.center() - path.boundingRect().center())
            transform = QTransform().translate(rect.center().x(), rect.center().y()).scale(
                grown.width() / rect.width(), grown.height() / rect.height()).translate(-rect.center().x(), -rect.center().y())
            shape = transform.map(shape)
        else:
            shape.addRoundedRect(grown, grown.height() / 2., grown.height() / 2.)
        adaptive = None
        if kind in {'circle', 'capsule'} or fusing:
            adaptive = self.backdrop_tint.sample(
                self.live_tint_scene if self.live_tint_scene is not None else scene,
                scene_bounds, rect, now, role=role)
        def paint_tint(p):
            if adaptive is not None:
                p.fillPath(shape, adaptive[1])
                p.setPen(QPen(adaptive[2], 1.))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(shape)
        overlays = (selected or hover > .001 or
                    any(amount > .001 for amount, _rect in (hover_segments or ())) or
                    state is not None and (state.glow > .001 or abs(lift) > .001))
        if not overlays:
            painter.save()
            if kind == "capsule" or fusing:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setClipPath(shape, Qt.ClipOperation.IntersectClip)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(grown, image)
            paint_tint(painter)
            painter.restore()
            return
        # Composite every edge and feedback layer at >=2x, even when callers
        # draw controls directly into a DPR 1 QImage.
        bounds = grown.adjusted(-1., -1., 1., 1.)
        def compose(p):
            p.save()
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            if kind == "capsule" or fusing:
                p.setClipPath(shape, Qt.ClipOperation.IntersectClip)
            p.drawImage(grown, image)
            paint_tint(p)
            p.setClipPath(shape)
            if selected:
                p.fillRect(grown, QColor(255, 255, 255, round(255 * GLASS_SELECTED_ALPHA)))
            for amount, segment in (hover_segments if hover_segments is not None else ((hover, hover_clip),)):
                if amount <= .001:
                    continue
                p.setClipPath(shape)
                if segment is not None:
                    p.setClipRect(segment, Qt.ClipOperation.IntersectClip)
                p.fillRect(grown, QColor(255, 255, 255, round((40 if ink.lightness() < 128 else 26) * amount)))
            p.setClipPath(shape)
            if state is not None and (state.glow > .001 or abs(lift) > .001):
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                p.fillRect(grown, QColor(255, 255, 255, round(14 * min(1., max(0., lift)))))
                short = min(grown.width(), grown.height())
                contact = state.contact + state.offset
                spot = QRadialGradient(contact, short * 1.3 * (1.2 - .2 * state.glow))
                spot.setColorAt(0., QColor(255, 255, 255, round(52 * state.glow)))
                spot.setColorAt(1., QColor(255, 255, 255, 0))
                p.fillRect(grown, spot)
                if kind != "path":
                    edge = QRadialGradient(contact, short * 1.4)
                    edge.setColorAt(0., QColor(255, 255, 255, round(170 * state.glow)))
                    edge.setColorAt(1., QColor(255, 255, 255, 0))
                    p.setPen(QPen(edge, 1.3))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(shape)
            p.restore()
        if painter.device().devicePixelRatioF() >= CONTROL_COMPOSITE_DPR:
            compose(painter)
        else:
            paint_control_overlay(painter, bounds, compose)
