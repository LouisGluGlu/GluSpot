"""Check immutable content geometry, veil bands and shared A/B timing."""
import itertools
import sys
from pathlib import Path

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QFontMetricsF
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from layout import NarrowLayout
from motion import EaseOutFade, Visibility
from veil import VeilOverlay

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from local.music_surface_window import MusicSurfaceWindow


def check(variant, width, mode, dj):
    layout = NarrowLayout(width, 640, float(variant == "B"), dj_available=dj)
    errors = []
    viewport = QRectF(20, 20, width - 40, 600)
    for alpha in (0, .1, .5, .9, 1):
        pose = NarrowLayout(width, 640, alpha)
        if (pose.viewport != viewport or pose.lyric_font != layout.lyric_font
                or pose.lyric_row != layout.lyric_row
                or pose.lyric_step != layout.lyric_step
                or pose.lyric_anchor != layout.lyric_anchor
                or pose.list_top != layout.list_top
                or pose.list_step(8) != layout.list_step(8)):
            errors.append("content geometry changes with visibility")
    if abs(layout.lyric_anchor - 320) > .5:
        errors.append("current lyric cap-height centre")
    if not 26 <= layout.lyric_font <= 38:
        errors.append("immersive lyric font outside 26–38px")
    if width == 360 and abs(layout.lyric_font - 31.5) > .001:
        errors.append("360px lyrics lost the 31.5px immersive size")
    if layout.lyric_row <= layout.lyric_font or layout.lyric_step <= layout.lyric_row:
        errors.append("lyric rows do not leave space around the large glyphs")
    if layout.list_heading != 0 or layout.list_top != viewport.top():
        errors.append("list heading or top")
    if abs(layout.list_top + layout.list_step(8) * 8 - viewport.bottom()) > .001:
        errors.append("list slots do not fill the viewport")
    font = QFont()
    font.setFamilies(list(MusicSurfaceWindow.SURFACE_FONT_FAMILIES))
    font.setPixelSize(round(layout.lyric_font))
    font.setWeight(QFont.Weight.Bold)
    # Fallback fonts reserve ascent/descent beyond their visible Chinese ink.
    # Check actual glyph bounds against the rhythm instead of that line box.
    ink = QFontMetricsF(font).tightBoundingRect("把心事交给晚风")
    if ink.height() > layout.lyric_step:
        errors.append("lyric glyphs exceed the responsive baseline rhythm")

    header = QRectF(0, 0, width, VeilOverlay.TOP_END)
    bottom = QRectF(0, layout.height - VeilOverlay.BOTTOM_HEIGHT,
                   width, VeilOverlay.BOTTOM_HEIGHT)
    top_controls = [layout.back, layout.favorite, *layout.top]
    if layout.dj.isValid():
        top_controls.append(layout.dj)
    header_content = [layout.cover, layout.text]
    if (layout.cover.width() < 84 or layout.cover.width() != layout.cover.height()
            or (width == 360 and abs(layout.cover.width() - 96) > .001)):
        errors.append("header cover is no longer a full-size square")
    if layout.cover.top() <= max(rect.bottom() for rect in [layout.back, *layout.top]):
        errors.append("header cover is not below the top buttons")
    if (layout.text.left() <= layout.cover.right()
            or layout.text.height() != layout.cover.height()
            or layout.text.top() != layout.cover.top()):
        errors.append("header title and artist have no full-height adjacent column")
    if max(layout.cover.bottom(), layout.text.bottom()) > VeilOverlay.TOP_FEATHER_START:
        errors.append("header extends into the top feather")
    transport = [layout.previous, layout.play, layout.next]
    if any(not header.contains(rect) for rect in top_controls + header_content):
        errors.append("header extends outside the top veil band")
    if any(not bottom.contains(rect) for rect in transport):
        errors.append("control extends outside the bottom veil band")
    if any(rect.width() != 44 or rect.height() != 44 for rect in transport):
        errors.append("transport hit target is not 44x44")
    if any(layout.transport_icon(rect) != rect for rect in transport):
        errors.append("transport icon and hit target differ")
    if layout.transport_panel.height() != 44:
        errors.append("transport row height")
    if any(rect.height() != layout.button_size for rect in top_controls):
        errors.append("top row height")
    if not all(a.right() < b.left() for a, b in zip(transport, transport[1:])):
        errors.append("transport order or spacing")
    ordered_top = sorted(top_controls, key=lambda rect: rect.left())
    if not all(a.right() <= b.left() + .001 for a, b in zip(ordered_top, ordered_top[1:])):
        errors.append("top row order or spacing")
    # Overlap with the content is intentional: chrome is above the veil.
    for first, second in itertools.combinations(top_controls + header_content + transport, 2):
        if first.intersects(second):
            errors.append("chrome elements overlap")
    if variant == "A":
        visibility = Visibility(False)
        visibility.advance(0, True, True, mode == "playlist")
        if visibility.opacity.value(1) != 0:
            errors.append("A chrome is visible")
    if NarrowLayout(width, 900).lyric_anchor != 450:
        errors.append("900px current lyric centre")
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
    if abs(visibility.opacity.value(3.16) - .875) > 1e-9:
        errors.append("320ms cubic ease-out")
    if not visibility.settled(3.32) or abs(visibility.opacity.value(3.32) - 1) > 1e-9:
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
    if abs(visibility.opacity.value(7.52)) > 1e-9:
        errors.append("320ms fade-out")
    utility = EaseOutFade(1)
    utility.retarget(0, 0, .18)
    if not (0 < utility.value(.09) < .5 and utility.value(.18) == 0):
        errors.append("utility motion")
    return errors


def check_capsules(width, dj, library):
    layout = NarrowLayout(width, 640, library_available=library, dj_available=dj)
    errors = []
    rects = layout._top_rects
    bounds = QRectF(8, 8, width - 16, 56)
    if "translation" not in rects or len(layout.top) != 5:
        errors.append("translation missing from five-slot chrome")
    if ("library" in rects) != library:
        errors.append("Back ignores owned-playback gate")
    if library and layout.back != QRectF(16, 16, layout.button_size, layout.button_size):
        errors.append("Back is not independent at (16,16)")
    if layout.capsule_b.right() != width - 16:
        errors.append("capsule B is not right aligned")
    if layout.capsule_a.right() + 8 > layout.capsule_b.left() + .001:
        errors.append("capsule frames overlap")
    if any(not bounds.contains(rect) for rect in [*rects.values(), layout.capsule_a, layout.capsule_b]):
        errors.append("chrome is outside the width budget")
    if any(first.intersects(second) for first, second in itertools.combinations(rects.values(), 2)):
        errors.append("chrome slots overlap")
    b_keys = ("audio_output", "content_layout", "playback_mode", "translation", "return")
    if any(not layout.capsule_b.contains(rects[key]) for key in b_keys):
        errors.append("B lost a required slot")
    if any(rects[first].right() > rects[second].left()
           for first, second in zip(b_keys, b_keys[1:])):
        errors.append("B order is not Device/Layout/PlayOrder/Translation/WindowSize")
    if any(not layout.capsule_a.contains(rect) for rect in layout.collection_slots):
        errors.append("collection slots escaped capsule A")
    if layout.dj.isValid() and not layout.capsule_a.contains(layout.dj):
        errors.append("DJ does not fit capsule A")
    without_back = NarrowLayout(width, 640, library_available=False, dj_available=dj)
    if (layout.capsule_a != without_back.capsule_a
            or layout.capsule_b != without_back.capsule_b):
        errors.append("hiding Back shifts capsules")
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
    print(f"{'FAIL' if errors else 'PASS'} timing: {', '.join(errors) or 'shared 320ms curve, 1200ms idle and continuous reversal'}")
    for width, dj, library in itertools.product((320, 336, 360, 480, 640), (False, True), (False, True)):
        errors = check_capsules(width, dj, library)
        failures += bool(errors)
        print(f"{'FAIL' if errors else 'PASS'} capsules {width} dj={dj} back={library}: "
              f"{', '.join(errors) or 'two frames, five B slots, no overlap or overflow'}")
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
