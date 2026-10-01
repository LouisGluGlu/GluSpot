"""Own one native GluSpot AirPlay sender; bounded queues and deterministic stop."""
from __future__ import annotations

import json
import os
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

    def __init__(self, executable=None, popen=None, *, source_id=""):
        self.executable = Path(executable) if executable is not None else engine_path()
        self._popen = popen or subprocess.Popen
        self._process = None
        self._events = queue.Queue(maxsize=32)
        self._reader = None
        self._started_at = 0.0
        self._streaming = False
        self._terminal_seen = False
        self._source_id = source_id

    def start(self, device, endpoint_id: str):
        self.stop()
        if not self.executable.is_file():
            raise RuntimeError("HomePod 投放组件未安装，请更新完整的 GluSpot 安装包")
        args = [str(self.executable), "--host", device.host, "--port", str(device.port),
                "--endpoint", endpoint_id, "--device-id", device.device_id or device.host,
                "--name", device.name]
        if self._source_id:
            args += ["--source-id", self._source_id]
        kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                      text=True, encoding="utf-8", errors="replace", bufsize=1)
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._process = self._popen(args, **kwargs)
        self._started_at = time.monotonic()
        self._streaming = self._terminal_seen = False
        # A new queue fences off a previous process reader's late events.
        events = self._events = queue.Queue(maxsize=32)
        self._reader = threading.Thread(target=self._read_events, args=(self._process, events),
                                        name="GluSpotAirPlayStatus", daemon=True)
        self._reader.start()

    @staticmethod
    def _read_events(process, events):
        try:
            while True:
                line = process.stdout.readline(8193)
                if not line:
                    break
                if len(line) > 8192 or not line.endswith("\n"):
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict) or event.get("event") not in {"connecting", "waiting_audio", "streaming", "error", "stopped", "stats"}:
                        continue
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
                results.append({"event": "error", "message": f"HomePod 投放已中断（{code}）"})
            elif code is None and not self._streaming and time.monotonic() - self._started_at > self.CONNECT_TIMEOUT:
                self._terminal_seen = True
                self.stop()
                results.append({"event": "error", "message": "连接 HomePod 超时，请检查局域网与家庭 App 的扬声器访问设置"})
        return tuple(results)

    def stop(self):
        process, self._process = self._process, None
        if process is not None:
            try:
                if process.poll() is None:
                    process.stdin.write("stop\n")
                    process.stdin.flush()
                process.stdin.close()
                process.wait(timeout=1.5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=2.0)
            finally:
                if process.stdout is not None:
                    process.stdout.close()
        if self._reader is not None:
            self._reader.join(timeout=0.5)
            self._reader = None
