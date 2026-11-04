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
        top = self.cover.bottom() + 24.
        bottom = self.transport_panel.top() - 20.
        return QRectF(20, top, self.width - 40, max(1., bottom - top))

    @property
    def lyric_viewport(self):
        # Reading occupies the whole window. Chrome changes only the soft
        # ink mask, never the viewport, wrapping or active-row anchor.
        return QRectF(20, 0., self.width - 40, self.height)

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
    def _capsule_metrics(self):
        """Fit both frames before progressively disclosing collection actions.

        The Back space stays reserved when ownership hides its button. This
        keeps both capsules stable, especially the five indispensable B slots.
        """
        side = self.button_size
        pad = gap = min(4.0, max(2.0, (self.width - 320.0) / 40.0))
        dj = self.dj_available
        collect = True

        def required_width():
            a = side * (2 if collect else 1) + (side + gap if dj else 0) + pad * 2
            b = side * 5 + gap * 4 + pad * 2
            return 32.0 + side + 16.0 + a + b

        # Compress spacing first; optional DJ, then Collect, are the only
        # actions allowed to yield. B always retains all five entries.
        if required_width() > self.width:
            pad = gap = 2.0
        if required_width() > self.width:
            dj = False
        if required_width() > self.width:
            collect = False
        return side, pad, gap, dj, collect

    @property
    def capsule_a(self):
        side, pad, gap, dj, collect = self._capsule_metrics
        width = side * (2 if collect else 1) + (side + gap if dj else 0) + pad * 2
        return QRectF(16.0 + side + 8.0, 16.0, width, side)

    @property
    def capsule_b(self):
        side, pad, gap, _dj, _collect = self._capsule_metrics
        width = side * 5 + gap * 4 + pad * 2
        return QRectF(self.width - 16.0 - width, 16.0, width, side)

    @property
    def _top_rects(self):
        side, pad, gap, dj, collect = self._capsule_metrics
        rects = {}
        if self.library_available:
            rects["library"] = QRectF(16.0, 16.0, side, side)
        a = self.capsule_a
        collection_width = side * (2 if collect else 1)
        rects["collection"] = QRectF(a.left() + pad, a.top(), collection_width, side)
        if dj:
            rects["personal_dj"] = QRectF(a.left() + pad + collection_width + gap,
                                         a.top(), side, side)
        left = self.capsule_b.left() + pad
        for key in ("audio_output", "content_layout", "playback_mode", "translation", "return"):
            rects[key] = QRectF(left, 16.0, side, side)
            left += side + gap
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
        return QRectF((self.width - width) / 2, self.height - 24. - 44., width, 44)

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
    def collection_slots(self):
        favorite = self.favorite
        side = self.button_size
        count = 2 if self._capsule_metrics[4] else 1
        return tuple(QRectF(favorite.left() + index * side, favorite.top(), side, side)
                     for index in range(count))

    @property
    def dj(self):
        return self._top_rects.get("personal_dj", QRectF())

    def utility(self, dj_fraction):
        # Compatibility aliases: these controls now occupy their single top slots.
        return self.top[2], self.top[0]
