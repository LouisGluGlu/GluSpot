# No-lyrics cover fallback and resize boundaries — t238

Source-only change on `grok/capture-narrow-v4`, starting at `bb060b7`.
The existing cover-only composition is reused. No version, package, provider,
background style, push or PC operation is part of this change.

## Reproduced causes

The supplied screenshots show an instrumental track with bars over its centered
cover at 883 × 540, and an unused lyric column at 610 × 540. A controlled fixture
through `api.providers.EmptyIslandDataProvider`, the real island owner and the
release narrow-live surface reproduces both from the exact starting source.
The fixture publishes the existing `纯音乐，请欣赏` sentinel; the actual provider
payload from the user's session is unavailable.

- At the starting commit, `local/music_surface_layout.py:382` recognized empty
  and absent-lyric placeholders, but counted the pure-music sentinel as usable
  lyrics. Meanwhile `local/music_surface_window.py:2486` centered sparse rows.
  The wide painter could therefore center the cover while retaining the
  external lyric mode; narrow still mounted its small-cover lyric composition.
- Starting `local/music_lyric_resize.py:330` used `force=True`, bypassing the
  loading gate even for terminal or already loaded tracks. Its skeleton path
  also bypassed the live painter's artwork exclusion. Width motion could draw
  bars across a moving/centered cover. This originates in `dde98b2`, rather than
  the diagnostic work in `386b441`.
- Starting `local/music_surface_layout.py:404` offered all four manual modes
  during a miss, and automatic restoration returned the saved mode. It did not
  implement the four authoritative t239 transition rules.
- Starting `local/music_surface_window.py:10303` keyed chrome by the saved
  layout. Its default prewarm also advertised completed icon animation while
  drawing the current pose. An automatic restore could reuse the cover glyph
  for an external-lyric layout. Final real-button crops verify the correction.

The existing degrade path was found using `git log -S
'_sync_no_lyrics_content_layout'`: `8caf672` introduced it and `79c0f90` refined
quiet same-track recovery. Full-height `ec89693` feathers lyric ink using an
offscreen DestinationIn mask; it is not a background/cover mask. The fix keeps
those edge feathers for real lyrics and skips the entire lyric layer in cover.

## Final behavior

`MusicSurfaceWindow._lyric_content_state` explicitly distinguishes idle, loading,
loaded and no lyrics using the existing provider placeholders and instrumental
classifier. Terminal misses, pure music and failed preparation stop the loader;
a pending lookup has the established one-second delay and a 30-second visual
deadline. Provider retries continue independently and late lyrics are accepted.
Loaded results end the loader immediately.

The existing `MusicContentLayoutMixin._sync_no_lyrics_content_layout` implements
the same policy at every width:

1. A no-lyrics track automatically uses cover-only until a manual layout click.
2. Following unresolved/no-lyrics tracks retain that choice. Usable lyrics
   restore external lyrics.
3. During that run, manual clicks toggle cover and list only.
4. On lyric arrival, manual list remains list; manual cover restores external
   lyrics. Automatic transitions never save settings.

Geometry and button caches read the effective layout. A terminal result discards
stale lyric snapshots. Cover/list bypass lyric ink and lyric masks entirely.
External live ink, loading bars and frozen endpoints exclude this frame's
artwork. Loaded lyric/list resize frames retain bounded snapshots at their
original glyph size, then blend to the settled live endpoint. Steady snapshot
painting reuses the image without another frame-sized allocation. Cover-inside
ink/shade is suppressed during width handoffs.

## Gray-field investigation

The classic background renderer independently reproduces the gray fields from
the screenshot's cover crop, without any lyric or frosted layer. The crop's
approximate support is cyan 76.20%, white-petal neutral 21.53%, mid-gray 1.97%.
`music_surface_palette.py:599` classifies low chroma as neutral, `:454` removes
its residual chroma, and `music_surface_background.py:104` maps its brightness
for foreground contrast. That renderer softens domain boundaries while retaining
opaque pigment cores (`:285`). Unused slots correctly reuse the dominant base.
Unrelated red/yellow test fallbacks never appear.

No palette/frosted/fallback bug was confirmed, so the established gray-source
behavior was retained. Original provider art/palette and exact animation phase
remain unverified; the crop is a reconstruction, not an original album asset.

## Evidence and verification

Box evidence is under `/workspace/gluspot/handoff/t238/`. `FINAL.md` records the
commits, full-suite counts and identity comparison against t234. `compare.png`
contains before | after instrumental frames at 960, 883, 690 and 610 × 540.
`after/layout-icons.png` pairs actual button crops with the layouts and transition
branches; `loading-sequence.png` and `loaded-sequence.png` show the other states.
`RENDERS.md`, structured frame reports, source/image hashes and background
diagnosis preserve the reproduction and its limits.

The focused boundary/state/resize run passes 36 tests; the final correction run
passes 38. Another 142-case related run passes. Background revalidation passes
79 tests with two existing skips. The requested three-worker isolated command
covers 342 modules / 6,186 tests in 251.835 seconds. Its 54 failure / 48 error /
32 skip identities exactly match t234: zero new or removed identities, with
unchanged source hashes during the run. This is baseline equivalence, not a
green suite. Three native-crashed modules were completed through the supplied
runner's ordinary per-test subprocess supplements, including fullscreen; t234
had needed supplements for the two owned-audio/artwork modules.

An additional external unittest covers six combinations (960/610 starting
widths × automatic/manual-cover/manual-list) and 12 track-result arrivals while
real Qt edge-drag events still hold the gesture. It passes in 1.425 seconds,
including icons, automatic settings zero writes and snapshot cancellation.
Its script, 12 images and measured arrival widths are in `t238/transition-drag/`;
the exact 960/610 raster proofs remain in the main render matrix.

The fullscreen module's additional native crash occurs in a lifecycle test's
`QApplication.processEvents()`. The same final module completed in a separate
focused run; one ordinary baseline `bb060b7` run also completed without crashing
and retained the same seven failures/one error. These samples do not establish
that the current crash is inherited or resolved. Individual supplements all
completed, but Qt native lifecycle stability remains an explicit uncertainty.
The baseline check and current crash stacks are retained in the handoff.

The first full run exposed a cold `__new__` provider fixture reading the new
state before constructor fields existed; the classifier now safely probes that
initial key. It also exposed a cadence-budget test depending on real cold-start
tick duration. The original `bb060b7` fails that isolated test too. Its clock is
now fixed like the adjacent ordinary-lyric budget fixture; production cadence
policy is unchanged. The first report and both baseline/final focused logs are
retained in the handoff.

未验证：Windows native mask/compositor behavior, physical drag timing, the user's
actual player/provider/retry outcome, original high-resolution artwork,
desktop frame rate and long-session playback. No PC was touched.
