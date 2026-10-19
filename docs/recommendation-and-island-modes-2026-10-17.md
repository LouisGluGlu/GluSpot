# 推荐封面与音乐岛三类模式 — 2026-10-17

GluSpot 6.412。本轮对应每日推荐／私人漫游只有平台标志，以及用户重新明确的
Free、Top、沉浸栏分类。版本号未变，现有 EXE／安装包未重建。

## 调整

- 每日推荐与私人漫游原先永久使用空封面地址，缩略图队列因此没有发起获取。
  现在复用已验证的推荐接口，取推荐批次中首个真实专辑图，并保留该批歌曲供实际播放
  一次性消费。封面获取和播放共享各自的工作线程锁，避免重复推进私人 FM。
  失败有界重试，账号切换、退出和过期均有围栏。详见
  [原因、缓存边界和完整图片验证](recommendation-entry-artwork-2026-10-17.md)。
- 设置主选择改为 **Free／Top／沉浸栏**。选择沉浸栏时显示实际可用的
  **Finder Bar／Tasker Bar** 子位置；只有一个可用位置时只读展示。
  诊断文案同步分类，原运行 ID、迁移、MyDockFinder 可用性和自动识别行为保留。
  UI 的 `immersive` 分类不会传给定位接口，也不会写入全局位置配置。
- 沉浸栏继续共用 **专辑与无背景／专辑与渐变色背景／专辑与背景**，设置同时预览
  居中顶栏 Finder 与左侧任务栏 Tasker 的效果，切换位置保留同一材质选择。
- Tasker 的材质、封面、歌词和既有悬浮按键在任务栏内上下各留 **5 个逻辑像素**。
  原任务栏跟随、自动隐藏、拖动行和定位范围保留；实际封面点击范围跟随缩小后的封面，
  留白区域仍用于拖动。短过渡帧按高度收敛留白，避免产生反向矩形。
- Free 紧凑态已经是最大圆角矩形，Top 已经是贴屏幕顶边的平顶、下圆角轮廓。
  本轮检查后复用其现有路径。Free 展开态保留内容安全圆角，避免裁掉左上封面和按钮。

## 验证证据

| 范围 | 结果 | 证据 |
| --- | --- | --- |
| 服务缓存、并发、账号失效 | 39 项通过 | `logs/oct17-recommendation-artwork-service.log` |
| 音乐库控制器 | DPR 1／2 各 80 项通过 | `logs/oct17-recommendation-artwork-controller-dpr{1,2}.log` |
| 真实本地 HTTP → 解码 → 卡片像素 | DPR 1／2 各 16 项通过、共 10 帧 | `.runtime/recommendation-entry-artwork-2026-10-17/dpr-{1,2}/evidence.json` |
| Finder／Tasker／Top 定位与材质 | DPR 1／2 各 245 项通过、共 16 帧 | `.runtime/oct17-island-modes-focused{,-dpr2}.log`、`.runtime/island-mode-families-2026-10-17-fonts/` |
| 设置分类、诊断、迁移 | DPR 1／2 各 101 项通过、共 28 帧 | `logs/oct17-three-island-settings-focused{,-dpr2}.log`、`.runtime/oct17-island-settings/` |

已查看原生 Qt 的封面完成图、三类轮廓及浅／深设置预览；两张卡片有实际图片，
Tasker 的上下留白清楚，设置文字和材质预览没有相互遮挡。
独立只读复核确认分类投影不改变运行路由，不在 Dock 状态刷新时保存或切换位置。

全套原生 Windows Python 回归 **5075 项通过，耗时 290.047 秒**，无跳过或 Qt 回调异常；
结果记录在 `logs/oct17-recommendation-island-full.log`。
本轮验证前冻结 348 个 Python 源码、测试及工具文件，清单为
`.runtime/oct17-recommendation-island-source-freeze.json`；验证后再次核对无源码漂移。

## 限制与交付

图片验证使用明确标注的合成账号和本地 HTTP 图片，未操作真实账号、用户正在运行的
程序或物理音频设备。它证明实际请求、重试、解码与显示链路，不证明网易云此刻的网络
可用性；真实接口未提供图片时仍显示平台占位。入口图来自实际推荐歌曲的专辑，
不是平台专用静态入口图。

此次三类整理保留原有的 Dock／真实 Finder Bar 可用性限制。未运行与本轮无关的
原生音频构建或安装验收。现有 October 17 installer/audio EXE、Setup 与 ZIP
仍对应前一轮构建，不含本轮修正；从此源码启动可使用本轮改动。
