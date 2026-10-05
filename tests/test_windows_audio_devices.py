from __future__ import annotations

import ctypes
import re
import struct
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.windows_audio_devices import (
    AudioOutputDevice, WindowsAudioDevices, _ComScope, _Endpoint, _GUID,
    _PROPVARIANT, _WindowsCoreAudioAdapter,
    _CONTAINER_ID, _DISABLE_SYSFX, _DRIVER_VERSION, _DRIVER_PROVIDER,
    _IID_AUDIO_CLIENT, _calibration_fingerprint,
)


class _Adapter:
    def __init__(self):
        self.endpoints = (
            _Endpoint("local", "Speakers", 1),
            _Endpoint("bt", "Same friendly name", 3, True),
            _Endpoint("usb", "Same friendly name", 5),
        )
        self.defaults = {0: "local", 1: "local", 2: "usb"}
        self.calls = []
        self.confirm = True
        self.mutes = {"local": False, "bt": True, "usb": False}
        self.mute_calls = []
        self.confirm_mute = True
        self.volumes = {"local": 0.6, "bt": 0.25, "usb": 1.0}
        self.volume_calls = []
        self.confirm_volume = True

    def snapshot(self):
        return self.endpoints, dict(self.defaults)

    def set_defaults(self, device_id, previous_defaults):
        self.calls.append((device_id, dict(previous_defaults)))
        if self.confirm:
            self.defaults.update({0: device_id, 1: device_id})

    def get_endpoint_mute(self, device_id):
        if device_id not in self.mutes:
            raise OSError("missing render endpoint")
        return self.mutes[device_id]

    def set_endpoint_mute(self, device_id, muted):
        self.mute_calls.append((device_id, muted))
        if self.confirm_mute:
            self.mutes[device_id] = muted

    def get_endpoint_volume(self, device_id):
        if device_id not in self.volumes:
            raise OSError("missing render endpoint")
        return self.volumes[device_id]

    def set_endpoint_volume(self, device_id, volume):
        self.volume_calls.append((device_id, volume))
        if self.confirm_volume:
            self.volumes[device_id] = volume


class WindowsAudioDevicesTests(unittest.TestCase):
    def setUp(self):
        self.adapter = _Adapter()
        self.backend = WindowsAudioDevices(self.adapter)
        self.addCleanup(self.backend.close)

    def test_devices_keep_endpoint_identity_with_duplicate_friendly_names(self):
        outputs = self.backend.list_outputs()
        self.assertEqual([device.id for device in outputs], ["local", "bt", "usb"])
        self.assertEqual([device.kind for device in outputs], ["speaker", "headphones", "headset"])
        self.assertEqual([device.is_bluetooth for device in outputs], [False, True, False])
        self.assertEqual([device.is_default for device in outputs], [True, False, False])
        self.assertEqual(self.adapter.calls, [])

    def test_default_state_tracks_media_role_and_external_changes(self):
        self.adapter.defaults[1] = "bt"
        self.assertEqual([device.id for device in self.backend.list_outputs() if device.is_default], ["bt"])
        self.adapter.defaults[1] = "usb"
        self.assertEqual([device.id for device in self.backend.list_outputs() if device.is_default], ["usb"])

    def test_console_default_is_fallback_when_multimedia_default_is_missing(self):
        self.adapter.defaults.pop(1)
        self.assertTrue(self.backend.list_outputs()[0].is_default)

    def test_no_default_and_no_devices_are_valid_snapshots(self):
        self.adapter.defaults.clear()
        self.assertFalse(any(device.is_default for device in self.backend.list_outputs()))
        self.adapter.endpoints = ()
        self.assertEqual(self.backend.list_outputs(), ())

    def test_unknown_transport_and_form_factor_are_not_guessed_from_name(self):
        self.adapter.endpoints = (_Endpoint("mystery", "AirPods Bluetooth", 999), _Endpoint("nameless", ""))
        mystery, nameless = self.backend.list_outputs()
        self.assertEqual(mystery.kind, "device")
        self.assertFalse(mystery.is_bluetooth)
        self.assertEqual(nameless.name, "音频设备")

    def test_invalid_and_duplicate_endpoint_ids_do_not_create_duplicate_controls(self):
        endpoint = self.adapter.endpoints[0]
        self.adapter.endpoints = (endpoint, endpoint, _Endpoint("", "not a render ID"))
        self.assertEqual(self.backend.list_outputs(), (AudioOutputDevice("local", "Speakers", "speaker", False, True, 0.6),))

    def test_switch_uses_id_and_leaves_communications_default_alone(self):
        self.backend.set_default("bt")
        self.assertEqual(self.adapter.calls, [("bt", {0: "local", 1: "local", 2: "usb"})])
        self.assertEqual(self.adapter.defaults[2], "usb")
        self.assertEqual([device.id for device in self.backend.list_outputs() if device.is_default], ["bt"])

    def test_already_selected_output_requires_no_policy_mutation(self):
        self.backend.set_default("local")
        self.assertEqual(self.adapter.calls, [])

    def test_role_mismatch_still_needs_selection_even_if_media_default_matches(self):
        self.adapter.defaults[1] = "bt"
        self.backend.set_default("bt")
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.adapter.defaults[0], "bt")

    def test_disconnected_endpoint_is_rejected_before_policy_call(self):
        self.adapter.endpoints = (self.adapter.endpoints[0],)
        with self.assertRaisesRegex(ValueError, "no longer available"):
            self.backend.set_default("bt")
        self.assertEqual(self.adapter.calls, [])

    def test_empty_invalid_and_embedded_nul_ids_are_rejected(self):
        for device_id in ("", None, "bt\0local"):
            with self.subTest(device_id=device_id), self.assertRaises(ValueError):
                self.backend.set_default(device_id)
        self.assertEqual(self.adapter.calls, [])

    def test_failed_readback_cannot_report_a_successful_switch(self):
        self.adapter.confirm = False
        with self.assertRaisesRegex(RuntimeError, "did not confirm"):
            self.backend.set_default("bt")
        self.assertTrue(self.backend.list_outputs()[0].is_default)

    def test_device_disappearing_after_policy_success_is_reported(self):
        original = self.adapter.set_defaults

        def disconnect_after_select(*args):
            original(*args)
            self.adapter.endpoints = (self.adapter.endpoints[0],)

        self.adapter.set_defaults = disconnect_after_select
        with self.assertRaisesRegex(RuntimeError, "did not confirm"):
            self.backend.set_default("bt")

    def test_native_errors_propagate_instead_of_becoming_fake_devices(self):
        self.adapter.snapshot = lambda: (_ for _ in ()).throw(OSError("enumeration failed"))
        with self.assertRaisesRegex(OSError, "enumeration failed"):
            self.backend.list_outputs()

    def test_close_is_idempotent_and_rejects_later_work(self):
        self.backend.close()
        self.backend.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.backend.list_outputs()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.backend.set_default("bt")

    def test_non_windows_does_not_load_dlls_or_create_fake_outputs(self):
        backend = WindowsAudioDevices()
        with patch("api.windows_audio_devices.sys.platform", "linux"):
            self.assertEqual(backend.list_outputs(), ())
            with self.assertRaisesRegex(RuntimeError, "requires Windows"):
                backend.set_default("bt")
        backend.close()

    def test_endpoint_mute_reads_and_changes_only_explicit_endpoint(self):
        self.assertFalse(self.backend.get_endpoint_mute("local"))
        self.assertTrue(self.backend.get_endpoint_mute("bt"))
        self.backend.set_endpoint_mute("local", True)
        self.assertTrue(self.backend.get_endpoint_mute("local"))
        self.backend.set_endpoint_mute("local", False)
        self.assertFalse(self.backend.get_endpoint_mute("local"))
        self.assertEqual(self.adapter.mute_calls, [("local", True), ("local", False)])
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.adapter.defaults, {0: "local", 1: "local", 2: "usb"})
        self.assertTrue(self.backend.get_endpoint_mute("bt"))

    def test_endpoint_mute_is_idempotent_and_strictly_validates_input(self):
        self.backend.set_endpoint_mute("local", False)
        for identity in (None, "", "local\0other"):
            for operation in (self.backend.get_endpoint_mute, lambda value: self.backend.set_endpoint_mute(value, True)):
                with self.subTest(identity=identity), self.assertRaises(ValueError):
                    operation(identity)
        for muted in (None, 0, 1, "false"):
            with self.subTest(muted=muted), self.assertRaises(ValueError):
                self.backend.set_endpoint_mute("local", muted)
        self.assertEqual(self.adapter.mute_calls, [])

    def test_endpoint_mute_failure_and_failed_readback_do_not_claim_success(self):
        with self.assertRaisesRegex(OSError, "missing"):
            self.backend.set_endpoint_mute("gone", True)
        self.adapter.confirm_mute = False
        with self.assertRaisesRegex(RuntimeError, "did not confirm"):
            self.backend.set_endpoint_mute("local", True)
        self.assertFalse(self.backend.get_endpoint_mute("local"))

    def test_endpoint_mute_work_after_close_and_on_other_platform_is_rejected(self):
        self.backend.close()
        for operation in (self.backend.get_endpoint_mute, lambda value: self.backend.set_endpoint_mute(value, True)):
            with self.assertRaisesRegex(RuntimeError, "closed"):
                operation("local")
        backend = WindowsAudioDevices()
        with patch("api.windows_audio_devices.sys.platform", "linux"):
            for operation in (backend.get_endpoint_mute, lambda value: backend.set_endpoint_mute(value, True)):
                with self.assertRaisesRegex(RuntimeError, "requires Windows"):
                    operation("local")

    def test_volume_snapshot_reads_actual_scalar_and_external_changes_without_mutations(self):
        first = self.backend.list_outputs()
        self.assertEqual([(d.volume, d.volume_muted) for d in first], [(0.6, False), (0.25, True), (1.0, False)])
        self.adapter.volumes["bt"] = 0.85
        self.adapter.mutes["bt"] = False
        second = self.backend.list_outputs()
        self.assertEqual((second[1].volume, second[1].volume_muted), (0.85, False))
        self.assertEqual(self.adapter.volume_calls, [])
        self.assertEqual(self.adapter.mute_calls, [])
        self.assertEqual(self.adapter.calls, [])

    def test_endpoint_without_volume_support_remains_listed_with_unknown_value(self):
        self.adapter.volumes.pop("bt")
        devices = self.backend.list_outputs()
        self.assertEqual([d.id for d in devices], ["local", "bt", "usb"])
        self.assertIsNone(devices[1].volume)
        self.assertEqual(devices[0].volume, 0.6)
        self.assertEqual(devices[2].volume, 1.0)

    def test_volume_changes_only_named_endpoint_and_preserves_cast_source_mute(self):
        self.adapter.mutes["local"] = True
        self.assertAlmostEqual(self.backend.set_endpoint_volume("local", 0.3), 0.3)
        self.assertAlmostEqual(self.backend.get_endpoint_volume("local"), 0.3)
        self.assertEqual(self.adapter.volume_calls, [("local", 0.3)])
        self.assertEqual(self.adapter.volumes["bt"], 0.25)
        self.assertTrue(self.adapter.mutes["local"])
        self.assertEqual(self.adapter.mute_calls, [])
        self.assertEqual(self.adapter.calls, [])
        self.backend.set_endpoint_volume("local", 0.3)
        self.assertEqual(self.adapter.volume_calls, [("local", 0.3)])

    def test_volume_input_failure_and_unconfirmed_readback_never_claim_success(self):
        for value in (None, True, "0.4", -0.1, 1.1, float("nan"), float("inf"), 10 ** 400):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.backend.set_endpoint_volume("local", value)
        self.assertEqual(self.adapter.volume_calls, [])
        with self.assertRaisesRegex(OSError, "missing"):
            self.backend.set_endpoint_volume("gone", 0.4)
        self.adapter.confirm_volume = False
        with self.assertRaisesRegex(RuntimeError, "did not confirm"):
            self.backend.set_endpoint_volume("local", 0.4)
        self.assertEqual(self.backend.get_endpoint_volume("local"), 0.6)
        self.assertEqual(self.adapter.mute_calls, [])

    def test_endpoint_volume_rejects_invalid_identity_closed_backend_and_other_platform(self):
        for identity in (None, "", "local\0other"):
            for operation in (self.backend.get_endpoint_volume, lambda value: self.backend.set_endpoint_volume(value, 0.4)):
                with self.subTest(identity=identity), self.assertRaises(ValueError):
                    operation(identity)
        self.backend.close()
        for operation in (self.backend.get_endpoint_volume, lambda value: self.backend.set_endpoint_volume(value, 0.4)):
            with self.assertRaisesRegex(RuntimeError, "closed"):
                operation("local")
        backend = WindowsAudioDevices()
        with patch("api.windows_audio_devices.sys.platform", "linux"):
            for operation in (backend.get_endpoint_volume, lambda value: backend.set_endpoint_volume(value, 0.4)):
                with self.assertRaisesRegex(RuntimeError, "requires Windows"):
                    operation("local")

    def test_airpods_pro_glyph_uses_explicit_model_without_guessing_bluetooth_transport(self):
        self.adapter.endpoints = (
            _Endpoint("pro", "Headphones (AirPods Pro)", 3, False),
            _Endpoint("brand", "AirPods", 3, True),
            _Endpoint("speaker", "AirPods Pro speaker", 1, False),
        )
        devices = self.backend.list_outputs()
        self.assertEqual([d.kind for d in devices], ["airpods-pro", "headphones", "speaker"])
        self.assertFalse(devices[0].is_bluetooth)

    def test_calibration_fingerprint_read_is_locked_and_has_no_output_mutations(self):
        observed = []
        def read(endpoint):
            self.assertTrue(self.backend._lock._is_owned())
            observed.append(endpoint)
            return "a" * 64
        self.adapter.get_endpoint_calibration_fingerprint = read
        self.assertEqual(self.backend.get_endpoint_calibration_fingerprint("bt"), "a" * 64)
        self.assertEqual(observed, ["bt"])
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.adapter.volume_calls, [])
        self.assertEqual(self.adapter.mute_calls, [])

    def test_calibration_fingerprint_unknown_backend_failure_and_malformed_hash_are_unknown(self):
        self.assertIsNone(self.backend.get_endpoint_calibration_fingerprint("bt"))
        for value in (None, "a" * 63, "A" * 64, "z" * 64, True, b"a" * 64):
            self.adapter.get_endpoint_calibration_fingerprint = lambda _id, v=value: v
            self.assertIsNone(self.backend.get_endpoint_calibration_fingerprint("bt"))
        self.adapter.get_endpoint_calibration_fingerprint = lambda _id: (_ for _ in ()).throw(OSError("gone"))
        self.assertIsNone(self.backend.get_endpoint_calibration_fingerprint("bt"))
        backend = WindowsAudioDevices()
        with patch("api.windows_audio_devices.sys.platform", "linux"):
            self.assertIsNone(backend.get_endpoint_calibration_fingerprint("bt"))

    def test_calibration_fingerprint_reuses_endpoint_identity_and_close_contract(self):
        for endpoint in (None, "", "bt\0other"):
            with self.assertRaises(ValueError):
                self.backend.get_endpoint_calibration_fingerprint(endpoint)
        self.backend.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.backend.get_endpoint_calibration_fingerprint("bt")


class CalibrationFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.format = struct.pack("<HHIIHHH", 3, 2, 48000, 384000, 8, 32, 0)
        self.container = _GUID.from_string("11223344-5566-7788-99aa-bbccddeeff00").bytes()
        self.drivers = (("SWD\\MMDEVAPI\\endpoint", "10.0.1.1", "Microsoft"),
                        ("BTHENUM\\speaker", "10.0.2.1", "Microsoft"))

    def fingerprint(self, **changes):
        args = {"format_blob": self.format, "container": self.container,
                "disable_sysfx": 0, "drivers": self.drivers}
        args.update(changes)
        return _calibration_fingerprint(**args)

    def test_canonical_hash_is_stable_across_enumeration_order_and_pnp_case(self):
        value = self.fingerprint()
        self.assertRegex(value, r"^[0-9a-f]{64}$")
        self.assertEqual(value, self.fingerprint(drivers=tuple(reversed(self.drivers))))
        self.assertEqual(value, self.fingerprint(drivers=tuple(tuple(s.upper() for s in r) for r in self.drivers)))

    def test_each_format_driver_container_and_effect_change_invalidates_evidence(self):
        old = self.fingerprint()
        variants = [
            {"format_blob": struct.pack("<HHIIHHH", 3, 2, 44100, 352800, 8, 32, 0)},
            {"container": _GUID.from_string("11223345-5566-7788-99aa-bbccddeeff00").bytes()},
            {"disable_sysfx": 1},
            {"disable_sysfx": "unset"},
            {"drivers": (self.drivers[0], ("BTHENUM\\speaker", "10.0.3.1", "Microsoft"))},
            {"drivers": (self.drivers[0], ("BTHENUM\\speaker", "10.0.2.1", "Other"))},
            {"drivers": (self.drivers[0], ("BTHENUM\\replacement", "10.0.2.1", "Microsoft"))},
            {"drivers": (self.drivers[1],)},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(old, self.fingerprint(**variant))

    def test_known_sysfx_override_absence_is_stable_and_distinct_from_enabled_disabled(self):
        absent = self.fingerprint(disable_sysfx="unset")
        self.assertRegex(absent, r"^[0-9a-f]{64}$")
        self.assertEqual(absent, self.fingerprint(disable_sysfx="unset"))
        self.assertNotEqual(absent, self.fingerprint(disable_sysfx=0))
        self.assertNotEqual(absent, self.fingerprint(disable_sysfx=1))

    def test_incomplete_or_malformed_evidence_cannot_validate_a_calibration(self):
        variants = [
            {"format_blob": None}, {"format_blob": self.format[:17]},
            {"format_blob": self.format + b"\0"},
            {"format_blob": struct.pack("<HHIIHHH", 0, 2, 48000, 384000, 8, 32, 0)},
            {"container": None}, {"container": bytes(16)}, {"container": bytes(15)},
            {"disable_sysfx": None}, {"disable_sysfx": True}, {"disable_sysfx": 2},
            {"disable_sysfx": "unknown"},
            {"drivers": None}, {"drivers": ()}, {"drivers": (self.drivers[0],)},
            {"drivers": (self.drivers[1], self.drivers[1])},
            {"drivers": (("BTHENUM\\speaker", "", "Microsoft"),)},
            {"drivers": (("BTHENUM\\speaker", "1.0", None),)},
            {"drivers": (("BTHENUM\\speaker\0another", "1.0", "Microsoft"),)},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertIsNone(self.fingerprint(**variant))


class NativeEndpointAbiTests(unittest.TestCase):
    def test_mix_format_reads_only_sdk_activate_and_getmixformat_then_frees_memory(self):
        adapter = object.__new__(_WindowsCoreAudioAdapter)
        freed, calls = [], []
        adapter._ole32 = SimpleNamespace(CoTaskMemFree=lambda pointer: freed.append(pointer.value))
        scope = unittest.mock.MagicMock()
        raw = ctypes.create_string_buffer(struct.pack("<HHIIHHH", 3, 2, 48000, 384000, 8, 32, 0))
        def call(pointer, index, argtypes, *args):
            calls.append((pointer, index, argtypes))
            if pointer == "device":
                self.assertEqual(index, 3)
                self.assertEqual(args[0]._obj.bytes(), _GUID.from_string(_IID_AUDIO_CLIENT).bytes())
                self.assertEqual(args[1:3], (23, None))
                args[-1]._obj.value = 100
            elif pointer.value == 100:
                self.assertEqual(index, 8)  # No Initialize/Start/GetService.
                self.assertEqual(argtypes, (ctypes.POINTER(ctypes.c_void_p),))
                args[0]._obj.value = ctypes.addressof(raw)
            return 0
        with patch("api.windows_audio_devices._call", side_effect=call):
            self.assertEqual(adapter._mix_format(scope, "device"), raw.raw[:18])
        self.assertEqual([row[1] for row in calls], [3, 8])
        self.assertEqual(freed, [ctypes.addressof(raw)])
        scope.own.assert_called_once()

    def test_mix_format_frees_allocated_buffer_on_failed_or_invalid_get(self):
        for result, extra in ((-2147467259, 0), (0, 513)):
            with self.subTest(result=result, extra=extra):
                adapter = object.__new__(_WindowsCoreAudioAdapter)
                freed = []
                adapter._ole32 = SimpleNamespace(CoTaskMemFree=lambda pointer: freed.append(pointer.value))
                scope = unittest.mock.MagicMock()
                raw = ctypes.create_string_buffer(struct.pack("<HHIIHHH", 3, 2, 48000, 384000, 8, 32, extra))
                def call(_pointer, index, _argtypes, *args):
                    args[-1]._obj.value = 100 if index == 3 else ctypes.addressof(raw)
                    return 0 if index == 3 else result
                with patch("api.windows_audio_devices._call", side_effect=call):
                    if result < 0:
                        with self.assertRaisesRegex(OSError, "GetMixFormat"):
                            adapter._mix_format(scope, "device")
                    else:
                        self.assertIsNone(adapter._mix_format(scope, "device"))
                self.assertEqual(freed, [ctypes.addressof(raw)])

    def test_fingerprint_requires_active_render_readonly_store_and_complete_native_evidence(self):
        adapter = object.__new__(_WindowsCoreAudioAdapter)
        adapter._ole32 = object()
        scope = unittest.mock.MagicMock()
        scope.__enter__.return_value = scope
        container = _GUID.from_string("11223344-5566-7788-99aa-bbccddeeff00").bytes()
        wave = struct.pack("<HHIIHHH", 3, 2, 48000, 384000, 8, 32, 0)
        drivers = (("BTHENUM\\speaker", "10.0.1.1", "Microsoft"),)
        calls = []
        def call(pointer, index, argtypes, *args):
            self.assertEqual(pointer, "device")
            calls.append((index, args))
            if index == 6: args[0]._obj.value = 1
            elif index == 4:
                self.assertEqual(args[0], 0)  # STGM_READ, never write.
                args[-1]._obj.value = 200
            else: self.fail(f"Unexpected COM mutation/stream method {index}")
            return 0
        def prop(_store, key):
            if key is _CONTAINER_ID: return container
            if key is _DISABLE_SYSFX: return 0
            self.fail("Unexpected endpoint property")
        with patch("api.windows_audio_devices._ComScope", return_value=scope), \
             patch.object(adapter, "_render_endpoint", return_value="device") as endpoint, \
             patch.object(adapter, "_property", side_effect=prop), \
             patch.object(adapter, "_calibration_sysfx_state", return_value=0), \
             patch.object(adapter, "_mix_format", return_value=wave), \
             patch.object(adapter, "_calibration_drivers", return_value=drivers), \
             patch("api.windows_audio_devices._call", side_effect=call):
            self.assertEqual(adapter.get_endpoint_calibration_fingerprint("render-id"),
                             _calibration_fingerprint(wave, container, 0, drivers))
        endpoint.assert_called_once_with(scope, "render-id")
        self.assertEqual([row[0] for row in calls], [6, 4, 6])

    def test_fingerprint_rejects_unknown_effect_flag_before_audio_client_activation(self):
        adapter = object.__new__(_WindowsCoreAudioAdapter)
        adapter._ole32 = object()
        scope = unittest.mock.MagicMock()
        scope.__enter__.return_value = scope
        def call(_pointer, index, _types, *args):
            args[-1]._obj.value = 1 if index == 6 else 200
            return 0
        with patch("api.windows_audio_devices._ComScope", return_value=scope), \
             patch.object(adapter, "_render_endpoint", return_value="device"), \
             patch.object(adapter, "_property", side_effect=[bytes(range(16)), None]), \
             patch.object(adapter, "_calibration_sysfx_state", return_value=None), \
             patch.object(adapter, "_mix_format") as activate, \
             patch("api.windows_audio_devices._call", side_effect=call):
            self.assertIsNone(adapter.get_endpoint_calibration_fingerprint("render-id"))
        activate.assert_not_called()

    def test_missing_sysfx_property_is_unknown_even_when_getvalue_succeeds(self):
        adapter = object.__new__(_WindowsCoreAudioAdapter)
        cleared = []
        adapter._ole32 = SimpleNamespace(PropVariantClear=lambda value: cleared.append(value._obj))
        # Both current physical endpoints return S_OK/VT_EMPTY. This is not
        # SDK VT_UI4/ENDPOINT_SYSFX_ENABLED and must not be guessed as zero.
        with patch("api.windows_audio_devices._call", return_value=0):
            self.assertIsNone(adapter._property("store", _DISABLE_SYSFX))
        self.assertEqual(len(cleared), 1)

    def test_calibration_sysfx_preserves_raw_absence_and_rejects_failed_or_wrong_typed_reads(self):
        adapter = object.__new__(_WindowsCoreAudioAdapter)
        cleared = []
        adapter._ole32 = SimpleNamespace(PropVariantClear=lambda value: cleared.append(value._obj))
        for result, vt, raw, expected in (
            (0, 0, 0, "unset"), (0, 19, 0, 0), (0, 19, 1, 1),
            (-2147467259, 0, 0, None), (1, 0, 0, None),
            (0, 11, 0, None), (0, 19, 2, None), (0, 31, 0, None),
        ):
            def call(_pointer, index, types, key, value):
                self.assertEqual(index, 5)
                self.assertEqual(key._obj.pid, 5)
                self.assertEqual(types, (ctypes.POINTER(type(_DISABLE_SYSFX)), ctypes.POINTER(_PROPVARIANT)))
                value._obj.vt = vt; value._obj.value.uint = raw
                return result
            with self.subTest(result=result, vt=vt, raw=raw), \
                 patch("api.windows_audio_devices._call", side_effect=call):
                self.assertEqual(adapter._calibration_sysfx_state("store"), expected)
        self.assertEqual(len(cleared), 8)

    def test_typed_pnp_strings_use_byte_count_and_missing_fields_never_become_empty_values(self):
        adapter = object.__new__(_WindowsCoreAudioAdapter)
        raw = "Microsoft\0".encode("utf-16-le")
        calls = []
        def prop(_node, key, prop_type, buffer, size, flags):
            self.assertEqual(key._obj.pid, 9)
            self.assertEqual(flags, 0)
            calls.append(buffer is None)
            prop_type._obj.value = 0x12
            size._obj.value = len(raw)
            if buffer is None: return 26
            ctypes.memmove(buffer, raw, len(raw)); return 0
        adapter._cfg = SimpleNamespace(CM_Get_DevNode_PropertyW=prop)
        self.assertEqual(adapter._node_property(1, _DRIVER_PROVIDER, 0x12), "Microsoft")
        self.assertEqual(calls, [True, False])
        adapter._cfg.CM_Get_DevNode_PropertyW = lambda *_args: 13
        self.assertIsNone(adapter._node_property(1, _DRIVER_PROVIDER, 0x12))

    def test_driver_rows_match_present_container_and_require_every_matching_driver_field(self):
        adapter = object.__new__(_WindowsCoreAudioAdapter)
        container = bytes(range(16))
        instances = ("SWD\\endpoint", "BTHENUM\\speaker", "USB\\other")
        def locate(out, instance, flags):
            self.assertEqual(flags, 0)
            out._obj.value = instances.index(instance)
            return 0
        adapter._cfg = SimpleNamespace(CM_Locate_DevNodeW=locate)
        def prop(node, key, expected):
            if key is _CONTAINER_ID:
                self.assertEqual(expected, 13)
                return container if node.value != 2 else bytes(16)
            self.assertEqual(expected, 0x12)
            return "10.0.1.1" if key is _DRIVER_VERSION else "Microsoft"
        with patch.object(adapter, "_present_device_ids", return_value=instances), \
             patch.object(adapter, "_node_property", side_effect=prop):
            self.assertEqual(adapter._calibration_drivers(container),
                             tuple((identity, "10.0.1.1", "Microsoft") for identity in instances[:2]))
        with patch.object(adapter, "_present_device_ids", return_value=instances), \
             patch.object(adapter, "_node_property", side_effect=lambda n, k, t: container if k is _CONTAINER_ID else None):
            self.assertIsNone(adapter._calibration_drivers(container))
        adapter._cfg.CM_Locate_DevNodeW = lambda *_args: 13
        with patch.object(adapter, "_present_device_ids", return_value=instances):
            self.assertIsNone(adapter._calibration_drivers(container))

    def test_endpoint_scalar_uses_sdk_float_and_never_setmute_or_default_output(self):
        adapter = object.__new__(_WindowsCoreAudioAdapter)
        adapter._ole32 = object()
        scope = unittest.mock.MagicMock()
        scope.__enter__.return_value = scope
        calls = []
        def call(pointer, index, argtypes, *args):
            self.assertEqual(pointer, "endpoint-volume")
            calls.append((index, argtypes, args))
            if index == 9:
                args[0]._obj.value = 0.3
            return 0
        with patch("api.windows_audio_devices._ComScope", return_value=scope), \
             patch.object(adapter, "_endpoint_volume", return_value="endpoint-volume") as activate, \
             patch("api.windows_audio_devices._call", side_effect=call):
            self.assertAlmostEqual(adapter.get_endpoint_volume("render-id"), 0.3)
            adapter.set_endpoint_volume("render-id", 0.7)
        self.assertEqual([entry[0] for entry in calls], [9, 7])
        self.assertEqual(calls[0][1], (ctypes.POINTER(ctypes.c_float),))
        self.assertEqual(calls[1][1], (ctypes.c_float, ctypes.c_void_p))
        self.assertEqual(calls[1][2], (0.7, None))
        self.assertEqual([entry.args[1] for entry in activate.call_args_list], ["render-id", "render-id"])

    def test_endpoint_mute_uses_sdk_bool_and_only_getmute_setmute_vtable_entries(self):
        adapter = object.__new__(_WindowsCoreAudioAdapter)
        adapter._ole32 = object()
        scope = unittest.mock.MagicMock()
        scope.__enter__.return_value = scope
        calls = []
        def call(pointer, index, argtypes, *args):
            self.assertEqual(pointer, "endpoint-volume")
            calls.append((index, argtypes, args))
            if index == 15:
                args[0]._obj.value = 1
            return 0
        with patch("api.windows_audio_devices._ComScope", return_value=scope), \
             patch.object(adapter, "_endpoint_volume", return_value="endpoint-volume") as activate, \
             patch("api.windows_audio_devices._call", side_effect=call):
            self.assertTrue(adapter.get_endpoint_mute("render-id"))
            adapter.set_endpoint_mute("render-id", False)
        self.assertEqual([call[0] for call in calls], [15, 14])
        self.assertEqual(calls[1][1], (ctypes.c_int32, ctypes.c_void_p))
        self.assertEqual(calls[1][2], (0, None))
        self.assertEqual([call.args[1] for call in activate.call_args_list], ["render-id", "render-id"])

    def test_endpoint_mute_rejects_capture_endpoint_before_volume_activation(self):
        adapter = object.__new__(_WindowsCoreAudioAdapter)
        scope = unittest.mock.MagicMock()
        scope.create.return_value = "enumerator"
        calls = []
        def call(pointer, index, argtypes, *args):
            calls.append((pointer, index))
            if pointer == "enumerator":
                args[-1]._obj.value = 100
            elif pointer.value == 100 and index == 0:
                args[-1]._obj.value = 200
            elif pointer.value == 200:
                args[-1]._obj.value = 1  # eCapture
            return 0
        with patch("api.windows_audio_devices._call", side_effect=call):
            with self.assertRaisesRegex(ValueError, "render endpoint"):
                adapter._endpoint_volume(scope, "capture-id")
        self.assertEqual(len(calls), 3)

    def test_guid_and_propvariant_layout_match_sdk_abi(self):
        self.assertEqual(ctypes.sizeof(_GUID), 16)
        self.assertEqual(_GUID.from_string("a95664d2-9614-4f35-a746-de8db63617e6").bytes(),
                         bytes.fromhex("d26456a91496354fa746de8db63617e6"))
        self.assertEqual(_PROPVARIANT.value.offset, 8)
        self.assertEqual(ctypes.sizeof(_PROPVARIANT), 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)

    def test_com_scope_balances_initialization_and_reverse_releases_on_exception(self):
        calls = []
        ole32 = SimpleNamespace(CoInitializeEx=lambda *_args: 0,
                                CoUninitialize=lambda: calls.append("uninitialize"))
        with patch("api.windows_audio_devices._call", side_effect=lambda pointer, *_a, **_k: calls.append(pointer)):
            with self.assertRaisesRegex(ValueError, "work failed"):
                with _ComScope(ole32) as scope:
                    scope.own(10)
                    scope.own(20)
                    raise ValueError("work failed")
        self.assertEqual(calls, [20, 10, "uninitialize"])

    def test_com_scope_does_not_uninitialize_an_existing_sta(self):
        calls = []
        ole32 = SimpleNamespace(CoInitializeEx=lambda *_args: -2147417850,
                                CoUninitialize=lambda: calls.append("uninitialize"))
        with _ComScope(ole32):
            pass
        self.assertEqual(calls, [])

    def test_native_policy_failure_restores_the_completed_role_only(self):
        calls = []
        scope = unittest.mock.MagicMock()
        scope.__enter__.return_value = scope
        scope.create.return_value = "policy-pointer"
        adapter = object.__new__(_WindowsCoreAudioAdapter)
        adapter._ole32 = object()

        def call(pointer, index, argtypes, device_id, role):
            calls.append((index, device_id, role))
            return -2147467259 if device_id == "bt" and role == 1 else 0

        with patch("api.windows_audio_devices._ComScope", return_value=scope), \
             patch("api.windows_audio_devices._call", side_effect=call):
            with self.assertRaisesRegex(OSError, "SetDefaultEndpoint"):
                adapter.set_defaults("bt", {0: "local", 1: "local", 2: "voice"})
        self.assertEqual(calls, [(13, "bt", 0), (13, "bt", 1), (13, "local", 0)])

    def test_native_policy_changes_only_console_and_multimedia_roles(self):
        scope = unittest.mock.MagicMock()
        scope.__enter__.return_value = scope
        scope.create.return_value = "policy-pointer"
        adapter = object.__new__(_WindowsCoreAudioAdapter)
        adapter._ole32 = object()
        with patch("api.windows_audio_devices._ComScope", return_value=scope), \
             patch("api.windows_audio_devices._call", return_value=0) as call:
            adapter.set_defaults("bt", {0: "local", 1: "local", 2: "voice"})
        self.assertEqual([entry.args[-1] for entry in call.call_args_list], [0, 1])
        self.assertTrue(all(entry.args[1] == 13 for entry in call.call_args_list))

    def test_bluetooth_transport_uses_present_pnp_container_guid_without_name_matching(self):
        container = _GUID.from_string("11223344-5566-7788-99aa-bbccddeeff00")
        ids = "USB\\AirPods Bluetooth\0BTHENUM\\real-speaker\0BTHLEDEVICE\\real-headphone\0\0"
        looked_up = []

        def size(result, _filter, flags):
            self.assertEqual(flags, 0x100)
            result._obj.value = len(ids)
            return 0

        def device_list(_filter, result, _length, flags):
            self.assertEqual(flags, 0x100)
            result[:] = ids
            return 0

        def locate(node, instance_id, _flags):
            looked_up.append(instance_id)
            node._obj.value = len(looked_up)
            return 0

        def property_value(node, _key, prop_type, result, result_size, _flags):
            prop_type._obj.value = 13
            result_size._obj.value = 16
            ctypes.memmove(result, ctypes.byref(container), 16)
            return 0

        adapter = object.__new__(_WindowsCoreAudioAdapter)
        adapter._cfg = SimpleNamespace(CM_Get_Device_ID_List_SizeW=size,
                                       CM_Get_Device_ID_ListW=device_list,
                                       CM_Locate_DevNodeW=locate,
                                       CM_Get_DevNode_PropertyW=property_value)
        self.assertEqual(adapter._bluetooth_containers(), {container.bytes()})
        self.assertEqual(looked_up, ["BTHENUM\\real-speaker", "BTHLEDEVICE\\real-headphone"])

    def test_property_read_clears_allocated_variant_even_on_hresult_failure(self):
        adapter = object.__new__(_WindowsCoreAudioAdapter)
        cleared = []
        adapter._ole32 = SimpleNamespace(PropVariantClear=lambda value: cleared.append(value._obj))
        from api.windows_audio_devices import _FRIENDLY_NAME
        with patch("api.windows_audio_devices._call", return_value=-2147467259):
            self.assertIsNone(adapter._property("store", _FRIENDLY_NAME))
        self.assertEqual(len(cleared), 1)

    @unittest.skipUnless(sys.platform == "win32", "native read-only enumeration requires Windows")
    def test_native_enumeration_is_read_only_and_repeatable(self):
        backend = WindowsAudioDevices()
        self.addCleanup(backend.close)
        first = backend.list_outputs()
        second = backend.list_outputs()
        self.assertIsInstance(first, tuple)
        self.assertTrue(all(isinstance(device, AudioOutputDevice) and device.id for device in second))
        self.assertEqual(len({device.id for device in second}), len(second))

    @unittest.skipUnless(sys.platform == "win32", "native read-only fingerprint requires Windows")
    def test_native_fingerprint_is_read_only_or_explicitly_unknown(self):
        backend = WindowsAudioDevices()
        self.addCleanup(backend.close)
        before = backend.list_outputs()
        for endpoint in before[:2]:
            value = backend.get_endpoint_calibration_fingerprint(endpoint.id)
            self.assertTrue(value is None or re.fullmatch(r"[0-9a-f]{64}", value))
        after = backend.list_outputs()
        self.assertEqual([(d.id, d.is_default, d.volume, d.volume_muted) for d in before],
                         [(d.id, d.is_default, d.volume, d.volume_muted) for d in after])


if __name__ == "__main__":
    unittest.main()
