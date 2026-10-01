"""Register packaged Qt DLL locations before PySide6 is imported."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


_DLL_DIRECTORY_HANDLES = []
_PRELOADED_DLLS = []


def _initialize_packaged_qt_dlls() -> None:
    if sys.platform != "win32" or not hasattr(sys, "_MEIPASS"):
        return
    bundle_root = Path(sys._MEIPASS)
    pyside_dir = bundle_root / "PySide6"
    shiboken_dir = bundle_root / "shiboken6"
    for directory in (bundle_root, shiboken_dir, pyside_dir):
        if not directory.is_dir():
            continue
        _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))
        os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
    for library in (
        pyside_dir / "Qt6Core.dll",
        shiboken_dir / "shiboken6.abi3.dll",
        pyside_dir / "pyside6.abi3.dll",
    ):
        if library.is_file():
            _PRELOADED_DLLS.append(ctypes.WinDLL(str(library)))


_initialize_packaged_qt_dlls()
