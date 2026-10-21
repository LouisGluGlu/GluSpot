"""Prepare only the frozen legacy Qt 5 DLL/plugin directories."""
import os
from pathlib import Path
import sys

_handles = []
if sys.platform == "win32" and hasattr(sys, "_MEIPASS"):
    root = Path(sys._MEIPASS)
    for relative in ("", "PySide2", "shiboken2"):
        directory = root / relative
        if directory.is_dir():
            if hasattr(os, "add_dll_directory"):
                try:
                    _handles.append(os.add_dll_directory(str(directory)))
                except (OSError, NotImplementedError):
                    # On Win7 this API also depends on the OS DLL-loader update.
                    # Preserve PATH fallback; it cannot supply a missing UCRT.
                    pass
            os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
