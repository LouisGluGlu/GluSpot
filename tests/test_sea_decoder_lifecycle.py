"""The sea-level decoder must not outlive one decode.

6.52 kept every QAudioDecoder (and, through its bufferReady closure, the
whole decoded song) alive for good: about 140 MB per three-minute song.
"""
import math
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import weakref
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shiboken6
from PySide6.QtCore import QCoreApplication

from local import sea_lyric_background as sea

ROOT = Path(__file__).resolve().parents[1]


def _write_float_wav(path: Path, seconds: float = 2.0, rate: int = 44100) -> None:
    """A stereo IEEE-float WAV, which FFmpeg hands back as Float buffers."""
    frames = int(seconds * rate)
    samples = bytearray()
    for index in range(frames):
        value = 0.4 * math.sin(2 * math.pi * 440.0 * index / rate)
        samples += struct.pack("<ff", value, -value)
    fmt = struct.pack("<HHIIHH", 3, 2, rate, rate * 8, 8, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(samples)) + bytes(samples)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


class DecoderLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def _decode(self, path: str, *, threaded: bool):
        seen = []
        real_dispose = sea._dispose_decoder

        def spy(decoder):
            seen.append(decoder)
            real_dispose(decoder)

        with mock.patch.object(sea, "_dispose_decoder", spy):
            worker = sea._DecodeThread(path)
            if threaded:
                worker.start()
                self.assertTrue(worker.wait(60000))
            else:
                worker.run()  # like playlist_audio_compare.decode_file
        self.assertEqual(len(seen), 1)
        return worker, seen[0]

    def test_decoder_is_destroyed_after_a_threaded_decode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tone.wav"
            _write_float_wav(path)
            worker, decoder = self._decode(str(path), threaded=True)
        self.assertFalse(shiboken6.isValid(decoder))
        self.assertIsNotNone(worker.pcm)
        self.assertAlmostEqual(worker.pcm.size / sea.RATE, 2.0, delta=0.1)

    def test_decoder_is_destroyed_when_run_on_the_calling_thread(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tone.wav"
            _write_float_wav(path)
            worker, decoder = self._decode(str(path), threaded=False)
        self.assertFalse(shiboken6.isValid(decoder))
        self.assertAlmostEqual(worker.pcm.size / sea.RATE, 2.0, delta=0.1)

    def test_decoded_chunks_are_released(self):
        """Nothing keeps the chunk list alive once run() returns."""
        captured = []
        real_to_8k = sea._to_8k_mono

        def keep_ref(chunks, rate, channels):
            captured.append(weakref.ref(chunks[0]) if chunks else None)
            return real_to_8k(chunks, rate, channels)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tone.wav"
            _write_float_wav(path)
            with mock.patch.object(sea, "_to_8k_mono", keep_ref):
                worker = sea._DecodeThread(str(path))
                worker.run()
        self.assertTrue(captured and captured[0] is not None)
        import gc
        gc.collect()
        self.assertIsNone(captured[0]())

    def test_a_failing_decode_still_disposes_the_decoder(self):
        seen = []
        real_dispose = sea._dispose_decoder

        def spy(decoder):
            seen.append(decoder)
            real_dispose(decoder)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tone.wav"
            _write_float_wav(path, seconds=0.5)
            with mock.patch.object(sea, "_dispose_decoder", spy), \
                    mock.patch.object(sea, "_to_8k_mono", side_effect=MemoryError):
                with self.assertRaises(MemoryError):
                    sea._DecodeThread(str(path)).run()
        self.assertEqual(len(seen), 1)
        self.assertFalse(shiboken6.isValid(seen[0]))


class OpenBlasThreadTests(unittest.TestCase):
    def _env_after_main_import(self, extra_env):
        env = {key: value for key, value in os.environ.items() if key != "OPENBLAS_NUM_THREADS"}
        env.update(extra_env)
        code = "import local.main, os; print(os.environ.get('OPENBLAS_NUM_THREADS'))"
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_entry_point_limits_openblas_to_one_thread(self):
        self.assertEqual(self._env_after_main_import({}), "1")

    def test_explicit_value_is_kept(self):
        self.assertEqual(self._env_after_main_import({"OPENBLAS_NUM_THREADS": "4"}), "4")


if __name__ == "__main__":
    unittest.main()
