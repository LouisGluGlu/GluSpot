# GluSpot visual decisions

The three choices are now the live defaults. Nothing changes the official
playback mode, buttons, glass grouping or timing curves. The Apple sample
remains a standalone demo.

Regenerate three Chinese comparison sheets with real QWidget painters:

```bash
QT_QPA_PLATFORM=offscreen /workspace/gluspot/qtvenv/bin/python _demos/par_decisions/render.py
```

The default output is `/workspace/gluspot/handoff/par-decisions/`; use `--out`
to choose another directory outside source control. No media player or network
connection is started. Runtime settings paths are temporary. The photograph
is the existing `_demos/narrow3/covers/night_sea.jpg`; sample text comes from the
existing narrow and Apple demos.

## Cover growth targets

`local/music_surface_layout.py` exposes `LAYOUT_G_COVER_GROWTH_MODE="current"`
and the optional `layout_g_targets(..., cover_growth_mode=...)` keyword.

| Mode | Requested gain | Hidden width at 960×540 | Tradeoff |
| --- | ---: | ---: | --- |
| `none` | 0% | 237.60 px | Stable artwork size |
| `current` (adopted) | 30% | 263.94 px | About 11% after the lyric lane cap |
| `large` | 50% | 299.56 px | Moves left to make room inside the same lyric boundary |

The original gain is capped by the available lane, so it is about 11% at this
size. Large mode moves left toward `max(8,1.2u)` (never right of the original
left), retaining a `1.35u` gutter before `44.6u`. Its width/height caps are
`0.46w` / `max(resting,0.66h)`; current keeps `0.40w` / `max(resting,0.56h)`.
All modes retain the existing 18% title gain and identical visible-control
geometry. Larger mode changes geometry linearly with the existing expansion.

The official side-lyric cover now follows this gain while the controls hide.
Visible controls keep the resting cover. The title lane keeps its width and
slides down so it stays under the artwork. The glass transport card stays on
the resting boxes and still slides off on its own. The lyric column keeps the
old 44.6u anchor. `item1-cover-growth.png` shows the adopted page under the
older comparison targets.

## Broadcast face bodies

`local/broadcast_indicator.py` exposes `BROADCAST_FACE_BODY_MODE="accent"`.
The color helper accepts `mode=...`; both shared face/indicator painters accept
`body_mode=...`. Omitted arguments resolve the named constant.

| Mode | RGB rule | With theme (92,158,232) |
| --- | --- | --- |
| `current` | 72% (10,10,12) + 28% theme; original 0.22 luminance gate and 84/16 fallback | (33,51,74) |
| `accent` (adopted) | 28% (10,10,12) + 72% theme; if luminance >0.18, scale linear-light RGB to 0.17 | (69,117,170) |
| `warm` | Fixed warm charcoal | (112,100,92) |

Eyes remain `(245,246,248)` in every case. The photo and white-cover rows of
`item2-face-body.png` show speaking and idle at the same 1.2-second clock.
The older near-black mix can blend into very dark art. The adopted theme body
is the clearer silhouette. No expression or face-motion change.

## Apple sample presets

`_demos/apple_lyrics/timeline.py` exposes immutable `APPLE_KNOB_PRESETS`,
`APPLE_KNOBS_DEFAULT_PRESET="current"`, and `AppleKnobs.from_preset(name)`.
The adopted default is neighboring lines at 88%, blur 2.2, and 350 ms lead
and scroll. `AppleKnobs()` keeps those dataclass defaults. The sample is still
not wired into the official player.

| Knob | `current` | `soft_close` | `dramatic` |
| --- | ---: | ---: | ---: |
| block_gap_ratio | 1.75 | 1.20 | 2.35 |
| translation_gap_ratio | 0.25 | 0.12 | 0.40 |
| other_scale | 0.88 | 0.95 | 0.80 |
| blur_px | 2.2 | 1.0 | 5.5 |
| unread_alpha | 0.55 | 0.68 | 0.42 |
| feather_chars | 0.75 | 1.25 | 0.40 |
| scroll_ms | 350 | 450 | 480 |
| lead_ms | 350 | 300 | 480 |

Run the existing standalone sample with `demo.py --preset soft_close` or
`--preset dramatic`, or call `DemoWindow.set_preset(name)`. The existing sliders
reflect the selection; reset restores today's values. No button style changes.
The original smoothstep scroll and linear syllable wipe remain unchanged.

`item3-apple-knobs.png` grabs the existing LyricCanvas at 3.35 s in every case,
on dark and white backgrounds. The long 慢 syllable is about 49% complete.
Soft/close makes neighbors easier to scan; dramatic adds separation and blur
but reduces context and may cost more CPU. These still captures do not measure
desktop performance or establish native Windows animation quality.

## Default verification

The renderer also writes `default-proofs.json` with raw QImage hashes and exact
values. To compare against an isolated baseline archive without changing it:

```bash
QT_QPA_PLATFORM=offscreen /workspace/gluspot/qtvenv/bin/python _demos/par_decisions/render.py --defaults-only --source-root /path/to/base --out /path/to/base-shots
```

Use the same interpreter, fonts and renderer for both revisions. Seven paths
are captured: official visible/hidden music windows, hidden layout target,
face on dark/white covers, Apple on dark/white backgrounds. Hashes establish
identity for these fixed fixtures, not every possible desktop state.

Focused tests: `tests/test_visual_decision_options.py`,
`tests/test_wide_layout_g.py`, `tests/test_broadcast_indicator.py`, plus the
standalone `_demos/apple_lyrics/check_demo.py` script. Always use offscreen Qt.

Offscreen `QT_QPA_PLATFORM=offscreen`, one process per test file, against
`df4e54e`: no failure identity appears on this branch that is absent from the
base. `test_music_fullscreen` exited -11 in the parallel batch on both
revisions; alone it runs 265 tests with the same 8 failures and 1 error.
The other files keep the same 32 skips. Fifteen tests that the base does not
have all pass, including the live 11% cover check. The hidden cover and the
theme-colored face rasters differ from the base on purpose. Visible controls,
the Apple sample, button styles, glass grouping and playback modes do not.
