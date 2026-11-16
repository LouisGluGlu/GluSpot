"""Lottie loops for the broadcast demo. No gradients and no highlights.

The official together clip is an opaque accent disc. The other two stay
flat white glass. A bright-blue placeholder marks colors the player replaces.

Qt 6 plays these through Quick VectorImage (the bundled Lottie plugin).
The files are written next to this module so the demo does not build them
on every launch.
"""
from __future__ import annotations

import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
OUT = HERE / "lottie"

CLIPS = (
    ("together", "涟漪张望"),
    ("rings", "涟漪"),
    ("gaze", "张望"),
)


def _hold(keys):
    """Ease between keyframes. The last key only marks the end."""
    frames = []
    for index, (time, value) in enumerate(keys):
        frame = {"t": time, "s": value}
        if index < len(keys) - 1:
            frame["i"] = {"x": [0.45], "y": [1]}
            frame["o"] = {"x": [0.55], "y": [0]}
        frames.append(frame)
    return {"a": 1, "k": frames}


def _still(value):
    return {"a": 0, "k": value}


def _layer(index, name, position, shapes, scale=None, opacity=None, end=120):
    return {
        "ddd": 0, "ind": index, "ty": 4, "nm": name, "sr": 1, "ao": 0, "bm": 0,
        "ip": 0, "op": end, "st": 0,
        "ks": {
            "o": opacity if opacity is not None else _still(100),
            "r": _still(0),
            "p": _still(position),
            "a": _still([0, 0, 0]),
            "s": scale if scale is not None else _still([100, 100, 100]),
        },
        "shapes": shapes,
    }


# Placeholder blue. The player replaces it with the opaque album accent.
BODY = [0.0, 0.5019607843, 1.0, 1.0]


def _disc(size, alpha, *, body=False):
    return [
        {"ty": "el", "nm": "disc", "d": 1, "p": _still([0, 0]), "s": _still([size, size])},
        {"ty": "fl", "nm": "fill", "c": _still(list(BODY) if body else [1, 1, 1, 1]),
         "o": _still(alpha), "r": 1},
    ]


def _eyes(alpha=92):
    return [
        {"ty": "rc", "nm": "left", "d": 1, "p": _still([-52, 0]), "s": _still([26, 64]), "r": _still(13)},
        {"ty": "rc", "nm": "right", "d": 1, "p": _still([52, 0]), "s": _still([26, 64]), "r": _still(13)},
        {"ty": "fl", "nm": "fill", "c": _still([1, 1, 1, 1]), "o": _still(alpha), "r": 1},
    ]


def _ring(size, *, body=False):
    return [
        {"ty": "el", "nm": "ring", "d": 1, "p": _still([0, 0]), "s": _still([size, size])},
        {"ty": "st", "nm": "stroke", "c": _still(list(BODY) if body else [1, 1, 1, 1]),
         "o": _still(100), "w": _still(7), "lc": 2, "lj": 2},
    ]


def _doc(name, frames, layers):
    return {
        "v": "5.7.4", "fr": 30, "ip": 0, "op": frames, "w": 512, "h": 512,
        "nm": name, "ddd": 0, "assets": [], "layers": layers,
    }


def breath():
    scale = _hold([(0, [92, 92, 100]), (60, [112, 112, 100]), (120, [92, 92, 100])])
    blink = _hold([
        (0, [100, 100, 100]), (86, [100, 100, 100]),
        (94, [100, 14, 100]), (104, [100, 100, 100]), (120, [100, 100, 100]),
    ])
    return _doc("breath", 120, [
        _layer(1, "eyes", [256, 242, 0], _eyes(), scale=blink),
        _layer(2, "glass", [256, 256, 0], _disc(312, 34), scale=scale),
    ])


def rings():
    swell = _hold([(0, [100, 100, 100]), (90, [168, 168, 100])])
    fade = _hold([(0, [0]), (10, [58]), (90, [0])])
    late = _hold([(0, [134, 134, 100]), (45, [168, 168, 100]), (46, [100, 100, 100]), (90, [134, 134, 100])])
    late_fade = _hold([(0, [24]), (45, [0]), (55, [58]), (90, [24])])
    return _doc("rings", 90, [
        _layer(1, "eyes", [256, 242, 0], _eyes(), end=90),
        _layer(2, "glass", [256, 256, 0], _disc(300, 32), end=90),
        _layer(3, "ring-a", [256, 256, 0], _ring(300), scale=swell, opacity=fade, end=90),
        _layer(4, "ring-b", [256, 256, 0], _ring(300), scale=late, opacity=late_fade, end=90),
    ])


def gaze():
    place = _hold([
        (0, [256, 242, 0]), (36, [228, 242, 0]),
        (84, [284, 242, 0]), (120, [256, 242, 0]),
    ])
    blink = _hold([
        (0, [100, 100, 100]), (54, [100, 100, 100]),
        (62, [100, 14, 100]), (70, [100, 100, 100]), (120, [100, 100, 100]),
    ])
    eye = _layer(2, "eyes", [256, 242, 0], _eyes(), scale=blink)
    eye["ks"]["p"] = place
    return _doc("gaze", 120, [
        eye,
        _layer(1, "glass", [256, 256, 0], _disc(312, 34)),
    ])


def together():
    """Rings fade outward while the eyes look side to side and blink once."""
    swell = _hold([(0, [100, 100, 100]), (120, [168, 168, 100])])
    fade = _hold([(0, [0]), (14, [62]), (120, [0])])
    late = _hold([
        (0, [134, 134, 100]), (60, [168, 168, 100]),
        (61, [100, 100, 100]), (120, [134, 134, 100]),
    ])
    late_fade = _hold([(0, [22]), (60, [0]), (74, [62]), (120, [22])])
    place = _hold([
        (0, [256, 242, 0]), (36, [228, 242, 0]),
        (84, [284, 242, 0]), (120, [256, 242, 0]),
    ])
    blink = _hold([
        (0, [100, 100, 100]), (54, [100, 100, 100]),
        (62, [100, 14, 100]), (70, [100, 100, 100]), (120, [100, 100, 100]),
    ])
    eye = _layer(1, "eyes", [256, 242, 0], _eyes(100), scale=blink)
    eye["ks"]["p"] = place
    return _doc("together", 120, [
        eye,
        _layer(2, "face", [256, 256, 0], _disc(312, 100, body=True)),
        _layer(3, "ring-a", [256, 256, 0], _ring(300, body=True), scale=swell, opacity=fade),
        _layer(4, "ring-b", [256, 256, 0], _ring(300, body=True), scale=late, opacity=late_fade),
    ])


def write_all() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    docs = {"together": together(), "rings": rings(), "gaze": gaze()}
    for name, doc in docs.items():
        (OUT / f"{name}.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    official = Path(__file__).resolve().parents[2] / "assets" / "broadcast"
    official.mkdir(parents=True, exist_ok=True)
    (official / "face.json").write_text(
        json.dumps(docs["together"], ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


if __name__ == "__main__":
    write_all()
