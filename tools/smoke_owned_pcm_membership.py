"""Exercise native rollback on an already silent Windows output.

No accounts or receivers are used. Endpoint/default/mute/volume state is
read-only. An invalid new endpoint must leave the original PCM process,
epoch, writer, presentation timeline and actual WASAPI rendering intact.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import struct
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.audio_routes import AudioRouteDevice
from api.owned_pcm_process import OwnedPcmProcess
from api.windows_audio_devices import WindowsAudioDevices


def run(timeout=15, executable=None):
    backend = WindowsAudioDevices()
    outputs = backend.list_outputs()
    before = {d.id: (backend.get_endpoint_mute(d.id), backend.get_endpoint_volume(d.id)) for d in outputs}
    defaults = tuple(d.id for d in outputs if d.is_default)
    silent = next((d for d in outputs if before[d.id][0] is True or before[d.id][1] == 0), None)
    if silent is None:
        backend.close()
        return {"passed": False, "reason": "no_existing_silent_endpoint", "endpoint_state_changed": False}
    device = AudioRouteDevice(silent.id, silent.name, silent.kind)
    missing = AudioRouteDevice("GluSpot-missing-fixture-" + uuid.uuid4().hex, "Offline test endpoint")
    sender = OwnedPcmProcess(executable)
    epoch, stage, submitted = 73, "initial", 0
    started = time.monotonic()
    due = waiting_at = streaming_at = None
    snapshots, membership = [], []
    native_errors, checks = [], {}
    process = writer = pending = None
    try:
        sender.start_pcm((device,), epoch)
        process, writer, pending = sender._process, sender._pcm_thread, sender._pcm_queue
        while time.monotonic() - started < timeout:
            now = time.monotonic()
            for event in sender.poll_events():
                kind = event.get("event")
                if kind == "waiting_audio":
                    due = waiting_at = now
                elif kind == "streaming":
                    streaming_at = now
                elif kind == "error":
                    native_errors.append("native_error")
                elif kind == "membership":
                    membership.append(sender.membership_state())
                elif kind == "stats":
                    group, renders = event.get("group", {}), event.get("local_renders", [])
                    if len(renders) == 1 and renders[0].get("endpoint_id") == silent.id:
                        render = renders[0]
                        if render.get("meaningful_samples", 0) > 0 and all(render.get(key) is True for key in ("ready", "armed", "playing")):
                            snapshots.append((stage, group.get("pcm_epoch"), group.get("timeline_frame", 0), render.get("written_frames", 0)))
            if due is not None:
                burst = 0
                while due <= now and burst < 8:
                    pcm = b"".join(struct.pack("<hh", value, value) for value in (
                        round(2000 * math.sin((submitted + frame) * math.tau * 440 / 44100)) for frame in range(352)))
                    if not sender.submit_pcm(pcm, epoch):
                        native_errors.append("pcm_submit")
                        break
                    submitted += 352; due += 352 / 44100; burst += 1
            if stage == "initial" and streaming_at is not None and len(snapshots) >= 2:
                checks["presentation_capability"] = sender.supports_owned_presentation(epoch)
                checks["streaming_after_declared_presentation"] = waiting_at is not None and streaming_at - waiting_at >= 1.8
                checks["new_join_accepted_without_restart"] = sender.reconfigure_pcm((device, missing), epoch)
                stage = "joining"
            state = sender.membership_state()
            if stage == "joining" and state.get("state") == "rejected":
                checks["failed_join_retains_original"] = state.get("confirmed_ids") == (silent.id,) and not state.get("pending_ids")
                stage = "rejected"
            if stage == "rejected" and sum(s[0] == "rejected" for s in snapshots) >= 2:
                checks["retained_solo_request_accepted"] = sender.reconfigure_pcm((device,), epoch)
                stage = "solo"
            if stage == "solo" and state.get("state") == "applied" and state.get("revision") == 2:
                checks["solo_applied_same_member"] = state.get("confirmed_ids") == (silent.id,) and not state.get("pending_ids")
                if sum(s[0] == "solo" for s in snapshots) >= 2:
                    break
            if native_errors or sender.pcm_error():
                break
            time.sleep(.002)
        checks["same_native_process_writer_queue"] = (sender._process is process and sender._pcm_thread is writer and
            sender._pcm_queue is pending and process.poll() is None and writer.is_alive())
        checks["same_native_epoch_all_samples"] = bool(snapshots) and all(s[1] == epoch for s in snapshots)
        checks["global_timeline_and_actual_render_advance"] = len(snapshots) >= 6 and all(
            newer[2] > older[2] and newer[3] > older[3] for older, newer in zip(snapshots, snapshots[1:]))
        checks["native_membership_pending_rejected_applied"] = [s.get("state") for s in membership] == ["pending", "rejected", "pending", "applied"]
        checks["no_native_or_pipe_error"] = not native_errors and not sender.pcm_error()
    finally:
        sender.stop()
        checks["native_and_writer_stopped"] = process is not None and process.poll() is not None and writer is not None and not writer.is_alive()
        after = {d.id: (backend.get_endpoint_mute(d.id), backend.get_endpoint_volume(d.id)) for d in outputs}
        checks["endpoint_mute_volume_unchanged"] = before == after
        checks["system_default_unchanged"] = defaults == tuple(d.id for d in backend.list_outputs() if d.is_default)
        backend.close()
    return {"passed": all(checks.values()), "checks": checks, "native_samples": len(snapshots),
        "submitted_pcm_frames": submitted, "elapsed_seconds": round(time.monotonic() - started, 3)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".runtime/owned-pcm-membership-smoke.json"))
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--executable", type=Path)
    args = parser.parse_args()
    report = run(args.timeout, args.executable)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    raise SystemExit(0 if report["passed"] else 1)
