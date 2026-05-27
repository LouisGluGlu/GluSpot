# Mac Preferences Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将设置窗口与插件管理窗口改成接近 MyDockFinder/macOS 偏好设置的完整 UI 骨架，而不是只做轻微苹果风格。

**Architecture:** 增加一个小型 `local/mac_style.py` 样式 helper，集中提供 mac 偏好设置配色、侧栏、搜索框、标题栏、内容卡片、滚动条和输入控件 QSS。`SettingsWindow` 和 `PluginManagerWindow` 继续保留各自业务逻辑，只替换窗口骨架和样式。

**Tech Stack:** Python 3、PySide6、unittest。

---

### Task 1: Mac Style Helper

**Files:**
- Create: `local/mac_style.py`
- Test: `tests/test_mac_style.py`

- [ ] **Step 1: Write failing tests**

```python
from local.mac_style import mac_preferences_theme, mac_preferences_stylesheet

def test_mac_theme_has_preferences_regions():
    theme = mac_preferences_theme(False)
    assert theme["sidebar"] == "#c9c9c9"
    assert theme["content"] == "#f2f2f2"
    assert theme["accent"] == "#0a84ff"

def test_mac_stylesheet_contains_sidebar_search_and_titlebar_selectors():
    qss = mac_preferences_stylesheet(mac_preferences_theme(False))
    assert "QFrame#MacSidebar" in qss
    assert "QLineEdit#MacSearch" in qss
    assert "QFrame#MacTitleBar" in qss
```

- [ ] **Step 2: Run tests and verify red**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_mac_style`

Expected: FAIL because `local.mac_style` does not exist.

- [ ] **Step 3: Implement helper**

Create `local/mac_style.py` with:

- `mac_preferences_theme(dark: bool | None = None) -> dict[str, str]`
- `mac_preferences_stylesheet(theme: dict[str, str]) -> str`
- `mac_sidebar_icon_colors() -> tuple[str, ...]`

The QSS must include `QFrame#MacSidebar`, `QFrame#MacTrafficLights`, `QLineEdit#MacSearch`, `QFrame#MacTitleBar`, `QLabel#MacTitle`, `QListWidget#MacSidebarList`, `QFrame#MacCard`, `QScrollArea#MacContentScroll`.

- [ ] **Step 4: Run tests and verify green**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_mac_style`

Expected: OK.

---

### Task 2: Settings Window Mac Shell

**Files:**
- Modify: `local/settings_window.py`

- [ ] **Step 1: Replace shell layout**

Keep all pages and setting callbacks. Change `_build_ui()` so:

- Root is a horizontal split.
- Left side is `QFrame#MacSidebar`, fixed width around 194.
- Top of sidebar has `QFrame#MacTrafficLights` with red/yellow/gray dots.
- Under it is `QLineEdit#MacSearch`, placeholder `搜索`.
- Navigation is `QListWidget#MacSidebarList` with icon+text items.
- Right side is a vertical area with `QFrame#MacTitleBar` and content stack.
- `MacTitleBar` shows the selected page title.

- [ ] **Step 2: Restyle pages**

Change `_page()` and `_card()` object names to match mac helper:

- Scroll area object name `MacContentScroll`.
- Cards object name `MacCard`.
- Page margins match screenshot: 22 px left/right, 18 px top.
- Card radius 8 px, background light gray-ish.

- [ ] **Step 3: Preserve dark mode schedule controls**

Keep `界面模式 / 深色开始 / 深色结束` in the appearance page.

- [ ] **Step 4: Compile**

Run: `.\\.venv\\Scripts\\python.exe -m py_compile local/settings_window.py`

Expected: exit 0.

---

### Task 3: Plugin Manager Mac Shell

**Files:**
- Modify: `local/plugin_manager_window.py`

- [ ] **Step 1: Replace shell layout**

Use the same mac helper:

- Window minimum around `620x560`.
- Left `MacSidebar` with traffic lights and plugin list: 时间、天气、日程.
- Right `MacTitleBar` with current plugin title.
- Content area uses `MacContentScroll`.

- [ ] **Step 2: Preserve plugin-specific controls**

Keep existing time/weather/agenda controls and callbacks, but put them in the content stack with `MacCard` cards.

- [ ] **Step 3: Compile**

Run: `.\\.venv\\Scripts\\python.exe -m py_compile local/plugin_manager_window.py`

Expected: exit 0.

---

### Task 4: Verification

**Files:**
- All touched files

- [ ] **Step 1: Focused tests**

Run: `.\\.venv\\Scripts\\python.exe -m unittest tests.test_mac_style tests.test_ui_theme tests.test_plugin_provider tests.test_island_stack_visual_messages`

Expected: OK.

- [ ] **Step 2: Full tests**

Run: `.\\.venv\\Scripts\\python.exe -m unittest discover -s tests`

Expected: OK.

- [ ] **Step 3: Clean runtime artifacts**

Remove local `__pycache__` folders and any newly generated lyric cache files that are not tracked.

---

## Self-Review

- Spec coverage: 参考图中的侧栏、搜索、红黄绿窗口点、右侧标题栏、浅灰卡片和滚动内容均有任务覆盖。
- Placeholder scan: no TBD/placeholder.
- Type consistency: mac style helper selectors match the object names used by both windows.
