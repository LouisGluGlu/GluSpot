"""The server-owned voice/song sequence is parsed without invented persona IDs."""
import copy
import unittest

from api.online_music import OnlineMusicError, OnlineMusicService, PersonalDjVoice
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


def voice_catalog():
    # Synthetic test IDs; production obtains all names/keys from its real catalog.
    return {"code": 200, "data": {"timbreOptionsInfos": [
        {"name": "音色甲", "timbreKey": "voice-a", "desc": "介绍甲", "defaultAudio": True, "bindingMode": None},
        {"name": "音色乙", "timbreKey": "voice-b", "desc": "介绍乙", "defaultAudio": False, "bindingMode": None},
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
        self.assertEqual(voice.introduced_song_id, song.song_id)
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

    def test_only_first_unassociated_prefix_before_same_list_first_song_intro_is_ignored(self):
        for relation in (None, []):
            value = batch()
            audios = value["data"]["aiDjResources"][0]["value"]["audioList"]
            prefix = copy.deepcopy(audios[0])
            prefix["audioId"] = "unassociated-prefix"
            prefix["introductionRelatedSongIds"] = relation
            audios.insert(0, prefix)
            with self.subTest(relation=relation):
                result = self.service._personal_dj_batch(value, "M791-C2")
                self.assertEqual([song.song_id for song in result], ["dj:voice-id", "9"])
                self.assertEqual(result[0].introduced_song_id, "9")

    def test_prefix_exception_does_not_accept_other_unassociated_shapes(self):
        for mutation in ("nonlist", "other_resource", "later_item", "only_prefix", "wrong_song", "bad_prefix_audio", "wrong_prefix_voice"):
            value = batch()
            resources = value["data"]["aiDjResources"]
            audios = resources[0]["value"]["audioList"]
            prefix = copy.deepcopy(audios[0])
            prefix["audioId"] = "unassociated-prefix"
            prefix["introductionRelatedSongIds"] = []
            audios.insert(0, prefix)
            if mutation == "nonlist": prefix["introductionRelatedSongIds"] = ""
            elif mutation == "other_resource":
                resources.insert(0, copy.deepcopy(resources[1]))
            elif mutation == "later_item": audios.reverse()
            elif mutation == "only_prefix": audios.pop()
            elif mutation == "wrong_song": audios[1]["introductionRelatedSongIds"] = [10]
            elif mutation == "bad_prefix_audio": prefix["duration"] = 0
            else: prefix["timbreKey"] = "other-key"
            with self.subTest(mutation=mutation), self.assertRaises(OnlineMusicError):
                self.service._personal_dj_batch(value, "M791-C2")

        # A valid association in a separate audio resource cannot authorize
        # dropping the first resource's unrelated speech.
        value = batch()
        resources = value["data"]["aiDjResources"]
        prefix_resource = copy.deepcopy(resources[0])
        prefix_resource["value"]["audioList"][0]["introductionRelatedSongIds"] = []
        resources.insert(0, prefix_resource)
        with self.assertRaises(OnlineMusicError):
            self.service._personal_dj_batch(value)

    def test_genuine_batch_may_omit_introduction_for_an_individual_song(self):
        value = batch()
        song = copy.deepcopy(value["data"]["aiDjResources"][1])
        song["value"]["songId"] = "10"
        song["value"]["songData"]["id"] = 10
        value["data"]["aiDjResources"].append(song)
        self.assertEqual([entry.media_kind for entry in self.service._personal_dj_batch(value)], ["dj_voice", "song", "song"])

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
        self.backend.catalog["personal_dj_voices"] = voice_catalog()
        value = batch()
        value["data"]["aiDjResources"][0]["value"]["audioList"][0]["timbreKey"] = "voice-a"
        self.backend.catalog["personal_dj"] = value
        self.assertEqual(len(self.service.personal_dj()), 2)
        self.assertEqual(self.backend.calls[-1], ("netease", "personal_dj", {"timbre_key": "voice-a"}))
        with self.assertRaises(OnlineMusicError):
            self.service.personal_dj("qq")

    def test_catalog_keeps_only_public_snapshot_and_cached_getter_never_requests(self):
        self.login()
        value = voice_catalog()
        value["data"]["timbreOptionsInfos"][0].update(userId=123)
        self.backend.catalog["personal_dj_voices"] = value
        voices = self.service.personal_dj_voices()
        self.assertEqual(voices, (PersonalDjVoice("voice-a", "音色甲", "介绍甲", True), PersonalDjVoice("voice-b", "音色乙", "介绍乙", False)))
        calls = len(self.backend.calls)
        self.assertEqual(self.service.personal_dj_voices_cached(), voices)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertNotIn("hidden", repr(voices))
        self.assertNotIn("userId", repr(voices))
        self.service.logout("netease")
        self.assertEqual(self.service.personal_dj_voices_cached(), ())

    def test_real_catalog_preview_is_bounded_and_hidden_from_repr(self):
        self.login()
        value = voice_catalog()
        value["data"]["timbreOptionsInfos"][0].update(
            playUrl="https://example.test/voice?secret=hidden", duration=7)
        self.backend.catalog["personal_dj_voices"] = value
        voice = self.service.personal_dj_voices()[0]
        self.assertEqual(voice.preview_duration_seconds, 7)
        self.assertEqual(voice.preview_stream.quality, "dj_preview")
        self.assertEqual(voice.preview_stream.url, "https://example.test/voice?secret=hidden")
        self.assertTrue(voice.preview_stream.playable)
        self.assertNotIn("hidden", repr(voice))
        self.assertEqual(self.service.personal_dj_voices_cached()[0], voice)
        self.service.logout("netease")
        self.assertEqual(self.service.personal_dj_voices_cached(), ())

    def test_catalog_preview_rejects_invalid_address_or_duration_without_caching(self):
        self.login()
        for url, duration in (("file:///audio.mp3", 7), ("https://user:pass@example.test/audio", 7),
                              ("https://example.test/audio", None), ("https://example.test/audio", True),
                              ("https://example.test/audio", float("nan")), ("https://example.test/audio", 121),
                              ("https://example.test/audio", 2**1024), ("https://example.test/audio\n", 7)):
            value = voice_catalog()
            value["data"]["timbreOptionsInfos"][0].update(playUrl=url, duration=duration)
            self.backend.catalog["personal_dj_voices"] = value
            with self.subTest(url=url, duration=duration), self.assertRaises(OnlineMusicError):
                self.service.personal_dj_voices()
            self.assertEqual(self.service.personal_dj_voices_cached(), ())

    def test_identical_same_account_refresh_preserves_voice_identity_and_new_order(self):
        self.login()
        value = voice_catalog()
        value["data"]["timbreOptionsInfos"][0].update(playUrl="https://example.test/audition.mp3", duration=7)
        self.backend.catalog["personal_dj_voices"] = value
        first = self.service.personal_dj_voices()
        refreshed = self.service.personal_dj_voices()
        self.assertTrue(all(a is b for a, b in zip(first, refreshed)))
        value["data"]["timbreOptionsInfos"].reverse()
        reordered = self.service.personal_dj_voices()
        self.assertIs(reordered[0], first[1])
        self.assertIs(reordered[1], first[0])

    def test_logout_relogin_identical_response_replaces_voice_identity(self):
        self.login()
        value = voice_catalog()
        value["data"]["timbreOptionsInfos"][0].update(playUrl="https://example.test/audition.mp3", duration=7)
        self.backend.catalog["personal_dj_voices"] = value
        first = self.service.personal_dj_voices()
        self.service.logout("netease")
        self.login()
        refreshed = self.service.personal_dj_voices()
        self.assertEqual(first, refreshed)
        self.assertTrue(all(a is not b for a, b in zip(first, refreshed)))

    def test_same_account_changed_hidden_preview_url_replaces_only_changed_voice(self):
        self.login()
        value = voice_catalog()
        row = value["data"]["timbreOptionsInfos"][0]
        row.update(playUrl="https://example.test/audition.mp3?token=first", duration=7)
        self.backend.catalog["personal_dj_voices"] = value
        first = self.service.personal_dj_voices()
        row["playUrl"] = "https://example.test/audition.mp3?token=second"
        refreshed = self.service.personal_dj_voices()
        self.assertIsNot(refreshed[0], first[0])
        self.assertIs(refreshed[1], first[1])
        self.assertEqual(refreshed[0].preview_stream.url, row["playUrl"])

    def test_same_account_changed_retained_catalog_fields_replace_voice_identity(self):
        self.login()
        value = voice_catalog()
        row = value["data"]["timbreOptionsInfos"][0]
        row.update(playUrl="https://example.test/audition.mp3", duration=7)
        self.backend.catalog["personal_dj_voices"] = value
        prior = self.service.personal_dj_voices()
        for field, changed in (("name", "新音色名"), ("desc", "新的描述"), ("defaultAudio", False),
                               ("duration", 8), ("timbreKey", "changed-key")):
            row[field] = changed
            refreshed = self.service.personal_dj_voices()
            with self.subTest(field=field):
                self.assertIsNot(refreshed[0], prior[0])
                self.assertIs(refreshed[1], prior[1])
            prior = refreshed

    def test_unknown_key_and_malformed_key_never_request_recommendations(self):
        self.login()
        self.backend.catalog["personal_dj_voices"] = voice_catalog()
        for key in ("unavailable", "../invalid", None):
            with self.subTest(key=key), self.assertRaises(OnlineMusicError):
                self.service.personal_dj(timbre_key=key)
        self.assertFalse(any(operation == "personal_dj" for _, operation, _ in self.backend.calls))

    def test_selected_voice_must_match_every_returned_speech(self):
        self.login()
        self.backend.catalog["personal_dj_voices"] = voice_catalog()
        value = batch()
        self.backend.catalog["personal_dj"] = value
        with self.assertRaises(OnlineMusicError):
            self.service.personal_dj(timbre_key="voice-b")
        value["data"]["aiDjResources"][0]["value"]["audioList"][0]["timbreKey"] = "voice-b"
        self.assertEqual(len(self.service.personal_dj(timbre_key="voice-b")), 2)

    def test_empty_malformed_duplicate_and_mode_bound_catalogs_do_not_become_choices(self):
        self.login()
        malformed = [{"code": 200, "data": {}}, {"code": 200, "data": {"timbreOptionsInfos": []}}, {"code": 500}]
        for mutation in ("duplicate", "bad_name", "bad_default", "mode_bound"):
            value = voice_catalog()
            rows = value["data"]["timbreOptionsInfos"]
            if mutation == "duplicate": rows[1]["timbreKey"] = rows[0]["timbreKey"]
            elif mutation == "bad_name": rows[0]["name"] = "invalid\nname"
            elif mutation == "bad_default": rows[0]["defaultAudio"] = "true"
            else:
                for row in rows: row["bindingMode"] = "unrelated-mode"
            malformed.append(value)
        for value in malformed:
            self.backend.catalog["personal_dj_voices"] = value
            with self.subTest(value=value), self.assertRaises(OnlineMusicError):
                self.service.personal_dj_voices()

    def test_logout_during_catalog_fetch_prevents_old_account_cache(self):
        self.login()
        self.backend.catalog["personal_dj_voices"] = voice_catalog()
        original = self.backend.request
        def request(source, operation, params, credential=None):
            if operation == "personal_dj_voices": self.service.logout(source)
            return original(source, operation, params, credential)
        self.backend.request = request
        with self.assertRaises(OnlineMusicError):
            self.service.personal_dj_voices()
        self.assertEqual(self.service.personal_dj_voices_cached(), ())

    def test_signed_out_refresh_discards_catalog_even_if_account_recovers(self):
        self.login()
        self.backend.catalog["personal_dj_voices"] = voice_catalog()
        self.service.personal_dj_voices()
        self.backend.verified = False
        self.assertFalse(self.service.account_status("netease", refresh=True).logged_in)
        self.assertEqual(self.service.personal_dj_voices_cached(), ())
        self.backend.verified = True
        self.assertTrue(self.service.account_status("netease", refresh=True).logged_in)
        self.assertEqual(self.service.personal_dj_voices_cached(), ())

    def test_replacement_account_between_voice_selection_and_request_is_cancelled(self):
        self.login()
        self.backend.catalog["personal_dj_voices"] = voice_catalog()
        self.service.personal_dj_voices()
        original = self.service.personal_dj_voices_cached
        def replace_account(source_id):
            old = original(source_id)
            self.service.logout(source_id)
            self.login(source_id)
            return old
        self.service.personal_dj_voices_cached = replace_account
        with self.assertRaises(OnlineMusicError) as caught:
            self.service.personal_dj()
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertFalse(any(operation == "personal_dj" for _, operation, _ in self.backend.calls))
