"""Check the static geometry currently implemented by the narrow study."""
import itertools
import sys

from layout import NarrowLayout


def distance(a, b):
    dx = max(0.0, a.left() - b.right(), b.left() - a.right())
    dy = max(0.0, a.top() - b.bottom(), b.top() - a.bottom())
    return (dx * dx + dy * dy) ** 0.5


def check(variant, width, mode, expanded):
    layout = NarrowLayout(width, 640)
    controls = [layout.back, layout.top[0]]
    if expanded:
        controls.extend(layout.top[1:])
    footer = variant != "A" and not (expanded and mode == "playlist")
    if footer:
        controls.extend((layout.play, layout.favorite))
    errors = []
    bounds = layout.viewport.adjusted(-20, -190, width - layout.viewport.right(), 96)
    for index, rect in enumerate(controls):
        if not bounds.contains(rect):
            errors.append(f"control {index} out of bounds")
    for (i, first), (j, second) in itertools.combinations(enumerate(controls), 2):
        if distance(first, second) < 16 - 1e-6:
            errors.append(f"controls {i}/{j} gap {distance(first, second):.1f}")
    for rect in controls:
        if rect.intersects(layout.cover) or rect.intersects(layout.text) or rect.intersects(layout.viewport):
            errors.append("control overlaps content")
    count = len(controls) + int(mode == "playlist")
    if count > 8:
        errors.append(f"glass count {count}")
    return errors


def main():
    failures = 0
    for variant, width, mode, expanded in itertools.product("ABC", (320, 360, 480), ("lyrics", "playlist"), (False, True)):
        errors = check(variant, width, mode, expanded)
        failures += bool(errors)
        print(f"{'FAIL' if errors else 'PASS'} {variant} {width} {mode} expanded={expanded}: {', '.join(errors) or 'static geometry'}")
    print("NOTE: Static geometry only; press expansion, animated frames, text metrics and footer hover are not verified.")
    return int(failures != 0)


if __name__ == "__main__":
    sys.exit(main())
