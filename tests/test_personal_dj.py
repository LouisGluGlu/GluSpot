"""The server-owned voice/song sequence is parsed without invented persona IDs."""
import copy
import unittest

from api.online_music import OnlineMusicError, OnlineMusicService
from tests import test_online_music as fixtures


def batch():
    return {"code": 200, "data": {"aiDjResources": [
        {"type": "audio", "value": {"audioList": [{"audioUrl": "https://example.test/voice.mp3?sign=synthetic",
            "audioId": "voice-id", "duration": 14.341224, "validTime": 86399,
            "introductionRelatedSongIds": [9], "textType": "开场", "timbreKey": "M791-C2"}]}},
        {"type": "song", "value": {"songId": "9", "songData": {"id": 9, "name": "曲目",
            "duration": 245000, "artists": [{"name": "歌手"}],
            "album": {"name": "专辑", "picUrl": "https://example.test/cover.jpg"}}}},
    ]}}


class PersonalDJParsingTests(unittest.TestCase):
    def setUp(self):
        self.service = object.__new__(OnlineMusicService)
        self.service._clock = lambda: 1000

    def test_preserves_order_seconds_authorized_stream_and_related_cover(self):
        voice, song = self.service._personal_dj_batch(batch())
        self.assertEqual((voice.media_kind, song.media_kind), ("dj_voice", "song"))
        self.assertEqual(voice.duration_seconds, 14.341224)
        self.assertEqual(song.duration_seconds, 245)
        self.assertEqual(voice.artwork_url, song.artwork_url)
        self.assertEqual(voice.direct_stream.expires_at, 87399)
        self.assertTrue(voice.direct_stream.playable)
        self.assertIsNone(song.direct_stream)
        self.assertNotIn("sign=", repr(voice))
        self.assertNotIn("豆花", voice.title)

    def test_audio_list_order_is_preserved(self):
        value = batch()
        audio = copy.deepcopy(value["data"]["aiDjResources"][0]["value"]["audioList"][0])
        audio["audioId"] = "second"
        value["data"]["aiDjResources"][0]["value"]["audioList"].append(audio)
        self.assertEqual([s.song_id for s in self.service._personal_dj_batch(value)], ["dj:voice-id", "dj:second", "9"])

    def test_malformed_audio_rejects_whole_batch_without_secret_in_error(self):
        for field, bad in (("audioUrl", "https://user:secret@example.test/x"), ("audioUrl", "file:///secret"),
                           ("duration", float("nan")), ("duration", True), ("duration", 0),
                           ("validTime", 0), ("validTime", float("inf")), ("audioId", ""),
                           ("duration", 10 ** 400), ("validTime", 10 ** 400)):
            value = batch()
            value["data"]["aiDjResources"][0]["value"]["audioList"][0][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(OnlineMusicError) as caught:
                self.service._personal_dj_batch(value)
            self.assertNotIn("secret", str(caught.exception))

    def test_mismatched_song_identity_unknown_resource_and_absent_voices_rejected(self):
        malformed = []
        value = batch(); value["data"]["aiDjResources"][1]["value"]["songId"] = "10"; malformed.append(value)
        value = batch(); value["data"]["aiDjResources"][0]["type"] = "unknown"; malformed.append(value)
        value = batch(); value["data"]["aiDjResources"].pop(0); malformed.append(value)
        value = batch(); value["data"]["aiDjResources"].pop(); malformed.append(value)
        malformed.extend([{}, {"code": 500}, {"code": 200, "data": {"aiDjResources": []}}])
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(OnlineMusicError):
                self.service._personal_dj_batch(value)

    def test_batch_limits_are_checked_before_processing(self):
        value = batch(); value["data"]["aiDjResources"] *= 33
        with self.assertRaises(OnlineMusicError):
            self.service._personal_dj_batch(value)

    def test_introduction_must_identify_following_song_and_never_previous_song(self):
        value = batch()
        song = copy.deepcopy(value["data"]["aiDjResources"][1])
        song["value"]["songId"] = "10"
        song["value"]["songData"]["id"] = 10
        value["data"]["aiDjResources"] = [value["data"]["aiDjResources"][1], value["data"]["aiDjResources"][0], song]
        with self.assertRaises(OnlineMusicError):
            self.service._personal_dj_batch(value)
        value["data"]["aiDjResources"][1]["value"]["audioList"][0]["introductionRelatedSongIds"] = [10]
        self.assertEqual([s.media_kind for s in self.service._personal_dj_batch(value)], ["song", "dj_voice", "song"])

    def test_trailing_or_unassociated_speech_is_rejected_before_playback(self):
        for mutation in ("trailing", "unassociated"):
            value = batch()
            if mutation == "trailing":
                value["data"]["aiDjResources"].reverse()
            else:
                value["data"]["aiDjResources"][0]["value"]["audioList"][0].pop("introductionRelatedSongIds")
            with self.subTest(mutation=mutation), self.assertRaises(OnlineMusicError):
                self.service._personal_dj_batch(value)


class PersonalDJServiceTests(unittest.TestCase):
    setUp = fixtures.OnlineMusicServiceTests.setUp
    login = fixtures.OnlineMusicServiceTests.login
    def test_personal_dj_uses_authenticated_existing_catalog_boundary(self):
        self.login()
        self.backend.catalog["personal_dj"] = batch()
        self.assertEqual(len(self.service.personal_dj()), 2)
        self.assertEqual(self.backend.calls[-1], ("netease", "personal_dj", {}))
        with self.assertRaises(OnlineMusicError):
            self.service.personal_dj("qq")
