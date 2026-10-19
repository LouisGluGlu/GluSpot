# 可调窗口、边缘收拢与 Windows 包 — 2026-10-19

版本保持 **6.412**。本轮按用户最新纠正，改为宽高均可调整的播放窗口，
最小尺寸 **330 × 400 逻辑像素**，并改进压缩时的白色边缘渐变阴影和
Free 岛交接位置。**安装程序与便携包已构建、完成自动验收并归档，
最终发布收据 `ok=true`。** 真实屏幕、网络与 HomePod 硬件的验收边界
保留于文末。

适用平台为 **Windows 10 1809 或更新版本／Windows 11，x64**。
不更新独立 Windows 7 兼容包。最终收据与 `BUILD-INFO.json` 的实际
归档时刻为 **`2026-10-19T01:12:15.366295+08:00`**。

## 本轮目标与实现边界

- 正常可用工作区内，普通播放窗口宽高均可调整，最小 330 × 400。
  默认、恢复与边缘拖动使用同一最小边界，不再将高度固定为 400。
- 达到最小边界后继续向内压缩，沿用现有 Free 岛收拢与不足阈值松手
  回弹的交互。先将宽高都缩至最小尺寸，再继续向内压缩；达到最小宽／高
  的 50% 时完成收拢，斜向按压缩更深的方向计算。四边与角部缩放保持
  对侧锚点，恢复窗口保留高于最小值的原高度，并受可用工作区约束。
- 白色边缘渐变阴影随压缩向内收，不以整面蒙版覆盖播放页；预览与
  最终 Free 岛显示在相同位置，避免提交时再次位移。
- 复用 `local/music_window_compact.py`、`local/music_surface_window.py`
  与 `local/island_stack_visual.py` 的现有窗口／岛交接，不新增播放器或
  音频接口。维持歌曲、播放位置与输出选择，不因窗口收拢发送播放命令。
- 保留本分支已有的控件材质、同步设备选中态与蓝色音量已读段修正。
  玻璃浓度仍为越不透明则背景模糊越强；不新增音乐控件或悬浮展开。

上述行为已由本轮源码、相关测试与完整回归确认，并包含于本轮分发包。
不将上一版固定高度行为或其测试结果作为本轮完成证据。

## 撤回上一版与保留范围

用户明确要求删除上一 `window-compact` 版程序、安装包与便携包。
实际删除与缩小范围后的删除均被自动审批审核策略拒绝，未继续绕过。
root 已将该版完整 payload、canonical Setup 及校验文件、日期 Setup／
ZIP 及校验文件共 7 个明确目标，以可恢复移动方式**撤回移出发布目录，
未物理删除（自动审核策略阻止）**。保留位置为
`.runtime/withdrawn-window-compact-2026-10-19`，记录见
`logs/window-resize-withdrawn-artifacts-2026-10-19.json`。

新准备和归档脚本已确认以下已撤回日期输出及其 `.sha256` 不在发布目录，
没有要求它们作为保留输入：

- `release/GluSpot-6.412-2026-10-18-window-compact-Setup-x64.exe`
- `release/GluSpot-6.412-2026-10-18-window-compact-windows-x64.zip`

此前 `installer-library` 与 `device-dj-glass` 两组日期 Setup／ZIP、
校验文件及各自保留的完整 payload 已核对并保留；`de57` 工作树中的
参考产物继续保留。本轮使用新的 `window-resize` 名称与 2026-10-19
日期，不覆盖这两组旧发布。上一版日志与报告仅作为历史审计记录。

## 发布产物

| 文件 | 字节 | SHA-256 |
| --- | ---: | --- |
| `release/GluSpot-6.412-2026-10-19-window-resize-Setup-x64.exe` | 161707792 | `16256e9ed67ffe4af72e087f73d28b389d4705218556b5ef994a227c3dd77395` |
| `release/GluSpot-6.412-2026-10-19-window-resize-windows-x64.zip` | 212908051 | `ebe8a1eb9b64d14cbcbcf81814d0997015f841cdda4b9fb312dd5350246c9e81` |
| `release/GluSpot-6.412/GluSpot.exe` | 10415185 | `c14a8adb8e783c5b23ba1406d2d43e55b7985c9d8c24fd5bb681957aa382a4be` |

便携 ZIP 共 **8399 个文件条目**，解压后合计 **484273406 字节**，
已通过 CRC、逐文件 SHA-256 与归档前后 payload 一致性核对。
`release/GluSpot-6.412-Setup-x64.exe` 与本轮日期 Setup 字节一致。
日期 Setup／ZIP 及通用 Setup 均附 SHA-256 校验文件。

便携包附 `BUILD-INFO.json` 和 `使用说明.txt`，`packaged_at` 与上述
最终收据时刻一致。安装器仅分发 EXE 与 `_internal`，不将这两份
便携收据／说明注入安装目录。

## 验证记录

两档 DPR 的最终相关回归均通过；395 个 Python 源码文件已保存完整
freeze，最终完整回归 **5298 项全部通过，304.338 秒**。依赖哈希、原生
构建、PyInstaller、静态审计、冻结诊断、在线预加载、安装器编译、
隔离安装场景与归档验收均已完成，不复用上一版的构建或归档结果。

| 验证项 | 本轮结果 |
| --- | --- |
| 最小 330 × 400、宽高调整、默认与恢复窗口、边缘和角部拖动 | 已纳入本轮最终相关回归，两档 DPR 均通过 |
| 白色边缘渐变阴影内收、无整面蒙版、取消回弹与完成位置一致 | 相关测试通过；DPR 2 离线联系图已由 root 视检，边缘渐变内收且中心内容保留 |
| 上下双屏、超窄工作区、DPR 1／2 与播放状态保持 | 两档相关测试通过；缓存预览与提交落点偏差均不超过 1 物理像素，预览收据记录播放继续 |
| DPR 1 最终相关回归 | 401 项通过，28.267 秒 |
| DPR 2 最终相关回归 | 401 项通过，28.955 秒 |
| DPR 1／2 生产绘制离线预览 | 每档 9 帧及 120 次预热后绘制，`callbacks=[]`，均提交 Free 并保持播放状态 |
| 最终完整 Python 回归、完整源码 freeze 与前后哈希一致性 | 5298 项全部通过，304.338 秒，无跳过、回溯或 Qt 回调异常；395 个源码文件在构建前、独立检查与最终归档复核均一致 |
| 当前原生 helper 构建／测试、依赖版本与许可来源 | 构建成功，12／12 通过，70.01 秒；依赖版本一致，包内原生许可与来源文件校验通过 |
| PyInstaller 新 EXE、当前源码／资源／无状态污染审计 | 新 EXE 构建成功；249／249 审计通过，8397 个文件、103 个源码模块；无用户状态、日志缓存或账号密钥污染 |
| 新 EXE 的冻结诊断与无设置／音频端点／接收会话副作用 | 实际本轮收据 `ok=true`；设置访问、默认端点改变、接收会话、音频保存及 Qt 应用创建均为 `false` |
| 包内在线运行时字节一致性与隔离 `prepare_login` 预加载 | 14／14 通过；7894 个依赖文件字节一致，包内另含 3 份适配器许可；真实 Node 隔离模块预加载通过，不含账号或网络登录验收 |
| 安装器编译、隔离安装／覆盖／回滚／卸载场景 | 编译成功，84.484 秒；10／10 隔离场景通过，fixture `b6af20c9`，仅运行无操作测试载荷 |
| 安装器原生视觉、字体恢复与首帧交接证明来源匹配 | 71 条证明的规范化模板来源哈希匹配，复用 25／30／16 条既有证明；当前截图来源与本轮源码一致 |
| 新包 CRC、逐文件 SHA-256、canonical 一致与旧包保留 | 8399 个 ZIP 条目 CRC 与逐文件 SHA-256 全部通过，payload 归档前后一致、通用与日期 Setup 一致；两组旧发布及 payload 保留 |
| 上一 `window-compact` 分发输出撤回 | 7 个明确目标已移出发布目录并保留于 `.runtime/withdrawn-window-compact-2026-10-19`；未物理删除，自动审核策略阻止删除 |

当前 395 条源码 freeze 文件的 SHA-256 为
`e617b7a63a9edb4f8164a789d67175becb06bc0e809ce45be90806fb694975f8`。
本轮 prepare 与 package 指向相同的新完整回归、freeze 和依赖收据，
依赖收据与最终归档已确认 395 条哈希一致，并匹配动态解析的测试数量、
耗时和源码数量。冻结诊断未创建 Qt 应用，不代表已完成
实际新包窗口展示或真实 HomePod 播放验收。

离线固定素材预览的渲染中位数／P95 为 DPR 1 **0.376／0.680 毫秒**、
DPR 2 **0.480／1.217 毫秒**。该计时和联系图用于检查生产绘制路径，
不能推定物理屏幕上的帧率或交互流畅度。

已生成的相关测试、预览与撤回证据：

- `logs/window-resize-final-focused-dpr1-2026-10-19.log`
- `logs/window-resize-final-focused-dpr2-2026-10-19.log`
- `.runtime/window-resize-preview-2026-10-19/dpr1/evidence.json` 及 `contact-sheet.png`
- `.runtime/window-resize-preview-2026-10-19/dpr2/evidence.json` 及 `contact-sheet.png`
- `.runtime/window-resize-source-freeze-2026-10-19.json`（395 条源码哈希）
- `logs/window-resize-final-full-2026-10-19.log`（5298 项通过，304.338 秒）
- `logs/window-resize-withdrawn-artifacts-2026-10-19.json`（7 个目标已可恢复撤回，未物理删除）
- `.runtime/window-resize-dependency-verification-2026-10-19.json`
- `logs/window-resize-native-build-2026-10-19.log`
- `logs/window-resize-pyinstaller-build-2026-10-19.log`
- `.runtime/window-resize-package-audit-2026-10-19/result.json`
- `logs/window-resize-frozen-acceptance-2026-10-19.json`
- `logs/window-resize-online-preload-2026-10-19.json`
- `logs/window-resize-installer-build-2026-10-19.log`
- `.runtime/installer-progress-48d92274/evidence.json`（25 条证明，来源哈希匹配）
- `.runtime/installer-fonts-3bfd9a9c/evidence.json`（30 条证明，来源哈希匹配）
- `.runtime/installer-handoff-6c3282e1/evidence.json`（16 条证明，来源哈希匹配）
- `logs/window-resize-installer-smoke-2026-10-19.log`
- `.runtime/installer-acceptance-2026-10-16-b6af20c9/result.json`（10 个隔离场景）
- `logs/window-resize-release-artifacts-2026-10-19.json`
- `release/GluSpot-6.412/BUILD-INFO.json`

## 支持脚本与实际执行顺序

源码修改、完整 freeze 与新的完整回归完成后，已按序执行：

1. `.runtime/prepare-window-resize-release-2026-10-19.py`：核对新源码与
   完整回归、既有依赖及需保留的两组旧发布，写入新依赖收据。
2. 环境预检后重新构建／验证原生 helper，并生成音频许可。原生完成
   日志晚于新完整回归及全部原生输入，保留真实日志时刻。
3. 按 `GluSpot.spec` 构建新的 `release/GluSpot-6.412`。
4. `.runtime/audit-window-resize-package-2026-10-19.py`，随后以实际新
   EXE 的 `--audio-package-check` 写入本轮冻结诊断收据。
5. `.runtime/verify-online-window-resize-2026-10-19.py`。
6. 编译安装器并运行隔离安装器场景，日志使用上列本轮文件名。
7. `.runtime/package-window-resize-2026-10-19.py`，核对证据后生成新
   日期 Setup、ZIP、收据与校验文件，实际结果已补齐于本报告。

所有输出均拒绝静默覆盖既有证据。旧 `window-compact` 二进制、完整
回归日志或日期产物不能改名充当本轮新构建／新验证。

## 使用与验收边界

便携包解压后须保持 `GluSpot.exe` 与 `_internal` 在一起，配置、账号和
缓存仍在程序旁的 `GluSpot` 子目录。新目录首次启用流程、覆盖升级与
完整卸载沿用现有安装契约。本轮已通过隔离安装场景；该结果使用无操作
测试载荷，不等于在用户真实安装目录中完整操作过应用或完成物理显示验收。

上一版输出已撤回，发布目录内不存在旧日期 compact 包；隔离保留目录
仍占用磁盘空间。本轮不会将“发布目录已撤回”表述为“已物理删除”。

本轮仍为未签名内部测试版。离线渲染和自动测试不能证明物理屏幕流畅度、
真实网络中断恢复、实际 Finder／Explorer 自动隐藏、HomePod 触控、
切歌声学同步与长时间听感。HomePod 原生播放列表读取、写入或歌曲注入
仍未实现；已有协议路径的限制见[既有调查](homepod-remote-control-research-2026-10-18.md)。
