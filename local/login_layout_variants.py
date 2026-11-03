"""Source-page geometry only; account/login state stays in PlaybackEntryWindow."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF


@dataclass(frozen=True)
class LoginVariantLayout:
    tiles: tuple[QRectF, ...]
    pane: QRectF
    upcoming: QRectF
    centered: bool
    rail: QRectF | None = None

    def login_content(self) -> tuple[QRectF, QRectF]:
        pane = self.pane
        if self.centered:
            side = min(156., max(104., pane.height() - 202.))
            qr = QRectF(pane.center().x() - side / 2, pane.top() + 18, side, side)
            return qr, QRectF(pane.left() + 20, qr.bottom() + 12, pane.width() - 40,
                             pane.bottom() - qr.bottom() - 28)
        side = min(180., pane.height() - 40, pane.width() * .34)
        gap = 28 if pane.width() > 480 else 18
        detail_width = min(280., pane.width() - side - gap - 40)
        left = pane.center().x() - (side + gap + detail_width) / 2
        qr = QRectF(left, pane.center().y() - side / 2, side, side)
        return qr, QRectF(qr.right() + gap, pane.top() + 24, detail_width, pane.height() - 48)

    def login_detail(self) -> tuple[QRectF, QRectF, QRectF, QRectF]:
        _qr, detail = self.login_content()
        title = QRectF(detail.left(), detail.top(), detail.width(), 26)
        helper = QRectF(detail.left(), title.bottom() + 4, detail.width(), 34)
        message = QRectF(detail.left(), helper.bottom() + 6, detail.width(),
                         max(28., min(46., detail.height() - 122)))
        action_width = min(156., detail.width())
        action = QRectF(detail.center().x() - action_width / 2 if self.centered else detail.left(),
                        max(message.bottom() + 8, detail.bottom() - 44), action_width, 44)
        return title, helper, message, action

    def account_content(self) -> tuple[QRectF, QRectF, QRectF]:
        pane = self.pane
        if self.centered:
            side = min(116., max(88., pane.height() * .31))
            top = pane.top() + max(24., (pane.height() - side - 150) / 2)
            avatar = QRectF(pane.center().x() - side / 2, top, side, side)
            name = QRectF(pane.left() + 20, avatar.bottom() + 18, pane.width() - 40, 32)
            action = QRectF(pane.center().x() - 78, name.bottom() + 50, 156, 44)
            return avatar, name, action
        side = min(112., pane.height() * .40, pane.width() * .23)
        left = pane.left() + (36 if pane.width() >= 500 else 24)
        avatar = QRectF(left, pane.center().y() - side / 2, side, side)
        text_x = avatar.right() + (32 if pane.width() >= 500 else 20)
        name = QRectF(text_x, pane.center().y() - 62, pane.right() - text_x - 20, 32)
        action = QRectF(text_x, name.bottom() + 48, min(156., name.width()), 44)
        return avatar, name, action


def login_variant_layout(name: str, width: int, height: int, supported: tuple[bool, ...]) -> LoginVariantLayout:
    """All rects use window coordinates; empty tiles are intentionally hidden."""
    wide = width >= 720 and height >= 520
    short = height < 500
    active = [index for index, enabled in enumerate(supported) if enabled]
    upcoming = [index for index, enabled in enumerate(supported) if not enabled]
    tiles = [QRectF() for _ in supported]
    if name == "sidebar":
        if wide:
            for row, index in enumerate(active):
                tiles[index] = QRectF(36, 118 + row * 68, 184, 58)
            for row, index in enumerate(upcoming):
                tiles[index] = QRectF(36, 350 + row * 48, 184, 44)
            return LoginVariantLayout(tuple(tiles), QRectF(252, 104, width - 276, height - 128),
                                      QRectF(48, 324, 164, 20), False,
                                      QRectF(24, 104, 208, height - 128))
        # Keep the platform axis vertical even on the smallest music window.
        # Compact spacing also fits all six 44 px targets on a 360 px display.
        top = 84 if height >= 440 else 72
        rail = QRectF(16, top, 64, height - top - 16)
        gap = min(10., max(0., (rail.height() - len(supported) * 44 - 8) / max(1, len(supported))))
        y = rail.top() + 4
        for index in active + upcoming:
            if index == (upcoming[0] if upcoming else -1):
                y += gap
            tiles[index] = QRectF(24, y, 48, 44)
            y += 44 + gap
        pane = QRectF(96, top, width - 112, height - top - 24)
        return LoginVariantLayout(tuple(tiles), pane, QRectF(),
                                  pane.width() < 400 and pane.height() >= 280, rail)
    if name == "hero" and not short:
        for row, index in enumerate(active):
            tiles[index] = QRectF(32 if wide else 24, (164 if wide else 142) + row * (64 if wide else 50),
                                 300 if wide else width - 48, 56 if wide else 44)
        if wide:
            for row, index in enumerate(upcoming):
                tiles[index] = QRectF(32, 370 + row * 48, 300, 44)
        pane = QRectF(356, 164, width - 380, height - 188) if wide else QRectF(24, 326, width - 48, height - 350)
        return LoginVariantLayout(tuple(tiles), pane,
                                  QRectF(44 if wide else 24, 346 if wide else 294,
                                         276 if wide else width - 48, 20), False)
    # Focus uses one compact segmented row at both widths. Unsupported
    # adapters share one quiet, noninteractive line.
    switch_width = min(216. if name == "focus" else 408., width - 48)
    chip_width = (switch_width - 12) / max(1, len(active))
    for column, index in enumerate(active):
        tiles[index] = QRectF((width - switch_width) / 2 + column * (chip_width + 6), 78, chip_width, 44)
    pane_width = min(520., width - 48) if name == "focus" else width - 48
    pane = QRectF((width - pane_width) / 2, 158, pane_width, height - 182)
    return LoginVariantLayout(tuple(tiles), pane, QRectF(24, 130, width - 48, 18), pane.height() >= 330)
