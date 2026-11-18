"""Read two playlists, build a sync plan, then write what the user decided.

Network stays in the service the caller passes in. Both directions are open:
the chosen source playlist is the authority (see api/apple_music_sync.py).
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from api.apple_music_catalog import SongIdentity, build_preview, rank_candidates
from api.apple_music_log import apple_log
from api.apple_music_sync import (
    DIRECTIONS,
    PlaylistLink,
    SyncDecisions,
    SyncPlan,
    SyncResult,
    apply_rebuild,
    apply_sync,
    verify_added,
    auto_decisions,
    plan_sync,
    sides,
    song_key,
)
from api.online_music import OnlineSong

PAGE_SIZE = 100
PAGE_LIMIT = 100  # 10,000 songs; a longer playlist is reported, never silently cut
APPLE_WRITE_CHUNK = 25


def song_identity(song) -> SongIdentity:
    return SongIdentity(
        getattr(song, "source_id", ""),
        getattr(song, "song_id", ""),
        getattr(song, "title", ""),
        getattr(song, "artist", ""),
        getattr(song, "album", ""),
        float(getattr(song, "duration_seconds", 0) or 0),
        getattr(song, "artwork_url", "") or "",
        getattr(song, "catalog_id", "") or "",
        getattr(song, "preview_url", "") or "",
    )


def online_song(song: SongIdentity) -> OnlineSong:
    """The playable song behind a preview row, for listening before choosing."""
    return OnlineSong(song.source_id, song.song_id, song.title, song.artist, song.album,
                      song.duration_seconds, song.artwork_url, catalog_id=song.catalog_id,
                      preview_url=song.preview_url)


def collect_songs(service, source_id: str, playlist_id: str, *, page_limit: int = PAGE_LIMIT):
    """Every song of one playlist. Returns (songs, truncated)."""
    if not playlist_id:
        return [], False
    songs = []
    seen = set()
    offset = 0
    for _ in range(page_limit):
        page = service.playlist_tracks(source_id, playlist_id, limit=PAGE_SIZE, offset=offset)
        fresh = [song for song in page.items if song.song_id and song.song_id not in seen]
        if not fresh:
            return songs, False
        songs.extend(fresh)
        seen.update(song.song_id for song in fresh)
        if not page.has_more or page.next_offset <= offset:
            return songs, False
        offset = page.next_offset
    return songs, True


def search_candidates(service, target_source: str, song: SongIdentity, limit: int = 8):
    query = f"{song.title} {song.artist}".strip()[:200]
    if not query:
        return ()
    page = service.search(target_source, query, limit=limit)
    return tuple(song_identity(item) for item in page.items if getattr(item, "song_id", "") and getattr(item, "title", ""))


def sync_direction_allowed(direction: str) -> bool:
    return direction in DIRECTIONS


def playlist_ids(direction: str, source_playlist_id: str, target_playlist_id: str) -> tuple[str, str]:
    """(NetEase playlist id, Apple playlist id) for either direction."""
    if direction == "netease_to_apple":
        return source_playlist_id, target_playlist_id
    return target_playlist_id, source_playlist_id


def link_for(store, direction: str, source_playlist_id: str, target_playlist_id: str) -> PlaylistLink:
    netease_id, apple_id = playlist_ids(direction, source_playlist_id, target_playlist_id)
    found = store.find("netease", netease_id, "apple_music", apple_id) if store is not None and netease_id and apple_id else None
    return found if found is not None else PlaylistLink("netease", netease_id, "apple_music", apple_id, direction=direction)


@dataclass
class SyncPreview:
    direction: str
    source_playlist_id: str
    target_playlist_id: str
    plan: SyncPlan
    link: PlaylistLink
    rows: tuple = ()
    truncated: bool = False


def find_candidates(service, target_source: str, song: SongIdentity):
    """Search "title artist"; when that finds nothing (an artist written differently), the title alone."""
    found = search_candidates(service, target_source, song)
    if not found and song.artist:
        title_only = SongIdentity(song.source_id, song.song_id, song.title)
        found = search_candidates(service, target_source, title_only)
    return found


def build_sync_preview(service, direction: str, source_playlist_id: str, target_playlist_id: str = "", store=None,
                       progress=None) -> SyncPreview:
    """Read both playlists and search the target catalog. Nothing is written.

    progress(done, total, song) is called after each source song, from this thread.
    """
    source_id, target_id = sides(direction)
    raw_source, cut_source = collect_songs(service, source_id, source_playlist_id)
    raw_target, cut_target = collect_songs(service, target_id, target_playlist_id)
    source_songs = [song_identity(song) for song in raw_source]
    target_songs = [song_identity(song) for song in raw_target]
    link = link_for(store, direction, source_playlist_id, target_playlist_id)
    paired = {pair[0] if source_id == "netease" else pair[1] for pair in link.pairs}
    paired.update(entry.split(":", 1)[1] for entry in link.ignored if entry.startswith(source_id + ":"))
    found: dict[str, tuple[SongIdentity, ...]] = {}
    total = len(source_songs)
    done = [0]
    if callable(progress):
        progress(0, total, None)

    def finder(song: SongIdentity):
        try:
            if song_key(song) in paired:
                return ()  # already paired: no search needed
            result = find_candidates(service, target_id, song)
            found[song_key(song)] = rank_candidates(song, result)
            return result
        finally:
            done[0] += 1
            if callable(progress):
                progress(done[0], total, song)

    rows = build_preview(source_songs, finder)
    plan = plan_sync(link, direction, source_songs, target_songs, rows, found)
    return SyncPreview(direction, source_playlist_id, target_playlist_id, plan, link, rows, cut_source or cut_target)


def execute_sync(service, preview: SyncPreview, decisions: SyncDecisions, store, *, new_name: str = "",
                 mode: str | None = None) -> SyncResult:
    """Create a missing target playlist, then write. Failed writes are not retried."""
    try:
        result = _execute_sync(service, preview, decisions, store, new_name=new_name, mode=mode)
    except Exception as error:
        apple_log("write", result="error", count=len(preview.plan.additions),
                  reason=getattr(error, "code", "") or type(error).__name__[:32])
        raise
    apple_log("write", result="ok" if result.failed == 0 else "partial",
              added=result.added, removed=result.removed, failed=result.failed)
    return result


def _execute_sync(service, preview: SyncPreview, decisions: SyncDecisions, store, *, new_name: str = "",
                  mode: str | None = None) -> SyncResult:
    _source_id, target_id = sides(preview.direction)
    if decisions.rebuild:
        return _rebuild(service, preview, decisions, store, new_name=new_name, mode=mode)
    target_playlist_id = preview.target_playlist_id
    link = preview.link
    has_writes = bool(preview.plan.additions or decisions.picks
                      or any(choice == "readd" for choice in decisions.target_removed.values()))
    if not target_playlist_id:
        if not has_writes:
            return SyncResult(pending=preview.plan.needs_user(), link=None)
        target_playlist_id = service.create_playlist(target_id, new_name or "GluSpot 同步")
        netease_id, apple_id = playlist_ids(preview.direction, preview.source_playlist_id, target_playlist_id)
        link = replace(link, left_playlist_id=netease_id, right_playlist_id=apple_id)
    if mode is not None:
        link = replace(link, mode=mode)

    def add_writer(ids):
        service.append_playlist_tracks(target_id, target_playlist_id, ids)

    def remove_writer(ids):
        service.remove_playlist_tracks(target_id, target_playlist_id, ids)

    chunk = APPLE_WRITE_CHUNK if target_id == "apple_music" else 1
    result = apply_sync(preview.plan, decisions, link, add_writer=add_writer, remove_writer=remove_writer, chunk=chunk)
    result = _check_landed(service, target_id, target_playlist_id, result, preview.direction)
    if store is not None and result.link is not None:
        store.save(result.link)
    return result


def _rebuild(service, preview: SyncPreview, decisions: SyncDecisions, store, *, new_name: str, mode: str | None):
    """A new target playlist, filled from the source. The old playlist is left as it is."""
    _source_id, target_id = sides(preview.direction)
    playlist_id = service.create_playlist(target_id, (new_name or "GluSpot 同步")[:40])
    netease_id, apple_id = playlist_ids(preview.direction, preview.source_playlist_id, playlist_id)
    link = replace(preview.link, left_playlist_id=netease_id, right_playlist_id=apple_id,
                   mode=mode or preview.link.mode)

    def add_writer(ids):
        service.append_playlist_tracks(target_id, playlist_id, ids)

    chunk = APPLE_WRITE_CHUNK if target_id == "apple_music" else 1
    result = apply_rebuild(preview.plan, decisions, link, add_writer=add_writer, chunk=chunk)
    result = _check_landed(service, target_id, playlist_id, result, preview.direction)
    if store is not None and result.link is not None:
        store.save(result.link)
    return result


VERIFY_WAITS = (1.5, 4.0)   # Apple's library can take a moment to show new songs


def _check_landed(service, target_id: str, playlist_id: str, result: SyncResult, direction: str,
                  waits=None, sleep=None) -> SyncResult:
    """Read the target playlist back: a song is synced only if it is really there."""
    import time
    if not result.added_keys:
        return result
    waits = VERIFY_WAITS if waits is None else waits
    sleep = sleep or time.sleep
    present: set[str] = set()
    for wait in waits:
        sleep(wait)
        try:
            songs, _cut = collect_songs(service, target_id, playlist_id)
        except Exception:  # noqa: BLE001 - an unreadable playlist proves nothing either way
            apple_log("write", stage="verify", result="unreadable")
            return result
        present = {song_key(song_identity(song)) for song in songs}
        if all(key in present for key in result.added_keys):
            break
    checked = verify_added(result, present, direction)
    apple_log("write", stage="verify", result="ok" if checked.failed == result.failed else "missing",
              added=checked.added, failed=checked.failed)
    return checked


def save_mode(store, direction: str, source_playlist_id: str, target_playlist_id: str, mode: str) -> PlaylistLink | None:
    """Remember manual or auto for an existing pair without syncing."""
    if store is None or not source_playlist_id or not target_playlist_id:
        return None
    link = replace(link_for(store, direction, source_playlist_id, target_playlist_id), direction=direction, mode=mode)
    store.save(link)
    return link


def run_auto_sync(service, store) -> tuple[tuple[PlaylistLink, SyncResult | None], ...]:
    """Every pair set to auto, in its saved direction. Target-side changes wait for the user."""
    if store is None:
        return ()
    done = []
    for link in store.load_all():
        if link.mode != "auto" or link.direction not in DIRECTIONS:
            continue
        source_side, target_side = sides(link.direction)
        source_playlist = link.playlist_id(source_side)
        target_playlist = link.playlist_id(target_side)
        if not source_playlist or not target_playlist:
            continue
        try:
            preview = build_sync_preview(service, link.direction, source_playlist, target_playlist, store)
            if preview.truncated:
                apple_log("write", stage="auto", result="truncated")
                done.append((link, None))
                continue
            result = execute_sync(service, preview, auto_decisions(), store)
        except Exception:  # noqa: BLE001 - one broken pair must not stop the others
            apple_log("write", stage="auto", result="error")
            done.append((link, None))
            continue
        done.append((link, result))
    return tuple(done)


def auto_summary(done) -> str:
    added = sum(result.added for _link, result in done if result is not None)
    removed = sum(result.removed for _link, result in done if result is not None)
    pending = sum(result.pending + len(result.cannot_remove) for _link, result in done if result is not None)
    failed = sum(1 for _link, result in done if result is None) + sum(result.failed for _link, result in done if result is not None)
    if not (added or removed or pending or failed):
        return ""
    parts = []
    if added or removed:
        parts.append(f"歌单自动同步：加了 {added} 首，删了 {removed} 首")
    if pending:
        parts.append(f"{pending} 首等你在歌单同步里确认")
    if failed:
        parts.append(f"{failed} 处没完成")
    return "，".join(parts) + "。"


def preview_summary(preview: SyncPreview) -> str:
    plan = preview.plan
    lines = [
        f"会新增 {len(plan.additions)} 首，"
        f"会删除 {len(plan.removals)} 首，"
        f"已经一致 {plan.in_sync + len(plan.pair_only)} 首。",
    ]
    if plan.needs_user():
        lines.append(f"有 {plan.needs_user()} 首需要你决定。")
    if plan.cannot_remove:
        lines.append(f"{len(plan.cannot_remove)} 首在来源里删了，但 Apple Music 不让 GluSpot 删，需要你自己去删。")
    if plan.skipped:
        lines.append(f"你以前选过跳过的 {plan.skipped} 首，这次也不管。")
    if preview.truncated:
        lines.append("歌单超过 10000 首，只读了前面的部分，自动同步不会处理这对歌单。")
    return "\n".join(lines)


def result_lines(result: SyncResult) -> str:
    lines = [f"加了 {result.added} 首，删了 {result.removed} 首，失败 {result.failed} 首。"]
    unconfirmed = [song_id for song_id, reason in result.failures if reason == "not_in_playlist"]
    if unconfirmed:
        lines.append(f"有 {len(unconfirmed)} 首写入时说成功，但回读歌单时不在里面，这次不算同步，下次会再试。")
    for song_id, reason in result.failures:
        if reason != "not_in_playlist":
            lines.append(f"失败：{song_id}，原因 {reason}")
    if result.cannot_remove:
        lines.append("这些歌在来源里删了，Apple Music 不允许第三方删歌，请在 Apple Music 里手动删：")
        for song in result.cannot_remove:
            lines.append(f"· {song.title} / {song.artist}")
    if result.pending:
        lines.append(f"还有 {result.pending} 首没做决定，下次同步会再问你。")
    return "\n".join(lines)
