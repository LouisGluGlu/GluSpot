"""Collect the legacy runtime's actual notices and checked LGPL sources.

Run with the pinned CPython 3.8 build environment. This deliberately does not
reuse the modern package's zeroconf sources or import any Qt/native module.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
from importlib.metadata import distribution
import json
from pathlib import Path
import re
import shutil
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "PySide2": "5.15.2.1", "shiboken2": "5.15.2.1", "numpy": "1.24.4",
    "Pillow": "10.4.0", "ToJyutping": "3.2.0", "zeroconf": "0.132.2",
    "ifaddr": "0.2.0", "async-timeout": "5.0.1",
    "setuptools": "56.0.0", "packaging": "26.2",
}
QT_SOURCE_ROOT = "https://download.qt.io/archive/qt/5.15/5.15.2/submodules/"
QT_SOURCES = (
    ("QtBase", "5.15.2", QT_SOURCE_ROOT + "qtbase-everywhere-src-5.15.2.tar.xz"),
    ("QtMultimedia", "5.15.2", QT_SOURCE_ROOT + "qtmultimedia-everywhere-src-5.15.2.tar.xz"),
    ("QtSvg", "5.15.2", QT_SOURCE_ROOT + "qtsvg-everywhere-src-5.15.2.tar.xz"),
    ("QtImageFormats", "5.15.2", QT_SOURCE_ROOT + "qtimageformats-everywhere-src-5.15.2.tar.xz"),
    ("QtDeclarative", "5.15.2", QT_SOURCE_ROOT + "qtdeclarative-everywhere-src-5.15.2.tar.xz"),
    ("PySide2/shiboken2", "5.15.2.1", "https://download.qt.io/official_releases/QtForPython/"
     "pyside2/PySide2-5.15.2.1-src/pyside-setup-opensource-src-5.15.2.1.tar.gz"),
)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def fetch_source(project, version, url, expected, target):
    destination = target / url.rsplit("/", 1)[-1]
    if not destination.is_file() or digest(destination) != expected:
        temporary = destination.with_name(destination.name + ".part")
        size = 0
        with urlopen(url, timeout=60) as response, temporary.open("wb") as output:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                size += len(block)
                if size > 100 * 1024 * 1024:
                    raise RuntimeError("Source archive exceeds the bounded download limit")
                output.write(block)
        if digest(temporary) != expected:
            raise RuntimeError("Upstream source SHA-256 mismatch: " + project)
        temporary.replace(destination)
    return {"project": project, "version": version, "url": url,
            "sha256": expected, "bytes": destination.stat().st_size,
            "file": destination.name, "unmodified_upstream_source": True}


def fetch_qt_source(item, target):
    project, version, url = item
    checksum_url = url + ".sha256"
    with urlopen(checksum_url, timeout=30) as response:
        expected = response.read(1024).decode("ascii").split()[0]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise RuntimeError("Invalid Qt source checksum: " + project)
    record = fetch_source(project, version, url, expected.lower(), target)
    record["checksum_url"] = checksum_url
    return record


def collect_distribution(name, expected, target):
    installed = distribution(name)
    if installed.version != expected:
        raise RuntimeError("Legacy dependency mismatch: %s %s != %s" % (name, installed.version, expected))
    files = []
    for entry in installed.files or ():
        if not (entry.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE"))
                or entry.name in ("METADATA", "WHEEL") and ".dist-info" in entry.parts[0]):
            continue
        source = Path(installed.locate_file(entry))
        if not source.is_file() or ".." in entry.parts:
            continue
        if entry.hash is not None and entry.hash.mode == "sha256":
            recorded = base64.urlsafe_b64decode(entry.hash.value + "===").hex()
            if digest(source) != recorded:
                raise RuntimeError("Installed wheel RECORD mismatch: " + str(entry))
        destination = target / "notices" / name / str(entry)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(source), str(destination))
        files.append({"path": destination.relative_to(target).as_posix(),
                      "sha256": digest(destination), "wheel_record": str(entry)})
    if not any(Path(item["path"]).name.upper().startswith(("LICENSE", "COPYING", "NOTICE")) for item in files):
        raise RuntimeError("No actual wheel license notice found: " + name)
    return {"name": name, "version": installed.version, "license_metadata": installed.metadata.get("License"),
            "license_classifiers": [value for value in installed.metadata.get_all("Classifier", ()) if "License" in value],
            "home_page": installed.metadata.get("Home-page"), "files": files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/audio-licenses-win7")
    args = parser.parse_args()
    if sys.version_info[:3] != (3, 8, 10) or sys.maxsize <= 2 ** 32:
        parser.error("Use the dedicated CPython 3.8.10 x64 legacy environment")
    target = args.output.resolve()
    target.mkdir(parents=True, exist_ok=True)
    packages = [collect_distribution(name, version, target) for name, version in PINS.items()]
    python_notice = Path(sys.prefix) / "LICENSE.txt"
    if not python_notice.is_file():
        raise RuntimeError("CPython distribution LICENSE.txt is missing")
    destination = target / "notices/CPython-3.8.10-LICENSE.txt"
    shutil.copyfile(str(python_notice), str(destination))
    with urlopen("https://pypi.org/pypi/zeroconf/0.132.2/json", timeout=30) as response:
        release = json.load(response)
    archive = next(item for item in release["urls"] if item["packagetype"] == "sdist")
    sources = [fetch_source("python-zeroconf", "0.132.2", archive["url"], archive["digests"]["sha256"], target)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        sources.extend(executor.map(lambda item: fetch_qt_source(item, target), QT_SOURCES))
    manifest = {
        "schema": 1, "variant": "GluSpot Windows 7 SP1 x64", "python": "3.8.10", "qt": "5.15.2",
        "package_versions": dict(PINS), "packages": packages, "sources": sources,
        "cpython_notice": {"file": destination.relative_to(target).as_posix(), "sha256": digest(destination)},
        "scope": "Actual pinned Python runtime notices; native helper and Node/npm notices remain in their separate package directories.",
        "licensing_note": "Original upstream license alternatives are retained verbatim. Their inclusion does not assert a commercial Qt license.",
    }
    (target / "SOURCE.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target / "README.txt").write_text(
        "GluSpot legacy runtime notices and unmodified sources\n"
        "CPython 3.8.10; PySide2/shiboken2 5.15.2.1; Qt 5.15.2; NumPy 1.24.4;\n"
        "Pillow 10.4.0; ToJyutping 3.2.0; zeroconf 0.132.2; ifaddr 0.2.0; async-timeout 5.0.1.\n\n"
        "Retained optional metadata/build helper modules are covered by the actual\n"
        "setuptools 56.0.0 and packaging 26.2 notices below.\n\n"
        "notices/ contains the actual installed wheel LICENSE/COPYING/NOTICE texts,\n"
        "METADATA and WHEEL records. NumPy's notice includes its bundled numerical libraries;\n"
        "CPython's notice includes its bundled third-party components. SOURCE.json records\n"
        "the exact versions, hashes and original paths; no modern zeroconf 0.151.5 source is used.\n\n"
        "The exact unmodified LGPL source archives for zeroconf, PySide2/shiboken2, QtBase,\n"
        "QtMultimedia, QtSvg, QtImageFormats and QtDeclarative accompany these notices.\n"
        "QtImageFormats covers the retained image-format plugins, including TIFF and WebP.\n"
        "Qt5Qml is a native\n"
        "dependency of the wheel's pyside2.abi3.dll even when no QML UI is used.\n"
        "QtBase includes the Core/Gui/Widgets/\n"
        "Network, platform, image and OpenGL implementation and third-party notices.\n"
        "Qt shared libraries and Python extensions stay outside PYZ in the onedir package.\n"
        "You may replace/rebuild LGPL libraries with compatible versions subject to their\n"
        "licenses. GluSpot does not prohibit reverse engineering for debugging modifications\n"
        "to these libraries. Retain upstream copyright and license notices.\n\n"
        "The wheel's upstream license alternatives are copied verbatim; their presence\n"
        "does not assert that a commercial Qt license was purchased. See each license text.\n"
        "The native AirPlay helper and Node/npm runtime have separate retained notices.\n",
        encoding="utf-8")
    print("Verified legacy notices: %d packages, %d source archives, %s" % (len(packages), len(sources), target))


if __name__ == "__main__":
    main()
