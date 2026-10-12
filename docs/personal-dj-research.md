# NetEase personal roaming and Private DJ research

Research dates: October 11–12, 2026. GluSpot source version: `6.412`.

## October 12: verified four-voice catalog and selection

The earlier missing-interface conclusion is superseded. Bounded static
inspection of the official public Android 9.6.05 package established the
real timbre catalog, the top-level recommendation `timbreKey`, and the
client profile. Read-only requests then returned **豆花 `F793-C2`、黄俊
`M791-C2`、李源 `M792-NP`、庞舸 `M795-NP`**, followed by genuine speech
whose metadata matched each requested key. The protected account file
remained byte-identical. Selection needs no official preference mutation.
See [verified client contract and evidence](personal-dj-client-contract.md).

Runtime uses the real catalog rather than the evidence table as a hardcoded
mapping. Its worker validates current-account membership and speech identity;
only the two DJ requests use the verified compatibility profile. The older
sections below record their original evidence cutoff, not the current
availability of the four requested voices.

A subsequent six-request verification through the actual production service
and bridge parsed all four selected-voice queues successfully, without
credential writes or physical playback. The service sometimes omits an
individual song's introduction; this is retained rather than fabricated.
The bounded first-audio unassociated-prefix compatibility and 57 focused
parser/service/bridge tests are documented with the verified contract.

The real catalog also supplies `playUrl` auditions, distinct from the boolean
`defaultAudio` choice marker. Four bounded audio downloads matched declared
sizes, preserved the protected account and remained in ignored research
storage. Local unprompted ASR found different self-introductions rather than
the requested common audition sentence; no audio was uploaded for recognition.
The service now retains validated audition streams without printing their
addresses. No arbitrary-text synthesis API for these personas was verified.
The user approved playing the genuine official auditions rather than the
original common sentence.
See [audition evidence and parser boundary](personal-dj-client-contract.md#official-audition-evidence).

## Earlier October 11 implementation evidence

The subsequent implementation now exposes the pinned `aidj_content_rcmd`
through the existing bridge and service. Authenticated requests returned
genuine speech and songs in order; the strict parser was checked against a
six-resource response. A service-provided voice file (428,870 bytes) and its
authorized song (2,922,669 bytes, not a trial) passed the complete download
boundary. Both downloads took one HTTP request and their session files were
cleaned afterward. These probes preserved the protected account file's SHA256
and printed no signed addresses, credentials or account identity.

Private DJ now belongs to **私人漫游**, with a persistent song-page toggle,
genuine introductions between songs and DJ continuation through the owned
player. There is no separate DJ library destination. A further authenticated
read-only response contained `song → audio → song → audio → song`; each voice's
`introductionRelatedSongIds` identified the following song. The protected
account file remained unchanged. See [FM integration and controls](personal-fm-dj-controls.md).

At this earlier implementation cutoff, the user's four requested personas
were **豆花、黄俊、李源、庞舸**. `M791-C2` was still unidentified
because `announcerName` was null and the module had no verified persona-choice
input. **豆花** could not then be claimed as the returned voice. The October 12
catalog now identifies `M791-C2` as 黄俊. See
[current behavior and final verification](buffered-playback-and-personal-dj.md).

A further bounded public-source check verified that the [current upstream
module at `2db6684`](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced/blob/2db6684453297ee86dec0492842f2320b08182d9/module/aidj_content_rcmd.js)
and types are byte-identical to the pinned 4.41.1 versions. Its complete
repository tree did not expose an additional AI DJ selection module. The
[independent Rust implementation](https://github.com/SPlayer-Dev/ncm-api-rs/blob/133b65bfe482e41ebccf018870d3fce07bf58eb3/src/api/aidj_content_rcmd.rs)
uses the same recommendation endpoint and location-only inputs. This research
did not establish a switching endpoint or valid name-to-ID mapping for the
four personas; it does not imply the official app lacks them. No real account
or client setting was accessed for this public-source check.

### Expanded public-source check

A further bounded check covered the original adapter contribution, independent
desktop players and their API contracts, current official product information,
and indexed public code. It still did **not** establish a voice-selection
endpoint or an auditable mapping for **豆花、黄俊、李源、庞舸**.

| Source, fixed revision | Observed contract | Voice-selection evidence |
| --- | --- | --- |
| [Original adapter PR #1785](https://github.com/Binaryify/NeteaseCloudMusicApi/pull/1785), merged August 18, 2023; merge `6b28f34` | Recommendation module and documentation, including optional location | No persona selector in the contributed module |
| [SPlayer `radioPersonalDj`](https://github.com/SPlayer-Dev/SPlayer/blob/40d9ec606cc1e323c964ffea7bf0ab02f137e93b/src/api/radio.ts#L44) | Calls `/aidj/content/rcmd` without a voice parameter | No persona-selection contract at this call site |
| [QCloudMusicApi module](https://github.com/s12mmm3/QCloudMusicApi/blob/bd07813dd415577cb86a75d558e430bb1e8a416b/QCloudMusicApi/module.cpp#L87) | Same recommendation URI and `extInfo`/optional-location fields | No persona selector in this implementation |
| [HyPlayer recommendation contract](https://github.com/HyPlayer/HyPlayer.NeteaseProvider/blob/c11cd1278ed0b3a232242eded7e6d14d1c9f8683/HyPlayer.NeteaseApi/ApiContracts/PersonalFM/AiDjContentRcmdInfo.cs), used by its [FM strategy](https://github.com/HyPlayer/HyPlayer/blob/158928459a0c952aea482872bd7e4d6e9bf86d5d/HyPlayer/Services/Playback/Strategies/PersonalFmStrategy.cs#L101) | Independent typed contract for `/api/aidj/content/rcmd/info`; request has `IsNewToAidj` and optional coordinates; response models song/audio resources and `introductionRelatedSongIds` | No voice-name/ID input or voice catalog in this contract |
| [HyPlayer skip contract](https://github.com/HyPlayer/HyPlayer.NeteaseProvider/blob/c11cd1278ed0b3a232242eded7e6d14d1c9f8683/HyPlayer.NeteaseApi/ApiContracts/PersonalFM/AiDjSkip.cs) | `/api/v1/radio/skip` with song/time/mode fields | A skip/reporting contract, not evidence of persona selection; not invoked here |

The [official App Store listing](https://apps.apple.com/cn/app/id590338362)
continues to describe upgraded AI Private DJ, but publishes no selection schema
or four-persona IDs. A [2023 launch article](https://news.qq.com/rain/a/20230811A03CPI00)
names **黄俊** as the launch voice and describes future voice-library expansion.
That secondary article cannot identify the current response's `M791-C2`:
`announcerName=null` remains the only observed name evidence for that response.
The user's requested names and official-client screenshot establish the target
experience, not a name-to-ID mapping.

Sourcegraph searches included recommendation URI/field names, `timbreKey`,
`aiDjResources`, `announcerName`, and the exact observed `M791-C2`, with forks
and archived repositories included. They exposed the implementations above but
no usable selector or mapping. Public GitHub issue/commit searches also produced
no usable selection contract. These are bounded index/search results, not proof
that an interface does not exist. Some searches had index shard/time limits;
grep.app returned HTTP 429, multiple general-search providers challenged or
redirected requests, and connected search/GitHub tools were unavailable. No
authenticated code search, user account, private client data, or account
preference was used. Downloaded public research material stays in ignored
runtime storage and is not included in source control.

The smallest next verification is a public official-client request schema, or
an explicitly authorized **test-account** comparison before and after one voice
change. Record only endpoint/field names and nonsecret voice identifiers,
restore the original preference, and compare returned speech metadata. This
check was not performed here. Once that evidence exists, the existing optional
bridge/service/provider can carry the verified voice catalog and selection
contract; a separate recommendation generation can cancel stale future speech
without reopening the current song. This is a proposed integration boundary,
not a claim that a particular endpoint, header, or persona ID is valid.

## Original study, before authenticated implementation

The remainder records the preceding read-only study and its evidence cutoff.
Statements that no response/runtime was available describe that historical
cutoff, superseded by the implementation update above.

## Conclusion

The supplied screenshot really shows **私人漫游 · 默认模式**, a **私人DJ** panel, and **Hi，我是你的私人DJ 豆花**. The screenshot establishes the requested official-client experience, including the displayed persona name. It does not establish the same account's API eligibility, an available voice-selection parameter, or third-party playback support.

GluSpot's pinned NetEase adapter already contains a specific Private DJ recommendation module. Its documentation describes both DJ voice and recommended songs, making it a useful candidate for genuine service-provided spoken content. However, GluSpot currently exposes ordinary recommendation songs only. No authenticated DJ response was requested during this study, so genuine spoken audio, its ordering with music, and the **豆花** persona remain unverified.

The official NetEase iOS listing also describes the upgraded AI Private DJ roaming feature in its update history, including versions `9.5.15`, `9.5.30`, and `9.5.37`. This confirms a current official product feature, but provides no API response contract or account, membership, regional, Windows-client, or persona eligibility matrix. It should not be used to claim that the feature is mobile-only. [Official listing, developer 杭州网易云音乐科技有限公司](https://apps.apple.com/cn/app/id590338362)

## Fixed adapter evidence

`native/online_music/package.json` and the installed runtime both pin `@neteasecloudmusicapienhanced/api` to **4.41.1**. The published package metadata identifies the maintained upstream and the MIT code license. This is an unofficial adapter; its code license does not establish permission to distribute NetEase music, generated speech, or persona assets. This study does not establish official authorization for a commercial DJ integration. [Published package metadata](https://registry.npmjs.org/@neteasecloudmusicapienhanced%2fapi/4.41.1)

| Interface | Fixed implementation | GluSpot state | What the evidence supports |
| --- | --- | --- | --- |
| `recommend_songs` | Ordinary daily song recommendations | Already exposed through `daily_recommendations` | A recommended music list; no DJ speech contract |
| `personal_fm` | `/api/v1/radio/get`, empty module body, `weapi` | Already exposed through `personal_fm` | Ordinary personal-roaming song batches |
| `personal_fm_mode` | Same upstream URI, with `mode`, `subMode`, and `limit` (default 3) | Dependency module exists; not exposed by the bridge | A mode-selectable recommendation candidate; speech is not established |
| `aidj_content_rcmd` | `/api/aidj/content/rcmd/info`, with serialized `extInfo` | Dependency module exists; not exposed by the bridge | A documented candidate for DJ voice plus recommended music; actual response not verified |

The wrapper route documented as `/aidj/content/rcmd` is distinct from the upstream request URI `/api/aidj/content/rcmd/info`. There is no need to create an additional HTTP server to use the installed module. Its existing module/request interface matches the optional bridge architecture. [Fixed Private DJ module](https://cdn.jsdelivr.net/npm/@neteasecloudmusicapienhanced/api@4.41.1/module/aidj_content_rcmd.js), [fixed FM module](https://cdn.jsdelivr.net/npm/@neteasecloudmusicapienhanced/api@4.41.1/module/personal_fm.js), [fixed FM mode module](https://cdn.jsdelivr.net/npm/@neteasecloudmusicapienhanced/api@4.41.1/module/personal_fm_mode.js)

The fixed FM-mode documentation lists `DEFAULT`, `FAMILIAR`, `EXPLORE`, `SCENE_RCMD`, and `PUZZLE_MODE_RCMD`. An older documentation mirror lists an `aidj` mode, but this is absent from the installed 4.41.1 documentation. That old mode value is not a verified integration contract for this branch. Ordinary podcast/electric-radio `dj_program` and voice-list modules also do not establish AI Private DJ support. [Fixed adapter documentation](https://cdn.jsdelivr.net/npm/@neteasecloudmusicapienhanced/api@4.41.1/public/docs/home.md)

## Actual Private DJ request fields

Without location arguments, the fixed module sends a `data.extInfo` string containing:

```js
{
  noAidjToAidj: false,
  lastRequestTimestamp: new Date().getTime(),
  listenedTs: false
}
```

The timestamp is numeric and uses milliseconds. If `latitude` is provided, the module adds one `lbsInfoList` entry with `lat`, `lon`, and current time in seconds. Location is optional and is unnecessary to establish the base interface; research does not justify adding location collection.

There is no voice/persona name, persona ID, conversation prompt, continuation cursor, or previous-song identifier in this module's declared inputs. The commented historical sample in the upstream file is not its current executable request contract.

`createOption(query)` leaves the encryption selection empty. The pinned request helper resolves this to `eapi` because its installed `APP_CONF.encrypt` is true, using the configured `https://interfacepc.music.163.com` domain and the `/eapi/aidj/content/rcmd/info` transport path. The helper attaches its normal account/client headers and response-encryption flag. This follows the installed adapter's default behavior; it provides no evidence that changing client identity would enable the requested persona. [Fixed option helper](https://cdn.jsdelivr.net/npm/@neteasecloudmusicapienhanced/api@4.41.1/util/option.js), [fixed request helper](https://cdn.jsdelivr.net/npm/@neteasecloudmusicapienhanced/api@4.41.1/util/request.js), [fixed request configuration](https://cdn.jsdelivr.net/npm/@neteasecloudmusicapienhanced/api@4.41.1/util/config.json)

GluSpot's existing `ncmCall` passes a copy of the protected account cookie, a 12-second timeout, and disables random-IP behavior. Its explicit method allowlist omits both `aidj_content_rcmd` and `personal_fm_mode`; a direct call through today's bridge would be unsupported. `OnlineMusicService.personal_fm()` currently parses only `OnlineSong` batches. The existing stream path resolves each song separately through normal `song_url` at 128 kb/s; none of this establishes a DJ segment queue.

## Response and eligibility evidence still missing

The fixed TypeScript declaration is `aidj_content_rcmd({ latitude?, longitude?, ...RequestBaseConfig }): Promise<Response>`. `Response` is generic; it does not specify DJ-audio fields or music/voice sequencing. [Fixed declarations](https://cdn.jsdelivr.net/npm/@neteasecloudmusicapienhanced/api@4.41.1/interface.d.ts)

The following must be established before implementation can honestly claim the supplied experience:

- A successful authenticated response with its structural schema, including whether genuine spoken audio is supplied as a playable URL, an identifier requiring another lookup, or another content form.
- Segment order, duration, interruption/skip semantics, and the supported way to fetch the next DJ batch.
- Whether **豆花** is the account's returned/default persona, whether an exposed selection interface exists, and whether the older module supports the newer persona.
- Actual availability and error behavior for the current account/client, including any membership, regional, version, or rollout gates. None is inferred from the screenshot or guessed client headers.
- Audio URL validity and content access boundaries, together with an authorization path appropriate to any public/commercial release.

No response schema or persona-selection example was found in the fixed module, its types/documentation, or the bounded public-source/issue search. A public GitHub issue search for `aidj` in the maintained upstream returned no matching issues; this is a search result, not proof that every server issue is absent. No current account cookie, account API, signed audio URL, private-app data, or physical audio output was accessed.

## Integration boundary

Ordinary recommendation and mode selection can be considered separately from the DJ investigation. Any future implementation should retain `api/providers.py`, the existing online service/optional bridge, protected account storage, and owned playback/output coordinator. It needs evidence-backed models for voice/music segments only after the actual schema is understood.

Service-generated DJ speech must be distinguished from ordinary song recommendations. A locally generated TTS introduction would not verify or reproduce NetEase's **豆花** content. This research introduces no substitute voice, scraping route, account/client gate bypass, or runtime UI behavior.

## Verification

Read the canonical README and inspected the installed dependency, bridge allowlist/operations, online service, module types/documentation, option helper, request helper, and default request configuration. The five published 4.41.1 files linked above (Private DJ, FM, FM mode, declarations, and documentation) returned HTTP 200 and matched the installed text after newline normalization. Public published-package metadata and the official App Store product listing were also checked.

Only this document is added. `git diff --check -- docs/personal-dj-research.md` passed. Runtime tests are not applicable to this documentation-only study; live server behavior remains explicitly unverified. GluSpot version, runtime code, packaging, account files, Git refs, and repository visibility were not changed by this study.
