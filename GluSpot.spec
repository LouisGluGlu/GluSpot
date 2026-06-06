# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path(SPECPATH)
APP_VERSION = "6.4"

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "data"), "data"),
    (str(ROOT / "lyrics"), "lyrics"),
]

hiddenimports = [
    "winsdk.windows.media.control",
    "winsdk.windows.storage.streams",
    "winsdk.windows.foundation",
    "soundcard",
    "pywinauto",
    "uiautomation",
    "PIL",
]

a = Analysis(
    [str(ROOT / "local" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GluSpot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "brand" / "gluspot.ico"),
    version=str(ROOT / "packaging" / "windows" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=f"GluSpot-{APP_VERSION}",
)
