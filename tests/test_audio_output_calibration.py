import json
from pathlib import Path
import tempfile
import unittest

from api.audio_output_calibration import measured_local_holds, POLICY
from api.audio_routes import AudioRouteDevice


class AudioOutputCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "calibration.json"
        self.home = AudioRouteDevice("home", "HomePod mini", transport="airplay")
        self.wired = AudioRouteDevice("wired", "Pro-9500")
        self.bluetooth = AudioRouteDevice("m6", "M6", is_bluetooth=True)
        self.devices = (self.home, self.wired, self.bluetooth)
        self.profile = dict(schema_version=1, policy=POLICY, windows_build="10.0.test", verified=True,
                            members=[dict(id=d.id, name=d.name, transport=d.transport,
                                          is_bluetooth=d.is_bluetooth) for d in self.devices],
                            local_holds={"m6": 8820},
                            windows_fingerprints={"m6": "a" * 64, "wired": "a" * 64})

    def tearDown(self):
        self.temp.cleanup()

    def load(self, profile=None, devices=None):
        self.path.write_text(json.dumps(self.profile if profile is None else profile), encoding="utf-8")
        return measured_local_holds(self.devices if devices is None else devices,
                                    path=self.path, windows_build="10.0.test",
                                    fingerprint_getter=lambda endpoint: "a" * 64)

    def test_matching_measured_members_apply_only_named_local_hold(self):
        self.assertEqual(self.load(), {"m6": 8820})
        self.assertEqual(self.load(devices=tuple(reversed(self.devices))), {"m6": 8820})

    def test_changed_membership_does_not_reuse_acoustic_measurement(self):
        self.assertEqual(self.load(devices=(self.home, self.bluetooth)), {})
        self.assertEqual(self.load(devices=self.devices + (AudioRouteDevice("new", "New"),)), {})

    def test_changed_identity_transport_or_name_requires_revalidation(self):
        for field, value in (("id", "new-m6"), ("name", "M6 Headset"),
                             ("transport", "airplay"), ("is_bluetooth", False)):
            with self.subTest(field=field):
                profile = json.loads(json.dumps(self.profile))
                profile["members"][2][field] = value
                self.assertEqual(self.load(profile), {})

    def test_unknown_policy_build_or_unverified_measurement_is_ignored(self):
        for field, value in (("policy", "older"), ("windows_build", "10.0.other"),
                             ("verified", False), ("verified", 1), ("schema_version", True)):
            with self.subTest(field=field):
                self.assertEqual(self.load(dict(self.profile, **{field: value})), {})

    def test_invalid_delays_never_become_command_line_values(self):
        for value in (-1, 22051, True, 1.2, "100", float("nan")):
            with self.subTest(value=value):
                self.assertEqual(self.load(dict(self.profile, local_holds={"m6": value})), {})
        for endpoint in ("home", "not-selected"):
            self.assertEqual(self.load(dict(self.profile, local_holds={endpoint: 100})), {})

    def test_duplicate_and_malformed_members_reject_the_whole_profile(self):
        self.assertEqual(self.load(dict(self.profile, members=[self.profile["members"][0]] * 3)), {})
        self.assertEqual(self.load(dict(self.profile, members=[None] * 3)), {})
        self.assertEqual(self.load(dict(self.profile, local_holds=[])), {})

    def test_missing_corrupt_and_oversized_files_preserve_zero_compensation(self):
        self.assertEqual(measured_local_holds(self.devices, path=self.path), {})
        for raw in (b"not json", b"\xff", b" " * 65537):
            self.path.write_bytes(raw)
            self.assertEqual(measured_local_holds(self.devices, path=self.path), {})

    def test_zero_hold_is_omitted_and_upper_bound_is_accepted(self):
        self.assertEqual(self.load(dict(self.profile, local_holds={"wired": 0, "m6": 22050})), {"m6": 22050})

    def test_driver_or_format_change_and_unknown_evidence_invalidate_measurement(self):
        self.load()
        for current in (None, "b" * 64):
            self.assertEqual(measured_local_holds(self.devices, path=self.path, windows_build="10.0.test",
                                                 fingerprint_getter=lambda endpoint: current), {})
        for stored in ({}, {"m6": "a" * 64}, {"m6": True, "wired": "a" * 64}):
            self.assertEqual(self.load(dict(self.profile, windows_fingerprints=stored)), {})

    def test_duplicate_fields_and_excessive_nesting_are_rejected(self):
        raw = json.dumps(self.profile).replace('"verified": true', '"verified": false, "verified": true')
        for value in (raw, "[" * 5000 + "0" + "]" * 5000):
            self.path.write_text(value, encoding="utf-8")
            self.assertEqual(measured_local_holds(self.devices, path=self.path, windows_build="10.0.test",
                                                 fingerprint_getter=lambda endpoint: "a" * 64), {})


if __name__ == "__main__":
    unittest.main()
