# Verified Private DJ voice contract

Evidence date: October 12, 2026. GluSpot source version: `6.412`.

The real service catalog identifies **豆花、黄俊、李源、庞舸**, and four
bounded recommendation requests returned speech with the requested catalog
`timbreKey`. This establishes genuine service-side selection, rather than a
local replacement voice. The feature remains part of personal FM. Selection
uses a recommendation parameter and does not modify an official account
preference.

## Public official-client evidence

The source chain was [NetEase's download page](https://music.163.com/download)
→ its [mobile download page](https://music.163.com/st/download) → that page's
[published JavaScript](https://s5.music.126.net/static_public/5d2e7e0f4c302eee822efc3d_5d2e7e0f4c302eee822efc3f/index.5b4c729bda1ef383c4a1.js)
→ its declared [Android download route](https://music.163.com/api/android/download/latest2)
→ [official package 9.6.05](https://d1.music.126.net/dmusic/NeteaseCloudMusic_Music_official_9.6.05.260923162310_3264.apk).

Only anonymous, bounded HTTP ranges were read. The ZIP directory transferred
2,772,414 bytes; selected DEX/default-FM entries transferred 63,081,049 bytes;
the manifest transferred another 53,692 bytes. The official client was never
installed or executed. Original binaries and static-analysis output remain in
ignored runtime storage and are not distributed with GluSpot.

The binary manifest supplies `com.netease.cloudmusic`, `versionName=9.6.05`,
`versionCode=9006005`. These values were parsed from the manifest, not inferred
from the package filename.

| Public entry | SHA256 |
| --- | --- |
| Download JavaScript | `4c0eff7b7e2414a575196ace0c0a0014d14aec02ba26720e69b380b485a9dce3` |
| `AndroidManifest.xml` | `5cb7248cc7bcdac6b94e34cb3b1601f6eae55316f92fc937864f0861058de452` |
| `classes4.dex` | `ea91ef26eeb837629c0fef254e4c2df08460d86c6ab9165328632e088af2117d` |
| `classes14.dex` | `80d658f4d9d49a9936c3749cecc5e5d0deab729c7b730d5c9ec6f3f6f71367a0` |
| `classes15.dex` | `f96846e15efb55edbd917bb87464bd3c5c927765db45d653d798264ee10ab37b` |
| `classes17.dex` | `4fdb509f45e4172149760173e59237344ee0f155ce25c178f859aca3f3c04a6b` |
| `classes19.dex` | `32d1909d6390aa223a11345eac108a19d186df83e3fe1ebb5bd986ea5f6be3ce` |

The following is an original protocol summary, not reproduced application
source:

- `AIDJModeStateManager.g` in `classes19.dex` requests
  `aidj/audio/timbre/get` through `r32/e.b`, without business parameters. It
  requires `code=200` and a nonempty `data.timbreOptionsInfos` array.
- The manager's `C` and `J` accessors read `timbreKey` and `name` respectively.
  Its typed catalog DTO includes `name`, `desc`, `defaultAudio`, and
  `timbreKey`; media preview/address and presenter-profile fields also exist.
- `rp0/d.d`, associated with `AIDJApi.kt`, sends `extInfo` as a JSON string
  **and a separate top-level `timbreKey`** to `aidj/content/rcmd/info`.
  `ExtInfo` defines `manualedTimbre`; the manager obtains this boolean from
  whether a selected timbre has been locally stored. GluSpot sends true when
  it supplies a catalog-validated voice.
- `UpdateAIDJTimbreInfoHandler` reads `timbreKey`, resolves it against the
  catalog, and signals the playback process. The manager stores the choice
  locally. No server preference-write endpoint was inferred or invoked.
- `r32/e.b` builds `r42/a`, whose parent request is POST. `r42/a.z` and
  `i42/a.i` map relative paths through `/eapi/`. `ts/a` constructs
  `interface3.music.163.com` and `interface9.music.163.com`; its normal
  default chooses interface3. `AbsCookieStore.os()` returns `android`, while
  `appver` and `versioncode` come from the installed package metadata.

## Authenticated, read-only verification

The existing protected account was read through `ProtectedCredentialStore`;
only stdio carried its envelope. Probes used the installed 4.41.1 request and
option helpers, a copied cookie, a 12-second timeout, and disabled random-IP
behavior. No credentials, account IDs, signed addresses, or preview addresses
were written to research output. The protected account file's SHA256 remained
identical before and after every probe.

The installed default request profile returned `code=200,data={}`. Changing
only the evidence-backed host to interface3 still returned the same empty
object. With the public package's three verified profile fields
`os=android,appver=9.6.05,versioncode=9006005`, the same catalog endpoint
returned four real entries. No invented device identity, location, header,
persona ID, or account preference was used.

| Returned catalog name | Returned `timbreKey` | `defaultAudio` |
| --- | --- | --- |
| 豆花 | `F793-C2` | true |
| 黄俊 | `M791-C2` | false |
| 李源 | `M792-NP` | false |
| 庞舸 | `M795-NP` | false |

All four entries had `bindingMode=null`. Subsequent recommendation requests
used the same profile, the catalog key, and the established `extInfo` fields
`noAidjToAidj`, `lastRequestTimestamp`, `listenedTs`, `manualedTimbre`. Every
returned speech record had a playable-address field and its requested
`timbreKey`; all four names passed. `announcerName` remained null, so names
come from the real catalog. **The previously unidentified `M791-C2` is now
verified as 黄俊.**

The service can omit an introduction for an individual song: the observed
庞舸 response contained three songs and two associated speech segments. An
initial 豆花 response also contained one leading speech segment without a
song association before the associated introduction. Its classification was
not preserved by that first metadata-only probe; later bounded probes did
not reproduce it. It must not be blindly relabeled as a song introduction.

The actual production `OnlineMusicService` and unchanged production bridge
were then exercised together through a read-only credential-store wrapper.
One account-status request, one catalog request and four selected-voice
recommendation requests parsed successfully. 豆花 returned two introductions
and three songs, 黄俊 one introduction and three songs, 李源 three
introductions and three songs, and 庞舸 two introductions and three songs.
黄俊's batch began with a song. Every returned speech key matched its chosen
catalog key and every parsed introduction identified its following song.
There was no physical playback or credential write, and the protected file
remained byte-identical. These observations confirm production-path selection
and parsing; the service does not guarantee an introduction for every song.
An entirely song-only DJ batch has not been verified and remains rejected.

## Integration boundary

`PersonalDjVoice` exposes `timbre_key`, `name`, `description`, `is_default`,
and the optional genuine `preview_stream`/`preview_duration_seconds`.
The stream is excluded from dataclass representations, as are its addresses.
Runtime choices are parsed from the actual catalog; the table
above is evidence, not a production lookup table. The cached snapshot is
bound to the account generation and cleared on login replacement, logout,
expiry, and service shutdown. GUI getters perform no requests.

`personal_dj(source_id="netease", timbre_key="")` runs on a worker. An empty
selection resolves the real catalog default. A supplied key must match the
current account's catalog; an unknown key is rejected before recommendations
are requested. Every returned speech key is checked against the selected
key. Catalog selection and recommendation are fenced to the same account
generation, so logout or account replacement cancels the request. Catalog
and recommendations alone use the verified profile; ordinary
music/login/search/lyric/stream requests retain their existing behavior.

Parsing retains actual song/introduction order, including individual songs
without introductions. One bounded compatibility rule handles the observed
unassociated prefix: only the first audio of the first resource may be
omitted, only when its association is missing/empty and a later audio in the
same list explicitly identifies the first following song. The omitted
audio's URL, identity, duration, expiry and selected voice are still
validated. This does not classify its unretained text type as a welcome,
drop speech from later resources, accept non-list associations, or weaken
trailing/mismatched-introduction checks.

The bridge copies account cookies before supplying the DJ profile, so the
profile is not persisted to credentials. Existing cookie-refresh handling
remains in the established protected boundary. The service exposes only the
validated catalog preview needed for audition; account/presenter identifiers
are discarded.

Validation includes real catalog/selection metadata requests without physical
playback, the six production-service requests above, and 57 focused
service/parser/actual-child bridge tests (3.261 seconds). Node syntax and
owned-file diff checks passed. The root task
records complete integration and full-suite verification separately.

## Official audition evidence

The catalog's `defaultAudio` is a **boolean default-choice marker**, not an
audio file. The separate `playUrl` is the genuine audition address;
`duration` is its service-declared duration in seconds. A bounded read-only
catalog request downloaded all four actual auditions, with no account write,
physical playback, or saved addresses. All downloads matched the catalog's
declared byte counts. Original audio remains in ignored runtime storage and
is not shipped as a GluSpot asset.

| Voice | Declared seconds | Downloaded bytes | SHA256 |
| --- | ---: | ---: | --- |
| 豆花 | 7 | 58,605 | `c5cca7dae7360f79295126e78ebc1e8920b750a738bed8808ddbde3cb0568b43` |
| 黄俊 | 7 | 63,021 | `54043cdab9723f2252be46cf00a675d0acab32272859dea4f4b5fa360e432f89` |
| 李源 | 8 | 81,165 | `5166cbfd8a926b7bff61e420f00aee65d3d64f4ef21af714b129fbd0d53950d5` |
| 庞舸 | 6 | 65,565 | `ad49640450be485a836e2c177ab5b7caed4ac7a6fc8ea208ce8537df13ddb70d` |

Local, unprompted multilingual ASR (`faster-whisper` 1.2.1, base model,
CPU int8, Chinese, beam 5) found a different personal self-introduction in
each audition. For example, the 豆花 audition introduces her name, mentions
music and getting to know the listener, and invites the listener to hear
songs together. The model produced homophone errors, so this is evidence of
the samples' content and difference, not an exact transcript. No audio was
sent to a transcription service. These auditions do **not** contain the
requested common phrase “我是您的私人dj，接下来我会带您走进音乐的世界”.
No verified arbitrary-text synthesis contract for these four voices was
established; system speech must not be presented as their voices.
The user subsequently approved these genuine official auditions instead of
the common sentence. The implementation therefore plays the returned clip
when a voice is chosen, without synthesized speech or a bundled substitute.

Preview parsing reuses `StreamInfo`, validates HTTP(S) addresses and bounded
finite positive durations up to 120 seconds, and invents no expiry timestamp.
Missing previews preserve catalog compatibility; malformed supplied previews
are rejected. Preview snapshots share the catalog's account-generation
invalidation and do not expose account identity. The current service/parser/
bridge suite passed 63 tests (3.232 seconds) after preview integration and the
catalog-refresh identity repair. A
further production-service read-only catalog request parsed all four genuine
preview streams with correct identity and durations; the protected account
remained byte-identical. Root
records playback integration and final verification separately.

A successful same-account-generation refresh reuses a prior immutable voice
only when every retained voice field and the complete preview `StreamInfo`,
including its hidden URL, are equal. Directory reordering preserves these
identities while adopting the new service order. This prevents opening or
refreshing a selector from canceling an unchanged active audition. A changed
name, description, default marker, key, duration, or address produces a new
voice object. Logout/account replacement still clears the catalog; a later
identical response creates new identities and cannot authorize old auditions.
Focused service regressions cover identical refresh, reordered refresh,
changed preview addresses and retained metadata, and identical responses
after logout/relogin. No new public account interface was added.
