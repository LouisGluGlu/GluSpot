"""Normalize public Apple Music resource JSON and compare songs.

The shapes below follow Apple's published catalog and library objects:
https://developer.apple.com/documentation/applemusicapi
This module does not talk to the network and does not keep tokens.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher


_DURATION_CLOSE_SECONDS = 3.0
_DURATION_CLOSE_RATIO = 0.02
_TITLE_STRONG = 0.92
_TITLE_WEAK = 0.84
_ARTIST_STRONG = 0.75
_ARTIST_WEAK = 0.6


@dataclass(frozen=True)
class SongIdentity:
    source_id: str
    song_id: str
    title: str
    artist: str = ""
    album: str = ""
    duration_seconds: float = 0.0
    artwork_url: str = ""
    catalog_id: str = ""


@dataclass(frozen=True)
class MatchRow:
    """One source song against the other catalog.

    matched: title, artist and duration are all close.
    uncertain: close enough to show, not close enough to write by default.
    unmatched: nothing safe to attach.
    """

    state: str
    source: SongIdentity
    target: SongIdentity | None
    reason: str
    score: float = 0.0


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _milliseconds(value) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number <= 0 or number > 24 * 3600 * 1000:
        return 0.0
    return number


def _number(value) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 and number < 24 * 3600 else 0.0


def artwork_url(value) -> str:
    """Turn an Apple artwork template into one https URL. Drop anything else."""
    if not isinstance(value, str):
        return ""
    url = value.replace("{w}", "500").replace("{h}", "500").replace("{f}", "jpg")
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith("https://") or len(url) > 2000:
        return ""
    return url


def _attributes(resource: dict) -> dict:
    attributes = resource.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def _catalog_id(resource: dict, attributes: dict) -> str:
    play = attributes.get("playParams") if isinstance(attributes.get("playParams"), dict) else {}
    catalog = _text(play.get("catalogId")).strip()
    if catalog:
        return catalog[:200]
    relationships = resource.get("relationships") if isinstance(resource.get("relationships"), dict) else {}
    catalog_rel = relationships.get("catalog") if isinstance(relationships.get("catalog"), dict) else {}
    rows = catalog_rel.get("data") if isinstance(catalog_rel.get("data"), list) else []
    if rows and isinstance(rows[0], dict):
        linked = _text(rows[0].get("id")).strip()
        if linked:
            return linked[:200]
    if _text(resource.get("type")) == "songs":
        return _text(resource.get("id")).strip()[:200]
    return ""


def _artwork_of(attributes: dict) -> str:
    artwork = attributes.get("artwork") if isinstance(attributes.get("artwork"), dict) else {}
    return artwork_url(_text(artwork.get("url")))


def song_from_resource(resource, *, source_id: str = "apple_music", resources: dict | None = None) -> SongIdentity | None:
    """Read one catalog or library song resource. Incomplete rows are dropped."""
    if not isinstance(resource, dict):
        return None
    song_id = _text(resource.get("id")).strip()
    attributes = _attributes(resource)
    title = _text(attributes.get("name") or resource.get("name")).strip()
    if not song_id or not title or len(song_id) > 200 or len(title) > 400:
        return None
    artist = _text(attributes.get("artistName") or resource.get("artist")).strip()[:300]
    album = _text(attributes.get("albumName") or resource.get("album_name")).strip()[:300]
    millis = attributes.get("durationInMillis", resource.get("durationInMillis"))
    # durationInMillis is documented in milliseconds and can exceed the
    # one-day cap used for values that are already in seconds.
    duration = _milliseconds(millis) / 1000.0 if _milliseconds(millis) else _number(resource.get("duration"))
    artwork = _artwork_of(attributes)
    catalog_id = _catalog_id(resource, attributes)
    catalog_bucket = resources.get("songs") if isinstance(resources, dict) else None
    catalog = catalog_bucket.get(catalog_id) if isinstance(catalog_bucket, dict) and catalog_id else None
    catalog_attributes = _attributes(catalog) if isinstance(catalog, dict) else {}
    if not artwork:
        artwork = _artwork_of(catalog_attributes)
    if not artist:
        artist = _text(catalog_attributes.get("artistName")).strip()[:300]
    if not album:
        album = _text(catalog_attributes.get("albumName")).strip()[:300]
    if not duration:
        duration = _milliseconds(catalog_attributes.get("durationInMillis")) / 1000.0
    return SongIdentity(source_id, song_id[:200], title[:400], artist, album, duration, artwork, catalog_id)


def playlist_from_resource(resource) -> dict | None:
    if not isinstance(resource, dict):
        return None
    # A folder is a container of playlists. It is not itself a playlist to write into.
    if _text(resource.get("type")) == "library-playlist-folders":
        return None
    playlist_id = _text(resource.get("id")).strip()
    attributes = _attributes(resource)
    title = _text(attributes.get("name") or resource.get("name")).strip()
    if not playlist_id or not title:
        return None
    artwork = attributes.get("artwork") if isinstance(attributes.get("artwork"), dict) else {}
    count = attributes.get("trackCount", resource.get("trackCount"))
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 0
    if count < 0 or count > 100000:
        count = 0
    return {
        "id": playlist_id[:200],
        "name": title[:400],
        "trackCount": count,
        "picUrl": artwork_url(_text(artwork.get("url"))),
    }


def _document(payload) -> dict:
    """One library document, whether MusicKit wrapped it or returned it raw."""
    if isinstance(payload, list):
        return {"data": [item for item in payload if isinstance(item, dict)]}
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("data")
    if isinstance(inner, dict) and any(isinstance(inner.get(key), (list, dict)) for key in ("data", "resources", "results")):
        return inner
    body = payload.get("body")
    if isinstance(body, dict) and any(key in body for key in ("data", "resources", "results")):
        return body
    return payload


def _hydrate_row(row, resources: dict):
    """Fill a sparse id/type row from the resources map when the name is not inline."""
    if not isinstance(row, dict):
        return None
    attributes = row.get("attributes")
    if isinstance(attributes, dict) and _text(attributes.get("name")).strip():
        return row
    kind = row.get("type")
    bucket = resources.get(kind) if isinstance(kind, str) else None
    full = bucket.get(row.get("id")) if isinstance(bucket, dict) else None
    return full if isinstance(full, dict) else row


def _resource_list(payload) -> list:
    """Accept a raw Apple payload or a list already unwrapped by the companion."""
    document = _document(payload)
    if not document:
        return []
    resources = document.get("resources") if isinstance(document.get("resources"), dict) else {}
    data = document.get("data")
    if isinstance(data, list):
        rows = []
        for item in data:
            hydrated = _hydrate_row(item, resources)
            if isinstance(hydrated, dict):
                rows.append(hydrated)
        if rows:
            return rows
    results = document.get("results") if isinstance(document.get("results"), dict) else document
    if isinstance(results, dict):
        for key in ("songs", "playlists", "library-songs", "library-playlists"):
            group = results.get(key)
            if isinstance(group, dict) and isinstance(group.get("data"), list):
                return [item for item in group["data"] if isinstance(item, dict)]
            if isinstance(group, list):
                return [item for item in group if isinstance(item, dict)]
    return []


def songs_from_payload(payload, *, source_id: str = "apple_music") -> tuple[SongIdentity, ...]:
    document = _document(payload)
    resources = document.get("resources") if isinstance(document.get("resources"), dict) else {}
    songs = []
    seen = set()
    for resource in _resource_list(payload):
        song = song_from_resource(resource, source_id=source_id, resources=resources)
        if song is None or song.song_id in seen:
            continue
        seen.add(song.song_id)
        songs.append(song)
    return tuple(songs)


def playlists_from_payload(payload) -> tuple[dict, ...]:
    rows = []
    seen = set()
    for resource in _resource_list(payload):
        playlist = playlist_from_resource(resource)
        if playlist is None or playlist["id"] in seen:
            continue
        seen.add(playlist["id"])
        rows.append(playlist)
    return tuple(rows)


def service_song(song: SongIdentity) -> dict:
    """Fields the existing OnlineMusicService song parser already understands."""
    return {
        "id": song.song_id,
        "name": song.title,
        "artist": song.artist,
        "album_name": song.album,
        "duration": song.duration_seconds,
        "picUrl": song.artwork_url,
        "catalog_id": song.catalog_id,
    }


def fold(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = text.replace("’", "'").replace("“", "").replace("”", "")
    text = re.sub(r"[\(\[（【].*?[\)\]）】]", " ", text)
    text = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _artist_tokens(value: str) -> tuple[str, ...]:
    folded = fold(value)
    raw = re.split(r"\s*(?:/|&|、|,| feat | ft )\s*", " " + folded + " ")
    tokens = []
    for token in raw:
        token = token.strip()
        if len(token) >= 2 and token not in tokens:
            tokens.append(token)
    if not tokens and folded:
        tokens.append(folded)
    return tuple(tokens)


def title_strength(left: str, right: str) -> str:
    ratio = _ratio(fold(left), fold(right))
    if ratio >= _TITLE_STRONG:
        return "strong"
    if ratio >= _TITLE_WEAK:
        return "weak"
    return "none"


def artist_strength(left: str, right: str) -> str:
    folded_left, folded_right = fold(left), fold(right)
    if not folded_left and not folded_right:
        return "unknown"
    ratio = _ratio(folded_left, folded_right)
    left_tokens, right_tokens = _artist_tokens(left), _artist_tokens(right)
    shared = set(left_tokens) & set(right_tokens)
    smaller = max(1, min(len(left_tokens), len(right_tokens)))
    overlap = len(shared) / smaller if left_tokens and right_tokens else 0.0
    if ratio >= _ARTIST_STRONG or overlap >= 0.5 and ratio >= 0.72:
        return "strong"
    if ratio >= _ARTIST_WEAK or overlap > 0 and ratio >= 0.55:
        return "weak"
    return "none"


def duration_relation(left: float, right: float) -> str:
    if left <= 0 or right <= 0:
        return "unknown"
    gap = abs(left - right)
    limit = max(_DURATION_CLOSE_SECONDS, _DURATION_CLOSE_RATIO * max(left, right))
    if gap <= limit:
        return "close"
    if gap <= 15:
        return "near"
    return "far"


def _pair_score(title: str, artist: str, duration: str) -> float:
    weights = {"strong": 1.0, "weak": 0.45, "none": 0.0, "unknown": 0.35, "close": 1.0, "near": 0.2, "far": 0.0}
    return weights[title] * 3 + weights[artist] * 2 + weights[duration] * 2


def classify_song(source: SongIdentity, candidates: tuple[SongIdentity, ...] | list[SongIdentity]) -> MatchRow:
    """Pick at most one target. A tie between two good songs stays uncertain."""
    ranked = []
    for candidate in candidates:
        if not isinstance(candidate, SongIdentity) or not candidate.song_id or not candidate.title:
            continue
        title = title_strength(source.title, candidate.title)
        artist = artist_strength(source.artist, candidate.artist)
        duration = duration_relation(source.duration_seconds, candidate.duration_seconds)
        if title == "none":
            continue
        ranked.append((_pair_score(title, artist, duration), title, artist, duration, candidate))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return MatchRow("unmatched", source, None, "曲库里没有歌名接近的歌", 0.0)
    score, title, artist, duration, target = ranked[0]
    rival = ranked[1][0] if len(ranked) > 1 else -1
    if title == "strong" and artist == "strong" and duration == "close":
        if rival >= score - 0.15 and ranked[1][1] == "strong" and ranked[1][2] == "strong" and ranked[1][3] == "close":
            return MatchRow("uncertain", source, target, "有两首同样接近，需要你选", score)
        return MatchRow("matched", source, target, "歌名、歌手、时长都接近", score)
    if title == "strong" and artist == "strong" and duration == "unknown":
        return MatchRow("uncertain", source, target, "歌名和歌手接近，但没有时长，不能直接当同一首", score)
    if title == "strong" and artist == "unknown" and duration == "close":
        return MatchRow("uncertain", source, target, "歌名和时长接近，但两边都没有歌手名", score)
    if title == "strong" and artist == "strong" and duration == "near":
        return MatchRow("uncertain", source, target, "歌名和歌手接近，时长差得有点多", score)
    if title == "strong" and artist == "weak" and duration == "close":
        return MatchRow("uncertain", source, target, "歌名和时长接近，歌手只对上一部分", score)
    if title == "weak" and artist == "strong" and duration == "close":
        return MatchRow("uncertain", source, target, "歌手和时长接近，歌名只是像", score)
    if duration == "far":
        return MatchRow("unmatched", source, None, "时长差太多，不当成同一首", score)
    return MatchRow("unmatched", source, None, "歌名、歌手、时长没有同时接近", score)


def build_preview(songs: tuple[SongIdentity, ...] | list[SongIdentity], finder) -> tuple[MatchRow, ...]:
    """finder(song) returns candidate songs. It must not write anything."""
    rows = []
    for song in songs:
        if not isinstance(song, SongIdentity):
            continue
        found = finder(song)
        candidates = tuple(found) if found is not None else ()
        rows.append(classify_song(song, candidates))
    return tuple(rows)
