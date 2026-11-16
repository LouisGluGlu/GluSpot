"""No hover text anywhere: tooltips are swallowed before any widget sees them.

Widgets keep their tooltip strings (some tests and accessibility helpers read
them), but nothing pops up on hover. Messages that matter are shown through
the boxed page notice (``local.glass_notice``) instead.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication

BLOCKED = frozenset({QEvent.Type.ToolTip, QEvent.Type.WhatsThis, QEvent.Type.QueryWhatsThis})


class NoToolTipsFilter(QObject):
    def eventFilter(self, watched, event):  # noqa: N802 - Qt name
        return event.type() in BLOCKED


def install(app: QApplication | None = None) -> NoToolTipsFilter | None:
    app = app or QApplication.instance()
    if app is None:
        return None
    existing = getattr(app, "_gluspot_no_tooltips", None)
    if existing is not None:
        return existing
    flt = NoToolTipsFilter(app)
    app.installEventFilter(flt)
    app._gluspot_no_tooltips = flt
    return flt
