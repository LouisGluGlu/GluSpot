"""Native offline fixtures for the reference device-row interaction and layout."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from local.audio_output_panel import AudioOutputPanel


class _FixtureProvider:
    def __init__(self):
        self.calls = []
        self.snapshot = AudioRouteSnapshot(
            devices=(
                AudioRouteDevice("fixture-airpods", "无惧的 airpods pro", "airpods-pro", volume=1),
                AudioRouteDevice("fixture-half", "HomePod mini", "homepod-mini", "airplay", volume=.5),
                AudioRouteDevice("fixture-muted", "HomePod mini", "homepod-mini", "airplay", volume=.4, volume_muted=True),
                AudioRouteDevice("fixture-join", "HomePod mini", "homepod-mini", "airplay", volume=.3),
            ),
            state="casting", member_ids=("fixture-airpods", "fixture-half", "fixture-muted"),
            capture_scope="music_process_tree", group_id="offline-layout-fixture", cast_latency_seconds=1.5,
        )

    def get_audio_route_snapshot(self):
        return self.snapshot

    def refresh_audio_outputs(self):
        self.calls.append(("refresh",))
        return True


class _Backdrop(QWidget):
    def __init__(self, light):
        super().__init__()
        self.light = light

    def _background_sample_colors(self, rect):
        return (QColor("#efede8" if self.light else "#343536"),)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.fillRect(self.rect(), QColor("#efede8" if self.light else "#171819"))
            painter.setPen(QColor("#303034" if self.light else "#dddddf"))
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(QRectF(24, 5, self.width() - 48, 23), "GluSpot · OFFLINE UI FIXTURE")
            painter.drawText(QRectF(24, 32, self.width() / 2 - 24, 23), "未悬停")
            painter.drawText(QRectF(self.width() / 2 + 12, 32, self.width() / 2 - 24, 23), "参考图各行悬停状态")


def _settle(panel):
    panel._reveal, panel._reveal_velocity, panel._appearance_elapsed = 1, 0, 1
    panel._apply_reveal_pose()
    panel._animation_timer.stop()
    panel._poll_timer.stop()
    panel._clear_row_hover()
    for row in panel._rows.values():
        row.progress = 1 if row.state == "active" else 0
        row.elapsed = 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/audio-output-interaction-preview"))
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    if app.platformName() != "windows":
        raise RuntimeError("This preview must use the native Windows Qt platform.")
    evidence = {"platform": app.platformName(), "fixtures_only": True, "frames": []}
    for light in (False, True):
        for source_size in (42, 56):
            width = 620 if source_size == 42 else 780
            owner = _Backdrop(light)
            owner.resize(width, 430)
            owner.show()
            panels = []
            for right in (width // 2 - 22, width - 22):
                provider = _FixtureProvider()
                panel = AudioOutputPanel(provider, owner)
                panel.open_at(QPoint(right, 66), source_rect=QRectF(right - source_size, 66, source_size, source_size))
                _settle(panel)
                panels.append(panel)
            app.processEvents()
            left, right = panels
            for row in right._rows.values():
                row.set_details_visible(True)
            theme = "light" if light else "dark"
            prefix = f"{theme}-{source_size}"
            path = options.output / f"{prefix}-comparison.png"
            if not owner.grab().save(str(path)):
                raise RuntimeError(f"Could not save {path}")
            before = tuple(right.provider.calls)
            for row in right._rows.values():
                row.set_details_visible(False)
            selected = right._rows["fixture-muted"]
            point = QPoint(selected.width() - 17, selected.height() // 2)
            global_point = selected.mapToGlobal(point)
            event = QMouseEvent(QEvent.Type.MouseMove, QPointF(point), QPointF(global_point),
                                Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
            app.sendEvent(selected, event)
            QTest.qWait(520)
            if not selected.details_visible:
                raise AssertionError("Stationary half-second native row hover did not reveal volume.")
            native_hover = options.output / f"{prefix}-native-muted-hover.png"
            owner.grab().save(str(native_hover))
            unknown = replace(right.provider.snapshot.devices[2], volume=None, volume_muted=False)
            right.provider.snapshot = replace(right.provider.snapshot,
                devices=tuple(unknown if device.id == unknown.id else device for device in right.provider.snapshot.devices))
            right.sync_snapshot()
            selected.set_details_visible(True)
            if selected.displayed_volume is not None:
                raise AssertionError("Unknown-volume fixture invented a percentage.")
            owner.grab().save(str(options.output / f"{prefix}-unknown-volume.png"))
            if tuple(right.provider.calls) != before:
                raise AssertionError("Hover/paint unexpectedly invoked an output operation.")
            for panel in panels:
                if panel.x() < 0 or panel.y() + panel.height() > owner.height() - 12:
                    raise AssertionError("Native chooser escaped its window bounds.")
                if panel.scroll.horizontalScrollBar().maximum() != 0:
                    raise AssertionError("Device-row layout introduced horizontal scrolling.")
            evidence["frames"].append({
                "comparison": str(path.resolve()), "native_hover": str(native_hover.resolve()),
                "theme": theme, "source_size": source_size, "device_pixel_ratio": owner.devicePixelRatioF(),
                "panel_size": [right.width(), right.height()], "row_size": [selected.width(), selected.height()],
                "known_percentages": [100, 50, 0], "unknown_percentage": None,
                "horizontal_scroll_max": right.scroll.horizontalScrollBar().maximum(),
            })
            for panel in panels:
                panel.dismiss(immediate=True)
            owner.close()
            app.processEvents()
    (options.output / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"platform": app.platformName(), "frames": len(evidence["frames"]),
                      "output": str(options.output.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
