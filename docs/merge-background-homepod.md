# Album flow and HomePod source integration

This combined source was created on October 2, 2026 at the user's request.
The original `gpt-island_background` and `gpt-island_Homepod_step1&2` directories
remain unchanged beside the independent `gpt-island_merged` repository.

## Inputs

| Part | Verified source commit | Tracked files |
| --- | --- | ---: |
| Album-color flow | `c08b5f20450e07954205f71bed9d046e7ec71f31` | 254 |
| HomePod level 1–2 / 6.406 | `21783249110990ff3a35a5bdec472bf3f684e379` | 275 |

Every tracked input file was compared with its Git blob, allowing only CRLF/LF
line-ending differences; there were no other differences or extra source files.
The shared ancestor is `7dd682432aa6bad6f6ae650a79f2c00c03c94aaa`.
The input directories' `.git` files point to their original worktrees; they were
not copied into the combined repository. The combined branch is
`codex/merge-background-homepod`, with both input histories retained.
The input manifest is in ignored `.runtime/integration-part-manifest.json`.

## Merge behavior

The three-way merge required manual resolution only in the music surface imports
and README. Imports retain the 6.406 separate audio-icon module plus the album
flow renderer. README retains both inputs' historical evidence and adds combined
verification, without presenting old HomePod packages as combined packages.

The combined music surface preserves:

- Album pigments, source coverage, soft irregular moving regions, interrupted
  retargeting, bounded 30 Hz rasters and stable whole-background partial paints.
- Brightness-safe lyric ink, the original island palette roles and background
  styles, and the shared provider contract.
- Circular output/translation/β/window controls and the cached frosted chooser
  with confirmed device volume, manual IP and automatic display compensation.
- Original source observations, source-app fencing for asynchronous Seek, and
  application-level QScreen lookup alongside shutdown/reopen callback handling.
- The complete 6.406 native sender, process-loopback fallback, encrypted event
  acknowledgements, signal diagnostics and mute ownership behavior.

`local/branding.py`, `GluSpot.spec`, Windows version metadata and README retain
version **6.406**. No new public release or combined executable is implied.

## Run and rebuild

Run `launch.bat` in this directory. It uses this directory's own `.venv` and
the freshly built helper under `build/native-airplay/`. Exit another running
GluSpot first because settings and the single-instance lock use the normal
GluSpot namespace. There is no automatic receiver selection or cast at startup.

The helper was built using the unchanged `packaging/windows/build-airplay.ps1`.
Generated environments, build outputs, diagnostics and preview media remain
ignored. A future rebuild needs CMake/Ninja and the Windows C++ toolchain
documented in `native/airplay_sender/README.md`; the runtime helper does not.

## Combined verification

The combined source passed **2825/2825 discovered Python tests in 88.189 s**,
with native Windows Qt, fault handling and normal cyclic GC. The ordinary
discovery command exited 0 with a complete `Ran`/`OK` summary. Earlier focused
music/audio validation passed 407 tests in 25.840 s. Three new integration tests
use actual Qt pointer/keyboard interaction and a fake route provider to verify
complete-background preservation during chooser capture, child-widget exclusion,
material color/opacity/contrast, circular controls, route readback and Escape.
Native windowed/full-screen fixture screenshots were inspected.

Python compilation, `pip check` and staged/unstaged whitespace checks passed.
No settings, real default-output, mute/volume or receiver session was changed by
these fixtures. The previous standalone background Qt crash did not recur in
this combined ordinary discovery run. HomePod's application-screen lookup and
shared-QScreen lifetime regressions are retained; the successful run does not
establish that every possible native failure is eliminated.

The unchanged native build completed successfully and **all seven CTest suites
passed in 10.31 s**. The freshly built helper then passed **13 actual-process
lifecycle cases** with zero unavailable cases and process exit 0. These checks
use a local non-authenticating receiver fixture and a short capture probe; they
confirm helper startup, command handling, capture availability and stop/EOF
ownership without a physical receiver cast or changing source mute/volume.
The helper is `build/native-airplay/GluSpotAirPlay.exe`, with SHA-256
`8cb979dbdc1f6ddeae452802b349df7f8abd2bfd470cb523cf54f0f6fc3917d5`.
The standard build script generated its license/provenance directory.

The initial pinned dependency download stalled while fetching MbedTLS's
framework submodule. The build resumed from an independent copy of the
already available clean MbedTLS source at the exact configured commit
`2ca6c285a0dd3f33982dd57299012dacab1ff206`, including its initialized framework.
Only the new ignored build/cache directories were written; the input caches
and toolchain were read-only. No tracked CMake or product code was changed
to bypass the dependency pin or tests.

Evidence remains ignored:

- `.runtime/merge-full-tests.log`
- `.runtime/merge-focused-tests.log`
- `.runtime/merge-native-build.log`
- `.runtime/merge-helper-lifecycle.log`
- `logs/album-flow-audio-windowed.png`
- `logs/album-flow-audio-fullscreen.png`

The input HomePod listening/package checks remain historical evidence. No new
combined executable, physical receiver volume test or multi-song listening
result is claimed. High-DPI/long-session desktop quality, other audio drivers,
unsupported process-loopback fallback and multi-hour receiver operation retain
the inputs' documented hardware QA limits.
