"""Turn playlist reads into a preview, then add only the rows the user kept.

Nothing in this module removes a song or a playlist. Network stays in the
service the caller passes in.
"""
from __future__ import annotations

from api.apple_music_catalog import SongIdentity, build_preview
from api.apple_music_log import apple_log
from api.apple_music_sync import (
    PlaylistLink,
    SyncAddition,
    SyncPlan,
    additions_for,
    apply_additions,
    plan_incremental,
    rows_to_add,
)


def song_identity(song) -> SongIdentity:
    return SongIdentity(
        getattr(song, "source_id", ""),
        getattr(song, "song_id", ""),
        getattr(song, "title", ""),
        getattr(song, "artist", ""),
        getattr(song, "album", ""),
        float(getattr(song, "duration_seconds", 0) or 0),
    )


def collect_songs(service, source_id: str, playlist_id: str, *, page_limit: int = 20):
    if not playlist_id:
        return []
    songs = []
    seen = set()
    offset = 0
    for _ in range(page_limit):
        page = service.playlist_tracks(source_id, playlist_id, limit=100, offset=offset)
        fresh = [song for song in page.items if song.song_id and song.song_id not in seen]
        if not fresh:
            break
        songs.extend(fresh)
        seen.update(song.song_id for song in fresh)
        if not page.has_more or page.next_offset <= offset:
            break
        offset = page.next_offset
    return songs


def search_candidates(service, target_source: str, song: SongIdentity, limit: int = 5):
    query = f"{song.title} {song.artist}".strip()[:200]
    if not query:
        return ()
    page = service.search(target_source, query, limit=limit)
    return tuple(song_identity(item) for item in page.items if getattr(item, "song_id", "") and getattr(item, "title", ""))


# Reverse copying stays in this module. The window and the controller leave it
# off until NetEase-to-Apple has been checked on a real Windows account.
REVERSE_SYNC_ENABLED = False


def sync_direction_allowed(direction: str) -> bool:
    if direction == "netease_to_apple":
        return True
    return direction == "apple_to_netease" and REVERSE_SYNC_ENABLED


def sides(direction: str) -> tuple[str, str, bool]:
    if direction == "netease_to_apple":
        return "netease", "apple_music", True
    if direction == "apple_to_netease":
        return "apple_music", "netease", False
    raise ValueError("未知同步方向")


def build_import_preview(service, direction: str, source_playlist_id: str, target_playlist_id: str = "", store=None):
    source_id, target_id, source_is_left = sides(direction)
    source_songs = [song_identity(song) for song in collect_songs(service, source_id, source_playlist_id)]
    target_songs = [song_identity(song) for song in collect_songs(service, target_id, target_playlist_id)]
    rows = build_preview(source_songs, lambda song: search_candidates(service, target_id, song))
    netease_id = source_playlist_id if source_is_left else target_playlist_id
    apple_id = target_playlist_id if source_is_left else source_playlist_id
    link = None
    if store is not None and netease_id and apple_id:
        link = store.find("netease", netease_id, "apple_music", apple_id)
    plan = plan_incremental(link, source_songs, target_songs, rows, source_is_left=source_is_left)
    return rows, plan, link


def selected_additions(rows, plan: SyncPlan, include_uncertain) -> tuple[SyncAddition, ...]:
    chosen = rows_to_add(tuple(row for row in rows if row.state == "uncertain"), include_uncertain=include_uncertain)
    extra = additions_for(chosen, target_source="", target_playlist_id="")
    seen = {item.song_id for item in plan.additions}
    kept = list(plan.additions)
    for item in extra:
        if item.song_id not in seen:
            kept.append(item)
            seen.add(item.song_id)
    return tuple(kept)


def orient_additions(additions, direction: str, netease_playlist_id: str, apple_playlist_id: str) -> tuple[SyncAddition, ...]:
    oriented = []
    for item in additions:
        if direction == "netease_to_apple":
            oriented.append(SyncAddition(
                "apple_music", apple_playlist_id, item.song_id, item.linked_song_id,
                item.linked_song_id, item.song_id,
            ))
        else:
            oriented.append(SyncAddition(
                "netease", netease_playlist_id, item.song_id, item.linked_song_id,
                item.song_id, item.linked_song_id,
            ))
    return tuple(oriented)


def execute_import(service, direction: str, netease_playlist_id: str, apple_playlist_id: str,
                   additions, store, *, new_name: str = ""):
    """Create a missing destination playlist, then add. Failed adds are not retried."""
    planned = len(tuple(additions))
    try:
        result = _execute_import(service, direction, netease_playlist_id, apple_playlist_id, additions, store, new_name=new_name)
    except Exception:
        apple_log("write", result="error", count=planned)
        raise
    apple_log("write", result="ok" if result.failed == 0 else "partial", added=result.added, failed=result.failed)
    return result


def _execute_import(service, direction: str, netease_playlist_id: str, apple_playlist_id: str,
                    additions, store, *, new_name: str = ""):
    if not additions:
        link = PlaylistLink("netease", netease_playlist_id, "apple_music", apple_playlist_id, ())
        return apply_additions((), lambda _item: None, link)
    if direction == "netease_to_apple" and not apple_playlist_id:
        apple_playlist_id = service.create_playlist("apple_music", new_name)
    elif direction == "apple_to_netease" and not netease_playlist_id:
        netease_playlist_id = service.create_playlist("netease", new_name)
    oriented = orient_additions(additions, direction, netease_playlist_id, apple_playlist_id)
    link = None
    if store is not None:
        link = store.find("netease", netease_playlist_id, "apple_music", apple_playlist_id)
    if link is None:
        link = PlaylistLink("netease", netease_playlist_id, "apple_music", apple_playlist_id, ())

    def writer(item: SyncAddition) -> None:
        if item.source_id == "netease":
            service.add_song_to_playlist("netease", item.song_id, item.playlist_id)
            return
        service.append_playlist_tracks("apple_music", item.playlist_id, (item.song_id,))

    result = apply_additions(oriented, writer, link)
    if store is not None and result.link is not None:
        store.save(result.link)
    return result


def preview_lines(rows, plan: SyncPlan) -> str:
    matched = sum(1 for row in rows if row.state == "matched")
    uncertain = sum(1 for row in rows if row.state == "uncertain")
    unmatched = sum(1 for row in rows if row.state == "unmatched")
    lines = [
        f"匹配上 {matched} 首，不确定 {uncertain} 首，没匹配上 {unmatched} 首。",
        f"确认后会新加 {len(plan.additions)} 首。不会删除任何歌或歌单。",
    ]
    for row in rows:
        if row.state == "matched" and row.target is not None:
            lines.append(f"匹配：{row.source.title} / {row.source.artist} → {row.target.title} / {row.target.artist}")
        elif row.state == "uncertain":
            target = f"{row.target.title} / {row.target.artist}" if row.target is not None else "没有候选"
            lines.append(f"不确定：{row.source.title} / {row.source.artist} → {target}。{row.reason}")
        else:
            lines.append(f"没匹配：{row.source.title} / {row.source.artist}。{row.reason}")
    if plan.conflicts:
        lines.append("两边都有变动，下面这些不会自动删，请你看过再决定：")
        for conflict in plan.conflicts:
            lines.append(f"冲突：{conflict.song.title} / {conflict.song.artist}。{conflict.detail}")
    if plan.skipped:
        lines.append(f"已同步过、这次跳过 {len(plan.skipped)} 首。")
    return "\n".join(lines)


def result_lines(result) -> str:
    lines = [f"成功 {result.added} 首，失败 {result.failed} 首。"]
    for song_id, reason in result.failures:
        lines.append(f"失败：{song_id}，原因 {reason}")
    if result.failed == 0:
        lines.append("没有失败。")
    return "\n".join(lines)
