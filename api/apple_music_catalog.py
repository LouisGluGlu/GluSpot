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
    # Apple's public 30-second preview (unencrypted AAC); "" elsewhere.
    preview_url: str = ""


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


def preview_url(attributes: dict) -> str:
    """The catalog's 30-second preview address, https only."""
    rows = attributes.get("previews") if isinstance(attributes.get("previews"), list) else []
    for row in rows:
        url = _text(row.get("url")).strip() if isinstance(row, dict) else ""
        if url.startswith("https://") and len(url) <= 2000:
            return url
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
    preview = preview_url(attributes) or preview_url(catalog_attributes)
    return SongIdentity(source_id, song_id[:200], title[:400], artist, album, duration, artwork, catalog_id, preview)


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
        "preview_url": song.preview_url,
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


_RELEASE_SUFFIX = re.compile(r"\s+(single|ep)$")
# Platform notes such as 【独家】 say nothing about the recording (GoMusic drops them too).
_PLATFORM_NOTE = re.compile(r"\s*【[^】]*】")
_BRACKETED = re.compile(r"[\(\[（［]([^\)\]）］]*)[\)\]）］]")
_DASH_SUFFIX = re.compile(r"\s+[-–—]\s+(.+)$")
# Bracket or dash text that marks a different recording. Kept apart so "(Live)"
# never matches the studio song, while "(feat. X)" or "(Remastered)" do.
_VERSION_WORDS = (
    "live", "现场", "演唱会", "remix", "mix", "acoustic", "不插电", "instrumental", "inst", "伴奏", "纯音乐",
    "karaoke", "demo", "cover", "翻唱", "sped up", "speed up", "slowed", "nightcore", "8d", "dj", "版",
    "version", "ver.", "edit", "piano", "orchestral", "a cappella", "acapella",
)
_IGNORED_WORDS = ("feat", "ft.", "with ", "remaster", "explicit", "clean", "mono", "stereo")
_CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿]+")
_LATIN = re.compile(r"[a-z0-9][a-z0-9'.&]*(?:\s+[a-z0-9][a-z0-9'.&]*)*")


def album_key(album: str) -> str:
    """Album name for comparison: Apple adds " - Single" / " - EP" to the same release."""
    return _RELEASE_SUFFIX.sub("", fold(album)).strip()


def title_parts(title: str) -> tuple[str, frozenset]:
    """(core title, version marks). "晴天 (Live)" -> ("晴天", {"live"}); "(feat. X)" is dropped."""
    text = unicodedata.normalize("NFKC", title or "").casefold()
    text = _PLATFORM_NOTE.sub("", text)
    marks = set()
    extras = [match.group(1) for match in _BRACKETED.finditer(text)]
    text = _BRACKETED.sub(" ", text)
    dash = _DASH_SUFFIX.search(text)
    if dash:
        extras.append(dash.group(1))
        text = text[:dash.start()]
    for extra in extras:
        extra = extra.strip()
        if not extra or any(word in extra for word in _IGNORED_WORDS):
            continue
        for word in _VERSION_WORDS:
            if word in extra:
                marks.add(word)
    core = re.sub(r"[^\w\s一-鿿]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", core).strip(), frozenset(marks)


def same_title(left: str, right: str) -> bool:
    """Same core title and the same version marks (Live, Remix, 伴奏 …)."""
    a, b = title_parts(left), title_parts(right)
    return bool(a[0]) and a == b


def artist_names(value: str) -> frozenset:
    """Every way one credit can be written: whole names, and their Chinese and Latin parts.

    "刘柏辛Lexie" -> {"刘柏辛lexie", "刘柏辛", "lexie"}, so it meets "Lexie Liu" (via
    "lexie") or "刘柏辛" on another platform.
    """
    folded = unicodedata.normalize("NFKC", value or "").casefold()
    names = set()
    for part in re.split(r"\s*(?:/|&|、|,|，|;|；| feat\.? | ft\.? | x | and | with )\s*", " " + folded + " "):
        part = re.sub(r"[\(（][^\)）]*[\)）]", " ", part).strip()
        if not part:
            continue
        whole = re.sub(r"[^\w一-鿿]", "", part)
        if len(whole) >= 2:
            names.add(whole)
        for cjk in _CJK.findall(part):
            if len(cjk) >= 2:
                names.add(cjk)
        for latin in _LATIN.findall(part):
            latin = latin.strip()
            words = latin.split()
            if len(latin) >= 3:
                names.add(latin.replace(" ", ""))
            names.update(word for word in words if len(word) >= 3)
    return frozenset(names)


def same_artist(left: str, right: str) -> bool:
    """Any shared name, or names that are nearly the same spelling."""
    a, b = artist_names(left), artist_names(right)
    if not a or not b:
        return False
    if a & b:
        return True
    return artist_strength(left, right) == "strong"


def _closest(source: SongIdentity, candidates):
    def key(candidate):
        same_album = bool(source.album and candidate.album and album_key(source.album) == album_key(candidate.album))
        gap = (abs(candidate.duration_seconds - source.duration_seconds)
               if candidate.duration_seconds and source.duration_seconds else 0.0)
        same_name = candidate.album.casefold().strip() == source.album.casefold().strip()
        return (not same_album, not same_name, gap)
    return min(candidates, key=key)


def same_release(source: SongIdentity, candidate: SongIdentity) -> bool:
    """Title, artist and album all agree: the same song on the same release."""
    if not source.album or not candidate.album:
        return False
    return (same_title(source.title, candidate.title) and same_artist(source.artist, candidate.artist)
            and album_key(source.album) == album_key(candidate.album))


def similarity(source: SongIdentity, candidate: SongIdentity) -> float:
    """How alike two songs look, 0..1: title most, then artist, duration and album."""
    a, b = title_parts(source.title), title_parts(candidate.title)
    title = _ratio(a[0], b[0])
    if a[1] != b[1]:
        title *= 0.8
    artist = 1.0 if same_artist(source.artist, candidate.artist) else _ratio(fold(source.artist), fold(candidate.artist))
    relation = duration_relation(source.duration_seconds, candidate.duration_seconds)
    duration = {"close": 1.0, "near": 0.5, "unknown": 0.5, "far": 0.0}[relation]
    album = 1.0 if source.album and candidate.album and album_key(source.album) == album_key(candidate.album) else 0.0
    score = (title * 5 + artist * 3 + duration * 1.5 + album * 0.5) / 10.0
    return score if title >= 0.5 else score * 0.5  # a different title is rarely the same song


# Never offered for a normal song: these are other products, not the recording.
_NEVER_SUGGEST = frozenset({"karaoke", "instrumental", "inst", "伴奏", "纯音乐", "nightcore", "sped up", "speed up",
                            "slowed", "8d"})


def _worth_asking(source: SongIdentity, candidate: SongIdentity) -> bool:
    """A fuzzy suggestion must be plausibly the same song.

    Same core title (only the version or the artist differs), or the same
    artist with a nearly identical title. Karaoke, instrumental and sped-up
    versions are never suggested for a song that is not one itself.
    """
    source_core, source_marks = title_parts(source.title)
    core, marks = title_parts(candidate.title)
    if (marks - source_marks) & _NEVER_SUGGEST:
        return False
    if core and core == source_core:
        return True
    return same_artist(source.artist, candidate.artist) and _ratio(source_core, core) >= 0.8


def classify_song(source: SongIdentity, candidates: tuple[SongIdentity, ...] | list[SongIdentity]) -> MatchRow:
    """Pick at most one target (rules confirmed by the user on 2026-11-18).

    Title and artist both agreeing (version marks such as Live must agree too;
    an artist may be written differently, e.g. "刘柏辛Lexie" / "Lexie Liu") is
    the same song: no question, no audio check. Among several, the same album,
    then the closest duration wins. Only when title or artist disagree is the
    match fuzzy, and then only the single most similar song is offered.
    """
    usable = [candidate for candidate in candidates
              if isinstance(candidate, SongIdentity) and candidate.song_id and candidate.title]
    exact = [candidate for candidate in usable
             if same_title(source.title, candidate.title) and same_artist(source.artist, candidate.artist)
             and duration_relation(source.duration_seconds, candidate.duration_seconds) != "far"]
    if exact:
        best = _closest(source, exact)
        same_album = bool(source.album and best.album and album_key(source.album) == album_key(best.album))
        return MatchRow("matched", source, best, "歌名、歌手、专辑都一致" if same_album else "歌名和歌手一致", 1.0)
    if not usable:
        return MatchRow("unmatched", source, None, "曲库里没有搜到", 0.0)
    worth = [candidate for candidate in usable if _worth_asking(source, candidate)]
    if not worth:
        return MatchRow("unmatched", source, None, "没有歌名接近的同一首歌", 0.0)
    best = max(worth, key=lambda candidate: similarity(source, candidate))
    score = similarity(source, best)
    reasons = []
    if not same_title(source.title, best.title):
        reasons.append("歌名不完全一样")
    if not same_artist(source.artist, best.artist):
        reasons.append("歌手名不一样")
    if duration_relation(source.duration_seconds, best.duration_seconds) == "far":
        reasons.append("时长差得多")
    return MatchRow("uncertain", source, best, "，".join(reasons) + "，这是最像的一首", score)


def rank_candidates(source: SongIdentity, candidates, limit: int = 1) -> tuple[SongIdentity, ...]:
    """The most similar candidate(s), best first. The sync window asks about one."""
    seen, usable = set(), []
    for candidate in candidates or ():
        if isinstance(candidate, SongIdentity) and candidate.song_id and candidate.title and candidate.song_id not in seen:
            seen.add(candidate.song_id)
            usable.append(candidate)
    usable.sort(key=lambda candidate: -similarity(source, candidate))
    return tuple(usable[:max(1, limit)])


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
