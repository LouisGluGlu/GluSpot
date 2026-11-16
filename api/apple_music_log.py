"""Fixed labels for Apple Music steps. Tokens, passwords and cookies never fit."""
from __future__ import annotations

import logging
import re
import sys


_log = logging.getLogger("GluSpot.apple")
_log.setLevel(logging.INFO)
if not _log.handlers and sys.stderr is not None:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    _log.addHandler(_handler)
    _log.propagate = False

_OPERATIONS = {"login", "playlists", "search", "play", "skip", "write", "artwork", "lyrics"}
_LABEL = re.compile(r"[A-Za-z0-9_-]{1,32}")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:token|cookie|password|authorization|secret)s?\b\s*[=:]\s*\S+"
)
_SECRET_WORD = re.compile(r"(?i)\b(?:token|cookie|password|authorization|secret)s?\b")
_COUNTS = {"count", "added", "failed"}
_LABELS = {"stage", "action", "result", "preview", "reason"}
_ORDER = ("stage", "action", "result", "count", "added", "failed", "preview", "reason")


def apple_log(operation: str, **fields) -> None:
    """Record one step. Unknown fields are dropped, including secrets."""
    if operation not in _OPERATIONS:
        return
    safe = {}
    for key in _ORDER:
        if key not in fields:
            continue
        value = fields[key]
        if key in _COUNTS:
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            safe[key] = max(0, min(100000, value))
            continue
        if key not in _LABELS or not isinstance(value, str) or not _LABEL.fullmatch(value):
            continue
        safe[key] = value
    parts = [f"operation={operation}"]
    parts.extend(f"{key}={safe[key]}" for key in _ORDER if key in safe)
    _log.info(" ".join(parts))


_TRACE_STAGES = {"send", "reply"}
_TRACE_ID = re.compile(r"[0-9a-f]{8,32}")
_TRACE_OP = re.compile(r"[a-z_]{1,32}")


def apple_trace(stage: str, request_id: str, op: str, result: str = "", ms: int = 0) -> None:
    """One protocol line. Only an id, an operation, a result and a duration."""
    if stage not in _TRACE_STAGES or not isinstance(request_id, str) or not _TRACE_ID.fullmatch(request_id):
        return
    if not isinstance(op, str) or not _TRACE_OP.fullmatch(op):
        return
    if result and (not isinstance(result, str) or not _LABEL.fullmatch(result)):
        return
    if isinstance(ms, bool) or not isinstance(ms, int):
        ms = 0
    ms = max(0, min(600000, ms))
    parts = [f"operation=protocol stage={stage} id={request_id} op={op}"]
    if result:
        parts.append(f"result={result}")
    parts.append(f"ms={ms}")
    _log.info(" ".join(parts))


def apple_note(text: str) -> None:
    """Forward one companion diagnostic. Secret words and their values are removed."""
    if not isinstance(text, str) or not text:
        return
    clean = _SECRET_ASSIGNMENT.sub("(redacted)", text)
    clean = _SECRET_WORD.sub("(redacted)", clean)
    clean = re.sub(r"[\x00-\x1f]", " ", clean)
    clean = re.sub(r" {2,}", " ", clean).strip()
    if not clean:
        return
    if len(clean) > 400:
        clean = clean[:400]
    _log.info("operation=companion %s", clean)
