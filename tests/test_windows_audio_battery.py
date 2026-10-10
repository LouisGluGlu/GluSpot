"""Optional driver battery data remains exact-device, typed and read-only."""
from __future__ import annotations

import ctypes
from dataclasses import FrozenInstanceError, replace
import math
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.audio_route_controller import AudioRouteController
from api.audio_routes import AudioRouteDevice
from api.windows_audio_devices import (
    AudioOutputDevice, WindowsAudioDevices, _Endpoint, _WindowsCoreAudioAdapter,
    _CONTAINER_ID, _BLUETOOTH_BATTERY_PERCENT,
)
from tests.test_audio_routes import _Backend, _Discovery, _Sender


class WindowsAudioBatteryEvidenceTests(unittest.TestCase):
    def _outputs(self, endpoint, now=2000.0):
        adapter = SimpleNamespace(snapshot=lambda: ((endpoint,), {1: endpoint.id}))
        backend = WindowsAudioDevices(adapter)
        self.addCleanup(backend.close)
        with patch("api.windows_audio_devices.time.time", return_value=now):
            return backend.list_outputs()[0]

    def test_zero_is_valid_and_models_remain_immutable(self):
        endpoint = _Endpoint("bt", "Earphones", 3, True, 0, "windows-driver-cache", 2000.0)
        output = self._outputs(endpoint)
        self.assertEqual((output.battery_percent, output.battery_source, output.battery_observed_at),
                         (0, "windows-driver-cache", 2000.0))
        with self.assertRaises(FrozenInstanceError):
            output.battery_percent = 1
        self.assertIsNone(AudioOutputDevice("id", "name").battery_percent)
        self.assertIsNone(AudioRouteDevice("id", "name").battery_percent)

    def test_invalid_percent_never_becomes_a_battery_ring(self):
        for percent in (None, True, False, -1, 101, 255, 50.0, "90", float("nan")):
            with self.subTest(percent=percent):
                output = self._outputs(_Endpoint("bt", "Earphones", 3, True,
                                               percent, "windows-driver-cache", 2000.0))
                self.assertEqual((output.battery_percent, output.battery_source,
                                  output.battery_observed_at), (None, "", None))

    def test_stale_future_unknown_source_and_nonbluetooth_evidence_are_ignored(self):
        base = _Endpoint("bt", "Earphones", 3, True, 90, "windows-driver-cache", 2000.0)
        variants = [replace(base, battery_source=""), replace(base, battery_source="nearby-broadcast"),
                    replace(base, is_bluetooth=False)]
        variants += [replace(base, battery_observed_at=value) for value in
                     (None, True, 0, -1, 1969.9, 2000.1, math.inf, math.nan, "2000", 10**400)]
        for endpoint in variants:
            with self.subTest(endpoint=endpoint):
                self.assertIsNone(self._outputs(endpoint).battery_percent)
        self.assertEqual(self._outputs(replace(base, battery_observed_at=1970.0)).battery_percent, 90)

    def test_missing_battery_keeps_other_endpoint_controls_available(self):
        output = self._outputs(_Endpoint("bt", "Earphones", 3, True))
        self.assertEqual(output.id, "bt")
        self.assertTrue(output.is_default)
        self.assertTrue(output.is_bluetooth)
        self.assertIsNone(output.battery_percent)


class NativeBatteryIdentityTests(unittest.TestCase):
    def _adapter(self, rows):
        adapter = object.__new__(_WindowsCoreAudioAdapter)
        instances = tuple(row[0] for row in rows)

        def locate(out, instance, flags):
            self.assertEqual(flags, 0)
            out._obj.value = instances.index(instance)
            return 0

        def property_value(node, key, dtype):
            row = rows[node.value]
            if key is _CONTAINER_ID:
                self.assertEqual(dtype, 13)
                return row[1]
            self.assertIs(key, _BLUETOOTH_BATTERY_PERCENT)
            self.assertEqual(dtype, 3)
            return row[2]

        adapter._cfg = SimpleNamespace(CM_Locate_DevNodeW=locate)
        adapter._present_device_ids = lambda: instances
        adapter._node_property = property_value
        return adapter

    def test_exact_physical_container_joins_hfp_battery_and_excludes_system_serial(self):
        m6, airpods, computer = bytes(range(1, 17)), bytes(range(17, 33)), bytes(range(33, 49))
        rows = [
            ("BTHENUM\\DEV_112233445566\\remote", m6, None),
            ("BTHENUM\\{11111111-1111-1111-1111-111111111111}\\hfp", m6, 90),
            ("BTHENUM\\DEV_AABBCCDDEEFF\\remote", airpods, None),
            ("BTHHFENUM\\profile\\earphones", airpods, None),
            ("BTHENUM\\{22222222-2222-2222-2222-222222222222}\\COM3", computer, 77),
            ("USB\\AirPods Bluetooth\\other", computer, 77),
        ]
        adapter = self._adapter(rows)
        with patch("api.windows_audio_devices.time.time", return_value=2000.0):
            containers, batteries = adapter._bluetooth_evidence()
        self.assertEqual(containers, {m6, airpods})
        self.assertEqual(batteries, {m6: (90, "windows-driver-cache", 2000.0)})

    def test_reordering_nodes_does_not_change_battery_identity(self):
        one, other = bytes(range(1, 17)), bytes(range(17, 33))
        rows = [("BTHENUM\\DEV_112233445566\\same-name", one, None),
                ("BTHENUM\\profile\\same-name", one, 60),
                ("BTHENUM\\DEV_AABBCCDDEEFF\\same-name", other, None),
                ("BTHENUM\\profile\\other-same-name", other, 40)]
        with patch("api.windows_audio_devices.time.time", return_value=2000.0):
            self.assertEqual(self._adapter(rows)._bluetooth_evidence(),
                             self._adapter(list(reversed(rows)))._bluetooth_evidence())

    def test_only_known_physical_address_node_pattern_establishes_transport(self):
        container = bytes(range(1, 17))
        for instance in ("BTHENUM\\DEV_112233445566\\device", "bthle\\dev_aabbccddeeff\\remote"):
            with self.subTest(instance=instance):
                self.assertEqual(self._adapter([(instance, container, 0)])._bluetooth_containers(), {container})
        for instance in ("BTHENUM\\device\\remote", "BTHLEDEVICE\\service\\remote",
                         "BTHENUM\\DEV_1122\\remote", "USB\\DEV_112233445566\\remote"):
            with self.subTest(instance=instance):
                self.assertEqual(self._adapter([(instance, container, 90)])._bluetooth_containers(), set())

    def test_conflicting_peripheral_batteries_are_unknown_and_same_zero_is_valid(self):
        container = bytes(range(1, 17))
        base = [("BTHENUM\\DEV_112233445566\\remote", container, None)]
        for values, expected in (((90, 80), {}), ((0, 0), {container: (0, "windows-driver-cache", 2000.0)}),
                                 ((True, 101), {})):
            rows = base + [(f"BTHHFENUM\\service\\{index}", container, value)
                           for index, value in enumerate(values)]
            with patch("api.windows_audio_devices.time.time", return_value=2000.0):
                self.assertEqual(self._adapter(rows)._bluetooth_evidence()[1], expected)

    def test_absent_or_changed_pnp_never_retains_previous_battery(self):
        adapter = self._adapter([])
        adapter._present_device_ids = lambda: None
        self.assertEqual(adapter._bluetooth_evidence(), (set(), {}))
        adapter._present_device_ids = lambda: ("BTHENUM\\DEV_112233445566\\remote",)
        adapter._cfg.CM_Locate_DevNodeW = lambda *_args: 13
        self.assertEqual(adapter._bluetooth_evidence(), (set(), {}))

    def test_battery_property_requires_exact_sdk_byte_type_size_and_success(self):
        def read(dtype=3, raw=b"\x5a", failure=0):
            def property_value(_node, _key, result_type, buffer, size, _flags):
                result_type._obj.value = dtype
                size._obj.value = len(raw)
                if buffer is None:
                    return 26
                if raw:
                    ctypes.memmove(buffer, raw, len(raw))
                return failure
            adapter = object.__new__(_WindowsCoreAudioAdapter)
            adapter._cfg = SimpleNamespace(CM_Get_DevNode_PropertyW=property_value)
            return adapter._node_property(1, _BLUETOOTH_BATTERY_PERCENT, 3)

        self.assertEqual(read(), 90)
        self.assertEqual(read(raw=b"\x00"), 0)
        for kwargs in ({"dtype": 17}, {"dtype": 7, "raw": b"Z\0\0\0"},
                       {"raw": b"ZZ"}, {"raw": b""}, {"failure": 13}):
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(read(**kwargs))

    @unittest.skipUnless(sys.platform == "win32", "native read-only metadata requires Windows")
    def test_native_snapshot_keeps_battery_bound_to_bluetooth_and_changes_no_defaults(self):
        backend = WindowsAudioDevices()
        self.addCleanup(backend.close)
        first, second = backend.list_outputs(), backend.list_outputs()
        self.assertEqual({d.id for d in first if d.is_default}, {d.id for d in second if d.is_default})
        for device in second:
            if device.battery_percent is not None:
                self.assertTrue(device.is_bluetooth)
                self.assertEqual(device.battery_source, "windows-driver-cache")
                self.assertTrue(0 <= device.battery_percent <= 100)


class AudioRouteBatteryForwardingTests(unittest.TestCase):
    def setUp(self):
        self.backend = _Backend()
        self.controller = AudioRouteController(self.backend, _Discovery(), _Sender(), start_worker=False)
        self.addCleanup(self.controller.close)

    def test_forwarded_fields_are_immutable_and_update_without_connection_notice(self):
        headphone = replace(self.backend.outputs[1], battery_percent=90,
                            battery_source="windows-driver-cache", battery_observed_at=2000.0)
        self.backend.outputs = (self.backend.outputs[0], headphone)
        with patch("api.audio_route_controller.time.time", return_value=2000.0):
            self.controller._refresh_devices()
        initial = self.controller.snapshot()
        device = next(d for d in initial.devices if d.id == headphone.id)
        self.assertEqual((device.battery_percent, device.battery_source, device.battery_observed_at),
                         (90, "windows-driver-cache", 2000.0))
        with self.assertRaises(FrozenInstanceError):
            device.battery_percent = 100
        self.backend.outputs = (self.backend.outputs[0], replace(headphone, battery_percent=0))
        with patch("api.audio_route_controller.time.time", return_value=2000.0):
            self.controller._refresh_devices()
        latest = self.controller.snapshot()
        self.assertEqual(latest.event_serial, initial.event_serial)
        self.assertEqual(next(d for d in latest.devices if d.id == headphone.id).battery_percent, 0)
        self.assertEqual(device.battery_percent, 90)

    def test_controller_rejects_malformed_stale_or_nonbluetooth_backend_data(self):
        base = replace(self.backend.outputs[1], battery_percent=90,
                       battery_source="windows-driver-cache", battery_observed_at=2000.0)
        variants = [replace(base, battery_percent=value) for value in (True, -1, 101, 50.0, "90")]
        variants += [replace(base, battery_observed_at=value) for value in (1969.0, 2001.0, True, math.nan)]
        variants += [replace(base, battery_source="nearby-broadcast"), replace(base, is_bluetooth=False)]
        for device in variants:
            with self.subTest(device=device):
                self.backend.outputs = (device,)
                with patch("api.audio_route_controller.time.time", return_value=2000.0):
                    self.controller._refresh_devices()
                actual = next(d for d in self.controller.snapshot().devices if d.id == device.id)
                self.assertEqual((actual.battery_percent, actual.battery_source,
                                  actual.battery_observed_at), (None, "", None))

    def test_legacy_backends_without_optional_fields_continue_to_work(self):
        self.backend.outputs = ()
        self.backend.list_outputs = lambda: (SimpleNamespace(id="legacy", name="Headphones",
            kind="headphones", is_bluetooth=True, is_default=True),)
        self.controller._refresh_devices()
        device = next(d for d in self.controller.snapshot().devices if d.id == "legacy")
        self.assertTrue(device.is_default)
        self.assertIsNone(device.battery_percent)


if __name__ == "__main__":
    unittest.main()
