from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools import audit_windows_compatibility as audit


class WindowsCompatibilityAuditTests(unittest.TestCase):
    def test_exact_symbol_and_provider_classification_uses_target_minimum(self):
        finding = audit.classify_import("KERNEL32.DLL", "SetThreadDescription", "direct", (6, 1, 7601))
        self.assertEqual(finding["status"], "known_loader_blocker")
        self.assertEqual(finding["minimum_windows"], "Windows 10 1607")
        self.assertTrue(finding["documentation"].startswith("https://learn.microsoft.com/"))
        self.assertIsNone(audit.classify_import("custom.dll", "SetThreadDescription", "direct", (6, 1, 7601)))
        self.assertIsNone(audit.classify_import("kernel32.dll", "SetThreadDescription", "direct", (10, 0, 14393)))
        self.assertIsNone(audit.classify_import("kernel32.dll", "FutureUnknownApi", "direct", (6, 1, 7601)))

    def test_delay_import_is_conditional_not_a_claim_of_loader_failure(self):
        finding = audit.classify_import("api-ms-win-core-synch-l1-2-0.dll", "WaitOnAddress", "delay", (6, 1, 7601))
        self.assertEqual(finding["status"], "known_unavailable_if_called")
        self.assertIsNone(audit.classify_import("kernel32.dll", "WaitOnAddress", "direct", (6, 2, 0)))

    def _module(self, *, direct=(), delay=()):
        pe = SimpleNamespace(
            FILE_HEADER=SimpleNamespace(Machine=0x8664),
            OPTIONAL_HEADER=SimpleNamespace(MajorOperatingSystemVersion=6, MinorOperatingSystemVersion=0,
                                           MajorSubsystemVersion=6, MinorSubsystemVersion=0),
            VS_FIXEDFILEINFO=[SimpleNamespace(FileVersionMS=(3 << 16) + 12, FileVersionLS=10 << 16,
                                             ProductVersionMS=(3 << 16) + 12, ProductVersionLS=10 << 16)],
            FileInfo=[[SimpleNamespace(StringTable=[SimpleNamespace(entries={b"ProductName": b"Python"})])]],
            DIRECTORY_ENTRY_IMPORT=direct, DIRECTORY_ENTRY_DELAY_IMPORT=delay,
            parse_data_directories=Mock(), close=Mock())
        module = SimpleNamespace(PE=Mock(return_value=pe), DIRECTORY_ENTRY={
            "IMAGE_DIRECTORY_ENTRY_IMPORT": 1, "IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT": 13,
            "IMAGE_DIRECTORY_ENTRY_RESOURCE": 2})
        return module, pe

    def _entry(self, library, *symbols):
        return SimpleNamespace(dll=library, imports=[SimpleNamespace(name=symbol, ordinal=7) for symbol in symbols])

    def test_reads_direct_delay_version_and_ordinals_as_data_then_closes(self):
        module, pe = self._module(
            direct=[self._entry(b"kernel32.dll", b"CopyFile2", None)],
            delay=[self._entry(b"kernel32.dll", b"SetThreadDescription")])
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "python312.dll"
            binary.write_bytes(b"synthetic PE fixture")
            result = audit.inspect_pe(binary, (6, 1, 7601), pe_module=module)
        self.assertEqual(result["status"], "known_blocked")
        self.assertEqual(result["version_info"]["fixed_file_version"], "3.12.10.0")
        self.assertEqual(result["version_info"]["display_version"], "3.12.10.0")
        self.assertEqual(result["version_info"]["strings"]["ProductName"], "Python")
        self.assertEqual(result["imports"][0]["symbols"], ["CopyFile2", "#7"])
        self.assertEqual([item["import_kind"] for item in result["known_findings"]], ["direct", "delay"])
        module.PE.assert_called_once_with(str(binary), fast_load=True)
        pe.close.assert_called_once_with()

    def test_unreadable_pe_keeps_error_instead_of_hiding_missing_evidence(self):
        module, pe = self._module()
        pe.parse_data_directories.side_effect = ValueError("invalid import table")
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "bad.dll"
            binary.write_bytes(b"bad")
            result = audit.inspect_pe(binary, (6, 1, 7601), pe_module=module)
        self.assertEqual(result["status"], "unreadable")
        self.assertIn("invalid import table", result["error"])
        pe.close.assert_called_once_with()

    def test_no_known_imports_never_confirms_support_and_reads_only_payload_metadata(self):
        module, _ = self._module()
        with tempfile.TemporaryDirectory() as temp:
            payload = Path(temp) / "payload"
            for relative in ("GluSpot.exe", "_internal/python312.dll", "_internal/PySide6/Qt6Core.dll"):
                path = payload / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            metadata = payload / "_internal/zeroconf-0.151.5.dist-info/METADATA"
            metadata.parent.mkdir()
            metadata.write_text("Name: zeroconf\nVersion: 0.151.5\nRequires-Python: >=3.10\n", encoding="utf-8")
            report = audit.build_report(payload, pe_module=module)
        self.assertEqual(report["overall_status"], "unverified")
        self.assertFalse(report["compatibility_confirmed"])
        self.assertTrue(report["audit_complete"])
        self.assertEqual(report["payload_package_metadata"][0]["requires_python"], ">=3.10")
        self.assertEqual(next(item for item in report["files"] if item["component"] == "python")["path"],
                         str(payload / "_internal/python312.dll"))

    def test_missing_payload_is_incomplete_and_cannot_look_compatible(self):
        with tempfile.TemporaryDirectory() as temp:
            report = audit.build_report(Path(temp) / "missing")
        self.assertFalse(report["audit_complete"])
        self.assertFalse(report["payload_exists"])
        self.assertFalse(report["compatibility_confirmed"])

    def test_cli_refuses_to_write_inside_audited_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(SystemExit) as result:
                audit.main(["--payload", temp, "--output", str(Path(temp) / "audit.json")])
        self.assertEqual(result.exception.code, 2)

    def test_cli_writes_report_and_uses_distinct_incomplete_exit(self):
        report = {"audit_complete": False, "overall_status": "unverified"}
        with tempfile.TemporaryDirectory() as temp, patch.object(audit, "build_report", return_value=report):
            output = Path(temp) / "reports/audit.json"
            self.assertEqual(audit.main(["--payload", str(Path(temp) / "payload"), "--output", str(output)]), 2)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)


if __name__ == "__main__":
    unittest.main()
