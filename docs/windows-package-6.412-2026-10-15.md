# GluSpot 6.412 Windows 打包 — 2026-10-15

本次按用户要求打包当前工作区，版本保持 6.412。包含前述 UI、搜索、封面导航、
歌词、播放队列与封面获取、Windows SMTC 和在线音乐改动。

## 产物

- 启动文件：`release/GluSpot-6.412/GluSpot.exe`。
- 完整目录：`release/GluSpot-6.412/`，EXE 必须与 `_internal` 保持在一起。
- 完整 ZIP：`release/GluSpot-6.412-2026-10-15-windows-x64.zip`。
- 构建记录：包内 `BUILD-INFO.json`，含版本、日期、EXE/native helper 哈希和源码模块哈希。
- 操作说明：包内 `使用说明.txt`。

这是未签名的 Windows x64 内部测试包，无需另装 Python 或 Node.js。
未更改版本号、用户设置、账户或音频设备选择，也未启动普通程序界面。

## 构建与修正

先运行 `build-exe.bat -NoPause`，完成环境检查、原生组件编译和 12 项原生测试、
发现库源码许可准备、PyInstaller 打包。原生测试耗时 69.93 秒。
当前工具链为 MSYS2 UCRT GCC 16.2.0；为本次静态链接产物补齐
`libgcc`、`libstdc++`、`crt` 和 `libwinpthread` 的 8 份许可。
`_internal/native/licenses/gcc-runtime/SOURCE.json` 记录实际编译器版本、
来源目录与逐文件 SHA-256。生成目录和许可证补充均未进入源码控制。

首次冻结验收发现清单仍要求已退休的 `local.audio_device_notice`，而应用与既有
`test_audio_device_surface_dispatch` 已明确移除该启动路径。现从验收清单删除此旧项，
保留实际音频面板、时钟和路由检查。没有恢复通知界面。
同时将 spec 已声明的 UIAutomation 依赖通过现有 PyInstaller
`collect_dynamic_libs` 接口收集，保持 `uiautomation/bin` 路径。
最终包包含两份 DLL；ctypes 裸文件名扫描仍有警告，实际包内路径与源文件一致。

修正后沿用已验证的原生组件与在线运行时，重新执行与批处理相同的最终打包步骤：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m PyInstaller --noconfirm --clean --workpath build/pyinstaller --distpath release GluSpot.spec
```

## 验证

- 版本资源：文件／产品版本均为 `6.412.0.0`，产品名 `GluSpot`。
- `tests.test_audio_package_check`、`tests.test_audio_device_surface_dispatch`、
  `tests.test_branding` 共 **32 项通过**。
- 实际运行本次 EXE 的 `--audio-package-check <报告绝对路径>`，
  **退出码 0，3.593 秒**。11 个实际模块、7 个依赖、设备枚举、mDNS 浏览、
  250 毫秒本地采集、group 与多输出参数校验均通过。
- **68 项包内容检查通过**：17 个关键源码模块与 EXE 中编译代码一致；
  原生 helper 与所有 native notices 哈希一致；Qt Windows/SVG、字体图标、
  ToJyutping、SMTC 模块、可替换发现库及其精确源码包齐全；不存在冲突的宿主 ICU。
- Node、锁文件、Node 许可与来源清单哈希一致；网易云 4.41.1、QQ 3.1.4、
  酷狗 1.6.0 的实际包内 `prepare_login` 离线加载均成功。
  测试缓存写入独立 `.runtime` 目录，没有读取用户账户。
- 打包前应用源码已通过 **4,879 项全套测试，278.573 秒**。
  本次仅调整打包依赖与诊断模块清单；修正后运行上述 32 项针对性测试，未重复全套。
- `git diff --check` 通过；运行时产物、日志、临时脚本与压缩包保持忽略状态。

ZIP 包含完整目录的 **8,391 个文件**，逐项 CRC 校验通过。
ZIP 为 **198,312,387 字节**（约 189.1 MiB），解压目录为 466,445,089 字节。
SHA-256 同时保存在 ZIP 旁的 `.zip.sha256` 文件中：

```text
ZIP  717a7ad33698be993bf5fade81caa665b4aa7ce0c85744ab31a0a64fab2fe8b2
EXE  8485900960a2a953702822d32637c831318b336b2cb479fa3cd029e068e8a5e2
```

## 证据与边界

最终构建日志：`logs/package-final-build-6.412-2026-10-15.log`。
针对性测试：`logs/package-final-focused-6.412-2026-10-15.log`。
冻结验收：`logs/audio-package-6.412-2026-10-15.json`。
内容校验：`logs/package-contents-6.412-2026-10-15.json`。
归档清单：`logs/package-artifact-proof-6.412-2026-10-15.json`。
首次失败验收另外保留为 `logs/audio-package-initial-6.412-2026-10-15.json`。

冻结验收未创建 Qt 应用或访问设置，没有更改默认端点、音量或静音状态，
没有保存采集音频或启动接收端会话。离线平台加载不等同于真实登录或播放验证；
本次没有进行普通窗口启动、物理耳机按键或长时间硬件收听验收。
