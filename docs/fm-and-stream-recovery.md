# Personal FM, device typography and interrupted streams

October 11, 2026 follow-up, source version `6.412`. The user runs this branch
through `launch.bat`. Version and the previously packaged executable are unchanged.

## Confirmed behavior

- Only confirmed selected output rows reveal compact text after a whole-row
  0.5-second hover. The name smoothly scales to the shared 10 px size, while
  the volume glyph/value fades in and its reserved width changes continuously.
  Leaving reverses from the current size. Unselected, pending and unavailable
  rows retain their ordinary font. Muted selected devices remain selected;
  unknown volume is still unknown. Solo, mute/restore, join and wheel-volume
  operations retain their established semantics.
- The existing personal FM button alternates **∞ continuation / single-song
  repeat**. Internally continuation uses the established sequential algorithm
  and the service's recommendation order. Shuffle is rejected for FM by the
  player and provider. Normal playlists retain sequential-list loop, shuffle
  and repeat, including their saved preference when entering/leaving FM.
- The supplied infinity SVG is retained byte-for-byte. Its center ink follows
  the song's existing lyric/control accent, smoothly travels to black on light
  occupied album tones or white on dark tones, and returns over four seconds.
  The neutral endpoint uses the existing album contrast helper. A thin edge
  keeps pale accents readable. Repeat and ordinary-playlist ink are unchanged.
  Only the visible FM control requests color frames; its SVG size mask is
  cached rather than every tint. The shared idle blur freezes that tiny tint,
  and prewarming uses the upcoming fade's stable color.

## Failure evidence and repair

The source-app log showed a 44.1 kHz stereo, 128 kb/s MP3 whose declared length
was 263.99 seconds. HTTP supplied 3,981,993 of the expected 4,224,775 bytes:
242,782 bytes missing, roughly 15.17 seconds at that bitrate. This is well
outside the previous 500 ms stalled-decoder EOF tolerance. Skipping that much
audio or projecting a timer to the declared song end would lose content.

A generated eight-second MP3 with its last two seconds omitted reproduced the
same real Qt behavior: ResourceError bursts, a clock stalled at about six
seconds, and no automatic range continuation. A transient three-second HTTP
stall resumed normally without error. This distinguishes a broken stream from
recoverable buffering; buffering alone does not trigger reauthorization.

The player now attempts one ordinary account-authorized reopen of **the same
song**, after a genuine Network/Resource fault and a stalled decoder clock.
The existing strict 500 ms real-decoder-tail rule still handles EOF first.
Automatic reopen requires a positive actual position within a known decoded
duration and seekable media. It has a single 12-second overall deadline.

Lyrics, identity and the actual stop position are retained. A new decoder
epoch invalidates old PCM and async results before the source changes.
Recovery waits for loaded, seekable audio, rejects a replacement shorter than
the stop point, requests the actual seek, and checks decoder readback within
250 ms before playing. The actual readback becomes the new clock baseline;
the tolerance itself cannot count as playback. It confirms success only after
the actual clock advances more than 20 ms beyond that baseline.
The visible track clock stays frozen while authorization/load/seek is pending.

Manual pause, seek, next/previous, new catalog/source, stop and shutdown fence
the automatic action. A timeout, invalid/denied stream, nonseekable source,
decoder error or another fault ends explicitly with lyrics retained; it does
not skip songs or loop retries. Explicit Play may use the established manual
same-song reload. No trial or account restriction is bypassed.

This is recovery after interruption, not gapless playback. A grouped owned
output must discard its old PCM epoch and rebuild from the recovered source;
the decoder/transport preparation can leave an audible gap. Physical listening,
server availability and long-session behavior still require user validation.

## Related research

[Private DJ / 豆花 capability study](personal-dj-research.md) identifies a
specific module in the pinned dependency, but its authenticated speech schema,
voice persona and segment sequencing remain unverified. This change does not
implement a DJ or substitute generated speech for the official service.

## Verification

- All 3868 final Python tests passed in 178.713 seconds, with no skips, after
  source freeze. The baseline command is `python -m unittest discover tests -v`.
  Staged diff/asset-byte/runtime-exclusion checks passed. Process readiness
  score: 100; scope remains the source alpha, with the limits below.
- The final focused player/FM/fault/recovery run passed 113 tests; independent
  recovery review passed all 19 tests, including pause/resume twice between
  timer ticks, synchronous decoder/seek errors, reentrant source changes,
  late authorization, and an offset seek that does not actually advance.
- Six real Qt MP3 recovery scenarios passed 86 checks (4.219 s successful
  reopen, 3.610 s permanent second fault, 5.047 s manual pause/resume,
  4.094 s manual seek, 3.594 s next cancellation, 5.156 s transient stall).
  Ten retained WAV/EOF/fault scenarios passed 178 checks. Fixtures use generated
  audio, localhost HTTP and this player's muted output only, with no accounts,
  global volume/routing changes, capture or physical receivers.
- The native library/app-constructor smoke passed 78 checks. Source/dependency
  compilation and `pip check` passed. No native helper source changed in this
  follow-up; its preceding rebuilt 12-suite baseline remains unchanged.
- Panel/motion integration passed 113 tests at each of DPR 1 and 2; native
  light/dark, 42/56 px row previews produced 64 inspected PNGs. Selected-row
  fractional sizes and reversal were checked; inactive sizes stayed unchanged.
- Mode/provider/surface/icon regression passed 26 tests at DPR 2. The native
  FM preview produced 36 inspected PNGs at DPR 1/2 on light/dark covers.
  Across each 120-frame tint run, SVG misses and control-layer growth were zero.
  Shared prewarm/fade used five stable entries, at most 454,080 bytes; fade
  added no entries. Existing budgets remain 48 entries / 64 MiB for control
  layers, 13 SVG renderers, 88 size/tint rasters capped at 256 px.

All generated audio, previews and logs stay in ignored runtime directories.
Online account playback after this repair and physical group recovery remain
unverified. The source app must restart normally to load the changes.
