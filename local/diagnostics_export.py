from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from local.runtime_experiment import build_runtime_experiment_plan


DIAGNOSTICS_EXPORT_SCHEMA_VERSION = 1
DIAGNOSTICS_EXPORT_SUPPORT_SCHEMA_VERSION = 1

_RAW_PAYLOAD_KEY_PARTS = (
    "album_art",
    "artwork",
    "cover_image",
    "image",
    "pixmap",
    "qimage",
    "qpixmap",
    "raw",
)


def build_diagnostics_export_payload(
    diagnostics: Mapping[str, Any] | None,
    *,
    generated_at: str,
    app_name: str,
    app_version: str,
    source: str = "settings",
) -> dict[str, object]:
    data = diagnostics if isinstance(diagnostics, Mapping) else {}
    return {
        "schema_version": DIAGNOSTICS_EXPORT_SCHEMA_VERSION,
        "generated_at": str(generated_at or ""),
        "source": str(source or "settings"),
        "app": {
            "name": str(app_name or ""),
            "version": str(app_version or ""),
        },
        "diagnostics": json_safe_diagnostics(data),
        "support": build_diagnostics_export_support_metadata(data),
    }


def build_diagnostics_export_support_metadata(
    diagnostics: Mapping[str, Any] | None,
) -> dict[str, object]:
    data = diagnostics if isinstance(diagnostics, Mapping) else {}
    plan = build_runtime_experiment_plan(data).as_dict()
    return {
        "schema_version": DIAGNOSTICS_EXPORT_SUPPORT_SCHEMA_VERSION,
        "runtime_experiment_plan": json_safe_diagnostics(plan),
        "runtime_experiment_compare": {
            "current_export_role": "before",
            "after_export": str(plan.get("after_capture") or ""),
            "capture_seconds": int(plan.get("capture_seconds") or 0),
            "command": str(plan.get("compare_command") or ""),
            "same_track_required": True,
        },
    }


def diagnostics_export_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        json_safe_diagnostics(payload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def default_diagnostics_export_path(
    log_dir: str | Path,
    *,
    generated_at: str,
    app_name: str = "GluSpot",
) -> Path:
    stamp = _filename_timestamp(generated_at)
    product = re.sub(r"[^a-z0-9]+", "-", str(app_name or "gluspot").lower()).strip("-") or "gluspot"
    return Path(log_dir) / f"{product}-runtime-diagnostics-{stamp}.json"


def write_diagnostics_export_file(payload: Mapping[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(diagnostics_export_json(payload), encoding="utf-8")
    return target


def json_safe_diagnostics(value: Any, *, key: str = "") -> object:
    if _should_redact_key(key):
        return _redacted_payload(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {"redacted": True, "type": "bytes", "size": len(value)}
    if is_dataclass(value) and not isinstance(value, type):
        return json_safe_diagnostics(asdict(value), key=key)
    if isinstance(value, Mapping):
        return {
            str(item_key): json_safe_diagnostics(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, tuple):
        return [json_safe_diagnostics(item) for item in value]
    if isinstance(value, set):
        return [json_safe_diagnostics(item) for item in sorted(value, key=lambda item: str(item))]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [json_safe_diagnostics(item) for item in value]
    return str(value)


def _should_redact_key(key: str) -> bool:
    name = str(key or "").lower()
    return any(part in name for part in _RAW_PAYLOAD_KEY_PARTS)


def _redacted_payload(value: Any) -> dict[str, object]:
    size = None
    try:
        size = len(value)
    except Exception:
        size = None
    result: dict[str, object] = {
        "redacted": True,
        "type": type(value).__name__,
    }
    if size is not None:
        result["size"] = int(size)
    return result


def _filename_timestamp(generated_at: str) -> str:
    text = str(generated_at or "").strip()
    digits = "".join(re.findall(r"\d+", text))
    if len(digits) >= 14:
        return f"{digits[:8]}-{digits[8:14]}"
    if len(digits) >= 8:
        return digits[:14]
    return "latest"
