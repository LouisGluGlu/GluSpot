"""Exercise the real native installer using inert payloads and isolated registry IDs.

Only generated inert GluSpot-named fixture processes are launched; no user app is launched. Run explicitly on Windows after preparing
the Inno compiler; it creates only a dated, ignored .runtime fixture and removes
the fixture's own install through its generated uninstaller.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import winreg


ROOT = Path(__file__).resolve().parents[1]


def run(command, *, log: Path, expected=0):
    process = subprocess.run([str(value) for value in command], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
    log.write_bytes(process.stdout)
    if (process.returncode == 0) != (expected == 0):
        raise AssertionError(f"Unexpected exit {process.returncode}: {log}")
    return process.returncode


def check_progress_assets(assets: Path) -> dict:
    """Reject retired perimeter rasters and check the native bar's cache contract."""
    manifest = json.loads((assets / "COMPACT.json").read_text(encoding="utf-8"))
    assert manifest.get("progress_presentation") == "native-directory-row-capsule-v1"
    assert manifest.get("progress_rect") == [16, 214, 338, 30]
    assert manifest.get("progress_radius") == 15
    assert manifest.get("progress_source") == "WizardForm.ProgressGauge.Position / Max"
    assert manifest.get("progress_rasters") == 0
    assert manifest.get("perimeter_progress") is False
    assert not list(assets.glob("compact-progress-*.png"))
    return {"progress_frames": 0, "fade_frames": 0,
            "native_directory_row_capsule": True, "progress_rect": [16, 214, 338, 30],
            "actual_extraction_progress": True, "perimeter_progress": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path, default=ROOT / "build/installer-toolchain/compiler/ISCC.exe")
    parser.add_argument("--reuse-assets", action="store_true",
                        help="Use the existing compact native-installer artwork cache")
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("This acceptance test requires Windows.")
    fixture_id = uuid.uuid4().hex
    fixture = ROOT / ".runtime" / f"installer-acceptance-2026-10-16-{fixture_id[:8]}"
    fixture.mkdir(parents=True)
    target = fixture / "installed"
    assets = ROOT / "build/installer-player-background"
    registry_key = rf"Software\GluSpot\InstallerAcceptance\{fixture_id}"
    app_id = f"GluSpotAcceptance-{fixture_id}"
    csc = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not csc.is_file():
        raise RuntimeError("The built-in .NET Framework C# compiler is unavailable.")
    sys.path.insert(0, str(ROOT))
    import importlib.util
    asset_spec = importlib.util.spec_from_file_location("installer_assets", ROOT / "packaging/windows/prepare-installer-assets.py")
    asset_module = importlib.util.module_from_spec(asset_spec)
    asset_spec.loader.exec_module(asset_module)
    if args.reuse_assets:
        manifest = json.loads((assets / "COMPACT.json").read_text(encoding="utf-8"))
        if manifest.get("raster_density") != 2 or not (assets / "compact-brand-299.png").is_file():
            raise RuntimeError("Current twofold-density compact artwork is missing")
    else:
        asset_module.prepare_brand_animation(assets, ROOT)
    progress_evidence = check_progress_assets(assets)
    outputs = {}
    checks = []
    for version in ("6.410", "6.411", "6.412", "6.413", "6.414"):
        payload = fixture / f"payload-{version}"
        internal = payload / "_internal"
        (internal / "assets/brand").mkdir(parents=True)
        (internal / "PySide6").mkdir(parents=True)
        (internal / "assets/brand/gluspot-glu.ico").write_bytes((ROOT / "assets/brand/gluspot-glu.ico").read_bytes())
        (internal / "PySide6/Qt6Core.dll").write_bytes(b"inert acceptance fixture")
        (internal / "payload-version.txt").write_text(version)
        if version == "6.411":
            (internal / "obsolete.dll").write_bytes(b"old dependency must be retired")
        source = payload / "Inert.cs"
        source.write_text('using System.Reflection;\n[assembly: AssemblyProduct("GluSpot")]\n'
                          f'[assembly: AssemblyFileVersion("{version}.0.0")]\n'
                          'class Inert { static void Main() { '
                          'string d=System.AppDomain.CurrentDomain.BaseDirectory; '
                          'System.IO.File.AppendAllText(System.IO.Path.Combine(d,"launch-events.txt"), '
                          f'"{version}|" + System.Diagnostics.Process.GetCurrentProcess().Id + "\\n"); '
                          'if (System.IO.File.Exists(System.IO.Path.Combine(d,"hold-open.fixture"))) '
                          'System.Threading.Thread.Sleep(180000); } }\n', encoding="utf-8")
        run([csc, "/nologo", "/target:winexe", f"/out:{payload / 'GluSpot.exe'}", source],
            log=fixture / f"csc-{version}.log")
        installer_source = ROOT / "packaging/windows/gluspot-installer.iss"
        if version == "6.413":
            # Instrument only a temporary copy, leaving the distributable
            # script without test-only abort paths or command-line switches.
            modified = installer_source.read_text(encoding="utf-8")
            trigger = "procedure CurStepChanged(CurStep: TSetupStep);\nbegin\n"
            assert modified.count(trigger) == 1
            modified = modified.replace(trigger, trigger +
                "  if CurStep = ssInstall then RaiseException('Isolated acceptance extraction failure');\n")
            modified = modified.replace("SetupIconFile=..\\..\\assets\\brand\\gluspot-glu.ico",
                                        "SetupIconFile=" + str(ROOT / "assets/brand/gluspot-glu.ico"))
            installer_source = fixture / "rollback-fixture.iss"
            installer_source.write_text(modified, encoding="utf-8")
        run([args.compiler, f"/DAppVersion={version}", f"/DPayloadDir={payload}",
             f"/DArtifactDir={fixture}", f"/DInstallerAssets={assets}",
             f"/DInstallerAppId={app_id}", f"/DInstallRegistryKey={registry_key}",
             f"/DInstallGroupName={app_id}",
             installer_source], log=fixture / f"compile-{version}.log")
        outputs[version] = fixture / f"GluSpot-{version}-Setup-x64.exe"
    options = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/NOICONS", f"/DIR={target}"]
    run([outputs["6.411"], *options, f"/LOG={fixture / 'install-native.log'}"], log=fixture / "install-run.log")
    assert (target / "GluSpot.exe").is_file()
    assert (target / "unins000.exe").is_file()
    checks.append("fresh-install-and-system-uninstaller")
    state = target / "GluSpot"
    (state / "accounts").mkdir()
    settings = b'{"v2_playback_onboarding_completed":true,"fixture":"preserve"}'
    (state / "settings.json").write_bytes(settings)
    (state / "accounts/fixture.dpapi").write_bytes(b"inert encrypted-state fixture")
    other_target = fixture / "unexpected-relocation"
    redirected_options = [value for value in options if not value.startswith("/DIR=")]
    run([outputs["6.412"], *redirected_options, f"/DIR={other_target}",
         f"/LOG={fixture / 'relocation-native.log'}"], log=fixture / "relocation.log", expected=1)
    assert not (other_target / "GluSpot.exe").exists()
    assert (state / "settings.json").read_bytes() == settings
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_key) as key:
        assert Path(winreg.QueryValueEx(key, "Path")[0]) == target
    checks.append("registered-upgrade-requires-original-directory-with-state-retained")
    for version in ("6.411", "6.410"):
        run([outputs[version], *options, f"/LOG={fixture / ('reject-' + version + '-native.log')}"],
            log=fixture / f"reject-{version}.log", expected=1)
        assert (state / "settings.json").read_bytes() == settings
        assert (target / "_internal/obsolete.dll").exists()
    checks.append("silent-same-and-lower-version-rejected-with-state-preserved")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                   ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.TerminateProcess.restype = ctypes.c_bool
    handle = kernel.CreateFileW(str(target / "GluSpot.exe"), 0x80000000, 0, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        run([outputs["6.412"], *options, f"/LOG={fixture / 'busy-native.log'}"],
            log=fixture / "busy.log", expected=1)
        assert (target / "_internal/obsolete.dll").exists()
        assert not (target / ".gluspot-upgrade-backup").exists()
        run([target / "unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
             f"/LOG={fixture / 'busy-uninstall-native.log'}"], log=fixture / "busy-uninstall.log", expected=1)
        assert (state / "settings.json").read_bytes() == settings
    finally:
        kernel.CloseHandle(handle)
    checks.append("busy-program-rejects-upgrade-and-uninstall-before-mutation")
    # Updating a running installed app is explicitly authorized. A same-named
    # process elsewhere must stay alive; compare exact canonical executable
    # paths with a held process handle, not names or PID-only stop commands.
    import shutil
    unrelated_process_dir = fixture / "unrelated-process"
    unrelated_process_dir.mkdir()
    shutil.copy2(target / "GluSpot.exe", unrelated_process_dir / "GluSpot.exe")
    (unrelated_process_dir / "hold-open.fixture").touch()
    (target / "hold-open.fixture").touch()
    target_process = subprocess.Popen([str(target / "GluSpot.exe")], creationflags=subprocess.CREATE_NO_WINDOW)
    other_process = subprocess.Popen([str(unrelated_process_dir / "GluSpot.exe")], creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 10
    while (not (target / "launch-events.txt").exists() or not (unrelated_process_dir / "launch-events.txt").exists()) and time.monotonic() < deadline:
        time.sleep(.05)
    assert target_process.poll() is None and other_process.poll() is None
    try:
        run([outputs["6.412"], *options, f"/LOG={fixture / 'upgrade-native.log'}"], log=fixture / "upgrade-run.log")
        target_process.wait(timeout=10)
        assert other_process.poll() is None
        deadline = time.monotonic() + 10
        while "6.412|" not in (target / "launch-events.txt").read_text() and time.monotonic() < deadline:
            time.sleep(.05)
        launches = (target / "launch-events.txt").read_text().splitlines()
        assert len(launches) == 2 and launches[0].startswith("6.411|") and launches[1].startswith("6.412|")
        restarted_pid = int(launches[1].split("|")[1])
        restarted = kernel.OpenProcess(0x100001, False, restarted_pid)
        assert restarted
        try:
            assert kernel.WaitForSingleObject(restarted, 0) == 0x102
            kernel.TerminateProcess(restarted, 0)
            assert kernel.WaitForSingleObject(restarted, 5000) == 0
        finally:
            kernel.CloseHandle(restarted)
    finally:
        (target / "hold-open.fixture").unlink(missing_ok=True)
        if target_process.poll() is None:
            target_process.terminate()
            target_process.wait(timeout=5)
        other_process.terminate()
        other_process.wait(timeout=5)
    checks.append("running-target-stopped-upgraded-and-restarted-once-unrelated-same-name-preserved")
    assert (state / "settings.json").read_bytes() == settings
    assert (state / "accounts/fixture.dpapi").read_bytes() == b"inert encrypted-state fixture"
    assert (target / "_internal/payload-version.txt").read_text() == "6.412"
    assert not (target / "_internal/obsolete.dll").exists()
    assert not (target / ".gluspot-upgrade-backup").exists()
    checks.append("higher-version-upgrades-preserves-state-and-retires-old-dependencies")
    before_failure = hashlib.sha256((target / "GluSpot.exe").read_bytes()).hexdigest()
    run([outputs["6.413"], *options, f"/LOG={fixture / 'rollback-native.log'}"],
        log=fixture / "rollback-run.log", expected=1)
    assert hashlib.sha256((target / "GluSpot.exe").read_bytes()).hexdigest() == before_failure
    assert (target / "_internal/payload-version.txt").read_text() == "6.412"
    assert (state / "settings.json").read_bytes() == settings
    assert not (target / ".gluspot-upgrade-backup").exists()
    checks.append("interrupted-install-restores-original-executable-and-dependencies-with-state-intact")
    # The owned marker keeps the previous version authoritative even when a
    # damaged installation has lost its EXE. A higher build can repair the
    # original directory, but equal versions still require interactive consent.
    (target / "GluSpot.exe").unlink()
    (target / "_internal/obsolete-repair.dll").write_bytes(b"must be retired by repair")
    run([outputs["6.412"], *options, f"/LOG={fixture / 'repair-same-native.log'}"],
        log=fixture / "repair-same.log", expected=1)
    assert not (target / "GluSpot.exe").exists()
    assert (state / "settings.json").read_bytes() == settings
    run([outputs["6.414"], *options, f"/LOG={fixture / 'repair-native.log'}"],
        log=fixture / "repair.log")
    assert (target / "GluSpot.exe").is_file()
    assert (target / "_internal/payload-version.txt").read_text() == "6.414"
    assert not (target / "_internal/obsolete-repair.dll").exists()
    assert (state / "settings.json").read_bytes() == settings
    assert (state / "accounts/fixture.dpapi").read_bytes() == b"inert encrypted-state fixture"
    assert not (target / ".gluspot-upgrade-backup").exists()
    checks.append("missing-executable-repair-retains-state-and-rejects-unapproved-same-version")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_key) as key:
        assert Path(winreg.QueryValueEx(key, "Path")[0]) == target
    checks.append("isolated-install-discovery")
    outside = fixture / "outside-sentinel.txt"
    outside.write_bytes(b"must remain")
    unrelated = target / "user-document.txt"
    unrelated.write_bytes(b"not a GluSpot-owned file")
    run([target / "unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
         f"/LOG={fixture / 'uninstall-native.log'}"], log=fixture / "uninstall-run.log")
    deadline = time.monotonic() + 15
    while ((target / "GluSpot.exe").exists() or state.exists() or (target / "_internal").exists()) and time.monotonic() < deadline:
        time.sleep(.1)
    assert not (target / "GluSpot.exe").exists()
    assert not state.exists()
    assert not (target / "_internal").exists()
    assert unrelated.read_bytes() == b"not a GluSpot-owned file"
    assert outside.read_bytes() == b"must remain"
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_key).Close()
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Isolated installation registry key survived uninstall")
    checks.append("full-owned-state-uninstall-preserves-unrelated-and-outside-files")
    report = {"fixture": str(fixture), "checks": checks,
              "registry_key": registry_key, "application_launched": "generated inert fixtures only",
              "progress_assets": progress_evidence,
              "installer_sha256": hashlib.sha256(outputs["6.412"].read_bytes()).hexdigest()}
    (fixture / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
