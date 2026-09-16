from __future__ import annotations

from dataclasses import dataclass
import hashlib


@dataclass(frozen=True)
class AlbumArtUpdatePlan:
    action: str
    reject_black_placeholder: bool = False
    clear_scaled_cache: bool = False
    next_signature: str = ""
    reason: str = ""

    @property
    def should_set_loaded_cover(self) -> bool:
        return self.action == "set"

    @property
    def should_clear_current_cover(self) -> bool:
        return self.action == "clear"


def album_art_bytes_signature(data: object) -> str:
    if not isinstance(data, bytes) or not data:
        return ""
    return hashlib.blake2b(data, digest_size=8).hexdigest()


def album_art_update_plan(
    *,
    incoming_signature: object,
    current_signature: object,
    cover_loaded: bool,
    black_placeholder_loaded: bool,
    has_existing_cover: bool,
    track_changed: bool,
    same_visible_track: bool,
    message_refresh: bool,
) -> AlbumArtUpdatePlan:
    incoming = str(incoming_signature or "")
    current = str(current_signature or "")
    reused = bool(incoming and incoming == current and has_existing_cover)
    if reused:
        return AlbumArtUpdatePlan(action="reuse", next_signature=current, reason="same_signature")

    unstable_refresh = bool((not track_changed) or same_visible_track or message_refresh)
    reject_black = bool(cover_loaded and black_placeholder_loaded and unstable_refresh)
    if reject_black:
        if has_existing_cover:
            return AlbumArtUpdatePlan(
                action="preserve",
                reject_black_placeholder=True,
                next_signature=current,
                reason="black_placeholder_preserve_existing",
            )
        return AlbumArtUpdatePlan(
            action="ignore",
            reject_black_placeholder=True,
            reason="black_placeholder_without_existing",
        )

    if cover_loaded:
        return AlbumArtUpdatePlan(
            action="set",
            clear_scaled_cache=True,
            next_signature=incoming,
            reason="loaded_cover",
        )

    if has_existing_cover and (not track_changed or same_visible_track):
        return AlbumArtUpdatePlan(action="preserve", next_signature=current, reason="missing_cover_preserve_existing")

    if track_changed:
        return AlbumArtUpdatePlan(action="clear", clear_scaled_cache=True, reason="track_changed_without_cover")

    return AlbumArtUpdatePlan(action="ignore", next_signature=current, reason="no_cover_change")
