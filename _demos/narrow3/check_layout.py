"""Check immutable content geometry, veil bands and shared A/B timing."""
import itertools
import sys
from pathlib import Path

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QTextLayout
from PySide6.QtWidgets import QApplication

from layout import NarrowLayout
from motion import EaseOutFade, Visibility

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from local.music_surface_window import MusicSurfaceWindow


def check(variant, width, mode, dj):
    layout = NarrowLayout(width, 640, float(variant == "B"))
    errors = []
    viewport = QRectF(20, 20, width - 40, 600)
    for alpha in (0, .1, .5, .9, 1):
        pose = NarrowLayout(width, 640, alpha)
        if (pose.viewport != viewport or pose.lyric_font != layout.lyric_font
                or pose.lyric_row != 31 or pose.lyric_step != 31
                or pose.lyric_anchor != layout.lyric_anchor
                or pose.list_top != layout.list_top
                or pose.list_step(8) != layout.list_step(8)):
            errors.append("content geometry changes with visibility")
    if abs(layout.lyric_anchor - 211) > .5:
        errors.append("current lyric baseline")
    if layout.list_heading != 0 or layout.list_top != viewport.top():
        errors.append("list heading or top")
    if abs(layout.list_top + layout.list_step(8) * 8 - viewport.bottom()) > .001:
        errors.append("list slots do not fill the viewport")
    font = QFont()
    font.setFamilies(list(MusicSurfaceWindow.SURFACE_FONT_FAMILIES))
    font.setPixelSize(round(layout.lyric_font))
    font.setWeight(QFont.Weight.Bold)
    text = QTextLayout("把心事交给晚风", font)
    text.beginLayout()
    text_line = text.createLine()
    text.endLayout()
    if text_line.height() > layout.lyric_step:
        errors.append("font exceeds the fixed baseline rhythm")

    header = QRectF(0, 0, width, 88)
    bottom = QRectF(0, 512, width, 128)
    top_controls = [layout.back, *layout.top, layout.cover, layout.text]
    loop, mode_rect = layout.utility(float(dj))
    footer = [layout.favorite, loop, mode_rect] + ([layout.dj] if dj else [])
    transport = [layout.previous, layout.play, layout.next]
    if any(not header.contains(rect) for rect in top_controls):
        errors.append("header extends outside the top veil band")
    if any(not bottom.contains(rect) for rect in transport + footer):
        errors.append("control extends outside the bottom veil band")
    if any(rect.width() != 44 or rect.height() != 44 for rect in transport):
        errors.append("transport hit target is not 44x44")
    if any(layout.transport_icon(rect) != rect for rect in transport):
        errors.append("transport icon and hit target differ")
    if layout.transport_panel.height() != 44:
        errors.append("transport row height")
    if any(rect.height() != 40 for rect in footer):
        errors.append("footer row height")
    if not all(a.right() < b.left() for a, b in zip(transport, transport[1:])):
        errors.append("transport order or spacing")
    if not all(a.right() < b.left() for a, b in zip(footer, footer[1:])):
        errors.append("footer order or spacing")
    # Overlap with the content is intentional: chrome is above the veil.
    for first, second in itertools.combinations(top_controls + transport + footer, 2):
        if first.intersects(second):
            errors.append("chrome elements overlap")
    if variant == "A":
        visibility = Visibility(False)
        visibility.advance(0, True, True, mode == "playlist")
        if visibility.opacity.value(1) != 0:
            errors.append("A chrome is visible")
    return errors


def check_timeline():
    errors = []
    visibility = Visibility(True)
    visibility.advance(0, False, False, False)
    if visibility.opacity.value(2) != 0 or not visibility.settled(0):
        errors.append("initial idle opacity")
    visibility.advance(3, True, False, False)
    if not (visibility.footer is visibility.opacity
            and all(fade is visibility.opacity for fade in visibility.top)):
        errors.append("veil and controls use different fades")
    if abs(visibility.opacity.value(3.15) - .875) > 1e-9:
        errors.append("300ms cubic ease-out")
    if not visibility.settled(3.3) or abs(visibility.opacity.value(3.3) - 1) > 1e-9:
        errors.append("reveal duration")
    visibility.advance(4, False, False, True)
    visibility.advance(5.199, False, False, True)
    if visibility.opacity.target != 1:
        errors.append("hide starts before 1200ms idle")
    visibility.advance(5.2, False, False, True)
    if visibility.opacity.target != 0 or visibility.opacity.value(5.2) != 1:
        errors.append("idle hide boundary")
    before = visibility.opacity.value(5.35)
    visibility.advance(5.35, False, True, True)
    if abs(visibility.opacity.value(5.35) - before) > 1e-9:
        errors.append("re-entry jumps opacity")
    if not (0 < before < visibility.opacity.value(5.4) < 1):
        errors.append("re-entry does not continue the current fade")
    visibility.advance(6, False, False, False)
    visibility.advance(7.2, False, False, False)
    if abs(visibility.opacity.value(7.5)) > 1e-9:
        errors.append("300ms fade-out")
    utility = EaseOutFade(1)
    utility.retarget(0, 0, .18)
    if not (0 < utility.value(.09) < .5 and utility.value(.18) == 0):
        errors.append("utility motion")
    return errors


def main():
    app = QApplication.instance() or QApplication([])
    failures = 0
    for variant, width, mode, dj in itertools.product("AB", (320, 360, 480), ("lyrics", "playlist"), (False, True)):
        errors = check(variant, width, mode, dj)
        failures += bool(errors)
        print(f"{'FAIL' if errors else 'PASS'} {variant} {width} {mode} dj={dj}: {', '.join(errors) or 'fixed geometry and bands'}")
    errors = check_timeline()
    failures += bool(errors)
    print(f"{'FAIL' if errors else 'PASS'} timing: {', '.join(errors) or 'shared 300ms curve, 1200ms idle and continuous reversal'}")
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
