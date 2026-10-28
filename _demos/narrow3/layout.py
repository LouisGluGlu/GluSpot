"""Logical-pixel geometry for the narrow study surface."""
from dataclasses import dataclass

from PySide6.QtCore import QRectF


@dataclass(frozen=True)
class NarrowLayout:
    width: int
    height: int

    @property
    def button_size(self):
        return min(36.0, max(32.0, 32.0 + (self.width - 320) / 10.0))

    @property
    def cover(self):
        side = min(96.0, max(84.0, self.width * 0.267))
        return QRectF(20, 70, side, side)

    @property
    def text(self):
        left = self.cover.right() + 16
        return QRectF(left, 70, self.width - left - 20, self.cover.height())

    @property
    def viewport(self):
        return QRectF(20, 190, min(340, self.width - 40), max(0, self.height - 416))

    @property
    def back(self):
        return QRectF(16, 16, self.button_size, self.button_size)

    @property
    def top(self):
        side = self.button_size
        return [QRectF(self.width - 16 - side - i * (side + 16), 16, side, side)
                for i in range(5)]

    @staticmethod
    def centered(x, y, side):
        return QRectF(x - side / 2, y - side / 2, side, side)

    @property
    def play(self):
        return self.centered(self.width / 2, self.height - 180, 72)

    @property
    def previous(self):
        return self.centered(self.width / 2 - 74, self.height - 180, 48)

    @property
    def next(self):
        return self.centered(self.width / 2 + 74, self.height - 180, 48)

    @property
    def favorite(self):
        return self.centered(38, self.height - 48, 44)

    @property
    def dj(self):
        return self.centered(self.width - 40, self.height - 48, 48)

    def utility(self, dj_fraction):
        # Interpolate between the centered row and the right-aligned bottom row.
        t = max(0.0, min(1.0, dj_fraction))
        y = (self.height - 48) * (1 - t) + (self.height - 108) * t
        loop_x = (self.width - 96) * (1 - t) + (self.width / 2 - 32) * t
        layout_x = (self.width - 36) * (1 - t) + (self.width / 2 + 32) * t
        return self.centered(loop_x, y, 40), self.centered(layout_x, y, 40)
