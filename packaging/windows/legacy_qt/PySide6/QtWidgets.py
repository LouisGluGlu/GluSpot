"""Native Qt 5 widgets used unchanged by GluSpot's V2 surfaces."""
from PySide2.QtWidgets import *  # noqa: F401,F403

if not hasattr(QApplication, "exec"):
    QApplication.exec = staticmethod(QApplication.exec_)
for _class in (QDialog, QMenu):
    if not hasattr(_class, "exec"):
        _class.exec = _class.exec_
