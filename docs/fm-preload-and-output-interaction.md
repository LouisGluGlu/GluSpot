# FM preparation and output interaction

October 12, 2026 follow-up for GluSpot `6.412`. Final source verification passed.
Version and the packaged EXE are unchanged.

## DJ activation

When DJ is enabled, clicking its compact control opens the voice selector;
hovering still opens it after 0.5 seconds of decelerating enlargement. Once the
selector has finished expanding, clicking the rings switches DJ off. A press
that began on the compact control cannot become a toggle merely because the
hover expansion completes before release. The caption/voice-choice region does
not toggle DJ. Clicking disabled rings enables DJ and opens the selector in the
same action. The two-second pointer-leave grace and genuine official auditions
remain unchanged.

## Personal FM preparation

The preceding single prefetched-file slot was tied to the current prepared
music epoch and cleared by navigation. Recommendation reserve also counted
speech parts as songs. Both could leave the following introduction unprepared
after manual Next.

DJ now prepares an independent rolling speech window as soon as the current
song is selected, before music authorization or playback completes. Manual Next
retargets the window immediately, including rapid consecutive skips. Pausing
and single-song repeat keep preparation running without advancing playback.
Recommendation lookahead counts actual upcoming songs, aiming for the existing
three-song reserve. Ordinary playlists and DJ-disabled FM retain their existing
prefetch policy.

The window keeps at most two prepared future speech files. Current multipart
speech's next part and the following song's first genuine introduction take
priority; completion refills the remaining parts. An in-flight future download
can become current speech without a second download. Prepared-file leases are
released when no current or future owner retains them. This preserves the
existing complete-file cache limits of four files / 192 MiB, two simultaneous
downloads and 64 MiB per file. Long introductions with more than two parts use
rolling preparation rather than an unbounded download of every part.

A cancelled older download may briefly retain a cache slot. New preparation
retries only the cache's temporary `busy` result for up to three seconds in its
cancellable worker; it does not sleep on the UI thread or hide other download
faults. A single 150 ms timer retries rejected background submissions. Account,
source, queue, voice, toggle and close boundaries cancel stale work and release
late results. Voice changes preserve the current genuine introduction and its
remaining parts, including downloads already promoted to current speech; only
future recommendations use the new voice. A pending replacement catalog also
retains healthy current music/speech until its own result wins. Existing
expired-URL checks remain. A service recommendation
without genuine speech remains in official song order without fabricated DJ
commentary; network/service failure cannot guarantee an introduction.

## Device interaction

The supplied screenshot showed row enlargement outside the small popup and
little room between content and its footer. The popup now reserves six-pixel
content gutters and nine-pixel row gaps. Active rows are at least 64 pixels tall
and available rows at least 48 pixels tall; larger source controls scale them
proportionally. Row growth is bounded at 104%; transformed pixels
and inverse pointer mapping share the viewport/content bounds. A partially
visible row retains its ordinary pose. Healthy successful state no longer adds
a redundant instructional footer; pending operations and errors remain visible.

Wheel input scrolls the device list without changing gain. During a held volume
drag, wheel input is consumed without scrolling so the drag coordinates remain
stable; scrolling resumes on release. Each active row has an explicit
horizontal volume track for pointer dragging and native keyboard adjustment.
The actual percentage and volume symbol remain visible for discoverability;
whole-row half-second hover smoothly reduces active-row typography to 12 pixels.
Name/speaker clicks retain mute/restore, the device icon retains solo, and the
available row's plus retains
join. Unselected rows retain their font size.

The actual child target and its transformed pose are retained throughout a
press/drag, so dragging beyond the slider does not become a row mute/solo click
or jump when hover changes. Snapshot volume readback and list reordering remain
deferred while dragging. Closing/hiding the popup cancels the held slider and
row press without emitting a mute/solo action. Departing rows hide before deferred deletion so an
empty-list rebuild cannot briefly draw duplicate controls. This change does not
replace the audio coordinator, rebuild healthy playback or alter device
synchronization policy.

## AirPods noise control

The [read-only Windows feasibility report](airpods-noise-control-research.md)
documents Apple capabilities, fixed public source revisions, private-protocol
mode commands and real state confirmation. Public Windows driver routes exist,
but current deployment requires separate driver/service support with signing
and compatibility constraints. No ANC/transparency/adaptive runtime,
installation, system-security change or physical mode switch was performed.

## Verification

The frozen final source passed **4244 Python tests in 251.738 seconds** with
normal native Qt discovery, no skipped tests and no Qt callback traceback:

```powershell
.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest discover tests -v
```

All twelve changed/new Python paths match the hashes captured before the final
full run. Python compilation, dependency consistency and diff whitespace checks
passed. Version/packaging inputs are unchanged; there is no new EXE build.

- Playback/prefetch focused suites: **271 tests, 12.223 seconds**, including 21
  new prefetch regressions. They cover early and rapid Next, pause/repeat,
  multipart file/pin bounds, genuine song reserve, pending-current voice/catalog
  ownership, callback expiry, reentrant replacement and shared-timer retry.
- Device/shared-hover/glass focused suites: **135 tests at each DPR**, in
  **18.958 seconds at DPR 1 / 15.999 seconds at DPR 2**. Real pointer tests cover
  transformed dragging beyond the popup, mute/solo/join isolation, keyboard
  adjustment, delayed readback/reorder, close/reopen cancellation, held-drag wheel
  freeze, deleted-row visibility and per-pixel viewport clipping.
- DJ click/voice/hover suites: **52 tests at each DPR**, in **2.123 seconds at
  DPR 1 / 1.882 seconds at DPR 2**. These use real surface handlers and animated
  geometry, including compact press followed by expansion before release.
- Actual local HTTP → existing complete-file cache → muted native Qt validation:
  **30 checks, 4.609 seconds**. A deliberately slow music response proves next
  speech prepares before current music completes. Actual PlayingState, clocks,
  lyrics, pause/repeat, fast skips, multipart voice changes, 20% gain and
  one-second restoration are checked. The final real cache held four entries,
  two leases and 1,526,036 bytes, with thirteen local HTTP requests. Shutdown
  left zero leases and no retry timer.
- The existing native dual-decoder/group/multipart acceptance script also passed
  **101 checks** earlier in this follow-up. Submitted mixed PCM matched actual
  decoded samples exactly (sample match 1.0 / maximum delta 0). No physical
  receiver or device route was operated by either script.
- Ten fresh device screenshots cover two-device idle/hover, empty/default output,
  small long lists and partial rows at DPR 1/2. Final images were generated at
  15:07:17 / 15:07:19 on October 12. Native and partial-list previews were
  inspected; the earlier deleted-row double glyph/border is absent.

The first ordinary discovery completed 4244 tests with one error in an older
provider-spectrum fixture: its service double omitted `personal_dj`, which early
lookahead now correctly requests. The fixture now supplies that existing
interface; its provider/prefetch follow-up passed 29 tests in 0.184 seconds.
Production behavior and assertions were not weakened.

A preliminary fast-skip native run stalled in Qt media initialization without a
completed result and was terminated. The unchanged production source's final
local acceptance completed normally with a traceback watchdog; no diagnosis of
that single native startup stall is claimed. An earlier multipart harness check
also sampled source assignment before Qt confirmed PlayingState; the final check
waits for actual speech playback and retains the song/state assertions. These
short, muted fixtures do not establish long real-account session reliability,
physical Bluetooth/HomePod behavior or desktop compositor smoothness.

Generated audio, research clones, screenshots, watchdog scripts and logs remain
outside Git. Restart this branch's source app to load these changes; the running
instance and packaged EXE are not automatically updated.
