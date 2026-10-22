"""Package generated scene assets without cropping or altering their pixels."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--backup", type=Path, required=True)
args = parser.parse_args()
out = Path(__file__).resolve().parents[1] / "public/assets"
args.backup.mkdir(parents=True, exist_ok=True)
records = []
for name in ("desktop-wallpaper", "room-devices-off", "room-small-on", "room-large-on", "room-devices-on", "speaker-waves", "speaker-top-light"):
    source = args.source / f"{name}.png"
    shutil.copy2(source, args.backup / source.name)
    with Image.open(source) as image:
        image.load()
        target = out / f"{name}.webp"
        image.save(target, format="WEBP", quality=91, method=6)
        records.append({"file": target.name, "size": list(image.size), "mode": image.mode,
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "description": "Generated website illustration; no user desktop or personal data"})
(out / "scene-provenance.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
shutil.copy2(out / "scene-provenance.json", args.backup / "scene-provenance.json")
print(json.dumps({"saved": len(records), "web_bytes": sum((out / row["file"]).stat().st_size for row in records)}))
