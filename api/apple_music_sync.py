"""Playlist sync plans between NetEase and Apple Music.

The user picks one direction per playlist pair: the source playlist is the
authority. Songs added to the source are added to the target; songs removed
from the source are removed from the target (manual mode asks first). Changes
made only on the target side are never acted on without the user's choice.
Apple Music's public interface cannot remove songs, so those are listed.

This module has no network code. The flow passes in writers.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

from api.apple_music_catalog import MatchRow, SongIdentity, build_preview


DIRECTIONS = ("netease_to_apple", "apple_to_netease")
MODES = ("manual", "auto")
# Sides that can remove a song from a playlist through the existing service.
REMOVABLE_SIDES = frozenset({"netease"})


def song_key(song: SongIdentity) -> str:
    """Stable id across reads: Apple library rows carry the catalog id they were added by."""
    if song.source_id == "apple_music" and song.catalog_id:
        return song.catalog_id
    return song.song_id


def sides(direction: str) -> tuple[str, str]:
    if direction == "netease_to_apple":
        return "netease", "apple_music"
    if direction == "apple_to_netease":
        return "apple_music", "netease"
    raise ValueError("未知同步方向")


@dataclass
class PlaylistLink:
    """One NetEase playlist paired with one Apple Music playlist.

    pairs are always (NetEase id, Apple catalog id). ignored holds
    "side:id" entries the user chose to leave alone.
    """

    left_source: str
    left_playlist_id: str
    right_source: str
    right_playlist_id: str
    pairs: tuple[tuple[str, str], ...] = ()
    direction: str = "netease_to_apple"
    mode: str = "manual"
    ignored: tuple[str, ...] = ()

    def key(self) -> tuple[str, str, str, str]:
        return (self.left_source, self.left_playlist_id, self.right_source, self.right_playlist_id)

    def playlist_id(self, side: str) -> str:
        return self.left_playlist_id if side == self.left_source else self.right_playlist_id


@dataclass(frozen=True)
class SyncItem:
    """One line of a plan. source/target follow the chosen direction."""

    kind: str
    source: SongIdentity | None = None
    target: SongIdentity | None = None
    candidates: tuple[SongIdentity, ...] = ()
    reason: str = ""


@dataclass
class SyncPlan:
    direction: str = "netease_to_apple"
    additions: tuple[SyncItem, ...] = ()      # source added → add to target
    pair_only: tuple[SyncItem, ...] = ()      # already in the target, just remember the pair
    removals: tuple[SyncItem, ...] = ()       # source removed → remove from target
    cannot_remove: tuple[SyncItem, ...] = ()  # source removed, but the target side cannot remove
    choices: tuple[SyncItem, ...] = ()        # several or uncertain candidates: the user picks or skips
    unmatched: tuple[SyncItem, ...] = ()      # nothing found on the target side
    target_added: tuple[SyncItem, ...] = ()   # only on the target side: keep or remove, user decides
    target_removed: tuple[SyncItem, ...] = () # removed on the target side: add back or accept, user decides
    dropped_pairs: tuple[tuple[str, str], ...] = ()
    in_sync: int = 0
    skipped: int = 0
    target_keys: frozenset = frozenset()
    source_pairs: tuple = ()   # remembered pairs whose source song is still in the source playlist

    def needs_user(self) -> int:
        return len(self.choices) + len(self.unmatched) + len(self.target_added) + len(self.target_removed)


@dataclass(frozen=True)
class SyncDecisions:
    picks: dict = field(default_factory=dict)            # source key -> chosen target SongIdentity
    skip: frozenset = frozenset()                        # source keys to stop asking about
    confirm_removals: bool = False
    target_added: dict = field(default_factory=dict)     # target key -> "keep" | "remove"
    target_removed: dict = field(default_factory=dict)   # source key -> "readd" | "accept"
    # Apple Music cannot remove songs: rebuild=True writes a new playlist from the
    # source instead; the old one is left for the user to delete.
    rebuild: bool = False


@dataclass
class SyncResult:
    added: int = 0
    removed: int = 0
    failed: int = 0
    failures: tuple[tuple[str, str], ...] = ()
    cannot_remove: tuple[SongIdentity, ...] = ()
    pending: int = 0
    link: PlaylistLink | None = None
    added_keys: tuple[str, ...] = ()   # target keys the writer accepted, to check against the playlist


def verify_added(result: SyncResult, present_keys, plan_direction: str) -> SyncResult:
    """Keep only additions that really are in the target playlist now.

    A write the service called a success but whose song is not in the
    playlist becomes a failure, and its pair is forgotten so it is offered again.
    """
    missing = tuple(key for key in result.added_keys if key not in set(present_keys))
    if not missing or result.link is None:
        return result
    source_side, _target = sides(plan_direction)
    gone = set(missing)
    pairs = tuple(pair for pair in result.link.pairs
                  if (pair[1] if source_side == "netease" else pair[0]) not in gone)
    failures = result.failures + tuple((key, "not_in_playlist") for key in missing)
    return replace(result, added=result.added - len(missing), failed=result.failed + len(missing), failures=failures,
                   link=replace(result.link, pairs=pairs),
                   added_keys=tuple(key for key in result.added_keys if key not in gone))


def _ignored_key(side: str, key: str) -> str:
    return f"{side}:{key}"


def plan_sync(link: PlaylistLink | None, direction: str, source_songs, target_songs,
              rows: tuple[MatchRow, ...], candidates: dict | None = None) -> SyncPlan:
    """Compare both playlists against the remembered pairs. Nothing is written here."""
    source_side, target_side = sides(direction)
    source_is_left = source_side == "netease"
    candidates = candidates or {}
    pairs = link.pairs if link is not None else ()
    ignored = set(link.ignored) if link is not None else set()
    source_by_key = {song_key(song): song for song in source_songs}
    target_by_key = {song_key(song): song for song in target_songs}

    def split(pair):
        left, right = pair
        return (left, right) if source_is_left else (right, left)

    removals, cannot, target_removed, dropped = [], [], [], []
    in_sync = 0
    live_sources_for_target: dict[str, int] = {}
    for pair in pairs:
        s_key, t_key = split(pair)
        if s_key in source_by_key:
            live_sources_for_target[t_key] = live_sources_for_target.get(t_key, 0) + 1
    seen_removal = set()
    for pair in pairs:
        s_key, t_key = split(pair)
        s_here, t_here = s_key in source_by_key, t_key in target_by_key
        if s_here and t_here:
            in_sync += 1
        elif t_here:
            # The source dropped this song. Another source version may still hold the target.
            if live_sources_for_target.get(t_key) or t_key in seen_removal:
                dropped.append(pair)
                continue
            seen_removal.add(t_key)
            item = SyncItem("remove", None, target_by_key[t_key], reason="来源歌单里删掉了")
            if target_side in REMOVABLE_SIDES:
                removals.append(item)
            else:
                cannot.append(replace(item, kind="cannot_remove", reason="Apple Music 不允许第三方从歌单里删歌，请在 Apple Music 里手动删"))
        elif s_here:
            target_removed.append(SyncItem("target_removed", source_by_key[s_key], None, reason="目标歌单里被删了"))
        else:
            dropped.append(pair)

    paired_source = {split(pair)[0] for pair in pairs}
    paired_target = {split(pair)[1] for pair in pairs}
    additions, pair_only, choices, unmatched = [], [], [], []
    claimed_target = set(paired_target)
    planned_target = set()
    skipped = 0
    for row in rows:
        s_key = song_key(row.source)
        if s_key in paired_source:
            continue
        if _ignored_key(source_side, s_key) in ignored:
            skipped += 1
            continue
        found = tuple(candidates.get(s_key, ()))
        if row.state == "matched" and row.target is not None:
            t_key = song_key(row.target)
            target = target_by_key.get(t_key, row.target)
            if t_key in target_by_key or t_key in planned_target or t_key in paired_target:
                pair_only.append(SyncItem("pair_only", row.source, target, reason="目标歌单里已经有这首"))
            else:
                additions.append(SyncItem("add", row.source, row.target, reason=row.reason))
                planned_target.add(t_key)
            claimed_target.add(t_key)
        elif row.state == "uncertain" and (found or row.target is not None):
            options = found or (row.target,)
            choices.append(SyncItem("choose", row.source, row.target, options, row.reason))
        else:
            unmatched.append(SyncItem("unmatched", row.source, None, found, row.reason))

    target_added = []
    for t_key, song in target_by_key.items():
        if t_key in claimed_target or _ignored_key(target_side, t_key) in ignored:
            continue
        reason = "只在目标歌单里有" if target_side in REMOVABLE_SIDES else "只在 Apple Music 歌单里有，GluSpot 删不掉它"
        target_added.append(SyncItem("target_added", None, song, reason=reason))
    return SyncPlan(
        direction, tuple(additions), tuple(pair_only), tuple(removals), tuple(cannot), tuple(choices),
        tuple(unmatched), tuple(target_added), tuple(target_removed), tuple(dropped), in_sync, skipped,
        frozenset(target_by_key),
        tuple(pair for pair in pairs if split(pair)[0] in source_by_key),
    )


def apply_audio_verdicts(plan: SyncPlan, verdicts: dict) -> SyncPlan:
    """A choice whose candidate sounds the same is decided: it becomes an addition.

    verdicts maps source key -> {candidate key -> verdict with .same}. Other
    choices stay for the user.
    """
    if not verdicts or not plan.choices:
        return plan
    additions, pair_only, choices = list(plan.additions), list(plan.pair_only), []
    planned = {song_key(item.target) for item in additions}
    for item in plan.choices:
        found = verdicts.get(song_key(item.source)) or {}
        chosen = next((candidate for candidate in item.candidates
                       if getattr(found.get(song_key(candidate)), "same", False)), None)
        if chosen is None:
            choices.append(item)
            continue
        t_key = song_key(chosen)
        if t_key in plan.target_keys or t_key in planned:
            pair_only.append(SyncItem("pair_only", item.source, chosen, reason="音轨一致，目标歌单里已经有这首"))
        else:
            additions.append(SyncItem("add", item.source, chosen, reason="音轨一致，自动选中"))
        planned.add(t_key)
    target_added = tuple(item for item in plan.target_added if song_key(item.target) not in planned)
    return replace(plan, additions=tuple(additions), pair_only=tuple(pair_only), choices=tuple(choices),
                   target_added=target_added)


def apply_rebuild(plan: SyncPlan, decisions: SyncDecisions, link: PlaylistLink, *, add_writer,
                  chunk: int = 1) -> SyncResult:
    """Fill a brand-new target playlist with every source song that has a match.

    link already names the new playlist. Songs removed from the source are simply
    not written; the old playlist is not touched. Decided choices are included,
    skipped ones are remembered as before.
    """
    source_side, _target_side = sides(plan.direction)
    source_is_left = source_side == "netease"

    def make_pair(s_key, t_key):
        return (s_key, t_key) if source_is_left else (t_key, s_key)

    ordered: list[tuple[str, str]] = []
    for pair in plan.source_pairs:
        s_key, t_key = (pair if source_is_left else (pair[1], pair[0]))
        ordered.append((s_key, t_key))
    for item in plan.pair_only + plan.additions:
        ordered.append((song_key(item.source), song_key(item.target)))
    ignored = list(link.ignored)
    for item in plan.choices + plan.unmatched:
        s_key = song_key(item.source)
        chosen = decisions.picks.get(s_key)
        if chosen is not None:
            ordered.append((s_key, song_key(chosen)))
        elif s_key in decisions.skip and f"{source_side}:{s_key}" not in ignored:
            ignored.append(f"{source_side}:{s_key}")
    pairs, written, failures = [], set(), []
    queue = []
    for s_key, t_key in ordered:
        pair = make_pair(s_key, t_key)
        if pair not in pairs:
            pairs.append(pair)
        if t_key not in written:
            written.add(t_key)
            queue.append(t_key)
    added = 0
    landed = set()
    written_keys: list[str] = []
    for batch in _chunks(queue, chunk):
        try:
            add_writer(tuple(batch))
        except Exception as exc:  # noqa: BLE001 - reported, not retried
            failures.extend((t_key, _reason(exc)) for t_key in batch)
            continue
        added += len(batch)
        landed.update(batch)
        written_keys.extend(batch)
    kept = tuple(pair for pair in pairs if (pair[1] if source_is_left else pair[0]) in landed)
    updated = replace(link, pairs=kept, ignored=tuple(ignored), direction=plan.direction)
    decided = set(decisions.picks) | set(decisions.skip)
    pending = sum(1 for item in plan.choices + plan.unmatched if song_key(item.source) not in decided)
    return SyncResult(added, 0, len(failures), tuple(failures), (), pending, updated, tuple(written_keys))


def auto_decisions() -> SyncDecisions:
    """Auto mode follows the source both ways. Target-side changes still wait for the user."""
    return SyncDecisions(confirm_removals=True)


def apply_sync(plan: SyncPlan, decisions: SyncDecisions, link: PlaylistLink, *,
               add_writer, remove_writer, chunk: int = 1) -> SyncResult:
    """Write the decided changes. add_writer(ids) / remove_writer(ids) raise on failure.

    A failed write is not retried here and its pair is not recorded, so the
    next preview offers it again.
    """
    source_side, target_side = sides(plan.direction)
    source_is_left = source_side == "netease"
    pairs = [pair for pair in link.pairs if pair not in set(plan.dropped_pairs)]
    ignored = list(link.ignored)

    def make_pair(s_key, t_key):
        return (s_key, t_key) if source_is_left else (t_key, s_key)

    def remember(pair):
        if pair not in pairs:
            pairs.append(pair)

    def ignore(side, key):
        entry = _ignored_key(side, key)
        if entry not in ignored:
            ignored.append(entry)

    failures: list[tuple[str, str]] = []
    adds: list[tuple[str, str]] = []  # (source key, target key)
    for item in plan.additions:
        adds.append((song_key(item.source), song_key(item.target)))
    for item in plan.pair_only:
        remember(make_pair(song_key(item.source), song_key(item.target)))
    for item in plan.choices + plan.unmatched:
        s_key = song_key(item.source)
        chosen = decisions.picks.get(s_key)
        if chosen is not None:
            adds.append((s_key, song_key(chosen)))
        elif s_key in decisions.skip:
            ignore(source_side, s_key)
    present_target = set(plan.target_keys)
    for item in plan.target_removed:
        s_key = song_key(item.source)
        choice = decisions.target_removed.get(s_key)
        old = [pair for pair in pairs if (pair[0] if source_is_left else pair[1]) == s_key]
        if choice == "readd" and old:
            t_key = old[0][1] if source_is_left else old[0][0]
            adds.append((s_key, t_key))
        elif choice == "accept":
            for pair in old:
                pairs.remove(pair)
            ignore(source_side, s_key)

    to_add: list[tuple[str, str]] = []
    queued = set()
    for s_key, t_key in adds:
        if t_key in queued:
            remember(make_pair(s_key, t_key))
            continue
        queued.add(t_key)
        if t_key in present_target:
            remember(make_pair(s_key, t_key))
            continue
        to_add.append((s_key, t_key))

    added = 0
    written_keys: list[str] = []
    for batch in _chunks(to_add, chunk):
        try:
            add_writer(tuple(t_key for _s, t_key in batch))
        except Exception as exc:  # noqa: BLE001 - every failure is reported, none retried
            failures.extend((t_key, _reason(exc)) for _s, t_key in batch)
            continue
        added += len(batch)
        for s_key, t_key in batch:
            remember(make_pair(s_key, t_key))
            written_keys.append(t_key)

    to_remove: list[str] = []
    if decisions.confirm_removals:
        to_remove.extend(song_key(item.target) for item in plan.removals)
    for item in plan.target_added:
        t_key = song_key(item.target)
        choice = decisions.target_added.get(t_key)
        paired_now = {pair[1] if source_is_left else pair[0] for pair in pairs}
        if choice == "remove" and target_side in REMOVABLE_SIDES and t_key not in paired_now:
            to_remove.append(t_key)
        elif choice == "keep":
            ignore(target_side, t_key)
    removed = 0
    if to_remove and target_side not in REMOVABLE_SIDES:
        raise ValueError("Apple Music 歌单不能由 GluSpot 删歌")
    for batch in _chunks(list(dict.fromkeys(to_remove)), chunk):
        try:
            remove_writer(tuple(batch))
        except Exception as exc:  # noqa: BLE001
            failures.extend((t_key, _reason(exc)) for t_key in batch)
            continue
        removed += len(batch)
        gone = set(batch)
        pairs = [pair for pair in pairs if (pair[1] if source_is_left else pair[0]) not in gone]

    updated = replace(link, pairs=tuple(pairs), ignored=tuple(ignored), direction=plan.direction)
    decided = set(decisions.picks) | set(decisions.skip)
    pending = sum(1 for item in plan.choices + plan.unmatched if song_key(item.source) not in decided)
    pending += sum(1 for item in plan.target_added if song_key(item.target) not in decisions.target_added)
    pending += sum(1 for item in plan.target_removed if song_key(item.source) not in decisions.target_removed)
    if not decisions.confirm_removals:
        pending += len(plan.removals)
    return SyncResult(added, removed, len(failures), tuple(failures),
                      tuple(item.target for item in plan.cannot_remove), pending, updated, tuple(written_keys))


def _chunks(items, size):
    size = max(1, int(size))
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _reason(exc) -> str:
    return str(getattr(exc, "code", None) or "failed")[:80]


@dataclass
class MemoryLinkStore:
    links: dict = field(default_factory=dict)

    def load_all(self) -> tuple[PlaylistLink, ...]:
        return tuple(self.links.values())

    def find(self, left_source, left_playlist_id, right_source, right_playlist_id) -> PlaylistLink | None:
        return self.links.get((left_source, left_playlist_id, right_source, right_playlist_id))

    def save(self, link: PlaylistLink) -> None:
        self.links[link.key()] = link


_STORE_LOCK = threading.RLock()


class FileLinkStore:
    """Local playlist pairs only. Tokens and cookies do not belong in this file."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load_all(self) -> tuple[PlaylistLink, ...]:
        with _STORE_LOCK:
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
        with _STORE_LOCK:
            current = [item for item in self.load_all() if item.key() != link.key()]
            current.append(link)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            body = {"version": 2, "links": [_link_to_json(item) for item in current]}
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)


def find_for_playlist(store, side: str, playlist_id: str) -> PlaylistLink | None:
    """The most recently saved pair that uses this playlist on this side."""
    if store is None or not playlist_id:
        return None
    found = None
    for link in store.load_all():
        if link.playlist_id(side) == playlist_id and side in (link.left_source, link.right_source):
            found = link
    return found


def _link_to_json(link: PlaylistLink) -> dict:
    return {
        "left_source": link.left_source,
        "left_playlist_id": link.left_playlist_id,
        "right_source": link.right_source,
        "right_playlist_id": link.right_playlist_id,
        "direction": link.direction,
        "mode": link.mode,
        "pairs": [{"left": left, "right": right} for left, right in link.pairs],
        "ignored": list(link.ignored),
    }


def _link_from_json(row) -> PlaylistLink | None:
    if not isinstance(row, dict):
        return None
    pairs = []
    for pair in row.get("pairs") or []:
        if isinstance(pair, dict) and pair.get("left") and pair.get("right"):
            pairs.append((str(pair["left"])[:200], str(pair["right"])[:200]))
    ignored = tuple(str(item)[:240] for item in row.get("ignored") or [] if isinstance(item, str) and ":" in item)
    direction = row.get("direction") if row.get("direction") in DIRECTIONS else "netease_to_apple"
    mode = row.get("mode") if row.get("mode") in MODES else "manual"
    try:
        return PlaylistLink(
            str(row["left_source"]), str(row["left_playlist_id"]),
            str(row["right_source"]), str(row["right_playlist_id"]), tuple(pairs),
            direction, mode, ignored,
        )
    except KeyError:
        return None


def preview_against(songs, finder) -> tuple[MatchRow, ...]:
    return build_preview(songs, finder)
