from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


APP_NAME = "GluSpot"
APP_VERSION = "6.412"
ROOT_DIR = Path(__file__).resolve().parents[1]
BRAND_ASSET_DIR = ROOT_DIR / "assets" / "brand"
APP_ICON_PATH = BRAND_ASSET_DIR / "gluspot-glu.ico"
TRAY_ICON_PATH = APP_ICON_PATH
LEGACY_APP_NAMES = ("DynamicIsland", "gpt-island")
CUSTOM_ICON_PNG_NAME = "custom-icon.png"
CUSTOM_ICON_ICO_NAME = "custom-icon.ico"


def app_data_dir(base_dir: str | os.PathLike[str] | None = None) -> str:
    root = str(base_dir or os.environ.get("APPDATA") or ROOT_DIR)
    return os.path.join(root, APP_NAME)


def local_app_data_dir(base_dir: str | os.PathLike[str] | None = None) -> str:
    root = str(base_dir or os.environ.get("LOCALAPPDATA") or ROOT_DIR)
    return os.path.join(root, APP_NAME)


def settings_file_for(base_dir: str | os.PathLike[str] | None = None) -> str:
    return os.path.join(app_data_dir(base_dir), "settings.json")


def legacy_app_data_dirs(base_dir: str | os.PathLike[str] | None = None) -> list[str]:
    root = str(base_dir or os.environ.get("APPDATA") or ROOT_DIR)
    return [os.path.join(root, name) for name in LEGACY_APP_NAMES]


@dataclass(frozen=True)
class IconPaths:
    app_icon: Path
    tray_icon: Path
    preview_icon: Path


def custom_icon_dir(base_dir: str | os.PathLike[str] | None = None) -> Path:
    return Path(app_data_dir(base_dir)) / "icons"


def resolve_icon_paths(custom_icon_path: str | os.PathLike[str] | None = None) -> IconPaths:
    raw_path = str(custom_icon_path or "").strip().strip('"')
    if raw_path:
        icon_path = Path(raw_path)
    else:
        icon_path = None
    if icon_path is not None and icon_path.exists():
        preview_path = icon_path.with_suffix(".png")
        if not preview_path.exists():
            preview_path = icon_path
        return IconPaths(app_icon=icon_path, tray_icon=icon_path, preview_icon=preview_path)
    return IconPaths(app_icon=APP_ICON_PATH, tray_icon=TRAY_ICON_PATH, preview_icon=BRAND_ASSET_DIR / "gluspot-glu-icon-256.png")


def install_custom_icon(source_path: str | os.PathLike[str], target_dir: str | os.PathLike[str] | None = None) -> dict[str, str]:
    from PIL import Image

    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(source)
    destination = Path(target_dir) if target_dir is not None else custom_icon_dir()
    destination.mkdir(parents=True, exist_ok=True)

    image = Image.open(source).convert("RGBA")
    image.thumbnail((256, 256), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (256, 256), (255, 255, 255, 0))
    left = (256 - image.width) // 2
    top = (256 - image.height) // 2
    canvas.alpha_composite(image, (left, top))

    png_path = destination / CUSTOM_ICON_PNG_NAME
    ico_path = destination / CUSTOM_ICON_ICO_NAME
    canvas.save(png_path)
    canvas.save(
        ico_path,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    return {"png_path": str(png_path), "ico_path": str(ico_path)}
