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


## Broader baseline evidence

The additional eight startup/island/surface/collection/queue modules run **845
tests** on both A and isolated base `3df61ef`: **20 failure identities and one
error identity match exactly**, with no new failures/errors. Reports and the
machine comparison are `A-lifecycle*.json` in the external handoff. Most are
old font/style expectations or offscreen geometry/aspect-ratio assumptions.
Ordinary full discovery on the isolated base terminates with SIGSEGV (`-11`)
after 138 test starts while `ArtworkVisualPriorityTests` calls image decoding
through the provider. It produces no complete unittest summary. Fault logs and
exact command metadata are retained; no GC/test workaround or full-suite pass
is claimed.


## Platform entries and settings (B)

The login page inventories six intended platforms. Real account support remains
with the existing service/controller/Node bridge; UI-only placeholders never
create a backend account or QR flow, even if a malformed catalog says available.

| Platform | Account backend | Entry behavior |
| --- | --- | --- |
| 网易云音乐 | Pinned NetEase API adapter | Existing QR login, verified status, logout, catalog/playback; enabled only when runtime is available |
| 酷狗音乐 | Pinned Kugou adapter | Existing QR login, status/logout/catalog/playback; runtime gated |
| QQ 音乐 | Pinned QQ adapter | Existing QR login, status/logout/catalog/playback; runtime gated |
| 汽水音乐 | None verified | Disabled, 即将支持 |
| Spotify | No owned-login backend | Disabled, 即将支持 |
| Apple Music | No owned-login backend | Disabled, 即将支持 |

NetEase has prior end-to-end user validation; QQ and Kugou implement real
backends but still need broader account/desktop acceptance. This change does
not claim fresh real-platform login testing. Optional pinned runtime absence
continues to explain disabled real entries. Six entries use two rows below
720 px and one row at wider sizes. The current account/login gate now follows
the pending library selection, current owned track, then saved supported source;
another platform's authenticated account cannot bypass it. Back opens that
platform through the existing login controller.

Settings now has five Chinese navigation pages, using the existing controls,
search registration and callbacks: 账号与平台 / 播放与采集 / 歌词与显示 /
窗口与布局 / 高级. Account actions reuse the existing source/login page.
Diagnostic page indexing and search destinations follow their new page.
The shell/cards use a dark translucent palette; switches have a local dark
paint override for readable labels when the saved app theme is light. The
island/background-follow still reads the saved app theme. Obsolete gold-reading
prose is removed; there are no new gold/lift controls or store migrations.

An old-settings fixture is compared against the existing manager-normalized
snapshot across UI construction, all page visits, widget sync, save and reload.
All values, unknown nested extension data and the legacy dock path survive
without additional loss. The store's preexisting retirement of gold/lift keys
is preserved, not reimplemented by this UI change.


Final combined verification runs all 32 A/B modules: **501 tests at each DPR
1/2 pass, with zero failures/errors and one skip** for the unavailable prepared
pinned online runtime (31.496 s / 33.370 s). The runtime-independent provider,
controller, login/logout and placeholder regressions still execute. Final
reports are `final-focus-dpr1.json` and `final-focus-dpr2.json` in the handoff;
all module names and the exact skip identity are recorded there. The entry test
fixtures explicitly provide a normal logical work area at both DPRs while
preserving separate short-screen cases. Both the window and controller enforce
placeholder gates, including malformed-catalog regression coverage.

Reviewed fixture renders cover narrow 433 × 550, wide, skeleton, six-entry login
at 330/433/wide, and every settings page at 860 × 620. These are named OFFSCREEN
in the external handoff and do not substitute for native PC screenshots.


Ordinary discovery on the B candidate reached **4572 test starts**, then made
no log progress for more than 60 s in `OwnedPlaybackTests` setup. SIGINT did not
finish it; the identified test process was manually aborted to capture stacks
(exit -6, not a spontaneous crash). The current thread was in
`owned_music_player.py:107` construction. No complete summary was produced and
no root cause is established. `full-final.log` and metadata retain this limit.
That broader run began before the final placeholder-controller guard and test
screen-fixture corrections; the 501-test dual-DPR reports cover the final source.
The clean-base ordinary run separately crashed earlier. No whole-suite pass or
whole-suite failure-equivalence claim is made.
