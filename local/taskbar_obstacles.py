"""Where the taskbar's own content sits, so the docked island never covers it.

On Windows 11 the taskbar icons live in a XAML island. The legacy child
windows (``MSTaskListWClass``) still exist but report a strip far narrower
than the real icons (220 px against about 400 px), so zones cut from it put
the island on top of the icons. UI Automation reports the real icon strip
(``TaskbarFrame``'s children). A query takes ~80 ms, so it runs on a daemon
thread every two seconds and paint/placement code only reads the cache.

Coordinates are physical screen pixels, the same space as ``GetWindowRect``.
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QRectF

REFRESH_SECONDS = 2.0
STALE_SECONDS = 10.0


def _query_uia_items():
    """Bounding rects of the taskbar frame's content (icons, widgets)."""
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        bar = auto.PaneControl(searchDepth=1, ClassName="Shell_TrayWnd")
        frame = bar.Control(searchDepth=3, AutomationId="TaskbarFrame")
        if not frame.Exists(0.3, 0.1):
            return None
        whole = frame.BoundingRectangle
        items = []
        for child in frame.GetChildren():
            box = child.BoundingRectangle
            width, height = box.right - box.left, box.bottom - box.top
            # Skip anything as wide as the bar itself (a background pane).
            if width <= 0 or height <= 0 or width >= (whole.right - whole.left) * 0.9:
                continue
            items.append(QRectF(box.left, box.top, width, height))
        return items


class TaskbarObstacleProbe:
    """Cached UI Automation view of the taskbar content, refreshed off the UI thread."""

    def __init__(self, query=_query_uia_items, refresh_seconds: float = REFRESH_SECONDS):
        self._query = query
        self._refresh = float(refresh_seconds)
        self._lock = threading.Lock()
        self._items: list[QRectF] | None = None
        self._at = 0.0
        self._running = False
        self._failures = 0

    def items(self, now: float | None = None) -> list[QRectF] | None:
        """The last content rects, or None when unknown (use the fallback)."""
        now = time.monotonic() if now is None else float(now)
        with self._lock:
            items, at, running = self._items, self._at, self._running
            due = not running and now - at >= self._refresh and self._failures < 5
            if due:
                self._running = True
        if due:
            threading.Thread(target=self._run, name="GluSpotTaskbarProbe", daemon=True).start()
        if items is None or now - at > STALE_SECONDS:
            return None
        return [QRectF(item) for item in items]

    def _run(self):
        try:
            found = self._query()
        except Exception:
            found = None
        with self._lock:
            self._running = False
            if found is None:
                self._failures += 1
                return
            self._failures = 0
            self._items = list(found)
            self._at = time.monotonic()


def taskbar_obstacles(taskbar: QRectF, child_rects, uia_items) -> list[QRectF]:
    """Everything on the taskbar the island must not cover.

    ``uia_items`` (the real icon strip and widgets) when known; otherwise the
    span from the Start button to the end of the legacy task list. The tray
    (``TrayNotifyWnd``) always counts.
    """
    bar = QRectF(taskbar)
    obstacles: list[QRectF] = []
    legacy = {name: QRectF(rect) for name, rect in child_rects or ()}
    if uia_items:
        obstacles.extend(QRectF(item).intersected(bar) for item in uia_items)
    else:
        start, tasks = legacy.get("Start"), legacy.get("MSTaskListWClass") or legacy.get("MSTaskSwWClass")
        boxes = [box for box in (start, tasks) if box is not None and not box.isNull()]
        if boxes:
            left = min(box.left() for box in boxes)
            right = max(box.right() for box in boxes)
            obstacles.append(QRectF(left, bar.top(), right - left, bar.height()))
    tray = legacy.get("TrayNotifyWnd")
    if tray is not None and not tray.isNull():
        obstacles.append(tray.intersected(bar))
    # Full-height bands, a little wider than the content, so the island keeps
    # clear of the first and last icon.
    margin = 8.0
    return [QRectF(box.left() - margin, bar.top(), box.width() + 2 * margin, bar.height())
            for box in obstacles if not box.isNull() and box.width() > 0]
