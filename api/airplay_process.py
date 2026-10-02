"""Own one native GluSpot AirPlay sender; bounded queues and deterministic stop."""
from __future__ import annotations

import json
import math
import os
from collections import deque
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def engine_path() -> Path:
    override = os.environ.get("GLUSPOT_AIRPLAY_ENGINE", "")
    if override:
        return Path(override).absolute()
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "native" / "GluSpotAirPlay.exe"
    return Path(__file__).resolve().parents[1] / "build" / "native-airplay" / "GluSpotAirPlay.exe"


class AirPlayProcess:
    CONNECT_TIMEOUT = 25.0
    TIMING_MAX_AGE = 2.0
    STOP_TIMEOUT = 1.5

    def __init__(self, executable=None, popen=None, *, source_id="", exclusive_output=True):
        self.executable = Path(executable) if executable is not None else engine_path()
        self._popen = popen or subprocess.Popen
        self._process = None
        self._events = queue.Queue(maxsize=32)
        self._reader = None
        self._stderr_reader = None
        self._diagnostic_lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._stderr_tail = deque(maxlen=24)
        self._last_stats = {}
        self._last_stats_at = 0.0
        self._last_terminal = {}
        self._stopped_at = 0.0
        self._mute_state = {}
        self._receiver_volume = {}
        self._receiver_connected = False
        self._exclusive_output = exclusive_output
        self.needs_local_output_restore = False
        self._started_at = 0.0
        self._streaming = False
        self._terminal_seen = False
        self._source_id = source_id

    def start(self, device, endpoint_id: str):
        self.stop()
        # Even a failed CreateProcess belongs to a new attempt. Never let a
        # previous session's mute ownership authorize restoring this endpoint.
        self.needs_local_output_restore = False
        self._started_at = self._stopped_at = 0.0
        with self._diagnostic_lock:
            self._last_stats = {}
            self._last_stats_at = 0.0
            self._last_terminal = {}
            tail = self._stderr_tail = deque(maxlen=24)
            mute_state = self._mute_state = {}
            self._receiver_volume = {}
            self._receiver_connected = False
        if not self.executable.is_file():
            raise RuntimeError("HomePod 投放组件未安装，请更新完整的 GluSpot 安装包")
        args = [str(self.executable), "--host", device.host, "--port", str(device.port),
                "--endpoint", endpoint_id, "--device-id", device.device_id or device.host,
                "--name", device.name]
        if self._source_id:
            args += ["--source-id", self._source_id]
        if self._exclusive_output:
            args.append("--exclusive-output")
        kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      text=True, encoding="utf-8", errors="replace", bufsize=1)
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._process = self._popen(args, **kwargs)
        self._started_at = time.monotonic()
        self._stopped_at = 0.0
        self._streaming = self._terminal_seen = False
        # A new queue fences off a previous process reader's late events.
        events = self._events = queue.Queue(maxsize=32)
        self._reader = threading.Thread(target=self._read_events, args=(
            self._process, events, lambda event: self._remember_native_event(mute_state, event)),
                                        name="GluSpotAirPlayStatus", daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, args=(self._process, tail),
                                               name="GluSpotAirPlayDiagnostics", daemon=True)
        self._stderr_reader.start()

    def _remember_native_event(self, state, event):
        with self._diagnostic_lock:
            if event.get("event") == "mute_state":
                state.update(event)
            if state is not self._mute_state:
                return
            if event.get("event") in {"waiting_audio", "streaming"}:
                self._receiver_connected = True
            elif event.get("event") in {"error", "stopped"}:
                self._receiver_connected = False
            if event.get("event") in {"stats", "receiver_volume"}:
                volume_state = event.get("receiver_volume")
                if isinstance(volume_state, dict):
                    self._receiver_volume = dict(volume_state)
            if event.get("event") == "stats":
                self._last_stats = dict(event)
                self._last_stats_at = time.monotonic()
            elif event.get("event") == "error" or (
                    event.get("event") == "stopped" and self._last_terminal.get("event") != "error"):
                self._last_terminal = dict(event)

    def _read_stderr(self, process, tail):
        try:
            while True:
                line = process.stderr.readline(8193)
                if not line:
                    break
                if len(line) > 8192 or not line.endswith("\n"):
                    continue
                with self._diagnostic_lock:
                    tail.append(line.strip()[:480])
        except (OSError, ValueError):
            pass

    def diagnostics(self):
        """Bounded transport evidence; no PCM, keys or pipe reads on the caller."""
        with self._diagnostic_lock:
            return {
                "session_seconds": round(max(0.0, (self._stopped_at or time.monotonic()) -
                                               self._started_at), 3) if self._started_at else 0.0,
                "stats": dict(self._last_stats), "terminal": dict(self._last_terminal),
                "transport_messages": tuple(self._stderr_tail),
                "local_output": dict(self._mute_state),
                "receiver_volume": dict(self._receiver_volume),
            }

    def receiver_volume_state(self):
        """Current receiver read/ack, never a guessed or merely requested level."""
        with self._diagnostic_lock:
            state = self._receiver_volume
            volume = state.get("volume")
            connected = self._process is not None and self._receiver_connected and not self._last_terminal
            known = (connected and state.get("known") is True and
                     state.get("source") in {"receiver_read", "receiver_info", "receiver_ack"} and
                     isinstance(volume, (int, float)) and not isinstance(volume, bool))
            if known:
                try:
                    volume = float(volume)
                    known = math.isfinite(volume) and 0 <= volume <= 1
                except OverflowError:
                    known = False
            code = state.get("last_code", 0)
            return {"volume": volume if known else None, "known": bool(known),
                    "source": state.get("source") if known else "unknown",
                    "operation": state.get("operation", "unknown") if state.get("operation") in
                                 {"read", "set", "info"} else "unknown",
                    "pending": bool(connected and state.get("pending") is True),
                    "last_code": code if isinstance(code, int) and not isinstance(code, bool) else 0}

    def receiver_volume(self):
        return self.receiver_volume_state()["volume"]

    def set_volume(self, volume: float) -> bool:
        """Queue an explicit normalized receiver level; True is not receiver ack.

        The audio controller calls this on its worker. Native code coalesces
        slider commands and publishes a confirmed level only after RTSP 200.
        This never changes the fixed loopback source's Windows mute/volume.
        """
        if isinstance(volume, bool) or not isinstance(volume, (int, float)):
            return False
        try:
            volume = float(volume)
        except OverflowError:
            return False
        if not math.isfinite(volume) or not 0 <= volume <= 1:
            return False
        with self._command_lock:
            with self._diagnostic_lock:
                process = self._process
                if process is None or not self._receiver_connected or self._last_terminal:
                    return False
            try:
                if process.poll() is not None:
                    return False
                process.stdin.write(json.dumps({"command": "set_volume", "volume": volume},
                                                allow_nan=False, separators=(",", ":")) + "\n")
                process.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def latency_estimate(self):
        """Fresh sender buffer estimate in seconds, or None; never acoustic latency.

        The fixed RTP buffer and the input queue have different sample rates.
        Native stats convert each separately and omit receiver/network latency.
        Reading this single retained snapshot does not touch the helper pipes.
        """
        with self._diagnostic_lock:
            age = time.monotonic() - self._last_stats_at
            timing = self._last_stats.get("timing")
            if (self._process is None or not self._last_stats_at or self._last_terminal or
                    self._stopped_at or age < 0 or age > self.TIMING_MAX_AGE or
                    not isinstance(timing, dict) or timing.get("valid") is not True or
                    timing.get("method") != "protocol_plus_input_queue"):
                return None
            values = tuple(timing.get(key) for key in (
                "estimated_delay_ms", "protocol_buffer_ms", "input_queue_ms"))
            if any(isinstance(value, bool) or not isinstance(value, (int, float))
                   for value in values):
                return None
            try:
                delay, protocol, queued = (float(value) for value in values)
            except OverflowError:
                return None
            if any(not math.isfinite(value) or value < 0 for value in (delay, protocol, queued)):
                return None
            if not math.isclose(delay, protocol + queued, abs_tol=0.1):
                return None
            return {"seconds": delay / 1000.0, "source": timing["method"],
                    "sample_age_seconds": age, "protocol_buffer_seconds": protocol / 1000.0,
                    "input_queue_seconds": queued / 1000.0}

    @staticmethod
    def _read_events(process, events, on_event=None):
        try:
            while True:
                line = process.stdout.readline(8193)
                if not line:
                    break
                if len(line) > 8192 or not line.endswith("\n"):
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict) or event.get("event") not in {"connecting", "waiting_audio", "streaming", "error", "stopped", "stats", "mute_state", "receiver_volume"}:
                        continue
                    # Receiver-supplied JSON cannot assert when Python received
                    # it. Stamp here so queued signal evidence can expire.
                    event["_received_at"] = time.monotonic()
                    if on_event is not None:
                        on_event(event)
                    if events.full():
                        # Stats may be dropped, but preserve the newest failure/stop.
                        try:
                            events.get_nowait()
                        except queue.Empty:
                            pass
                    events.put_nowait(event)
                except (ValueError, queue.Full):
                    continue
        except (OSError, ValueError):
            pass

    def poll_events(self):
        results = []
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            if event["event"] in {"waiting_audio", "streaming"}:
                self._streaming = True
            if event["event"] in {"error", "stopped"}:
                self._terminal_seen = True
            results.append(event)
        if self._process is not None and not self._terminal_seen:
            code = self._process.poll()
            # Drain all final output before diagnosing an exit.
            reader_done = self._reader is None or not self._reader.is_alive()
            if code is not None and reader_done:
                self._terminal_seen = True
                event = {"event": "error", "message": f"HomePod 投放已中断（{code}）"}
                with self._diagnostic_lock:
                    self._last_terminal = dict(event)
                results.append(event)
            elif code is None and not self._streaming and time.monotonic() - self._started_at > self.CONNECT_TIMEOUT:
                self._terminal_seen = True
                self.stop()
                event = {"event": "error", "message": "连接 HomePod 超时，请检查局域网与家庭 App 的扬声器访问设置"}
                with self._diagnostic_lock:
                    self._last_terminal = dict(event)
                results.append(event)
        return tuple(results)

    def stop(self):
        with self._command_lock:
            process, self._process = self._process, None
        try:
            if process is not None:
                self._stopped_at = time.monotonic()
                try:
                    if process.poll() is None:
                        process.stdin.write("stop\n")
                        process.stdin.flush()
                    process.wait(timeout=self._stop_timeout())
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=2.0)
        finally:
            # Pipe cleanup errors must not skip another reader or the mute
            # recovery decision, including when kill/wait itself fails.
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except (OSError, ValueError):
                            continue
            try:
                for name in ("_reader", "_stderr_reader"):
                    reader = getattr(self, name)
                    if reader is not None:
                        try:
                            reader.join(timeout=0.5)
                        finally:
                            setattr(self, name, None)
            finally:
                if process is not None:
                    with self._diagnostic_lock:
                        restored = self._mute_state.get("phase") == "restored"
                        owned = self._mute_state.get("applied") is True
                        self.needs_local_output_restore = owned and not restored and process.returncode != 0

    def _stop_timeout(self):
        return self.STOP_TIMEOUT
