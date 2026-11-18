"""Background audio check for playlist-sync candidates.

For every song that needs the user's choice, fetch the source and each
candidate, decode them to 8 kHz mono and compare (api.audio_compare). Apple
Music only gives a 30-second preview, so that clip is located inside the
NetEase recording. A candidate that sounds the same is chosen automatically;
the others keep the passages that differ, for the user to listen to.

Network and decoding happen on this QThread; results arrive as signals.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import urllib.request
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from api.apple_music_sync import song_key
from api.audio_compare import AudioVerdict, compare

_log = logging.getLogger("GluSpot.sync")
MAX_DOWNLOAD = 60 * 1024 * 1024
TIMEOUT = 20


def pair_key(source, candidate) -> str:
    return f"{source.source_id}:{song_key(source)}|{candidate.source_id}:{song_key(candidate)}"


class VerdictCache:
    """Remembered results, so a later preview does not fetch the same audio again."""

    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}
        if self.path is not None and self.path.is_file():
            try:
                body = json.loads(self.path.read_text(encoding="utf-8"))
                self._rows = {str(k): v for k, v in (body.get("pairs") or {}).items() if isinstance(v, dict)}
            except (OSError, ValueError, AttributeError):
                self._rows = {}

    def get(self, key: str) -> AudioVerdict | None:
        row = self._rows.get(key)
        if row is None:
            return None
        try:
            return AudioVerdict(bool(row["same"]), float(row["score"]), float(row.get("offset_seconds", 0.0)),
                                tuple((float(a), float(b)) for a, b in row.get("differing", ())),
                                bool(row.get("found", True)))
        except (KeyError, TypeError, ValueError):
            return None

    def put(self, key: str, verdict: AudioVerdict) -> None:
        with self._lock:
            self._rows[key] = asdict(verdict)
            if self.path is None:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"version": 1, "pairs": self._rows}, ensure_ascii=False), encoding="utf-8")
                temporary.replace(self.path)
            except OSError:
                pass


def decode_file(path: str):
    """8 kHz mono float PCM, through the same QAudioDecoder path as the sea-level background."""
    from local.sea_lyric_background import _DecodeThread
    worker = _DecodeThread(path)
    worker.run()  # runs in the calling thread, with its own event loop
    return worker.pcm


class AudioSource:
    """Where each song's audio comes from. Overridable in tests."""

    def __init__(self, service):
        self.service = service

    def url(self, song) -> str:
        if song.source_id == "apple_music":
            if song.preview_url:
                return song.preview_url
            return self._apple_preview_by_search(song)
        info = self.service.stream_url(song.source_id, song.song_id)
        return info.url if info.playable else ""

    def _apple_preview_by_search(self, song) -> str:
        # Library rows may lack the preview; the catalog search result has it.
        page = self.service.search("apple_music", f"{song.title} {song.artist}".strip()[:200], limit=8)
        wanted = song.catalog_id or song.song_id
        for item in page.items:
            if item.song_id == wanted and getattr(item, "preview_url", ""):
                return item.preview_url
        return ""

    def pcm(self, song):
        url = self.url(song)
        if not url:
            return None
        handle, path = tempfile.mkstemp(prefix="gluspot-compare-", suffix=".audio")
        os.close(handle)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "GluSpot"})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(path, "wb") as out:
                size = 0
                while True:
                    block = response.read(256 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > MAX_DOWNLOAD:
                        return None
                    out.write(block)
            return decode_file(path)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


def judge(source, source_pcm, candidate, candidate_pcm) -> AudioVerdict:
    """The Apple side is the 30-second clip; locate it inside the other recording."""
    if source.source_id == "apple_music" or len(source_pcm) <= len(candidate_pcm):
        return compare(source_pcm, candidate_pcm)
    return compare(candidate_pcm, source_pcm)


class AudioCompareJob(QThread):
    """Compare every candidate of every choice. Emits progress(done, total)."""

    progress = Signal(int, int)
    verdictReady = Signal(str, str, object)   # source key, candidate key, AudioVerdict
    done = Signal(int, int)                   # compared songs, songs that now need nobody

    def __init__(self, choices, audio: AudioSource, cache: VerdictCache, parent=None):
        super().__init__(parent)
        self.choices = tuple(choices)
        self.audio = audio
        self.cache = cache
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        total = len(self.choices)
        resolved = 0
        self.progress.emit(0, total)
        for index, item in enumerate(self.choices):
            if self._stop.is_set():
                break
            if self._check(item):
                resolved += 1
            self.progress.emit(index + 1, total)
        self.done.emit(total, resolved)

    def _check(self, item) -> bool:
        source = item.source
        source_pcm = None
        same = False
        for candidate in item.candidates:
            if self._stop.is_set():
                return same
            key = pair_key(source, candidate)
            verdict = self.cache.get(key)
            if verdict is None:
                try:
                    if source_pcm is None:
                        source_pcm = self.audio.pcm(source)
                    if source_pcm is None or len(source_pcm) == 0:
                        _log.info("sync stage=compare result=no-source")
                        return same
                    candidate_pcm = self.audio.pcm(candidate)
                    if candidate_pcm is None or len(candidate_pcm) == 0:
                        continue
                    verdict = judge(source, source_pcm, candidate, candidate_pcm)
                except Exception as error:  # noqa: BLE001 - one song must not stop the queue
                    _log.info("sync stage=compare result=error kind=%s", type(error).__name__)
                    continue
                self.cache.put(key, verdict)
            self.verdictReady.emit(song_key(source), song_key(candidate), verdict)
            same = same or verdict.same
        return same
