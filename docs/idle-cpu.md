# 空闲 CPU 排查

基于 `df4e54e`，补上旧启动耗时 PR 缺少的分析。只记现状，不改行为。

## 空闲不等于停工

这里指启动、加载、过渡已结束，没有鼠标键盘操作。要分开记录：无歌曲、已暂停、继续播放，以及窗口可见或全部隐藏／最小化。被其他窗口挡住不等于隐藏；登录等待、加载失败和投放连接中也要另算。

播放中没有操作，歌词、进度、波形和解码仍有工作。暂停以后，界面时钟也不一定停。

## 还有什么在跑

**频率来自代码，不是实测。** 这是请求或检查间隔，不是屏幕帧率，不能相加换算 CPU。行号对应上述基线。

| 路径 | 来自代码的频率 | 空闲时的工作和停止条件 |
| --- | --- | --- |
| 主岛 `local/island_stack_visual.py:1703,1720,3828` | 默认静止 17 ms，约 59 次/秒；播放歌词可升频；隐藏通常 100 ms，媒体期限前会更密 | 暂停不停止时钟。可见时检查状态、波形和重绘；隐藏保留媒体／歌词／DJ 时钟，跳过普通绘制。 |
| 正式音乐窗口 `_demos/narrow3/narrow_window.py:232,469` | 33 ms，约 30 次/秒 | 入口复用 live 子类（`local/island_stack_visual.py:7178`），覆盖基类16 ms。跑歌词弹簧、控件、背景和绘制检查；隐藏／最小化停止。没有第二个 study timer。 |
| 库底部岛 `local/library_now_playing.py:121`；状态点 `local/library_layout_variants.py:319` | 33 ms；状态点 90 ms | 可见且保留歌曲时，底部岛暂停后仍重绘；隐藏／最小化停。状态点仅在已连接按钮可见时呼吸。 |
| 背景／玻璃 `local/music_surface_background.py:48`、`local/music_control_glass.py:473` | 背景缓存预算 30 Hz；稳定几何下变化的玻璃采样最多 10 Hz | 页面带动，没有独立玻璃定时器。相同区域复用缓存，静止时停止新玻璃栅格工作；不代表父窗口停止刷新。 |
| 动态封面 `local/motion_cover_playback.py:154,376` | 可见性检查 250 ms；帧由解码器送来 | 有视频且可见就可播放，未检查歌曲是否暂停。隐藏后暂停，约 1 秒后停解码；检查时钟可能保留。成本还包括解码、缩放和重绘。 |
| 媒体状态 `local/island_stack_visual.py:1279`、`api/providers.py:2013,2169` | 岛通常 650 ms，前台跟踪 180 ms，短时强跟踪 120 ms；provider 通常 550 ms | 同一调用链上的限频，不是几条独立轮询。到期读取、应用状态，其间本地推算进度。 |
| SMTC／UIA `api/capture/smtc.py:143`、`api/capture/uia.py:201`、`api/capture/runtime.py:180` | SMTC 500 ms加系统事件；UIA 校准 7.5 秒、异常 2 秒、缺进度回退最短 1 秒 | 绑定对应采集路线才运行；暂停仍观察。WinRT／COM 树读取比标志检查重。 |
| 网易 CDP `api/capture/cdp.py:394`、`api/capture/netease_js.py:455,548` | 心跳每秒；进度推送通常限到 10 Hz；断线退避最长 10 秒 | 不是十次/秒 CDP 查询。关闭路线停止；50 ms启动探测最多 5 秒。当前基线自动连接不会重启网易。 |
| 音频设备 `api/audio_route_controller.py:1186`、`api/owned_audio_outputs.py:660` | 路由 150 ms；库内输出空闲 250 ms、组播运行时 25 ms；设备清单 2 秒 | 启用后可在界面隐藏时继续。另有可选 PCM 分析、峰值表；后者无请求超过 1 秒休眠（`api/audio_analyzer.py:110,245`）。 |
| 常驻辅助 `local/app.py:249,706`、`local/gc_scheduler.py:55` | 心跳／GC各 250 ms；看门狗线程 0.8 秒；Finder 检测 2.5 秒 | 心跳通常只改时间戳，GC到条件才收集。Finder实际跟随另可每 250 ms采样背景（`local/island_stack_visual.py:5032`）。 |

## 哪些会停，哪些要留意

- [动画审计](animation-audit.md)的共享弹簧确实会停：最后一个弹簧结束后停止定时器（`local/spring_motion.py:249,255,405`）。这不包括岛／音乐窗口时钟。几何、导航、滚动等 Qt 动画是有限过渡，未发现无限循环配置。
- 搜索是 350 ms单次防抖（`local/music_library_window.py:705`）。目录／封面失败会重新安排重试，最长退到 30 秒（`local/music_library_controller.py:724,1137`）。加载灰条、来源选择页、打开的收藏弹窗另可约 30 次/秒重绘。
- 音乐窗口每 200 ms取设备快照；设置预览显示时每 500 ms刷新；输出弹窗每 350 ms取快照（`local/music_surface_window.py:613`、`local/settings_window.py:3005`、`local/audio_output_panel.py:1234`）。工作队列空时线程阻塞等待。
- Apple Music 时钟只在伴随播放成功后启动：250 ms推算，每四次查询状态；普通启动、伴随未运行时不启动，暂停曲目仍可能查询（`local/owned_music_player.py:2398–2441`）。未发现独立常驻伴随心跳。
- Windows对外发布SMTC每 500 ms检查，状态不变跳过发布（`local/windows_smtc.py:293`）。默认关闭的内外打断观察器，开启后才每 500 ms轮询（`api/capture/interruption.py:719`）。
- 启动日志没有周期任务（`local/main.py:12`、`local/startup_timing.py:25`）。保存、宽度收尾和延迟导航通常是操作触发的单次任务。

## 优先查谁

1. **可见页面持续绘制**：音乐窗口、库底部岛、主岛。先对比暂停和隐藏；大面积绘制比小标志检查更值得查。
2. **动态封面**：暂停歌曲未必停视频。另已复核淡入中清空封面后，16 ms时钟早退却未停止的路径（`local/motion_cover_playback.py:179,420`）。
3. **Windows采集、音频设备、Finder采样**：全部隐藏仍高时，先查这组。
4. **未结束的加载、弹窗和状态点**，再查心跳／GC。共享弹簧和启动日志不应直接背锅。

这是查证顺序，不是已测出的耗时排名。

## 离屏 Linux，不是 Windows 实测

脚本：[tools/measure_idle_wakeups.py](../tools/measure_idle_wakeups.py)。Qt 6.11.2、DPR 1、离屏报告 60 Hz；音乐窗口 960×540、frosted 背景。每组独立进程，等 12 秒再测 30 秒，各测一次；使用合成歌曲／封面，没有真实音视频和完整应用后台服务。

| 场景 | 主岛定时器次数 | 音乐窗口定时器次数 | 主显示表面绘制次数 | 进程 CPU，占一个核 |
| --- | ---: | ---: | ---: | ---: |
| 可见岛，无歌曲 | 1765 | — | 1733 | 19.79% |
| 可见岛，暂停 | 1765 | — | 1733 | 18.10% |
| 可见岛，播放 | 1765 | — | 1727 | 18.55% |
| 音乐窗口，暂停；主岛隐藏 | 322 | 909 | 515 | 16.37% |
| 音乐窗口，播放；主岛隐藏 | 322 | 909 | 909 | 26.31% |
| 全部隐藏，暂停 | 323 | — | 0 | 0.45% |

绘制列只计主显示表面，CPU包含子控件和计数开销；原始输出另列子控件。六组共享弹簧回调均为 0，音乐窗口设备快照各 150 次。另用合成帧确认：淡入中调用 `clear()` 后，16 ms定时器仍开；这是状态路径验证，没有真实解码。

运行：`QT_QPA_PLATFORM=offscreen /workspace/gluspot/qtvenv/bin/python tools/measure_idle_wakeups.py`。本次原始输出在交接目录 `par-idle-cpu/measure-offscreen.txt`。这些数值只说明上述界面夹具的工作量，不是完整应用占用，也不是优化前后对比。

**未在真实 Windows PC 测量**：完整程序CPU、SMTC／UIA／网易CDP、Finder／DWM／GPU、真实音视频解码、Apple Music伴随、投放、多屏和高刷新率。离屏回调数不等于屏幕帧率或系统唤醒数。已有动画、动态封面、玻璃线程 benchmark 也不能代替本次完整程序空闲测量。
