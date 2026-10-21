"""Qt 5 core bindings with the Qt 6 spellings used by GluSpot.

PySide2 5.15.2.1 already supplies the scoped enum members used by the UI; keep
those native enum types and their values rather than duplicating constants.
"""
from PySide2.QtCore import *  # noqa: F401,F403
from PySide2 import QtCore as _native

if not hasattr(QLibraryInfo, "LibraryPath"):
    QLibraryInfo.LibraryPath = QLibraryInfo.LibraryLocation
if not hasattr(QLibraryInfo, "path"):
    QLibraryInfo.path = staticmethod(QLibraryInfo.location)

if not hasattr(QCoreApplication, "exec"):
    QCoreApplication.exec = staticmethod(QCoreApplication.exec_)
for _class in (QEventLoop, QThread):
    if not hasattr(_class, "exec"):
        _class.exec = _class.exec_

__version__ = _native.__version__
