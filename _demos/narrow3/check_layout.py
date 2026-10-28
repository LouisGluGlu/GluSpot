"""Check A/B geometry and visibility at supported demo widths."""
import itertools
import sys

from PySide6.QtCore import QRectF
from layout import NarrowLayout
from motion import EaseOutFade, Visibility


def check(variant, width, mode, dj):
    layout = NarrowLayout(width, 640, float(variant == "B"))
    controls = []
    if variant == "B":
        controls += [layout.back] + layout.top + [layout.previous, layout.play, layout.next, layout.favorite] + list(layout.utility(float(dj)))
        if dj:
            controls.append(layout.dj)
    errors = []
    viewport = layout.viewport
    if variant == "A" and (viewport.top() != 20 or viewport.bottom() != 620 or viewport.width() != width - 40):
        errors.append("idle viewport")
    if variant == "B" and (viewport.top() != 190 or viewport.bottom() != 414):
        errors.append("hover viewport")
    middle = NarrowLayout(width, 640, .5).viewport
    if middle.top() != 105 or middle.bottom() != 517:
        errors.append("interpolated viewport")
    if width == 360 and not 30 <= layout.lyric_font <= 32:
        errors.append("lyric size at 360")
    fonts = [NarrowLayout(size, 640).lyric_font for size in (320, 360, 480)]
    if not (fonts[0] < fonts[1] <= fonts[2]) or fonts[-1] > 40:
        errors.append("lyric size does not scale")
    if viewport.top() < 16 or width - viewport.right() < 16:
        errors.append("viewport margin")
    if abs(layout.lyric_anchor - (viewport.top() + viewport.height() * .33)) > .001:
        errors.append("lyric anchor")
    if viewport.top() + layout.lyric_step * 5 > 640 * 2 / 3 and variant == "A":
        errors.append("lyrics do not reach bottom third")
    rows = layout.list_top + layout.list_step(8) * 8
    if variant == "A" and rows < 640 * 2 / 3:
        errors.append("list does not reach bottom third")
    if variant == "A" and layout.list_heading > .001:
        errors.append("idle list keeps heading")
    bounds = QRectF(0, 0, width, 640)
    for rect in controls:
        if not bounds.contains(rect):
            errors.append("control out of bounds")
        if any(rect.intersects(content) for content in (layout.cover, layout.text, layout.viewport)):
            errors.append("control overlaps content")
    for first, second in itertools.combinations(controls, 2):
        if first.intersects(second):
            errors.append("controls overlap")
    if variant == "B" and not (layout.previous.right() <= layout.play.left()
                               and layout.play.right() <= layout.next.left()):
        errors.append("transport order or spacing")
    visibility = Visibility(variant == "B")
    for frame in range(60):
        visibility.advance(frame / 60, True, True, mode == "playlist")
    values = [fade.value(1.0) for fade in visibility.top] + [visibility.footer.value(1.0)]
    if any(abs(value - (1 if variant == "B" else 0)) > .001 for value in values):
        errors.append("hover visibility")
    for frame in range(60, 120):
        visibility.advance(frame / 60, False, False, mode == "playlist")
    if any(fade.value(2.0) > .001 for fade in visibility.top + [visibility.footer]):
        errors.append("idle visibility")
    return errors


def main():
    failures = 0
    layout = NarrowLayout(360, 640)
    for slot in (layout.previous, layout.play, layout.next):
        icon = layout.transport_icon(slot)
        if icon.width() != 44 or icon.height() != 44 or icon.center() != slot.center():
            print(f"FAIL transport icon {icon}")
            failures += 1
    if layout.transport_panel.width() != 300 or layout.transport_panel.height() != 60:
        print("FAIL transport panel")
        failures += 1
    motion = EaseOutFade(1)
    motion.retarget(0, 0, .18)
    if not (0 < motion.value(.09) < .5 and motion.value(.18) == 0):
        print("FAIL utility motion")
        failures += 1
    for variant, width, mode, dj in itertools.product("AB", (320, 360, 480), ("lyrics", "playlist"), (False, True)):
        errors = check(variant, width, mode, dj)
        failures += bool(errors)
        print(f"{'FAIL' if errors else 'PASS'} {variant} {width} {mode} dj={dj}: {', '.join(errors) or 'geometry and visibility'}")
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
