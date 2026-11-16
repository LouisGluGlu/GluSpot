"""Pure search matching, history and latency logs.

The library window paints these results. This module does not choose a grouped
layout: songs, albums, artists and playlists stay in the caller's order.
"""
from __future__ import annotations

import logging
import sys
import time

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor


LOCAL_PREVIEW_LIMIT = 30
HISTORY_LIMIT = 12

_log = logging.getLogger("GluSpot.search")
_log.setLevel(logging.INFO)
if not _log.handlers and sys.stderr is not None:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    _log.addHandler(_handler)
    _log.propagate = False


def log_search_phase(phase, started, *, rows=0, query_chars=0, generation=0, detail=""):
    """Log timing without the query text. ``detail`` is a short safe token."""
    try:
        elapsed = time.perf_counter() - float(started)
    except (TypeError, ValueError):
        elapsed = 0.0
    elapsed_ms = max(0, int(round(elapsed * 1000.0)))
    token = "".join(ch for ch in str(detail) if ch.isalnum() or ch in "-_")[:24]
    _log.info(
        "phase=%s ms=%d rows=%d query_chars=%d generation=%d detail=%s",
        str(phase), elapsed_ms, int(rows), int(query_chars), int(generation), token,
    )


def match_spans(text, query):
    """Half-open spans of ``query`` inside ``text``.

    Matching is case-insensitive when casefolding does not change length.
    An empty query matches nothing, so idle catalog text stays unchanged.
    """
    text = "" if text is None else str(text)
    query = "" if query is None else str(query).strip()
    if not text or not query:
        return ()
    folded_text, folded_query = text.casefold(), query.casefold()
    if len(folded_text) != len(text) or len(folded_query) != len(query):
        if query not in text:
            return ()
        spans, start = [], 0
        while True:
            found = text.find(query, start)
            if found < 0:
                break
            spans.append((found, found + len(query)))
            start = found + len(query)
        return tuple(spans)
    spans, start = [], 0
    while True:
        found = folded_text.find(folded_query, start)
        if found < 0:
            break
        spans.append((found, found + len(folded_query)))
        start = found + len(folded_query)
    return tuple(spans)


NO_MATCH = 9


def _fold(text):
    """Case, width and spacing/punctuation-insensitive form for matching."""
    import unicodedata
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return "".join(ch for ch in value if ch.isalnum())


def _is_subsequence(needle, haystack):
    position = 0
    for ch in needle:
        position = haystack.find(ch, position)
        if position < 0:
            return False
        position += 1
    return True


def _rank_fields(fields, query):
    """0 prefix, 1 substring, 2 the same ignoring spaces/punctuation/width,
    3 every word found across the fields ("歌手 歌名"), 4 the letters in order
    (a typo-tolerant subsequence of 3+ characters), else ``NO_MATCH``."""
    folded = query.casefold()
    best = NO_MATCH
    for field in fields:
        value = str(field or "").casefold()
        if not value:
            continue
        if value.startswith(folded):
            return 0
        if folded in value:
            best = min(best, 1)
    if best <= 1:
        return best
    compact_query = _fold(query)
    if not compact_query:
        return best
    compact_fields = [_fold(field) for field in fields if field]
    if any(compact_query in field for field in compact_fields):
        return 2
    words = [_fold(word) for word in str(query).split()]
    words = [word for word in words if word]
    if len(words) > 1 and all(any(word in field for field in compact_fields) for word in words):
        return 3
    if len(compact_query) >= 3 and any(_is_subsequence(compact_query, field) for field in compact_fields):
        return 4
    return best


FUZZY_RANK_LIMIT = 4


def _collection_fields(row):
    fields = [getattr(row, "title", "")]
    song = getattr(row, "song", None)
    if song is not None:
        fields.extend((getattr(song, "title", ""), getattr(song, "artist", ""), getattr(song, "album", "")))
    return tuple(fields)


def local_collection_matches(rows, query, *, limit=LOCAL_PREVIEW_LIMIT):
    """Catalog rows whose title, artist or album contains ``query``.

    Prefix matches come first. The original row objects are returned so
    artwork keys and playback identity stay intact.
    """
    query = "" if query is None else str(query).strip()
    if not query:
        return ()
    limit = max(0, int(limit))
    ranked = []
    for index, row in enumerate(rows or ()):
        rank = _rank_fields(_collection_fields(row), query)
        if rank <= FUZZY_RANK_LIMIT:
            ranked.append((rank, index, row))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in ranked[:limit])


def local_song_matches(songs, query, *, source_id="", limit=LOCAL_PREVIEW_LIMIT):
    """Playable songs already in memory, ranked the same way as catalog rows."""
    query = "" if query is None else str(query).strip()
    if not query:
        return ()
    limit = max(0, int(limit))
    ranked, seen = [], set()
    for index, song in enumerate(songs or ()):
        if source_id and getattr(song, "source_id", "") != source_id:
            continue
        if getattr(song, "media_kind", "song") not in {"", "song"}:
            continue
        identity = (getattr(song, "source_id", ""), str(getattr(song, "song_id", "")))
        if not identity[1] or identity in seen:
            continue
        rank = _rank_fields((getattr(song, "title", ""), getattr(song, "artist", ""), getattr(song, "album", "")), query)
        if rank <= FUZZY_RANK_LIMIT:
            seen.add(identity)
            ranked.append((rank, index, song))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in ranked[:limit])


class SearchHistory:
    """Queries, oldest first. The same text moves to the newest end.

    ``on_change`` (optional) receives the items after every change, so the
    owner can keep them in settings across runs.
    """

    def __init__(self, limit=HISTORY_LIMIT, items=(), on_change=None):
        self.limit = max(1, int(limit))
        self._items = [str(item).strip() for item in items if str(item).strip()][-self.limit:]
        self._on_change = on_change

    def clear(self):
        self._items = []
        if callable(self._on_change):
            self._on_change(())

    def forget(self, query):
        query = str(query or "").strip()
        if query in self._items:
            self._items = [item for item in self._items if item != query]
            if callable(self._on_change):
                self._on_change(tuple(self._items))

    @property
    def items(self):
        return tuple(self._items)

    def remember(self, query):
        query = "" if query is None else str(query).strip()
        if not query:
            return
        self._items = [item for item in self._items if item != query]
        self._items.append(query)
        if len(self._items) > self.limit:
            self._items = self._items[-self.limit:]
        if callable(self._on_change):
            self._on_change(tuple(self._items))


def paint_highlighted_text(painter, rect, text, query, color, accent):
    """Draw elided text, painting matched spans in ``accent``."""
    metrics = painter.fontMetrics()
    shown = metrics.elidedText(str(text or ""), Qt.TextElideMode.ElideRight, max(1, round(rect.width())))
    spans = match_spans(shown, query)
    if not spans:
        painter.setPen(QColor(color))
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, shown)
        return
    parts, cursor = [], 0
    for start, end in spans:
        if start > cursor:
            parts.append((shown[cursor:start], False))
        parts.append((shown[start:end], True))
        cursor = end
    if cursor < len(shown):
        parts.append((shown[cursor:], False))
    baseline = rect.y() + (rect.height() + metrics.ascent() - metrics.descent()) / 2.0
    x = rect.x()
    for part, hit in parts:
        painter.setPen(QColor(accent if hit else color))
        painter.drawText(QPointF(x, baseline), part)
        x += metrics.horizontalAdvance(part)
