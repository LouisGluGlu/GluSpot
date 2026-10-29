"""Logical-pixel geometry for the narrow study surface."""
from dataclasses import dataclass

from PySide6.QtCore import QRectF
from local.music_lyric_presentation import surface_pixel_size, LYRIC_LINE_SPACING


@dataclass(frozen=True)
class NarrowLayout:
    width: int
    height: int
    visibility: float = 1.0
    library_available: bool = True
    dj_available: bool = False

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
        return surface_pixel_size(self.width)

    @property
    def lyric_step(self):
        return self.lyric_font * LYRIC_LINE_SPACING

    @property
    def lyric_row(self):
        return self.lyric_font * 1.3

    @property
    def lyric_anchor(self):
        # The active cap-height centre and intro dots share one fixed slot.
        return round(self.height * .5)

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
        return self._top_rects.get("library", QRectF())

    @property
    def _top_rects(self):
        side = self.button_size
        # Only the missing library slot is reclaimed on the left. Window-mode
        # stays after translation on the right, including capture-only use.
        slots = ([("library", side)] if self.library_available else [])
        slots += [("collection", side * 2), ("audio_output", side),
                  ("playback_mode", side), ("content_layout", side),
                  ("translation", side), ("return", side)]
        if self.dj_available:
            slots.append(("personal_dj", side))
        gap = (self.width - 32 - sum(width for _key, width in slots)) / (len(slots) - 1)
        left = 16.0
        rects = {}
        for key, width in slots:
            rects[key] = QRectF(left, 16, width, side)
            left += width + gap
        return rects

    @property
    def top(self):
        rects = self._top_rects
        return [rects[key] for key in ("content_layout", "audio_output", "playback_mode",
                                      "translation", "return")]

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
        return self._top_rects["collection"]

    @property
    def dj(self):
        return self._top_rects.get("personal_dj", QRectF())

    def utility(self, dj_fraction):
        # Compatibility aliases: these controls now occupy their single top slots.
        return self.top[2], self.top[0]
