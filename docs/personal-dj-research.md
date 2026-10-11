# NetEase personal roaming and Private DJ research

Research date: October 11, 2026. GluSpot source version: `6.412`.

## Current implementation update

The subsequent implementation now exposes the pinned `aidj_content_rcmd`
through the existing bridge and service. Authenticated requests returned
genuine speech and songs in order; the strict parser was checked against a
six-resource response. A service-provided voice file (428,870 bytes) and its
authorized song (2,922,669 bytes, not a trial) passed the complete download
boundary. Both downloads took one HTTP request and their session files were
cleaned afterward. These probes preserved the protected account file's SHA256
and printed no signed addresses, credentials or account identity.

The music library now offers **私人DJ**, with genuine speech/song playback and
DJ continuation through the owned player. `M791-C2` remains an unidentified
persona: `announcerName` was null and the module has no verified persona-choice
input. **豆花** cannot yet be claimed as the returned voice. See
[current behavior and final verification](buffered-playback-and-personal-dj.md).

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
