# Artwork recovery and complete thumbnail reads

October 15, 2026 follow-up for GluSpot `6.412`. The version and packaged EXE
are unchanged. The changes use the existing playback owner, artwork worker
pool and shared Windows media provider; no additional provider is introduced.

## Observed failure paths

The reported song page had working title and lyrics but no cover. The image
alone cannot identify which network response or cache event caused that live
instance. Source review and controlled regressions established these concrete
weaknesses:

- Owned artwork requests left both attempted/failed markers set after a
  transient download failure or rejected worker submission. The retained
  queue therefore had no autonomous retry for that image.
- The owned downloader requested the known NetEase image object's larger
  size but did not fall back to the exact supplied URL when that variant
  failed. An available small image could remain unused.
- HTTP image decoding did not prove a response's declared byte length had
  arrived. The Windows thumbnail reader likewise performed a single
  `READ_AHEAD` call and accepted any positive length. A decodable prefix could
  be cached even when the stream advertised additional bytes.

The source DTO audit traced modern/legacy FM songs and DJ `songData` through
the existing song normalizer; verified fixtures supply album `picUrl`, which
is already handled. DJ speech inherits its associated song's artwork URL.
Explicit URL assertions now prevent those fixtures passing with two empty
values. No verified raw FM response established an omitted common artwork
field, so no speculative aliases or metadata lookup were added.

The existing system-provider URL cache already rejects undecodable in-memory
and disk entries, retries downloads after its cooldown, and retains the
sharpest valid same-track artwork. These protections are preserved. The
library's collection thumbnail completion already releases its pending marker,
and later visibility/scroll refresh can request a null image again; it does
not have the owned player's permanent attempted-marker failure. Its existing
fetch call also benefits from the HTTP fallback and completeness checks.

## Changes

Owned playback retries `network`, `timeout`, `incomplete`, `invalid_image` and
worker `busy` results at most twice per retained image key. Retry delays are
0.75 and 1.5 seconds after the preceding result. The owner's single-shot timer
runs independently of the visible song page or island. Invalid URLs, byte or
dimension limits, authentication failures and missing HTTP resources remain
terminal. Retry work keeps the existing two-request concurrency cap and queue
epoch/request fences; changing the queue or stopping cancels its authority.

Only the existing known unsigned NetEase size normalization gets a fallback
to the exact supplied song-bound URL. Both attempts share the original
six-second request budget. The deadline is checked before and after blocking
reads; an in-progress read may finish later and is then discarded. This is
not hard socket cancellation. Signed and unknown URLs retain their exact target.
No alternate song identity, search result or invented image URL is used. Other
callers keep the existing bytes-or-empty fetch interface; the owner opts into
sanitized error codes without URL or response text.

A valid image below 320 pixels on its shortest side stays visible with the
existing `thumbnail` provenance as a preview while the bounded upgrade attempts
continue. Failed or rejected upgrades do not clear it. Revisiting a cached
song preserves preview provenance and the
remaining/exhausted retry state. A result with a shorter minimum dimension
cannot replace a sharper same-song preview. Image decoding, palette extraction
and dimension measurement remain on the worker; GUI callbacks receive bytes,
colors and plain dimension metadata. Exhausting the upgrade budget ends the
loading state while retaining the usable preview.

The Windows thumbnail reader now drains the advertised bounded stream using
at most 64 KiB per read, rejects zero progress, overreads and reader-length
mismatches, and publishes only a complete valid image. Old incomplete or
undecodable thumbnail-cache entries are rejected before reuse. Every exit
closes readers and the stream, including cancellation and cache hits. Existing
50 ms first-frame and 2.5-second total read deadlines, cache-clear epochs and
task-identity publication fences remain. An unsuccessful read can be retried
by a later provider refresh rather than pinning partial bytes.

## Reference evidence and independent implementation

Only text source was inspected at the supplied commits. No reference code,
dependency setup or application was executed. These sources informed the
review; the changes are implemented within GluSpot's existing Python/Qt
boundaries rather than importing their implementation:

- Folia Major
  [`useLocalCoverPreloader.ts:8–59`](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/hooks/useLocalCoverPreloader.ts#L8-L59)
  drains the complete response, bounds concurrency and removes its pending
  record in cleanup so a failed image can be requested later.
- Folia Major
  [`localCoverBinaryStore.ts:81–149`](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/services/localCoverBinaryStore.ts#L81-L149)
  compares stored byte length with its descriptor and publishes metadata only
  after the binary write completes.
- Mineradio
  [`05-cover-loading-crop.js:13–74`](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/public/js/modules/03-beat/05-cover-loading-crop.js#L13-L74)
  checks track identity around image completion and makes one direct fallback
  after a proxy failure.
- Mineradio
  [`15-ripples-cover-depth.js:517–632`](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/public/js/modules/02-visual/15-ripples-cover-depth.js#L517-L632)
  checks both track and cover-process identity before delayed dependent work
  can publish. Its outgoing-track retention policy is not adopted: GluSpot
  keeps artwork attached to the actual current/introduced song.

Pinned source excerpts and the fuller read-only audit are retained under
ignored `.runtime/artwork-reference/`.

## Verification

Twelve new owner regressions cover autonomous recovery, corrupt HTTP success,
retry exhaustion, terminal errors, preview retention and upgrade, cached-song
revisits during scheduled/in-flight upgrades, rejected workers, non-square
downgrade rejection and stale queue callbacks. Five new HTTP regressions cover declared short reads, exact-URL
fallback, signed-target preservation, sanitized HTTP error classes and invalid
image responses. Twelve new thumbnail tests cover complete short-read
assembly, decodable prefixes, damaged cache entries, resource closure, bounds,
zero progress, mismatched reader lengths, deadline cancellation and cache-clear
races.

The old owner method bodies reproduced four assertion failures in the new
latch/preview/bounded-retry checks. A separate pure-Python transport harness
with a fake decoder and no network confirmed the old downloader accepted a
declared short response and discarded an available exact-URL fallback. The
thumbnail helper's prior single-read body also accepted and cached a prefix
while leaving its remainder unread.

The first focused verification passed **265 tests in 6.410 seconds**, before
the final static review corrected the thumbnail provenance contract and added
the in-flight cached-song revisit regression:

```text
tests.test_owned_artwork_retry
tests.test_online_music_artwork
tests.test_thumbnail_stream_integrity
tests.test_owned_playback
tests.test_music_cache_clear
tests.test_music_recovery
tests.test_provider_state
```

After those corrections, 73 cover/surface/thumbnail integration tests passed
in 7.546 seconds, followed by 121 related tests at DPR 2 in 8.704 seconds.
The final combined source passed **4627 tests in 322.896 seconds**, with no
skips or Qt callback tracebacks. Python compilation and `git diff --check`
also passed. Ignored evidence is in `logs/artwork-resilience-focused.log`,
`logs/artwork-resilience-baseline.log`, `logs/artwork-http-baseline.log`,
`logs/gluspot-integrated-repairs.log`,
`logs/gluspot-integrated-final-dpr2.log`,
`logs/gluspot-integrated-final-2026-10-15.log` and
`.runtime/artwork-reference/`. These checks establish
controlled recovery and identity handling; they do not prove which response
caused the supplied live screenshot or guarantee every remote CDN's uptime.
No user artwork cache, account or credential was cleared for this work.
