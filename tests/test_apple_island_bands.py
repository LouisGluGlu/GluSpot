import unittest
from types import SimpleNamespace

from api.models import AudioFrame
from api.providers import PlaybackSourceProvider


class AppleIslandBandsTests(unittest.TestCase):
    """Apple Music plays inside its companion page; the island still needs bands."""

    def provider(self, *, owned_frame, companion):
        system_frame = AudioFrame(0.3, 0.2, 0.1, peak=0.4)
        fake = SimpleNamespace(
            _owned_active=lambda: True,
            _owned_audio_bridge=SimpleNamespace(get_music_audio_frame=lambda: owned_frame),
            owned=SimpleNamespace(_apple_companion_current=lambda: companion),
            system=SimpleNamespace(get_audio_frame=lambda: system_frame),
        )
        fake._owned_plays_in_companion = lambda: PlaybackSourceProvider._owned_plays_in_companion(fake)
        return fake, system_frame

    def test_companion_playback_uses_the_system_output_meter(self):
        fake, system_frame = self.provider(owned_frame=None, companion=True)
        self.assertIs(PlaybackSourceProvider.get_audio_frame(fake), system_frame)

    def test_local_owned_playback_keeps_its_own_pcm_bands(self):
        own = AudioFrame(0.5, 0.5, 0.5)
        fake, _system = self.provider(owned_frame=own, companion=False)
        self.assertIs(PlaybackSourceProvider.get_audio_frame(fake), own)
        fake, _system = self.provider(owned_frame=None, companion=False)
        self.assertIsNone(PlaybackSourceProvider.get_audio_frame(fake))


if __name__ == "__main__":
    unittest.main()
