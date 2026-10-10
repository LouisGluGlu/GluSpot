"""Actual helper/pipe/coordinator against independent localhost AP2 receivers.

No physical receiver, Windows audio endpoint, mDNS, account or credential
store is used. The test fixture performs strict SRP and decrypts final PCM.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api.audio_routes import AudioRouteDevice
from api.owned_pcm_process import OwnedPcmProcess


def run(timeout=20, executable=None, fixture=None):
    root = Path(__file__).resolve().parents[1]
    fixture = fixture or root / "build/native-airplay/gluspot_owned_membership_receiver_fixture.exe"
    peer = subprocess.Popen([str(fixture)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
    sender = OwnedPcmProcess(executable)
    checks, native_errors, samples, phases = {}, [], [], []
    process = writer = pending = None
    started = time.monotonic()
    epoch, submitted, stage = 73, 0, "initial"
    due = waiting_at = streaming_at = None
    wire = None
    try:
        ports = json.loads(peer.stdout.readline())["ports"]
        devices = tuple(AudioRouteDevice("airplay:fixture-" + str(index), "Loopback fixture", transport="airplay",
            host="127.0.0.1", port=port, device_id="loopback-" + str(index)) for index, port in enumerate(ports))
        sender.start_pcm((devices[0],), epoch)
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
                    phases.append((event.get("revision"), event.get("state")))
                elif kind == "stats":
                    group, protocol = event.get("group", {}), event.get("protocol", {})
                    samples.append((stage, group.get("pcm_epoch"), group.get("timeline_frame", 0), protocol.get("audio_frames_sent", 0)))
            if due is not None:
                burst = 0
                while due <= now and burst < 8:
                    pcm = b"".join(struct.pack("<hh", value, -value) for value in (
                        round(3000 * math.sin((submitted + frame) * math.tau * 440 / 44100)) for frame in range(352)))
                    if not sender.submit_pcm(pcm, epoch):
                        native_errors.append("pcm_submit")
                        break
                    submitted += 352; due += 352 / 44100; burst += 1
            if stage == "initial" and streaming_at is not None:
                checks["presentation_capability"] = sender.supports_owned_presentation(epoch)
                checks["streaming_after_declared_presentation"] = waiting_at is not None and streaming_at - waiting_at >= 1.8
                checks["late_join_accepted"] = sender.reconfigure_pcm(devices[:2], epoch)
                stage = "joining"
            state = sender.membership_state()
            if stage == "joining" and state.get("state") == "applied" and state.get("revision") == 1:
                checks["late_join_applied_same_epoch"] = state.get("confirmed_ids") == tuple(d.id for d in devices[:2]) and state.get("pcm_epoch") == epoch
                stage = "joined"
            if stage == "joined" and sum(s[0] == "joined" for s in samples) >= 2:
                checks["subset_solo_accepted"] = sender.reconfigure_pcm((devices[1],), epoch)
                stage = "removing"
            if stage == "removing" and state.get("state") == "applied" and state.get("revision") == 2:
                checks["subset_solo_applied_same_epoch"] = state.get("confirmed_ids") == (devices[1].id,) and state.get("pcm_epoch") == epoch
                stage = "solo"
            if stage == "solo" and sum(s[0] == "solo" for s in samples) >= 2:
                checks["bad_auth_join_accepted_as_pending"] = sender.reconfigure_pcm(devices[1:], epoch)
                stage = "failing"
            if stage == "failing" and state.get("state") == "rejected" and state.get("revision") == 3:
                checks["bad_auth_rejected_retains_solo"] = state.get("confirmed_ids") == (devices[1].id,) and not state.get("pending_ids")
                stage = "rejected"
            if stage == "rejected" and sum(s[0] == "rejected" for s in samples) >= 2:
                break
            if native_errors or sender.pcm_error():
                break
            time.sleep(.002)
        checks["same_native_process_writer_queue"] = (sender._process is process and sender._pcm_thread is writer and
            sender._pcm_queue is pending and process.poll() is None and writer.is_alive())
        active = [s for s in samples if s[2] > 0]
        checks["same_native_epoch_all_samples"] = bool(active) and all(s[1] == epoch for s in active)
        checks["global_timeline_advances_without_reset"] = len(active) >= 8 and all(new[2] > old[2] for old, new in zip(active, active[1:]))
        checks["global_transport_progress_does_not_reset"] = len(active) >= 8 and all(new[3] > old[3] for old, new in zip(active, active[1:]))
        checks["membership_pending_applied_and_rejected"] = phases == [(1, "pending"), (1, "applied"), (2, "pending"), (2, "applied"), (3, "pending"), (3, "rejected")]
        checks["no_native_or_pipe_error"] = not native_errors and not sender.pcm_error()
        peer.stdin.write("stats\n"); peer.stdin.flush()
        wire = json.loads(peer.stdout.readline())["receivers"]
        a, b, bad = wire
        checks["independent_receiver_crypto_and_packet_continuity"] = all(
            p["decrypt_failures"] == 0 and p["continuity_failures"] == 0 and not p["control_decrypt_failed"] for p in wire)
        checks["authenticated_event_channels_acknowledged"] = a["event_ack"] and b["event_ack"]
        checks["removed_receiver_teardown_only"] = a["teardown"] and not b["teardown"] and b["packets"] > 250
        checks["bad_auth_receiver_sends_no_audio"] = bad["packets"] == 0
        join_frame = round((b["anchor"] - a["anchor"]) * 44100 / 2**32)
        original = {rtp: hash_value for rtp, hash_value in a["records"]}
        matching = [(rtp, hash_value) for rtp, hash_value in b["records"] if rtp + join_frame in original]
        checks["same_final_pcm_for_common_global_source_frames"] = len(matching) >= 100 and all(
            original[rtp + join_frame] == hash_value for rtp, hash_value in matching)
        checks["same_declared_source_presentation_ntp"] = bool(matching) and all(abs(
            a["anchor"] + ((rtp + join_frame) * 2**32 // 44100) -
            (b["anchor"] + (rtp * 2**32 // 44100))) <= 1 for rtp, _ in matching)
    finally:
        sender.stop()
        checks["native_and_writer_stopped"] = process is not None and process.poll() is not None and writer is not None and not writer.is_alive()
        if peer.poll() is None:
            peer.stdin.write("stop\n"); peer.stdin.flush()
            try:
                peer.wait(timeout=3)
            except subprocess.TimeoutExpired:
                peer.kill(); peer.wait(timeout=2)
        checks["receiver_fixture_stopped"] = peer.returncode == 0
        for stream in (peer.stdin, peer.stdout):
            stream.close()
    return {"passed": all(checks.values()), "checks": checks, "native_samples": len(samples),
        "receiver_packets": [p["packets"] for p in wire] if wire is not None else [],
        "elapsed_seconds": round(time.monotonic() - started, 3)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".runtime/owned-pcm-loopback-membership-smoke.json"))
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args()
    report = run(args.timeout, args.executable, args.fixture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    raise SystemExit(0 if report["passed"] else 1)
