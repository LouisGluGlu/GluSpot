# GluSpot UI observation with Taste Skill

Date: 2026-10-14  
Final verification date: 2026-10-15 (Asia/Shanghai)  
Product: GluSpot 6.412  
Scope: existing PySide6 desktop UI, using the installed Taste Skill's screenshot-first critique method.

The upstream `design-taste-frontend` skill is written for public web pages and its browser inspector is
not applicable to a native Qt application. This review therefore uses the same evidence order—real
screenshots first, then hierarchy, color, spacing, states and interaction—but does not apply web-only
rules such as a landing-page image count, hero composition or mobile viewport scoring. Round 1 records
the initial observation; the dated backup and authorized implementation are recorded below.

## Evidence

The inspected native rasters were produced by the existing UI alignment matrix at DPR 1:

- [settings overview](../.runtime/ui-visual-alignment/dpr-1/final/overview-settings.png)
- [source and library overview](../.runtime/ui-visual-alignment/dpr-1/final/overview-entry-library.png)
- [music overview](../.runtime/ui-visual-alignment/dpr-1/final/overview-music.png)
- [island overview](../.runtime/ui-visual-alignment/dpr-1/final/overview-island.png)
- [media settings](../.runtime/ui-visual-alignment/dpr-1/final/settings-light-page-1.png)
- [source selection](../.runtime/ui-visual-alignment/dpr-1/final/entry-sources.png)
- [library at 520 pixels](../.runtime/ui-visual-alignment/dpr-1/final/library-520-expanded.png)
- [windowed music at 640 pixels](../.runtime/ui-visual-alignment/dpr-1/final/music-640-window.png)

The screenshots are synthetic fixtures, so they establish composition and states, not real account
availability, artwork quality, physical output behavior or long-session playback.

## Round 1

Seen in one second: a dark, compact music island with album color carried into the background and
clear platform-specific source controls; the settings window is a familiar light/dark utility panel.

Idea: GluSpot turns the current song's cover and lyric atmosphere into a small object that can move
between the desktop island, music library and output controls.

Live thing: the same song state can move between compact island, window/fullscreen playback, library,
Finder/Tasker placement and coordinated output controls while preserving the current source and route.

Layout words by surface: entry=choice panel / login=source panel / library=header + search + list /
music=cover split / settings=sidebar + stacked cards / island=single object.

### What already works

1. The product has a recognizable visual idea. Album color, the cover block and blurred lyric field
   survive across the windowed music page and island modes; the surfaces do not read like unrelated
   screens.
2. Source selection uses the real source colors and keeps the unsupported Qishui tile visibly dimmed.
   The selected source, login card and library retain the same source identity.
3. The output panel has a clear hierarchy after the alignment pass: device identity, route state and
   volume action occupy distinct lanes. The focused/hovered states are more deliberate than a generic
   row of buttons.
4. Settings navigation is easy to locate in both themes. The blue active row, traffic-light window
   controls and restored input faces make the shell feel intentional rather than default Qt.
5. The empty and error library captures are honest states rather than fake content. Search, back and
   source switching have visible affordances and accessible names in the implementation.

## Improvements, ordered by user impact

### P1 — Explain unavailable sources in the source page itself

The disabled-source screenshot collapses every failure into the footer text
`音乐服务暂不可用，请稍后重试或返回系统抓取`. The implementation already retains a per-source reason in
`local/playback_entry_window.py:353-366` and puts it in the button tooltip, but a disabled tile does
not communicate that reason at a glance or reliably to keyboard/touch users. This was the root of the
reported screenshot when the optional runtime was absent.

Recommended change: keep the tiles disabled, but add a short visible status beneath each unavailable
source (for example `未安装运行时` or `暂不支持`) and make the footer name the recovery path. Keep
Qishui's unsupported state distinct from a missing local runtime. This reduces the time from “all
buttons are dead” to “I know what to repair” without changing login or provider behavior.

### P1 — Make the library header survive narrow widths

At 520 logical pixels the account title is visibly elided (`音乐账户用户名 · UI…`) while three
icon-only controls compete for the same header row. The implementation at
`local/music_library_window.py:519-530` already supplies tooltips and accessible names, so the issue
is visual prioritization rather than missing semantics.

Recommended change: reserve the three 44-pixel action targets first, give the account title a bounded
two-line treatment (nickname on line one, source/status on line two), and move the full nickname to a
tooltip or an explicit account detail view. This preserves the current actions while making the
identity readable before truncation.

### P1 — Make the search and empty states carry more hierarchy

The library search field is visually close to the surrounding dark surface and the empty library
capture leaves a large uninterrupted field below a small centered sentence. The existing states are
truthful, but the next action is easy to miss.

Recommended change: increase the contrast of the search placeholder and give the empty state one
specific action tied to the current source—such as `搜索歌曲` or `切换音乐来源`—using the existing
search/source callbacks. Keep the empty state compact; the goal is a clear next step, not a decorative
card.

### P2 — Rebalance short settings pages without adding ornament

The basic settings page has a large unused lower field at the tested window height, while the media
page becomes vertically dense with long helper paragraphs and many controls. This is a rhythm issue,
not a need for gradients or extra cards.

Recommended change: allow short pages to use a smaller content height or a denser two-column grouping
where the controls are already conceptually paired; keep long pages scrollable. On the media page,
shorten helper copy and cap its reading measure so each paragraph explains one decision. Do not fill the
empty area with decorative illustrations.

### P2 — Protect the current-song reading lane on music surfaces

The windowed music capture intentionally blurs lyric rows behind the current title, but the blurred
field still carries strong warm/teal contrast and can compete with the current title at a glance.

Recommended change: lower the background lyric field's opacity or contrast when the current title is
visible, and keep the title/artist lane at a stable width before controls appear. Preserve the existing
album-color ambience and the tested hover/idle transitions.

### P2 — Give compact islands one persistent orientation cue

The compact Free/Finder/Tasker captures are visually clean but can reduce to a small color capsule and
one line of text. The full controls appear on hover, yet a user returning to the desktop may not know
which surface owns the object or whether it is currently playing.

Recommended change: retain one small, always-visible cue—play state, source mark or a restrained cover
corner—whose meaning is identical in all placement modes. Use the existing accessible names and hover
controls; do not add a second panel or a permanent toolbar.

## Do not change in the next pass

- Do not replace GluSpot's album-color/lyric ambience with a generic blue-purple “AI” palette.
- Do not flatten source identity into one accent; NetEase, Kugou, QQ and unsupported Qishui need distinct
  status communication.
- Do not remove the output panel's separate route, volume and join actions to make rows simpler.
- Do not add web-only hero sections, card grids, decorative imagery or browser layout rules to the Qt
  application.
- Do not treat synthetic fixture artwork or the 4527-test result as proof of real account, network,
  acoustic or long-session behavior.

## Suggested next slice

The highest-value, smallest follow-up is the source page: visible reason text for each disabled source,
distinct copy for unsupported versus uninstalled, and a focused native screenshot for all combinations.
After that, refine the 520-pixel library header and empty state. Both changes can reuse current signals,
tooltips, source metadata and callbacks, and can be verified without login, network or playback.

## Dated backup before implementation

The October 14 implementation starts from the complete current checkout snapshot at
`E:\Glusopt_waitingforlaunch\GluSpot-6.412-2026-10-14`, preserving the preceding uncommitted UI/runtime
work as well as the tracked baseline. Its manifest records branch `codex/ui-visual-alignment`, HEAD
`0cde741f522530284d344e12ef85a19ce248de3d`, version 6.412, date and the original working-tree status.

`BACKUP-FILE-HASHES-2026-10-14.sha256` indexes 13,069 preserved files (927,747,869 bytes). All 508
currently present tracked files were found in the snapshot; the only nine tracked differences before
this report update are the four production and five test files modified in this follow-up. Nine key
archive-file hashes were checked against the index. The actual snapshot also contains `.venv` and a
log, which the manifest records explicitly. Build and runtime-preview directories are absent. The
original worktree Git pointer is preserved as `BACKUP-GIT-LINK-2026-10-14.txt` so the backup cannot
accidentally operate the live checkout's Git index. No backup source file was edited.

## Round 2 — implemented changes

The implementation uses Taste's screenshot critique together with the installed
`redesign-existing-projects` skill's targeted-upgrade workflow. It keeps the existing Qt widgets,
signals, provider boundary, fonts, source colors and album atmosphere.

| Observed issue | Implemented behavior | Evidence |
| --- | --- | --- |
| Disabled sources had no visible diagnosis | Disabled tiles show `需安装运行时`, `需更新运行时`, `暂不支持` or `暂不可用`; the footer names the recovery path. Full reasons remain in tooltips and accessible descriptions, and repaired sources clear stale status and resume the existing click signal. | Native supported, missing-runtime and mixed-diagnostic fixtures at DPR 1/2; source recovery tests. |
| Narrow library identity competed with three controls | Below 640 logical pixels, the three existing actions occupy the title row; the 520-pixel account lane grows from 212 to 392 pixels. The nickname is a plain-text native label with full tooltip and accessible identity. | Populated 520-pixel fixtures and geometry throughout toolbar motion at DPR 1/2. |
| Search and empty states had a weak next step | An opaque lighter search placeholder improves contrast. Empty collections offer `搜索歌曲`; empty results offer `修改搜索词`, focusing/selecting the existing field without an invented provider request. Loading, error and disabled-search states hide the action. | 520 × 360/720 empty/error fixtures, keyboard action and no-unintended-request tests. |
| Surrounding lyrics competed with the title | Only nonfocused blurred lyric ink is attenuated, up to 28%, following both the title fade and fractional blur motion. Current/duet focus and manual browsing retain their ink; background, title width, seek regions and hover timing retain their existing behavior. | Same-frame 640-pixel before/after images at DPR 1/2; window/fullscreen, translation, browsing, idle and continuity regressions. |
| Media settings helper paragraphs were repetitive or too wide | Two static helpers are shortened and split by decision; both wrap within 600 logical pixels. Dynamic status, detail controls and sync/placement behavior remain in the same widgets. | Light/dark media pages at 900/1920-pixel widths, DPR 1/2. |

The basic settings page retains its content-sized cards and elastic bottom space. Its time-based theme
controls can reveal two additional rows; shrinking the window on every page change or compressing the
already compact rows would not improve the measured reading hierarchy. No filler or extra columns
were introduced.

The compact-island recommendation is already met by the existing cover lane in default Free, Finder
and Tasker modes. Source inspection and the original compact screenshots show the same artwork anchor;
Finder's transparent mode deliberately uses cover plus timed lyrics, and the crowded Tasker cover-only
path already includes its existing play/pause glyph. This follow-up preserves the user's cover-off
preference and the actual 24-pixel Finder layout rather than adding another persistent control.

### Final visual review

The disabled-source fixtures now communicate installation/update versus unsupported status before
hover. All footer variants fit their current lane. The populated narrow library keeps a full representative
nickname; minimum-size empty states retain a visible 44-pixel action. The same-frame music comparison
keeps current lyrics and album color stable while the surrounding field recedes. Settings paragraphs
stay bounded even when the window is widened. These native images establish layout and state
presentation only; they do not establish account/network health, physical audio synchronization or
long-session playback.

Generated evidence is ignored by Git:

- `.runtime/taste-redesign/entry-settings/dpr-{1,2}/`: 22 native frames and JSON geometry/state records.
- `.runtime/taste-redesign/library/dpr-{1,2}/`: 10 native frames and JSON identity/action records.
- `.runtime/taste-ui/music-readability/dpr-{1,2}/music-640-{before,after}.png`: four same-frame rasters.

### Verification

All Qt checks ran serially to avoid native window and pointer interference.

- Entry/controller/preferences/window controls: 160 tests at DPR 1 (15.309 s) and DPR 2 (14.604 s).
- Library window/controller: 84 tests at DPR 1 (6.331 s) and DPR 2 (6.654 s).
- Music visual layers/fullscreen/hover/transition/edge/background: 352 tests at DPR 1 (22.867 s,
  before the test-only DPR corrections) and DPR 2 (21.973 s, final fixtures).
- Output panel/motion/redesign/shared hover: 156 tests at DPR 1 (17.137 s) and DPR 2 (18.103 s).
- Compilation of `api`, `local` and `tests`, and tracked whitespace checks passed.

An initial broad DPR 2 music check exposed two inherited fixture assumptions. Running the original
painter and the attenuated painter in the same process reproduced identical failures. The direct/cache
comparison now uses equal physical image scales and a common pixel phase: Qt's live text and image
samplers have different coverage at half-physical-pixel positions. The original 0.005 byte-difference
threshold is unchanged. The four-corner cache size is normalized by its actual DPR squared before
applying the existing 20 KB logical-area threshold. Production helpers and cache limits were not changed.
The final DPR 2 command passes all 352 tests.

The first full discovery run completed 4,539 tests in 241.684 seconds with three device-hover fixture
failures. A focused run reproduced all three. Native handoff had initialized `local` hover at the real
monotonic time 31,824.531; the next simulated 101-second move kept that row identity, producing a
negative elapsed duration. Keeping the cursor explicitly outside until each synthetic fixture supplies
its input passed the same three methods without altering production code. The affected fixtures now
exclude unrelated native Enter/Leave and restore the cursor mock in cleanup; their MouseMove global
positions, half-second boundaries, animation assertions and real input tests remain unchanged. Both
DPR output/hover commands pass all 156 tests. The reproduction and controlled-input logs are retained
under ignored `logs/taste-panel-*.log`.

The final native Windows / DPR 1 full discovery passed all **4,539 tests in 229.447 seconds**, with no
skips, failures or Qt callback tracebacks. Command:

```powershell
.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest discover tests -v
```

The final log is `logs/taste-redesign-final-full.log`. This full result includes the preceding whole-UI
alignment and device hover/click repairs, alongside the current Taste follow-up. Version 6.412 and the
existing EXE are unchanged; no commit, push or package was created by this follow-up. Restart this
checkout's `launch.bat` to load the changed source.
