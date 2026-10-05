"""Read measured endpoint holds without opening a microphone or audio stream.

A profile is valid only for its measured output membership, Windows build and
the current 44.1 kHz group presentation policy. It stores scalar evidence, never
recorded PCM. Unknown or invalid profiles retain the uncalibrated timeline.
"""
from __future__ import annotations

import json
from pathlib import Path
import platform
import re

from api.audio_routes import AudioRouteDevice

POLICY = "group-44100-stereo16-77175-v1"
MAX_EXTRA_FRAMES = 22050
MAX_PROFILE_BYTES = 65536


def default_profile_path() -> Path:
    from local.branding import local_app_data_dir
    return Path(local_app_data_dir()) / "audio-output-calibration.json"


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate calibration field")
        result[key] = value
    return result


def measured_local_holds(devices, *, path=None, windows_build=None, fingerprint_getter=None) -> dict[str, int]:
    """Take one validated profile snapshot at group startup.

    Matching uses full opaque IDs and transport evidence, not a device's display
    name alone. Changing membership requires another acoustic validation.
    """
    if not isinstance(devices, (tuple, list)) or not 1 <= len(devices) <= 8:
        return {}
    identities = {}
    for device in devices:
        if (not isinstance(device, AudioRouteDevice) or not device.id
                or device.id in identities or device.available is not True
                or device.transport not in {"windows", "airplay"}):
            return {}
        identities[device.id] = (device.transport, device.is_bluetooth, device.name)
    try:
        profile_path = Path(path) if path is not None else default_profile_path()
        with profile_path.open("rb") as stream:
            raw = stream.read(MAX_PROFILE_BYTES + 1)
        if len(raw) > MAX_PROFILE_BYTES:
            return {}
        profile = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (OSError, ValueError, TypeError, RecursionError):
        return {}
    build = platform.version() if windows_build is None else windows_build
    if (not isinstance(profile, dict) or type(profile.get("schema_version")) is not int
            or profile["schema_version"] != 1 or profile.get("policy") != POLICY
            or profile.get("windows_build") != build
            or profile.get("verified") is not True):
        return {}
    members = profile.get("members")
    holds = profile.get("local_holds")
    if (not isinstance(members, list) or len(members) != len(identities)
            or not isinstance(holds, dict) or len(holds) > len(identities)):
        return {}
    observed = {}
    for member in members:
        if not isinstance(member, dict):
            return {}
        identity = member.get("id")
        if not isinstance(identity, str) or identity not in identities or identity in observed:
            return {}
        if type(member.get("is_bluetooth")) is not bool:
            return {}
        observed[identity] = (member.get("transport"), member["is_bluetooth"], member.get("name"))
    if observed != identities:
        return {}
    result = {}
    for endpoint, frames in holds.items():
        if (endpoint not in identities or identities[endpoint][0] != "windows"
                or type(frames) is not int or not 0 <= frames <= MAX_EXTRA_FRAMES):
            return {}
        if frames:
            result[endpoint] = frames
    if not result:
        return {}
    fingerprints = profile.get("windows_fingerprints")
    locals_ = {key for key, identity in identities.items() if identity[0] == "windows"}
    if (not isinstance(fingerprints, dict) or set(fingerprints) != locals_
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in fingerprints.values())):
        return {}
    backend = None
    try:
        if fingerprint_getter is None:
            from api.windows_audio_devices import WindowsAudioDevices
            backend = WindowsAudioDevices()
            fingerprint_getter = backend.get_endpoint_calibration_fingerprint
        for endpoint in locals_:
            if fingerprint_getter(endpoint) != fingerprints[endpoint]:
                return {}
    except (OSError, ValueError, RuntimeError, TypeError):
        return {}
    finally:
        if backend is not None:
            backend.close()
    return result
