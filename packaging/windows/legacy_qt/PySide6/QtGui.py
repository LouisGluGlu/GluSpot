"""Qt 5 painting and input bindings for the build-only legacy facade."""
from PySide2.QtGui import *  # noqa: F401,F403
from PySide2.QtWidgets import QAction, QActionGroup, QShortcut

# Patch the native input classes, not subclasses. Events delivered by Qt itself
# must have exactly the same coordinate accessors as explicitly mapped events.
for _event_class in (QMouseEvent, QEnterEvent):
    if not hasattr(_event_class, "position"):
        _event_class.position = _event_class.localPos
    if not hasattr(_event_class, "globalPosition"):
        _event_class.globalPosition = _event_class.screenPos


def _painter_enter(self):
    return self


def _painter_exit(self, exception_type, exception, traceback):
    self.end()
    return False


if not hasattr(QPainter, "__enter__"):
    QPainter.__enter__ = _painter_enter
    QPainter.__exit__ = _painter_exit

# These became static in Qt 6. Hold the original native descriptors so an
# application call does not recurse through our static entry point.
_font_database_families = QFontDatabase.families
_font_database_writing_systems = QFontDatabase.writingSystems


def _families(writing_system=QFontDatabase.WritingSystem.Any):
    return _font_database_families(QFontDatabase(), writing_system)


def _writing_systems(family=None):
    database = QFontDatabase()
    if family is None:
        return _font_database_writing_systems(database)
    return _font_database_writing_systems(database, family)


QFontDatabase.families = staticmethod(_families)
QFontDatabase.writingSystems = staticmethod(_writing_systems)

if not hasattr(QGuiApplication, "exec"):
    QGuiApplication.exec = staticmethod(QGuiApplication.exec_)
