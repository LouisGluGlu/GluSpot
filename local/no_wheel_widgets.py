from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QSlider, QSpinBox
from local.control_hover import install_control_hover


class NoWheelComboBox(QComboBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        install_control_hover(self, menu_callback=self.showPopup)

    def wheelEvent(self, event):
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        install_control_hover(self)

    def wheelEvent(self, event):
        event.ignore()


class NoWheelSlider(QSlider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        install_control_hover(self)

    def wheelEvent(self, event):
        event.ignore()
