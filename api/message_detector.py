from __future__ import annotations

import asyncio
import ctypes
import io
import re
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Iterable

from api.models import ColorToken, MessageData


@dataclass(frozen=True)
class WindowCandidate:
    title: str = ""
    class_name: str = ""
    process_name: str = ""
    texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class NativeWindowCandidate:
    hwnd: int
    title: str = ""
    class_name: str = ""
    process_id: int = 0
    process_path: str = ""
    visible: bool = False


@dataclass(frozen=True)
class WeChatSessionCandidate:
    session: str
    text: str
    unread_count: int
    raw_texts: tuple[str, ...] = ()
    avatar_image_bytes: bytes = b""


class WindowsMessageDetector:
    APP_HINTS = (
        (("wechat", "weixin", "微信", "wxwork", "tencent.wechat", "tencentwechat"), "微信", ColorToken(75, 210, 110)),
        (("qq", "tim"), "QQ", ColorToken(80, 170, 255)),
        (("telegram",), "Telegram", ColorToken(70, 170, 245)),
        (("dingtalk", "钉钉"), "钉钉", ColorToken(50, 150, 255)),
        (("feishu", "lark", "飞书"), "飞书", ColorToken(82, 120, 255)),
        (("teams",), "Teams", ColorToken(105, 95, 255)),
    )
    NOISE_TEXT = {
        "",
        "通知",
        "新通知",
        "notification",
        "notifications",
        "操作中心",
        "notification center",
        "windows shell experience host",
    }

    def __init__(self, settings_getter: Callable[[str, object], object] | None = None):
        self.settings_getter = settings_getter or (lambda _key, default=None: default)
        self._last_poll_at = 0.0
        self._last_notification_poll_at = 0.0
        self._last_candidate_poll_at = time.monotonic()
        self._last_wechat_session_scan_at = 0.0
        self._wechat_last_total_change_at = 0.0
        self._wechat_detail_scan_reason = ""
        self._messages: list[MessageData] = []
        self._seen_at: dict[str, float] = {}
        self._last_error = ""
        self._last_candidate_count = 0
        self._last_notification_count = 0
        self._notification_error = ""
        self._notification_access = ""
        self._last_notification_apps: tuple[str, ...] = ()
        self._notification_initialized = False
        self._seen_notification_ids: set[str] = set()
        self._wechat_initialized = False
        self._wechat_seen_sessions: dict[str, str] = {}
        self._wechat_seen_active_chats: dict[str, str] = {}
        self._wechat_window_count = 0
        self._wechat_session_count = 0
        self._wechat_unread_count = 0
        self._wechat_total_unread_count = 0
        self._wechat_previous_total_unread_count: int | None = None
        self._wechat_error = ""
        self._wechat_debug_items: tuple[str, ...] = ()
        self._wechat_native_window_count = 0
        self._wechat_native_windows: tuple[str, ...] = ()
        self._wechat_backend = ""
        self._polling = False
        self._lock = threading.Lock()

    def messages(self) -> tuple[MessageData, ...]:
        if not self._message_detection_enabled():
            return ()
        now = time.monotonic()
        if now - self._last_poll_at >= 1.25:
            self._schedule_poll(now)
        with self._lock:
            return tuple(self._messages[-5:])

    def diagnostics(self) -> dict[str, object]:
        return {
            "last_error": self._last_error,
            "last_candidate_count": self._last_candidate_count,
            "notification_access": self._notification_access,
            "notification_error": self._notification_error,
            "last_notification_count": self._last_notification_count,
            "last_notification_apps": self._last_notification_apps,
            "message_detection_enabled": self._message_detection_enabled(),
            "wechat_notification_enabled": self._wechat_notification_enabled(),
            "wechat_detail_notification_enabled": self._wechat_detail_enabled(),
            "qq_notification_enabled": self._qq_notification_enabled(),
            "dingtalk_notification_enabled": self._dingtalk_notification_enabled(),
            "wechat_window_count": self._wechat_window_count,
            "wechat_session_count": self._wechat_session_count,
            "wechat_unread_count": self._wechat_unread_count,
            "wechat_total_unread_count": self._wechat_total_unread_count,
            "wechat_error": self._wechat_error,
            "wechat_debug_items": self._wechat_debug_items,
            "wechat_native_window_count": self._wechat_native_window_count,
            "wechat_native_windows": self._wechat_native_windows,
            "wechat_backend": self._wechat_backend,
            "wechat_detail_scan_reason": self._wechat_detail_scan_reason,
            "wechat_last_session_scan_age": round(time.monotonic() - self._last_wechat_session_scan_at, 3) if self._last_wechat_session_scan_at else None,
            "cached_messages": len(self._messages),
        }

    def _schedule_poll(self, now: float):
        with self._lock:
            if self._polling:
                return
            self._polling = True
            self._last_poll_at = now
        thread = threading.Thread(target=self._poll_windows_thread, args=(now,), name="MessageDetectorPoll", daemon=True)
        thread.start()

    def _poll_windows_thread(self, now: float):
        try:
            self._poll_windows(now)
        finally:
            with self._lock:
                self._polling = False

    def _poll_windows(self, now: float):
        if sys.platform != "win32":
            return
        notification_messages: list[MessageData] = []
        candidates: list[WindowCandidate] = []
        if now - self._last_notification_poll_at >= 1.35:
            self._last_notification_poll_at = now
            notification_messages = self._notification_messages(now)
        if now - self._last_candidate_poll_at >= 8.0:
            self._last_candidate_poll_at = now
            candidates = self._collect_candidates()
        has_wechat_notification = any(self._is_wechat_app_name(message.app) for message in notification_messages)
        wechat_messages: list[MessageData] = []
        if self._wechat_notification_enabled() and self._wechat_detail_enabled():
            wechat_messages = self._wechat_messages(
                now,
                force_detail=has_wechat_notification,
                suppress_summary=has_wechat_notification,
            )
        messages: list[MessageData] = []
        messages.extend(notification_messages)
        messages.extend(wechat_messages)
        for candidate in candidates:
            message = self._message_from_candidate(candidate)
            if message is None:
                continue
            messages.append(message)
        with self._lock:
            for key, seen_at in list(self._seen_at.items()):
                if now - seen_at > 90.0:
                    self._seen_at.pop(key, None)
            self._last_candidate_count = len(candidates)
            for message in messages:
                key = f"{message.app}\0{message.sender}\0{message.text}"
                if key in self._seen_at:
                    continue
                self._seen_at[key] = now
                self._messages.append(message)
            self._messages = self._messages[-12:]

    def _is_wechat_app_name(self, value: str) -> bool:
        folded = str(value or "").casefold()
        return any(token in folded for token in ("wechat", "weixin", "tencent.wechat", "微信"))

    def _message_detection_enabled(self) -> bool:
        return bool(self.settings_getter("v2_enable_message_detection", True))

    def _wechat_notification_enabled(self) -> bool:
        return bool(self.settings_getter("v2_enable_wechat_notification", True))

    def _wechat_detail_enabled(self) -> bool:
        return bool(self.settings_getter("v2_enable_wechat_detail_notification", False))

    def _qq_notification_enabled(self) -> bool:
        return bool(self.settings_getter("v2_enable_qq_notification", False))

    def _dingtalk_notification_enabled(self) -> bool:
        return bool(self.settings_getter("v2_enable_dingtalk_notification", False))

    def _message_app_enabled(self, app_name: str) -> bool:
        folded = str(app_name or "").casefold()
        if self._is_wechat_app_name(app_name):
            return self._wechat_notification_enabled()
        if "wechat" in folded or "weixin" in folded or "微信" in app_name:
            return self._wechat_notification_enabled()
        if folded == "qq":
            return self._qq_notification_enabled()
        if "dingtalk" in folded or "钉钉" in app_name:
            return self._dingtalk_notification_enabled()
        return True

    def _notification_messages(self, now: float) -> list[MessageData]:
        try:
            return asyncio.run(self._notification_messages_async(now))
        except Exception as exc:
            self._notification_error = f"notification scan failed: {exc}"
            return []

    async def _notification_messages_async(self, now: float) -> list[MessageData]:
        try:
            from winsdk.windows.ui.notifications import KnownNotificationBindings, NotificationKinds
            from winsdk.windows.ui.notifications.management import UserNotificationListener, UserNotificationListenerAccessStatus
        except Exception as exc:
            self._notification_error = f"winsdk notification unavailable: {exc}"
            return []

        listener = UserNotificationListener.current
        if listener is None:
            self._notification_error = "notification listener unavailable"
            return []
        status = listener.get_access_status()
        self._notification_access = getattr(status, "name", str(int(status)))
        if status == UserNotificationListenerAccessStatus.UNSPECIFIED:
            try:
                status = await listener.request_access_async()
                self._notification_access = getattr(status, "name", str(int(status)))
            except Exception as exc:
                self._notification_error = f"notification access request failed: {exc}"
                return []
        if status != UserNotificationListenerAccessStatus.ALLOWED:
            self._notification_error = f"notification access {self._notification_access}"
            return []

        notifications = list(await listener.get_notifications_async(NotificationKinds.TOAST))
        self._last_notification_count = len(notifications)
        app_names: list[str] = []
        out: list[MessageData] = []
        current_ids: set[str] = set()
        for notification in notifications:
            try:
                app_name = self._app_display_name(notification.app_info)
                if app_name and app_name not in app_names:
                    app_names.append(app_name)
            except Exception:
                pass
            key = self._notification_key(notification)
            if not key:
                continue
            current_ids.add(key)
            message = self._message_from_user_notification(notification, KnownNotificationBindings.toast_generic)
            if message is None:
                continue
            if not self._notification_initialized:
                continue
            if key in self._seen_notification_ids:
                continue
            out.append(message)
        with self._lock:
            self._last_notification_apps = tuple(app_names[-12:])
            self._seen_notification_ids.update(current_ids)
            if len(self._seen_notification_ids) > 300:
                self._seen_notification_ids = set(list(self._seen_notification_ids)[-240:])
            self._notification_initialized = True
        self._notification_error = ""
        return out

    def _notification_key(self, notification) -> str:
        try:
            app_info = notification.app_info
            app_id = str(getattr(app_info, "app_user_model_id", "") or getattr(app_info, "id", "") or "")
            return f"{app_id}:{notification.id}:{notification.creation_time}"
        except Exception:
            return ""

    def _message_from_user_notification(self, notification, toast_template: str) -> MessageData | None:
        try:
            app_info = notification.app_info
            app_name = self._app_display_name(app_info)
            app_id = str(getattr(app_info, "app_user_model_id", "") or getattr(app_info, "id", "") or "")
            if app_name and not self._message_app_enabled(app_name):
                return None
            visual = notification.notification.visual
            binding = visual.get_binding(toast_template) if visual is not None else None
            texts = []
            if binding is not None:
                for item in binding.get_text_elements() or []:
                    text = str(getattr(item, "text", "") or "").strip()
                    if text:
                        texts.append(text)
            return self._message_from_notification_parts(app_name, app_id, texts)
        except Exception as exc:
            self._notification_error = f"notification parse failed: {exc}"
            return None

    def _app_display_name(self, app_info) -> str:
        if app_info is None:
            return ""
        try:
            display_info = app_info.display_info
            display_name = str(getattr(display_info, "display_name", "") or "")
            if display_name:
                return display_name
        except Exception:
            pass
        return str(getattr(app_info, "app_user_model_id", "") or getattr(app_info, "id", "") or "")

    def _message_from_notification_parts(self, app_name_hint: str, app_id: str, texts: Iterable[str]) -> MessageData | None:
        pieces = self._clean_pieces((app_name_hint, app_id, *texts))
        if not pieces:
            return None
        haystack = " ".join(pieces).casefold()
        app_name = ""
        color = ColorToken(96, 186, 255)
        for tokens, name, token_color in self.APP_HINTS:
            if any(token.casefold() in haystack for token in tokens):
                app_name = name
                color = token_color
                break
        if not app_name:
            return None
        if not self._message_app_enabled(app_name):
            return None
        content = [piece for piece in self._clean_pieces(texts) if not self._is_noise(piece, app_name)]
        content = [piece for piece in content if piece.casefold() != app_name.casefold()]
        if not content:
            return None
        sender = app_name
        text = content[-1]
        if len(content) >= 2:
            sender = content[-2][:24] or app_name
        if self._is_noise(text, app_name):
            return None
        return MessageData(app=app_name, sender=sender, text=text[:96], name_color=color, avatar_color=color)

    def _collect_candidates(self) -> list[WindowCandidate]:
        candidates = []
        candidates.extend(self._uia_candidates())
        return candidates

    def _native_wechat_windows(self) -> list[NativeWindowCandidate]:
        if sys.platform != "win32":
            return []
        user32 = ctypes.windll.user32
        out: list[NativeWindowCandidate] = []
        seen: set[int] = set()

        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def add_window(hwnd: int):
            hwnd = int(hwnd)
            if hwnd in seen:
                return
            title = self._window_title(hwnd)
            class_name = self._window_class(hwnd)
            process_id = self._window_process_id(hwnd)
            process_path = self._process_path(process_id)
            hint = f"{title} {class_name} {process_path}".casefold()
            if not self._looks_like_wechat_window_hint(hint):
                return
            seen.add(hwnd)
            visible = bool(user32.IsWindowVisible(wintypes.HWND(hwnd)))
            out.append(
                NativeWindowCandidate(
                    hwnd=hwnd,
                    title=title,
                    class_name=class_name,
                    process_id=process_id,
                    process_path=process_path,
                    visible=visible,
                )
            )

        def callback(hwnd, _lparam):
            add_window(hwnd)
            return True

        try:
            user32.EnumWindows(enum_proc(callback), 0)
        except Exception:
            return out
        return out

    def _looks_like_wechat_window_hint(self, hint: str) -> bool:
        tokens = (
            "wechat",
            "weixin",
            "微信",
            "tencent\\wechat",
            "tencent/wechat",
            "wxwork",
            "wework",
            "wechatmainwndforpc",
        )
        return any(token in hint for token in tokens)

    def _window_process_id(self, hwnd: int) -> int:
        try:
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            return int(pid.value)
        except Exception:
            return 0

    def _process_path(self, process_id: int) -> str:
        if not process_id or sys.platform != "win32":
            return ""
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
        except Exception:
            return ""
        finally:
            kernel32.CloseHandle(handle)
        return ""

    def _set_wechat_native_diagnostics(self, windows: list[NativeWindowCandidate]):
        self._wechat_native_window_count = len(windows)
        labels: list[str] = []
        for window in windows[:8]:
            process = window.process_path.rsplit("\\", 1)[-1] if window.process_path else str(window.process_id or "")
            visible = "visible" if window.visible else "hidden"
            labels.append(f"{window.hwnd:x} {window.class_name or '-'} {window.title or '-'} {process} {visible}")
        self._wechat_native_windows = tuple(labels)

    def _wechat_main_native_windows(self, windows: list[NativeWindowCandidate]) -> list[NativeWindowCandidate]:
        mains: list[NativeWindowCandidate] = []
        for window in windows:
            title = window.title.casefold().strip()
            class_name = window.class_name.casefold()
            process = window.process_path.rsplit("\\", 1)[-1].casefold() if window.process_path else ""
            if process != "weixin.exe":
                continue
            if title not in {"微信", "weixin", "wechat"}:
                continue
            if class_name.startswith("qt") or class_name in {"wechatmainwndforpc", "mmui::mainwindow"}:
                mains.append(window)
        return mains or windows[:1]

    def _wechat_should_scan_sessions(self, total_unread: int, now: float, force_detail: bool = False) -> bool:
        if total_unread <= 0:
            self._wechat_detail_scan_reason = "no-unread"
            return False
        if force_detail and now - self._last_wechat_session_scan_at >= 1.8:
            self._wechat_detail_scan_reason = "notification-detail"
            return True
        if self._wechat_previous_total_unread_count is None:
            self._wechat_detail_scan_reason = "baseline"
            return True
        total_delta = total_unread - self._wechat_previous_total_unread_count
        if total_delta > 0 and now - self._last_wechat_session_scan_at >= 1.0:
            self._wechat_detail_scan_reason = "unread-increase"
            return True
        if total_unread > 0 and now - self._last_wechat_session_scan_at >= 24.0:
            self._wechat_detail_scan_reason = "resync"
            return True
        self._wechat_detail_scan_reason = "total-only"
        return False

    def _wechat_messages(self, now: float, force_detail: bool = False, suppress_summary: bool = False) -> list[MessageData]:
        if not self._wechat_notification_enabled():
            return []
        native_windows = self._native_wechat_windows()
        self._set_wechat_native_diagnostics(native_windows)
        sessions: list[WeChatSessionCandidate] = []
        window_count = 0
        main_windows = self._wechat_main_native_windows(native_windows)
        total_unread = self._uiautomation_wechat_total_unread(main_windows)
        try:
            if total_unread >= 0:
                window_count = len(main_windows)
                self._wechat_backend = "uiautomation-hwnd"
                if self._wechat_should_scan_sessions(total_unread, now, force_detail):
                    self._last_wechat_session_scan_at = now
                    sessions.extend(self._uiautomation_wechat_sessions(main_windows))
                    if not sessions:
                        sessions.extend(self._pywinauto_wechat_sessions(main_windows))
            else:
                try:
                    from pywinauto import Desktop
                except Exception as exc:
                    self._wechat_error = f"pywinauto unavailable: {exc}"
                    return []
                desktop = Desktop(backend="uia")
                windows = []
                for native in main_windows:
                    try:
                        windows.append(desktop.window(handle=native.hwnd))
                    except Exception:
                        continue
                if not windows and not native_windows:
                    windows = list(desktop.windows(visible_only=False))
                total_unread = 0
                for window in windows:
                    if not self._is_wechat_main_window(window):
                        continue
                    window_count += 1
                    total_unread = max(total_unread, self._wechat_total_unread_from_window(window))
                    if self._wechat_should_scan_sessions(total_unread, now, force_detail):
                        self._last_wechat_session_scan_at = now
                        sessions.extend(self._wechat_sessions_from_window(window))
                if native_windows and not sessions:
                    self._wechat_backend = "pywinauto-hwnd"
                else:
                    self._wechat_backend = "pywinauto"
        except Exception as exc:
            self._wechat_error = f"wechat uia scan failed: {exc}"
            self._set_wechat_diagnostics(window_count, sessions)
            return []

        self._set_wechat_diagnostics(window_count, sessions)
        self._wechat_total_unread_count = total_unread
        current: dict[str, str] = {session.session: self._wechat_session_key(session) for session in sessions}
        out: list[MessageData] = []
        total_delta = 0 if self._wechat_previous_total_unread_count is None else total_unread - self._wechat_previous_total_unread_count
        if total_delta != 0:
            self._wechat_last_total_change_at = now
        if self._wechat_initialized and total_delta > 0:
            message = self._message_from_wechat_unread(total_unread, sessions, self._wechat_seen_sessions, total_delta=total_delta)
            summary_message = message is not None and message.sender == message.app and not getattr(message, "avatar_image_bytes", b"")
            if summary_message and not self._wechat_notification_enabled():
                message = None
            if suppress_summary and message is not None and message.sender == message.app and not getattr(message, "avatar_image_bytes", b""):
                message = None
            if message is not None:
                out.append(message)
        self._wechat_previous_total_unread_count = total_unread
        if total_unread <= 0:
            self._wechat_seen_sessions.clear()
        elif sessions:
            self._wechat_seen_sessions = current
        else:
            self._wechat_seen_sessions.update(current)
        for session_name in list(self._wechat_seen_sessions):
            if session_name not in current and now - self._seen_at.get(session_name, now) > 30.0:
                self._wechat_seen_sessions.pop(session_name, None)
        self._wechat_initialized = True
        self._wechat_error = ""
        return out

    def _is_wechat_main_window(self, window) -> bool:
        try:
            info = window.element_info
            title = (window.window_text() or "").strip()
            class_name = str(getattr(info, "class_name", "") or "")
        except Exception:
            return False
        title_key = title.casefold()
        class_key = class_name.casefold()
        if class_name == "WeChatMainWndForPC":
            return True
        if class_name == "mmui::MainWindow" and title_key in {"微信", "weixin", "wechat"}:
            return True
        if class_key.startswith("qt") and title_key in {"微信", "weixin", "wechat"}:
            return True
        return class_key.startswith("wechat") and title_key in {"微信", "weixin", "wechat"}

    def _wechat_total_unread_from_window(self, window) -> int:
        try:
            toolbar = window.child_window(auto_id="MainView.main_tabbar", control_type="ToolBar")
            if not toolbar.exists(timeout=0.05):
                return 0
            children = toolbar.children()
            if not children:
                return 0
            element = children[0].element_info.element
            for property_id in (30159, 30007):
                try:
                    text = str(element.GetCurrentPropertyValue(property_id) or "")
                except Exception:
                    continue
                match = re.search(r"(\d{1,4})\s*(?:条|條)?\s*(?:新消息|未读|unread|new messages?)", text, re.IGNORECASE)
                if match:
                    return int(match.group(1))
        except Exception:
            return 0
        return 0

    def _pywinauto_wechat_sessions(self, native_windows: list[NativeWindowCandidate]) -> list[WeChatSessionCandidate]:
        try:
            from pywinauto import Desktop
        except Exception:
            return []
        sessions: list[WeChatSessionCandidate] = []
        try:
            desktop = Desktop(backend="uia")
            for native in native_windows[:2]:
                try:
                    window = desktop.window(handle=native.hwnd)
                except Exception:
                    continue
                sessions.extend(self._wechat_sessions_from_window(window))
                if sessions:
                    self._wechat_backend = "uiautomation-hwnd+pywinauto-detail"
                    break
        except Exception as exc:
            self._wechat_error = f"pywinauto detail scan failed: {exc}"
        return sessions

    def _uiautomation_wechat_total_unread(self, native_windows: list[NativeWindowCandidate]) -> int:
        try:
            import uiautomation as uia
        except Exception as exc:
            self._wechat_error = f"uiautomation unavailable: {exc}"
            return -1
        total = -1
        previous_timeout = getattr(uia, "TIME_OUT_SECOND", None)
        try:
            if hasattr(uia, "SetGlobalSearchTimeout"):
                uia.SetGlobalSearchTimeout(0.08)
            for native in native_windows:
                try:
                    control = uia.ControlFromHandle(native.hwnd)
                except Exception:
                    continue
                toolbar = self._uiautomation_find_by_auto_id(control, "MainView.main_tabbar", max_depth=6)
                if toolbar is None:
                    continue
                children = self._uia_children(toolbar)
                if not children:
                    continue
                for property_id in (30159, 30007):
                    try:
                        text = str(children[0].GetPropertyValue(property_id) or "")
                    except Exception:
                        continue
                    count = self._wechat_unread_count_from_texts([text])
                    if count:
                        return count
                total = max(total, 0)
        finally:
            try:
                if previous_timeout is not None and hasattr(uia, "SetGlobalSearchTimeout"):
                    uia.SetGlobalSearchTimeout(previous_timeout)
            except Exception:
                pass
        return total

    def _wechat_sessions_from_window(self, window) -> list[WeChatSessionCandidate]:
        conversation_list = self._wechat_conversation_list(window)
        if conversation_list is None:
            return []
        sessions: list[WeChatSessionCandidate] = []
        try:
            items = conversation_list.children(control_type="ListItem")
        except Exception:
            return []
        for item in list(items)[:36]:
            candidate = self._wechat_session_from_item(item)
            if candidate is not None:
                sessions.append(candidate)
        return sessions

    def _wechat_active_chat_messages_from_window(self, window) -> list[MessageData]:
        chat_list = self._wechat_message_list(window)
        if chat_list is None:
            return []
        try:
            items = chat_list.children(control_type="ListItem")
        except Exception:
            return []
        if not items:
            return []
        last_item = items[-1]
        runtime_id = self._runtime_id(last_item)
        if not runtime_id:
            return []
        chat_name = self._wechat_current_chat_name(window)
        if not chat_name:
            return []
        try:
            handle = int(getattr(window, "handle", 0) or 0)
        except Exception:
            handle = 0
        key = f"{handle}:{chat_name}"
        previous = self._wechat_seen_active_chats.get(key)
        self._wechat_seen_active_chats[key] = runtime_id
        if not self._wechat_initialized or previous in {None, runtime_id}:
            return []
        text = self._wechat_chat_message_text(last_item)
        if not text or self._is_noise(text, "微信"):
            return []
        color = ColorToken(75, 210, 110)
        return [MessageData(app="微信", sender=chat_name[:32], text=text[:96], name_color=color, avatar_color=color)]

    def _wechat_message_list(self, window):
        for kwargs in (
            {"auto_id": "chat_message_list", "control_type": "List"},
            {"title": "消息", "control_type": "List"},
            {"title": "Messages", "control_type": "List"},
        ):
            try:
                control = window.child_window(**kwargs)
                if control.exists(timeout=0.05):
                    return control
            except Exception:
                continue
        try:
            lists = window.descendants(control_type="List")
        except Exception:
            return None
        for control in lists:
            try:
                if (control.automation_id() or "") == "chat_message_list":
                    return control
            except Exception:
                continue
        for control in lists:
            try:
                if (control.window_text() or "").strip() in {"消息", "Messages"}:
                    return control
            except Exception:
                continue
        return None

    def _wechat_current_chat_name(self, window) -> str:
        try:
            for control in window.descendants(control_type="Text"):
                try:
                    automation_id = control.automation_id() or ""
                except Exception:
                    automation_id = ""
                text = (control.window_text() or "").strip()
                if text and automation_id.endswith("current_chat_name_label"):
                    return text[:32]
        except Exception:
            pass
        return ""

    def _wechat_chat_message_text(self, item) -> str:
        texts = self._wechat_item_texts(item)
        for text in reversed(texts):
            if not text:
                continue
            if re.search(r"\d{1,3}\s*(?:条|條)?\s*(?:新消息|new messages?|unread)", text, re.IGNORECASE):
                continue
            if self._looks_like_wechat_meta(text):
                continue
            return text
        return ""

    def _runtime_id(self, control) -> str:
        try:
            runtime_id = getattr(control.element_info, "runtime_id", None)
            if runtime_id:
                return ".".join(str(part) for part in runtime_id)
        except Exception:
            pass
        try:
            runtime_id = control.element_info.element.GetRuntimeId()
            if runtime_id:
                return ".".join(str(part) for part in runtime_id)
        except Exception:
            pass
        return ""

    def _uiautomation_wechat_sessions(self, native_windows: list[NativeWindowCandidate]) -> list[WeChatSessionCandidate]:
        try:
            import uiautomation as uia
        except Exception as exc:
            self._wechat_error = f"uiautomation unavailable: {exc}"
            return []

        controls = []
        previous_timeout = getattr(uia, "TIME_OUT_SECOND", None)
        try:
            if hasattr(uia, "SetGlobalSearchTimeout"):
                uia.SetGlobalSearchTimeout(0.2)
            for native in native_windows:
                try:
                    control = uia.ControlFromHandle(native.hwnd)
                    if control and getattr(control, "ControlTypeName", ""):
                        controls.append(control)
                except Exception:
                    continue
            if not controls:
                for class_name in ("WeChatMainWndForPC", "Qt5152QWindowIcon", "Chrome_WidgetWin_0"):
                    try:
                        control = uia.WindowControl(searchDepth=1, ClassName=class_name)
                        if control.Exists(0.05):
                            controls.append(control)
                    except Exception:
                        continue
            sessions: list[WeChatSessionCandidate] = []
            for control in controls[:4]:
                sessions.extend(self._uiautomation_sessions_from_window(control))
                if sessions:
                    break
            return sessions
        except Exception as exc:
            self._wechat_error = f"uiautomation scan failed: {exc}"
            return []
        finally:
            try:
                if previous_timeout is not None and hasattr(uia, "SetGlobalSearchTimeout"):
                    uia.SetGlobalSearchTimeout(previous_timeout)
            except Exception:
                pass

    def _uiautomation_sessions_from_window(self, control) -> list[WeChatSessionCandidate]:
        conversation_list = self._uiautomation_conversation_list(control)
        if conversation_list is None:
            return []
        sessions: list[WeChatSessionCandidate] = []
        for item in self._uia_children(conversation_list)[:36]:
            if getattr(item, "ControlTypeName", "") not in {"ListItemControl", "ButtonControl", "PaneControl", "CustomControl"}:
                continue
            raw_texts = self._uiautomation_item_texts(item)
            if not raw_texts:
                continue
            session = self._wechat_session_name(raw_texts)
            if not session:
                continue
            unread_count = self._wechat_unread_count_from_texts(raw_texts)
            text = self._wechat_last_session_text(session, raw_texts) or "收到新消息"
            sessions.append(WeChatSessionCandidate(session=session[:32], text=text[:96], unread_count=unread_count, raw_texts=tuple(raw_texts[:8])))
        return sessions

    def _uiautomation_conversation_list(self, control):
        direct = self._uiautomation_find_by_auto_id(control, "session_list", max_depth=8)
        if direct is not None:
            return direct
        lists = []
        stack = [(control, 0)]
        while stack and len(lists) < 20:
            node, depth = stack.pop(0)
            if depth > 6:
                continue
            control_type = getattr(node, "ControlTypeName", "")
            if control_type == "ListControl":
                lists.append(node)
            for child in self._uia_children(node)[:18]:
                stack.append((child, depth + 1))
        names = {"会话", "聊天", "Chats", "Chat"}
        for item in lists:
            if str(getattr(item, "Name", "") or "").strip() in names:
                return item
        for item in lists:
            name = str(getattr(item, "Name", "") or "").strip()
            children = self._uia_children(item)
            if name not in {"消息", "Messages"} and children:
                return item
        return None

    def _uiautomation_find_by_auto_id(self, control, automation_id: str, max_depth: int = 6):
        stack = [(control, 0)]
        while stack:
            node, depth = stack.pop(0)
            if str(getattr(node, "AutomationId", "") or "") == automation_id:
                return node
            if depth >= max_depth:
                continue
            for child in self._uia_children(node)[:32]:
                stack.append((child, depth + 1))
        return None

    def _uiautomation_item_texts(self, control) -> list[str]:
        values: list[str] = []
        stack = [(control, 0)]
        while stack and len(values) < 20:
            node, depth = stack.pop(0)
            name = str(getattr(node, "Name", "") or "").strip()
            if name:
                values.extend(self._split_text_pieces(name))
            if depth >= 5:
                continue
            for child in self._uia_children(node)[:12]:
                stack.append((child, depth + 1))
        return self._clean_pieces(values)

    def _uia_children(self, control) -> list:
        try:
            return list(control.GetChildren() or [])
        except Exception:
            return []

    def _wechat_conversation_list(self, window):
        names = ("会话", "聊天", "Chats", "Chat")
        try:
            for name in names:
                control = window.child_window(title=name, control_type="List")
                if control.exists(timeout=0.05):
                    return control
        except Exception:
            pass
        try:
            lists = window.descendants(control_type="List")
        except Exception:
            return None
        for control in lists:
            try:
                title = (control.window_text() or "").strip()
                if title in names:
                    return control
            except Exception:
                continue
        for control in lists:
            try:
                title = (control.window_text() or "").strip()
                if title not in {"消息", "Messages"} and control.children(control_type="ListItem"):
                    return control
            except Exception:
                continue
        return None

    def _wechat_session_from_item(self, item) -> WeChatSessionCandidate | None:
        raw_texts = self._wechat_item_texts(item)
        if not raw_texts:
            return None
        session = self._wechat_session_name(raw_texts)
        if not session:
            return None
        unread_count = self._wechat_unread_count_from_item(item, raw_texts)
        text = self._wechat_last_session_text(session, raw_texts)
        if not text:
            text = "收到新消息"
        return WeChatSessionCandidate(
            session=session[:32],
            text=text[:96],
            unread_count=unread_count,
            raw_texts=tuple(raw_texts[:8]),
            avatar_image_bytes=self._wechat_avatar_bytes_from_item(item) if unread_count > 0 else b"",
        )

    def _wechat_item_texts(self, item) -> list[str]:
        values: list[str] = []
        try:
            values.extend(self._split_text_pieces(item.window_text() or ""))
        except Exception:
            pass
        for control_type in ("Text", "Button"):
            try:
                controls = item.descendants(control_type=control_type)
            except Exception:
                controls = []
            for control in controls[:12]:
                try:
                    values.extend(self._split_text_pieces(control.window_text() or ""))
                except Exception:
                    pass
        return self._clean_pieces(values)

    def _split_text_pieces(self, text: str) -> list[str]:
        pieces = []
        for line in re.split(r"[\r\n]+", str(text or "")):
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                pieces.append(line)
        return pieces

    def _wechat_avatar_bytes_from_item(self, item) -> bytes:
        try:
            image = item.capture_as_image()
            if image is None:
                return b""
            width, height = image.size
            if width <= 0 or height <= 0:
                return b""
            side = max(28, min(52, height - 20, width // 4))
            left = max(0, min(14, width - side))
            top = max(0, (height - side) // 2)
            avatar = image.crop((left, top, left + side, top + side))
            buffer = io.BytesIO()
            avatar.save(buffer, format="PNG")
            return buffer.getvalue()
        except Exception:
            return b""

    def _wechat_session_name(self, texts: list[str]) -> str:
        for text in texts:
            if text in {"SessionListItem", "会话", "聊天"}:
                continue
            if self._looks_like_wechat_meta(text):
                continue
            cleaned = re.sub(r"\s*\d+\s*条新消息\s*", " ", text).strip()
            if cleaned:
                return cleaned
        return ""

    def _wechat_unread_count_from_item(self, item, texts: list[str]) -> int:
        count = self._wechat_unread_count_from_texts(texts)
        if count:
            return count
        try:
            for text_control in item.descendants(control_type="Text")[:12]:
                text = (text_control.window_text() or "").strip()
                if not re.fullmatch(r"\d{1,3}", text):
                    continue
                rect = text_control.rectangle()
                if rect.width() <= 34 and rect.height() <= 28:
                    return int(text)
        except Exception:
            pass
        return 0

    def _wechat_unread_count_from_texts(self, texts: list[str]) -> int:
        for text in texts:
            match = re.search(r"\[?\s*(\d{1,4})\s*(?:条|條)?\s*(?:新消息|未读|new messages?|unread)?\s*\]?", text, re.IGNORECASE)
            if match:
                suffix = re.sub(r"\s+", "", text.casefold())
                if any(token in suffix for token in ("新消息", "未读", "unread", "newmessage", "newmessages")) or re.fullmatch(r"\[?\s*\d{1,4}\s*(?:条|條)\s*\]?", text):
                    return int(match.group(1))
        for text in texts:
            if re.fullmatch(r"\d{1,3}", text):
                try:
                    value = int(text)
                except ValueError:
                    continue
                if 0 < value < 1000:
                    return value
        return 0

    def _wechat_last_session_text(self, session: str, texts: list[str]) -> str:
        candidates: list[str] = []
        for text in texts:
            if text == session or text in {"SessionListItem", "会话", "聊天"}:
                continue
            if self._looks_like_wechat_meta(text):
                continue
            if re.search(r"\[?\s*\d{1,4}\s*(?:条|條)?\s*(?:新消息|未读|new messages?|unread)?\s*\]?", text, re.IGNORECASE):
                suffix = re.sub(r"\s+", "", text.casefold())
                if any(token in suffix for token in ("新消息", "未读", "unread", "newmessage", "newmessages")) or re.fullmatch(r"\[?\s*\d{1,4}\s*(?:条|條)\s*\]?", text):
                    continue
            if re.fullmatch(r"\[?\s*\d{1,4}\s*(?:条|條)\s*\]?", text):
                continue
            if re.fullmatch(r"\d{1,3}", text):
                continue
            candidates.append(text)
        if not candidates:
            return ""
        text = candidates[-1]
        match = re.match(r"([^:：]{1,24})[:：]\s*(.+)", text)
        if match:
            return f"{match.group(1)}: {match.group(2)}"
        return text

    def _looks_like_wechat_meta(self, text: str) -> bool:
        folded = text.casefold().strip()
        if not folded:
            return True
        if folded in {"sessionlistitem", "免打扰", "置顶", "已置顶", "pinned", "muted"}:
            return True
        if folded in {"昨天", "今天", "前天", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日", "星期天"}:
            return True
        return bool(re.fullmatch(r"(昨天|星期[一二三四五六日天]|周[一二三四五六日天]|上午|下午)?\s*\d{1,2}:\d{2}", text))

    def _message_from_wechat_session(self, session: WeChatSessionCandidate) -> MessageData | None:
        if not session.session or not session.text:
            return None
        color = ColorToken(75, 210, 110)
        return MessageData(app="微信", sender=session.session, text=session.text, name_color=color, avatar_color=color, avatar_image_bytes=session.avatar_image_bytes)

    def _message_from_wechat_unread(
        self,
        total_unread: int,
        sessions: list[WeChatSessionCandidate],
        previous_sessions: dict[str, str] | None = None,
        total_delta: int = 0,
    ) -> MessageData | None:
        color = ColorToken(75, 210, 110)
        unread_sessions = [session for session in sessions if session.unread_count > 0]
        if unread_sessions:
            previous_sessions = previous_sessions or {}
            changed_sessions = [
                session
                for session in unread_sessions
                if previous_sessions.get(session.session) != self._wechat_session_key(session)
            ]
            if not changed_sessions and (len(unread_sessions) > 1 or total_delta > 1):
                count = total_delta if total_delta > 0 else total_unread
                return MessageData(app="微信", sender="微信", text=f"{count}条新消息", name_color=color, avatar_color=color)
            session = changed_sessions[0] if changed_sessions else unread_sessions[0]
            text = session.text or f"{total_unread}条新消息"
            return MessageData(app="微信", sender=session.session[:32], text=text[:96], name_color=color, avatar_color=color, avatar_image_bytes=session.avatar_image_bytes)
        if total_unread > 0:
            return MessageData(app="微信", sender="微信", text=f"{total_unread}条新消息", name_color=color, avatar_color=color)
        return None

    def _wechat_session_key(self, session: WeChatSessionCandidate) -> str:
        return f"{session.unread_count}\0{session.text}\0{'|'.join(session.raw_texts[:4])}"

    def _set_wechat_diagnostics(self, window_count: int, sessions: list[WeChatSessionCandidate]):
        self._wechat_window_count = window_count
        self._wechat_session_count = len(sessions)
        self._wechat_unread_count = sum(1 for session in sessions if session.unread_count > 0)
        debug_items = []
        for session in sessions[:6]:
            marker = f"{session.unread_count}x" if session.unread_count else "0x"
            debug_items.append(f"{marker} {session.session}: {session.text}")
        self._wechat_debug_items = tuple(debug_items)

    def _uia_candidates(self) -> list[WindowCandidate]:
        try:
            from pywinauto import Desktop
        except Exception as exc:
            self._last_error = f"pywinauto unavailable: {exc}"
            return []
        out: list[WindowCandidate] = []
        try:
            desktop = Desktop(backend="uia")
            for window in desktop.windows():
                info = window.element_info
                title = window.window_text() or ""
                class_name = str(getattr(info, "class_name", "") or "")
                process_name = str(getattr(info, "name", "") or "")
                hint = f"{title} {class_name} {process_name}".lower()
                should_scan = any(token in hint for token in ("toast", "notification", "xaml", "shell", "windows.ui", "windows.ui.core"))
                if not should_scan:
                    continue
                texts = self._window_texts(window)
                if title or texts:
                    out.append(WindowCandidate(title=title, class_name=class_name, process_name=process_name, texts=tuple(texts)))
        except Exception as exc:
            self._last_error = f"uia scan failed: {exc}"
        return out

    def _window_texts(self, window) -> list[str]:
        texts: list[str] = []
        try:
            for control in window.descendants(control_type="Text"):
                text = (control.window_text() or "").strip()
                if text:
                    texts.append(text)
                if len(texts) >= 12:
                    break
        except Exception:
            pass
        return texts

    def _foreground_candidate(self) -> WindowCandidate | None:
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            return WindowCandidate(title=self._window_title(hwnd), class_name=self._window_class(hwnd), texts=())
        except Exception:
            return None

    def _message_from_candidate(self, candidate: WindowCandidate) -> MessageData | None:
        pieces = self._clean_pieces((candidate.title, candidate.process_name, candidate.class_name, *candidate.texts))
        if not pieces:
            return None
        haystack = " ".join(pieces).casefold()
        app_name = ""
        color = ColorToken(96, 186, 255)
        for tokens, name, token_color in self.APP_HINTS:
            if any(token.casefold() in haystack for token in tokens):
                app_name = name
                color = token_color
                break
        if not app_name:
            return None
        if not self._message_app_enabled(app_name):
            return None

        content = [piece for piece in pieces if not self._is_noise(piece, app_name)]
        if not content:
            return None
        sender = app_name
        text = content[-1]
        if len(content) >= 2:
            sender = content[-2][:24] or app_name
        if text == sender and len(content) >= 3:
            text = content[-3]
        text = text[:96]
        if not text or self._is_noise(text, app_name):
            return None
        return MessageData(
            app=app_name,
            sender=sender,
            text=text,
            name_color=color,
            avatar_color=color,
        )

    def _clean_pieces(self, values: Iterable[str]) -> list[str]:
        pieces = []
        for value in values:
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            if text and text.casefold() not in self.NOISE_TEXT and text not in pieces:
                pieces.append(text)
        return pieces

    def _is_noise(self, text: str, app_name: str) -> bool:
        folded = text.casefold().strip()
        if folded in self.NOISE_TEXT:
            return True
        if folded == app_name.casefold():
            return True
        return folded in {"windows 输入体验", "program manager"}

    def _window_title(self, hwnd: int) -> str:
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, length + 1)
        return buffer.value.strip()

    def _window_class(self, hwnd: int) -> str:
        user32 = ctypes.windll.user32
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(wintypes.HWND(hwnd), buffer, 256)
        return buffer.value.strip()
