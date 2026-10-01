from __future__ import annotations

import ctypes
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.windows_audio_devices import (
    AudioOutputDevice, WindowsAudioDevices, _ComScope, _Endpoint, _GUID,
    _PROPVARIANT, _WindowsCoreAudioAdapter,
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


class NativeEndpointAbiTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
