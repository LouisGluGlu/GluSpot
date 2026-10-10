"""Conservative artwork fallback for the internal, non-commercial test build.

MusicBrainz is metadata, not a guarantee that the first search hit is the
playing release. Keep the complete version title, album, credited performers
and source duration. Multiple exact editions can share one verified album
release-group front; otherwise reject the ambiguous artwork.
"""
from __future__ import annotations

from collections import OrderedDict
import json
import math
import re
import threading
import time
import unicodedata
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import UUID

ARTWORK_USER_AGENT = "GluSpot/internal-test (Windows non-commercial artwork fallback)"
ARTWORK_JSON_BYTE_LIMIT = 2 * 1024 * 1024


def normalize_artwork_url(url: str, size: int = 2560) -> str:
    """Request larger pixels from the same known NetEase artwork object.

    Unknown hosts, paths and signed queries retain their exact URL. This is
    an upgrade of player-bound artwork, not another song metadata search.
    """
    value = str(url or "").strip()
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username or parsed.password
            or parsed.port not in {None, 80, 443}
            or not re.fullmatch(r"(?:p\d+\.)?music\.126\.net", parsed.hostname or "", re.IGNORECASE)
            or not re.search(r"\.(?:jpe?g|png|webp)$", parsed.path, re.IGNORECASE)
        ):
            return value
        params = parse_qsl(parsed.query, keep_blank_values=True)
        if any(key.casefold() != "param" for key, _value in params):
            return value
        requested_side = max(320, min(4096, int(size)))
    except (TypeError, ValueError):
        return value
    return urlunsplit((
        parsed.scheme, parsed.netloc, parsed.path,
        urlencode({"param": f"{requested_side}y{requested_side}"}), parsed.fragment,
    ))


def read_artwork_response(response, deadline: float, *, clock=time.monotonic) -> bytes:
    """Observe the budget between HTTPResponse.read1 calls, not hard-cancel IO."""
    content = bytearray()
    while True:
        if clock() >= deadline:
            raise TimeoutError("artwork metadata budget expired")
        chunk = response.read1(min(64 * 1024, ARTWORK_JSON_BYTE_LIMIT + 1 - len(content)))
        if clock() >= deadline:
            raise TimeoutError("artwork metadata budget expired")
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > ARTWORK_JSON_BYTE_LIMIT:
            raise ValueError("artwork metadata exceeded byte limit")


def artwork_text_key(value: str) -> str:
    # Preserve punctuation and version qualifiers; never strip Live/Remix.
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def artwork_artist_key(value: str) -> tuple[str, ...]:
    return tuple(sorted(artwork_text_key(part) for part in re.split(r"[/、;；]", str(value or "")) if part.strip()))


def artwork_duration_matches(expected_seconds: float, candidate_seconds: float, *, require_known: bool = False) -> bool:
    try:
        expected = float(expected_seconds or 0.0)
        candidate = float(candidate_seconds or 0.0)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(expected) or not math.isfinite(candidate):
        return False
    if expected <= 0.0 or candidate <= 0.0:
        return not require_known
    return abs(expected - candidate) <= 2.0


def musicbrainz_credit_names(credits) -> str:
    if not isinstance(credits, list):
        return ""
    names = []
    for credit in credits:
        if not isinstance(credit, dict):
            continue
        artist = credit.get("artist") or {}
        name = str(credit.get("name") or (artist.get("name") if isinstance(artist, dict) else "") or "").strip()
        if name:
            names.append(name)
    return "/".join(names)


def _valid_mbid(value) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return ""


def _musicbrainz_matching_releases(payload, title: str, artist: str, album_title: str, duration_seconds: float) -> dict[str, set[str]]:
    """Return exact editions and their album groups from a complete search page."""
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("recordings")
    if not isinstance(rows, list):
        return {}
    try:
        if int(payload.get("count", len(rows))) > len(rows):
            return {}  # An omitted page may contain another matching edition.
    except (TypeError, ValueError):
        return {}
    title_key, album_key = artwork_text_key(title), artwork_text_key(album_title)
    artist_key = artwork_artist_key(artist)
    if not title_key or not artist_key:
        return {}
    releases: dict[str, set[str]] = {}
    for recording in rows:
        if not isinstance(recording, dict) or recording.get("video") is True:
            continue
        if artwork_text_key(recording.get("title")) != title_key:
            continue
        if artwork_artist_key(musicbrainz_credit_names(recording.get("artist-credit"))) != artist_key:
            continue
        try:
            length = float(recording.get("length") or 0.0) / 1000.0
        except (TypeError, ValueError):
            continue
        if not artwork_duration_matches(duration_seconds, length, require_known=True):
            continue
        for release in recording.get("releases") or ():
            if not isinstance(release, dict) or (album_key and artwork_text_key(release.get("title")) != album_key):
                continue
            if str(release.get("status") or "").casefold() != "official":
                continue
            if artwork_artist_key(musicbrainz_credit_names(release.get("artist-credit"))) != artist_key:
                continue
            release_id = _valid_mbid(release.get("id"))
            if not release_id:
                continue
            # Search media contains only the tracks associated with this
            # recording, under 'track' rather than lookup's 'tracks'.
            matched_track = False
            for medium in release.get("media") or ():
                if not isinstance(medium, dict):
                    continue
                for track in medium.get("track") or ():
                    if not isinstance(track, dict) or artwork_text_key(track.get("title")) != title_key:
                        continue
                    try:
                        track_length = float(track.get("length") or 0.0) / 1000.0
                    except (TypeError, ValueError):
                        continue
                    if artwork_duration_matches(duration_seconds, track_length, require_known=True):
                        matched_track = True
            if matched_track:
                group = release.get("release-group") or {}
                group_id = _valid_mbid(group.get("id")) if isinstance(group, dict) else ""
                releases.setdefault(release_id, set()).add(group_id)
    return releases


def musicbrainz_release_id(payload, title: str, artist: str, album_title: str, duration_seconds: float) -> str:
    """Return a unique edition backed by a matching track in the search data."""
    releases = _musicbrainz_matching_releases(payload, title, artist, album_title, duration_seconds)
    return next(iter(releases)) if len(releases) == 1 else ""


def musicbrainz_release_group(payload, title: str, artist: str, album_title: str, duration_seconds: float) -> tuple[str, frozenset[str]]:
    """Accept editions only when every exact match shares one album group."""
    releases = _musicbrainz_matching_releases(payload, title, artist, album_title, duration_seconds)
    if not releases:
        return "", frozenset()
    groups = {group for release_groups in releases.values() for group in release_groups}
    if len(groups) != 1 or not next(iter(groups)):
        return "", frozenset()
    return next(iter(groups)), frozenset(releases)


def cover_art_archive_front_url(payload, release_id: str, *, bounded: bool = False) -> str:
    release_id = _valid_mbid(release_id)
    if not isinstance(payload, dict) or not release_id:
        return ""
    release = urlsplit(str(payload.get("release") or ""))
    if (
        release.scheme not in {"http", "https"} or release.username or release.password
        or release.hostname != "musicbrainz.org" or release.path != f"/release/{release_id}"
    ):
        return ""
    for image in payload.get("images") or ():
        if not isinstance(image, dict) or image.get("front") is not True or image.get("approved") is not True:
            continue
        url = urlsplit(str(image.get("image") or ""))
        host = str(url.hostname or "").casefold()
        if url.scheme not in {"http", "https"} or url.username or url.password:
            continue
        if host != "coverartarchive.org" and host != "archive.org" and not host.endswith(".archive.org"):
            continue
        if bounded:
            # CAA originals can exceed the downloader's 8 MiB budget. Use
            # the documented size endpoint of this verified front rather
            # than caching an undeliverable original URL for a full day.
            return _cover_art_archive_bounded_front_url(image, "release", release_id)
        return urlunsplit(("https", url.netloc, url.path, url.query, ""))
    return ""


def _cover_art_archive_bounded_front_url(image: dict, kind: str, resource_id: str) -> str:
    thumbnails = image.get("thumbnails") or {}
    if isinstance(thumbnails, dict) and thumbnails.get("1200"):
        candidate = urlsplit(str(thumbnails["1200"]))
        host = str(candidate.hostname or "").casefold()
        if (
            candidate.scheme in {"http", "https"}
            and not candidate.username and not candidate.password
            and (host == "coverartarchive.org" or host == "archive.org" or host.endswith(".archive.org"))
        ):
            return urlunsplit(("https", candidate.netloc, candidate.path, candidate.query, ""))
    if not _valid_mbid(resource_id):
        return ""
    return f"https://coverartarchive.org/{kind}/{resource_id}/front-1200"


def cover_art_archive_group_front_url(payload, group_id: str, verified_releases: frozenset[str]) -> str:
    """Use the approved group front only when its member is an exact match."""
    group_id = _valid_mbid(group_id)
    if not isinstance(payload, dict) or not group_id or not verified_releases:
        return ""
    member = urlsplit(str(payload.get("release") or ""))
    if member.scheme not in {"http", "https"} or member.hostname != "musicbrainz.org" or member.username or member.password:
        return ""
    member_id = _valid_mbid(member.path.removeprefix("/release/"))
    if member.path != f"/release/{member_id}" or member_id not in verified_releases:
        return ""
    for image in payload.get("images") or ():
        # Validate this exact image before reading its size variants. CAA
        # can omit the 1200 thumbnail while its /front-1200 still exists.
        if not cover_art_archive_front_url({"release": payload.get("release"), "images": [image]}, member_id):
            continue
        return _cover_art_archive_bounded_front_url(image, "release-group", group_id)
    return ""


class MusicBrainzRequestGate:
    """One process-wide request start per second, including different tracks."""
    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self._next_at = 0.0
        self._lock = threading.Lock()

    def wait(self, deadline: float) -> bool:
        remaining = deadline - self.clock()
        if remaining <= 0.0 or not self._lock.acquire(timeout=remaining):
            return False
        try:
            now = self.clock()
            delay = max(0.0, self._next_at - now)
            if now + delay >= deadline:
                return False
            if delay:
                self.sleep(delay)
            now = self.clock()
            if now >= deadline:
                return False
            self._next_at = now + 1.01
            return True
        finally:
            self._lock.release()


_MUSICBRAINZ_REQUEST_GATE = MusicBrainzRequestGate()


class MusicBrainzArtworkResolver:
    CACHE_LIMIT = 64
    MISS_SECONDS = 600.0
    FAILURE_SECONDS = 60.0
    SUCCESS_SECONDS = 24 * 60 * 60.0

    def __init__(self, *, clock=time.monotonic, request_gate=None, user_agent: str = ARTWORK_USER_AGENT):
        self.clock = clock
        self.request_gate = request_gate or _MUSICBRAINZ_REQUEST_GATE
        self.user_agent = str(user_agent or ARTWORK_USER_AGENT)
        self._cache: OrderedDict[tuple, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()
        self._failed_until = 0.0
        self._pending: set[tuple] = set()
        self._cache_generation = 0

    def clear_cache(self) -> None:
        """Forget lookup results without letting an older request restore them."""
        with self._lock:
            self._cache_generation += 1
            self._cache.clear()
            self._pending.clear()
            self._failed_until = 0.0

    def resolve(self, title: str, artist: str, album_title: str, duration_seconds: float, *, deadline: float | None = None) -> str:
        if not artwork_text_key(title) or not artwork_artist_key(artist):
            return ""
        if not artwork_duration_matches(duration_seconds, duration_seconds, require_known=True):
            return ""
        now = self.clock()
        # The provider's current-track generation has a nine-second delivery
        # deadline. A MusicBrainz search plus CAA group lookup can need more
        # than six seconds even when both services answer successfully.
        deadline = min(now + 8.5, float(deadline)) if deadline is not None else now + 8.5
        key = (artwork_text_key(title), artwork_artist_key(artist), artwork_text_key(album_title), round(float(duration_seconds), 3))
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached[0] > now:
                self._cache.move_to_end(key)
                return cached[1]
            if self._failed_until > now or now >= deadline or key in self._pending:
                return ""
            self._pending.add(key)
            generation = self._cache_generation
        url = ""
        failed = False
        try:
            def quoted(value):
                escaped = re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', str(value))
                return f'"{escaped}"'
            query = f"recording:{quoted(title)} AND artist:{quoted(artist)}"
            if artwork_text_key(album_title):
                query += f" AND release:{quoted(album_title)}"
            if not self.request_gate.wait(deadline):
                raise TimeoutError("artwork metadata rate-limit budget expired")
            payload = self._fetch_json("https://musicbrainz.org/ws/2/recording/?" + urlencode({"query": query, "fmt": "json", "limit": "12"}), deadline)
            release_id = musicbrainz_release_id(payload, title, artist, album_title, duration_seconds) if artwork_text_key(album_title) else ""
            if release_id:
                art = self._fetch_json(f"https://coverartarchive.org/release/{release_id}", deadline)
                url = cover_art_archive_front_url(art, release_id, bounded=True)
            else:
                group_id, releases = musicbrainz_release_group(payload, title, artist, album_title, duration_seconds)
                if group_id:
                    art = self._fetch_json(f"https://coverartarchive.org/release-group/{group_id}", deadline)
                    url = cover_art_archive_group_front_url(art, group_id, releases)
        except Exception:
            failed = True
        now = self.clock()
        with self._lock:
            if generation != self._cache_generation:
                return ""
            self._pending.discard(key)
            if failed:
                self._failed_until = now + self.FAILURE_SECONDS
            lifetime = self.SUCCESS_SECONDS if url else self.FAILURE_SECONDS if failed else self.MISS_SECONDS
            self._cache[key] = (now + lifetime, url)
            self._cache.move_to_end(key)
            while len(self._cache) > self.CACHE_LIMIT:
                self._cache.popitem(last=False)
        return url

    def _fetch_json(self, url: str, deadline: float):
        remaining = deadline - self.clock()
        if remaining <= 0.0:
            raise TimeoutError("artwork source budget expired")
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        try:
            with urlopen(request, timeout=min(3.0, remaining)) as response:
                content = read_artwork_response(response, deadline, clock=self.clock)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise
        return json.loads(content.decode("utf-8"))
