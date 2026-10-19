"""Display-only preparation for the large music page's line-timed lyrics.

Neither the provider's lyrics nor their source timestamps are rewritten. A
translation is hidden only when a bilingual boundary is reasonably explicit;
code-switched song lines are otherwise left alone.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Sequence

from api.lyrics import is_structured_lyric_metadata
from api.lyric_metadata import is_explicit_lyric_credit as _is_credit_line


INTRO_COUNTDOWN_TEXT = "· · ·"
INTRO_COUNTDOWN_MIN_START_SECONDS = 5.0

# Legacy edge annotations remain explicit fields, rather than a general rule
# that treats every short colon-prefixed lyric as disposable metadata.
_EDGE_NOTE_LABELS = frozenset(("节目", "片头", "旁白", "备注", "注记", "说明", "介绍"))
_STAFF_FIELD_RE = re.compile(r"^([^:：=\n]{1,120})[:：=]\s*(\S.*)$")
_SENTENCE_FIELD_RE = re.compile(
    r"^(?:我|你|他|她|它|我们|你们|他们|她们).+|"
    r"(?:鼓起|勇气|记得|记住|想起|告诉|因为|所以|如果|但是|曾经|永远|说道|说过|说着|说$)",
)
_FIELD_PUNCTUATION_RE = re.compile(r"[，。！？；…!?;{}\[\]()（）]")
# These nouns are also plausible sung subjects. An exact repeated row is
# lyric evidence for them; concrete roles such as Producer/作词 remain credits
# even when the source repeats its production block.
_AMBIGUOUS_CREDIT_LABELS = frozenset(("music", "master", "strings"))
_VERSE_TAG_RE = re.compile(
    r"[\[【［(（]\s*verse\s+\d+(?:\s*[:：]\s*[^\]】］)）]+)?\s*[\]】］)）]",
    re.I,
)
# A section heading is not a backing vocal. Match the whole subrow only;
# parentheses containing real sung text must still reach the popout layer.
_SECTION_NAME = r"(?:verse|(?:pre|post)[-\s]*chorus|chorus|bridge|intro|outro|refrain|hook|interlude)"
_SECTION_LABEL = rf"{_SECTION_NAME}(?:\s+\d+)?\s*[:：]?"
_SECTION_ONLY_RE = re.compile(
    rf"\s*(?:\(\s*{_SECTION_LABEL}\s*\)|（\s*{_SECTION_LABEL}\s*）|"
    rf"\[\s*{_SECTION_LABEL}\s*\]|［\s*{_SECTION_LABEL}\s*］|"
    rf"【\s*{_SECTION_LABEL}\s*】|{_SECTION_NAME}(?:\s+\d+)?\s*[:：])\s*",
    re.I,
)
_TRANSLATION_LABEL_RE = re.compile(r"^\s*(?:翻译|译文|中文翻译|粤语译文|粵語譯文|粤语音译|粵語音譯|音译|音譯|粤拼|粵拼|jyutping|translation|translated|romanization)\s*[:：]\s*", re.I)
# Display-only provenance for separately timed parentheses attached to an
# earlier source row. Never add this label to provider data or painted ink.
_PARENTHETICAL_LABEL_RE = re.compile(r"^\s*括号词\s*[:：]\s*")
_INLINE_TRANSLATION_LABEL_RE = re.compile(
    r"(?P<original>.+?)\s+(?P<translation>(?:翻译|译文|中文翻译|translation|translated)\s*[:：]\s*\S.*)$",
    re.I,
)
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
_HAN_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_HANGUL_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")
_INLINE_LATIN_HAN_RE = re.compile(
    r"(?P<latin>[A-Za-z][A-Za-z0-9'’.,!?;:()\-\s]*\s+"
    r"[A-Za-z][A-Za-z0-9'’.,!?;:()\-\s]*)(?P<han>[\u3400-\u9fff\uf900-\ufaff]{2,})"
)
_INLINE_SINGLE_WORD_TRANSLATION_RE = re.compile(
    r"^(?P<latin>[A-Za-z][A-Za-z'’\-]{3,}[.!?]?)(?P<han>[\u3400-\u9fff\uf900-\ufaff]{2,}[，。！？]?)$"
)
_SINGER_JOIN_RE = re.compile(r"\s*(?:[+＋＆&、/,，]|与|和)\s*")
_POSSIBLE_SINGER_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]{2,6}\Z")
_PAREN_CONTENT_RE = re.compile(r"\(([^()]*)\)|（([^（）]*)）")
_PAREN_METADATA_RE = re.compile(
    r"^(?:feat(?:uring)?\.?|ft\.?|prod(?:uced)?\.?|remix|live|instrumental|"
    r"作词|作曲|编曲|演唱|翻译|译者)(?:\s|[:：.]|$)",
    re.I,
)


@dataclass(frozen=True)
class SingerLyricRow:
    """One cleaned timeline row, without changing its original time or index."""

    source_index: int
    start_seconds: float
    original_text: str
    body_text: str
    singer: str | None = None
    alignment: str = "left"
    dual_group_index: int | None = None


@dataclass(frozen=True)
class DualLyricGroup:
    """Two adjacent singer-marked rows that share a visual group, not a clock."""

    first_index: int
    second_index: int
    first_start_seconds: float
    second_start_seconds: float


@dataclass(frozen=True)
class DuetLyricPresentation:
    rows: tuple[SingerLyricRow, ...]
    groups: tuple[DualLyricGroup, ...]


@dataclass(frozen=True)
class DisplayLyricLayers:
    """Display-only sublayers; ``original_text`` remains available for timing."""

    original_text: str
    original_primary_text: str
    primary_text: str
    primary_source_offsets: tuple[int, ...] = ()
    translation_text: str | None = None
    parenthetical_text: str | None = None
    parenthetical_translation_text: str | None = None


def _split_parenthetical_subrow(text: str) -> tuple[str, str | None, tuple[int, ...]]:
    annotations: list[str] = []
    kept: list[tuple[str, int]] = []
    cursor = 0
    scanned = 0
    nesting = 0
    for match in _PAREN_CONTENT_RE.finditer(text):
        for char in text[scanned:match.start()]:
            if char in "(（":
                nesting += 1
            elif char in ")）":
                nesting = max(0, nesting - 1)
        kept.extend((char, index) for index, char in enumerate(text[cursor:match.start()], cursor))
        content = (match.group(1) or match.group(2) or "").strip()
        if nesting or not content or _PAREN_METADATA_RE.match(content):
            kept.extend((char, index) for index, char in enumerate(text[match.start():match.end()], match.start()))
        else:
            annotations.append(content)
        cursor = match.end()
        scanned = cursor
    kept.extend((char, index) for index, char in enumerate(text[cursor:], cursor))
    compact: list[tuple[str, int]] = []
    index = 0
    while index < len(kept):
        char, source_index = kept[index]
        if char in " \t":
            end = index + 1
            while end < len(kept) and kept[end][0] in " \t":
                end += 1
            if end - index > 1:
                compact.append((" ", source_index))
                index = end
                continue
        compact.append((char, source_index))
        index += 1
    while compact and compact[0][0].isspace():
        compact.pop(0)
    while compact and compact[-1][0].isspace():
        compact.pop()
    return (
        "".join(char for char, _index in compact),
        " ".join(annotations) or None,
        tuple(source_index for _char, source_index in compact),
    )


def _explicit_translation_payloads(source_text: str) -> tuple[str, ...]:
    payloads: list[str] = []
    for part in str(source_text or "").replace("\u2028", "\n").splitlines():
        if _TRANSLATION_LABEL_RE.match(part):
            payloads.append(_TRANSLATION_LABEL_RE.sub("", part).strip())
        elif match := _INLINE_TRANSLATION_LABEL_RE.match(part):
            payloads.append(_TRANSLATION_LABEL_RE.sub("", match.group("translation")).strip())
    return tuple(payload for payload in payloads if payload)


def split_display_layers(
    text: str,
    *,
    source_text: str | None = None,
    show_translation: bool = True,
) -> DisplayLyricLayers:
    """Separate a sung row, recognized translation and non-nested parentheses.

    This does not rewrite provider text or clocks. It accepts the result of
    ``present_lyric_text`` (either newline form) and uses the same bilingual
    evidence as that presenter. Pass the pre-cleanup ``source_text`` when an
    explicit label was removed; this preserves same-language translations
    without mistaking an unlabeled same-language duet for one. Non-nested
    parenthetical vocal
    text may occur anywhere, while obvious metadata and nested brackets stay
    in the original line. The unmodified input and primary source text are
    returned for callers that match genuine timed units to their source.
    """
    original = str(text or "")
    parts = [part.strip() for part in original.replace("\u2028", "\n").splitlines() if part.strip()]
    if not parts:
        return DisplayLyricLayers(original, "", "", (0,))
    split_parts = [
        _split_parenthetical_subrow(_PARENTHETICAL_LABEL_RE.sub("", _TRANSLATION_LABEL_RE.sub("", part)))
        for part in parts
    ]
    attached_parentheticals = frozenset(
        index for index, part in enumerate(parts) if _PARENTHETICAL_LABEL_RE.match(part)
    )
    explicit_translations = _explicit_translation_payloads(source_text or original)
    first_content_index = next((index for index, (body, paren, _offsets) in enumerate(split_parts) if body or paren), None)
    if first_content_index is None:
        return DisplayLyricLayers(
            original, "\n".join(parts), "",
            primary_source_offsets=(0,),
            parenthetical_text=" ".join(paren for _body, paren, _offsets in split_parts if paren) or None,
        )
    first_body_index = next((index for index, (body, _paren, _offsets) in enumerate(split_parts) if body), None)
    if (
        not split_parts[first_content_index][0]
        and first_body_index is not None
        and split_parts[first_body_index][0] not in explicit_translations
        and not _is_translation_pair(
            split_parts[first_content_index][1] or "", split_parts[first_body_index][0],
        )
    ):
        # A leading backing-vocal annotation can precede a normal original.
        # It owns the whole row only when the next body is its translation.
        first_content_index = first_body_index
    first_body, first_parenthetical, _first_offsets = split_parts[first_content_index]
    first_content = first_body or first_parenthetical or ""
    # A separately timed backing vocal can be attached to the preceding sung
    # row by the song presenter. Keep its explicitly paired, parenthesized
    # companion beneath that vocal rather than stealing the main translation.
    parenthetical_pairs = {
        index: index + 1
        for index in range(first_content_index + 1, len(parts) - 1)
        if not split_parts[index][0] and split_parts[index][1]
        and _TRANSLATION_LABEL_RE.match(parts[index + 1])
        and not split_parts[index + 1][0] and split_parts[index + 1][1]
    }
    parenthetical_companions = frozenset(parenthetical_pairs.values())
    translation_index = next((
        index for index in range(first_content_index + 1, len(parts))
        if index not in attached_parentheticals
        and index not in parenthetical_pairs and index not in parenthetical_companions
        and (split_parts[index][0] or split_parts[index][1]) and (
            _TRANSLATION_LABEL_RE.match(parts[index])
            or parts[index] in explicit_translations
            or _is_translation_pair(
                first_content, split_parts[index][0] or split_parts[index][1] or "",
            )
        )
    ), None)

    primary_source: list[str] = []
    primary_body: list[str] = []
    primary_char_offsets: list[int] = []
    source_length = 0
    primary_parentheses: list[str] = []
    translated_parentheses: list[str] = []
    translated_body: str | None = None
    for index, (source, (body, parenthetical, offsets)) in enumerate(zip(parts, split_parts)):
        if index in parenthetical_pairs:
            primary_parentheses.append(parenthetical or "")
            continue
        if index in attached_parentheticals:
            primary_parentheses.append(parenthetical or "")
            continue
        if index in parenthetical_companions:
            if show_translation:
                translated_parentheses.append(parenthetical or "")
            continue
        if index == translation_index:
            # A whole translated row enclosed in parentheses is still a
            # translation. Only a bracket *inside* a translated sentence is
            # an animated parenthetical subrow.
            translated_content = _TRANSLATION_LABEL_RE.sub("", body or parenthetical or "").strip()
            if show_translation:
                if not first_body and first_parenthetical:
                    # A parenthetical-only sung original owns its translated
                    # counterpart; neither should leak into permanent main ink.
                    if translated_content:
                        translated_parentheses.append(translated_content)
                else:
                    translated_body = translated_content or None
                if body and parenthetical:
                    translated_parentheses.append(parenthetical)
            continue
        if body or index == first_content_index:
            if primary_source:
                primary_char_offsets.append(source_length)
                source_length += 1
            primary_source.append(source)
            if body:
                primary_body.append(body)
            primary_char_offsets.extend(source_length + offset for offset in offsets)
            source_length += len(source)
        if parenthetical:
            if translation_index is not None and index > translation_index and show_translation:
                translated_parentheses.append(parenthetical)
            elif translation_index is None or index < translation_index:
                primary_parentheses.append(parenthetical)
    return DisplayLyricLayers(
        original_text=original,
        original_primary_text="\n".join(primary_source),
        primary_text="\n".join(primary_body),
        primary_source_offsets=(
            tuple(primary_char_offsets) + (source_length,)
            if primary_char_offsets else (0,)
        ),
        translation_text=translated_body,
        parenthetical_text=" ".join(primary_parentheses) or None,
        parenthetical_translation_text=" ".join(translated_parentheses) or None,
    )


def _singer_aliases(singer_names: Sequence[str] | str) -> dict[str, str]:
    """Resolve a track's named performers and unambiguous short surnames."""
    if isinstance(singer_names, str):
        names = [part.strip() for part in _SINGER_JOIN_RE.split(singer_names) if part.strip()]
    else:
        names = [str(name).strip() for name in singer_names if str(name).strip()]
    full: dict[str, str] = {}
    initial_owners: dict[str, set[str]] = {}
    for name in names:
        key = " ".join(name.split()).casefold()
        full[key] = name
        if _POSSIBLE_SINGER_RE.fullmatch(name) and len(name) > 1:
            initial_owners.setdefault(name[0], set()).add(name)
    for initial, owners in initial_owners.items():
        if len(owners) == 1 and initial.casefold() not in full:
            full[initial.casefold()] = next(iter(owners))
    return full


def _line_singer_prefix(text: str, aliases: dict[str, str]) -> tuple[str, str] | None:
    """Recognize a line-leading performer marker, never an arbitrary colon."""
    value = str(text or "")
    first, separator, rest = value.replace("\u2028", "\n").partition("\n")
    colon_index = min((position for position in (first.find(":"), first.find("：")) if position >= 0), default=-1)
    if colon_index < 0:
        return None
    label, body = first[:colon_index], first[colon_index + 1:]
    label = " ".join(label.split())
    if not label or not body.strip() or len(label) > 32:
        return None
    components = _SINGER_JOIN_RE.split(label)
    if not components or any(not component.strip() for component in components):
        return None
    singers = [aliases.get(component.casefold()) for component in components]
    if any(singer is None for singer in singers):
        return None
    singer = "+".join(dict.fromkeys(str(item) for item in singers))
    return singer, body.strip() + ("\n" + rest if separator else "")


def analyze_duet_lyric_rows(
    rows: Sequence[tuple[float, str]],
    *,
    singer_names: Sequence[str] | str = (),
) -> DuetLyricPresentation:
    """Plan duet grouping and alignment for already-cleaned display rows.

    ``rows`` is the output of ``present_timed_lyric_rows``. Its length, order,
    indexes and start times are preserved, so callers can keep their existing
    timeline, reading units and seek map. Pass the track's singer names to
    distinguish performer prefixes from ordinary lyric colons; a unique first
    Han character of a known performer is accepted as a short prefix. Without
    performer evidence, text before a colon is *not* assumed to be a singer.

    Only adjacent, individually prefixed rows at most one second apart form a
    pair. Grouping uses their source start times, never OBO, offsets or visual
    advance. Distinct solo roles alternate left/right by first appearance;
    a combined A+B role is left. A sole role first entering after the first
    sung row uses the right side and unmarked following rows inherit its side.
    """
    aliases = _singer_aliases(singer_names)
    initial: list[SingerLyricRow] = []
    for index, (start, text) in enumerate(rows):
        value = str(text or "")
        prefix = _line_singer_prefix(value, aliases)
        singer, body = prefix if prefix is not None else (None, value)
        initial.append(SingerLyricRow(index, float(start), value, body, singer))

    solo_names = list(dict.fromkeys(
        row.singer for row in initial
        if row.singer is not None and "+" not in row.singer
    ))
    first_sung_index = next(
        (row.source_index for row in initial if row.original_text != INTRO_COUNTDOWN_TEXT),
        0,
    )
    lone_late_role = len(solo_names) == 1 and next(
        (row.source_index for row in initial if row.singer == solo_names[0]),
        first_sung_index,
    ) > first_sung_index
    sides = {name: ("right" if (lone_late_role or index % 2) else "left")
             for index, name in enumerate(solo_names)}
    aligned: list[SingerLyricRow] = []
    inherited_side = "left"
    for row in initial:
        if row.singer is not None:
            inherited_side = "left" if "+" in row.singer else sides[row.singer]
        aligned.append(replace(row, alignment=inherited_side))

    groups: list[DualLyricGroup] = []
    index = 0
    while index + 1 < len(aligned):
        first, second = aligned[index:index + 2]
        if (
            first.singer is not None
            and second.singer is not None
            and math.isfinite(first.start_seconds)
            and math.isfinite(second.start_seconds)
            and 0.0 <= second.start_seconds - first.start_seconds <= 1.0
        ):
            group_index = len(groups)
            aligned[index] = replace(first, dual_group_index=group_index)
            aligned[index + 1] = replace(second, dual_group_index=group_index)
            groups.append(DualLyricGroup(
                first.source_index, second.source_index,
                first.start_seconds, second.start_seconds,
            ))
            index += 2
        else:
            index += 1
    return DuetLyricPresentation(tuple(aligned), tuple(groups))




def _staff_separator(text: str) -> bool:
    value = str(text or "").strip()
    return len(value) <= 8 and not any(char.isalnum() for char in value)


def _possible_staff_field(text: str) -> bool:
    """A structural candidate, never sufficient evidence on its own.

    This is used only after two explicit credits establish a leading block.
    Sentence punctuation and pronoun/verb clauses end that block, so ordinary
    sung colons cannot be absorbed just because they are short.
    """
    match = _STAFF_FIELD_RE.fullmatch(str(text or "").strip())
    if match is None:
        return False
    label, payload = (part.strip() for part in match.groups())
    if (
        not label or _FIELD_PUNCTUATION_RE.search(label)
        or _SENTENCE_FIELD_RE.search(label)
        or re.search(r"\d\s*[:：]\s*\d", text)
        or len(payload) > 160 or re.search(r"[。！？!?{}\[\]]", payload)
    ):
        return False
    return True


def _leading_staff_block_indexes(
    texts: Sequence[str], explicit: set[int], protected: set[int],
) -> set[int]:
    """Find evidence-backed neighbours of a leading production-credit block.

    Explicit single rows are cleaned independently. Expansion needs two
    explicit fields, and an unanchored trailing run needs at least two more
    fields. A protected singer/translation/vocal always terminates the scan.
    No row duration, playback position or source data is changed here.
    """
    start = 0
    while start < len(texts):
        if start in protected:
            return set()
        if start in explicit:
            break
        if not (_staff_separator(texts[start]) or _colon_prefix(texts[start]) in _EDGE_NOTE_LABELS):
            return set()
        start += 1
    accepted: list[int] = []
    separators: list[int] = []
    pending: list[int] = []
    hits = 0
    for index in range(start, len(texts)):
        if index in protected:
            break
        if _staff_separator(texts[index]):
            pending.append(index)
            continue
        if index in explicit:
            hits += 1
        elif hits < 2 or not _possible_staff_field(texts[index]):
            break
        accepted.append(index)
        separators.extend(pending)
        pending.clear()
    if hits < 2:
        return set()
    trailing = 0
    for index in reversed(accepted):
        if index in explicit:
            break
        trailing += 1
    if trailing == 1:
        accepted.pop()
        pending.clear()
    if not accepted:
        return set()
    last = accepted[-1]
    return set(accepted) | {index for index in separators if index < last} | set(pending)


def _colon_prefix(text: str) -> str | None:
    """Return a comparable speaker/note label without changing displayed ink."""
    before, separator, after = text.replace("：", ":").partition(":")
    if not separator or not before.strip() or not after.strip():
        return None
    return " ".join(before.split()).casefold()


def _is_translation_pair(first: str, second: str) -> bool:
    if _PARENTHETICAL_LABEL_RE.match(second):
        return False
    if _TRANSLATION_LABEL_RE.match(second):
        return True
    if _is_credit_line(second):
        return False
    first_latin = bool(_LATIN_WORD_RE.search(first))
    second_latin = bool(_LATIN_WORD_RE.search(second))
    first_han = bool(_HAN_RE.search(first))
    second_han = bool(_HAN_RE.search(second))
    # A translated Chinese sentence may keep an English name or quoted word.
    # Require a substantial Han body rather than rejecting every Latin token;
    # a mostly Latin code-switched vocal still remains a sung subrow.
    dominant_second_han = len(_HAN_RE.findall(second)) >= max(2, 2 * len(_LATIN_WORD_RE.findall(second)))
    if first_latin and not first_han and second_han and (not second_latin or dominant_second_han):
        return True
    if (bool(_KANA_RE.search(first)) or bool(_HANGUL_RE.search(first))) and second_han:
        return not bool(_KANA_RE.search(second) or _HANGUL_RE.search(second))
    if first_han and not first_latin and second_latin and not second_han:
        return len(_LATIN_WORD_RE.findall(second)) >= 2
    return False


def _split_inline_translation(text: str) -> tuple[str, str] | None:
    labeled = _INLINE_TRANSLATION_LABEL_RE.match(text)
    if labeled is not None:
        return labeled.group("original").strip(), labeled.group("translation").strip()
    for separator in (r"\s+[|｜]\s+", r"\s+/{1,2}\s+"):
        parts = re.split(separator, text, maxsplit=1)
        if len(parts) == 2 and _is_translation_pair(parts[0], parts[1]):
            return parts[0].strip(), parts[1].strip()
    # The mixed-script expression has two broad whitespace-capable runs.  On
    # long Latin-only lines it can backtrack repeatedly despite there being no
    # possible Han suffix; avoid invoking it for the common case on track load.
    match = _INLINE_LATIN_HAN_RE.search(text) if _HAN_RE.search(text) else None
    if match is not None and match.start("han") > 0 and not text[match.start("han") - 1].isspace():
        first = text[:match.end("latin")].strip()
        second = text[match.start("han"):].strip()
        if _is_translation_pair(first, second):
            return first, second
    single = _INLINE_SINGLE_WORD_TRANSLATION_RE.match(text.strip())
    if single is not None:
        return single.group("latin"), single.group("han")
    return None


def present_lyric_text(
    text: str,
    *,
    show_translation: bool = True,
    clean_intro: bool = True,
    clean_credits: bool = True,
) -> str:
    """Return painted text after display-only annotation and translation cleanup.

    ``clean_intro`` controls bracketed ``[Verse n]`` labels and standalone
    section markers. Explicit credit
    subrows are removed, while other colon-prefixed lines are left for the
    whole-song pass to distinguish a repeated singer from an intro note.
    ``clean_credits=False`` leaves credit-shaped rows for the whole-song edge
    pass, which has the repetition and surrounding lyric evidence unavailable
    to this single-row helper. A labeled translation is recognized first so its text can remain visible
    without the label when translation display is enabled.
    """
    value = str(text or "").replace("\u2028", "\n")
    parts = [
        (_VERSE_TAG_RE.sub("", part) if clean_intro else part).strip()
        for part in value.splitlines()
        if not (clean_intro and _SECTION_ONLY_RE.fullmatch(part))
    ]
    # A coalesced timestamp may contain a credit before the original and its
    # translation. Remove that credit *before* deciding which two subrows are
    # bilingual, but retain the translation label until pairing is complete.
    parts = [part for part in parts if part and (
        not clean_credits or not _is_credit_line(part) or _TRANSLATION_LABEL_RE.match(part)
    )]
    if not parts:
        return ""
    # Some stores emit a labeled translation before the original at the same
    # timestamp. The explicit label is enough evidence to pair it with the
    # first other surviving subrow, regardless of their input order.
    labeled_index = next((index for index, part in enumerate(parts)
                          if _TRANSLATION_LABEL_RE.match(part)
                          and not (index > 0 and _PARENTHETICAL_LABEL_RE.match(parts[index - 1]))), None)
    if labeled_index is not None and len(parts) >= 2:
        # Deferred edge cleanup leaves notes before the sung original. Pair
        # against that original, never against the first retained credit.
        original_index = next((index for index, part in enumerate(parts)
                               if not _is_credit_line(part)
                               and not _TRANSLATION_LABEL_RE.match(part)
                               and not _PARENTHETICAL_LABEL_RE.match(part)), None)
        if original_index is not None:
            labeled = parts.pop(labeled_index)
            if labeled_index < original_index:
                original_index -= 1
            parts.insert(original_index + 1, labeled)
    if not show_translation:
        # A generated source cache can retain both translation and phonetic
        # alternatives at the same timestamp. Neither may leak into primary
        # lyrics when the shared toggle is off.
        parts = [part for part in parts if not _TRANSLATION_LABEL_RE.match(part)]
    if len(parts) == 1:
        pair = _split_inline_translation(parts[0])
        if pair is not None:
            parts = list(pair)
    primary_index = next((index for index, part in enumerate(parts)
                          if not _is_credit_line(part)
                          and not _TRANSLATION_LABEL_RE.match(part)), None)
    secondary_index = primary_index + 1 if primary_index is not None else len(parts)
    if secondary_index < len(parts) and _is_translation_pair(
        _split_parenthetical_subrow(parts[primary_index])[0] or parts[primary_index],
        _split_parenthetical_subrow(parts[secondary_index])[0]
        or _split_parenthetical_subrow(parts[secondary_index])[1] or parts[secondary_index],
    ):
        parts[secondary_index] = _TRANSLATION_LABEL_RE.sub("", parts[secondary_index]).strip()
        if not show_translation:
            parts = parts[:secondary_index] + parts[secondary_index + 1:]
    # An explicit production credit can appear after the first sung row, but
    # a generic colon is not enough evidence: duet lyrics often label singers.
    # Filter per subrow so a coalesced timestamp retains its sung text.
    return "\n".join(part for part in parts if part and (not clean_credits or not _is_credit_line(part)))


def present_timed_lyric_rows(
    rows: Sequence[tuple[float, str]],
    *,
    show_translation: bool = True,
    clean_intro: bool = True,
    intro_countdown: bool = True,
    singer_names: Sequence[str] | str = (),
    attach_parentheticals: bool = True,
) -> list[tuple[float, str]]:
    """Prepare display rows without rewriting provider text or unit clocks.

    Explicit production rows are removed wherever they occur; unknown fields
    require a leading, evidence-backed credit block. Known edge notes may be
    removed at the song's beginning/end. Numeric colons,
    repeated sung rows or speaker labels, verified
    singers, actual translations and colons within lyric phrases survive.
    A standalone parenthetical vocal joins the immediately preceding sung
    display row, retaining the latter's start and unchanged primary source
    subrow. Its translation remains paired to that annotation. The provider's
    separately timed source row is never mutated by this display-only merge.
    ``attach_parentheticals=False`` keeps those rows separate for consumers
    whose visual slots retain the original source indexes, such as the island.
    The optional countdown starts at zero only when the first surviving sung
    line starts at least five seconds in; it is the sole synthetic timestamp.
    """
    prepared: list[tuple[float, list[str]]] = []
    prepared_sources: list[str] = []
    for timestamp, raw_text in rows:
        try:
            started_at = float(timestamp)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(started_at):
            continue
        text = present_lyric_text(
            raw_text,
            show_translation=show_translation,
            clean_intro=clean_intro,
            clean_credits=False,
        )
        if text:
            prepared.append((timestamp, text.splitlines()))
            prepared_sources.append(str(raw_text or ""))

    # Work over visual subrows, not timestamps: an original, translation and
    # production note can share one source timestamp. Keep each surviving
    # row at exactly that timestamp; provider data and unit times stay intact.
    flat = [(row_index, part_index, part)
            for row_index, (_time, parts) in enumerate(prepared)
            for part_index, part in enumerate(parts)]
    labels = [_colon_prefix(part) for _, _, part in flat]
    row_counts = Counter(" ".join(part.replace("：", ":").split()).casefold()
                         for _, _, part in flat)
    label_counts = Counter(label for label in labels if label is not None)
    singer_aliases = _singer_aliases(singer_names)
    translated_subrows: set[tuple[int, int]] = set()
    for row_index, (_timestamp, parts) in enumerate(prepared):
        layers = split_display_layers(
            "\n".join(parts), source_text=prepared_sources[row_index],
            show_translation=show_translation,
        )
        companions = {value for value in (
            layers.translation_text, layers.parenthetical_translation_text,
        ) if value}
        for part_index, part in enumerate(parts):
            payload = _TRANSLATION_LABEL_RE.sub("", part).strip()
            body, parenthetical, _offsets = _split_parenthetical_subrow(payload)
            if (body or parenthetical) in companions:
                translated_subrows.add((row_index, part_index))

    def repeated_lyric(flat_index: int) -> bool:
        part = flat[flat_index][2]
        normalized = " ".join(part.replace("：", ":").split()).casefold()
        return row_counts[normalized] > 1 and (
            not _is_credit_line(part) or labels[flat_index] in _AMBIGUOUS_CREDIT_LABELS
        )

    def removable(flat_index: int) -> bool:
        row_index, part_index, part = flat[flat_index]
        if repeated_lyric(flat_index):
            return False
        if (
            (row_index, part_index) not in translated_subrows
            and not _TRANSLATION_LABEL_RE.match(part)
            and _line_singer_prefix(part, singer_aliases) is None
            and _is_credit_line(part)
        ):
            return True
        if (
            re.search(r"\d\s*[:：]\s*\d", part)
            or (row_index, part_index) in translated_subrows
            or _PARENTHETICAL_LABEL_RE.match(part)
            or _line_singer_prefix(part, singer_aliases) is not None
        ):
            return False
        label = labels[flat_index]
        if label is not None and label_counts[label] > 1 and not _is_credit_line(part):
            return False
        if _is_credit_line(part):
            return True
        if label is None:
            return False
        return label in _EDGE_NOTE_LABELS

    removed: set[int] = set()
    # Strongly identified production/rights metadata must not leak into a
    # coalesced sung row or reappear merely because the service repeats it.
    # Translations are recognized first and keep their existing toggle.
    removed.update(
        index for index, (row_index, part_index, part) in enumerate(flat)
        if (row_index, part_index) not in translated_subrows
        and not _TRANSLATION_LABEL_RE.match(part) and _is_credit_line(part)
        and _line_singer_prefix(part, singer_aliases) is None
        and not repeated_lyric(index)
    )
    protected = {
        index for index, (row_index, part_index, part) in enumerate(flat)
        if (row_index, part_index) in translated_subrows
        or _TRANSLATION_LABEL_RE.match(part) or _PARENTHETICAL_LABEL_RE.match(part)
        or _line_singer_prefix(part, singer_aliases) is not None or repeated_lyric(index)
    }
    removed.update(_leading_staff_block_indexes(
        [part for _row_index, _part_index, part in flat], removed, protected,
    ))
    for positions in (range(len(flat)), range(len(flat) - 1, -1, -1)):
        for flat_index in positions:
            if not removable(flat_index):
                break
            removed.add(flat_index)

    result: list[tuple[float, str]] = []
    result_sources: list[str] = []
    remaining: dict[int, list[str]] = {}
    for flat_index, (row_index, _part_index, part) in enumerate(flat):
        if flat_index not in removed:
            remaining.setdefault(row_index, []).append(part)
    for row_index, (timestamp, _parts) in enumerate(prepared):
        if parts := remaining.get(row_index):
            result.append((timestamp, "\n".join(parts)))
            result_sources.append(prepared_sources[row_index])
    if attach_parentheticals:
        result = _attach_standalone_parenthetical_rows(
            result, source_texts=result_sources, show_translation=show_translation,
        )
    if (
        clean_intro
        and intro_countdown
        and result
        and float(result[0][0]) >= INTRO_COUNTDOWN_MIN_START_SECONDS
    ):
        result.insert(0, (0.0, INTRO_COUNTDOWN_TEXT))
    return result


def _attach_standalone_parenthetical_rows(
    rows: list[tuple[float, str]], *, source_texts: Sequence[str], show_translation: bool,
) -> list[tuple[float, str]]:
    """Move a standalone annotation into its previous display row's layers."""
    result: list[tuple[float, str]] = []
    for row_index, (timestamp, text) in enumerate(rows):
        layers = split_display_layers(
            text, source_text=source_texts[row_index], show_translation=show_translation,
        )
        if (
            not layers.primary_text and layers.parenthetical_text and result
            and split_display_layers(result[-1][1], show_translation=show_translation).primary_text
        ):
            previous_time, previous_text = result[-1]
            parts = previous_text.splitlines()
            annotation = [f"括号词：（{layers.parenthetical_text}）"]
            if show_translation and layers.parenthetical_translation_text:
                # Explicit provenance distinguishes an annotation companion
                # from an otherwise absent main-sentence translation, even
                # when both use the same writing system.
                annotation.append(f"翻译：（{layers.parenthetical_translation_text}）")
            # Keep the original first subrow byte-for-byte for source matching;
            # added annotations never enter original_primary_text or offsets.
            insertion = 1
            while insertion < len(parts) and _PARENTHETICAL_LABEL_RE.match(parts[insertion]):
                insertion += 1
                if insertion < len(parts) and _TRANSLATION_LABEL_RE.match(parts[insertion]):
                    body, parenthetical, _offsets = _split_parenthetical_subrow(
                        _TRANSLATION_LABEL_RE.sub("", parts[insertion]),
                    )
                    if not body and parenthetical:
                        insertion += 1
            result[-1] = (previous_time, "\n".join(parts[:insertion] + annotation + parts[insertion:]))
        else:
            result.append((timestamp, text))
    return result


def attached_parenthetical_read_end(
    display_text: str, source_rows: Sequence[tuple[float, str]], source_start: float,
    *, show_translation: bool = True, clean_intro: bool = True,
) -> float | None:
    """Retain the old reading boundary when a later bracket joins this row.

    The display timeline skips separately timed parentheses after attachment.
    A main sentence's estimated interval and any missing last-unit end must
    still stop at the first attached source row, not stretch until the next
    sung sentence. Ordinary credit cleanup does not acquire this override;
    genuine explicit unit ends and the caller's duet clock remain authoritative.
    """
    parts = str(display_text or "").replace("\u2028", "\n").splitlines()
    if not parts or not any(_PARENTHETICAL_LABEL_RE.match(part) for part in parts):
        return None
    primary = parts[0].strip()
    matched = False
    for timestamp, source_text in source_rows:
        try:
            start = float(timestamp)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start):
            continue
        prepared = present_lyric_text(
            source_text, show_translation=show_translation,
            clean_intro=clean_intro, clean_credits=False,
        )
        if not matched:
            matched = (
                abs(start - float(source_start)) <= 0.0005 and bool(prepared)
                and prepared.splitlines()[0].strip() == primary
            )
            continue
        if not prepared:
            continue
        if start <= float(source_start):
            continue
        layers = split_display_layers(
            prepared, source_text=source_text, show_translation=show_translation,
        )
        return start if not layers.primary_text and layers.parenthetical_text else None
    return None


def intro_countdown_progress(position_seconds: float, first_lyric_start_seconds: float) -> float:
    """Return 0..1 progress for painting the three-dot intro indicator."""
    try:
        end = float(first_lyric_start_seconds)
        position = float(position_seconds)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(end) or not math.isfinite(position) or end <= 0.0:
        return 0.0
    return max(0.0, min(1.0, position / end))
