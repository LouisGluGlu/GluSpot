"""Sea-level analysis for Apple Music songs, borrowed from a matching recording.

Apple Music plays inside its signed-in page, so GluSpot never has the audio
file the sea level analyses (drum lights, the colour change). When the
listener is also signed in to NetEase, the same recording is usually there:
same title, same artist, the same length to within a second or two. That
file is downloaded once through the existing stream cache, analysed exactly
like a local song, and only the analysis (times, no audio) is kept on disk,
so the next play of the song has its lights and colour change at once.

No match, a preview-only stream or a length that differs by more than
``MAX_LENGTH_GAP`` seconds means no borrowed analysis: the field then stays
one calm colour with no lights, which is the fallback the user asked for.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from pathlib import Path

_log = logging.getLogger("GluSpot.sea")

MAX_LENGTH_GAP = 2.0
MISS_RETRY_SECONDS = 24 * 3600.0
STORE_LIMIT = 400
_FORMAT = 1


def store_dir() -> Path:
    from local.branding import local_app_data_dir
    return Path(local_app_data_dir()) / "sea-plans"


def plan_key(song) -> str:
    """One key per Apple recording: the catalog id when known."""
    ident = str(getattr(song, "catalog_id", "") or "") or str(getattr(song, "song_id", "") or "")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", ident)[:80]
    return f"apple-{safe}" if safe else ""


def _path(key: str, root: Path | None = None) -> Path:
    return (root or store_dir()) / f"{key}.json"


def load(key: str, root: Path | None = None) -> dict | None:
    """The stored analysis, ``{"miss": ...}`` for a recent no-match, or None."""
    if not key:
        return None
    try:
        data = json.loads(_path(key, root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != _FORMAT:
        return None
    if data.get("miss"):
        if time.time() - float(data.get("at", 0)) > MISS_RETRY_SECONDS:
            return None
        return data
    return data if isinstance(data.get("drums"), list) else None


def save(key: str, data: dict, root: Path | None = None) -> None:
    if not key:
        return
    folder = root or store_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = _path(key, folder)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(data, format=_FORMAT, at=time.time())), encoding="utf-8")
        os.replace(temporary, target)
        files = sorted(folder.glob("*.json"), key=lambda item: item.stat().st_mtime)
        for old in files[:-STORE_LIMIT]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def plan_data(plan) -> dict:
    """Only times and strengths; nothing that identifies the audio."""
    return {
        "change_end": plan.change_end,
        "drums": [list(item) for item in plan.drums],
        "syllables": [list(item) for item in plan.syllables],
        "pushes": [list(item) for item in plan.pushes],
        "word_switch": list(plan.word_switch) if plan.word_switch else None,
        "audio_seconds": plan.audio_seconds,
    }


def _normal(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    text = re.sub(r"[\(\[（【].*?[\)\]）】]", " ", text)           # (feat. …), (Remastered)
    text = re.sub(r"\s+-\s+.*$", " ", text)                       # "Title - Single Version"
    return re.sub(r"[\W_]+", "", text)


def _artists(text: str) -> set[str]:
    parts = re.split(r"[,&/、，;；]| feat\.? | ft\.? | x | and ", unicodedata.normalize("NFKC", str(text or "")).casefold())
    return {_normal(part) for part in parts if _normal(part)}


def best_match(candidates, title: str, artist: str, duration: float):
    """The candidate with the same title, a shared artist and the closest length."""
    want_title, want_artists = _normal(title), _artists(artist)
    if not want_title:
        return None
    best, best_gap = None, None
    for song in candidates:
        if getattr(song, "media_kind", "song") != "song":
            continue
        name = _normal(getattr(song, "title", ""))
        if not name or not (name == want_title or (min(len(name), len(want_title)) >= 4
                                                   and (name in want_title or want_title in name))):
            continue
        if want_artists and not (want_artists & _artists(getattr(song, "artist", ""))):
            continue
        length = float(getattr(song, "duration_seconds", 0) or 0)
        if duration > 0 and (length <= 0 or abs(length - duration) > MAX_LENGTH_GAP):
            continue
        gap = abs(length - duration) if duration > 0 else 0.0
        if best is None or gap < best_gap:
            best, best_gap = song, gap
    return best


def decode(path: str):
    """8 kHz mono PCM of a file, on a short-lived decoding thread."""
    from local.sea_lyric_background import _DecodeThread
    worker = _DecodeThread(str(path))
    worker.start()
    if not worker.wait(150000):
        worker.requestInterruption()
        worker.quit()
        worker.wait(2000)
        return None
    # No deleteLater: this worker thread has no event loop; Python frees it.
    return worker.pcm


def borrow(service, stream_cache, song, *, cancelled=lambda: False, root: Path | None = None) -> dict | None:
    """Worker thread: find, fetch and analyse the matching NetEase recording.

    Returns the stored analysis, or None. A definite no-match is remembered
    for a day so a song without a twin is not searched on every play.
    """
    from local.sea_lyric_background import RATE, SeaPlan, analyse
    from PySide6.QtGui import QColor

    key = plan_key(song)
    if not key or stream_cache is None:
        return None
    query = f"{song.title} {song.artist}".strip()
    try:
        page = service.search("netease", query, kind="songs", limit=10, offset=0)
    except Exception as error:
        _log.info("sea stage=borrow result=search-error kind=%s", getattr(error, "code", type(error).__name__))
        return None
    match = best_match(getattr(page, "items", ()), song.title, song.artist, float(song.duration_seconds or 0))
    if match is None:
        _log.info("sea stage=borrow result=no-match")
        save(key, {"miss": True}, root)
        return None
    if cancelled():
        return None
    try:
        stream = service.stream_url("netease", match.song_id)
    except Exception as error:
        _log.info("sea stage=borrow result=stream-error kind=%s", getattr(error, "code", type(error).__name__))
        return None
    if not getattr(stream, "playable", False) or getattr(stream, "trial", False):
        # A preview clip cannot place a colour change in a whole song.
        _log.info("sea stage=borrow result=preview-only")
        save(key, {"miss": True}, root)
        return None
    prepared = None
    try:
        prepared = stream_cache.prepare(stream, cancelled)
        if cancelled():
            return None
        pcm = decode(str(prepared.path))
    except Exception as error:
        _log.info("sea stage=borrow result=download-error kind=%s", getattr(error, "code", type(error).__name__))
        return None
    finally:
        if prepared is not None:
            stream_cache.release(prepared)
    if pcm is None or pcm.size < RATE * 30:
        _log.info("sea stage=borrow result=short")
        return None
    plan = SeaPlan(QColor(), "white", QColor(), "white")
    plan.audio_seconds = pcm.size / RATE
    analyse(pcm, plan)
    data = plan_data(plan)
    save(key, data, root)
    _log.info("sea stage=borrow result=ok audio_s=%.0f gap_s=%.1f change=%s drums=%d", plan.audio_seconds,
              abs(float(match.duration_seconds or 0) - float(song.duration_seconds or 0)),
              "none" if plan.change_end is None else f"{plan.change_end:.1f}", len(plan.drums))
    return data
