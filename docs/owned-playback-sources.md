# 自主播放源研究与实施建议

日期：2026-10-10。基线为 GluSpot `6.412`；用户在对话中称为 `6.4.12`。版本和当前功能以 [README](../README.md) 为准。

本文是播放源分支的研究与方案记录。当前任务包括从该基线建立本地 Git 分支、研究两个参考项目，以及将审核后的项目内容同步到用户自己的 GitHub 私有仓库。本文不代表自主播放器、在线登录、歌单界面或直接 PCM 输入已经实现，也不代表已有新的播放性能或设备验证结果。

## 已确认需求与待确认事项

| 项目 | 当前依据 |
| --- | --- |
| 自主播放采用设置开关 | 用户已确认。自主模式默认采用在线歌单；具体控件名称和位置待确定。 |
| 默认方向为在线歌单播放 | 用户已确认。不能将开发用本地音频 fixture 改写为用户默认播放来源。 |
| 需要前置界面 | 用户已确认。界面承担歌单播放前的入口；具体页面结构、打开时机和交互仍待确定。 |
| 第一家平台 | 尚未确认。网易云是结合现有问题和两份参考的建议首选，不是已确定的平台。 |
| 关闭自主播放开关后的行为 | 建议继续使用现有外部播放器兼容模式；尚未获得用户对这一切换行为的明确确认。 |
| 歌单和账号入口 | 是否接受歌单链接、读取账号收藏、手动导入或其他方式，以及是否需要首次登录，均待确认。 |
| 保密范围 | 用户要求 GitHub 同步必须使用私人仓库，绝对不能公开。账号凭据、会话、运行数据和上游临时克隆不应进入 Git。 |

可以先完成接口核查、隔离测试和技术原型准备。依赖上述待确认事项的产品界面与平台接入，需要在具体方案确认后实施；不能凭参考项目替用户决定业务规则。

## 固定参考版本

本次仅阅读源码，没有运行上游脚本、安装上游依赖、登录平台或进行上游实际播放。两个参考项目均克隆到工作区外的临时目录。

| 项目 | 本次固定版本 | 许可证及维护状态 |
| --- | --- | --- |
| [Folia](https://github.com/chthollyphile/folia-major/tree/d824c0b854e54d5411cb072092999823e9bd7071) | 默认分支 `main`，`d824c0b854e54d5411cb072092999823e9bd7071`；包版本 `0.7.16` | [package.json](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/package.json#L11-L12) 和 [LICENSE](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/LICENSE) 标明 AGPL-3.0。 |
| [Mineradio-paused](https://github.com/XxHuberrr/Mineradio-paused/tree/d43de565acabfdc1a9c9820a27e81a98ccbebcef) | 默认分支 `main`，`d43de565acabfdc1a9c9820a27e81a98ccbebcef` | [package.json:7](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/package.json#L7) 标明 GPL-3.0-only；[README:3–7](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/README.md#L3-L7) 明确长期停更。 |

### Folia：源码能够证明的内容

| 观察 | 固定源码证据 | 对 GluSpot 的意义 |
| --- | --- | --- |
| 自主播放使用两个 HTML Audio deck 和 Web Audio 图 | [App.tsx:2352–2359](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/App.tsx#L2352-L2359)；[usePlaybackAudioBridge.ts:134–158](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/hooks/usePlaybackAudioBridge.ts#L134-L158) | 它直接播放取得的音频 URL，无需网易云桌面客户端。Electron 媒体管线并不能直接移植成当前 PySide6/native 音频后端。 |
| 网易 API 内嵌到桌面进程、监听 IPv4 回环；支持扫码会话 | [neteaseBackend.cjs:36–40](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/electron/neteaseBackend.cjs#L36-L40)、[171–176](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/electron/neteaseBackend.cjs#L171-L176)；[neteaseProvider.ts:402–429](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/services/onlineMusic/neteaseProvider.ts#L402-L429) | 可以参考平台适配与会话生命周期。接口、账号权益、地区和平台服务仍决定具体歌曲能否播放。 |
| 平台适配返回音频 URL、获取时间和音质 | [neteaseProvider.ts:312–323](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/services/onlineMusic/neteaseProvider.ts#L312-L323)；[netease.ts:751–760](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/services/netease.ts#L751-L760) | 应把音源解析与解码、渲染分开，处理 URL 过期和无权限结果，避免把失败误报为可播放。 |
| 歌词时钟读取正在播放的音频元素时间，另加歌词偏移 | [usePlaybackVisualizerBridge.ts:133–145](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/hooks/usePlaybackVisualizerBridge.ts#L133-L145) | 自主播放能够减少外部状态观察和推算造成的一类误差；音频元素时间仍不等于扬声器实测发声时间。 |
| 网易 YRC 词时间有实际解析，配套翻译采用 ytlrc | [neteaseProcessing.ts:44–52](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/utils/lyrics/neteaseProcessing.ts#L44-L52)、[68–89](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/utils/lyrics/neteaseProcessing.ts#L68-L89)；[parserCore.ts:507–546](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/utils/lyrics/parserCore.ts#L507-L546) | 应保留真实词起止时间和源身份，沿用 GluSpot 已有真假逐字判据；有 YRC 字段不代表每行都有可验证的逐字数据。 |
| 高频信号避开 React 状态；歌词解析与重型分析分别离开 UI 线程/进程 | [motionSignals.ts:5–15](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/stores/motionSignals.ts#L5-L15)；[workerClient.ts:22–38](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/utils/lyrics/workerClient.ts#L22-L38)；[analysis/host.cjs:85–101](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/electron/analysis/host.cjs#L85-L101) | 借鉴的是计算边界、稳定快照和任务调度，不是把整个渲染框架搬入当前 V2 岛面。 |
| 预取范围、缓存和 URL 寿命有上限；可选歌词升级提前释放播放 | [prefetchService.ts:28–40](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/services/prefetchService.ts#L28-L40)；[onlinePlayback.ts:169–177](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/services/onlinePlayback.ts#L169-L177) | 音频应独立于封面和可选歌词升级就绪。参考代码本身还存在等待首次歌词加载的放行条件，见 [usePlaybackAudioBridge.ts:268](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/hooks/usePlaybackAudioBridge.ts#L268)。 |

### Mineradio：源码能够证明的内容

| 观察 | 固定源码证据 | 对 GluSpot 的意义 |
| --- | --- | --- |
| 创建 HTML Audio，并接入 Web Audio | [08-audio-graph-controls.js:50](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/public/js/modules/05-playback/08-audio-graph-controls.js#L50)、[113–138](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/public/js/modules/05-playback/08-audio-graph-controls.js#L113-L138) | 同样证明独立客户端可直接播放音源，不证明当前 GluSpot 已拥有相同能力。 |
| 网易源先尝试 song_url_v1，再尝试旧接口，带用户 cookie 和请求时限 | [server.js:4065–4076](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/server.js#L4065-L4076) | 值得参考请求截止时间和明确失败处理；不能把上游回退路径理解为版权、付费或地区限制可忽略。 |
| 获取 lyric_new，按需补旧 lyric，并保留 YRC 与配套翻译；客户端解析真实词时间 | [server.js:6432–6451](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/server.js#L6432-L6451)；[00-lyrics-fetch-parse.js:599–632](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/public/js/modules/06-lyrics/00-lyrics-fetch-parse.js#L599-L632) | 音源与歌词可共享歌曲身份；具体歌词版本匹配和时间真实性仍需验证。 |
| 正常歌词时间读取 audio.currentTime，另处理拖动预览与恢复 | [14-stage-lyrics-rendering.js:1515–1520](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/public/js/modules/02-visual/14-stage-lyrics-rendering.js#L1515-L1520) | 可以借鉴播放时钟和交互预览的分离，不能用预览位置修改实际音频进度。 |
| 多输出复制成独立 Audio 元素，约每 2200 ms 检查一次，偏差超过 220 ms 才 Seek | [00-api-quality-output.js:819–868](https://github.com/XxHuberrr/Mineradio-paused/blob/d43de565acabfdc1a9c9820a27e81a98ccbebcef/public/js/modules/05-playback/00-api-quality-output.js#L819-L868) | 这不是共享 PCM、共享呈现 epoch 和独立设备时钟的同步实现，不能替换 GluSpot 已有 Windows/HomePod 输出引擎。 |

### 代码与内容授权

Folia 的 LICENSE 是 AGPL-3.0，但其 [README:210–214](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/README.md#L210-L214) 又写“仅供个人学习、技术交流与非营利测试”“请勿商业盈利”。AGPL 本身允许收费，文档表述存在冲突；直接复用前需要核清。Mineradio 的 GPL-3.0-only 也不是宽松许可。派生代码的分发可能产生源码提供和许可证义务；AGPL 还包括修改版的远程网络交互条款。私人仓库本身不能消除这些义务。

当前建议提炼架构后自行实现，不复制上游源文件、品牌、界面资源或音乐内容。若后续需要纳入任何上游代码或依赖，应单独记录精确来源、版本、许可证、修改和分发方式，核对是否符合本项目未来开源及付费发行计划。平台 API 可访问、账号付费和代码开源，都不自动授予音乐、歌词或封面的再分发权。

## GluSpot 现有边界与真实缺口

- [api/models.py:25](../api/models.py#L25)、[35](../api/models.py#L35)、[54](../api/models.py#L54) 已定义 `TimedLyricUnit`、`TimedLyricLine` 和 `TrackData`。自主模式应继续向现有岛面、窗口和全屏页提供这些数据，保留真实词时间、歌词来源、歌曲身份和原始位置观察身份；不要另造一套仅服务新界面的音乐模型。
- [local/app.py:282](../local/app.py#L282) 当前直接构造 `WindowsMediaProvider`。播放模式分派应经过 [api/providers.py](../api/providers.py)，保留真实与测试数据的既有边界，再使设置选择实际生效。启动入口仍是 `local/main.py`，当前 V2 岛面仍是唯一产品岛面。
- 现有同步组并不接受任意 PCM。[api/airplay_group_process.py:68](../api/airplay_group_process.py#L68) 要求真实 `MusicAudioSource` 和会话证据，并明确拒绝 GluSpot 自己的 PID；[start_group:130](../api/airplay_group_process.py#L130) 将外部音乐进程身份交给 helper。
- [GroupCapture:55](../native/airplay_sender/group_capture.h#L55) 的实现固定激活外部进程 loopback，见 [105](../native/airplay_sender/group_capture.h#L105)。[group_main.cpp:162](../native/airplay_sender/group_main.cpp#L162) 的 stdin 只解析停止及音量等控制文本，不是音频数据入口。不能把音频字节写入该通道，也不能删除外部来源身份校验来冒充接入完成。
- 自主解码产生的 PCM 要进入现有共享输出引擎，需要新增经过验证的直接 PCM 输入方式，再复用后续的条件处理、共享 PCM、呈现 epoch、AirPlay 发送与 Windows renderer。它是尚未实现的工程阶段，必须与当前捕获模式分开验证。自主模式能否取消“必须存在未选中捕获端点”的条件，也只能在该输入方式完成后讨论，不能先全局移除旧模式的保护。
- 所有新用户可见命名和运行时命名空间使用 GluSpot；旧路径仅作迁移别名。当前研究不变更版本、不恢复 Pill UI 或旧 live-media 源树，不改变既有输出音量、静音所有权和回滚规则。

## 建议的实现顺序

### 1. 先确定产品入口与第一家平台

以已确认的“设置开关 + 在线歌单默认 + 前置界面”为约束，制作具体交互草案，确认首个平台、账号/歌单获取方式和关闭后的行为。建议保留外部播放器模式，并复用当前媒体控制设置和 V2 音乐表面。页面结构、登录控件、歌单导入格式及自动播放策略不能在方案阶段视作已定。

技术评估需比较可供 Python/native 调用的播放后端是否能取得已解码 PCM、渲染进度与 Seek 完成证据，以及支持的格式、依赖大小、许可证和 Windows 打包条件。仅能返回一个播放器 position 的后端，不足以证明其可与现有多设备引擎共用 PCM。

### 2. 平台解析、播放和资源任务分离

在线歌单是目标默认入口。目录、歌单、音频地址、歌词、封面和登录应各自有清晰的失败状态；账号无权限、歌曲不可用、URL 过期、缓冲和实际播放不能混成一个“已播放”。音频就绪即可进入播放准备流程，不等待封面、翻译或可选逐字升级。

网络请求、解码、歌词解析和封面准备放在已有后台机制或专用工作线程/进程。UI 只读取不可变快照；paint 和动画 tick 不进行网络、磁盘或解码。每次换歌、模式切换及 Seek 更新任务代次；所有晚到结果校验模式、歌曲身份、源版本和代次，旧结果不能替换当前音频、歌词、封面或状态。取消请求之外，还要处理已无法中止但晚到的任务。

缓存按容量、数量和生命周期设上限；音频 URL 按上游有效期刷新，不能照搬参考项目固定 TTL 为平台保证。预取只占用明确的后台预算，前台换歌有优先级，退出或关闭开关后释放监听、工作任务、解码器和缓存引用。

### 3. 建立自主音频与歌词的共同时间轴

由真实音频处理和呈现证据生成播放位置，而非下载字节数、播放按钮状态或 UI 帧数。播放器时钟、歌词偏移和输出呈现补偿各有职责，歌词偏移只影响显示。保留真实词时间来源；行时间插值不能伪装为逐字采样。

暂停应冻结播放时间；恢复沿正确 epoch 继续。Seek 应由引擎确认，更新代次、清理旧解码/输出缓冲，再重建歌曲位置与呈现关系，避免旧 PCM 与新歌词交叉。发生欠载时明确区分“UI 想播放”与“实际有音频呈现”，不能只让歌词时钟继续跑。EOF 应区分网络/解码输入结束、缓冲耗尽和输出尾部完成，防止提前切歌、重复切歌或遗漏末尾歌词。

本地授权测试音频可用于验证这些底层行为，包括短文件、长文件、不同采样率、损坏内容、连续 Seek 和 EOF。它是可重复的工程 fixture，不取代用户要求的在线歌单默认播放方向。

### 4. 为现有输出引擎增加直接 PCM 输入

直接 PCM 路径需明确格式、声道、采样率、帧序号、epoch、输入寿命和控制协议。音频与控制通道应有清晰边界；缓冲、背压、格式错误、源退出和 helper 异常均要有上限和确定的清理路径，不能因 UI 停顿无限积压音频。

继续复用现有共享 PCM 和各设备时钟；按真实音频帧建立呈现时间。当前外部捕获模式保留进程身份校验、捕获范围、备用端点要求及拥有状态回滚。新增模式分别测试本机输出、单 AirPlay、全 Windows 组及混合组，物理声学偏差与传输正常分开记录。

### 5. 接入确认后的平台和前置界面

在平台选择与交互确认后，完成在线歌单读取、可播放地址获取、账号状态和现有播放命令分派。真实播放、暂停、上一首、下一首、Seek 和歌曲切换状态回到现有 provider 契约，岛面和大音乐页读取同一来源；不能仅在新界面中播放而继续展示外部播放器的时间轴。

验证未登录、会话过期、用户退出账号、不可播放歌曲和网络恢复等情况。遇到限制时显示真实原因；替换歌曲、跳过或回退到其他平台的策略需要产品确认，不默认加入隐式跨源替换。

### 6. 性能、硬件与打包验收

先跑确定性的源/时钟/队列/缓冲测试，再跑相关 UI 和全量测试。直接 PCM helper 如有变化，需要 native 全量测试；新增依赖进入 Windows 包时还需 frozen 验收、资源/许可证和打包来源检查。版本变更时同步 `local/branding.py`、`GluSpot.spec`、`packaging/windows/version_info.txt` 和 README；本研究阶段不升版本。

## 建议的验收证据

以下是后续实现需要形成的工程证据，尚不是已通过的验收结果。性能预算和真实网络/设备目标需根据确认后的实现记录并评审。

| 领域 | 需要记录的指标或通过条件 |
| --- | --- |
| 身份与生命周期 | 快速 A → B → A、关闭开关、退出和连续 Seek 后，旧代次结果写入当前状态次数为 0；关闭后无残留源监听、解码器或后台循环。 |
| 单一时间轴 | 确定性音频 fixture 下同时记录引擎位置、呈现帧位置和歌词位置；暂停冻结、确认 Seek 重建 epoch、自然播放不产生无依据跳行。导入行时间歌词时保留估算性质。 |
| EOF 与队列 | 自然结束只推进一次；输入结束后播放尾部不被提前截掉；下载失败、断流与真正 EOF 可区分。 |
| 背压与故障 | 记录队列容量、缓存字节、峰值占用、欠载/丢弃帧和取消耗时；慢源、慢消费者、helper 崩溃及父进程退出下仍有界，不死锁、不泄漏。 |
| 在线平台 | 按真实歌曲 ID、账号权益和所选音质验证授权 URL、到期刷新、歌词版本与配套翻译；只记录脱敏结果，不保存 cookie 或完整签名 URL。 |
| 模式切换 | 确认后的开关行为可重复；当前窗口、全屏、岛面使用同一来源；旧外部模式的进程捕获、设备范围及状态恢复回归通过。 |
| 渲染与响应 | 在相同歌曲、封面、分辨率、DPR、窗口模式与设备组合下，分别记录冷启动/切歌及稳态的 tick、paint、GUI 事件耗时 P50/P95/P99、最大值、CPU/RSS 和音频欠载；与 6.412 对照。 |
| 物理输出 | 分开记录连接、有效非零 PCM、持续传输、实际可听声音和测得的设备间声学偏差；短时播放不能代替长时漂移、睡眠/重连和重启恢复。 |
| 分发 | focused/full/native/frozen 检查按实际变更完成，依赖许可证与来源可追溯，包内无账号凭据、上游临时项目或运行缓存。 |

6.412 的 [现有冷封面实验](lyric-ripple-fluidity.md) 在物理 1920 × 1080、DPR 1、连续冷封面负载下，whole tick + paint + GUI events 的 P95 为 **35.18 ms**；这是合成媒体的 Qt 工作量，不是桌面显示 FPS。来源替换可减少外部观察和捕获负担，但不会自动降低字体、歌词布局、背景场或封面 paint 成本，不能预先承诺“卡顿锐减”。应分别衡量来源延迟、歌词同步误差与渲染耗时，明确剩余瓶颈。

## 账号、运行数据与私人仓库

在线歌单默认方向需要网络服务访问，私人 Git 仓库只保护源码仓库的可见性。请求中的账号信息会交给所选平台；是否增加其他服务端要在平台实现时明确，不能把私仓等同于整个运行过程离线或绝对保密。

账号凭据应由受控后台持有，使用系统保护机制持久化到 GluSpot 用户数据目录；保护机制不可用时采用明确的无持久化行为。UI 仅取得必要的会话状态。不得把真实 cookie、登录 token 或密码放入 query、renderer localStorage、settings、日志、测试 fixture 或 Git。平台给出的签名播放地址仅进入必要的受控播放请求，不进入 settings、日志、测试 fixture 或 Git。Folia 的 [providerStorage.ts:34–54](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/services/onlineMusic/providerStorage.ts#L34-L54) 与 [netease.ts:128–164](https://github.com/chthollyphile/folia-major/blob/d824c0b854e54d5411cb072092999823e9bd7071/src/services/netease.ts#L128-L164) 采用 localStorage 和 cookie query；这部分不作为 GluSpot 的实现模板。

如采用本地 API，只监听回环，并明确客户端身份、来源和端口访问边界。退出账号应取消相关任务、删除拥有的会话持久化数据，不影响手动歌词和其他用户文件。诊断保留错误类型、请求阶段与计数，移除凭据、签名和不必要的账号标识。

GitHub 同步前应核对目标 owner、仓库身份和 `PRIVATE` 可见性，再审核待提交内容及推送范围。已有忽略规则不是历史安全证明；分支继承的 Git 历史也需检查。仅推送已审核的必要 refs，不使用未经审核的 `--mirror`/`--all`，不将运行目录、缓存、日志、账号文件或临时参考克隆加入仓库。同步后再次读取服务端可见性；发现可见性或目标不符时停止上传并处理。

## 本次验证范围

本次主任务在 `C:\Users\USER\.codex\worktrees\2425\gpt-island` 中执行了两组不操作真实设备的 focused fixture 检查，使用已有解释器 `E:/gpt-island/.venv/Scripts/python.exe`：

```powershell
& 'E:/gpt-island/.venv/Scripts/python.exe' -X utf8 -X faulthandler -m unittest tests.test_media_position_pipeline tests.test_provider_lyric_anchor_guard tests.test_lyric_unit_timing tests.test_lyric_timeline tests.test_audio_route_provider tests.test_airplay_group_process -q
```

结果：99 tests，5.105 s，OK。

```powershell
& 'E:/gpt-island/.venv/Scripts/python.exe' -X utf8 -X faulthandler -m unittest tests.test_island_lyric_clock tests.test_music_playback_sync tests.test_audio_routes tests.test_windows_only_output_group -q
```

结果：196 tests，2.333 s，OK。合计 **295 项通过**。它们用于现有契约、歌词和输出边界的基线研究；没有自主音源或直接 PCM 实现，因此不能把这些通过结果写成新播放器验收。

未运行参考项目测试或真实播放，未登录在线音乐平台，也没有进行真实设备音量、路由、投放或声学验证。后续源码实现必须补足相应测试、完整测试和硬件证据。研究性判断与用户已经确定的产品要求在本文中分开记录。
