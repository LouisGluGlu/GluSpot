"""Read PE/metadata evidence without loading or running a GluSpot payload.

This is a bounded known-incompatibility audit, not a Windows certification tool.
Exit 1 means known hard blockers; 2 means incomplete/unreadable input. Exit 0
only means no known hard blocker was found: compatibility remains unverified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.parser import Parser
import hashlib
import json
from pathlib import Path
import re
import sys


TARGETS = {
    "win7-sp1-x64": ("Windows 7 SP1 x64", (6, 1, 7601)),
    "win81-x64": ("Windows 8.1 x64", (6, 3, 9600)),
    "win10-1809-x64": ("Windows 10 1809 x64", (10, 0, 17763)),
    "win11-x64": ("Windows 11 x64", (10, 0, 22000)),
}

_MS_DOCS = "https://learn.microsoft.com/en-us/windows/win32/api/"


def _rule(minimum, label, documentation, libraries):
    return {"minimum_version": minimum, "minimum_windows": label,
            "documentation": _MS_DOCS + documentation, "libraries": libraries}


_KERNEL = ("kernel32.dll", "kernelbase.dll", "api-ms-win-core-")
_USER = ("user32.dll", "ext-ms-win-ntuser-")
KNOWN_IMPORTS = {
    "WaitOnAddress": _rule((6, 2, 0), "Windows 8", "synchapi/nf-synchapi-waitonaddress", _KERNEL),
    "WakeByAddressSingle": _rule((6, 2, 0), "Windows 8", "synchapi/nf-synchapi-wakebyaddresssingle", _KERNEL),
    "WakeByAddressAll": _rule((6, 2, 0), "Windows 8", "synchapi/nf-synchapi-wakebyaddressall", _KERNEL),
    "SetThreadDescription": _rule((10, 0, 14393), "Windows 10 1607", "processthreadsapi/nf-processthreadsapi-setthreaddescription", _KERNEL),
    "GetThreadDescription": _rule((10, 0, 14393), "Windows 10 1607", "processthreadsapi/nf-processthreadsapi-getthreaddescription", _KERNEL),
    "SetThreadInformation": _rule((6, 2, 0), "Windows 8", "processthreadsapi/nf-processthreadsapi-setthreadinformation", _KERNEL),
    "GetSystemTimePreciseAsFileTime": _rule((6, 2, 0), "Windows 8", "sysinfoapi/nf-sysinfoapi-getsystemtimepreciseasfiletime", _KERNEL),
    "CopyFile2": _rule((6, 2, 0), "Windows 8", "winbase/nf-winbase-copyfile2", _KERNEL),
    "SetWaitableTimerEx": _rule((6, 2, 0), "Windows 8", "synchapi/nf-synchapi-setwaitabletimerex", _KERNEL),
    "GetCurrentPackageFullName": _rule((6, 2, 0), "Windows 8", "appmodel/nf-appmodel-getcurrentpackagefullname", _KERNEL),
    "PssCaptureSnapshot": _rule((6, 3, 0), "Windows 8.1", "processsnapshot/nf-processsnapshot-psscapturesnapshot", _KERNEL),
    "PssQuerySnapshot": _rule((6, 3, 0), "Windows 8.1", "processsnapshot/nf-processsnapshot-pssquerysnapshot", _KERNEL),
    "PssFreeSnapshot": _rule((6, 3, 0), "Windows 8.1", "processsnapshot/nf-processsnapshot-pssfreesnapshot", _KERNEL),
    "PathCchCombineEx": _rule((6, 2, 0), "Windows 8", "pathcch/nf-pathcch-pathcchcombineex", ("pathcch.dll", "api-ms-win-core-path-")),
    "PathCchSkipRoot": _rule((6, 2, 0), "Windows 8", "pathcch/nf-pathcch-pathcchskiproot", ("pathcch.dll", "api-ms-win-core-path-")),
    "RoGetActivationFactory": _rule((6, 2, 0), "Windows 8", "roapi/nf-roapi-rogetactivationfactory", ("combase.dll", "api-ms-win-core-winrt-")),
    "RoOriginateLanguageException": _rule((6, 3, 0), "Windows 8.1", "roerrorapi/nf-roerrorapi-rooriginatelanguageexception", ("combase.dll", "api-ms-win-core-winrt-")),
    "GetDpiForWindow": _rule((10, 0, 14393), "Windows 10 1607", "winuser/nf-winuser-getdpiforwindow", _USER),
    "GetSystemMetricsForDpi": _rule((10, 0, 14393), "Windows 10 1607", "winuser/nf-winuser-getsystemmetricsfordpi", _USER),
    "SetThreadDpiAwarenessContext": _rule((10, 0, 14393), "Windows 10 1607", "winuser/nf-winuser-setthreaddpiawarenesscontext", _USER),
    "SetProcessDpiAwarenessContext": _rule((10, 0, 15063), "Windows 10 1703", "winuser/nf-winuser-setprocessdpiawarenesscontext", _USER),
}


def classify_import(library, symbol, import_kind, target_version):
    """Classify an exact documented Windows API, respecting its DLL provider."""
    rule = KNOWN_IMPORTS.get(symbol)
    library = library.lower()
    if rule is None or not any(
            library.startswith(provider) if provider.endswith("-") else library == provider
            for provider in rule["libraries"]):
        return None
    if tuple(target_version) >= rule["minimum_version"]:
        return None
    return {
        "library": library, "symbol": symbol, "import_kind": import_kind,
        "minimum_windows": rule["minimum_windows"],
        "minimum_version": list(rule["minimum_version"]),
        "documentation": rule["documentation"],
        "status": "known_loader_blocker" if import_kind == "direct" else "known_unavailable_if_called",
    }


def _text(value):
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _version_info(pe):
    result = {"fixed_file_version": None, "fixed_product_version": None, "strings": {}}
    for fixed in getattr(pe, "VS_FIXEDFILEINFO", ()):
        for prefix, key in (("File", "fixed_file_version"), ("Product", "fixed_product_version")):
            high, low = getattr(fixed, prefix + "VersionMS"), getattr(fixed, prefix + "VersionLS")
            result[key] = ".".join(str(part) for part in (high >> 16, high & 65535, low >> 16, low & 65535))
        break
    for group in getattr(pe, "FileInfo", ()):
        for info in group if isinstance(group, (list, tuple)) else (group,):
            for table in getattr(info, "StringTable", ()):
                for key, value in table.entries.items():
                    name = _text(key)
                    if name in {"FileVersion", "ProductVersion", "ProductName", "OriginalFilename", "FileDescription"}:
                        result["strings"][name] = _text(value)
    result["display_version"] = (result["strings"].get("ProductVersion")
                                 or result["strings"].get("FileVersion")
                                 or result["fixed_product_version"] or result["fixed_file_version"])
    return result


def inspect_pe(path, target_version, *, pe_module=None):
    """Parse a file as data. Never load a DLL or import its Python package."""
    path = Path(path)
    result = {"path": str(path), "exists": path.is_file(), "status": "unverified",
              "imports": [], "known_findings": [], "error": ""}
    if not result["exists"]:
        result.update(status="missing", error="Expected payload file is absent")
        return result
    pe = None
    try:
        if pe_module is None:
            import pefile as pe_module
        pe = pe_module.PE(str(path), fast_load=True)
        pe.parse_data_directories(directories=[pe_module.DIRECTORY_ENTRY[name] for name in (
            "IMAGE_DIRECTORY_ENTRY_IMPORT", "IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT", "IMAGE_DIRECTORY_ENTRY_RESOURCE")])
        result["size_bytes"] = path.stat().st_size
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        result["sha256"] = digest.hexdigest()
        result["machine"] = hex(pe.FILE_HEADER.Machine)
        result["version_info"] = _version_info(pe)
        result["pe_header_versions"] = {
            "operating_system": [pe.OPTIONAL_HEADER.MajorOperatingSystemVersion, pe.OPTIONAL_HEADER.MinorOperatingSystemVersion],
            "subsystem": [pe.OPTIONAL_HEADER.MajorSubsystemVersion, pe.OPTIONAL_HEADER.MinorSubsystemVersion],
            "note": "Header versions alone do not establish runtime compatibility.",
        }
        for attribute, kind in (("DIRECTORY_ENTRY_IMPORT", "direct"), ("DIRECTORY_ENTRY_DELAY_IMPORT", "delay")):
            for entry in getattr(pe, attribute, ()):
                library = _text(entry.dll).lower()
                symbols = []
                for imported in entry.imports:
                    symbol = _text(imported.name) if imported.name is not None else "#" + str(imported.ordinal)
                    symbols.append(symbol)
                    finding = classify_import(library, symbol, kind, target_version)
                    if finding:
                        result["known_findings"].append(finding)
                result["imports"].append({"library": library, "kind": kind, "symbols": symbols})
        if any(item["status"] == "known_loader_blocker" for item in result["known_findings"]):
            result["status"] = "known_blocked"
    except Exception as exc:
        result.update(status="unreadable", error=type(exc).__name__ + ": " + str(exc))
    finally:
        if pe is not None:
            pe.close()
    return result


def _payload_metadata(payload):
    result = []
    for directory in (payload, payload / "_internal"):
        if not directory.is_dir():
            continue
        for metadata_path in sorted(directory.glob("*.dist-info/METADATA")):
            item = {"path": str(metadata_path)}
            try:
                metadata = Parser().parsestr(metadata_path.read_text(encoding="utf-8"), headersonly=True)
                item.update(name=metadata.get("Name", ""), version=metadata.get("Version", ""),
                            requires_python=metadata.get("Requires-Python", ""))
            except (OSError, UnicodeError) as exc:
                item["error"] = type(exc).__name__ + ": " + str(exc)
            result.append(item)
    return result


def _select_path(payload, candidates):
    return next((payload / candidate for candidate in candidates if (payload / candidate).is_file()), payload / candidates[0])


def build_report(payload, target="win7-sp1-x64", *, pe_module=None):
    payload = Path(payload).resolve()
    label, version = TARGETS[target]
    python_candidates = sorted(set(
        path for directory in (payload / "_internal", payload)
        for path in directory.glob("python3*.dll") if path.stem[7:].isdigit()))
    components = [
        ("application", payload / "GluSpot.exe", True),
        ("python", python_candidates[0] if python_candidates else payload / "_internal/python3xx.dll", True),
        ("qt_core", _select_path(payload, ("_internal/PySide6/Qt6Core.dll", "PySide6/Qt6Core.dll",
                                          "_internal/PySide2/Qt5Core.dll", "PySide2/Qt5Core.dll")), True),
        ("online_music_node", _select_path(payload, ("_internal/online_music/node.exe", "online_music/node.exe")), False),
        ("airplay_helper", _select_path(payload, ("_internal/native/GluSpotAirPlay.exe", "native/GluSpotAirPlay.exe")), False),
    ]
    files = []
    for component, path, required in components:
        evidence = inspect_pe(path, version, pe_module=pe_module)
        evidence.update(component=component, required_for_base_app=required)
        files.append(evidence)
    metadata = _payload_metadata(payload)
    incomplete = (not payload.is_dir() or any(
        item["status"] == "unreadable" or item["required_for_base_app"] and not item["exists"] for item in files)
        or any("error" in item for item in metadata))
    known_blocked = any(item["status"] == "known_blocked" for item in files)
    node = next(item for item in files if item["component"] == "online_music_node")
    node_version = str(node.get("version_info", {}).get("display_version") or "")
    node_match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:\.0)?", node_version)
    node_ref = "v" + node_match.group(1) if node_match else "main"
    python = next(item for item in files if item["component"] == "python")
    python_version = str(python.get("version_info", {}).get("display_version") or "")
    python_match = re.match(r"(\d+\.\d+)\.", python_version)
    python_ref = python_match.group(1) if python_match else "3"
    qt = next(item for item in files if item["component"] == "qt_core")
    qt_reference = ("https://doc.qt.io/archives/qt-5.15/supported-platforms.html"
                    if Path(qt["path"]).name.lower() == "qt5core.dll" else
                    "https://doc.qt.io/qt-6/supported-platforms.html")
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "payload": str(payload), "payload_exists": payload.is_dir(),
        "target": {"id": target, "name": label, "version": list(version)},
        "overall_status": "known_blocked" if known_blocked else "unverified",
        "compatibility_confirmed": False,
        "audit_complete": not incomplete,
        "method": "Static PE import, VERSIONINFO and payload-only package metadata reads; no payload execution or imports.",
        "limitations": [
            "The API mapping is deliberately incomplete. No findings never proves compatibility.",
            "Only the listed component binaries are inspected, not their complete transitive dependency closure.",
            "Delay imports fail only if their unsupported API is called; dynamic GetProcAddress/WinRT activation is not resolved.",
            "A component loader blocker does not imply every other component is blocked; optional feature availability requires runtime verification.",
            "Drivers, OS updates, codecs, runtime libraries, GPU behavior, TLS and actual installation/playback are unverified.",
        ],
        "runtime_references": {
            "python": "https://docs.python.org/" + python_ref + "/using/windows.html",
            "qt": qt_reference,
            "node": "https://github.com/nodejs/node/blob/" + node_ref + "/BUILDING.md#platform-list",
            "system_media_control": "https://learn.microsoft.com/en-us/uwp/api/windows.media.control.globalsystemmediatransportcontrolssessionmanager",
        },
        "files": files, "payload_package_metadata": metadata,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True, help="Unpacked GluSpot application directory")
    parser.add_argument("--output", type=Path, required=True, help="JSON evidence report outside the payload")
    parser.add_argument("--target", choices=TARGETS, default="win7-sp1-x64")
    args = parser.parse_args(argv)
    payload, output = args.payload.resolve(), args.output.resolve()
    if output == payload or payload in output.parents:
        parser.error("--output must be outside the read-only payload directory")
    report = build_report(payload, args.target)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print("Cannot write compatibility report: " + str(exc), file=sys.stderr)
        return 2
    print(report["overall_status"] + "; compatibility remains unverified; report: " + str(output))
    if not report["audit_complete"]:
        return 2
    return 1 if report["overall_status"] == "known_blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
