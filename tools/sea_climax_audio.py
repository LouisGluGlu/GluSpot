"""8 kHz mono PCM for the climax evaluation songs, cached as .npy (not shipped).

The NetEase client cache stores each song XOR 0xA3. The file is unmasked
into tmp/ and decoded with the app's own QAudioDecoder path.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp" / "sea_climax_eval"
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pcm_for(song_id: str, cache_file: str) -> np.ndarray | None:
    target = OUT / f"{song_id}.npy"
    if target.exists():
        return np.load(target)
    from PySide6.QtCore import QCoreApplication
    from local.sea_lyric_background import _DecodeThread
    _app = QCoreApplication.instance() or QCoreApplication([])
    raw = np.fromfile(cache_file, dtype=np.uint8) ^ 0xA3
    kind = ".flac" if raw[:4].tobytes() == b"fLaC" else ".mp3"
    plain = OUT / f"{song_id}{kind}"
    raw.tofile(plain)
    try:
        worker = _DecodeThread(str(plain))
        worker.start()
        worker.wait(180000)
        pcm = worker.pcm
    finally:
        plain.unlink(missing_ok=True)
    if pcm is None or pcm.size < 8000 * 30:
        return None
    np.save(target, pcm)
    return pcm


if __name__ == "__main__":
    import json
    for path in sorted(OUT.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        pcm = pcm_for(record["id"], record["file"])
        print(record["id"], record.get("title"), None if pcm is None else round(pcm.size / 8000.0, 1))
