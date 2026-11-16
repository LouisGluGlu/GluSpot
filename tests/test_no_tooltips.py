import unittest

from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QApplication, QWidget

from local.no_tooltips import install


class NoToolTipsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_hover_tooltips_never_reach_widgets(self):
        seen = []

        class Probe(QWidget):
            def event(self, event):
                if event.type() == QEvent.Type.ToolTip:
                    seen.append(True)
                return super().event(event)

        widget = Probe()
        widget.setToolTip("hover text")
        flt = install(self.app)
        self.assertIs(install(self.app), flt)
        QApplication.sendEvent(widget, QHelpEvent(QEvent.Type.ToolTip, QPoint(1, 1), QPoint(1, 1)))
        self.assertEqual(seen, [])
        self.assertEqual(widget.toolTip(), "hover text")
        self.app.removeEventFilter(flt)
        del self.app._gluspot_no_tooltips


if __name__ == "__main__":
    unittest.main()
