from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from local.no_wheel_widgets import NoWheelComboBox, NoWheelSpinBox


class FakeWheelEvent:
    def __init__(self):
        self.ignored = False

    def ignore(self):
        self.ignored = True


class NoWheelWidgetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_combo_box_ignores_wheel_event(self):
        event = FakeWheelEvent()

        NoWheelComboBox().wheelEvent(event)

        self.assertTrue(event.ignored)

    def test_spin_box_ignores_wheel_event(self):
        event = FakeWheelEvent()

        NoWheelSpinBox().wheelEvent(event)

        self.assertTrue(event.ignored)


if __name__ == "__main__":
    unittest.main()
