# t156 PC handoff — prepared, NOT RUN

Source: `grok/capture-narrow-v4`, base `12c1e0fa9524da4982b5932d9d591d8f13ffb0bb`.
The packaged `manifest.json` records the exact committed tip and SHA-256 hashes.
No PC execution or transfer is claimed by this preparation.

After the final commit and a clean tree, run only on Box:

```bash
/workspace/gluspot/qtvenv/bin/python handoff/narrow-v4/pc_verify_t156/pack_box.py
```

This prepares `/workspace/gluspot/handoff/narrow-v4/pc_verify_t156/`,
`MEGA_T156_PC.md` and `mega_t156_pc.zip` outside the checkout. Transfer that ZIP
to a fresh scripts folder on the PC using the established copy mechanism.
The intended result destination is **Desktop\_flicker_shared\mega_t156\**,
resolved through the Windows Desktop known folder. This environment has no
mounted PC Desktop; the scripts and archive are the transfer deliverables.

When a later human explicitly chooses to run PC verification, leave NetEase
and its current 13371 endpoint running. The runner stops if another GluSpot
view is visible; it never closes the live view. Preserve prior result folders.
Extract the ZIP into `Desktop\_flicker_shared\mega_t156_scripts`, then invoke:

```powershell
& "$([Environment]::GetFolderPath('DesktopDirectory'))\_flicker_shared\mega_t156_scripts\run_pc.ps1"
```

The runner verifies every copied hash, requires the fixed
`E:\grok-gluspot\_wt_capture2` mirror clean on `grok/capture-narrow-v4`, and
fast-forwards it only from the local copied Git bundle. It uses
`E:\gpt-island\.venv\Scripts\python.exe`. Missing prerequisites or divergence
stop the run. It never pushes, checks out main, installs anything, restarts
NetEase, changes ports, accesses keys or reads/writes real settings.

Each `dpr1` / `dpr2` subfolder contains native Qt focused tests, a disposable
attach-only probe, separate-process `PIL.ImageGrab` physical client-area PNGs,
and `results.json`. The existing probe exercises all four content modes,
chrome reveal/hide, fixed Back/return routing and the real edge-resize handlers.
Owned-playback flags used for route checks belong only to the temporary
profile. Transport, seek, queue, DJ and collection mutations remain denied or
suppressed. The probe closes its own disposable view after capture.

Readiness keeps the existing advancing-playback gate and a separate lyric
gate. With advancing playback but no source lyrics after 45 seconds, attach
continues as `degraded`. `probe-state.json` and `native-result.json` contain
`attach_mode` and `source_lines`. Word-click acceptance is `UNVERIFIED`;
the attach-only probe never performs seeks. Unavailable/nonadvancing playback
still stops with the original error.

Additional t156 outputs:

- `seam-scan.json`: centerline maximum adjacent luma step for width checkpoints
  330 / 400 / content-derived W_b / 460 / 640, plus 360×640 and 480×800 captures.
  The raw `<3/255` gate is recorded literally. Live lyric/artwork contrast also
  contributes to this raw metric; inspect PNGs before attributing a failure to
  a veil seam. Controlled Box raster tests provide the veil-only evidence.
- `resize-chrome.json`: tagged captures every 4 logical pixels in both drag
  directions, the actual hold/progress state and physical contrast per hit
  rectangle. Empty patches fail; background contrast alone cannot prove icon
  visibility, so human confirmation remains required.
- `transition-timing.json`: timestamped completed split/merge spans, expected
  duration and observed duration, including a fast drag to the maximum width.
  Missing slow/fast evidence is `UNVERIFIED`; early completion is a failure.
- `acceptance-table.md/json`: PC scope results, including explicit `UNVERIFIED`
  physical review. These do not replace the separate Box acceptance table.

`QT_SCALE_FACTOR=1/2` requests raster DPR and records actual DPR. It does not
change Windows display settings or establish native monitor scaling. A desktop
too small for a capture stops rather than clipping the evidence. Physical
seam appearance, 0.5-second finish-then-return motion, every-button visibility,
native scaling, high-refresh feel and audio continuity remain unverified until
the later PC run and human review. The earlier full-suite crash and countdown
diameter gate remain outside this task.
