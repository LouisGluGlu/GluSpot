# GluSpot 6.412 安装程序与便携包 — 2026-10-17

按用户要求将当前工作区打包为 Windows x64 安装程序和便携 ZIP，版本仍为 6.412。
本轮包含之前尚未进入安装包的推荐封面、岛模式与自动位置、Dock64／Finder 检测及
HomePod 切歌竞态修复。具体行为与实机验证限制见
[Dock64／Finder 与切歌报告](dock64-finder-and-homepod-switch-2026-10-17.md)、
[自动位置规则](automatic-island-placement-2026-10-17.md)及
[推荐封面与岛模式](recommendation-and-island-modes-2026-10-17.md)。

## 产物

| 文件（位于 `release/`） | 字节 | SHA-256 |
| --- | ---: | --- |
| `GluSpot-6.412-2026-10-17-dock64-homepod-Setup-x64.exe` | 161,298,168 | `7929526289d02fba42a7059f021bdb5dc1023f409368995de792f2b444d6264b` |
| `GluSpot-6.412-2026-10-17-dock64-homepod-windows-x64.zip` | 212,835,177 | `a5280d0fd0f5c98361a01e4f0c26c0b8f7a7752c2aee49ec0a0fabd25df66460` |
| `GluSpot-6.412/GluSpot.exe` | 10,347,318 | `d1a81d1cac3868fb37df75c0310f69ab1a5328050ecdcfef6e9ba1dd45545c58` |

canonical `GluSpot-6.412-Setup-x64.exe` 与带日期的安装器逐字节一致；两个安装器和 ZIP
均带 SHA-256 校验文件。ZIP 共 8399 个唯一文件条目，完整 CRC 检查通过，解压后
484,191,592 字节。包内 `BUILD-INFO.json` 记录当前源码与构建证据，`使用说明.txt`
说明首次使用、覆盖升级及配置位置。归档证明为
`logs/oct17-dock64-homepod-release-artifacts.json`。

## 构建与验收

- 当前源码已通过 5168 项原生 Windows Python 回归，303.421 秒，无跳过或 Qt 回调异常；
  打包前后校验 352 个 Python 文件的 SHA-256，沿用同一验证源码。
- `build-exe.bat -NoPause` 成功；12 组原生测试全部通过，81.27 秒。
- 包内容审计 246/246 项通过，100 个封装源码模块及入口与当前源码编译结果一致。
  Qt、原生 helper、字体、SVG、在线音乐 Node 运行时、依赖许可证与来源哈希齐全；
  8397 个初始 payload 文件未混入账号、配置、缓存或测试数据。
- 实际新 EXE 的 `--audio-package-check` 退出 0；未创建 Qt 应用、读取设置、保存音频、
  选择系统端点或启动接收器会话。此诊断包含 2 秒 mDNS 浏览和 250 ms 本地采集探测。
- 安装器通过 10 类隔离原生场景：首次安装、原目录升级、同／低版静默拒绝、文件占用、
  精确目录运行实例替换重启、配置保留、失败回滚、缺失 EXE 修复、安装发现和完整卸载。
  使用独立 UUID 注册表项及惰性模拟程序，未安装真实 payload 或操作用户实例。
- 原生进度条在 DPR 1/2 通过 18 项绘制检查；首帧交接通过 16 项跨进程协议检查。
  安装器沿用已验证的双倍密度素材、底部圆角进度条和首帧交接机制。

## 使用与覆盖

便携包解压整个 `GluSpot-6.412` 文件夹后运行 `GluSpot.exe`，保留同目录的 `_internal`。
不需要另外安装 Python 或 Node.js。新目录不携带旧配置，会进入首次使用选择；配置、
账号与缓存写入程序目录内的 `GluSpot` 子目录。

安装程序可选择目录并注册 Windows 卸载入口。已注册安装使用原目录覆盖，保留配置、
账号和缓存；同版或低版弹出覆盖确认，静默安装拒绝未经确认的覆盖。更新只结束目标
安装目录的实例，成功后重启一次。完整卸载清除本安装的程序及 GluSpot 状态，保留
非程序所有的用户文件。

旧便携目录及 canonical 安装器已备份到
`release/before-dock64-homepod-2026-10-17/`；此前带日期的安装包与 ZIP 均保留。

## 构建证据

- 构建：`logs/oct17-dock64-homepod-build.log`。
- 包内容：`.runtime/release-audit-2026-10-17-dock64-homepod/result.json`。
- 冻结诊断：`logs/oct17-dock64-homepod-frozen-acceptance.json`。
- 安装编译：`logs/oct17-dock64-homepod-setup-build.log`。
- 安装策略：`logs/oct17-dock64-homepod-installer-smoke.log`。
- 进度与交接：`.runtime/oct17-dock64-homepod-installer-{progress,handoff}/evidence.json`。
- 源码回归及冻结：`logs/oct17-dock64-homepod-full.log`、
  `.runtime/oct17-dock64-homepod-source-freeze.json`。

这些仍是未签名内部测试包。诊断和模拟所属栏、接收器验证不能替代真实 HomePod
听感、无线稳定性、长期播放或物理 Finder／任务栏隐藏流畅度验收。
