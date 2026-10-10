# 音乐库排版与授权播放修复

2026-10-10，`codex/owned-playback-sources`，源码版本保持 `6.412`。用户反馈每日推荐、私人漫游只有封面、没有播放和歌词，列表缺少返回操作，界面排版不齐，并明确此前图片为概念图，允许重新设计。

## 原因与修复

网易流请求原先直接使用 XEAPI v1，但桥接没有执行上游服务器入口的密钥初始化。真实账户下该路径失败，独立的封面请求仍可完成。同时音乐库在歌曲元数据与流任务刚排队时就进入播放页，实际失败消息留在已隐藏的列表中。

桥接现调用固定 `@neteasecloudmusicapienhanced/api@4.41.1` 的 `song_url` 模块，`br=128000`，使用原有账号 cookie 和标准授权接口。返回的试听、无权限和空 URL 状态继续保留，没有加入跨源匹配或解锁。网易旧版 FM `duration` 按毫秒转换；QQ/酷狗秒字段保持原契约。

`OwnedMusicPlayer` 只在 Qt 实际进入 PlayingState 后报告 playing/trial。同步或异步解码错误不能被随后排队的播放覆盖；新目录请求作废旧流的确认资格。错误只保留安全类别，界面不显示 Qt 原始错误文本或签名地址。音乐库必须同时确认当前请求就绪、playing/trial 和真实 `is_playing` 才交接到现有 V2 播放页；失败保留列表、释放控件并显示可重试原因。

每日推荐、私人漫游的封面在账号歌单显示后独立请求，使用实际返回歌曲的首个可用图片地址，不阻塞列表。过时来源结果不能修改新列表，后台封面更新不清空搜索。尚无图片时使用平台 SVG 与类别文字占位。搜索移除歌曲后的迟到图片会释放 pending 标记，但不显示或缓存未知条目；再次搜索同曲可以重新请求。

## 界面与返回行为

音乐库、平台入口和账号卡共用深灰分层、统一字体、圆角和间距。账号头像缩为 64px，平台工具成组；展开状态的播放模式控件固定右栏，收起状态在标题下方增大。长文本省略，搜索始终可用，底部错误与加载提示有独立空间，列表显示细滚动条。

返回、音乐库标题和关闭按钮在展开/收起状态始终可见；交互目标至少 44px。Back 与 Escape 先退出搜索，再返回来源选择。保留已登录账号和已经开始的播放；尚未完成的播放请求会停止并作废，迟到结果不能再打开歌词页，包括从搜索结果返回的情况。

保留上滚/顶部悬停展开、下滚收起、封面几何放大和三个播放模式的既有逻辑。首次选择页的动态背景与文字高光不变，二维码继续使用粗平台主题色边框。窗口尺寸受当前屏幕可用区域限制，短屏二维码适当缩小以留出消息与刷新按钮。

完整回归发现新增的原生 `QWidget.screen()` 查询会在 PySide6 中把共享屏幕 wrapper 绑定到临时窗口，关闭后影响其他窗口。尺寸查询改用与既有 `MusicSurfaceWindow` 相同的 `QGuiApplication.screenAt` / `primaryScreen` 方式，并验证销毁入口与列表窗口不会破坏共享屏幕对象。

## 验证证据

所有测试使用此 worktree 的 `.venv/Scripts/python.exe`（CPython 3.12.10 x64、PySide6 6.12.0）。可复现命令：

```powershell
.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest discover tests -v
.venv/Scripts/python.exe -X utf8 tools/smoke_music_library.py
.venv/Scripts/python.exe -X utf8 tools/smoke_owned_playback.py
.venv/Scripts/python.exe -X utf8 tools/preview_music_library.py
.venv/Scripts/python.exe -X utf8 tools/preview_playback_entry.py
build/online-music/node.exe --check native/online_music/bridge.cjs
```

- 服务、实际 Node 桥接 fixtures 与凭据存储 55 项专测通过；自主播放器 34 项专测通过；controller 36 项专测通过。
- 原生 UI 52 项专测在 DPR 1、1.5、2 均通过；随后增加搜索封面与两窗口销毁回归后，最终 UI/图标 55 项通过，controller/两入口/播放页的跨窗口组合 108 项通过。原生截图检查覆盖展开、收起、520px 窄窗、长名称、缺图、错误、账号卡和短屏登录。
- 真实本机 HTTP / Qt 自主解码 14 项检查通过（2.218 s）；音乐库与 V2 界面/实际 app 构造的 57 项联动检查通过。使用本地合成账号和静音音频，无全局音量操作。
- 已有网易云账号只读目录验证：每日推荐 30 首、FM 3 首，非空歌单分页可读取。两类首曲均返回标准授权非试听流与歌词。
- 两类首曲的真实封面地址均非空；复用列表实际的 `api.online_music_artwork.fetch_online_thumbnail` 下载与解码，均得到非空 256×256 缩略图，无账号文件修改。
- 两类首曲在生产 `OwnedMusicPlayer` / Qt 中实际静音解码，PlayingState 已观察，时钟推进至 1202/1228 ms，歌词解析 48/50 行，零 Qt 错误，合计 4.859 s。仅本次测试的 `QAudioOutput` 音量为零，加密账户文件内容和修改时间前后不变。
- 修复屏幕生命周期后的最终完整套件 3643 项全部通过（175.220 s，无跳过）；最终生产代码的 57 项原生联动再次通过，Python 编译、Node 语法与 Git 差异检查通过。此前完整套件定位到的屏幕对象失败已由两项销毁回归与跨窗口组合覆盖。

本机证据位于 ignored `logs/library-playback-routing-tests.log`、`logs/library-entry-ui-focused-final.log`、`logs/library-playback-ui-full-tests-final.log`、`.runtime/owned-playback-smoke/result.json`、`.runtime/music-library-smoke/result.json`、`.runtime/online-playback-readonly-probe.json`、`.runtime/online-playback-qt-readonly-probe.json`、`.runtime/online-recommendation-artwork-readonly-probe.json` 及两组 preview 目录。UI 截图明确使用合成 fixture，不含真实账号身份或二维码；真实账户验证不记录 cookie、身份或签名音频 URL。

## 生效与限制

正常退出并从本目录重启源码版后生效。版本与已有 EXE 均未改变，这次没有重新打包。provider 边界、V2 岛、系统音量、原 native helper 和 HomePod 输出逻辑保持原有架构。

真实短测验证授权、解码、时钟和歌词解析，不代表实际扬声器听感、每首歌曲权限、完整会话重启或在线长时性能。QQ/酷狗没有新增真实账号播放证据；汽水、QQ/酷狗逐字歌词仍是待办。后续已接入自主 PCM 设备路径并通过真实静音 Windows 渲染与时间线重建验证；物理多设备同步听感仍待验证，详见[后续修复](owned-playback-refinements.md)。
