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
| 2 | 修复基线里的原生崩溃（Python 循环 GC 在任意时刻回收 Qt 对象，见下） | 已定位，待你确认方案 |
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
所有消失的测试都能在 `.refactor/removed_tests_step1.txt` 里找到；没有任何原本通过的测试变成失败。

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
