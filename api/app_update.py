"""Find, download and verify a newer GluSpot release. No Qt, no UI.

Rules confirmed by the user on 2026-11-18:
- Only official releases count: GitHub's "latest" release (never drafts or
  pre-releases) of the public LouisGluGlu/GluSpot repository.
- "自动检查更新" checks once at start; "自动更新" (only possible when checking
  is on) downloads in the background and installs silently when GluSpot quits.
- A portable copy is never replaced: it is only told where to download.
- A version the user chose to ignore is not announced again.

The request carries only a generic client name, no account or personal data.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPOSITORY = "LouisGluGlu/GluSpot"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
USER_AGENT = "GluSpot-Updater"
INSTALLER_PATTERN = re.compile(r"^GluSpot-[0-9.]+-Setup-x64\.exe$")
MAX_INSTALLER_BYTES = 800 * 1024 * 1024
TIMEOUT = 20


def version_key(version: str) -> tuple[int, ...]:
    """GluSpot versions keep a three-digit minor: 6.5 = 6.500 < 6.52 = 6.520, and 6.418 < 6.50."""
    text = str(version or "").strip().lstrip("vV")
    match = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return ()
    major, minor, patch = match.group(1), match.group(2), match.group(3) or "0"
    minor_value = int(minor.ljust(3, "0")) if len(minor) <= 3 else int(minor[:3])
    return int(major), minor_value, int(patch)


def is_newer(candidate: str, current: str) -> bool:
    a, b = version_key(candidate), version_key(current)
    return bool(a and b and a > b)


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    title: str
    notes: str
    page_url: str
    installer_url: str = ""
    installer_name: str = ""
    installer_size: int = 0
    installer_sha256: str = ""


def parse_release(payload) -> ReleaseInfo | None:
    """The fields we need from GitHub's release JSON; anything malformed is None."""
    if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
        return None
    tag = str(payload.get("tag_name") or "")
    if not version_key(tag):
        return None
    page = str(payload.get("html_url") or RELEASES_PAGE)
    if not page.startswith("https://github.com/"):
        page = RELEASES_PAGE
    installer = {}
    for asset in payload.get("assets") or ():
        if isinstance(asset, dict) and INSTALLER_PATTERN.match(str(asset.get("name") or "")):
            installer = asset
            break
    url = str(installer.get("browser_download_url") or "")
    if url and not url.startswith("https://github.com/"):
        url = ""
    digest = str(installer.get("digest") or "")
    sha = digest.split(":", 1)[1].lower() if digest.lower().startswith("sha256:") else ""
    if sha and not re.fullmatch(r"[0-9a-f]{64}", sha):
        sha = ""
    size = installer.get("size")
    return ReleaseInfo(
        tag.lstrip("vV"), str(payload.get("name") or tag)[:200], str(payload.get("body") or "")[:20000], page,
        url, str(installer.get("name") or ""), int(size) if isinstance(size, int) and size > 0 else 0, sha,
    )


def fetch_latest(opener=None) -> ReleaseInfo | None:
    request = urllib.request.Request(LATEST_URL, headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    opener = opener or urllib.request.urlopen
    with opener(request, timeout=TIMEOUT) as response:
        body = response.read(2 * 1024 * 1024)
    return parse_release(json.loads(body.decode("utf-8")))


def check(current_version: str, ignored: str = "", *, manual: bool = False, opener=None):
    """("available", release) | ("current", release) | ("ignored", release) | ("none", None)."""
    release = fetch_latest(opener)
    if release is None:
        return "none", None
    if not is_newer(release.version, current_version):
        return "current", release
    if not manual and ignored and version_key(ignored) == version_key(release.version):
        return "ignored", release
    return "available", release


def download(release: ReleaseInfo, folder: Path, *, progress=None, opener=None, cancelled=None) -> Path:
    """Fetch the installer into folder and verify it. Raises on any mismatch."""
    if not release.installer_url or not INSTALLER_PATTERN.match(release.installer_name):
        raise ValueError("no installer in this release")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / release.installer_name
    if final.is_file() and _verified(final, release):
        return final
    partial = final.with_suffix(".part")
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(release.installer_url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    received = 0
    with opener(request, timeout=TIMEOUT) as response, open(partial, "wb") as out:
        while True:
            if callable(cancelled) and cancelled():
                raise InterruptedError("download cancelled")
            block = response.read(1024 * 1024)
            if not block:
                break
            received += len(block)
            if received > MAX_INSTALLER_BYTES:
                raise ValueError("installer larger than expected")
            digest.update(block)
            out.write(block)
            if callable(progress):
                progress(received, release.installer_size)
    if release.installer_size and received != release.installer_size:
        partial.unlink(missing_ok=True)
        raise ValueError("installer size does not match the release")
    if release.installer_sha256 and digest.hexdigest() != release.installer_sha256:
        partial.unlink(missing_ok=True)
        raise ValueError("installer checksum does not match the release")
    partial.replace(final)
    return final


def _verified(path: Path, release: ReleaseInfo) -> bool:
    if release.installer_size and path.stat().st_size != release.installer_size:
        return False
    if not release.installer_sha256:
        return bool(release.installer_size)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == release.installer_sha256


def install_kind(executable: str | None = None, frozen: bool | None = None, registered_path: str = "") -> str:
    """"installed" (the Setup's copy, can be updated), "portable" or "source" (only told)."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not frozen:
        return "source"
    exe_dir = Path(executable or sys.executable).resolve().parent
    if registered_path:
        try:
            if Path(registered_path).resolve() == exe_dir:
                return "installed"
        except OSError:
            pass
    return "portable"


def registered_install_path() -> str:
    """The folder the Setup recorded under HKCU\\Software\\GluSpot\\Install, or ""."""
    if sys.platform != "win32":
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\GluSpot\Install") as key:
            value, _kind = winreg.QueryValueEx(key, "Path")
            return str(value or "")
    except OSError:
        return ""


def installer_command(installer: Path, install_dir: str, *, relaunch: bool) -> list[str]:
    """A hidden PowerShell that runs the Setup silently, then (if asked) opens GluSpot again."""
    def quoted(value) -> str:
        return "'" + str(value).replace("'", "''") + "'"
    script = (f"Start-Process -Wait -FilePath {quoted(installer)} "
              "-ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'")
    if relaunch:
        script += f"; Start-Process -FilePath {quoted(os.path.join(install_dir, 'GluSpot.exe'))}"
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script]


def start_installer(installer: Path, install_dir: str, *, relaunch: bool) -> None:
    """Detach the installer so it outlives GluSpot, which is about to quit."""
    import subprocess
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
        flags |= getattr(subprocess, name, 0)
    subprocess.Popen(installer_command(installer, install_dir, relaunch=relaunch), creationflags=flags,
                     close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
