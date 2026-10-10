"""Cantonese secondary lyrics, prepared once before rendering.

Language evidence is conservative: explicit metadata, supplied Jyutping, or
multiple distinctive Cantonese words. Artist identity alone is never evidence.
"""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
import re
from typing import Sequence

from api.models import TimedLyricLine

_LANGUAGE_RE = re.compile(r"(?:\[language\s*:\s*(?:yue(?:[-_]\w+)?|zh[-_]hk|cantonese|粤语|粵語)\s*\]|粤语|粵語|cantonese)", re.I)
_PHONETIC_LABEL_RE = re.compile(r"^\s*(?:粤语音译|粵語音譯|音译|音譯|粤拼|粵拼|jyutping|romanization)\s*[:：]\s*", re.I)
_CANTONESE_PHONETIC_LABEL_RE = re.compile(r"^\s*(?:粤语音译|粵語音譯|粤拼|粵拼|jyutping)\s*[:：]", re.I | re.M)
_TRANSLATION_LABEL_RE = re.compile(r"^\s*(?:粤语译文|粵語譯文|翻译|译文|中文翻译|translation|translated)\s*[:：]\s*", re.I)
_JYUTPING_RE = re.compile(r"[a-z]+[1-6]", re.I)
_DIALECT_CHARS = frozenset("嘅唔佢啲嘢咁冇喺睇嚟咩嗰乜㗎")
_HAN_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_SPEAKER_RE = re.compile(r"^\s*[^:：\n]{1,24}[:：]\s*")


@lru_cache(maxsize=1024)
def _valid_jyutping_syllable(token: str) -> bool:
    if not _JYUTPING_RE.fullmatch(token):
        return False
    try:
        from ToJyutping.Jyutping import Jyutping
        Jyutping(token.casefold())
    except (ImportError, OSError, ValueError):
        return False
    return True


def is_jyutping(text: str) -> bool:
    value = _PHONETIC_LABEL_RE.sub("", str(text or ""))
    if _HAN_RE.search(value):
        return False
    tokens = re.findall(r"[A-Za-z]+[0-9]*", value)
    return bool(tokens) and all(_valid_jyutping_syllable(token) for token in tokens)


def has_cantonese_evidence(text: str, hint: str = "") -> bool:
    value = str(text or "")
    if (_LANGUAGE_RE.search(hint) or _LANGUAGE_RE.search(value) or _CANTONESE_PHONETIC_LABEL_RE.search(value)
            or re.search(r"(?:^|\s)(?:yue(?:[-_]\w+)?|zh[-_]hk)(?:$|\s)", hint, re.I)):
        return True
    if any(is_jyutping(part) for part in value.splitlines()):
        return True
    return len(_DIALECT_CHARS.intersection(value)) >= 2


@lru_cache(maxsize=2048)
def cantonese_phonetics(text: str) -> str:
    if not _HAN_RE.search(text):
        return ""
    try:
        from ToJyutping import get_jyutping_text
        result = get_jyutping_text(text)
    except (ImportError, OSError, ValueError):
        return ""
    # Keep parentheses around sung asides so their phonetics use the existing
    # parenthetical popout together with the original aside.
    return result.replace("（", "(").replace("）", ")").strip()


def group_secondary_source_rows(rows: Sequence[TimedLyricLine]) -> tuple[TimedLyricLine, ...]:
    """Rejoin equal-time companions while preserving independently timed voices."""
    grouped: list[TimedLyricLine] = []
    for row in rows:
        # Disk caches separate an original and its companion at equal times.
        # Two singer-prefixed originals or independently timed originals are
        # separate voices and must never be collapsed by translation handling.
        if (grouped and abs(grouped[-1].time_seconds - row.time_seconds) <= 0.0005
                and not row.units and (not _SPEAKER_RE.match(row.text)
                    or _PHONETIC_LABEL_RE.match(row.text) or _TRANSLATION_LABEL_RE.match(row.text))):
            anchor = grouped[-1]
            grouped[-1] = replace(anchor, text=anchor.text + "\n" + row.text)
        else:
            grouped.append(row)
    return tuple(grouped)


def group_cantonese_plain_lines(lines: Sequence[str], language_hint: str = "") -> tuple[str, ...]:
    """Keep explicit untimed companions in their original paragraph.

    They must not consume a synthetic three-second placeholder of their own
    when a generated plain cache is reloaded.
    """
    if not has_cantonese_evidence("\n".join(lines), language_hint):
        return tuple(lines)
    grouped: list[str] = []
    for line in lines:
        if (grouped and _HAN_RE.search(grouped[-1].split("\n", 1)[0])
                and (_PHONETIC_LABEL_RE.match(line) or _TRANSLATION_LABEL_RE.match(line))):
            grouped[-1] += "\n" + line
        else:
            grouped.append(line)
    return tuple(grouped)


def cantonese_secondary_rows(
    rows: Sequence[TimedLyricLine], *, priority: str, language_hint: str = "", generate_missing: bool = True,
) -> tuple[TimedLyricLine, ...]:
    raw_text = "\n".join(row.text for row in rows)
    if not has_cantonese_evidence(raw_text, language_hint):
        return tuple(rows)
    grouped = group_secondary_source_rows(rows)
    result: list[TimedLyricLine] = []
    for row in grouped:
        parts = [part.strip() for part in row.text.splitlines() if part.strip()]
        if not parts:
            continue
        original = parts[0]
        # Intro metadata is handled by presentation and has no phonetics.
        if not _HAN_RE.search(original) or re.match(r"^(?:作词|作曲|编曲|制作|翻译|译者)\s*[:：]", original):
            result.append(row)
            continue
        phonetic = next((_PHONETIC_LABEL_RE.sub("", part) for part in parts[1:]
                         if _PHONETIC_LABEL_RE.match(part) or is_jyutping(part)), "")
        translation = next((_TRANSLATION_LABEL_RE.sub("", part) for part in parts[1:]
                            if not _PHONETIC_LABEL_RE.match(part) and not is_jyutping(part)
                            and (not _SPEAKER_RE.match(part) or _TRANSLATION_LABEL_RE.match(part))), "")
        if generate_missing and not phonetic and (priority == "phonetic_first" or not translation):
            phonetic = cantonese_phonetics(_SPEAKER_RE.sub("", original))
        if priority == "translation_first" and translation:
            companion = "粤语译文：" + translation
        elif phonetic:
            companion = "粤语音译：" + phonetic
        elif translation:
            companion = "粤语译文：" + translation
        else:
            result.append(row)
            continue
        # Retain other singer subrows; they are originals, not alternatives.
        voices = [part for part in parts[1:] if _SPEAKER_RE.match(part)
                  and not _PHONETIC_LABEL_RE.match(part) and not _TRANSLATION_LABEL_RE.match(part)]
        result.append(replace(row, text="\n".join((original, companion, *voices))))
    return tuple(result)
