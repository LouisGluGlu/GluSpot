from __future__ import annotations

import base64
import ctypes
import html
import json
import math
import os
import re
import sys
import threading
import time
import unicodedata
from collections import OrderedDict
from ctypes import wintypes
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from api.models import TimedLyricLine, TimedLyricUnit
from api.cantonese import (
    cantonese_secondary_rows, group_cantonese_plain_lines,
    group_secondary_source_rows, has_cantonese_evidence,
)
from api.lyric_timeline import genuinely_timed_unit_row

LRC_TIME_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
LRC_OFFSET_RE = re.compile(r"^\[offset\s*:\s*([+-]?\d+)\s*\]$", re.I)
ENHANCED_LRC_TIME_RE = re.compile(r"<\d{1,2}:\d{2}(?:[.:]\d{1,3})?>")
ENHANCED_LRC_UNIT_RE = re.compile(r"<(?P<minutes>\d{1,2}):(?P<seconds>\d{2})(?:[.:](?P<fraction>\d{1,3}))?>")
YRC_LINE_RE = re.compile(r"^\[(\d+)\s*,\s*(\d+)\](.*)$")
YRC_WORD_RE = re.compile(r"\(\d+\s*,\s*\d+(?:\s*,\s*\d+)?\)")
YRC_UNIT_RE = re.compile(r"\(\s*(?P<start>\d+)\s*,\s*(?P<duration>\d+)\s*,\s*\d+\s*\)")
QRC_UNIT_RE = re.compile(r"\(\s*(?P<start>\d+)\s*,\s*(?P<duration>\d+)\s*\)")
KRC_UNIT_RE = re.compile(r"<\s*(?P<start>\d+)\s*,\s*(?P<duration>\d+)\s*,\s*\d+\s*>")
QRC_CONTENT_RE = re.compile(r"<Lyric_1\b[^>]*\bLyricContent\s*=\s*([\"'])(.*?)\1", re.I | re.S)
JSONP_RE = re.compile(r"^[A-Za-z_][\w.]*\((.*)\)\s*;?\s*$", re.S)
METADATA_RE = re.compile(r"^\[(?:ar|ti|al|by|offset|length|re|ve|kana|language):.*\]$", re.I)
ONLINE_SOURCE_ORDER = ("netease", "qq", "lrclib")
SECONDARY_LABEL_RE = re.compile(
    r"^\s*(?:翻译|译文|中文翻译|粤语译文|粵語譯文|粤语音译|粵語音譯|"
    r"音译|音譯|粤拼|粵拼|jyutping|translation|translated|romanization)\s*[:：]\s*", re.I,
)
SECONDARY_SPEAKER_RE = re.compile(r"^\s*[^:：\n]{1,24}[:：]\s*")
SECONDARY_PARENTHESES_RE = re.compile(r"^\s*(?:\([^()]*\)|（[^（）]*）)\s*$")
SECONDARY_HAN_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
SECONDARY_LATIN_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
SECONDARY_KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
SECONDARY_HANGUL_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")
TRADITIONAL_TO_SIMPLIFIED_FALLBACK = str.maketrans(
    {
        "後": "后",
        "來": "来",
        "風": "风",
        "裏": "里",
        "裡": "里",
        "妳": "你",
        "雲": "云",
        "愛": "爱",
        "聽": "听",
        "說": "说",
        "夢": "梦",
        "臺": "台",
        "台": "台",
        "門": "门",
        "開": "开",
        "關": "关",
        "萬": "万",
        "與": "与",
        "為": "为",
        "麼": "么",
        "歲": "岁",
        "時": "时",
        "間": "间",
        "會": "会",
        "無": "无",
        "還": "还",
        "點": "点",
        "聲": "声",
        "誰": "谁",
        "獨": "独",
        "遠": "远",
        "邊": "边",
        "顏": "颜",
        "難": "难",
        "記": "记",
        "憶": "忆",
        "終": "终",
        "輕": "轻",
        "過": "过",
        "長": "长",
        "舊": "旧",
        "別": "别",
        "葉": "叶",
        "華": "华",
        "淚": "泪",
    }
)
SIMPLIFIED_TO_TRADITIONAL_FALLBACK = str.maketrans({value: key for key, value in TRADITIONAL_TO_SIMPLIFIED_FALLBACK.items()})


@dataclass(frozen=True)
class LyricsMatch:
    timed_lyrics: tuple[TimedLyricLine, ...]
    lyrics: tuple[str, ...]
    source: str = ""
    # Preserve source alternatives for preference changes and generated-cache
    # reloads. The provider still consumes only the selected timed_lyrics.
    secondary_source_rows: tuple[TimedLyricLine, ...] = ()


class LocalLyricsStore:
    RESOLVED_CACHE_LIMIT = 128
    MISSED_CACHE_LIMIT = 256
    LOCAL_MISS_CACHE_LIMIT = 256
    LOCAL_FUZZY_FILE_LIMIT = 256
    TRANSIENT_MISS_RETRY_SECONDS = 45.0
    PERSISTED_CACHE_FILE_LIMIT = 96
    PERSISTED_CACHE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
    ENRICH_RETRY_SECONDS = 30 * 60.0
    ENRICH_ATTEMPT_LIMIT = 256
    GENERATED_CACHE_DIR_ENV = "GLUSPOT_LYRICS_CACHE_DIR"
    GENERATED_CACHE_MARKER = "[re:GluSpot generated cache v1]"

    def __init__(self, root: Path | None = None, request_timeout: float = 4.5, generated_root: Path | None = None, settings_getter=None):
        root_provided = root is not None
        self.root = Path(root) if root_provided else Path(__file__).resolve().parents[1] / "lyrics"
        if generated_root is not None:
            self.generated_root = Path(generated_root)
        elif root_provided:
            self.generated_root = self.root
        else:
            self.generated_root = self._default_generated_root(self.root)
        self.request_timeout = float(request_timeout)
        self.root.mkdir(parents=True, exist_ok=True)
        self.generated_root.mkdir(parents=True, exist_ok=True)
        self._generated_root_identity = self.generated_root.resolve()
        self._legacy_source_hits_path = self.root / ".source_hits.json"
        self._source_hits_path = self.generated_root / ".source_hits.json"
        self._source_hit_counts = self._load_source_hit_counts()
        self._indexed_local_lookup_enabled = root_provided
        self._resolved_cache: OrderedDict[str, LyricsMatch] = OrderedDict()
        self._pending_keys: set[str] = set()
        self._enrich_pending_keys: set[str] = set()
        self._enrich_attempted_at: OrderedDict[str, float] = OrderedDict()
        self._enrich_retry_seconds: dict[str, float] = {}
        self._resolved_local_paths: OrderedDict[str, Path] = OrderedDict()
        self._missed_at: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._miss_retry_seconds = 600.0
        # Publication, disk replacement and manual cache clearing share one
        # lock so a pre-clear lookup cannot restore a discarded result.
        self._cache_lock = threading.RLock()
        self._cache_generation = 0
        self._local_miss_cache: OrderedDict[str, tuple[tuple[str, int], ...]] = OrderedDict()
        self._lyric_index_cache: dict[str, tuple[int, dict[str, tuple[Path, ...]]]] = {}
        self._lyric_index_lock = threading.Lock()
        self.settings_getter = settings_getter or (lambda _key, default=None: default)

    @classmethod
    def _default_generated_root(cls, source_root: Path) -> Path:
        override = str(os.environ.get(cls.GENERATED_CACHE_DIR_ENV, "") or "").strip()
        if override:
            return Path(override)
        base_dir = str(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or "").strip()
        if base_dir:
            return Path(base_dir) / "GluSpot" / "lyrics-cache"
        return Path(source_root) / ".generated"

    def clear_generated_cache(self) -> dict:
        """Discard online results without touching the local lyric library."""
        result = {"deleted_files": 0, "deleted_bytes": 0, "errors": []}
        with self._cache_lock:
            self._cache_generation += 1
            self._resolved_cache.clear()
            self._resolved_local_paths.clear()
            self._missed_at.clear()
            self._local_miss_cache.clear()
            self._pending_keys.clear()
            self._enrich_pending_keys.clear()
            self._enrich_attempted_at.clear()
            self._enrich_retry_seconds.clear()
            with self._lyric_index_lock:
                self._lyric_index_cache.clear()
            try:
                directory = Path(self.generated_root)
                is_junction = getattr(directory, "is_junction", lambda: False)
                if directory.is_symlink() or is_junction():
                    raise OSError("Refusing to clear a linked lyric cache directory")
                if not directory.exists():
                    return result
                resolved = directory.resolve()
                if resolved != self._generated_root_identity:
                    raise OSError("Lyric cache directory changed since initialization")
                shared_with_library = resolved == Path(self.root).resolve()
                for path in directory.glob("*.lrc"):
                    try:
                        # Only direct, regular generated files are eligible.
                        # Never follow a file link or recurse into directories.
                        if path.is_symlink() or not path.is_file():
                            continue
                        if path.resolve().parent != resolved or not self._is_generated_online_cache(path):
                            continue
                        if shared_with_library:
                            # A user-imported LRC may credit an online source.
                            # In a shared library, only our explicit writer
                            # marker proves that it is disposable generated data.
                            with path.open("r", encoding="utf-8-sig") as handle:
                                if self.GENERATED_CACHE_MARKER not in handle.read(1024).splitlines():
                                    continue
                        size = path.stat().st_size
                        path.unlink()
                        result["deleted_files"] += 1
                        result["deleted_bytes"] += size
                    except (OSError, RuntimeError, UnicodeError) as exc:
                        result["errors"].append(f"{path.name}: {exc}")
            except (OSError, RuntimeError) as exc:
                result["errors"].append(str(exc))
        return result

    def find(self, title: str, artist: str) -> tuple[tuple[TimedLyricLine, ...], str]:
        lines, source = self._read_local(title, artist)
        return lines, source

    def resolve_cached_or_local(self, title: str, artist: str, album_title: str = "", duration_seconds: float = 0.0) -> LyricsMatch:
        cache_key = self._cache_key(title, artist, album_title, duration_seconds)
        with self._cache_lock:
            generation = self._cache_generation
            cached = self._resolved_cache.get(cache_key)
            if cached is not None:
                self._resolved_cache.move_to_end(cache_key)
                if cache_key in self._resolved_local_paths:
                    self._resolved_local_paths.move_to_end(cache_key)
        if cached is not None:
            return cached

        match, local_source = self._read_local_match(title, artist)
        if match.timed_lyrics:
            if not self._store_cache(cache_key, match, generation=generation):
                return LyricsMatch((), (), "")
            with self._cache_lock:
                if generation != self._cache_generation:
                    return LyricsMatch((), (), "")
                self._resolved_local_paths[cache_key] = Path(local_source)
                self._resolved_local_paths.move_to_end(cache_key)
                while len(self._resolved_local_paths) > int(getattr(self, "RESOLVED_CACHE_LIMIT", 128)):
                    self._resolved_local_paths.popitem(last=False)
            return match

        # A retained song can have no generated file after an unavailable
        # cache write or disk pruning. Flipping only the shared translation
        # switch must still supply the known original to the enrichment path.
        # Local lookup above keeps a newly authored manual file authoritative.
        identity, separator, preferences = cache_key.rpartition("\0")
        dual, preference_separator, unchanged_preferences = preferences.partition("|")
        if separator and preference_separator and dual in {"dual", "single"}:
            alternate_dual = "single" if dual == "dual" else "dual"
            sibling_key = f"{identity}\0{alternate_dual}|{unchanged_preferences}"
            with self._cache_lock:
                sibling = self._resolved_cache.get(sibling_key)
                sibling_path = self._resolved_local_paths.get(sibling_key)
            if sibling is not None and (sibling.timed_lyrics or sibling.lyrics):
                inherited = self._finalize_lyrics_match(sibling)
                if not self._store_cache(cache_key, inherited, generation=generation):
                    return LyricsMatch((), (), "")
                if sibling_path is not None:
                    with self._cache_lock:
                        if generation == self._cache_generation and cache_key in self._resolved_cache:
                            self._resolved_local_paths[cache_key] = sibling_path
                            self._resolved_local_paths.move_to_end(cache_key)
                return inherited
        return LyricsMatch((), (), "")

    def resolve_in_background(self, title: str, artist: str, album_title: str = "", duration_seconds: float = 0.0) -> bool:
        cache_key = self._cache_key(title, artist, album_title, duration_seconds)
        with self._cache_lock:
            if cache_key in self._resolved_cache or cache_key in self._pending_keys:
                return False
            missed_at, retry_seconds = self._miss_record(cache_key)
            if missed_at > 0.0:
                self._missed_at.move_to_end(cache_key)
            if missed_at and time.monotonic() - missed_at < retry_seconds:
                return False
            self._pending_keys.add(cache_key)
            generation = self._cache_generation
        thread = threading.Thread(
            target=self._resolve_background,
            args=(cache_key, title, artist, album_title, duration_seconds, generation),
            name="LyricsResolver",
            daemon=True,
        )
        thread.start()
        return True

    def enrich_in_background(
        self, title: str, artist: str, album_title: str = "", duration_seconds: float = 0.0, unit_timing_only: bool = False
    ) -> bool:
        cache_key = self._cache_key(title, artist, album_title, duration_seconds)
        with self._cache_lock:
            if cache_key in self._enrich_pending_keys:
                return False
            selected_path = self._resolved_local_paths.get(cache_key)
            if selected_path is not None and selected_path.is_file() and not self._is_generated_online_cache(selected_path):
                # This source was selected explicitly from the local library.
                # Its precedence also makes an online companion request
                # ineligible; completion protection still covers later files.
                return False
            now = time.monotonic()
            last_attempt = self._enrich_attempted_at.get(cache_key)
            retry_seconds = self._enrich_retry_seconds.get(cache_key, float(self.ENRICH_RETRY_SECONDS))
            if last_attempt is not None and now - last_attempt < retry_seconds:
                return False
            if unit_timing_only:
                cached = self._resolved_cache.get(cache_key)
                path = self._resolved_local_paths.get(cache_key)
                if cached is None or path is None or any(line.units for line in cached.timed_lyrics):
                    return False
                # This is a generated online result, never a hand-authored
                # local lyric. A source-root match takes precedence and is
                # deliberately ineligible even if a generated peer exists.
                if not self._is_generated_online_cache(path):
                    return False
            self._enrich_attempted_at[cache_key] = now
            self._enrich_attempted_at.move_to_end(cache_key)
            self._enrich_retry_seconds[cache_key] = float(self.ENRICH_RETRY_SECONDS)
            while len(self._enrich_attempted_at) > int(self.ENRICH_ATTEMPT_LIMIT):
                expired_key, _timestamp = self._enrich_attempted_at.popitem(last=False)
                self._enrich_retry_seconds.pop(expired_key, None)
            self._enrich_pending_keys.add(cache_key)
            generation = self._cache_generation
        thread = threading.Thread(
            target=self._enrich_background,
            args=(cache_key, title, artist, album_title, duration_seconds, unit_timing_only, generation),
            name="LyricsEnricher",
            daemon=True,
        )
        thread.start()
        return True

    def _is_generated_online_cache(self, path: Path) -> bool:
        try:
            if path.is_symlink():
                return False
            if path.parent.resolve() != self.generated_root.resolve():
                return False
            with path.open("r", encoding="utf-8-sig") as handle:
                prefix = handle.read(1024)
        except (OSError, UnicodeError):
            return False
        return bool(re.search(r"^\[by\s*:\s*(?:netease|qq|lrclib)\s*\]$", prefix, re.I | re.M))

    def is_lookup_pending(self, title: str, artist: str, album_title: str = "", duration_seconds: float = 0.0) -> bool:
        cache_key = self._cache_key(title, artist, album_title, duration_seconds)
        with self._cache_lock:
            return cache_key in self._pending_keys

    def resolve(self, title: str, artist: str, album_title: str = "", duration_seconds: float = 0.0) -> LyricsMatch:
        cache_key = self._cache_key(title, artist, album_title, duration_seconds)
        with self._cache_lock:
            generation = self._cache_generation
            cached = self._resolved_cache.get(cache_key)
            if cached is not None:
                self._resolved_cache.move_to_end(cache_key)
                if cache_key in self._resolved_local_paths:
                    self._resolved_local_paths.move_to_end(cache_key)
        if cached is not None:
            return cached

        local_match = self.resolve_cached_or_local(title, artist, album_title, duration_seconds)
        if local_match.timed_lyrics or local_match.lyrics:
            return local_match

        match = self._resolve_online(title, artist, album_title, duration_seconds)
        if match.timed_lyrics or match.lyrics:
            self._persist_match(title, artist, album_title, match, generation=generation)
            if not self._store_cache(cache_key, match, generation=generation):
                return LyricsMatch((), (), "")
        return match

    def _read_local(self, title: str, artist: str) -> tuple[tuple[TimedLyricLine, ...], str]:
        match, source = self._read_local_match(title, artist)
        return match.timed_lyrics, source

    def _read_local_match(self, title: str, artist: str) -> tuple[LyricsMatch, str]:
        miss_key = self._local_lookup_key(title, artist)
        roots_signature = self._local_roots_signature()
        with self._cache_lock:
            cached_signature = self._local_miss_cache.get(miss_key)
            if cached_signature == roots_signature:
                self._local_miss_cache.move_to_end(miss_key)
                return LyricsMatch((), ()), ""
        for path in self._candidate_paths(title, artist):
            match = self._read_lrc_match(path)
            if match.timed_lyrics:
                self._clear_local_miss(miss_key)
                return match, str(path)
        self._store_local_miss(miss_key, roots_signature)
        return LyricsMatch((), ()), ""

    def _candidate_paths(self, title: str, artist: str) -> tuple[Path, ...]:
        paths: list[Path] = []
        seen: set[str] = set()
        for directory in self._local_roots():
            for candidate in self._candidate_names(title, artist):
                path = Path(directory) / f"{candidate}.lrc"
                key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                paths.append(path)
        if bool(getattr(self, "_indexed_local_lookup_enabled", False)):
            for path in self._indexed_candidate_paths(title, artist):
                key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                paths.append(path)
        return tuple(paths)

    def _indexed_candidate_paths(self, title: str, artist: str) -> tuple[Path, ...]:
        candidate_keys = tuple(
            key
            for key in (self._normalize_text(candidate) for candidate in self._candidate_names(title, artist))
            if key
        )
        if not candidate_keys:
            return ()
        matches: list[Path] = []
        for directory in self._local_roots():
            index = self._lyric_file_index(directory)
            for key in candidate_keys:
                matches.extend(index.get(key, ()))
        if not matches:
            matches.extend(self._fuzzy_indexed_candidate_paths(title, artist))
        return tuple(matches)

    def _fuzzy_indexed_candidate_paths(self, title: str, artist: str) -> tuple[Path, ...]:
        query_title = self._normalize_text(title)
        if not query_title:
            return ()
        scored: list[tuple[float, str, Path]] = []
        for directory in self._local_roots():
            paths = self._lyric_file_paths(directory)
            if len(paths) > int(getattr(self, "LOCAL_FUZZY_FILE_LIMIT", 256)):
                continue
            for path in paths:
                file_artist, file_title = self._split_indexed_lyric_stem(path.stem)
                score = self._local_lyric_filename_score(title, artist, file_title, file_artist)
                if score >= 0.88:
                    scored.append((score, path.name.casefold(), path))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(path for _score, _name, path in scored[:4])

    def _local_roots(self) -> tuple[Path, ...]:
        roots: list[Path] = []
        seen: set[str] = set()
        for directory in (self.root, self.generated_root):
            path = Path(directory)
            key = self._path_cache_key(path)
            if key in seen:
                continue
            seen.add(key)
            roots.append(path)
        return tuple(roots)

    def _path_cache_key(self, path: Path) -> str:
        try:
            return str(Path(path).resolve())
        except OSError:
            return str(path)

    def _local_roots_signature(self) -> tuple[tuple[str, int], ...]:
        signature: list[tuple[str, int]] = []
        for directory in self._local_roots():
            try:
                timestamp = int(Path(directory).stat().st_mtime_ns)
            except OSError:
                timestamp = 0
            signature.append((self._path_cache_key(directory), timestamp))
        return tuple(signature)

    def _local_lookup_key(self, title: str, artist: str) -> str:
        return f"{self._normalize_text(title)}\0{self._normalize_text(artist)}"

    def _store_local_miss(self, key: str, roots_signature: tuple[tuple[str, int], ...]):
        with self._cache_lock:
            self._local_miss_cache[key] = roots_signature
            self._local_miss_cache.move_to_end(key)
            while len(self._local_miss_cache) > int(getattr(self, "LOCAL_MISS_CACHE_LIMIT", 256)):
                self._local_miss_cache.popitem(last=False)

    def _clear_local_miss(self, key: str):
        with self._cache_lock:
            self._local_miss_cache.pop(key, None)

    def _lyric_file_paths(self, directory: Path) -> tuple[Path, ...]:
        directory = Path(directory)
        try:
            signature = int(directory.stat().st_mtime_ns)
        except OSError:
            return ()
        try:
            cache_key = str(directory.resolve())
        except OSError:
            cache_key = str(directory)
        with self._lyric_index_lock:
            cached = self._lyric_index_cache.get(cache_key)
            if cached is not None and cached[0] == signature:
                cached_index = cached[1]
                return tuple(path for paths in cached_index.values() for path in paths)
        try:
            files = [path for path in directory.glob("*.lrc") if path.is_file()]
        except OSError:
            files = []
        files = sorted(files, key=lambda item: item.name.casefold())
        return tuple(files)

    def _split_indexed_lyric_stem(self, stem: str) -> tuple[str, str]:
        parts = re.split(r"\s+-\s+", stem, maxsplit=1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return "", stem

    def _local_lyric_filename_score(self, query_title: str, query_artist: str, file_title: str, file_artist: str) -> float:
        query_title_key = self._normalize_text(query_title)
        query_artist_key = self._normalize_text(query_artist)
        file_title_key = self._normalize_text(file_title)
        file_artist_key = self._normalize_text(file_artist)
        if not query_title_key or not file_title_key:
            return 0.0
        if query_title_key == file_title_key:
            title_score = 1.0
        elif self._local_title_starts_with_file_title(query_title, file_title):
            title_score = 0.96
        elif query_title_key in file_title_key and len(query_title_key) >= max(3, int(len(file_title_key) * 0.70)):
            title_score = 0.90
        else:
            title_score = SequenceMatcher(None, query_title_key, file_title_key).ratio()
        artist_score = 0.0
        if query_artist_key and file_artist_key:
            if query_artist_key == file_artist_key:
                artist_score = 1.0
            elif file_artist_key in query_artist_key or query_artist_key in file_artist_key:
                artist_score = 0.92
            else:
                artist_score = SequenceMatcher(None, query_artist_key, file_artist_key).ratio()
        if title_score < 0.88:
            return 0.0
        if file_artist_key and query_artist_key and artist_score < 0.55:
            return 0.0
        return title_score * 0.78 + max(artist_score, 0.55 if not file_artist_key or not query_artist_key else 0.0) * 0.22

    def _local_title_starts_with_file_title(self, query_title: str, file_title: str) -> bool:
        query_words = self._normalized_words(query_title)
        file_words = self._normalized_words(file_title)
        if not query_words or not file_words or len(file_words) > len(query_words):
            return False
        if query_words[: len(file_words)] == file_words:
            return True
        return False

    def _normalized_words(self, value: str) -> tuple[str, ...]:
        normalized = unicodedata.normalize("NFKC", value or "").casefold()
        return tuple(part for part in re.split(r"[^0-9a-z\u4e00-\u9fff]+", normalized) if part)

    def _lyric_file_index(self, directory: Path) -> dict[str, tuple[Path, ...]]:
        directory = Path(directory)
        try:
            signature = int(directory.stat().st_mtime_ns)
        except OSError:
            return {}
        try:
            cache_key = str(directory.resolve())
        except OSError:
            cache_key = str(directory)
        with self._lyric_index_lock:
            cached = self._lyric_index_cache.get(cache_key)
            if cached is not None and cached[0] == signature:
                return cached[1]
        grouped: dict[str, list[Path]] = {}
        try:
            files = [path for path in directory.glob("*.lrc") if path.is_file()]
        except OSError:
            files = []
        files.sort(key=lambda item: item.name.casefold())
        for path in files:
            key = self._normalize_text(path.stem)
            if not key:
                continue
            grouped.setdefault(key, []).append(path)
        index = {key: tuple(paths) for key, paths in grouped.items()}
        with self._lyric_index_lock:
            self._lyric_index_cache[cache_key] = (signature, index)
        return index

    def _candidate_names(self, title: str, artist: str) -> tuple[str, ...]:
        title_key = self._normalize_name(title)
        artist_key = self._normalize_name(artist)
        names: list[str] = []
        if artist_key and title_key:
            names.extend((f"{artist_key} - {title_key}", f"{title_key} - {artist_key}"))
        if title_key:
            names.append(title_key)
        seen: set[str] = set()
        result: list[str] = []
        for name in names:
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return tuple(result)

    def _normalize_name(self, value: str) -> str:
        value = unicodedata.normalize("NFKC", value or "").strip().lower()
        value = re.sub(r"[\\/:*?\"<>|]", " ", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip(" .")

    def _normalize_text(self, value: str) -> str:
        value = unicodedata.normalize("NFKC", value or "").casefold()
        value = re.sub(r"[\s\-_—·、,.;:：!?！？'\"“”‘’\[\]{}()（）<>《》/\\]+", "", value)
        return value

    def _cache_key(self, title: str, artist: str, album_title: str, duration_seconds: float) -> str:
        raw = f"{title}\0{artist}\0{self._cache_preference_key()}"
        return raw.casefold()

    def _setting_value(self, key: str, default=None):
        getter = getattr(self, "settings_getter", None)
        if not callable(getter):
            return default
        try:
            return getter(key, default)
        except Exception:
            return default

    def _cache_preference_key(self) -> str:
        dual = "dual" if self._dual_line_lyrics_enabled() else "single"
        return f"{dual}|{self._dual_line_secondary_source()}|{self._lyric_chinese_conversion_mode()}|{self._cantonese_priority()}"

    def _cantonese_priority(self) -> str:
        value = str(self._setting_value("v2_cantonese_secondary_priority", "phonetic_first") or "")
        return value if value in {"phonetic_first", "translation_first"} else "phonetic_first"

    def _dual_line_lyrics_enabled(self) -> bool:
        if bool(self._setting_value("v2_enable_dual_line_lyrics", False)):
            return True
        style = str(self._setting_value("v2_lyric_flow_style", "") or "").strip().casefold()
        return style == "ember_fade" and bool(self._setting_value("v2_ember_dual_line_enabled", False))

    def _dual_line_secondary_source(self) -> str:
        value = str(self._setting_value("v2_dual_line_secondary_source", "translation") or "translation").strip().casefold()
        if value in {"transliteration", "romanization", "romaji", "phonetic", "roma"}:
            return "transliteration"
        if value in {"none", "off", "disabled"}:
            return "none"
        return "translation"

    def _lyric_chinese_conversion_mode(self) -> str:
        value = str(self._setting_value("v2_lyric_chinese_conversion", "off") or "off").strip().casefold()
        if value in {"simplified", "to_simplified", "hans", "zh-hans", "zh_cn", "zh-cn"}:
            return "simplified"
        if value in {"traditional", "to_traditional", "hant", "zh-hant", "zh_tw", "zh-tw"}:
            return "traditional"
        return "off"

    def _store_cache(
        self, key: str, match: LyricsMatch, protect_manual: bool = False, manual_names: tuple[str, ...] = (),
        generation: int | None = None,
    ) -> bool:
        with self._cache_lock:
            if generation is not None and generation != self._cache_generation:
                return False
            if protect_manual:
                selected_path = self._resolved_local_paths.get(key)
                if selected_path is not None and selected_path.is_file() and not self._is_generated_online_cache(selected_path):
                    return False
                manual_path = self._manual_lyric_path(manual_names)
                if manual_path is not None:
                    manual_match = self._read_lrc_match(manual_path)
                    if manual_match.timed_lyrics:
                        self._resolved_cache[key] = manual_match
                        self._resolved_cache.move_to_end(key)
                        self._resolved_local_paths[key] = manual_path
                        self._missed_at.pop(key, None)
                    return False
            self._resolved_cache[key] = match
            self._resolved_cache.move_to_end(key)
            self._resolved_local_paths.pop(key, None)
            while len(self._resolved_cache) > int(getattr(self, "RESOLVED_CACHE_LIMIT", 128)):
                expired_key, _match = self._resolved_cache.popitem(last=False)
                self._resolved_local_paths.pop(expired_key, None)
            self._missed_at.pop(key, None)
        return True

    def _manual_lyric_path(self, names: tuple[str, ...]) -> Path | None:
        if not names:
            return None
        for candidate in names:
            path = self.root / f"{candidate}.lrc"
            if path.is_file() and not self._is_generated_online_cache(path):
                return path
        return None

    def _miss_record(self, key: str) -> tuple[float, float]:
        raw = self._missed_at.get(key)
        if raw is None:
            return 0.0, self._miss_retry_seconds
        if isinstance(raw, tuple):
            missed_at = float(raw[0] or 0.0)
            retry_seconds = float(raw[1] or self._miss_retry_seconds)
            return missed_at, retry_seconds
        try:
            return float(raw), self._miss_retry_seconds
        except (TypeError, ValueError):
            return 0.0, self._miss_retry_seconds

    def _store_miss(
        self, key: str, now: float | None = None, retry_seconds: float | None = None,
        generation: int | None = None,
    ):
        with self._cache_lock:
            if generation is not None and generation != self._cache_generation:
                return
            retry_window = self._miss_retry_seconds if retry_seconds is None else float(retry_seconds)
            self._missed_at[key] = (time.monotonic() if now is None else float(now), max(0.0, retry_window))
            self._missed_at.move_to_end(key)
            while len(self._missed_at) > int(getattr(self, "MISSED_CACHE_LIMIT", 256)):
                self._missed_at.popitem(last=False)

    def _resolve_background(
        self, cache_key: str, title: str, artist: str, album_title: str, duration_seconds: float,
        generation: int | None = None,
    ):
        with self._cache_lock:
            if generation is None:
                generation = self._cache_generation
            if generation != self._cache_generation:
                return
        match = LyricsMatch((), (), "")
        fallback_published = False
        fallback_match: LyricsMatch | None = None

        def publish_fallback(candidate: LyricsMatch) -> None:
            nonlocal fallback_published, fallback_match
            if fallback_published or not (candidate.timed_lyrics or candidate.lyrics):
                return
            # The generated cache writes atomically, then the in-memory
            # cache makes the first usable line visible while the same online
            # lookup continues seeking genuinely timed units.
            if self._store_cache(
                cache_key, candidate, protect_manual=True, manual_names=self._candidate_names(title, artist),
                generation=generation,
            ):
                self._persist_match(title, artist, album_title, candidate, generation=generation)
                fallback_published = True
                fallback_match = candidate

        try:
            try:
                match = self._resolve_online(title, artist, album_title, duration_seconds, publish_fallback)
            except Exception:
                match = fallback_match or LyricsMatch((), (), "")
            if match.timed_lyrics or match.lyrics:
                # Check for a manual file even if later sources only offered
                # the same fallback. A file created during the lookup must
                # replace its earlier in-memory provisional result.
                if self._store_cache(
                    cache_key, match, protect_manual=True, manual_names=self._candidate_names(title, artist),
                    generation=generation,
                ):
                    if not fallback_published or match != fallback_match:
                        self._persist_match(
                            title, artist, album_title, match, overwrite=fallback_published, generation=generation,
                        )
            else:
                # A completed lookup with no result is final for this cache key.
                # Reopening the search after the old 45 s miss timeout made
                # "暂无歌词" flip back to "查找歌词中" mid-song.
                self._store_miss(cache_key, retry_seconds=float("inf"), generation=generation)
        finally:
            # Publish the match/miss before clearing pending so readers can
            # never launch a duplicate lookup in the completion gap.
            with self._cache_lock:
                if generation == self._cache_generation:
                    self._pending_keys.discard(cache_key)

    def _enrich_background(
        self, cache_key: str, title: str, artist: str, album_title: str, duration_seconds: float,
        unit_timing_only: bool = False, generation: int | None = None,
    ):
        with self._cache_lock:
            if generation is None:
                generation = self._cache_generation
            if generation != self._cache_generation:
                return
        wants_secondary = not unit_timing_only and self._dual_line_lyrics_enabled()
        secondary_found = False
        try:
            with self._cache_lock:
                previous = self._resolved_cache.get(cache_key)
            match = self._resolve_online(title, artist, album_title, duration_seconds)
            if previous is not None:
                if any(line.timing_kind == "unit" and genuinely_timed_unit_row(line.units)
                       for line in previous.timed_lyrics):
                    # A translated line-only source may supplement the known
                    # original, but cannot replace its measured word clock.
                    match = self._retain_matching_subrows(previous, match)
                else:
                    match = self._retain_matching_subrows(match, previous)
            secondary_found = self.has_secondary_companion(match.timed_lyrics)
            has_actual_units = any(
                line.timing_kind == "unit" and genuinely_timed_unit_row(line.units)
                for line in match.timed_lyrics
            )
            has_multiline = match.timed_lyrics and self._has_multiline_timed_lyrics(match.timed_lyrics)
            if has_actual_units or (not unit_timing_only and has_multiline):
                if self._store_cache(
                    cache_key, match, protect_manual=True, manual_names=self._candidate_names(title, artist),
                    generation=generation,
                ):
                    self._persist_match(title, artist, album_title, match, overwrite=True, generation=generation)
        finally:
            with self._cache_lock:
                if generation == self._cache_generation and cache_key in self._enrich_attempted_at:
                    self._enrich_retry_seconds[cache_key] = (
                        float(self.TRANSIENT_MISS_RETRY_SECONDS)
                        if wants_secondary and not secondary_found else float(self.ENRICH_RETRY_SECONDS)
                    )
                if generation == self._cache_generation:
                    self._enrich_pending_keys.discard(cache_key)

    def _resolve_online(
        self,
        title: str,
        artist: str,
        album_title: str,
        duration_seconds: float,
        on_line_fallback: Callable[[LyricsMatch], None] | None = None,
    ) -> LyricsMatch:
        resolvers = self._ordered_online_resolvers()
        line_fallback: LyricsMatch | None = None
        unit_match: LyricsMatch | None = None
        wants_secondary = self._dual_line_lyrics_enabled() and self._dual_line_secondary_source() != "none"
        published_fallback = False

        def publish(candidate: LyricsMatch) -> None:
            nonlocal published_fallback
            if on_line_fallback is not None and not published_fallback:
                on_line_fallback(candidate)
                published_fallback = True

        for source_name, resolver in resolvers:
            try:
                match = resolver(title, artist, album_title, duration_seconds)
            except Exception:
                # A failed provider must not discard an earlier usable match.
                continue
            if match is not None:
                if any(
                    line.timing_kind == "unit" and genuinely_timed_unit_row(line.units)
                    for line in match.timed_lyrics
                ):
                    if unit_match is None:
                        unit_match = self._retain_matching_subrows(match, line_fallback) if line_fallback is not None else match
                    else:
                        unit_match = self._retain_matching_subrows(unit_match, match)
                    if not wants_secondary or self.has_secondary_companion(unit_match.timed_lyrics):
                        self._record_source_hit(str(unit_match.source or source_name))
                        return unit_match
                    # Publish the usable original while this same background
                    # lookup checks the remaining finite source list for its
                    # companion. Source-hit ordering cannot hide translation.
                    publish(unit_match)
                elif unit_match is not None:
                    unit_match = self._retain_matching_subrows(unit_match, match)
                    if self.has_secondary_companion(unit_match.timed_lyrics):
                        self._record_source_hit(unit_match.source)
                        return unit_match
                elif line_fallback is None and (match.timed_lyrics or match.lyrics):
                    line_fallback = match
                    publish(match)
                elif line_fallback is not None:
                    # Actual LRC row boundaries outrank the provisional 3 s
                    # plain-text timeline, even if the synthetic result was
                    # already published to make the original readable early.
                    current_has_rows = any(row.timing_kind != "synthetic" for row in match.timed_lyrics)
                    fallback_has_rows = any(row.timing_kind != "synthetic" for row in line_fallback.timed_lyrics)
                    if current_has_rows and not fallback_has_rows:
                        line_fallback = self._retain_matching_subrows(match, line_fallback)
                    elif wants_secondary and not self.has_secondary_companion(line_fallback.timed_lyrics):
                        line_fallback = self._retain_matching_subrows(line_fallback, match)
        selected = unit_match or line_fallback
        if selected is not None:
            self._record_source_hit(selected.source)
            return selected
        return LyricsMatch((), (), "")

    def _ordered_online_resolvers(self):
        resolvers = {
            "netease": self._resolve_netease,
            "qq": self._resolve_qq,
            "lrclib": self._resolve_lrclib,
        }
        base_index = {name: index for index, name in enumerate(ONLINE_SOURCE_ORDER)}
        names = sorted(
            ONLINE_SOURCE_ORDER,
            key=lambda name: (-int(self._source_hit_counts.get(name, 0)), base_index[name]),
        )
        return [(name, resolvers[name]) for name in names]

    def _load_source_hit_counts(self) -> dict[str, int]:
        counts = {name: 0 for name in ONLINE_SOURCE_ORDER}
        for path in (self._legacy_source_hits_path, self._source_hits_path):
            for name, value in self._read_source_hit_counts(path).items():
                counts[name] = max(counts.get(name, 0), value)
        return counts

    def _read_source_hit_counts(self, path: Path) -> dict[str, int]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, AttributeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        counts: dict[str, int] = {}
        for name in ONLINE_SOURCE_ORDER:
            try:
                counts[name] = max(0, int(raw.get(name, 0)))
            except (TypeError, ValueError):
                counts[name] = 0
        return counts

    def _save_source_hit_counts(self):
        try:
            self._source_hits_path.write_text(json.dumps(self._source_hit_counts, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return

    def _record_source_hit(self, source: str):
        name = str(source or "").split(":", 1)[0].strip().lower()
        if name not in ONLINE_SOURCE_ORDER:
            return
        self._source_hit_counts[name] = int(self._source_hit_counts.get(name, 0)) + 1
        self._save_source_hit_counts()

    def _parse_lrc_text(self, text: str) -> tuple[tuple[TimedLyricLine, ...], tuple[str, ...]]:
        text = self._unwrap_qrc_content(text) or text
        timed: list[TimedLyricLine] = []
        plain: list[str] = []
        raw_lines = text.splitlines()
        offset_seconds = 0.0
        for raw_line in raw_lines:
            offset_match = LRC_OFFSET_RE.match(raw_line.strip())
            if offset_match:
                try:
                    offset_seconds = int(offset_match.group(1)) / 1000.0
                except ValueError:
                    offset_seconds = 0.0
                break
        for raw_line in raw_lines:
            line = raw_line.strip()
            if not line or METADATA_RE.match(line):
                continue
            yrc_match = YRC_LINE_RE.match(raw_line.strip())
            if yrc_match:
                timestamp = max(0.0, int(yrc_match.group(1)) / 1000.0 + offset_seconds)
                raw_content = yrc_match.group(3)
                content = self._clean_timed_lyric_content(raw_content)
                if content:
                    line_start_ms = int(yrc_match.group(1))
                    line_duration_ms = int(yrc_match.group(2))
                    if KRC_UNIT_RE.search(raw_content):
                        units = self._parse_krc_units(raw_content, line_start_ms, line_duration_ms, offset_seconds)
                    elif YRC_UNIT_RE.search(raw_content):
                        units = self._parse_yrc_units(raw_content, line_start_ms, line_duration_ms, offset_seconds)
                    else:
                        units = self._parse_qrc_units(raw_content, line_start_ms, line_duration_ms, offset_seconds)
                    if units and "".join(unit.text for unit in units) == content:
                        timed.append(TimedLyricLine(timestamp, content, units, "unit"))
                    else:
                        timed.append(TimedLyricLine(timestamp, content))
                continue
            # Only consecutive leading [] tags mark whole-line timestamps.
            # A [] tag after text is a verbatim LRC word boundary.
            leading_times = []
            content_start = 0
            while match := LRC_TIME_RE.match(raw_line, content_start):
                leading_times.append(match)
                content_start = match.end()
            raw_content = raw_line[content_start:]
            content = self._clean_timed_lyric_content(raw_content)
            if leading_times and content:
                for match in leading_times:
                    timestamp = self._lrc_time_seconds(match, offset_seconds)
                    units = ()
                    if len(leading_times) == 1:
                        if ENHANCED_LRC_UNIT_RE.search(raw_content):
                            units = self._parse_enhanced_lrc_units(raw_content, offset_seconds)
                        elif LRC_TIME_RE.search(raw_content):
                            units = self._parse_verbatim_lrc_units(raw_content, timestamp, offset_seconds)
                    if units and "".join(unit.text for unit in units) == content:
                        timed.append(TimedLyricLine(timestamp, content, units, "unit"))
                    else:
                        timed.append(TimedLyricLine(timestamp, content))
                continue
            if line:
                plain.append(line)
        timed.sort(key=lambda item: item.time_seconds)
        return self._dedupe_timed_lines(timed), self._plain_lines(plain)

    @staticmethod
    def _unwrap_qrc_content(text: str) -> str | None:
        if "LyricContent" not in text:
            return None
        # Extracting the attribute first preserves raw lyric line breaks,
        # which an XML parser normalizes to spaces inside attributes.
        match = QRC_CONTENT_RE.search(text)
        if match:
            return html.unescape(match.group(2))
        if len(text) <= 1_000_000:
            try:
                root = ElementTree.fromstring(text)
                for node in root.iter():
                    if node.tag.rsplit("}", 1)[-1].casefold() == "lyric_1":
                        content = node.attrib.get("LyricContent")
                        if content:
                            return content
            except ElementTree.ParseError:
                pass
        return None

    @staticmethod
    def _lrc_time_seconds(match: re.Match[str], offset_seconds: float) -> float:
        minutes = int(match.group("minutes") if "minutes" in match.re.groupindex else match.group(1))
        seconds = int(match.group("seconds") if "seconds" in match.re.groupindex else match.group(2))
        fraction_group = "fraction" if "fraction" in match.re.groupindex else 3
        fraction = match.group(fraction_group) or "0"
        millis = int(fraction.ljust(3, "0")[:3])
        return max(0.0, minutes * 60 + seconds + millis / 1000.0 + offset_seconds)

    @staticmethod
    def _normalize_timed_units(units: list[TimedLyricUnit]) -> tuple[TimedLyricUnit, ...]:
        # Normalize display whitespace once across unit boundaries while
        # preserving which timestamp owns each displayed character.
        chars = [(char, index) for index, unit in enumerate(units) for char in unit.text]
        output = [[] for _ in units]
        first_nonspace = next((index for index, (char, _) in enumerate(chars) if not char.isspace()), len(chars))
        last_nonspace = next((index for index in range(len(chars) - 1, -1, -1) if not chars[index][0].isspace()), -1)
        index = first_nonspace
        while index <= last_nonspace:
            char, owner = chars[index]
            if char.isspace():
                output[owner].append(" ")
                while index <= last_nonspace and chars[index][0].isspace():
                    index += 1
            else:
                output[owner].append(char)
                index += 1
        return tuple(
            TimedLyricUnit(unit.start_seconds, unit.end_seconds, "".join(output[index]))
            for index, unit in enumerate(units)
            if output[index]
        )

    def _parse_yrc_units(self, content: str, line_start_ms: int, line_duration_ms: int, offset_seconds: float) -> tuple[TimedLyricUnit, ...]:
        markers = list(YRC_UNIT_RE.finditer(content))
        if not markers or content[: markers[0].start()].strip():
            return ()
        raw_units: list[TimedLyricUnit] = []
        line_end_ms = line_start_ms + line_duration_ms
        for index, marker in enumerate(markers):
            start_ms = int(marker.group("start"))
            end_ms = start_ms + int(marker.group("duration"))
            if start_ms < line_start_ms - 50 or end_ms > line_end_ms + 50 or end_ms <= start_ms:
                return ()
            next_start = markers[index + 1].start() if index + 1 < len(markers) else len(content)
            part = content[marker.end() : next_start]
            if part:
                raw_units.append(TimedLyricUnit(max(0.0, start_ms / 1000.0 + offset_seconds), max(0.0, end_ms / 1000.0 + offset_seconds), part))
        return self._normalize_timed_units(raw_units)

    def _parse_qrc_units(self, content: str, line_start_ms: int, line_duration_ms: int, offset_seconds: float) -> tuple[TimedLyricUnit, ...]:
        markers = list(QRC_UNIT_RE.finditer(content))
        if not markers:
            return ()
        raw_units: list[TimedLyricUnit] = []
        cursor = 0
        line_end_ms = line_start_ms + line_duration_ms
        for marker in markers:
            part = content[cursor : marker.start()]
            start_ms = int(marker.group("start"))
            end_ms = start_ms + int(marker.group("duration"))
            if not part or start_ms < line_start_ms - 50 or end_ms > line_end_ms + 50 or end_ms <= start_ms:
                return ()
            raw_units.append(TimedLyricUnit(max(0.0, start_ms / 1000.0 + offset_seconds), max(0.0, end_ms / 1000.0 + offset_seconds), part))
            cursor = marker.end()
        if content[cursor:].strip():
            return ()
        return self._normalize_timed_units(raw_units)

    def _parse_krc_units(self, content: str, line_start_ms: int, line_duration_ms: int, offset_seconds: float) -> tuple[TimedLyricUnit, ...]:
        markers = list(KRC_UNIT_RE.finditer(content))
        if not markers or content[: markers[0].start()].strip():
            return ()
        raw_units: list[TimedLyricUnit] = []
        for index, marker in enumerate(markers):
            start_ms = line_start_ms + int(marker.group("start"))
            end_ms = start_ms + int(marker.group("duration"))
            if end_ms > line_start_ms + line_duration_ms + 50 or end_ms <= start_ms:
                return ()
            next_start = markers[index + 1].start() if index + 1 < len(markers) else len(content)
            part = content[marker.end() : next_start]
            if part:
                raw_units.append(TimedLyricUnit(max(0.0, start_ms / 1000.0 + offset_seconds), max(0.0, end_ms / 1000.0 + offset_seconds), part))
        return self._normalize_timed_units(raw_units)

    def _parse_enhanced_lrc_units(self, content: str, offset_seconds: float) -> tuple[TimedLyricUnit, ...]:
        markers = list(ENHANCED_LRC_UNIT_RE.finditer(content))
        if not markers or content[: markers[0].start()].strip():
            return ()
        raw_units: list[TimedLyricUnit] = []
        for index, marker in enumerate(markers):
            next_marker = markers[index + 1] if index + 1 < len(markers) else None
            part = content[marker.end() : next_marker.start() if next_marker else len(content)]
            if not part:
                continue
            start = self._lrc_time_seconds(marker, offset_seconds)
            end = self._lrc_time_seconds(next_marker, offset_seconds) if next_marker else None
            if end is not None and end <= start:
                return ()
            if raw_units and start < raw_units[-1].start_seconds:
                return ()
            raw_units.append(TimedLyricUnit(start, end, part))
        return self._normalize_timed_units(raw_units)

    def _parse_verbatim_lrc_units(self, content: str, line_start: float, offset_seconds: float) -> tuple[TimedLyricUnit, ...]:
        markers = list(LRC_TIME_RE.finditer(content))
        if not markers:
            return ()
        raw_units: list[TimedLyricUnit] = []
        cursor = 0
        start = line_start
        for marker in markers:
            end = self._lrc_time_seconds(marker, offset_seconds)
            part = content[cursor : marker.start()]
            if part:
                if end <= start:
                    return ()
                raw_units.append(TimedLyricUnit(start, end, part))
            start = end
            cursor = marker.end()
        if content[cursor:]:
            raw_units.append(TimedLyricUnit(start, None, content[cursor:]))
        return self._normalize_timed_units(raw_units)

    def _clean_timed_lyric_content(self, value: str) -> str:
        value = ENHANCED_LRC_TIME_RE.sub("", value or "")
        value = YRC_WORD_RE.sub("", value)
        value = KRC_UNIT_RE.sub("", value)
        value = LRC_TIME_RE.sub("", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    def _dedupe_timed_lines(self, lines: list[TimedLyricLine]) -> tuple[TimedLyricLine, ...]:
        result: list[TimedLyricLine] = []
        seen: dict[tuple[int, str], int] = {}
        for line in lines:
            text = line.text.strip()
            if not text:
                continue
            key = (int(round(line.time_seconds * 1000)), text)
            if key in seen:
                # A second source may contain the same line with real word
                # timestamps. Keep the richer copy without duplicating text.
                old_index = seen[key]
                if len(line.units) > len(result[old_index].units):
                    result[old_index] = TimedLyricLine(max(0.0, line.time_seconds), text, line.units, line.timing_kind)
                continue
            seen[key] = len(result)
            result.append(TimedLyricLine(max(0.0, line.time_seconds), text, line.units, line.timing_kind))
        return tuple(result)

    def _plain_lines(self, lines: list[str]) -> tuple[str, ...]:
        result: list[str] = []
        for line in lines:
            cleaned = line.strip()
            if cleaned and not METADATA_RE.match(cleaned):
                result.append(cleaned)
        return tuple(result)

    def _synthesize_timed_lyrics(
        self, lines: tuple[str, ...], step_seconds: float = 3.0, *, language_hint: str = "",
    ) -> tuple[TimedLyricLine, ...]:
        lines = group_cantonese_plain_lines(lines, language_hint)
        return tuple(TimedLyricLine(index * step_seconds, line, timing_kind="synthetic") for index, line in enumerate(lines))

    def _lyrics_match_from_text(self, text: str | tuple[str, ...], source: str) -> LyricsMatch | None:
        if isinstance(text, tuple):
            texts = tuple(item for item in text if str(item or "").strip())
            if not texts:
                return None
            parsed = [self._parse_lrc_text(item) for item in texts]
            timed_groups = [timed for timed, _plain in parsed if timed]
            if timed_groups:
                timed = self._merge_timed_lyric_sources(timed_groups)
                if not self._lyrics_have_vocal_content(tuple(line.text for line in timed)):
                    return None
                return self._finalize_lyrics_match(LyricsMatch(timed, tuple(line.text for line in timed), source), "\n".join(texts))
            plain_lines: list[str] = []
            for _timed, plain in parsed:
                plain_lines.extend(plain)
            if plain_lines:
                plain = tuple(plain_lines)
                if not self._lyrics_have_vocal_content(plain):
                    return None
                synth = self._synthesize_timed_lyrics(plain, language_hint="\n".join(texts))
                return self._finalize_lyrics_match(LyricsMatch(synth, plain, source), "\n".join(texts))
            return None
        if not text:
            return None
        timed, plain = self._parse_lrc_text(text)
        if timed:
            if not self._lyrics_have_vocal_content(tuple(line.text for line in timed)):
                return None
            return self._finalize_lyrics_match(LyricsMatch(timed, tuple(line.text for line in timed), source), text)
        if plain:
            if not self._lyrics_have_vocal_content(plain):
                return None
            synth = self._synthesize_timed_lyrics(plain, language_hint=text)
            return self._finalize_lyrics_match(LyricsMatch(synth, plain, source), text)
        return None

    def _lyrics_have_vocal_content(self, lines: tuple[str, ...]) -> bool:
        for raw in lines:
            text = unicodedata.normalize("NFKC", str(raw or "")).strip()
            if not text:
                continue
            if self._looks_like_clock_or_number_only(text):
                continue
            if any(char.isalpha() for char in text):
                return True
        return False

    def _looks_like_clock_or_number_only(self, text: str) -> bool:
        cleaned = re.sub(r"\s+", "", str(text or ""))
        if not cleaned:
            return True
        if re.fullmatch(r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.:]\d{1,3})?", cleaned):
            return True
        return bool(re.fullmatch(r"[\d.,:：/\\\-+_~·•|()[\]{}<>]+", cleaned))

    def _finalize_lyrics_match(self, match: LyricsMatch, language_hint: str = "") -> LyricsMatch:
        originals = match.secondary_source_rows or match.timed_lyrics
        # This field carries previously verified Cantonese source provenance,
        # including ordinary written Chinese with only an explicit language tag.
        hint = "yue" if match.secondary_source_rows else language_hint
        timed = cantonese_secondary_rows(
            originals, priority=self._cantonese_priority(), language_hint=hint,
            generate_missing=self._dual_line_lyrics_enabled(),
        )
        alternatives = originals if has_cantonese_evidence(
            "\n".join(row.text for row in originals), hint,
        ) else ()
        match = LyricsMatch(timed, tuple(line.text for line in timed), match.source, alternatives)
        mode = self._lyric_chinese_conversion_mode()
        if mode == "off":
            return match
        timed = tuple(
            self._convert_timed_line(line, mode)
            for line in match.timed_lyrics
        )
        plain = tuple(self._convert_chinese_text(line, mode) for line in match.lyrics)
        return LyricsMatch(timed, plain, match.source, match.secondary_source_rows)

    def _convert_timed_line(self, line: TimedLyricLine, mode: str) -> TimedLyricLine:
        text = self._convert_chinese_text(str(line.text or ""), mode)
        units = tuple(
            TimedLyricUnit(unit.start_seconds, unit.end_seconds, self._convert_chinese_text(unit.text, mode))
            for unit in line.units
        )
        # Conversion can change a whole phrase differently from its fragments.
        # In that case keep the visible text and fall back to line timing.
        primary = text.split("\n", 1)[0]
        if units and "".join(unit.text for unit in units) != primary:
            units = ()
        return TimedLyricLine(line.time_seconds, text, units, line.timing_kind if units else ("synthetic" if line.timing_kind == "synthetic" else "line"))

    def _convert_chinese_text(self, text: str, mode: str) -> str:
        value = str(text or "")
        if not value or mode not in {"simplified", "traditional"}:
            return value
        converted = self._windows_convert_chinese_text(value, mode)
        table = TRADITIONAL_TO_SIMPLIFIED_FALLBACK if mode == "simplified" else SIMPLIFIED_TO_TRADITIONAL_FALLBACK
        return (converted or value).translate(table)

    def _windows_convert_chinese_text(self, text: str, mode: str) -> str:
        if sys.platform != "win32" or not text:
            return ""
        try:
            LCMAP_SIMPLIFIED_CHINESE = 0x02000000
            LCMAP_TRADITIONAL_CHINESE = 0x04000000
            flags = LCMAP_SIMPLIFIED_CHINESE if mode == "simplified" else LCMAP_TRADITIONAL_CHINESE
            locale = "zh-CN" if mode == "simplified" else "zh-TW"
            kernel32 = ctypes.windll.kernel32
            src_len = len(text)
            dest_len = src_len * 2 + 8
            dest = ctypes.create_unicode_buffer(dest_len)
            result = kernel32.LCMapStringEx(
                wintypes.LPCWSTR(locale),
                wintypes.DWORD(flags),
                wintypes.LPCWSTR(text),
                ctypes.c_int(src_len),
                dest,
                ctypes.c_int(dest_len),
                None,
                None,
                wintypes.LPARAM(0),
            )
            if int(result) <= 0:
                return ""
            return dest.value
        except Exception:
            return ""

    def _ordered_lyric_row_pairs(
        self,
        anchors: tuple[TimedLyricLine, ...] | list[TimedLyricLine],
        track: tuple[TimedLyricLine, ...] | list[TimedLyricLine],
        *,
        same_original_text: bool = False,
    ) -> tuple[tuple[int, int], ...]:
        """Pair ordered source rows without consuming a neighbor's companion.

        Ordinary bilingual pairing uses the existing 180 ms tolerance. An
        original-to-original bridge instead needs identical normalized sung
        text; its measured clocks may differ, so time only breaks ties between
        repeated occurrences. It never pairs a translation by its row number.
        """
        count, translated_count = len(anchors), len(track)
        anchor_keys = tuple(self._normalize_text(line.text.split("\n", 1)[0]) for line in anchors)
        track_keys = tuple(self._normalize_text(line.text.split("\n", 1)[0]) for line in track)
        scores = [[(0, 0.0)] * (translated_count + 1) for _ in range(count + 1)]
        choices = [[""] * translated_count for _ in range(count)]
        for anchor_index in range(count - 1, -1, -1):
            for translation_index in range(translated_count - 1, -1, -1):
                best = scores[anchor_index + 1][translation_index]
                choice = "anchor"
                if scores[anchor_index][translation_index + 1] > best:
                    best = scores[anchor_index][translation_index + 1]
                    choice = "translation"
                distance = abs(anchors[anchor_index].time_seconds - track[translation_index].time_seconds)
                eligible = (
                    bool(anchor_keys[anchor_index]) and anchor_keys[anchor_index] == track_keys[translation_index]
                    if same_original_text else distance <= 0.18
                )
                if eligible:
                    later = scores[anchor_index + 1][translation_index + 1]
                    paired = (later[0] + 1, later[1] - distance)
                    if paired >= best:
                        best = paired
                        choice = "pair"
                scores[anchor_index][translation_index] = best
                choices[anchor_index][translation_index] = choice
        pairs: list[tuple[int, int]] = []
        anchor_index = translation_index = 0
        while anchor_index < count and translation_index < translated_count:
            choice = choices[anchor_index][translation_index]
            if choice == "pair":
                pairs.append((anchor_index, translation_index))
                anchor_index += 1
                translation_index += 1
            elif choice == "anchor":
                anchor_index += 1
            else:
                translation_index += 1
        return tuple(pairs)

    def _retain_matching_subrows(self, match: LyricsMatch, previous: LyricsMatch) -> LyricsMatch:
        """Keep known companions when the same original gains unit timing."""
        retain_alternatives = bool(match.secondary_source_rows or previous.secondary_source_rows)
        # Generated LRC reloads represent a composite as separate source rows
        # at exactly the same timestamp. Reconstruct only the local association
        # view; the original model/source rows are not mutated.
        previous_rows = group_secondary_source_rows(previous.secondary_source_rows or previous.timed_lyrics)
        if not retain_alternatives and not self._has_multiline_timed_lyrics(previous_rows):
            return match
        timed = list(match.secondary_source_rows or match.timed_lyrics)
        changed = False
        pairs = self._ordered_lyric_row_pairs(
            timed, previous_rows, same_original_text=True,
        )
        for current_index, old_index in pairs:
            current = timed[current_index]
            old = previous_rows[old_index]
            if (self.has_secondary_companion((current,)) and not retain_alternatives) or "\n" not in old.text:
                continue
            companions = "\n".join(part for part in old.text.split("\n")[1:]
                                   if part not in current.text.split("\n"))
            if not companions.strip():
                continue
            timed[current_index] = TimedLyricLine(
                current.time_seconds, current.text + "\n" + companions,
                current.units, current.timing_kind,
            )
            changed = True
        if not changed and not (retain_alternatives and pairs):
            return match
        result = tuple(timed)
        retained = LyricsMatch(result, tuple(line.text for line in result), match.source,
                               result if retain_alternatives else ())
        return self._finalize_lyrics_match(retained) if retain_alternatives else retained

    def _merge_timed_lyric_sources(self, sources: list[tuple[TimedLyricLine, ...]]) -> tuple[TimedLyricLine, ...]:
        tracks: list[list[TimedLyricLine]] = []
        for source in sources:
            track = []
            for line in source:
                # Newlines carry original/translation/bracket/singer subrow
                # structure. Normalize horizontal whitespace per subrow only.
                text = "\n".join(
                    " ".join(part.split()) for part in str(line.text or "").splitlines() if part.strip()
                )
                if text:
                    track.append(TimedLyricLine(
                        max(0.0, float(line.time_seconds)), text,
                        line.units if text == line.text else (),
                        line.timing_kind if text == line.text else "line",
                    ))
            if track:
                tracks.append(track)
        if not tracks:
            return ()

        # The first source is the requested original/YRC track; later sources
        # are its translation or transliteration. Match one row at a time in
        # source order. A global 180 ms time bucket can swallow two quick
        # original lines and attach the first translation to the wrong one.
        anchors = tracks[0]
        secondary = tracks[1:]
        matches: list[list[TimedLyricLine]] = [[] for _line in anchors]
        unmatched_timed_originals: list[TimedLyricLine] = []
        for track in secondary:
            # Optimize the whole ordered pairing, not each row greedily. Two
            # original lines can be 120 ms apart while a translation track is
            # shifted by 70 ms; a nearest-first choice would steal the first
            # translation for line two and orphan the second translation.
            paired_indices: set[int] = set()
            for anchor_index, translation_index in self._ordered_lyric_row_pairs(anchors, track):
                matches[anchor_index].append(track[translation_index])
                paired_indices.add(translation_index)
            for index, item in enumerate(track):
                if index not in paired_indices and item.units and item.timing_kind == "unit":
                    # A richer original track can contain a real line absent
                    # from source one; unpaired translations remain omitted.
                    unmatched_timed_originals.append(item)

        merged: list[TimedLyricLine] = []
        for anchor, companions in zip(anchors, matches):
            unique = [anchor]
            for companion in companions:
                duplicate = next((index for index, part in enumerate(unique) if part.text == companion.text), None)
                if duplicate is None:
                    unique.append(companion)
                elif len(companion.units) > len(unique[duplicate].units):
                    unique[duplicate] = companion
            primary = next((part for part in unique if part.units and part.timing_kind == "unit"), unique[0])
            ordered = [primary, *(part for part in unique if part is not primary)]
            merged.append(TimedLyricLine(
                anchor.time_seconds,
                "\n".join(part.text for part in ordered),
                primary.units, primary.timing_kind,
            ))
        merged.extend(unmatched_timed_originals)
        merged.sort(key=lambda line: line.time_seconds)
        return self._dedupe_timed_lines(merged)

    def _read_lrc(self, path: Path) -> tuple[TimedLyricLine, ...]:
        return self._read_lrc_match(path).timed_lyrics

    def _read_lrc_match(self, path: Path) -> LyricsMatch:
        """Read selected display rows and their source alternatives in one pass."""
        if not path.exists() or not path.is_file():
            return LyricsMatch((), ())
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = path.read_text(encoding="gb18030")
            except OSError:
                return LyricsMatch((), ())
        except OSError:
            return LyricsMatch((), ())

        timed, plain = self._parse_lrc_text(text)
        lines = timed or self._synthesize_timed_lyrics(plain, language_hint=text)
        return self._finalize_lyrics_match(
            LyricsMatch(lines, tuple(line.text for line in lines), f"local:{path.name}"), text,
        )

    def _persist_match(
        self, title: str, artist: str, album_title: str, match: LyricsMatch, overwrite: bool = False,
        generation: int | None = None,
    ):
        with self._cache_lock:
            if generation is not None and generation != self._cache_generation:
                return
            self._persist_match_locked(title, artist, album_title, match, overwrite)

    def _persist_match_locked(
        self, title: str, artist: str, album_title: str, match: LyricsMatch, overwrite: bool,
    ):
        if not match.lyrics:
            return
        candidates = self._candidate_names(title, artist)
        if not candidates:
            return
        path = self.generated_root / f"{candidates[0]}.lrc"
        if path.exists() and not overwrite:
            return
        if path.exists() and overwrite and not self._is_generated_online_cache(path):
            # A manually authored file can share this name when callers use
            # the same root for generated and local lyrics.
            return
        temporary = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(self._render_lrc(title, artist, album_title, match), encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            return
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        self._clear_local_miss(self._local_lookup_key(title, artist))
        self._prune_persisted_cache()

    def _prune_persisted_cache(self, now: float | None = None):
        try:
            generated_root = Path(self.generated_root)
            local_root = Path(self.root)
        except TypeError:
            return
        try:
            if generated_root.resolve() == local_root.resolve():
                return
        except OSError:
            return
        try:
            limit = max(0, int(getattr(self, "PERSISTED_CACHE_FILE_LIMIT", 96)))
        except (TypeError, ValueError):
            limit = 96
        try:
            max_age = max(0.0, float(getattr(self, "PERSISTED_CACHE_MAX_AGE_SECONDS", 30 * 24 * 60 * 60)))
        except (TypeError, ValueError):
            max_age = 30 * 24 * 60 * 60
        timestamp = time.time() if now is None else float(now)
        try:
            files = [path for path in generated_root.glob("*.lrc") if path.is_file()]
        except OSError:
            return
        records: list[tuple[float, Path]] = []
        for path in files:
            try:
                mtime = float(path.stat().st_mtime)
            except OSError:
                continue
            records.append((mtime, path))
        expired = {path for mtime, path in records if max_age > 0.0 and timestamp - mtime > max_age}
        active = sorted((record for record in records if record[1] not in expired), key=lambda item: item[0])
        overflow = max(0, len(active) - limit) if limit > 0 else len(active)
        removable = set(expired)
        removable.update(path for _mtime, path in active[:overflow])
        for path in removable:
            try:
                path.unlink()
            except OSError:
                continue

    def _render_lrc(self, title: str, artist: str, album_title: str, match: LyricsMatch) -> str:
        lines: list[str] = [self.GENERATED_CACHE_MARKER]
        if artist:
            lines.append(f"[ar:{artist}]")
        if title:
            lines.append(f"[ti:{title}]")
        if album_title:
            lines.append(f"[al:{album_title}]")
        if match.source:
            lines.append(f"[by:{match.source}]")
        if match.secondary_source_rows:
            lines.append("[language:yue]")
        timed = match.secondary_source_rows or match.timed_lyrics or self._synthesize_timed_lyrics(match.lyrics)
        if timed and all(line.timing_kind == "synthetic" and not line.units for line in timed):
            # Keep plain input plain on disk. Writing the provisional 3 s
            # playback positions as LRC tags would turn a later reload into
            # apparently authoritative line timing.
            for lyric in timed:
                lines.extend(part.strip() for part in str(lyric.text or "").splitlines() if part.strip())
            return "\n".join(lines) + "\n"
        for lyric in timed:
            stamp = f"[{self._format_lrc_time(lyric.time_seconds)}]"
            for index, part in enumerate(str(lyric.text or "").splitlines()):
                cleaned = part.strip()
                if cleaned:
                    if index == 0 and lyric.units and "".join(unit.text for unit in lyric.units) == cleaned:
                        word_text = ""
                        last_marker_time: float | None = None
                        for unit in lyric.units:
                            if last_marker_time is None or abs(unit.start_seconds - last_marker_time) > 0.0005:
                                word_text += f"<{self._format_lrc_time(unit.start_seconds)}>"
                            word_text += unit.text
                            last_marker_time = unit.start_seconds
                            if unit.end_seconds is not None:
                                word_text += f"<{self._format_lrc_time(unit.end_seconds)}>"
                                last_marker_time = unit.end_seconds
                        lines.append(f"{stamp}{word_text}")
                    else:
                        lines.append(f"{stamp}{cleaned}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_lrc_time(seconds: float) -> str:
        total_ms = max(0, int(round(seconds * 1000.0)))
        return f"{total_ms // 60000:02d}:{(total_ms // 1000) % 60:02d}.{total_ms % 1000:03d}"

    def _has_multiline_timed_lyrics(self, timed_lyrics: tuple[TimedLyricLine, ...]) -> bool:
        return any("\n" in str(line.text or "") for line in timed_lyrics)

    @staticmethod
    def has_secondary_companion(timed_lyrics: tuple[TimedLyricLine, ...]) -> bool:
        """Recognize a displayed translation/phonetic companion conservatively.

        Provider lookup and the finite online-source loop share this predicate.
        A newline, backing vocal or singer cue alone does not certify a
        translation. Unlabeled same-language alternatives remain ambiguous.
        """
        for row in group_secondary_source_rows(timed_lyrics or ()):
            parts = [part.strip() for part in str(row.text or "").splitlines() if part.strip()]
            if len(parts) < 2:
                continue
            primary = parts[0]
            primary_han = bool(SECONDARY_HAN_RE.search(primary))
            primary_latin = bool(SECONDARY_LATIN_RE.search(primary))
            primary_east_asian = bool(SECONDARY_KANA_RE.search(primary) or SECONDARY_HANGUL_RE.search(primary))
            for companion in parts[1:]:
                if SECONDARY_LABEL_RE.match(companion):
                    if SECONDARY_LABEL_RE.sub("", companion).strip():
                        return True
                    continue
                if SECONDARY_PARENTHESES_RE.fullmatch(companion) or SECONDARY_SPEAKER_RE.match(companion):
                    continue
                companion_han_count = len(SECONDARY_HAN_RE.findall(companion))
                companion_latin_count = len(SECONDARY_LATIN_RE.findall(companion))
                companion_east_asian = bool(SECONDARY_KANA_RE.search(companion) or SECONDARY_HANGUL_RE.search(companion))
                if primary_latin and not primary_han and companion_han_count:
                    if not companion_latin_count or companion_han_count >= max(2, 2 * companion_latin_count):
                        return True
                if primary_east_asian and companion_han_count and not companion_east_asian:
                    return True
                if primary_han and not primary_latin and not companion_han_count and companion_latin_count >= 2:
                    return True
        return False

    def _resolve_netease(self, title: str, artist: str, album_title: str, duration_seconds: float) -> LyricsMatch | None:
        query = self._search_query(title, artist, album_title)
        if not query:
            return None
        payload = self._fetch_json(
            "https://music.163.com/api/search/get/web",
            params={"type": "1", "s": query, "offset": "0", "limit": "8"},
            headers=self._netease_headers(),
        )
        songs = self._extract_list(payload, ("result", "songs"), ("songs",))
        candidate = self._pick_best_candidate(
            songs, title, artist, album_title, self._netease_candidate_text, duration_seconds,
        )
        if candidate is None:
            return None
        song_id = candidate.get("id") or candidate.get("song_id") or candidate.get("songid")
        if song_id is None:
            return None
        lyric_payload = self._fetch_json(
            "https://music.163.com/api/song/lyric",
            params={"id": str(song_id), "lv": "1", "kv": "1", "tv": "-1", "rv": "1", "yv": "-1"},
            headers=self._netease_headers(),
        )
        lyric_text = self._extract_netease_lyric_text(
            lyric_payload,
            prefer_original=self._prefers_original_lyrics(title, artist),
            secondary_source=self._dual_line_secondary_source(),
            prefer_secondary=self._dual_line_lyrics_enabled(),
        )
        match = self._lyrics_match_from_text(lyric_text, "netease")
        if match is not None and self._dual_line_lyrics_enabled():
            hint = title + " " + str(candidate.get("language") or "")
            if isinstance(lyric_payload, dict):
                hint += " " + str(lyric_payload.get("language") or "")
            match = self._finalize_lyrics_match(match, language_hint=hint)
        return match

    def _resolve_qq(self, title: str, artist: str, album_title: str, duration_seconds: float) -> LyricsMatch | None:
        query = self._search_query(title, artist, album_title)
        if not query:
            return None
        payload = self._fetch_json(
            "https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
            params={"p": "1", "n": "8", "w": query, "format": "json"},
            headers=self._qq_search_headers(),
        )
        songs = self._extract_list(payload, ("data", "song", "list"), ("data", "song", "list"), ("song", "list"))
        candidate = self._pick_best_candidate(
            songs, title, artist, album_title, self._qq_candidate_text, duration_seconds,
        )
        if candidate is None:
            return None
        song_mid = candidate.get("songmid") or candidate.get("mid") or candidate.get("song_mid")
        if not song_mid:
            return None
        lyric_payload = self._fetch_json(
            "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
            params={
                "songmid": str(song_mid),
                "format": "json",
                "nobase64": "1",
                "platform": "yqq.json",
                "needNewCode": "0",
            },
            headers=self._qq_lyric_headers(),
        )
        lyric_text = self._extract_qq_lyric_text(lyric_payload)
        return self._lyrics_match_from_text(lyric_text, "qq")

    def _resolve_lrclib(self, title: str, artist: str, album_title: str, duration_seconds: float) -> LyricsMatch | None:
        params: dict[str, str] = {"track_name": title, "artist_name": artist}
        if album_title:
            params["album_name"] = album_title
        if duration_seconds > 0:
            params["duration"] = str(int(round(duration_seconds)))
        payload = self._fetch_json(
            "https://lrclib.net/api/get",
            params=params,
            headers=self._lrclib_headers(),
        )
        candidate_text = self._lrclib_candidate_text(payload) if isinstance(payload, dict) else ("", "", "")
        identified = self._candidate_identity_matches(
            title, artist, candidate_text[0], candidate_text[1],
            duration_seconds, self._candidate_duration_seconds(payload, "lrclib") if isinstance(payload, dict) else 0.0,
            allow_missing_metadata=True,
        )
        match = self._lrclib_match_from_payload(payload, "lrclib") if identified else None
        if match is not None:
            return match

        search_payload = self._fetch_json(
            "https://lrclib.net/api/search",
            params={"q": self._search_query(title, artist, album_title)},
            headers=self._lrclib_headers(),
        )
        candidates = [item for item in search_payload if isinstance(item, dict)] if isinstance(search_payload, list) else []
        candidate = self._pick_best_candidate(
            candidates, title, artist, album_title, self._lrclib_candidate_text, duration_seconds,
        )
        if candidate is None:
            return None
        return self._lrclib_match_from_payload(candidate, "lrclib")

    def _lrclib_match_from_payload(self, payload: Any, source: str) -> LyricsMatch | None:
        if not isinstance(payload, dict):
            return None
        lyric_text = str(payload.get("syncedLyrics") or payload.get("plainLyrics") or payload.get("lyrics") or "")
        return self._lyrics_match_from_text(lyric_text, source)

    def _search_query(self, title: str, artist: str, album_title: str) -> str:
        variants = [
            " ".join(part for part in (title, artist, album_title) if part),
            " ".join(part for part in (artist, title, album_title) if part),
            " ".join(part for part in (title, artist) if part),
            title,
            artist,
        ]
        seen: set[str] = set()
        for variant in variants:
            cleaned = self._normalize_query_value(variant)
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                return variant
        return ""

    def _normalize_query_value(self, value: str) -> str:
        return self._normalize_text(value)

    def _extract_list(self, payload: Any, *paths: tuple[str, ...]) -> list[dict[str, Any]]:
        for path in paths:
            current = payload
            for key in path:
                if not isinstance(current, dict):
                    current = None
                    break
                current = current.get(key)
            if isinstance(current, list):
                return [item for item in current if isinstance(item, dict)]
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _pick_best_candidate(
        self,
        candidates: list[dict[str, Any]],
        title: str,
        artist: str,
        album_title: str,
        text_resolver,
        duration_seconds: float = 0.0,
    ) -> dict[str, Any] | None:
        best_score = 0.0
        best_candidate: dict[str, Any] | None = None
        for candidate in candidates:
            candidate_title, candidate_artist, candidate_album = text_resolver(candidate)
            source = str(getattr(text_resolver, "__name__", "")).removeprefix("_").removesuffix("_candidate_text")
            if not self._candidate_identity_matches(
                title, artist, candidate_title, candidate_artist, duration_seconds,
                self._candidate_duration_seconds(candidate, source),
            ):
                continue
            score = self._candidate_score(title, artist, album_title, candidate_title, candidate_artist, candidate_album)
            if score > best_score:
                best_score = score
                best_candidate = candidate
        return best_candidate

    def _candidate_identity_matches(
        self, title: str, artist: str, candidate_title: str, candidate_artist: str,
        duration_seconds: float = 0.0, candidate_duration_seconds: float = 0.0,
        *, allow_missing_metadata: bool = False,
    ) -> bool:
        for requested, supplied, threshold in ((title, candidate_title, 0.75), (artist, candidate_artist, 0.60)):
            if not self._normalize_text(requested):
                continue
            if allow_missing_metadata and not self._normalize_text(supplied):
                continue
            if self._text_score(requested, supplied) < threshold:
                return False
        if candidate_title and self._explicit_version_tags(title) != self._explicit_version_tags(candidate_title):
            return False
        if (duration_seconds > 0.0 and candidate_duration_seconds > 0.0
                and abs(duration_seconds - candidate_duration_seconds) > max(3.0, 0.02 * duration_seconds)):
            return False
        return True

    @staticmethod
    def _candidate_duration_seconds(candidate: dict[str, Any], source: str) -> float:
        if source == "netease":
            raw = candidate.get("dt") or candidate.get("duration")
            divisor = 1000.0
        elif source == "qq":
            raw = candidate.get("interval")
            divisor = 1.0
        else:
            raw = candidate.get("duration")
            divisor = 1.0
        try:
            value = float(raw or 0.0) / divisor
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return value if math.isfinite(value) and value > 0.0 else 0.0

    @staticmethod
    def _explicit_version_tags(title: str) -> frozenset[str]:
        value = str(title or "")
        qualifiers = re.findall(r"[\[(（【]([^\])）】]*)[\])）】]", value)
        qualifiers.extend(re.findall(r"\s[-–—]\s(.+)$", value))
        # Also recognize an explicit version word at the end when the source
        # omitted parentheses, without treating 'Live Forever' as a version.
        trailing = re.search(r"\b(?:live|remix|instrumental|studio)(?:\s+version)?\s*$", value, re.I)
        if trailing:
            qualifiers.append(trailing.group())
        text = " ".join(qualifiers)
        patterns = {
            "live": r"\blive\b|现场(?:版)?",
            "remix": r"\bremix\b|混音",
            "instrumental": r"\binstrumental\b|纯音乐|伴奏",
            "studio": r"\bstudio\b|录音室",
        }
        return frozenset(kind for kind, pattern in patterns.items() if re.search(pattern, text, re.I))

    def _candidate_score(
        self,
        title: str,
        artist: str,
        album_title: str,
        candidate_title: str,
        candidate_artist: str,
        candidate_album: str,
    ) -> float:
        title_score = self._text_score(title, candidate_title)
        artist_score = self._text_score(artist, candidate_artist)
        album_score = self._text_score(album_title, candidate_album)
        score = title_score * 0.62 + artist_score * 0.30 + album_score * 0.08
        if self._normalize_text(title) and self._normalize_text(title) == self._normalize_text(candidate_title):
            score += 0.18
        if self._normalize_text(artist) and self._normalize_text(artist) in self._normalize_text(candidate_artist):
            score += 0.08
        return score

    def _text_score(self, query: str, candidate: str) -> float:
        q = self._normalize_text(query)
        c = self._normalize_text(candidate)
        if not q or not c:
            return 0.0
        if q == c:
            return 1.0
        score = SequenceMatcher(None, q, c).ratio()
        if q in c or c in q:
            score += 0.12
        return min(1.0, score)

    def _netease_candidate_text(self, candidate: dict[str, Any]) -> tuple[str, str, str]:
        title = str(candidate.get("name") or candidate.get("title") or "")
        artists = candidate.get("artists") or candidate.get("ar") or []
        artist_names = self._join_names(artists)
        album = candidate.get("album") or {}
        if isinstance(album, dict):
            album_title = str(album.get("name") or album.get("title") or "")
        else:
            album_title = str(candidate.get("album_name") or candidate.get("albumName") or "")
        return title, artist_names, album_title

    def _qq_candidate_text(self, candidate: dict[str, Any]) -> tuple[str, str, str]:
        title = str(candidate.get("songname") or candidate.get("title") or candidate.get("name") or "")
        singers = candidate.get("singer") or candidate.get("singers") or []
        artist_names = self._join_names(singers)
        album_title = str(candidate.get("albumname") or candidate.get("album") or candidate.get("album_name") or "")
        return title, artist_names, album_title

    def _lrclib_candidate_text(self, candidate: dict[str, Any]) -> tuple[str, str, str]:
        title = str(candidate.get("trackName") or candidate.get("track_name") or candidate.get("name") or "")
        artist = str(candidate.get("artistName") or candidate.get("artist_name") or candidate.get("artist") or "")
        album_title = str(candidate.get("albumName") or candidate.get("album_name") or candidate.get("album") or "")
        return title, artist, album_title

    def _join_names(self, entries: Any) -> str:
        names: list[str] = []
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    name = str(entry.get("name") or entry.get("title") or "").strip()
                    if name:
                        names.append(name)
                else:
                    name = str(entry).strip()
                    if name:
                        names.append(name)
        elif entries:
            name = str(entries).strip()
            if name:
                names.append(name)
        return " / ".join(names)

    def _prefers_original_lyrics(self, title: str, artist: str) -> bool:
        text = f"{title} {artist}"
        latin_count = len(re.findall(r"[A-Za-z]", text))
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
        return latin_count >= 3 and cjk_count == 0

    def _secondary_on_original_clock(self, original: str, reference: str, secondary: str) -> str:
        """Associate LRC companions with matching YRC originals in one payload.

        NetEase can publish its line and unit originals on different clocks.
        Keep every measured YRC start/end untouched. First pair the secondary
        to its own LRC original, then bridge only equal original text in order;
        unmatched companions retain their source stamp and the usual tolerance.
        """
        original_rows, _ = self._parse_lrc_text(original)
        reference_rows, _ = self._parse_lrc_text(reference)
        secondary_rows, _ = self._parse_lrc_text(secondary)
        if not (original_rows and reference_rows and secondary_rows):
            return secondary
        reference_to_original = {
            reference_index: original_index
            for original_index, reference_index in self._ordered_lyric_row_pairs(
                original_rows, reference_rows, same_original_text=True,
            )
        }
        secondary_starts: dict[int, float] = {}
        absent_original_companions: set[int] = set()
        for reference_index, secondary_index in self._ordered_lyric_row_pairs(reference_rows, secondary_rows):
            if reference_index in reference_to_original:
                secondary_starts[secondary_index] = original_rows[reference_to_original[reference_index]].time_seconds
            else:
                absent_original_companions.add(secondary_index)
        if not secondary_starts and not absent_original_companions:
            return secondary
        return "\n".join(
            f"[{self._format_lrc_time(secondary_starts.get(index, line.time_seconds))}]{line.text}"
            for index, line in enumerate(secondary_rows) if index not in absent_original_companions
        )

    def _extract_netease_lyric_text(
        self,
        payload: Any,
        prefer_original: bool = False,
        secondary_source: str = "translation",
        prefer_secondary: bool = False,
    ) -> str | tuple[str, ...]:
        if not isinstance(payload, dict):
            return ""
        timed_sources: list[str] = []
        original_kind = ""
        # NetEase's yrc field has real word durations. Check that at least one
        # line parses into units before using it; malformed yrc must not hide
        # a valid line-level lrc fallback.
        for key in ("yrc", "lrc"):
            value = payload.get(key)
            if isinstance(value, dict):
                lyric = value.get("lyric") or value.get("text")
                if isinstance(lyric, str) and lyric.strip():
                    if key == "yrc" and not any(line.units for line in self._parse_lrc_text(lyric)[0]):
                        continue
                    timed_sources.append(lyric)
                    original_kind = key
                    break
        secondary_mode = str(secondary_source or "translation").strip().casefold()
        raw_evidence = "\n".join(
            str(value.get("lyric") or value.get("text") or "")
            for value in payload.values() if isinstance(value, dict)
        )
        cantonese = has_cantonese_evidence(raw_evidence, str(payload.get("language") or "") + " " + raw_evidence)
        roman_keys = ("yromalrc", "romalrc", "romaLrc", "romalyrics") if original_kind == "yrc" else (
            "romalrc", "romaLrc", "romalyrics", "yromalrc",
        )
        translated_keys = ("ytlrc", "tlyric") if original_kind == "yrc" else ("tlyric", "ytlrc")
        if secondary_mode in {"transliteration", "romanization", "romaji", "phonetic", "roma"}:
            secondary_keys = ("yromalrc", "romalrc", "romaLrc", "romalyrics") if original_kind == "yrc" else (
                "romalrc", "romaLrc", "romalyrics", "yromalrc",
            )
        elif secondary_mode in {"none", "off", "disabled"}:
            secondary_keys = ()
        else:
            secondary_keys = translated_keys
        if cantonese and prefer_secondary:
            secondary_keys = (roman_keys + translated_keys if self._cantonese_priority() == "phonetic_first"
                              else translated_keys + roman_keys)
        if prefer_original and not prefer_secondary:
            if timed_sources:
                return timed_sources[0]
            for key in ("klyric",):
                value = payload.get(key)
                if isinstance(value, dict):
                    lyric = value.get("lyric") or value.get("text")
                    if isinstance(lyric, str) and lyric.strip():
                        return lyric
            lyric = payload.get("lyric")
            if isinstance(lyric, str) and lyric.strip():
                return lyric
            return ""
        added_secondary_families: set[str] = set()
        for key in secondary_keys:
            value = payload.get(key)
            if isinstance(value, dict):
                lyric = value.get("lyric") or value.get("text")
                if isinstance(lyric, str) and lyric.strip():
                    family = "phonetic" if key in roman_keys else "translation"
                    if family in added_secondary_families:
                        continue
                    if key in {"ytlrc", "yromalrc"} and not self._parse_lrc_text(lyric)[0]:
                        continue
                    if timed_sources and original_kind == "yrc" and key not in {"ytlrc", "yromalrc"}:
                        line_source = payload.get("lrc")
                        reference = (line_source.get("lyric") or line_source.get("text")) if isinstance(line_source, dict) else None
                        if isinstance(reference, str) and reference.strip():
                            lyric = self._secondary_on_original_clock(timed_sources[0], reference, lyric)
                    if cantonese and prefer_secondary:
                        secondary_rows, _plain = self._parse_lrc_text(lyric)
                        label = "粤语音译：" if family == "phonetic" else "粤语译文："
                        labeled = tuple(TimedLyricLine(row.time_seconds, label + row.text) for row in secondary_rows)
                        lyric = self._render_lrc("", "", "", LyricsMatch(labeled, tuple(row.text for row in labeled)))
                    timed_sources.append(lyric)
                    added_secondary_families.add(family)
                    if not (cantonese and prefer_secondary):
                        break
        if cantonese and prefer_secondary and timed_sources:
            timed_sources[0] = "[language:yue]\n" + timed_sources[0]
        if timed_sources:
            return tuple(timed_sources)
        for key in ("yrc", "klyric", "tlyric"):
            value = payload.get(key)
            if isinstance(value, dict):
                lyric = value.get("lyric") or value.get("text")
                if isinstance(lyric, str) and lyric.strip():
                    return lyric
        lyric = payload.get("lyric")
        if isinstance(lyric, str):
            return lyric
        return ""

    @staticmethod
    def _decode_qq_lyric_field(lyric: Any) -> str:
        if not isinstance(lyric, str):
            return ""
        lyric = lyric.strip()
        if not lyric:
            return ""
        if "[" in lyric or "\n" in lyric:
            return lyric
        try:
            padding = "=" * (-len(lyric) % 4)
            decoded = base64.b64decode((lyric + padding).encode("ascii"), validate=False)
            return decoded.decode("utf-8", errors="replace")
        except Exception:
            return lyric

    def _extract_qq_lyric_text(self, payload: Any) -> str | tuple[str, ...]:
        if not isinstance(payload, dict):
            return ""
        lyric = payload.get("lyric") or payload.get("lyricinfo") or ""
        data = payload.get("data")
        if not lyric and isinstance(data, dict):
            lyric = data.get("lyric") or data.get("lyricinfo") or ""
        original = self._decode_qq_lyric_field(lyric)
        if not original:
            return ""
        translation = payload.get("trans")
        if not translation and isinstance(data, dict):
            translation = data.get("trans")
        translated = self._decode_qq_lyric_field(translation)
        if translated and translated != original and self._dual_line_secondary_source() == "translation":
            return original, translated
        return original

    def _fetch_json(self, url: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> Any | None:
        text = self._fetch_text(url, params=params, headers=headers)
        if not text:
            return None
        raw = text.strip()
        if not raw:
            return None
        match = JSONP_RE.match(raw)
        if match:
            raw = match.group(1).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    return None
            return None

    def _fetch_text(self, url: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> str:
        query = urlencode(params or {}, doseq=True)
        full_url = f"{url}?{query}" if query else url
        request_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if headers:
            request_headers.update(headers)
        request = Request(full_url, headers=request_headers)
        try:
            with urlopen(request, timeout=self.request_timeout) as response:
                data = response.read()
                encoding = response.headers.get_content_charset() or "utf-8"
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return ""
        try:
            return data.decode(encoding, errors="replace")
        except LookupError:
            return data.decode("utf-8", errors="replace")

    def _netease_headers(self) -> dict[str, str]:
        return {
            "Referer": "https://music.163.com/",
            "Origin": "https://music.163.com",
        }

    def _qq_search_headers(self) -> dict[str, str]:
        return {
            "Referer": "https://y.qq.com/portal/player.html",
            "Origin": "https://y.qq.com",
        }

    def _qq_lyric_headers(self) -> dict[str, str]:
        return {
            "Referer": "https://y.qq.com/portal/player.html",
            "Origin": "https://y.qq.com",
        }

    def _lrclib_headers(self) -> dict[str, str]:
        return {
            "Referer": "https://lrclib.net/",
            "Origin": "https://lrclib.net",
        }
