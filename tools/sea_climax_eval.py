"""Score the sea level's colour-change picker against hand-marked choruses (not shipped).

    python tools/sea_climax_eval.py

Needs tools/sea_climax_prep.py fetch + tools/sea_climax_audio.py run first
(lyrics, NetEase chorus times and 8 kHz PCM under tmp/sea_climax_eval).
Prints, per song, the old pick (fullness only), each new source and the
layered pick, with the error in seconds against the marked chorus start.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from local import sea_climax  # noqa: E402
from local.sea_lyric_background import _drums  # noqa: E402

DATA = ROOT / "tmp" / "sea_climax_eval"
HIT_S = 2.0          # a pick this close to the marked start counts as right


def parse_lrc(text: str):
    out = []
    for line in text.splitlines():
        stamps = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", line)
        words = re.sub(r"\[[^\]]*\]", "", line).strip()
        for minutes, seconds in stamps:
            out.append((int(minutes) * 60 + float(seconds), words))
    return sorted(out)


def old_arrival(pcm: np.ndarray, drums) -> float | None:
    """The picker before this change, verbatim: where the arrangement gets
    fuller and stays full, snapped to the nearest drum hit."""
    RATE = 8000
    step, window = 0.5, 1024
    hop = int(RATE * step)
    count = max(0, (len(pcm) - window) // hop)
    if count < 120:
        return None
    index = np.arange(window)[None, :] + hop * np.arange(count)[:, None]
    frames = pcm[index] * np.hanning(window).astype(np.float32)[None, :]
    spectrum = np.abs(np.fft.rfft(frames, axis=1)) + 1e-9
    freqs = np.fft.rfftfreq(window, 1.0 / RATE)
    loud = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-6)
    low = 20 * np.log10(spectrum[:, freqs < 150.0].mean(axis=1))
    bands = np.array_split(np.arange(spectrum.shape[1]), 24)
    band_db = np.stack([20 * np.log10(spectrum[:, b].mean(axis=1)) for b in bands], axis=1)
    filled = (band_db > np.percentile(band_db, 40)).mean(axis=1)
    drum_times = np.array([moment for moment, _strength in drums])
    times = np.arange(count) * step
    density = np.array([((drum_times >= t - 1.0) & (drum_times < t + 1.0)).sum() / 2.0 for t in times])
    feats = np.stack([loud, low, density, filled], axis=1)
    feats = (feats - feats.mean(axis=0)) / (feats.std(axis=0) + 1e-6)
    full = feats[:, :2].mean(axis=1) * 0.5 + feats[:, 2] * 0.3 + feats[:, 3] * 0.2
    span, build = int(30 / step), int(15 / step)
    best, best_score, best_uplift = None, -1e9, 0.0
    for i in range(int(count * 0.15), int(count * 0.75)):
        before, after = full[max(0, i - span): i], full[i: i + span]
        if len(before) < span * 0.5 or len(after) < span * 0.5:
            continue
        uplift = float(after.mean() - before.mean())
        held = float((after > before.mean() + 0.5 * before.std()).mean())
        jump = float(full[i: i + 8].mean() - full[max(0, i - 8): i].mean())
        dens = feats[max(0, i - build): i, 2]
        slope = float(np.polyfit(np.arange(len(dens)), dens, 1)[0] * len(dens)) if len(dens) > 4 else 0.0
        score = uplift * held + 0.6 * jump + 0.3 * max(0.0, slope)
        if score > best_score:
            best, best_score, best_uplift = i * step, score, uplift
    # Every song changes once. A song without a clear lift still gets its
    # best-scoring moment rather than no change at all.
    if best is None:
        return None
    near = drum_times[np.abs(drum_times - best) <= 1.0] if drum_times.size else drum_times
    if near.size:
        best = float(near[np.argmin(np.abs(near - best))])
    return float(best)


def fmt(value, truth):
    if value is None:
        return "     -        "
    return f"{value:6.1f} ({value - truth:+6.1f})"


def main() -> None:
    labels = json.loads((ROOT / "tools" / "sea_climax_labels.json").read_text(encoding="utf-8"))
    rows, timings = [], []
    print(f"{'song':28} {'marked':>6} | {'old':^14} | {'netease':^14} | {'lyrics':^14} | {'audio':^14} | {'new':^14} src")
    for song in labels["songs"]:
        record = json.loads((DATA / f"{song['id']}.json").read_text(encoding="utf-8"))
        pcm = np.load(DATA / f"{song['id']}.npy")
        truth = float(song["chorus"])
        onsets = _drums(pcm)
        old = old_arrival(pcm, onsets)
        netease = record["chorus"][0][0] / 1000.0 if record.get("chorus") else None
        lyrics = parse_lrc(record.get("lrc") or "")
        began = time.perf_counter()
        found = sea_climax.candidates(pcm, onsets, lyrics=lyrics, netease=netease, audio=True)
        new, source = sea_climax.choose(found, onsets)
        timings.append(time.perf_counter() - began)
        # The shipped order with no lyric (Apple songs, missing lyrics) too.
        bare, bare_source = sea_climax.choose(dict(found, lyrics=None, section=None), onsets)
        rows.append((song, truth, old, found, new, bare))
        print(f"{song['title'][:28]:28} {truth:6.1f} | {fmt(old, truth)} | {fmt(found['netease'], truth)} | "
              f"{fmt(found['lyrics'], truth)} | {fmt(found['audio'], truth)} | {fmt(new, truth)} {source}"
              f"   [no lyric: {fmt(bare, truth)} {bare_source}]")

    def summary(name, values):
        errors = [abs(v - t) for v, t in values if v is not None]
        missing = sum(1 for v, _t in values if v is None)
        hits = sum(1 for e in errors if e <= HIT_S)
        median = float(np.median(errors)) if errors else float("nan")
        print(f"{name:22} hits(<= {HIT_S:.0f}s) {hits:2d}/{len(values)}  median err {median:5.1f}s  "
              f"mean err {np.mean(errors) if errors else float('nan'):5.1f}s  none {missing}")

    print()
    summary("old (fullness)", [(old, t) for _s, t, old, _f, _n, _b in rows])
    summary("netease alone", [(f["netease"], t) for _s, t, _o, f, _n, _b in rows])
    summary("lyrics alone", [(f["lyrics"], t) for _s, t, _o, f, _n, _b in rows])
    summary("audio alone", [(f["audio"], t) for _s, t, _o, f, _n, _b in rows])
    summary("new (layered)", [(n, t) for _s, t, _o, _f, n, _b in rows])
    summary("new, no lyric", [(b, t) for _s, t, _o, _f, _n, b in rows])
    print(f"\npicker time per song: median {np.median(timings) * 1000:.0f} ms, max {max(timings) * 1000:.0f} ms"
          " (all sources, audio always on)")


if __name__ == "__main__":
    main()
