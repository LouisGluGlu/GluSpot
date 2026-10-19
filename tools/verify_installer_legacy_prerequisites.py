"""Exercise the Win7 installer prerequisite guard in isolated native setups.

Temporary scripts call the production guard from InitializeSetup, write their
proof, and exit before the wizard or installation engine. The payload is inert;
no real GluSpot process, install directory, or user registration is touched.
Missing updates are injected only through wrappers in those temporary copies.
Run serially with other native installer/Qt verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("actual-host", "missing-add-dll-directory", "missing-ucrt")


WRAPPERS = r"""
var
  LegacyTestProcCalls, LegacyTestUcrtCalls: Integer;
  LegacyTestNativeProcFound, LegacyTestNativeUcrtFound: Boolean;

function LegacyGetProcAddress(Module: THandle; Name: AnsiString): NativeUInt;
begin
  LegacyTestProcCalls := LegacyTestProcCalls + 1;
  Result := NativeLegacyGetProcAddress(Module, Name);
  LegacyTestNativeProcFound := Result <> 0;
  if LegacyTestScenario = 'missing-add-dll-directory' then Result := 0;
end;

function LegacyTestUcrtExists(Name: String): Boolean;
begin
  LegacyTestUcrtCalls := LegacyTestUcrtCalls + 1;
  Result := FileExists(Name);
  LegacyTestNativeUcrtFound := Result;
  if LegacyTestScenario = 'missing-ucrt' then Result := False;
end;

"""


PROOF = r"""
procedure LegacyTestExitProcess(Code: LongWord);
  external 'ExitProcess@kernel32.dll stdcall';

function InitializeSetup: Boolean;
var
  ErrorText, ErrorKind: String;
begin
  Result := False;
  ErrorText := LegacyRuntimePrerequisiteError;
  ErrorKind := 'unexpected';
  if ErrorText = '' then ErrorKind := 'none'
  else if Pos('KB2533623', ErrorText) > 0 then ErrorKind := 'KB2533623'
  else if Pos('KB2999226', ErrorText) > 0 then ErrorKind := 'KB2999226';
  if not SaveStringToFile('{#LegacyTestProofDir}\prerequisite-proof.txt',
    'scenario=' + LegacyTestScenario + #13#10 +
    'error_kind=' + ErrorKind + #13#10 +
    'proc_calls=' + IntToStr(LegacyTestProcCalls) + #13#10 +
    'ucrt_calls=' + IntToStr(LegacyTestUcrtCalls) + #13#10 +
    'native_proc_found=' + IntToStr(Ord(LegacyTestNativeProcFound)) + #13#10 +
    'native_ucrt_found=' + IntToStr(Ord(LegacyTestNativeUcrtFound)) + #13#10 +
    'guard_returned=1' + #13#10 +
    'exit_from=InitializeSetup', False) then LegacyTestExitProcess(11);
  LegacyTestExitProcess(0);
end;
"""


def instrument(source: str, scenario: str) -> str:
    """Preserve the production guard body apart from its UCRT read wrapper."""
    if scenario not in SCENARIOS:
        raise ValueError("Unknown prerequisite scenario: " + scenario)
    if re.search(r"function\s+InitializeSetup\s*[:(]", source, re.IGNORECASE):
        raise RuntimeError("Review instrumentation: production now owns InitializeSetup")
    declaration = "function LegacyGetProcAddress(Module: THandle; Name: AnsiString): NativeUInt;"
    if source.count(declaration) != 1:
        raise RuntimeError("Production GetProcAddress declaration changed")
    source = source.replace(declaration, declaration.replace("LegacyGetProcAddress", "NativeLegacyGetProcAddress"), 1)
    guard = "function LegacyRuntimePrerequisiteError: String;"
    if source.count(guard) != 1:
        raise RuntimeError("Production prerequisite guard changed")
    source = source.replace(guard, "const\n  LegacyTestScenario = '" + scenario + "';\n" + WRAPPERS + guard, 1)
    ucrt_read = "FileExists(ExpandConstant('{sys}\\ucrtbase.dll'))"
    if source.count(ucrt_read) != 1:
        raise RuntimeError("Production UCRT existence check changed")
    source = source.replace(ucrt_read, "LegacyTestUcrtExists(ExpandConstant('{sys}\\ucrtbase.dll'))", 1)
    icon = "SetupIconFile=..\\..\\assets\\brand\\gluspot-glu.ico"
    if source.count(icon) != 1:
        raise RuntimeError("Production installer icon reference changed")
    source = source.replace(icon, "SetupIconFile=" + str(ROOT / "assets/brand/gluspot-glu.ico"), 1)
    return source + "\n" + PROOF


def run(command: list[str], log: Path) -> None:
    result = subprocess.run(command, capture_output=True, timeout=120,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    log.write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"Native prerequisite proof failed ({result.returncode}): {log}")


def registry_exists(path: str) -> bool:
    """Read both views of an isolated key; never create or delete any key."""
    import winreg

    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | view):
                return True
        except FileNotFoundError:
            pass
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path,
                        default=ROOT / "build/installer-toolchain/compiler/ISCC.exe")
    parser.add_argument("--assets", type=Path, default=ROOT / "build/installer-player-background")
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("Native installer prerequisite verification requires Windows")
    identity = uuid.uuid4().hex
    fixture = (options.output or ROOT / ".runtime" / f"installer-legacy-prerequisites-{identity[:8]}").resolve()
    fixture.mkdir(parents=True, exist_ok=False)
    payload = fixture / "inert-payload"
    (payload / "_internal").mkdir(parents=True)
    (payload / "GluSpot.exe").write_bytes(b"Inert prerequisite proof; never installed or executed")
    (payload / "_internal/inert.txt").write_text("Never installed", encoding="utf-8")
    source_path = ROOT / "packaging/windows/gluspot-installer.iss"
    source = source_path.read_text(encoding="utf-8")
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    version_match = re.search(r'APP_VERSION = "([0-9.]+)"',
                              (ROOT / "local/branding.py").read_text(encoding="utf-8"))
    if not version_match:
        raise RuntimeError("Production version could not be read")
    version = version_match.group(1)
    checks = []
    results = []
    for scenario in SCENARIOS:
        directory = fixture / scenario
        directory.mkdir()
        script = directory / "prerequisite-proof.iss"
        instrumented = instrument(source, scenario)
        script.write_text(instrumented, encoding="utf-8")
        app_id = "GluSpotLegacyPrerequisiteProof-" + identity + "-" + scenario
        registry_key = "Software\\GluSpot\\LegacyPrerequisiteProof\\" + identity + "-" + scenario
        uninstall_key = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + app_id + "_is1"
        target = directory / "never-installed"
        assert not target.exists()
        assert not registry_exists(registry_key) and not registry_exists(uninstall_key)
        run([str(options.compiler), f"/DAppVersion={version}", f"/DPayloadDir={payload}",
             f"/DArtifactDir={directory}", f"/DInstallerAssets={options.assets.resolve()}",
             "/DLegacyWindows7=1", "/DPayloadMinVersion=6.1sp1",
             f"/DInstallerAppId={app_id}", f"/DInstallRegistryKey={registry_key}",
             f"/DInstallGroupName={app_id}", f"/DLegacyTestProofDir={directory}",
             str(script)], directory / "compile.log")
        run([str(directory / f"GluSpot-{version}-Setup-x64.exe"), "/SP-", "/NORESTART",
             f"/DIR={target}", f"/LOG={directory / 'native.log'}"], directory / "run.log")
        proof = dict(line.split("=", 1) for line in
                     (directory / "prerequisite-proof.txt").read_text(encoding="utf-8-sig").splitlines())
        assert proof["scenario"] == scenario
        assert proof["guard_returned"] == "1" and proof["exit_from"] == "InitializeSetup"
        assert proof["proc_calls"] == "1" and proof["native_proc_found"] == "1"
        if scenario == "missing-add-dll-directory":
            assert proof["error_kind"] == "KB2533623" and proof["ucrt_calls"] == "0"
        else:
            assert proof["ucrt_calls"] == "1" and proof["native_ucrt_found"] == "1"
            assert proof["error_kind"] == ("none" if scenario == "actual-host" else "KB2999226")
        assert not target.exists()
        assert not registry_exists(registry_key) and not registry_exists(uninstall_key)
        native_log = (directory / "native.log").read_text(encoding="utf-8-sig")
        assert "Starting the installation process" not in native_log
        assert "Installation process succeeded" not in native_log
        checks.extend([f"{scenario}: production guard returned expected result",
                       f"{scenario}: actual host API/file evidence confirmed",
                       f"{scenario}: exited from InitializeSetup before installation",
                       f"{scenario}: target and isolated registry keys remain absent"])
        results.append({"scenario": scenario, "proof": proof,
                        "instrumented_source_sha256": hashlib.sha256(instrumented.encode("utf-8")).hexdigest(),
                        "app_id": app_id, "registry_key": registry_key})
    assert hashlib.sha256(source_path.read_text(encoding="utf-8").encode("utf-8")).hexdigest() == source_hash
    checks.append("production ISS source remains unchanged")
    evidence = {"source_sha256": source_hash,
                "source_hash_normalization": "Path.read_text UTF-8 universal newlines, then UTF-8 encode",
                "assertion_count": len(checks), "assertions": checks, "results": results,
                "host": platform.platform(), "fixture_exits_from": "InitializeSetup",
                "legacy_windows_7_define": True, "payload_min_version": "6.1sp1",
                "fault_injection": "temporary source API/file wrappers only; production guard retained",
                "no_installed_payload": True, "no_registry_write": True,
                "no_real_application_process": True, "actual_windows_7_execution": False}
    (fixture / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"fixture": str(fixture), "checks": len(checks), "scenarios": len(SCENARIOS)}))


if __name__ == "__main__":
    main()
