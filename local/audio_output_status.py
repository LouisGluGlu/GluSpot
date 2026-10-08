"""Confirmed output glyphs derived from the provider's immutable route state."""
from __future__ import annotations

from api.audio_routes import AudioRouteSnapshot


def connected_output_kinds(snapshot: AudioRouteSnapshot | None) -> tuple[str, ...]:
    """Keep connection targets and capture endpoints out of the playing badge.

    Missing discovery metadata does not remove a confirmed member: it gets a
    generic device glyph until its explicit model evidence returns. Errors and
    initial connection states must never present an old group as connected.
    """
    if not isinstance(snapshot, AudioRouteSnapshot) or snapshot.state == "error":
        return ()
    pending = snapshot.pending_ids or ((snapshot.pending_id,) if snapshot.pending_id else ())
    if snapshot.member_ids and snapshot.state in {"casting", "waiting_audio"}:
        identities = snapshot.member_ids
    elif snapshot.active_id and not pending:
        identities = (snapshot.active_id,)
    else:
        return ()
    devices = {device.id: device for device in snapshot.devices}
    kinds = []
    seen = set()
    for identity in identities:
        if not identity or identity == snapshot.cast_source_id or identity in seen:
            continue
        seen.add(identity)
        device = devices.get(identity)
        if device is None:
            kinds.append("device")
            continue
        if device.transport == "airplay" and snapshot.state not in {"casting", "waiting_audio"}:
            continue
        kinds.append(device.kind or ("airplay" if device.transport == "airplay" else "device"))
    return tuple(kinds)
