"""Verify the copied artifact, fast-forward the one PC mirror, and run checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(r"E:\grok-gluspot\_wt_capture2")
PYTHON = Path(r"E:\gpt-island\.venv\Scripts\python.exe")
BRANCH = "grok/capture-narrow-v4"
HERE = Path(__file__).resolve().parent


def git(*args):
    return subprocess.check_output(["git", "-C", str(REPO), *args], text=True,
                                   encoding="utf-8", errors="replace",
                                   stderr=subprocess.STDOUT).strip()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt" or Path(sys.executable).resolve() != PYTHON.resolve():
        raise SystemExit("Use the fixed PC Python through run_pc.ps1")
    # The wrapper supplies the Windows known Desktop path (including redirection).
    if (args.out.name not in {"dpr1", "dpr2"} or args.out.parent.name != "mega_t156"
            or args.out.parent.parent.name != "_flicker_shared"):
        raise SystemExit("Output must be Desktop\\_flicker_shared\\mega_t156\\dpr1 or dpr2")
    args.out.mkdir(parents=True, exist_ok=True)
    result_path = args.out / "results.json"
    if result_path.exists():
        raise SystemExit("Preserve the previous mega_t156 directory before another run")
    result = dict(schema=1, started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  repo=str(REPO), python=sys.executable, sync_pass=False,
                  automated_pass=False, physical_acceptance="manual_review_required")
    try:
        manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
        if (manifest["branch"] != BRANCH or manifest["pc_repo"] != str(REPO)
                or manifest["python"] != str(PYTHON)):
            raise RuntimeError("Manifest targets a different branch, mirror or interpreter")
        for name, expected in manifest["files"].items():
            path = HERE / name
            if path.parent != HERE or digest(path) != expected:
                raise RuntimeError("Copied script/document hash mismatch: " + name)
        bundle = HERE / manifest["bundle"]
        if bundle.parent != HERE or digest(bundle) != manifest["bundle_sha256"]:
            raise RuntimeError("Copied git bundle hash mismatch")
        result["expected_tip"] = manifest["tip"]
        result["head_before"] = git("rev-parse", "HEAD")
        if git("branch", "--show-current") != BRANCH:
            raise RuntimeError("Wrong PC mirror branch; preserve it and stop")
        if git("status", "--porcelain"):
            raise RuntimeError("PC mirror has local changes; preserve them and stop")
        git("bundle", "verify", str(bundle))
        advertised = f'{manifest["tip"]} refs/heads/{BRANCH}'
        if advertised not in git("bundle", "list-heads", str(bundle)).splitlines():
            raise RuntimeError("Bundle does not advertise the exact manifest tip")
        fetched_ref = "refs/bundle/mega-t156"
        git("fetch", str(bundle), f"refs/heads/{BRANCH}:{fetched_ref}")
        git("merge-base", "--is-ancestor", result["head_before"], manifest["tip"])
        git("merge", "--ff-only", fetched_ref)
        result["head_after"] = git("rev-parse", "HEAD")
        if result["head_after"] != manifest["tip"] or git("status", "--porcelain"):
            raise RuntimeError("PC mirror did not reach the exact clean manifest tip")
        result["sync_pass"] = True
        write(result_path, result)
        environment = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONUTF8="1",
                           PYTHONIOENCODING="utf-8")
        for script, log, report in (
            ("focused_tests.py", "focused-console.log", "focused-tests.json"),
            ("native_probe.py", "native-console.log", "native-result.json"),
        ):
            with (args.out / log).open("w", encoding="utf-8") as output:
                completed = subprocess.run(
                    [str(PYTHON), "-X", "utf8", str(HERE / script), "--out", str(args.out)],
                    cwd=REPO, env=environment, stdout=output, stderr=subprocess.STDOUT,
                    timeout=240)
            result[script] = dict(exit_code=completed.returncode, log=log, report=report)
            if (args.out / report).exists():
                result[script]["result"] = json.loads((args.out / report).read_text(encoding="utf-8"))
            write(result_path, result)
            if completed.returncode:
                raise RuntimeError(f"{script} did not pass; inspect its saved report/log")
        from metrics import analyse
        result["t156_acceptance"] = analyse(args.out)
        result["automated_pass"] = not any(row["status"] == "FAIL" for row in result["t156_acceptance"]["checks"])
        result["manual_review"] = [
            "Review physical chrome/mode/back PNGs and reveal/hide frame sequences",
            "Confirm slide motion and no visible reveal/hide flicker on the actual monitor",
            "Native frame MAD is diagnostic only; no historical threshold is silently closed",
            "Review font-derived breakpoint tables, both edge-drag directions and fixed original lift",
            "Physical mouse feel, audio continuity, End/auto-next and long-session CPU remain separate",
        ]
        return 0 if result["automated_pass"] else 1
    except Exception as error:
        result["error"] = type(error).__name__ + ": " + str(error)
        return 1
    finally:
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        write(result_path, result)
        print("PC result: " + str(result_path))


if __name__ == "__main__":
    raise SystemExit(main())
