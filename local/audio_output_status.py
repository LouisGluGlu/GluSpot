"""Confirmed output glyphs derived from the provider's immutable route state."""
from __future__ import annotations

from dataclasses import dataclass

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot


_CONNECTED_KINDS = frozenset({
    "speaker", "homepod", "homepod-mini", "airpods-pro", "headphones", "airplay", "device",
})


def _member_kind(kind: str, transport: str = "") -> str:
    key = str(kind or "").lower().replace("_", "-")
    key = {"homepodmini": "homepod-mini", "speakers": "speaker", "headphone": "headphones",
           "headset": "headphones", "earphone": "headphones", "earphones": "headphones",
           "earbuds": "headphones", "airpods": "headphones"}.get(key, key)
    if not key:
        key = "airplay" if transport == "airplay" else "device"
    return key if key in _CONNECTED_KINDS else "device"


@dataclass(frozen=True)
class OutputMember:
    """One confirmed output; model evidence is never inferred from its name."""

    id: str
    name: str
    kind: str

    @classmethod
    def from_device(cls, device: AudioRouteDevice) -> "OutputMember":
        return cls(device.id, device.name or "未识别设备", _member_kind(device.kind, device.transport))


@dataclass(frozen=True)
class ConnectedOutputStatus:
    """Connection-ordered members and their first two distinct visible kinds."""

    members: tuple[OutputMember, ...] = ()

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(member.kind for member in self.members)

    @property
    def count(self) -> int:
        return len(self.members)

    @property
    def representatives(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.kinds))[:2]


@dataclass(frozen=True)
class OutputMembershipUpdate:
    previous: ConnectedOutputStatus
    current: ConnectedOutputStatus
    added: tuple[OutputMember, ...] = ()
    removed: tuple[OutputMember, ...] = ()
    baseline: bool = False
    held_pending: bool = False

    @property
    def changed(self) -> bool:
        return self.previous != self.current


def _confirmed_ids(snapshot: AudioRouteSnapshot | None) -> tuple[str, ...]:
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
    result = []
    for identity in identities:
        if not identity or identity == snapshot.cast_source_id or identity in result:
            continue
        device = devices.get(identity)
        if (device is not None and device.transport == "airplay"
                and snapshot.state not in {"casting", "waiting_audio"}):
            continue
        result.append(identity)
    return tuple(result)


class OutputMembershipTracker:
    """Track confirmations without letting provider sorting reorder the badge.

    A group's native sender restarts while membership is changing. During that
    pending interval the last confirmed badge stays visible; pending targets
    never count as playing. Failure clears it. The initial sample establishes
    a baseline so reopening a surface does not replay connection animations.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._current = ConnectedOutputStatus()
        self._initialized = False

    @property
    def current(self) -> ConnectedOutputStatus:
        return self._current

    def update(self, snapshot: AudioRouteSnapshot | None) -> OutputMembershipUpdate:
        previous = self._current
        baseline = not self._initialized
        self._initialized = True
        pending = (isinstance(snapshot, AudioRouteSnapshot) and snapshot.state != "error"
                   and bool(snapshot.pending_ids or snapshot.pending_id))
        if pending:
            return OutputMembershipUpdate(previous, previous, baseline=baseline, held_pending=True)
        identities = _confirmed_ids(snapshot)
        old_members = {member.id: member for member in previous.members}
        ordered = [member.id for member in previous.members if member.id in identities]
        ordered.extend(identity for identity in identities if identity not in old_members)
        devices = ({device.id: device for device in snapshot.devices}
                   if isinstance(snapshot, AudioRouteSnapshot) else {})
        members = []
        for identity in ordered:
            device = devices.get(identity)
            if device is not None:
                members.append(OutputMember.from_device(device))
            else:
                members.append(old_members.get(identity, OutputMember(identity, "未识别设备", "device")))
        current = ConnectedOutputStatus(tuple(members))
        self._current = current
        return OutputMembershipUpdate(
            previous, current,
            added=() if baseline else tuple(member for member in current.members if member.id not in old_members),
            removed=tuple(member for member in previous.members if member.id not in identities),
            baseline=baseline,
        )


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
