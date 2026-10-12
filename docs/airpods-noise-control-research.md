# AirPods Pro 噪声控制：Windows 接入调研

调研日期：2026-10-12。范围仅为可行性报告。本次没有安装驱动、修改系统安全配置、连接控制通道、切换实体耳机模式或增加运行时代码。

## 结论

**有技术上的落实可能，但不是 GluSpot 现有设备音量接口能够直接调用的功能。** 已有 Windows 项目通过额外的 Bluetooth profile 内核驱动，向 AirPods 发送 Apple 私有协议命令，实现降噪、通透以及受支持型号的自适应模式。当前公开可复核路线的主要阻碍是驱动签名、部署和实体设备兼容性，而非缺少模式切换命令。[NTPods 驱动说明](https://github.com/arctumn/ntpods/blob/188e45df20259b616ce155be812086259d1a2488/windows/drivers/aap/README.md)

因此，后续若推进，建议作为**单独、可选的耳机控制集成**评估，先复用已有常驻服务，保持音乐输出与同步播放路径。现阶段不能承诺在用户这副耳机、当前蓝牙适配器和 Windows 安全配置下直接可用，也不能承诺首次连接或异常恢复完全无声断。这是根据公开源码作出的方案判断，并非本机实测结果。

## 官方能力与设备限制

Apple 的非 Apple 设备指南提供 Bluetooth 耳机配对、听歌和通话；噪声模式软件设置文档提供 iPhone、iPad、Apple Watch 和 Mac 路径。本次未找到 Apple 提供的公开 Windows 噪声控制 API。耳机柄长按仍是官方支持的直接切换方式，不能把“Windows 没有官方软件 API”理解为耳机连接 Windows 后自身降噪失效。[非 Apple 设备配对](https://support.apple.com/en-us/108786)、[官方模式切换](https://support.apple.com/en-us/108918)

| 耳机 | 降噪 / 通透 | 自适应模式 | 对 GluSpot 的含义 |
| --- | --- | --- | --- |
| AirPods Pro 1 | 支持 | 不支持 | 不能因设备名称含 Pro 就显示自适应 |
| AirPods Pro 2，包括 Lightning / USB-C | 支持 | 支持 | 逆向资料对 Pro 2 的模式命令、回读和功能初始化有具体证据；仍需目标固件实测 |
| AirPods Pro 3 | 支持 | 支持 | 官方能力已确认；不能直接沿用 Pro 2 的所有固件行为判断 |

能力依据是 Apple 文档，不能由音频端点的友好名称判断代际。现有截图中的“AirPods…”名称不足以确认型号或固件。[官方 ANC / Transparency / Adaptive 支持范围](https://support.apple.com/en-us/108918)

“自适应模式”“个性化音量”和“对话感知”是不同功能。前者调整降噪与环境声的组合；个性化音量调节媒体响度；对话感知在佩戴者说话时压低媒体并突出面前人声。用户请求的三种模式切换不必附带启用后两项，尤其不能让它们暗中改写 GluSpot 设备音量。[Apple Adaptive Audio 说明](https://support.apple.com/en-us/104979)

## 协议证据

LibrePods 实现了 Apple 的 AAP / AACP 通道。相关源码和逆向记录使用 classic Bluetooth L2CAP，PSM `0x1001`；噪声控制标识为 `0x0D`，模式值为 Off `0x01`、ANC `0x02`、Transparency `0x03`、Adaptive `0x04`。同一通道还能够接收模式变化通知。这里列值仅用于可复核证据，没有加入 GluSpot 的发送实现。[LibrePods 模式构造与解析](https://github.com/librepods-org/librepods/blob/efccc4499b2c1fd2ac243e975ae1f42d8e828ffd/linux/airpods_packets.h#L14)、[协议记录](https://github.com/librepods-org/librepods/blob/efccc4499b2c1fd2ac243e975ae1f42d8e828ffd/docs/AAP%20Definitions.md#L191)

**写入成功不等于模式已改变。** Pro 2 的逆向记录明确描述：未发送必要的功能初始化时，请求自适应可能收到另一个实际模式的回包。因此未来界面应区分“切换中”和“耳机已确认”，以真实回读结束操作；不支持的模式不显示。文档中的早期 “Adaptive Transparency” 命名也不能把自适应模式、通透下的大声抑制和对话感知混为一项。[初始化和确认的限制](https://github.com/librepods-org/librepods/blob/efccc4499b2c1fd2ac243e975ae1f42d8e828ffd/docs/AAP%20Definitions.md#L13)

Windows 的公开 L2CAP 驱动接口通过 Bluetooth profile driver 使用；普通音频端点 API 并不提供 Apple 私有控制通道。NTPods 的 Windows 实现使用 KMDF profile driver 和 `DeviceIoControl`，不是 Python / PySide6 直接打开现有音量接口即可完成。[Microsoft Bluetooth profile drivers](https://learn.microsoft.com/en-us/windows-hardware/drivers/bluetooth)、[Microsoft L2CAP 示例](https://learn.microsoft.com/en-us/samples/microsoft/windows-driver-samples/bluetooth-echo-l2cap-profile-driver/)、[NTPods 传输说明](https://github.com/arctumn/ntpods/blob/188e45df20259b616ce155be812086259d1a2488/windows/drivers/aap/README.md#L1)

本项目 `api/windows_audio_devices.py` 的现有方法负责 render-endpoint 枚举、`IAudioEndpointVolume` 音量和静音；模块明确不打开 Bluetooth 连接。它与噪声模式是独立的职责，不能把音量滑块当作耳机 ANC 控制，也不应为了 ANC 重建现有播放器、输出组或音频设备。

## 可参考项目及其真实成熟度

以下均固定到本次查阅提交，而非只根据项目宣传判断。

| 项目 / 查阅提交 | 可复核能力 | 对 Windows GluSpot 的限制 |
| --- | --- | --- |
| [LibrePods](https://github.com/librepods-org/librepods/tree/efccc4499b2c1fd2ac243e975ae1f42d8e828ffd)，`efccc4499b2c1fd2ac243e975ae1f42d8e828ffd` | Linux / Android 的模式命令、状态解析和控制界面；提供较完整的协议来源 | 不是 Windows 驱动；Android 是否需要 root / Xposed 取决于 OS、功能与 VendorID hook，不能据此承诺 Windows 无驱动可用 |
| [NTPods](https://github.com/arctumn/ntpods/tree/188e45df20259b616ce155be812086259d1a2488)，`188e45df20259b616ce155be812086259d1a2488` | 开源 Windows AAP 驱动、Rust 常驻 daemon 和 IPC UI；源码有发送 ANC 命令、持久会话及回包解析，作者报告真实设备日常使用 | 驱动未获 Microsoft 生产签名；要求 Test Mode、关闭 Secure Boot；控制通道由 daemon 独占，其他客户端应通过其 IPC；源码包含自动连接 / 音频路由恢复策略，不能整包照搬 |
| [SoundStage](https://github.com/capriqqw/soundstage-for-airpods/tree/114143c27670275a5ef6336b91047d84b8a16eb5)，`114143c27670275a5ef6336b91047d84b8a16eb5` | 独立 AAP/UARP profile driver、模式命令、状态解析；设计保持 A2DP Stereo | 作者标为 experimental / public beta，使用测试签名；其主要明确硬件验证是 AirPods 4 ANC，不代表 Pro 全系列已验证；还含音效与安装修复逻辑，不应整体引入 |
| [MagicPods / MagicAAP](https://github.com/steam3d/MagicPods-Windows/blob/d8fdbb3c3271718eeba6dde8d22b4c47029774aa/README.md)，`d8fdbb3c3271718eeba6dde8d22b4c47029774aa` | 商业 Windows 产品的官方说明明确支持 Off / ANC / Transparency / Adaptive，依赖 MagicAAP | 本次查阅公开仓库是说明与素材，没有可供审计复用的 Windows 应用 / 驱动实现；不能当成开源 SDK。厂商当前页面称官方版需 Test Mode，社区签名版只适用 2026 年 4 月更新前 Windows |
| [LibrePods Windows 实验移植](https://github.com/ivLis-Studio/librepods-windows/blob/36b526e29ffea0892db5f90c03b1c72cf2c54c54/README.md)，`36b526e29ffea0892db5f90c03b1c72cf2c54c54` | BLE 扫描、命令生成、loopback、实验 KMDF transport | README 明确将跨蓝牙栈重复真实控制和生产签名列为未完成；loopback 成功不能证明实体耳机模式已切换 |
| [NoiseBuddy](https://github.com/insidegui/NoiseBuddy/blob/9d1a32d244d7063a21132a90413203351768cefc/README.md)，`9d1a32d244d7063a21132a90413203351768cefc` | 较早 macOS 菜单栏 / Touch Bar ANC 与通透控制 | 作者明确称近期 macOS 已不工作；不是可移植到 Windows 的成熟当前接口 |

NTPods 的 [driver README](https://github.com/arctumn/ntpods/blob/188e45df20259b616ce155be812086259d1a2488/windows/drivers/aap/README.md#L85) 与 [Windows 架构](https://github.com/arctumn/ntpods/blob/188e45df20259b616ce155be812086259d1a2488/windows/README.md#L149) 是当前 Windows 真实性较强的证据。SoundStage 的 [明确硬件范围及限制](https://github.com/capriqqw/soundstage-for-airpods/blob/114143c27670275a5ef6336b91047d84b8a16eb5/README.md#L137) 和 MagicAAP 的 [当前签名说明](https://magicpods.app/magicaap/)限定了部署判断。以上作者测试不等于本项目已验证。

NTPods 现有 IPC 确实提供 `SetAnc` 和状态消息；不过固定提交的 `main.rs` 第 968–970 行先保存目标、写入 `state.anc` 并执行 `push_state()`，第 971–972 行才尝试发送：没有 driver handle 时直接跳过，`drv.send(...)` 的结果也被丢弃。因此首条 IPC 状态即使到达，也可能对应尚未发出或已经发送失败的操作；它不能视为硬件确认。未来接入须明确等待后续实际 AAP 回包，并保留超时或失败状态，不能只相信乐观 UI 更新。[IPC 定义](https://github.com/arctumn/ntpods/blob/188e45df20259b616ce155be812086259d1a2488/windows/ipc/src/lib.rs#L122)、[先 push_state 后忽略发送结果的源码](https://github.com/arctumn/ntpods/blob/188e45df20259b616ce155be812086259d1a2488/windows/daemon/src/main.rs#L968)、[接收模式回包的处理](https://github.com/arctumn/ntpods/blob/188e45df20259b616ce155be812086259d1a2488/windows/daemon/src/main.rs#L1670)

## 能否在播放中切换且不打断续播

**正常、已建立的控制会话有保持音乐续播的架构可能。** NTPods 的 `SetAnc` 分支在已有 driver handle 上发送模式命令，没有在每次切换时重连音频；SoundStage 的驱动只附着 Apple 服务，并明确不替换 A2DP / HFP / AVRCP。由此推断可以把模式切换与 GluSpot 的音乐时钟、音量和多设备队列分开。[NTPods SetAnc](https://github.com/arctumn/ntpods/blob/188e45df20259b616ce155be812086259d1a2488/windows/daemon/src/main.rs#L958)、[SoundStage 驱动边界](https://github.com/capriqqw/soundstage-for-airpods/blob/114143c27670275a5ef6336b91047d84b8a16eb5/docs/ARCHITECTURE.md#L42)

但不能推断**首次连接、手机抢占、服务重新枚举、失联恢复**都不会中断。NTPods 中确实存在异常时音频路由重建；新连接通知也不应该被重复请求。macOS 的 [airpods-ctl 实测记录](https://github.com/BorgoGustavo/airpods-ctl/blob/b42f4e8a944d39fdd9fa25533ae262bf647ddb3b/README.md)甚至需要约 1.7 秒重连才能建立它的实验通道，且其 CLI 的 `set` 当时仍只写本地状态。这不能当成 Windows 必然中断的证据，却说明“命令存在”不足以证明无中断体验。

耳机模式切换自身会播放提示音；官方已说明这一行为，不能把提示音误报为 GluSpot 播放故障。[Apple 模式提示音](https://support.apple.com/en-us/108918)

## 若以后实施，建议采用的边界

这部分是建议，尚未获实现授权，本次不实施。

1. 优先核对已经安装、明确允许客户端访问的控制服务。以单个 daemon 拥有控制会话，GluSpot 使用其版本化 IPC；避免多个应用同时争用 AACP。服务不存在时显示不可用，不在后台安装驱动或改变系统启动配置。
2. 从真实 Bluetooth 设备身份、型号、固件与服务能力决定可选模式。发出操作后等待真实模式回读，并处理用户长按耳机造成的外部变化；失败恢复显示已确认状态。
3. ANC 操作不得选择独奏、取消同步、改变输出端点、调用 microphone / HFP、改音量或自动抢占 iPhone。故障不触发重建健康的 GluSpot 音频组。
4. 先只做降噪 / 通透 / 受支持代际的自适应模式。自适应强度、个性化音量、对话感知另行评估，避免和现有 DJ 压音及用户音量恢复叠加。
5. 在宣布可发布前，要求目标 AirPods 代际 / 固件、目标蓝牙适配器、播放中连续切换、外部柄操作、断连 / 重连、同步队列和既有第三方控制应用共存的实机证据。驱动需有明确的 Microsoft 可信分发路径，而不是把测试签名安装当普通功能启用。[Microsoft 签名政策](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/kernel-mode-code-signing-policy--windows-vista-and-later-)

LibrePods、NTPods、SoundStage 的相关代码是 GPL-3.0 / GPL-3.0-or-later。若未来复制、修改或分发，须审核代码来源和对应许可义务；进程分离不自动消除所分发组件的许可要求。此次只读研究没有引入第三方代码或二进制。

## 本次验证与未验证事项

- 已读 Apple 官方能力 / 非 Apple 配对资料、Microsoft L2CAP 和驱动签名资料。
- 已固定并读上述公开项目提交、README 和相关模式构造 / driver / daemon 源码；特别核对了真实模式回读、实验移植的未完成项，以及 macOS CLI 只写状态的限制。
- 已读 GluSpot 现有音频端点控制边界；没有改变任何生产文件。
- 报告 UTF-8 读取、Markdown 链接结构和尾随空白检查通过；本次仅新增文档，不需要运行音频测试。
- 未验证用户耳机的具体代际、固件、已安装控制服务或蓝牙适配器兼容性；未进行实体噪声模式切换、听感验证、驱动安装或系统安全设置修改。
