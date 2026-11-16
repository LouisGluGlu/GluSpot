# t229 — restore the playing surface's animated album background

Source-only hotfix on `grok/hotfix-t229`, based on `395d6ed`, version 6.417.
No push, build, PC operation, player restart or user settings change.

## Confirmed background cause and fix

`local/music_surface_window.py:461` at 395d6ed constructed
`AlbumFlowBackgroundRenderer()` without a playing-surface default.
`local/backdrop_variants.py:20–25` resolved an unset environment selector to
`frosted`. `local/music_surface_background.py:335–354` returned a static frame
for that style without using time or motion intensity. An album-derived
palette could be fully ready while the background stayed still.

This behavior is present in **both ec89693 and 395d6ed**. The static shared
default originated in t200 (`94129dc`), before the suspected F–J change.
The evidence does not support attributing this background path to a shared
worker being stopped by the library.

The playing surface now supplies `default_backdrop_variant="classic"` to
the existing AlbumFlow renderer. The resolver/renderer accept an optional
default; existing callers retain `frosted`. This restores the existing animated
album painter for window/fullscreen without changing the library/login
material. Explicit environment selections, changes to that environment during
rendering, and `set_backdrop_variant` overrides retain their behavior. Empty
or unknown explicit selectors keep their existing `frosted` fallback.

## Lyric lifecycle investigation and remaining limit

The production entry is `IslandStackVisual._show_music_fullscreen`, which
constructs `NarrowStudyWindow(..., live=True)`. Reproductions cover that entry
as well as its base `MusicSurfaceWindow`, with ready data and with delayed
lyric/artwork enrichment after showing the loading skeleton.

At both exact commits, lyrics resolve and the palette matches artwork
extraction after library construction while hidden, show/hide, close/reopen,
deletion, and settings preview destruction. The library owns a distinct
`LyricPresentationStore` and palette bridge; it shares the provider for reads.
Its suspend path closes its own workers. The main store/bridge identities
remain unchanged and open. Resize capture and freeze flags settle to false.
The full SettingsWindow lifecycle also passes an external probe.

**The reported PC lyric stall is not reproduced and its cause remains
unresolved.** No speculative lyric/provider/lifecycle change is included.
Passing offline fixtures does not establish that the live NetEase lookup
completes on the PC. This hotfix establishes the background restoration and
guards the suspected lifecycle sequence; it does not claim the entire PC
report is fixed.

## Evidence and verification

External artifacts are under `/workspace/gluspot/handoff/t229/`:

- `repro-{ec89693,395d6ed}/`: base-window lifecycle reports and frames.
- `repro-live-{ec89693,395d6ed}/`: release subclass lifecycle comparisons.
- `repro-delayed-{ec89693,395d6ed}/`: late provider-result comparisons.
- `before/` and `after/`: exact 395d6ed and fixed release-window frames,
  using the supplied PC screenshot's cover and clearly synthetic lyric rows.
  `ready-960x540.png` and `ready-later-960x540.png` compare moving background
  pixels; `loading-960x540.png` records the pending state. `report.json`
  records palette, skeleton, worker and background hash evidence.
- `dynamic-default-baseline.log`: the new animation regression fails at
  exact 395d6ed; the new library/login and explicit-selector cases pass there.
- `baseline-suite/summary.json`: exact 395d6ed, all 333 test modules, 6126
  tests, 54 failures / 48 errors / 32 skips, unchanged source hashes.
- `fixed-suite/summary.json` and `suite-identity-comparison.json`: final
  module-isolated execution and comparison against that exact baseline.

The regressions use actual Qt objects, asynchronous lyric/palette completion,
and the production release entry. They check resolved lyric text, skeleton
exit, independent worker ownership, album-derived palette and changing
background pixels. Existing background-selector expectations are updated only
for the restored playing-surface default.

The requested full command is:

```sh
/workspace/gluspot/qtvenv/bin/python /workspace/gluspot/handoff/t202/run_full_isolated.py REPO OUT --workers 3
```

Final focused verification passes **85 tests**. The fixed isolated suite
completes **335 modules / 6131 tests**, with the same **54 failures / 48 errors /
32 skips**, zero new or resolved identities, and unchanged Python source
hashes. The two incomplete module processes are fully supplemented, as on the
baseline. The commit SHA is recorded in the external `FINAL.md`.
Module isolation preserves normal GC and private profiles/caches;
the runner supplements incomplete module processes with individual test
processes. It is not a claim that ordinary monolithic discovery or native
Windows acceptance passes.

Process score: 100/100 for the scoped, verified background change; the
unresolved PC lyric symptom remains an explicit handoff limitation.
