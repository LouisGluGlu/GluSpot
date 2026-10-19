# 默认窗口、Free 可见性与 Windows 包 — 2026-10-19

版本保持 **6.412**。本轮处理默认窗口比例、压缩转 Free 后的隐藏状态，
并将片头制作信息清理固定开启。**最终完整回归 5310 项／276.895 秒及
DPR 1／2 各 542 项相关套件均通过，397 个源码哈希保持一致。原生构建、
PyInstaller、249 项包审计、实际 EXE 冻结诊断与 14 项在线 runtime
检查通过。安装器与便携 ZIP 已完成；十项隔离安装场景、8399 个归档
条目的 CRC 和逐文件 SHA-256 均通过，日期 Setup 与通用版字节一致。**

适用平台为 **Windows 10 1809 或更新版本／Windows 11，x64**，不更新
独立 Windows 7 兼容包。实际打包时刻为
**2026-10-19 10:30:59.735016 +08:00**，与 `BUILD-INFO.json` 和最终
release receipt 一致。

## 本轮目标与接口

- 默认窗口优先采用 **960 × 540、16:9**。可用工作区较小时按比例收缩，
  必要时采用现有 4:3 候选；若工作区容纳不了比例与最小高度，则以界面
  完整留在可用范围内为准。手动窗口尺寸、宽高可调与 **330 × 400**
  逻辑像素最小边界保留，不将首次默认比例强加给每次拖动或恢复窗口。
- 压缩转 Free 时，清理从沉浸栏自动隐藏／外部全屏跟随继承的缓存，
  规范化最小化状态，并在设置变更回调前完成可见状态恢复。继续使用
  缓存预览的目标位置，保留当前歌曲、播放位置与音频输出选择。
- 设置中的片头制作信息清理入口移除，
  `v2_music_intro_cleanup_enabled` 固定为 `true`。旧配置中的 `false`
  迁移为 `true`，播放页与岛继续使用既有信息清理／占位和倒计时规则。
- 保留上轮宽高缩放、白色边缘渐变内收、最小尺寸后半宽／半高收拢、
  不足阈值回弹与缓存落点，以及此前控件材质、蓝色音量和设备成员修正。
  玻璃浓度仍为越不透明则背景模糊越强，不新增音乐控件或悬浮展开。

## 根因、修正与证据范围

### 默认窗口比例

默认窗口变矮有明确的代码原因：原路径先求出按比例约束的窗口尺寸，
返回矩形时却把高度重新写成 `400`，覆盖了已计算的高度。修正后返回
实际的 `size.width()` 与 `size.height()`，首次打开优先为 **960 × 540**。
最小可调边界与首次默认尺寸各自保留；已保存的手动窗口不会被重置为
默认比例。

`tests/test_music_window_default.py` 定义五项对应回归：普通与大工作区的
首次 960 × 540、较小横屏的 16:9、窄工作区的 4:3、极小工作区完整
可见，以及手动尺寸重新打开后保持。工作区包含负坐标副屏；居中按
工作区原点计算，避免 `QRect.center()` 的整数截断额外偏移。相关回归
已随最终 DPR 1／2 各 542 项扩展套件与 5310 项完整回归通过。

### 压缩转 Free 后可见性

已复现的一条消失路径是：播放器隐藏期间，岛仍持有“外部全屏”的旧
探测结果；压缩完成虽然选中 Free，下一次 tick 却能用旧结果重新选中
隐藏的 Finder／Tasker，并延续自动放置状态。**这证明了可触发同类
现象的继承缓存路径；用户已安装桌面上那次消失的唯一确切原因仍未
证实。** 修复同时防护残留的栏隐藏透明度、鼠标穿透／遮罩和原生最小化
状态，不把这些状态未经取证地统称为已确认根因。

完成压缩时，既有入口清除继承的全屏探测与放置缓存、恢复栏可见值，
在最终测量与放置前将已最小化的岛恢复为普通可见窗口，再按缓存
预览对齐，最后发送设置变更通知。这样即使设置观察者抛错，可见
交接已完成，播放器预览也能释放。明确的新外部全屏事件仍由正常
监测处理；此处重置的是交接前缓存。

独立原生桌面的补充探针还复现了另一条落点错误：先前最小化且随后
隐藏的 QWidget，其 `pos()` 已变，但未显示前的 `mapToGlobal()` 仍沿用
旧原生原点；在此状态下做两轮中心对齐会过量移动。探针推翻了最初
“恢复窗口覆盖最终几何”的推测，修正针对的是**显示后的原生坐标
同步**。最终路径在 `_restoring_position` 保护内先恢复／显示，再测量
和对齐；`_center_on_screen()` 保留外层保护值，结束对齐后再收定显示
弹簧。没有在 commit 处额外调用 `setWindowState()` 清除最小化位。

`tests/test_music_compact_visibility.py` 定义以下 **六个真实 QWidget
生命周期场景**。它们保留生产 `show`／`hide`、窗口事件、绘制和 Qt
动画完成路径，使用离屏 QWidget 与惰性 provider；没有启动已安装
程序、账号登录或真实音频输出。最终 DPR 1／2 扩展相关套件已各通过
542 项，详见下述记录。

| 场景 | 实际检查 |
| --- | --- |
| 普通压缩交接 | 等待真实 `animation.finished`，检查交接后及首次 tick 后的 Free 可见性与预览落点 |
| 继承外部全屏缓存 | 完成前注入旧全屏结果，首次 tick 必须重新探测且不被旧缓存拉回隐藏栏 |
| 继承栏淡出与输入遮罩 | 注入零透明度、淡出速度、鼠标穿透和小遮罩，完成后恢复完整可见与可交互 |
| 先前已最小化的岛 | 经真实最小化再打开播放器，完成压缩后恢复普通可见岛，首次 tick 保持 |
| 连续三轮打开／压缩 | 每轮经真实动画及事件队列交接，落点和音频状态持续保持 |
| 设置回调失败 | 观察者抛错前已完成可见交接；预览快照和 committing 状态在失败后释放 |

共同断言包括：播放器已隐藏、岛可见且未最小化、Free 已生效、导航
未阻断、透明度接近 1、鼠标穿透关闭、遮罩清除，以及真实绘制中心
具有可见 alpha。落点以已缓存预览为准，中心偏差按设备像素计量。
六项场景还比较曲目标识、播放位置和播放状态，并要求 provider 没有
收到播放命令或 seek，因而不通过重播来掩盖界面交接。

补充的 `tests/test_music_free_island_handoff.py` 覆盖 DPR 1／2 缓存
落点优先、屏边约束、窄屏、上下堆叠屏幕与预览失败恢复。这组几何／
状态测试会 mock 部分窗口显示调用，不能替代上表的真实生命周期
场景。两组证据在最终报告中分别记录，避免把几何通过等同于可见。

相同六项场景另在新建的**非活动 Windows 桌面**执行，移除测试的
`WA_DontShowOnScreen` 属性，让原生 HWND 真正经历显示与最小化恢复。
最终独立新进程 **6／6 通过，3.612 秒，退出码 0**；14 个检查点均为
HWND 可见且未最小化，Qt 状态一致，Free、透明度与预览落点断言通过。
launcher 记录前后活动桌面均为 `Default`，未切换交互桌面，也未访问
用户实际安装的程序。

这一补充验证中，中间版本曾在设置观察者异常路径后发生访问冲突；
该次运行不算通过。修正恢复顺序和保护标志的嵌套后，上述最终六项由
独立新进程重新全部执行成功。中间失败仍保留为调查记录，最终日志
不能用于否认它曾发生。非活动桌面的 `GetPixel` 无法取得 DWM 合成
像素；保存的 widget grab 仅证明自身绘制，原生可见性证据来自 HWND
状态，尚不能证明用户物理屏幕上的最终合成、流畅度或安装现场的
精确成因。

### 片头制作信息清理

设置页控件、关联 setter 与搜索索引入口一并移除；兼容配置键保留，
默认值和固定默认集合将其锁定为 `true`。旧文件的 `false` 与异常值
迁移并保存为 `true`，后续配置写入不能重新关闭；岛的兼容 setter 与
导出快照也固定开启，播放页与岛均执行清理。

这次仅固定既有清理行为并收起入口，**既有倒计时规则保持**。没有
新增一种倒计时，也没有移除原有倒计时。对应回归位于
`tests/test_mac_preferences_shell.py`（入口／搜索项缺席、新旧配置与
不可关闭）和 `tests/test_simplified_settings_defaults.py`（固定值迁移
与持久化）；两项入口／迁移回归已随最终 DPR 1／2 扩展套件及完整
回归通过。

静态核对的版本值一致：`local/branding.py` 与 `GluSpot.spec` 为
`6.412`，`packaging/windows/version_info.txt` 的文件／产品版本为
`6.412.0.0`，与 README 一致；本轮不升版本。

## 旧版保留与构建前暂存

此前已发布的 `window-resize` 日期安装包、ZIP 及 `.sha256` 均已保留，
本轮使用新的 `window-visibility` 名称，不覆盖它们。`installer-library`
与 `device-dj-glass` 两组旧包、校验文件及旧 payload 继续保留；`de57`
工作树中的参考发布不变。

为给新构建腾出 canonical 路径，新 prepare 脚本已在**本轮完整 freeze、
完整回归及依赖验证均通过后**完成以下可恢复暂存；preservation 收据
于 **2026-10-19 10:21:00 +08:00** 记录成功：

| 原 canonical 路径 | 旧版保留目标 |
| --- | --- |
| `release/GluSpot-6.412` | `release/GluSpot-6.412-2026-10-19-window-resize-payload` |
| `release/GluSpot-6.412-Setup-x64.exe` | `.runtime/window-visibility-previous-canonical-2026-10-19/GluSpot-6.412-Setup-x64.exe` |
| `release/GluSpot-6.412-Setup-x64.exe.sha256` | `.runtime/window-visibility-previous-canonical-2026-10-19/GluSpot-6.412-Setup-x64.exe.sha256` |

暂存前核对已发布日期包的固定 SHA-256，并将当前完整 payload 与历史
release receipt／ZIP 逐文件比较；确认目的路径不存在后，才移动这三个
明确目标，写入新的 preservation 与 dependency 收据。保留过程没有
物理删除文件或静默覆盖目标。

本次实际验证并保留旧 `window-resize` payload 的 **8399 个文件**，
与原 ZIP 及历史逐文件清单一致。三组旧日期发布及三份旧 payload EXE
共 **9 个清单项**均通过核对。27 项 Python 依赖版本与准备环境相符，
7898 个在线运行时文件与既有验证 staging 一致；没有以网络登录或
真实播放来替代依赖核对。`build-exe.bat -CheckOnly` 环境预检通过。

上一 `window-compact` 版的 7 个目标仍位于
`.runtime/withdrawn-window-compact-2026-10-19`：自动审批审核策略阻止
物理删除，实际仅撤回移出发布目录，隔离目录仍占用空间。本轮不重试
该删除，也不将撤回表述为已删除。

## 分发产物

| 文件 | 字节 | SHA-256 |
| --- | ---: | --- |
| `release/GluSpot-6.412-2026-10-19-window-visibility-Setup-x64.exe` | 161699583 | `eaaf650b5662b1094c71f5c76e4b72b11499f746a89f41579112e6be05be97a7` |
| `release/GluSpot-6.412-2026-10-19-window-visibility-windows-x64.zip` | 212907772 | `62f694053fbbec7499cd1980d73d8c73f0e36299dd345eb54515de4e59d22ab2` |
| `release/GluSpot-6.412/GluSpot.exe` | 10415154 | `2b13fadfa2d742380b90046bc30fd0dfa7a74bc0b2581d06dd9d4aa124225b1c` |
| `release/GluSpot-6.412-Setup-x64.exe` | 161699583 | `eaaf650b5662b1094c71f5c76e4b72b11499f746a89f41579112e6be05be97a7` |

源码／资源审计、EXE 诊断、原生测试、在线运行时预加载与隔离安装器
场景已通过；最终归档 **8399 个唯一条目**全部通过 CRC 和逐文件
SHA-256，解压文件总量 **484276556 字节**。通用／日期 Setup 字节
一致，日期 Setup 与 ZIP 及通用 Setup 均有 `.sha256`。便携包包含
`BUILD-INFO.json` 和 `使用说明.txt`；当前安装器分发 EXE 与 `_internal`
树，不另装这两份便携收据。旧报告保留为历史证据。

## 本轮验证记录

前置相关 Qt 套件通过 **DPR 1：493 项／33.728 秒；DPR 2：493 项／
35.442 秒**，覆盖默认窗口、压缩交接、真实生命周期、清理迁移与相关
界面回归。随后首次完整回归执行 **5310 项／290.125 秒，14 项失败**：
13 项仍假设片头清理／倒计时关闭的旧断言，以及 1 项设置交通灯控件的
物理指针派发夹具。前者只更新与明确产品规则冲突的测试断言；后者
失败于第一次 group Enter 尚未送达，已改为离屏显式派发 group／child
Enter、Leave、Move，保留命中掩码与展开进度检查。具体是什么桌面条件
阻止了原物理事件派发仍未证实；改后的夹具不宣称覆盖 Windows 物理
指针路由。两类修正均未改动生产行为。

测试夹具调整后，最终扩展相关 Qt 套件重新通过：**DPR 1：542 项／
38.979 秒；DPR 2：542 项／41.129 秒**。独立原生桌面六项记录见上。
新的最终源码 freeze 包含 **397 个 Python 文件**，其 SHA-256 为
`fe1bb1e196b9c7dc45333e0220d91f5cfb9833ab4086e14957581b7009bd1f3b`。
首次失败日志与对应旧快照另行保留，未作为最终发布输入。**新的完整
回归已通过 5310 项／276.895 秒**，没有 skipped、Qt callback 错误或
traceback；397 个冻结 Python 源码哈希在回归和最终打包核对时保持一致。
最终收据记录 `ok: true`，日期副本与 ZIP 已通过归档验收。

新原生 helper 已重新构建，**12／12 个 CTest 通过，75.76 秒**，许可
准备完成。PyInstaller 日志在 **111.765 秒**记录构建完成。静态包
审计 **249／249 通过**，核对 103 个源码模块与 **8397 个 payload
文件**（审计发生在加入两份便携收据前，归档最终为 8399 文件）。实际
新 EXE 的
`--audio-package-check` 冻结诊断通过，记录未访问用户设置、未更改
默认音频端点、未开始接收会话、未保存音频、未创建 Qt application。
该诊断不等于完整 GUI 实机验收。

包内在线 runtime 检查 **14／14 通过**，包括 staging 字节一致、
适配器版本与三个平台的隔离 `prepare_login` 预加载；这证明离线运行时
准备完成，不代表已执行真实账号登录或网络播放。Inno Setup 已成功
编译新 canonical 安装器，耗时 **105.219 秒**；十项隔离安装场景及
日期副本／ZIP 归档检查均通过。

安装场景使用 `.runtime/installer-acceptance-2026-10-16-c7f115e7` 的
生成惰性 EXE：新装与系统卸载入口、原目录注册升级、同／低版静默
拒绝、繁忙实例拒绝、目标实例退出后覆盖重启而保留其他目录同名副本、
高版升级保留状态、安装中断恢复、缺失 EXE 修复、隔离安装发现及完整
拥有状态卸载共 **10／10 通过**。stdout JSON 与 fixture result 对应；
其 Setup SHA-256 为
`8cd256d9616a8c077f0986aad60bdd2d6302ca0a45f8b4fbff1af7973e33ab4c`，
仅标识隔离测试安装器，不能与上表真实分发 Setup 混用。进度检查使用
原生目录行最大圆角条、真实解压进度，周界进度与旧进度帧均关闭。

归档门槛已核对两份 focused 日志独立完整通过并具有相同动态测试数，
独立原生桌面证据的六项／14 个 HWND 检查点、退出状态及桌面隔离
同时满足；这五份证据的路径、字节数与 SHA-256 均随最终收据保存。
安装器已有视觉进度、字体恢复与首帧交接证明的 **71 条断言**，在当前
安装模板的标准化源码 SHA-256 完全匹配后复用；不冒充本轮重新执行
这 71 条。当前真实安装器编译及十项隔离安装场景另有本轮新日志。

最终交接核对再次验证当前 **397 个 Python 源码**及完整路径集合与
freeze 一致、收据的 **7 个产物记录**字节数／SHA-256 正确，并验证
日期 Setup／ZIP 的校验 sidecar；结果记录为成功。README 的产品
行为与发布状态已同步，`git diff --check` 通过。

| 验证项 | 本轮结果 |
| --- | --- |
| 默认 960 × 540／16:9、4:3 适配、较小工作区与负坐标屏幕 | 最终 DPR 1／2 扩展套件通过 |
| 手动尺寸恢复、330 × 400 最小值与宽高缩放 | 最终 DPR 1／2 扩展套件通过 |
| 隐藏栏／外部全屏缓存清理、最小化恢复与设置回调顺序 | 最终 DPR 1／2 扩展套件通过 |
| Free 可见性、缓存预览落点与播放状态保持 | 最终 DPR 1／2 扩展套件通过 |
| 独立非活动原生桌面的六项生命周期、HWND 可见／最小化状态 | 6／6，3.612 秒；14 个检查点通过；独立进程退出 0，活动桌面未切换 |
| 片头信息清理固定开启、旧值迁移、设置入口与搜索项移除 | 最终 DPR 1／2 扩展套件通过 |
| DPR 1／2 相关测试 | 最终 542／38.979 秒；542／41.129 秒，均 OK |
| 最终完整 Python 回归、完整 freeze 与构建前后哈希 | 5310／276.895 秒，OK；397 个源码哈希回归及最终打包核对时保持一致 |
| 三组旧日期发布、完整 window-resize payload 与 canonical 可恢复保留 | 已完成；9 个保留清单项，旧 payload 8399 文件与历史 ZIP／清单一致 |
| Python 依赖版本、在线 runtime 与环境预检 | 27 项版本一致、7898 个 runtime 文件一致；CheckOnly 通过 |
| 当前原生 helper 与许可 | 12／12 CTest，75.76 秒；许可准备完成 |
| PyInstaller、冻结源码／资源与无状态污染审计 | 构建完成；249／249 包审计，103 源码模块，8397 个收据前 payload 文件 |
| 实际新 EXE 冻结诊断、无用户设置／端点／接收会话副作用 | 通过；未访问设置、改默认端点、启动接收会话、保存音频或创建 Qt application |
| 包内在线运行时字节一致与隔离 `prepare_login` 预加载 | 14／14 通过 |
| 安装器编译 | Inno Setup 成功，105.219 秒；canonical Setup 字节与 SHA-256 已记录 |
| 隔离安装／覆盖／回滚／卸载场景 | 10／10 通过；只启动生成惰性 fixture |
| 安装器 71 条既有视觉／字体／交接证明的当前来源匹配 | 标准化模板源码 SHA-256 匹配，按记录复用，未声称本轮重跑 |
| 新 ZIP 的 CRC、逐文件 SHA-256、payload 前后一致与 Setup 副本一致 | 8399 条目全部通过；payload 前后一致，日期／通用 Setup 字节一致 |
| 最终交接再核对 | 397 源码集合与哈希、7 个产物大小／哈希、日期 sidecar 全通过 |

本轮证据路径：

- `.runtime/compact-native-desktop-2026-10-19/launcher.json`
- `.runtime/compact-native-desktop-2026-10-19/result.json`
- `.runtime/compact-native-desktop-2026-10-19/child.log`
- `logs/window-visibility-final-focused-dpr1-2026-10-19.log`
- `logs/window-visibility-final-focused-dpr2-2026-10-19.log`
- `logs/window-visibility-initial-full-2026-10-19.log`（首次失败，历史调查记录）
- `.runtime/window-visibility-initial-source-freeze-2026-10-19.json`（首次失败对应旧快照）
- `.runtime/window-visibility-source-freeze-2026-10-19.json`
- `logs/window-visibility-final-full-2026-10-19.log`
- `.runtime/window-visibility-prior-release-preservation-2026-10-19.json`
- `.runtime/window-visibility-dependency-verification-2026-10-19.json`
- `logs/window-visibility-build-preflight-2026-10-19.log`
- `logs/window-visibility-native-build-2026-10-19.log`
- `logs/window-visibility-pyinstaller-build-2026-10-19.log`
- `.runtime/window-visibility-package-audit-2026-10-19/result.json`
- `logs/window-visibility-frozen-acceptance-2026-10-19.json`
- `logs/window-visibility-online-preload-2026-10-19.json`
- `logs/window-visibility-installer-build-2026-10-19.log`
- `logs/window-visibility-installer-smoke-2026-10-19.log`
- `.runtime/installer-acceptance-2026-10-16-c7f115e7/result.json`
- `logs/window-visibility-release-artifacts-2026-10-19.json`
- `logs/window-visibility-archive-2026-10-19.log`
- `logs/window-visibility-final-handoff-2026-10-19.json`
- `release/GluSpot-6.412/BUILD-INFO.json`
- `release/GluSpot-6.412/使用说明.txt`

## 使用与验收边界

便携版解压后保持 `GluSpot.exe` 与 `_internal` 在一起。配置、账号与缓存
仍位于程序旁的 `GluSpot` 子目录。安装契约及新目录首次启用流程沿用
现有实现，新目录首次启动选择程序内播放／系统抓取，两项默认开启。
注册安装在原目录升级并保留配置；同版或低版交互确认覆盖，静默拒绝。
完整卸载会移除安装目录内拥有的配置、账号与缓存，需保留时先自行备份。

本轮仍为未签名内部测试版。离屏渲染与自动测试不能证明物理显示流畅度、
实际 Finder／Explorer 自动隐藏、真实网络登录／中断恢复、HomePod
实体触控、无线切歌听感和长期声学同步。HomePod 原生队列读取、写入或
歌曲注入未实现；既有协议限制见[调查](homepod-remote-control-research-2026-10-18.md)。
