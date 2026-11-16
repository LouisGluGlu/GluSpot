"""System translate mark, taken from the supplied 1024 viewBox path.

The path is split into four filled pieces. Holes stay in the same path and
use even-odd fill, so the two squares in 中 and the triangle in A stay open.
"""
from __future__ import annotations

import math
import re

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QTransform

VIEWBOX = 1024.0
_PATH = (
    "M213.333333 640v85.333333a85.333333 85.333333 0 0 0 78.933334 85.12"
    "L298.666667 810.666667h128v85.333333H298.666667"
    "a170.666667 170.666667 0 0 1-170.666667-170.666667v-85.333333h85.333333z"
    " m554.666667-213.333333l187.733333 469.333333h-91.946666l-51.242667-128"
    "h-174.506667l-51.157333 128h-91.904L682.666667 426.666667h85.333333z"
    " m-42.666667 123.093333L672.128 682.666667h106.325333L725.333333 549.76z"
    "M341.333333 85.333333v85.333334h170.666667v298.666666H341.333333v128"
    "H256v-128H85.333333V170.666667h170.666667V85.333333h85.333333z"
    " m384 42.666667a170.666667 170.666667 0 0 1 170.666667 170.666667v85.333333"
    "h-85.333333V298.666667a85.333333 85.333333 0 0 0-85.333334-85.333334h-128V128h128z"
    "M256 256H170.666667v128h85.333333V256z"
    " m170.666667 0H341.333333v128h85.333334V256z"
)
_TOKEN = re.compile(r"[MmLlHhVvAaZz]|[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?")


def _arc_points(x1, y1, rx, ry, phi_deg, large, sweep, x2, y2, steps=48):
    if math.hypot(x1 - x2, y1 - y2) < 1e-6:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-6 or ry < 1e-6:
        return [(x2, y2)]
    phi = math.radians(phi_deg)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale
    sign = -1.0 if large == sweep else 1.0
    numer = max(0.0, rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p)
    denom = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = sign * math.sqrt(numer / denom) if denom else 0.0
    cxp = coef * rx * y1p / ry
    cyp = coef * -ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2.0

    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        norm = math.hypot(ux, uy) * math.hypot(vx, vy)
        amount = math.acos(max(-1.0, min(1.0, dot / norm))) if norm else 0.0
        if ux * vy - uy * vx < 0.0:
            amount = -amount
        return amount

    theta1 = angle(1.0, 0.0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    delta = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and delta > 0.0:
        delta -= math.tau
    elif sweep and delta < 0.0:
        delta += math.tau
    points = []
    for index in range(1, steps + 1):
        theta = theta1 + delta * index / steps
        points.append((
            cx + rx * math.cos(phi) * math.cos(theta) - ry * math.sin(phi) * math.sin(theta),
            cy + rx * math.sin(phi) * math.cos(theta) + ry * math.cos(phi) * math.sin(theta),
        ))
    return points


def _subpaths(source: str) -> list[QPainterPath]:
    tokens = _TOKEN.findall(source)
    cursor = 0
    command = ""
    x = y = start_x = start_y = 0.0
    paths: list[QPainterPath] = []
    path: QPainterPath | None = None

    def number():
        nonlocal cursor
        value = float(tokens[cursor])
        cursor += 1
        return value

    while cursor < len(tokens):
        if tokens[cursor].isalpha():
            command = tokens[cursor]
            cursor += 1
        relative = command.islower()
        op = command.lower()
        if op == "m":
            nx, ny = number(), number()
            if relative and path is not None:
                nx += x
                ny += y
            x, y = nx, ny
            start_x, start_y = x, y
            path = QPainterPath()
            path.moveTo(x, y)
            paths.append(path)
            command = "l" if relative else "L"
            continue
        if path is None:
            raise ValueError(f"path command {command} before a move")
        if op == "z":
            path.closeSubpath()
            x, y = start_x, start_y
            continue
        if op == "l":
            nx, ny = number(), number()
            if relative:
                nx += x
                ny += y
            x, y = nx, ny
            path.lineTo(x, y)
        elif op == "h":
            nx = number()
            x = x + nx if relative else nx
            path.lineTo(x, y)
        elif op == "v":
            ny = number()
            y = y + ny if relative else ny
            path.lineTo(x, y)
        elif op == "a":
            rx, ry, phi = number(), number(), number()
            large, sweep = int(number()), int(number())
            nx, ny = number(), number()
            if relative:
                nx += x
                ny += y
            for px, py in _arc_points(x, y, rx, ry, phi, large, sweep, nx, ny, steps=128):
                path.lineTo(px, py)
            x, y = nx, ny
        else:
            raise ValueError(f"unsupported path command {command}")
    for item in paths:
        item.setFillRule(Qt.FillRule.OddEvenFill)
    return paths


def _join(parts: list[QPainterPath]) -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    for part in parts:
        path.addPath(part)
    path.setFillRule(Qt.FillRule.OddEvenFill)
    return path


_SUBPATHS = _subpaths(_PATH)
# Order in the source: lower-left arc, A, A's hole, 中, upper-right arc, two holes in 中.
ZHONG = _join([_SUBPATHS[3], _SUBPATHS[5], _SUBPATHS[6]])
AYE = _join([_SUBPATHS[1], _SUBPATHS[2]])
ARC_LOWER = _SUBPATHS[0]
ARC_UPPER = _SUBPATHS[4]
MARK = _join([ZHONG, AYE, ARC_LOWER, ARC_UPPER])


def _center(path: QPainterPath) -> QPointF:
    bounds = path.boundingRect()
    return bounds.center()


ZHONG_CENTER = _center(ZHONG)
AYE_CENTER = _center(AYE)


ICON_CENTER = QPointF(VIEWBOX / 2.0, VIEWBOX / 2.0)


def spin(degrees: float) -> QTransform:
    """Clockwise rotation around the viewBox center. Qt's rotate is clockwise."""
    transform = QTransform()
    transform.translate(ICON_CENTER.x(), ICON_CENTER.y())
    transform.rotate(degrees)
    transform.translate(-ICON_CENTER.x(), -ICON_CENTER.y())
    return transform


def letter_pose(progress: float) -> tuple[QPointF, QPointF]:
    """Centers of 中 and A after clockwise half-turns. Glyphs stay upright.

    One unit of progress is 180 degrees. The letters ride the circle; they
    are not rotated. Progress 2 is the same picture as progress 0.
    """
    turned = spin(float(progress) * 180.0)
    return turned.map(ZHONG_CENTER), turned.map(AYE_CENTER)


def _scaled(path: QPainterPath, rect: QRectF, degrees: float, *, upright_from: QPointF | None = None,
            upright_to: QPointF | None = None) -> QPainterPath:
    """Map viewBox space into rect. Arcs spin; letters only slide to a new center."""
    scale_x = rect.width() / VIEWBOX
    scale_y = rect.height() / VIEWBOX
    transform = QTransform()
    transform.translate(rect.left(), rect.top())
    transform.scale(scale_x, scale_y)
    if upright_from is not None and upright_to is not None:
        transform.translate(upright_to.x() - upright_from.x(), upright_to.y() - upright_from.y())
    else:
        transform.translate(ICON_CENTER.x(), ICON_CENTER.y())
        transform.rotate(degrees)
        transform.translate(-ICON_CENTER.x(), -ICON_CENTER.y())
    mapped = transform.map(path)
    mapped.setFillRule(Qt.FillRule.OddEvenFill)
    return mapped


def paint_translation_icon(painter: QPainter, rect: QRectF, progress: float, ink: QColor) -> None:
    """Spin the whole mark clockwise. Letters stay upright; arcs turn with the wheel."""
    if rect.width() < 2.0 or rect.height() < 2.0:
        return
    degrees = float(progress) * 180.0
    zhong_at, aye_at = letter_pose(progress)
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(ink)
    painter.drawPath(_scaled(ARC_LOWER, rect, degrees))
    painter.drawPath(_scaled(ARC_UPPER, rect, degrees))
    painter.drawPath(_scaled(ZHONG, rect, degrees, upright_from=ZHONG_CENTER, upright_to=zhong_at))
    painter.drawPath(_scaled(AYE, rect, degrees, upright_from=AYE_CENTER, upright_to=aye_at))
    painter.restore()


def icon_paths(rect: QRectF, progress: float) -> QPainterPath:
    """Filled mark at this turn, for hit tests and the resting comparison."""
    degrees = float(progress) * 180.0
    zhong_at, aye_at = letter_pose(progress)
    return _join([
        _scaled(ZHONG, rect, degrees, upright_from=ZHONG_CENTER, upright_to=zhong_at),
        _scaled(AYE, rect, degrees, upright_from=AYE_CENTER, upright_to=aye_at),
        _scaled(ARC_LOWER, rect, degrees),
        _scaled(ARC_UPPER, rect, degrees),
    ])
