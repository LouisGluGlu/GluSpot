"""Build-only Qt 6 import facade for the Windows 7 Qt 5 package.

The normal application continues to import the real PySide6 package. Only the
legacy build adds this directory to its module search path. Native objects and
rendering come from PySide2; this module does not emulate a Qt installation.
"""
from PySide2 import __version__, __version_info__

__all__ = ["QtCore", "QtGui", "QtWidgets", "QtSvg", "QtMultimedia", "QtNetwork"]
GLUSPOT_LEGACY_QT = True
__legacy_qt5__ = True
