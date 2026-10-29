# Narrow shared runtime

The authoritative design advice for this work is `/workspace/gluspot/grok/narrow_shared_claude_advice.md`; the observed PC gap is recorded in `/workspace/gluspot/handoff/flicker/LIVE-GAP.md`. These are workspace handoff documents, not files included in a release.

The implemented graph is `SettingsManager -> WindowsMediaProvider(attach_only=True) + OwnedMusicPlayer -> PlaybackSourceProvider -> IslandStackVisual`. The selected view is created only after the full graph. The narrow `study_timer` coalesces visual repaint only; `owner.timer` and surface timers stay running. Each live lyric frame reads the owner snapshot, and inherited seek uses the same provider. Attach-only capture uses an existing NetEase debug endpoint; the narrow CLI rejects `--netease-relaunch`, while the existing wide relaunch path is untouched. An unavailable or zero SMTC timeline does not reset a valid capture position.

`SettingsManager(settings_path=temp/settings.json)` skips legacy migration and binds every loader, delayed saver and atomic replace to the explicit path; real user settings are never opened or written. In NarrowLive logs, trace source `cef-playprogress` is a display label for canonical `capture-model` when runtime `position_route=cef`; `owner_source` retains the canonical value.

The automated checks use fixture providers and isolated temporary settings; they cannot establish behavior with a real player or prove physical-display smoothness. The previous PC trace attached successfully but reported `capture-unavailable`, zero position and no playing state throughout; word seek could not be attempted. A fresh PC session must verify advancing CEF progress, stable lyric index, pause/resume and a single word seek without rollback, plus visible flicker and paint cadence. Guarded PC scripts are at `/workspace/gluspot/handoff/flicker-shared`; the executor uses `E:\gpt-island\.venv\Scripts\python.exe` at `E:\grok-gluspot\_wt_capture2`. The manual command is `python local/main.py --narrow-live`. Box evidence is in `/workspace/gluspot/handoff/flicker-shared/box-evidence`. No version or packaged executable changes are implied.

The parent ran full discovery during implementation: 5,725 tests completed with 18 failures, 11 errors and 31 skips; one failure was the attach-setting validator subsequently corrected. The parent's final full-discovery rerun terminated with a native segmentation fault before a complete test summary. These runs do not establish a clean full-suite result or that every failure is inherited. Focused test results below are separate from that incomplete full run.

On the final shared runtime, three Linux Qt offscreen repetitions each passed 261 capture tests, 2 bootstrap/settings tests, 26 narrow live tests, 5 input tests and 25 layout checks. The tests cover attach-only overriding a stored relaunch setting, fixture CEF progress through the shared playback provider and active owner timer into the narrow view, owner lyric focus, inherited seek identity, and the absence of a private live lyric clock. They use fixture providers and temporary settings; they do not exercise a real NetEase process. The following commands were each run three times with `/workspace/gluspot/qtvenv/bin/python`, `QT_QPA_PLATFORM=offscreen`, `PYTHONDONTWRITEBYTECODE=1`, and temporary `APPDATA`, `LOCALAPPDATA` and XDG directories:

```text
python -m unittest discover -s tests -p 'test_capture*.py' -v
python -m unittest tests.test_narrow_shared_runtime -v
python _demos/narrow3/check_live.py
python _demos/narrow3/check_input.py
python _demos/narrow3/check_layout.py
```

Individual logs, exit codes and the exact runner are in `/workspace/gluspot/handoff/flicker-shared/box-evidence`. This is source-level fixture evidence; the remaining PC checks and full-suite limitation above still apply.
