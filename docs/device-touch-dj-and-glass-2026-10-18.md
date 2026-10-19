# Device controls, HomePod touch, initial DJ and glass — 2026-10-18

Version remains **6.412**. The separately dated modern Windows x64 installer
and portable archive now include these changes. See the [release artifacts,
verification and usage](windows-release-device-dj-glass-2026-10-18.md); previous
dated packages are preserved.

## Requested behavior

- In a synchronized group, the existing selected device icon removes that
  device through the provider's existing membership toggle. It no longer
  switches the whole group to that device alone. Device-name clicks still
  mute, and the volume slider still controls its own device. The group-wide
  “取消同步播放” action is removed; the established single-AirPlay stop action
  remains. Snapshot and held-gesture checks prevent a removed member being
  rejoined, or a held removal becoming a solo-selection after a group change.
- HomePod play/pause commands return over the existing authenticated AirPlay 2
  event channel, then invoke the owned player's existing toggle on Qt's thread.
  This applies to owned direct PCM for both a single receiver and a group.
  Encrypted ACK, receiver/session/member authority, sequence deduplication and
  expiry checks are retained. This implements a supported protocol path;
  physical HomePod touch behavior is still awaiting hardware acceptance.
- Enabling DJ and explicitly entering personal FM prioritizes the first real
  official introduction and its matched song. Leading ordinary songs are
  retained after that pair; continuation batches preserve the platform order.
  An initial pending FM request is reissued when the DJ preference changes,
  with the previous generation fenced off. Toggling DJ during an already
  playing song preserves that song and applies to the next batch. No official
  introduction is invented when the service supplies none.
- Window and fullscreen function buttons, connection capsules, collection
  actions/chooser, DJ controls and device gain tracks/thumbs share scene-colored
  glass. Playback glyph bodies acquire the material within their existing
  outlines; no enclosing transport plates or new playback controls are added.
  Device sliders retain pointer, keyboard and wheel behavior and use a short
  rounded capsule thumb.
- Basic settings exposes one saved **播放控件玻璃浓度** slider, 0–100%, default
  50%. The user's corrected direction is authoritative: **more opaque means
  stronger mapped-background blur; more transparent means weaker blur**.
  Labels and icon edges remain sharp. Open panels update immediately, and
  control-hide caches are invalidated and prewarmed again without resetting
  the user's idle timer. The same whole-scene black/white decision accounts
  for the shared theme veil, keeping every control in agreement and readable
  as the material strength changes. Song changes retain color interpolation.

## HomePod queue boundary

Touch control is separate from HomePod's native music queue. pyatv demonstrates
an MRP tunnel and queue-state messages, but this is not evidence of a complete,
accepted arbitrary-song queue-injection contract. No native queue read/write
feature or simulated queue is presented here. See the pinned upstream links,
wire semantics, implemented fences and physical acceptance checklist in
[the HomePod investigation](homepod-remote-control-research-2026-10-18.md).

## Verification

The final native Windows Qt regression passed **5252 tests in 311.016 seconds**,
with no skips, failures or Qt callback tracebacks. All **334 Python source/test
hashes** remained unchanged during that run. Log:
`logs/device-touch-dj-glass-final-full-2026-10-18.log`.
Focused verification passed **345 tests at DPR 2 in 43.098 seconds**, following
342 tests at each DPR 1/2 before the final contrast/held-gesture refinements.
The final DPR 1 focused rerun passed 124 tests; the complete DPR 1 regression
also covers the final source. DJ verification passed 267 player/UI checks and
63 service/parser checks. HomePod process/controller checks passed 31 tests,
its Qt bridge/controller checks passed 67, and all **12 native suites passed
in 109.40 seconds** after rebuilding the helper.
After correcting the legacy whole-group-stop expectations and sharing the
transport's existing ink decision in its material cache key, 19 integration
checks passed at each DPR 1/2. These include the queue selection face's theme
and strength, authoritative member removal in a scrolling mixed-device list,
and the unchanged three transport hit slots with no enclosing plate.
The focused material tests inspect real raster alpha, Gaussian detail reduction,
immediate cache invalidation, bounded allocations, persistence, whole-scene
contrast and scene pigment inside pause bars without a background plate.
Interaction regressions include selected-icon removal, name mute, volume-only
gestures, stale snapshots and a membership change while a press is held.

Native Qt screenshots use in-memory test providers and the production renderer.
They do not start audio, access accounts or connect receivers. Each DPR 1/2 run
renders 24 combinations: window/fullscreen × light/dark × material 0/50/100 ×
two identifiable color scenes. Both runs completed without Qt callback errors.
The final frozen-source rerun additionally saves the actual basic-settings
page at each DPR; the total is **48 player/device states and two settings
screenshots**, with matching production-source hashes and zero callback errors.
Snapshots, control geometry and warm grab timings are retained under ignored
`.runtime/glass-device-2026-10-18/`; timings describe hidden-widget rendering,
not physical display smoothness or HomePod acoustic behavior.

No login session, receiver, system volume, installation or existing release
artifact was changed for these checks. Physical HomePod tap/resume behavior,
long pauses and network/account narration availability remain unverified.

Readiness process score: **100/100 for this source handoff**. Interfaces and
call sites were checked, existing provider/bridge/UI boundaries were reused,
the user's corrected linkage direction was applied, identified gaps were
corrected before final verification, and hardware limitations are explicit.
Binary verification is recorded separately in the release report above;
physical HomePod behavior remains unverified.
