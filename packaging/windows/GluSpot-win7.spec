# -*- mode: python ; coding: utf-8 -*-
"""Separate Python 3.8 / Qt 5 artifact; never replaces the modern package."""
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, copy_metadata
from PyInstaller.building.datastruct import Tree

ROOT = Path(SPECPATH).parents[1]
if sys.version_info[:2] != (3, 8) or sys.maxsize <= 2 ** 32:
    raise RuntimeError("The Win7 x64 build requires its isolated CPython 3.8 x64 toolchain")
LEGACY = ROOT / "packaging/windows/legacy_qt"
ONLINE = ROOT / "build/online-music-win7"
AIRPLAY = ROOT / "build/native-airplay/GluSpotAirPlay.exe"
LICENSES = ROOT / "build/audio-licenses-win7"
if not (ONLINE / "SOURCE.json").is_file() or not (ONLINE / "bridge.cjs").is_file():
    raise RuntimeError("Prepare the separate legacy online runtime before packaging")
if not AIRPLAY.is_file():
    raise RuntimeError("Build the native AirPlay helper before packaging")
if not (LICENSES / "SOURCE.json").is_file():
    raise RuntimeError("Prepare the licenses from the pinned legacy environment before packaging")

datas = [(str(ROOT / directory), directory) for directory in ("assets", "data", "lyrics")]
datas += [(str(ROOT / "packaging/windows/README-Win7.txt"), ".")]
datas += [(str(AIRPLAY.parent / "licenses"), "native/licenses")]
for package in ("ToJyutping", "zeroconf"):
    datas += collect_data_files(package) + copy_metadata(package)
datas += copy_metadata("ifaddr")

a = Analysis(
    [str(ROOT / "local/main.py")], pathex=[str(LEGACY), str(ROOT)],
    binaries=[(str(AIRPLAY), "native"), (str(ONLINE / "node.exe"), "online_music")], datas=datas,
    hiddenimports=["PySide2.QtCore", "PySide2.QtGui", "PySide2.QtWidgets", "PySide2.QtMultimedia",
                   "PySide2.QtSvg", "PySide2.QtNetwork", "shiboken2", "numpy", "PIL",
                   "ToJyutping", "zeroconf", "ifaddr"],
    hookspath=[str(LEGACY / "hooks"), str(ROOT / "packaging/windows/hooks")],
    runtime_hooks=[str(LEGACY / "runtime_qt5.py")],
    excludes=["winsdk", "pywinauto", "uiautomation", "win32com", "soundcard",
              "win32api", "win32gui", "win32process", "win32con", "pywintypes",
              "pythoncom", "win32timezone", "win32pdh", "win32wnet", "netbios",
              "cffi", "_cffi_backend"],
    noarchive=False, optimize=0,
)
# Qt's multimedia hook collects every audio plugin. The WinRT WASAPI plugin
# imports APIs absent on Win7; the actual legacy backend uses WinMM/DirectShow.
# No application module uses Qt Bluetooth/QML or the WinRT C++ helper either.
legacy_unused_winrt = {"qtaudio_wasapi.dll", "qtposition_winrt.dll",
                       "qt5bluetooth.dll", "vccorlib140.dll", "ucrtbase.dll",
                       "qpdf.dll", "qt5pdf.dll", "qtvirtualkeyboardplugin.dll",
                       "qt5virtualkeyboard.dll", "qwebgl.dll", "qt5websockets.dll"}
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in legacy_unused_winrt
              and not Path(entry[0]).name.lower().startswith("api-ms-win-")]
# Use the target OS UCRT update, never a Windows 11 UCRT/API-set copy found
# through a build-host tool's PATH. The legacy installer checks this prerequisite.
# PyInstaller's runtime-hook registry sees the facade's import name even with
# our no-op analysis hook. Only PySide2 is a native binding in this artifact;
# its genuine runtime hook owns Qt's plugin paths and single-binding guard.
a.scripts = [entry for entry in a.scripts
             if Path(entry[1]).name != "pyi_rth_pyside6.py"]
# These verified source/notice trees contain no native modules. Collect them as
# data after analysis; node.exe above still receives normal dependency analysis.
a.datas += Tree(str(ONLINE), prefix="online_music", excludes=["node.exe"])
a.datas += Tree(str(LICENSES), prefix="licenses/legacy-runtime")
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="GluSpot", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False, target_arch=None,
    icon=str(ROOT / "assets/brand/gluspot-glu.ico"),
    version=str(ROOT / "packaging/windows/version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name="GluSpot-6.412-Win7-x64")
