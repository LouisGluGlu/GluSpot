"""Build the executor ZIP from a clean, committed branch; run only on Box."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

BRANCH = "grok/capture-narrow-v4"
ROOT = Path(__file__).resolve().parents[3]


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True,
                                   encoding="utf-8", stderr=subprocess.STDOUT).strip()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        default=Path("/workspace/gluspot/handoff/narrow-v4/mega_t149_pc.zip"))
    args = parser.parse_args()
    if git("branch", "--show-current") != BRANCH or git("status", "--porcelain"):
        raise SystemExit("Package only the clean, committed grok/capture-narrow-v4 tip")
    if args.out.resolve().is_relative_to(ROOT.resolve()):
        raise SystemExit("Generated bundles must remain outside the source checkout")
    tip = git("rev-parse", "HEAD")
    files = [path for path in Path(__file__).parent.iterdir()
             if path.suffix in {".py", ".ps1"}]
    files.append(Path(__file__).parent.parent / "MEGA_PC.md")
    with tempfile.TemporaryDirectory(prefix="gluspot-mega-package-") as temporary:
        bundle = Path(temporary) / "mega_t149.bundle"
        git("bundle", "create", str(bundle), f"refs/heads/{BRANCH}")
        git("bundle", "verify", str(bundle))
        manifest = dict(schema=1, tip=tip, branch=BRANCH,
                        pc_repo=r"E:\grok-gluspot\_wt_capture2",
                        python=r"E:\gpt-island\.venv\Scripts\python.exe",
                        bundle=bundle.name, bundle_sha256=digest(bundle),
                        files={path.name: digest(path) for path in files})
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(bundle, bundle.name)
            for path in sorted(files):
                archive.write(path, path.name)
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps(dict(zip=str(args.out), zip_sha256=digest(args.out), tip=tip), indent=2))


if __name__ == "__main__":
    main()
