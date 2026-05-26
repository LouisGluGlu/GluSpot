from __future__ import annotations

from api.fake_dataset import FAKE_VISUAL_DATA
from api.models import AudioFrame, IslandVisualData
from api.providers import PlaybackCommand


class FakeIslandDataProvider:
    def get_visual_data(self) -> IslandVisualData:
        return FAKE_VISUAL_DATA

    def get_audio_frame(self) -> AudioFrame | None:
        return None

    def send_playback_command(self, command: PlaybackCommand) -> bool:
        return False

    def seek_playback_position(self, position_seconds: float) -> bool:
        return False

