"""Current-user Windows DPAPI storage for GluSpot-owned account envelopes."""
from __future__ import annotations

import ctypes
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any, Protocol


class CredentialProtectionError(RuntimeError):
    pass


class CredentialProtector(Protocol):
    def protect(self, data: bytes) -> bytes: ...
    def unprotect(self, data: bytes) -> bytes: ...


class WindowsDPAPI:
    _entropy = b"GluSpot/online-music/accounts/v1"

    def _crypt(self, data: bytes, *, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise CredentialProtectionError("Windows DPAPI is unavailable")
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        def blob(raw: bytes):
            buffer = ctypes.create_string_buffer(raw)
            return Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer

        source, source_owner = blob(data)
        entropy, entropy_owner = blob(self._entropy)
        output = Blob()
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if decrypt:
            fn = crypt.CryptUnprotectData
            fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
            ok = fn(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output))
        else:
            fn = crypt.CryptProtectData
            fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
            ok = fn(ctypes.byref(source), "GluSpot online music", ctypes.byref(entropy), None, None, 1, ctypes.byref(output))
        fn.restype = wintypes.BOOL
        if not ok:
            raise CredentialProtectionError("Windows credential protection failed")
        try:
            return ctypes.string_at(output.data, output.size)
        finally:
            kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))

    def protect(self, data: bytes) -> bytes:
        return self._crypt(data, decrypt=False)

    def unprotect(self, data: bytes) -> bytes:
        return self._crypt(data, decrypt=True)


class ProtectedCredentialStore:
    """Only a dedicated encrypted file is written; no settings or plaintext fallback."""
    _magic = b"GLUSPOT-ACCOUNT-DPAPI-1\n"
    _max_bytes = 2 * 1024 * 1024
    _sources = frozenset(("netease", "qq", "kugou", "qishui"))

    def __init__(self, root: Path | str | None = None, protector: CredentialProtector | None = None):
        if root is None:
            base = os.environ.get("LOCALAPPDATA")
            if not base:
                raise CredentialProtectionError("Local application data is unavailable")
            root = Path(base) / "GluSpot" / "accounts"
        self.root = Path(root).absolute()
        self.protector = protector or WindowsDPAPI()
        self._lock = threading.RLock()

    def _path(self, source_id: str) -> Path:
        if source_id not in self._sources:
            raise CredentialProtectionError("Unknown account source")
        return self.root / (source_id + ".dpapi")

    def _safe_root(self, create: bool = False) -> None:
        def validate() -> None:
            for parent in (self.root, *self.root.parents):
                if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
                    raise CredentialProtectionError("Account storage cannot follow a redirected directory")
        validate()
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
            validate()

    def load(self, source_id: str) -> dict[str, Any] | None:
        with self._lock:
            path = self._path(source_id)
            self._safe_root()
            if not path.exists():
                return None
            if path.is_symlink() or not path.is_file() or path.stat().st_size > self._max_bytes:
                raise CredentialProtectionError("Invalid protected account file")
            raw = path.read_bytes()
            if not raw.startswith(self._magic):
                raise CredentialProtectionError("Invalid protected account file")
            try:
                value = json.loads(self.protector.unprotect(raw[len(self._magic):]).decode("utf-8"))
            except Exception:
                raise CredentialProtectionError("Protected account could not be restored") from None
            if not isinstance(value, dict) or value.get("source_id") != source_id or value.get("version") != 1 or not isinstance(value.get("credential"), dict):
                raise CredentialProtectionError("Invalid protected account envelope")
            return value["credential"]

    def save(self, source_id: str, credential: dict[str, Any]) -> None:
        with self._lock:
            path = self._path(source_id)
            self._safe_root(create=True)
            if path.is_symlink():
                raise CredentialProtectionError("Account storage cannot follow a link")
            data = json.dumps({"version": 1, "source_id": source_id, "credential": credential}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            if len(data) > self._max_bytes // 2:
                raise CredentialProtectionError("Protected account envelope is too large")
            protected = self._magic + self.protector.protect(data)
            temporary = self.root / (source_id + "." + secrets.token_hex(8) + ".tmp")
            try:
                with temporary.open("xb") as handle:
                    handle.write(protected)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

    def delete(self, source_id: str) -> None:
        with self._lock:
            path = self._path(source_id)
            self._safe_root()
            if path.is_symlink():
                raise CredentialProtectionError("Account storage cannot follow a link")
            path.unlink(missing_ok=True)
