# Buffered owned playback and genuine NetEase Private DJ

October 11, 2026, source version `6.412`, branch `codex/owned-playback-sources`.
Restart this checkout's `launch.bat` to load source changes. The previously
packaged executable is unchanged.

## Playback transport

Production owned playback prepares a complete session-local file before Qt
decodes it. Stream authorization, download, artwork and lyric work remain off
the GUI thread. The current entry and the next entry have separate file leases;
changing song, mode, source or catalog invalidates late work. Signed addresses
and account material remain in memory. Audio files use random names in the
local GluSpot audio cache and never enter Git.

The downloader accepts a known positive HTTP Content-Length and identity
encoding, enforces a 64 MiB file limit and a 192 MiB session budget, and retries
at most three requests within 45 seconds. A truncated response may continue
with Range only after checking Content-Range, total length, ETag where
available, and overlapping bytes. A server that ignores Range receives a full
restart rather than unsafe append. Unknown lengths, changed resources,
permanent access failures and incomplete bodies fail explicitly. Successful
downloads publish atomically after a recognized audio header check. Qt then
opens a local file; decoder format support remains Qt's responsibility.

Current-file leases prevent eviction during playback. Next-entry prefetch is
bounded. Session locks preserve another live instance's cache, and cleanup
touches only this cache's recognized flat session files. Abandoned sessions
are cleaned after obtaining their lock. The existing one-reopen decoder
recovery remains a fallback; it is no longer the primary response to a network
body ending early.

This adds initial download latency and disk use. It does not promise acoustic
gapless transitions, solve every possible decoder error, or establish physical
HomePod/Bluetooth alignment. Account trial restrictions remain authoritative.

## What the reference projects establish

The fixed Folia commit remains
`d824c0b854e54d5411cb072092999823e9bd7071`. Its
[prefetch service](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/services/prefetchService.ts)
prefetches lightweight metadata/URLs/lyrics/artwork; online playback can reuse
an existing blob but otherwise opens the remote address. Its played-track cache
downloads a blob after a complete playback. Those paths do not prove the first
remote body was fully received before decoding.

The fixed Mineradio commit remains
`d43de565acabfdc1a9c9820a27e81a98ccbebcef`. Its
[proxy](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/server.js#L6639-L6677)
forwards Range/Content-Length with connection/chunk timeouts, without a complete
body verification or continuation contract. Its Qishui decryption path buffers
before serving local ranges, but is not a generic first-play integrity repair.
GluSpot therefore implements its own verified download boundary. No reference
implementation code or assets are copied. See the existing
[source and license study](owned-playback-sources.md).

## NetEase Private DJ

The 网易云 music library adds **私人DJ** alongside daily recommendations and
ordinary personal roaming. The pinned `aidj_content_rcmd` module returns an
ordered `aiDjResources` list containing `audio` and `song` resources. Authenticated
probes succeeded with genuine voice addresses, durations in seconds, expiry
and related song IDs. Related covers come from matching `songData`. The strict
parser preserves resource order and rejects incomplete or mismatched batches.

Speech uses its service-provided authorized address and the same complete
download path as songs. It never goes through `song_url` or the song lyric
endpoint. The UI labels it **私人DJ正在介绍音乐** without invented lyrics. Songs
use the ordinary authorized stream and genuine lyric paths. DJ continuation
requests further DJ batches instead of reverting to ordinary FM. Natural
playback follows the spoken introductions and songs. Manual previous/next
chooses songs; single-song repeat repeats music rather than introductions.
Normal playlist mode preferences are preserved when leaving DJ/FM.

The server returned `timbreKey=M791-C2`, with no announcer name or persona
selection input in the pinned module. Its association with **豆花** remains
unverified. The implementation exposes genuine Private DJ content under the
neutral name **私人DJ**; it does not label an unknown voice 豆花, synthesize a
replacement introduction, guess persona parameters or collect location.
See [the updated DJ investigation](personal-dj-research.md).

## Device volume

Program percentages are absolute per-device targets, rounded to 1% steps.
For Windows they reach the selected endpoint's system volume; for active
AirPlay outputs they reach the existing receiver control. Actual readback and
mute state update the UI. External system-volume changes remain observable;
targets are never published as confirmed device values. There is no additional
Qt gain multiplying the percentage. Previous-volume restoration is rounded to
the same units; a zero-percent history restores 30%.

## Controls

Personal roaming/DJ infinity uses the existing lyric accent as the middle
color, a darker version and a lighter version in repeating horizontal bands.
The pattern travels right with a four-second period and preserves the supplied
SVG silhouette. Visible frames reuse the alpha mask; idle/fade frames freeze
their phase and reuse bounded layers.

The song-page library control is a circle containing the existing return icon.
Hover expands it to the icon plus **音乐库**; leaving reverses from its current
pose. The shared control timeout, opacity, motion and blur hide/reveal it with
the other controls. Pointer hit testing follows the visible rounded shape and
interaction threshold; local repaint and ripple exclusion follow that shape.

## Verification

All **3932** final Python tests passed in **197.117 seconds**, with no skips,
using this checkout's CPython 3.12 environment after source freeze:

```powershell
.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest discover tests -v
```

Dependency consistency, Python compilation, Node bridge syntax and the final
diff checks also passed. The source version and existing EXE remain unchanged.

Focused HTTP integrity/lease, strict service/bridge, DJ queue and device-volume
regression tests passed. Native evidence uses actual Qt decoding and background
tasks, with isolated fixture storage and the app's own output muted:

| Verification | Result |
| --- | --- |
| Four DJ cases: continuation, pause/resume, seek and music-only repeat | 98 checks passed |
| Four complete-file MP3 cases: offline playback/seek/EOF, truncation resume, permanent truncation rejection and prefetch reuse | 57 checks passed |
| Healthy complete-buffer Qt/provider playback and sequential looping | 14 checks passed |
| Library/player/provider flow and real application constructor | 64 + 14 checks passed |
| Control gradient/navigation/prewarm tests at DPR 1 and DPR 2 | 80 tests passed at each scale |
| Final controls/icons/pixel-comparison fixtures at DPR 1 and DPR 2 | 152 tests passed at each scale |
| Authorized account, genuine DJ voice/song/voice Qt playback | 13 checks passed in 19.062 seconds |

The final paused-window control regression covers both actual two-group system
capture and three-group owned playback. It verifies one blur build per paint,
local damage, bounded caches, no build during fade and on-time hidden hit
targets. In-place source switching updates only the small prewarm budget and
does not reset idle visibility. The bridge fixture waits for its own Node
process before removing a Windows temporary working directory; production
shutdown retains its existing nonblocking reap.

Pixel-regression fixtures retain their cropped `QImage` objects until each
`constBits()` snapshot has been copied. A minimal lifetime experiment confirmed
that the borrowed memoryview does not own the image and that a later image
allocation can overwrite it after the temporary is collected. The fixture
repair preserves exact partial/full-frame pixel comparisons and ripple
exclusion checks; it does not relax drawing assertions or change runtime paint.

Real-account probes report only safe structural summaries; protected account
files are hash checked before and after. The real voice and song also passed
complete downloads of 428,870 and 2,922,669 bytes, one request each. Session
files were cleaned. No physical device volume was changed for verification.
This establishes decoding, queue order, clock, seek and end-of-file behavior;
it does not establish physical listening, acoustic synchronization, sustained
hardware performance, or the returned persona's identity as 豆花.
