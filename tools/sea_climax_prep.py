"""Gather songs for the sea-level climax evaluation (not shipped).

Reads the NetEase client's own play cache (read only), keeps complete
files, and fetches each song's title, timed lyric and NetEase chorus time
with the same anonymous requests the app already makes. Nothing about the
listener's account is sent or stored.

    python tools/sea_climax_prep.py list [count]     # candidates as JSON lines
    python tools/sea_climax_prep.py fetch id [id...] # lyric + chorus into tmp/sea_climax_eval
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

CACHE = Path(os.environ.get("LOCALAPPDATA", "")) / "NetEase" / "CloudMusic" / "Cache" / "Cache"
OUT = Path(__file__).resolve().parents[1] / "tmp" / "sea_climax_eval"
HEADERS = {"Referer": "https://music.163.com/", "Origin": "https://music.163.com",
           "User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"}


def post(url: str, data: dict) -> dict:
    request = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers=HEADERS, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def complete_files() -> dict[str, Path]:
    """song id -> fully cached .uc file."""
    out = {}
    for index in CACHE.glob("*.idx"):
        try:
            info = json.loads(index.read_text(encoding="utf-8", errors="replace"))
            size = int(info.get("size") or 0)
            zone = info.get("zone") or []
        except (OSError, ValueError):
            continue
        audio = index.with_suffix(".uc")
        if size and zone == [f"0 {size - 1}"] and audio.exists() and audio.stat().st_size == size:
            out.setdefault(index.name.split("-")[0], audio)
    return out


def details(ids) -> dict[str, dict]:
    out = {}
    ids = list(ids)
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        body = post("https://music.163.com/api/song/detail/", {"ids": "[" + ",".join(chunk) + "]"})
        for song in body.get("songs") or ():
            out[str(song["id"])] = {"title": song.get("name"), "artist": ",".join(a["name"] for a in song.get("artists") or ()),
                                    "duration": (song.get("duration") or 0) / 1000.0}
        time.sleep(0.3)
    return out


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "list"
    if command == "list":
        count = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        files = complete_files()
        ids = sorted(files)
        random.Random(7).shuffle(ids)
        for song_id, meta in details(ids[:count]).items():
            print(json.dumps(dict(meta, id=song_id, file=str(files[song_id])), ensure_ascii=False))
        return
    OUT.mkdir(parents=True, exist_ok=True)
    files = complete_files()
    meta = details(sys.argv[2:])
    for song_id in sys.argv[2:]:
        lyric = post("https://music.163.com/api/song/lyric", {"id": song_id, "lv": "-1", "kv": "-1", "tv": "-1"})
        chorus = post("https://music.163.com/api/song/chorus", {"ids": json.dumps([int(song_id)])})
        record = dict(meta.get(song_id, {}), id=song_id, file=str(files.get(song_id, "")),
                      lrc=(lyric.get("lrc") or {}).get("lyric") or "",
                      chorus=[(c.get("startTime"), c.get("endTime")) for c in chorus.get("chorus") or ()])
        (OUT / f"{song_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        print(song_id, record.get("title"), "-", record.get("artist"), record["chorus"], len(record["lrc"]))
        time.sleep(0.3)


if __name__ == "__main__":
    main()
