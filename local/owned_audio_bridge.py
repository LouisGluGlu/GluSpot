"""Qt-thread device controls and decoded PCM, without external capture."""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6 import QtMultimedia
from PySide6.QtMultimedia import QAudioFormat, QMediaDevices


class OwnedAudioBridge(QObject):
    requested = Signal(object)

    def __init__(self, owned, parent=None, *, media_devices=None, buffer_output=None):
        super().__init__(parent or owned)
        self.owned = owned
        self._devices = media_devices or QMediaDevices
        self._controller = None
        self._closed = False
        self._epoch = 0
        self._direct = False
        self._original_muted = None
        self._last_buffer_end = None
        self._last_position = None
        self._restarting = False
        self._owner_token = self.media_token()
        self.requested.connect(self._apply, Qt.ConnectionType.QueuedConnection)
        buffer_output_type = getattr(QtMultimedia, "QAudioBufferOutput", None)
        self.supported = buffer_output_type is not None and callable(getattr(owned.player, "setAudioBufferOutput", None))
        self.output = None
        if self.supported:
            format_ = QAudioFormat()
            format_.setSampleRate(44100)
            format_.setChannelCount(2)
            format_.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            self.output = buffer_output or buffer_output_type(format_, self)
            self.output.audioBufferReceived.connect(self._buffer)
            owned.player.setAudioBufferOutput(self.output)
        owned.changed.connect(self._owner_changed)
        if hasattr(owned.player, "positionChanged"):
            owned.player.positionChanged.connect(self._position_changed)

    def attach(self, controller):
        self._controller = controller

    def media_token(self):
        track = self.owned.track
        return (getattr(track, "track_id", ""), getattr(self.owned, "_epoch", 0))

    @staticmethod
    def endpoint_id(device):
        try:
            value = bytes(device.id()).decode("utf-8", "strict")
            return value if value and not any(ord(c) < 32 for c in value) else ""
        except (ValueError, TypeError, UnicodeDecodeError):
            return ""

    def _invoke(self, operation, value=None, *, token=None):
        if self._closed:
            return False
        request = [operation, value, self.media_token() if token is None else token,
            time.monotonic() + 1.5, threading.Event(), False, threading.Event()]
        if QThread.currentThread() == self.thread():
            self._apply(request)
        else:
            self.requested.emit(request)
            if not request[4].wait(1.5):
                request[6].set()
                return False
        return request[5]

    def current_device(self):
        return self._invoke("current")

    def select_device(self, endpoint, token=None):
        return self._invoke("select", str(endpoint), token=token)

    def set_direct(self, enabled, epoch, token=None):
        return self._invoke("direct", (bool(enabled), epoch), token=token)

    @Slot(object)
    def _apply(self, request):
        operation, value, token, deadline, done, _, cancelled = request
        try:
            release = operation == "direct" and value[0] is False
            # Releasing an owned mute remains necessary after a busy GUI's
            # request deadline. Its epoch check below prevents a late release
            # from touching a newer session, even after the track changes.
            if self._closed or (not release and
                    (cancelled.is_set() or time.monotonic() >= deadline or token != self.media_token())):
                return
            audio = self.owned.audio
            if operation == "current":
                request[5] = self.endpoint_id(audio.device()) if hasattr(audio, "device") else ""
            elif operation == "select":
                device = next((d for d in self._devices.audioOutputs()
                    if self.endpoint_id(d).casefold() == value.casefold()), None)
                if device is None or not hasattr(audio, "setDevice"):
                    return
                audio.setDevice(device)
                request[5] = self.endpoint_id(audio.device()).casefold() == value.casefold()
            elif operation == "direct":
                enabled, epoch = value
                if enabled:
                    if not self.supported or not self.owned.track or not hasattr(audio, "setMuted"):
                        return
                    if not self._direct:
                        self._original_muted = audio.isMuted()
                    audio.setMuted(True)
                    self._epoch, self._direct = epoch, True
                    self._last_buffer_end = None
                    self._restarting = False
                else:
                    # A stop for an older session cannot unmute its replacement.
                    if self._direct and epoch not in {0, self._epoch}:
                        return
                    if self._direct and self._original_muted is not None and audio.isMuted():
                        audio.setMuted(self._original_muted)
                    self._direct, self._epoch, self._original_muted = False, 0, None
                    self._last_buffer_end = None
                    self._restarting = False
                request[5] = True
        finally:
            done.set()

    @Slot()
    def _owner_changed(self):
        token = self.media_token()
        if token != self._owner_token:
            self._owner_token = token
            self._last_buffer_end = None
            self._last_position = None
            self._restarting = self._direct
            if self._controller is not None:
                self._controller.media_changed(token)

    @Slot(int)
    def _position_changed(self, milliseconds):
        now = time.monotonic()
        previous, self._last_position = self._last_position, (milliseconds, now)
        if not self._direct or self._controller is None or previous is None or self._restarting:
            return
        if self.media_token() != self._owner_token:
            self._owner_changed()
            return
        old_position, sampled = previous
        rate = self.owned.player.playbackRate() if hasattr(self.owned.player, "playbackRate") else 1.0
        expected = max(0.0, now - sampled) * 1000 * max(0.0, rate)
        if milliseconds < old_position - 500 or milliseconds - old_position > expected + 800:
            self._restarting = True
            self._last_buffer_end = None
            self._controller.media_changed(self.media_token(), force=True)

    @Slot(object)
    def _buffer(self, buffer):
        if self._closed or not self._direct or self._restarting or self._controller is None or not buffer.isValid() or not buffer.frameCount():
            return
        if not self._controller.accepts_pcm(self._epoch):
            return
        format_ = buffer.format()
        if (format_.sampleRate() != 44100 or format_.channelCount() != 2
                or format_.sampleFormat() != QAudioFormat.SampleFormat.Int16
                or buffer.byteCount() != buffer.frameCount() * 4 or buffer.byteCount() > 176400):
            self._controller.pcm_failed("format", self._epoch)
            return
        start = buffer.startTime()
        if (start >= 0 and self._last_buffer_end is not None
                and abs(start - self._last_buffer_end) > 100000):
            # A seek starts a new native presentation epoch, never appends to
            # the old delayed history. Ordinary contiguous chunks retain it.
            self._last_buffer_end = None
            self._restarting = True
            self._controller.media_changed(self.media_token(), force=True)
            return
        self._last_buffer_end = start + buffer.duration() if start >= 0 else None
        content = bytes(buffer.constData())
        for offset in range(0, len(content), 65536):
            if not self._controller.submit_pcm(content[offset:offset + 65536], self._epoch):
                if self._controller.accepts_pcm(self._epoch):
                    self._controller.pcm_failed("backpressure", self._epoch)
                break

    def close(self):
        if self._closed:
            return
        self.set_direct(False, 0)
        self._closed = True
        if self.supported:
            self.owned.player.setAudioBufferOutput(None)
        self._controller = None
