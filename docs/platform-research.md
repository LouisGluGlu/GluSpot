# 再接音乐平台：调研

日期：2026-11-05。只读代码和公开网页，没有改播放、登录或打包。没有用真实账号去请求网易云、酷狗、QQ、酷我、咪咕或其他平台，所以“现在还能不能播”只写资料里能看到的，本次没复测的一律写未确认。

公开资料里的接口地址、签名和加密步骤没有抄进本文。要接的时候单独开任务，并先过下面的拍板。

## 1. 现在的平台抽象

程序内播放和系统抓取是两条路。程序内播放走 `OnlineMusicService`：登录、搜索、歌单、音频地址、歌词都变成不可变快照，真正发请求的是单独安装的 Node 桥。系统抓取继续跟外部播放器，不在这条桥上。

来源：[api/online_music.py](../api/online_music.py)、[docs/online-music-runtime.md](online-music-runtime.md)、[README 播放来源](../README.md)。

### 统一快照

| 快照 | 干什么 | 来源 |
| --- | --- | --- |
| `MusicSource` | 平台 id、显示名、运行时是否就绪、能力列表 | [api/online_music.py](../api/online_music.py) 的 `MusicSource` |
| `LoginSnapshot` | 扫码会话：等待、已扫、确认、过期、取消；二维码不进日志 | 同文件 `LoginSnapshot` |
| `AccountSnapshot` | 是否登录、用户 id、昵称、头像。不含 cookie | 同文件 `AccountSnapshot` |
| `OnlineSong` / `OnlinePlaylist` | 歌和歌单。私人 DJ 语音自带地址，不能拿去当普通歌要歌词 | 同文件 `OnlineSong` |
| `CatalogPage` | 一页结果、下一页偏移、还有没有、总数 | 同文件 `CatalogPage` |
| `StreamInfo` | 可播放的 http(s) 地址、是不是试听、音质、过期时间。地址不进日志 | 同文件 `StreamInfo` |
| `LyricPayload` | LRC、网易 YRC、QQ QRC、译文、罗马音 | 同文件 `LyricPayload` |

界面只读这些快照。网络在后台做。换源、取消、退出会作废晚到的结果。账号用 Windows DPAPI 加密，放在本机 `%LOCALAPPDATA%/GluSpot/accounts/`，不写进普通设置。来源：[docs/online-music-runtime.md 私密数据](online-music-runtime.md)。

### 桥接怎么接平台

Python 侧 `OnlineBackend` 只有三件事：这个源的运行时在不在、按操作名发一次请求、关闭。来源：[api/online_music.py](../api/online_music.py) 的 `OnlineBackend`。

Node 子进程不对外开 HTTP 服务，只用 stdin/stdout 交换一行一个 JSON。允许的源只有 `netease`、`qq`、`kugou`。来源：[native/online_music/bridge.cjs](../native/online_music/bridge.cjs)、[docs/online-music-runtime.md](online-music-runtime.md)。

已固定的三个包：

| 平台 | 固定包 | 直接许可证 | 来源 |
| --- | --- | --- | --- |
| 网易云 | `@neteasecloudmusicapienhanced/api@4.41.1` | MIT | [native/online_music/package.json](../native/online_music/package.json)、[native/online_music/licenses/netease-MIT.txt](../native/online_music/licenses/netease-MIT.txt) |
| QQ | `@yakult-green-tea/qq-music-api@3.1.4` | MIT | 同上 package.json、[qq-MIT.txt](../native/online_music/licenses/qq-MIT.txt) |
| 酷狗 | `MakcRe/KuGouMusicApi` 提交 `fe1838ae`，包版本 1.6.0 | MIT | 同上 package.json、[kugou-MIT.txt](../native/online_music/licenses/kugou-MIT.txt) |

上游 README 里还有“不要商用”的额外话，和 MIT 正文不是一回事，见第 3 节。

### 三家现在各自会什么

能力名单以服务代码为准，不按界面文案。

| 能力 | 网易云 | QQ | 酷狗 | 汽水 |
| --- | --- | --- | --- | --- |
| 扫码登录 | 有 | 有 | 有 | 无 |
| 我的歌单 | 有 | 有。自建歌单分页；回退接口超过 1000 首就明确说不支持 | 有 | 无 |
| 搜索歌和搜索歌单 | 有 | 有。搜索走单独核对过的公开目录接口，不把 cookie 拼进 URL | 有。文档写明歌曲搜索曾出现要账号的错误码 | 无 |
| 推荐歌单 | 有 | 有 | 无 | 无 |
| 每日推荐 / 私人 FM / 私人 DJ | 都有 | 无 | 无 | 无 |
| 红心、写入歌单 | 有 | 无 | 无 | 无 |
| 音频地址 | 有。标准音质 `br=128000`，空地址就是不能播，试听会标出来 | 有。`128` | 有。`quality=128` | 无 |
| 歌词 | 有。LRC、YRC、译文、罗马音 | 有。LRC，以及解密后的 QRC、译文、罗马音 | 无。服务直接拒绝 | 无 |

来源：[api/online_music.py](../api/online_music.py) 的 `_capabilities`、`stream_url`、`lyrics`；[native/online_music/bridge.cjs](../native/online_music/bridge.cjs) 里三家的 `stream_url` / `lyrics` 分支；[docs/online-music-runtime.md](online-music-runtime.md)。

酷狗桥接的模块白名单里写了 `lyric` 和 `search_lyric`，但分发函数没有把它们接到 `lyrics` 操作。Python 对网易云和 QQ 以外的来源直接报“此来源暂不支持逐字歌词获取”。上游仓库今天的说明里有“获取歌词”，那是上游现状，不是 GluSpot 已经接到界面上。来源：上面的 bridge 与 service；上游说明见 [KuGouMusicApi README](https://github.com/MakcRe/KuGouMusicApi)。

明确不做的事：桥接白名单没有解锁、跨平台配歌、用别家音源替换灰歌。来源：[docs/online-music-runtime.md](online-music-runtime.md)。

登录页上，汽水、Spotify、Apple Music 是灰色的“即将支持”，`login_supported` 为假。来源：[local/playback_entry_window.py](../local/playback_entry_window.py) 的 `ENTRY_SOURCES`。系统抓取能认出 Spotify 进程，那只是跟外部播放器，不是程序内登录播放。来源：[api/capture/capabilities.py](../api/capture/capabilities.py)。

汽水在 2026-10-10 的结论仍然是：没有能随这个 Qt 程序分发的扫码适配器，不复制参考项目里的混淆安全运行时。来源：[docs/online-music-runtime.md 汽水](online-music-runtime.md)。

## 2. 别人怎么接平台

下面只比较“接了谁、用什么方式、许可证让不让把代码拿进来”。播放是否今天仍可用，除特别写明外都未确认。

| 项目 | 平台 | 方式 | 许可证 | 能不能把代码拿进 GluSpot |
| --- | --- | --- | --- | --- |
| [YesPlayMusic](https://github.com/qier222/YesPlayMusic) | 网易云。灰歌可用 [UnblockNeteaseMusic](https://github.com/qier222/YesPlayMusic) 说明里的替换音源，网页版不做这件事 | 自己的界面 + 单独部署的网易云 API 服务。桌面版是 Electron | 仓库标 MIT。README 又写“仅供个人学习研究使用，禁止用于商业及非法用途” | 不要整份复制。MIT 正文和 README 限制冲突，商用前未确认。解灰/换源和 GluSpot 现有白名单相反 |
| [listen1_desktop](https://github.com/listen1/listen1_desktop) | 网易云、QQ、酷狗、酷我、bilibili、咪咕、千千 | 桌面版包了一份浏览器扩展。扩展里每个平台一个 JS provider，直接请求网站 | [MIT](https://github.com/listen1/listen1_desktop/blob/master/LICENSE.md) | 可以读结构、自己重写。若复制源文件，必须保留版权和 MIT 声明。复制请求代码仍然可能违反平台条款，许可证不管这个 |
| [MusicFree](https://github.com/maotoumao/MusicFree) / [桌面版](https://github.com/maotoumao/MusicFreeDesktop) | 本体不带任何平台。装什么插件就有什么源 | 插件是 CommonJS。插件自己做搜索、播放地址、专辑、歌手、导入歌单、歌词 | [AGPL-3.0](https://github.com/maotoumao/MusicFree/blob/master/LICENSE)。README 另外要求保留出处、不要商用 | 不要复制播放器或插件实现。AGPL 会要求结合后的程序也按 AGPL 提供源码。可以只学“播放器不内置音源”这个形状。[插件模板](https://github.com/maotoumao/MusicFreePluginTemplate) 是 MIT，模板不是现成音源 |
| [lx-music-desktop](https://github.com/lyswhut/lx-music-desktop) | 补充协议点名内置源包括酷我、酷狗、咪咕等。五个缩写是否就是酷我、酷狗、咪咕、QQ、网易云：未确认。一份写了这五个缩写的 [PR 2869](https://github.com/lyswhut/lx-music-desktop/pull/2869) 已关闭且没有合并 | 曲目信息从平台公开服务器拉。真正的音频地址来自用户自己选的“自定义源”脚本，软件不保证链接就是那首歌 | [Apache-2.0](https://github.com/lyswhut/lx-music-desktop/blob/master/LICENSE)，但 README 写补充协议优先，并禁止商业使用、要求 24 小时内删掉产生的版权数据 | 不要把补充协议当成普通 Apache。第三方自定义源脚本的许可证本次未逐份确认，不要收进仓库 |
| [NeteaseCloudMusicApi](https://github.com/Binaryify/NeteaseCloudMusicApi) | 网易云 | 非官方接口做成 Node 服务 | GitHub 显示已归档，`license` 字段为空，README 写“保护版权，此仓库不再维护”。推送停在 2024-02-28 | 不要从这份归档仓库取代码。许可证现在未确认 |
| [api-enhanced](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced) | 网易云。说明里包含登录、歌单、歌词、搜索、私人 FM，以及“歌曲解锁（解灰）” | 同上，给 Node 直接调用。GluSpot 已经锁了它的 4.41.1 | 仓库标 MIT | 继续当依赖可以，但要保留 MIT 声明。不要打开解灰。传递依赖不都是 MIT，见 [docs/online-music-runtime.md](online-music-runtime.md) |
| [jsososo/QQMusicApi](https://github.com/jsososo/QQMusicApi) | QQ | Express 服务，部分接口要 cookie | GPL-3.0 | 不要复制。分发结合后的程序会带上 GPL 义务 |
| [yakult-green-tea/qq-music-api](https://github.com/yakult-green-tea/qq-music-api) | QQ。当前 README 写扫码有手机 QQ、微信、QQ 音乐 App 几种通道 | 非官方 API，可嵌进 Node。GluSpot 已锁 3.1.4 | 当前 main 的 README 写 MIT，同时写“当前代码仅供学习，不可做商业用途”。3.1.4 那个 tag 的 README 是否同样：未确认 | 已经在用。商用和 MIT 正文冲突，未确认 |
| [MakcRe/KuGouMusicApi](https://github.com/MakcRe/KuGouMusicApi) | 酷狗。当前 README 写登录、歌单、搜索、播放地址、歌词、每日推荐等 | 作者写明是伪造请求头去调官方接口 | 当前 README 是 MIT，另有学习用途和禁止商业的声明。GluSpot 锁的 `fe1838ae` 是否也有这段声明：未确认。仓库里留存的许可证正文是 MIT | 已经在用旧提交。新功能不要整文件抄过来 |
| [qyhqiu/kuwoMusicApi](https://github.com/qyhqiu/kuwoMusicApi) | 酷我。文档有搜索、歌单、歌词、播放地址 | Node 服务，请求酷我网站 | Apache-2.0。最后推送 2023-07-25 | 可以在保留声明、标明改动的前提下借鉴。年份已久，接口是否还通未确认。文档目录里没有登录章节 |
| [jsososo/MiguMusicApi](https://github.com/jsososo/MiguMusicApi) | 咪咕。说明写部分接口依赖 cookie | Node 服务 | GitHub `license` 为空。最后推送 2024-02-08 | 未确认。不要复制 |
| [guohuiyuan/music-lib](https://github.com/guohuiyuan/music-lib) 和 [go-music-api](https://github.com/guohuiyuan/go-music-api) | 网易云、QQ、酷狗、酷我、咪咕、千千、汽水、Bilibili、JOOX、5sing、Jamendo、Apple Music 等。两份 README 对汽水搜索是否调通的说法不一致 | 非官方接口聚合。含音频下载、按歌名换源。汽水路径写了调用官方客户端组件给加密音频签名 | AGPL-3.0 | 不要复制，也不要把它的下载、换源、解密收进来。表格只是作者自己的能力声明，本次没有复测 |

Folia 是 AGPL-3.0，以前的播放源研究已经写过，这里不重复抄它的实现。来源：[docs/owned-playback-sources.md](owned-playback-sources.md)。

listen1 的千千 provider 能看清三件事：直接请求 `music.taihe.com`；`get_user` 固定返回失败；没有账号登录流程。同目录还有网易云、QQ、酷狗、酷我、咪咕、bilibili 的 provider 文件。那些文件的请求细节本次没有逐份打开，不把千千的写法说成所有平台都一样。来源：[taihe.js](https://github.com/listen1/listen1_chrome_extension/blob/master/js/provider/taihe.js)、[provider 目录](https://github.com/listen1/listen1_chrome_extension/tree/master/js/provider)。

洛雪 README 写得很直白：软件自己不算音频地址，只把歌名交给用户安装的自定义源；源返回什么链接就播什么。所以不能把“洛雪能播某平台”理解成“仓库里有一份可分发的正式适配器”。来源：[lx-music-desktop README 1.2](https://github.com/lyswhut/lx-music-desktop/blob/master/README.md)。

## 3. 许可证，大白话

GluSpot 仓库根目录没有项目级 LICENSE。本次只看到字体和 AirPlay 第三方目录里的许可证文件。以后如果开源，许可证还没定。在没定之前，不要把 GPL / AGPL 代码并进来，也不要把“README 禁止商用、许可证却写 MIT”的仓库整份抄进来。

| 许可证 | 看到它时怎么处理 | 依据 |
| --- | --- | --- |
| MIT | 可以复制、修改、随程序分发，必须带上版权声明和 MIT 正文。作者另在 README 写“禁止商用”时，两处冲突，商用未确认 | [listen1 LICENSE](https://github.com/listen1/listen1_desktop/blob/master/LICENSE.md)、[YesPlayMusic README 开源许可](https://github.com/qier222/YesPlayMusic)、[KuGouMusicApi 免责声明](https://github.com/MakcRe/KuGouMusicApi) |
| Apache-2.0 | 可以借鉴，要带许可证、保留声明、改过的文件要标明改动 | [kuwoMusicApi 仓库许可证](https://github.com/qyhqiu/kuwoMusicApi)、[Apache-2.0 原文](https://www.apache.org/licenses/LICENSE-2.0) |
| Apache-2.0 再加一份“冲突时以补充协议为准” | 不要当普通 Apache。洛雪补充协议禁止商业使用 | [lx-music-desktop README 项目协议](https://github.com/lyswhut/lx-music-desktop/blob/master/README.md) |
| GPL-3.0 | 不要复制进 GluSpot。分发时结合后的程序也要按 GPL 给源码 | [jsososo/QQMusicApi](https://github.com/jsososo/QQMusicApi) |
| AGPL-3.0 | 不要复制。它比 GPL 更进一步，网络服务也要提供对应源码。MusicFree、music-lib、go-music-api、Folia 都是这种 | [MusicFree LICENSE](https://github.com/maotoumao/MusicFree/blob/master/LICENSE)、[music-lib 仓库许可证](https://github.com/guohuiyuan/music-lib) |

代码许可证只管软件版权。它不授予歌曲、歌词、封面的再分发权。平台条款也不因为仓库是 MIT 就自动允许。这是以前播放源研究里已经写过的边界。来源：[docs/owned-playback-sources.md 代码与内容授权](owned-playback-sources.md)。

## 4. 候选平台

工作量是相对估计，不是工期：

- 小：现有三家里补一个操作，播放器不用改。
- 中：新平台，但仍是“搜索 + 歌单 + 一个 http(s) 音频地址 + 歌词”，桥接要新写。
- 大：拿不到 GluSpot 现在这种音频地址，得嵌官方播放器，或者要解密、换源。

“完整播放”指：登录后能拿到整首、可交给现有播放器的地址，而不是 30 秒试听，也不是只能在官方 App 里听。本次没有实播，下表的“能否定”只反映公开文档，没写死的就是未确认。

| 平台 | 接入方式 | 登录 | 完整播放 | 歌词 | 风险 | 工作量 |
| --- | --- | --- | --- | --- | --- | --- |
| 酷我 | 非官方网页接口。listen1 有 provider；[kuwoMusicApi](https://github.com/qyhqiu/kuwoMusicApi) 的 [文档](https://github.com/qyhqiu/kuwoMusicApi/blob/master/docs/README.md) 有搜索、歌单、歌词、播放地址 | 这份文档目录没有登录一节。music-lib 作者表写扫码登录和个人歌单都没有。本次未确认有没有可用的扫码 | 文档有播放地址。是整首还是试听、会员曲能不能播：未确认 | 文档有歌词接口。是整行 LRC 还是逐字：未确认 | 非官方。GitHub 显示该仓库最后推送是 2023-07-25。封号和条款风险未确认，不能当成允许 | 中 |
| 咪咕 | 非官方接口。listen1 有 [migu.js](https://github.com/listen1/listen1_chrome_extension/blob/master/js/provider/migu.js)。[MiguMusicApi](https://github.com/jsososo/MiguMusicApi) 写部分接口要 cookie | 个人歌单和扫码，music-lib 作者表写没有。MiguMusicApi 没有许可证。登录方式未确认 | 作者声称能解析歌曲。是不是整首：未确认 | 作者声称有歌词。格式未确认 | 无许可证不能抄代码。接口稳定性未确认 | 中 |
| 千千音乐 | listen1 直接请求 `music.taihe.com`。music-lib 作者表写搜索、歌词、歌曲可以，歌单搜索不稳定 | listen1 这份代码没有账号登录 | 代码在接口返回路径时就当播放地址。是不是整首：未确认 | 代码再去拉一个歌词地址，正文格式未确认 | 网站接口随时能改。没有账号就没有“我的歌单” | 中，若接受没有登录可以比酷我少一块 |
| Bilibili 音频 | 官方开放平台列出的是账号、稿件、专栏、直播，没有音乐曲库播放。[开放平台文档](https://open.bilibili.com/doc)。社区有非官方音频说明，网页端付费内容写明是试听 | go-music-api 作者表写可以扫码。个人歌单那一格是没有 | [社区文档](https://lxb007981.github.io/bilibili-API-collect/audio/musicstream_url.html)写：网页端付费歌曲是 30 秒试听；带会员 cookie 才写能拿更完整的地址。本次未复测。go-music-api 又写音频来自视频资源，和旧音频区不是同一条产品 | 社区和聚合项目声称有歌词。是字幕还是逐字：未确认 | 非官方接口可能随时失效。官方开放平台没有这条产品。不要把社区文档里的下载步骤收进程序 | 中到大。内容和现在的三家曲库不一样 |
| 汽水 | 本仓库已有结论：没有可分发的扫码适配器。新的 music-lib 声称要调用官方客户端里的签名组件，才能把加密音频解成整首；它的两份 README 对搜索是否调通说法还不一致 | music-lib 作者把扫码写成未调通。go-music-api 表里扫码也是没有 | 未确认。作者声称的整首依赖官方组件和加密音频，不能当成 GluSpot 能拿到的普通地址 | 作者声称有。格式未确认 | 高。加密音频、捆绑官方组件、AGPL，都不适合收进来。登录页应继续保持不可用 | 大，而且不建议做 |
| Spotify | 官方 [Web API](https://developer.spotify.com/documentation/web-api) 做资料、歌单和控制播放。[Web Playback SDK](https://developer.spotify.com/documentation/web-playback-sdk) 在网页里建一个 Spotify Connect 设备，音频留在 Spotify 播放器里 | OAuth。播放 SDK 的入门文档要求个人 Premium 账号的 access token | 不能拿到交给 Qt 的音频文件地址。完整播放必须走 Spotify 自己的播放器 | 公开文档这次没看到可交给外部歌词引擎的逐字歌词。未确认 | [官方入门](https://developer.spotify.com/documentation/web-playback-sdk/quick-start)写明：不能做商业串流集成；不能改音频内容；不能把录音和幻灯片、视频等视觉内容同步。歌词跟唱算不算这一条，未确认，不能自行解释成允许。注册地区名单有香港、澳门、台湾，没有中国大陆：[Where is Spotify available?](https://support.spotify.com/us/article/where-spotify-is-available/) | 大。播放模型要改，还可能和歌词产品撞条款 |
| Apple Music | 官方 [MusicKit](https://developer.apple.com/musickit/)：网页或 App 里播放，目录和资料库走 Apple Music API。开发者要自己的 developer token，用户再给 Music User Token | 用户授权 Apple Music 账号。开发者要加入 Apple Developer Program 才能签 token | MusicKit 自己播。公开 API 有试听对象。整首音频文件地址：未在本次读到的 API 目录里出现 | 歌曲对象有 [`hasLyrics`](https://developer.apple.com/documentation/musickit/song/haslyrics) 这种“有没有歌词”的标记。[Apple 工程师在论坛说明](https://developer.apple.com/forums/thread/702228)：只能用文档里的取 token 办法，别的办法可能随时被挡。公开目录这次没有逐字歌词接口 | 用网页特权 token 调未公开接口：同一帖里 Apple 工程师写明，文档以外的取 token 办法可能随时被挡。Windows 原生 MusicKit：本次 MusicKit 页面写了 Apple 平台、Android 和网页，没有写 Windows 桌面原生 SDK，未确认 | 大。要嵌播放器，歌词仍可能没有 |
| YouTube Music | 本次没有找到 YouTube Music 自己的官方开发者 API。能对上的是 YouTube Data API，它不是音乐会员曲库接口 | 若走 YouTube API，是 Google 开发者凭据加用户 OAuth。Music 账号登录：未确认 | 官方政策禁止用 API 下载、缓存、离线播放 YouTube 视听内容，也禁止把音轨拆出来。[开发者政策 E.1](https://developers.google.com/youtube/terms/developer-policies)、[合规说明里的拆音轨例子](https://developers.google.com/youtube/terms/developer-policies-guide) | 未确认。没有官方“拿逐字歌词”的接口记录 | 高。抽音频做桌面播放器和上述政策相反。大陆网络能不能打开 YouTube：本次没有官方依据，未确认 | 大，而且和现有“自己播地址”的做法相反 |

表里的“作者表”指 [music-lib](https://github.com/guohuiyuan/music-lib) 和 [go-music-api](https://github.com/guohuiyuan/go-music-api) 自己的 README，本次没有复测。

Jamendo 在 music-lib 里被标成 CC 曲库。它有没有官方 API、歌词全不全，本次没有打开 Jamendo 自己的文档，未确认。不放进优先名单。

## 5. 建议先做哪几个

下面三段是 2026-11-05 第一次调研时的建议。当天稍后用户已经改了顺序：汽水、Apple Music、Spotify 优先，酷我和咪咕靠后，并且允许非官方接口，只要长期能用。当前方案以第 8 节为准。

建议顺序：

1. 酷我。它和现在的模型一样：搜索、歌单、一个播放地址、一份歌词，国内用户用得上。listen1 和一份 Apache-2.0 的 Node 服务都证明有人把这四件事接通过。登录是缺口，仓库也旧，所以第一版可以先做“能搜公开歌单和歌曲”，我的歌单等登录查清再做。
2. 咪咕。同样是国内曲库，listen1 已有独立 provider。MiguMusicApi 没有许可证，必须自己写桥接，不能把那个仓库抄进来。个人歌单作者写成没有，要先接受“可能没有我的音乐库”。
3. 先别接 Spotify、Apple Music、YouTube Music、汽水。前三家拿不到现在这种音频地址，条款还可能碰歌词或抽音频。汽水要碰加密音频和官方组件，本仓库已经把它标成不可用。

如果目标是“少冒险、先让已有平台更好听”，比新接平台更小的一件事是：给酷狗补歌词。模块名已经在桥接白名单里，服务层却直接拒绝。这不是新平台，要不要插队由你定。

不建议从聚合库“一次接十家”。那些项目把下载、换源、解密放在一起，许可证是 AGPL，和 GluSpot 现在“只播当前平台授权地址”的边界相反。

## 6. 需要拍板

2026-11-05 已定的两件事：可以走非官方接口，但只保留长期还能用的做法；项目不卖软件、不接广告，顶多在爱发电这类地方收捐赠。捐赠算不算上游 README 里的“商用”，仍然未确认，不能当成已经合法。

| 要定的事 | 状态 |
| --- | --- |
| 新平台是否继续走非官方接口 | 已定：可以，但必须能长期用。失效后先退回系统媒体会话和官方公开接口 |
| 商用还是捐赠 | 已定：不商用，只接受捐赠。仓库仍然没有项目 LICENSE |
| 优先平台 | 已定：汽水、Apple Music、Spotify 先做；酷我、咪咕靠后 |
| 酷我第一版没有登录、没有我的歌单，能不能上 | 还没定。公开资料里没有确认可用的酷我扫码 |
| 咪咕若同样没有个人歌单，还接不接 | 还没定。music-lib 作者表是这么写的，本次未复测 |
| 酷狗歌词要不要排在新平台前面 | 还没定。改动比新平台小 |
| Spotify 的歌词跟唱算不算“把录音和视觉内容同步” | 还没定。官方写了禁止同步，没有写歌词算不算 |
| 要不要做跨平台换源或解灰 | 还没定。现有桥接明确没有。按长期可用，不建议做 |

## 7. 这次没核实的事

- 没有登录任何平台，没有请求播放地址，所以上表的“今天还能播”都未确认。
- 没有逐行阅读 listen1 除千千以外的 provider，没有阅读洛雪自定义源脚本。
- 没有核对 kuwoMusicApi 2023 年最后一版和今天酷我网站是否仍一致。
- 没有核对咪咕仓库为什么没有许可证，只确认 GitHub 的 license 字段为空。
- Apple Music 在 Windows 上除了嵌网页以外有没有支持的原生播放 SDK：未确认。
- YouTube 在中国大陆的网络可达性：未确认。
- 汽水、Apple Music、Spotify 的 Windows 客户端这次没有实机播放，系统媒体会话是否真的上报：未确认。
- 抖音开放平台“汽水音乐”页这次只读到目录名，接口正文没有展开。

## 8. 前提：不商用，只接受捐赠

从这一节起，许可和条款都按这个前提读：GluSpot 不出售软件、不放广告、不把播放能力卖给别人。用户可以在爱发电等处自愿捐赠。这是产品决定，不是律师意见。

| 规则 | 在“不商用、只捐赠”下怎么看 | 来源 |
| --- | --- | --- |
| MIT 正文 | 允许使用、修改、分发，要保留版权声明。捐赠不改变“必须带声明” | [listen1 LICENSE](https://github.com/listen1/listen1_desktop/blob/master/LICENSE.md) |
| README 另写“禁止商用” | 爱发电捐赠算不算商用：未确认。不能因为“我们只收捐赠”就抄这些仓库 | [YesPlayMusic README](https://github.com/qier222/YesPlayMusic)、[KuGouMusicApi 免责声明](https://github.com/MakcRe/KuGouMusicApi)、[qq-music-api README](https://github.com/yakult-green-tea/qq-music-api/blob/master/README.md) |
| GPL / AGPL | 不商用也不能免除。复制进程序后，分发时仍要按对应许可证公开源码 | [MusicFree LICENSE](https://github.com/maotoumao/MusicFree/blob/master/LICENSE)、[music-lib](https://github.com/guohuiyuan/music-lib) |
| Spotify 播放条款 | 不商用对上了“不能做商业串流集成”这一条的字面。另一条“不能把录音和幻灯片、视频等视觉内容同步”没有写“非商业就可以”。歌词跟唱算不算：未确认 | [Web Playback SDK 入门](https://developer.spotify.com/documentation/web-playback-sdk/quick-start) |
| Musixmatch | 条款默认只授予非商业使用，同时写明要付适用的费用。和“不卖软件”对得上，但不是免费全词。本次看到的定价从每月 49 美元起，基础档只有静态歌词，没有逐字时间，也不许缓存 | [API Terms 1.1](https://about.musixmatch.com/apiterms)、[定价](https://developer.musixmatch.com/plans) |
| LRCLIB | 服务端代码是 MIT。歌词正文的版权是另一件事，作者没有把它说成 CC0。维护者在讨论里说展示歌词“generally OK”，并要求带能联系到的客户端标识；这不是歌词版权授权 | [lrclib 仓库](https://github.com/tranxuanthang/lrclib)、[讨论 #53](https://github.com/tranxuanthang/lrclib/discussions/53) |
| Apple MusicKit | 要用它，得是付费的 Apple Developer Program 成员。这是开发者账号费用，不是把 GluSpot 变成商业软件 | [Apple 工程师说明](https://developer.apple.com/forums/thread/706861) |

## 9. 什么叫长期可用

一种接法要同时过三关，才算能长期用：

1. 接口有公开文档，或者是操作系统自己的接口，不靠猜签名。
2. 维护还在。归档、几年不更新、或作者写明随时会封，都不算。
3. 挂了以后有退路。退路顺序：先读 Windows 正在播放的客户端状态，再用官方公开接口补歌名和歌词。拿不到就只显示歌名，不换源、不解密。

| 方式 | 稳定性 | 维护 | 挂了以后 | 长期可用 |
| --- | --- | --- | --- | --- |
| Windows 系统媒体会话（SMTC） | 高。系统从 Windows 10 1809 起提供标题、歌手、专辑、封面缩略图 | 微软公开 API，不跟某一家音乐接口改版 | 客户端不上报时，这一路本来就没有数据，不能再去扒窗口凑 | 适合当保底。它不包含歌词 | [媒体属性](https://learn.microsoft.com/en-us/uwp/api/windows.media.control.globalsystemmediatransportcontrolssessionmediaproperties?view=winrt-22621)、[封面](https://learn.microsoft.com/en-us/uwp/api/windows.media.control.globalsystemmediatransportcontrolssessionmediaproperties.thumbnail?view=winrt-28000) |
| 现有歌词链：网易云搜索、QQ 搜索、LRCLIB | 中。三家都按歌名和歌手找词，不要求用户登录那一家 | 网易云、QQ 是非官方页面或接口，会失效。LRCLIB 有公开文档，匿名可用，但限流数字不保证 | 一家失效就用下一家。三家都没有时，沿用现在的无歌词封面 | 可做歌词保底。不是逐字承诺 | [api/lyrics.py](../api/lyrics.py) 的 `ONLINE_SOURCE_ORDER`、[LRCLIB 文档](https://lrclib.net/docs) |
| Spotify 官方 Web API / 网页播放 SDK | 中高。文档在，但播放音频留在 Spotify 播放器里，不给文件地址 | Spotify 自己维护。入门写明要 Premium，且不能做商业串流 | 不能播时退回系统媒体会话，只读状态和封面 | 适合以后做“控制官方客户端”。不适合替换现在的库内播放 | [Web API](https://developer.spotify.com/documentation/web-api)、[播放 SDK](https://developer.spotify.com/documentation/web-playback-sdk) |
| Apple MusicKit / Apple Music API | 中高。目录、搜索、资料库、网页播放都有文档 | Apple 维护。要付费开发者会员才能签 token。没有订阅时只能碰已购或同步的资料库，曲库播放要订阅 | 公开接口没有逐字歌词。词用 LRCLIB 和现有搜索链。不要用网页特权 token | 官方播放长期可维护，但要改播放器模型，不进最小版本 | [MusicKit](https://developer.apple.com/musickit/)、[生成 developer token](https://developer.apple.com/documentation/applemusicapi/generating-developer-tokens)、[WWDC26：没有订阅只能访问已购或已同步的音乐](https://developer.apple.com/videos/play/wwdc2026/254/) |
| 抖音开放平台里的汽水目录 | 未确认。这次只看到目录里有“首页推荐”“相关歌曲推荐” | 官方页面还在，正文没读到 | 没有播放和歌词正文前，不能当保底 | 未确认。不能写成已经能接播放 | [汽水音乐 OpenAPI 目录](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/qishui-music/) |
| Musixmatch 官方 API | 条款稳定，默认非商业，并且要付费 | 官方在维护。本次定价页最低是每月 49 美元的基础档：有静态歌词，没有逐字时间，不许缓存。逐字时间从每月 199 美元的档开始 | 没有买到对应档位就不要显示它的词，退回 LRCLIB | 许可方向和“不商用”一致，但要月费，不进最小版本 | [API Terms](https://about.musixmatch.com/apiterms)、[定价](https://developer.musixmatch.com/plans) |
| 汽水 / Spotify / Apple 的非官方整曲或私有歌词接口 | 低。签名、cookie、特权 token 会变 | 社区项目在跟，Apple 工程师写明非文档 token 可能随时被挡。汽水解密还要调用官方客户端组件 | 不要自动换一家的音频来顶。退回系统媒体会话加公开歌词 | 不过长期可用这一关。最小版本不做 | [Apple 论坛](https://developer.apple.com/forums/thread/702228)、[music-lib 汽水说明](https://github.com/guohuiyuan/music-lib) |
| UIA 读窗口、CDP 附调试端口 | 低到中。网易云、QQ 已经有专用路径，这三家没有 | 客户端一改界面就坏 | 退回 SMTC 的进度，哪怕它几秒才跳一次 | 不作为这三家的第一版 | [api/capture/capabilities.py](../api/capture/capabilities.py)、[api/providers.py](../api/providers.py) 的 `_player_position_reader_for` |

现有程序内播放用的网易云、QQ、酷狗包，也按这三关看：包还在更新的可以继续用；归档仓库和 2023 年后再无推送的酷我包，不能因为“以前能播”就当成长期方案。挂了以后同样退回系统媒体会话，不换源。

## 10. 最小版本：只读客户端状态，再加上歌词

先不做登录，也不在 GluSpot 里放这三家的音频。用户自己打开官方客户端听歌。GluSpot 只读 Windows 正在播放会话，再用歌名去找歌词。

系统会话能给的，微软写明了：标题、歌手、专辑、封面缩略图，以及时间轴和播放状态。歌词不在这里。来源见上一节的微软文档。GluSpot 现在的系统抓取已经会挑一个外部会话，读这些字段，并按歌名、歌手、专辑、时长去找词。封面走会话缩略图。来源：[api/providers.py](../api/providers.py) 的 `_select_media_session`、`_read_visual_data`、`_read_thumbnail`；歌词顺序是网易云、QQ、LRCLIB，见 [api/lyrics.py](../api/lyrics.py)。

这一路不区分是不是会员曲。客户端肯上报，就有歌名和进度；不肯上报，就没有。进度可能几秒才动一次，歌词跟唱会比网易云的调试端口粗。这是保底，不是逐字承诺。

UIA 和 CDP 对这三家都不可行，作为第一版：进度读取器只为网易云和 QQ 创建；CDP 暂停补发也只认网易云进程。汽水在设置里有一个默认安装路径，那只是配置占位，没有对应的窗口读取器。

| 平台 | 系统抓取现在能不能认 | 库内登录播放 | 最小版本实际怎么做 |
| --- | --- | --- | --- |
| Spotify | 代码已把 `spotify.exe` 和名字里的 Spotify 标成 SMTC。实机有没有上报：未确认 | 不行。官方播放器不交出音频地址。非官方整曲不过长期可用 | 先在 Windows 上确认现有抓取已经能跟上 Spotify。歌词用现有三来源。不新做登录 |
| Apple Music | 代码没有单独的 Apple Music 名字。会话若出现，会落到通用抓取，显示名可能是一长串应用 id。第三方工具自称能读到 Windows 版 Apple Music 的会话，GluSpot 没复测 | 不行，作为最小版本。MusicKit 要付费开发者账号；曲库播放还要用户订阅。公开 API 没有逐字歌词 | 只加显示名，并在 Windows 上看会话出不出现。词仍走现有搜索。不嵌 MusicKit |
| 汽水 | 应用 id 里若带“汽水 / qishui / douyin”，显示名会写成汽水音乐。没有专用 SMTC 能力项，也没有窗口读取器。官方客户端有 Windows 10 及以上的下载页。它会不会上报会话：未确认 | 不行。本仓库已认定没有可分发的扫码适配器。新的非官方做法要解密或调用官方签名组件，两份说明还互相矛盾 | 先确认官方 Windows 客户端会不会出现在系统正在播放里。会，就沿用通用抓取和现有歌词。不会，这一版就停，不扒窗口 |

来源：[api/capture/capabilities.py](../api/capture/capabilities.py)、[api/providers.py](../api/providers.py) 的 `_source_app_name` 与 `_player_position_reader_for`、[local/settings_store.py](../local/settings_store.py) 的 `qishui` 配置、[汽水 Windows 下载页](https://www.qishui.com/download)、[一份自称读取 Apple Music Windows 会话的插件说明](https://github.com/Brian-Simmons1/vencord-apple-music-windows)。

LRCLIB 文档现在要求请求带上应用名和联系方式。GluSpot 现有请求头只有来源网页，没有应用名。最小版本若继续用它，应补上标识。这是文档要求，不是新接口。来源：[LRCLIB 客户端标识](https://lrclib.net/docs)、[api/lyrics.py](../api/lyrics.py) 的 `_lrclib_headers`。

### 三家各自的官方能力

| 平台 | 登录 | 官方能给 | 歌词 |
| --- | --- | --- | --- |
| Spotify | OAuth。播放 SDK 入门要求 Premium 账号的 token。注册地区有香港、澳门、台湾，没有中国大陆 | 资料、搜索、歌单、控制播放。音频在 Spotify 自己的播放器里 | 本次读到的 Web API 概览没有歌词。词用 LRCLIB 和现有网易云/QQ 搜索。Musixmatch 要付费，基础档还没有逐字时间 |
| Apple Music | 用户授权，外加开发者自己签的 token。开发者必须是付费 Apple Developer Program 成员 | 目录、搜索、资料库、网页里的 MusicKit 播放。整首文件地址未在公开目录里出现 | 公开对象只有“有没有歌词”。逐字词在未公开接口上，官方说可能随时被挡。合法保底仍是 LRCLIB 和现有搜索 |
| 汽水 | 官方目录这次没看到登录或扫码说明。未确认 | 目录名只有首页推荐、相关歌曲推荐。播放地址未确认 | 官方歌词：未确认。保底仍是按歌名去现有歌词链 |

Spotify 与 Apple 的链接见第 4 节和第 9 节。汽水官方目录见 [开放平台](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/qishui-music/)。

### 最小版本要改什么

| 项 | 范围 | 工作量 | 风险 |
| --- | --- | --- | --- |
| 认人和显示名 | Spotify 已有。Apple Music、汽水只补名字映射，不新写抓取器 | 小 | 实机会话没出现时，改名也没有歌 |
| 歌词 | 继续网易云、QQ、LRCLIB。给 LRCLIB 补应用标识 | 小 | 歌名对不上就没有词。LRCLIB 限流会变，文档写明数字不保证 |
| 进度和封面 | 继续系统会话。不给这三家做 UIA / CDP | 小，主要是 Windows 验收 | 进度粗，封面可能没有或很小 |
| 库内登录播放 | 不做 | 不做 | 非官方整曲和特权歌词都过不了长期可用 |
| 条款 | 不卖软件。Spotify 歌词跟唱是否违规仍未确认，最小版本先只读客户端，不把 Spotify 音频放进 GluSpot | 文档和验收 | 未确认前，不要宣传成“官方授权歌词” |

## 11. 建议的实施顺序

1. Spotify 的只读会话。代码已经认得进程，先在 Windows 上看歌名、进度、封面和现有歌词链是否够用。
2. Apple Music 的只读会话。补显示名，同一套歌词。MusicKit 等有人拿到付费开发者账号、并接受公开接口没有逐字歌词之后再单独立项。
3. 汽水的只读会话。先看官方 Windows 客户端会不会上报。官方开放平台在播放和歌词上仍是未确认，非官方解密不排进这一版。
4. 酷我和咪咕仍靠后。它们的非官方包要么多年不更新，要么没有许可证，过不了这次的长期可用标准。

三家都挂了系统会话时，降级是同一句：只留歌名和封面，歌词空着，不换源、不解密。

