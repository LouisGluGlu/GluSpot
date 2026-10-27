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

import numpy as np
from PIL import Image
from PIL.ImageFilter import GaussianBlur
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QRadialGradient, QTransform

from local.control_hover import HoverMotion, advance_neighbor_spring, neighbor_displacement
from local.glass_style import (CONTROL_COMPOSITE_DPR, DJ_TINT, GLASS_CLEAR, GLASS_DJ,
                               GLASS_GLYPH, GLASS_SELECTED_ALPHA, PRESS_DAMPING,
                               PRESS_GROWTH, PRESS_HOLD_SECONDS, PRESS_STIFFNESS)
from local.liquid_glass import GLASS_MATERIAL_FRAME_SECONDS, GLASS_MATERIAL_MAX_EDGE


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

    def pose_key(self):
        return (tuple((key, round(s.lift, 5), round(s.glow, 5), round(s.offset.x(), 4),
                       round(s.offset.y(), 4), round(s.contact.x(), 2), round(s.contact.y(), 2))
                      for key, s in self.controls.items() if s.lift or s.glow or not s.offset.isNull()),
                tuple((key, round(value, 5)) for key, value in self.hover.items() if value))


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
        for polygon in path.toSubpathPolygons(QTransform().scale(dpr, dpr)):
            points = list(polygon)
            for a, b in zip(points, points[1:] + points[:1]):
                vx, vy = b.x() - a.x(), b.y() - a.y()
                t = np.clip(((xx - a.x()) * vx + (yy - a.y()) * vy) / max(1e-10, vx * vx + vy * vy), 0., 1.)
                nearest = np.minimum(nearest, np.hypot(xx - a.x() - t * vx, yy - a.y() - t * vy))
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
        maps.append((ix, iy, x - ix, y - iy))
    return tuple(maps)


def _sample_cached(channel, mapping):
    ix, iy, fx, fy = mapping
    top = channel[iy, ix] * (1. - fx) + channel[iy, ix + 1] * fx
    bottom = channel[iy + 1, ix] * (1. - fx) + channel[iy + 1, ix + 1] * fx
    return top * (1. - fy) + bottom * fy


def _shade(scene, scene_bounds, rect, dpr, spec, geometry_key, tint):
    geometry = control_geometry(*geometry_key)
    _, light, alpha = geometry
    h, w = alpha.shape
    pad = math.ceil((spec.lens * (1. + spec.chroma) + spec.blur * 3.) * dpr + 3.)
    full_w, full_h = w + 2 * pad, h + 2 * pad
    ratio = min(1., GLASS_MATERIAL_MAX_EDGE / max(full_w, full_h))
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
        rgba = crop.convertToFormat(QImage.Format.Format_RGBA8888)
        pixels = np.frombuffer(rgba.constBits(), np.uint8).reshape(sh, rgba.bytesPerLine())[:, :sw * 4].reshape(sh, sw, 4)[..., :3]
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
    pixels = np.asarray(Image.fromarray(pixels).filter(GaussianBlur(spec.blur * dpr * ratio)), dtype=np.float32)
    rgb = np.stack([_sample_cached(pixels[..., c], mapping)
                    for c, mapping in enumerate(_sampling_maps(geometry_key, pad, sw, sh))], axis=-1)
    lum = rgb @ np.array([.2126, .7152, .0722], np.float32)
    rgb = lum[..., None] + (rgb - lum[..., None]) * spec.sat
    if spec.veil:
        dark = float(lum.mean()) / 255. < .55
        amount = spec.veil * (1. if dark else 1.6)
        rgb = rgb * (1. - amount) + (255. if dark else 0.) * amount
    if spec.tint_alpha:
        rgb = rgb * (1. - spec.tint_alpha) + np.array(tint, np.float32) * spec.tint_alpha
    rgb = np.clip(rgb, 0., 255.)
    rgb += (255. - rgb) * light
    rgba = np.empty((h, w, 4), np.uint8)
    rgba[..., :3], rgba[..., 3] = rgb, alpha * 255.
    return QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()


class GlassControlRenderer:
    """Bounded per-surface shaded patches; scene refresh is limited to 10 Hz."""
    def __init__(self):
        self.patches = OrderedDict()
        self.bytes = 0

    def clear(self):
        self.patches.clear()
        self.bytes = 0

    def paint(self, painter, rect, *, scene, scene_bounds, ink, now, role,
              state=None, hover=0., path=None, kind="circle", level=50,
              selected=False, hover_clip=None, hover_segments=None):
        spec = (GLASS_GLYPH if kind == "path" else GLASS_DJ if kind == "dj" else GLASS_CLEAR).at_level(level)
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
        base_spec = GLASS_GLYPH if kind == "path" else GLASS_DJ if kind == "dj" else GLASS_CLEAR
        geometry_key = (w, h, round(min(rect.width(), rect.height()) / 2., 6), dpr, base_spec, signature)
        tint = DJ_TINT if kind == "dj" else (ink.red(), ink.green(), ink.blue())
        pose = (geometry_key, round(rect.x() * dpr), round(rect.y() * dpr), level, tint,
                scene_bounds.width(), scene_bounds.height())
        previous = self.patches.get(role)
        moving = state is not None and state.active(now)
        if (previous is None or previous[0] != pose or
                (previous[1] != scene.cacheKey() and (moving or now - previous[2] >= GLASS_MATERIAL_FRAME_SECONDS))):
            image = _shade(scene, scene_bounds, rect, dpr, spec, geometry_key, tint)
            if previous is not None:
                self.bytes -= previous[3].sizeInBytes()
            self.patches[role] = (pose, scene.cacheKey(), now, image)
            self.bytes += image.sizeInBytes()
            self.patches.move_to_end(role)
            while len(self.patches) > 32 or self.bytes > 8 * 1024 * 1024:
                self.bytes -= self.patches.popitem(last=False)[1][3].sizeInBytes()
        else:
            image = previous[3]
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
        overlays = (selected or hover > .001 or
                    any(amount > .001 for amount, _rect in (hover_segments or ())) or
                    state is not None and (state.glow > .001 or abs(lift) > .001))
        if not overlays:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(grown, image)
            painter.restore()
            return
        # Composite every edge and feedback layer at >=2x, even when callers
        # draw controls directly into a DPR 1 QImage.
        bounds = grown.adjusted(-1., -1., 1., 1.)
        def compose(p):
            p.save()
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(grown, image)
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
