"""Offline runtime preparation keeps missing notices explicit and auditable."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
NOTICE = "GluSpot optional Node.js runtime: full release LICENSE unavailable."
PINNED = {"@neteasecloudmusicapienhanced/api": "4.41.1",
          "@yakult-green-tea/qq-music-api": "3.1.4", "kugoumusicapi": "1.6.0"}


@unittest.skipUnless(shutil.which("node"), "Node is required for offline runtime verification")
class OnlineRuntimePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "build/online-music"
        self.notices = self.runtime / "licenses"
        self.notices.mkdir(parents=True)
        self.node = shutil.which("node")
        self.version = subprocess.check_output([self.node, "--version"], text=True).strip()
        self.url = f"https://raw.githubusercontent.com/nodejs/node/{self.version}/LICENSE"
        self.license = "Node.js\nPermission is hereby granted\n" + "retained dependency notice\n" * 50
        packages = {}
        for name, version in PINNED.items():
            key = f"node_modules/{name}"
            package = self.runtime / key
            package.mkdir(parents=True)
            (package / "package.json").write_text(json.dumps({"version": version, "license": "MIT"}))
            (package / "LICENSE").write_text("Permission is hereby granted")
            packages[key] = {"version": version, "license": "MIT"}
        (self.runtime / "package-lock.json").write_text(json.dumps({"packages": packages}))
        (self.runtime / "node.exe").write_bytes(b"offline node receipt fixture")

    def verify(self, *arguments):
        return subprocess.run([self.node, str(ROOT / "native/online_music/verify-runtime.cjs"),
                               str(self.runtime), *arguments], capture_output=True, text=True, timeout=15)

    def test_full_license_is_verified_and_hashed(self):
        path = self.notices / "Node-LICENSE.txt"
        path.write_text(self.license)
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((self.runtime / "SOURCE.json").read_text())
        self.assertEqual(receipt["node"]["license_status"], "complete")
        self.assertEqual(receipt["node"]["license_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_explicit_fallback_verifies_adapters_but_records_missing_full_license(self):
        (self.notices / "Node-LICENSE-unavailable.txt").write_text(f"{NOTICE}\n{self.url}\n")
        self.assertNotEqual(self.verify().returncode, 0)
        result = self.verify("--allow-node-license-notice")
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((self.runtime / "SOURCE.json").read_text())
        self.assertEqual(receipt["node"]["license_status"], "unavailable")
        self.assertEqual(receipt["node"]["license_file"], "licenses/Node-LICENSE-unavailable.txt")
        self.assertEqual(len(receipt["packages"]), 3)
        # The packaging gate still requires this absent full-license file.
        self.assertFalse((self.notices / "Node-LICENSE.txt").exists())

    def test_fallback_cannot_bypass_bad_adapter_or_truncated_full_license(self):
        (self.notices / "Node-LICENSE-unavailable.txt").write_text(f"{NOTICE}\n{self.url}\n")
        (self.notices / "Node-LICENSE.txt").write_text("truncated download")
        self.assertNotEqual(self.verify("--allow-node-license-notice").returncode, 0)
        (self.notices / "Node-LICENSE.txt").unlink()
        (self.runtime / "node_modules/kugoumusicapi/LICENSE").write_text("incomplete")
        self.assertNotEqual(self.verify("--allow-node-license-notice").returncode, 0)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required for preparation script execution")
    def test_prepare_script_survives_download_failure_and_prefers_adjacent_license(self):
        source = self.root / "native/online_music"
        source.mkdir(parents=True)
        for name in ("prepare-runtime.ps1", "verify-runtime.cjs", "package.json", "package-lock.json"):
            shutil.copyfile(ROOT / "native/online_music" / name, source / name)
        node_dir = self.root / "node"
        node_dir.mkdir()
        node = node_dir / "node.exe"
        shutil.copy2(self.node, node)
        npm = self.root / "npm.ps1"
        npm.write_text("$global:LASTEXITCODE = 0\n")
        wrapper = self.root / "prepare.ps1"
        wrapper.write_text("""param($Prepare, $Node, $Npm)
function Invoke-WebRequest { throw 'offline fixture download failure' }
& $Prepare -NodePath $Node -NpmPath $Npm
""")
        command = [shutil.which("pwsh"), "-NoProfile", "-File", str(wrapper),
                   str(source / "prepare-runtime.ps1"), str(node), str(npm)]
        for local in (False, True):
            if local:
                (node_dir / "LICENSE").write_text(self.license)
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            receipt = json.loads((self.runtime / "SOURCE.json").read_text())
            self.assertEqual(receipt["node"]["license_status"], "complete" if local else "unavailable")
            self.assertEqual((self.notices / "Node-LICENSE-unavailable.txt").exists(), not local)


if __name__ == "__main__":
    unittest.main()
