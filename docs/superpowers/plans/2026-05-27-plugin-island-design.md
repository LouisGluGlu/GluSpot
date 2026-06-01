# Plugin Island Design Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the plugin island opaque by default and redesign its expanded plugin content around an Apple-like time, weather, and agenda layout.

**Architecture:** Keep plugin data behind `api/plugin_provider.py` and keep island rendering inside `local/island_stack_visual.py`. Finder material remains the only transparent/adaptive material; plugin, message, alert, and music base shells become opaque standalone surfaces.

**Tech Stack:** Python, PySide6 `QPainter`, `unittest`.

---

### Task 1: Opaque Island Material

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests that `_plugin_theme_colors()` returns alpha `255` for plugin shell, cells, and alert surfaces, and that `_paint_black_glass()` / `_paint_music_glass_body()` source code no longer uses semi-transparent base fills for standalone island bodies.

- [ ] **Step 2: Run tests to verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_plugin_theme_surfaces_are_opaque`

Expected: FAIL because current plugin theme alphas are below `255`.

- [ ] **Step 3: Implement opacity**

Set plugin shell, expanded shell, cells, active cells, and alert base surfaces to alpha `255`. Set music and black glass base body fills to alpha `255`, while retaining semi-transparent highlights, strokes, and Finder material rendering.

- [ ] **Step 4: Run tests**

Run the new focused tests and the existing plugin visual tests.

### Task 2: Time Plugin Yellow Theme And Three-Zone Panel

**Files:**
- Modify: `api/plugin_provider.py`
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_plugin_provider.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests that the time plugin exports a yellow/gold accent, `_plugin_panel_sections()` returns `clock`, `countdown`, and `alarm` zones for TIME, and `_plugin_panel_layout()` exposes stable rectangles for those zones.

- [ ] **Step 2: Run tests to verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_plugin_provider.PluginProviderTests.test_time_plugin_uses_yellow_accent tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_time_plugin_panel_has_clock_countdown_and_alarm_sections`

Expected: FAIL because time currently uses blue and the panel has no section model.

- [ ] **Step 3: Implement minimal code**

Update provider accent to Apple-like yellow. Add helper methods for plugin display color, plugin panel sections, and plugin panel content layout. Specialize TIME painting into a left analog clock, middle countdown module, and right alarm/settings module. Keep bottom reminder rail.

- [ ] **Step 4: Run tests**

Run the focused provider and visual tests.

### Task 3: Weather And Agenda Initial Functional Layout

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] **Step 1: Write failing tests**

Add tests that WEATHER sections expose current weather, metrics, and source/action; AGENDA sections expose next item, todo roadmap, and import/reminder affordance.

- [ ] **Step 2: Run tests to verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_weather_and_agenda_panel_sections_are_functional`

Expected: FAIL because only generic lines are exposed.

- [ ] **Step 3: Implement minimal code**

Specialize WEATHER and AGENDA panel painting using the same opaque module vocabulary, while preserving the existing contextual action commands and reminder rail.

- [ ] **Step 4: Run tests**

Run focused tests, then the full test suite.

### Task 4: Verification

**Files:**
- Test only.

- [ ] Run `.\.venv\Scripts\python.exe -m py_compile local\island_stack_visual.py api\plugin_provider.py`
- [ ] Run `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages tests.test_plugin_provider`
- [ ] Run `.\.venv\Scripts\python.exe -m unittest discover -s tests`
- [ ] Inspect `git diff --check`
