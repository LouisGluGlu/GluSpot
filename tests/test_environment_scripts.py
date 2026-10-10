"""Actual Windows subprocess checks; every environment belongs to a temp fixture.

Fixtures use the interpreter running this suite, real offline pip, empty
requirements and inert dependency modules. They never start GluSpot, build the
native helper, download packages, or mutate a developer/user environment.
"""
from __future__ import annotations

import json
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_PYTHON = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
WINDOWS = os.name == "nt"


@unittest.skipUnless(WINDOWS, "Windows batch and PowerShell integration")
class EnvironmentScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="GluSpot-env-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.container = Path(self.temporary.name)
        self.root = self.container / "中文 空格 & ! 项目"
        self.root.mkdir()
        self.outside = self.container / "outside"
        self.outside.mkdir()
        self.env = dict(os.environ)
        self.env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PIP_NO_INDEX="1",
            PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_CACHE_DIR="1")
        # A inherited selector must not turn a fixture into a user's environment.
        for key in ("BASEPY", "VENV", "PYEXE", "GLUSPOT_PYTHON"):
            self.env.pop(key, None)
        for name in ("setup-env.bat", "repair-env.bat", "launch.bat", "build-exe.bat"):
            shutil.copy2(PROJECT_ROOT / name, self.root / name)
        for source in PROJECT_ROOT.glob("*.ps1"):
            shutil.copy2(source, self.root / source.name)
        shutil.copytree(PROJECT_ROOT / "packaging" / "windows", self.root / "packaging" / "windows",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (self.root / "local").mkdir()
        (self.root / "local" / "main.py").write_text(
            "import json, os\nfrom pathlib import Path\n"
            "Path(os.environ['GLUSPOT_TEST_MAIN_RECORD']).write_text(json.dumps({'cwd':os.getcwd()}))\n",
            encoding="utf-8")
        (self.root / "requirements.txt").write_text("", encoding="utf-8")
        (self.root / "GluSpot.spec").write_text("# inert fixture spec\n", encoding="utf-8")
        self.env["GLUSPOT_TEST_MAIN_RECORD"] = str(self.container / "main-record.json")
        self.invocation_count = 0

    def run_batch(self, name, *arguments, env=None, timeout=30):
        # A UTF-8 invoker avoids subprocess/CMD's extra `/c` quote-stripping.
        # Delayed expansion is disabled so a literal ! in the source path survives.
        self.invocation_count += 1
        invoker = self.container / f"invoke-{self.invocation_count}.cmd"
        command = 'call "' + str(self.root / name) + '"'
        for argument in arguments:
            if '"' in str(argument) or '\n' in str(argument):
                raise ValueError("fixture argument cannot contain quote or newline")
            command += ' "' + str(argument) + '"'
        invoker.write_text('@echo off\nchcp 65001 >nul\n' + command + '\nexit /b %errorlevel%\n', encoding="utf-8")
        return subprocess.run([self.env.get("COMSPEC", "cmd.exe"), "/d", "/v:off", "/c", str(invoker)],
            cwd=self.outside, env=env or self.env, input="", capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout)

    def run_powershell(self, script, *arguments, env=None, timeout=30):
        shell = Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        return subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script), *map(str, arguments)], cwd=self.outside,
            env=env or self.env, input="", capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout)

    def create_venv(self, root=None, *, with_pip=False):
        root = root or self.root
        venv = root / ".venv"
        subprocess.run([str(BASE_PYTHON), "-m", "venv", "--without-pip", str(venv)],
            env=self.env, capture_output=True, check=True, timeout=30)
        python = venv / "Scripts" / "python.exe"
        if with_pip:
            subprocess.run([str(python), "-m", "ensurepip", "--default-pip"],
                env=self.env, capture_output=True, check=True, timeout=30)
        return python

    def fixture_modules(self):
        files = {}
        for module in ("soundcard", "numpy", "pywinauto", "uiautomation", "PIL", "PyInstaller", "ToJyutping", "zeroconf"):
            files[module + ".py"] = b"# inert import-only environment test fixture\n"
        for package in ("PySide6", "winsdk", "winsdk/windows", "winsdk/windows/media"):
            files[package + "/__init__.py"] = b"# inert import-only environment test fixture\n"
        for module in ("PySide6/QtCore", "PySide6/QtGui", "PySide6/QtWidgets", "PySide6/QtMultimedia", "winsdk/windows/media/control"):
            files[module + ".py"] = b"# inert import-only environment test fixture\n"
        packaging = Path(importlib.util.find_spec("packaging").origin).parent
        for source in packaging.rglob("*.py"):
            files["packaging/" + source.relative_to(packaging).as_posix()] = source.read_bytes()
        return files

    def seed_ready_environment(self, root=None):
        root = root or self.root
        python = self.create_venv(root)
        site = root / ".venv" / "Lib" / "site-packages"
        # Real pip is copied only inside this disposable venv; all application
        # dependencies are expressly inert fixtures, not application validation.
        pip_package = Path(importlib.util.find_spec("pip").origin).parent
        shutil.copytree(pip_package, site / "pip", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name, content in self.fixture_modules().items():
            destination = site / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        return python

    def prepare_offline_fixture_wheel(self):
        wheels = self.container / "offline fixture wheels"
        wheels.mkdir()
        wheel = wheels / "gluspot_env_test_fixtures-0.0.0-py3-none-any.whl"
        files = self.fixture_modules()
        info = "gluspot_env_test_fixtures-0.0.0.dist-info/"
        files[info + "METADATA"] = b"Metadata-Version: 2.1\nName: gluspot-env-test-fixtures\nVersion: 0.0.0\n\n"
        files[info + "WHEEL"] = b"Wheel-Version: 1.0\nGenerator: GluSpot-offline-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        files[info + "RECORD"] = ("\n".join(name + ",," for name in files) + "\n" + info + "RECORD,,\n").encode()
        with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        (self.root / "requirements.txt").write_text("gluspot-env-test-fixtures==0.0.0\n", encoding="utf-8")
        self.env["PIP_FIND_LINKS"] = wheels.as_uri()

    def canonical(self, *arguments, root=None, env=None, timeout=40):
        return self.run_powershell(self.root / "packaging" / "windows" / "setup-environment.ps1",
            "-NoPause", "-ProjectRoot", root or self.root, *arguments, env=env, timeout=timeout)

    @staticmethod
    def snapshot(root):
        return {path.relative_to(root).as_posix(): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
                for path in root.rglob("*") if path.is_file()}

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def wait_for_file(self, path):
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(path.is_file(), f"fixture child did not create {path.name}")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_setup_batch_creates_offline_environment_from_noncwd_special_path(self):
        self.prepare_offline_fixture_wheel()
        result = self.run_batch("setup-env.bat", "-NoPause", "-PythonPath", BASE_PYTHON, timeout=55)
        self.assert_success(result)
        self.assertIn("Environment is ready", result.stdout)
        self.assertNotIn("Press Enter", result.stdout)
        self.assert_success(self.canonical("-CheckOnly"))
        self.assertFalse((self.root / "logs").exists())
        self.assertFalse(Path(self.env["GLUSPOT_TEST_MAIN_RECORD"]).exists())

    def test_repair_keeps_existing_valid_venv_and_marker(self):
        python = self.seed_ready_environment()
        marker = python.parent.parent / "preserved-user-marker.txt"
        marker.write_text("fixture must survive repair", encoding="utf-8")
        created = python.stat().st_mtime_ns
        result = self.canonical("-PythonPath", BASE_PYTHON)
        self.assert_success(result)
        self.assertEqual(python.stat().st_mtime_ns, created)
        self.assertEqual(marker.read_text(encoding="utf-8"), "fixture must survive repair")
        self.assertEqual(list(self.root.glob(".venv.backup-*")), [])

    def test_copied_venv_with_valid_prefix_but_old_creation_path_is_rebuilt_with_backup(self):
        old = self.container / "old copied environment"
        old.mkdir()
        self.seed_ready_environment(old)
        shutil.copytree(old / ".venv", self.root / ".venv", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        marker = self.root / ".venv" / "original-contents.txt"
        marker.write_text("copy backup proof", encoding="utf-8")
        probe = subprocess.run([str(self.root / ".venv" / "Scripts" / "python.exe"), "-I", "-X", "utf8", "-c", "import sys; print(sys.prefix)"],
            env=self.env, capture_output=True, encoding="utf-8", check=True)
        self.assertEqual(Path(probe.stdout.strip()).resolve(), (self.root / ".venv").resolve())
        before = self.snapshot(self.root)
        checked = self.canonical("-CheckOnly")
        self.assertEqual(checked.returncode, 7, checked.stdout + checked.stderr)
        self.assertEqual(self.snapshot(self.root), before)
        self.prepare_offline_fixture_wheel()
        self.assert_success(self.canonical("-PythonPath", BASE_PYTHON, timeout=55))
        backups = list(self.root.glob(".venv.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "original-contents.txt").read_text(encoding="utf-8"), "copy backup proof")
        self.assertTrue((old / ".venv" / "Scripts" / "python.exe").is_file())
        self.assert_success(self.canonical("-CheckOnly"))

    def test_invalid_explicit_or_environment_python_fails_without_touching_old_env(self):
        old = self.root / ".venv"
        old.mkdir()
        (old / "preserved.txt").write_text("broken fixture", encoding="utf-8")
        before = self.snapshot(self.root)
        absent = self.container / "absent python.exe"
        for selected_by in ("argument", "environment"):
            with self.subTest(selected_by=selected_by):
                env = dict(self.env)
                arguments = ()
                if selected_by == "argument":
                    arguments = ("-PythonPath", absent)
                else:
                    env["GLUSPOT_PYTHON"] = str(absent)
                result = self.canonical(*arguments, env=env)
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertIn("No compatible CPython 3.12 x64", result.stdout)
                self.assertEqual(self.snapshot(self.root), before)
                self.assertEqual(list(self.root.glob(".venv.backup-*")), [])

    def test_no_discoverable_python_reports_actionable_failure_without_creating_env(self):
        # This machine has no fixed C:/Python312 fallback. A different Windows
        # host can still test the required failure via an explicit absent path.
        env = dict(self.env)
        env["PATH"] = str(self.outside)
        env["LOCALAPPDATA"] = str(self.outside / "empty-local-app-data")
        env["ProgramFiles"] = str(self.outside / "empty-program-files")
        arguments = () if not Path("C:/Python312/python.exe").is_file() else \
            ("-PythonPath", self.outside / "absent-python.exe")
        before = self.snapshot(self.root)
        result = self.canonical(*arguments, env=env)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("-PythonPath or GLUSPOT_PYTHON", result.stdout)
        self.assertEqual(self.snapshot(self.root), before)
        self.assertFalse((self.root / ".venv").exists())

    def test_missing_environment_checkonly_leaves_project_unchanged(self):
        before = self.snapshot(self.root)
        result = self.run_batch("launch.bat", "-NoPause", "-CheckOnly")
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(self.root), before)
        self.assertFalse((self.root / "logs").exists())

    def test_pip_install_failure_returns_six_and_never_launches(self):
        self.seed_ready_environment()
        (self.root / "requirements.txt").write_text("gluspot-definitely-absent-test-fixture==0.0.0\n", encoding="utf-8")
        result = self.canonical()
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertIn("Dependency installation failed", result.stdout)
        self.assertFalse(Path(self.env["GLUSPOT_TEST_MAIN_RECORD"]).exists())

    def test_checkonly_wrappers_are_readonly_and_do_not_launch_or_build(self):
        self.seed_ready_environment()
        before = self.snapshot(self.root)
        for name in ("setup-env.bat", "repair-env.bat", "launch.bat", "build-exe.bat"):
            with self.subTest(wrapper=name):
                result = self.run_batch(name, "-NoPause", "-CheckOnly")
                self.assert_success(result)
                self.assertEqual(self.snapshot(self.root), before)
        self.assertFalse((self.root / "logs").exists())
        self.assertFalse((self.root / "release").exists())
        self.assertFalse(Path(self.env["GLUSPOT_TEST_MAIN_RECORD"]).exists())

    def test_checkonly_blocks_import_time_filesystem_writes(self):
        self.seed_ready_environment()
        site = self.root / ".venv" / "Lib" / "site-packages"
        (site / "pywinauto.py").write_text("from pathlib import Path\nPath(__file__).with_name('unexpected-write.txt').write_text('unsafe')\n", encoding="utf-8")
        before = self.snapshot(self.root)
        result = self.canonical("-CheckOnly")
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn("PermissionError", result.stdout)
        self.assertEqual(self.snapshot(self.root), before)

    def test_checkonly_does_not_allow_site_startup_write_before_doctor_hook(self):
        self.seed_ready_environment()
        site = self.root / ".venv" / "Lib" / "site-packages"
        destination = site / "unexpected-startup-write.txt"
        (site / "fixture-startup.pth").write_text(
            "import pathlib; pathlib.Path(" + ascii(str(destination)) + ").write_text('fixture startup side effect')\n",
            encoding="utf-8")
        before = self.snapshot(self.root)
        result = self.canonical("-CheckOnly")
        self.assertFalse(destination.exists(), result.stdout + result.stderr)
        self.assertEqual(self.snapshot(self.root), before)

    def test_multiroot_check_continues_after_invalid_first_folder(self):
        self.seed_ready_environment()
        invalid = self.container / "unmarked project"
        invalid.mkdir()
        result = self.run_powershell(self.root / "packaging" / "windows" / "setup-environment.ps1",
            "-NoPause", "-CheckOnly", "-ProjectRoot", invalid, self.root)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("Invalid project folder", result.stdout)
        self.assertIn("Environment is ready", result.stdout)

    def test_repair_batch_multiple_quoted_folders_preserves_error_and_visits_second(self):
        self.seed_ready_environment()
        invalid = self.container / "未标记 & ! 文件夹"
        invalid.mkdir()
        before = self.snapshot(self.root)
        result = self.run_batch("repair-env.bat", "-NoPause", "-CheckOnly", invalid, self.root)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("Invalid project folder", result.stdout)
        self.assertIn("Environment is ready", result.stdout)
        self.assertEqual(self.snapshot(self.root), before)

    def test_foreign_repository_path_in_pth_is_rejected_in_readonly_mode(self):
        self.seed_ready_environment()
        foreign = self.container / "foreign repository site"
        foreign.mkdir()
        site = self.root / ".venv" / "Lib" / "site-packages"
        (site / "foreign-path.pth").write_text(str(foreign) + "\n", encoding="utf-8")
        before = self.snapshot(self.root)
        result = self.canonical("-CheckOnly")
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(self.root), before)

    def test_environment_junction_is_refused_without_target_mutation(self):
        target = self.container / "junction target"
        target.mkdir()
        (target / "preserved.txt").write_text("external target fixture", encoding="utf-8")
        helper = self.container / "create-junction.ps1"
        helper.write_text("param([string]$Link,[string]$Target)\nNew-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n", encoding="utf-8-sig")
        self.assert_success(self.run_powershell(helper, self.root / ".venv", target))
        before = self.snapshot(target)
        result = self.canonical("-PythonPath", BASE_PYTHON)
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertIn("Redirected environment path refused", result.stdout)
        self.assertEqual(self.snapshot(target), before)
        self.assertEqual(list(self.root.glob(".venv.backup-*")), [])

    def test_launch_healthy_fixture_from_noncwd_starts_only_fixture_main(self):
        self.seed_ready_environment()
        result = self.run_batch("launch.bat", "-NoPause")
        self.assert_success(result)
        record = self.wait_for_file(Path(self.env["GLUSPOT_TEST_MAIN_RECORD"]))
        self.assertEqual(Path(record["cwd"]).resolve(), self.root.resolve())
        self.assertTrue((self.root / "logs").is_dir())
