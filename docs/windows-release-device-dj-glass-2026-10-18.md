# GluSpot 6.412 设备、私人播报与玻璃材质安装包 — 2026-10-18

本轮设备控制、HomePod 播放／暂停返回通道、私人漫游首次播报与 Liquid Glass 调整已打入安装程序和便携包，版本保持 **6.412**。完整行为和源码验证见[本轮调整报告](device-touch-dj-and-glass-2026-10-18.md)。

本包适用于 **Windows 10 1809 或更新版本 / Windows 11，x64**。本次未更新独立的 Windows 7 兼容测试包。

## 产物

以下文件位于当前 `292c` 工作树的 `release/`：

| 文件 | 字节 | SHA-256 |
| --- | ---: | --- |
| `GluSpot-6.412-2026-10-18-device-dj-glass-Setup-x64.exe` | 161689238 | `f987eb6c951adfd0ec12aab928da6c2e58783185fd1d5b49b27240c4f64681e2` |
| `GluSpot-6.412-2026-10-18-device-dj-glass-windows-x64.zip` | 212894442 | `b3b86f2d5db52993bd1ef0b09deb00010cefae606d4e48c821aecc6a82903a16` |

安装程序约 **154.2 MiB**，便携 ZIP 约 **203.0 MiB**，各附 `.sha256` 校验文件。当前通用文件名 `GluSpot-6.412-Setup-x64.exe` 与本轮带日期安装包逐字节一致。新 `GluSpot-6.412/GluSpot.exe` 为 10402100 字节，SHA-256 为 `58b28add039772b782ca479c9e212788d7dc12a7c13883b890bb2075d65e32f7`。

ZIP 含 **8399 个唯一文件条目 / 484259560 字节解压内容**，统一根目录；完整 CRC、每个文件的 SHA-256 和归档前后 payload 一致性均通过。便携目录附 `BUILD-INFO.json` 与 `使用说明.txt`。

同日较早的 `installer-library` 安装包、便携 ZIP 及其校验文件仍在当前 `292c/release/`。原通用程序目录已保留为 `GluSpot-6.412-2026-10-18-installer-library-payload`，旧 EXE 哈希匹配；此前 `de57` 工作树中的发布包继续保留。

## 本轮包含的调整

1. 同步组中点击已选设备的图标，将该设备移出同步队列；名称静音与独立音量保留。移除全组“取消同步播放”，保留单 AirPlay 的停止操作。快照与按住手势校验防止过期移除变成重加或单独播放。
2. HomePod 播放／暂停命令经现有加密 AirPlay 2 事件通道回到程序内播放器，覆盖直接 PCM 的单设备和同步组，保留会话、成员、序号去重和过期校验。该协议路径已实现，实体触控仍待实机验收。
3. 开启私人播报后首次进入私人漫游，优先真实官方开场播报及其匹配歌曲，再保留此前普通曲目。首次请求尚未返回时修改播报设置，会重新请求并隔离旧回复；服务未提供播报时不虚构。
4. 窗口／全屏按钮、连接胶囊、收藏面板、私人播报控件与设备滑条共享透出背景颜色的玻璃材质。播放图形仅在原有轮廓内着材质，不增加音乐控件或外包底板。
5. 基础设置新增持久化的 **播放控件玻璃浓度**，单滑块 0–100%，默认 50%。按用户最终要求：**按钮越不透明，映射的背景模糊越强；越透明，映射的背景模糊越弱**。已打开面板立即更新，文字与图标边缘保持清晰，黑白颜色按整个场景统一决定。

## 构建与验收

- 沿用本轮最终 **5252 项完整源码回归 / 311.016 秒**，无跳过、失败或 Qt 回调异常。完整回归冻结的 **334 个源码／测试哈希**均与打包源码一致。另保存并在打包完成后复核 **391 个 Python 源码文件**，没有应用源码变化，因此未重复完整回归。
- 本工作树 Python **3.12.10** 环境与完整回归依赖一致，包括 PySide6 **6.12.0**、Pillow **12.3.0** 和 PyInstaller **6.22.3**。按 `build-exe.bat` 的阶段运行环境检查、原生构建、许可证准备和当前 `GluSpot.spec` 的 PyInstaller 构建。
- 本轮原生构建通过 **12 组测试 / 98.97 秒**。打包的 `GluSpotAirPlay.exe` 与当前构建 helper 哈希一致，SHA-256 为 `3b1d40b1d89dc62c73ed15e6132de06b1870309269b5d3d9aedef6fe81964e06`；原生来源和 GCC 运行时许可证收据一并审计。
- 静态包审计 **248/248 项通过**，**102 个源码模块及入口**与当前源码编译结果一致。Qt、helper、字体、SVG、许可证和来源收据齐全；添加便携说明前的 8397 个文件未混入用户账号、配置、缓存或测试数据。
- 实际新 EXE 运行 `--audio-package-check`，**退出 0，诊断内部耗时 3.359 秒**。该模式未创建 Qt 应用或读取用户设置，未改变默认音频端点、保存音频或启动接收器会话，包含 2 秒 mDNS 浏览和 250 毫秒采集探测。
- 包内 Node 和在线音乐桥接通过 **14 项离线检查**。**7894 个 staging 依赖／许可文件**与包内对应文件逐文件 SHA-256 一致，另有三份项目适配器许可；网易云、QQ、酷狗真实 `prepare_login` 模块预加载成功。三个平台分别使用隔离临时目录，无用户凭据。
- Inno Setup 7 编译成功，**117.656 秒**，编译日志中的 Setup 哈希与分发文件一致。此前 **71 项安装动画、字体恢复和首帧交接原生断言**的证据复用，当前安装模板哈希均与证据匹配；40 帧向内扩展留白字结尾及 24 像素透明阴影边距的素材清单通过核对。
- 本轮从当前安装模板生成的隔离安装器通过 **10 类场景**：首次安装、原目录升级、同／低版静默拒绝、文件占用、精确目录实例替换重启、配置保留、失败回滚、缺失 EXE 修复、安装发现及完整卸载。使用 UUID 注册表项和惰性模拟程序，不是对真实应用或用户安装的操作。
- 最后再次核对 **7 份产物哈希、2 份新日期校验文件、2 份旧日期发布包、保留的旧 EXE 与 391 个源码哈希**，全部通过。`git diff --check` 通过。

## 使用、覆盖与卸载

便携版解压完整 `GluSpot-6.412` 文件夹，运行 `GluSpot.exe`，保持 EXE 与 `_internal` 在一起。已包含在线音乐运行时，无需另装 Python 或 Node.js。配置、账号和缓存保存在程序旁的 `GluSpot` 子目录。新目录不携带旧配置，首次运行会显示播放方式选择，两项默认开启。

安装程序支持目录选择和 Windows 卸载入口。已注册安装沿原目录覆盖，保留配置、账号和缓存；同版或低版需确认覆盖，静默安装拒绝未经确认的覆盖。替换重启只处理目标安装目录中的运行实例，不影响其他目录副本。完整卸载会清除本安装拥有的程序和目录内状态，保留无关文件；需要的配置和账号信息应先保存。

`BUILD-INFO.json` 和 `使用说明.txt` 随便携 ZIP 提供，当前安装器分发 EXE 与 `_internal` 树。

## 证据与限制

- 完整源码回归：`logs/device-touch-dj-glass-final-full-2026-10-18.log`；原回归哈希：`.runtime/glass-device-2026-10-18/source-hashes-before-final-full.json`。
- 打包源码冻结及依赖对齐：`.runtime/device-glass-source-freeze-2026-10-18.json`、`.runtime/device-glass-dependency-verification-2026-10-18.json`。
- 构建：`logs/device-glass-build-preflight-2026-10-18.log`、`logs/device-glass-native-build-2026-10-18.log`、`logs/device-glass-audio-licenses-2026-10-18.log`、`logs/device-glass-pyinstaller-build-2026-10-18.log`。
- 包审计和冻结验收：`.runtime/device-glass-package-audit-2026-10-18/result.json`、`logs/device-glass-frozen-acceptance-2026-10-18.json`。
- 在线模块：`logs/device-glass-online-preload-2026-10-18.json`。
- 安装器：`logs/device-glass-installer-build-2026-10-18.log`、`logs/device-glass-installer-smoke-2026-10-18.log`、`.runtime/installer-acceptance-2026-10-16-0befd43c/result.json`。
- 复用的 71 项安装原生证明：`.runtime/installer-progress-48d92274/evidence.json`、`.runtime/installer-fonts-3bfd9a9c/evidence.json`、`.runtime/installer-handoff-6c3282e1/evidence.json`；素材：`build/installer-player-background/COMPACT.json`。
- 完整分发、逐文件归档和最终复核：`logs/device-glass-release-artifacts-2026-10-18.json`、`logs/device-glass-archive-2026-10-18.log`、`logs/device-glass-final-artifact-check-2026-10-18.json`；便携收据：`release/GluSpot-6.412/BUILD-INFO.json`。

本包未签名，仅供内部测试。HomePod 触控暂停／继续已实现协议通道并通过模拟接收器测试，**实体 HomePod 的点击、长暂停、单／多设备切换、听感同步及无线中断仍未实机验收；HomePod 原生队列读取、写入或歌曲注入未实现**，详见[协议调查](homepod-remote-control-research-2026-10-18.md)。离线模块预加载不等同于真实账号登录；自动恢复、Finder／Explorer 隐藏和渲染夹具也不等同于实际断网恢复或物理屏幕动画流畅度。本次未刷新 Win7 包。
