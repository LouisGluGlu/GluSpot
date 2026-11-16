"""Opt-in lyric lookup/loader evidence without lyric text, URLs or credentials."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys


_log = logging.getLogger("GluSpot.lyrics")
_log.setLevel(logging.INFO)
if not _log.handlers and sys.stderr is not None:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    _log.addHandler(_handler)
    _log.propagate = False

_metrics = frozenset({
    "raw_rows", "raw_timed", "display_rows", "display_timed", "waiting", "visible",
    "hold_ms", "age_ms", "store_closed", "store_active", "store_pending", "store_failed",
    "resize_frozen", "resize_capturing", "width_moving", "epoch", "current_epoch",
    "generation", "current_generation", "elapsed_ms", "rows", "timed", "error",
})
_stages = frozenset({"surface", "owned_lookup", "owned_completion", "owned_publish"})
_reasons = frozenset({
    "resize_motion", "resize_settle", "provider_pending", "preparation_closed", "preparation_failed",
    "preparation_pending", "result_cycle_exit", "preparation_failed_exit", "resolved",
    "untimed_or_empty_exit", "fetch_begin", "fetch_cancelled", "fetch_failed", "official_unavailable",
    "parse_failed", "fetch_ready", "fetch_empty", "stale_epoch", "result_error", "result_received",
    "match_applied", "lyrics_unavailable",
})


def lyric_debug_enabled() -> bool:
    return os.environ.get("GLUSPOT_LYRIC_DEBUG") == "1"


def log_lyric_event(stage: str, track: str, reason: str, **metrics) -> None:
    """Emit controlled labels, a track hash and numeric state only."""
    if not lyric_debug_enabled() or stage not in _stages or reason not in _reasons:
        return
    identity = hashlib.sha256(str(track).encode("utf-8", errors="replace")).hexdigest()[:16]
    labels = [re.sub(r"[^a-zA-Z0-9_.-]", "_", str(value))[:64] for value in (stage, reason)]
    values = " ".join(
        f"{key}={int(value) if isinstance(value, bool) else value}"
        for key, value in metrics.items()
        if key in _metrics and isinstance(value, (int, bool))
    )
    _log.info("lyric stage=%s track=%s reason=%s %s", labels[0], identity, labels[1], values)
