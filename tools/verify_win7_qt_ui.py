"""Verify the build-only Qt 5 facade against GluSpot's actual UI interfaces.

Run with the legacy Python environment and legacy_qt on PYTHONPATH. The render
check uses only offline fixtures and hidden widgets; it does not load settings,
create accounts, play audio, or contact receivers. This is a Qt 5 check on the
current host, not evidence of a real Windows 7 run.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import json
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def audit_interfaces():
    failures, symbols, modules = [], set(), set()
    for folder in ("local", "api"):
        for path in (ROOT / folder).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            bindings = {}
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith("PySide6"):
                    continue
                modules.add(path.relative_to(ROOT).with_suffix("").as_posix().replace("/", "."))
                try:
                    module = importlib.import_module(node.module)
                    for alias in node.names:
                        value = getattr(module, alias.name, None)
                        if value is None and node.module == "PySide6":
                            value = importlib.import_module(node.module + "." + alias.name)
                        elif value is None:
                            raise AttributeError(node.module + "." + alias.name)
                        bindings[alias.asname or alias.name] = value
                        symbols.add(node.module + "." + alias.name)
                except Exception as exc:
                    failures.append([str(path.relative_to(ROOT)), "import", str(exc)])
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                parts = []
                while isinstance(node, ast.Attribute):
                    parts.insert(0, node.attr)
                    node = node.value
                if not isinstance(node, ast.Name) or node.id not in bindings:
                    continue
                value = bindings[node.id]
                try:
                    for part in parts:
                        value = getattr(value, part)
                    symbols.add(node.id + "." + ".".join(parts))
                except AttributeError as exc:
                    failures.append([str(path.relative_to(ROOT)), "symbol", str(exc)])
    for module in sorted(modules):
        try:
            importlib.import_module(module)
        except Exception:
            failures.append([module, "load", traceback.format_exc()])
    return {"modules": len(modules), "interfaces": len(symbols), "failures": failures}


def render_interfaces(output):
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QMouseEvent, QPainter
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    from local.playback_entry_window import PlaybackEntryWindow
    from local.music_library_window import MusicLibraryWindow
    from local.music_surface_window import MusicSurfaceWindow
    from local.island_stack_visual import IslandStackVisual
    from api.providers import EmptyIslandDataProvider
    from tools.preview_single_hue_background import _capture

    app = QApplication.instance() or QApplication([])
    callbacks = []
    previous_hook = sys.excepthook
    sys.excepthook = lambda typ, value, tb: callbacks.append("".join(traceback.format_exception(typ, value, tb)))
    results = {"frames": [], "native_pointer_positions": [], "callback_failures": callbacks}
    widgets = []
    try:
        class PointerProbe(QWidget):
            def mousePressEvent(self, event):
                point = event.position()
                global_point = event.globalPosition()
                results["native_pointer_positions"].append([point.x(), point.y(), global_point.x(), global_point.y()])

        probe = PointerProbe()
        widgets.append(probe)
        probe.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        probe.resize(80, 60)
        probe.show()
        app.processEvents()
        QTest.mouseClick(probe, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(17, 23))
        mapped = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(11, 19), QPointF(111, 119),
                             Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        app.sendEvent(probe, mapped)
        assert [entry[:2] for entry in results["native_pointer_positions"]] == [[17., 23.], [11., 19.]]
        assert results["native_pointer_positions"][1][2:] == [111., 119.]
        families = QFontDatabase.families()
        assert families and QFontDatabase.writingSystems(families[0])
        results["font_family_count"] = len(families)
        image = QImage(40, 40, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            with painter:
                painter.fillRect(image.rect(), QColor("#23a78c"))
                raise ValueError("verify painter cleanup")
        except ValueError:
            pass
        assert not painter.isActive() and image.pixelColor(20, 20) == QColor("#23a78c")
        font = QFont()
        font.setFamilies(["Missing GluSpot Acceptance Font", "Segoe UI", "Microsoft YaHei"])
        font.setWeight(QFont.Weight.Bold)
        results["native_font_weight"] = font.weight()
        for name, factory in (("island", lambda: IslandStackVisual(EmptyIslandDataProvider())),
                              ("entry", PlaybackEntryWindow), ("library", MusicLibraryWindow)):
            window = factory()
            widgets.append(window)
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            if name == "island":
                window.timer.stop()
            window.show()
            app.processEvents()
            frame = window.grab().toImage()
            assert not frame.isNull()
            assert frame.save(str(output / (name + ".png")))
            results["frames"].append({"name": name, "width": frame.width(), "height": frame.height(),
                                      "dpr": frame.devicePixelRatio(), "hidden_native_widget": True})
            window.close()
        colors = (QColor("#5f548c"), QColor("#ce795f"), QColor("#265e83"))
        def hidden_music_surface(owner):
            window = MusicSurfaceWindow(owner)
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            return window

        with patch("tools.preview_single_hue_background.MusicSurfaceWindow", side_effect=hidden_music_surface):
            frame = _capture(app, "Qt 5 compatibility fixture", colors, False)
        assert not frame.isNull() and frame.save(str(output / "music-surface.png"))
        results["frames"].append({"name": "music-surface", "width": frame.width(), "height": frame.height(),
                                  "dpr": frame.devicePixelRatio(), "synthetic_ui_fixture": True,
                                  "hidden_native_widget": True})
        assert not callbacks, callbacks
    finally:
        for widget in reversed(widgets):
            widget.close()
            widget.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        sys.excepthook = previous_hook
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/win7-qt-ui")
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    import PySide6
    if not getattr(PySide6, "__legacy_qt5__", False):
        parser.error("Use the dedicated legacy environment and legacy_qt import path")
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"qt_binding": PySide6.__version__, "python": sys.version, "imports": audit_interfaces()}
    try:
        if args.render and not result["imports"]["failures"]:
            result["render"] = render_interfaces(args.output)
    except Exception:
        result["render_failure"] = traceback.format_exc()
    (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return bool(result["imports"]["failures"] or result.get("render_failure"))


if __name__ == "__main__":
    raise SystemExit(main())
