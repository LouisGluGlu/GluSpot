# 原生 Qt 动画生命周期验收

`tools/verify_animation_lifecycle.py` 使用可见的原生 Windows Qt 窗口、真实墙钟和 `QTimer` 事件循环，观察现有生产绘制与动画。所有歌曲、账号快照、歌单、封面和设备状态均来自离线 fixture；不启动已安装的 GluSpot，不读取账号或配置，不联网、不操作音频输出。

这份工具验证动画状态和调度正确性。它不测 DWM 合成、显示器实际出帧或物理输入到显示延迟，不能用其 paint 次数宣称桌面 FPS。Python 方法包装、16 ms 状态观察和定时截图均有额外开销。截图触发的 `grab` 绘制与普通事件循环 paint 分开记录，但截图造成的主线程占用仍会影响后续调度，因此这些结果不能替代独立性能基准。

## 运行

先冻结候选源码，停止其他回归、性能测试和构建，再分别运行两个独立进程：

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$env:QT_SCALE_FACTOR = '1'
.\.venv\Scripts\python.exe tools/verify_animation_lifecycle.py --expect-dpr 1 --strict-source --output .runtime/animation-lifecycle-6.416/final-v3-dpr1
$env:QT_SCALE_FACTOR = '2'
.\.venv\Scripts\python.exe tools/verify_animation_lifecycle.py --expect-dpr 2 --strict-source --output .runtime/animation-lifecycle-6.416/final-v3-dpr2
```

工具要求实际窗口 DPR 与请求一致；不会通过替换 `devicePixelRatioF()` 假装高 DPI。`--strict-source` 核对运行前后的全部 `api/`、`local/` Python 源文件 SHA-256。生成的证据 JSON、PNG 和日志留在忽略的运行目录，不进入源码。

## 覆盖

- 控件 hover、真实 idle 隐藏与恢复；包含中途实际绘制帧。
- 窗口四种布局正反切换、未完成时反向切换和快照释放。
- 原生窗口／全屏几何过渡、全屏四布局和几何中途反向。
- 暂停、向前／向后 Seek 数据更新、切歌后的歌词身份绑定。
- 隐藏、最小化、恢复，检查对应活动时钟是否停启。
- 音乐库旋转封面与播放界面双向过渡；来源／登录页展开与普通页面过渡。
- 导航过程中隐藏覆盖层，检查目标 enabled／opacity 和被冻结时钟恢复、快照释放。
- 首来源绘制后才准备目标；反向请求即时及目标真正就绪交接时，核对窗口、独立封面位姿与解析速度的连续性，而非只检查 request 瞬间。
- 关闭全部页面后，在有界等待内核对像素任务的 active／pending／input／cache／owner 为零；应用退出后 worker 为零。
- 真正的 `IslandStackVisual` 与播放窗口完成最小尺寸收拢，检查 Free 岛可见、位置与压缩预览一致、输入掩码恢复以及后续事件循环仍保持可见。

`evidence.json` 按 phase 保存真实时间、16 ms 观察样本、Python `update()` 请求、生产 timer 回调及 paint 的耗时／间隔。这里的 update 计数只包含经过 Python 方法的调用，不包含 Qt 内部全部更新请求。PNG 记录实际生产绘制结果，不能证明用户显示器上的所有合成帧。

## 最终验证结果

冻结应用源码后，DPR 1／2 分别在独立进程、串行执行：每个 scale **165 项检查、47 张 PNG、53 个 phase** 全部通过，无 Qt 回调异常，运行前后应用源码哈希一致。两个进程都从 1 个 Python 线程开始，应用退出后回到 1 个，GluSpotMusic worker 为 0。窗口关闭后像素任务 active=false，pending／input bytes／cache bytes／owners 均为零；应用 shutdown 后仍为零。

已实际打开检查 DPR 1 的专辑内歌词、反向导航中间帧及 Free 岛，DPR 2 的全屏专辑内歌词和 Free 岛。其他 PNG 由工具检查非空、大小、DPR，并记录哈希。不是每个过渡帧都被人工逐帧观察。证据位于 `.runtime/animation-lifecycle-6.416/final-v3-dpr1/evidence.json` 和 `final-v3-dpr2/evidence.json`，原始样本另汇总到 `logs/animation-6.416-final-native-summary.json`。截图按完整同步材质路径生成，不能据此证明所有 live 异步接纳帧的视觉品质。

目标真正就绪前后的独立封面／窗口位姿最大差为零，速度最大差为 DPR 1 的 `7.11e-15`、DPR 2 的零，小于工具要求的 `1e-5`。这验证生产插值的连续性，不等于截图准备期间没有调度停顿。

下表来自原始 event-loop 样本的独立后处理，单位 ms，**含方法包装、状态观察和定时截图的干扰，不作为无插桩正式性能对比**。P99 与超预算计数由完整原始样本计算，没有删除长帧。

| DPR | 项目 | 样本 | P50 | P95 | P99 | max | >8.33 | >16.67 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | surface animation tick | 913 | 0.62 | 2.07 | 5.63 | 15.70 | 4 | 0 |
| 1 | surface event-loop paint | 1088 | 9.64 | 32.60 | 39.36 | 113.36 | 683 | 279 |
| 1 | capture paint | 46 | 20.34 | 85.49 | 90.29 | 90.29 | 39 | 28 |
| 2 | surface animation tick | 728 | 0.64 | 2.63 | 3.54 | 17.87 | 2 | 1 |
| 2 | surface event-loop paint | 730 | 17.93 | 43.52 | 51.42 | 86.77 | 604 | 485 |
| 2 | capture paint | 46 | 27.44 | 47.86 | 54.05 | 54.05 | 44 | 37 |

仍存在超 16.67 ms 的 native paint，不能声称所有交互已经稳定 60Hz。DPR 2 在本机 2560×1440 显示器下的逻辑可用区域为 1280×708，native 工具按实际 work area 选取可容纳的普通窗口并核对恢复到原尺寸；固定 960×540／1920×1080 DPR 1／2 的同尺寸性能比较另由离屏矩阵完成，不与此 native 表混合。无截图的正式可见采样另见[回调对比](animation-visible-callbacks-2026-10-20.md)。

历史候选的 132 检查／44 PNG／43 phase 记录保留在 `dpr1-final` 与 `dpr2-verified`，不作为最终源码的验收。早期工具曾出现 callback 参数和 work area fixture 问题，均只修工具。最终 v2 在全屏专辑内歌词阶段未按期限完成；记录显示动画已正常推进至 353.6ms，随后 `grab` 内的同步 PNG 编码／写盘／哈希使观察事件停顿，退出定时器先于下次动画 tick，不能据此认定生产永久卡住。

最终 v3 只把 PNG 导出延至每阶段计时结束：当时仍抓取真实 QImage，记录 `capture_raster_ms`，另记 `export_ms` 和 `deferred_export_ms`。没有延长动画、降低通过条件或改生产代码。修正后两个 scale 完整重跑通过；失败证据仍保留在 `final-v2-dpr1`，不得计作通过。抓图仍会占用 GUI，因此本表保留所有长样本，但不能把它与无截图基准作性能前后比较。

真实音频、真实扫码、HomePod、MyDockFinder、物理鼠标拖动、长期资源稳定性及 DWM／显示器流畅度不属于本工具的验证结论。
