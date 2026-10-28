"""Logical-pixel geometry for the narrow study surface."""
from dataclasses import dataclass

from PySide6.QtCore import QRectF


@dataclass(frozen=True)
class NarrowLayout:
    width: int
    height: int
    visibility: float = 1.0

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
        t = max(0.0, min(1.0, self.visibility))
        top = 20 + 170 * t
        bottom = self.height - 20 - 206 * t
        return QRectF(20, top, self.width - 40, max(1, bottom - top))

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
    def transport_panel(self):
        width = min(300.0, self.width - 32.0)
        return QRectF((self.width - width) / 2, self.height - 128, width, 60)

    def transport_slot(self, index):
        panel = self.transport_panel
        width = panel.width() / 3
        return QRectF(panel.left() + index * width, panel.top(), width, panel.height())

    @property
    def previous(self):
        return self.transport_slot(0)

    @property
    def play(self):
        return self.transport_slot(1)

    @property
    def next(self):
        return self.transport_slot(2)

    def transport_icon(self, slot):
        side = min(44.0, self.transport_panel.height() - 12.0)
        return self.centered(slot.center().x(), slot.center().y(), side)

    @property
    def favorite(self):
        return QRectF(16, self.height - 60, 105, 42)

    @property
    def dj(self):
        return self.centered(self.width - 39, self.height - 39, 46)

    def utility(self, dj_fraction):
        # Keep both buttons on the bottom baseline as the DJ lane opens or closes.
        t = max(0.0, min(1.0, dj_fraction))
        y = self.height - 39
        left = self.favorite.right()
        right = (self.width - 16) * (1 - t) + self.dj.left() * t
        loop_x = left + (right - left) / 3
        layout_x = left + 2 * (right - left) / 3
        return self.centered(loop_x, y, 40), self.centered(layout_x, y, 40)
