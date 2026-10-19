# 固定高度窗口、收拢 Free 岛与 Windows 包 — 2026-10-18

**此版已撤回。** 2026-10-19 用户将高度规则改为可调、最小 400，
压缩提示改为边缘白色渐变内收，最终岛落在预览位置。
本文是历史构建记录；对应程序、安装包和便携包已移出 `release`，
新进度见[双轴缩放修正版](window-resize-and-release-2026-10-19.md)。
删除操作被自动审批审核以策略阻止，撤回产物暂存于忽略目录，未物理删除。

版本保持 **6.412**。本轮将窗口固定高度、最窄宽度与越界收拢交互加入
现有播放页，并将此前尚未打包的控件材质修正一并更新到现代 Windows
安装程序与便携包。**安装程序与便携包已完成构建、自动验收与归档，
最终发布收据 `ok=true`。** 物理屏幕、真实网络与 HomePod 硬件验收边界
仍按文末说明保留。

任务于 2026-10-18 开始，发布流程跨至 2026-10-19。产物与证据文件名
继续保留 `2026-10-18` 标记以保持整条证据链一致。便携包
`BUILD-INFO.json` 的实际 `packaged_at` 为
`2026-10-19T00:18:21.076183+08:00`，与最终发布收据生成时刻一致。

适用平台为 **Windows 10 1809 或更新版本／Windows 11，x64**。
本轮不更新独立 Windows 7 兼容包。

## 窗口与收拢行为

- 普通播放窗口在正常可用工作区中保持 **400 逻辑像素高度**，最窄为
  **330 逻辑像素**。边缘和角部拖动沿用固定高度，左右缩放保留对应的
  对侧锚点；默认窗口和恢复窗口使用相同几何边界。
- 达到最窄宽度后，继续向内拖动使用虚拟压缩距离，实际播放页不重新
  排成极小界面。向内压缩到原最窄宽度的 **50%** 时，动画完成收拢并
  转为现有 **Free 音乐岛**；最窄状态下的纵向压缩也使用半高阈值。
  未达阈值松开则回弹，斜向拖动按压缩更深的方向计算。
- 预览使用当前播放页的一帧与现有 Free 岛生产绘制，提交时沿用现有岛
  导航和位置接口。过渡期间关闭弹出面板并保留内容布局，额外透明画布
  容纳宽于最窄窗口的岛，避免目标岛被窗口边界截断。
- 此交互只改变显示与位置，不发送暂停、跳转歌曲或音频输出命令。
  预览失败、取消和完成均回收过渡图像及临时画布状态。
- 修复上下排列双屏中透明画布中心落入另一块屏幕而选错屏的问题。
  屏幕选择以实际绘制的岛中心为准，预览与提交共用工作区约束，避免
  收拢结束后岛跳到另一块屏幕。
- 修复可用宽度仅 330 逻辑像素时 Free 岛预览保留默认半宽下限的问题。
  预览沿用实际独立岛布局的最小宽度与工作区约束，避免预览目标过宽，
  同时使提交后的岛尺寸与预览保持一致。

新增 `local/music_window_compact.py` 负责窗口边界和压缩过渡，
`local/music_surface_window.py` 接入现有输入／绘制路径，
`local/island_stack_visual.py` 提供现有 Free 岛的预览与提交。
没有另建播放器、数据源或音频接口。

## 本包同时包含的控件修正

此前的[控件材质与同步设备选中态调整](control-material-and-selected-devices-2026-10-18.md)
已包含于本轮包中：玻璃采用柔和单层边缘，播放图形内部保留清楚的统一
黑／白色，悬浮提亮；已确认同步成员为 `#f5f5f7` 灰白底、黑色内容，
实际音量已读段固定为 `#0a84ff`。原 6 像素轨道与 15 × 11 像素短胶囊
滑块保留，不增加音乐控件、播放图标外围底板或悬浮展开。

玻璃浓度仍按用户最终确认的方向联动：越不透明，映射背景模糊越强；
越透明，映射背景模糊越弱。此前设备成员移除、私人漫游首次官方播报和
HomePod 返回控制的协议实现继续包含，硬件验证限制保持原说明。

## 发布产物与旧包保留

| 文件 | 字节 | SHA-256 |
| --- | ---: | --- |
| `release/GluSpot-6.412-2026-10-18-window-compact-Setup-x64.exe` | 161708646 | `168b24f1e605a7990c67c08d36d472f1ec0421ba368814031b23d7945d8e3909` |
| `release/GluSpot-6.412-2026-10-18-window-compact-windows-x64.zip` | 212905022 | `fcf331f0deb030e53b3445d2fc729916766ba6f3af9c72873b0a0392ffa2960c` |
| `release/GluSpot-6.412/GluSpot.exe` | 10413188 | `ef5e7178c91ec69219d7dc5ba6e594a1adc4ca798fce5e128b6288deb66b629f` |

便携 ZIP 共 **8399 个文件条目**，解压后合计 **484270585 字节**；CRC、
逐文件 SHA-256 及归档前后 payload 一致性均通过。通用安装程序
`release/GluSpot-6.412-Setup-x64.exe` 与日期安装程序字节一致。
已生成 SHA-256 校验文件，便携目录附 `BUILD-INFO.json` 和 `使用说明.txt`。
安装程序分发 EXE 与 `_internal`，这两个便携说明／收据文件不注入安装目录。

同日较早的 `installer-library` 与 `device-dj-glass` 安装包、ZIP 和各自
校验文件继续保留。新构建前已将旧完整 `GluSpot-6.412` payload 保留为
`GluSpot-6.412-2026-10-18-device-dj-glass-payload`，不覆盖已保留的
`GluSpot-6.412-2026-10-18-installer-library-payload`。此前 `de57`
工作树中的发布产物继续保留。

## 验证记录

已完成的相关测试与预览结果如下。394 个 Python 源码文件已保存哈希
freeze，修正旧测试契约后的最终完整回归全部通过。当前新 EXE、安装
程序与便携归档均已完成对应的自动验收，不沿用此前包的构建或归档结果。

| 验证项 | 最终结果 |
| --- | --- |
| 固定高度、最窄边界、半宽／半高阈值、回弹、失败恢复和播放状态保持 | 已纳入最终相关测试，DPR 1／2 均通过 |
| 最终完整 Python 回归及源码哈希一致性 | 394 个源码文件已 freeze，构建与归档时哈希复核一致；5286 项全部通过，293.882 秒，无跳过、回溯或 Qt 回调异常 |
| DPR 1 相关测试 | 385 项通过，29.397 秒 |
| DPR 2 相关测试 | 385 项通过，29.257 秒 |
| DJ 底栏契约补充测试 | DPR 1：4 项通过，0.998 秒；DPR 2：4 项通过，0.797 秒 |
| DPR 1／2 生产绘制离线预览 | 每档 9 帧及 120 次预热后绘制；均无异常回调，成功提交 Free，播放状态保持 |
| 当前原生 helper 构建和测试、许可收据 | 构建成功；12／12 测试通过，68.43 秒；包内原生许可与来源文件哈希一致 |
| PyInstaller 新 EXE 构建 | EXE 与 COLLECT 均成功；当前 EXE 10413188 字节，哈希见上表 |
| 冻结包源码、资源与状态污染审计 | 249／249 通过；审计时 payload 8397 个文件、103 个源码模块；无日志、缓存、账号或密钥污染 |
| 实际新 EXE 的诊断验收 | `ok=true`；包内依赖、原生 helper 和只读发现／诊断检查通过 |
| 包内在线运行时字节一致性与隔离模块预加载 | 14／14 通过；7894 个依赖文件与 staging 字节一致，另有 3 份项目适配器许可；真实 Node 隔离预加载通过 |
| 当前安装器编译、隔离安装／覆盖／回滚／卸载场景 | 编译成功，128.015 秒；10 个隔离场景全部通过，使用无操作测试载荷，不安装或启动用户真实应用 |
| 安装器原生视觉、字体恢复与首帧交接 | 71 条断言通过；25／30／16 条既有证明经当前模板与素材来源哈希匹配后复用 |
| 新包 CRC、逐文件 SHA-256 与旧包保留核对 | 8399 个 ZIP 条目全部通过 CRC 和逐文件 SHA-256，归档前后 payload 一致；通用与日期 Setup 一致，旧包与旧 payload 均保留 |

新 EXE 的诊断收据明确记录：未访问设置、未改变默认音频端点、未建立
接收设备会话、未保存音频，且未创建 Qt 应用。此结果验证包内诊断路径，
不代表已经验证 Qt 实际窗口、真实 HomePod 播放或外部设备听感。

首轮完整回归执行了 5286 项，耗时 327.579 秒，5285 项通过、1 项失败，
该轮**不能作为完整回归通过证据**。唯一失败项仍断言旧的最小宽度为
1，与本轮明确的 330 最窄宽度契约冲突。修正只涉及该测试：从合法的
430 × 400 窗口拖动至 330 × 400，继续验证固定高度与 y 坐标保留；
生产源码未因此改变。该模块 4 项在两档 DPR 下均通过后，重新保存
394 个源码文件的 freeze，并重跑完整回归。最终该轮 5286 项全部通过，
耗时 293.882 秒，见 `logs/window-compact-final-full-2026-10-18.log`。

当前 freeze 文件包含 394 条源码 SHA-256，文件自身 SHA-256 为
`6bbb9c2d963ca10f319095b323e5856d1b14d7e1bf7940d4f178eaac9319b162`。
构建前与最终归档均已检查源文件与该快照一致。

上下双屏错误保留了修复前的失败复现日志，随后在两档 DPR 的最终相关
测试中通过。DPR 2 的提交中心断言改为比较实际绘制使用的
`QRectF(target).center()`，并维持每轴 1 逻辑像素公差；这使断言与
绘制几何一致，避免 `QRect.center()` 的整数包含边界造成半像素偏差。

离线预览的 `callbacks` 均为 `[]`，渲染中位数／P95 分别为 DPR 1
0.313／0.546 毫秒、DPR 2 0.384／0.984 毫秒。该计时来自离屏固定素材
绘制，不能据此推定物理屏幕动画帧率。

已生成的相关证据：

- `logs/window-compact-stacked-screen-repro-2026-10-18.log`（修复前失败复现）
- `logs/window-compact-final-focused-2026-10-18.log`
- `logs/window-compact-final-focused-dpr2-2026-10-18.log`
- `logs/window-compact-final-full-2026-10-18-pre-dj-contract.log`（首轮失败记录，非通过证据）
- `logs/window-compact-final-full-2026-10-18.log`（最终 5286 项通过）
- `.runtime/window-compact-source-freeze-2026-10-18.json`（394 条源码哈希）
- `logs/window-compact-dj-footer-contract-dpr1-2026-10-18.log`
- `logs/window-compact-dj-footer-contract-dpr2-2026-10-18.log`
- `logs/window-compact-preview-dpr1-2026-10-18.log`
- `logs/window-compact-preview-dpr2-2026-10-18.log`
- `.runtime/window-compact-preview-2026-10-18/dpr1/evidence.json` 及 `contact-sheet.png`
- `.runtime/window-compact-preview-2026-10-18/dpr2/evidence.json` 及 `contact-sheet.png`
- `.runtime/window-compact-dependency-verification-2026-10-18.json`
- `logs/window-compact-native-build-2026-10-18.log`
- `logs/window-compact-pyinstaller-build-2026-10-18.log`
- `.runtime/window-compact-package-audit-2026-10-18/result.json`
- `logs/window-compact-frozen-acceptance-2026-10-18.json`
- `logs/window-compact-online-preload-2026-10-18.json`
- `logs/window-compact-installer-build-2026-10-18.log`
- `logs/window-compact-installer-smoke-2026-10-18.log`
- `logs/window-compact-release-artifacts-2026-10-18.json`
- `release/GluSpot-6.412/BUILD-INFO.json`
- `.runtime/installer-progress-48d92274/evidence.json`（25 条视觉／进度证明）
- `.runtime/installer-fonts-3bfd9a9c/evidence.json`（30 条字体恢复证明）
- `.runtime/installer-handoff-6c3282e1/evidence.json`（16 条首帧交接证明）

本次安装模板和素材与上述证明的来源哈希重新匹配后，复用了原生视觉、
字体恢复和首帧交接证明；本次分发文件已独立完成实际构建与包内容检查。

## 使用与验收边界

便携包必须解压完整目录，保持 `GluSpot.exe` 与 `_internal` 在一起。
配置、账号和缓存位于程序旁的 `GluSpot` 子目录，新目录走首次设置。
安装程序沿用目录选择和 Windows 卸载入口；同版覆盖需确认，升级保留
目录内状态，完整卸载清理本安装拥有的状态并保留无关文件。

本轮仍为未签名内部测试版。离线渲染和自动测试不等于物理屏幕动画
流畅度或真实网络登录；HomePod 实体触控、长时间听感、无线中断及
原生播放列表读写／注入的限制见[既有协议调查](homepod-remote-control-research-2026-10-18.md)。
