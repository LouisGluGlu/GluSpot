# Island vocal float, palette sync and cover flip — t202

The collapsed sea used a different color role from the expanded sea. At parent
`94129dc`, `local/music_background_renderer.py:1154` returned
`solid_album_background` and a nearly white crest before reading the live
`sea_level_palette()` at line 1156. Both sizes now use that same primary,
HSL contrast lift and tinted specular. Palette RGBA remains in the DPR raster
key, so a palette update invalidates the light without waiting for audio.

Normal expanded ripple input now includes the existing analyzer's absolute
centered 300–3400 Hz estimate, independent of the preview intensity selector:

```text
before target = sqrt(clamp(peak))
after  target = min(1, sqrt(clamp(peak)) + 1.4*max(0, clamp(vocal) - .02))
before displacement = (.20 + 8.5*level)*shape
after  displacement = (.20 + 12.5*level)*shape
before / after glow depth = .3 + 20.7*level / .3 + 24.7*level
before / after crest width = 1 + .65*level / 1 + .95*level
```

Attack/release time constants remain 60/250 ms. No preview AGC enters this
envelope. Quiet peak .0004 measures .2792 → .3396 logical px maximum absolute
displacement at the fixed proof phase. Peak .64 with vocal .25 measures
5.2822 → 9.5834 px (peak-to-peak 9.5424 → 17.3126). With the same peak and no
vocal input it measures 5.2822 → 7.6969 px. Centered 1 kHz, bass 60 Hz and
antiphase PCM fixtures confirm the additional centered-band response. This is
band recognition, not source separation; centered instruments can contribute.
The expanded damage strip grows from 52 to 58 logical units to contain the
stronger glow, and the fixed body gradient remains independent of audio.

Track identity still comes from `_apply_visual_data`. The old cover flips out
and the new one flips in over 640 ms through the shared visible-cover painter.
It reuses `album_cover_projection`, the bounded artwork cache and the island's
precise timer, including paused switches. No page overlay or new timer is
created. A rapid switch captures the visible pose in a bounded 192 px image;
same-track artwork replacement does not restart the flip. Hide/show, no-media
and suppressed/navigation states release or cancel the pose. Soft palette
reflections travel along the actual crest and beneath the cover; the sea's
old whole-shell wash is replaced by these reflections. The frame's existing
paint clock synchronizes cover and light, including the DPR raster cache.

All evidence is outside Git at `/workspace/gluspot/handoff/t202/`:

- `t202-amplitude-comparison.json`, `t202-wave-comparison.png`, baseline and
  final low/high PNGs and isolated transparent light PNGs.
- `t202-color-{red,magenta,teal}-{expanded,collapsed}.png`,
  `t202-color-comparison.png`, and exact same-color/live-update assertions.
- `t202-transition-strip.png` and sequential compact/expanded frames, produced
  through real `TrackData` / `_apply_visual_data` and Qt painters.
- `t202_proof.py`, paired CPU JSON/logs, focused verification and ordinary
  discovery reports against exact `94129dc`. `FINAL.md` records final numbers,
  commit, reproduction commands and deferred PC sync/restart instructions.

The proof fixtures use private profiles/caches and offline data. PC verification
未验证. Box raster/CPU measurements do not establish native Windows appearance,
live endpoint behavior, compositor cadence or actual PC CPU. No version/build,
provider boundary, player lifecycle, login, keys, push or PC operation changes.

146 focused checks pass (143 sea/audio/palette/cover/lifecycle checks in
2.090 s, three retained non-sea transition checks in .003 s). Ordinary nested
discovery completes on both revisions with normal GC and no bootstrap:
baseline 6,055 tests / 199.813 s; final 6,064 / 249.026 s. Both report exactly
the same 54 failures, 48 errors and 32 skips; no new or removed identities,
native crash or timeout. Product/test hashes stay unchanged during the runs.
The four inherited failures from the broader 170-check attempt are retained
in the evidence; the final focused selection passes. Compilation and
`git diff --check` pass. This does not claim a green whole-branch suite.

The paired box CPU proxy uses the same hashed 520×136 fixture, medium intensity,
state + full body/wave painting, 60 warmup and 600 measured frames:

| DPR | Baseline P95 | Final P95 | Baseline / final one-core CPU at 30 Hz equivalent |
| --- | ---: | ---: | ---: |
| 1 | 1.9198 ms | 1.4692 ms | 4.2786% / 3.6780% |
| 2 | 2.4496 ms | 2.4902 ms | 6.4650% / 5.8024% |

These are one paired fixture sample, with no regression claim beyond the
measured proxy. Process score: 100/100 for the scoped source handoff, retaining
the inherited suite and unverified PC limits above.
