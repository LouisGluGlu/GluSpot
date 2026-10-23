# GluSpot 重构路线（Claude-spot 工作区）

> 工作区：`E:\Claude-spot\GluSpot`，从 `E:\gpt-island` 复制；原目录不做任何修改。
> 每一步都必须：静态检查无未定义名 → Windows 全量测试与基线对比 → 记录删除/改动的测试。

## 决策：解耦重构 + 清理，不重写

**为什么不重写**

1. 约 10 万行代码里沉淀了大量只在真机上试出来的 Windows 行为：任务栏/自动隐藏跟随、MyDockFinder、SMTC 时钟、
   AirPlay/多设备 PCM 同步、逐字歌词计时、网易云/QQ 登录恢复。这些行为在 README 和 90 多份 docs 里只有零散记录，
   重写等于把这些坑重新踩一遍。
2. 已有约 5,700 个能跑通的测试。它们虽然耦合实现细节，但仍是现成的安全网；重写会让它们全部失效。
3. 真正的问题集中在少数几个文件：`island_stack_visual.py`（2.1 万行）、`music_surface_window.py`（1 万行）、
   `settings_window.py`（4,800 行）。问题局部、可以逐步拆开，不需要推倒整个系统。
4. 可以随时停下：每一步都能独立验证、独立交付，重写在完成之前没有任何可交付物。

**什么时候会改主意**：如果拆分到某个模块时发现它的行为没有测试保护、文档也说不清，就对那一个模块
“按规格重写”（先补特性测试，再替换实现），而不是整体重写。

## 步骤

| 步骤 | 内容 | 状态 |
| --- | --- | --- |
| 0 | 建立工作区、按模块分进程的 Windows 测试基线 | 完成 |
| 1 | 删除已下线功能（插件/时间/天气/日程/消息/弹幕/设备到达弹窗）的代码与测试 | 完成，见下 |
| 2 | 修复基线里的原生崩溃（Python 循环 GC 在任意时刻回收 Qt 对象，见下） | 完成（真机观察待做），见下 |
| 3 | 删除产品代码中不可达的方法（`IslandStackVisual` 约 190 个、`SettingsWindow` 约 100 个），需确认 Gelv/Ember 歌词样式已废弃 | 待确认 |
| 4 | 移除 `IslandVisualData.messages/plugins` 字段、presentation/positioning 模块里只服务下线功能的函数 | 待做 |
| 5 | 拆分 `IslandStackVisual`：任务栏/位置跟随 → 歌词 → 绘制 → 动画状态，每次一个职责，抽成纯逻辑类 | 待做 |
| 6 | 测试改造：用工厂/纯逻辑类替代 `__new__` + 塞属性；去掉 `inspect.getsource` 断言 | 随步骤 5 进行 |
| 7 | 吞异常改为至少记录 debug 日志；README 拆分为用户版与开发状态 | 待做 |

## 工作规则

- 只删“产品代码中不可达”的代码。判定工具：`_claude/tools/deadmethods.py`（以属性名/字符串/名称引用为准，
  对 `getattr(self, f"set_{x}")` 这类动态分发要人工核对）。
- 删除的测试必须是“只测试已删除代码”的测试，逐条记录在本文件或对应步骤记录里。
- 不改变任何用户可见行为；如需改变，先问。
- 保持 CRLF 行尾（Windows 工作副本是 CRLF）。

## 步骤 1 记录：删除已下线功能

README 已写明时间、天气、日程、消息通知、插件提醒、弹幕和设备到达弹窗从设置和运行时下线，但代码仍在，
只靠 `_runtime_extras_retired` 开关挡住。本步删除这些代码。

**删除的模块**：`api/message_detector.py`、`api/plugin_provider.py`、`local/plugin_presentation.py`、
`local/plugin_manager_window.py`、`local/audio_device_notice.py`。

**删除的类/方法**：`IslandStackVisual` 中 240 个插件/消息/弹幕/闹钟/倒计时方法，以及因此变成不可达的 27 个
时间面板辅助方法；`FullscreenPluginAlertOverlay`、`BarrageOverlay`、`Message` 类；`SettingsWindow` 中 47 个
只服务已下线页面的处理函数和 `_actions_page`；诊断页中的微信/QQ/钉钉消息诊断行。

**修正的活代码引用**（删除后会在运行时报错的地方）：
- `local/app.py` 启动时调用 `_set_plugin_focus` —— 删除该调用。
- `SettingsWindow._touch` 调用 `_enforce_stack_limit` —— 删除该调用。

**保留未动**（留给后续步骤）：`IslandVisualData.messages/plugins` 字段及 provider 中清空它们的逻辑；
`settings_store` 中旧设置键的迁移逻辑；`api/fake_*` 演示数据。

**删除的测试**：见 `.refactor/removed_tests_step1.txt`（逐条列出）。

**修改的测试**（测试的是现存行为，只去掉对已删除方法的 patch）：
- `test_music_fullscreen` 的 music surface tick 测试：去掉对 `_prune_expired_messages` 等已删除方法的 patch。
- `test_island_audio_device_surface` 的背景采样测试：去掉对 `_sample_background_luma` 的 patch。
- 多个 provider 测试中给已不存在的 `plugin_provider/message_detector` 属性赋值的行。

## 基线与步骤 1 的测试结果（Windows，按模块分进程运行）

| | 通过 | 跳过 | 失败 | 原生崩溃 |
| --- | ---: | ---: | ---: | ---: |
| 基线（gpt-island 原样） | 5,669 | 3 | 0 | 3 个模块 |
| 步骤 1 之后 | 5,331* | 3 | 0 | 1 个模块（与基线同一处） |

\* 基线 5,675 − 删除的 284 个只测下线功能的测试 = 5,391。差额来自 `test_owned_playback` 在第 14 个测试处崩溃、后面 61 个没跑到；该模块单独重跑 75/75 通过。
没有任何原本通过的测试变成失败。（更正：第 1 步当时只按文件记录了 4 个整文件删除的测试模块，另有 1 个活代码测试被误删；
已在步骤 2 中补全清单并恢复该测试，见下。）

## 步骤 2 记录：原生崩溃定位

基线里 `test_music_fullscreen`、`test_owned_playback`、`test_owned_queue_artwork` 会随机触发
`0xC0000005` 访问冲突，崩溃点每次落在不同的 Qt 调用上（`QImage.fromData` 等）。

已确认的事实：

1. 崩溃点的代码单独运行 2,000 次不崩；目标测试单独运行 8/8 通过。
2. 只有在同进程里先跑过 `OwnedControlThreadTests` 的前 3 个测试，目标测试才会崩（3/6 次）。
   换成前 6 个测试则变成进程卡死。
3. 关闭 Python 自动循环 GC、只在主线程每个测试结束后手动 `gc.collect()`：**0/6 崩溃**。

结论：某些含 Qt 对象（`OwnedMusicPlayer` 及其 QTimer/QMediaPlayer 子对象）的引用环，被 Python 的
循环 GC 在任意时刻回收（可能是另一个测试执行到一半时，也可能在工作线程上），Qt 对象在错误的时机或线程析构，
破坏了内存。基线日志中出现的 `QObject::~QObject: Timers cannot be stopped from another thread` 与此一致。

这不只是测试问题：应用本身也在大量使用工作线程（`MusicTasks` 等），同样可能在用户机器上随机崩溃。

候选修复（需确认后再做）：

- A. 应用级：启动时 `gc.disable()`，用主线程 QTimer 在空闲时定期 `gc.collect()`（PySide 常见做法）。
  风险：改变全局内存回收节奏，需要观察长时间运行的内存曲线。
- B. 定点：`OwnedMusicPlayer.close()` 等关闭路径主动断开信号、`deleteLater()` 子对象，打破引用环；
  测试 `tearDown` 中主线程 `gc.collect()`。风险小，但只覆盖已知对象。
- 建议先做 B 让测试稳定，再用 A 作为应用级兜底并在真机上观察内存。

## 步骤 2 记录：GC 时机修复

采用 B（测试）+ A（应用）两部分。

**测试端**（`tests/test_owned_playback.py`）：
- 假 `Tasks.close()` 像真 `MusicTasks.close()` 一样清空排队任务，排队回调不再持有播放器形成引用环。
- `OwnedPlaybackTests`、`OwnedControlThreadTests` 在 `setUp` 中 `self.addCleanup(gc.collect)`：在测试之间、主线程上回收，
  而不是在下一个测试中途。

**应用端**（新增 `local/gc_scheduler.py`，在 `LocalIslandApp.__init__` 中安装）：
- 启动即 `gc.disable()`；主线程 QTimer 每 250ms 检查一次，按 Python 原阈值（700/10）执行第 0/1 代回收。
- 第 2 代（实测每次停顿约 39ms）只在没有动画时执行（距上次 ≥30s），或距上次 ≥120s 时强制执行。
  “有动画”= 岛的帧定时器处于动画档（`_retune_frame_timer` 记录 `_visual_animation_active`）或全屏音乐界面打开。
- 启动 5 秒后 `gc.collect()` + `gc.freeze()`，把启动期的长寿对象移出之后的全量扫描，降低每次第 2 代回收的成本。
- 单元测试：`tests/test_gc_scheduler.py`（注入假 gc 与时钟，6 个用例）。

**验证**（Windows，`_claude/module_loop.py`，每次新进程）：

| | 运行次数 | 崩溃 |
| --- | ---: | ---: |
| `test_owned_playback`，修复后 | 15 | 0 |
| `test_owned_playback`，原版 gpt-island | 15 | 8（0xC0000005） |
| `test_owned_queue_artwork`，第 1 步后（未改） | 15 | 0 |
| `test_music_fullscreen`，第 1 步后（未改） | 15 | 0 |

全量（按模块分进程，264 个模块）：**5,399 通过 / 3 跳过 / 0 失败 / 0 个崩溃模块**；与基线相比无回归。
另恢复 1 个测试后该模块单独运行 5/5 通过。

**清单更正**：第 1 步只记录了逐条删除的 284 个测试，整文件删除的 4 个测试模块（92 个测试）未逐条列出，现已补进
`.refactor/removed_tests_step1.txt`。另有 3 个测试是改名（新测试在跑并通过），1 个测试
（`test_live_tick_skips_extra_polling_and_overlay_work_on_both_music_surfaces`）同时断言了活行为
（两种音乐界面每帧都刷新媒体数据和歌词），按规则应修改而非删除——已恢复为
`RetiredIslandRuntimeTests.test_live_tick_keeps_media_and_lyric_work_on_both_music_surfaces`，只去掉对已删除方法的断言。

**真机测量**（2026-10-23，源码版各跑 15 分钟，播放音乐、MyDockFinder 在运行；工具 `_claude/live_probe.py`，
不改产品代码，16ms 探针定时器测主线程延迟，卡顿 >35ms 时采样主线程调用栈；日志 `_claude/results/live_*.log/json`）。
以下为启动 60 秒之后的数据：

| | 新方式（调度器） | 旧方式（自动 GC） |
| --- | ---: | ---: |
| 在工作线程上发生的 GC（崩溃根因） | **0** | 68 |
| 第 2 代回收次数 / 总耗时 | 7 次 / 4.0s | 13 次 / 6.4s |
| 第 0/1 代回收总耗时 | 3.3s | 3.6s |
| 主线程卡顿 >33ms 次数 / 总时长 | 3,360 / 152s | 3,503 / 170s |
| 由 GC 造成的卡顿 | 8 | 18 |
| 内存（工作集）开始 → 15 分钟 | 369 → 1,090 MB | 353 → 1,030 MB |

结论：
1. 调度器达到目的：GC 不再发生在工作线程上；GC 总耗时和由 GC 造成的卡顿都减少。
2. **内存泄漏是原有问题，与本次改动无关**：两种方式都以约 45–48 MB/分钟增长；存活对象越来越多，第 2 代回收的
   单次停顿随之从约 130ms 涨到 1 秒以上（两种方式都是）。已安装的 GluSpot 运行中占用约 2 GB，与此一致。
3. **动画抽搐的主因不是 GC**：约 2/3 的卡顿采样落在 `mydockfinder.py`——岛跟随 MyDockFinder 条时，
   `_refresh_finder_bar_follow_position` 每 250ms 在主线程调用 `read_finder_material`：`grabWindow` 截取整屏宽度，
   再由 `_average_strip/_average_area` 用纯 Python 逐像素 `pixelColor` 取色，每次约 35–50ms，即每秒约 4 次掉帧。
   其次是 `_poll_mydockfinder_presence` / `refresh_live_finder_bar` 中的窗口枚举。

**待做（需确认后再改）**：
- A. 查内存泄漏：已完成，见下“内存泄漏修复”。
- B. MyDockFinder 取色移出主线程热路径：先缩小采样（Qt 在 C++ 里缩放后再取色，或按块取样），降低频率或只在条移动/变色时取。

## 内存泄漏修复

**定位**（`_claude/leak_probe.py`，真机 10 分钟：每分钟按类型统计 GC 追踪对象 + tracemalloc 分配位置）：
增长的几乎全是 ctypes 类型对象——`PyCStructType` 与 `PyCPointerType` 各 +10,830，以及它们附带的 `CField`、
`StgDict`、`getset_descriptor`、tuple/list/weakref。tracemalloc 增长最多的行正是函数体内的 `class ...(ctypes.Structure)`
定义和 `ctypes.POINTER(...)` 调用。

**根因**：3 个高频函数每次调用都在函数体内重新定义 `ctypes.Structure` 子类，并用 `ctypes.POINTER(cls)` 设置 argtypes；
ctypes 把每个 `POINTER(cls)` 永久缓存在 `ctypes._pointer_type_cache` 里，于是每次调用都永久留下一个结构体类型和一个指针类型：
- `mydockfinder._native_finder_bar_window_snapshots`（`ProcessEntry`、`MonitorInfo`）——跟随 MyDockFinder 条时频繁调用
- `mydockfinder._running_mydockfinder_process`（`PROCESSENTRY32W`）
- `app._running_process_snapshots_for_names`（`PROCESSENTRY32W`）

每分钟约 6,600 个类型、每个约 7KB，与实测约 46 MB/分钟一致；存活对象越来越多，第 2 代回收也越来越慢。

**修复**：结构体改由 `@lru_cache` 的工厂函数只定义一次（`_native_window_structs`、`_process_entry32w_type`），
仍在原 `try` 内调用，非 Windows 上仍不在导入时加载 `wintypes`。结构体字段和调用行为不变。
`island_stack_visual` 里的 `APPBARDATA` 也是函数内定义，但只用 `byref`、不进 `POINTER` 缓存，可被回收，未改。

**测试**：新增 `tests/test_native_struct_leak.py`：反复调用这 3 个函数，`ctypes._pointer_type_cache` 不得增长。
修复后通过；用原版 gpt-island 代码运行同一测试失败（断言失败）。全量：**5,401 通过 / 3 跳过 / 0 失败 / 0 崩溃模块**。

**真机验证**（同样 `live_probe.py`、新 GC 方式、MyDockFinder 在运行）：

| | 修复前（15 分钟） | 修复后（22 分钟） |
| --- | ---: | ---: |
| 工作集内存 | 369 → 1,090 MB | 330 → 271 MB，平稳 |
| 提交内存 | 665 → 1,495 MB | 633 → 670 MB |
| 第 2 代回收单次停顿 | 131ms → 1,029ms，逐次变长 | 4–6ms |

注：这次源码版在 15 分钟时没有按计划自动退出（`QApplication.quit()` 未生效，前两次运行正常），手动结束。
主线程卡顿（每 30 秒约 110–130 次 >33ms）依旧，主因是 MyDockFinder 主线程取色，见上方待做 B。
