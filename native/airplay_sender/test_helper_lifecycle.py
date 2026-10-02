"""Exercise the actual helper without connecting to a physical receiver.

Run explicitly after the native build; this needs an active Windows render
endpoint and the existing soundcard dependency. No sound is generated, changed,
recorded to disk, or sent to any host other than the local loopback test socket.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, default=Path("build/native-airplay/GluSpotAirPlay.exe"))
    options = parser.parse_args()
    executable = str(options.exe.resolve())
    import soundcard as sc
    project_root = str(Path(__file__).resolve().parents[2])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from api.windows_audio_devices import WindowsAudioDevices

    endpoint = sc.default_speaker().id
    base = [executable, "--host", "127.0.0.1", "--endpoint", endpoint]
    results = []

    def collect(process, label, expected, timeout=5):
        stdout, stderr = process.communicate(timeout=timeout)
        events = [json.loads(line) for line in stdout.splitlines()]
        assert process.returncode == expected, (label, process.returncode, events, stderr)
        assert "streaming" not in [item["event"] for item in events], (label, events)
        results.append({"case": label, "status": "passed", "exit": expected, "events": events,
                        "elapsed_seconds": round(time.monotonic() - process.gluspot_started_at, 3)})
        return events

    def launch(arguments, *, preloaded_input=None):
        started_at = time.monotonic()
        kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        if preloaded_input is None:
            process = subprocess.Popen(arguments, stdin=subprocess.PIPE, **kwargs)
        else:
            # Have cancellation waiting before the child can finish activation.
            # Closing a real anonymous pipe exercises EOF; DEVNULL is not a pipe
            # and would not use the helper's PeekNamedPipe cancellation path.
            read_fd, write_fd = os.pipe()
            try:
                if preloaded_input:
                    assert os.write(write_fd, preloaded_input) == len(preloaded_input)
            finally:
                os.close(write_fd)
            try:
                process = subprocess.Popen(arguments, stdin=read_fd, **kwargs)
            finally:
                os.close(read_fd)
        process.gluspot_started_at = started_at
        return process

    def endpoint_state():
        backend = WindowsAudioDevices()
        try:
            return {"muted": backend.get_endpoint_mute(endpoint),
                    "volume": backend.get_endpoint_volume(endpoint)}
        finally:
            backend.close()

    collect(launch([executable, "--host", "invalid", "--endpoint", endpoint]), "invalid host", 2)
    collect(launch([executable, "--probe-capture", "--exclusive-output", "--endpoint", endpoint]),
            "capture probe rejects mute option", 2)
    collect(launch([executable, "--probe-capture", "--endpoint", "missing-endpoint"]), "missing endpoint", 1)
    invalid = collect(launch(base + ["--process-loopback"]), "process mode requires capture probe", 2)
    assert any("only supported with --probe-capture" in item.get("message", "") for item in invalid)
    invalid = collect(launch([executable, "--probe-capture", "--process-loopback", "--exclusive-output",
                             "--endpoint", endpoint]), "process capture probe rejects mute option", 2)
    assert any("does not support --exclusive-output" in item.get("message", "") for item in invalid)
    assert not any(item["event"] in {"capture_ready", "mute_state"} for item in invalid)

    # Cancellation that arrives during capture initialization must be observed
    # before sender.start: there must not even be a queued TCP connection.
    for mode, pending in (("stop", b"stop\n"), ("eof", b"")):
        before = endpoint_state()
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            process = launch(base + ["--port", str(server.getsockname()[1]), "--exclusive-output"],
                             preloaded_input=pending)
            events = collect(process, f"initialization {mode} before receiver start", 0, timeout=8)
            assert events[-1]["event"] == "stopped", events
            assert not any(item["event"] in {"connecting", "waiting_audio", "streaming", "stats", "error"}
                           for item in events), (mode, events)
            mute_events = [item for item in events if item["event"] == "mute_state"]
            assert mute_events and all(item["phase"] == "prepared" and item["applied"] is False
                                       for item in mute_events), (mode, mute_events)
            server.settimeout(.1)
            try:
                connection, _ = server.accept()
            except socket.timeout:
                pass
            else:
                connection.close()
                raise AssertionError(f"{mode}: helper started receiver networking after cancellation")
        assert endpoint_state() == before, f"{mode}: cancellation changed the source mute/volume"
        results[-1]["source_state_unchanged"] = True
        results[-1]["receiver_network_started"] = False

    # Close a bound port, then assert the actual native host reports refusal.
    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        port = port_probe.getsockname()[1]
    process = launch(base + ["--port", str(port)])
    # Keep stdin open while waiting; communicate() would close it and cancel.
    process.wait(timeout=5)
    events = collect(process, "refused connection", 1)
    assert any(item["event"] == "error" for item in events)

    # A loopback peer accepts TCP but withholds pairing replies. Stop and EOF
    # must both end that partially initialized encrypted-protocol session.
    for mode in ("stop", "eof", "timeout"):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            server.settimeout(5)
            process = launch(base + ["--port", str(server.getsockname()[1]), "--exclusive-output"])
            connection, _ = server.accept()
            with connection:
                connection.settimeout(5)
                pairing_request = bytearray()
                while b"\r\n\r\n" not in pairing_request:
                    chunk = connection.recv(8192)
                    assert chunk, "native sender closed before its pairing request"
                    pairing_request.extend(chunk)
                    assert len(pairing_request) < 65536, "oversized pairing request"
                assert b"/pair-setup" in pairing_request
                if mode == "stop":
                    process.stdin.write("stop\n")
                    process.stdin.flush()
                    process.wait(timeout=3)
                elif mode == "eof":
                    process.stdin.close()
                    process.stdin = None
                    process.wait(timeout=3)
                else:
                    process.wait(timeout=25)
                events = collect(process, f"handshake {mode}", 1 if mode == "timeout" else 0)
                assert events[-1]["event"] == "stopped", events
                assert (any(item["event"] == "error" for item in events)) == (mode == "timeout")
                mute_events = [item for item in events if item["event"] == "mute_state"]
                assert [item["phase"] for item in mute_events] == ["prepared", "restored"]
                assert not any(item["applied"] for item in mute_events), "partial handshake must never mute"

    capture = launch([executable, "--probe-capture", "--endpoint", endpoint])
    output, error = capture.communicate(timeout=5)
    capture_events = [json.loads(line) for line in output.splitlines()]
    assert capture.returncode == 0, (capture_events, error)
    assert capture_events[0]["event"] == "capture_ready"
    assert capture_events[0]["sample_rate"] > 0
    results.append({"case": "real WASAPI loopback probe", "status": "passed", "exit": 0, "events": capture_events})

    # Require the process path explicitly. A successful endpoint fallback would
    # hide the driver-mute bug, so success must report the verified broader mix.
    before = endpoint_state()
    capture = launch([executable, "--probe-capture", "--process-loopback", "--endpoint", endpoint])
    output, error = capture.communicate(timeout=8)
    process_events = [json.loads(line) for line in output.splitlines()]
    windows_build = sys.getwindowsversion().build
    if capture.returncode == 0:
        assert process_events[0]["event"] == "capture_ready", process_events
        assert process_events[0]["sample_rate"] > 0
        stats = [item for item in process_events if item["event"] == "stats"]
        assert len(stats) == 1, process_events
        signal = stats[0]["capture_signal"]
        assert signal["mode"] == "process_loopback" and signal["scope"] == "all_other_processes", signal
        assert isinstance(signal["nonzero_samples"], int) and signal["nonzero_samples"] >= 0, signal
        assert process_events[-1]["event"] == "stopped", process_events
        assert not any(item["event"] in {"connecting", "waiting_audio", "mute_state", "error"}
                       for item in process_events), process_events
        assert "using selected endpoint loopback" not in error, error
        results.append({"case": "required process loopback probe", "status": "passed", "exit": 0,
                        "supported": True, "windows_build": windows_build, "events": process_events})
    else:
        failures = [item.get("message", "") for item in process_events if item["event"] == "error"]
        assert capture.returncode == 1 and windows_build < 20348, (windows_build, process_events, error)
        assert failures and all(message.startswith(("Process loopback activation failed",
                                                    "Process loopback initialization failed"))
                                for message in failures), (process_events, error)
        assert not any(item["event"] in {"capture_ready", "stats", "mute_state", "connecting"}
                       for item in process_events), process_events
        results.append({"case": "required process loopback probe", "status": "unavailable", "exit": 1,
                        "supported": False, "windows_build": windows_build, "events": process_events})
    assert endpoint_state() == before, "process probe changed the source mute/volume"
    results[-1]["source_state_unchanged"] = True
    print(json.dumps({"passed": sum(item["status"] == "passed" for item in results),
                      "unavailable": sum(item["status"] == "unavailable" for item in results),
                      "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
