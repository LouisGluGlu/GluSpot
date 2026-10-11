# Personal FM introductions, speech control and shared playback surfaces

October 11, 2026 source follow-up. GluSpot remains `6.412`; the packaged EXE
is unchanged. Restart this checkout's `launch.bat` to load the source changes.

The subsequent [presentation and manual-navigation follow-up](personal-dj-presentation.md)
supersedes the below/right-of-infinity layout and full-page voice presentation
described at this checkpoint. DJ now appears at the bottom right, preserves the
previous music's paused page, and uses real introductions during manual navigation.

## Private DJ belongs to personal FM

The NetEase library keeps daily recommendations, personal FM and playlists;
the separate DJ destination is removed. The personal-FM song page has a
dedicated curved-waveform control below/right of its infinity button. Clicking
toggles genuine service-provided DJ introductions. The boolean preference is
saved and restored; it defaults to disabled and other playlists/platforms do
not show the control.

Enabling DJ keeps the currently decoded song, clock, lyrics and complete-file
lease. Future recommendations use the existing authenticated DJ boundary.
Continuing batches begin at a genuine introduction and retain its following
song. The parser requires `introductionRelatedSongIds` to identify that next
song, never an earlier song or a trailing orphan voice. The service can begin
the first batch with a song without an introduction; no introduction is
invented. Disabling removes future voices. During speech it selects the
introduced song, preserving pause state and a usable prefetched file.

Ordinary playlists retain sequential-loop, shuffle and repeat-one. Personal
FM retains infinity continuation and repeat-one; repeat-one repeats music,
never an introduction. A distinct recommendation generation rejects stale
FM/DJ replies after toggles while preserving the current music stream epoch.
Old Qt position/duration/play-state signals cannot publish a loaded new song
as playing before its actual stream is established.

## Speech icon and lyric browsing

Actual speech automatically reveals only the DJ control with a short pop/fade.
Outside speech it follows the existing shared control visibility. Its circular
hit region is independent of the cover ripple. The five curved vector strokes
respond to measured low/mid/high frequency energy; no fake pulse or additional
capture/decoder is used.

The existing `QAudioBufferOutput` supplies 44.1 kHz stereo Int16 PCM. Each
complete 50 ms block is analyzed using the shared live-analysis frequency
bands. At most 96 timed frequency values are retained; raw PCM is not stored.
The getter uses the actual Qt presentation position rather than the newest
decoded-ahead buffer. Silence, pause, source changes and shutdown produce no
synthetic energy. Track epochs and within-track position discontinuities
clear the old frequency history. Drawing uses vector pens, without per-frame
raster blur or growing caches.

Manual wheel browsing clears blur from every visible lyric row, including
during automatic return. Selecting a lyric immediately exits browsing; the
existing async Seek fencing and actual clock remain authoritative. When focus
returns, the established lyric motion smoothly restores ordinary blur.

## Song/interface consistency

Owned source and play-state boundaries immediately publish one shared island
owner snapshot. Clock, artwork and lyric updates retain the existing bounded
poll cadence. Visible song pages finish an obsolete mode-transition bitmap
when its song identity changes. A hidden song page reopening after a background
song change discards the previous song's visual/lyric transition before
capturing its destination. Visible song metadata uses complementary old/new
opacity during its ordinary transition.

A catalog request rejected while the current song remains playing now says
that the current song continues and preserves the specific failure reason.
Healthy old-source identity and decoded duration survive a pending catalog
request. A failure restores only that still-current source's actual clock;
it never issues Play. Old-source EOF during the request can continue the
original queue after failure. A later Stop, explicit song selection or newer
catalog request fences the prior reply. Retained original-song lyrics can
finish loading without confirming the replacement catalog as ready.
The running-process read-only inspection found one active GluSpot audio
session, no other GSMTC media session, and a hidden song page. This does not
prove the cause of the reported sustained dual-song playback; the repaired
poll, stale-bitmap and hidden-reopen paths are independently reproducible UI
conflicts.

## Verification and remaining evidence

The final full Python suite passed all **4000 tests in 206.019 seconds**, with
no skips, using `.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest
discover tests -v`. Compilation, dependency consistency, Node bridge syntax
and whitespace checks passed. The actual muted Qt baseline, catalog and DJ
scenarios below passed **220 checks** in total.

| Focused/native verification | Result |
| --- | --- |
| Player, catalog rollback, DJ generation, stream faults and provider regression | 228 tests passed |
| Actual muted Qt complete-file/clock/EOF baseline | 14 checks passed |
| Actual Qt rejected catalog before and after old-song EOF | 20 checks each passed |
| Actual Qt DJ continuation, pause, seek, repeat and FM toggle with PCM/UI clock matching | 166 checks across five cases passed |
| DJ control tests at DPR 1 and DPR 2 | 10 tests each passed |
| Lyric browsing/focus tests at DPR 1 and DPR 2 | 22 tests each passed |
| Shared song identity/hidden reopen at DPR 1, plus existing lifecycle/cover upgrade at DPR 2 | 9 and 11 tests passed |
| Three viewport sizes at DPR 1 and DPR 2, including measured low/mid/high glow | 48 native preview checks passed; screenshots inspected |

The repeatable native scenarios are `tools/smoke_personal_dj.py --scenario all`
and `tools/smoke_owned_playback.py --catalog-failure-delay 1` / `2.4`. Fixtures
and bounded reports remain ignored under `.runtime/`; raw account material,
signed URLs, media caches, generated previews and runtime logs are not tracked.

The real account's read-only response confirms genuine introductions for the
following songs and unchanged protected account bytes. It returns
`timbreKey=M791-C2` and no announcer name. The fixed 4.41.1 module exposes no
verified voice-selection parameter or name-to-ID mapping for **豆花、黄俊、
李源、庞舸**. Four-persona switching remains unimplemented pending a verified
interface; the returned voice is not labeled 豆花. See [research evidence](personal-dj-research.md).

Synthetic muted Qt checks establish decoding, queue order and clock/spectrum
integration. They do not establish audible continuity on physical outputs,
HomePod acoustic synchronization or sustained online playback.
