"""Windows render-endpoint discovery and explicit music-output selection.

This module opens no audio streams, microphones or Bluetooth connections. Poll
``list_outputs`` from the provider's worker: endpoint arrival and default changes
are observed without holding COM objects across threads. Music selection changes
eConsole/eMultimedia only; eCommunications is deliberately left to Windows.

ABI sources consulted:
https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/mmdeviceapi.idl
https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/functiondiscoverykeys_devpkey.h
https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/cfgmgr32.h
https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/endpointvolume.idl
https://github.com/frgnca/AudioDeviceCmdlets/blob/master/SOURCE/IPolicyConfig.cs

PolicyConfig is an undocumented Windows interface, verified against the above
existing implementation. Failure is surfaced, rather than guessing another ABI.
Changing a default cannot force an external player's pinned stream to migrate.
"""
from __future__ import annotations

import ctypes
import math
import re
import sys
import threading
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class AudioOutputDevice:
    id: str
    name: str
    kind: str = "device"
    is_bluetooth: bool = False
    is_default: bool = False
    volume: float | None = None
    volume_muted: bool = False


@dataclass(frozen=True)
class _Endpoint:
    id: str
    name: str
    form_factor: int | None = None
    is_bluetooth: bool = False


class WindowsAudioDevices:
    """Thread-safe, lazily opened backend; ``adapter`` is a test seam.

    An adapter supplies snapshot() -> (tuple[_Endpoint, ...], dict[role, id])
    and set_defaults(device_id, previous_defaults). Native calls are scoped to
    the calling thread, so construction on the GUI thread is harmless.
    """

    def __init__(self, adapter=None):
        self._adapter = adapter
        self._lock = threading.RLock()
        self._closed = False

    def _backend(self):
        if self._closed:
            raise RuntimeError("Audio output backend is closed")
        if self._adapter is None:
            if sys.platform != "win32":
                return None
            self._adapter = _WindowsCoreAudioAdapter()
        return self._adapter

    def list_outputs(self) -> tuple[AudioOutputDevice, ...]:
        with self._lock:
            adapter = self._backend()
            if adapter is None:
                return ()
            endpoints, defaults = adapter.snapshot()
            default_id = defaults.get(1) or defaults.get(0)
            seen = set()
            result = []
            for endpoint in endpoints:
                if not endpoint.id or endpoint.id in seen:
                    continue
                seen.add(endpoint.id)
                volume, muted = None, False
                try:
                    read_volume = getattr(adapter, "get_endpoint_volume", None)
                    if callable(read_volume):
                        volume = self._validate_volume(read_volume(endpoint.id))
                        muted = bool(adapter.get_endpoint_mute(endpoint.id))
                except (OSError, ValueError, RuntimeError, NotImplementedError):
                    # An endpoint can vanish or lack volume support while the
                    # output list itself remains valid. Unknown is not zero.
                    volume, muted = None, False
                kind = {1: "speaker", 3: "headphones", 5: "headset"}.get(endpoint.form_factor, "device")
                if kind in {"headphones", "headset"} and re.search(
                    r"\bAirPods\s+Pro\b", endpoint.name or "", re.IGNORECASE,
                ):
                    # Explicit model text selects a glyph only. The PnP
                    # container remains the authority for Bluetooth transport.
                    kind = "airpods-pro"
                result.append(AudioOutputDevice(
                    id=endpoint.id,
                    name=endpoint.name or "音频设备",
                    kind=kind,
                    is_bluetooth=endpoint.is_bluetooth,
                    is_default=endpoint.id == default_id,
                    volume=volume,
                    volume_muted=muted,
                ))
            return tuple(result)

    def set_default(self, device_id: str) -> None:
        if not isinstance(device_id, str) or not device_id or "\0" in device_id:
            raise ValueError("A valid output endpoint ID is required")
        with self._lock:
            adapter = self._backend()
            if adapter is None:
                raise RuntimeError("Audio output selection requires Windows")
            endpoints, defaults = adapter.snapshot()
            if device_id not in {endpoint.id for endpoint in endpoints}:
                raise ValueError("The selected audio output is no longer available")
            if all(defaults.get(role) == device_id for role in (0, 1)):
                return
            adapter.set_defaults(device_id, defaults)
            endpoints, actual = adapter.snapshot()
            if device_id not in {endpoint.id for endpoint in endpoints} or any(
                actual.get(role) != device_id for role in (0, 1)
            ):
                raise RuntimeError("Windows did not confirm the selected music output")

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def get_endpoint_mute(self, device_id: str) -> bool:
        """Read one explicit render endpoint; does not select an output."""
        self._validate_endpoint_id(device_id)
        with self._lock:
            adapter = self._backend()
            if adapter is None:
                raise RuntimeError("Audio endpoint mute requires Windows")
            return bool(adapter.get_endpoint_mute(device_id))

    def get_endpoint_volume(self, device_id: str) -> float:
        """Read one render endpoint's master scalar without changing mute."""
        self._validate_endpoint_id(device_id)
        with self._lock:
            adapter = self._backend()
            if adapter is None:
                raise RuntimeError("Audio endpoint volume requires Windows")
            return self._validate_volume(adapter.get_endpoint_volume(device_id))

    def set_endpoint_volume(self, device_id: str, volume: float) -> float:
        """Set and confirm master volume; never select, mute or unmute.

        A cast source remains muted even when its remembered scalar changes.
        The returned value is Windows readback, allowing small hardware steps.
        """
        self._validate_endpoint_id(device_id)
        value = self._validate_volume(volume)
        with self._lock:
            adapter = self._backend()
            if adapter is None:
                raise RuntimeError("Audio endpoint volume requires Windows")
            actual = self._validate_volume(adapter.get_endpoint_volume(device_id))
            if math.isclose(actual, value, abs_tol=1e-6):
                return actual
            adapter.set_endpoint_volume(device_id, value)
            actual = self._validate_volume(adapter.get_endpoint_volume(device_id))
            if not math.isclose(actual, value, abs_tol=0.005):
                raise RuntimeError("Windows did not confirm the selected endpoint volume")
            return actual

    def set_endpoint_mute(self, device_id: str, muted: bool) -> None:
        """Set/confirm mute only; master/channel volumes remain unchanged.

        This is an explicit routing/recovery primitive, not an automatic mute
        policy. In particular, it makes no claim that every driver preserves
        loopback PCM while muted.
        """
        self._validate_endpoint_id(device_id)
        if not isinstance(muted, bool):
            raise ValueError("Audio endpoint mute must be a bool")
        with self._lock:
            adapter = self._backend()
            if adapter is None:
                raise RuntimeError("Audio endpoint mute requires Windows")
            if bool(adapter.get_endpoint_mute(device_id)) == muted:
                return
            adapter.set_endpoint_mute(device_id, muted)
            if bool(adapter.get_endpoint_mute(device_id)) != muted:
                raise RuntimeError("Windows did not confirm the selected endpoint mute")

    @staticmethod
    def _validate_endpoint_id(device_id):
        if not isinstance(device_id, str) or not device_id or "\0" in device_id:
            raise ValueError("A valid output endpoint ID is required")

    @staticmethod
    def _validate_volume(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Audio endpoint volume must be a scalar from 0 to 1")
        try:
            value = float(value)
        except OverflowError as exc:
            raise ValueError("Audio endpoint volume must be a scalar from 0 to 1") from exc
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("Audio endpoint volume must be a scalar from 0 to 1")
        return value


_DWORD = ctypes.c_uint32
_HRESULT = ctypes.c_int32


class _GUID(ctypes.Structure):
    _fields_ = [("data1", _DWORD), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_string(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)

    def bytes(self):
        return bytes(self)


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", _DWORD)]


class _Blob(ctypes.Structure):
    _fields_ = [("size", _DWORD), ("data", ctypes.c_void_p)]


class _VariantValue(ctypes.Union):
    _fields_ = [("uint", _DWORD), ("pointer", ctypes.c_void_p),
                ("int64", ctypes.c_int64), ("blob", _Blob)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_uint16), ("reserved", ctypes.c_uint16 * 3),
                ("value", _VariantValue)]


def _key(guid, pid):
    return _PROPERTYKEY(_GUID.from_string(guid), pid)


_FRIENDLY_NAME = _key("a45c254e-df1c-4efd-8020-67d146a850e0", 14)
_FORM_FACTOR = _key("1da5d803-d492-4edd-8c23-e0c0ffee7f0e", 0)
_CONTAINER_ID = _key("8c7ed206-3f8a-4827-b3ab-ae9e1faefc6c", 2)
_MM_ENUMERATOR = "bcde0395-e52f-467c-8e3d-c4579291692e"
_IID_ENUMERATOR = "a95664d2-9614-4f35-a746-de8db63617e6"
_POLICY_CONFIG = "870af99c-171d-4f9e-af0d-e63df40c2bc9"
_IID_POLICY_CONFIG = "f8679f50-850a-41cf-9c72-430f290290c8"
_IID_ENDPOINT_VOLUME = "5cdf2c82-841e-4546-9722-0cf74078229a"


def _check(hr, operation):
    if hr < 0:
        raise OSError(f"{operation} failed (HRESULT 0x{hr & 0xffffffff:08X})")


def _call(pointer, index, argtypes, *args, result_type=_HRESULT):
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    method = ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *argtypes)(vtable[index])
    return method(pointer, *args)


class _ComScope:
    def __init__(self, ole32):
        self.ole32 = ole32
        self.pointers = []
        self.initialized = False

    def __enter__(self):
        hr = self.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
        if hr & 0xffffffff != 0x80010106:  # Existing STA is usable; do not uninitialize it.
            _check(hr, "CoInitializeEx")
            self.initialized = True
        return self

    def __exit__(self, *_args):
        for pointer in reversed(self.pointers):
            _call(pointer, 2, (), result_type=_DWORD)  # IUnknown::Release
        if self.initialized:
            self.ole32.CoUninitialize()

    def own(self, pointer):
        if pointer:
            self.pointers.append(pointer)
        return pointer

    def create(self, clsid, iid):
        result = ctypes.c_void_p()
        hr = self.ole32.CoCreateInstance(
            ctypes.byref(_GUID.from_string(clsid)), None, 1,
            ctypes.byref(_GUID.from_string(iid)), ctypes.byref(result),
        )
        _check(hr, "CoCreateInstance")
        return self.own(result)


class _WindowsCoreAudioAdapter:
    def __init__(self):
        self._ole32 = ctypes.WinDLL("ole32")
        self._ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, _DWORD]
        self._ole32.CoInitializeEx.restype = _HRESULT
        self._ole32.CoUninitialize.argtypes = []
        self._ole32.CoUninitialize.restype = None
        self._ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, _DWORD,
                                                ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        self._ole32.CoCreateInstance.restype = _HRESULT
        self._ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        self._ole32.CoTaskMemFree.restype = None
        self._ole32.PropVariantClear.argtypes = [ctypes.POINTER(_PROPVARIANT)]
        self._ole32.PropVariantClear.restype = _HRESULT
        self._cfg = ctypes.WinDLL("cfgmgr32")
        self._cfg.CM_Get_Device_ID_List_SizeW.argtypes = [ctypes.POINTER(_DWORD), ctypes.c_wchar_p, _DWORD]
        self._cfg.CM_Get_Device_ID_ListW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_wchar), _DWORD, _DWORD]
        self._cfg.CM_Locate_DevNodeW.argtypes = [ctypes.POINTER(_DWORD), ctypes.c_wchar_p, _DWORD]
        self._cfg.CM_Get_DevNode_PropertyW.argtypes = [_DWORD, ctypes.POINTER(_PROPERTYKEY),
                                                    ctypes.POINTER(_DWORD), ctypes.c_void_p,
                                                    ctypes.POINTER(_DWORD), _DWORD]
        for name in ("CM_Get_Device_ID_List_SizeW", "CM_Get_Device_ID_ListW", "CM_Locate_DevNodeW",
                     "CM_Get_DevNode_PropertyW"):
            getattr(self._cfg, name).restype = _DWORD

    def _device_id(self, device):
        text = ctypes.c_void_p()
        _check(_call(device, 5, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(text)), "IMMDevice.GetId")
        try:
            return ctypes.wstring_at(text)
        finally:
            self._ole32.CoTaskMemFree(text)

    def _endpoint_volume(self, scope, device_id):
        """Activate SDK IAudioEndpointVolume for an explicit render ID."""
        enumerator = scope.create(_MM_ENUMERATOR, _IID_ENUMERATOR)
        device = ctypes.c_void_p()
        _check(_call(enumerator, 5, (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)),
                     device_id, ctypes.byref(device)), "IMMDeviceEnumerator.GetDevice")
        scope.own(device)
        # IAudioEndpointVolume also exists on capture devices. Never alter one.
        direction = ctypes.c_void_p()
        _check(_call(device, 0, (ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)),
                     ctypes.byref(_GUID.from_string("1be09788-6894-4089-8586-9a2a6c265ac5")),
                     ctypes.byref(direction)), "IMMDevice.QueryInterface(IMMEndpoint)")
        scope.own(direction)
        flow = ctypes.c_int()
        _check(_call(direction, 3, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(flow)),
               "IMMEndpoint.GetDataFlow")
        if flow.value != 0:  # eRender
            raise ValueError("Audio endpoint controls require a render endpoint")
        volume = ctypes.c_void_p()
        _check(_call(device, 3, (ctypes.POINTER(_GUID), _DWORD, ctypes.c_void_p,
                               ctypes.POINTER(ctypes.c_void_p)),
                     ctypes.byref(_GUID.from_string(_IID_ENDPOINT_VOLUME)), 23, None,
                     ctypes.byref(volume)), "IMMDevice.Activate(IAudioEndpointVolume)")
        return scope.own(volume)

    def get_endpoint_mute(self, device_id):
        with _ComScope(self._ole32) as scope:
            volume = self._endpoint_volume(scope, device_id)
            value = ctypes.c_int32()  # Windows BOOL is 32-bit, including x64.
            _check(_call(volume, 15, (ctypes.POINTER(ctypes.c_int32),), ctypes.byref(value)),
                   "IAudioEndpointVolume.GetMute")
            return bool(value.value)

    def set_endpoint_mute(self, device_id, muted):
        with _ComScope(self._ole32) as scope:
            volume = self._endpoint_volume(scope, device_id)
            _check(_call(volume, 14, (ctypes.c_int32, ctypes.c_void_p), int(muted), None),
                   "IAudioEndpointVolume.SetMute")

    def get_endpoint_volume(self, device_id):
        with _ComScope(self._ole32) as scope:
            volume = self._endpoint_volume(scope, device_id)
            value = ctypes.c_float()
            _check(_call(volume, 9, (ctypes.POINTER(ctypes.c_float),), ctypes.byref(value)),
                   "IAudioEndpointVolume.GetMasterVolumeLevelScalar")
            return float(value.value)

    def set_endpoint_volume(self, device_id, value):
        with _ComScope(self._ole32) as scope:
            volume = self._endpoint_volume(scope, device_id)
            _check(_call(volume, 7, (ctypes.c_float, ctypes.c_void_p), value, None),
                   "IAudioEndpointVolume.SetMasterVolumeLevelScalar")

    def _property(self, store, key):
        value = _PROPVARIANT()
        hr = _call(store, 5, (ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT)),
                   ctypes.byref(key), ctypes.byref(value))
        try:
            if hr < 0:
                return None
            if value.vt == 31 and value.value.pointer:  # VT_LPWSTR
                return ctypes.wstring_at(value.value.pointer)
            if value.vt == 19:  # VT_UI4
                return int(value.value.uint)
            if value.vt == 72 and value.value.pointer:  # VT_CLSID
                return ctypes.string_at(value.value.pointer, 16)
            return None
        finally:
            self._ole32.PropVariantClear(ctypes.byref(value))

    def _bluetooth_containers(self):
        """Match endpoint container GUIDs to present Bluetooth PnP nodes.

        Friendly names are not evidence of transport. Missing driver properties
        result in an ordinary device, rather than a guessed Bluetooth badge.
        """
        cfg = self._cfg
        for _attempt in range(2):  # PnP may change between length and MULTI_SZ reads.
            length = _DWORD()
            if cfg.CM_Get_Device_ID_List_SizeW(ctypes.byref(length), None, 0x100) != 0:
                return set()
            if not 1 <= length.value <= 1_000_000:
                return set()
            buffer = ctypes.create_unicode_buffer(length.value)
            status = cfg.CM_Get_Device_ID_ListW(None, buffer, length, 0x100)
            if status == 0:
                break
        else:
            return set()
        containers = set()
        for instance_id in buffer[:].split("\0"):
            if not instance_id.upper().startswith(("BTHENUM\\", "BTHLEDEVICE\\", "BTHHFENUM\\")):
                continue
            node = _DWORD()
            if cfg.CM_Locate_DevNodeW(ctypes.byref(node), instance_id, 0) != 0:
                continue
            value = _GUID()
            size, prop_type = _DWORD(16), _DWORD()
            status = cfg.CM_Get_DevNode_PropertyW(node, ctypes.byref(_CONTAINER_ID), ctypes.byref(prop_type),
                                               ctypes.byref(value), ctypes.byref(size), 0)
            if status == 0 and prop_type.value == 13 and size.value == 16 and any(value.bytes()):
                containers.add(value.bytes())  # DEVPROP_TYPE_GUID
        return containers

    def snapshot(self):
        bluetooth = self._bluetooth_containers()
        with _ComScope(self._ole32) as scope:
            enumerator = scope.create(_MM_ENUMERATOR, _IID_ENUMERATOR)
            defaults = {}
            for role in (0, 1):
                device = ctypes.c_void_p()
                hr = _call(enumerator, 4, (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                           0, role, ctypes.byref(device))
                if hr & 0xffffffff == 0x80070490:  # No default endpoint.
                    continue
                _check(hr, "IMMDeviceEnumerator.GetDefaultAudioEndpoint")
                defaults[role] = self._device_id(scope.own(device))
            collection = ctypes.c_void_p()
            _check(_call(enumerator, 3, (ctypes.c_int, _DWORD, ctypes.POINTER(ctypes.c_void_p)),
                         0, 1, ctypes.byref(collection)), "IMMDeviceEnumerator.EnumAudioEndpoints")
            scope.own(collection)
            count = _DWORD()
            _check(_call(collection, 3, (ctypes.POINTER(_DWORD),), ctypes.byref(count)), "IMMDeviceCollection.GetCount")
            endpoints = []
            for index in range(count.value):
                device = ctypes.c_void_p()
                hr = _call(collection, 4, (_DWORD, ctypes.POINTER(ctypes.c_void_p)), index, ctypes.byref(device))
                if hr < 0:  # An unplugged device can disappear during enumeration.
                    continue
                scope.own(device)
                try:
                    device_id = self._device_id(device)
                except OSError:
                    continue
                store = ctypes.c_void_p()
                hr = _call(device, 4, (_DWORD, ctypes.POINTER(ctypes.c_void_p)), 0, ctypes.byref(store))
                name, factor, container = None, None, None
                if hr >= 0:
                    scope.own(store)
                    name = self._property(store, _FRIENDLY_NAME)
                    factor = self._property(store, _FORM_FACTOR)
                    container = self._property(store, _CONTAINER_ID)
                endpoints.append(_Endpoint(device_id, name or "音频设备", factor, container in bluetooth))
            return tuple(endpoints), defaults

    def set_defaults(self, device_id, previous_defaults):
        with _ComScope(self._ole32) as scope:
            policy = scope.create(_POLICY_CONFIG, _IID_POLICY_CONFIG)
            changed = []
            try:
                for role in (0, 1):
                    if previous_defaults.get(role) == device_id:
                        continue
                    hr = _call(policy, 13, (ctypes.c_wchar_p, ctypes.c_int), device_id, role)
                    _check(hr, "IPolicyConfig.SetDefaultEndpoint")
                    changed.append(role)
            except OSError as exc:
                failed_restores = []
                for role in reversed(changed):
                    previous = previous_defaults.get(role)
                    if previous and _call(policy, 13, (ctypes.c_wchar_p, ctypes.c_int), previous, role) < 0:
                        failed_restores.append(role)
                if failed_restores:
                    raise OSError(f"{exc}; previous output could not be restored for roles {failed_restores}") from exc
                raise
