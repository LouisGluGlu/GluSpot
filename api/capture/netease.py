"""Read-only NetEase discovery and explicit transport-A launch/relaunch.

This module never writes to NetEase's installation, registry or shortcuts.
Native process/version discovery imports only on Windows and is injectable in
unit tests. A relaunch is an explicit caller action, never reconnect recovery.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class NetEaseInstallation:
    path: str
    version: str = ''
    pids: tuple[int, ...] = ()


@dataclass(frozen=True)
class NetEaseLaunch:
    port: int
    installation: NetEaseInstallation
    pid: int


class NetEaseLocator:
    def __init__(self, path='', *, processes=None, registry_paths=None, version=None,
                 exists=None, spawn=None, stop=None):
        self.path = str(path or '')
        self._processes = processes or _windows_processes
        self._registry_paths = registry_paths or _registry_candidates
        self._version = version or _file_version
        self._exists = exists or (lambda path: Path(path).is_file())
        self._spawn = spawn or subprocess.Popen
        self._stop = stop or _stop_process

    def locate(self) -> NetEaseInstallation | None:
        processes = tuple(self._processes())
        candidates = ([self.path] if self.path else []) + [path for _, path in processes]
        candidates += list(self._registry_paths())
        for variable in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
            if os.environ.get(variable):
                candidates.append(str(Path(os.environ[variable]) / 'NetEase' / 'CloudMusic' / 'cloudmusic.exe'))
        seen = set()
        for path in candidates:
            key = _path_key(path)
            if not key or key in seen:
                continue
            seen.add(key)
            if key.rsplit('/', 1)[-1] != 'cloudmusic.exe' or not self._exists(path):
                continue
            pids = tuple(pid for pid, process_path in processes if _path_key(process_path) == key)
            return NetEaseInstallation(str(path), self._version(str(path)), pids)
        return None

    async def launch(self, installation=None, *, relaunch=False) -> NetEaseLaunch:
        installation = installation or await asyncio.to_thread(self.locate)
        if installation is None:
            raise FileNotFoundError('NetEase CloudMusic was not found')
        key = _path_key(installation.path)
        if key.rsplit('/', 1)[-1] != 'cloudmusic.exe' or not self._exists(installation.path):
            raise ValueError('NetEase executable is unavailable')
        current = tuple(await asyncio.to_thread(self._processes))
        matching = [pid for pid, path in current if _path_key(path) == key]
        if matching and not relaunch:
            raise RuntimeError('NetEase is running; explicit relaunch is required')
        if relaunch:
            for pid in matching:
                # Re-check path immediately before stopping, to fence PID reuse.
                verified = tuple(await asyncio.to_thread(self._processes))
                if (pid, key) in [(p, _path_key(path)) for p, path in verified]:
                    await asyncio.to_thread(self._stop, pid)
            for _ in range(50):
                running = tuple(await asyncio.to_thread(self._processes))
                if not any(_path_key(path) == key for _, path in running):
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError('NetEase did not stop for relaunch')
        # The socket reserves a random loopback port until directly before spawn.
        # CEF cannot inherit this listener; any bind race fails in CDP discovery
        # and requires explicit repair, rather than attaching a different host.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        arguments = [installation.path, f'--remote-debugging-port={port}',
                     '--remote-debugging-address=127.0.0.1']
        process = await asyncio.to_thread(self._spawn, arguments, cwd=str(Path(installation.path).parent))
        return NetEaseLaunch(port, installation, int(process.pid))


def _path_key(path):
    return str(path or '').strip().replace('\\', '/').rstrip('/').casefold()


def _registry_candidates():
    if sys.platform != 'win32':
        return []
    import winreg
    paths = []
    keys = (
        (r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\cloudmusic.exe', ''),
        (r'SOFTWARE\NetEase\CloudMusic', 'InstallPath'),
        (r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\网易云音乐', 'InstallLocation'),
    )
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for access in (winreg.KEY_READ | winreg.KEY_WOW64_64KEY, winreg.KEY_READ | winreg.KEY_WOW64_32KEY):
            for key, value in keys:
                try:
                    with winreg.OpenKey(hive, key, 0, access) as handle:
                        path, _ = winreg.QueryValueEx(handle, value)
                    if isinstance(path, str) and path.strip():
                        path = os.path.expandvars(path.strip().strip('"'))
                        paths.append(path if path.casefold().endswith('.exe') else str(Path(path) / 'cloudmusic.exe'))
                except OSError:
                    pass
    return paths


def _windows_processes():
    if sys.platform != 'win32':
        return []
    import ctypes
    from ctypes import wintypes
    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return []
    result = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.szExeFile.casefold() == 'cloudmusic.exe':
                handle = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                if handle:
                    try:
                        size = wintypes.DWORD(32768)
                        buffer = ctypes.create_unicode_buffer(size.value)
                        if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                            result.append((int(entry.th32ProcessID), buffer.value))
                    finally:
                        kernel.CloseHandle(handle)
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return result


def _file_version(path):
    if sys.platform != 'win32':
        return ''
    import ctypes
    from ctypes import wintypes
    class VersionInfo(ctypes.Structure):
        _fields_ = [(field, wintypes.DWORD) for field in (
            'signature', 'structure', 'fileMS', 'fileLS', 'productMS', 'productLS',
            'mask', 'flags', 'os', 'type', 'subtype', 'dateMS', 'dateLS')]
    version = ctypes.WinDLL('version', use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    ignored = wintypes.DWORD()
    size = version.GetFileVersionInfoSizeW(path, ctypes.byref(ignored))
    if not size:
        return ''
    data = ctypes.create_string_buffer(size)
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if (not version.GetFileVersionInfoW(path, 0, size, data)
            or not version.VerQueryValueW(data, '\\', ctypes.byref(pointer), ctypes.byref(length))
            or length.value < ctypes.sizeof(VersionInfo)):
        return ''
    info = ctypes.cast(pointer, ctypes.POINTER(VersionInfo)).contents
    if info.signature != 0xFEEF04BD:
        return ''
    return '.'.join(str(value) for value in (info.fileMS >> 16, info.fileMS & 65535, info.fileLS >> 16, info.fileLS & 65535))


def _stop_process(pid):
    if sys.platform != 'win32':
        raise OSError('NetEase relaunch is available only on Windows')
    # Explicit restart can discard unsaved player UI state. Never call this from
    # automatic reconnect; only the enabled/restart flow invokes launch(relaunch).
    result = subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                            capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise OSError('NetEase process could not be stopped')
