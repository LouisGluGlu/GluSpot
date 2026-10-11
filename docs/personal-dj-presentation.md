# Personal DJ presentation and manual navigation

October 11, 2026 source follow-up for GluSpot `6.412`. Private DJ remains a
NetEase personal-FM feature. The packaged executable is unchanged; restart
this checkout's `launch.bat` after exiting the running source instance.

## Requested visual behavior

The six supplied references define the bottom-right location, five overlapping
elliptical rings, a circular enabled/disabled face, a larger speech glyph above
a small next-song title capsule, and a separate loading-state outline. The
colored rings in the motion reference identify movement only; those colors are
not a product palette.

The compact control shares the other controls' idle hiding. Speech reveals
the DJ control at twice its compact diameter. During speech, the backing
circle unfolds into the title capsule beneath the rings; the title identifies
the upcoming music, while measured low/mid/high speech energy drives the glyph
and outline. Completion reverses the presentation into the compact control.
The ring animation uses the current song's album accent for one traveling
halo and background-aware neutral ink for the remaining rings. It moves along
a straight right-to-left path, changing size with depth, over 0.85 seconds
with decreasing speed, followed by a 0.5-second hold.

An additional blue outline indicates introduction loading, green indicates a
prepared introduction, and red indicates failure. Blue and green glow rises
and fades. The underlying idle motion continues in each state; state colors
do not recolor every ring. These controls remain independent of lyric ripples.

## Audio and visual identity

One existing Qt decoder plays the authentic service-provided introduction and
its associated song in sequence. Voice segments are audio identities, not
full-page songs. After music ends or the user selects a different song, the
previous song's cover, title, duration, lyric rows and clock remain visible
and paused during the introduction. The title capsule alone identifies the
upcoming song. When there is no previous song, the real introduced song's
static metadata supplies the initial page. Speech completion starts that
song; speech clock and duration never overwrite the music clock.

Manual Next and Previous use a real introduction already associated with
their target song when available. Another Next during speech selects the
following song's introduction. A song without a supplied introduction is not
given fabricated speech. Single-song repeat repeats music. Pausing during
preparation or speech preserves user intent, and disabling DJ removes speech
without starting paused music. Failure falls through to the associated song
with a brief red state rather than leaving an endless introduction wait.

The existing provider boundary exposes a bounded presentation snapshot with
state, next-song title, speaking flag and token. Actual decoded PCM continues
to drive the frequency frame through `OwnedAudioBridge`; no second decoder,
system capture, synthetic speech or persona imitation is introduced. A real
introduction can open its paused song page from the library after actual
playback is established; a loading or paused decoder cannot open it early.

## Verification and limits

The final frozen source passed **4026 Python tests in 192.253 seconds**, with
no skips, using `.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest
discover tests -v`. Compilation, dependency consistency and whitespace checks
passed. An earlier full run was deliberately stopped after discovering a
synchronous cancellation edge; the complete result above includes its repair.
Native scenarios use generated loopback audio and mute only their own Qt
output. Reports, audio fixtures and animation previews remain ignored under
`.runtime/` and `logs/`.

| Focused/native verification | Result |
| --- | --- |
| Player/provider/catalog/stream/cache/DJ and library integration | 254 tests passed |
| DJ control, ring timing, state colors, hit testing, title transition and ripple isolation at DPR 1 and DPR 2 | 38 tests each passed |
| Three viewport sizes at DPR 1 and DPR 2 | 60 preview checks passed; screenshots inspected |
| Actual Qt automatic continuation, pause, seek, repeat, enable/disable, manual Next/Previous, rejected voice download and multi-part introduction | 333 checks across nine scenarios passed |

The repeatable native command is `tools/smoke_personal_dj.py --scenario all`.
It checks actual decoding and presentation-time frequency matching, paused music
metadata during speech, real introduction/song order, manual navigation, rejected
loopback voice downloads and cache cleanup. Cancellation, generation and lyric
preference regressions additionally use controlled worker deliveries. Native
fixtures do not establish physical HomePod acoustic alignment, long online
sessions or the identity of the service's default voice.

Four-persona switching needs a verified service interface and voice mapping.
The returned voice must not be labeled **豆花、黄俊、李源、庞舸** based on an
unidentified timbre key. See [continued voice research](personal-dj-research.md).
