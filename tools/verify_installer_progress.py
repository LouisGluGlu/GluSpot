"""Render native installer progress offscreen without performing installation.

The isolated executable exits from InitializeWizard, before the native engine
can install files, create shortcuts, write registration, or launch a payload.
Only the real layered compositor is exercised, using its presented PARGB DIB.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], log: Path) -> None:
    result = subprocess.run(command, capture_output=True, timeout=120,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    log.write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"Native progress proof failed ({result.returncode}): {log}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path,
                        default=ROOT / "build/installer-toolchain/compiler/ISCC.exe")
    parser.add_argument("--assets", type=Path,
                        default=ROOT / "build/installer-player-background")
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("Native GDI+ progress proof requires Windows.")
    from PIL import Image

    identity = uuid.uuid4().hex
    fixture = (options.output or ROOT / ".runtime" / f"installer-progress-{identity[:8]}").resolve()
    fixture.mkdir(parents=True, exist_ok=False)
    payload = fixture / "payload"
    (payload / "_internal").mkdir(parents=True)
    (payload / "GluSpot.exe").write_bytes(b"Inert bytes, never executed")
    (payload / "_internal/inert.txt").write_text("Never installed", encoding="utf-8")
    source = (ROOT / "packaging/windows/gluspot-installer.iss").read_text(encoding="utf-8")
    manifest = json.loads((options.assets / "COMPACT.json").read_text(encoding="utf-8"))
    assert manifest["progress_presentation"] == "native-directory-row-capsule-v1"
    assert not list(options.assets.glob("compact-progress-*.png"))
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    source = source.replace("SetupIconFile=..\\..\\assets\\brand\\gluspot-glu.ico",
                            "SetupIconFile=" + str(ROOT / "assets/brand/gluspot-glu.ico"))
    source = source.replace("  InitializeLayeredScene;", "  WizardForm.Position := poDesigned;\n"
                            "  WizardForm.Left := -30000; WizardForm.Top := -30000;\n"
                            "  InitializeLayeredScene;")
    source = source.replace("procedure InitializeWizard;", "procedure ExitProcess(Code: LongWord);\n"
                            "  external 'ExitProcess@kernel32.dll stdcall';\n\n"
                            "procedure InitializeWizard;")
    proof = r"""
  CustomPageID := wpSelectDir;
  LastCompositionTick := 0;
  RenderScene(AnimationStartedTick);
  ExportLayerFrame('preflight', True);
  CustomPageID := wpInstalling;
  WizardForm.ProgressGauge.Max := 100;
  for Index := 0 to 100 do begin
    if (Index = 0) or (Index = 1) or (Index mod 25 = 0) then begin
      WizardForm.ProgressGauge.Position := Index;
      LastCompositionTick := 0;
      RenderScene(AnimationStartedTick + 300);
      ExportLayerFrame('progress-' + IntToStr(Index), True);
    end;
  end;
  CustomPageID := wpFinished;
  CompletionRunning := True;
  CompletionStartedTick := AnimationStartedTick + 600;
  FinishBrandLoopTick := AnimationStartedTick + 5000;
  LastCompositionTick := 0;
  RenderScene(CompletionStartedTick + 300);
  ExportLayerFrame('completion-fade', True);
  LastCompositionTick := 0;
  RenderScene(CompletionStartedTick + 600);
  ExportLayerFrame('completion-clean', True);
  ExitProcess(0);
"""
    needle = "  TimerResolutionHeld := timeBeginPeriod(1) = 0;"
    assert source.count(needle) == 1
    source = source.replace(needle, proof + needle)
    script = fixture / "progress-proof.iss"
    script.write_text(source, encoding="utf-8")
    checks = []
    for dpi in (96, 192):
        directory = fixture / f"dpi-{dpi}"
        directory.mkdir()
        run([str(options.compiler), "/DAppVersion=6.412", f"/DPayloadDir={payload}",
             f"/DArtifactDir={directory}", f"/DInstallerAssets={options.assets}",
             f"/DVisualDiagnosticDir={directory}", f"/DVisualDiagnosticDPI={dpi}",
             f"/DInstallerAppId=GluSpotProgressProof-{identity}-{dpi}",
             f"/DInstallRegistryKey=Software\\GluSpot\\ProgressProof\\{identity}-{dpi}",
             str(script)], directory / "compile.log")
        run([str(directory / "GluSpot-6.412-Setup-x64.exe"), "/SP-", "/NORESTART",
             f"/DIR={directory / 'never-installed'}", f"/LOG={directory / 'native.log'}"],
            directory / "run.log")
        assert not (directory / "never-installed").exists()
        density = dpi // 96
        images = {value: Image.open(directory / f"progress-{value}.png").convert("RGBA")
                  for value in (0, 1, 25, 50, 75, 100)}
        checks.append(f"DPR{density}: exits before installation and never creates target")
        for value, image in images.items():
            assert image.size == (370 * density, 258 * density)
            assert image.getpixel((185 * density, 229 * density))[3] == 255
            green = []
            for y in range(214 * density, 244 * density):
                for x in range(16 * density, 354 * density):
                    red, grn, blue, alpha = image.getpixel((x, y))
                    if abs(red - 85) < 3 and abs(grn - 183) < 3 and abs(blue - 44) < 3 and alpha > 250:
                        green.append(x)
            if value == 0:
                assert not green
            else:
                # The actual width follows the native engine, without an
                # artificial minimum fill or elapsed-time approximation.
                actual = (max(green) + 1) / density - 16
                assert abs(actual - 338 * value / 100) <= 2, (value, actual)
            checks.append(f"DPR{density}: {value}% engine progress stays in 338×30 directory row")
        zero = images[0]
        for image in images.values():
            assert image.crop((0, 0, 370 * density, 72 * density)).tobytes() == zero.crop(
                (0, 0, 370 * density, 72 * density)).tobytes()
        checks.append(f"DPR{density}: progress never changes the black brand island")
        clean = Image.open(directory / "completion-clean.png").convert("RGBA")
        assert clean.getpixel((185 * density, 229 * density)) == (255, 255, 255, 255)
        fade = Image.open(directory / "completion-fade.png").convert("RGBA")
        assert fade.getpixel((100 * density, 218 * density))[1] > images[100].getpixel((100 * density, 218 * density))[1]
        checks.append(f"DPR{density}: completion fades bar to clean white with no rim residue")
        montage = Image.new("RGB", (370 * density * 3, 258 * density * 2), "#d4d4d4")
        for index, image in enumerate(images.values()):
            montage.paste(image, ((index % 3) * 370 * density, (index // 3) * 258 * density), image)
        montage.save(directory / "progress-montage.png")
    evidence = {"source_sha256": source_hash, "assertions": checks,
                "assertion_count": len(checks), "renderer": "actual GDI+ PARGB presented DIB",
                "no_installed_payload": True, "no_physical_input": True,
                "fixture_exits_from": "InitializeWizard before timer/install engine"}
    (fixture / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"fixture": str(fixture), "checks": len(checks)}))


if __name__ == "__main__":
    main()
