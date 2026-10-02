from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class BrandingTests(unittest.TestCase):
    def test_branding_icon_paths_match_requested_assets(self):
        from local.branding import APP_ICON_PATH, APP_NAME, APP_VERSION, TRAY_ICON_PATH

        self.assertEqual(APP_NAME, "GluSpot")
        self.assertEqual(APP_VERSION, "6.408")
        self.assertEqual(APP_ICON_PATH.name, "gluspot-card-center.ico")
        self.assertEqual(TRAY_ICON_PATH.name, "gluspot.ico")
        self.assertTrue(APP_ICON_PATH.exists())
        self.assertTrue(TRAY_ICON_PATH.exists())

    def test_branding_icon_assets_use_black_white_card_mark(self):
        from PIL import Image

        root = Path(__file__).resolve().parents[1]
        png_path = root / "assets" / "brand" / "gluspot-card-center-icon-256.png"
        ico_path = root / "assets" / "brand" / "gluspot.ico"

        image = Image.open(png_path).convert("RGBA")
        self.assertEqual(image.size, (256, 256))

        def brightness(x: int, y: int) -> float:
            red, green, blue, _alpha = image.getpixel((x, y))
            return (red + green + blue) / 3.0

        self.assertGreater(image.getpixel((0, 0))[3], 245)
        self.assertGreater(brightness(16, 16), 236)
        self.assertGreater(brightness(128, 54), 236)
        self.assertLess(brightness(55, 130), 24)
        self.assertLess(brightness(114, 130), 24)
        self.assertLess(brightness(165, 130), 24)
        self.assertLess(brightness(206, 130), 24)

        with Image.open(ico_path) as ico:
            self.assertGreaterEqual(ico.size[0], 16)
            self.assertGreaterEqual(ico.size[1], 16)

    def test_windows_build_and_documented_version_match_runtime_branding(self):
        from local.branding import APP_VERSION

        root = Path(__file__).resolve().parents[1]
        spec = (root / "GluSpot.spec").read_text(encoding="utf-8")
        version = (root / "packaging" / "windows" / "version_info.txt").read_text(encoding="utf-8")
        build = (root / "build-exe.bat").read_text(encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'APP_VERSION = "([^"]+)"', spec).group(1), APP_VERSION)
        expected_tuple = tuple(int(part) for part in APP_VERSION.split(".")) + (0, 0)
        self.assertEqual(tuple(int(part.strip()) for part in re.search(r"filevers=\(([^)]+)\)", version).group(1).split(",")), expected_tuple)
        self.assertEqual(tuple(int(part.strip()) for part in re.search(r"prodvers=\(([^)]+)\)", version).group(1).split(",")), expected_tuple)
        self.assertIn(f"StringStruct('FileVersion', '{APP_VERSION}.0.0')", version)
        self.assertIn(f"StringStruct('ProductVersion', '{APP_VERSION}.0.0')", version)
        self.assertIn(f"echo GluSpot {APP_VERSION} build is ready:", build)
        self.assertIn(f"release\\GluSpot-{APP_VERSION}\\GluSpot.exe", build)
        self.assertIn(f"Current test-build version: `{APP_VERSION}`.", readme)
        self.assertIn(f"release\\GluSpot-{APP_VERSION}\\GluSpot.exe", readme)

    def test_custom_icon_installer_creates_resolvable_png_and_ico(self):
        from PIL import Image

        from local.branding import APP_ICON_PATH, install_custom_icon, resolve_icon_paths

        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "picked.png"
            target_dir = Path(root) / "installed"
            Image.new("RGBA", (72, 48), (20, 120, 220, 255)).save(source)

            installed = install_custom_icon(source, target_dir=target_dir)

            png_path = Path(installed["png_path"])
            ico_path = Path(installed["ico_path"])
            self.assertTrue(png_path.exists())
            self.assertTrue(ico_path.exists())
            with Image.open(png_path) as png:
                self.assertEqual(png.size, (256, 256))
            with Image.open(ico_path) as ico:
                self.assertGreaterEqual(ico.size[0], 16)

            resolved = resolve_icon_paths(str(ico_path))
            self.assertEqual(resolved.app_icon, ico_path)
            self.assertEqual(resolved.tray_icon, ico_path)
            self.assertEqual(resolve_icon_paths("").app_icon, APP_ICON_PATH)

    def test_settings_paths_use_gluspot_and_keep_legacy_import_locations(self):
        from local.branding import legacy_app_data_dirs, settings_file_for
        from local.settings_store import APP_DATA_DIR, APP_NAME, SETTINGS_FILE

        self.assertEqual(APP_NAME, "GluSpot")
        self.assertTrue(APP_DATA_DIR.endswith(os.path.join("", "GluSpot")))
        self.assertTrue(SETTINGS_FILE.endswith(os.path.join("GluSpot", "settings.json")))

        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(settings_file_for(root), os.path.join(root, "GluSpot", "settings.json"))
            legacy_dirs = legacy_app_data_dirs(root)

        self.assertIn(os.path.join(root, "DynamicIsland"), legacy_dirs)
        self.assertIn(os.path.join(root, "gpt-island"), legacy_dirs)

    def test_runtime_branding_sources_do_not_expose_legacy_product_names(self):
        root = Path(__file__).resolve().parents[1]
        checked = [
            root / "api" / "__init__.py",
            root / "api" / "providers.py",
            root / "launch.bat",
            root / "local" / "__init__.py",
            root / "local" / "app.py",
            root / "local" / "island_stack_visual.py",
            root / "local" / "settings_store.py",
            root / "local" / "settings_window.py",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in checked)

        self.assertNotIn("gpt-island", combined)
        self.assertNotIn("DynamicIsland", combined)
        self.assertNotIn("Island Stack V2", combined)

    def test_app_uses_main_icon_and_distinct_tray_icon(self):
        source = Path(__file__).resolve().parents[1].joinpath("local", "app.py").read_text(encoding="utf-8")

        self.assertIn("resolve_icon_paths", source)
        self.assertIn("def _apply_global_icon", source)
        self.assertIn("icon_changed_callback = self._apply_global_icon", source)
        self.assertIn("setWindowIcon(app_icon)", source)
        self.assertIn("QSystemTrayIcon(QIcon(str(icon_paths.tray_icon))", source)
        self.assertIn("setApplicationName(APP_NAME)", source)
        self.assertIn("setToolTip(APP_NAME)", source)

    def test_windows_process_identity_uses_gluspot_app_id(self):
        import local.app as app_module

        calls: list[tuple[str, str]] = []
        fake_windll = SimpleNamespace(
            shell32=SimpleNamespace(
                SetCurrentProcessExplicitAppUserModelID=lambda value: calls.append(("app_id", value)) or 0,
            ),
            kernel32=SimpleNamespace(
                SetConsoleTitleW=lambda value: calls.append(("console", value)) or 1,
            ),
        )

        with patch.object(app_module.sys, "platform", "win32"), patch.object(app_module.ctypes, "windll", fake_windll, create=True):
            self.assertTrue(app_module._set_windows_process_identity())

        self.assertIn(("app_id", "GluSpot"), calls)
        self.assertIn(("console", "GluSpot"), calls)

    def test_windows_package_registers_qt_and_shiboken_dll_directories(self):
        root = Path(__file__).resolve().parents[1]
        spec = (root / "GluSpot.spec").read_text(encoding="utf-8")
        hook = root / "packaging" / "windows" / "runtime_qt_dlls.py"

        self.assertIn('runtime_qt_dlls.py', spec)
        self.assertTrue(hook.exists())
        source = hook.read_text(encoding="utf-8")
        self.assertIn('os.add_dll_directory', source)
        self.assertIn('pyside_dir / "Qt6Core.dll"', source)
        self.assertIn('shiboken_dir / "shiboken6.abi3.dll"', source)

    def test_windows_package_excludes_incompatible_host_icu(self):
        root = Path(__file__).resolve().parents[1]
        spec = (root / "GluSpot.spec").read_text(encoding="utf-8")

        self.assertIn("a.binaries = [", spec)
        self.assertIn('"icuuc.dll", "icudt78.dll"', spec)
        self.assertIn("Path(entry[0]).name.lower()", spec)


if __name__ == "__main__":
    unittest.main()
