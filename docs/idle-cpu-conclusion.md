# 空闲 CPU 结论

这份文档只记录已经合并的启动计时（PreGluspot PR #2，提交 `e4fff50`）当时没写进仓库的结论。它不改运行时代码。

读数单位是**一个逻辑核**：这段时间里进程用掉的 CPU 秒，除以墙钟秒，再乘 100。100% 表示占满一个核，不是占满整台机器。

结论针对当前基线 `grok/capture-narrow-v4` 的尖端 `fdbd7e1`。定时器间隔按这个树上的代码核对过。PR #2 自己没有改这些间隔。

## 测到了什么

真机空闲，按重启的先后，读数是 **36.6% → 52.9% → 84.0%**。

先前 PR 说明里写的是大约 45%，以及更早到过大约 83%。84.0% 对得上“到过大约 83%”。36.6% 和 52.9% 是更早的两档，那份说明没有分开写。

这三次没有记下岛是否可见、播放页是否开着、氛围是不是 `sea_level`。它们不是同一窗口状态下的对照，也不是这次在 Windows 上重测的结果。

## 为什么重启之后 CPU 往上走

升高不是 `e4fff50` 造成的。那次提交只加启动日志，时钟、轮询和绘制都没动，所以它也没有把已经升高的空闲 CPU 降下来。

代码里没有“每重启一次就多挂一个定时器”的路径。`local/app.py` 的 `SingleInstanceGuard` 拿不到锁就直接退出，正常不会叠两个 GluSpot 进程。量的时候仍要在任务管理器里确认只有一个进程。

能对上“没在播，却占掉大半个核”的，是可见时的绘制钟：

- 岛还显示时，定时器停在 `animation_fps`。默认是 60，类型是 `PreciseTimer`。隐藏或最小化之后才降到最多 100ms。可见时不降。
- 默认氛围是 `sea_level`。波浪用墙钟推进。`is_playing` 只影响幅度，不把这个钟停掉。所以不播放、岛还在，画面仍会重画。
- 正在播放并且有歌词时，钟还可以抬到 `lyric_animation_fps`（默认 120），但不超过屏幕刷新率。
- `tests/test_island_stack_visual_messages.py` 里的 `test_playback_and_finder_keep_full_animation_interval` 锁着：播放中，以及 Finder 模式，稳定间隔和当前间隔相同。可见时不会因为“没在做过渡”而放慢。
- 播放页开着时，动画定时器固定 16ms（`MusicSurfaceWindow.ANIMATION_FRAME_MS`）。歌词不动时，整页 `update()` 仍按 AlbumFlow 的 1/30 秒做。页面隐藏后这个定时器会停。

同一次“空闲”，岛或播放页从隐藏变成可见，读数可以从三十多抬到八十左右。这是目前说得通的原因。36.6、52.9、84.0 三档没有窗口状态记录，不能把每一档说成某一次提交。若要确认，需要在同一种窗口状态下各量一次。

## 合并进去的代码改了什么

`e4fff50` 在原有启动顺序上，每个阶段结束时打一行 INFO：

`GluSpot.startup phase=<name> ms=<毫秒>`

日志在 `local/startup_timing.py`，写到 stderr。调用没有搬家，启动顺序没有变。

当前树上的阶段名：

| 阶段 | 位置 |
| --- | --- |
| `import_ui` | `local/main.py` |
| `qapplication`、`settings`、`lyric_fonts`、`windows_media_provider`、`online_music`、`owned_player`、`windows_smtc`、`audio_outputs`、`island_window`、`saved_settings`、`settings_window`、`playback_entry`、`music_library`、`song_collection`、`mydockfinder` | `local/app.py` |
| `narrow_view` 或 `tray` | 窄视图走前者，普通启动走后者 |
| `entry_start` | `LocalIslandApp.run` |
| `platform_login_status`、`platform_account_status` | `local/playback_entry_controller.py`，账号读取时 |
| `windows_media_session` | `api/providers.py`，只记第一次打开系统媒体会话 |

这些行只说明启动哪一段慢。它们不解释空闲 CPU。

## 还没动的可疑点

下面几条是读定时器和轮询之后留下的。PR #2 故意没改。放慢它们会碰到帧率、会话重试或设备轮询，不能确定没有副作用。缓存命中时仍 `replace()` 一份新的视觉数据，是为了清掉 messages 和 plugins，这次也没有改成复用。

1. 岛可见时的 60Hz 绘制。见上一节。这是最可疑的一项。
2. 播放页可见时的 16ms 动画钟，加上大约每 1/30 秒的整页刷新。同一页还挂着 200ms 的音频输出状态定时器，页面一隐藏就停。
3. Windows 媒体刷新的基准间隔仍是 0.55 秒（`api/providers.py` 的 `_refresh_interval`）。缓存命中也会 `replace()`。前台或强跟踪时会再缩短到 0.12 或 0.14 秒。岛这边的 `media_refresh_interval` 默认是 0.65 秒，前台路径是 0.18 秒（`local/media_position_pipeline.py`）。SMTC 采集器默认每 0.5 秒读一次（`api/capture/smtc.py`）。`WindowsSmtcBridge` 在 Windows 上每 500ms 调用 `refresh()`。单看不够解释 45% 或 84%，但空闲时会反复读会话。
4. 程序内播放默认开着（`v2_owned_playback_enabled` 默认 True）。启动时 `start_audio_outputs()` 会拉起 `GluSpotAudioRouting` 线程。线程每 0.15 秒醒一次，每 2 秒 `list_outputs()`。蓝牙电量这条路径会枚举当前全部 PnP 设备（`WindowsAudioDevices.snapshot` → `_bluetooth_evidence`）。
5. 前台窗口场景探测大约每 0.10 秒一次，里面有 `OpenProcess`（`IslandStackVisual._update_scene_state` / `_window_process_name`）。Finder、任务栏紧凑或全屏回退时，探测会改成大约 16ms。MyDockFinder 监听默认每 2.5 秒做一次进程快照（`local/app.py` 的 `mydockfinder_listener_timer`）。GC 调度器默认 250ms。主线程看门狗的 Qt 定时器是 250ms，只更新心跳；旁边的线程每 0.8 秒看一次。这几项相对 84% 更次要。设置预览是 500ms，只在设置窗口显示时跑，隐藏就停。

没有把“重复 `replace()` 改成复用”落地。

## 在 Windows 上怎么自己量

先只留一个 GluSpot 进程。任务管理器里如果有两个，先关掉旧的再量。

空闲时不要播放，鼠标不要停在岛上。记下四件事，三次对比必须相同：岛是显示还是隐藏、播放页开没开、氛围是哪一种、是不是只有一个进程。

在 PowerShell 里对那个 PID 采 10 秒。`CPU` 是进程累计的处理器秒。下面的结果就是一个核的百分比：

```powershell
$p = Get-Process -Id <PID>
$cpu0 = $p.CPU
$t0 = Get-Date
Start-Sleep -Seconds 10
$p.Refresh()
$wall = ((Get-Date) - $t0).TotalSeconds
$oneCore = ($p.CPU - $cpu0) / $wall * 100
"{0:N1}% of one core over {1:N1}s" -f $oneCore, $wall
```

开发时从控制台运行 `python local/main.py`，stderr 里会有 `GluSpot.startup phase=... ms=...`。哪一段最慢，看这些毫秒数。安装版有没有控制台，这次没有在 Windows 上验证。

建议至少做三组，每组都用上面的命令，并且窗口状态保持不变：

- 岛隐藏，播放页关闭。
- 岛显示，不播放，氛围保持现在的默认 `sea_level`。
- 播放页开着，不播放。

若只有“岛显示”这一组接近 84%，就可以把空闲 CPU 收到可见岛的绘制钟上。若三组差不多，再看音频路由线程和 0.55 秒的媒体刷新。这次没有在 Windows 上跑这三组。
