"""Prove installer font recovery with the real native GDI+ compositor.

Each generated, isolated installer exits from InitializeWizard before the
installation engine, timers, registration, shortcuts or payload launch. Missing
font/style statuses are injected into temporary source copies only; successful
calls still use Windows GDI+ and the production fallback/drawing code. Run this
serially with other native installer/Qt verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("normal", "ui-missing", "all-named-missing", "bold-missing", "partial-font-failure")


def run(command: list[str], log: Path) -> None:
    result = subprocess.run(command, capture_output=True, timeout=120,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    log.write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"Native font proof failed ({result.returncode}): {log}")


WRAPPERS = r"""
function GdipCreateFontFamilyFromName(Name: String; Collection: NativeUInt; var Family: NativeUInt): Integer;
begin
  FontTestNamedAttempts := FontTestNamedAttempts + 1;
  if (FontTestScenario = 'all-named-missing') or
     ((FontTestScenario = 'ui-missing') and (Name = 'Microsoft YaHei UI')) then begin
    Family := 0;
    FontTestForcedMissing := FontTestForcedMissing + 1;
    Result := 14;
    exit;
  end;
  Result := NativeGdipCreateFontFamilyFromName(Name, Collection, Family);
  if (Result = 0) and (Family <> 0) then begin
    FontTestFamilies := FontTestFamilies + 1;
    if FontTestFirstFamily = 0 then FontTestFirstFamily := Family;
  end;
end;

function GdipGetGenericFontFamilySansSerif(var Family: NativeUInt): Integer;
begin
  FontTestGenericAttempts := FontTestGenericAttempts + 1;
  Result := NativeGdipGetGenericFontFamilySansSerif(Family);
  if (Result = 0) and (Family <> 0) then FontTestFamilies := FontTestFamilies + 1;
end;

function GdipDeleteFontFamily(Family: NativeUInt): Integer;
begin
  Result := NativeGdipDeleteFontFamily(Family);
  if Result = 0 then FontTestFamilies := FontTestFamilies - 1;
end;

function GdipCreateFont(Family: NativeUInt; Size: Single; Style, UnitKind: Integer; var Font: NativeUInt): Integer;
begin
  if (Style = 1) and (FontTestScenario = 'bold-missing') then begin
    Font := 0;
    FontTestForcedBold := FontTestForcedBold + 1;
    Result := 15;
    exit;
  end;
  if (Style = 1) and (FontTestScenario = 'partial-font-failure') and
     (Family = FontTestFirstFamily) and (FontTestForcedPartial = 0) then begin
    Font := 0;
    FontTestForcedPartial := FontTestForcedPartial + 1;
    Result := 1;
    exit;
  end;
  Result := NativeGdipCreateFont(Family, Size, Style, UnitKind, Font);
  if (Result = 0) and (Font <> 0) then FontTestFonts := FontTestFonts + 1;
end;

function GdipDeleteFont(Font: NativeUInt): Integer;
begin
  Result := NativeGdipDeleteFont(Font);
  if Result = 0 then FontTestFonts := FontTestFonts - 1;
end;

function GdipDrawString(Graphics: NativeUInt; Text: String; Length: Integer; Font: NativeUInt;
  var Layout: TFloatRect; Format, Brush: NativeUInt): Integer;
begin
  Result := NativeGdipDrawString(Graphics, Text, Length, Font, Layout, Format, Brush);
  if Result = 0 then begin
    FontTestDraws := FontTestDraws + 1;
    if Pos('安装', Text) > 0 then FontTestChineseDraws := FontTestChineseDraws + 1;
  end;
end;
"""


PROOF = r"""
  CustomPageID := wpSelectDir;
  LastCompositionTick := 0;
  RenderScene(AnimationStartedTick);
  ExportLayerFrame('font-preflight', True);
  CustomPageID := wpInstalling;
  WizardForm.ProgressGauge.Max := 100;
  WizardForm.ProgressGauge.Position := 50;
  LastCompositionTick := 0;
  RenderScene(AnimationStartedTick + 300);
  ExportLayerFrame('font-progress', True);
  if (FontFamily = 0) or (DirectoryFont = 0) or (PrimaryFont = 0) then
    RaiseException('Font proof: committed font handles are invalid');
  if (FontTestFamilies <> 1) or (FontTestFonts <> 2) then
    RaiseException('Font proof: failed candidate leaked native font resources');
  if (FontTestDraws < 6) or (FontTestChineseDraws < 3) then
    RaiseException('Font proof: real GDI+ label drawing was not exercised');
  if (FontTestScenario = 'ui-missing') and
     ((FontTestForcedMissing <> 1) or (SceneFontName = 'Microsoft YaHei UI')) then
    RaiseException('Font proof: missing Win8 UI font did not recover');
  if (FontTestScenario = 'all-named-missing') and
     ((FontTestForcedMissing <> 5) or (FontTestGenericAttempts <> 1) or (SceneFontName <> '')) then
    RaiseException('Font proof: generic sans serif fallback was not reached');
  if (FontTestScenario = 'bold-missing') and (FontTestForcedBold < 1) then
    RaiseException('Font proof: bold style failure was not injected');
  if (FontTestScenario = 'partial-font-failure') and (FontTestForcedPartial < 1) then
    RaiseException('Font proof: partial candidate failure was not injected');
  GdiCheck(GdipDeleteFont(PrimaryFont), 'font proof release primary'); PrimaryFont := 0;
  GdiCheck(GdipDeleteFont(DirectoryFont), 'font proof release directory'); DirectoryFont := 0;
  GdiCheck(GdipDeleteFontFamily(FontFamily), 'font proof release family'); FontFamily := 0;
  if (FontTestFamilies <> 0) or (FontTestFonts <> 0) then
    RaiseException('Font proof: font resources did not balance after release');
  SaveStringToFile('{#VisualDiagnosticDir}\font-proof.txt',
    'scenario=' + FontTestScenario + #13#10 +
    'selected=' + SceneFontName + #13#10 +
    'draw_calls=' + IntToStr(FontTestDraws) + #13#10 +
    'chinese_draw_calls=' + IntToStr(FontTestChineseDraws) + #13#10 +
    'named_attempts=' + IntToStr(FontTestNamedAttempts) + #13#10 +
    'generic_attempts=' + IntToStr(FontTestGenericAttempts) + #13#10 +
    'forced_missing=' + IntToStr(FontTestForcedMissing) + #13#10 +
    'forced_bold=' + IntToStr(FontTestForcedBold) + #13#10 +
    'forced_partial=' + IntToStr(FontTestForcedPartial) + #13#10 +
    'remaining_fonts=' + IntToStr(FontTestFonts) + #13#10 +
    'remaining_families=' + IntToStr(FontTestFamilies), False);
  ExitProcess(0);
"""


def instrument(source: str, scenario: str) -> str:
    """Keep fault injection and accounting outside the distributable script."""
    assert scenario in SCENARIOS
    for name in ("GdipCreateFontFamilyFromName", "GdipGetGenericFontFamilySansSerif",
                 "GdipDeleteFontFamily", "GdipCreateFont", "GdipDeleteFont", "GdipDrawString"):
        needle = f"function {name}("
        assert source.count(needle) == 1, name
        source = source.replace(needle, f"function Native{name}(", 1)
    needle = "  FileAttributeReparsePoint = $400;"
    assert source.count(needle) == 1
    source = source.replace(needle, f"  FontTestScenario = '{scenario}';\n" + needle)
    needle = "  PaletteTimer: UINT_PTR;"
    assert source.count(needle) == 1
    source = source.replace(needle, "  FontTestFamilies, FontTestFonts, FontTestDraws, FontTestChineseDraws: Integer;\n"
                            "  FontTestNamedAttempts, FontTestGenericAttempts: Integer;\n"
                            "  FontTestForcedMissing, FontTestForcedBold, FontTestForcedPartial: Integer;\n"
                            "  FontTestFirstFamily: NativeUInt;\n" + needle)
    needle = "procedure GdiCheck(Status: Integer; Operation: String);"
    assert source.count(needle) == 1
    source = source.replace(needle, WRAPPERS + "\n" + needle)
    needle = "  InitializeLayeredScene;"
    assert source.count(needle) == 1
    source = source.replace(needle, "  WizardForm.Position := poDesigned;\n"
                            "  WizardForm.Left := -30000; WizardForm.Top := -30000;\n" + needle)
    needle = "procedure InitializeWizard;"
    assert source.count(needle) == 1
    source = source.replace(needle, "procedure ExitProcess(Code: LongWord);\n"
                            "  external 'ExitProcess@kernel32.dll stdcall';\n\n" + needle)
    needle = "  TimerResolutionHeld := timeBeginPeriod(1) = 0;"
    assert source.count(needle) == 1
    source = source.replace(needle, PROOF + needle)
    return source.replace("SetupIconFile=..\\..\\assets\\brand\\gluspot-glu.ico",
                          "SetupIconFile=" + str(ROOT / "assets/brand/gluspot-glu.ico"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path,
                        default=ROOT / "build/installer-toolchain/compiler/ISCC.exe")
    parser.add_argument("--assets", type=Path,
                        default=ROOT / "build/installer-player-background")
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("Native GDI+ font proof requires Windows.")
    from PIL import Image

    identity = uuid.uuid4().hex
    fixture = (options.output or ROOT / ".runtime" / f"installer-fonts-{identity[:8]}").resolve()
    fixture.mkdir(parents=True, exist_ok=False)
    payload = fixture / "payload"
    (payload / "_internal").mkdir(parents=True)
    (payload / "GluSpot.exe").write_bytes(b"Inert font proof bytes, never executed")
    (payload / "_internal/inert.txt").write_text("Never installed", encoding="utf-8")
    source = (ROOT / "packaging/windows/gluspot-installer.iss").read_text(encoding="utf-8")
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    version = re.search(r'APP_VERSION = "([0-9.]+)"',
                        (ROOT / "local/branding.py").read_text(encoding="utf-8")).group(1)
    checks: list[str] = []
    results: list[dict] = []
    for scenario in SCENARIOS:
        script = fixture / f"font-{scenario}.iss"
        script.write_text(instrument(source, scenario), encoding="utf-8")
        for dpi in (96, 192):
            directory = fixture / f"{scenario}-dpi-{dpi}"
            directory.mkdir()
            density = dpi // 96
            run([str(options.compiler), f"/DAppVersion={version}", f"/DPayloadDir={payload}",
                 f"/DArtifactDir={directory}", f"/DInstallerAssets={options.assets}",
                 f"/DVisualDiagnosticDir={directory}", f"/DVisualDiagnosticDPI={dpi}",
                 f"/DInstallerAppId=GluSpotFontProof-{identity}-{scenario}-{dpi}",
                 f"/DInstallRegistryKey=Software\\GluSpot\\FontProof\\{identity}-{scenario}-{dpi}",
                 str(script)], directory / "compile.log")
            run([str(directory / f"GluSpot-{version}-Setup-x64.exe"), "/SP-", "/NORESTART",
                 f"/DIR={directory / 'never-installed'}", f"/LOG={directory / 'native.log'}"],
                directory / "run.log")
            assert not (directory / "never-installed").exists()
            proof = dict(line.split("=", 1) for line in
                         (directory / "font-proof.txt").read_text(encoding="utf-8-sig").splitlines())
            assert proof["scenario"] == scenario
            assert proof["remaining_fonts"] == proof["remaining_families"] == "0"
            log = (directory / "native.log").read_text(encoding="utf-8-sig")
            assert "Installer UI font selected:" in log
            if scenario == "ui-missing":
                assert "Installer UI font unavailable: Microsoft YaHei UI (14)" in log
                assert proof["selected"] != "Microsoft YaHei UI"
            elif scenario == "all-named-missing":
                assert "Installer UI font selected: generic sans serif" in log
                assert proof["selected"] == ""
            elif scenario == "bold-missing":
                assert "Installer UI bold unavailable; using regular:" in log
            elif scenario == "partial-font-failure":
                assert "Installer UI font unavailable:" in log
            images = []
            for name in ("font-preflight", "font-progress"):
                image = Image.open(directory / f"{name}.png").convert("RGBA")
                assert image.size == (370 * density, 258 * density)
                assert image.getpixel((185 * density, 229 * density))[3] == 255
                images.append(image)
            # Real Chinese installation labels and progress text must produce
            # ink in their production bounds, beyond successful API return codes.
            preflight_ink = sum(1 for red, green, blue, alpha in images[0].crop(
                (25 * density, 215 * density, 182 * density, 230 * density)).getdata()
                if alpha > 240 and max(red, green, blue) < 200)
            progress_ink = sum(1 for red, green, blue, alpha in images[1].crop(
                (30 * density, 220 * density, 250 * density, 240 * density)).getdata()
                if alpha > 240 and max(red, green, blue) < 100)
            assert preflight_ink > 20 * density, (scenario, dpi, preflight_ink)
            assert progress_ink > 20 * density, (scenario, dpi, progress_ink)
            checks.extend([f"{scenario} DPR{density}: exits before install and leaves target absent",
                           f"{scenario} DPR{density}: real GDI+ Chinese/progress label ink is visible",
                           f"{scenario} DPR{density}: font/font-family handles balance after recovery"])
            montage = Image.new("RGB", (740 * density, 258 * density), "#d4d4d4")
            for index, image in enumerate(images):
                montage.paste(image, (370 * density * index, 0), image)
            montage.save(directory / "font-montage.png")
            results.append({"scenario": scenario, "dpi": dpi, "proof": proof,
                            "preflight_ink": preflight_ink, "progress_ink": progress_ink})
    evidence = {"source_sha256": source_hash, "assertions": checks,
                "assertion_count": len(checks), "results": results,
                "renderer": "production GDI+ PARGB presented DIB",
                "fault_injection": "temporary source wrappers only; no production test hooks",
                "no_installed_payload": True, "no_physical_input": True,
                "fixture_exits_from": "InitializeWizard before timer/install engine",
                "actual_windows_7_execution": False}
    (fixture / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"fixture": str(fixture), "checks": len(checks), "scenarios": len(SCENARIOS)}))


if __name__ == "__main__":
    main()
