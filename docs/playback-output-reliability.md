# Playback and output reliability — October 11, 2026

This source change implements the requested music-library return control, output-row interaction, owned-player group reconfiguration and song-ending repair. Product version remains 6.412; the previously packaged executable is unchanged. Restart this checkout's source application to load the Python changes and rebuilt native helper.

## Music-library return control

The song page uses an original rounded-stroke return arrow beside “音乐库”. The supplied library SVG remains preserved with its original provenance. The left control now has its own damage-region repaint: previously only right-hand controls could trigger foreground repaint, so a partial ripple/background repaint could erase the left button. Its face also occludes spatial ripple light. The glyph and spacing follow the surface scale.

## Device interaction

The same existing provider boundary serves owned playback and system capture. Device-row actions are:

| Row | Device icon | Name, body or volume area | Right-hand plus |
| --- | --- | --- | --- |
| Confirmed output | Select this output alone | Mute or restore | Not shown |
| Available, unselected output | Select this output alone | Select this output alone | Add to synchronized playback |

Hovering anywhere in a row for 0.5 seconds reveals its smaller, uniformly sized name and a volume glyph plus actual level sliding from the right. Muted volume is zero; an unknown receiver level is “—”. Long names elide visually while retaining their full accessible name and tooltip. Hovered selected rows with known volume accept wheel changes across the whole row, including at zero. Other wheel events continue scrolling the list. Keyboard and accessibility actions retain separate solo, mute and join commands.

Mute restoration remembers only observed positive endpoint/receiver scalars. If no usable positive level was observed, restoration requests 30%. History and pending user intents are bounded to 64 identities; intents expire after two seconds and are fenced by the current route/source generation. An unacknowledged command never becomes displayed actual volume. Explicit positive volume or restoration may unmute a selected Windows output; unrelated outputs and a system group's spare capture endpoint keep their mute ownership. Volume commands reuse the running sender instead of reconnecting it.

## Owned synchronized playback

Previously every membership change stopped the helper, released direct PCM and rebuilt the entire group. Receiver teardown and authentication therefore interrupted the old group and reset its source frame timeline.

A capability-confirmed helper now accepts a bounded, authenticated, revision-fenced membership command over its existing stdin. The PCM pipe, source epoch, shared history, retained receivers and retained Windows renderers continue. New members prepare asynchronously and join a future packet-aligned source frame. Their relative source frame, RTP/NTP schedule and local-history offset refer to the established global timeline. Pending members do not replace confirmed membership or reset the old group's lyric compensation. A failed newcomer is rolled back; the established group remains selected. Removal requests stop the removed transports and retire their potentially blocking cleanup off the packet thread. Active and still-retiring transports share the eight-output budget.

Solo selection can keep one Windows output in the native group so its timeline continues. Further clicks during preparation retain the latest next selection; a later solo selection supersedes queued joins. Source changes and seeks revoke the previous PCM epoch and restart using the current intended selection. Initial preparation cannot be presented as a confirmed playing group. Receiver volume commands use exact identities, avoiding index changes when a group is reordered.

For the first direct-Qt-to-group handoff, the new helper advertises a presentation capability. Qt continues its current output while PCM fills the synchronized schedule; private Qt mute commits only after meaningful PCM and the helper's declared presentation boundary. Older helpers retain the compatible restart/immediate-mute path. This reduces silent setup gaps; it does not remove the approximately 1.9-second initial presentation reserve or guarantee a sample-continuous acoustic transition. A buffered handoff may repeat a short segment. Receiver audibility, Bluetooth latency and physical speaker alignment require listening measurements. System capture still uses its established source/mute migration lifecycle; owned warm reconfiguration does not imply uninterrupted system-capture switching.

## Song-ending repair

Existing logs contain repeated FFmpeg I/O errors. A local HTTP fixture reproduces a relevant failure: a 4-second WAV response omits its final 0.1 second while declaring the complete length. Qt emits repeated ResourceError notifications, remains PlayingState and stalls around 3808/4000 ms without EndOfMedia. The previous error handler immediately erased lyrics, published paused playback and invalidated the stream epoch, preventing normal mode routing.

Active network/resource errors now retain established lyrics and observe the actual Qt decoder clock. Only with a known decoder duration, at least one second without real forward progress and a position within the final 500 ms does the player use the existing EOF path. Repeated error notifications and clock jitter do not restart the observer. Sequential list, shuffle, single-song and personal-FM behavior retain their existing source authorization and queue logic. Lyric timestamps are never evidence that audio ended.

Mid-song, unknown-duration, format and access failures remain bounded failures: Qt explicitly pauses and lyrics/position remain available. Clicking Play after a terminal stream failure reauthorizes the same valid queue song; ordinary pause/resume retains its stream. This is an explicit retry, not an automatic retry loop. Pause, seek, new source, stop, close and stale authorization/lyric tasks fence the observer. The reproduced fault explains one real failure path; it does not prove every online near-end failure has the same cause.

## Verification

Tests and previews use synthetic account/device state and local HTTP/PCM fixtures. Generated logs, runtime evidence, build output and account data remain outside source control.

The final frozen Python source passed all 3835 tests with no skips in 199.523 seconds using `.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest discover tests -v`. The final DPR2 panel/navigation/surface pass passed 120 tests in 19.381 seconds; normal-DPI panel/motion integration passed 109 tests in 17.843 seconds, and the whole suite includes normal-DPI library damage/ripple tests. Python compilation, dependency consistency and staged diff checks passed. Native helper sources were rebuilt and the final CTest run passed all 12 suites in 46.48 seconds.

The real Qt fault pass uses `tools/smoke_owned_stream_faults.py --scenario all`: ten scenarios and 177 checks passed, including normal EOF and truncated tails across all three playback modes, mid-song failure, pause, seek and manual retry. Device-panel native previews cover dark/light themes, compact/reference widths and DPR 1/2. Actual damage-region and ripple-occlusion pixel tests verify the library control separately from the right controls. Native protocol tests verify shifted late-join PCM/RTP/NTP and local-history contracts without contacting a physical HomePod.

The complete actual-helper pass uses `tools/smoke_owned_pcm_loopback_membership.py`, with the test-only CMake target `gluspot_owned_membership_receiver_fixture`. Independent localhost receivers perform strict SRP, acknowledge encrypted events and decrypt final audio. All 22 checks passed in 6.907 seconds: A → A+B → B alone → B plus a bad-SRP newcomer. The native process, authenticated named pipe, writer, PCM queue and epoch 73 remained the same. Source and transport frames increased without resetting; common global source frames had identical decoded PCM hashes and aligned shifted RTP/NTP mapping within one NTP tick. Receiver A received teardown while B continued; the bad-auth receiver received no audio. This end-to-end check also caught and repaired Windows CRLF command framing, which pure parser tests had not exercised. It verifies protocol continuity, not physical speaker output.

The existing real Qt library/player/application smoke passed 78 checks (64 library and 14 application-constructor checks). This includes muted fixture playback, provider clocks, lyrics, navigation and cleanup using isolated settings.

`tools/smoke_owned_pcm_membership.py` requires an already-silent Windows output and does not change its default, mute or volume. The current read-only inventory had no suitable silent output, so this actual-Windows membership pass could not establish physical continuity. Physical multi-device listening, long-session drift and online long-session recovery remain unverified.
