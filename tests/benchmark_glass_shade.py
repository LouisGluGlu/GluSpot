"""Warmed, stage-by-stage glass shading benchmark against the frozen reference.

Run with QT_QPA_PLATFORM=offscreen python tests/benchmark_glass_shade.py.
The reference belongs to the regression suite and is not production code.
"""
import inspect
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage
from local import music_control_glass as glass
from local.glass_style import GLASS_CLEAR
from tests import glass_shade_reference


def benchmark(function, module):
    source = inspect.getsource(function)
    markers = [("crop_and_conversion", "    if not isinstance(pixels, Image.Image):"),
               ("format", "    pixels = np.asarray(pixels.filter("),
               ("blur", "    flat = pixels.reshape(-1)"),
               ("sampling", "    lum = "),
               ("color", "    np.clip(rgb," if function is glass._shade else "    rgb = np.clip("),
               ("light", "    rgba = np.empty("),
               ("pack", "    return QImage(")]
    source = source.replace("    geometry = control_geometry(*geometry_key)",
                            "    _start = _last = time.perf_counter()\n    geometry = control_geometry(*geometry_key)", 1)
    for name, marker in markers:
        if marker not in source:
            raise RuntimeError("missing stage marker: " + marker)
        source = source.replace(marker, f"    _now = time.perf_counter(); _times[{name!r}].append((_now - _last) * 1000); _last = _now\n" + marker, 1)
    source = source.replace("    return QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()",
                            "    result = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()\n    _now = time.perf_counter(); _times['qimage'].append((_now - _last) * 1000); _times['total'].append((_now - _start) * 1000)\n    return result")
    namespace = dict(vars(module), time=time, _times=defaultdict(list))
    exec(source, namespace)
    shade = namespace[function.__name__]
    scene = QImage(960, 540, QImage.Format.Format_RGB32)
    scene.fill(QColor(94, 121, 151))
    rect = QRectF(300, 200, 80, 80)
    key = (160, 160, 40., 2., GLASS_CLEAR, None)
    for i in range(350):
        shade(scene, QRectF(0, 0, 960, 540), rect, 2., GLASS_CLEAR.at_level(50), key, (255, 255, 255))
        if i == 49:
            namespace["_times"].clear()
    for name, values in namespace["_times"].items():
        print(f"  {name}: mean={statistics.mean(values):.3f} median={statistics.median(values):.3f} p95={sorted(values)[int(.95 * (len(values)-1))]:.3f}")


if __name__ == "__main__":
    for label, function, module in (("before", glass_shade_reference.reference_shade, glass_shade_reference),
                                     ("after", glass._shade, glass)):
        print(label)
        benchmark(function, module)
