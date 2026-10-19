"""Explicit package acceptance check, before GUI, settings or provider startup.

Only the requested report is written. Discovery browses mDNS advertisements;
the native helper captures for 250 ms without a receiver/network destination.
No provider/controller is constructed and no Windows endpoint is selected.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from api.airplay_discovery import AirPlayDiscovery
from api.airplay_process import engine_path
from api.windows_audio_devices import WindowsAudioDevices
from local.branding import APP_NAME, APP_VERSION

DISCOVERY_SECONDS = 2.0
CAPTURE_TIMEOUT_SECONDS = 10.0
_MODULES = (
    "api.providers", "api.audio_route_controller", "api.audio_routes",
    "api.windows_audio_devices", "api.airplay_discovery", "api.airplay_process",
    "api.windows_audio_sessions", "api.windows_app_audio_routes", "api.airplay_group_process",
    "local.audio_output_panel", "local.music_output_clock",
)
_DEPENDENCIES = (
    "PySide6", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
    "shiboken6", "zeroconf", "ifaddr",
)
_EXECUTED_MODULES = frozenset({"api.windows_audio_sessions", "api.windows_app_audio_routes",
                             "api.airplay_group_process", "local.music_output_clock"})


def _error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:1200]


def _location(spec) -> dict:
    origin = str(getattr(spec, "origin", "") or "")
    exists = bool(origin and Path(origin).is_file())
    loader = getattr(spec, "loader", None)
    return {
        "origin": origin,
        "loader": type(loader).__name__ if loader is not None else "",
        "filesystem_file": exists,
        # Report facts, not a guessed loose-file override for frozen PYZ code.
        "replacement_path": origin if exists else None,
        "storage": "filesystem" if exists else "archive_or_virtual",
    }


def _module_info(name: str) -> dict:
    try:
        spec = importlib.util.find_spec(name)
        if spec is None:
            return {"name": name, "ok": False, "error": "Module spec missing"}
        if name in _EXECUTED_MODULES:
            module = importlib.import_module(name)
            return {"name": name, "ok": True, "execution_checked": True, **_location(module.__spec__)}
        return {"name": name, "ok": True, "execution_checked": False, **_location(spec)}
    except Exception as exc:
        return {"name": name, "ok": False, "error": _error(exc)}


def _dependency_info(name: str) -> dict:
    try:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", "")
        if not version:
            try:
                version = importlib.metadata.version(name.split(".")[0])
            except importlib.metadata.PackageNotFoundError:
                version = ""
        result = {"name": name, "ok": True, "execution_checked": True, "version": str(version),
                  **_location(module.__spec__)}
        if name == "PySide6.QtCore":
            result["qt_version"] = module.qVersion()
            plugins = Path(module.QLibraryInfo.path(module.QLibraryInfo.LibraryPath.PluginsPath))
            result["plugins_path"] = str(plugins)
            if sys.platform == "win32":
                platform_plugin = plugins / "platforms" / "qwindows.dll"
                result["platform_plugin"] = str(platform_plugin)
                result["platform_plugin_exists"] = platform_plugin.is_file()
                if not platform_plugin.is_file():
                    result.update(ok=False, error="Qt Windows platform plugin missing")
        return result
    except Exception as exc:
        return {"name": name, "ok": False, "error": _error(exc)}


def _check_endpoints() -> dict:
    backend = None
    result = {"ok": False, "devices": [], "default_endpoint_id": ""}
    try:
        backend = WindowsAudioDevices()
        outputs = backend.list_outputs()
        result.update(ok=True, devices=[asdict(device) for device in outputs],
                      default_endpoint_id=next((device.id for device in outputs if device.is_default), ""))
    except Exception as exc:
        result["error"] = _error(exc)
    finally:
        if backend is not None:
            try:
                backend.close()
            except Exception as exc:
                result.update(ok=False, cleanup_error=_error(exc))
    return result


def _check_discovery() -> dict:
    discovery = None
    result = {"ok": False, "state": "error", "devices": []}
    try:
        discovery = AirPlayDiscovery()
        discovery.start()
        if discovery.state == "browsing":
            time.sleep(DISCOVERY_SECONDS)
        result.update(ok=discovery.state == "browsing", state=discovery.state,
                      error=discovery.error, duration_seconds=DISCOVERY_SECONDS,
                      devices=[asdict(device) for device in discovery.devices()])
    except Exception as exc:
        result["error"] = _error(exc)
    finally:
        if discovery is not None:
            try:
                discovery.close()
            except Exception as exc:
                result.update(ok=False, cleanup_error=_error(exc))
    return result


def _check_capture(endpoint_id: str) -> dict:
    executable = engine_path()
    result = {"ok": False, "executable": str(executable),
              "replacement_path": str(executable), "override_variable": "GLUSPOT_AIRPLAY_ENGINE",
              "override_active": bool(os.environ.get("GLUSPOT_AIRPLAY_ENGINE", "")),
              "exists": executable.is_file(), "returncode": None, "events": []}
    if not executable.is_file():
        result["error"] = "Native AirPlay helper missing"
        return result
    if not endpoint_id:
        result["error"] = "No default render endpoint; capture probe skipped"
        return result
    arguments = [str(executable), "--probe-capture", "--endpoint", endpoint_id]
    result["arguments"] = arguments
    kwargs = dict(stdin=subprocess.DEVNULL, capture_output=True, text=True,
                  encoding="utf-8", errors="replace", timeout=CAPTURE_TIMEOUT_SECONDS)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(arguments, **kwargs)
        result["returncode"] = completed.returncode
        result["stderr"] = completed.stderr[-4000:]
        try:
            events = [json.loads(line) for line in completed.stdout.splitlines()]
            if not all(isinstance(event, dict) for event in events):
                raise ValueError("Native helper events must be JSON objects")
            result["events"] = events
            event_names = [event.get("event") for event in events]
            result["ok"] = (completed.returncode == 0 and "capture_ready" in event_names
                            and bool(event_names) and event_names[-1] == "stopped"
                            and "error" not in event_names)
            if not result["ok"]:
                result["error"] = "Native capture probe failed or produced incomplete events"
        except (ValueError, TypeError) as exc:
            result["error"] = _error(exc)
            result["stdout"] = completed.stdout[-4000:]
    except Exception as exc:
        # subprocess.run reaps a timed-out child; this never leaves a helper alive.
        result["error"] = _error(exc)
    return result


def _check_group_dispatch() -> dict:
    """Reject incomplete group arguments before Windows/audio/network work.

    An old single-output executable must not pass package acceptance alongside
    the new Python group controller. This validates dispatch and packaged helper
    compatibility, not receiver playback or application routing support.
    """
    executable = engine_path()
    result = {"ok": False, "executable": str(executable), "returncode": None,
              "receiver_contacted": False, "windows_state_changed": False}
    if not executable.is_file():
        result["error"] = "Native group helper missing"
        return result
    kwargs = dict(stdin=subprocess.DEVNULL, capture_output=True, text=True,
                  encoding="utf-8", errors="replace", timeout=CAPTURE_TIMEOUT_SECONDS)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run([str(executable), "--group"], **kwargs)
        result["returncode"] = completed.returncode
        events = [json.loads(line) for line in completed.stdout.splitlines()]
        result["events"] = events
        result["ok"] = (completed.returncode == 2 and len(events) == 1
                        and isinstance(events[0], dict) and events[0].get("event") == "error"
                        and events[0].get("message") ==
                        "Group requires a verified music process, source endpoint and two outputs or AirPlay")
        if not result["ok"]:
            result["error"] = "Packaged helper does not implement the expected group dispatch"
    except Exception as exc:
        result["error"] = _error(exc)
    return result


def _check_multi_output_dispatch() -> dict:
    """Use an intentionally invalid source/sink collision before Windows work.

    The parser must accept repeated local-output arguments, then reject the
    second one's collision with the muted source. Older one-renderer helpers
    reject the repeated flag instead and cannot certify this package.
    """
    executable = engine_path()
    result = {"ok": False, "executable": str(executable), "returncode": None,
              "receiver_contacted": False, "windows_state_changed": False}
    if not executable.is_file():
        result["error"] = "Native group helper missing"
        return result
    args = [str(executable), "--group", "--music-pid", "1", "--music-created-at", "1",
            "--source-endpoint", "Source", "--local-endpoint", "First", "--local-endpoint", "sOuRcE",
            "--receiver-id", "offline", "--host", "127.0.0.1", "--port", "7000",
            "--device-id", "offline", "--name", "Offline parser check"]
    kwargs = dict(stdin=subprocess.DEVNULL, capture_output=True, text=True,
                  encoding="utf-8", errors="replace", timeout=CAPTURE_TIMEOUT_SECONDS)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(args, **kwargs)
        result["returncode"] = completed.returncode
        events = [json.loads(line) for line in completed.stdout.splitlines()]
        result["events"] = events
        result["ok"] = (completed.returncode == 2 and len(events) == 1
                        and isinstance(events[0], dict) and events[0].get("event") == "error"
                        and events[0].get("message") ==
                        "Local group outputs must be unique and differ from muted source")
        if not result["ok"]:
            result["error"] = "Packaged helper does not implement the expected multiple-output parser"
    except Exception as exc:
        result["error"] = _error(exc)
    return result


def build_audio_package_report() -> dict:
    began = time.monotonic()
    frozen = bool(getattr(sys, "frozen", False))
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    dependencies = [_dependency_info(name) for name in _DEPENDENCIES]
    modules = [_module_info(name) for name in _MODULES]
    endpoints = _check_endpoints()
    discovery = _check_discovery()
    capture = _check_capture(endpoints["default_endpoint_id"])
    group = _check_group_dispatch()
    multiple = _check_multi_output_dispatch()
    external_helper = frozen and bool(os.environ.get("GLUSPOT_AIRPLAY_ENGINE", ""))
    if external_helper:
        # Runtime overrides remain useful for diagnostics, but an external
        # binary cannot certify the native helper shipped inside this bundle.
        capture.update(ok=False, error="Frozen package acceptance requires the bundled helper; clear GLUSPOT_AIRPLAY_ENGINE")
        group.update(ok=False, error="External helper cannot certify frozen group dispatch")
        multiple.update(ok=False, error="External helper cannot certify frozen multiple-output dispatch")
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "runtime": {"frozen": frozen, "executable": sys.executable,
                    "bundle_root": str(bundle), "python_version": sys.version,
                    "platform": sys.platform, "stdout_available": sys.stdout is not None},
        "scope": {"settings_accessed": False, "default_endpoint_changed": False,
                  "receiver_session_started": False, "audio_saved": False,
                  "qt_application_created": False, "qt_platform_runtime_tested": False,
                  "capture_duration_ms": 250, "mdns_browse_seconds": DISCOVERY_SECONDS},
        "dependencies": dependencies,
        "modules": modules,
        "render_endpoints": endpoints,
        "mdns": discovery,
        "native_capture": capture,
        "native_group_dispatch": group,
        "native_multi_output_dispatch": multiple,
        "elapsed_seconds": round(time.monotonic() - began, 3),
        "ok": all(item["ok"] for item in dependencies + modules)
              and endpoints["ok"] and discovery["ok"] and capture["ok"] and group["ok"] and multiple["ok"],
    }


def run_audio_package_check(report_file: str | os.PathLike) -> int:
    try:
        report = build_audio_package_report()
    except Exception as exc:
        report = {"schema_version": 1, "ok": False, "error": _error(exc)}
    try:
        # Deliberately use only the caller-supplied location, never settings/log roots.
        target = Path(report_file).absolute()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        return 2
    return 0 if report["ok"] else 1
