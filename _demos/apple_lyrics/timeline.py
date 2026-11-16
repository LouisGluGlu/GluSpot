"""Sample clock for the Apple-style lyric demo.

The sentences are original. Nothing here is a published song, and nothing
talks to a player. Word times use the same idea as ``TimedLyricUnit``:
each piece has a start and an end, and the wipe walks that interval.

Scrolling does not follow the end of the current sentence. The visual
emphasis moves at ``next_first_word - lead``. A long rest therefore waits,
and the wipe of a syllable keeps using its own start and end.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType


LOOP_END = 16.0

# Ratios taken from the video notes. One unread slider keeps them in proportion.
REF_UNREAD = 0.55
REF_ADJACENT = 0.25
REF_FAR = 0.10
REF_TRANSLATION = 0.45

# Adopted demo default: neighboring lines 88%, blur 2.2, lead and scroll 350 ms.
APPLE_KNOBS_DEFAULT_PRESET = "current"


@dataclass(frozen=True)
class AppleKnobs:
    block_gap_ratio: float = 1.75
    translation_gap_ratio: float = 0.25
    other_scale: float = 0.88
    blur_px: float = 2.2
    unread_alpha: float = 0.55
    feather_chars: float = 0.75
    scroll_ms: float = 350.0
    lead_ms: float = 350.0

    @classmethod
    def from_preset(cls, name: str | None = None) -> AppleKnobs:
        """Return a frozen sample preset without changing dataclass defaults."""
        return APPLE_KNOB_PRESETS[APPLE_KNOBS_DEFAULT_PRESET if name is None else name]

    def lead_seconds(self) -> float:
        return max(0.0, self.lead_ms / 1000.0)

    def scroll_seconds(self) -> float:
        return max(0.0, self.scroll_ms / 1000.0)

    def adjacent_alpha(self) -> float:
        return _scaled_alpha(self.unread_alpha, REF_ADJACENT)

    def far_alpha(self) -> float:
        return _scaled_alpha(self.unread_alpha, REF_FAR)

    def translation_alpha(self) -> float:
        return _scaled_alpha(self.unread_alpha, REF_TRANSLATION)


APPLE_KNOB_PRESETS = MappingProxyType({
    "current": AppleKnobs(),
    "soft_close": AppleKnobs(
        block_gap_ratio=1.20, translation_gap_ratio=0.12, other_scale=0.95,
        blur_px=1.0, unread_alpha=0.68, feather_chars=1.25,
        scroll_ms=450.0, lead_ms=300.0,
    ),
    "dramatic": AppleKnobs(
        block_gap_ratio=2.35, translation_gap_ratio=0.40, other_scale=0.80,
        blur_px=5.5, unread_alpha=0.42, feather_chars=0.40,
        scroll_ms=480.0, lead_ms=480.0,
    ),
})


def _scaled_alpha(unread: float, reference: float) -> float:
    return max(0.02, min(1.0, float(unread) * reference / REF_UNREAD))


@dataclass(frozen=True)
class Unit:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Line:
    primary: str
    translation: str
    start: float
    end: float
    units: tuple[Unit, ...] = ()
    #: ``unit`` matches a real per-word source. ``line`` is the even fallback.
    timing: str = "unit"

    def spans(self) -> tuple[Unit, ...]:
        if self.units:
            return self.units
        parts = split_graphemes(self.primary)
        count = max(1, len(parts))
        duration = max(0.05, self.end - self.start)
        step = duration / count
        return tuple(
            Unit(part, self.start + index * step, self.start + (index + 1) * step)
            for index, part in enumerate(parts)
        )

    def first_word_start(self) -> float:
        spans = self.spans()
        return spans[0].start if spans else self.start


def split_graphemes(text: str) -> tuple[str, ...]:
    """One CJK character, or one Latin word. Spaces are not their own beats."""
    parts: list[str] = []
    latin = ""
    for character in text:
        if _is_cjk(character):
            if latin:
                parts.append(latin)
                latin = ""
            parts.append(character)
        elif character.isspace():
            if latin:
                parts.append(latin)
                latin = ""
        else:
            latin += character
    if latin:
        parts.append(latin)
    return tuple(parts)


def _is_cjk(character: str) -> bool:
    code = ord(character)
    return (
        0x2E80 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


# The long last syllable of line 0 is still moving when line 1's scroll starts,
# and it is still moving after line 1 has become the large line. Line 2 has no
# word times on purpose. The rest after line 1 is long, so a scroll-on-ending
# rule would leave early; the lead rule must not.
LINES: tuple[Line, ...] = (
    Line(
        "夜色把窗照得慢",
        "The night keeps the window slow",
        0.40,
        4.55,
        (
            Unit("夜", 0.40, 0.68),
            Unit("色", 0.68, 0.98),
            Unit("把", 0.98, 1.26),
            Unit("窗", 1.26, 1.58),
            Unit("照", 1.58, 1.90),
            Unit("得", 1.90, 2.20),
            Unit("慢", 2.20, 4.55),
        ),
        "unit",
    ),
    Line(
        "风从谱边走过去",
        "Wind walks along the score",
        3.80,
        6.45,
        (
            Unit("风", 3.80, 4.10),
            Unit("从", 4.10, 4.40),
            Unit("谱", 4.40, 4.80),
            Unit("边", 4.80, 5.15),
            Unit("走", 5.15, 5.70),
            Unit("过", 5.70, 6.05),
            Unit("去", 6.05, 6.45),
        ),
        "unit",
    ),
    Line(
        "桥下的灯一盏盏亮",
        "Lights under the bridge come on",
        9.70,
        12.40,
        (),
        "line",
    ),
    Line(
        "把今天折进口袋",
        "I fold today into a pocket",
        12.60,
        15.30,
        (
            Unit("把", 12.60, 12.90),
            Unit("今", 12.90, 13.15),
            Unit("天", 13.15, 13.50),
            Unit("折", 13.50, 13.85),
            Unit("进", 13.85, 14.15),
            Unit("口", 14.15, 14.55),
            Unit("袋", 14.55, 15.30),
        ),
        "unit",
    ),
)


def visual_target(t: float, lead: float) -> int:
    """Line that should already look current: its first word is within ``lead``."""
    target = 0
    moment = float(t)
    lead = max(0.0, float(lead))
    for index, line in enumerate(LINES):
        if moment + 1e-9 >= line.first_word_start() - lead:
            target = index
    return target


def audio_line_index(t: float) -> int:
    """Line whose first word has actually started. No visual lead."""
    return visual_target(t, 0.0)


def smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def displayed_focus(t: float, lead: float, scroll_s: float) -> float:
    """One shared focus. Fractional values are the rigid scroll in progress.

    The scroll begins at ``first_word - lead`` and lasts ``scroll_s``. It does
    not start when the previous sentence's last word ends.
    """
    target = visual_target(t, lead)
    if target <= 0:
        return 0.0
    begin = LINES[target].first_word_start() - max(0.0, lead)
    if scroll_s <= 1e-6 or t >= begin + scroll_s:
        return float(target)
    if t <= begin:
        return float(target - 1)
    eased = smoothstep((t - begin) / scroll_s)
    return (target - 1) + eased


def active_local(line: Line, t: float) -> tuple[Unit | None, float | None]:
    """Syllable under the playhead, and 0..1 progress inside it."""
    for unit in line.spans():
        if unit.start <= t < unit.end:
            span = max(1e-6, unit.end - unit.start)
            return unit, (t - unit.start) / span
    return None, None


def coverage_for_spans(
    spans: tuple[Unit, ...] | list[Unit],
    advances: list[tuple[float, float]],
    t: float,
    feather: float,
) -> list[float]:
    """Per-pixel read amount from 0 to 1.

    A finished syllable stays at 1. A syllable that has not started stays at 0,
    so the feather cannot light the next word early. Inside the active
    syllable the edge moves linearly and is soft across ``feather`` pixels.
    """
    if not spans or not advances:
        return []
    width = max(1, int(math.ceil(advances[-1][1])) + 1)
    output = [0.0] * width
    feather = max(0.0, float(feather))
    for unit, (x0, x1) in zip(spans, advances):
        left = max(0, int(math.floor(x0)))
        right = min(width, int(math.ceil(x1)))
        if right <= left:
            continue
        if t >= unit.end - 1e-4:
            for index in range(left, right):
                output[index] = 1.0
            continue
        if t <= unit.start + 1e-6:
            continue
        local = (t - unit.start) / max(1e-6, unit.end - unit.start)
        if feather <= 0.5:
            front = x0 + (x1 - x0) * local
            for index in range(left, right):
                output[index] = 1.0 if index + 0.5 < front else 0.0
            continue
        front = x0 + (x1 - x0 + feather) * local
        for index in range(left, right):
            output[index] = max(0.0, min(1.0, (front - (index + 0.5)) / feather))
    return output


def wiping_label(t: float) -> str:
    found: list[str] = []
    for line in LINES:
        unit, local = active_local(line, t)
        if unit is not None and local is not None:
            found.append(f"「{unit.text}」{round(local * 100)}%")
    if not found:
        return "没有字在擦"
    return "正在擦 " + "、".join(found)


def describe(t: float, style: str, knobs: AppleKnobs) -> str:
    if style == "apple":
        lead = knobs.lead_seconds()
        scroll = knobs.scroll_seconds()
        focus = displayed_focus(t, lead, scroll)
        target = visual_target(t, lead)
        name = "仿 Apple"
    else:
        focus = displayed_focus(t, 0.0, 0.42)
        target = audio_line_index(t)
        name = "现在的样式（唱到才滚，不提前）"
    scrolling = abs(focus - round(focus)) > 0.04
    if style == "apple" and scrolling:
        upcoming = LINES[target]
        if t < upcoming.first_word_start():
            motion = (
                f"提前滚向第 {target + 1} 句，它还要 "
                f"{upcoming.first_word_start() - t:.2f}s 才开唱"
            )
        else:
            motion = f"滚向第 {target + 1} 句"
    else:
        motion = f"强调第 {int(round(focus)) + 1} 句"
    gap_note = ""
    if style == "apple":
        nxt = target + 1
        if nxt < len(LINES):
            wait = LINES[nxt].first_word_start() - knobs.lead_seconds() - t
            if wait > 0.8 and not scrolling:
                gap_note = f" · 离下一次滚动还有 {wait:.2f}s（不在这句唱完时滚）"
    return f"{name} · {t:.2f}s · {motion} · {wiping_label(t)}{gap_note}"


def loop_time(t: float) -> float:
    if not math.isfinite(t):
        return 0.0
    return t % LOOP_END
