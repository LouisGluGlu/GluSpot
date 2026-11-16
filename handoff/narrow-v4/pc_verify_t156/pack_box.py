"""Package the clean committed t156 tip on Box; never invoke the PC."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

REPO = Path("/workspace/gluspot/grok-integrate4")
HERE = Path(__file__).resolve().parent
BRANCH = "grok/capture-narrow-v4"
BASE = "12c1e0fa9524da4982b5932d9d591d8f13ffb0bb"
EXTERNAL = Path("/workspace/gluspot/handoff/narrow-v4")

def git(*args):
    return subprocess.check_output(["git", "-C", str(REPO), *args], text=True,
        encoding="utf-8", stderr=subprocess.STDOUT).strip()

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=EXTERNAL / "mega_t156_pc.zip")
    args = parser.parse_args()
    if git("branch", "--show-current") != BRANCH or git("status", "--porcelain"):
        raise SystemExit("Package only the exact clean committed grok/capture-narrow-v4 tip")
    if args.out.resolve().is_relative_to(REPO.resolve()):
        raise SystemExit("Keep generated artifacts outside the source checkout")
    tip = git("rev-parse", "HEAD")
    git("merge-base", "--is-ancestor", BASE, tip)
    sources = REPO / "handoff/narrow-v4/pc_verify_t156"
    destination = EXTERNAL / "pc_verify_t156"
    destination.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in sources.iterdir() if path.suffix in {".py", ".ps1"})
    files.append(REPO / "handoff/narrow-v4/MEGA_T156_PC.md")
    for source in files:
        target = destination / source.name
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
    shutil.copyfile(REPO / "handoff/narrow-v4/MEGA_T156_PC.md", EXTERNAL / "MEGA_T156_PC.md")
    with tempfile.TemporaryDirectory(prefix="gluspot-t156-package-") as temporary:
        bundle = Path(temporary) / "mega_t156.bundle"
        git("bundle", "create", str(bundle), f"{BASE}..refs/heads/{BRANCH}")
        git("bundle", "verify", str(bundle))
        manifest = dict(schema=1, tip=tip, base=BASE, branch=BRANCH,
            pc_repo=r"E:\grok-gluspot\_wt_capture2", python=r"E:\gpt-island\.venv\Scripts\python.exe",
            bundle=bundle.name, bundle_sha256=digest(bundle),
            files={path.name: digest(path) for path in files})
        shutil.copyfile(bundle, destination / bundle.name)
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(bundle, bundle.name)
            for path in files:
                archive.write(path, path.name)
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    with zipfile.ZipFile(args.out) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP CRC verification failed")
        for name, expected in {**manifest["files"], manifest["bundle"]: manifest["bundle_sha256"]}.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise RuntimeError("ZIP entry hash mismatch: " + name)
    print(json.dumps(dict(tip=tip, base=BASE, zip=str(args.out),
        zip_sha256=digest(args.out), bundle_sha256=manifest["bundle_sha256"], pc_run=False), indent=2))

if __name__ == "__main__":
    main()
