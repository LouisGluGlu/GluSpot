"""Bounded presentation snapshots inside the existing protected account envelope.

These records are never authentication authority. No new plaintext account cache
or credential location is introduced.
"""
from __future__ import annotations

from api.online_music import AccountSnapshot, CatalogPage, OnlinePlaylist

KEY = "_gluspot_resume"
MAX_AGE = 30 * 24 * 3600


def read_resume(credential, source, now):
    data = credential.get(KEY) if isinstance(credential, dict) else None
    if not isinstance(data, dict) or data.get("version") != 1 or data.get("source") != source:
        return None
    stamp = data.get("saved_at")
    if not isinstance(stamp, (int, float)) or not 0 <= now - stamp <= MAX_AGE:
        return None
    profile = data.get("profile")
    if not isinstance(profile, dict) or not isinstance(profile.get("user_id"), str) or not profile["user_id"]:
        return None
    if any(not isinstance(profile.get(key, ""), str) for key in ("nickname", "avatar_url")):
        return None
    return data


def cached_account(data, source):
    if data is None:
        return AccountSnapshot(source)
    profile = data["profile"]
    return AccountSnapshot(source, False, profile["user_id"][:128], profile.get("nickname", "")[:256],
                           profile.get("avatar_url", ""), "cached", "正在核验登录，当前显示本地快照")


def attach_profile(credential, account, previous, now):
    result = dict(credential)
    old = read_resume(previous, account.source_id, now)
    data = dict(old) if old and old["profile"]["user_id"] == account.user_id else {}
    data.update(version=1, source=account.source_id, saved_at=now,
                profile={"user_id": account.user_id, "nickname": account.nickname, "avatar_url": account.avatar_url})
    result[KEY] = data
    return result


def read_catalog(data, source, section):
    page = data.get(section) if data else None
    if not isinstance(page, dict) or not isinstance(page.get("items"), list):
        return CatalogPage(())
    items = []
    for row in page["items"][:100]:
        if (not isinstance(row, dict) or any(not isinstance(row.get(key), str)
                for key in ("id", "title", "artwork")) or not row["id"] or not row["title"]):
            continue
        count = row.get("count", 0)
        items.append(OnlinePlaylist(source, row["id"][:128], row["title"][:512], row["artwork"][:4096],
                                    count if isinstance(count, int) and 0 <= count <= 1000000 else 0))
    offset = page.get("next_offset", len(items))
    offset = offset if isinstance(offset, int) and 0 <= offset <= 100000 else len(items)
    return CatalogPage(tuple(items), offset, bool(page.get("has_more")))


def attach_catalog(credential, section, page):
    result = dict(credential)
    data = dict(result.get(KEY, {}))
    if not data:
        return result
    data[section] = {"items": [{"id": row.playlist_id[:128], "title": row.title[:512],
                               "artwork": row.artwork_url[:4096], "count": row.track_count}
                              for row in page.items[:100] if isinstance(row, OnlinePlaylist)],
                     "next_offset": page.next_offset, "has_more": page.has_more}
    result[KEY] = data
    return result
