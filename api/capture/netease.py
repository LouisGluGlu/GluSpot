"""Read-only NetEase discovery and explicit transport-A launch/relaunch.

This module never writes to NetEase's installation, registry or shortcuts.
Native process/version discovery imports only on Windows and is injectable in
unit tests. A relaunch is an explicit caller action, never reconnect recovery.
"""
from __future__ import annotations

import asyncio
import os
import hashlib
import json
import logging
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
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
    reused: bool = False


class NetEaseLocator:
    def __init__(self, path='', *, processes=None, registry_paths=None, version=None,
                 exists=None, spawn=None, stop=None, owner_dir=None, debug_ports=None,
                 healthy=None, clock=None, sleep=None, startup_timeout=15.0):
        self.path = str(path or '')
        self._processes = processes or _windows_processes
        self._registry_paths = registry_paths or _registry_candidates
        self._version = version or _file_version
        self._exists = exists or (lambda path: Path(path).is_file())
        self._spawn = spawn or subprocess.Popen
        self._stop = stop or _stop_process
        self._owner_dir = Path(owner_dir) if owner_dir is not None else Path(tempfile.gettempdir()) / 'GluSpot' / 'netease-relaunch'
        self._debug_ports = debug_ports or _windows_debug_ports
        self._healthy = healthy or _healthy_debug_port
        self._clock = clock or time.time
        self._sleep = sleep or time.sleep
        self._startup_timeout = startup_timeout

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
        """One durable owner covers stop, verified startup and normal recovery.

        Cancellation cannot abandon a subprocess worker between stop and spawn.
        All callers/processes share the executable-specific OS file lock and
        persisted cooldown; a healthy existing debug endpoint always wins.
        """
        cancelled = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(self._launch_owned, installation, relaunch, cancelled))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            try:
                await asyncio.shield(task)
            except Exception:
                logging.getLogger(__name__).exception('NetEase recovery worker failed during cancellation')
            raise

    async def restore(self, launch):
        """Request normal recovery without stopping any running player."""
        if launch.reused:
            return
        await asyncio.to_thread(self._restore_owned, launch.installation)

    async def attach(self, installation=None) -> NetEaseLaunch | None:
        """Discover an existing endpoint without starting or stopping a player."""
        return await asyncio.to_thread(self._attach_existing, installation)

    def _attach_existing(self, installation):
        installation = installation or self.locate()
        if installation is None:
            return None
        with self._owner(installation) as owner:
            return self._existing_launch(installation, owner.read())

    def _existing_launch(self, installation, state):
        key = _path_key(installation.path)
        matching = self._matching(installation)
        def candidates():
            yield state.get('pid'), installation.path, state.get('port')
            yield from self._debug_ports()
        for pid, path, port in candidates():
            if (pid in matching and _path_key(path) == key
                    and isinstance(port, int) and not isinstance(port, bool)
                    and 1 <= port <= 65535 and self._healthy(port)):
                return NetEaseLaunch(port, installation, pid, reused=True)
        return None

    def _matching(self, installation):
        key = _path_key(installation.path)
        return [pid for pid, path in self._processes() if _path_key(path) == key]

    def _owner(self, installation, cancelled=None):
        return _RelaunchOwner(self._owner_dir, installation.path, cancelled)

    def _restore_owned(self, installation):
        with self._owner(installation):
            self._restore_normal(installation, request=True)

    def _restore_normal(self, installation, *, request=False):
        # Never stop a running player during fallback, including one manually
        # reopened by the user. An unflagged launch restores a missing player.
        if not request and self._matching(installation):
            return
        logging.getLogger(__name__).warning('Restoring NetEase normally after capture startup failure')
        self._spawn([installation.path], cwd=str(Path(installation.path).parent))
        deadline = time.monotonic() + self._startup_timeout
        while time.monotonic() < deadline:
            if self._matching(installation):
                return
            self._sleep(0.1)
        raise RuntimeError('Normal NetEase recovery failed; player is not running')

    def _launch_owned(self, installation, relaunch, cancelled):
        installation = installation or self.locate()
        if installation is None:
            raise FileNotFoundError('NetEase CloudMusic was not found')
        key = _path_key(installation.path)
        if key.rsplit('/', 1)[-1] != 'cloudmusic.exe' or not self._exists(installation.path):
            raise ValueError('NetEase executable is unavailable')
        with self._owner(installation, cancelled) as owner:
            state = owner.read()
            matching = self._matching(installation)
            existing = self._existing_launch(installation, state)
            if existing is not None:
                return existing
            if cancelled.is_set():
                raise RuntimeError('NetEase launch cancelled before process changes')
            if self._clock() - state.get('attempted_at', float('-inf')) < 120.0:
                self._restore_normal(installation)
                raise RuntimeError('NetEase relaunch cooldown active; player left running')
            if matching and not relaunch:
                raise RuntimeError('NetEase is running; explicit relaunch is required')
            state = dict(attempted_at=self._clock(), phase='starting')
            owner.write(state)  # Durable reservation before the first stop.
            touched = False
            try:
                if matching:
                    logging.getLogger(__name__).warning('Explicit NetEase capture relaunch: stopping verified PIDs %s', matching)
                    touched = True  # Even a partial/failed stop requires recovery.
                    for pid in matching:
                        if cancelled.is_set():
                            raise RuntimeError('NetEase launch cancelled during stop')
                        if pid in self._matching(installation):
                            try:
                                self._stop(pid)
                            except OSError:
                                # taskkill /T may already have stopped a child
                                # from the original snapshot. Recheck, not abort.
                                if pid in self._matching(installation):
                                    raise
                    deadline = time.monotonic() + 5.0
                    while self._matching(installation):
                        if cancelled.is_set() or time.monotonic() >= deadline:
                            raise RuntimeError('NetEase did not stop for relaunch')
                        self._sleep(0.1)
                if cancelled.is_set():
                    raise RuntimeError('NetEase launch cancelled before spawn')
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                    listener.bind(('127.0.0.1', 0))
                    port = listener.getsockname()[1]
                touched = True
                process = self._spawn([installation.path, f'--remote-debugging-port={port}',
                                       '--remote-debugging-address=127.0.0.1'],
                                      cwd=str(Path(installation.path).parent))
                state.update(port=port, pid=int(process.pid), phase='spawned')
                owner.write(state)
                deadline = time.monotonic() + self._startup_timeout
                while time.monotonic() < deadline:
                    if cancelled.is_set():
                        raise RuntimeError('NetEase launch cancelled during startup')
                    running = self._matching(installation)
                    if running and self._healthy(port):
                        state.update(pid=running[0], phase='ready')
                        owner.write(state)
                        return NetEaseLaunch(port, installation, running[0])
                    self._sleep(0.1)
                raise RuntimeError('NetEase debug startup failed or player exited')
            except BaseException:
                if touched:
                    self._restore_normal(installation, request=True)
                state['phase'] = 'recovered'
                owner.write(state)
                logging.getLogger(__name__).exception('NetEase capture relaunch failed; normal player recovery completed')
                raise


class _RelaunchOwner:
    """The same byte/file-lock convention as existing GluSpot cache/route owners."""
    def __init__(self, directory, path, cancelled):
        self.path = directory / (hashlib.sha256(_path_key(path).encode()).hexdigest() + '.lock')
        self.cancelled = cancelled

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+b')
        if self.handle.seek(0, os.SEEK_END) == 0:
            self.handle.write(b'\0')
            self.handle.flush()
        deadline = time.monotonic() + 45.0
        while True:
            try:
                self.handle.seek(0)
                if sys.platform == 'win32':
                    import msvcrt
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if (self.cancelled is not None and self.cancelled.is_set()) or time.monotonic() >= deadline:
                    self.handle.close()
                    raise RuntimeError('Another GluSpot process owns the NetEase relaunch')
                time.sleep(0.05)

    def read(self):
        state_path = self.path.with_suffix('.json')
        state = json.loads(state_path.read_bytes()) if state_path.exists() else {}
        if not isinstance(state, dict) or not isinstance(state.get('attempted_at', 0), (int, float)):
            raise RuntimeError('Invalid NetEase relaunch owner state; refusing restart')
        return state

    def write(self, state):
        # Never truncate the durable cooldown reservation. A crash during a
        # later phase update must leave either the old or the new reservation.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=self.path.stem,
                                             suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(state).encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path.with_suffix('.json'))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def __exit__(self, *args):
        # Closing releases the OS lock even when the process exits unexpectedly.
        self.handle.close()


def _healthy_debug_port(port):
    from api.capture.cdp import discover_target
    try:
        asyncio.run(discover_target(port, timeout=0.5))
        return True
    except (OSError, ValueError, asyncio.TimeoutError):
        return False


def _windows_debug_ports():
    """Read existing CloudMusic command lines; never guess a fixed debug port."""
    if sys.platform != 'win32':
        return []
    import re
    script = "Get-CimInstance Win32_Process -Filter \"Name='cloudmusic.exe'\" | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or not result.stdout.strip():
        return []
    rows = json.loads(result.stdout)
    rows = rows if isinstance(rows, list) else [rows]
    ports = []
    for row in rows:
        match = re.search(r'--remote-debugging-port(?:=|\s+)([0-9]+)', row.get('CommandLine') or '')
        if match:
            ports.append((int(row['ProcessId']), row.get('ExecutablePath') or '', int(match[1])))
    return ports


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
