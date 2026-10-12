# Device control redesign

October 12, 2026 follow-up for GluSpot `6.412`. Version and the packaged EXE
remain unchanged. The implemented panel has completed native DPR 1/2 previews,
focused high-DPI regressions and the final full-source test suite.

## Observed problem and approved scope

The user supplied a running-device-panel screenshot and requested a fresh
assessment of both interaction and presentation. The assessment checked that
image against `local/audio_output_panel.py`, the shared glass/hover helpers,
`api/audio_routes.py` and the preceding output-interaction documents. The user
then approved all three repair areas in this order: presentation, volume
interaction, and truthful route state. The existing solo/mute/join commands
remain the product contract.

The screenshot's uneven scale has a concrete layout cause. The old popup width
and row height followed the calling device button's height; the device-name
font then followed the row height. Volume text and device glyphs retained fixed
sizes. A large source button consequently produced tall rows and large names
with much smaller volume numbers. The old painter also filled a portion of the
entire selected row in proportion to volume, clipping repeated dark text and
glyphs over the lighter part. That boundary could split a device name into two
colors. It conflated volume, selection and material in the same surface.

The audit also found that the existing solo and join actions had independent
native hit targets, but little visible local feedback. A low-volume selected
row and a retained stopped-status footer did not clearly distinguish selected
output from current playback. These are UI findings; a screenshot alone does
not establish whether an endpoint is audibly producing sound.

## Presentation contract

The final popup uses a nominal **360 logical-pixel width**, clamped to the
available viewport. A small calling button uses the same 360-pixel layout as a
large calling button; only the initial morph follows that source circle. Its
placement and morph contain the calling circle. Device rows use **72 logical
pixels for confirmed active outputs** and **56 for inactive outputs**, rather
than scaling with the calling button. Logical sizes continue to follow Qt's
display scaling.

Names use a fixed 14-pixel font. Hovering anywhere in a confirmed active row for
0.5 seconds smoothly reduces its name to 12 pixels; leaving reverses that
animation. Inactive devices retain their original name size. Source-button
morphing, bounded row hover motion and keyboard focus remain separate from
these layout sizes.

The selected row no longer paints its volume as a white portion of the entire
capsule. Its name and device glyph retain one contrast color. A dedicated
horizontal track and a readable percentage communicate gain; volume no longer
changes the text's color at an arbitrary point. The row's existing `subtitle`
is visible, distinguishing current output, synchronized playback, connecting
and unavailable states. Device identity and glyph kind still come from the
provider, with long names elided visually and retained in accessible metadata.

The material continues to use the existing original face and fine directional
glass rim. Inactive faces remain transparent; a confirmed face uses a slight,
uniform contrast tint independent of volume. It does not add another glass
backing, change the shared material helper or reintroduce the removed Settings
elastic effects.

## Interaction and volume evidence

| Device row | Device icon | Name/body | Right-hand join control | Volume track |
| --- | --- | --- | --- | --- |
| Confirmed output | Select this output alone | Mute or restore | Not shown | Adjust this output's actual gain |
| Available output | Select this output alone | Select this output alone | Add to synchronized playback | Not active |

Solo and join retain distinct native accessible targets, with accurate action
tooltips and local hover/focus feedback. The selected row's normal accessible
activation remains mute/restore. Dragging the volume track cannot become a
solo, mute or join action when the pointer moves outside the track.

A requested slider position is an **intent**, not an observed endpoint level.
While readback is pending, the value is explicitly labelled `目标 78%`, for
example, and the row says `调整中`; its retained actual device level can still
be 14%. Matching confirmed readback clears the target label and displays the
actual `78%`. Command acceptance alone does not confirm that level.

The latest request has a two-second confirmation deadline. Once the held drag
is released, snapshot maintenance clears a matching request or returns an
expired request to the confirmed value without reissuing the command. An
expired or rejected request shows `未确认` for a further two seconds; immediate
rejection also retains the actionable panel error. Unknown receiver volume is
`—`, with no invented percentage or enabled scrubber. Muted confirmed volume is
zero: a raw endpoint scalar of 50% while the endpoint is still muted cannot
confirm a requested audible 50%. A matching unmuted readback is required for
that positive target.

The existing provider owns absolute per-device percentages, acknowledgement,
readback and mute restoration; the panel does not introduce another output
gain or reconnect a healthy route merely to adjust volume.

Wheel input remains list scrolling. A held slider gesture keeps its stable
coordinate frame and consumes wheel input until release. Native keyboard
adjustment, held-drag order/readback protection, close cancellation and bounded
transformed hit-testing remain part of the existing interaction contract.

## Route and playback state

UI command acceptance alone does not confirm selection, membership, connection
or sound. Confirmed members and pending targets come from the existing
`AudioRouteSnapshot`; selection cannot be inferred from the last clicked row.
Current output is a route designation, not proof that music is playing or that
a physical receiver is audible.

Normal route subtitles stay within their own rows. Pending operations and
errors remain visible without allowing stale routine success/stop messages to
compete with the current device list. Device failures and provider read errors
remain actionable and accessible. Healthy confirmed routes remain intact while
newcomers are pending. A member-level `error`/`failed` state renders a failure
subtitle and its `route_message` in the tooltip without unchecking confirmed
members or issuing a route command.

Program-owned groups containing several Windows devices are recognized as
synchronized playback even when there is no AirPlay receiver. Their stop
control remains available. After a warm membership reduction leaves one owned
renderer, the existing nonempty `group_id` still owns that renderer lifecycle;
the panel retains its stop action instead of treating it as an ordinary
single-output route. These fixes read existing `AudioRouteSnapshot` fields;
they add no API or coordinator behavior.

## Gesture ownership across route changes

The panel fences interaction by the published owner tuple
`(capture_scope, music_source_app_id, group_id)`. A source, controller scope or
renderer-epoch change cancels old volume intents and pressed row/solo/join/stop
targets, clears transformed pointer capture, and discards the old held move
and release sequence. Clearing a native button's pressed appearance alone is
insufficient: a held move could otherwise re-arm it before release.

Every route-action entry point cheaply reads the published snapshot before
sending a command, so an owner change between the 350 ms polls cannot send the
old gesture to the new owner. This read performs no discovery or device I/O.
A fresh gesture after cancellation acts on the new current owner normally.

Healthy member additions within the same owner and renderer epoch do not
cancel a held volume drag. The captured transformed pose, slider coordinates
and pending target stay intact until ordinary release, while membership still
updates from the provider. Selection or membership changes are not themselves
treated as owner changes. The repair preserves the existing audio coordinator,
warm join lifecycle and synchronization policy.

## Verification and limits

Before implementation, the root task captured **eight native screenshots** and
ran the existing focused baseline of **108 tests**. These establish the
pre-change reference only.

The final redesigned native previews completed **36 frames and 318 checks at
DPR 1**, and **36 frames and 318 checks at DPR 2**: **72 frames and 636 checks**
in total. The actual native widgets use synthetic provider snapshots; no
physical devices, accounts, volumes or audio sessions are changed. The light
and dark captures cover small/large calling buttons, selected hover, target
versus confirmed gain, 0/14/100% volume, mute/restoration timeout, pending join,
confirmed synchronization, member failure, long/scrolled lists
and a narrow viewport.

Final source verification passed:

- `python -X utf8 -X faulthandler -m unittest discover tests -v`:
  **4456 tests in 226.075 seconds**, no skips or Qt callback tracebacks.
- At `QT_SCALE_FACTOR=2`, the panel/hover/island/volume/provider/glass suites:
  **275 tests in 24.148 seconds**. The final album-page integration and three
  corrected Tasker geometry fixtures additionally passed **10 tests in
  1.672 seconds** at DPR 2 and **1.623 seconds** at DPR 1.
- **22 new usability regressions** cover fixed dimensions, target/readback
  distinction, mute restoration, rejection/timeout, uniform pigment,
  contrast, keyboard actions, owned group stop, member errors, real held
  slider/mute/solo/join/stop cancellation, changes between polls and retained
  same-session joins. The final source/test hashes were fixed for verification.
- Python compilation, dependency consistency and `git diff --check` passed.

Detailed generated evidence stays in ignored paths:
`logs/device-redesign-full-verified.log`,
`logs/device-redesign-integrated-dpr2.log`,
`logs/device-redesign-integrated-fixtures-dpr2.log`,
`logs/device-redesign-final-source-hashes.json` and
`.runtime/device-control-redesign/dpr-{1,2}/steps.json`.

The first full run also exposed three pre-existing geometry fixtures that
constructed an island through `__new__` and read real Windows auto-hide state.
They failed identically with the committed panel/hover loaded in a fresh
process. Those geometry fixtures now explicitly disable both auto-hide
probes, as their neighboring fixtures already did; production Tasker/Finder
code and the separate genuine auto-hide tests are unchanged. Old pale
volume-fill assertions now compare the actual native panel handoff and
separate gain evidence instead of requiring the retired white fill.

Generated screenshots, fixture data and test logs stay in ignored runtime/log
directories. No new physical Bluetooth, AirPlay or multi-device listening
result is claimed by this document. Automated snapshot and native widget
checks cannot establish actual speaker audibility, acoustic synchronization or
long-session hardware reliability. Restart this branch's `launch.bat` to load
the source changes after final verification; the old EXE is unchanged.

The earlier behavior and hardware boundaries remain documented in
[output reliability](playback-output-reliability.md),
[FM preparation and output interaction](fm-preload-and-output-interaction.md)
and [audio routing](audio-routing.md). This document supersedes their earlier
row-scaling and full-row volume-fill presentation details for this follow-up.
