# 音乐岛自动位置与隐藏跟随 — 2026-10-17

GluSpot 6.412。本轮实现用户明确的环境、拖动、外部全屏和隐藏规则。
“隐藏时跟随”默认开启并保存；退出外部全屏保留自动选择的位置模式。
版本号未变，现有 EXE、Setup 和便携 ZIP 未重建。

后续现场调查发现服务自启实际使用 `Dock_64.exe`。用户已确认兼容它与 `dock.exe`，
此报告的单一进程名限制及当时未识别 Finder 的结论由
[服务自启与 Finder 修正](dock64-finder-and-homepod-switch-2026-10-17.md) 更新。
以下验证数字保留本轮当时的结果；位置矩阵的 Dock 条件现指两种允许主程序之一。

## 位置与设置

| 实时环境 | 手动可用位置 | 拖动吸附 | 沉浸栏样式预览 | 外部全屏自动位置 |
| --- | --- | --- | --- | --- |
| 没有 `dock.exe` | Top、Free、Tasker Bar | 顶边为 Top；任务栏为 Tasker，含自动隐藏任务栏 | Tasker | Tasker |
| 有 `dock.exe` 且有启用的真实 Finder | Finder Bar、Free | 真实 Finder 区域为 Finder；其他位置 Free | Finder | Finder |
| 有 `dock.exe`，Finder 未启用／不存在 | Top、Free | 顶边为 Top；其他位置 Free | 不显示，样式选择禁用，原选择保留 | Top |

只枚举不区分大小写的 `dock.exe`。旧程序名、安装目录中的文件、窗口标题和旧手动
Finder 标记不能建立运行证据。确认 Dock 运行后，真实 Finder 仍须通过进程身份、
`MyFinderApp` 窗口类和显示器边界验证。关闭监听会清空真实栏快照并规范化旧位置。

设置保留 Free／Top／沉浸栏分类；运行接口继续使用原有 `free/top/finder/tasker`。
Finder 和 Tasker 共用已保存的三种沉浸栏样式。只有当前环境的一个预览可见，
Finder 隐藏但仍启用时不会被误当作未启用。

## 隐藏与全屏

- 开启跟随：Finder／Tasker 随所属栏移动、隐藏和显示；隐藏状态释放鼠标输入，
  音频、曲目和播放时钟不变。Dock 无 Finder 时，Top 在外部全屏期间隐藏。
- 关闭跟随：所属栏隐藏时临时使用置顶 Top，所属栏恢复后回到对应沉浸栏。
  Dock 无 Finder 时，外部全屏保留可见 Top。Dock+Finder 的临时 Top 不扩充手动选择。
- 退出外部全屏保留自动选择的位置；若此时正在等待隐藏栏恢复，继续保留该临时
  Top，栏恢复后返回对应沉浸栏，不恢复进入全屏前的 Free。
- 自身播放页／音乐库继续持有导航显示权。托盘隐藏、退出状态不被置顶刷新重新显示。
  真正的外部全屏按原生窗口与显示器物理坐标识别，避免负坐标和混合 DPI 误判；
  临时线程 DPI 上下文在读取后恢复。

## 跟随修正

复用现有原生栏接口和定位模型，不增加第二套位置系统。

1. Finder 保留原始窗口可见性，区分半收起和真正的 `SW_HIDE`；窗口失效或屏幕映射
   失败时，不再用旧快照恢复可见状态。跟随保留真实完整栏高并使用实时位移。
2. Tasker 保留原始 HWND 矩形，结合所属显示器内的可见交集判断隐藏。完整高度窗口
   平移出屏幕时不再误判为可见，也不将每个移动帧裁成薄条提前隐藏。
3. 位置跟随最短采样间隔为 8 ms，实际更新频率仍遵循原有帧率与屏幕刷新率配置。
   原生栏自身的移动不再叠加第二次位置缓动，
   隐藏前后保留相同居中偏移。拖入任务栏的独立入场动画仍保留。
4. 隐藏透明度使用可反向的连续运动，重定向保留透明度和速度，不额外缩放或沿边滑动。
   悬浮／点击保留计时不能使被动跟随的岛先于真实任务栏出现。
5. 隐藏清除实际输入遮罩时同步清除遮罩缓存，重新显示后恢复真实点击边界。
   临时 Top 通过岛的可见内容定位显示器，避免画布越界导致监听错副屏任务栏。

## 验证与限制

| 验证范围 | 结果 | 本地证据 |
| --- | --- | --- |
| 完整 Python 回归 | 5126 项通过，244.163 秒，无跳过或 Qt 回调异常 | `logs/oct17-auto-placement-full-final.log` |
| 定位、检测、设置、导航、输入及旧回归 | DPR 1／2 各 1227 项通过，14.276／14.322 秒 | `logs/oct17-auto-placement-combined-final{,-dpr2}.log` |
| Finder DJ、真实栏表面、自动位置和共享样式 | DPR 1／2 各 57 项通过，0.983／0.995 秒 | `logs/oct17-auto-placement-finder-dj-final{,-dpr2}.log` |
| 完整生产 `_tick`、原生 Qt 可见窗口绘制、60／120 Hz × DPR 1／2 × Finder／Tasker | 8 组、1232 个测量帧、128 个导出帧通过 | `logs/oct17-auto-placement-follow-tick-final/{summary,trajectory}.json` |
| 设置环境切换、3 种共享材质预览 | DPR 1／2 共 18 帧，已检查 Finder 单预览及禁用状态 | `logs/oct17-auto-placement-settings/` |
| 原生混合 DPI 全屏证据 | 11 项新测试；只读真实前台窗口探测约 0.097 ms，线程 DPI 上下文前后一致 | `tests/test_native_fullscreen_scene.py` 及专项日志 |

跟随轨迹中 Finder 的目标／所属栏位移误差均为 0；Tasker 均不超过 1 个逻辑像素
（DPR 2 时不超过 2 个物理像素）。每组真实经过四次隐藏／揭示目标重定向，包含
透明度尚未结束时的反向；透明度与速度重定向跳变均为 0，输入穿透始终匹配隐藏状态，
播放时钟、曲目、播放状态和拖动字段不变。各组 QWidget 绘制 P95 为 1.73–3.06 ms，
此数值不包括 OS 栏探测、合成截图和写盘。已查看最终联系图；Finder 内容保持居中，
隐藏清除遮罩、揭示恢复遮罩，没有水平重定位。前一版工具的遮罩截图原点错误已修正，
不作为产品修复记录。

完整回归命令：`.\.venv\Scripts\python.exe -X faulthandler -m unittest discover -s tests -v`。
Finder DJ 的旧测试夹具现明确提供 Dock 运行证据，与生产环境的 Finder 可用条件一致；
没有放宽生产检测。最终验证前冻结 351 个 Python 源码／测试／工具文件，清单为
`.runtime/oct17-auto-placement-source-freeze-final.json`；完成后逐一比对，无源码漂移。
`git diff --check` 通过。

只读运行探测时，本机未运行 `dock.exe`；因此没有真实 Finder 隐藏／显示的正向现场
验收。轨迹工具使用合成的 OS 栏矩形，经生产 Qt 更新、定位、绘制和输入状态处理，
验证确定性路径，不能替代 MyDockFinder／Explorer 的实际轮询抖动、DWM 合成和物理
显示器流畅度验收。未操作用户的音乐播放、鼠标、任务栏设置或 Dock 进程。

此次只交付源码。现有 October 17 installer/audio 构建不包含本轮自动位置修正。
