from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from api.models import TimedLyricLine

LRC_TIME_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
LRC_OFFSET_RE = re.compile(r"^\[offset\s*:\s*([+-]?\d+)\s*\]$", re.I)
ENHANCED_LRC_TIME_RE = re.compile(r"<\d{1,2}:\d{2}(?:[.:]\d{1,3})?>")
YRC_LINE_RE = re.compile(r"^\[(\d+)\s*,\s*(\d+)\](.*)$")
YRC_WORD_RE = re.compile(r"\(\d+\s*,\s*\d+(?:\s*,\s*\d+)?\)")
JSONP_RE = re.compile(r"^[A-Za-z_][\w.]*\((.*)\)\s*;?\s*$", re.S)
METADATA_RE = re.compile(r"^\[(?:ar|ti|al|by|offset|length|re|ve|kana|language):.*\]$", re.I)
ONLINE_SOURCE_ORDER = ("netease", "qq", "lrclib")


@dataclass(frozen=True)
class LyricsMatch:
    timed_lyrics: tuple[TimedLyricLine, ...]
    lyrics: tuple[str, ...]
    source: str = ""


class LocalLyricsStore:
    RESOLVED_CACHE_LIMIT = 128
    MISSED_CACHE_LIMIT = 256
    LOCAL_MISS_CACHE_LIMIT = 256
    LOCAL_FUZZY_FILE_LIMIT = 256
    TRANSIENT_MISS_RETRY_SECONDS = 45.0
    PERSISTED_CACHE_FILE_LIMIT = 96
    PERSISTED_CACHE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
    GENERATED_CACHE_DIR_ENV = "GLUSPOT_LYRICS_CACHE_DIR"

    def __init__(self, root: Path | None = None, request_timeout: float = 4.5, generated_root: Path | None = None):
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
        self._legacy_source_hits_path = self.root / ".source_hits.json"
        self._source_hits_path = self.generated_root / ".source_hits.json"
        self._source_hit_counts = self._load_source_hit_counts()
        self._indexed_local_lookup_enabled = root_provided
        self._resolved_cache: OrderedDict[str, LyricsMatch] = OrderedDict()
        self._pending_keys: set[str] = set()
        self._enrich_pending_keys: set[str] = set()
        self._missed_at: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._miss_retry_seconds = 600.0
        self._cache_lock = threading.Lock()
        self._local_miss_cache: OrderedDict[str, tuple[tuple[str, int], ...]] = OrderedDict()
        self._lyric_index_cache: dict[str, tuple[int, dict[str, tuple[Path, ...]]]] = {}
        self._lyric_index_lock = threading.Lock()

    @classmethod
    def _default_generated_root(cls, source_root: Path) -> Path:
        override = str(os.environ.get(cls.GENERATED_CACHE_DIR_ENV, "") or "").strip()
        if override:
            return Path(override)
        base_dir = str(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or "").strip()
        if base_dir:
            return Path(base_dir) / "GluSpot" / "lyrics-cache"
        return Path(source_root) / ".generated"

    def find(self, title: str, artist: str) -> tuple[tuple[TimedLyricLine, ...], str]:
        lines, source = self._read_local(title, artist)
        return lines, source

    def resolve_cached_or_local(self, title: str, artist: str, album_title: str = "", duration_seconds: float = 0.0) -> LyricsMatch:
        cache_key = self._cache_key(title, artist, album_title, duration_seconds)
        with self._cache_lock:
            cached = self._resolved_cache.get(cache_key)
            if cached is not None:
                self._resolved_cache.move_to_end(cache_key)
        if cached is not None:
            return cached

        local_lines, local_source = self._read_local(title, artist)
        if local_lines:
            match = LyricsMatch(local_lines, tuple(line.text for line in local_lines), f"local:{Path(local_source).name}")
            self._store_cache(cache_key, match)
            return match
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
        thread = threading.Thread(
            target=self._resolve_background,
            args=(cache_key, title, artist, album_title, duration_seconds),
            name="LyricsResolver",
            daemon=True,
        )
        thread.start()
        return True

    def enrich_in_background(self, title: str, artist: str, album_title: str = "", duration_seconds: float = 0.0) -> bool:
        cache_key = self._cache_key(title, artist, album_title, duration_seconds)
        with self._cache_lock:
            if cache_key in self._enrich_pending_keys:
                return False
            self._enrich_pending_keys.add(cache_key)
        thread = threading.Thread(
            target=self._enrich_background,
            args=(cache_key, title, artist, album_title, duration_seconds),
            name="LyricsEnricher",
            daemon=True,
        )
        thread.start()
        return True

    def is_lookup_pending(self, title: str, artist: str, album_title: str = "", duration_seconds: float = 0.0) -> bool:
        cache_key = self._cache_key(title, artist, album_title, duration_seconds)
        with self._cache_lock:
            return cache_key in self._pending_keys

    def resolve(self, title: str, artist: str, album_title: str = "", duration_seconds: float = 0.0) -> LyricsMatch:
        cache_key = self._cache_key(title, artist, album_title, duration_seconds)
        with self._cache_lock:
            cached = self._resolved_cache.get(cache_key)
            if cached is not None:
                self._resolved_cache.move_to_end(cache_key)
        if cached is not None:
            return cached

        local_match = self.resolve_cached_or_local(title, artist, album_title, duration_seconds)
        if local_match.timed_lyrics or local_match.lyrics:
            return local_match

        match = self._resolve_online(title, artist, album_title, duration_seconds)
        if match.timed_lyrics or match.lyrics:
            self._persist_match(title, artist, album_title, match)
            self._store_cache(cache_key, match)
        return match

    def _read_local(self, title: str, artist: str) -> tuple[tuple[TimedLyricLine, ...], str]:
        miss_key = self._local_lookup_key(title, artist)
        roots_signature = self._local_roots_signature()
        with self._cache_lock:
            cached_signature = self._local_miss_cache.get(miss_key)
            if cached_signature == roots_signature:
                self._local_miss_cache.move_to_end(miss_key)
                return (), ""
        for path in self._candidate_paths(title, artist):
            lines = self._read_lrc(path)
            if lines:
                self._clear_local_miss(miss_key)
                return lines, str(path)
        self._store_local_miss(miss_key, roots_signature)
        return (), ""

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
        raw = f"{title}\0{artist}"
        return raw.casefold()

    def _store_cache(self, key: str, match: LyricsMatch):
        with self._cache_lock:
            self._resolved_cache[key] = match
            self._resolved_cache.move_to_end(key)
            while len(self._resolved_cache) > int(getattr(self, "RESOLVED_CACHE_LIMIT", 128)):
                self._resolved_cache.popitem(last=False)
            self._missed_at.pop(key, None)

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

    def _store_miss(self, key: str, now: float | None = None, retry_seconds: float | None = None):
        with self._cache_lock:
            retry_window = self._miss_retry_seconds if retry_seconds is None else float(retry_seconds)
            self._missed_at[key] = (time.monotonic() if now is None else float(now), max(0.0, retry_window))
            self._missed_at.move_to_end(key)
            while len(self._missed_at) > int(getattr(self, "MISSED_CACHE_LIMIT", 256)):
                self._missed_at.popitem(last=False)

    def _resolve_background(self, cache_key: str, title: str, artist: str, album_title: str, duration_seconds: float):
        match = LyricsMatch((), (), "")
        try:
            match = self._resolve_online(title, artist, album_title, duration_seconds)
        finally:
            with self._cache_lock:
                self._pending_keys.discard(cache_key)
        if match.timed_lyrics or match.lyrics:
            self._persist_match(title, artist, album_title, match)
            self._store_cache(cache_key, match)
        else:
            self._store_miss(cache_key, retry_seconds=float(getattr(self, "TRANSIENT_MISS_RETRY_SECONDS", 45.0)))

    def _enrich_background(self, cache_key: str, title: str, artist: str, album_title: str, duration_seconds: float):
        try:
            match = self._resolve_online(title, artist, album_title, duration_seconds)
            if match.timed_lyrics and self._has_multiline_timed_lyrics(match.timed_lyrics):
                self._persist_match(title, artist, album_title, match, overwrite=True)
                self._store_cache(cache_key, match)
        finally:
            with self._cache_lock:
                self._enrich_pending_keys.discard(cache_key)

    def _resolve_online(self, title: str, artist: str, album_title: str, duration_seconds: float) -> LyricsMatch:
        resolvers = self._ordered_online_resolvers()
        for source_name, resolver in resolvers:
            match = resolver(title, artist, album_title, duration_seconds)
            if match is not None:
                if match.timed_lyrics or match.lyrics:
                    self._record_source_hit(str(match.source or source_name))
                return match
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
                content = self._clean_timed_lyric_content(yrc_match.group(3))
                if content:
                    timed.append(TimedLyricLine(timestamp, content))
                continue
            matches = list(LRC_TIME_RE.finditer(raw_line))
            content = self._clean_timed_lyric_content(LRC_TIME_RE.sub("", raw_line))
            if matches and content:
                for match in matches:
                    minutes = int(match.group(1))
                    seconds = int(match.group(2))
                    fraction = match.group(3) or "0"
                    millis = int(fraction.ljust(3, "0")[:3])
                    timestamp = max(0.0, minutes * 60 + seconds + millis / 1000.0 + offset_seconds)
                    timed.append(TimedLyricLine(timestamp, content))
                continue
            if line:
                plain.append(line)
        timed.sort(key=lambda item: item.time_seconds)
        return self._dedupe_timed_lines(timed), self._plain_lines(plain)

    def _clean_timed_lyric_content(self, value: str) -> str:
        value = ENHANCED_LRC_TIME_RE.sub("", value or "")
        value = YRC_WORD_RE.sub("", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    def _dedupe_timed_lines(self, lines: list[TimedLyricLine]) -> tuple[TimedLyricLine, ...]:
        result: list[TimedLyricLine] = []
        seen: set[tuple[int, str]] = set()
        for line in lines:
            text = line.text.strip()
            if not text:
                continue
            key = (int(round(line.time_seconds * 1000)), text)
            if key in seen:
                continue
            seen.add(key)
            result.append(TimedLyricLine(max(0.0, line.time_seconds), text))
        return tuple(result)

    def _plain_lines(self, lines: list[str]) -> tuple[str, ...]:
        result: list[str] = []
        for line in lines:
            cleaned = line.strip()
            if cleaned and not METADATA_RE.match(cleaned):
                result.append(cleaned)
        return tuple(result)

    def _synthesize_timed_lyrics(self, lines: tuple[str, ...], step_seconds: float = 3.0) -> tuple[TimedLyricLine, ...]:
        return tuple(TimedLyricLine(index * step_seconds, line) for index, line in enumerate(lines))

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
                return LyricsMatch(timed, tuple(line.text for line in timed), source)
            plain_lines: list[str] = []
            for _timed, plain in parsed:
                plain_lines.extend(plain)
            if plain_lines:
                plain = tuple(plain_lines)
                if not self._lyrics_have_vocal_content(plain):
                    return None
                synth = self._synthesize_timed_lyrics(plain)
                return LyricsMatch(synth, plain, source)
            return None
        if not text:
            return None
        timed, plain = self._parse_lrc_text(text)
        if timed:
            if not self._lyrics_have_vocal_content(tuple(line.text for line in timed)):
                return None
            return LyricsMatch(timed, tuple(line.text for line in timed), source)
        if plain:
            if not self._lyrics_have_vocal_content(plain):
                return None
            synth = self._synthesize_timed_lyrics(plain)
            return LyricsMatch(synth, plain, source)
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

    def _merge_timed_lyric_sources(self, sources: list[tuple[TimedLyricLine, ...]]) -> tuple[TimedLyricLine, ...]:
        merged: list[TimedLyricLine] = []
        for source in sources:
            for line in source:
                text = str(line.text or "").strip()
                if text:
                    merged.append(TimedLyricLine(max(0.0, float(line.time_seconds)), text))
        if not merged:
            return ()
        merged.sort(key=lambda item: item.time_seconds)
        grouped: list[TimedLyricLine] = []
        group_time: float | None = None
        group_parts: list[str] = []

        def flush():
            if group_time is None or not group_parts:
                return
            if len(group_parts) == 1:
                text = group_parts[0]
            else:
                text = "\n".join(group_parts[:2])
            grouped.append(TimedLyricLine(group_time, text))

        for line in merged:
            line_time = max(0.0, float(line.time_seconds))
            text = " ".join(str(line.text or "").split())
            if group_time is None:
                group_time = line_time
                group_parts = [text]
                continue
            if abs(line_time - group_time) <= 0.18:
                if text not in group_parts:
                    group_parts.append(text)
                continue
            flush()
            group_time = line_time
            group_parts = [text]
        flush()
        return self._dedupe_timed_lines(grouped)

    def _read_lrc(self, path: Path) -> tuple[TimedLyricLine, ...]:
        if not path.exists() or not path.is_file():
            return ()
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = path.read_text(encoding="gb18030")
            except OSError:
                return ()
        except OSError:
            return ()

        timed, plain = self._parse_lrc_text(text)
        return timed or self._synthesize_timed_lyrics(plain)

    def _persist_match(self, title: str, artist: str, album_title: str, match: LyricsMatch, overwrite: bool = False):
        if not match.lyrics:
            return
        candidates = self._candidate_names(title, artist)
        if not candidates:
            return
        path = self.generated_root / f"{candidates[0]}.lrc"
        if path.exists() and not overwrite:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self._render_lrc(title, artist, album_title, match), encoding="utf-8")
        except OSError:
            return
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
        lines: list[str] = []
        if artist:
            lines.append(f"[ar:{artist}]")
        if title:
            lines.append(f"[ti:{title}]")
        if album_title:
            lines.append(f"[al:{album_title}]")
        if match.source:
            lines.append(f"[by:{match.source}]")
        timed = match.timed_lyrics or self._synthesize_timed_lyrics(match.lyrics)
        for lyric in timed:
            minutes = int(lyric.time_seconds // 60)
            seconds = lyric.time_seconds - minutes * 60
            stamp = f"[{minutes:02d}:{seconds:05.2f}]"
            for part in str(lyric.text or "").splitlines():
                cleaned = part.strip()
                if cleaned:
                    lines.append(f"{stamp}{cleaned}")
        return "\n".join(lines) + "\n"

    def _has_multiline_timed_lyrics(self, timed_lyrics: tuple[TimedLyricLine, ...]) -> bool:
        return any("\n" in str(line.text or "") for line in timed_lyrics)

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
        candidate = self._pick_best_candidate(songs, title, artist, album_title, self._netease_candidate_text)
        if candidate is None:
            return None
        song_id = candidate.get("id") or candidate.get("song_id") or candidate.get("songid")
        if song_id is None:
            return None
        lyric_payload = self._fetch_json(
            "https://music.163.com/api/song/lyric",
            params={"id": str(song_id), "lv": "1", "kv": "1", "tv": "-1"},
            headers=self._netease_headers(),
        )
        lyric_text = self._extract_netease_lyric_text(
            lyric_payload,
            prefer_original=self._prefers_original_lyrics(title, artist),
        )
        return self._lyrics_match_from_text(lyric_text, "netease")

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
        candidate = self._pick_best_candidate(songs, title, artist, album_title, self._qq_candidate_text)
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
        match = self._lrclib_match_from_payload(payload, "lrclib")
        if match is not None:
            return match

        search_payload = self._fetch_json(
            "https://lrclib.net/api/search",
            params={"q": self._search_query(title, artist, album_title)},
            headers=self._lrclib_headers(),
        )
        candidates = [item for item in search_payload if isinstance(item, dict)] if isinstance(search_payload, list) else []
        candidate = self._pick_best_candidate(candidates, title, artist, album_title, self._lrclib_candidate_text)
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
    ) -> dict[str, Any] | None:
        best_score = 0.0
        best_candidate: dict[str, Any] | None = None
        for candidate in candidates:
            candidate_title, candidate_artist, candidate_album = text_resolver(candidate)
            score = self._candidate_score(title, artist, album_title, candidate_title, candidate_artist, candidate_album)
            if score > best_score:
                best_score = score
                best_candidate = candidate
        if best_score < 0.35:
            return None
        return best_candidate

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

    def _extract_netease_lyric_text(self, payload: Any, prefer_original: bool = False) -> str | tuple[str, ...]:
        if not isinstance(payload, dict):
            return ""
        timed_sources: list[str] = []
        for key in ("lrc",):
            value = payload.get(key)
            if isinstance(value, dict):
                lyric = value.get("lyric") or value.get("text")
                if isinstance(lyric, str) and lyric.strip():
                    timed_sources.append(lyric)
                    break
        if prefer_original:
            if timed_sources:
                return timed_sources[0]
            for key in ("yrc", "klyric"):
                value = payload.get(key)
                if isinstance(value, dict):
                    lyric = value.get("lyric") or value.get("text")
                    if isinstance(lyric, str) and lyric.strip():
                        return lyric
            lyric = payload.get("lyric")
            if isinstance(lyric, str) and lyric.strip():
                return lyric
            return ""
        for key in ("tlyric", "ytlrc"):
            value = payload.get(key)
            if isinstance(value, dict):
                lyric = value.get("lyric") or value.get("text")
                if isinstance(lyric, str) and lyric.strip():
                    timed_sources.append(lyric)
                    break
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

    def _extract_qq_lyric_text(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        lyric = payload.get("lyric") or payload.get("lyricinfo") or ""
        data = payload.get("data")
        if not lyric and isinstance(data, dict):
            lyric = data.get("lyric") or data.get("lyricinfo") or ""
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
