"""Native, silent contract checks for the separately staged Qt 5 adapter.

Run serially with the legacy Python interpreter. The default contract fixture
does not call play(). --decode explicitly exercises two generated local WAVs
with native mute and volume zero. Neither path selects a Windows endpoint,
contacts a receiver, or modifies app settings.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
import wave


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--decode", action="store_true",
        help="Opt in to generated local WAV decoding through a private muted player")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from PySide2 import QtMultimedia as native
    from PySide2.QtCore import QCoreApplication, QUrl
    from PySide6.QtMultimedia import (
        QAudioBuffer, QAudioBufferOutput, QAudioFormat, QAudioOutput,
        QMediaDevices, QMediaPlayer,
    )

    app = QCoreApplication.instance() or QCoreApplication([])
    checks = []

    def require(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    format_ = QAudioFormat()
    format_.setSampleRate(44100)
    format_.setChannelCount(2)
    format_.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    require("native PCM format configured", format_.isValid())
    require("16-bit signed sample format", format_.sampleSize() == 16
        and format_.sampleType() == native.QAudioFormat.SignedInt)
    require("native little endian", format_.byteOrder() == native.QAudioFormat.LittleEndian)
    require("roundtrip sample enum", format_.sampleFormat() == QAudioFormat.SampleFormat.Int16)
    foreign = QAudioFormat(format_)
    foreign.setByteOrder(native.QAudioFormat.BigEndian)
    require("foreign endian rejected", foreign.sampleFormat() == QAudioFormat.SampleFormat.Unknown)
    foreign.setByteOrder(native.QAudioFormat.LittleEndian)
    foreign.setSampleSize(24)
    require("unsupported width rejected", foreign.sampleFormat() == QAudioFormat.SampleFormat.Unknown)

    content = b"\x01\x80\x03\x40" * 882
    buffer = QAudioBuffer(content, format_, 45000)
    require("native audio buffer valid", buffer.isValid())
    require("byte ownership preserved", buffer.constData() == content)
    require("PCM frame count", buffer.frameCount() == 882)
    require("PCM duration", buffer.duration() == 20000)
    require("native presentation timestamp", buffer.startTime() == 45000)
    copied = QAudioBuffer(buffer)
    del buffer
    require("copied native buffer retains data", copied.constData() == content)
    require("wrapped native format", copied.format().sampleFormat() == QAudioFormat.SampleFormat.Int16)

    player = QMediaPlayer()
    audio = QAudioOutput()
    audio.setVolume(.37)
    audio.setMuted(True)
    player.setAudioOutput(audio)
    require("native volume receives initial gain", player.volume() == 37)
    require("native mute receives initial state", player.isMuted())
    audio.setVolume(.21)
    require("native volume follows controller", player.volume() == 21)
    audio.setVolume(4)
    require("gain clamps at native maximum", player.volume() == 100 and audio.volume() == 1.0)
    audio.setVolume(-3)
    require("gain clamps at native minimum", player.volume() == 0 and audio.volume() == 0.0)
    audio.setMuted(False)
    require("native mute follows controller", not player.isMuted())
    require("audio output ownership", player.audioOutput() is audio)
    player.setAudioOutput(None)
    require("detached output is silent", player.isMuted() and player.audioOutput() is None)
    player.setAudioOutput(audio)

    states = []
    player.playbackStateChanged.connect(states.append)
    player.stateChanged.emit(native.QMediaPlayer.PlayingState)
    require("state signal forwards native enum", states == [QMediaPlayer.PlaybackState.PlayingState])
    errors = []
    player.errorOccurred.connect(lambda code, detail: errors.append((code, detail)))
    player.error.emit(native.QMediaPlayer.NetworkError)
    require("error forwards code and native detail", errors == [(QMediaPlayer.Error.NetworkError, player.errorString())])
    player.setSource(QUrl())
    require("empty source clears native media", player.source().isEmpty())
    require("actual playback state", player.playbackState() == QMediaPlayer.PlaybackState.StoppedState)
    require("no invented endpoint identifiers", bytes(audio.device().id()) == b"")
    require("no false routable devices", QMediaDevices.audioOutputs() == [])
    output = QAudioBufferOutput(format_)
    require("requested format retained", output.format().sampleFormat() == QAudioFormat.SampleFormat.Int16)
    probe = callable(player.setAudioBufferOutput)
    if probe:
        player.setAudioBufferOutput(output)
        require("real probe output attachment", player.audioBufferOutput() is output)
        observed = []
        output.audioBufferReceived.connect(observed.append)
        player._probe.audioBufferProbed.emit(copied._buffer)
        require("native probe signal provides owned PCM", len(observed) == 1
            and observed[0].constData() == content
            and observed[0].startTime() == 45000)
        player.setAudioBufferOutput(None)
        player._probe.audioBufferProbed.emit(copied._buffer)
        require("detached probe forwards no stale PCM", len(observed) == 1)
    decode_result = None
    if args.decode:
        def pump_until(label, condition, timeout=10):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                app.processEvents()
                if condition():
                    return
                time.sleep(.005)
            raise AssertionError(label)

        def pump_for(seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)

        audio.setVolume(0)
        audio.setMuted(True)
        decoded = []
        decode_errors = []
        player.errorOccurred.connect(lambda code, detail: decode_errors.append((int(code), detail)))
        if probe:
            player.setAudioBufferOutput(output)
            output.audioBufferReceived.connect(lambda value: decoded.append({
                "bytes": len(value.constData()), "frames": value.frameCount(),
                "rate": value.format().sampleRate(), "channels": value.format().channelCount(),
                "sample_format": int(value.format().sampleFormat()), "start": value.startTime(),
            }))
        with tempfile.TemporaryDirectory(prefix="GluSpot-legacy-muted-media-") as directory:
            paths = []
            for index, duration in enumerate((1.8, .65)):
                path = Path(directory) / ("source-%d.wav" % index)
                with wave.open(str(path), "wb") as wav:
                    wav.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
                    wav.writeframes(b"\x01\x00\xff\xff" * round(44100 * duration))
                paths.append(path)
            player.setSource(QUrl.fromLocalFile(str(paths[0])))
            require("native source URL retained", Path(player.source().toLocalFile()) == paths[0])
            player.play()
            pump_until("native first-source playback did not start", lambda:
                player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                and player.duration() > 0 and player.position() >= 120)
            require("actual decoder reports duration", 1700 <= player.duration() <= 1900)
            player.pause()
            pump_until("native pause not acknowledged", lambda:
                player.playbackState() == QMediaPlayer.PlaybackState.PausedState)
            paused_position = player.position()
            pump_for(.18)
            require("paused decoder clock is held", abs(player.position() - paused_position) <= 60)
            player.setPosition(700)
            pump_until("native seek not acknowledged", lambda: 650 <= player.position() <= 900)
            require("actual media is seekable", player.isSeekable())
            player.play()
            pump_until("native first-source EOF missing", lambda:
                player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia)
            require("first source reaches EOF", player.position() >= 1700)
            player.stop()
            player.setSource(QUrl())
            player.setSource(QUrl.fromLocalFile(str(paths[1])))
            require("next source replaces native media", Path(player.source().toLocalFile()) == paths[1])
            player.play()
            pump_until("native next-source playback did not start", lambda:
                player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                and 550 <= player.duration() <= 750)
            pump_until("native next-source EOF missing", lambda:
                player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia)
            require("real decode has no media errors", not decode_errors)
            if probe:
                require("real native probe yields decoded PCM", bool(decoded))
                require("real probe byte counts match PCM frames", all(
                    row["bytes"] == row["frames"] * 4 and row["rate"] == 44100
                    and row["channels"] == 2 and row["sample_format"] == 2
                    for row in decoded if row["frames"]))
            decode_result = {"sources": 2, "decoded_buffers": len(decoded),
                "formats": sorted({(row["rate"], row["channels"], row["sample_format"])
                    for row in decoded}), "errors": decode_errors,
                "pause_seek_eof_next": True, "native_muted": player.isMuted(),
                "native_volume": player.volume()}
            player.stop()
            player.setSource(QUrl())
            pump_for(.05)

    player.stop()
    player.deleteLater()
    audio.deleteLater()
    output.deleteLater()
    app.processEvents()
    result = {"passed": True, "checks": checks,
        "probe_available": probe, "native_qt_version": native.__version__
        if hasattr(native, "__version__") else None,
        "decode": decode_result,
        "limitations": ["No Windows 7 machine was exercised by this contract fixture",
            "No receiver selection or Windows default endpoint change was requested",
            "Qt 5 probe does not negotiate sample rate/representation",
            "Only the default local audio endpoint is supported"]}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "checks": len(checks), "probe_available": probe,
        "decode": decode_result}))


if __name__ == "__main__":
    main()
