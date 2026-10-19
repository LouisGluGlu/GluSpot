"""Small, read-only AMLL TTML adapter for GluSpot's existing lyric models.

Only source timestamps are retained. Unsupported clocks and malformed XML do
not become synthetic vocals. No network, presentation filtering or cache
writes occur here. The parser uses the stdlib XML tree, not Folia's code or an
additional TTML runtime. Raw XML and metadata remain available to its caller.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from xml.etree import ElementTree

from api.models import TimedLyricLine, TimedLyricUnit


MAX_TTML_BYTES = 2 * 1024 * 1024
MAX_TTML_ELEMENTS = 30000
# Existing ordered companion pairing is quadratic in row count. Bound a new
# remote format before it enters that shared algorithm, independently of XML
# byte/element limits; ordinary song documents remain well below this bound.
MAX_TTML_ROWS = 2000
_TTML_NS = "http://www.w3.org/ns/ttml"
_TTM_NS = "http://www.w3.org/ns/ttml#metadata"
_SECONDARY_ROLES = {"x-translation", "x-roman", "x-romanization", "x-transliteration"}


@dataclass(frozen=True)
class TtmlDocument:
    raw_text: str
    originals: tuple[TimedLyricLine, ...]
    translations: tuple[TimedLyricLine, ...] = ()
    romanizations: tuple[TimedLyricLine, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()
    # Parallel to originals; retain source duet attribution without inventing
    # singer labels or altering the sung text in the existing line model.
    agents: tuple[str, ...] = ()
    # TTML body's explicit document duration can stop at the last vocal. It
    # is not authoritative full-song duration and must not be compared for
    # equality with a music provider's track interval.
    duration_seconds: float | None = None


def _name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _role(node: ElementTree.Element) -> str:
    return str(node.get(f"{{{_TTM_NS}}}role") or node.get("role") or "")


def parse_ttml_time(value: str | None) -> float | None:
    """Accept TTML clock and offset units supported by the AMLL profile.

    Frame/tick clocks require a declared rate and are deliberately rejected
    here rather than guessed. Empty values and negative/nonfinite times are
    also rejected.
    """
    text = str(value or "").strip()
    if not text or len(text) > 64:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)?", text)
    if match:
        number = float(match[1]) * {None: 1.0, "ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[match[2]]
    else:
        parts = text.split(":")
        if len(parts) not in (2, 3) or any(not re.fullmatch(r"\d+(?:\.\d+)?", part) for part in parts):
            return None
        if any("." in part for part in parts[:-1]) or float(parts[-1]) >= 60 or len(parts) == 3 and int(parts[-2]) >= 60:
            return None
        number = float(parts[-1]) + int(parts[-2]) * 60 + (int(parts[0]) * 3600 if len(parts) == 3 else 0)
    return number if math.isfinite(number) and 0 <= number < 7 * 24 * 3600 else None


def _vocal_text(node: ElementTree.Element, *, skip_background: bool = True) -> str:
    result = [node.text or ""]
    for child in node:
        role = _role(child)
        if role not in _SECONDARY_ROLES and (not skip_background or role != "x-bg"):
            result.append("\n" if _name(child.tag) == "br" else _vocal_text(child, skip_background=skip_background))
        result.append(child.tail or "")
    return "".join(result)


def _row(node: ElementTree.Element, fallback_start: float | None = None, *, background: bool = False) -> TimedLyricLine | None:
    text = _vocal_text(node).strip()
    if not text:
        return None
    units: list[TimedLyricUnit] = []

    def collect(parent: ElementTree.Element) -> None:
        for child in parent:
            if _role(child) in _SECONDARY_ROLES or _role(child) == "x-bg":
                continue
            children_with_times = any(
                nested.get("begin") is not None and _role(nested) not in _SECONDARY_ROLES
                for nested in child.iter() if nested is not child
            )
            start = parse_ttml_time(child.get("begin"))
            if start is not None and not children_with_times:
                end = parse_ttml_time(child.get("end"))
                duration = parse_ttml_time(child.get("dur"))
                if end is None and duration is not None:
                    end = start + duration
                content = _vocal_text(child)
                if child.tail and child.tail.isspace():
                    content += child.tail
                if content.strip() and (end is None or end >= start):
                    units.append(TimedLyricUnit(start, end, content))
            else:
                before = len(units)
                collect(child)
                if len(units) > before and child.tail and child.tail.isspace():
                    last = units[-1]
                    units[-1] = TimedLyricUnit(last.start_seconds, last.end_seconds, last.text + child.tail)

    collect(node)
    start = parse_ttml_time(node.get("begin"))
    if start is None:
        start = min((unit.start_seconds for unit in units), default=fallback_start)
    if start is None:
        return None
    # A plain TTML <p end=...> is still line timing, not fabricated word
    # timing. A single source-timed word remains a genuine source unit.
    if background and not text.startswith(("(", "（")):
        text = f"({text})"
        if units:
            first, last = units[0], units[-1]
            units[0] = TimedLyricUnit(first.start_seconds, first.end_seconds, "(" + first.text)
            last = units[-1]
            units[-1] = TimedLyricUnit(last.start_seconds, last.end_seconds, last.text + ")")
    complete_units = bool(units) and "".join(unit.text for unit in units).strip() == text
    return TimedLyricLine(start, text, tuple(units), "unit" if complete_units else "line")


def parse_ttml(raw_text: str) -> TtmlDocument | None:
    """Parse a bounded absolute-clock AMLL TTML document, preserving sources."""
    if not isinstance(raw_text, str):
        return None
    try:
        if len(raw_text.encode("utf-8")) > MAX_TTML_BYTES:
            return None
    except UnicodeError:
        return None
    # ElementTree has no external fetches; explicitly rejecting DTD/entity
    # declarations also rejects internal expansion attacks before parsing.
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", raw_text, re.I):
        return None
    try:
        root = ElementTree.fromstring(raw_text)
    except (ElementTree.ParseError, ValueError, RecursionError):
        return None
    if root.tag not in ("tt", f"{{{_TTML_NS}}}tt"):
        return None
    nodes = list(root.iter())
    if len(nodes) > MAX_TTML_ELEMENTS or any(node.get("timeContainer") == "seq" for node in nodes):
        return None
    metadata = tuple((str(node.get("key")), str(node.get("value"))) for node in nodes
                     if _name(node.tag) == "meta" and node.get("key") is not None and node.get("value") is not None)
    body = next((node for node in root if _name(node.tag) == "body"), None)
    if body is None:
        return None
    originals: list[tuple[TimedLyricLine, str]] = []
    translations: list[TimedLyricLine] = []
    romanizations: list[TimedLyricLine] = []
    try:
        for paragraph in body.iter():
            if _name(paragraph.tag) != "p":
                continue
            agent = str(paragraph.get(f"{{{_TTM_NS}}}agent") or paragraph.get("agent") or "")
            paragraph_role = _role(paragraph)
            if paragraph_role in _SECONDARY_ROLES:
                secondary = _row(paragraph)
                if secondary is not None:
                    destination = translations if paragraph_role == "x-translation" else romanizations
                    destination.append(secondary)
                continue
            main = _row(paragraph, background=paragraph_role == "x-bg")
            if main is not None:
                originals.append((main, agent))
            fallback = main.time_seconds if main is not None else parse_ttml_time(paragraph.get("begin"))
            def companions(parent: ElementTree.Element, parent_start: float | None) -> None:
                for child in parent:
                    role = _role(child)
                    if role == "x-bg":
                        row = _row(child, parent_start, background=True)
                        if row is not None:
                            originals.append((row, agent))
                        companions(child, row.time_seconds if row is not None else parent_start)
                    elif role in _SECONDARY_ROLES:
                        secondary = _row(child, parent_start)
                        if secondary is not None:
                            destination = translations if role == "x-translation" else romanizations
                            destination.append(secondary)
                    else:
                        companions(child, parent_start)

            companions(paragraph, fallback)
    except RecursionError:
        return None
    if not originals or len(originals) + len(translations) + len(romanizations) > MAX_TTML_ROWS:
        return None
    originals.sort(key=lambda entry: entry[0].time_seconds)
    duration = parse_ttml_time(body.get("dur"))
    if duration is None:
        duration = parse_ttml_time(body.get("end"))
    return TtmlDocument(raw_text, tuple(row for row, _agent in originals), tuple(translations),
                        tuple(romanizations), metadata, tuple(agent for _row, agent in originals), duration)
