"""Exercise installer completion against a separate inert WinForms process.

No installation engine, real GluSpot process, registry write, or global input
is used. The native wizard and fixture Form remain outside all screen bounds.
Only a temporary ISS copy is instrumented; production code is read unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

from verify_installer_progress import run


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_SOURCE = r'''
using System;
using System.IO;
using System.Drawing;
using System.Threading;
using System.Windows.Forms;
class Fixture : Form {
    static string Root = AppDomain.CurrentDomain.BaseDirectory;
    static EventWaitHandle Painted;
    static System.Threading.Timer Watchdog;
    static bool Acknowledge;
    bool first = true;
    static void Record(string name) {
        File.AppendAllText(Path.Combine(Root, "payload.tsv"), name + "\t" +
            unchecked((uint)Environment.TickCount).ToString() + "\n");
    }
    protected override void OnPaint(PaintEventArgs e) {
        base.OnPaint(e);
        e.Graphics.Clear(Color.Black);
        if (first) {
            first = false;
            Record("paint-return");
            BeginInvoke((MethodInvoker)delegate {
                // A delayed receipt proves the installer retains its surface
                // even after reveal until a completed paint is acknowledged.
                var receipt = new System.Windows.Forms.Timer();
                receipt.Interval = 180;
                receipt.Tick += delegate {
                    receipt.Stop();
                    if (Acknowledge) { Record("painted-signal"); Painted.Set(); }
                    var quit = new System.Windows.Forms.Timer();
                    quit.Interval = 250;
                    quit.Tick += delegate { quit.Stop(); Close(); };
                    quit.Start();
                };
                receipt.Start();
            });
        }
    }
    [STAThread] static void Main(string[] args) {
        try {
            Record("payload-start");
            Watchdog = new System.Threading.Timer(delegate { Record("fixture-watchdog"); Environment.Exit(9); }, null, 15000, Timeout.Infinite);
            if (args.Length != 7 || args[0] != "--installer-handoff" || args[5] != "--installer-sync")
                throw new Exception("Unexpected native launch interface");
            string token = args[6];
            string prefix = @"Local\GluSpot.InstallerHandoff." + token + ".";
            using (var ready = EventWaitHandle.OpenExisting(prefix + "ready"))
            using (var reveal = EventWaitHandle.OpenExisting(prefix + "reveal"))
            using (Painted = EventWaitHandle.OpenExisting(prefix + "painted")) {
                string[] scenario = File.ReadAllLines(Path.Combine(Root, "scenario.txt"));
                Acknowledge = scenario[1] == "yes";
                var window = new Fixture();
                window.FormBorderStyle = FormBorderStyle.None;
                window.StartPosition = FormStartPosition.Manual;
                window.Location = new Point(-30000, -31000);
                window.Size = new Size(370, 64);
                window.ShowInTaskbar = false;
                if (window.Visible || window.IsHandleCreated) throw new Exception("Premature native window");
                Thread.Sleep(Int32.Parse(scenario[0]));
                Record("hidden-before-ready");
                ready.Set(); Record("ready-signal");
                if (!reveal.WaitOne(15000)) throw new Exception("Reveal gate timeout");
                Record("reveal-observed");
                if (window.Visible || window.IsHandleCreated) throw new Exception("Premature native window");
                Record("show-after-reveal");
                window.Shown += delegate {
                    window.BeginInvoke((MethodInvoker)delegate {
                        using (var bitmap = new Bitmap(window.Width, window.Height))
                            window.DrawToBitmap(bitmap, new Rectangle(0, 0, bitmap.Width, bitmap.Height));
                    });
                };
                Application.Run(window);
            }
            Record("payload-exit");
            Watchdog.Dispose();
        } catch (Exception error) { Record("error:" + error.ToString().Replace('\n',' ')); }
    }
}
'''


def timeline(path: Path) -> list[dict]:
    return [{"event": parts[0], "tick": int(parts[1]), "detail": parts[2:]}
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if len(parts := line.split("\t")) >= 2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path,
                        default=ROOT / "build/installer-toolchain/compiler/ISCC.exe")
    parser.add_argument("--assets", type=Path, default=ROOT / "build/installer-player-background")
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("Cross-process native event verification requires Windows.")
    identity = uuid.uuid4().hex
    fixture = (options.output or ROOT / ".runtime" / f"installer-handoff-{identity[:8]}").resolve()
    fixture.mkdir(parents=True, exist_ok=False)
    original = (ROOT / "packaging/windows/gluspot-installer.iss").read_text(encoding="utf-8")
    source_hash = hashlib.sha256(original.encode()).hexdigest()
    csc = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    scenarios = (("fast-ready", 100, True, 3000), ("slow-ready", 6900, True, 4000),
                 ("missing-paint", 100, False, 650))
    evidence = []
    for name, delay, acknowledge, timeout in scenarios:
        directory = fixture / name
        directory.mkdir()
        payload = directory / "payload"
        (payload / "_internal").mkdir(parents=True)
        (payload / "_internal/inert.txt").write_text("Never installed", encoding="utf-8")
        (payload / "scenario.txt").write_text(f"{delay}\n{'yes' if acknowledge else 'no'}\n")
        (payload / "Fixture.cs").write_text(PAYLOAD_SOURCE, encoding="utf-8")
        run([str(csc), "/nologo", "/target:winexe", "/r:System.Windows.Forms.dll",
             "/r:System.Drawing.dll", f"/out:{payload / 'GluSpot.exe'}", str(payload / "Fixture.cs")],
            directory / "csc.log")
        source = original.replace("SetupIconFile=..\\..\\assets\\brand\\gluspot-glu.ico",
            "SetupIconFile=" + str(ROOT / "assets/brand/gluspot-glu.ico"))
        source = source.replace("ExpandConstant('{app}\\GluSpot.exe')", "'" + str(payload / "GluSpot.exe") + "'")
        source = source.replace("ExpandConstant('{app}')", "'" + str(payload) + "'")
        source = source.replace("  PaletteTimer: UINT_PTR;", "  PaletteTimer: UINT_PTR;\n"
            "  ProofOrigin, ProofNow: DWORD;")
        source = source.replace("  InitializeLayeredScene;", "  WizardForm.Position := poDesigned;\n"
            "  WizardForm.Left := -30000; WizardForm.Top := -30000;\n  InitializeLayeredScene;")
        declarations = r'''
procedure ExitProcess(Code: LongWord);
  external 'ExitProcess@kernel32.dll stdcall';
procedure ProofSleep(Milliseconds: LongWord);
  external 'Sleep@kernel32.dll stdcall';
procedure ProofRecord(Name: String);
var Bounds: TRect;
begin
  GetWindowRect(WizardForm.Handle, Bounds);
  SaveStringToFile('TRACE', Name + #9 + IntToStr(GetTickCount) + #9 +
    IntToStr(Bounds.Left) + #9 + IntToStr(Bounds.Top) + #9 +
    IntToStr(Bounds.Right - Bounds.Left) + #9 + IntToStr(Bounds.Bottom - Bounds.Top) + #13#10, True);
end;
procedure ProofFinish(Sender: TObject);
begin
  ProofRecord('installer-close');
  if WaitForSingleObject(HandoffPaintedEvent, 0) = 0 then ProofRecord('painted-at-close')
  else ProofRecord('missing-painted-at-close');
  ExitProcess(0);
end;
'''.replace("TRACE", str(directory / "installer.tsv"))
        source = source.replace("procedure LaunchInstalledWithHandoff;", declarations + "\nprocedure LaunchInstalledWithHandoff;")
        source = source.replace("GetTickCount - HandoffStartedTick >= 30000",
                                f"GetTickCount - HandoffStartedTick >= {timeout}")
        release = "            if not HandoffFailed then SetEvent(HandoffRevealEvent);"
        assert source.count(release) == 1
        source = source.replace(release, "            ProofRecord('at-top-reveal-released');\n" + release)
        prompt = "              SuppressibleMsgBox('安装已完成，但程序尚未显示。请从安装目录打开 GluSpot。', mbError, MB_OK, IDOK);"
        assert source.count(prompt) == 1
        source = source.replace(prompt, "              ProofRecord('expected-missing-paint-timeout');")
        proof = r'''
  WizardForm.NextButton.OnClick := @ProofFinish;
  CustomPageID := wpFinished;
  CompletionRunning := True;
  CompletionStartedTick := GetTickCount;
  FinishBrandLoopTick := CompletionStartedTick + 1200;
  GetWindowRect(WizardForm.Handle, OriginalWindowRect);
  FinalLeft := -30000; FinalTop := -31000;
  FinalWidth := UiX(370); FinalHeight := UiY(64);
  ProofOrigin := GetTickCount;
  ProofRecord('completion-start');
  LaunchInstalledWithHandoff;
  if HandoffFailed then begin ProofRecord('launch-failed'); ExitProcess(7); end;
  ProofRecord('payload-launched-early');
  while GetTickCount - ProofOrigin < 14000 do begin
    ProofNow := GetTickCount;
    RenderScene(ProofNow);
    ProofSleep(16);
  end;
  ProofRecord('proof-deadline'); ExitProcess(8);
'''
        needle = "  TimerResolutionHeld := timeBeginPeriod(1) = 0;"
        assert source.count(needle) == 1
        source = source.replace(needle, proof + needle)
        script = directory / "handoff-proof.iss"
        script.write_text(source, encoding="utf-8")
        run([str(options.compiler), "/DAppVersion=6.412", f"/DPayloadDir={payload}",
             f"/DArtifactDir={directory}", f"/DInstallerAssets={options.assets}",
             f"/DInstallerAppId=GluSpotHandoffProof-{identity}-{name}",
             f"/DInstallRegistryKey=Software\\GluSpot\\HandoffProof\\{identity}-{name}",
             str(script)], directory / "compile.log")
        run([str(directory / "GluSpot-6.412-Setup-x64.exe"), "/SP-", "/NORESTART",
             f"/DIR={directory / 'never-installed'}", f"/LOG={directory / 'native.log'}"],
            directory / "run.log")
        # The child exits autonomously. A process termination command is never
        # applied to a user process or even needed for the fixture child.
        import time
        for _ in range(40):
            if "payload-exit" in (payload / "payload.tsv").read_text():
                break
            time.sleep(.05)
        installer = timeline(directory / "installer.tsv")
        child = timeline(payload / "payload.tsv")
        events = {item["event"]: item for item in installer + child}
        assert not any(key.startswith("error:") for key in events), child
        assert not (directory / "never-installed").exists()
        assert events["payload-launched-early"]["tick"] < events["at-top-reveal-released"]["tick"]
        assert events["completion-start"]["tick"] <= events["payload-start"]["tick"]
        assert events["payload-start"]["tick"] < events["at-top-reveal-released"]["tick"]
        assert events["hidden-before-ready"]["tick"] <= events["ready-signal"]["tick"]
        assert events["show-after-reveal"]["tick"] >= events["at-top-reveal-released"]["tick"]
        assert events["show-after-reveal"]["tick"] >= events["ready-signal"]["tick"]
        assert events["paint-return"]["tick"] >= events["show-after-reveal"]["tick"]
        margin = json.loads((options.assets / "COMPACT.json").read_text(encoding="utf-8"))["shadow_margin"]
        assert events["at-top-reveal-released"]["detail"][:2] == [str(-30000 - margin), str(-31000 - margin)]
        checks = ["separate payload starts before top arrival", "hidden until ready and reveal gate",
                  "native top arrival is the reveal boundary", "no installation target created"]
        if acknowledge:
            assert events["installer-close"]["tick"] >= events["painted-signal"]["tick"]
            assert "painted-at-close" in events and "expected-missing-paint-timeout" not in events
            assert events["painted-signal"]["tick"] - events["paint-return"]["tick"] >= 150
            checks.append("installer remains until delayed completed-paint receipt")
            if name == "slow-ready":
                assert events["ready-signal"]["tick"] > events["at-top-reveal-released"]["tick"]
                checks.append("slow initialization retains installer beyond travel instead of blank gap")
        else:
            assert "painted-signal" not in events and "missing-painted-at-close" in events
            assert "expected-missing-paint-timeout" in events
            assert events["installer-close"]["tick"] - events["at-top-reveal-released"]["tick"] >= timeout
            checks.append("missing paint follows bounded explicit timeout without false success")
        evidence.append({"scenario": name, "checks": checks,
                         "installer": installer, "payload": child,
                         "fixture_iss_sha256": hashlib.sha256(source.encode()).hexdigest()})
    report = {"production_iss_sha256": source_hash,
              "fixture_payload_sha256": hashlib.sha256(PAYLOAD_SOURCE.encode()).hexdigest(),
              "scenarios": evidence, "check_count": sum(len(item["checks"]) for item in evidence),
              "limits": "Inert WinForms event peer; actual Qt/frozen startup is separately tested. All native windows are offscreen; no global input, real app or installation is used.",
              "fixture_adjustments": "loop wait shortened to 1200ms, no-paint timeout shortened, native close callback records and exits before installation engine"}
    (fixture / "evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"fixture": str(fixture), "checks": report["check_count"]}))


if __name__ == "__main__":
    main()
