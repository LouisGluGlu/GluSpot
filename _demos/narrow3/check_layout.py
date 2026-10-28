"""Check A/B geometry and visibility at supported demo widths."""
import itertools
import sys

from PySide6.QtCore import QRectF
from layout import NarrowLayout
from motion import EaseOutFade, Visibility


def check(variant, width, mode, dj):
    layout = NarrowLayout(width, 640)
    controls = [layout.back, layout.top[0]]
    if variant == "B":
        controls += layout.top[1:] + [layout.previous, layout.play, layout.next, layout.favorite] + list(layout.utility(float(dj)))
        if dj:
            controls.append(layout.dj)
    errors = []
    bounds = QRectF(0, 0, width, 640)
    for rect in controls:
        if not bounds.contains(rect):
            errors.append("control out of bounds")
        if any(rect.intersects(content) for content in (layout.cover, layout.text, layout.viewport)):
            errors.append("control overlaps content")
    for first, second in itertools.combinations(controls, 2):
        if first.intersects(second):
            errors.append("controls overlap")
    if variant == "B" and not (layout.previous.right() < layout.play.left()
                               and layout.play.right() < layout.next.left()):
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
    expected = [(layout.previous, (106, 460, 48)), (layout.play, (180, 460, 72)),
                (layout.next, (254, 460, 48)), (layout.favorite, (38, 592, 44)),
                (layout.dj, (320, 592, 48))]
    for fraction, points in ((1, ((148, 532), (212, 532))),
                             (0, ((264, 592), (324, 592)))):
        expected.extend((rect, (x, y, 40)) for rect, (x, y) in zip(layout.utility(fraction), points))
    for rect, (x, y, side) in expected:
        if (rect.center().x(), rect.center().y(), rect.width()) != (x, y, side):
            print(f"FAIL expected {(x, y, side)} got {rect}")
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
