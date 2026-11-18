"""NetEase's own chorus time for a song, cached on disk.

``/api/song/chorus`` answers without a cookie:
``{"code": 200, "chorus": [{"id": 103841, "startTime": 41844, "endTime": 64356, ...}]}``
with times in milliseconds. It is NetEase's highlight segment: usually the
first chorus, sometimes a later chorus, the bridge or the pre-chorus, so
``local.sea_climax`` checks it against the lyric before trusting it.

Only the song id is sent and only (id, start seconds) is stored; no cookie
or account is used.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_log = logging.getLogger("GluSpot.sea")

URL = "https://music.163.com/api/song/chorus"
HEADERS = {"Referer": "https://music.163.com/", "Origin": "https://music.163.com",
           "User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"}
TIMEOUT_S = 4.0
MISS_RETRY_S = 7 * 24 * 3600.0       # a song without a chorus is asked again a week later
FAIL_RETRY_S = 15 * 60.0             # a failed request is asked again after a while
LIMIT = 4000
_lock = threading.Lock()
_memory: dict[str, dict] | None = None
_failed: dict[str, float] = {}


def song_id(track_id: str) -> str:
    """The NetEase song id in an owned track id (``owned:netease:123``), or ""."""
    parts = str(track_id or "").split(":")
    if len(parts) >= 3 and parts[0] == "owned" and parts[1].casefold() in {"netease", "cloudmusic"} and parts[2].isdigit():
        return parts[2]
    return ""


def _path() -> Path:
    from local.branding import local_app_data_dir
    return Path(local_app_data_dir()) / "sea-plans" / "netease-chorus.json"


def _load(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _save(path: Path, data: dict[str, dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if len(data) > LIMIT:
            for key in sorted(data, key=lambda k: data[k].get("at", 0))[: len(data) - LIMIT]:
                data.pop(key, None)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


def parse(body: dict) -> float | None:
    """The first chorus start in seconds, or None."""
    for item in (body or {}).get("chorus") or ():
        start = item.get("startTime") if isinstance(item, dict) else None
        if isinstance(start, (int, float)) and start > 0:
            return float(start) / 1000.0
    return None


def fetch(ident: str, opener=urlopen) -> float | None:
    body = urlencode({"ids": json.dumps([int(ident)])}).encode("ascii")
    request = Request(URL, data=body, headers=HEADERS, method="POST")
    with opener(request, timeout=TIMEOUT_S) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise ValueError("chorus response")
    return parse(payload)


def chorus_start(ident: str, *, path: Path | None = None, opener=urlopen, clock=time.time) -> float | None:
    """NetEase's chorus start for a song id, from the cache or one request.

    Blocking (call it off the UI thread). None for no chorus, no id or a
    failed request.
    """
    global _memory
    ident = str(ident or "")
    if not re.fullmatch(r"\d{1,20}", ident):
        return None
    target = path or _path()
    with _lock:
        if _memory is None or path is not None:
            _memory = _load(target)
        entry = _memory.get(ident)
        if entry is not None:
            start = entry.get("start")
            if isinstance(start, (int, float)):
                return float(start)
            if clock() - float(entry.get("at", 0)) < MISS_RETRY_S:
                return None
        if clock() - _failed.get(ident, -1e18) < FAIL_RETRY_S:
            return None
    try:
        start = fetch(ident, opener)
    except (HTTPError, URLError, OSError, TimeoutError, ValueError) as error:
        with _lock:
            _failed[ident] = clock()
        _log.info("sea stage=chorus result=error kind=%s", type(error).__name__)
        return None
    with _lock:
        _memory[ident] = {"start": start, "at": clock()}
        _save(target, dict(_memory))
    _log.info("sea stage=chorus result=%s", "none" if start is None else f"{start:.1f}")
    return start
