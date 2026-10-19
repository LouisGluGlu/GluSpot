# DJ background preparation and explicit continuation

October 15, 2026 follow-up for GluSpot `6.412`. Version and packaged EXE are
unchanged. These changes are in the existing owned playback controller; they
do not add a surface-specific playback path.

## Reproduced causes

The playback owner has no window-focus or surface-visibility gate on DJ
preparation. Its timer, workers and completion handlers continue independently
of the island and song page. Hiding the song page stops that page's animation
and status polling, not the owned player's preparation. The investigation did
not reproduce an operating-system focus failure and does not attribute the
reported symptom to one.

Three owner-level paths were reproduced with controlled worker and decoder
fixtures:

- A failed recommendation prefetch retained its queue-tail attempt marker.
  A transient network or timeout failure could therefore prevent another
  background recommendation request for that tail indefinitely.
- Next at an FM/DJ queue tail waited for either the existing background
  catalog or a fresh catalog without retiring the skipped decoders. Music
  and speech could keep playing while that request was pending. A pending
  multipart speech download could also be promoted into the replacement
  catalog's current epoch and revive the skipped introduction.
- A future speech download that exhausted its background cache-contention
  budget could become the current introduction before returning `busy`.
  That result failed the current introduction immediately, bypassing the
  existing bounded foreground preparation retry.

## Resulting behavior

NetEase DJ background recommendation failures reported as `network`, `timeout`
or `busy` use the existing 150 ms owner timer for at most two retries per queue
tail, catalog and voice generation. Authentication errors, invalid responses
and empty results do not retry. Each request has an exact private token, so a
late completion cannot append an old batch or finish a newer retry. The timer
cannot request the previous FM catalog while an explicit catalog replacement
is pending.

Next/EOF continuation at the queue tail now retires the old music and speech
decoders, clears their sources and speech work, releases the active prepared
audio, and invalidates the old stream epoch before waiting for recommendations.
It preserves the user's play/pause intent for the incoming song. Ordinary
catalog replacement retains its established healthy-current-playback behavior.

When a future speech download becomes current and returns `busy`, it now uses
the existing shared three-second foreground preparation budget. Other current
speech failures retain their existing policy. The new song still accompanies
its genuine introduction at 20% gain and restores smoothly over one second
after speech. Official recommendation order and genuine speech remain the
source of truth; song-only recommendations do not gain invented introductions.

## Verification and limits

Ten regressions cover owner-only retry, retry bounds, stale completion fences,
non-retryable catalog errors, immediate decoder retirement, multipart download
retirement, promoted cache contention, explicit catalog replacement, pause
during recommendation retry and source/stop changes made synchronously by a
decoder-state subscriber. Two older playback fixtures were corrected to
pause the actual second-song preparation and to end the independent speech
decoder, instead of issuing an additional Next/music EOF that skipped the
song under test.

Before the fix, the new module reproduced six expected failures among 28
tests. Before the final synchronous-subscriber guard and its regression were
added, all 243 tests passed across:

```text
tests.test_personal_dj_prefetch
tests.test_personal_dj_playback
tests.test_owned_playback
tests.test_personal_dj_completion
tests.test_personal_dj_overlay
tests.test_owned_buffered_playback
tests.test_owned_audio_bridge_handoff
tests.test_personal_dj_pcm_mix
```

The fixed run took 4.485 seconds. Ignored logs are
`logs/dj-continuation-reproduction.log` and `logs/dj-continuation-focused.log`.
Python compilation and `git diff --check` also passed for the final source.

The first integrated 4,565-test run exercised the final subscriber guard. It
reported seven failures: five appearance expectations handled by the surface
work, and two older playback expectations for retaining a queue-tail source.
The playback fixtures now require the intentionally retired source to be
stopped and empty, reject its late decoder error after a voice change, and
verify that the selected new voice can continue. A separate regression retains
ordinary catalog replacement's buffered-fault/watchdog behavior across voice
selection. Those updated fixtures are included in the successful final
integrated run recorded below.

The repeatable local HTTP acceptance tool, `tools/smoke_personal_dj.py`, was
also updated to the current independent music/speech decoder contract. Its
earlier assertions still assumed speech occupied the music decoder and that
the introduced song's clock paused. It now checks both actual decoder clocks,
durations, complete-file sources and decoded buffers, associates frequency
frames with the speech clock, and verifies music at 20% gain under normal-gain
speech. Both process-local outputs are explicitly muted; nonzero baseline
volumes allow gain verification without audible output. Ordinary manual Next
also waits to observe the completed introduction's one-second gain restoration.

The new `manual_next_tail` scenario blocks the genuine background catalog
worker using a fixture event. Next during speech must immediately stop both
Qt decoders, clear both sources and remove the speech frame before releasing
that catalog. At least half a second of event-loop sampling must show no old
source revival; state signals also reject an old source returning to playback.
Releasing the fixture must then produce the matching next music and speech,
with actual decoded speech frequency and the intended gain. This is a Qt/cache
acceptance boundary; it does not establish physical receiver-buffer behavior.
The final serial Windows runs passed `manual_next` (43 checks, 3.484 seconds)
and `manual_next_tail` (45 checks, 2.516 seconds). Both outputs remained muted;
actual decoded buffers and clocks were observed. Reports are under ignored
`.runtime/glass-dj-final-smoke-v2/` and logs under `logs/`.
The final combined source passed **4627 tests in 322.896 seconds**, with no
skips or Qt callback tracebacks. The ignored log is
`logs/gluspot-integrated-final-2026-10-15.log`; Python compilation and
`git diff --check` also passed.

These tests establish controller, cache-lease and decoder-command behavior,
including the existing real PCM gain-mix checks. This investigation did not
perform authenticated network playback, a long-running background session or
physical-output listening. The native output worker stops asynchronously;
the tests do not prove that every physical device's buffered audio tail is
eliminated or that the reported half-second duration has one exclusive cause.
