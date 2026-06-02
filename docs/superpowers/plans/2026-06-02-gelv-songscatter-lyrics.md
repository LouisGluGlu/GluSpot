# Gelv Songscatter Lyrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `格律宋散` text-flow style, its `逐字染色` effect, and the no-lyrics album island while preserving `波光逐流` as the default lyric style.

**Architecture:** Keep media data behind `api/providers.py` and add rendering behavior inside the existing V2 island path. Put deterministic text/layout logic in small helper functions on `IslandStackVisual` first, then connect settings and painting. Use TDD: write focused tests in `tests/test_island_stack_visual_messages.py` and `tests/test_mac_preferences_shell.py` before production edits.

**Tech Stack:** Python 3, PySide6 `QPainter`/`QFontMetrics`, `unittest`, project settings JSON.

---

## File Structure

- Modify `local/settings_store.py`: add persisted settings for lyric flow, color fill, no-lyrics mode, and album island count.
- Modify `local/app.py`: load new settings into `IslandStackVisual`.
- Modify `local/settings_window.py`: reorganize media/appearance controls, remove the public global font selector, add style-scoped lyric controls.
- Modify `local/island_stack_visual.py`: add `格律宋散`, chunk splitting, long lyric split, color fill, black background override, no-lyrics album island, and settings snapshot fields.
- Modify `tests/test_island_stack_visual_messages.py`: add helper and rendering tests.
- Modify `tests/test_mac_preferences_shell.py`: add settings/defaults tests.
- Create `assets/fonts/README.md`: document Source Han Serif SC source and packaging requirement.
- Later execution may add the actual font file under `assets/fonts/SourceHanSerifSC/` after downloading from Adobe's official release.

## Task 1: Settings Defaults And Snapshot

**Files:**
- Modify: `local/settings_store.py`
- Modify: `local/island_stack_visual.py`
- Modify: `local/app.py`
- Test: `tests/test_island_stack_visual_messages.py`
- Test: `tests/test_mac_preferences_shell.py`

- [ ] **Step 1: Write failing defaults test**

Add to `tests/test_mac_preferences_shell.py`:

```python
def test_lyric_flow_settings_defaults_are_present(self):
    from local.settings_store import DEFAULT_SETTINGS

    self.assertEqual(DEFAULT_SETTINGS["v2_lyric_flow_style"], "wave_stream")
    self.assertFalse(DEFAULT_SETTINGS["v2_gelv_color_fill_enabled"])
    self.assertEqual(DEFAULT_SETTINGS["v2_gelv_color_fill_fallback"], "#00D7FF")
    self.assertEqual(DEFAULT_SETTINGS["v2_no_lyrics_display_mode"], "text")
    self.assertEqual(DEFAULT_SETTINGS["v2_album_island_cover_count"], 5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_mac_preferences_shell.TestMacPreferencesShell.test_lyric_flow_settings_defaults_are_present -v`

Expected: FAIL with missing `v2_lyric_flow_style`.

- [ ] **Step 3: Add minimal settings defaults**

Add to `DEFAULT_SETTINGS` in `local/settings_store.py`:

```python
    "v2_lyric_flow_style": "wave_stream",
    "v2_gelv_color_fill_enabled": False,
    "v2_gelv_color_fill_fallback": "#00D7FF",
    "v2_no_lyrics_display_mode": "text",
    "v2_album_island_cover_count": 5,
```

- [ ] **Step 4: Write failing snapshot test**

Add to `tests/test_island_stack_visual_messages.py` near `test_dual_line_secondary_settings_are_saved_in_snapshot`:

```python
def test_lyric_flow_settings_are_saved_in_snapshot(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.music_playing = True
    visual.music_auto_detect = False
    visual.persistent_plugin = True
    visual.finder_mode = False
    visual.manual_finder_available = False
    visual.expanded = False
    visual.show_seconds = True
    visual.enabled_plugin_count = 3
    visual.ambient_intensity = 0.0
    visual.music_ambience_style = "sea_level"
    visual.music_flow_intensity = 0.74
    visual.compact_duotone_balance = 50
    visual.expanded_duotone_balance = 50
    visual.ui_font_family = "PingFang SC"
    visual.high_quality_font_rendering = False
    visual.animation_fps = 60
    visual.animation_speed = 1.0
    visual.empty_bar_palette = "aurora"
    visual.empty_bar_width = 92
    visual.empty_bar_height = 14
    visual.empty_bar_y_offset = 0
    visual.empty_bar_opacity = 100
    visual.empty_bar_custom_colors = ("#46D6FF", "#73FFA6", "#FF5A86")
    visual.stack_center_ratio = 0.5
    visual.saved_window_pos = None
    visual.stack_direction = 1
    visual.barrage_y_offset = 0
    visual.barrage_font_size = 10
    visual.barrage_speed = 100
    visual.barrage_use_backdrop = True
    visual.barrage_show_icon = True
    visual.auto_expand_bounds = True
    visual.expand_direction = "auto"
    visual.lyric_offset_seconds = 0.0
    visual.lyric_font_size = 18
    visual.lyric_elastic_strength = 100
    visual.lyric_elastic_mode = "both"
    visual.lyric_word_spacing = 100
    visual.enable_dual_line_lyrics = False
    visual.dual_line_secondary_weight = 72
    visual.dual_line_secondary_spacing = 100
    visual.no_lyrics_text = "暂无歌词"
    visual.super_topmost_enabled = True
    visual.lyric_flow_style = "gelv_songscatter"
    visual.gelv_color_fill_enabled = True
    visual.gelv_color_fill_fallback = "#00D7FF"
    visual.no_lyrics_display_mode = "album_island"
    visual.album_island_cover_count = 7

    snapshot = visual.settings_snapshot()

    self.assertEqual(snapshot["v2_lyric_flow_style"], "gelv_songscatter")
    self.assertTrue(snapshot["v2_gelv_color_fill_enabled"])
    self.assertEqual(snapshot["v2_gelv_color_fill_fallback"], "#00D7FF")
    self.assertEqual(snapshot["v2_no_lyrics_display_mode"], "album_island")
    self.assertEqual(snapshot["v2_album_island_cover_count"], 7)
```

- [ ] **Step 5: Run snapshot test to verify it fails**

Run: `python -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_lyric_flow_settings_are_saved_in_snapshot -v`

Expected: FAIL with missing snapshot keys.

- [ ] **Step 6: Add widget attributes, setters, snapshot, and app loading**

In `IslandStackVisual.__init__`, add:

```python
        self.lyric_flow_style = "wave_stream"
        self.gelv_color_fill_enabled = False
        self.gelv_color_fill_fallback = "#00D7FF"
        self.no_lyrics_display_mode = "text"
        self.album_island_cover_count = 5
```

Add setter methods near existing lyric setters:

```python
    def set_lyric_flow_style(self, style: str, notify: bool = True):
        style = str(style or "wave_stream").strip()
        if style not in {"wave_stream", "gelv_songscatter"}:
            style = "wave_stream"
        self.lyric_flow_style = style
        if style == "gelv_songscatter":
            self.enable_dual_line_lyrics = False
        self._clear_lyric_metrics_cache()
        self.update()
        self._emit_settings_changed(notify)

    def set_gelv_color_fill_enabled(self, enabled: bool, notify: bool = True):
        self.gelv_color_fill_enabled = bool(enabled)
        self.update()
        self._emit_settings_changed(notify)

    def set_gelv_color_fill_fallback(self, color: str, notify: bool = True):
        qcolor = QColor(str(color or "#00D7FF"))
        self.gelv_color_fill_fallback = qcolor.name().upper() if qcolor.isValid() else "#00D7FF"
        self.update()
        self._emit_settings_changed(notify)

    def set_no_lyrics_display_mode(self, mode: str, notify: bool = True):
        mode = str(mode or "text").strip()
        self.no_lyrics_display_mode = mode if mode in {"text", "album_island"} else "text"
        self.update()
        self._emit_settings_changed(notify)

    def set_album_island_cover_count(self, count: int, notify: bool = True):
        count = int(count)
        if count not in (3, 5, 7):
            count = 5
        self.album_island_cover_count = count
        self.update()
        self._emit_settings_changed(notify)
```

Add these snapshot fields in `settings_snapshot()`:

```python
            "v2_lyric_flow_style": str(getattr(self, "lyric_flow_style", "wave_stream")),
            "v2_gelv_color_fill_enabled": bool(getattr(self, "gelv_color_fill_enabled", False)),
            "v2_gelv_color_fill_fallback": str(getattr(self, "gelv_color_fill_fallback", "#00D7FF")),
            "v2_no_lyrics_display_mode": str(getattr(self, "no_lyrics_display_mode", "text")),
            "v2_album_island_cover_count": int(getattr(self, "album_island_cover_count", 5)),
```

In `local/app.py`, load the settings after the existing lyric settings:

```python
        self.window.set_lyric_flow_style(str(self.settings.get("v2_lyric_flow_style", "wave_stream")), notify=False)
        self.window.set_gelv_color_fill_enabled(bool(self.settings.get("v2_gelv_color_fill_enabled", False)), notify=False)
        self.window.set_gelv_color_fill_fallback(str(self.settings.get("v2_gelv_color_fill_fallback", "#00D7FF")), notify=False)
        self.window.set_no_lyrics_display_mode(str(self.settings.get("v2_no_lyrics_display_mode", "text")), notify=False)
        self.window.set_album_island_cover_count(int(self.settings.get("v2_album_island_cover_count", 5)), notify=False)
```

- [ ] **Step 7: Run focused tests**

Run:

```bash
python -m unittest tests.test_mac_preferences_shell.TestMacPreferencesShell.test_lyric_flow_settings_defaults_are_present tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_lyric_flow_settings_are_saved_in_snapshot -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add local/settings_store.py local/island_stack_visual.py local/app.py tests/test_island_stack_visual_messages.py tests/test_mac_preferences_shell.py
git commit -m "feat: add lyric flow settings"
```

## Task 2: Settings UI Reclassification

**Files:**
- Modify: `local/settings_window.py`
- Test: `tests/test_mac_preferences_shell.py`

- [ ] **Step 1: Write failing source-level settings UI test**

Add to `tests/test_mac_preferences_shell.py`:

```python
def test_settings_window_scopes_gelv_controls_and_hides_global_font_selector(self):
    from pathlib import Path

    source = Path("local/settings_window.py").read_text(encoding="utf-8")

    self.assertIn("LYRIC_FLOW_STYLES", source)
    self.assertIn("格律宋散", source)
    self.assertIn("逐字染色", source)
    self.assertIn("NO_LYRICS_DISPLAY_MODES", source)
    self.assertIn("专辑岛模式", source)
    self.assertIn("_update_lyric_flow_controls", source)
    self.assertNotIn('self._row(c, "全局字体", font_combo)', source)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_mac_preferences_shell.TestMacPreferencesShell.test_settings_window_scopes_gelv_controls_and_hides_global_font_selector -v`

Expected: FAIL because new constants and controls do not exist.

- [ ] **Step 3: Add constants**

In `SettingsWindow`, add:

```python
    LYRIC_FLOW_STYLES = (
        ("波光逐流", "wave_stream"),
        ("格律宋散", "gelv_songscatter"),
    )

    NO_LYRICS_DISPLAY_MODES = (
        ("显示特定文字", "text"),
        ("专辑岛模式", "album_island"),
    )

    ALBUM_ISLAND_COVER_COUNTS = (
        ("3 张", 3),
        ("5 张", 5),
        ("7 张", 7),
    )
```

- [ ] **Step 4: Add controls to media page**

In `_media_page()`, after the no-lyrics input, add:

```python
        self.lyric_flow_style = self._combo(
            list(self.LYRIC_FLOW_STYLES),
            str(getattr(self.island_widget, "lyric_flow_style", self._setting_value("v2_lyric_flow_style", "wave_stream"))),
            self._set_lyric_flow_style,
        )
        self._row(c, "文字流转", self.lyric_flow_style)
        self.gelv_color_fill_checkbox = self._check(
            "逐字染色",
            bool(getattr(self.island_widget, "gelv_color_fill_enabled", False)),
            self._set_gelv_color_fill_enabled,
        )
        c.addWidget(self.gelv_color_fill_checkbox)
        self.gelv_color_fill_fallback = ColorButton(
            str(getattr(self.island_widget, "gelv_color_fill_fallback", "#00D7FF")),
            self._set_gelv_color_fill_fallback,
        )
        self._row(c, "染色默认色", self.gelv_color_fill_fallback)
        self.no_lyrics_display_mode = self._combo(
            list(self.NO_LYRICS_DISPLAY_MODES),
            str(getattr(self.island_widget, "no_lyrics_display_mode", self._setting_value("v2_no_lyrics_display_mode", "text"))),
            self._set_no_lyrics_display_mode,
        )
        self._row(c, "无歌词模式", self.no_lyrics_display_mode)
        self.album_island_cover_count = self._combo(
            list(self.ALBUM_ISLAND_COVER_COUNTS),
            int(getattr(self.island_widget, "album_island_cover_count", self._setting_value("v2_album_island_cover_count", 5))),
            self._set_album_island_cover_count,
        )
        self._row(c, "专辑数量", self.album_island_cover_count)
```

- [ ] **Step 5: Hide/remove global font selector row**

Replace the current public font-row code in `_appearance_page()` with:

```python
        self.font_combo = None
```

Do not add `"全局字体"` as a visible row.

- [ ] **Step 6: Add callbacks and control refresh**

Add methods:

```python
    def _set_lyric_flow_style(self, _index: int = -1):
        style = str(self.lyric_flow_style.currentData() or "wave_stream")
        if hasattr(self.island_widget, "set_lyric_flow_style"):
            self.island_widget.set_lyric_flow_style(style, notify=False)
        else:
            self.island_widget.lyric_flow_style = style
            if style == "gelv_songscatter":
                self.island_widget.enable_dual_line_lyrics = False
            self.island_widget.update()
        self._save("v2_lyric_flow_style", style)
        if style == "gelv_songscatter":
            self._save("v2_enable_dual_line_lyrics", False)
        self._update_lyric_flow_controls()
        self._touch()

    def _set_gelv_color_fill_enabled(self, enabled: bool):
        if hasattr(self.island_widget, "set_gelv_color_fill_enabled"):
            self.island_widget.set_gelv_color_fill_enabled(bool(enabled), notify=False)
        else:
            self.island_widget.gelv_color_fill_enabled = bool(enabled)
            self.island_widget.update()
        self._save("v2_gelv_color_fill_enabled", bool(enabled))
        self._touch()

    def _set_gelv_color_fill_fallback(self):
        color = self.gelv_color_fill_fallback.color()
        if hasattr(self.island_widget, "set_gelv_color_fill_fallback"):
            self.island_widget.set_gelv_color_fill_fallback(color, notify=False)
        else:
            self.island_widget.gelv_color_fill_fallback = color
            self.island_widget.update()
        self._save("v2_gelv_color_fill_fallback", color)
        self._touch()

    def _set_no_lyrics_display_mode(self, _index: int = -1):
        mode = str(self.no_lyrics_display_mode.currentData() or "text")
        if hasattr(self.island_widget, "set_no_lyrics_display_mode"):
            self.island_widget.set_no_lyrics_display_mode(mode, notify=False)
        else:
            self.island_widget.no_lyrics_display_mode = mode
            self.island_widget.update()
        self._save("v2_no_lyrics_display_mode", mode)
        self._update_lyric_flow_controls()
        self._touch()

    def _set_album_island_cover_count(self, _index: int = -1):
        count = int(self.album_island_cover_count.currentData() or 5)
        if hasattr(self.island_widget, "set_album_island_cover_count"):
            self.island_widget.set_album_island_cover_count(count, notify=False)
        else:
            self.island_widget.album_island_cover_count = count
            self.island_widget.update()
        self._save("v2_album_island_cover_count", count)
        self._touch()

    def _update_lyric_flow_controls(self):
        style = str(getattr(self.island_widget, "lyric_flow_style", self._setting_value("v2_lyric_flow_style", "wave_stream")))
        gelv = style == "gelv_songscatter"
        for widget in (
            getattr(self, "gelv_color_fill_checkbox", None),
            getattr(self, "gelv_color_fill_fallback", None),
        ):
            if widget is not None:
                widget.setEnabled(gelv)
        if hasattr(self, "dual_line_lyrics_checkbox"):
            self.dual_line_lyrics_checkbox.setEnabled(not gelv)
            if gelv:
                self.dual_line_lyrics_checkbox.setChecked(False)
        album_mode = str(getattr(self.island_widget, "no_lyrics_display_mode", self._setting_value("v2_no_lyrics_display_mode", "text"))) == "album_island"
        if hasattr(self, "album_island_cover_count"):
            self.album_island_cover_count.setEnabled(album_mode)
```

Call `_update_lyric_flow_controls()` at the end of `_media_page()`.

- [ ] **Step 7: Run focused settings UI test**

Run: `python -m unittest tests.test_mac_preferences_shell.TestMacPreferencesShell.test_settings_window_scopes_gelv_controls_and_hides_global_font_selector -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add local/settings_window.py tests/test_mac_preferences_shell.py
git commit -m "feat: reorganize lyric settings"
```

## Task 3: Font Asset Contract

**Files:**
- Create: `assets/fonts/README.md`
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing font contract tests**

Add to `tests/test_island_stack_visual_messages.py`:

```python
def test_gelv_songscatter_font_family_is_source_han_serif_only(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    families = visual._gelv_font_families()

    self.assertEqual(families, ["Source Han Serif SC"])

def test_default_ui_font_is_pingfang(self):
    self.assertEqual(IslandStackVisual.DEFAULT_FONT_FAMILY, "PingFang SC")
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_gelv_songscatter_font_family_is_source_han_serif_only tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_default_ui_font_is_pingfang -v`

Expected: first test fails because `_gelv_font_families` does not exist.

- [ ] **Step 3: Add font helper**

Add to `IslandStackVisual`:

```python
    GELV_FONT_FAMILY = "Source Han Serif SC"

    def _gelv_font_families(self) -> list[str]:
        return [self.GELV_FONT_FAMILY]

    def _gelv_font(self, size: float, weight: QFont.Weight = QFont.Weight.Normal, shrink: bool = True) -> QFont:
        font = QFont()
        font.setFamilies(self._gelv_font_families())
        font.setPointSize(self._font_size(size, shrink=shrink))
        font.setWeight(weight)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font
```

- [ ] **Step 4: Add font README**

Create `assets/fonts/README.md`:

```markdown
# Font Assets

`格律宋散` requires Source Han Serif SC (`思源宋体`) and intentionally does not fall back to another typeface.

Development and packaging should use Adobe's official Source Han Serif repository:

- https://github.com/adobe-fonts/source-han-serif
- License: SIL Open Font License 1.1, as documented by the upstream repository.

The packaged build should include the Simplified Chinese Source Han Serif SC OTF/OTC asset and load it before rendering `格律宋散`.
```

- [ ] **Step 5: Run focused tests**

Run: `python -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_gelv_songscatter_font_family_is_source_han_serif_only tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_default_ui_font_is_pingfang -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add local/island_stack_visual.py tests/test_island_stack_visual_messages.py assets/fonts/README.md
git commit -m "feat: define gelv serif font contract"
```

## Task 4: Chunking And Long Lyric Split

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing chunk and split tests**

Add:

```python
def test_gelv_chunks_keep_punctuation_with_previous_unit(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    self.assertEqual(visual._gelv_chunks("你好，world again!"), ["你", "好，", "world", "again!"])

def test_gelv_split_long_cjk_line_gives_upper_extra_unit(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    split = visual._gelv_split_line("一二三四五六七", threshold=4)

    self.assertEqual(split, ("一二三四", "五六七"))

def test_gelv_split_long_english_line_does_not_split_words(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    split = visual._gelv_split_line("one two three four five", threshold=4)

    self.assertEqual(split, ("one two three", "four five"))

def test_gelv_short_line_does_not_split(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    self.assertIsNone(visual._gelv_split_line("短句", threshold=8))
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_gelv_chunks_keep_punctuation_with_previous_unit tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_gelv_split_long_cjk_line_gives_upper_extra_unit tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_gelv_split_long_english_line_does_not_split_words tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_gelv_short_line_does_not_split -v`

Expected: FAIL because helpers do not exist.

- [ ] **Step 3: Add helpers**

Add to `IslandStackVisual` near lyric chunk helpers:

```python
    def _gelv_chunks(self, text: str) -> list[str]:
        return [chunk.strip() for chunk in self._lyric_chunks(text) if str(chunk).strip()]

    def _gelv_split_line(self, text: str, threshold: int | None = None) -> tuple[str, str] | None:
        chunks = self._gelv_chunks(text)
        threshold = int(threshold if threshold is not None else 18)
        if len(chunks) <= max(1, threshold):
            return None
        upper_count = (len(chunks) + 1) // 2
        upper = "".join(chunks[:upper_count]).strip()
        lower = "".join(chunks[upper_count:]).strip()
        if not upper or not lower:
            return None
        return upper, lower
```

- [ ] **Step 4: Run focused tests**

Run the same command from Step 2.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add local/island_stack_visual.py tests/test_island_stack_visual_messages.py
git commit -m "feat: add gelv lyric splitting helpers"
```

## Task 5: Gelv Layout Metrics And Width Reservation

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing layout metric tests**

Add:

```python
def test_gelv_line_sizes_keep_ten_px_difference(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.lyric_font_size = 22

    active, next_size = visual._gelv_line_sizes(taskbar=False)

    self.assertEqual(active, 22)
    self.assertEqual(next_size, 12)

def test_gelv_compact_music_size_grows_for_two_rows(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.expanded = False
    visual.lyric_flow_style = "gelv_songscatter"
    visual.lyric_font_size = 22
    visual.lyrics = ["当前歌词", "下一句"]
    visual.lyric_index = 0

    _width, height = visual._main_size_for_kind(IslandKind.MUSIC, 420.0)

    self.assertGreaterEqual(height, 80.0 * IslandStackVisual.VISUAL_SCALE)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_gelv_line_sizes_keep_ten_px_difference tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_gelv_compact_music_size_grows_for_two_rows -v`

Expected: FAIL because `_gelv_line_sizes` does not exist or height does not grow.

- [ ] **Step 3: Add line size helper**

Add:

```python
    def _gelv_enabled(self) -> bool:
        return str(getattr(self, "lyric_flow_style", "wave_stream")) == "gelv_songscatter"

    def _gelv_line_sizes(self, taskbar: bool = False) -> tuple[int, int]:
        active = max(14, min(24, int(getattr(self, "lyric_font_size", 18))))
        if taskbar:
            active = max(12, active - 4)
        return active, max(8, active - 10)
```

Update `_main_size_for_kind()` branch for `IslandKind.MUSIC` so compact `格律宋散` returns at least the dual-line height:

```python
        if kind == IslandKind.MUSIC and not self.expanded and self._gelv_enabled():
            height = max(height, self._u(80.0))
```

- [ ] **Step 4: Include Gelv width in `_music_target_width()`**

Before returning compact width, calculate `gelv_w` when enabled:

```python
        if self._gelv_enabled():
            active_size, next_size = self._gelv_line_sizes(taskbar=False)
            gelv_lines = self._gelv_display_lines()
            gelv_w = max((self._gelv_layout_width(line, active_size if i == 0 else next_size) for i, line in enumerate(gelv_lines)), default=0.0)
            current_dual_w = max(current_dual_w, gelv_w)
```

Add helper:

```python
    def _gelv_layout_width(self, text: str, size: float, spacing_progress: float = 1.0) -> float:
        layout = self._lyric_layout(text, size, False, spacing_percent=int(100 + 50 * max(0.0, min(1.0, spacing_progress))))
        return layout.total_width
```

- [ ] **Step 5: Run focused tests**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add local/island_stack_visual.py tests/test_island_stack_visual_messages.py
git commit -m "feat: reserve space for gelv lyric layout"
```

## Task 6: Paint Gelv Songscatter Lyrics

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing routing test**

Add:

```python
def test_compact_lyric_viewport_routes_to_gelv_renderer(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.lyric_flow_style = "gelv_songscatter"
    visual.lyrics = ["当前歌词", "下一句"]
    visual.lyric_index = 0
    calls = []
    visual._paint_gelv_compact_lyrics = lambda *_args: calls.append("gelv")
    image = QImage(300, 90, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)

    visual._paint_compact_lyric_viewport(painter, QRectF(0.0, 0.0, 260.0, 70.0))
    painter.end()

    self.assertEqual(calls, ["gelv"])
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_compact_lyric_viewport_routes_to_gelv_renderer -v`

Expected: FAIL because `_paint_compact_lyric_viewport` does not route to Gelv.

- [ ] **Step 3: Add display-line helper**

Add:

```python
    def _gelv_display_lines(self) -> list[str]:
        if not getattr(self, "lyrics", None):
            return []
        active = self.lyrics[self.lyric_index % len(self.lyrics)]
        split = self._gelv_split_line(active)
        if split is not None:
            return [split[0], split[1]]
        next_line = self.lyrics[(self.lyric_index + 1) % len(self.lyrics)] if len(self.lyrics) > 1 else ""
        return [active, next_line] if next_line else [active]
```

- [ ] **Step 4: Add compact route**

At the top of `_paint_compact_lyric_viewport()` after `lines` is loaded:

```python
        if self._gelv_enabled():
            self._paint_gelv_compact_lyrics(painter, rect)
            painter.restore()
            return
```

- [ ] **Step 5: Add Gelv painter**

Add:

```python
    def _paint_gelv_compact_lyrics(self, painter: QPainter, rect: QRectF):
        phase = self._lyric_phase(time.monotonic())
        progress = max(0.0, min(1.0, float(phase.get("progress", 0.0))))
        active_size, next_size = self._gelv_line_sizes(taskbar=False)
        lines = self._gelv_display_lines()
        if not lines:
            return
        split_active = self._gelv_split_line(lines[0]) is not None
        upper_h = rect.height() * (0.56 if not split_active else 0.50)
        lower_h = rect.height() - upper_h
        upper = QRectF(rect.left(), rect.top(), rect.width(), upper_h)
        lower = QRectF(rect.left(), upper.bottom(), rect.width(), lower_h)
        lift = self._u(8.0 * math.sin(progress * math.pi))
        self._paint_gelv_line(painter, upper.translated(0.0, -lift), lines[0], active_size, progress, active=True)
        if len(lines) > 1 and not split_active:
            self._paint_gelv_line(painter, lower, lines[1], next_size, min(1.0, progress * 0.65), active=False)
        elif len(lines) > 1:
            self._paint_gelv_line(painter, lower, lines[1], active_size, progress, active=True)

    def _paint_gelv_line(self, painter: QPainter, rect: QRectF, text: str, size: float, progress: float, active: bool):
        spacing = int(100 + 50 * max(0.0, min(1.0, progress)))
        layout = self._lyric_layout(text, size, False, spacing_percent=spacing)
        x = rect.center().x() - layout.total_width / 2.0
        alpha = 245 if active else 156
        self._paint_elastic_lyric_chunks(
            painter,
            rect,
            x,
            layout,
            size,
            False,
            QColor(255, 255, 255, alpha),
            QColor(255, 255, 255, alpha),
            progress,
            0.0,
        )
```

- [ ] **Step 6: Run focused route test**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add local/island_stack_visual.py tests/test_island_stack_visual_messages.py
git commit -m "feat: render gelv compact lyrics"
```

## Task 7: Color Fill Effect And Black Background

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing color-selection tests**

Add:

```python
def test_gelv_fill_color_uses_fallback_for_neutral_album_color(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.album_color = QColor(120, 120, 120)
    visual.gelv_color_fill_fallback = "#00D7FF"

    color = visual._gelv_fill_color()

    self.assertEqual(color.name().upper(), "#00D7FF")

def test_gelv_fill_color_saturates_album_color(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.album_color = QColor(80, 120, 170)
    visual.gelv_color_fill_fallback = "#00D7FF"

    color = visual._gelv_fill_color()

    self.assertGreater(color.saturation(), visual.album_color.saturation())
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_gelv_fill_color_uses_fallback_for_neutral_album_color tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_gelv_fill_color_saturates_album_color -v`

Expected: FAIL because `_gelv_fill_color` does not exist.

- [ ] **Step 3: Add fill color helper**

Add:

```python
    def _gelv_fill_color(self) -> QColor:
        album = QColor(getattr(self, "album_color", QColor(0, 215, 255)))
        if album.saturation() < 28 or album.value() < 28 or album.value() > 238:
            fallback = QColor(str(getattr(self, "gelv_color_fill_fallback", "#00D7FF")))
            return fallback if fallback.isValid() else QColor("#00D7FF")
        hsv = album.toHsv()
        hsv.setHsv(hsv.hue(), max(190, hsv.saturation()), max(210, hsv.value()), 255)
        return hsv.toRgb()
```

- [ ] **Step 4: Add black background override**

In `_paint_music()`, before normal music background painting, branch:

```python
        gelv_black = self._gelv_enabled() and bool(getattr(self, "gelv_color_fill_enabled", False))
        if gelv_black:
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 255))
            painter.drawRoundedRect(rect, radius, radius)
            painter.restore()
        else:
            self._paint_music_glass_body(painter, rect, radius)
```

Keep the rest of the border/cover/text painting intact.

- [ ] **Step 5: Wire fill color into `_paint_gelv_line()`**

Inside `_paint_gelv_line()`, choose colors:

```python
        if bool(getattr(self, "gelv_color_fill_enabled", False)):
            live = self._gelv_fill_color()
            base = QColor(255, 255, 255, 120 if active else 82)
            live.setAlpha(250 if active else 178)
        else:
            base = QColor(255, 255, 255, 245 if active else 156)
            live = QColor(base)
```

Use `base` and `live` when calling `_paint_elastic_lyric_chunks`.

- [ ] **Step 6: Run focused tests**

Run command from Step 2.

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add local/island_stack_visual.py tests/test_island_stack_visual_messages.py
git commit -m "feat: add gelv color fill effect"
```

## Task 8: No-Lyrics Album Island Model And Layout

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing no-lyrics mode tests**

Add:

```python
def test_album_island_cover_count_allows_only_odd_choices(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)

    visual.set_album_island_cover_count(4, notify=False)
    self.assertEqual(visual.album_island_cover_count, 5)
    visual.set_album_island_cover_count(7, notify=False)
    self.assertEqual(visual.album_island_cover_count, 7)

def test_should_use_album_island_when_no_lyrics_mode_selected(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.no_lyrics_display_mode = "album_island"
    visual.lyrics = ["暂无歌词"]
    visual.no_lyrics_text = "暂无歌词"

    self.assertTrue(visual._should_paint_album_island())
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_album_island_cover_count_allows_only_odd_choices tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_should_use_album_island_when_no_lyrics_mode_selected -v`

Expected: FAIL because `_should_paint_album_island` does not exist or setter is missing before Task 1.

- [ ] **Step 3: Add album island predicate**

Add:

```python
    def _should_paint_album_island(self) -> bool:
        if str(getattr(self, "no_lyrics_display_mode", "text")) != "album_island":
            return False
        if self._is_instrumental_track():
            return True
        lyrics = [str(line).strip() for line in getattr(self, "lyrics", []) if str(line).strip()]
        if not lyrics:
            return True
        no_text = str(getattr(self, "no_lyrics_text", "暂无歌词")).strip()
        return len(lyrics) == 1 and lyrics[0] == no_text
```

- [ ] **Step 4: Add layout helper**

Add:

```python
    def _album_island_slots(self, rect: QRectF) -> list[dict[str, object]]:
        count = int(getattr(self, "album_island_cover_count", 5))
        if count not in (3, 5, 7):
            count = 5
        center = count // 2
        cover = min(rect.height() * 0.78, rect.width() / (count + 1.6))
        gap = cover * 0.28
        total = cover * count + gap * (count - 1)
        left = rect.center().x() - total / 2.0
        slots = []
        for index in range(count):
            distance = abs(index - center)
            scale = max(0.72, 1.0 - 0.12 * distance)
            alpha = max(70, 255 - 58 * distance)
            size = cover * scale
            x = left + index * (cover + gap) + (cover - size) / 2.0
            y = rect.center().y() - size / 2.0
            slots.append({"index": index, "rect": QRectF(x, y, size, size), "alpha": alpha, "center": index == center})
        return slots
```

- [ ] **Step 5: Route no-lyrics album island**

In `_paint_compact_lyric_viewport()` and `_paint_lyric_viewport()`, before normal no-lyrics text painting:

```python
        if self._should_paint_album_island():
            self._paint_album_island(painter, rect)
            painter.restore()
            return
```

Add minimal painter:

```python
    def _paint_album_island(self, painter: QPainter, rect: QRectF):
        painter.save()
        painter.setClipRect(rect)
        for slot in self._album_island_slots(rect):
            painter.setOpacity(float(slot["alpha"]) / 255.0)
            self._paint_cover(painter, slot["rect"], self._u(8.0 if slot["center"] else 6.0))
        if not getattr(self, "album_art", QImage()).isNull():
            pass
        painter.restore()
```

- [ ] **Step 6: Run focused tests**

Run command from Step 2.

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add local/island_stack_visual.py tests/test_island_stack_visual_messages.py
git commit -m "feat: add no lyrics album island layout"
```

## Task 9: Queue-Unavailable History Text And Track Shift

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing history text tests**

Add:

```python
def test_listened_hours_text_has_no_decimals(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.listened_seconds_total = 3660 * 12.4

    self.assertEqual(visual._album_island_listened_text(), "已经听歌 12 小时")

def test_album_history_moves_previous_track_left_on_change(self):
    visual = IslandStackVisual.__new__(IslandStackVisual)
    visual.album_history = []
    visual.media_track_id = "old"
    visual.track_title = "Old"

    visual._remember_album_history("new")

    self.assertEqual(visual.album_history[0]["track_id"], "old")
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_listened_hours_text_has_no_decimals tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_album_history_moves_previous_track_left_on_change -v`

Expected: FAIL because helpers do not exist.

- [ ] **Step 3: Add helpers**

Add:

```python
    def _album_island_listened_text(self) -> str:
        seconds = max(0.0, float(getattr(self, "listened_seconds_total", 0.0)))
        hours = int(round(seconds / 3600.0))
        return f"已经听歌 {hours} 小时"

    def _remember_album_history(self, incoming_track_id: str):
        history = list(getattr(self, "album_history", []))
        previous_id = str(getattr(self, "media_track_id", "") or "")
        if previous_id and previous_id != incoming_track_id:
            history.insert(0, {
                "track_id": previous_id,
                "title": str(getattr(self, "track_title", "") or ""),
                "album_art": getattr(self, "album_art", QImage()),
            })
        self.album_history = history[:6]
```

Call `_remember_album_history(incoming_track_id)` in `_apply_visual_data()` before overwriting `self.media_track_id`.

- [ ] **Step 4: Paint right-side text when queue is unavailable**

In `_paint_album_island()`, after cover slots:

```python
        text_rect = QRectF(rect.center().x() + rect.width() * 0.22, rect.top(), rect.width() * 0.28, rect.height())
        self._paint_scrolling_text(painter, text_rect, self._album_island_listened_text(), 10, QColor(255, 255, 255, 145), False, centered=True)
```

- [ ] **Step 5: Run focused tests**

Run command from Step 2.

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add local/island_stack_visual.py tests/test_island_stack_visual_messages.py
git commit -m "feat: add album island history fallback"
```

## Task 10: Visual Checks And Full Verification

**Files:**
- Modify: tests or local visual check scripts only if needed.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
python -m unittest tests.test_island_stack_visual_messages tests.test_mac_preferences_shell -v
```

Expected: PASS.

- [ ] **Step 2: Run provider tests to protect data boundary**

Run:

```bash
python -m unittest tests.test_provider_state -v
```

Expected: PASS.

- [ ] **Step 3: Run full test discovery**

Run:

```bash
python -m unittest discover tests -v
```

Expected: PASS.

- [ ] **Step 4: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output except existing line-ending warnings for untouched files if present.

- [ ] **Step 5: Manual visual verification**

Launch the app from `local/main.py` and verify:

- `波光逐流` remains default.
- `格律宋散` displays upper current lyric and lower next lyric.
- Upper/lower line size difference is visibly 10 px.
- Long current lyric splits into two centered large lines and hides lower next lyric.
- `逐字染色` forces black background and fills text with album/fallback color.
- No-lyrics text mode still shows the configured text.
- No-lyrics album island mode shows 3/5/7 centered album layout.
- Taskbar fusion uses reduced `格律宋散` sizing without losing hierarchy.

- [ ] **Step 6: Commit verification fixes**

If verification requires small fixes, commit them:

```bash
git add local tests assets docs
git commit -m "fix: polish gelv lyric verification"
```

## Self-Review

- Spec coverage: `格律宋散`, `逐字染色`, font rules, long lyric split, settings cleanup, no-lyrics album island, provider boundary, and taskbar applicability are covered.
- Placeholder scan: no `TBD`, `TODO`, or "implement later" placeholders are used in implementation steps.
- Type consistency: settings keys use `v2_lyric_flow_style`, `v2_gelv_color_fill_enabled`, `v2_gelv_color_fill_fallback`, `v2_no_lyrics_display_mode`, and `v2_album_island_cover_count` consistently.
