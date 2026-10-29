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
        side = min(96.0, max(84.0, self.width * .267))
        return QRectF(20, 70, side, side)

    @property
    def text(self):
        left = self.cover.right() + 16
        return QRectF(left, 70, self.width - left - 20, self.cover.height())

    @property
    def viewport(self):
        # Chrome overlays this full-height content; visibility never changes it.
        return QRectF(20, 20, self.width - 40, max(1, self.height - 40))

    @property
    def lyric_font(self):
        # Restore the immersive reading size; visibility never changes it.
        return min(40.0, max(26.0, self.width * .0875))

    @property
    def lyric_step(self):
        return self.lyric_font * 1.45

    @property
    def lyric_row(self):
        return self.lyric_font * 1.3

    @property
    def lyric_anchor(self):
        # Baseline, rather than the center of a font-dependent row rectangle.
        return self.height * .33

    @property
    def list_heading(self):
        return 0.0

    @property
    def list_top(self):
        return self.viewport.top() + self.list_heading

    def list_step(self, count):
        available = max(1.0, self.viewport.bottom() - self.list_top)
        return min(76.0, max(52.0, available / max(1, count)))

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
        return QRectF((self.width - width) / 2, self.height - 116, width, 44)

    def transport_slot(self, index):
        panel = self.transport_panel
        width = panel.width() / 3
        return self.centered(panel.left() + (index + .5) * width, panel.center().y(), 44)

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
        return self.centered(slot.center().x(), slot.center().y(), 44)

    @property
    def favorite(self):
        return QRectF(16, self.height - 59, 105, 40)

    @property
    def dj(self):
        return self.centered(self.width - 39, self.height - 39, 40)

    def utility(self, dj_fraction):
        # Keep both buttons on the bottom baseline as the DJ lane opens or closes.
        t = max(0.0, min(1.0, dj_fraction))
        y = self.height - 39
        left = self.favorite.right()
        right = (self.width - 16) * (1 - t) + self.dj.left() * t
        loop_x = left + (right - left) / 3
        layout_x = left + 2 * (right - left) / 3
        return self.centered(loop_x, y, 40), self.centered(layout_x, y, 40)
