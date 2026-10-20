"""Bounded stdio transport: account material never appears in argv, URL or logs."""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from contextvars import copy_context
from pathlib import Path
from typing import Any

from api.online_music import OnlineMusicError
from api.online_music_diagnostics import current_login_trace, login_phase, login_request_cancelled, record_login_phase


class NodeOnlineMusicBackend:
    _packages = {"netease": "@neteasecloudmusicapienhanced/api", "qq": "@yakult-green-tea/qq-music-api", "kugou": "kugoumusicapi"}
    _max_line_bytes = 8 * 1024 * 1024
    _authentication_operations = frozenset({"begin_login", "poll_login", "cancel_login", "account_status", "logout", "prepare_login"})
    _prepared_idle_seconds = 60.0

    def __init__(self, node_path: str | Path | None = None, module_root: str | Path | None = None, timeout_seconds: float = 18.0, *, _authentication_lane: bool = False):
        project_root = Path(__file__).resolve().parent.parent
        bundled = Path(getattr(sys, "_MEIPASS", project_root)) / "online_music"
        self.bridge_path = bundled / "bridge.cjs" if (bundled / "bridge.cjs").is_file() else project_root / "native" / "online_music" / "bridge.cjs"
        runtime = bundled if (bundled / "node_modules").is_dir() else project_root / "build" / "online-music"
        self.module_root = Path(module_root or os.environ.get("GLUSPOT_ONLINE_MUSIC_MODULE_ROOT") or (runtime / "node_modules")).absolute()
        configured_node = node_path or os.environ.get("GLUSPOT_ONLINE_MUSIC_NODE")
        self.node_path = str(configured_node or (runtime / "node.exe" if (runtime / "node.exe").is_file() else shutil.which("node") or ""))
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 45.0))
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._active_request: tuple[subprocess.Popen, str, threading.Event] | None = None
        self._cancellation_epoch = 0
        self._retiring_processes: set[subprocess.Popen] = set()
        self._process: subprocess.Popen | None = None
        self._responses: queue.Queue = queue.Queue(maxsize=16)
        self._serial = 0
        self._closed = False
        self._authentication_lane = _authentication_lane
        self._authentication_lock = threading.Lock()
        self._authentication_backends: dict[str, NodeOnlineMusicBackend] = {}
        self._prepare_started = False
        self._authentication_used = False
        self._prepare_retirement_timer: threading.Timer | None = None

    def available(self, source_id: str) -> tuple[bool, str]:
        if source_id == "qishui":
            return False, "汽水音乐尚无已验证且可随本项目分发的扫码适配器"
        package = self._packages.get(source_id)
        if not package:
            return False, "未知音乐来源"
        if not self.node_path or not Path(self.node_path).is_file() or not self.bridge_path.is_file():
            return False, "在线音乐运行时未安装"
        manifest = self.module_root / package / "package.json"
        if not manifest.is_file():
            return False, "在线音乐适配依赖未安装，请运行 prepare-runtime.ps1"
        try:
            version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
        except (OSError, ValueError, KeyError):
            return False, "在线音乐适配依赖无效"
        expected = {"netease": "4.41.1", "qq": "3.1.4", "kugou": "1.6.0"}[source_id]
        if version != expected:
            return False, "在线音乐适配依赖版本不匹配"
        return True, ""

    def _reader(self, process: subprocess.Popen, responses: queue.Queue) -> None:
        try:
            while process.stdout is not None:
                line = process.stdout.readline(self._max_line_bytes + 1)
                if not line:
                    break
                if len(line) > self._max_line_bytes:
                    break
                try:
                    response = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeError):
                    break
                try:
                    responses.put(response, timeout=1.0)
                except queue.Full:
                    break
        except (OSError, ValueError):
            pass
        finally:
            try:
                responses.put_nowait(None)
            except queue.Full:
                pass

    def _start(self) -> subprocess.Popen:
        if self._closed:
            raise OnlineMusicError("closed", message="在线音乐服务已关闭")
        if self._process is not None and self._process.poll() is None:
            return self._process
        with self._lifecycle_lock:
            if len(self._retiring_processes) >= 2:
                raise OnlineMusicError("runtime", message="登录服务正在回收旧请求，请稍后重试")
        # Inherited platform cookies are deliberately not consumed by this bridge.
        environment = {key: value for key, value in os.environ.items() if not any(word in key.upper() for word in ("COOKIE", "TOKEN", "SECRET"))}
        environment.update({"GLUSPOT_ONLINE_MUSIC_MODULE_ROOT": str(self.module_root), "NODE_ENV": "test", "QQ_AUTH_STATE_PATH": "memory", "AUTO_OPEN_EXPLORER": "false", "ENABLE_RANDOM_CN_IP": "false"})
        from local.branding import is_packaged_app, local_app_data_dir
        state_root = Path(local_app_data_dir()) if is_packaged_app() else Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GluSpot"
        work_root = state_root / "cache" / "online-music-bridge"
        work_root.mkdir(parents=True, exist_ok=True)
        environment.update({"TEMP": str(work_root), "TMP": str(work_root)})
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.Popen([self.node_path, str(self.bridge_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=work_root, env=environment, creationflags=creationflags)
        except OSError:
            raise OnlineMusicError("runtime", message="在线音乐运行时无法启动") from None
        self._responses = queue.Queue(maxsize=16)
        self._process = process
        if self._closed:
            self._stop_process()
            raise OnlineMusicError("closed", message="在线音乐服务已关闭")
        threading.Thread(target=self._reader, args=(process, self._responses), daemon=True, name="GluSpot-online-music-reader").start()
        return process

    def _authentication_backend(self, source_id: str, *, used: bool = False) -> NodeOnlineMusicBackend:
        with self._authentication_lock:
            if self._closed:
                raise OnlineMusicError("closed", source_id, "在线音乐服务已关闭")
            backend = self._authentication_backends.get(source_id)
            if backend is None:
                backend = NodeOnlineMusicBackend(self.node_path, self.module_root,
                    self.timeout_seconds, _authentication_lane=True)
                backend.bridge_path = self.bridge_path
                self._authentication_backends[source_id] = backend
            if used:
                # Claim the lane before a real request can wait for its network
                # lock. Retirement must never interrupt a QR/account/session.
                backend._authentication_used = True
            return backend

    def _retire_prepared_authentication_backend(self, source_id: str, backend: NodeOnlineMusicBackend) -> None:
        with self._authentication_lock:
            if (self._closed or self._authentication_backends.get(source_id) is not backend
                    or backend._authentication_used):
                return
            del self._authentication_backends[source_id]
        # Only the daemon retirement callback owns this close; it never runs on
        # Qt's thread and cannot race a newly registered replacement lane.
        backend.close()

    def prepare_login(self, source_id: str) -> bool:
        """Preload verified modules off-thread, without credentials, QR or network."""
        if self._authentication_lane or not self.available(source_id)[0]:
            return False
        try:
            backend = self._authentication_backend(source_id)
        except OnlineMusicError:
            return False
        with backend._authentication_lock:
            if backend._closed:
                return False
            if backend._prepare_started:
                return True
            backend._prepare_started = True
            timer = threading.Timer(self._prepared_idle_seconds,
                self._retire_prepared_authentication_backend, args=(source_id, backend))
            timer.daemon = True
            backend._prepare_retirement_timer = timer
        def prepare():
            try:
                backend.request(source_id, "prepare_login", {})
            except OnlineMusicError:
                # The deliberate login remains the source of user-facing errors
                # and can restart a failed warm-up using the same transport.
                with backend._authentication_lock:
                    backend._prepare_started = False
                    if backend._prepare_retirement_timer is not None:
                        backend._prepare_retirement_timer.cancel()
        context = copy_context()
        threading.Thread(target=lambda: context.run(prepare), daemon=True, name=f"GluSpot-login-prepare-{source_id}").start()
        timer.start()
        return True

    def cancel_login_request(self, source_id: str) -> bool:
        """Interrupt only this source's currently owned authentication request.

        The controller calls this synchronously before submitting a replacement.
        Idle QR state, catalog I/O and every other source remain untouched.
        """
        with self._authentication_lock:
            backend = self._authentication_backends.get(source_id)
        if backend is None:
            return False
        with backend._lifecycle_lock:
            backend._cancellation_epoch += 1
            active = backend._active_request
            if active is None or active[1] not in {"begin_login", "poll_login", "account_status"}:
                return False
            process, _, cancelled = active
            cancelled.set()
            backend._stop_process(expected_process=process)
            return True

    def request(self, source_id: str, operation: str, params: dict[str, Any], credential: dict[str, Any] | None = None) -> dict[str, Any]:
        if operation in self._authentication_operations and login_request_cancelled():
            raise OnlineMusicError("cancelled", source_id, "登录请求已取消", stage="auth_queue", category="cancelled")
        ready, reason = self.available(source_id)
        if not ready:
            raise OnlineMusicError("unavailable", source_id, reason)
        if not self._authentication_lane and operation in self._authentication_operations:
            # Catalog/lyrics/DJ calls can occupy the sequential bridge for up to
            # its network deadline. Authentication uses at most one lazy child
            # per supported platform, so neither catalog I/O nor another source's
            # abandoned QR request holds the currently selected login behind it.
            # Each child keeps the existing bounded protocol and serial state.
            return self._authentication_backend(source_id, used=operation != "prepare_login").request(
                source_id, operation, params, credential)
        trace = current_login_trace()
        with self._lifecycle_lock:
            cancellation_epoch = self._cancellation_epoch
        deadline = min(time.monotonic() + self.timeout_seconds, trace.deadline) if trace else time.monotonic() + self.timeout_seconds
        with login_phase("auth_queue"):
            acquired = self._lock.acquire(timeout=max(0., deadline - time.monotonic()))
        if not acquired:
            raise OnlineMusicError("timeout", source_id, "登录请求排队超时，请重试", stage="auth_queue", category="timeout")
        process = None
        cancelled = threading.Event()
        try:
            if deadline <= time.monotonic():
                raise OnlineMusicError("timeout", source_id, "登录请求已超时，请重试", stage="auth_queue", category="timeout")
            with self._lifecycle_lock:
                if (cancellation_epoch != self._cancellation_epoch
                        or operation in self._authentication_operations and login_request_cancelled()):
                    raise OnlineMusicError("cancelled", source_id, "登录请求已取消", stage="auth_queue", category="cancelled")
                with login_phase("runtime_start"):
                    process = self._start()
                self._active_request = (process, operation, cancelled)
            if deadline <= time.monotonic():
                self._stop_process(expected_process=process)
                raise OnlineMusicError("timeout", source_id, "登录服务启动超时，请重试", stage="runtime_start", category="timeout")
            self._serial += 1
            serial = self._serial
            budget_ms = max(1, int((deadline - time.monotonic()) * 1000) - 50)
            payload = json.dumps({"id": serial, "source": source_id, "operation": operation, "params": params, "credential": credential, "timeout_ms": budget_ms}, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
            if len(payload) > self._max_line_bytes:
                raise OnlineMusicError("response", source_id, "在线音乐请求超出大小限制")
            responses = self._responses
            def send_authentication() -> None:
                # A broken child that never reads stdin must not hold this
                # worker inside a blocking pipe write beyond its total budget.
                # This writer belongs to the exact bounded child; killing that
                # child releases its pipe, including during GUI cancellation.
                try:
                    if not cancelled.is_set():
                        process.stdin.write(payload)
                        process.stdin.flush()
                except (OSError, ValueError):
                    try:
                        responses.put_nowait({"id": serial, "ok": False, "error": "runtime", "stage": "request"})
                    except queue.Full:
                        pass
            try:
                with login_phase("request"):
                    if self._authentication_lane:
                        threading.Thread(target=send_authentication, daemon=True, name="GluSpot-online-music-writer").start()
                    else:
                        process.stdin.write(payload)
                        process.stdin.flush()
                    response = responses.get(timeout=max(.001, deadline - time.monotonic()))
            except (OSError, ValueError, queue.Empty) as error:
                self._stop_process(expected_process=process)
                if cancelled.is_set():
                    raise OnlineMusicError("cancelled", source_id, "登录请求已取消", stage="request", category="cancelled") from None
                if not isinstance(error, queue.Empty):
                    raise OnlineMusicError("runtime", source_id, "在线音乐运行时连接中断", stage="request", category="runtime", session_lost=self._authentication_lane) from None
                raise OnlineMusicError("timeout", source_id, "在线音乐请求超时，请重试", stage="request", category="timeout", session_lost=self._authentication_lane) from None
            if cancelled.is_set():
                raise OnlineMusicError("cancelled", source_id, "登录请求已取消", stage="request", category="cancelled")
            if not isinstance(response, dict) or response.get("id") != serial:
                self._stop_process(expected_process=process)
                raise OnlineMusicError("runtime", source_id, "在线音乐运行时连接中断", session_lost=self._authentication_lane)
            if trace is not None:
                timings = response.get("timings")
                if isinstance(timings, dict):
                    for stage in ("module_load",):
                        elapsed = timings.get(stage)
                        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and 0 <= elapsed <= 45000:
                            record_login_phase(source_id, trace.operation, stage, elapsed / 1000, trace.generation)
            if response.get("ok") is not True:
                code = response.get("error")
                code = code if code in {"network", "timeout", "invalid_response", "auth_required", "unsupported", "expired", "runtime", "account_changed", "not_writable", "mutation_failed"} else "network"
                messages = {"auth_required": "请先登录此音乐来源", "unsupported": "此来源暂不支持该操作", "expired": "二维码已过期，请刷新", "timeout": "音乐服务请求超时，请重试", "invalid_response": "音乐服务返回的数据无效", "account_changed": "账号已变化，请刷新后重试", "not_writable": "此歌单不可添加歌曲，请选择自己的歌单", "mutation_failed": "音乐服务未确认收藏操作，请刷新后重试"}
                stage = response.get("stage")
                stage = stage if stage in {"begin_login", "poll_login", "account_status", "module_load", "request"} else "request"
                lost = self._authentication_lane and (code == "timeout" or code == "runtime" and stage == "request")
                if lost:
                    self._stop_process(expected_process=process)
                raise OnlineMusicError(code, source_id, messages.get(code, "音乐服务连接失败，请重试"), stage=stage, category=code, session_lost=lost)
            return response
        finally:
            with self._lifecycle_lock:
                if self._active_request and self._active_request[0] is process:
                    self._active_request = None
            self._lock.release()

    def _stop_process(self, *, expected_process=None) -> None:
        with self._lifecycle_lock:
            if expected_process is not None and self._process is not expected_process:
                return
            process, self._process = self._process, None
            self._prepare_started = False
            try:
                self._responses.put_nowait(None)
            except queue.Full:
                pass
            if process is not None:
                self._retiring_processes.add(process)
        if process is None:
            return
        # Do not acquire the long request lock or wait for a child from the GUI
        # shutdown path. Wake the pending request immediately, then reap off-thread.
        try:
            if process.poll() is None:
                process.terminate()
        except OSError:
            pass
        def reap() -> None:
            try:
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)
            except OSError:
                pass
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream:
                        try:
                            stream.close()
                        except (OSError, ValueError):
                            pass
                with self._lifecycle_lock:
                    if process.poll() is not None:
                        self._retiring_processes.discard(process)
        threading.Thread(target=reap, daemon=True, name="GluSpot-online-music-reaper").start()

    def close(self) -> None:
        # This short lock only guards child registration, never a network wait.
        with self._authentication_lock:
            self._closed = True
            authentication_backends = tuple(self._authentication_backends.values())
            timer = self._prepare_retirement_timer
        if timer is not None:
            timer.cancel()
        for backend in authentication_backends:
            backend.close()
        self._stop_process()
