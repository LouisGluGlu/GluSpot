# t254 / t255: restore lyric reading, expanded layout and loading animation

The deployed `78a7732` compact sea painter drew an already computed karaoke phase as static white text. The t245 cover-inset helper also enlarged expanded covers and moved their metadata/lyrics. The user clarified that equal cover insets apply only to collapsed islands and that skeletons should stop only for confirmed no-lyrics/instrumental/terminal failure.

## Corrections

- Compact sea keeps its black pill and four audio bars, but actual lyrics now use the existing `_paint_live_lyric_text`/`LyricReadingRenderer` path, including genuine word timing, lifted ink, unread/read contrast, held text+phase during shell growth and unread next-row focus. Terminal cover-only titles retain their ordinary static label.
- Standard expanded cover, radius, metadata, full-width below-cover lyric viewport, dual-panel geometry, album/history/queue stage, listened text and DJ completion morph return to `b4601bd`. The helper remains compact-only. Radio expanded controls remain available and use the baseline ordinary expanded cover/title/transport slots; this explicitly retained feature differs from the baseline's compact-only radio speech pixels.
- Loading skeletons retain the baseline one-second entrance, seven-row animation, loaded-result finishing cycle and forced resize handoff. A first retryable preparation failure remains loading; only exhausted provider/preparation failure is terminal. Unknown/empty/loading does not inherit an earlier instrumental track's cover-only suppression, including an explicit same-track reopening; it uses temporary external lyrics unless playlist was manually retained, without changing the saved layout. There is no independent 30-second paint deadline.
- Confirmed instrumental/no-lyrics tracks retain t238 cover-only and manual cover/list rules. Their manually selected playlist keeps its bitmap through resize settle so the no-skeleton exception cannot create an empty crossfade frame. Skeletons never paint over artwork. Loaded cover lyrics remain visible through resize rather than being blanked. Provider-selected track recovery, artwork deadlines, preparation retry budgets, Finder exit repair, idle-work caches and radio command filtering remain.

## Root causes

Locations in deployed `78a7732`:

| Regression | Source evidence | Correction |
| --- | --- | --- |
| Compact sea lost word reading | `local/island_stack_visual.py:14059` discards the returned phase; `:14065` calls `_paint_scrolling_text` with a single white color. Added by `f8d5203`, retained by `24ba733`. | Keep snapshot phase and feed the existing live reader. |
| Equal insets changed expanded UI | `local/island_stack_visual.py:14618` uses shell-height cover geometry; `:14690` moves lyrics beside it; `:14701` changes dual-panel geometry; `:14733`/`:14745` move album metadata/stage. | Restore baseline formulas/calls, including DJ morph and listened text. |
| Skeleton over-removal | `local/music_surface_window.py:2618` treats elapsed visual time as terminal; `:2674` removes the result finishing cycle; `:2769` rejects baseline forced handoffs. `local/music_lyric_resize.py:156` preserves static resolved snapshots and `:333` rejects loaded resize skeletons. | Restore baseline animation; classify terminal state from actual delivery/retry outcome. |
| Loading after instrumental remained suppressed | `local/music_surface_layout.py:397` keeps a prior terminal cover-only override during a pending lookup. | Any unresolved lookup, including explicit same-track loading, owns its lyric column; retain explicit list choice. |
| Cover lyrics disappear during motion | `local/music_lyric_resize.py:450` blanks loaded cover-lyric ink/shade on resize/layout motion. | Restore baseline loaded cover-lyric visibility, retaining cover exclusion for skeletons. |

The t243 publication/refetch work, `678d3f3` measurement cache and `48c68f9` hidden clock do not cause the reproduced reading loss: the deployed source phase advances 6→18 characters, but compact sea calls the reader zero times. Other backgrounds and expanded painters already retained their reader. The focused real-clock matrix additionally verifies both supported lyric typefaces, all backgrounds and both poses at DPR 1/2.

## Verification

Evidence directory: `/workspace/gluspot/handoff/t254/`. The clean baseline worktree is `baseline-src` at `b4601bd`; `deployed-src` is `78a7732`. Scripts, captures, complete JSON measurements and audit decisions are outside source control.

- `compare.png`: b4601bd | 78a7732 | fixed, all backgrounds, collapsed and expanded at a mid-line karaoke moment. `progress.png`: advancing word ink at 2.4→5.6 seconds.
- 138/138 non-radio expanded rasters exactly equal baseline pixels across normal/dual-panel/album/history/queue/DJ-outro states, Free/Finder/Tasker and DPR 1/2. 48/48 normal geometry captures equal. 18/18 radio cases match baseline ordinary-expanded geometry while retaining requested radio controls; radio pixels are not claimed equal to baseline speech.
- 360/360 complete cover-hover/flip ARGB buffers equal the actual baseline cover function.
- Additional loaded window matrix: 8/8 geometry/state captures match b4601bd across four layouts at wide/narrow widths; 7/8 complete images match exactly. Narrow cover differs in eight antialiased glyph-edge pixels (maximum RGB delta 2), with no geometry/style change identified; the exact raster-state cause remains unassigned and is documented in the external window audit.
- 12/12 captured background×pose×DPR karaoke cases advance 6→18 characters with actual shared-reader calls and changed lyric pixels. The regression test adds 24 background×typeface×pose×DPR combinations at two times (48 advancing-clock frames), including paired no-reader pixel subtraction checks.
- Loading skeleton raster proofs at widths 960/883/690/610 remain visible, including mid-resize; time advances change 8,104 / 5,848 / 5,089 ink pixels at the three resize widths. Every frame has zero skeleton ink over artwork. Confirmed instrumental has zero lyric/skeleton pixels and effective cover layout.
- All 1,320 t227 persistent-buffer mask frames pass at DPR 1/2, Finder available/unavailable: zero queued/paint clipping, oversized logical/native masks or ink outside the shell.
- Initial focused karaoke/audio tests: 11/11 green; geometry/radio/outro: 35/35 green. Ten skeleton/window modules cover 378 tests; only the inherited fullscreen edge test's seven failure identities and one error remain, zero new identities vs t250.
- Final requested isolated suite: 352 modules / 6,260 tests in 173.057 s; 52 failures / 48 errors / 32 skips, zero new failure/error/skip identities vs t250, unchanged source hashes. The same two inherited native-incomplete modules were supplemented individually. This is baseline equivalence, not a green suite.

Detailed inventories are `geometry-report.md`, `visual-report.md`, `skeleton-report.md` and `FINAL.md` in the evidence directory. They list each found difference and its retain/revert decision.

## 未验证

Windows native compositor/mask behavior, actual MyDockFinder/Tasker placement, real PC media/provider timing, real desktop drag/resize and user visual acceptance remain 未验证. This work uses Box Qt offscreen with deterministic source observations and existing test fakes. No PC operation, push, main-branch operation, build/version change or button/glass restyle. Process score: 100/100 after lookup, baseline comparison, focused/full verification and explicit limits.
