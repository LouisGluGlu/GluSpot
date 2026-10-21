# Current-song collection contracts

Date: 2026-10-16. GluSpot version remains 6.412. This records service and
controller evidence, independently of the final player-capsule visual review.

## Verified platform scope

The optional runtime remains pinned by `native/online_music/package-lock.json`.
The following exact NetEase 4.41.1 modules were read before implementation:

| User action | Pinned module and contract |
| --- | --- |
| Read heart state | `likelist.js`: `/api/song/like/get`, `uid`, successful `code:200` and an `ids` array. |
| Like or unlike | `like.js`: `/api/radio/like`, `id`; unlike must pass the string `"false"`, because the pinned module compares that string. Only successful `code:200` confirms the requested target. |
| Read destination lists | `user_playlist.js`: `/api/user/playlist`, `uid`, bounded `limit` and `offset`. Only entries with `creator.userId` matching the current account and no true subscribed flag are offered. The cursor consumes all original rows, including filtered subscribed lists. |
| Add current song | `playlist_detail.js` checks the selected list's exact ID and creator immediately before the write; `playlist_tracks.js` performs `op:"add"`, `pid`, and one `tracks` string. The module's successful result may wrap the request body once, which the bridge checks explicitly. |
| Bind account | `login_status.js` reads `/api/w/nuser/account/get`; the bridge compares the returned account with the controller's expected user ID before collection requests. |

QQ 3.1.4 provides `getUserLikedSongs` and owned-list reads but no verified write
method in its pinned service or routes. Kugou 1.6.0's write module requires
private `listid`, title/hash/album/mixsong fields, and its delete module requires
private `fileid`; these are different from the public global collection ID and
song hash intentionally retained by the present models. Both adapters therefore
return an explicit unsupported collection state. No endpoint, server success,
or local substitute favorite was invented. Existing search and playback remain
available for those adapters.

## Public service and controller

The existing `OnlineMusicService` now has worker-thread methods:

```python
favorite_status(source_id, song_id, *, expected_user_id="") -> bool
set_favorite(source_id, song_id, liked, *, expected_user_id="") -> bool
writable_playlists(source_id, limit=100, offset=0, *, expected_user_id="") -> CatalogPage
add_song_to_playlist(source_id, song_id, playlist_id, *, expected_user_id="") -> None
```

`SongCollectionController` reuses `MusicTasks`; painting reads only its immutable
`SongCollectionState` via `context(track_id)`. It resolves the visible ordinary
song by exact `owned:source:song` identity, including when a DJ speech segment is
the current decoder item. Queue position alone is never favorite authority.

Song changes, logout, account replacement and same-user re-login fence queued
actions and late UI replies. The controller retains the immutable cached account
object while its identity is used, preventing object-ID reuse. The service also
checks its own account generation before and after a request; stale credentials
cannot be repersisted by a late response. Playlist add requires a destination
from the displayed account's loaded lists, and the bridge checks ownership again.

The player captures `capture_authority(track_id)` at pointer press and checks
the same opaque object with `authority_matches(track_id, token)` on release.
Collection actions accept `expected_authority=token`. This prevents a held
gesture from targeting a newly ready account on the same song, including a
same-user re-login. Playlist rows and refresh buttons retain their panel's
original authority; they cannot silently rebind when a song/account changes.
Opening the panel while its initial status is loading triggers exactly one
playlist read when that binding becomes ready. Later change signals and failed
reads do not automatically repeat the request; explicit refresh remains available.

Unknown heart state remains `None`. It is not interpreted as unliked. Writes
wait for service confirmation, suppress repeated clicks and never automatically
retry a timeout whose remote result is uncertain. After an uncertain favorite
write, a deliberate click refreshes state before another target can be sent.
Only fixed safe error messages reach the UI; arbitrary exception text and
credentials are not included.

## True no-media palette

`empty_visual_data` explicitly uses `position_source="none"`, confidence zero
and achromatic provider tones. Island UI exposes `media_no_audio` only when the
marker also has no playback, artwork, duration or actual session identity.
Effective idle colors follow Windows' system theme while the saved song ambience
is retained. Empty-cover and mist painting stay achromatic rather than synthesizing
the previous blue hue. A genuine paused track keeps its normal position source,
artwork and album colors; pause alone never selects the idle palette.

## Verification

- 91 isolated Python/service and actual Node-child fixture tests passed in
  10.413 seconds, including 16 new collection boundary cases.
  Log: `logs/favorite-backend-2026-10-16.log`.
- 32 focused native Qt tests passed in 2.784 seconds: 14 controller cases,
  five true-idle palette cases and 13 existing system/album background cases.
  Log: `logs/collection-idle-final-2026-10-16.log`.
- Checks cover explicit unlike encoding, nested platform success, denied account
  and playlist writes, late logout replies, wrong-source destinations, paging,
  DJ visual-song identity, unknown state, queued/in-flight song changes and
  same-user re-login.
- Follow-up authority verification passed 16 backend contract tests in 0.725
  seconds and 27 native Qt controller/surface behavior tests in 1.252 seconds.
  Logs: `logs/collection-authority-backend-2026-10-16.log` and
  `logs/collection-authority-qt-2026-10-16.log`. The latter excludes the separate
  collection geometry case under active layout repair; integrated layout and
  full regression remain the enclosing task's verification. Held presses after
  account replacement/same-user re-login, returned songs, stale panel children,
  and loading-to-ready exactly-once dispatch are covered.
- No real account favorite or playlist mutation was performed. These fixtures
  establish contract/error/ownership behavior, not live network success.

The final integrated application, native player visual review, full regression
and any rebuilt executable remain the enclosing task's separate verification.
