"""Check A/B geometry and visibility at supported demo widths."""
import itertools
import sys

from PySide6.QtCore import QRectF
from layout import NarrowLayout
from motion import Visibility


def check(variant, width, mode):
    layout = NarrowLayout(width, 640)
    controls = [layout.back, layout.top[0]]
    if variant == "B":
        controls += layout.top[1:] + [layout.previous, layout.play, layout.next, layout.favorite]
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
                               and layout.play.right() < layout.next.left()
                               and layout.next.right() < layout.favorite.left()):
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
    for variant, width, mode in itertools.product("AB", (320, 360, 480), ("lyrics", "playlist")):
        errors = check(variant, width, mode)
        failures += bool(errors)
        print(f"{'FAIL' if errors else 'PASS'} {variant} {width} {mode}: {', '.join(errors) or 'geometry and visibility'}")
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
