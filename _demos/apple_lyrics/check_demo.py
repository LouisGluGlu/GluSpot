"""Offscreen checks for the Apple-style lyric sample. No player is started."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from timeline import (  # noqa: E402
    LINES,
    AppleKnobs,
    active_local,
    coverage_for_spans,
    displayed_focus,
    visual_target,
)


def fake_advances(spans, width=20.0):
    cursor = 0.0
    advances = []
    for _unit in spans:
        advances.append((cursor, cursor + width))
        cursor += width
    return advances


def mean(values) -> float:
    return sum(values) / max(1, len(values))


def check_timing() -> None:
    lead = 0.35
    scroll = 0.35
    line0, line1, line2 = LINES[0], LINES[1], LINES[2]
    scroll_at = line1.first_word_start() - lead
    unit, local = active_local(line0, scroll_at)
    assert unit is not None and unit.text == "慢", unit
    assert local is not None and 0.50 <= local <= 0.75, local
    assert visual_target(scroll_at - 0.02, lead) == 0
    assert visual_target(scroll_at + 0.02, lead) == 1
    # A long rest must not scroll when the previous sentence ends.
    assert line1.end < 7.0 < line2.first_word_start() - lead
    assert displayed_focus(6.60, lead, scroll) == 1.0
    assert displayed_focus(line2.first_word_start() - lead - 0.05, lead, scroll) == 1.0
    assert displayed_focus(line2.first_word_start() - lead, lead, scroll) == 1.0
    assert displayed_focus(line2.first_word_start(), lead, scroll) == 2.0
    # Emphasis can move early. The next sentence is still unread.
    early = line1.first_word_start() - 0.08
    assert displayed_focus(early, lead, scroll) > 0.5
    assert active_local(line1, early) == (None, None)
    # Lead 0 waits for the first word itself.
    assert visual_target(scroll_at, 0.0) == 0
    spans = line0.spans()
    coverage = coverage_for_spans(spans, fake_advances(spans), 3.35, feather=15)
    slow = coverage[6 * 20 : 7 * 20]
    assert mean(slow[:8]) > mean(slow[-8:]) > 0.0
    assert any(0.15 < value < 0.85 for value in slow)
    done = coverage_for_spans(spans, fake_advances(spans), line0.end, feather=15)
    assert all(value == 1.0 for value in done[6 * 20 : 7 * 20])
    again = coverage_for_spans(spans, fake_advances(spans), line0.end, feather=15)
    assert done == again
    waiting = coverage_for_spans(line1.spans(), fake_advances(line1.spans()), early, feather=15)
    assert all(value == 0.0 for value in waiting)
    # Even fallback: halfway through the 5th character, earlier ones are done.
    parts = line2.spans()
    assert line2.timing == "line" and len(parts) == len(line2.primary)
    step = parts[1].start - parts[0].start
    moment = parts[4].start + step * 0.5
    fallback = coverage_for_spans(parts, fake_advances(parts), moment, feather=8)
    assert all(value == 1.0 for value in fallback[3 * 20 : 4 * 20])
    fifth = fallback[4 * 20 : 5 * 20]
    assert mean(fifth[:8]) > mean(fifth[-8:])
    assert all(value == 0.0 for value in fallback[5 * 20 : 6 * 20])
    narrow = coverage_for_spans(spans, fake_advances(spans), 3.35, feather=4)
    wide = coverage_for_spans(spans, fake_advances(spans), 3.35, feather=18)
    def band(values):
        return sum(1 for value in values if 0.15 < value < 0.85)
    assert band(wide[6 * 20 : 7 * 20]) > band(narrow[6 * 20 : 7 * 20])
    print(
        f"提前滚动 {scroll_at:.2f}s，此时「慢」{local * 100:.1f}%；"
        f"第 2 句 {line1.end:.2f}s 唱完，第 3 句 {line2.first_word_start():.2f}s 才开唱，"
        f"滚动到 {line2.first_word_start() - lead:.2f}s 才开始"
    )


def check_pixels() -> None:
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from paint import CURRENT_LIFT_PX, _raster_line, make_font, render_scene
    from paint import Scene

    app = QApplication.instance() or QApplication([])
    assert app is not None
    scene = Scene(style="apple", theme="dark", t=3.35)
    dark = render_scene(900, 640, scene)
    light = render_scene(900, 640, Scene(style="apple", theme="light", t=3.35))
    current = render_scene(900, 640, Scene(style="current", theme="dark", t=3.72))
    early = render_scene(900, 640, Scene(style="apple", theme="dark", t=3.72))

    def luminance(image, x, y, size=24) -> float:
        total = 0.0
        count = 0
        for row in range(y, y + size):
            for column in range(x, x + size):
                color = image.pixelColor(column, row)
                total += 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()
                count += 1
        return total / count

    assert luminance(dark, 860, 12) < 80, luminance(dark, 860, 12)
    assert luminance(light, 860, 12) > 240, luminance(light, 860, 12)
    different = 0
    for y in range(80, 560, 8):
        for x in range(60, 700, 8):
            if dark.pixel(x, y) != current.pixel(x, y):
                different += 1
    assert different > 200, different
    assert early.pixel(200, 300) != current.pixel(200, 300) or different > 200
    font = make_font(40, QFont.Weight.Bold)
    plain = _raster_line(LINES[0].spans(), font, 5.0, (22, 22, 24), 1.0, 0.55, 0.75, 0.0)
    lifted = _raster_line(LINES[0].spans(), font, 5.0, (22, 22, 24), 1.0, 0.55, 0.75, CURRENT_LIFT_PX)
    assert lifted.height() > plain.height()
    dark_ink = None
    for y in range(plain.height()):
        for x in range(min(30, plain.width())):
            color = plain.pixelColor(x, y)
            if color.alpha() > 180:
                dark_ink = color
                break
        if dark_ink is not None:
            break
    assert dark_ink is not None
    assert dark_ink.red() < 40 and dark_ink.green() < 40 and dark_ink.blue() < 40
    # Apple blocks reserve no lift. The scene path passes 0 for that style.
    apple_line = _raster_line(LINES[1].spans(), font, 3.72, (255, 255, 255), 0.55, 0.55, 0.75, 0.0)
    assert all(apple_line.pixelColor(x, y).alpha() < 40 for y in range(2) for x in range(apple_line.width()))
    knobs = AppleKnobs()
    assert knobs.lead_ms == 350 and knobs.scroll_ms == 350
    assert abs(knobs.adjacent_alpha() - 0.25) < 1e-6
    assert abs(knobs.far_alpha() - 0.10) < 1e-6
    assert abs(knobs.translation_alpha() - 0.45) < 1e-6
    print(f"深色角亮度 {luminance(dark, 860, 12):.1f}，白底角亮度 {luminance(light, 860, 12):.1f}，样式像素差 {different}")


def main() -> None:
    check_timing()
    check_pixels()
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (HERE / "demo.py", HERE / "paint.py", HERE / "timeline.py", HERE / "grab.py")
    )
    assert "4422" not in source
    assert "netease-relaunch" not in source
    print("check_demo ok")


if __name__ == "__main__":
    main()
