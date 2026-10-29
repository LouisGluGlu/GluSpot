# t149 PC executor handoff

Source branch: `grok/capture-narrow-v4`. Sync target:
`E:\grok-gluspot\_wt_capture2`. Interpreter:
`E:\gpt-island\.venv\Scripts\python.exe`. Model: `sol`.

The PC bundle is prepared on Box; **it has not been run on PC**. The copied
manifest records the exact final committed branch tip and all artifact hashes.
The single entry point synchronizes that tip, runs focused native Windows Qt
unittests, then opens one disposable attach-only live view for physical
`PIL.ImageGrab` screenshots. Results go to the Windows Desktop known folder at
`_flicker_shared\mega_t149\`.

## Prepare the final bundle on Box

After all code, handoff and final-report commits, with a clean working tree:

```bash
/workspace/gluspot/qtvenv/bin/python handoff/narrow-v4/pc_verify/pack_box.py
```

This creates `/workspace/gluspot/handoff/narrow-v4/mega_t149_pc.zip` outside
source control. It contains one complete Git bundle, `manifest.json`, this
document and all executor scripts. Save the printed ZIP SHA-256 and tip with
the final handoff. Packaging reads Git and writes only the external archive;
it performs no PC action.

## CopyFromBox, then Shell on PC

Use the executor's `CopyFromBox` to copy the ZIP above to the PC Desktop at
`_flicker_shared\mega_t149_pc.zip`. Close only an old GluSpot view before
running the checks. Leave NetEase and its existing port 13371 session running.
Use an unlocked desktop and leave the test window unobscured during capture.

Run this with the executor's PowerShell `Shell`:

```powershell
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$shared = Join-Path $desktop '_flicker_shared'
$bundle = Join-Path $shared 'mega_t149_bundle'
if (Test-Path -LiteralPath $bundle) { throw 'Preserve the old mega_t149_bundle directory first' }
if (Test-Path -LiteralPath (Join-Path $shared 'mega_t149\results.json')) {
    throw 'Preserve the old mega_t149 results directory first'
}
Expand-Archive -LiteralPath (Join-Path $shared 'mega_t149_pc.zip') -DestinationPath $bundle
& (Join-Path $bundle 'run_pc.ps1')
```

The runner stops on a changed artifact, wrong branch, dirty/divergent mirror,
absent interpreter, absent 13371 endpoint or another visible GluSpot Qt view.
Sync uses `git fetch` from the copied local bundle and `git merge --ff-only`.
It never resets the mirror, pushes, switches to main or starts/restarts
NetEase. The live probe uses the existing `run_live([])` entry with private
settings and caches; it denies player launch/restore and playback/seek/queue
actions. The reclaimed collection action is recorded and suppressed. The
owned-playback gate is changed only in that temporary profile. No account,
credential or settings-file content is printed. The new probe view closes at
the end; the running player is retained.

Legacy-settings migration checks use disposable temporary files in focused
tests. Do not point this probe at a real settings file. The existing loader
filters retired keys and can schedule its established two-second autosave;
an untouched real file until the next manual save is therefore not established
for a future ordinary cold launch. This task neither runs that migration on
the user's file nor changes the existing autosave policy.

## Evidence produced

- `results.json`: exact before/after SHAs, sync result, test/probe exit codes
  and report references. `automated_pass` does not close physical acceptance.
- `focused-tests.json` and `focused-unittest.log`: native Windows Qt tests for
  chrome, four modes, back, isolated runtime, lag, veil, width animation,
  hysteresis, slide, stronger blur, singer tags, retired reading settings,
  full original lift and word-click seeking. A failed group stops before live capture.
- `baseline-diagnostics.log` and `focused-tests.json.baseline_diagnostics`: the older unbound lag harness is recorded separately. It produces 25 errors in 15 tests on both the Box baseline and final source; it predates delegation to the shared renderer and does not gate captures. Current shared-runtime and render checks remain in the main acceptance suite.
- `native-result.json`: advancing real playback/source-lyric readiness;
  four button-selected modes at 360 and 1100 logical pixels; fixed `(34,34)`
  with owned=False; intentional right-side window-mode return; actual
  owned=True library/login routing; retained player PIDs and port check;
  font-shaped entry/exit tables at heights 540/640/900 and the 1300/960/700/483/360
  width ladder. It records removal of both reading settings and gold-ring hooks,
  the surviving low-perception scheme and lift fixed to `GLYPH_FLOAT_LIFT = 5.2`.
- `frames/*.png`: physical client-area crops from a separate ImageGrab process.
  Includes chrome and every mode, owned=False before/after, the intentional
  right return, owned=True before/destination, reveal/hide/steady sequences and
  a bidirectional width sweep and mouse-event edge drag through the measured
  content breakpoint. No fixed 936/960 trigger is used. Frame JSON includes
  physical bounds, foreground status, control rectangles and width-motion
  progress. The fixed left click must leave width 483, with no window-mode
  dispatch and no resize to 960; only the deliberate right click may do that.
- `screen-observer.json`: frame/timing metadata, empty/foreground checks and
  raw consecutive top/bottom-band MAD. This records the physical desktop;
  no QWidget/QScreen grab is presented as physical-display evidence.
- Console logs and ignored `logs/NarrowLive-*` in the synchronized mirror
  preserve diagnostics from the established live entry.

Review the sequence PNGs on the actual PC for edge slide, opacity stability,
readable stronger blur and reveal/hide flicker. MAD is diagnostic: the moving
background, live lyric ink, desktop capture cadence and machine load affect
it. The scripts do not invent a new smoothness threshold or turn the older
failed MAD/CPU/countdown gates into passes. A foreground/empty-image failure
requires a fresh unobscured run, preserving the first result directory.

The automatic width sequence first measures the hypothetical wide column with
the production font and shared `fits` helper, then uses programmatic resizes
and Qt mouse press/move/release through the real edge-resize handlers. It captures
near-threshold reversals, both drag directions and the resulting animation. The
prescribed half-glyph hysteresis can retain wide below the nominal five-character
minimum: the JSON records the actual column and threshold rather than claiming
an absolute wrap guarantee. The Latin rule uses the plan's three-word probe;
an unusually long word can still wrap.

Repeat on the executor at Windows display scales 100%, 150% and 200%, preserving
each prior result directory first. The script records the current DPR; it does
not change display settings. A desktop too small for the measured entry/exit
plus capture margins stops with an explicit error. Physical mouse drag feel,
audio continuity, End/auto-next, real-player highlight lag,
long-session CPU and listening also remain separate. No physical PC result
is claimed until the executor returns these artifacts and the visual review.
