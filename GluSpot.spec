# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata


ROOT = Path(SPECPATH)
APP_VERSION = "6.412"
AIRPLAY_EXE = ROOT / "build" / "native-airplay" / "GluSpotAirPlay.exe"
AIRPLAY_LICENSES = AIRPLAY_EXE.parent / "licenses"
DISCOVERY_LICENSES = ROOT / "build" / "audio-licenses"
if not AIRPLAY_EXE.is_file() or not AIRPLAY_LICENSES.is_dir():
    raise RuntimeError("Build the AirPlay helper with packaging/windows/build-airplay.ps1 before packaging")
if not (DISCOVERY_LICENSES / "SOURCE.json").is_file():
    raise RuntimeError("Run packaging/windows/prepare-audio-licenses.py before packaging")

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "data"), "data"),
    (str(ROOT / "lyrics"), "lyrics"),
    (str(AIRPLAY_LICENSES), "native/licenses"),
    (str(DISCOVERY_LICENSES), "licenses/audio-discovery"),
]
# The bridge source and notices are always inspectable. Its optional prepared
# runtime is included only when installation has completed; otherwise source
# availability reports the missing dependency rather than creating fake login.
ONLINE_MUSIC_SOURCE = ROOT / "native" / "online_music"
ONLINE_MUSIC_RUNTIME = ROOT / "build" / "online-music"
if ONLINE_MUSIC_SOURCE.is_dir():
    datas += [(str(ONLINE_MUSIC_SOURCE / "bridge.cjs"), "online_music"),
              (str(ONLINE_MUSIC_SOURCE / "package.json"), "online_music"),
              (str(ONLINE_MUSIC_SOURCE / "package-lock.json"), "online_music"),
              (str(ONLINE_MUSIC_SOURCE / "licenses"), "online_music/licenses")]
if (ONLINE_MUSIC_RUNTIME / "node.exe").is_file() and (ONLINE_MUSIC_RUNTIME / "node_modules").is_dir():
    if not (ONLINE_MUSIC_RUNTIME / "SOURCE.json").is_file() or not (ONLINE_MUSIC_RUNTIME / "licenses" / "Node-LICENSE.txt").is_file():
        raise RuntimeError("Run native/online_music/prepare-runtime.ps1 before packaging the optional online runtime")
    datas += [(str(ONLINE_MUSIC_RUNTIME / "node.exe"), "online_music"),
              (str(ONLINE_MUSIC_RUNTIME / "node_modules"), "online_music/node_modules"),
              (str(ONLINE_MUSIC_RUNTIME / "SOURCE.json"), "online_music"),
              (str(ONLINE_MUSIC_RUNTIME / "licenses"), "online_music/licenses")]

datas += collect_data_files("ToJyutping") + copy_metadata("ToJyutping")
datas += collect_data_files("zeroconf") + copy_metadata("zeroconf") + copy_metadata("ifaddr")

hiddenimports = [
    "winsdk.windows.media.control",
    "winsdk.windows.storage.streams",
    "winsdk.windows.foundation",
    "soundcard",
    "pywinauto",
    "uiautomation",
    "PIL",
    "ToJyutping",
    "zeroconf",
    "ifaddr",
]

a = Analysis(
    [str(ROOT / "local" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[(str(AIRPLAY_EXE), "native")],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "windows" / "hooks")],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "windows" / "runtime_qt_dlls.py")],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Qt6Core.dll imports the unversioned Windows ICU API (for example ucnv_open).
# PyInstaller can pick up Poppler's versioned ICU from the build host's PATH;
# placing that icuuc.dll beside the app makes the Windows loader select it first.
# Let Qt use the compatible Windows system ICU instead.
a.binaries = [
    entry for entry in a.binaries
    if Path(entry[0]).name.lower() not in {"icuuc.dll", "icudt78.dll"}
]
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
    icon=str(ROOT / "assets" / "brand" / "gluspot-glu.ico"),
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
