from __future__ import annotations

import unittest


class FakeRegistry:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []

    def set_run_value(self, name: str, command: str):
        self.values[name] = command

    def delete_run_value(self, name: str):
        self.deleted.append(name)
        self.values.pop(name, None)


class FakeRunner:
    def __init__(self):
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]):
        self.commands.append(command)
        return 0


class StartupModeTests(unittest.TestCase):
    def test_none_removes_registry_and_scheduled_task(self):
        from local.startup import StartupManager

        registry = FakeRegistry()
        runner = FakeRunner()
        manager = StartupManager(registry=registry, runner=runner, platform="win32")

        self.assertTrue(manager.apply("none", "pythonw.exe local/main.py"))

        self.assertEqual(registry.deleted, ["GluSpot"])
        self.assertEqual(runner.commands, [["schtasks", "/Delete", "/TN", "GluSpot", "/F"]])

    def test_registry_mode_writes_hkcu_run_and_removes_scheduled_task(self):
        from local.startup import StartupManager

        registry = FakeRegistry()
        runner = FakeRunner()
        manager = StartupManager(registry=registry, runner=runner, platform="win32")

        self.assertTrue(manager.apply("registry", '"GluSpot.exe"'))

        self.assertEqual(registry.values["GluSpot"], '"GluSpot.exe"')
        self.assertEqual(runner.commands, [["schtasks", "/Delete", "/TN", "GluSpot", "/F"]])

    def test_task_mode_creates_on_logon_task_and_removes_registry_entry(self):
        from local.startup import StartupManager

        registry = FakeRegistry()
        runner = FakeRunner()
        manager = StartupManager(registry=registry, runner=runner, platform="win32")

        self.assertTrue(manager.apply("task", '"GluSpot.exe"'))

        self.assertEqual(registry.deleted, ["GluSpot"])
        self.assertEqual(
            runner.commands,
            [
                [
                    "schtasks",
                    "/Create",
                    "/TN",
                    "GluSpot",
                    "/SC",
                    "ONLOGON",
                    "/TR",
                    '"GluSpot.exe"',
                    "/RL",
                    "LIMITED",
                    "/F",
                ]
            ],
        )

    def test_non_windows_is_noop_without_failure(self):
        from local.startup import StartupManager

        registry = FakeRegistry()
        runner = FakeRunner()
        manager = StartupManager(registry=registry, runner=runner, platform="linux")

        self.assertTrue(manager.apply("task", '"GluSpot.exe"'))

        self.assertEqual(registry.values, {})
        self.assertEqual(registry.deleted, [])
        self.assertEqual(runner.commands, [])


if __name__ == "__main__":
    unittest.main()
