"""Exercise the actual helper without connecting to a physical receiver.

Run explicitly after the native build; this needs an active Windows render
endpoint and the existing soundcard dependency. No sound is generated, changed,
recorded to disk, or sent to any host other than the local loopback test socket.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, default=Path("build/native-airplay/GluSpotAirPlay.exe"))
    options = parser.parse_args()
    executable = str(options.exe.resolve())
    import soundcard as sc

    endpoint = sc.default_speaker().id
    base = [executable, "--host", "127.0.0.1", "--endpoint", endpoint]
    results = []

    def collect(process, label, expected, timeout=5):
        stdout, stderr = process.communicate(timeout=timeout)
        events = [json.loads(line) for line in stdout.splitlines()]
        assert process.returncode == expected, (label, process.returncode, events, stderr)
        assert "streaming" not in [item["event"] for item in events], (label, events)
        results.append({"case": label, "exit": expected, "events": events,
                        "elapsed_seconds": round(time.monotonic() - process.gluspot_started_at, 3)})
        return events

    def launch(arguments):
        process = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8")
        process.gluspot_started_at = time.monotonic()
        return process

    collect(launch([executable, "--host", "invalid", "--endpoint", endpoint]), "invalid host", 2)
    collect(launch([executable, "--probe-capture", "--exclusive-output", "--endpoint", endpoint]),
            "capture probe rejects mute option", 2)
    collect(launch([executable, "--probe-capture", "--endpoint", "missing-endpoint"]), "missing endpoint", 1)

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
    results.append({"case": "real WASAPI loopback probe", "exit": 0, "events": capture_events})
    print(json.dumps({"passed": len(results), "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
