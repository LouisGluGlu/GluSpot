# 歌词改进版 Windows 打包（2026-10-19）

产品版本保持 **GluSpot 6.412**。本轮将[署名识别、可靠间奏与歌词来源扩展](lyric-pipeline-2026-10-19.md)打入现代 Windows x64 安装程序和便携包。适用 Windows 10 1809 或更新版本／Windows 11；没有重建 Win7 包。

## 打包前修正

发行复核发现固定版本 QQ SDK `3.1.4` 的 HTTP client 只有 `request`／`post`，新 QRC 路径的备用 LRC 调用了不存在的 `get`。已改用实际 `request(config)` 接口，测试替身与真实 SDK 接口对齐，并新增本地回环测试，覆盖 QRC 缺失与损坏后的 LRC 回退。没有增加依赖或改动 SDK。

修正后的完整原生 Windows Python 回归通过 **5443 项／287.814 秒**。打包前后核对 **421 个源码和构建输入**，没有发生变化。旧发布目录、日期安装程序、ZIP 和校验文件均保留；当前已安装的 `D:\Apps\GluSpot\GluSpot.exe` 没有被停止或覆盖。

## 产物

- [安装程序](../release/GluSpot-6.412-2026-10-19-lyrics-Setup-x64.exe)
- [便携 ZIP](../release/GluSpot-6.412-2026-10-19-lyrics-windows-x64.zip)
- 通用安装入口：`release/GluSpot-6.412-Setup-x64.exe`，与日期安装程序字节一致。
- 解压目录：`release/GluSpot-6.412/`，附带 `BUILD-INFO.json` 和 `使用说明.txt`。

安装程序和便携 ZIP 均带 `.sha256` 校验文件。完整文件清单、大小与 SHA-256 记录在 `logs/lyric-release-artifacts-2026-10-19.json`。

| 产物 | 字节数 | SHA-256 |
| --- | ---: | --- |
| 日期安装程序 | 161741594 | `bad205bc8bf3aaeb8868abab6015996703223ffdeb861955093f22eb6ef37c66` |
| 日期便携 ZIP | 212960983 | `de886ce8a5bfd8f67b5f93e9c0fd99f4115f4b9d2e8969c313526d588b4f9640` |
| 包内 GluSpot.exe | 10456228 | `9d5cc62f53f941efd6ac270513712a664f20a7a58b49174b0cd722362b8acefa` |

## 验证

| 项目 | 结果 |
| --- | --- |
| 最终完整 Python 回归 | 5443 项通过，287.814 秒，无跳过或 Qt 回调异常 |
| 本轮原生音频 helper 构建与 CTest | 12／12 通过，69.49 秒 |
| PyInstaller 构建 | 成功，190.805 秒 |
| 包内源码、资源、许可与污染审计 | 274／274 通过，106 个冻结源码模块与当前源代码一致，收据前 8397 文件 |
| 提取 PYZ 后的隔离歌词执行 | DES 标准向量、QRC 解密、TTML 原文／译文／罗马音／真实时间、署名与正文保护、间奏边界／时钟／幂等检查通过；无 Qt、网络、设置或音频 |
| 新 EXE `--audio-package-check` | 退出 0；未访问设置、改默认端点、建立接收会话、保存音频或创建 Qt application |
| 包内在线 runtime | 14／14 通过，依赖字节一致及三个平台的隔离 `prepare_login` 预加载 |
| 包内真实 QQ SDK 回环与冻结解析 | 10／10 通过，覆盖 QRC 缺失／损坏回退、真实解密和 TTML 时间保留；无外部请求或账号 |
| Inno Setup 7.1.0 编译 | 成功，174.531 秒 |
| 隔离安装、覆盖、回滚与卸载 | 10／10 场景通过，仅生成的惰性测试进程与独立注册表标识 |

安装测试包括新装、原目录升级、同版／低版静默拒绝、占用拒绝、只退出目标目录实例后覆盖重启、高版保留状态、安装中断恢复、缺失 EXE 修复、隔离发现及完整拥有状态卸载。测试安装器的 SHA-256 仅用于识别 fixture，不作为分发安装器哈希。

两次首次验证失败的记录均保留：安装 smoke 未显式指定当前 Inno compiler 路径时未能启动 compiler；指定已安装的 7.1.0 后十场景通过。包审计初始离线样例没有提供未知署名扩展所需的两个明确字段，已对齐既有回归样例后重新通过；生产源码无需因该样例修改，原 106 个冻结模块一致性检查始终通过。

最终归档的 **8399 个文件**全部通过 ZIP 路径安全、无重复条目、CRC、逐文件 SHA-256、与完整 payload 的文件集合及前后字节一致性检查；日期安装程序与通用入口相同，421 个源码／构建输入和历史日期产物未变。最终数量与哈希同时记录在完整发布收据。

## 证据

- `.runtime/lyric-release-source-freeze-2026-10-19.json`
- `logs/lyric-release-final-full-2026-10-19.log`
- `logs/lyric-release-preservation-2026-10-19.json`
- `logs/lyric-release-preflight-2026-10-19.log`
- `logs/lyric-release-native-build-2026-10-19.log`
- `logs/lyric-release-pyinstaller-2026-10-19.log`
- `.runtime/lyric-package-audit-final-2026-10-19/result.json`
- `logs/lyric-release-frozen-acceptance-2026-10-19.json`
- `logs/lyric-release-online-preload-2026-10-19.json`
- `logs/lyric-packaged-qrc-fallback-2026-10-19.json`
- `logs/lyric-release-installer-build-2026-10-19.log`
- `logs/lyric-release-installer-smoke-final-2026-10-19.log`
- `.runtime/installer-acceptance-2026-10-16-8d595d35/result.json`
- `logs/lyric-release-archive-2026-10-19.log`
- `logs/lyric-release-artifacts-2026-10-19.json`

## 使用与边界

便携版完整解压后运行 `GluSpot.exe`，保留旁边的 `_internal`。配置、账号和缓存默认保存在程序旁的 `GluSpot` 目录，新目录首次启动仍走首次选择。注册安装应在原目录升级；本次仍是 6.412，同版覆盖需要交互确认，静默模式会拒绝未经确认的同／低版覆盖。完整卸载会删除安装拥有的配置、账号和缓存。

这是未签名内部测试包。源码渲染、公开只读取词、包内回环和安装 fixture 验证不能证明所有真实账号歌曲均可取词、实体屏幕动画流畅或长时间后台播放／HomePod 听感同步；这些仍未验收。没有打包参考项目、AMLL 数据库或抓取的歌词；外来歌词仍遵循原提供方条款。
