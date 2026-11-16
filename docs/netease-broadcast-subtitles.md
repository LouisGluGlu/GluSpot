# 网易云私人播报有没有字幕

结论：**没有。** 现有接口和 GluSpot 已经读取的页面数据里，没有私人播报的逐句字幕或口播文稿。不要用语音识别去补。

## 已经在读的接口

私人 DJ 走两条请求，都在 `native/online_music/bridge.cjs` 的 `ncmPersonalDjCall`：

| 用途 | 路径 |
| --- | --- |
| 音色目录 | `POST /api/aidj/audio/timbre/get` |
| 播报和歌曲队列 | `POST /api/aidj/content/rcmd/info` |

队列解析在 `OnlineMusicService._personal_dj_batch`。每段语音只保留这些字段：

- `audioUrl`、`audioId`、`duration`、`validTime`
- `introductionRelatedSongIds`
- `timbreKey`
- `textType`

`textType` 是最多 24 字的短分类，例如「开场」。标题会写成「私人DJ · 开场」。它不是口播原文。

同一条队列里的 `song` 才有普通 `songData`。那些歌的歌词仍走原来的 `lyric` / `lyric_new`。语音段不走歌词接口，文档 `docs/buffered-playback-and-personal-dj.md` 也写了这一点。

公开的同类模型（HyPlayer 的 `AiDjContentRcmdAudioResourceValueAudio`）同样只有地址、时长、`audioId`、`introductionRelatedSongIds`、`gain` 等，没有字幕字段。

## 已经在读的页面数据

桌面抓取适配器 `api/capture/netease_js.py` 只听 `PlayProgress`、`PlayState`、`Load`、`End`。它不读私人播报的字幕节点。

## 想显示播报文字时怎么拿

- 分类短标签：读语音对象的 `textType`。现在已经解析。
- 音色名字和介绍：读音色目录的 `name` 和 `desc`。这是音色说明，不是某一句播报。
- 口播原文：上述接口和页面数据都没有。不要对音频做识别来假装它是接口返回的字幕。
