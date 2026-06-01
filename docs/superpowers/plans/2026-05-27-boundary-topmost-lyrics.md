# Boundary Topmost Lyrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复副岛边界、超级置顶、拖拽锚点，并补强下一首歌词预加载，先保住现有音乐同步能力不被污染。

**Architecture:** 只在现有 V2 岛逻辑上补充小型纯函数，不引入旧 Pill UI。副岛布局由 `IslandStackVisual` 统一计算真实屏幕边界；歌词预加载继续留在 `api/providers.py`，通过 provider 层读取队列与歌词缓存。

**Tech Stack:** Python, PySide6, unittest, Windows media provider, NetEase/QQ position readers.

---

### Task 1: Side Island Boundary Layout

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

```python
def test_plugin_alert_moves_left_when_right_screen_edge_would_clip_it():
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.plugin_alert_width = 168.0
    visual.width = lambda: 940
    visual.height = lambda: 260
    visual.finder_mode = False
    visual.mapToGlobal = lambda point: QPoint(point.x() + 240, point.y())
    visual._current_screen_geometry = lambda: QRect(0, 0, 800, 600)
    main_rect = QRectF(520.0, 80.0, 220.0, 48.0)

    rect = visual._plugin_alert_rect(main_rect, 0, 1)

    assert rect.right() <= main_rect.left() - visual._u(9.0)
```

```python
def test_plugin_alert_becomes_circular_left_when_main_shrinks_against_right_edge():
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.plugin_alert_width = 168.0
    visual.width = lambda: 940
    visual.height = lambda: 260
    visual.finder_mode = False
    visual.mapToGlobal = lambda point: QPoint(point.x() + 320, point.y())
    visual._current_screen_geometry = lambda: QRect(0, 0, 800, 600)
    visual._main_kind = lambda: IslandKind.MUSIC
    visual._stack_target_width = lambda _kind: 420.0
    main_rect = QRectF(365.0, 80.0, 210.0, 48.0)

    rect = visual._plugin_alert_rect(main_rect, 0, 1)

    assert rect.width() == rect.height()
    assert rect.right() <= main_rect.left() - visual._u(9.0)
```

- [ ] **Step 2: Verify red**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages`

Expected: FAIL because current layout only uses local widget width and keeps rectangular right-side alerts when screen edge clips them.

- [ ] **Step 3: Implement minimal layout helper**

Add helper methods in `IslandStackVisual`:
- `_screen_bounds_in_local()`
- `_main_rect_screen_pressure()`
- `_plugin_alert_circle_size()`
- `_plugin_alert_rect()`

The target behavior:
- Default alert stays to the right.
- If right side clips on the real screen, move rectangular alert left.
- If main island is already shrunk against right edge, alert becomes circular on the left.
- If main island is already shrunk against left edge, alert becomes circular on the right.
- Clamp final target inside real screen bounds.

- [ ] **Step 4: Verify green**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages`

Expected: PASS.

### Task 2: Drag Anchor And Super Topmost

**Files:**
- Modify: `local/island_stack_visual.py`
- Modify: `local/settings_store.py`
- Modify: `local/settings_window.py`
- Modify: `local/app.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

```python
def test_plugin_alert_drag_anchor_uses_main_center():
    visual = IslandStackVisual.__new__(IslandStackVisual)
    main_rect = QRectF(100.0, 70.0, 300.0, 48.0)
    alert_rect = QRectF(410.0, 73.0, 42.0, 42.0)

    offset = visual._drag_main_offset_for_press(QPointF(430.0, 94.0), main_rect, "plugin-alert:time")

    assert offset == QPointF(main_rect.width() / 2.0, main_rect.height() / 2.0)
```

```python
def test_settings_snapshot_includes_super_topmost_flag():
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.super_topmost_enabled = True
    # set existing snapshot fields as in nearby snapshot tests
    snapshot = visual.settings_snapshot()
    assert snapshot["v2_super_topmost"] is True
```

- [ ] **Step 2: Verify red**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages`

Expected: FAIL because the helper and snapshot setting do not exist yet.

- [ ] **Step 3: Implement minimal behavior**

Add `set_super_topmost(enabled, notify=True)` and make `_force_super_topmost()` apply when enabled instead of only Finder mode. Add default setting `v2_super_topmost`, load it in `LocalIslandApp._apply_saved_settings()`, save it in `settings_snapshot()`, and expose an iOS-style switch in the Settings system page.

- [ ] **Step 4: Verify green**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages`

Expected: PASS.

### Task 3: Next Lyrics Prefetch

**Files:**
- Modify: `api/providers.py`
- Test: `tests/test_provider_state.py`

- [ ] **Step 1: Write failing test**

```python
def test_command_prefetch_uses_reader_current_track_when_cached_track_is_stale():
    provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
    reader = FakeQueuePositionReader()
    reader.current_track = lambda: SimpleNamespace(title="Live Song", artist="Live Artist", album_title="Live Album", duration_seconds=200.0)
    provider._last_data = replace(empty_visual_data(), track=replace(empty_visual_data().track, title="Old Song", artist="Old Artist"))
    provider._music_player_adapter = "netease"
    provider.netease_position = reader
    provider.qq_position = None

    provider._prefetch_adjacent_lyrics_for_command("next")

    assert reader.adjacent_calls == [("Live Song", "Live Artist", "next")]
```

- [ ] **Step 2: Verify red**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_provider_state`

Expected: FAIL because command prefetch currently uses cached `_last_data` metadata only.

- [ ] **Step 3: Implement minimal behavior**

When prefetching after next/previous command, ask the player reader for `current_track()` first; if it returns metadata, prefetch adjacent lyrics from that live queue context. Fall back to cached `_last_data` when unavailable.

- [ ] **Step 4: Verify green**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_provider_state`

Expected: PASS.

### Task 4: Finder Material Strategy

**Files:**
- Modify: `docs/PRD.md`
- Optional Modify: `local/mydockfinder.py`

- [ ] **Step 1: Document decision**

Superseded by `docs/superpowers/plans/2026-06-01-finder-material-replication-fix.md`: do not read/copy MyDockFinder private settings beyond compatible public config fields. Finder fused material now replicates the visible upper Finder bar material; it must not use full-screen wallpaper/screen crop as the island/cradle background.

- [ ] **Step 2: Verify docs**

Run: `git diff -- docs/PRD.md`

Expected: PRD contains the Finder material decision and no implementation promise that requires copying another app's protected behavior.

### Task 5: Full Verification

**Files:**
- All changed files.

- [ ] **Step 1: Compile changed Python files**

Run: `.\.venv\Scripts\python.exe -m py_compile local\island_stack_visual.py local\settings_window.py local\app.py api\providers.py`

Expected: no output and exit 0.

- [ ] **Step 2: Run focused tests**

Run:
- `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages`
- `.\.venv\Scripts\python.exe -m unittest tests.test_provider_state`

Expected: PASS.

- [ ] **Step 3: Run full tests**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests`

Expected: PASS.
