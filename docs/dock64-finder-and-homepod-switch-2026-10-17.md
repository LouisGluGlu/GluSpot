# Dock 服务自启、Finder 与 HomePod 切歌 — 2026-10-17

GluSpot 6.412。此次修复按用户确认兼容 `dock.exe` 与 `Dock_64.exe` 两种主程序。
版本未变；现有 EXE、Setup、便携 ZIP 尚未重建。

后续已按用户要求将此修复与推荐封面、自动位置改动一起重建为安装器和便携包，见
[2026-10-17 打包产物](windows-release-dock64-homepod-2026-10-17.md)。以下验证保留修复时的记录。

## 实际漏检与修复

现场只读进程与原生窗口探测确认：MyDock 自动启动服务运行在 Session 0，实际桌面主程序
是 Session 1 的 `Dock_64.exe`；同时有 Dockmod／Dockmod64 辅助进程。此前仅匹配
`dock.exe`，因此把实际正在运行的 MyDockFinder 判断成未运行。运行中的 GluSpot 来自
当前工作树 `local/main.py`，此次问题并非运行了旧 EXE。

修复后生产检测接口实际返回 `running=True`、`matched_name=Dock_64.exe`、
`finder_enabled=True`，并找到真实的 `MyFinderApp` HWND，区域为 2560×24 物理像素。
单次完整探测约 22.08 ms，单次缓存 HWND 刷新约 0.30 ms；线程 DPI 上下文恢复。
这些是单次只读测量，不代表长时间性能或物理动画验收。

- 只允许两种主程序名，不以服务、辅助进程、标题、安装文件或旧路径独立建立运行证据。
- 旧配置路径不再屏蔽其他位置实际运行的允许主程序。旧监听字段仍仅作迁移数据，
  不允许它改变当前检测范围。
- Finder 继续要求实际 PID、`MyFinderApp` 窗口类和显示器几何。提权／服务启动下若
  第二次进程路径查询被拒，仍使用当前 Toolhelp 名称、PID 与实际 HWND 证据；已知且
  不相符的路径仍被拒绝。同一 HWND、PID、主程序名在隐藏期间丢失路径读取权限时，
  可沿用此前核验的路径和启用配置；窗口、进程或已知路径冲突时不得继承，恢复读取后
  新配置仍有最终决定权。读取配置不会凭空建立一个 Finder 栏。
- 拖动／按住期间迟到的检测不再改变岛的位置、尺寸和拖动基准；释放后按真实目标栏
  命中决定吸附。命中先刷新实际 HWND，使用栏当前可见部分与屏幕的交集，不使用隐藏前
  原位置。跨屏拖动按该屏真实 Finder HWND 和 DPI 映射选择目标。
- 普通点击遇到检测完成只更新检测数据，不会被当成拖放；打开播放页或音乐库的点击
  仍由导航持有显示权，不在释放时重置页面或过渡。

证据位于忽略目录 `.runtime/oct17-dock-service-detection-observation.json` 与
`.runtime/oct17-dock64-detection-positive.json`，未修改 Dock 服务、设置或运行状态。

## 位置、预览与隐藏规则

| 实时环境 | 可用位置 | 设置预览 | 外部全屏 |
| --- | --- | --- | --- |
| 两种 Dock 主程序均未运行 | Top、Free、Tasker | Tasker | Tasker |
| Dock 主程序运行且有启用的真实 Finder | Finder、Free | Finder | Finder |
| Dock 主程序运行但 Finder 未启用／不存在 | Top、Free | 不显示，样式选择禁用并保留原选择 | Top |

无 Dock 时拖到任务栏进入 Tasker，拖到顶边进入 Top。Dock 有 Finder 时只吸附实际
Finder 区域；其他位置为 Free。Dock 无 Finder 时顶边进入 Top。

“隐藏时跟随”默认开启并保存。开启时随所属 Finder／任务栏移动、隐藏和显示，隐藏后
释放鼠标输入；Dock 无 Finder 时，Top 在外部全屏期间隐藏。关闭时，所属栏隐藏就
临时使用可见置顶 Top，栏恢复后返回对应沉浸栏。退出全屏保留自动选定的模式。
播放页、音乐库和托盘隐藏继续持有原显示权，不会因自动置顶被重新显示。

当前真实栏的高频位置刷新仍使用原有 8 ms 最短间隔和帧率上限。新主程序／重建 HWND
的发现仍通过既有 2.5 秒监听轮询；未把未发现的新 HWND 当作旧栏继续显示。

## 程序内播放切歌断联

用户确认仅 HomePod 和 HomePod 加其他输出两种组合都会出现。已复现一条会把用户
选择清空的竞态：播放器递增 epoch 后停止旧解码器，同步变更通知可能先携带“旧曲目
ID＋新 epoch”；下一首赋值后，旧的 GUI 输出预备请求被正确拒绝。原控制器却把
这次请求取消当作终端故障，清空 HomePod／组成员，之后的新歌曲已无选择可恢复。

修复将已被新歌曲替代的请求与真正的准备／传输失败分开：保留用户输出选择，合并
最新歌曲后继续准备，包括首次选择和快速连续切歌。真实 PCM 错误、helper error
和 stopped 事件仍及时终止并恢复程序内输出。另记录已预留的输出 epoch，停止、
退出或切回本地时同时释放相应私有静音和解码暂停，避免旧 epoch 无权释放新预留。
关闭发生在输出预备等待或 helper 启动期间时，取消对应预留并由路由线程结束原生输出；
关闭后的旧 `waiting_audio` 不再重新静音或发布播放状态。关闭未增加 GUI 线程等待。

此次未更改 AirPlay native 协议、认证、同步缓冲或播放器混音。现有跨曲的 helper
重建仍会发生，不能据此宣称跨曲无缝或消除真实无线／接收端断线。

## 验证与限制

所有 Qt 进程串行执行；生产源码与测试、工具共 352 个 Python 文件记录 SHA-256，
验证期间检查无源码漂移。各项数量有重叠，不能相加当作独立用例总数。

| 验证 | 结果与证据 |
| --- | --- |
| 服务与实际桌面运行态，只读原生接口 | `MyDock` 服务与实际 `Dock_64.exe` 区分；真实 Finder HWND、配置启用和物理边界均命中，见上述现场 JSON |
| 真实 Windows QScreen 接线 | 生产检测 → presence → follow → 实时拖放命中通过；本机屏幕名 `LQ25F165L` 正确按几何映射到 2560×24 的 Finder，位置为 Finder、可选 Free/Finder。使用未显示的测试 QWidget，未改用户窗口；证据 `.runtime/oct17-actual-finder-integration.json` |
| 检测与原生全屏纯测试 | 85 项通过，包含权限被拒、配置恢复、句柄释放、DPI 上下文恢复以及新窗口不得继承 |
| 位置与拖放专项 | DPR 1、2 各 832 项通过；覆盖隐藏栏、普通点击、导航显示权、跨屏、负坐标与混合 DPI |
| 合并检测／设置／输出专项 | DPR 1 为 404 项 / 24.260 秒，DPR 2 为 404 项 / 23.613 秒；日志 `logs/oct17-dock64-homepod-focused-dpr{1,2}-final.log` |
| 输出控制器纯测试 | 51 项通过；单 HomePod／混合输出取消、24 次快速切歌合并、首次选择、真实故障、停止与关闭时的预留释放 |
| Qt 队列竞态 | 新增 2 个真实桥集成测试，单设备与混合组均保留；涵盖旧请求在 GUI 处理前失效，以及已成功预留的较新 epoch 在停止时释放 |
| 真实 Qt 静音解码 | 12 项通过；首个 PCM 时间戳依次为 0、跳转目标 2,000,000 µs、新曲 0；接收器联系 0，系统端点修改 0，证据 `.runtime/oct17-dock64-homepod-real-decode-final.json` |
| 生产 tick 跟随与绘制 | 8 组 Finder/Tasker × DPR 1/2 × 60/120 Hz，共 1232 帧、128 导出帧。Finder 位移误差 0；Tasker ≤1 逻辑像素，隐藏反向时透明度跳变 0。各组绘制 P95 为 1.95–3.24 ms，含离屏 QWidget 绘制，不含原生探测与文件导出。证据 `logs/oct17-dock64-follow-final/trajectory.json`，已查看两种模式的 DPR 2 轨迹图 |

完整原生 Windows 回归通过 **5168 项 / 303.421 秒**，无跳过、Qt 回调异常或崩溃。
日志为 `logs/oct17-dock64-homepod-full.log`，使用：

```powershell
.\.venv\Scripts\python.exe -X utf8 -X faulthandler -m unittest discover -s tests -v
```

`git diff --check` 通过；验证前后 352 个 Python 文件的 SHA-256 一致，记录位于
`.runtime/oct17-dock64-homepod-source-freeze.json`。本轮版本仍为 6.412，运行中的源码实例
需要正常退出后重启；现有 EXE／Setup／便携 ZIP 不包含此次修复。

真实 Finder 的进程、窗口、启用状态及屏幕映射已建立正向证据；未操作用户任务栏或
Finder 自动隐藏来伪称现场动画验收。模拟所属栏轨迹用于验证生产定位、隐藏与绘制
路径，不能代替真实 DWM 合成和物理屏幕流畅度。

音频验证使用真实 Qt 队列请求、私有 QAudioOutput 和静音解码，接收器为内存夹具，
不联系真实 HomePod，不改变系统默认输出、端点音量或静音。已复现竞态得到修复；
用户实际 HomePod 的切歌听感、网络中断和长期播放仍须现场确认。
