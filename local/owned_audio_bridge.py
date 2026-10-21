"""Qt-thread device controls and decoded PCM, without external capture."""
from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6 import QtMultimedia
from PySide6.QtMultimedia import QAudioFormat, QMediaDevices
from api.audio_analyzer import analyze_frequency_block
from api.models import AudioFrame


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
        self._direct_token = None
        self._original_muted = None
        self._owns_mute = False
        self._changing_mute = False
        self._mute_committed = False
        self._last_buffer_end = None
        self._last_position = None
        self._restarting = False
        self._owner_token = self.media_token()
        self._dj_frames = deque(maxlen=96)
        self._dj_lock = threading.RLock()
        self._dj_smooth = [0.0, 0.0, 0.0]
        self._voice_token = None
        self._voice_pcm = deque()
        self._voice_intervals = deque()
        self._music_pcm = deque()
        self._music_pcm_bytes = 0
        self._hold_paused_music = False
        self._voice_original_muted = None
        self._voice_owns_mute = False
        self._voice_mute_committed = False
        self._changing_voice_mute = False
        self.requested.connect(self._apply, Qt.ConnectionType.QueuedConnection)
        buffer_output_type = getattr(QtMultimedia, "QAudioBufferOutput", None)
        self.supported = buffer_output_type is not None and callable(getattr(owned.player, "setAudioBufferOutput", None))
        self.output = None
        self.dj_output = None
        if self.supported:
            format_ = QAudioFormat()
            format_.setSampleRate(44100)
            format_.setChannelCount(2)
            format_.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            self.output = buffer_output or buffer_output_type(format_, self)
            self.output.audioBufferReceived.connect(self._buffer)
            owned.player.setAudioBufferOutput(self.output)
            voice_player = getattr(owned, "dj_player", None)
            if callable(getattr(voice_player, "setAudioBufferOutput", None)):
                self.dj_output = buffer_output_type(format_, self)
                self.dj_output.audioBufferReceived.connect(self._dj_buffer)
                voice_player.setAudioBufferOutput(self.dj_output)
        owned.changed.connect(self._owner_changed)
        if hasattr(owned.audio, "mutedChanged"):
            owned.audio.mutedChanged.connect(self._muted_changed)
        voice_audio = getattr(owned, "dj_audio", None)
        if hasattr(voice_audio, "mutedChanged"):
            voice_audio.mutedChanged.connect(self._voice_muted_changed)
        if hasattr(owned.player, "positionChanged"):
            owned.player.positionChanged.connect(self._position_changed)

    def attach(self, controller):
        self._controller = controller

    def media_token(self):
        track = self.owned.track
        return (getattr(track, "track_id", ""), getattr(self.owned, "_epoch", 0))

    def get_personal_dj_audio_frame(self):
        speaking = getattr(self.owned, "is_personal_dj_speaking", None)
        if self._closed or not callable(speaking) or not speaking() or self.media_token() != self._owner_token:
            return None
        # Qt can decode ahead. Select the sample at its real presentation
        # clock, not the most recently decoded buffer or a simulated pulse.
        voice_player = getattr(self.owned, "dj_player", self.owned.player)
        position = voice_player.position() / 1000.0
        with self._dj_lock:
            frames = tuple(self._dj_frames)
        for start, end, frame in reversed(frames):
            if start <= position < end:
                return frame
        return None

    def _sample_personal_dj_buffer(self, buffer):
        speaking = getattr(self.owned, "is_personal_dj_speaking", None)
        if not callable(speaking) or not speaking() or self.media_token() != self._owner_token:
            return
        format_ = buffer.format()
        if (format_.sampleRate() != 44100 or format_.channelCount() != 2
                or format_.sampleFormat() != QAudioFormat.SampleFormat.Int16
                or buffer.byteCount() != buffer.frameCount() * 4 or buffer.byteCount() > 176400 or buffer.startTime() < 0):
            return
        samples = np.frombuffer(buffer.constData(), dtype="<i2").reshape(-1, 2)
        start = buffer.startTime() / 1000000.0
        # At most twenty small transforms per one-second Qt buffer; the
        # retained timeline stores frequency values only, never raw audio.
        for offset in range(0, len(samples), 2205):
            block = samples[offset:offset + 2205].astype(np.float32) / 32768.0
            frame = analyze_frequency_block(block, 44100, np, self._dj_smooth)
            if frame is None:
                self._dj_smooth[:] = [0.0, 0.0, 0.0]
            with self._dj_lock:
                self._dj_frames.append((start + offset / 44100.0,
                    start + min(len(samples), offset + 2205) / 44100.0, frame or AudioFrame(0.0, 0.0, 0.0)))

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

    def set_direct(self, enabled, epoch, token=None, *, muted=True):
        return self._invoke("direct", (bool(enabled), epoch, bool(muted)), token=token)

    def prepare_direct(self, epoch, token=None):
        """Reserve the owned outputs and decoder clock before receiver setup."""
        return self._invoke("prepare", epoch, token=token)

    def _hold_decoder(self, preparing):
        hold = getattr(self.owned, "set_output_preparing", None)
        if callable(hold):
            hold(preparing)

    def commit_direct_mute(self, epoch, token=None):
        """Mute the Qt output after this exact feeding session can present PCM."""
        return self._invoke("commit_mute", epoch, token=token)

    def _commit_mute(self, audio):
        if self._mute_committed:
            return True
        # A user or another owner may have muted Qt during unmuted prefill.
        # Only a false-to-true change performed here belongs to this bridge.
        if not audio.isMuted():
            self._original_muted = False
            self._owns_mute = True
            self._changing_mute = True
            try:
                audio.setMuted(True)
            finally:
                self._changing_mute = False
            if not audio.isMuted():
                self._owns_mute, self._original_muted = False, None
                return False
        self._mute_committed = True
        return True

    def _release_mute(self, audio):
        if self._owns_mute and self._original_muted is not None and audio.isMuted():
            self._changing_mute = True
            try:
                audio.setMuted(self._original_muted)
            finally:
                self._changing_mute = False
        self._owns_mute, self._original_muted, self._mute_committed = False, None, False

    def _commit_voice_mute(self):
        audio = getattr(self.owned, "dj_audio", None)
        if audio is None:
            return True
        if not hasattr(audio, "isMuted") or not hasattr(audio, "setMuted"):
            return False
        if self._voice_mute_committed:
            return True
        if not audio.isMuted():
            self._voice_original_muted = False
            self._voice_owns_mute = True
            self._changing_voice_mute = True
            try:
                audio.setMuted(True)
            finally:
                self._changing_voice_mute = False
            if not audio.isMuted():
                self._voice_owns_mute, self._voice_original_muted = False, None
                return False
        self._voice_mute_committed = True
        return True

    def _release_voice_mute(self):
        audio = getattr(self.owned, "dj_audio", None)
        if (audio is not None and self._voice_owns_mute and self._voice_original_muted is not None
                and audio.isMuted()):
            self._changing_voice_mute = True
            try:
                audio.setMuted(self._voice_original_muted)
            finally:
                self._changing_voice_mute = False
        self._voice_owns_mute, self._voice_original_muted, self._voice_mute_committed = False, None, False

    def _commit_outputs_mute(self, audio):
        if not self._commit_mute(audio):
            return False
        if not self._commit_voice_mute():
            self._release_mute(audio)
            return False
        return True

    @Slot(bool)
    def _voice_muted_changed(self, muted):
        if not self._changing_voice_mute and not muted:
            self._voice_owns_mute, self._voice_original_muted = False, None

    @Slot(bool)
    def _muted_changed(self, muted):
        if not self._changing_mute and not muted:
            # An explicit external unmute ends our mute ownership. A later
            # external mute must not be undone when this session stops.
            self._owns_mute, self._original_muted = False, None

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
                voice_audio = getattr(self.owned, "dj_audio", None)
                if voice_audio is not None and hasattr(voice_audio, "setDevice"):
                    voice_audio.setDevice(device)
                request[5] = (self.endpoint_id(audio.device()).casefold() == value.casefold()
                    and (voice_audio is None or not hasattr(voice_audio, "device")
                         or self.endpoint_id(voice_audio.device()).casefold() == value.casefold()))
            elif operation == "prepare":
                if not self.supported or not self.owned.track or not hasattr(audio, "setMuted"):
                    return
                if not self._commit_outputs_mute(audio):
                    return
                self._epoch, self._direct, self._direct_token = value, True, token
                self._last_buffer_end = None
                self._restarting = True
                self._hold_decoder(True)
                request[5] = True
            elif operation == "direct":
                enabled, epoch, muted = value
                if enabled:
                    if not self.supported or not self.owned.track or not hasattr(audio, "setMuted"):
                        return
                    new_session = not self._direct or (epoch, token) != (self._epoch, self._direct_token)
                    if new_session:
                        if not muted:
                            self._release_mute(audio)
                            self._release_voice_mute()
                        self._mute_committed = False
                    if muted and not self._commit_outputs_mute(audio):
                        return
                    self._epoch, self._direct = epoch, True
                    self._direct_token = token
                    self._last_buffer_end = None
                    self._restarting = False
                    self._hold_decoder(False)
                else:
                    # A stop for an older session cannot unmute its replacement.
                    if self._direct and epoch not in {0, self._epoch}:
                        return
                    self._release_mute(audio)
                    self._release_voice_mute()
                    self._direct, self._epoch, self._direct_token = False, 0, None
                    self._last_buffer_end = None
                    self._restarting = False
                    self._music_pcm.clear()
                    self._music_pcm_bytes = 0
                    self._voice_pcm.clear()
                    self._voice_intervals.clear()
                    self._hold_paused_music = False
                    self.owned._dj_pcm_clock_floor = 0.0
                    self._hold_decoder(False)
                request[5] = True
            elif operation == "commit_mute":
                if (not self._direct or self._restarting or value != self._epoch
                        or token != self._direct_token or not hasattr(audio, "setMuted")):
                    return
                request[5] = self._commit_outputs_mute(audio)
        finally:
            done.set()

    @Slot()
    def _owner_changed(self):
        token = self.media_token()
        if token != self._owner_token:
            self._owner_token = token
            self._last_buffer_end = None
            self._last_position = None
            with self._dj_lock:
                self._dj_frames.clear()
            self._dj_smooth[:] = [0.0, 0.0, 0.0]
            self._voice_pcm.clear()
            self._voice_intervals.clear()
            self._music_pcm.clear()
            self._music_pcm_bytes = 0
            self._hold_paused_music = False
            self.owned._dj_pcm_clock_floor = 0.0
            self._restarting = self._direct
            if self._direct:
                # Do this synchronously on the source change, before the route
                # worker tears down/re-authenticates. The next decoder may be
                # loaded from a prefetched file in this same Qt turn.
                self._commit_outputs_mute(self.owned.audio)
                self._hold_decoder(True)
            if self._controller is not None:
                self._controller.media_changed(token)
        voice_token = getattr(self.owned, "personal_dj_audio_token", None)
        voice_token = voice_token() if callable(voice_token) else None
        if voice_token != self._voice_token:
            if self._voice_intervals and self._voice_intervals[-1][2] is None:
                end = round(getattr(self.owned, "_dj_music_end_seconds", 0.0) * 44100)
                prior = self._voice_intervals[-1]
                self._voice_intervals[-1] = (prior[0], prior[1], max(prior[1], end))
                # Cancellation clips already decoded future speech; it cannot
                # leak through after a toggle/seek or into the following part.
                clipped = deque()
                for start, samples in self._voice_pcm:
                    count = min(len(samples), max(0, end - start))
                    if count:
                        clipped.append((start, samples[:count]))
                self._voice_pcm = clipped
            self._voice_token = voice_token
            with self._dj_lock:
                self._dj_frames.clear()
            self._dj_smooth[:] = [0.0, 0.0, 0.0]
        speaking = getattr(self.owned, "is_personal_dj_speaking", None)
        if (getattr(self.owned, "_dj_overlay_voices", ())
                and not getattr(self.owned, "_dj_source_ready", True)
                and getattr(self.owned, "_dj_music_gain", None) is not None
                and getattr(self.owned, "_dj_restore_start_seconds", None) is None
                and (not self._voice_intervals or self._voice_intervals[-1][0] != voice_token)):
            # A multipart source can take seconds to prepare. Keep streaming
            # ducked music through the gap instead of blocking the group clock.
            start = round(getattr(self.owned, "_dj_music_end_seconds", 0.0) * 44100)
            self._voice_intervals.append((voice_token, start, None))
        paused_preview = (getattr(self.owned, "_dj_overlay_is_preview", False)
            and self.owned.player.playbackState() != QtMultimedia.QMediaPlayer.PlaybackState.PlayingState)
        if paused_preview:
            self._hold_paused_music = True
        elif self._hold_paused_music and self.owned.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState:
            self._hold_paused_music = False
        if callable(speaking) and speaking() and not paused_preview and (not self._voice_intervals or self._voice_intervals[-1][0] != voice_token):
            mix_start = getattr(self.owned, "_dj_music_mix_start_seconds", None)
            start = round((getattr(self.owned, "_dj_music_start_seconds", 0.0) if mix_start is None else mix_start) * 44100)
            self._voice_intervals.append((voice_token, start, None))
        self._flush_music_pcm()

    @Slot(int)
    def _position_changed(self, milliseconds):
        now = time.monotonic()
        previous, self._last_position = self._last_position, (milliseconds, now)
        if self.media_token() != self._owner_token:
            self._owner_changed()
            return
        if previous is None:
            return
        old_position, sampled = previous
        rate = self.owned.player.playbackRate() if hasattr(self.owned.player, "playbackRate") else 1.0
        expected = max(0.0, now - sampled) * 1000 * max(0.0, rate)
        # Speech can seek without changing the track epoch. Invalidate the
        # frequency history independently of a coordinated output session.
        if milliseconds < old_position - 50 or milliseconds - old_position > expected + 100:
            with self._dj_lock:
                self._dj_frames.clear()
            self._dj_smooth[:] = [0.0, 0.0, 0.0]
        if not self._direct or self._controller is None or self._restarting:
            return
        if milliseconds < old_position - 500 or milliseconds - old_position > expected + 800:
            self._restarting = True
            self._last_buffer_end = None
            self._hold_decoder(True)
            self._controller.media_changed(self.media_token(), force=True)

    @Slot(object)
    def _buffer(self, buffer):
        if self._closed or not buffer.isValid() or not buffer.frameCount():
            return
        if self.dj_output is None:
            self._sample_personal_dj_buffer(buffer)
        if self._closed or not self._direct or self._restarting or self._controller is None or not buffer.isValid() or not buffer.frameCount():
            return
        if self.media_token() != self._direct_token:
            self._owner_changed()
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
            self._hold_decoder(True)
            self._controller.media_changed(self.media_token(), force=True)
            return
        self._last_buffer_end = start + buffer.duration() if start >= 0 else None
        content = bytes(buffer.constData())
        envelopes = getattr(self.owned, "personal_dj_music_gain_envelopes", None)
        if self.dj_output is not None and (self._hold_paused_music or self._voice_intervals or getattr(self.owned, "_dj_overlay_voices", ())
                or callable(envelopes) and envelopes()):
            if start < 0:
                self._controller.pcm_failed("timestamp", self._epoch)
                return
            self._music_pcm.append((round(start * 44100 / 1000000), content))
            self._music_pcm_bytes += len(content)
            if self._music_pcm_bytes > 352800:
                self._controller.pcm_failed("mix_backpressure", self._epoch)
                self._music_pcm.clear()
                self._music_pcm_bytes = 0
                return
            self._flush_music_pcm()
            return
        self._submit_pcm(content)
        if start >= 0:
            self.owned._dj_pcm_clock_floor = (start + buffer.duration()) / 1000000.0

    def _submit_pcm(self, content):
        if self._closed or not self._direct or self._restarting or self._controller is None:
            return
        for offset in range(0, len(content), 65536):
            if not self._controller.submit_pcm(content[offset:offset + 65536], self._epoch):
                if self._controller.accepts_pcm(self._epoch):
                    self._controller.pcm_failed("backpressure", self._epoch)
                break

    @Slot(object)
    def _dj_buffer(self, buffer):
        speaking = getattr(self.owned, "is_personal_dj_speaking", None)
        if (self._closed or not callable(speaking) or not speaking()
                or not buffer.isValid() or not buffer.frameCount() or self.media_token() != self._owner_token):
            return
        self._sample_personal_dj_buffer(buffer)
        if not self._direct or self._restarting or self._controller is None:
            return
        format_ = buffer.format()
        if (format_.sampleRate() != 44100 or format_.channelCount() != 2
                or format_.sampleFormat() != QAudioFormat.SampleFormat.Int16
                or buffer.byteCount() != buffer.frameCount() * 4 or buffer.byteCount() > 176400 or buffer.startTime() < 0):
            if self._direct and self._controller is not None:
                self._controller.pcm_failed("format", self._epoch)
            return
        offset = getattr(self.owned, "_dj_music_start_seconds", 0.0)
        if self._hold_paused_music or (getattr(self.owned, "_dj_overlay_is_preview", False)
                and self.owned.player.playbackState() != QtMultimedia.QMediaPlayer.PlaybackState.PlayingState):
            # Deliberate auditions preserve paused music on the current group.
            self._submit_pcm(bytes(buffer.constData()))
            return
        start = round((buffer.startTime() / 1000000 + offset) * 44100)
        self._voice_pcm.append((start, np.frombuffer(bytes(buffer.constData()), dtype="<i2").reshape(-1, 2)))
        # Decoded speech is bounded to the same two seconds as source PCM.
        if sum(len(samples) for _, samples in self._voice_pcm) > 88200:
            if self._direct and self._controller is not None:
                self._controller.pcm_failed("mix_backpressure", self._epoch)
            self._voice_pcm.clear()
            return
        self._flush_music_pcm()

    def _flush_music_pcm(self):
        if not self._music_pcm or self._closed or not self._direct or self._restarting or self._controller is None:
            return
        if self._hold_paused_music or (getattr(self.owned, "_dj_overlay_is_preview", False)
                and self.owned.player.playbackState() != QtMultimedia.QMediaPlayer.PlaybackState.PlayingState):
            # Decodeahead is retained for resume; only the explicit voice layer
            # is submitted while the underlying song remains paused.
            return
        active = (bool(getattr(self.owned, "_dj_overlay_voices", ())) and getattr(self.owned, "_dj_source_ready", True)
            and not (getattr(self.owned, "_dj_overlay_is_preview", False)
                and self.owned.player.playbackState() != QtMultimedia.QMediaPlayer.PlaybackState.PlayingState))
        available = max((start + len(samples) for start, samples in self._voice_pcm), default=-1)
        while self._music_pcm:
            start, content = self._music_pcm[0]
            samples = np.frombuffer(content, dtype="<i2").reshape(-1, 2)
            count = len(samples)
            if active:
                count = min(count, max(0, available - start))
                if not count:
                    return
            result = samples[:count].astype(np.float32)
            end = start + count
            for _token, voice_start, voice_end in self._voice_intervals:
                left, right = max(start, voice_start), min(end, voice_end if voice_end is not None else end)
                if right > left:
                    result[left - start:right - start] *= .2
            envelopes = getattr(self.owned, "personal_dj_music_gain_envelopes", None)
            for restore_start, restore_end, cutoff in envelopes() if callable(envelopes) else ():
                left = max(start, round(restore_start * 44100))
                right = min(end, round(cutoff * 44100))
                if right > left:
                    progress = np.clip((np.arange(left, right) / 44100.0 - restore_start)
                        / (restore_end - restore_start), 0.0, 1.0)
                    gain = .2 + .8 * progress * progress * (3.0 - 2.0 * progress)
                    result[left - start:right - start] *= gain[:, None]
            for voice_start, voice in self._voice_pcm:
                left, right = max(start, voice_start), min(end, voice_start + len(voice))
                if right > left:
                    a, b = left - start, right - start
                    result[a:b] += voice[left - voice_start:right - voice_start]
            mixed = np.clip(np.rint(result), -32768, 32767).astype("<i2").tobytes()
            self._music_pcm.popleft()
            self._music_pcm_bytes -= count * 4
            if count < len(samples):
                self._music_pcm.appendleft((end, content[count * 4:]))
            self._submit_pcm(mixed)
            self.owned._dj_pcm_clock_floor = end / 44100.0
            while self._voice_pcm and self._voice_pcm[0][0] + len(self._voice_pcm[0][1]) <= end:
                self._voice_pcm.popleft()
            while self._voice_intervals and self._voice_intervals[0][2] is not None and self._voice_intervals[0][2] <= end:
                self._voice_intervals.popleft()

    def close(self):
        if self._closed:
            return
        self.set_direct(False, 0)
        self._closed = True
        with self._dj_lock:
            self._dj_frames.clear()
        if self.supported:
            self.owned.player.setAudioBufferOutput(None)
            if self.dj_output is not None:
                self.owned.dj_player.setAudioBufferOutput(None)
        self._music_pcm.clear()
        self._voice_pcm.clear()
        self._voice_intervals.clear()
        self._music_pcm_bytes = 0
        self._controller = None
