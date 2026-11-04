"""Bilingual song-page typography with A as the product default."""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Callable

from PySide6.QtGui import QFont


@dataclass(frozen=True)
class TranslationStyle:
    name: str
    size_ratio: float
    weight: QFont.Weight | int
    alpha_active: float
    alpha_inactive: float
    gap_px_fn: Callable[[float, float], float]
    blur_factor: float
    karaoke_on_translation: bool
    opacity_stagger_s: float
    entry_duration_s: float
    slide_factor: float
    inactive_scale: float


CLASSIC = TranslationStyle(
    "classic", 16.0 / 38.0, QFont.Weight.Normal, 1.0, 1.0,
    lambda main_px, scale: max(4.0, main_px * 0.12),
    1.0, True, 0.0, 0.48, 0.55, 1.0,
)
A = TranslationStyle(
    "a", 0.50, QFont.Weight.Normal, 0.30, 0.18,
    lambda main_px, scale: 0.30 * main_px,
    1.0, False, 0.25, 0.40, 0.35, 0.97,
)
B = TranslationStyle(
    "b", 0.62, QFont.Weight.Normal, 0.78, 0.42,
    lambda main_px, scale: 8.0 * scale,
    0.50, False, 0.0, 0.32, 0.25, 0.99,
)
C = TranslationStyle(
    "c", 0.55, QFont.Weight.Normal, 0.55, 0.28,
    lambda main_px, scale: max(4.0, 0.22 * main_px),
    0.70, False, 0.08, 0.42, 0.40, 0.98,
)


def resolve_translation_style() -> TranslationStyle:
    """Resolve once per surface; unset, empty and unknown values use CLASSIC."""
    name = os.environ.get("GLUSPOT_TRANSLATION_VARIANT", "").strip().lower()
    return {"a": A, "b": B, "c": C, "classic": CLASSIC}.get(name, CLASSIC)
