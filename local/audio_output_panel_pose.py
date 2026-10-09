"""The chooser's single, reversible circle-to-capsule-to-list trajectory."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF


def _ease(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def _blend_rect(first: QRectF, last: QRectF, amount: float) -> QRectF:
    return QRectF(*(a + (b - a) * amount for a, b in zip(
        (first.x(), first.y(), first.width(), first.height()),
        (last.x(), last.y(), last.width(), last.height()))))


@dataclass(frozen=True)
class OutputPanelPose:
    shell_rect: QRectF
    first_rect: QRectF
    radius: float
    horizontal: float
    row_unfold: float


def output_panel_pose(progress: float, source: QRectF, full: QRectF,
                      first_row: QRectF) -> OutputPanelPose:
    """Keep horizontal expansion ahead of the list's vertical unfolding.

    Geometry derives from the same spring in either direction. A reversal
    changes only its target, preserving both the displayed pose and velocity.
    """
    horizontal = _ease(progress / .48)
    vertical = _ease((progress - .48) / .52)
    # The bounded spring's ordinary 60 Hz peak is about 1.008. Preserve that
    # small overrun in row spacing instead of flattening every pose >= 1.
    # The shell remains inside its canvas; only the cached rows bounce.
    row_unfold = vertical + max(0.0, min(.02, (progress - 1.0) * 2.0))
    capsule_shell = QRectF(full.x(), full.y(), full.width(),
                           min(full.height(), first_row.bottom() + first_row.top()))
    shell = _blend_rect(source, capsule_shell, horizontal)
    shell.setHeight(shell.height() + (full.height() - shell.height()) * vertical)
    first = _blend_rect(source, first_row, horizontal)
    if progress < 0:
        # The closing spring briefly compresses the source face inside its
        # canvas before settling; top/right remain fixed in both directions.
        compression = max(-.02, progress * 2.0)
        shell.setHeight(shell.height() * (1.0 + compression))
        first.setHeight(first.height() * (1.0 + compression))
    radius = source.height() / 2 + (30.0 - source.height() / 2) * horizontal
    # A short viewport can be smaller than the ordinary capsule shell.
    radius = min(radius, shell.height() / 2, shell.width() / 2)
    return OutputPanelPose(shell, first, radius, horizontal, row_unfold)
