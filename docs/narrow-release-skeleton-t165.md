# Release chrome and width skeletons (t165/t166)

The full app created a bare `MusicSurfaceWindow`, while narrow-live used
`NarrowStudyWindow`. At narrow release widths, the bare window still painted
wide transport next to/over the cover and the old lyric intro region. The
release entry now constructs the shared responsive window. All four content
modes share its t151 top controls and bottom-centered previous/pause/next lane.
At 433 × 550, the three transport targets are distinct, at least 44 px wide,
centered at x=216.5 and y=456, and clear of the cover.

Live initialization preserves the saved content mode, loads no study covers,
hides study hints, and synchronizes DJ/order state through the existing provider
capability methods before the first frame. Collection uses the existing
controller. DJ requires owned personal FM and provider DJ support; collection
requires the existing track/controller capability; order uses the provider's
supported modes. At 433 px DJ fits; at smaller widths the existing optional-slot
clearance may hide it. Back retains the current login/library gate. Live Return,
Esc and close preserve the inherited island restore path, and focus/reopen reuse
the same overlay. Narrow painting and input also honor inherited entry/return
geometry snapshots and compact snapshots; the valid-origin regression checks
the actual bitmap morph and owner restoration. Window size keeps its explicit narrow/wide action.

The shared t160 resize gate still freezes prepared veil, sharp background and
chrome tint, disables lyric/list targets, and defers full-song shaping/prewarm.
Its visible path now fades old ink out in 180 ms into grey rounded bars with a
subtle shimmer. Bars are recomputed arithmetically from the current column on
each frame, with no text layouts. A short entrance-only ink snapshot stays at
its original pixel size, is clipped to the current column, and is discarded by
180 ms. There is no scaled lyric pixmap during drag. Playlist ink follows the
same gate. Cover and controls keep their existing painters.

After 150 ms quiet and completion of any queued split/merge, the final wrap
signature is probed once. Wrap-equivalent content reuses its shaped layouts;
changed content/wrap builds once. A very short gesture waits for the 180 ms
entrance to complete before committing. Both outcomes fade the skeleton back
to live ink in 180 ms, including equal-wrap reuse. Premultiplied endpoint weights
prevent a midpoint brightness dip; a width event during reveal captures the
visible composite before starting the next fade, avoiding a full-ink flash.
`lyric_relayout_count` remains the cumulative count of full-song build starts.

The shared width style duration is now
`clamp(1.0 / (1 + 0.8 * speed / 60), 0.4, 1.0)` seconds; a fast jump uses
0.4 s. Both initial Qt setters use 1000 ms. Reverse intent still queues until
the running transition completes, then chooses the reverse duration.

## Verification and limits

Focused checks run with `QT_QPA_PLATFORM=offscreen`, private temporary settings,
AppData and lyric/artwork caches, using the t156 Box runner and
`/workspace/gluspot/qtvenv/bin/python`. All **96 focused tests pass at each DPR 1/2**, with zero failures, errors or
skips (15.459 s / 17.334 s). Reports are `A-final-dpr1.json` and
`A-final-dpr2.json` in the external handoff. Tests exercise the actual release owner/overlay, all-four narrow
transport geometry, first-frame capabilities, restore/reopen, zero shaping and
LRU mutation during drag, current-width lyric/list bars, changed/equal-wrap
settle, both fade endpoints and interrupted reveals, frozen veil/tint, and
1.0/0.4 durations with queued reverse.

Physical Windows screenshots, real platform logins, native width-drag appearance,
sustained CPU/FPS and audio acceptance require the PC. This session exposes no
PC executor. Safe bundle sync/backup/launch scripts and final test reports are
outside source control in `/workspace/gluspot/handoff/t165/`; they do not restart
NetEase or take ownership of its CDP endpoint. No version bump, build or push.

The 13 focused modules are `test_release_chrome_t166`,
`test_lyric_resize_freeze_t160`, `test_music_width_transition_t156`,
`test_narrow_t156_veil`, `test_narrow_flicker_v2`, `test_lyric_fit_width`,
`test_t156_resize_tint`, `test_narrow_liquid_glass`,
`test_narrow_content_layouts`, `test_narrow_width_breakpoint`,
`test_narrow_back_resize`, `test_narrow_back_pc_regression` and
`test_narrow_shared_runtime` (each under `tests`). The release fixture supplies
an explicit 1600 × 1000 logical screen so 433 × 550 is tested at both DPRs,
independent of the default small offscreen screen.

Earlier t160 ordinary full-suite/native-GC and offscreen geometry limitations
remain documented in the README; focused success is not a whole-suite or PC
acceptance claim. No unrelated behavior is changed to bypass those limits.
