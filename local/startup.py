from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


APP_ID = "GluSpot"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_MODES = {"none", "registry", "task"}


def normalize_startup_mode(mode: str | None) -> str:
    value = str(mode or "").strip().lower()
    return value if value in STARTUP_MODES else "none"


def quote_windows_arg(value: str) -> str:
    text = str(value or "")
    if not text:
        return '""'
    escaped = text.replace('"', r"\"")
    return f'"{escaped}"' if any(ch.isspace() for ch in escaped) else escaped


def build_startup_command(root_dir: str | os.PathLike[str] | None = None) -> str:
    root = Path(root_dir) if root_dir is not None else Path(__file__).resolve().parents[1]
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return quote_windows_arg(str(executable))

    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    launcher = pythonw if pythonw.exists() else executable
    main_py = root / "local" / "main.py"
    return f"{quote_windows_arg(str(launcher))} -X utf8 {quote_windows_arg(str(main_py))}"


class WinRunRegistry:
    def set_run_value(self, name: str, command: str):
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)

    def delete_run_value(self, name: str):
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            return


def _run_command(command: list[str]) -> int:
    completed = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return int(completed.returncode)


class StartupManager:
    def __init__(self, registry=None, runner=None, platform: str | None = None):
        self.registry = registry or WinRunRegistry()
        self.runner = runner or _run_command
        self.platform = platform or sys.platform

    def apply(self, mode: str, command: str) -> bool:
        if self.platform != "win32":
            return True
        normalized = normalize_startup_mode(mode)
        if normalized == "none":
            self._delete_registry()
            self._delete_task()
            return True
        if normalized == "registry":
            self._delete_task()
            return self._set_registry(command)
        if normalized == "task":
            self._delete_registry()
            return self._create_task(command)
        return False

    def _set_registry(self, command: str) -> bool:
        try:
            self.registry.set_run_value(APP_ID, command)
            return True
        except OSError:
            return False

    def _delete_registry(self):
        try:
            self.registry.delete_run_value(APP_ID)
        except OSError:
            return

    def _create_task(self, command: str) -> bool:
        return (
            self.runner(
                [
                    "schtasks",
                    "/Create",
                    "/TN",
                    APP_ID,
                    "/SC",
                    "ONLOGON",
                    "/TR",
                    command,
                    "/RL",
                    "LIMITED",
                    "/F",
                ]
            )
            == 0
        )

    def _delete_task(self):
        self.runner(["schtasks", "/Delete", "/TN", APP_ID, "/F"])
