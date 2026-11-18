"""Software update (api.app_update, local.app_update_controller, settings 软件更新).

Rules confirmed by the user on 2026-11-18: two switches, both off by default;
自动更新 only with 自动检查更新 on; checking happens once at start, plus a
manual 检查更新; a found release is announced and shown in settings with its
notes, 更新 and 忽略此版本; 自动更新 downloads in the background and installs
silently when GluSpot quits; only official releases; a portable copy is only
pointed at the download page.
"""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from api import app_update
from api.app_update import ReleaseInfo, check, download, install_kind, installer_command, is_newer, parse_release, version_key
from local.app_update_controller import UpdateController

PAYLOAD = b"GluSpot installer bytes" * 1000


def release_json(tag="v6.53", *, prerelease=False, draft=False, digest=True):
    return {
        "tag_name": tag, "name": f"GluSpot {tag[1:]}", "body": "歌单同步更稳了", "draft": draft, "prerelease": prerelease,
        "html_url": f"https://github.com/LouisGluGlu/GluSpot/releases/tag/{tag}",
        "assets": [
            {"name": f"GluSpot-{tag[1:]}-source.zip", "browser_download_url": "https://github.com/x/source.zip", "size": 9},
            {"name": f"GluSpot-{tag[1:]}-Setup-x64.exe", "size": len(PAYLOAD),
             "browser_download_url": f"https://github.com/LouisGluGlu/GluSpot/releases/download/{tag}/setup.exe",
             "digest": ("sha256:" + hashlib.sha256(PAYLOAD).hexdigest()) if digest else None},
        ],
    }


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def opener_for(payload=None, body=PAYLOAD, seen=None):
    def opener(request, timeout=0):
        if seen is not None:
            seen.append((request.full_url, dict(request.header_items())))
        if "api.github.com" in request.full_url:
            return FakeResponse(json.dumps(payload).encode("utf-8"))
        return FakeResponse(body)
    return opener


class VersionAndReleaseTests(unittest.TestCase):
    def test_versions_keep_a_three_digit_minor(self):
        self.assertEqual(version_key("v6.52"), (6, 520, 0))
        self.assertTrue(is_newer("6.52", "6.51"))
        self.assertTrue(is_newer("6.5", "6.418"))
        self.assertFalse(is_newer("6.418", "6.5"))
        self.assertFalse(is_newer("6.52", "6.52"))
        self.assertFalse(is_newer("nonsense", "6.52"))

    def test_only_official_releases_with_a_github_installer_count(self):
        self.assertIsNone(parse_release(release_json(prerelease=True)))
        self.assertIsNone(parse_release(release_json(draft=True)))
        release = parse_release(release_json())
        self.assertEqual((release.version, release.installer_name), ("6.53", "GluSpot-6.53-Setup-x64.exe"))
        self.assertEqual(release.installer_sha256, hashlib.sha256(PAYLOAD).hexdigest())
        foreign = release_json()
        foreign["assets"][1]["browser_download_url"] = "https://evil.example/setup.exe"
        self.assertEqual(parse_release(foreign).installer_url, "")

    def test_check_reports_available_current_and_ignored(self):
        seen = []
        self.assertEqual(check("6.52", opener=opener_for(release_json(), seen=seen))[0], "available")
        url, headers = seen[0]
        self.assertEqual(url, app_update.LATEST_URL)
        self.assertEqual(headers.get("User-agent"), "GluSpot-Updater", "no personal data in the request")
        self.assertEqual(check("6.53", opener=opener_for(release_json()))[0], "current")
        self.assertEqual(check("6.52", "6.53", opener=opener_for(release_json()))[0], "ignored")
        self.assertEqual(check("6.52", "6.53", manual=True, opener=opener_for(release_json()))[0], "available")

    def test_download_verifies_size_and_checksum(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        release = parse_release(release_json())
        path = download(release, Path(folder.name), opener=opener_for(body=PAYLOAD))
        self.assertEqual(path.read_bytes(), PAYLOAD)
        calls = []
        self.assertEqual(download(release, Path(folder.name), opener=lambda *a, **k: calls.append(1)), path,
                         "a verified copy is not fetched again")
        self.assertEqual(calls, [])
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        with self.assertRaises(ValueError):
            download(release, Path(other.name), opener=opener_for(body=PAYLOAD[:-1] + b"X"))
        self.assertEqual(list(Path(other.name).glob("*.exe")), [])

    def test_install_kind_and_silent_installer_command(self):
        self.assertEqual(install_kind(frozen=False), "source")
        exe = Path(r"C:\Users\x\AppData\Local\Programs\GluSpot\GluSpot.exe")
        self.assertEqual(install_kind(str(exe), frozen=True, registered_path=str(exe.parent)), "installed")
        self.assertEqual(install_kind(r"D:\Apps\GluSpot\GluSpot.exe", frozen=True, registered_path=str(exe.parent)), "portable")
        command = installer_command(Path(r"C:\tmp\it's\GluSpot-6.53-Setup-x64.exe"), r"C:\Prog\GluSpot", relaunch=True)
        script = command[-1]
        self.assertIn("'C:\\tmp\\it''s\\GluSpot-6.53-Setup-x64.exe'", script)
        self.assertIn("/VERYSILENT", script)
        self.assertIn("GluSpot.exe", script)
        quiet = installer_command(Path("s.exe"), "C:\\P", relaunch=False)[-1]
        self.assertNotIn("GluSpot.exe", quiet, "installing on quit does not reopen GluSpot")


class Settings:
    def __init__(self, **values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def controller(self, settings=None, *, kind="installed", outcome="available"):
        release = ReleaseInfo("6.53", "GluSpot 6.53", "说明", "https://github.com/LouisGluGlu/GluSpot/releases/tag/v6.53",
                              "https://github.com/x/setup.exe", "GluSpot-6.53-Setup-x64.exe", 10)
        self.downloads, self.started, self.opened = [], [], []
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        installer = Path(folder.name) / "GluSpot-6.53-Setup-x64.exe"
        installer.write_bytes(b"x")

        def fake_download(rel, where, progress=None):
            self.downloads.append(rel.version)
            progress(10, 10)
            return installer

        controller = UpdateController(
            settings or Settings(), "6.52", check=lambda current, ignored, manual=False: (outcome, release),
            download=fake_download, kind=kind, install_dir=r"C:\Prog\GluSpot", folder=Path(folder.name),
            starter=lambda path, where, relaunch: self.started.append((Path(path).name, relaunch)),
            opener=self.opened.append)
        return controller

    def wait(self, controller, *states):
        deadline = time.monotonic() + 3
        while controller.state not in states and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertIn(controller.state, states)

    def test_both_switches_start_off_and_nothing_runs_at_start(self):
        controller = self.controller()
        self.assertFalse(controller.check_enabled())
        self.assertFalse(controller.auto_enabled())
        controller.start()
        self.app.processEvents()
        self.assertEqual(controller.state, "idle")

    def test_auto_update_needs_checking_and_turns_off_with_it(self):
        settings = Settings()
        controller = self.controller(settings)
        controller.set_auto_enabled(True)
        self.assertFalse(settings.values.get("v2_update_auto_enabled"))
        controller.set_check_enabled(True)
        controller.set_auto_enabled(True)
        self.assertTrue(controller.auto_enabled())
        controller.set_check_enabled(False)
        self.assertFalse(settings.values["v2_update_auto_enabled"])

    def test_startup_check_announces_and_auto_update_installs_on_quit_only(self):
        settings = Settings(v2_update_check_enabled=True, v2_update_auto_enabled=True)
        controller = self.controller(settings)
        notes = []
        controller.notify.connect(notes.append)
        controller.check(manual=False)
        self.wait(controller, "ready")
        self.assertEqual(self.downloads, ["6.53"])
        self.assertIn("6.53", notes[0])
        self.assertEqual(self.started, [], "nothing installs while GluSpot runs")
        controller.on_quit()
        self.assertEqual(self.started, [("GluSpot-6.53-Setup-x64.exe", False)])

    def test_manual_check_without_auto_offers_update_and_ignore(self):
        settings = Settings(v2_update_check_enabled=False)
        controller = self.controller(settings)
        notes = []
        controller.notify.connect(notes.append)
        controller.check(manual=True)
        self.wait(controller, "available")
        self.assertEqual((self.downloads, notes), ([], []))
        quits = []
        controller.quit_callback = lambda: quits.append(True)
        controller.update_now()
        self.wait(controller, "installing")
        self.assertEqual(self.started, [("GluSpot-6.53-Setup-x64.exe", True)])
        self.assertEqual(quits, [True])
        other = self.controller(settings)
        other.check(manual=True)
        self.wait(other, "available")
        other.ignore()
        self.assertEqual(settings.values["v2_update_ignored_version"], "6.53")
        other.on_quit()
        self.assertEqual(self.started, [], "an ignored, undownloaded release never installs")

    def test_portable_copy_only_opens_the_download_page(self):
        settings = Settings(v2_update_check_enabled=True, v2_update_auto_enabled=True)
        controller = self.controller(settings, kind="portable")
        controller.check(manual=False)
        self.wait(controller, "available")
        self.assertEqual(self.downloads, [])
        controller.update_now()
        self.assertEqual(self.opened, ["https://github.com/LouisGluGlu/GluSpot/releases/tag/v6.53"])
        controller.on_quit()
        self.assertEqual(self.started, [])

    def test_offline_check_is_reported_not_raised(self):
        controller = UpdateController(Settings(), "6.52", check=lambda *a, **k: (_ for _ in ()).throw(OSError("offline")),
                                      kind="installed", install_dir="", starter=lambda *a, **k: None)
        controller.check(manual=True)
        self.wait(controller, "error")
        self.assertIn("没能连上", controller.message)


class SettingsCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_update_card_switches_default_off_and_show_a_found_release(self):
        from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager
        from local.settings_window import SettingsWindow
        settings = FakeSettingsManager()
        window = SettingsWindow(FakeIslandWidget(), settings)
        self.addCleanup(window.close)
        self.assertFalse(window.update_check_switch.isChecked())
        self.assertFalse(window.update_auto_switch.isChecked())
        self.assertFalse(window.update_auto_switch.isEnabled(), "自动更新 needs 自动检查更新")
        self.assertTrue(window.update_notes_row.isHidden())
        controller = ControllerTests.controller(ControllerTests("test_both_switches_start_off_and_nothing_runs_at_start"), settings)
        window.update_controller = controller
        controller.changed.connect(window.refresh_update_state)
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        QTest.mouseClick(window.update_check_switch, Qt.MouseButton.LeftButton,
                         pos=window.update_check_switch.rect().center())
        self.assertTrue(settings.get("v2_update_check_enabled"))
        self.assertTrue(window.update_auto_switch.isEnabled())
        controller.check(manual=True)
        deadline = time.monotonic() + 3
        while controller.state != "available" and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(window.update_notes_row.isHidden())
        self.assertIn("说明", window.update_notes_label.text())
        self.assertEqual(window.update_now_button.text(), "更新")
        window.update_ignore_button.click()
        self.assertEqual(settings.get("v2_update_ignored_version"), "6.53")
        self.assertTrue(window.show_update_section())


if __name__ == "__main__":
    unittest.main()
