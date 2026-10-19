"""The owned-player subset of Qt 6 Multimedia, backed by Qt 5.15.

This module is staged only in the Windows 7 build. It does not capture system
audio, change the default Windows endpoint, or emulate a Windows media session.
Qt 5 owns decoding and local audio presentation. A real QAudioProbe supplies
optional PCM; availability is checked against the selected multimedia service.
"""
from enum import IntEnum
import math
import weakref

from PySide2 import QtMultimedia as _native
from PySide2.QtCore import QByteArray, QObject, QUrl, Signal
from shiboken2 import VoidPtr


class _SampleFormat(IntEnum):
    Unknown = 0
    UInt8 = 1
    Int16 = 2
    Int32 = 3
    Float = 4


class QAudioFormat(_native.QAudioFormat):
    SampleFormat = _SampleFormat

    def setSampleFormat(self, value):
        value = _SampleFormat(value)
        sample_type, size = {
            _SampleFormat.Unknown: (_native.QAudioFormat.Unknown, 0),
            _SampleFormat.UInt8: (_native.QAudioFormat.UnSignedInt, 8),
            _SampleFormat.Int16: (_native.QAudioFormat.SignedInt, 16),
            _SampleFormat.Int32: (_native.QAudioFormat.SignedInt, 32),
            _SampleFormat.Float: (_native.QAudioFormat.Float, 32),
        }[value]
        self.setCodec("audio/pcm")
        self.setByteOrder(_native.QAudioFormat.LittleEndian)
        self.setSampleType(sample_type)
        self.setSampleSize(size)

    def sampleFormat(self):
        # The bridge consumes little-endian PCM. Never label an unsupported
        # native representation as Int16 merely because its width is 16 bits.
        if self.byteOrder() != _native.QAudioFormat.LittleEndian:
            return _SampleFormat.Unknown
        return {
            (_native.QAudioFormat.UnSignedInt, 8): _SampleFormat.UInt8,
            (_native.QAudioFormat.SignedInt, 16): _SampleFormat.Int16,
            (_native.QAudioFormat.SignedInt, 32): _SampleFormat.Int32,
            (_native.QAudioFormat.Float, 32): _SampleFormat.Float,
        }.get((self.sampleType(), self.sampleSize()), _SampleFormat.Unknown)


class QAudioBuffer:
    """Keep native buffer ownership while exposing its Python byte view."""

    def __init__(self, data=None, format=None, startTime=-1):
        if isinstance(data, QAudioBuffer):
            self._buffer = _native.QAudioBuffer(data._buffer)
        elif isinstance(data, _native.QAudioBuffer):
            self._buffer = _native.QAudioBuffer(data)
        elif data is None:
            self._buffer = _native.QAudioBuffer()
        else:
            self._buffer = _native.QAudioBuffer(QByteArray(bytes(data)), format, int(startTime))

    def format(self):
        return QAudioFormat(self._buffer.format())

    def constData(self):
        data = self._buffer.constData()
        if data is None:
            return b""
        # PySide2 exposes const void * as a Shiboken VoidPtr. Its byte count is
        # supplied by QAudioBuffer rather than inferred from a Python address.
        if isinstance(data, (VoidPtr, int)):
            return VoidPtr(int(data), self._buffer.byteCount(), 0).toBytes()
        return bytes(data)[:self._buffer.byteCount()]

    def data(self):
        return self.constData()

    def byteCount(self):
        return self._buffer.byteCount()

    def frameCount(self):
        return self._buffer.frameCount()

    def sampleCount(self):
        return self._buffer.sampleCount()

    def startTime(self):
        return self._buffer.startTime()

    def duration(self):
        return self._buffer.duration()

    def isValid(self):
        return self._buffer.isValid()


class QAudioDevice:
    """A Qt 5 local device; its name is never presented as a WASAPI ID."""

    def __init__(self, info=None):
        self._info = info or _native.QAudioDeviceInfo.defaultOutputDevice()

    def id(self):
        # Qt 5's WinMM/DirectShow device names do not identify MMDevice
        # endpoints. An empty ID lets existing routing reject explicit WASAPI
        # selection without claiming success or changing the Windows default.
        return QByteArray()

    def description(self):
        return self._info.deviceName()

    def isNull(self):
        return self._info.isNull()

    def isDefault(self):
        return self._info == _native.QAudioDeviceInfo.defaultOutputDevice()

    def preferredFormat(self):
        return QAudioFormat(self._info.preferredFormat())


class QMediaDevices:
    @staticmethod
    def defaultAudioOutput():
        return QAudioDevice()

    @staticmethod
    def audioOutputs():
        # Named Qt 5 devices cannot satisfy the application's exact endpoint
        # contract. Keep default playback working and report no routable IDs.
        return []


class _LegacyAudioOutput(QObject):
    """Qt 6 gain/mute controller attached to the real Qt 5 media player."""

    volumeChanged = Signal(float)
    mutedChanged = Signal(bool)
    deviceChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._volume = 1.0
        self._muted = False
        self._players = weakref.WeakSet()
        self._device = QAudioDevice()

    def _attach(self, player):
        self._players.add(player)
        player.setVolume(round(self._volume * 100))
        player.setMuted(self._muted)

    def _detach(self, player):
        self._players.discard(player)

    def volume(self):
        return self._volume

    def setVolume(self, value):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Audio volume must be finite")
        value = max(0.0, min(1.0, value))
        if value != self._volume:
            self._volume = value
            for player in tuple(self._players):
                player.setVolume(round(value * 100))
            self.volumeChanged.emit(value)

    def isMuted(self):
        return self._muted

    def setMuted(self, value):
        value = bool(value)
        if value != self._muted:
            self._muted = value
            for player in tuple(self._players):
                player.setMuted(value)
            self.mutedChanged.emit(value)

    def device(self):
        return self._device

    def setDevice(self, device):
        # No misleading success for an endpoint Qt 5 cannot identify.
        if not isinstance(device, QAudioDevice) or not device.isDefault():
            raise ValueError("Windows 7 playback uses the default audio output")


QAudioOutput = _LegacyAudioOutput


class QAudioBufferOutput(QObject):
    """Observe actual decoded PCM; Qt 5 cannot negotiate its native format.

    format() preserves the requested format for API compatibility. Received
    buffers report their actual format, allowing the existing bridge to reject
    incompatible PCM safely instead of forwarding incorrectly labelled audio.
    """

    audioBufferReceived = Signal(object)

    def __init__(self, format=None, parent=None):
        super().__init__(parent)
        self._format = QAudioFormat(format) if format is not None else QAudioFormat()

    def format(self):
        return QAudioFormat(self._format)


class _LegacyMediaPlayer(_native.QMediaPlayer):
    PlaybackState = _native.QMediaPlayer.State
    playbackStateChanged = Signal(object)
    errorOccurred = Signal(object, str)
    sourceChanged = Signal(QUrl)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Qt 5 defaults to one position notification per second, too coarse
        # for the existing lyric clock and decoder-stall recovery checks.
        self.setNotifyInterval(50)
        self._audio_output = None
        self._buffer_output = None
        self._probe = _native.QAudioProbe(self)
        self.stateChanged.connect(self.playbackStateChanged.emit)
        self.error.connect(self._error_occurred)
        self.currentMediaChanged.connect(self._source_changed)
        self._probe.audioBufferProbed.connect(self._audio_buffer_probed)
        # Probe support differs between Windows multimedia services. The
        # bridge checks this callable before advertising coordinated PCM.
        self.setAudioBufferOutput = (self._set_audio_buffer_output
            if self._probe.setSource(self) else None)

    def _error_occurred(self, code):
        self.errorOccurred.emit(code, self.errorString())

    def _source_changed(self, media):
        self.sourceChanged.emit(media.canonicalUrl())

    def playbackState(self):
        return self.state()

    def setSource(self, source):
        self.setMedia(_native.QMediaContent(QUrl(source)))

    def source(self):
        return self.currentMedia().canonicalUrl()

    def setAudioOutput(self, output):
        if output is not None and not isinstance(output, QAudioOutput):
            raise TypeError("audio output must be QAudioOutput or None")
        if self._audio_output is not None:
            self._audio_output._detach(self)
        self._audio_output = output
        if output is not None:
            output._attach(self)
        else:
            # Qt 6 detaching the audio output makes the player inaudible.
            self.setMuted(True)

    def audioOutput(self):
        return self._audio_output

    def _set_audio_buffer_output(self, output):
        if output is not None and not isinstance(output, QAudioBufferOutput):
            raise TypeError("buffer output must be QAudioBufferOutput or None")
        self._buffer_output = output

    def audioBufferOutput(self):
        return self._buffer_output

    def _audio_buffer_probed(self, buffer):
        output = self._buffer_output
        if output is not None:
            output.audioBufferReceived.emit(QAudioBuffer(buffer))


# Unique Python QObject class names avoid PySide2's collision with the native
# QMediaPlayer metaobject (which otherwise drops the newly declared signals).
QMediaPlayer = _LegacyMediaPlayer
