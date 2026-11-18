# GluSpot 6.52 资源占用分析与优化方案

日期：2026-11-18。对象：GitHub 发布 `v6.52`（文件版本 6.520.0.0；代码与 `release/6.52-public` 一致，本地检出 `6e6ff10` 与其只差 5 个 `_demos` 封面文件）。

本文只做分析和计划，**没有改运行时代码**。所有数字都是本机一次实测，不是多次统计；结论里会写清楚哪些是实测、哪些是读代码推断。

## 1. 结论摘要

| 场景（实测） | 进程 CPU（一个核） | 主线程 | 私有内存 | 缺页/秒 |
| --- | ---: | ---: | ---: | ---: |
| 6.52 便携版刚启动，只开「音乐来源」窗口，岛在 Finder 栏模式 | 4.7 % – 10.8 % | 2–7 % | 662–669 MB | 770–970 |
| 6.52 安装版，播放页窗口化（960×540）正在放歌，岛 2560×488 | **54.9 % – 63.5 %** | **40–48 %** | 860–874 MB | 1 300–4 700 |
| 6.51 安装版运行 2 h 45 min，播放页窗口化 + 设置窗口打开 | 21.1 %（全程平均 21.6 %） | 8.8 % | **1 315 MB**（峰值工作集 1 125 MB） | 2 700–3 100 |

GPU：性能计数器里 GluSpot **没有任何 GPU 引擎实例**，所有绘制都是 CPU 光栅（Qt raster），GPU 一点没用上。

**内存是最严重的问题**（第 3.4 节、第 4.0 节），已查到根因并在独立进程里验证过修法：

- **音频解码器泄漏**：`local/sea_lyric_background.py` 的 `_DecodeThread` 每解码一首歌，整首歌的解码数据（3 分钟 48 kHz 立体声约 140 MB）就**永久留在内存里**。原因是 `QAudioDecoder` 从不销毁，它的信号连接一直攥着装满音频块的 Python 闭包。海平面背景、Apple Music 海平面来源和 6.52 新增的音轨比对都走这条路。文件还在下载时最多会重试解码 6 次，所以一首歌可能泄漏好几份。运行中的 6.52 在 15 分钟内从 1 027 MB 涨到 1 322 MB，新增的内存几乎全是塞满立体声 float 音频的堆段。
- **OpenBLAS 白占 350 MB 提交**：仅 `import numpy` 就为 12 个逻辑核各提交 32 MB 缓冲（一共 384 MB，一页没用）。设 `OPENBLAS_NUM_THREADS=1` 后只剩约 10 MB。
- 次要：同时保留 3 张 2560×2560 的封面位图（75 MB）；两个 `node.exe` 在线音乐桥（约 180 MB，在任务管理器里算在 GluSpot 名下）。

CPU 方面四个最大的问题，按收益/风险排序：

1. **播放页每帧全在 CPU 上画**：播放时主线程 53 % 的忙碌时间在 `MusicSurfaceWindow.paintEvent`，其中歌词列 25 %、背景 8 %；另有 27 % 是 Qt 原生光栅/合成。16 ms 定时器只要窗口可见就一直跑。
2. **封面取色没有缓存**：`WindowsMediaProvider._colors_from_art` 每次媒体刷新（0.55 s，快速跟踪时 0.12 s）都把整张封面重新解码、缩放，再用 Python 逐像素循环 3 136 次。空闲时它占媒体线程忙碌时间的 **67–86 %**。
3. **MyDockFinder 轮询顺带刷新整个设置窗口**：每 2.5 s 一次的 `_poll_mydockfinder_presence` 做全系统进程快照、Finder 栏窗口快照和取色，然后**无论设置窗口是否可见**都调用 `settings_window.sync_from_widget()`，其中对下拉框 `setStyleSheet()`（样式表重解析 + 重新 polish）。空闲时占主线程忙碌时间的 **38–49 %**。
4. 缺页每秒数千次：每帧都在分配新的图像内存（见 P1-1 第 4 步）。

## 2. 怎么测的

- 环境：Windows 11 Pro 26300，2560×1440 主屏，MyDockFinder（Dock_64）在运行，网易云音乐在运行。
- CPU：PowerShell 对进程取 `CPU` 累计秒数差 ÷ 墙钟秒数 × 100（一个逻辑核 = 100 %），并逐线程取 `TotalProcessorTime` 差。
- 内存/缺页：`WorkingSet64`、`PrivateMemorySize64`、`\Process(GluSpot)\Page Faults/sec`。
- GPU：`GPU Engine` / `GPU Process Memory` 计数器按 `pid_<PID>_` 过滤。
- 函数级热点：py-spy 0.4.3，`--nonblocking --threads -r 100`，每次 15–30 s。统计口径是 Python 叶子帧和包含帧在“忙碌样本”里的占比。
- 脚本留在本次会话临时目录（`measure.ps1`、`gpu.ps1`、`agg.py`、`agg2.py`），需要时可以收进 `tools/`。

**注意**：有一次用了 `py-spy --native`（阻塞模式），它反复挂起进程，导致 GluSpot 被 Windows 判定“停止响应”并关闭（事件日志 10:23:39 AppHang），`_internal/logs/freeze-dump.log` 里 10:23 的几条“main thread stalled”也是它造成的。这些不计入结论。10:21:57 启动时那条 3.19 s 卡顿（停在 `music_resize_hit_test.py:191 nativeEvent`）是真实的。

## 3. 热点明细

### 3.1 播放页正在播放（6.52 安装版，25 s，1 105 样本）

线程：主线程 88 %，`GluSpotCaptureUIA` 8 %，音频路由 1.5 %，媒体提供器 1.2 %。

主线程包含时间（忙碌样本占比）：

| 位置 | 占比 |
| --- | ---: |
| `MusicSurfaceWindow.paintEvent` → `_paint_surface_frame` | 53 % |
| 　`_paint_lyric_column` / `_paint_live_lyric_column` | 25 % |
| 　`_paint_lyric_column_ink` | 17.5 % |
| 　`_paint_background`（含 `_sea_frame` 4.3 %） | 8 % |
| 　封面绘制 / 翻转 `paint_track_cover_flip` | 3–3.5 % |
| 　控件 `_paint_media_controls` | 2.9 % |
| Qt 原生（`LocalIslandApp.run` 自身，光栅 + 合成 + 分层窗口提交） | 27 % |
| `NarrowStudyWindow._on_animation_tick` / `tick` | 5 % |
| `OwnedAudioBridge._buffer` + `analyze_frequency_block`（主线程上做频谱） | 3 % + 2.5 % |
| `_poll_mydockfinder_presence` | 2.6 % |
| `IslandStackVisual._tick` | 2.5 % |
| `SeaAnalyzer.plan` / `pick_colors` | 2 % |
| `album_art_bytes_signature`（每次对整张封面字节做 blake2b） | 1.4 % |

### 3.2 空闲（6.52 便携版，30 s，非阻塞）

主线程忙碌样本：`_poll_mydockfinder_presence` 38 %（进程快照 10 %、Finder 栏窗口快照 10 %、Finder 取色 7 %、`sync_from_widget` 7 %、`style_combo_popup` 2–4 %）。媒体线程忙碌样本：`_colors_from_art` + `_add_color_cluster` + `_hue_distance_value` 合计 80 % 以上。

### 3.3 6.51 长时间运行（15 s）

主线程 63 %（原生绘制占其中大头），媒体线程 31 %，几乎全在 `_colors_from_art`。

### 3.4 内存构成（6.52 安装版，播放页开着放歌）

方法：用 `VirtualQueryEx` 遍历整个地址空间，按分配块归类；用 `QueryWorkingSetEx` 查每块有多少页真的驻留在物理内存里；用 `ReadProcessMemory` 抽样看块里装的是什么。脚本是会话临时目录里的 `vmwalk.ps1` 和 `peek.ps1`。

运行 15 分钟时私有提交 1 247 MB，任务管理器的“活动的专用工作集”约 660–775 MB：

| 类别 | 私有提交 | 驻留 | 内容 / 判断依据 |
| --- | ---: | ---: | --- |
| 约 16 MB 的堆段 × 约 38 | **599 MB** | 几乎全驻留 | 段头是 NT 堆签名 `EE FF EE FF`，里面是交错的立体声 float32 音频（正负交替的小数）。15 分钟内新增 16 段（252 MB），而且是一阶一阶跳上去的（10:43:40 一次就多了 38 MB 私有、117 MB 工作集） |
| 32 MB × 12，按 32 MB 对齐 | 384 MB | **0 MB** | OpenBLAS 每线程缓冲，12 = 逻辑核数。见下面的实验 |
| 2560×2560 ARGB 位图 × 3 | 75 MB | 75 MB | alpha 全是 0xFF。`owned_music_player.py:90` `ARTWORK_ORIGINAL_SIDE = 2560`，`island_stack_visual.py:590` `CURRENT_ALBUM_ART_MAX_SIDE = 2560` |
| 1 MB pymalloc arena | 62 MB | — | Python 小对象，正常 |
| 其他 | 126 MB | — | Qt、字体缓存、numpy 小数组等 |
| 子进程 `node.exe bridge.cjs` × 2 | 180 MB | — | 在线音乐桥，任务管理器把它们算进 GluSpot 一组 |

映射文件约 708 MB（主要是 7 个中日韩字体文件，约 155 MB）是共享的，不算私有。

#### 实验 A：OpenBLAS

用项目 `.venv`（numpy 2.5.3）在空白进程里测：

| | `import numpy` 后的私有提交 |
| --- | ---: |
| 默认 | 369.9 MB |
| `OPENBLAS_NUM_THREADS=1` | 16.4 MB |

#### 实验 B：海平面解码器

同一首 3 分 05 秒、48 kHz 立体声 mp3，同一进程里连续解码 3 次；每次结束后 `del` 并 `gc.collect()`，再读私有内存。脚本是 `leakexp.py`。

| 写法 | 第 1 次 | 第 2 次 | 第 3 次 | 峰值工作集 |
| --- | ---: | ---: | ---: | ---: |
| 现行（`bytes()` → `frombuffer().copy()`，块存进列表） | +168 MB | +308 MB | +450 MB | 474 MB |
| 去掉 `bytes()` 这一步 | +170 | +311 | +451 | 475 |
| 每块到达就降成 8 kHz 单声道 | +39 | +51 | +66 | 98 |
| 现行写法 + 结束时 `decoder.deleteLater()` + `processEvents()` | +27 | +29 | +27 | 197 |
| 现行写法 + 断开信号 + `shiboken6.delete(decoder)` | +27 | +26 | +27 | 198 |
| 同上，但像 `playlist_audio_compare.decode_file` 那样直接在调用线程 `run()` | +27 | +27 | +27 | 198 |
| 只 `read()` 不保存（对照组） | +25 | +25 | +25 | 59 |

+25～27 MB 是第一次初始化 Qt 多媒体 / FFmpeg 的固定开销，之后不再增长。

结论：泄漏的不是 numpy，是 `QAudioDecoder`。它在 `run()` 里没有父对象，`bufferReady` 连着的闭包引用了 `decoder` 本身和 `chunks` 列表。这个引用环有一段在 C++ 的信号连接里，Python 的 GC 看不见，于是解码器连同整首歌的音频永远不会释放。

另外试了让解码器直接输出 8 kHz 单声道（`setAudioFormat`）：在 Qt 6.11.2 的 FFmpeg 后端上**一块数据都不出**，最后靠 120 s 超时才退出。所以不能走这条捷径，降采样只能在 Python 里一块一块做。

触发频率：`SeaAnalyzer.plan`（`sea_lyric_background.py:817`）每首有本地音频文件的歌都会分析一次。文件还在下载时会“先解码已到的部分”，最多重试 6 次（`plan.attempts < 6`），每次都泄漏一份。6.52 的音轨比对（`local/playlist_audio_compare.py:75`）对每首候选都要解码网易云整首歌，走的是同一个类。

## 4. 优化方案

每项都写：问题、证据、改法、预期、风险、怎么验。优先级 M0 = 内存，最先做；P0 = 小改动大收益、几乎不碰视觉；P1 = 需要视觉确认；P2 = 结构性或需要先测。

### 4.0 内存（最先做）

#### M0-1 解码器用完就销毁

- 位置：`local/sea_lyric_background.py:98` `_DecodeThread.run`。三个调用方（`SeaAnalyzer` / `_AnalyseThread`、`local/apple_sea_source.py:133`、`local/playlist_audio_compare.py:75`）都会自动受益。
- 改法：用 `try/finally` 包住解码；在 `finally` 里断开 `bufferReady`、`finished`、`error` 三个信号，`shiboken6.delete(decoder)`，再把 `chunks` 清空。不能只靠 `deleteLater()`：`decode_file` 是在调用线程里直接跑 `run()` 的，那里不一定有事件循环去执行它。实验 B 证明 `shiboken6.delete` 在这两种跑法下都有效。
- 预期：每解码一首歌不再留下 70–140 MB。按 2 h 45 min 涨到 1.3 GB 的速度推算，长时间运行的增长基本消失。
- 风险：低。只影响解码器的生命周期，不改分析结果。
- 验证：新增回归测试，解码完以后 `shiboken6.isValid(decoder)` 为 False、闭包没有被引用；真机上连续切 10 首歌，私有内存的增长 < 50 MB。

#### M0-2 边解码边降成 8 kHz 单声道

- 改法：`ready()` 里拿到一块就立刻降混成单声道，再用每块自带的采样率降到 8 kHz（沿用 `_to_8k_mono` 现在的盒式滤波 + 插值，把状态带到下一块，避免块边界出毛刺），只保留 8 kHz 结果。同时去掉多余的 `bytes()` 拷贝，直接 `np.frombuffer(buffer.constData())`。
- 预期：每次解码的峰值工作集从约 200 MB 降到约 70 MB；整首歌只留下约 6 MB，而不是 70 MB 以上。
- 风险：中低。分析输入应该和现在逐样本几乎一致，必须用 `tests/` 里海平面分析的现有测试，加上两三首真歌对比新旧 `SeaPlan`（变色点、鼓点数、音节数）来确认。

#### M0-3 下载中的文件少解码几次

- 改法：文件大小还在增长时不解码，等它稳定 2 s 或者下载完成的信号到达再解码；`attempts < 6` 改成最多 2 次。
- 预期：一首歌最多完整解码 1–2 次。修好 M0-1 以后这一项省的主要是 CPU 和峰值内存。

#### M0-4 限制 OpenBLAS 线程

- 改法：在 `local/main.py` 最前面、任何 `import numpy` 之前设 `os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")`；打包版再加一个 PyInstaller 运行时钩子做同样的事（`GluSpot.spec`），确保比所有模块都早。GluSpot 里的矩阵运算都很小（比如 `rgb @ (3,)` 求亮度），用不到多线程 BLAS。
- 预期：私有提交减少约 350 MB。任务管理器“详细信息”页的“提交大小”会明显下降；“内存”列看的是驻留页，这一项不变，因为这些页本来就没有驻留。
- 风险：很低。验证：`tests/perf_music_surface.py` 的背景 / 玻璃基准 P95 不回退。

#### M1-1 封面位图按显示需要的尺寸解码

- 问题：每张封面都按 2560×2560 解码成 ARGB（25 MB 一张），同时留着 3 张。播放页窗口化时只显示约 300 px；全屏在 1440p 上也不超过 1440 px。
- 改法：压缩后的原始字节照常保留，供以后需要更大尺寸时重新解码；常驻的位图按“当前屏幕短边 × DPR”封顶，在这台机器上是 1440 px，一张约 8 MB。只有真正要显示更大尺寸时才按需重新解码。
- 预期：约省 50 MB 驻留。风险：中，封面清晰度要在全屏上看过才能定。

#### M1-2 在线音乐桥只留一个进程 / 空闲退出

- 现状：两个 `node.exe bridge.cjs`，各约 90 MB。从 `api/online_music_bridge.py` 看，登录用的后端和 `background_requests` 用的后台请求各起一个。
- 改法：先确认分成两个是不是为了防止前台请求被后台请求堵住；如果不是，就合并成一个。如果是，就让后台那个空闲 2 分钟后退出，下次用到时再拉起来。
- 预期：约省 90 MB。风险：中，涉及登录和后台请求的时序，需要先读完桥的生命周期代码再定。

#### M1-3 内存诊断日志

- 改法：加一个默认关闭的开关，每 5 分钟往日志写一行：私有提交、专用工作集、海平面计划条数、正在跑的解码线程数、封面位图条数、歌词栅格缓存条数。
- 用途：修完以后跑 4 小时验收，以后再有增长也能直接看到是哪一项。

#### 内存验收指标

| 场景 | 现在 | 目标 |
| --- | ---: | ---: |
| 刚启动的私有提交 | 约 660 MB | ≤ 330 MB |
| 播放页开着放歌，专用工作集 | 660–775 MB 并持续上涨 | ≤ 450 MB |
| 连续播放 4 小时（每首都换歌）的增长 | 每首 70–140 MB 起 | ≤ 50 MB |
| 音轨比对 60 首以后的增长 | 预计每首 70 MB 以上（未实测） | ≤ 50 MB |

### P0-1 封面取色加缓存

- 问题：`api/providers.py:4636` 在每次 `_read_visual_data` 都调用 `_colors_from_art`（`api/providers.py:5989`），后者 `QImage.fromData` 解码整张封面（常见 1 200 px），缩到 56×56，再逐像素建 `QColor`、`getHslF`、聚类。同一首歌的封面不变，结果也不变。
- 改法：
  1. 以封面字节摘要（已有的 `album_art_bytes_signature`）+ 两个 fallback 颜色为键，放一个小 LRU（16 项足够）。命中直接返回。
  2. 同一刷新里的 `hashlib.sha256(album_art)`（`providers.py` 4591、4616 行附近，用于日志去重）改为复用同一个摘要。
  3. 可选：未命中时把逐像素循环换成 numpy 向量化（已依赖 numpy），或直接用 `QImage.constBits()` 一次取出 56×56 数组。
- 预期：媒体线程空闲 CPU 下降 70–85 %（约 2–7 % 单核）；切歌时首次计算不变。
- 风险：低。颜色结果完全相同；只要键里包含 fallback 颜色就不会串色。
- 验证：已有取色单测（`tests/test_idle_neutral_palette.py`、`tests/test_island_palette_async.py`）全过；加一条“同一封面第二次不解码”的测试；重测空闲场景媒体线程 CPU。

### P0-2 MyDockFinder 轮询瘦身

- 问题：`local/app.py:776-825`。每 2.5 s：`CreateToolhelp32Snapshot` 全进程枚举 + 对匹配进程 `OpenProcess`/`QueryFullProcessImageNameW`；Dock 在运行时再做 Finder 栏窗口快照和屏幕取色（`read_live_finder_bars` → `sample_live_finder_bar`）；随后**无条件**调用 `settings_window.sync_from_widget()`（`local/settings_window.py:2985`），它会重设几十个控件、`_refresh_placement_mode_options()` 里对下拉框重新 `setStyleSheet()`（`settings_window.py:2353` → `local/ui_theme.py:266`），还会 `_update_previews()`。
- 改法：
  1. 设置窗口不可见时不调用 `sync_from_widget()`；在 `show_settings()` 时补一次全量同步。轮询结果只更新真正变化的两行状态文字（`mydockfinder_status_label`、`myfinder_status_label`）和放置模式选项。
  2. `style_combo_popup` 只在主题 token 变化时设置样式表：在 view 上记一个主题签名，相同就跳过。这一条对所有调用点都有效。
  3. 进程快照：上次结果与本次相同且 Dock 正在运行时，把 Finder 栏窗口快照/取色只留给当前确实在 Finder 模式的岛；非 Finder 模式只需要“是否在运行”。
  4. 可选（P1）：Dock 未运行时用 `SetWinEventHook(EVENT_OBJECT_CREATE/DESTROY)` 或 WMI 进程启动事件代替 2.5 s 轮询；运行后再按需轮询。
- 预期：空闲主线程 CPU 下降 35–45 %；同时消除轮询造成的 UI 小卡顿。
- 风险：中低。设置窗口打开时行为必须一模一样；要确认 `sync_from_widget` 里有没有别处依赖它被定期调用（先搜调用点再改）。
- 验证：`tests/` 里设置窗口、MyDockFinder、Finder 栏相关测试；手动：打开设置时插拔 Dock，状态文字与放置选项仍会及时更新。

### P0-3 绘制路径上的整封面哈希

- 问题：`local/album_art_pipeline.py:24` 的 `album_art_bytes_signature` 每次都对整张封面字节做 blake2b，播放页每帧会调到（主线程 1.4 %）。
- 改法：按 `id(bytes)` + 长度做一个只保留最近几项的弱缓存，或在 `TrackData` 生成时算好签名随数据传下去。
- 预期：主线程约 1–1.5 %。风险低。

### P1-1 播放页绘制降本（最大一块）

- 问题：`local/music_surface_window.py:216` `ANIMATION_FRAME_MS = 16`，`626` 行的 PreciseTimer 只要窗口可见就跑；播放并有逐字歌词时每 tick 都会 `update()` 歌词区域，并按 `AMBIENT_FRAME_SECONDS`（约 1/30 s）全页重画；海平面颜色变化期间 `backdrop_period = 0`，每 tick 全页重画（`music_surface_window.py` 2577 行附近）。全部在 CPU 上光栅化，而且每帧分配新图像（缺页数千/秒）。
- 改法（按收益排序，每步单独做、单独看效果）：
  1. **窗口被遮挡或失焦时降帧**：用 Windows `DwmGetWindowAttribute(DWMWA_CLOAKED)` + Qt `isExposed()`，被完全挡住时停表，失焦但可见时降到 30 fps。实测场景中播放页就在其他窗口后面。
  2. **分层缓存**：背景（海平面/album-flow）、封面、静态控件各自缓存成与 DPR 对齐的 QPixmap，只有歌词层每帧重画；背景只在其自己的 20/30 fps 节拍更新，合成时直接贴图。
  3. **歌词局部重画**：现在一旦 `companion_animating`、`layout_animating` 或弹簧在动，就退回整个 `_lyric_animation_rect`；把脏区收窄到当前行和正在动的行。
  4. **复用帧缓冲**：找出每帧新建 `QImage`/`QPixmap` 的地方（以 `_paint_lyric_column_ink`、`_draw_subpixel_lyric_image`、`_sea_frame` 为先），改为按尺寸复用；目标是播放时缺页 < 500/秒。
  5. **频谱分析移出主线程**：`OwnedAudioBridge._buffer` / `analyze_frequency_block` 在主线程上占 5 %，可以放到已有的音频分析线程，主线程只取结果。
  6. **空闲停表**：暂停 + 静态背景 + 没有过渡时停掉 16 ms 定时器，由状态变化事件重新唤醒（不是降帧，是不跑）。
- 预期：播放时进程 CPU 从约 55–63 % 降到 25–30 %（目标值，需实测确认）。
- 风险：中。任何改动都会影响歌词手感。按记忆里的约定，视觉/动效变化要在真实歌曲、带进度条的 demo 上由你亲眼确认后再进正式版。
- 验证：`tests/perf_music_surface.py` 的离屏基准 P95 不回退；真机：同一首歌、同一窗口大小下前后各测 30 s。

### P1-2 岛窗口 2560×488 的分层提交

- 问题：本次真实状态下岛窗口是 2560×488 的透明分层窗口（`WS_EX_LAYERED`）。分层窗口每次刷新要把整块 ARGB 缓冲交给 DWM，面积越大越贵，而岛实际可见的内容只占一小块。
- 改法：先确认这个尺寸来自哪种模式（全宽 Finder 栏/顶栏），把窗口几何收到内容外接矩形 + 动画余量；动画期间临时放大，结束后收回。
- 预期：Qt 原生合成部分下降（具体多少要测，目前只是推断）。
- 风险：中，涉及放置与动画边界，需要你看过再定。

### P1-3 UIA 采集线程

- 问题：播放时 `GluSpotCaptureUIA` 线程占约 2.3–3 % 单核（py-spy 样本 8 %）。UIA 树读取是 COM 跨进程调用。
- 改法：SMTC 或网易 CDP 已能提供进度时，UIA 只做 7.5 s 校准，不做持续读取；检查目前是否因为某个回退条件一直留在高频路径。
- 风险：中低；涉及进度准确性，需要用网易云真机核对歌词对齐。

### P1-4 内存

已经移到 4.0 节，根因见 3.4 节。

### P2-1 让 GPU 参与（结构性，需要你拍板）

- 现状：完全 CPU 光栅。播放页是 Python 驱动的 QPainter，每帧数百次 Python 调用。
- 方向：把背景（海平面、album-flow）这种全屏像素活改为 Qt Quick / QRhi 着色器，或至少用 `QOpenGLWidget` 做底层合成；歌词层保持 QPainter。
- 这是大改，影响打包体积、Windows 7 兼容和液态玻璃实现，只列为方向，不建议在 6.5x 里做。

### P2-2 其他小项（低收益，顺手做）

- 媒体刷新命中缓存仍 `replace()` 新对象（`api/providers.py` `_refresh_interval = 0.55`，1550 行）：命中且无变化时复用旧对象，减少下游比较和重绘。
- 音频路由线程每 0.15 s 醒一次、每 2 s 枚举输出设备，蓝牙电量会枚举全部 PnP 设备：改为监听 `IMMNotificationClient` 设备变化，电量 30 s 一次。
- 主线程看门狗 250 ms 心跳本身很便宜，保留。
- 启动时 3.2 s 卡顿停在 `local/music_resize_hit_test.py:191 nativeEvent`：用已有的 `GluSpot.startup phase=` 日志确认是哪一段，单独排查。

## 5. 实施顺序与验收指标

| 批次 | 内容 | 是否需要视觉确认 | 验收 |
| --- | --- | --- | --- |
| 第 0 批 | M0-1、M0-4，然后 M0-2、M0-3 | 否（M0-2 要对比海平面分析结果） | 达到第 4.0 节的内存验收指标 |
| 第 1 批 | P0-1、P0-2、P0-3 | 否 | 空闲（只开岛）进程 CPU ≤ 3 %；媒体线程空闲 ≤ 0.5 %；全部相关单测通过 |
| 第 2 批 | P1-1 的 1、5、6（遮挡降帧、频谱移线程、空闲停表） | 轻度 | 播放页被遮挡时 ≤ 10 %；暂停静止时 ≤ 5 % |
| 第 3 批 | P1-1 的 2、3、4（分层缓存、局部重画、复用缓冲） | 是，真机 demo | 播放页可见播放 ≤ 30 %，缺页 < 500/秒，帧时间 P95 不回退 |
| 第 4 批 | P1-2、P1-3、M1-1、M1-2、M1-3 | 部分 | 封面全屏清晰度你确认过；岛动画无裁切 |
| 以后 | P2-1 | 是 | 另立项 |

每批都按同一组场景前后对比测量（同一首歌、同一窗口大小、同一岛模式），结果写回本文件。

## 6. 本次没有覆盖

- 全屏播放页、音乐库窗口、歌单同步窗口、音轨比对后台任务、软件更新下载，都没有单独测。
- 岛单独可见（非 Finder 栏模式）的 60 Hz 绘制、动态封面视频解码，没有在这次实测里出现。
- 每个场景只测了一次，没有多次取平均；CPU 读数随歌曲和歌词密度变化。
- 6.51 那组数据来自升级前就在运行的旧进程，用作长时间运行的参照，不是 6.52 的读数。
