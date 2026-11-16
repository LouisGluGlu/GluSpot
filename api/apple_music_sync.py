"""Playlist sync plans. This module never deletes a song or a playlist.

A confirmed import only adds the rows the caller explicitly kept.
Rows that only exist on one side are reported. They are not removed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from api.apple_music_catalog import MatchRow, SongIdentity, build_preview


@dataclass(frozen=True)
class SyncConflict:
    kind: str
    song: SongIdentity
    detail: str


@dataclass(frozen=True)
class SyncAddition:
    source_id: str
    playlist_id: str
    song_id: str
    linked_song_id: str = ""
    # Pair order is always NetEase id, then Apple Music id.
    pair_left: str = ""
    pair_right: str = ""


@dataclass
class PlaylistLink:
    left_source: str
    left_playlist_id: str
    right_source: str
    right_playlist_id: str
    pairs: tuple[tuple[str, str], ...] = ()

    def key(self) -> tuple[str, str, str, str]:
        return (self.left_source, self.left_playlist_id, self.right_source, self.right_playlist_id)


@dataclass
class SyncPlan:
    additions: tuple[SyncAddition, ...] = ()
    conflicts: tuple[SyncConflict, ...] = ()
    skipped: tuple[MatchRow, ...] = ()


@dataclass
class SyncResult:
    added: int = 0
    failed: int = 0
    failures: tuple[tuple[str, str], ...] = ()
    link: PlaylistLink | None = None


def rows_to_add(rows: tuple[MatchRow, ...] | list[MatchRow], *, include_uncertain: tuple[str, ...] | list[str] = ()) -> tuple[MatchRow, ...]:
    """Matched rows are included. Uncertain rows stay out unless the user names them."""
    chosen = {str(item) for item in include_uncertain}
    kept = []
    for row in rows:
        if row.state == "matched" and row.target is not None:
            kept.append(row)
        elif row.state == "uncertain" and row.target is not None and row.source.song_id in chosen:
            kept.append(row)
    return tuple(kept)


def additions_for(rows: tuple[MatchRow, ...], *, target_source: str, target_playlist_id: str) -> tuple[SyncAddition, ...]:
    return tuple(
        SyncAddition(target_source, target_playlist_id, row.target.song_id, row.source.song_id)
        for row in rows if row.target is not None
    )


@dataclass
class MemoryLinkStore:
    links: dict = field(default_factory=dict)

    def load_all(self) -> tuple[PlaylistLink, ...]:
        return tuple(self.links.values())

    def find(self, left_source, left_playlist_id, right_source, right_playlist_id) -> PlaylistLink | None:
        return self.links.get((left_source, left_playlist_id, right_source, right_playlist_id))

    def save(self, link: PlaylistLink) -> None:
        self.links[link.key()] = link


class FileLinkStore:
    """Local playlist pairs only. Tokens and cookies do not belong in this file."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load_all(self) -> tuple[PlaylistLink, ...]:
        if not self.path.is_file():
            return ()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ()
        links = []
        for row in payload.get("links", []) if isinstance(payload, dict) else []:
            link = _link_from_json(row)
            if link is not None:
                links.append(link)
        return tuple(links)

    def find(self, left_source, left_playlist_id, right_source, right_playlist_id) -> PlaylistLink | None:
        for link in self.load_all():
            if link.key() == (left_source, left_playlist_id, right_source, right_playlist_id):
                return link
        return None

    def save(self, link: PlaylistLink) -> None:
        current = [item for item in self.load_all() if item.key() != link.key()]
        current.append(link)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {"version": 1, "links": [_link_to_json(item) for item in current]}
        self.path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def _link_to_json(link: PlaylistLink) -> dict:
    return {
        "left_source": link.left_source,
        "left_playlist_id": link.left_playlist_id,
        "right_source": link.right_source,
        "right_playlist_id": link.right_playlist_id,
        "pairs": [{"left": left, "right": right} for left, right in link.pairs],
    }


def _link_from_json(row) -> PlaylistLink | None:
    if not isinstance(row, dict):
        return None
    pairs = []
    for pair in row.get("pairs") or []:
        if isinstance(pair, dict) and pair.get("left") and pair.get("right"):
            pairs.append((str(pair["left"])[:200], str(pair["right"])[:200]))
    try:
        return PlaylistLink(
            str(row["left_source"]), str(row["left_playlist_id"]),
            str(row["right_source"]), str(row["right_playlist_id"]), tuple(pairs),
        )
    except KeyError:
        return None


def plan_incremental(link: PlaylistLink | None, source_songs, target_songs, fresh_rows: tuple[MatchRow, ...], *, source_is_left: bool = True) -> SyncPlan:
    """Second sync adds songs that are not already paired. It never deletes.

    A song that disappeared on one side, or that appeared on both since the
    last pair list, is a conflict for the user. The plan has no delete action.
    Stored pairs are NetEase id, then Apple Music id. source_is_left is false
    when the songs being copied start on the Apple side.
    """
    pairs = link.pairs if link is not None else ()
    known_source = {left if source_is_left else right for left, right in pairs}
    known_target = {right if source_is_left else left for left, right in pairs}
    source_ids = {song.song_id for song in source_songs}
    target_ids = {song.song_id for song in target_songs}
    additions = []
    skipped = []
    for row in fresh_rows:
        if row.source.song_id in known_source:
            skipped.append(row)
            continue
        if row.state != "matched" or row.target is None:
            skipped.append(row)
            continue
        if row.target.song_id in known_target:
            skipped.append(row)
            continue
        additions.append(SyncAddition(
            row.target.source_id, "", row.target.song_id, row.source.song_id,
        ))
    conflicts = []
    source_by_id = {song.song_id: song for song in source_songs}
    target_by_id = {song.song_id: song for song in target_songs}
    for left, right in pairs:
        source_pair = left if source_is_left else right
        target_pair = right if source_is_left else left
        if source_pair not in source_ids and target_pair in target_ids:
            conflicts.append(SyncConflict(
                "missing_on_source", target_by_id[target_pair],
                "这一边的歌单里没有了，另一边还在。不会自动删。",
            ))
        elif target_pair not in target_ids and source_pair in source_ids:
            conflicts.append(SyncConflict(
                "missing_on_target", source_by_id[source_pair],
                "另一边的歌单里没有了，这一边还在。不会自动删。",
            ))
    extra_target = [
        song for song in target_songs
        if song.song_id not in known_target and song.song_id not in {item.song_id for item in additions}
    ]
    for song in extra_target:
        conflicts.append(SyncConflict("added_on_target", song, "另一边多了这首。不会自动删，你可以选择要不要补回来。"))
    return SyncPlan(tuple(additions), tuple(conflicts), tuple(skipped))


def refuse_delete(action: str) -> None:
    if action in {"delete", "remove", "clear"}:
        raise ValueError("同步不会删除任何一边的歌或歌单")


def apply_additions(additions: tuple[SyncAddition, ...], writer, link: PlaylistLink) -> SyncResult:
    """writer(addition) adds one song or raises. A failed add is not retried."""
    added = 0
    failures = []
    pairs = list(link.pairs)
    known = {(left, right) for left, right in pairs}
    for addition in additions:
        refuse_delete("add")
        try:
            writer(addition)
        except Exception as exc:
            reason = getattr(exc, "code", None) or "failed"
            failures.append((addition.song_id, str(reason)[:80]))
            continue
        added += 1
        pair = (
            addition.pair_left or addition.linked_song_id or addition.song_id,
            addition.pair_right or addition.song_id,
        )
        if addition.linked_song_id and pair not in known:
            pairs.append(pair)
            known.add(pair)
    updated = PlaylistLink(
        link.left_source, link.left_playlist_id, link.right_source, link.right_playlist_id, tuple(pairs),
    )
    return SyncResult(added, len(failures), tuple(failures), updated)


def preview_against(songs, finder) -> tuple[MatchRow, ...]:
    return build_preview(songs, finder)
