# DJ completion, island return and neutral-cover rings

October 12, 2026 follow-up for GluSpot `6.412`. The version and packaged EXE
remain unchanged; restart this branch's `launch.bat` to load the source changes.

## Presentation

Window/fullscreen narration contracts into its compact icon in 0.32 seconds.
The icon then remains visible independently for two complete idle cycles:
2 seconds of travel and 0.5 seconds of rest per cycle, 5 seconds total. It then
uses the existing 0.52-second control hiding motion. Other hidden controls stay
hidden. A small caption reports the actual following introduction's loading,
ready, failed or waiting state. Only all valid cached parts associated with the
immediate next song qualify for “下一首歌播报已准备好”; a later cached introduction
or a partially prepared multipart introduction does not qualify.

In the visible island, genuine narration completion first morphs the hollow
rings and song/artist text into the normal expanded artwork and metadata slots
over 0.32 seconds. The expanded island holds for 2 seconds and then collapses.
The normal hover-leave timer cannot shorten that hold. Explicit interaction,
new narration, a different song/source, hiding the island or disabling DJ ends
the automatic hold. Hidden library/login/song-page presentations consume old
completion events without later replaying them in the island.

Only the independent DJ decoder's final genuine introduction `EndOfMedia`
publishes completion identity, associated track and monotonic completion time.
Pausing, loading between parts, failure, cancellation and official auditions
do not publish an introduction completion. Both surfaces read the existing
provider presentation dictionary; no second playback owner is introduced.

Island narration now includes the current announced song's artist. The app
publishes the new media snapshot before synchronizing DJ state, so a synchronous
speech event cannot pair a new song title with the preceding artist. Short
taskbar shells adapt both text rows to the available height. Below 72 logical
units, loading/failure captions reserve their measured width at the right of
the artist row, while the rings retain the full available height; artist text
elides into the remaining space. This avoids clipping both state captions to
the same unreadable middle characters beneath small rings.

## Island lyric oscillation and ring color

The return bug was reproduced with the original methods: the hidden island's
old tall shell and spring velocity resumed after the page-to-island transition,
and fitting compact lyrics against that live height produced repeated
`16 → 15 → 16 → 15 → 16 → 15` font sizes. Return now synchronizes the current lyric
and settles the main shell to the destination already reached by the page
transition. It preserves source clocks, reading progress and lyric-row springs.
The app's own full-screen song page is also excluded from external-fullscreen
scene suppression. Unrelated full-screen applications keep their existing
suppression behavior.

The island previously took its DJ pigment from `_wave_fill_color`, which rejects
neutral/dark provider colors and boosts chromatic saturation/value. DJ now uses
the shared artwork palette's characteristic role and existing contrast-aware
tone selector. Black/white/gray covers produce valid neutral rings without
inventing an accent hue. On light or gray flat islands, side-ring lightening is
bounded before it crosses the actual background tone; dark islands and the
window/fullscreen renderer retain their existing lightening behavior. This
changes the DJ ring role, not the cover pixels or lyric color settings.

## Automatic continuation investigation and hardening

The reported automatic-song-change failure was **not reproduced** in the
authenticated test session. A temporary muted production player used the
existing account, real NetEase recommendations/introductions, complete-file
cache and both actual Qt decoders. It reached two real music `EndOfMedia`
events, and each following introduction entered actual `PlayingState`,
published provider/island speech and revealed the window DJ independently.
The test sought only the temporary decoders near their ends to shorten the
run; it did not send Next or alter the user's running player. Both audio
outputs remained muted, the encrypted account file was unchanged, refreshed
credentials stayed in memory, and session audio was removed after cancellation.
These checks do not establish the cause of the user's live failure or prove
long-session/physical-output listening behavior.

An independently reproduced weakness was corrected: rejection of a foreground
DJ stream/download submission due to temporary worker contention previously
failed the introduction immediately. The existing 150 ms preparation timer now
retries that current part within one shared 3-second budget across submission
and cache-busy preparation. Network/expiry faults and obsolete requests do not
retry. Paused preparation cannot resume music, and generation/source/song/stop
fences prevent late speech resurrection.

Current genuine introduction loading/readiness and failure now remain visible
independently of hidden controls in both presentations. Failure visibility uses
the provider's real 2-second deadline. Future lookahead and auditions do not
claim that the current song's introduction is waiting. This makes preparation
or failure observable without fabricating speech or a completion animation.

## Verification

- Player/provider focus: 153 tests passed; actual muted local HTTP/cache/Qt
  acceptance passed 45 checks, including two temporary submission rejection
  paths after genuine automatic music EOF.
- Authenticated muted real-audio continuation: 34 checks passed in 17.110 s,
  covering two automatic transitions, actual decoder clocks, both surfaces,
  account preservation and cleanup. No account, signed URL or personal song
  metadata is recorded in source control.
- Island/return/rings/transition focus: 86 tests passed at each DPR 1/2.
  Window/outro/loading/hover focus: 66 passed at each DPR 1/2.
- Original-return-method comparison reproduced 11 expected regression failures
  without test errors; the fixed return focus passed at both DPRs.
- Full Python suite before the final short-shell caption correction: all 4305
  tests passed in 230.459 seconds, with no skips or Qt callback tracebacks.
  The verbose result is retained in ignored
  `logs/dj-outro-island-full-python-final.log`. The later caption correction
  passed all 15 focused island-outro tests. The subsequent integrated Finder
  follow-up passed all 4383 tests in 250.812 seconds after that correction and
  all final placement changes, with no skips, errors or Qt callback tracebacks.
  Final integrated evidence is retained in ignored
  `logs/finder-bar-final-full-python.log`.
- Native window/fullscreen preview frames cover speech end, contraction,
  compact readiness, the second idle cycle, hiding and fully hidden states at
  640 × 360, 960 × 540 and 1920 × 1080, at DPR 1/2 (36 frames).
- Final native island QA normally constructs `IslandStackVisual` with
  `EmptyIslandDataProvider`, stops its timers, and supplies synthetic covers,
  lyrics and prepared palettes without accounts or audio. The production
  music painter covers black/white/gray/accent speech, morph and expanded
  states, plus speech/loading/failure at 40/48/60/112 logical shell heights.
  DPR 1/2 ran sequentially: 60 frames and 339 checks passed at each DPR,
  including full status-caption ink fit, artist/title bounds and the neutral
  ring contrast path. Both transition montages, the short-shell montage and
  original-size small-shell samples were visually checked. This inspection
  found the caption clipping above; the final images include its correction.
  The ignored script is `logs/preview_island_dj_outro.py`; final images and
  result snapshots are under `.runtime/island-dj-outro-final-preview/`.

Generated evidence lives in ignored `logs/` and `.runtime/`. No executable,
version metadata, system routing, saved account or original Git backup changes
are part of this update.
