# Apple Music 伴随程序

这篇说明 GluSpot 怎么接 Apple Music，以及网易云歌单和 Apple Music 歌单怎么对。写于 2026-11-10。

## 用的是哪种接法

Apple Music 的声音不从 GluSpot 自己的播放器里出。Windows 11 上会启动一个很小的伴随程序，里面用 WebView2 打开 `https://music.apple.com/`。登录、会员和版权保护都留在 Apple 自己的网页里。GluSpot 只通过一行一个 JSON 的标准输入、标准输出，让那个网页做这几件事：

- 打开登录窗口、读是否已经登录、退出
- 读资料库歌单和歌。网页上「我的资料库」来自根文件夹 `p.playlistsroot` 的子项，歌名经常在 `resources` 里，不在每一行的 `attributes` 上。歌曲封面用 `attributes.artwork` 的 `{w}` `{h}` `{f}` 模板换成一张 https 图。读资料库歌曲时带 `include=catalog`。空的 artwork 对象不会挡住曲库封面。没有 `playParams.catalogId` 时用 catalog 关系；关系里也没有，才再按编号向曲库要一次
- 按歌名加歌手搜索曲库
- 读已登录曲库歌曲的逐字或逐行歌词。先请求 syllable-lyrics，没有再请求 lyrics，正文是 TTML。这条失败才退回按歌名搜索
- 播放、暂停、继续、跳到某个时间
- 新建资料库歌单、往歌单里加歌

正在播放的歌名、歌手、专辑、时长和封面交给现有的岛。歌词先用这条已登录的 MusicKit 读取曲库 TTML，转成现有的逐字或逐行模型；这一步没有结果时，再用项目里已经有的「歌名 + 歌手」查找（网易云、QQ、LRCLIB 那条链）。

伴随程序的源码在 `native/apple_music_companion/`。Python 这边的协议客户端是 `api/apple_music_companion.py`，只负责 Apple Music 的后端是 `api/apple_music_backend.py`。网易云、QQ、酷狗仍走原来的 Node 桥，由 `api/online_music_router.py` 分开。

网页里的 MusicKit 还没准备好时，页面脚本按定时器重试，准备好了才用 `postMessage` 回一句 ready。账号和歌单的结果也是网页主动回传，宿主不去等一段脚本的返回值。除了等人登录的那一次，每次查询最多 20 秒；到点没有回传，伴随程序重载 `music.apple.com`。GluSpot 退出时关掉标准输入，伴随程序自己退出；1.5 秒还在，就强制结束。

Linux 上这个伴随程序不会启动。来源页会显示暂不支持。

## 参照了 folia-major 的哪些部分

参照的仓库是 [chthollyphile/folia-major](https://github.com/chthollyphile/folia-major)。查看时的提交是 `185e60feeeb2b942ac818833cdfb17f020ec86d8`（2026-11-09）。

它的许可证是 **GNU Affero General Public License v3.0（AGPL-3.0）**。GluSpot 这次没有把该仓库的代码抄进来，也没有把它的源文件放进本仓库。下面写的是行为上的取舍，不是把它的实现搬过来。

参照的部分：

- Apple Music 单独做一个伴随进程，不和网易云、QQ、酷狗的 Node 桥混在一起。
- Windows 上用 WebView2 打开 `music.apple.com`，让网页里的 MusicKit 自己处理登录和播放。这样不需要把开发者令牌交给 GluSpot。
- 父进程和伴随程序之间用标准输入、标准输出传一行一个 JSON。
- 登录态留在 WebView 自己的用户目录里，不把 cookie、music user token、开发者令牌交回 GluSpot。
- Linux 不在这条路径里。
- 完整曲库播放需要 Apple Music 订阅；没有订阅时可能只有试听。
- 中国区账号如果店面不一致，网页可能只给大约 30 秒试听。伴随程序在播放前用文档里的 `/v1/me/storefront` 把店面拨到账号自己的店面。若实际播出来的时长明显短于曲目时长，界面标成试听。
- 从不显示的 WebView 可能被 Chromium 推迟出声。folia 用私有字段把页面钉成可见。GluSpot 不读那个私有字段，改成播放时显示一个放到屏幕外的小窗口。

没有照搬的部分：

- 没有复制它的 JavaScript、C#、协议字段或 WebView2 版本号。本仓库的页面脚本按 Apple 文档里的 MusicKit JS 和 REST 路径另写。
- 没有复制它读取逐字歌词的代码。GluSpot 自己用已登录网页里的 MusicKit 请求曲库歌曲的 syllable-lyrics 或 lyrics，拿到 TTML 再转成现有歌词模型。这不是 Apple 公开给第三方应用的稳定接口。
- 没有复制它保存或回传网页令牌的做法。这里只在网页里把 music user token 做 SHA-256，取前 8 个字节的十六进制当作本地用户标记。这个标记不是登录凭证。
- 没有把它的许可证代码并进 GluSpot。

## 需要准备什么

- 系统：Windows 11 x64。Windows 10 和 Linux 不启动这个伴随程序。
- 运行库：WebView2 Evergreen，以及 .NET Framework 4.8 x64。用户不需要另装音乐插件。
- 构建：在 Windows 上执行 `dotnet build native/apple_music_companion/GluSpotAppleMusicCompanion.csproj -c Release`。WebView2 的 NuGet 版本是本仓库自己选的 `1.0.2903.40`，不是 folia 的版本。
- 订阅：读资料库和搜索可以在登录后尝试。把整首歌播完通常需要 Apple Music 订阅。没有订阅时，页面可能只给试听。
- 开发者令牌：这条路不需要付费的 Apple Developer Program，也不需要把开发者令牌写进仓库或配置。
- 伴随程序路径是写死的，不在几个目录里找。开发时只认 `native/apple_music_companion/bin/Release/net48/GluSpotAppleMusicCompanion.exe`。打包后只认和 `GluSpot.exe` 放在一起的 `GluSpotAppleMusicCompanion.exe`。

登录窗口由伴随程序弹出。来源页上 Apple Music 的状态灯和网易云、QQ、酷狗一样：未登录、已连接、出错。刷新按钮的文字是「重新打开登录窗口」。

## 播放控制

GluSpot 自己的顺序播放、随机、单曲循环不传给 Apple Music。伴随程序只接受播放、继续、暂停、跳转时间和读取状态。下一首和上一首仍由 GluSpot 的队列决定，然后让网页播放那一首的曲库编号。

官方 Apple Music 桌面程序的系统媒体控制没有另外接一条控制通道。岛上若看到来源名 Apple Music，只是系统会话显示名的别名。能控制播放的是这条伴随程序。

## 歌单同步

匹配在 `api/apple_music_catalog.py`。一首歌要同时满足三点才算匹配上：歌名接近、歌手接近、时长差在 3 秒和 2% 里较大的那个之内。对不上的不写入。缺时长、时长差在 15 秒内、歌名或歌手只对上一部分、或者有两首一样接近，都算不确定，默认不写入，除非预览里勾选了那一首。

导入前先出预览：匹配上的、没匹配上的、不确定的、两边不一致的。用户点确认之后才写入。结果里有成功几首、失败几首、失败原因。失败的不会自动重试。

现在窗口里只开一个方向：网易云歌单导入成 Apple Music 歌单。可以新建一个 Apple Music 歌单，也可以加进已有歌单。

反向同步的代码还在 `local/apple_music_sync_flow.py` 里，开关 `REVERSE_SYNC_ENABLED` 默认是关的。界面上没有「Apple Music → 网易云」这个按钮。等第一、二阶段在真机上跑稳了，再决定要不要把开关打开。打开之前，控制器也不会提交反向写入。

再次同步时，本机的 `apple-music-sync.json` 只记歌单编号和歌曲编号的对应关系，不记令牌。已经配对的歌不会再加一次。某一边少了歌，或者另一边多了歌，会列成冲突。没有删除动作。`refuse_delete` 会拒绝删除、清空这类操作。

来源页只有 Apple Music 已连接时才多一个「歌单同步」按钮。放不下时会藏起来，不挪动原来的「使用此平台」和「退出登录」。歌单下拉框用来源页同一种扁平玻璃：半透明、一圈细亮边、没有高光。原来的按钮、玻璃分组和播放模式没有改。

## 日志

这几步会写一条 `GluSpot.apple` 日志：登录、读歌单、封面、歌词、搜索、出声、切歌、写入歌单。封面日志带 `reason`，`count` 是已经拿到封面地址的歌曲数。歌词在伴随程序把这首歌播出去之后才请求，避免和播放抢同一次 20 秒重载。播放失败也会补一次歌词请求。日志里能看到 `stage=request`，然后是 `stage=resolve`、`stage=ttml` 或 `stage=title-search`。日志里只有步骤名、结果和数量。歌名、歌词正文、封面地址、令牌、密码、cookie 和用户标记都不会写进去。不需要设置 `GLUSPOT_LYRIC_DEBUG`。

用 `launch.bat` 打开时，日志在程序目录的 `logs\GluSpot-*.log`。直接跑程序时，同样的行出现在标准错误输出里。

## 真机验收清单

先在放着源码的目录打开 PowerShell，跑：

`powershell -NoProfile -ExecutionPolicy Bypass -File native/apple_music_companion/accept.ps1`

脚本是带 BOM 的 UTF-8，正文只有英文，避免 Windows PowerShell 5.1 把中文读乱。通过时每行是 `PASS step=... detail=...`。失败时两行：`FAIL step=...` 和 `reason=...`。记录在 `%LOCALAPPDATA%\GluSpot\apple-music-accept.log`。

1. `step=webview2`：检查有没有 WebView2 Evergreen。没有就停，reason 里写要安装 Microsoft Edge WebView2 Runtime。
2. `step=dotnet-framework` 和 `step=dotnet`：检查 .NET Framework 4.8 和 `dotnet`。然后 `step=build` 编译伴随程序。编译失败就停，reason 里有退出码。工程固定编成 64 位，并关掉 Prefer32Bit，避免 32 位进程去加载 64 位的 WebView2Loader。
3. `step=start`：启动编好的伴随程序。进程一进入 Main 就先建 `%LOCALAPPDATA%\GluSpot`，并往 `apple-music-companion.log` 写一行 `started`，然后才去做 WebView2。脚本要在这份日志里看到 `started`。启动时窗口先放在屏幕里，等页面导航完成并且页面脚本发出 ready 之后再藏起来。这之前收到的请求先排队，不往还没装上监听的页面里丢。初始化失败会把异常类型和消息写到标准输出，再写一份到同一份日志，然后进程退出，不会空挂着。导航失败码、WebView2 进程失败、页面脚本异常和 console error 也进这份日志。标准错误会被 GluSpot 收进 `GluSpot.apple`。消息里的令牌、cookie、密码会被抹掉。脚本的 reason 会带上 `error`、`exception`、`message`。能保持运行的话，脚本会把它关掉。播放时才把已经建好的窗口挪到屏幕外，避免网页不出声。脚本还会打一行 `NOTE step=login`，说明登录等待不能用普通请求超时，也不能把伴随进程停掉。

上一轮真机没有生成 `apple-music-companion.log`，是因为当时只有初始化失败才写这个文件。那次进程已经启动成功，登录请求从页面得到 `runtime` 之后伴随进程被停掉并重新拉起，成功路径上没有任何一行日志，所以文件根本不会出现。不是目录没建到一半，也不是路径写到了别处。

脚本通过之后，用 `launch.bat` 打开 GluSpot，再按下面的顺序点。每一步旁边写了日志里应该出现的字。到 `logs` 里打开最新的 `GluSpot-*.log`，搜索 `GluSpot.apple`。伴随程序自己的日志在 `%LOCALAPPDATA%\GluSpot\apple-music-companion.log`。

4. 打开来源页。GluSpot 会发一次 `account`，按 WebView 里的真实登录态刷新状态灯。已经登录应是已连接，没登录应是未连接。不能停在「暂时无法连接」。伴随日志的第一行附近有 `started`。
5. 选 Apple Music。应弹出登录窗口，并且在人登录完或关掉窗口之前一直留着。`GluSpot.apple` 里有 `operation=login stage=begin result=ok`。这一步不能因为普通请求超时把伴随进程停掉；任务管理器里这个进程还在。
6. 在窗口里登录，或直接关掉窗口。关掉时 GluSpot 再发一次 `account` 刷新状态灯。登录成功后灯变成已连接，日志有 `operation=login stage=confirmed result=ok`。只是关掉窗口、人还没登录时，灯是未连接，伴随进程仍在。
7. 完全退出 GluSpot 再打开，回到来源页。不等再次点登录，也会发一次 `account`，状态灯和当前 WebView 登录态一致。
8. 点「退出登录」，再重新登录一次。退出时日志有 `operation=login stage=sign_out result=ok`。
9. 登录状态下点「歌单同步」。窗口里只有「网易云 → Apple Music」，没有反向按钮。两个歌单下拉框是半透明的，边是细的亮线，没有高光。
10. 打开「要搬走的歌单」。里面是自己的网易云歌单。旁边的 Apple Music 下拉框里是自己的资料库歌单，文件夹里的歌单也在，文件夹本身不作为可选歌单。日志有 `operation=playlists stage=list result=ok`，后面的数量大于 0。下拉框仍是半透明、细亮边、没有高光。
11. 选一个歌单，再选要放进去的 Apple Music 歌单，或选新建并写一个名字。点「先看预览」。
12. 预览里能看出哪些匹配上、哪些没匹配、哪些不确定。对不上的不会被写进去。搜索时日志有 `operation=search result=ok`，后面是数量，没有歌名。
13. 点「确认写入」。窗口写出成功几首、失败几首和失败原因。日志有 `operation=write`。打开 Apple Music，新歌在目标歌单里，原来的歌还在。
14. 在 GluSpot 里播放一首 Apple Music 的歌。能听到声音，或只有大约 30 秒试听。封面不再是空灰块，专辑名和时长出现在歌曲信息里。日志有 `operation=play action=play result=ok`，以及 `operation=artwork stage=tracks result=ok count=` 后面大于 0，并带 `reason`。封面仍然空时，`reason` 会写明是 `no-catalog-id`、`catalog-empty`、`catalog-no-art` 还是 `request-failed`。
14b. 播放返回之后应出现 `operation=lyrics stage=request result=begin`，伴随日志里有 `op=song_lyrics`。成功时还有 `stage=ttml result=ok`。没有 catalog id 时先看到 `stage=resolve`。网页没有 TTML 时再看到 `stage=title-search`。有逐字时间轴时，字会跟着时间走。
14c. 动态封面如果没拿到，日志是 `motion_cover provider=apple result=miss` 或 `result=cancelled error=none`。不应再出现 `error=AttributeError`。
15. 点下一首。切到队列里的下一首，随机和循环保持原样。日志先有 `operation=skip action=next result=ok`，接着再有一条 `operation=play`。
16. 点上一首。日志有 `operation=skip action=previous result=ok`。
17. 在 `GluSpot-*.log` 和 `apple-music-companion.log` 里搜索 `token`、`cookie`、`password`。这些字不应该出现。登录凭证仍留在本机的 WebView 目录里，不在日志里。

第 4 步到第 17 步需要真实账号。脚本本身也要在 Windows 11 上跑过才算验收通过。

## 长期风险

- `music.apple.com` 的页面结构、MusicKit JS 的方法名或登录跳转一变，伴随程序就会失效。这是未公开集成，不是 Apple 给第三方客户端的稳定合约。
- Apple 可以限制网页里的自动播放、店面或资料库写入。订阅、地区和账号状态都会改变结果。
- 中国区店面即使先对齐，仍可能只剩试听。这不是把播放模式改掉就能解决的。
- WebView2 和 .NET Framework 4.8 要由 Windows 机器提供。版本过旧时伴随程序起不来。
- 公开的 Apple Music API 若以后要改成正式第三方应用，才需要付费开发者账号和开发者令牌。那是以后的选择，不是这条伴随程序的前提。
- 逐字歌词走的是网页里已登录 MusicKit 的 syllable-lyrics / lyrics，不是公开的第三方 API。Apple 改掉这两个路径后，会退回按歌名搜索，并在日志里写明没命中的那一步。
- 网易云新建歌单依赖现有 Node 桥里的接口。接口名称在桥里，真账号是否接受还没验证。

## 这次没有在真机上验证的事

下面这些需要 Windows 11、WebView2、编好的伴随程序，以及真实的 Apple Music 和网易云账号。本环境是 Linux，没有做这些验证：

- 弹出 Apple 登录窗口并完成登录、退出。登录等待不再用 20 秒请求超时，也不该再把伴随进程停掉
- 网页版已经登录时，来源页状态灯是否变成已连接，而不是停在「暂时无法连接」
- 关掉登录窗口，以及退出后重新打开 GluSpot，状态灯是否按一次 `account` 刷新
- `%LOCALAPPDATA%\GluSpot\apple-music-companion.log` 是否在进程刚启动时就有 `started`。上一轮没有这个文件，是因为当时只有初始化失败才写它
- 登录之后，歌单下拉框和资料库页是否出现真实的 Apple Music 歌单（含文件夹里的歌单）。本环境只用模拟的歌单接口返回验证了解析，没有真实账号
- MusicKit 还没就绪时的定时重试、20 秒没回传就重载网页、以及退出 GluSpot 后伴随程序在 1.5 秒内自己退出。这些要在 Windows 上看任务管理器和 `apple-music-companion.log` 里的 `query-timeout reload`、`parent-closed`
- WinExe 下重定向的标准输入是否被读到。本环境没有 Windows 窗体，不能把隐藏窗口和 WinExe 管道一起跑起来。Linux 上只验证了：子进程读到管道并按编号回包，以及不回包时 Python 记下 `operation=protocol stage=reply result=timeout`。Windows 上打开 `%LOCALAPPDATA%\GluSpot\apple-music-companion.log`，一次歌单查询应该依次出现 `trace stage=stdin`、`trace stage=dispatch`、`trace stage=deliver`、`trace stage=post`、`trace stage=stdout`。若页面挂住，20 秒内必须有 `query-timeout reload`，并且 Python 的 `GluSpot.apple` 里有同一请求编号的 `stage=reply result=timeout`。日志里只有编号、操作名、ok/error 和耗时，没有令牌
- 搜索真实曲库
- 网页里真正出声、暂停、切歌、跳转
- 订阅不足时是否只剩试听，以及中国区店面是否仍是试听
- 播放资料库歌曲时，封面是否不再是空灰块，专辑名和时长是否出现。歌词是否来自 syllable-lyrics 或 lyrics 的 TTML；失败时 `GluSpot.apple` 是否写明 `stage=ttml` 或 `stage=title-search` 的 `reason`。动态封面失败时是否不再是 `error=AttributeError`
- 把歌写入真实的 Apple Music 歌单或网易云歌单
- 修过启动之后，伴随程序能否在这台已经装了 WebView2 157.0.4341.42 的电脑上初始化成功。更早一轮编译通过，但启动只返回 `runtime` 并且进程不退出。后来改了窗口位置、64 位加载和失败退出。这一轮又改了登录等待、页面就绪和启动日志，还没有在那台电脑上重跑
- 验收脚本在 Windows PowerShell 5.1 里是否不再乱码。脚本已改成带 BOM 的英文，本环境不能替 Windows PowerShell 5.1 执行它
- 上面「真机验收清单」第 4 步到第 17 步
