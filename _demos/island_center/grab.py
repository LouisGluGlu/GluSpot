"""Offscreen shots of each lyric and magnet scheme. Does not open a player."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from demo import DemoWindow  # noqa: E402
from layout import (  # noqa: E402
    CONTENT_WIDTH,
    EDGE_WIDTH,
    LONG_LINE,
    MAGNET_H,
    RELEASE_X,
    SCREEN_W,
    SHORT_LINE,
    STANDARD_WIDTH,
    album_anchor_offset,
    lyric_pose,
    magnet_pose,
    text_width,
)


def _luminance(color: QColor) -> int:
    return int(0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue())


def _ink_span(image, rect) -> tuple[float, float, int]:
    """Mean x, height and count of bright lyric pixels inside rect."""
    left = max(0, int(rect.left()) + 1)
    right = min(image.width() - 1, int(rect.right()) - 1)
    top = max(0, int(rect.top()) + 12)
    bottom = min(image.height() - 1, int(rect.bottom()) - 6)
    total_x = 0.0
    count = 0
    min_y = bottom
    max_y = top
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            color = image.pixelColor(x, y)
            if color.red() > 190 and color.green() > 190 and color.blue() > 190:
                total_x += x
                count += 1
                min_y = min(min_y, y)
                max_y = max(max_y, y)
    if count < 20:
        raise RuntimeError(f"lyric ink missing in {rect}")
    return total_x / count, float(max_y - min_y), count


def _dark_bbox(image, bounds: QRect) -> QRect:
    min_x, min_y = bounds.right(), bounds.bottom()
    max_x, max_y = bounds.left(), bounds.top()
    found = False
    for y in range(bounds.top(), bounds.bottom()):
        for x in range(bounds.left(), bounds.right()):
            color = image.pixelColor(x, y)
            if _luminance(color) < 48 and color.alpha() > 200:
                found = True
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
    if not found:
        raise RuntimeError("magnet island not found")
    return QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def _assert_lyric(window: DemoWindow, image) -> None:
    short, long = window.lyric_islands()
    anchors = []
    for rect, text, phase in ((short, SHORT_LINE, 0.0), (long, LONG_LINE, 0.0)):
        pose = lyric_pose(window.scheme, rect, window.shift, text, phase)
        center, height, _count = _ink_span(image, pose.clip)
        if pose.text_width <= pose.clip.width() + 1:
            expected = pose.text_center
        else:
            left = max(pose.clip.left(), pose.text_left)
            right = min(pose.clip.right(), pose.text_right)
            expected = (left + right) / 2.0
        if abs(center - expected) > 14:
            raise RuntimeError(f"{window.scheme} ink at {center:.1f}, expected {expected:.1f}")
        anchors.append(pose.anchor)
        if text == LONG_LINE and window.scheme == "b" and height > 18:
            raise RuntimeError(f"scheme B long line did not shrink, ink height {height}")
    if window.scheme == "a" and abs(anchors[0] - (short.center().x())) < 8:
        raise RuntimeError("scheme A collapsed onto the island center")


def _assert_magnet(window: DemoWindow, image) -> None:
    pose = window._magnet()
    origin, scale = window._magnet_view()
    dest = window.magnet_dest()
    top_left = window._to_widget(pose.rect.left(), pose.rect.top(), origin, scale, dest)
    mapped = QRect(int(round(top_left.x())), int(round(top_left.y())),
                   int(round(pose.rect.width() * scale)), int(round(pose.rect.height() * scale)))
    # Search only the light screen, so the dark desk outside it cannot count.
    screen = QRect(int(dest.x()), int(dest.y()), int(origin.width() * scale), int(origin.height() * scale))
    found = _dark_bbox(image, screen)
    if abs(found.center().x() - mapped.center().x()) > 6 or abs(found.center().y() - mapped.center().y()) > 6:
        raise RuntimeError(f"magnet island landed at {found.center().x()},{found.center().y()} not {mapped.center().x()},{mapped.center().y()}")
    if abs(found.width() - mapped.width()) > 6 or abs(found.height() - mapped.height()) > 6:
        raise RuntimeError(f"magnet size {found.width()}x{found.height()} != {mapped.width()}x{mapped.height()}")


def _button_face(image, button) -> int:
    rect = button.geometry()
    # Left padding is fill, not a glyph.
    color = image.pixelColor(rect.left() + 12, rect.center().y())
    return _luminance(color)


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "shots"
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyle("Fusion")
    window = DemoWindow()
    window.show()
    app.processEvents()

    print(f"short {text_width(SHORT_LINE, 16):.1f}px  long16 {text_width(LONG_LINE, 16):.1f}px  long11 {text_width(LONG_LINE, 11):.1f}px")
    print(f"album offset {album_anchor_offset():.2f}")
    short, _long = window.lyric_islands()
    centers = {}
    for scheme in ("a", "b", "c"):
        pose = lyric_pose(scheme, short, 24.0, SHORT_LINE, 0.0)
        centers[scheme] = pose.anchor
        print(f"lyric {scheme} anchor {pose.anchor:.1f} font {pose.font_px}")
    if abs(centers["a"] - centers["b"]) < 20 or abs(centers["c"] - centers["b"]) < 20 or abs(centers["a"] - centers["c"]) < 20:
        raise SystemExit(f"lyric anchors are not distinct: {centers}")

    top_a = magnet_pose("a", "top", RELEASE_X, 420)
    top_b = magnet_pose("b", "top")
    top_c = magnet_pose("c", "top")
    corner_c = magnet_pose("c", "top_left")
    if top_a.y != 0 or top_a.w != CONTENT_WIDTH or abs(top_a.rect.center().x() - RELEASE_X) > 0.1:
        raise SystemExit(f"scheme A top unexpected {top_a}")
    if top_b.margin != 16 or top_b.w != STANDARD_WIDTH or abs(top_b.rect.center().x() - SCREEN_W / 2) > 0.1:
        raise SystemExit(f"scheme B top unexpected {top_b}")
    if top_c.margin != 8 or top_c.w != EDGE_WIDTH or abs(top_c.rect.center().x() - SCREEN_W / 2) > 0.1:
        raise SystemExit(f"scheme C edge unexpected {top_c}")
    if corner_c.w != 320 or corner_c.margin != 16 or corner_c.x != 16 or corner_c.y != 16:
        raise SystemExit(f"scheme C corner unexpected {corner_c}")
    if MAGNET_H != 48:
        raise SystemExit("magnet height drifted")

    saved = []
    window.resize(1280, 580)
    app.processEvents()
    for scheme in ("a", "b", "c"):
        window.prepare_lyric(scheme)
        window.repaint()
        app.processEvents()
        image = window.grab().toImage()
        if image.size() != window.size():
            raise SystemExit(f"unexpected grab size {image.size()}")
        _assert_lyric(window, image)
        path = out / f"lyric-{scheme}.png"
        if not image.save(str(path)):
            raise SystemExit(f"could not save {path}")
        saved.append(image)
        print(path)
    if saved[0] == saved[1] or saved[1] == saved[2]:
        raise SystemExit("lyric shots are identical")

    window.resize(1280, 620)
    app.processEvents()
    for scheme in ("a", "b", "c"):
        window.prepare_magnet(scheme)
        window.repaint()
        app.processEvents()
        image = window.grab().toImage()
        _assert_magnet(window, image)
        path = out / f"magnet-{scheme}.png"
        if not image.save(str(path)):
            raise SystemExit(f"could not save {path}")
        print(path)

    window.resize(1280, 580)
    window.prepare_lyric("a")
    window.repaint()
    app.processEvents()
    face = window.grab().toImage()
    selected = _button_face(face, window.scheme_buttons[0][1])
    idle = _button_face(face, window.scheme_buttons[1][1])
    print(f"button luminance selected {selected} idle {idle}")
    if selected <= idle + 6:
        raise SystemExit("selected flat button is not brighter than idle")
    if selected > 170:
        raise SystemExit("button face is too solid to be translucent glass")
    window.close()


if __name__ == "__main__":
    main()
