# 内外播放打断 · t260

设置位于“播放与采集 → 播放来源”，复用现有 IosSwitch、提示、搜索及持久化路径。`v2_playback_interruption_enabled` 缺失或非法时为 `False`；运行时 setter 仅接受 bool。稳定 OFF 状态不创建 t260 监视器、不额外读取会话、不发送暂停或 CDP 调用；ON→OFF 仅解绑和移除既有观察器，已 dispatch 原生命令的边界见下文。原有手动播放和独立启用的采集按原路径运行。

## 已批准规则

- 分类是两个方向的共同门槛，应用类别不作为门槛。SMTC 明确 Music、精确已知音乐 app id，或可读标题加歌手/有效原始专辑图可确认为 MUSIC；包括浏览器和网页音乐。任一明确 Video/Image 类型优先否决音乐证据。坏值、读取失败、身份不明或证据不足不授权干预。
- 开启时、重新连接时及新发现即 Playing 仅建立基线。只有开启后观察到的非播放→播放转换才是外部新开始；换曲、seek、补图和重复 Playing 均不算。外部观察区间须严格在 owned 的实际开始和适用用户意图之后；跨界、重叠或时间相同不能猜先后。
- 明确库内用户请求须成功并真正开始才暂停当时已观察为 Playing 的 MUSIC 会话。目标集合与当时音乐资格一并冻结；失败请求、旧 retained stream、自动下一曲、缓冲恢复和迟到 metadata 不产生新授权。
- 更晚外部 MUSIC 开始时，通过 GUI 队列幂等 Pause owned，取消待加载 autoplay、恢复和关联 DJ 续播，保留队列与位置。确认后临时 pin 同一个外部会话；展示、音频、seek、传输、owned 能力和系统媒体桥共享来源决定。来源偏好和两方播放模式均不写入。pin 换曲继续跟随，退出安全释放，不自动恢复播放器。
- 所有自动控制都是 Pause；不使用外部 toggle、Play、Stop、Seek、静音或全局媒体键。确认的原始 Paused ACK 不产生反向意图；真正恢复属于新边沿。自动事务有一秒冷却，真实恢复保留至到期复核，明确用户操作优先。
- 设置代次、用户 serial、会话实例、媒体代次、播放区间和 manager 身份在最终 dispatch 复核。OFF、手动来源选择、新用户意图或连接替换撤销旧动作。一个目标/播放区间/用户意图只尝试一次；不确定 native 写入不触发另一种写入。

## 分类和检测扩展

`api/capture/interruption.py` 提供 `MediaKind`（MUSIC / VIDEO / GAME / UNKNOWN）、可替换 `MediaClassifier` 与 `InterruptionPolicy`。`PlaybackSourceProvider` 接受可选分类器/策略注入；默认 VIDEO、GAME 钩子均返回 False，UNKNOWN 不干预。`ReservedAudioDetector.detect()` 仅返回 UNKNOWN，不枚举进程或音频会话。

未来每进程游戏/视频音频检测可接该空桩，但需要单独实现、验证及明确的策略授权；当前没有音量峰值推断、无 SMTC 音频检测，也没有面向用户的插件体系。

观察器沿现有 provider asyncio loop 订阅 manager/session 事件并以 0.5 秒快照补偿。绑定保留原 native 对象，只接受对象同一性或明确对称布尔相等；不以 AUMID 合并实例。不同同名实例有歧义时不自动控制，新 wrapper 无法证明相等时重建基线。每批读取结束才发布边沿，避免先处理慢批次中的首个源。

## NetEase 的保守 CDP 路径

首选目标绑定的 `try_pause_async()`。只有明确返回 False 且独立确认仍 Playing，才考虑现有 ready CDP；超时、异常或接受后无 Paused 均视为不确定，不 fallback。

仓库此前只有已核实的 seek 协议，没有可直接使用的 Pause 契约。新增观察仅在开关开启且已有 ready 通道时启用：保留原 `channel.call` 的接收者、参数、结果和异常，观察播放器自身的明确 `audioplayer.pause` 调用（实际观察到的空参数或当前 native track 参数），再由独立 PlayState=false 确认。只在同通道、同 native 曲目和同连接代次复用这个契约；其他参数形状、旧 adapter 或未观察到已确认 Pause 时保持不可用。自动调用不会乐观改写状态，必须由独立 readback 确认。

provider 还要求既存 capture runtime 对应同一个 SMTC 会话、当前 metadata、native track 与新鲜 CEF 代次。临时 pin 返回 owned 时只保留可复核的内存证据。临时读取保留来源标记，即使 OFF/unpin 发生在异步读取中途，也不能从该读取进入 CDP ensure/start/reload/relaunch。

本功能不启动或重启 NetEase、不写安装目录、不改播放模式、不要求用户安装插件。

## 验证与边界

Box，`QT_QPA_PLATFORM=offscreen`，`/workspace/gluspot/qtvenv/bin/python`。新增五个模块的 82 项 fake 测试全部通过，覆盖浏览器音乐/视频/未知源、默认 OFF 零调用、开启基线、后开始交接、临时设置不变、保留队列位置、ACK/冷却/真实恢复、用户优先、冻结资格、manager 重连、负类型最终校验、时间歧义、owned 请求失败/取消/恢复/DJ、CDP 契约观察及清理竞态。

完整隔离套件覆盖 361 模块 / 6,374 tests，191.439 s，`complete=true`；359 模块正常结束，继承的 `test_owned_audio_bridge_handoff`、`test_owned_queue_artwork` 原生崩溃模块由 runner 分别逐例补齐。52 failure / 48 error / 32 skip 与 t258 逐身份及 skip 原因的 multiset 完全一致，新增、移除均为 0；这不是全绿套件。运行前后 Python source SHA256 一致。全部证据在 `/workspace/gluspot/handoff/t260/`，包括 `focused-final.log`、`focused-results.json`、`suite/summary.json`、`suite-comparison.json` 与 `FINAL.md`。

完整验证使用用户指定命令：

```text
/workspace/gluspot/qtvenv/bin/python /workspace/gluspot/handoff/t202/run_full_isolated.py /workspace/gluspot/grok-integrate4 /workspace/gluspot/handoff/t260/suite --workers 3
```

未验证：真实 Windows SMTC/NetEase 暂停，包括第三方类型声明、native wrapper 相等行为、事件延迟、CDP 实际 Pause 参数及最后状态复核到 native dispatch 之间的竞态。已 dispatch 的 native Pause 无法原子撤回；OFF/更新意图会阻止后续动作，不会补偿性 Play。Box fake 通过不等于 Windows 实机互斥已验收。
