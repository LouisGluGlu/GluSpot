# 在线音乐账户与运行时来源

日期：2026-10-10；对应 `codex/owned-playback-sources`，GluSpot 版本保持 `6.412`。产品入口、Qt 自主播放和当前限制见[实施与验证](owned-playback-entry.md)。本文说明独立账户服务、实际调用接口、依赖来源和验证范围。

## 固定依赖与分发记录

`native/online_music/package.json` 和完整 `package-lock.json` 固定以下适配器。桥接代码独立编写，没有复制 Folia 的 AGPL 或 Mineradio 的 GPL 源文件；这两个参考项目的固定版本和研究证据见[播放源研究](owned-playback-sources.md)。

| 适配器 | 固定来源 | 直接包许可证 |
| --- | --- | --- |
| 网易云 | npm `@neteasecloudmusicapienhanced/api@4.41.1` | MIT |
| QQ | npm `@yakult-green-tea/qq-music-api@3.1.4` | MIT |
| 酷狗 | `MakcRe/KuGouMusicApi` 的提交 `fe1838ae58ea69f84941ffa8758a34a9121bb2ea`，包版本 `1.6.0` | MIT |

前两个包使用 npm 官方 registry；酷狗包在官方 registry 返回 404，因此锁定[对应提交的 GitHub tarball](https://codeload.github.com/MakcRe/KuGouMusicApi/tar.gz/fe1838ae58ea69f84941ffa8758a34a9121bb2ea)。所审查酷狗 tarball 的 SHA-256 为 `de6cad95059ad26b979ef5d21994b5277b275b75ea584786e45750e7a01c8a11`。三个包的 tarball integrity 值均写入锁文件；源码下 `native/online_music/licenses/` 保留三份原始 MIT 文本。

运行时准备命令：

```powershell
.\native\online_music\prepare-runtime.ps1
```

可以通过 `-NodePath`、`-NpmPath` 指定现有可执行文件，通过 `-NodeLicensePath` 指定对应 Node 发行版的原始 LICENSE；默认从 Node 精确版本的官方源码标签取得 LICENSE。脚本以 `npm ci --ignore-scripts --omit=dev --no-audit --no-fund` 安装固定锁文件，复制 Node，再运行 `verify-runtime.cjs`。安装输出只在 ignored `build/online-music`，不进入 Git。

本次实际准备使用 Node `v24.19.0`，安装 397 个包，保留 378 份 LICENSE、NOTICE 或 ATTRIBUTION 文件（包含 Node 完整 LICENSE 及其自带第三方声明）。`SOURCE.json` 逐包记录版本、来源、integrity、许可证元数据和保留文件哈希，同时记录 Node 可执行文件、Node LICENSE 与锁文件的 SHA-256。部分传递依赖使用其他许可证，因此不能把“直接适配器为 MIT”描述成“全部依赖均为 MIT”。缺少独立声明文件的包仍保留其原始安装目录和元数据。

此次锁文件 SHA-256：`154a324536ddee3c0368737102550107f689a04c76e2cff8c0c6848d37034ff7`。Node 可执行文件 SHA-256：`3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237`；Node LICENSE SHA-256：`148eacf7863ef4329224a29398623077200a27194aa075569faf4a0a85566ca5`。这些是本机准备结果，不是对任意同版本 Node 二进制的全局声明。

`GluSpot.spec` 总是收纳桥接代码、包清单、锁文件和直接包许可证。存在已准备的 Node 与 `node_modules` 时，还收纳 Node、全部安装包、运行时 `SOURCE.json` 和收集的声明；缺少 SOURCE 或 Node LICENSE 时拒绝打包该运行时。本次工作区缺少既有 native AirPlay 打包前置产物，没有生成新 EXE，也没有声称完成 frozen 验收。

## 服务接口与平台契约

`api/online_music.py` 的 `OnlineMusicService` 提供不可变账户、扫码、歌曲、歌单、流和歌词快照。同步网络方法由现有后台任务调度；构造服务和默认账户快照不发起网络请求。Qt 播放与系统媒体选择继续经过 `api/providers.py`，不恢复旧 live-media 源树。

| 来源 | 已接入操作 | 固定接口与处理 |
| --- | --- | --- |
| 网易云 | 扫码、验证、歌单与分页、歌曲/歌单搜索、每日推荐、私人 FM、授权流、LRC/YRC/翻译/罗马音 | 包内 QR key/create、login_status、user_playlist、playlist_track_all、cloudsearch、recommend_songs、personal_fm、lyric_new/lyric；QR check 使用已核对的 request API 直接调用 `/api/login/qrcode/client/login`，绕过固定版本 QR module 的错误 catch；流使用固定包 `song_url` 模块，`br=128000`，对应 `/api/song/enhance/player/url`。 |
| QQ | 扫码、验证、歌单与分页、歌曲/歌单搜索、授权流 | 固定 `dist/src/services/auth/qrLogin.node.js` 的 `createNodeQrLoginService`；`getUserPlaylists` 返回 `v_playlist`，`getOwnedPlaylistSongs` 返回单个窗口，`getMusicPlay` 按 songmid 返回地址映射。搜索单独使用已核对的固定包 HTTP client 和实测 musicu Desktop 契约，详见下文。 |
| 酷狗 | 扫码、验证、歌单与分页、歌曲/歌单搜索、授权流 | 包内 `register_dev`、login_qr_key/create/check、user_detail、user_playlist、playlist_track_all、search、song_url；使用 `util/request.js` 的 `createRequest`，保留完整设备 cookie 与新账号 cookie。 |
| 汽水 | 明确不可用 | 没有已验证、可随 Qt 项目分发的扫码适配器。未复制参考项目的混淆安全运行时。 |

QQ owned 歌单接口每页最多 100 首，服务据此限制单页并保留后续分页标记。其回退接口只能读取前 1000 首，超出后明确返回不支持，不将截断声称为完整歌单。QQ 收藏歌单接受固定包核对过的 `dissid/dirId/dirid`；酷狗曲目端点需要 `global_collection_id`，优先保留此 ID，避免误用其他列表编号。

网易流仅调用当前账号可获得的标准音质地址；桥接 allowlist 没有解锁、跨源匹配或替代音源操作。返回 URL 为空时为不可播放；平台提供试听信息时保留其性质。原先绕过上游启动入口却直接请求 XEAPI v1，缺少该路径所需的密钥状态，导致真实授权流失败；现使用固定包已有的标准授权模块，不再依赖 XEAPI 初始化。网易旧版 FM 的 `duration` 与新版 `dt` 均按毫秒转换，QQ/酷狗秒字段保留。QQ/酷狗逐字歌词尚未接入。登录后的内容界面与用户确认范围参见[入口实施记录](owned-playback-entry.md)。

## 当前平台歌曲与歌单搜索

用户随后确认搜索当前平台的歌曲与歌单。服务复用现有 `CatalogPage`、`OnlineSong` 和 `OnlinePlaylist`，接口为 `search(source_id, query, kind="songs", limit=30, offset=0)`，其中 kind 只能为 `songs` 或 `playlists`。关键词去除前后空白，限制 1–200 个字符，拒绝控制字符；单页最多 100 项。搜索在后台执行，保留所选来源，不跨平台匹配。服务的账户校验与其他目录方法一致；汽水仍明确不可用。

| 来源 | 实际调用与返回 |
| --- | --- |
| 网易云 | `cloudsearch({keywords,type,limit,offset})`，歌曲 type=1、歌单 type=1000；读取 `result.songs/songCount` 或 `result.playlists/playlistCount`，使用原生 offset。 |
| QQ | 固定包 `dist/src/services/auth/httpClient.js` 的 `createAuthHttpClient().post()`，向 `https://u.y.qq.com/cgi-bin/musicu.fcg` 调用 `music.search.SearchCgiService.DoSearchForQQMusicDesktop`；歌曲 search_type=0、歌单=3，`num_per_page/page_num` 分页，返回 `request.data.body.song.list` 或 `songlist.list`，读取 `meta.sum/nextpage`。这是一段独立编写的请求适配，固定包旧 `getSearchByKey` 强制 t=0，不能用于歌单搜索。 |
| 酷狗 | `search({keywords,type,page,pagesize,cookie})`，歌曲 type=`song`、歌单=`special`，读取 `data.lists/total`。搜索歌单的 `gid` 可直接交给现有 `playlist_track_all`，已匿名实测；仅有 specialid 且缺 gid/global_collection_id 的搜索结果不会被错误当作可读取歌单。 |

QQ 搜索调用公开目录接口，不将账号 credential 拼入请求 URL；歌曲和歌单的后续授权流仍使用所选平台已保存账号。请求/平台 code 异常或搜索过滤标记为错误，不把失败转换成空结果。分页对 QQ 的稀疏页采用 `meta.nextpage` 指定的下一窗口，不以返回行数推算页码。QQ/酷狗任意非对齐 offset 先换算原生页码，再切去页内已消费部分；下一次 offset 位于该平台的下一窗口。服务保留原始分页游标，过滤坏条目不会导致重复第一页。

搜索歌曲保留专辑标题。QQ 有合法 album mid 时构造该平台固定封面地址；酷狗兼容实际字段 `FileHash/SongName/SingerName/AlbumName/Duration`，现有歌单曲目同时保留 `albuminfo/singerinfo`，封面模板中的 `{size}` 转为 500。

## 私密数据与扫码生命周期

子进程只通过私有 stdin/stdout JSON 行交换命令和响应，不启动 API HTTP 服务器。桥接 bypass 上游 server/main/.env 入口，抑制上游 console，丢弃 stderr；固定错误消息不输出上游原始文本。继承环境中过滤 COOKIE/TOKEN/SECRET 字段，子进程缓存位于 `%LOCALAPPDATA%/GluSpot/cache/online-music-bridge`，不在仓库。Node 15 秒请求截止后退出；Python 默认 18 秒等待，超时终止已放弃进程。关闭不等待长请求锁，由后台回收子进程。

QQ 使用自定义内存会话仓库和内存设备状态；其私有 envelope 必须包含完整 `{token, sessions}`，仅保存 opaque token 不足以重启恢复。网易和酷狗保存完整 cookie map。所有持久账户记录经当前 Windows 用户 DPAPI 加密，写到 `%LOCALAPPDATA%/GluSpot/accounts/{source}.dpapi`，不写普通设置，无明文回退。存储拒绝文件或祖先目录符号链接/目录连接，使用受限大小、源名称校验和原子替换。公开快照不含认证 cookie 或完整 QQ 会话；二维码图片、二维码 URL 和播放流 URL 不进入对象 repr。

每次二维码尝试建立独立的适配器状态，按来源和二维码 key 保存。已有账户的 catalog/status 请求即使在扫码之后返回，也只能更新普通账户状态，无法向本次二维码导入旧凭据。QQ 每次创建新的 service/session resolver，避免固定包在仓库切换时重新合并旧会话。确认时网易必须取得本次新 `MUSIC_U`；QQ 必须取得与本次完整 session 对应的 token；酷狗必须取得非空新 token 和有效数字 userid，拒绝缺失及 `undefined/null`。

随后 Python 另外验证真实账户身份，只有确认、身份验证、DPAPI 保存均成功才返回 confirmed。刷新、切源、取消、退出和重新扫码均使用代次屏障，晚到结果不能保存账号。终态清理公开二维码数据；QQ 终态 `logout("")` 只清理二维码监听，不撤销已取得会话，此结论来自固定包源码。

`has_login_record` 仅判断本机受保护登录记录，和联网有效性分开；离线失败不会抹掉记录。DPAPI 保存/删除和短暂文件同步仍在服务锁内，GUI 的立即作废操作可能等待这段本地存储；没有宣称任意慢磁盘下取消或退出严格无阻塞。

## 验证与未验证范围

```powershell
python -m unittest tests.test_online_music tests.test_online_music_bridge tests.test_online_music_credentials -v
node native/online_music/verify-runtime.cjs build/online-music
node --check native/online_music/bridge.cjs
```

加入搜索后的最终专测 52 项全部通过，无跳过：服务 20 项、实际 Node 子进程 15 项、凭据存储 17 项。覆盖真实 Windows DPAPI 的合成密钥与篡改、目录重定向拒绝、迟到确认取消、完整会话恢复、分页、不可播放状态、YRC 保留、错误脱敏、超时退出和关闭。实际桥接测试使用固定 API 契约的本地 module fixtures，额外覆盖旧账号请求在扫码后返回、三源确认缺新凭据的拒绝、搜索类型、原生分页转换、QQ 下一窗口、酷狗 gid 和搜索过滤失败。QQ 仓库恢复及二维码清理行为另由固定包源码独立复核。

最终真实匿名联网验证结果只记录状态、耗时、PNG 长度和凭据文件数，不记录 key、二维码或 cookie：

| 来源 | 创建 | PNG 字节数 | 创建耗时 | 一次未扫描轮询 |
| --- | --- | ---: | ---: | --- |
| 网易云 | waiting | 1975 | 12.484 s | waiting，0.110 s |
| 酷狗 | waiting | 2342 | 0.578 s | waiting，0.016 s |
| QQ | waiting | 429 | 0.781 s | waiting，0.000 s（四舍五入） |

临时账户目录生成 0 个凭据文件，未扫描、未完成账号登录、未播放账号歌曲。机器本地证据在 ignored `logs/online-music-anonymous-qr-result.json`、`logs/online-music-focused-verified.log`、`logs/online-music-prepare-runtime.log`；运行时来源在 ignored `build/online-music/SOURCE.json`。Python 编译、Node 语法和所涉 diff whitespace 检查通过。

搜索另用临时环境、上游原始 module 与固定 QQ HTTP client 作匿名核对，未导入已有账号。网易歌曲/歌单在 offset=2、limit=2 均返回两项；QQ 独立歌曲探测第 1、2 页各返回 20 项，歌单第 1、2 页返回 20、19 项并分别声明下一页 2、3；酷狗匿名 special 第 2 页返回两项及 total=480，返回的 gid 读取曲目成功。酷狗匿名歌曲实际返回 error_code=152，文档要求账号；部分 QQ 合并探测实际返回 code=2001/is_filter=-12，已保留失败证据，没有宣称稳定成功。脱敏记录在 ignored `logs/online-music-anonymous-search-result.json`，仅保留字段名、数量、分页与错误码，不含账号、曲目流地址或会话。服务端登录后的完整搜索流程仍由 fixtures 覆盖，真实用户账户验证待进行。

随后修复的服务、桥接和凭据专测 55 项全部通过，新增标准流模块契约、原有账号 cookie 传递和 FM 毫秒时长回归。已有网易云账户的只读验证返回每日推荐 30 首、FM 3 首，并验证非空歌单的分页。两类首曲均取得非试听授权流，Qt 实际进入 PlayingState，时钟分别推进至 1202/1228 ms，歌词分别解析 48/50 行，无 Qt 错误；仅测试播放器音量为零，加密账户文件字节与修改时间前后不变。证据在 ignored `.runtime/online-playback-readonly-probe.json` 和 `.runtime/online-playback-qt-readonly-probe.json`，不保留账号身份、cookie 或签名 URL。完整会话重启、所有目录歌曲、长时播放与实际扬声器听感未由这次短测覆盖，详见[修复验收](library-playback-repair.md)。

酷狗文档没有 user_detail 成功 profile 的完整 schema，当前按成功状态、非空 data 和账号 ID 验证，只有合成响应覆盖。三源匿名成功不代表会员、版权、地区或未来 API 可用性保证，也不构成性能或长时歌词同步的实测结论。项目全量回归结果由 README 与本次提交的交付记录统一报告。
