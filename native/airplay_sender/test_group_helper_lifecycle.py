"""Actual group-helper lifecycle checks, with local sockets only.

This uses an already playing music app's verified process identity. It generates
no audio, sends no audio to a real receiver, and never authenticates a receiver,
so group-owned source mute is never applied. PCM is not written to disk.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, default=Path("build/native-airplay/GluSpotAirPlay.exe"))
    parser.add_argument("--source-app", default="cloudmusic.exe")
    parser.add_argument("--idle-test-process", action="store_true",
                        help="Use this verified, silent Python test process; verifies lifecycle only, never music capture")
    parser.add_argument("--report", type=Path, default=Path("logs/group-helper-lifecycle.json"))
    options = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from api.windows_audio_devices import WindowsAudioDevices
    from api.windows_audio_sessions import resolve_music_audio_source, _WindowsAudioSessionAdapter

    backend = WindowsAudioDevices()
    try:
        if options.idle_test_process:
            # This is actual Windows creation-time evidence for the parent test
            # process, not a guessed music session. It emits no audio and is
            # deliberately suitable only for no-authentication lifecycle tests.
            source = _WindowsAudioSessionAdapter().process_identity(os.getpid())
            source_endpoint = next(output.id for output in backend.list_outputs() if output.is_default)
        else:
            source = resolve_music_audio_source(options.source_app)
            if source is None:
                raise RuntimeError("A uniquely verified active music app is required for lifecycle checks")
            source_endpoint = source.sessions[0].endpoint_id
            if any(session.endpoint_id != source_endpoint for session in source.sessions):
                raise RuntimeError("Music app must have one evidenced source endpoint for these checks")
        original = (backend.get_endpoint_mute(source_endpoint), backend.get_endpoint_volume(source_endpoint))
        base = [str(options.exe.resolve()), "--group", "--music-pid", str(source.process_id),
                "--music-created-at", str(source.process_created_at_100ns), "--source-endpoint", source_endpoint]
        results = []

        def member(identifier, port=7000):
            return ["--receiver-id", identifier, "--host", "127.0.0.1", "--port", str(port),
                    "--device-id", identifier, "--name", "Offline lifecycle receiver"]

        def read_pairing_request(connection):
            # TCP may split the headers and binary pairing body across reads.
            # Consume the whole request before checking peer EOF after stop.
            request = bytearray()
            header_end = None
            request_size = None
            while request_size is None or len(request) < request_size:
                block = connection.recv(8192)
                assert block, "receiver closed before its pairing request completed"
                request.extend(block)
                assert len(request) < 65536
                if header_end is None:
                    separator = request.find(b"\r\n\r\n")
                    if separator >= 0:
                        header_end = separator + 4
                        lengths = [int(line.split(b":", 1)[1].strip())
                                   for line in request[:separator].split(b"\r\n")
                                   if line.lower().startswith(b"content-length:")]
                        assert len(lengths) <= 1
                        request_size = header_end + (lengths[0] if lengths else 0)
                        assert 0 < request_size < 65536
            assert b"/pair-setup" in request[:header_end]
            assert len(request) == request_size, "unexpected pipelined receiver request"

        def launch(args, pending=None):
            start = time.monotonic()
            kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                          creationflags=subprocess.CREATE_NO_WINDOW)
            if pending is None:
                process = subprocess.Popen(args, stdin=subprocess.PIPE, **kwargs)
            else:
                read_fd, write_fd = os.pipe()
                try:
                    if pending:
                        os.write(write_fd, pending)
                finally:
                    os.close(write_fd)
                try:
                    process = subprocess.Popen(args, stdin=read_fd, **kwargs)
                finally:
                    os.close(read_fd)
            process.group_test_started = start
            process.group_stdout = []
            process.group_stderr = []
            process.group_readers = []
            for stream, output in ((process.stdout, process.group_stdout), (process.stderr, process.group_stderr)):
                reader = threading.Thread(target=lambda channel=stream, target=output: target.extend(channel), daemon=True)
                reader.start(); process.group_readers.append(reader)
            return process

        def collect(process, case, code, timeout=8):
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
                raise
            if process.stdin is not None:
                process.stdin.close(); process.stdin = None
            for reader in process.group_readers:
                reader.join(timeout=5)
            output, errors = "".join(process.group_stdout), "".join(process.group_stderr)
            events = [json.loads(line) for line in output.splitlines()]
            assert process.returncode == code, (case, process.returncode, events, errors)
            assert not any(item["event"] in {"waiting_audio", "streaming"} for item in events), (case, events)
            assert not any(item["event"] == "mute_state" and item["applied"] is True for item in events), (case, events)
            actual = (backend.get_endpoint_mute(source_endpoint), backend.get_endpoint_volume(source_endpoint))
            assert actual == original, (case, original, actual)
            results.append(dict(case=case, status="passed", exit=code, events=events,
                                elapsed_seconds=round(time.monotonic() - process.group_test_started, 3),
                                source_mute_volume_unchanged=True))
            return events

        invalid = list(base + member("one"))
        invalid[invalid.index("--host") + 1] = "untrusted.invalid"
        events = collect(launch(invalid), "invalid group host before any Windows work", 2)
        assert all(event["event"] == "error" for event in events)
        invalid = base + member("one") + ["--music-pid", str(source.process_id)]
        collect(launch(invalid), "duplicate music identity", 2)
        collect(launch(base + member("one") + member("two")), "duplicate physical receiver", 2)
        collect(launch(base + member("one") + ["--local-endpoint", source_endpoint]),
                "local cannot render through muted source", 2)
        invalid = list(base + member("one"))
        invalid[invalid.index("--music-created-at") + 1] = str(source.process_created_at_100ns + 1)
        collect(launch(invalid), "process creation mismatch", 1)

        for mode, pending in (("stop", b"stop\n"), ("eof", b"")):
            with socket.socket() as server:
                server.bind(("127.0.0.1", 0)); server.listen(); server.settimeout(.1)
                events = collect(launch(base + member("one", server.getsockname()[1]), pending),
                                 f"initialization {mode} prevents receiver networking", 0)
                assert events[-1]["event"] == "stopped"
                assert not any(item["event"] == "connecting" for item in events), events
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    pass
                else:
                    connection.close()
                    raise AssertionError("group starts network after cancelled pipe")

        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0)); refused = reserved.getsockname()[1]
        process = launch(base + member("one", refused))
        process.wait(timeout=8)
        events = collect(process, "one refused member ends the whole group", 1)
        assert any(item["event"] == "error" for item in events)
        assert events[-1]["event"] == "stopped"

        for mode in ("stop", "eof", "partial-command-stop", "timeout"):
            with socket.socket() as server:
                server.bind(("127.0.0.1", 0)); server.listen(); server.settimeout(8)
                process = launch(base + member("one", server.getsockname()[1]))
                try:
                    connection, _ = server.accept()
                    with connection:
                        connection.settimeout(8)
                        read_pairing_request(connection)
                        if mode == "stop":
                            process.stdin.write("stop\n"); process.stdin.flush()
                        elif mode == "partial-command-stop":
                            process.stdin.write('{"command":"set_volume","receiver":0,"volume":0.35}\n')
                            process.stdin.flush()
                            process.stdin.write("stop\n"); process.stdin.flush()
                        elif mode == "eof":
                            process.stdin.close(); process.stdin = None
                        process.wait(timeout=24 if mode == "timeout" else 8)
                        events = collect(process, f"partial group pairing {mode}", 1 if mode == "timeout" else 0)
                        assert events[-1]["event"] == "stopped"
                        if mode == "timeout":
                            assert any(item["event"] == "error" for item in events)
                finally:
                    if process.poll() is None:
                        process.kill(); process.wait()
        # Real host/socket ownership for two members: both pairing requests
        # start concurrently, then parent stop must close both partial sessions.
        with socket.socket() as first, socket.socket() as second:
            for server in (first, second):
                server.bind(("127.0.0.1", 0)); server.listen(); server.settimeout(8)
            process = launch(base + member("one", first.getsockname()[1]) + member("two", second.getsockname()[1]))
            connections = []
            try:
                for server in (first, second):
                    connection, _ = server.accept(); connections.append(connection); connection.settimeout(8)
                    read_pairing_request(connection)
                process.stdin.write("stop\n"); process.stdin.flush()
                events = collect(process, "two simultaneous partial members stop as one group", 0)
                assert events[-1]["event"] == "stopped"
                for connection in connections:
                    # The sender flushes a final TEARDOWN before its FIN even
                    # when AP2 pairing was only partial. Drain that request;
                    # bounded read timeout still detects a leaked peer socket.
                    teardown = bytearray()
                    while block := connection.recv(8192):
                        teardown.extend(block)
                        assert len(teardown) < 65536, "unbounded receiver data after stop"
                    assert teardown.startswith(b"TEARDOWN "), teardown
            finally:
                for connection in connections:
                    connection.close()
                if process.poll() is None:
                    process.kill(); process.wait()
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps({"cases": results, "source_pid": source.process_id,
                                             "source_mode": "silent_test_process" if options.idle_test_process else "verified_music_app",
                                             "physical_receiver_contacted": False,
                                             "generated_audio": False, "saved_pcm": False}, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"Actual group-helper lifecycle: {len(results)} cases passed; {options.report}")
    finally:
        backend.close()


if __name__ == "__main__":
    main()
