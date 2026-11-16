"""Font-shaped minimum lyric width shared by both song-page surfaces."""
from __future__ import annotations

from functools import lru_cache
import math

from PySide6.QtGui import QFont, QTextLayout, QTextOption

from local.music_lyric_reading import apply_cjk_letter_spacing


LYRIC_MIN_CJK = 5
LYRIC_MIN_LATIN_WORDS = 3
CJK_PROBE = "音乐正在播放"
LATIN_PROBE = "we sing on"


def _advance(text: str, font: QFont) -> float:
    """Use the renderer's shaping and CJK spacing, including fallback fonts."""
    layout = QTextLayout(text, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.NoWrap)
    layout.setTextOption(option)
    apply_cjk_letter_spacing(layout, text, font)
    layout.beginLayout()
    line = layout.createLine()
    line.setLineWidth(1e9)
    layout.endLayout()
    width = line.naturalTextWidth()
    assert math.isfinite(width) and width > 0., "Degenerate lyric font"
    return width


@lru_cache(maxsize=128)
def _probes(font_description: str, dpr: float, families: tuple[str, ...]):
    # DPR belongs in the cache identity even though widths use logical units.
    assert math.isfinite(dpr) and dpr > 0., "Invalid lyric device scale"
    font = QFont()
    assert font.fromString(font_description), "Invalid lyric font description"
    font.setFamilies(list(families))
    assert font.pixelSize() > 0 or font.pointSizeF() > 0., "Invalid lyric font size"
    cjk = _advance(CJK_PROBE, font) / len(CJK_PROBE)
    space = _advance(" ", font)
    word_count = len(LATIN_PROBE.split())
    latin = (_advance(LATIN_PROBE, font) - (word_count - 1) * space) / word_count
    assert latin > 0., "Degenerate Latin lyric font"
    return cjk, latin, space


def _measure(font: QFont, dpr: float):
    return _probes(font.toString(), float(dpr), tuple(font.families()))


def cjk_unit(font: QFont, *, dpr: float = 1.) -> float:
    return _measure(font, dpr)[0]


def latin_unit(font: QFont, *, dpr: float = 1.) -> float:
    return _measure(font, dpr)[1]


def required_width(font: QFont, *, dpr: float = 1.) -> float:
    cjk, latin, space = _measure(font, dpr)
    return max(LYRIC_MIN_CJK * cjk, LYRIC_MIN_LATIN_WORDS * latin + 2 * space)


def fits(column_width: float, font: QFont, *, previous: bool | None,
         dpr: float = 1.) -> bool:
    """Previous means previously narrow; widen needs one more glyph of room."""
    threshold = required_width(font, dpr=dpr)
    if previous is not None:
        threshold += cjk_unit(font, dpr=dpr) * (.5 if previous else -.5)
    return float(column_width) >= threshold
