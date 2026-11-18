"""Software update: check at start, notify, download, and install when GluSpot quits.

Settings (all off by default, confirmed by the user on 2026-11-18):
- ``v2_update_check_enabled``: check once each time GluSpot starts.
- ``v2_update_auto_enabled``: only with checking on; download in the
  background and install silently when GluSpot quits, without asking.
- ``v2_update_ignored_version``: a version the user chose to ignore.

Network work runs on short-lived threads; results come back as signals.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

from api import app_update

_log = logging.getLogger("GluSpot.update")

CHECK_KEY = "v2_update_check_enabled"
AUTO_KEY = "v2_update_auto_enabled"
IGNORED_KEY = "v2_update_ignored_version"
START_DELAY_MS = 8000


def update_folder() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "GluSpot" / "updates"


class UpdateController(QObject):
    """States: idle, checking, current, available, ignored, downloading, ready, installing, error."""

    changed = Signal()
    notify = Signal(str)
    _checked = Signal(str, object, bool, str)   # outcome, release, manual, error
    _progress = Signal(int, int)
    _downloaded = Signal(object, str)          # installer path or None, error

    def __init__(self, settings, current_version: str, parent=None, *, check=None, download=None,
                 kind=None, install_dir=None, folder: Path | None = None, starter=None, opener=None):
        super().__init__(parent)
        self.settings = settings
        self.current_version = current_version
        self._check = check or app_update.check
        self._download = download or app_update.download
        self._install_dir = install_dir if install_dir is not None else app_update.registered_install_path()
        self._kind = kind or app_update.install_kind(registered_path=self._install_dir)
        self._folder = Path(folder) if folder else update_folder()
        self._starter = starter or app_update.start_installer
        self._open_url = opener or self._open_in_browser
        self.state = "idle"
        self.release = None
        self.installer: Path | None = None
        self.received = self.total = 0
        self.message = ""
        self.quit_callback = None
        self._checked.connect(self._check_done)
        self._progress.connect(self._download_progress)
        self._downloaded.connect(self._download_done)

    # ---- settings -------------------------------------------------------------
    def _get(self, key, default):
        getter = getattr(self.settings, "get", None)
        return getter(key, default) if callable(getter) else default

    def _set(self, key, value):
        setter = getattr(self.settings, "set", None)
        if callable(setter):
            setter(key, value)

    def check_enabled(self) -> bool:
        return bool(self._get(CHECK_KEY, False))

    def auto_enabled(self) -> bool:
        return self.check_enabled() and bool(self._get(AUTO_KEY, False))

    def set_check_enabled(self, enabled: bool):
        self._set(CHECK_KEY, bool(enabled))
        if not enabled:
            self._set(AUTO_KEY, False)  # auto update needs checking
        self.changed.emit()

    def set_auto_enabled(self, enabled: bool):
        self._set(AUTO_KEY, bool(enabled) and self.check_enabled())
        if self.auto_enabled() and self.state == "available" and self._kind == "installed":
            self.download()
        self.changed.emit()

    def kind(self) -> str:
        return self._kind

    # ---- checking ---------------------------------------------------------------
    def start(self):
        """At application start: one check, if the user turned checking on."""
        if self.check_enabled():
            QTimer.singleShot(START_DELAY_MS, lambda: self.check(manual=False))

    def check(self, *, manual: bool = True):
        if self.state in {"checking", "downloading", "installing"}:
            return False
        self._set_state("checking", "正在检查更新…")
        ignored = str(self._get(IGNORED_KEY, "") or "")

        def work():
            try:
                outcome, release = self._check(self.current_version, ignored, manual=manual)
                self._checked.emit(outcome, release, manual, "")
            except Exception as error:  # noqa: BLE001 - offline or GitHub down: report, never crash
                self._checked.emit("error", None, manual, type(error).__name__)

        threading.Thread(target=work, name="GluSpotUpdateCheck", daemon=True).start()
        return True

    def _check_done(self, outcome, release, manual, error):
        self.release = release
        _log.info("update stage=check result=%s manual=%s", outcome, "yes" if manual else "no")
        if outcome == "error":
            self._set_state("error", "没能连上更新服务器，稍后再试。")
            return
        if outcome in {"current", "none"}:
            self._set_state("current", f"已是最新版本（{self.current_version}）。")
            return
        if outcome == "ignored":
            self._set_state("ignored", f"已忽略 {release.version}。")
            return
        self._set_state("available", f"GluSpot {release.version} 可以更新。")
        if not manual:
            self.notify.emit(f"GluSpot {release.version} 可以更新，在设置 → 高级 → 软件更新里查看。")
        if self.auto_enabled() and self._kind == "installed":
            self.download()

    # ---- downloading and installing ---------------------------------------------
    def download(self):
        release = self.release
        if release is None or self._kind != "installed" or self.state in {"downloading", "installing"}:
            return False
        self.received, self.total = 0, release.installer_size
        self._set_state("downloading", "正在下载更新…")

        def work():
            try:
                path = self._download(release, self._folder,
                                      progress=lambda got, total: self._progress.emit(int(got), int(total)))
                self._downloaded.emit(path, "")
            except Exception as error:  # noqa: BLE001 - reported in settings, retried next time
                self._downloaded.emit(None, type(error).__name__)

        threading.Thread(target=work, name="GluSpotUpdateDownload", daemon=True).start()
        return True

    def _download_progress(self, received, total):
        self.received, self.total = received, total
        if total:
            self.message = f"正在下载更新… {received * 100 // max(1, total)}%"
        self.changed.emit()

    def _download_done(self, path, error):
        if path is None:
            _log.info("update stage=download result=error kind=%s", error)
            self._set_state("error", "更新没有下载完整，下次再试。")
            return
        self.installer = Path(path)
        _log.info("update stage=download result=ok")
        if self.auto_enabled():
            self._set_state("ready", f"{self.release.version} 已下载，退出 GluSpot 时会自动装好。")
        else:
            self._set_state("ready", f"{self.release.version} 已下载，点「更新」安装并重新打开 GluSpot。")

    def update_now(self):
        """The 更新 button: install now and reopen; a portable copy opens the download page."""
        if self.release is None:
            return False
        if self._kind != "installed":
            self._open_url(self.release.page_url or app_update.RELEASES_PAGE)
            self._set_state(self.state, "便携版不会自动替换文件，已打开下载页。")
            return True
        if self.installer is None:
            self._install_after_download = True
            return self.download()
        return self._install(relaunch=True)

    def _install(self, *, relaunch: bool) -> bool:
        if self.installer is None or not self.installer.is_file() or not self._install_dir:
            return False
        self._set_state("installing", "正在安装更新…")
        _log.info("update stage=install relaunch=%s", "yes" if relaunch else "no")
        self._starter(self.installer, self._install_dir, relaunch=relaunch)
        if relaunch and callable(self.quit_callback):
            self.quit_callback()
        return True

    def ignore(self):
        if self.release is None:
            return
        self._set(IGNORED_KEY, self.release.version)
        self._set_state("ignored", f"已忽略 {self.release.version}，之后启动不会再提醒这一版。")

    def on_quit(self):
        """GluSpot is quitting: with automatic update on, a downloaded update installs now."""
        if self.state == "ready" and self.auto_enabled() and self._kind == "installed":
            self._install(relaunch=False)

    # ---- state ------------------------------------------------------------------
    def _set_state(self, state: str, message: str = ""):
        self.state = state
        self.message = message
        if state == "ready" and getattr(self, "_install_after_download", False):
            self._install_after_download = False
            self.changed.emit()
            self._install(relaunch=True)
            return
        self.changed.emit()

    @staticmethod
    def _open_in_browser(url: str):
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))
