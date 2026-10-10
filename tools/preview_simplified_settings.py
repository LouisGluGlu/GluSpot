"""Render native settings pages with offline fixtures and inspect their bounds."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QFrame, QLabel

from local.settings_window import SettingsWindow
from local.ui_theme import set_theme_settings_getter
from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/settings-simplification-preview"))
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    evidence = {"platform": QGuiApplication.platformName(), "pages": []}
    for dark in (False, True):
        theme = "dark" if dark else "light"
        settings = FakeSettingsManager()
        settings.values.update(v2_theme_mode=theme, v2_user_id="UI FIXTURE")
        set_theme_settings_getter(settings.get)
        owner = FakeIslandWidget()
        owner.location_mode = "top"
        with patch("local.mac_style.app_dark_mode", return_value=dark), \
                patch("local.settings_window.app_dark_mode", return_value=dark):
            window = SettingsWindow(owner, settings)
            try:
                for size in ((688, 496), (900, 650)):
                    window.resize(*size)
                    window.show()
                    app.processEvents()
                    for index, entry in enumerate(window._settings_pages):
                        window.nav.setCurrentRow(index)
                        window.sync_from_widget()
                        app.processEvents()
                        page = entry["page"]
                        page.verticalScrollBar().setValue(0)
                        app.processEvents()
                        filename = f"{theme}-{size[0]}-page-{index}.png"
                        window.grab().save(str(options.output / filename))
                        evidence["pages"].append({
                            "image": filename, "title": entry["title"],
                            "viewport_width": page.viewport().width(), "body_width": page.widget().width(),
                            "cards": len(page.widget().findChildren(QFrame, "MacCard")),
                            "dpr": window.devicePixelRatioF(),
                        })
                        if index == 1:
                            window.mydockfinder_listener_detail_toggle.setChecked(True)
                            page.ensureWidgetVisible(window.mydockfinder_listener_detail_body)
                            app.processEvents()
                            window.grab().save(str(options.output / f"{theme}-{size[0]}-mydockfinder.png"))
                            page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
                            app.processEvents()
                            window.grab().save(str(options.output / f"{theme}-{size[0]}-media-bottom.png"))
                        if index == 2:
                            page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
                            app.processEvents()
                            window.grab().save(str(options.output / f"{theme}-{size[0]}-secondary-content.png"))
                        if index == 3:
                            toggle, _body = next(pair for pair in window._detail_panels if "软件诊断" in pair[0].text())
                            toggle.setChecked(True)
                            app.processEvents()
                            page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
                            app.processEvents()
                            window.grab().save(str(options.output / f"{theme}-{size[0]}-diagnostics.png"))
                    for query in ("MyDockFinder 应用位置", "背景样式", "通知设置", "字体导入"):
                        window.search_input.setText(query)
                        app.processEvents()
                        titles = [label.text() for label in window.settings_search_page.findChildren(QLabel)
                                  if label.objectName() == "SearchResultTitle"]
                        evidence.setdefault("searches", []).append({"query": query, "titles": titles, "theme": theme, "size": size})
                        if query == "背景样式":
                            window.grab().save(str(options.output / f"{theme}-{size[0]}-search.png"))
                    window.search_input.clear()
            finally:
                window.close()
                window.deleteLater()
                owner.close()
                owner.deleteLater()
                app.processEvents()
        set_theme_settings_getter(None)
    (options.output / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    overflows = [row for row in evidence["pages"] if row["body_width"] > row["viewport_width"]]
    print(json.dumps({"platform": evidence["platform"], "pages": len(evidence["pages"]), "overflows": overflows}, ensure_ascii=False))
    return bool(overflows)


if __name__ == "__main__":
    raise SystemExit(main())
