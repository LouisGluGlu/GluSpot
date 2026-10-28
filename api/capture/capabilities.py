"""Player capabilities and isolated, opt-in capture profile migration.

The legacy application profiles remain untouched. Their historical default
``sync_enabled=False`` with an empty path cannot be distinguished from a user
who deliberately saved that same pair. Capture treats that ambiguous pair as
``auto``; the new explicit ``off`` mode preserves a deliberate override.
Nothing here reads processes, checks executable paths, or imports native APIs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from api.capture.seek import SeekPolicy, seek_policy


PROFILE_SETTING = "v2_capture_app_profiles"
LEGACY_PROFILE_SETTING = "v2_music_app_profiles"
PROFILE_MODES = frozenset({"auto", "hybrid", "smtc", "off"})


@dataclass(frozen=True)
class PlayerCapability:
    """Immutable default route, seek limits, and passive seek-probe eligibility.

    ``probe`` requests learning from an ordinary user-initiated seek's physical
    readback. It never authorizes an unsolicited seek or an unknown UIA adapter.
    """

    service: str
    mode: str
    policy: SeekPolicy
    probe: bool = False


CAPABILITIES: Mapping[str, PlayerCapability] = MappingProxyType({
    "netease": PlayerCapability("netease", "hybrid", seek_policy("cloudmusic")),
    "qq": PlayerCapability("qq", "hybrid", seek_policy("qqmusic")),
    "spotify": PlayerCapability("spotify", "smtc", SeekPolicy()),
    "browser": PlayerCapability("browser", "smtc", SeekPolicy()),
    "unknown": PlayerCapability("unknown", "smtc", SeekPolicy(), probe=True),
})

# Native seek helpers already guard cloudmusic.exe / qqmusic.exe identity.
# Executable matching is exact; an executable containing another app's name
# must not inherit that application's UIA capability.
_PROCESS_SERVICES: Mapping[str, str] = MappingProxyType({
    "cloudmusic.exe": "netease",
    "qqmusic.exe": "qq",
    "spotify.exe": "spotify",
    "chrome.exe": "browser",
    "chromium.exe": "browser",
    "msedge.exe": "browser",
    "firefox.exe": "browser",
    "brave.exe": "browser",
    "opera.exe": "browser",
})
_SESSION_SERVICES: Mapping[str, str] = MappingProxyType({
    "netease": "netease",
    "cloudmusic": "netease",
    "orpheus": "netease",
    "qqmusic": "qq",
    "spotify": "spotify",
})
_AUMID_COMPONENT_SERVICES: Mapping[str, str] = MappingProxyType({
    "cloudmusic": "netease",
    "neteasecloudmusic": "netease",
    "qqmusic": "qq",
    "spotify": "spotify",
    "spotifymusic": "spotify",
    "chrome": "browser",
    "chromium": "browser",
    "msedge": "browser",
    "microsoftedge": "browser",
    "firefox": "browser",
    "brave": "browser",
    "opera": "browser",
})


def capability_for(session_id: str, process_name: str = "") -> PlayerCapability:
    """Choose by AUMID components or an exact process basename, without I/O.

    Recognized session identity takes precedence over an optional process hint.
    Paths are never parsed into executable identities; callers can supply a
    separately verified process basename when the AUMID is unfamiliar.
    """
    session = str(session_id or "").strip().casefold()
    service = _PROCESS_SERVICES.get(session) or _SESSION_SERVICES.get(session)
    if service is None and "/" not in session and "\\" not in session and not session.endswith(".exe"):
        for component in re.split(r"[.!_\-]", session):
            service = _AUMID_COMPONENT_SERVICES.get(component)
            if service is not None:
                break
    if service is None:
        service = _PROCESS_SERVICES.get(str(process_name or "").strip().casefold(), "unknown")
    return CAPABILITIES[service]


def normalize_capture_profiles(legacy_profiles, new_profiles) -> dict[str, dict[str, str]]:
    """Create an independent migration envelope of ``{service: {mode: ...}}``.

    New valid modes are authoritative, including an explicit ``auto`` that
    supersedes an old manual disable. Absent or malformed new modes retain a
    legacy manual disable only when ``sync_enabled is False`` and a nonempty
    path proves that the old profile was configured. Other profiles use auto.
    The returned dictionaries share no mutable data with either input.
    """
    legacy = _profile_mapping(legacy_profiles)
    current = _profile_mapping(new_profiles)
    services = set(CAPABILITIES) | set(legacy) | set(current)
    result = {}
    for service in sorted(services):
        old = legacy.get(service)
        configured_off = (
            isinstance(old, Mapping)
            and old.get("sync_enabled") is False
            and isinstance(old.get("path"), str)
            and bool(old["path"].strip())
        )
        mode = "off" if configured_off else "auto"
        profile = current.get(service)
        if isinstance(profile, Mapping):
            saved_mode = profile.get("mode")
            if isinstance(saved_mode, str) and saved_mode.strip().casefold() in PROFILE_MODES:
                mode = saved_mode.strip().casefold()
        result[service] = {"mode": mode}
    return result


def capture_profile(settings_getter, capability: PlayerCapability) -> str:
    """Resolve effective off/smtc/hybrid mode; the master flag is caller-owned.

    Capability auto-enablement requires no configured path. Explicit hybrid is
    retained as a preference; callers may construct a UIA adapter only when the
    capability's service has one. Unknown services therefore remain safe to
    probe through ordinary SMTC seek readback without inventing a UIA adapter.
    """
    if not callable(settings_getter):
        return capability.mode
    profiles = normalize_capture_profiles(
        settings_getter(LEGACY_PROFILE_SETTING, None),
        settings_getter(PROFILE_SETTING, None),
    )
    mode = profiles.get(capability.service, {"mode": "auto"})["mode"]
    return capability.mode if mode == "auto" else mode


def _profile_mapping(value) -> dict[str, object]:
    """Canonicalize service keys while ignoring malformed settings payloads."""
    if not isinstance(value, Mapping):
        return {}
    return {
        key.strip().casefold(): profile
        for key, profile in value.items()
        if isinstance(key, str) and key.strip()
    }
