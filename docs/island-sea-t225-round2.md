# Moderate sea response — t225 round 2

This supersedes the amplitude rollback in [the first t225 pass](island-sea-t225.md).
The existing absolute 60/250 ms peak/vocal envelope remains, and quiet input
keeps its 0.339 px reference peak. Expanded ripple amplitude is now
`.20 + 12.5 * level + 10 * max(0, level - .02)^1.5 + 9.5 * beat`.
The reused absolute low-onset envelope retains its 20/110 ms attack/release;
its displacement is much smaller than t205's original 24-unit contribution.
The 1.0-unit translucent palette hairline, at most 1.2 extra rising units,
160 ms edge EMA, palette glow and shared track reflection remain.

The same 49 production surface positions and phases measure:

| Input | ebe525e / fee7410 | 395d6ed | Round 2 |
| --- | ---: | ---: | ---: |
| Quiet .0004 | 0.339079 px | 0.327595 px | 0.339079 px |
| Vocal .64 + vocal .25 | 9.569557 px | 50.635222 px | 16.879722 px |
| Six-frame low onset .64 | 7.247002 px | 55.925845 px | 19.033115 px |

The corresponding dense 1001-position round-2 peaks are
0.339568 / 16.904081 / 19.077417 px. Held low input settles to 12.876534 px;
the 19 px target refers to the onset rather than a permanent bass boost.

The previous 58-unit damage strip cannot include the new legal maximum
crest plus glow/reflection. A fixed 104-unit strip clears old water while
preserving the top header. High-DPI painting allocates only the current
pose's crest/glow/reflection bounds, with two logical pixels of sampling
margin. The audio tick caches amplitude and phase; ribbon construction avoids
per-segment slices/generators. No timer or provider change is introduced.

83 focused checks passed in 2.853 seconds, including maximum-input damage
bounds for 360 frames, low-onset release/pause, actual reference peaks,
DPR-2 tight-versus-full-raster pixel equality in quiet/vocal/onset and track
reflection poses, edge taper, palette and repeat-paint purity.

Five alternating fresh-process pairs, pinned to Box CPU 0, compare exact
fee7410 with round 2 using the existing 520×136 / 60 warmup / 600 measured
frame CPU proxy. Median wave CPU seconds per 600 frames:

| Case | fee7410 | Round 2 | Ratio |
| --- | ---: | ---: | ---: |
| Steady DPR 1 | 0.285308 | 0.276840 | 0.9703 |
| Steady DPR 2 | 0.471435 | 0.452005 | 0.9588 |
| Track reflection DPR 1 | 0.449240 | 0.448084 | 0.9974 |
| Track reflection DPR 2 | 0.651881 | 0.636921 | 0.9771 |

State/body/wave combined ratios are 0.9542 / 0.9445 / 0.9837 / 0.9579.
These are offscreen raster measurements, not desktop FPS or a Windows CPU
claim. Initial wider-raster experiments and the two-pixel sampling-margin
correction were verified before the final source measurements.

Evidence lives outside source control in `/workspace/gluspot/handoff/t225/`:
`t225-compare.png`, `round2-proofs/{ebe525e,fee7410,395d6ed,round2}/`,
`proof-comparison-round2.json`, `cpu-comparison-round2.json`, `cpu-round2/`
and `item2-focused.log`. Renders use the real island painter with offline
fixtures at DPR 1 and 2, including red, magenta, blue-green and pale covers.

PC 未验证. No PC operation, restart, build, version bump or push.
