"""Existing frameless music pages share the player's ordinary resize bounds."""
from PySide6.QtCore import QPoint, QRect, Qt
from local.music_resize_hit_test import NativeMusicResizeMixin, resize_cursor, resize_edges


class MusicPageResizeMixin(NativeMusicResizeMixin):
    """Native edge resize with a bounded fallback for unsupported platforms."""

    def _page_resize_enabled(self):
        return getattr(self, "page", "sources") == "sources"

    def _native_resize_enabled(self):
        return self._page_resize_enabled() and super()._native_resize_enabled()

    def _page_resize_edges(self, position):
        if not self._page_resize_enabled():
            return Qt.Edge(0)
        names = resize_edges(position.x(), position.y(), self.width(), self.height(),
                             dpr=self.devicePixelRatioF())
        edges = Qt.Edge(0)
        for name, edge in (("left", Qt.Edge.LeftEdge), ("right", Qt.Edge.RightEdge),
                           ("top", Qt.Edge.TopEdge), ("bottom", Qt.Edge.BottomEdge)):
            if name in names:
                edges |= edge
        return edges

    def _begin_page_resize(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        edges = self._page_resize_edges(event.position())
        if not edges:
            return False
        handle = self.windowHandle()
        if handle is None or not handle.startSystemResize(edges):
            self._page_resize_origin = (event.globalPosition().toPoint(), QRect(self.geometry()), edges)
        event.accept()
        return True

    def _move_page_resize(self, event):
        origin = getattr(self, "_page_resize_origin", None)
        if origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            pointer, rect, edges = origin
            delta = event.globalPosition().toPoint() - pointer
            left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
            if edges & Qt.Edge.LeftEdge:
                left = min(right - self.minimumWidth() + 1, left + delta.x())
            if edges & Qt.Edge.RightEdge:
                right = max(left + self.minimumWidth() - 1, right + delta.x())
            if edges & Qt.Edge.TopEdge:
                top = min(bottom - self.minimumHeight() + 1, top + delta.y())
            if edges & Qt.Edge.BottomEdge:
                bottom = max(top + self.minimumHeight() - 1, bottom + delta.y())
            self.setGeometry(QRect(QPoint(left, top), QPoint(right, bottom)))
            event.accept()
            return True
        if event.buttons() == Qt.MouseButton.NoButton:
            edges = self._page_resize_edges(event.position())
            names = {name for name, edge in (("left", Qt.Edge.LeftEdge), ("right", Qt.Edge.RightEdge),
                                            ("top", Qt.Edge.TopEdge), ("bottom", Qt.Edge.BottomEdge))
                     if edges & edge}
            self.setCursor(resize_cursor(names))
        return False

    def _end_page_resize(self):
        self._page_resize_origin = None
