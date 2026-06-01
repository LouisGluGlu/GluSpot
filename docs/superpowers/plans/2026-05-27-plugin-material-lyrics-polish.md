# Plugin Material Lyrics Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make plugin expansion feel like one elastic island, improve Finder material positional matching, and reduce lyric lookup latency with earlier preload and source ranking.

**Architecture:** Keep the existing V2 stack and provider boundaries. Plugin interaction remains in `local/island_stack_visual.py`; Finder sampling remains in `local/mydockfinder.py`; lyric lookup and preload remain in `api/lyrics.py` and `api/providers.py`.

**Tech Stack:** Python, PySide6, unittest, Windows media provider APIs.

---

### Task 1: Plugin Panel Becomes One Joined Island

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] Write tests that expanded plugin panel has no visual gap from the plugin strip and uses a connected shell rectangle.
- [ ] Run `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages.IslandStackVisualMessageTests.test_plugin_panel_joins_strip_without_gap` and confirm it fails.
- [ ] Modify `_build_visuals()` so plugin strip to panel uses a zero join gap while preserving normal gaps to messages below.
- [ ] Modify panel painting so the panel shell paints as a continuation behind the strip, not as a second independent rounded pill.
- [ ] Run the targeted test and confirm it passes.

### Task 2: Plugin Panel Hover Collapse Direction

**Files:**
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

- [ ] Write tests for three cases: moving from open plugin panel up to music closes the panel; moving down to a message keeps it open; clicking a message still acts normally.
- [ ] Run the targeted tests and confirm they fail.
- [ ] Add `_should_close_plugin_panel_for_hover()` and call it from `mouseMoveEvent()` after hit testing.
- [ ] Close only `plugin_panel_open`, not global `expanded`, when pointer moves to the music island.
- [ ] Run the targeted tests and confirm they pass.

### Task 3: Finder Material Uses Full Screen Position Crop

**Files:**
- Modify: `local/mydockfinder.py`
- Modify: `local/island_stack_visual.py`
- Test: `tests/test_island_stack_visual_messages.py`

Superseded by `docs/superpowers/plans/2026-06-01-finder-material-replication-fix.md`: Finder fused material must replicate the visible upper Finder bar material and must not use a full-screen wallpaper/screen crop as the island/cradle background.

### Task 4: Lyric Preload Starts On Current Track Read

**Files:**
- Modify: `api/providers.py`
- Test: `tests/test_provider_state.py`

- [ ] Write a provider test that `_read_visual_data()` starts a background lyric lookup for the current track before adjacent prefetch.
- [ ] Run the targeted test and confirm it fails if current-track preload is skipped.
- [ ] Add `_ensure_lyrics_lookup_started()` to centralize cached/local/pending/background lookup checks.
- [ ] Use it in `_read_visual_data()` before adjacent-track preload and in `_prefetch_lyrics_for_track()`.
- [ ] Run targeted tests and confirm they pass.

### Task 5: Lyric Source Ranking From Hit Logs

**Files:**
- Modify: `api/lyrics.py`
- Test: `tests/test_lyrics_store.py`

- [ ] Write tests that successful source hits are counted and source resolver order prefers the highest-hit source while keeping deterministic fallback order.
- [ ] Run targeted tests and confirm they fail.
- [ ] Add in-memory source hit counters and `_ordered_online_resolvers()`.
- [ ] Record a source hit only when the resolver returns usable lyrics.
- [ ] Run targeted lyric tests and provider tests.

### Verification

- [ ] Run `.\.venv\Scripts\python.exe -m unittest tests.test_island_stack_visual_messages`.
- [ ] Run `.\.venv\Scripts\python.exe -m unittest tests.test_provider_state tests.test_lyrics_store`.
- [ ] Run `.\.venv\Scripts\python.exe -m unittest discover -s tests`.
- [ ] Run `.\.venv\Scripts\python.exe -m py_compile local\island_stack_visual.py local\mydockfinder.py api\providers.py api\lyrics.py`.
- [ ] Run a Qt smoke test for plugin hover open, music-hover collapse, and message-down keep-open behavior.
