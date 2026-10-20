"""Existing frameless music pages share the player's ordinary resize bounds."""
from PySide6.QtCore import QPoint, QRect, Qt


class MusicPageResizeMixin:
    """Native edge resize with a bounded fallback for unsupported platforms."""

    def _page_resize_enabled(self):
        return getattr(self, "page", "sources") == "sources"

    def _page_resize_edges(self, position):
        if not self._page_resize_enabled():
            return Qt.Edge(0)
        edges = Qt.Edge(0)
        if position.x() <= 8:
            edges |= Qt.Edge.LeftEdge
        elif position.x() >= self.width() - 9:
            edges |= Qt.Edge.RightEdge
        if position.y() <= 8:
            edges |= Qt.Edge.TopEdge
        elif position.y() >= self.height() - 9:
            edges |= Qt.Edge.BottomEdge
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
            horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
            vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
            if horizontal and vertical:
                forward = bool(edges & Qt.Edge.LeftEdge) == bool(edges & Qt.Edge.TopEdge)
                cursor = Qt.CursorShape.SizeFDiagCursor if forward else Qt.CursorShape.SizeBDiagCursor
            else:
                cursor = (Qt.CursorShape.SizeHorCursor if horizontal else
                          Qt.CursorShape.SizeVerCursor if vertical else Qt.CursorShape.ArrowCursor)
            self.setCursor(cursor)
        return False

    def _end_page_resize(self):
        self._page_resize_origin = None
