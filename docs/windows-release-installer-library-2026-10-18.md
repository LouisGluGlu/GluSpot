# GluSpot 6.412 安装程序与便携包 — 2026-10-18

按用户要求，将本轮安装动画、首次使用位置、音乐库骨架与自动恢复、立即进入播放窗口及往返导航修复打入 Windows x64 安装程序和便携包，版本保持 **6.412**。具体行为及离线 UI 验证见[本轮调整报告](installer-library-followup-2026-10-18.md)。

本包适用于 **Windows 10 1809 或更新版本 / Windows 11，x64**，与之前的 Windows 7 兼容测试版分开。

## 产物

文件均在当前工作树的 `release/`：

| 文件 | 字节 | SHA-256 |
| --- | ---: | --- |
| `GluSpot-6.412-2026-10-18-installer-library-Setup-x64.exe` | 161671735 | `4b80d845f0c61500c49f0fbdb5c6af53c0b5c527642d631dad9f8ed8faacd7aa` |
| `GluSpot-6.412-2026-10-18-installer-library-windows-x64.zip` | 212883278 | `4cd52ac8658a385f590cfb7fe00b14fbc7b06d40202fa33e125c5a07f12b025b` |
| `GluSpot-6.412/GluSpot.exe` | 10393112 | `0cf45fe0a373328231e2c6c8edea18aa5c369704520c1a0ec168ffaf5564d52c` |

安装包约 **154.2 MiB**，便携 ZIP 约 **203.0 MiB**。canonical `GluSpot-6.412-Setup-x64.exe` 与带日期的安装包逐字节一致。三个分发文件均附有 `.sha256` 校验文件。

ZIP 含 **8399 个唯一文件条目 / 484240516 字节解压内容**，统一根目录、完整 CRC、构建记录及每个文件的 SHA-256 均通过验证，归档前后 payload 一致。完整目录附 `BUILD-INFO.json` 和中文说明。

## 构建与验收

- 沿用刚完成的 **5223 项完整源码回归 / 300.816 秒**。打包前保存并在归档时复核 390 个 Python 源码文件的哈希；打包期间未修改应用源码，未重复全量回归。
- 为当前工作树建立独立 Python 3.12.10 环境，依赖版本与完整回归使用的环境逐项一致，包含 PySide6 6.12.0、PyInstaller 6.22.3 与 Pillow 12.3.0。环境检查通过。
- 从当前源码重新编译原生音频组件，**12 组原生测试全部通过 / 70.00 秒**；8 份 GCC 运行时许可与现用编译器 16.2.0 的收据一致。随后按 `build-exe.bat` 的阶段执行许可证准备及当前 `GluSpot.spec` 的 PyInstaller 构建。
- 包内容审计 **248/248 项通过**，**102 个源码模块及入口**与当前源码编译结果一致。Qt、原生 helper、字体、SVG、许可证及来源收据齐全；初始 payload 8397 个文件中未混入用户账号、配置、缓存或测试数据。
- 实际新 EXE 的 `--audio-package-check` **退出 0 / 3.187 秒**。该诊断未创建 Qt 应用或读取设置，未改变默认音频端点、保存音频或启动接收器会话；包含 2 秒 mDNS 浏览和 250 毫秒采集探测。
- 包内 Node 及在线音乐桥接通过 **14 项检查**：7894 个依赖与许可文件和已验证 staging 逐文件 SHA-256 一致；网易云、QQ、酷狗的真实 `prepare_login` 模块均离线预加载成功。每个平台使用独立临时目录和隔离环境，无用户凭据。
- Inno Setup 7 编译成功，**167.922 秒**。当前安装模板和素材保留本轮三横条配色、24 像素透明阴影边距及 40 帧向内扩展留白字结尾；此前 **71 项原生视觉、字体和交接断言**的模板哈希与当前文件一致。
- 当前安装器通过 **10 类隔离原生场景**：首次安装、原目录升级、同／低版静默拒绝、文件占用、精确目录实例替换重启、配置保留、失败回滚、缺失 EXE 修复、安装发现及完整卸载。使用 UUID 注册表项和惰性模拟程序，未安装真实应用或操作用户实例。

## 使用与覆盖

便携版解压完整 `GluSpot-6.412` 文件夹后运行 `GluSpot.exe`，保留旁边的 `_internal`。无需另装 Python 或 Node.js。新目录不携带旧配置，首次启动会显示播放方式选择；配置、账号与缓存保存在程序目录中的 `GluSpot` 子目录。

安装程序支持目录选择和 Windows 卸载入口。已注册安装在原目录覆盖，保留配置、账号和缓存；**同版需要确认覆盖**，静默安装拒绝未经确认的覆盖。只处理目标安装目录中的运行实例，不影响其他副本。完整卸载清除本安装拥有的程序和状态，保留无关文件。

`BUILD-INFO.json` 与 `使用说明.txt` 随便携 ZIP 提供。当前安装器仅分发 EXE 和 `_internal` 树。此前带日期的安装包、ZIP 及旧程序目录仍保留在 `de57` 工作树的 `release/`，本轮产物位于当前 `292c` 工作树的 `release/`。

## 证据与限制

- 环境与构建：`logs/oct18-package-environment.log`、`logs/oct18-python-version-alignment.log`、`logs/oct18-build-preflight.log`、`logs/oct18-native-build.log`、`logs/oct18-pyinstaller-build.log`。
- 源冻结与依赖：`.runtime/oct18-package-source-freeze.json`、`.runtime/oct18-build-dependency-verification.json`。
- 包审计：`.runtime/release-audit-2026-10-18/result.json`。
- 冻结验收：`logs/oct18-frozen-acceptance.json`。
- 在线运行时：`logs/oct18-online-preload.json`。
- 安装器：`logs/oct18-installer-build.log`、`logs/oct18-installer-smoke.log`。
- 最终文件与逐文件归档证明：`logs/oct18-release-artifacts.json`。

这些是未签名内部测试包。离线模块加载不等同于真实账号登录，自动恢复夹具不等同于实际断网恢复；实体 HomePod、耳机操作、长时间播放和物理屏幕上的动画流畅度仍需实机复测。本轮未更新独立 Win7 包。
