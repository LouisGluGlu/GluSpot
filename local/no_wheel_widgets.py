from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QSpinBox


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()
