# Plugin TIME Visual Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Translate the external Solaris Obsidian HTML reference into the PySide6 hand-painted TIME plugin panel while preserving existing countdown, alarm, music progress, lyric sync, and NetEase jump behavior.

**Architecture:** Keep the UI Python/PySide6-only in `local/island_stack_visual.py`. Add small pure visual helpers for TIME palette and picker wheel display values, then route only TIME panel painting through those helpers. Keep data and settings writes behind the existing plugin/provider paths.

**Tech Stack:** Python, PySide6 `QPainter`, `unittest`.

---

## Reference Mapping

### Adopt

- **Palette:** map Solaris Obsidian to TIME-only tokens: `#131313` shell surface, `#1c1b1b` module fill, `#2a2a2a` handle fill, `#353535` divider, `#ffb800` gold accent, `#02c953` active switch, `#e5e2e1` primary text, `#d5c4ab` muted text.
- **Structure:** keep the existing three TIME modules: left clock, middle countdown, right alarms.
- **Module shape:** use 12 to 16 px rounded module corners, 1.5 px gold outlines, and no nested card treatment beyond the functional module boundary.
- **Countdown idle state:** translate the reference 2x2 preset circles for `5`, `15`, `10`, `20` minutes.
- **Countdown running state:** keep the existing active ring, but render it with gold track and charcoal center rather than a generic plugin progress style.
- **Alarm list:** keep rows with time, label, dividers, and pill switches; active switches use green, inactive switches use muted outline plus light thumb.
- **Alarm editor:** translate the editor into compact picker modules with previous/current/next wheel values, a note field, frequency/date fields, and existing save/cancel commands.

### Do Not Adopt

- No WebView, HTML, Tailwind, CSS animation, DOM event handling, Google font loading, Material Symbols, custom web scrollbars, or browser assets.
- No old Pill UI.
- No restored live-media source tree.
- No old side reminder rail for TIME countdown/alarm states.
- No changes to music progress, lyric timeline, lyric offset, NetEase position/jump logic, `api/providers.py`, `api/lyrics.py`, or `api/netease_position.py`.

### PySide Mapping Points

- Add `_time_reference_palette(theme, color)` to centralize the Solaris colors and keep TIME styling separate from generic plugin, music, weather, and agenda styling.
- Add `_alarm_picker_display_values(field)` so picker drawing can show a wheel-like previous/current/next stack without adding test-only methods.
- Keep wide TIME panels reference-weighted: clock and countdown stay instrument-sized while the alarm list takes extra width.
- Update `_paint_time_module_shell()` to use the TIME-only palette: charcoal fill, gold outline, and active low-alpha gold wash.
- Update `_paint_time_countdown_module()` so idle preset hit targets form a 2x2 grid matching the reference image.
- Update `_paint_alarm_picker_field()` to draw wheel neighbors and keep wheel hit zones unchanged.
- Keep `_activate_plugin_action()`, `_handle_time_alarm_editor_wheel()`, and settings writes behaviorally intact.

## Addendum: Follow-Up UI And Reminder Mapping

### Current Corrections From Latest Review

- **Countdown command gating:** idle countdown cards only register preset actions. `timer-pause`, `timer-resume`, and `timer-clear` are registered only when a countdown is running or paused, so a timer cannot be paused before one exists.
- **Click path:** the ring hit target must travel through `plugin_action_rects -> _hit_test() -> _activate_key() -> _activate_plugin_action()`. Tests should simulate this path instead of only calling `_activate_plugin_action()` directly.
- **Real analog clock:** the clock hands must use the displayed TIME clock value. Parse `HH:MM` or `HH:MM:SS`, include minute contribution in the hour hand and second contribution in the minute hand. Fall back to current local time only when parsing fails.
- **Height usage:** TIME dashboard body should consume more of the expanded component height, with bottom padding reduced to the smallest stable margin that keeps rounded corners and hit targets readable.
- **Layout modes:** `double` keeps the left clock visible and pages the right slot between countdown and alarm. `triple` shows clock/countdown/alarm. `stretch` keeps three modules while making countdown wide enough for two active countdown rings or stretched controls.
- **Extreme ratios:** breakpoints are clamps, not raw percentages. Below the double-layout minimum, countdown remains the default right page. Above the stretch breakpoint, countdown expands but alarm still keeps list density.

### Countdown Pause Mapping

- **Running, double/triple:** a single click inside the countdown ring pauses the active countdown. The paused state paints a black translucent overlay on the countdown module and shows continue plus cancel controls inside the ring area.
- **Running, stretch:** the first active countdown still accepts ring click pause. The stretched card also exposes pause and cancel controls beside the ring when space allows.
- **Paused:** continue resumes from `plugin_timer_paused_remaining_seconds` by writing fresh `v2_timer_started_at` and `v2_timer_end_at`; cancel clears paused state and timer settings.
- **Multiple countdowns:** keep at most two entries. Default and double layouts show the most recent entry first; stretch can show two rings side by side. Only the primary ring owns the pause hit target.

### Reminder Mapping

- **Side island running reminder:** when TIME has a running countdown or alarm reminder and the main island is not the plugin island, show a compact side reminder using a dark pill, the TIME icon, capped/scrolled note text, and a remaining-time pill.
- **Side island done reminder:** when time is up, invert TIME icon/background color: yellow surface with dark icon/text, and show the done copy as `时间到` or the provider alert body if present.
- **Plugin island unexpanded non-focus:** keep a compact TIME cell reminder when TIME is active but another plugin is focused. It should be based only on TIME's reminder status, not weather/agenda styling.
- **Plugin island unexpanded focus:** focused TIME cell shows the countdown/alarm state directly with the ring/progress accent.
- **Expanded music island:** active TIME reminder may still appear as a side alert because the plugin strip is secondary to music. The reminder must not change music progress, lyrics, offset, or NetEase jump behavior.
- **Expanded plugin island:** the side reminder animation disappears and the active TIME reminder folds into the plugin panel reminder rail. This applies even when TIME itself is focused.
- **Long notes:** reminder note strings are capped for layout measurement and painted through the existing scrolling text helper so overlong remarks scroll instead of overflowing.
- **Assets:** use local SVG assets from `D:\Program\gpt-island\logos`: `倒计时黑.svg`, `倒计时黄.svg`, `闹钟黑.svg`, and `闹钟黄.svg`. No external downloads are needed for this pass.

### Additional Tests To Add Before Code

- `_time_clock_hand_angles("15:22:30")` returns real hour/minute/second angles instead of fixed placeholder directions.
- `_draw_time_clock_face()` or the dashboard clock painter consumes the parsed clock value from the TIME model.
- A painted running countdown ring hit-test resolves to `plugin-action:timer-pause`, and activating it pauses the timer.
- A painted paused countdown overlay resolves `timer-resume` and `timer-clear`.
- `_time_plugin_panel_layout()` uses more vertical module depth and reduced bottom remainder across double, triple, and stretch layouts.
- Stretch layout gives countdown a meaningfully wider slot while preserving clock and alarm usability.
- `_active_plugin_alerts()` includes TIME running/done reminders when appropriate, but suppresses side TIME alerts when the expanded plugin panel is visible.
- `_plugin_panel_reminder_models()` includes folded TIME reminders for expanded plugin panels.
- Long TIME reminder bodies return a capped display string and still use scrolling paint.
- TIME reminder icon asset paths resolve to the local `logos` directory and pick black/yellow variants according to running versus done state.

## Addendum 2: Click And Reminder Corrections

- **Paused overlay hit area:** after a countdown is paused, only the green continue icon and red clear icon are actionable. Clicking the ring body, countdown text, overlay background, or any other non-icon area must do nothing.
- **Expanded click behavior:** after the island is expanded, blank clicks inside the island stack do not collapse it. Collapse is reserved for mouse-leave behavior and the main top island click path.
- **Done ring clear:** when a countdown reaches `done`, `due`, or alarm-complete status, the countdown ring interior maps to `timer-clear`, not `timer-pause`, and clearing returns the module to the preset selection grid.
- **TIME side alert:** TIME side reminders use a dedicated pill renderer. They must not call the generic plugin status-dot painter, must not use the old square icon cell, and must use the figure-4 segmented language: running state is dark with gold line icon + note + time chip; done state is yellow with inverted dark text/icon and `时间到`.
- **TIME compact plugin strip:** the compact TIME cell is a segmented strip, not a generic icon/detail cell. Segment 1 is the exact time and respects `show_seconds`; following segments show at most one countdown and one alarm, sorted by nearest due time.

## Addendum 3: Width Caps And Scrolling Rules

- **Side alert width cap:** TIME side reminders are capped capsules, not content-sized generic plugin alerts. Long titles, alarm notes, and countdown strings must not widen the side island beyond the TIME cap; overflow is handled inside the capsule.
- **No countdown scrolling:** countdown values such as `4:47`, `14:29`, or `3:53` are time data and remain static. This applies to the side alert time chip and the compact plugin strip countdown segment.
- **Alarm note scrolling only:** alarm remarks are the only TIME reminder text allowed to use the scrolling text helper, and only when the remark exceeds the available note budget. Short alarm labels and all countdown labels use clipped/static drawing.
- **Generic alert cleanup:** after the TIME early-dispatch in `_paint_plugin_alert()`, the generic plugin alert path should contain no TIME-specific square icon, status-dot, or inverted countdown/alarm remnants.
- **PySide-only mapping:** these corrections stay in `local/island_stack_visual.py` and tests. They do not change WebView/HTML, plugin provider state, music progress, lyric sync, lyric offset, or NetEase jump/position logic.

## Addendum 4: Idle Compact Strip And Boundary Ring Standard

- **Idle TIME compact strip:** when TIME has no active countdown and no explicit enabled alarm, the compact plugin strip shows only the left exact-time segment. It must not reinterpret dates, summaries, or ordinary detail strings as countdown content.
- **No date segment:** date text is not a TIME compact reminder. The left exact time is sufficient; following segments are reserved strictly for active countdown/alarm reminders.
- **No rectangular alert ring:** rectangular side reminders for all plugins do not paint circular progress animation. They use their icon/value language without progress-ring decoration.
- **Boundary-only ring:** ring animation is reserved for the circular fallback state produced by boundary pressure/main-island shrink behavior. Narrow but still rectangular capsules do not qualify.
- **Shared plugin standard:** this boundary-only ring rule applies to TIME, WEATHER, AGENDA, and any generic plugin side reminder.

## Addendum 5: Single-Layer TIME Side Pills

- **No nested chip:** TIME side reminders are a single capsule. Do not draw an inner value pill, inner due pill, or second mini icon inside the same capsule.
- **Running countdown:** match the figure-2 language: black capsule, yellow countdown/hourglass icon, static countdown value. No `Timer running`, no `备注`, no date text.
- **Running alarm:** black capsule, yellow clock/alarm icon, alarm remark text. Long remarks may scroll inside the same capsule.
- **Done state:** use the same single-layer structure as waiting pills: yellow capsule, black line icon, black text. Countdown done shows `时间到`; alarm done shows the remark. This replaces the earlier text-only done pill.
- **Layer discipline:** a border/stroke is allowed, but it must not read as an additional contained control. The side reminder should read as one object.

---

## Addendum 6: Compact Strip Ratios, Larger Alert Icons, Alarm Editing

- **Compact TIME left clock width:** the left exact-time segment owns a larger fixed width budget before reminders are laid out. `HH:MM:SS` gets a wider floor than `HH:MM`, so enabling seconds does not squeeze or visually collide with countdown/alarm cells.
- **Fixed reminder chips:** compact countdown and alarm reminders use fixed target widths instead of stretching across all leftover space. The countdown chip is shorter and value-focused; the alarm chip may be slightly wider for a short note.
- **Circular fallback:** if the remaining compact-strip space cannot fit fixed reminder chips, each reminder degrades to a circular icon-only cell. Countdown values do not scroll in this state, and idle/date summaries still do not create fake countdown cells.
- **TIME side icon scale:** rectangular TIME side pills keep the current single-layer style but enlarge the yellow line icon. The icon should read clearly at the 42 px side-alert height.
- **Boundary done fallback:** when boundary pressure collapses a done TIME reminder into the circular fallback, draw a yellow circle with the matching black countdown/alarm icon. Rectangular done pills keep the same icon + text structure as waiting pills.
- **Alarm row editing:** alarm rows expose a dedicated edit hit target separate from the switch toggle. Opening edit pre-fills hour, minute, note, frequency, and weekdays from the selected row; saving updates that same row and preserves its enabled state. Adding a new alarm still inserts a new enabled row.

---

### Task 1: Visual Mapping Tests

**Files:**
- Modify: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests that require a Solaris palette helper, a 2x2 countdown preset grid, and wheel picker neighbor values:

```python
def test_time_reference_palette_translates_solaris_tokens(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    palette = visual._time_reference_palette(visual._plugin_theme_colors(), QColor(255, 204, 0))

    self.assertEqual(palette["surface"].getRgb()[:3], (19, 19, 19))
    self.assertEqual(palette["module"].getRgb()[:3], (28, 27, 27))
    self.assertEqual(palette["handle"].getRgb()[:3], (42, 42, 42))
    self.assertEqual(palette["accent"].getRgb()[:3], (255, 184, 0))
    self.assertEqual(palette["active"].getRgb()[:3], (2, 201, 83))
    self.assertEqual(palette["text"].getRgb()[:3], (229, 226, 225))

def test_time_countdown_presets_paint_as_two_by_two_grid(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.plugin_action_rects = {}
    visual.plugin_time_mode = "dashboard"
    image = QImage(460, 220, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)

    visual._paint_structured_plugin_panel(
        painter,
        QRectF(20.0, 20.0, 420.0, 188.0),
        {"label": "TIME", "detail": "22:52", "summary": "05/27 Wed", "status": "idle", "progress": 0.0},
        QColor(255, 204, 0),
        visual._plugin_theme_colors(),
    )
    painter.end()

    rect5 = visual.plugin_action_rects["timer-start-5"]
    rect15 = visual.plugin_action_rects["timer-start-15"]
    rect10 = visual.plugin_action_rects["timer-start-10"]
    rect20 = visual.plugin_action_rects["timer-start-20"]
    self.assertAlmostEqual(rect5.top(), rect15.top(), delta=2.0)
    self.assertAlmostEqual(rect10.top(), rect20.top(), delta=2.0)
    self.assertGreater(rect10.top(), rect5.top() + 8.0)

def test_alarm_picker_display_values_show_neighbor_rows(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.plugin_alarm_editor = {"hour": 15, "minute": 22, "note": "", "frequency": "每周", "weekdays": "二、四"}

    hour_values = visual._alarm_picker_display_values("hour")
    frequency_values = visual._alarm_picker_display_values("frequency")

    self.assertEqual([item["text"] for item in hour_values], ["14", "15", "16"])
    self.assertFalse(hour_values[0]["current"])
    self.assertTrue(hour_values[1]["current"])
    self.assertLess(hour_values[0]["opacity"], hour_values[1]["opacity"])
    self.assertEqual([item["text"] for item in frequency_values], ["仅一次", "每周", "每天"])

def test_time_panel_wide_layout_keeps_alarm_list_prominent(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    layout = visual._plugin_panel_layout(QRectF(0.0, 0.0, 880.0, 220.0))

    self.assertGreater(layout["time_clock"].width(), 120.0)
    self.assertLess(layout["time_countdown"].width(), layout["time_alarm"].width())
    self.assertGreater(layout["time_alarm"].width(), layout["time_countdown"].width() * 1.8)
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_time_reference_palette_translates_solaris_tokens tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_time_countdown_presets_paint_as_two_by_two_grid tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_alarm_picker_display_values_show_neighbor_rows`

Expected: FAIL because `_time_reference_palette()` and `_alarm_picker_display_values()` do not exist, and countdown presets still paint in one horizontal row.

### Task 2: TIME Palette And Countdown Grid

**Files:**
- Modify: `local/island_stack_visual.py`

- [ ] **Step 1: Implement the TIME-only palette helper**

Add `_time_reference_palette(theme, color)` near `_plugin_theme_colors()` and use it from TIME panel painters only.

- [ ] **Step 2: Convert TIME module shell painting**

Update `_paint_time_module_shell()`, `_draw_time_clock_face()`, `_paint_time_alarm_switch()`, and `_paint_time_alarm_module()` to use the palette helper for charcoal fills, gold outlines, green active switches, and muted dividers.

- [ ] **Step 3: Convert countdown preset layout**

Update `_paint_time_countdown_module()` so idle presets draw as a stable 2x2 grid and keep the existing commands `timer-start-5`, `timer-start-15`, `timer-start-10`, `timer-start-20`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same focused tests from Task 1.

### Task 3: Alarm Editor Wheel Translation

**Files:**
- Modify: `local/island_stack_visual.py`

- [ ] **Step 1: Implement picker display values**

Add `_alarm_picker_display_values(field)` using existing editor state and the same option sets as `_adjust_alarm_editor_field()`.

- [ ] **Step 2: Paint wheel neighbors**

Update `_paint_alarm_picker_field()` to draw previous/current/next values with muted opacity above and below the current value while preserving `plugin_alarm_editor_rects[field]`.

- [ ] **Step 3: Preserve editor actions**

Keep `alarm-editor-note`, `alarm-editor-save`, `alarm-editor-cancel`, and wheel hit zones registered exactly as behavior tests expect.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages`

### Task 4: Verification

**Files:**
- Test only.

- [ ] Run `.\.venv\Scripts\python.exe -m py_compile local\island_stack_visual.py`
- [ ] Run `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages`
- [ ] Run `.\.venv\Scripts\python.exe -m unittest discover -s tests`
- [ ] Run `git diff --check`
- [ ] Inspect `git diff -- local/island_stack_visual.py tests/test_island_stack_visual_messages.py docs/superpowers/plans/2026-05-28-plugin-time-visual-reference.md` and confirm no WebView/HTML and no provider, lyric, progress, or NetEase edits were introduced by this task.

---

## Addendum 7: Side Alert Bounds And Figure-2 Alarm Editor

### Latest Corrections

- **Side alert width is independent from the main island width.** Main island clamping keeps the main object on screen, but side reminders need their own row-level footprint check. The row must measure the widest active alert in the row before deciding whether the row fits.
- **No half-width side pills.** If two or more side reminders cannot fit at their full row width, the whole side-alert row falls back to circular icon-only reminders. Mixed half-width rectangles are not allowed because they visually cut the reminder content.
- **Standalone preview canvas reserves side-alert room.** The local preview canvas stays wider than the main island so side reminders can be inspected without being clipped by the widget's local bounds.
- **Alarm setup and editing share the figure-2 editor.** Both add and edit modes use a compact dark rounded shell with a centered gold title and one horizontal row of four bordered fields: time, note, frequency, and weekdays/date.
- **Figure-2 editor interaction mapping.** The visual time field is a single large `HH:MM` block, but its left/right halves still register the existing `hour` and `minute` wheel hit zones. The note field registers `alarm-editor-note`; frequency and weekdays keep their existing wheel hit zones; save/cancel stay available as top edge action rects.

### Tests Added Before Implementation

- `_screen_bounds_in_local()` clamps external screen bounds to the local widget canvas.
- `_standalone_canvas_width()` keeps enough room for main island plus side reminders.
- Multiple side reminders in tight space return circular rects instead of compressed rectangular pills.
- `_time_alarm_editor_layout()` returns a single-row figure-2 field layout.
- `_paint_time_alarm_editor()` registers figure-2 fields and does not require the old handle-strip layout.

---

## Addendum 8: Alert Type Scale And Alarm Editor Input

### Latest Corrections

- **TIME side-alert text scale:** rectangular TIME reminders keep the same bold weight but reduce the value/done text by two font-size steps. The yellow `时间到` pill should remain legible without filling the full pill height.
- **Side-alert collapse motion:** when the main island expands and side reminders leave the active visual list, plugin alert motions shrink toward the nearest edge of the main island instead of drifting upward.
- **Visible editor actions:** the figure-2 alarm editor paints compact `取消` and `确定` pills at the shell top edge. `确定` is a gold primary action and uses the existing `alarm-editor-save` command.
- **Field adjustment motion:** hour, minute, frequency, and weekday wheel changes register a short spin animation offset that the painter applies to neighbor rows.
- **Direct time input:** clicking or activating hour/minute fields focuses them for numeric keyboard input. Two typed digits replace the value, clamped to `00-23` for hour and `00-59` for minute. Enter commits through the same save command.

### Tests Added Before Implementation

- TIME side-alert text calls use the smaller bold sizes.
- Exiting plugin-alert rects shrink toward the main island edge.
- Alarm editor paints a visible gold confirm button.
- Alarm field adjustment records spin animation direction and timestamp.
- Picker display values expose animation offsets while the spin is active.
- Hour/minute fields accept direct numeric keyboard input.
