"""Fetch and verify the exact unmodified LGPL source for redistribution."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


def main():
    version = "0.151.5"
    root = Path(__file__).resolve().parents[2]
    target = root / "build" / "audio-licenses"
    target.mkdir(parents=True, exist_ok=True)
    with urlopen(f"https://pypi.org/pypi/zeroconf/{version}/json", timeout=30) as response:
        metadata = json.load(response)
    source = next(item for item in metadata["urls"] if item["packagetype"] == "sdist")
    expected = source["digests"]["sha256"]
    destination = target / Path(source["filename"]).name
    if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
        with urlopen(source["url"], timeout=30) as response:
            content = response.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != expected:
            raise RuntimeError("Zeroconf source archive integrity verification failed")
        destination.write_bytes(content)
    (target / "SOURCE.json").write_text(json.dumps({"project": "python-zeroconf", "version": version,
        "url": source["url"], "sha256": expected}, indent=2) + "\n", encoding="utf-8")
    (target / "README.txt").write_text(
        "python-zeroconf 0.151.5 is unmodified and licensed LGPL-2.1-or-later.\n"
        "Its exact source distribution is included here; COPYING is in the archive\n"
        "and the zeroconf dist-info/licenses directory. The onedir package keeps\n"
        "zeroconf Python files and extension modules outside PYZ under _internal.\n"
        "You may replace/rebuild this library with a compatible version, subject\n"
        "to its license. GluSpot does not forbid reverse engineering for debugging\n"
        "modifications to this LGPL library. Do not remove its license notices.\n",
        encoding="utf-8")
    print(f"Verified LGPL source: {destination}")


if __name__ == "__main__":
    main()
