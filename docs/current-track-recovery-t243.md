# Current-track publication and bounded recovery — t243

Box-only source work on `grok/capture-narrow-v4`, starting at `c9ced9a`.
The supplied PC screenshot/log were recorded from `b4601bd`; both revisions
contain the artwork scheduling defect below. No version, build, push, PC
operation, button/glass restyle or translation-style change is included.

## Reproduced mechanisms

- PC-build `local/owned_music_player.py:1936` returns after applying prefetched
  audio and skips the artwork pump at `:1953`. Once playlist thumbnails and
  prefetched audio are available, a newly selected 320 px thumbnail can remain
  pending without an original request. The buffered-playback regression fails
  on the starting source and passes with the pump restored before return.
- PC-build artwork capacity at `local/owned_music_player.py:2752` counts two
  unfinished jobs indefinitely; queue reset at `:2648` does not clear those
  obsolete running records. There is no delivery deadline. Injected stalled
  jobs reproduce starvation, including a current track queued behind them.
- Starting `local/music_library_controller.py:118` / `:1254` publishes the
  owned player's track directly, while the floating island reads the active
  `PlaybackSourceProvider`. Giving the library child the provider shares audio
  but does not update its track. All seven new consistency cases fail against
  the original controller with the same provider/owner fixtures.
- Starting `local/music_surface_window.py:1000` / `:1977` only retires stale
  geometry bitmaps on the surface animation clock. A hidden/stopped surface
  can retain title, artwork and skeleton pixels from A after its owner has B.
  A paired original/current-source harness reproduces this with actual page
  bitmaps: both model IDs are B, but the original retains all four snapshot
  types; the repair retires them at publication. This is stale displayed data,
  separate from the library's independent track-selection defect.
- Owned initial lyric fetches share the stream-authorization task lane and
  have no delivery deadline. Capture lyric `_pending_keys` and the single
  presentation-preparation worker also have no stalled-attempt expiry.
  Existing epoch guards reject obsolete results but cannot release a stuck
  slot. `386b441` fixes failed preparation returning a cached loading marker;
  it does not bound acquisition or worker delivery. t238 bounds the window's
  visual lyric wait; this change additionally resolves the source/worker state.

## Recovery and authority

The floating owner publishes one immutable `current_visual_data` snapshot
through `visualDataChanged` only after all its fields have been applied. The
music windows observe that owner; library/footer reuse the same published
track and command route. Child preparation stores retain their independent
hide/show lifecycle but do not select a playback source. Track publication
retires old geometry, compact, layout and resize bitmaps before the next paint,
including same-ID metadata replacement and hidden windows.

The prefetched-audio path starts current artwork upgrades. Owned artwork uses
bounded request leases, existing retries and one 30 s current-cover budget
including queue wait. Queue replacement releases obsolete bookkeeping. Expired
request tokens cannot overwrite a retry. Timed `MusicTasks` has fixed dispatcher
and reserve-action limits; exhausted capacity reports timeout rather than
creating unlimited threads. It suppresses late delivery, not native I/O itself.
Capture artwork likewise expires metadata/download delivery leases, releases
logical capacity for retry, and bounds the reserve of physically blocked
threads. URL/track generations fence stale queued work and late publication.

Owned initial lyrics use a dedicated timed lane, one transient retry and a
30 s track budget. Duplicate DJ/song requests retain the same genuine reply;
serial, song, playback and preference guards reject superseded results.
Capture lyric lookup retries a hung attempt after 15 s and ends the same budget
at 30 s, fencing memory, disk and pending-state publication. Genuine completed
misses remain terminal. Preparation has bounded delivery and one retry, with
failure notification immediately releasing cached loaders.
The capture lyric store retains its established resolver-thread model, with at
most two attempts per recording; it does not impose a global cap on blocked
resolver threads across different recordings. Its terminal delivery deadline
still holds if the underlying I/O ignores socket timeouts.

Window artwork wait is also bounded to 30 s. Failed/expired acquisition keeps
the current thumbnail sharp, or the existing placeholder when no bytes exist.
Lyrics resolve to the existing no-lyrics state and t238 cover/list rules;
automatic degradation writes no settings. Fresh valid lyrics restore their
existing layout policy. Classic translation, t217/t227 masks, personal-radio
controls and idle measurement caching are retained.

## What the PC log establishes

The untimestamped `AUDCLNT_E_DEVICE_INVALIDATED` line is followed by four
successful original-artwork displays, the last at **15:12:06.564**. FFmpeg
continues decoding later songs. It records an invalidated native audio device;
there is no evidence tying it to the cover queue or lyric worker.

From **15:18:20.945**, later displays retain 320 px thumbnails in pending state.
At **15:39:27.746**, display hash `4edb1db80ceb1e8f` has no bytes and is pending;
**2.491 s** later it is failed with no image. The prefetched-art scheduling bug
explains how the observed pending-upgrade pattern can arise. The missing-byte
terminal case also needs the bounded placeholder/lyric fallback.

These are display logs. Their hash is normalized title/artist, not the provider
track ID; `err=none` is the display logger's default, not a successful network
result. No worker stack/request trace or lyric diagnostics were supplied.
Neither the exact stalled call nor the screenshot's title-to-hash mapping can
be established. The seven-bar wide screenshot is not the two-bar pending
library-selection painter, and its floating island is not proof of the
library-child split. The exact PC trigger remains **未验证**.

## Verification

Focused tests exercise prefetched audio, stalled/queued artwork, retry budgets,
late responses, rapid switches, reused IDs, hidden/new windows, provider-selected
library consistency, DJ overlap and preparation recovery. Real-thread fixtures
also exercise timed-worker capacity and stale delivery. Paired synthetic
production renders show matching current title/artwork/lyrics and the existing
terminal cover-only presentation.

Complete verification results and sanitized evidence are recorded in
`/workspace/gluspot/handoff/t243/FINAL.md`. Final-source isolated discovery
covers **348 modules / 6,229 tests in 159.775 s**, with exactly the t238
**54 failure / 48 error / 32 skip identities**, zero new or removed identities
and unchanged Python source hashes. Two native-incomplete modules were
supplemented with individual ordinary test processes. The canonical ordinary
single-process discovery also ran and terminated with a native segmentation
fault in `test_library_control_focus` after 46.586 s; it did not complete.
This is baseline equivalence for the isolated coverage, not a green suite or
native stability claim. Compilation and whitespace checks pass.

Native Windows compositor/masks,
live account/network services, SMTC/device recovery, long-session playback and
the original PC incident remain **未验证**. Offscreen renders are fixtures,
not a PC acceptance run.
