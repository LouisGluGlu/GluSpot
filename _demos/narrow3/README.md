# 窄窗 v3 演示

本目录是独立实验，不是生产界面或发布版本。Windows 启动器：`窄窗v3-方案A-静态.bat`、`窄窗v3-方案B-悬停.bat`（使用启动器中配置的 Python 环境）。

A 静态空闲时隐藏全部控件和封面标题；歌词、列表以 20px 边距铺满窗口。歌词恢复 `b1bf5ca` 的粗体沉浸字号：`width×0.0875`，夹在 26–40px；360px 宽为31.5px，基线步进为字号×1.45（45.675px），行框为字号×1.3。当前行基线在360×640时为211.2px。B 指针进入或触摸按下后叠加控件，字号、行距、视口与滚动位置均不改变，无字号动画。无指针／触摸1200ms后隐藏。控件、完整封面标题头和veil共用一个300ms cubic ease-out，反向切换从当前透明度续接。

共用 `VeilOverlay` 在顶部0–214px、底部448–640px叠加渐进模糊和黑色暗化；166–214px为48px顶部羽化，448–528px为80px底部羽化。模糊半径与dim共用端点斜率为零的smoothstep，最大逻辑模糊半径16px、暗化alpha0.55；214–448px中部不受veil影响。完整头部需要较原PRD更高的顶部band，本轮brief授权此调整。透明内容的模糊带在计时器内预计算为半分辨率缓存（含DPR），按文本布局、主题、DPR、实际当前行与整行取整滚动位置索引；最多六张并按LRU淘汰。不同半径的采样条带保存在半分辨率 atlas；最近一次准备的键另有快捷命中；滚动残差只移动内容纹理，羽化边界留在原处。零半径字形保持原生分辨率；缓存数据源保留普通内容的远行模糊，避免羽化中反向锐化。绘制帧仅混合清晰／缓存模糊内容，再暗化底图，不在paint中重模糊。半分辨率仅用于veil，生产clear-glass／背景绘制链不变。

周边歌词静态栅格复用，当前行继续调用生产逐词阅读；暂停时当前行墨迹按精确的时间、来源与布局键缓存；玻璃控件仍由生产renderer绘制，但合成结果在计时器内准备，淡入淡出期间复用。输入反馈与主题改变立即失效，静止玻璃按10Hz刷新。列表选中玻璃的10Hz更新同样移到计时器，paint只读取已准备的列表层，淡入仅合成；veil缓存命中不重画数据源。300ms显隐期间调用生产背景的 `advance_frame=False` 复用现有栅格，两态稳态恢复正常背景更新。合成夹具与暂停的真实来源在 paint 外预合成两张不透明的完整 A/B 背景加内容端点图，淡入淡出时混合，结束即释放；360×640、DPR2 时两张共 7,372,800 字节。正在推进的 `live=True` 歌词保留普通合成路径，以保持可变高度来源歌词的阅读状态实时更新，不据此声称活动真实播放 CPU 已达标。暂停来源使用精确内容键，来源／内容变化使端点失效；已绘制的逐词点击几何仅在来源／模型或滚动位置变化时重建。应用原有33ms绘制节拍不变；性能脚本记录真实300ms动画内的paint耗时，同时保留advancing-clock稳态、动画与tick＋paint总成本，另记录淡入CPU和60Hz压力数值。

顶部按钮下方恢复约96px圆角封面，旁侧为20px歌名与13px歌手，绘于veil之上并与顶钮同步淡入。底部居中播放三键实际绘制和点击区域均44×44，下方40px高行依次为喜欢与加入歌单胶囊、循环、布局和私人DJ；无DJ时循环和布局按钮在180ms内移向右侧。胶囊右侧将当前歌曲加入演示内存中的歌单。无进度条。DJ开启显示绿色环，说话时环旋转；状态仅保存在内存。

L / P 切歌词 / 列表，Tab 切换内容；N / W 切窄窗 / 宽窗；← / → 切歌；1–4 切封面；Space 暂停 / 继续；D 切换 DJ 是否可用，点击 DJ 开关，S 切换模拟说话；H 显隐提示；Esc 退出。循环按钮切换顺序、随机、单曲循环图标；布局按钮切换歌词 / 列表。合成演示使用测试夹具，不播放音频，也不临时修改 HOME、APPDATA 或 LOCALAPPDATA。

宽版调用生产 `MusicSurfaceWindow.paintEvent`；窄版复用生产背景、玻璃、歌词栅格和逐字阅读绘制，播放三键调用生产 `_paint_transport_icon`，DJ 使用生产 `GlassControlRenderer` 的 `GLASS_DJ` 和 `paint_personal_dj_icon`，封面取色调用生产 provider。歌词时间是演示夹具，不代表实际歌曲。宽窄切换仍为硬切，真实桌面交互、材质和长时间表现尚未验证。

运行 `E:\gpt-island\.venv\Scripts\python.exe -X utf8 _demos/narrow3/check_layout.py` 检查 A/B 布局和显隐；`E:\gpt-island\.venv\Scripts\python.exe _demos/narrow3/check_input.py` 检查真实Qt进入、触摸生命周期、44px点击边界及宽窗委派。运行 `E:\gpt-island\.venv\Scripts\python.exe -X utf8 _demos/narrow3/grab_all.py --out DIR` 生成 A/B 歌词与列表截图、生产宽窗参考及 `narrow3_overview.png`。B 截图固定在展开状态。`E:\gpt-island\.venv\Scripts\python.exe _demos/narrow3/check_veil.py --out DIR --frames 180` 在独立 DPR1／2 进程验证清晰中部、零位移、模糊／暗化、缓存及 paint p95≤16.7ms／p99≤24ms，同时输出 A／B-top／B-bottom 截图与性能 JSON。Linux 使用 `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 /workspace/gluspot/qtvenv/bin/python`。离屏耗时不代表原生显示帧率。

基线比较：恢复`b1bf5ca`的大字与完整头部，同时保留后续已修好的A/B固定满窗视口、当前行基线和44×44点击槽。演示歌词重复扩展为48行，截图选中段53.6s。这次brief明确覆盖PRD的31px固定行距与原始短羽化要求，验收以31.5px大字、完整头部、A/B零几何变化和清晰中部像素一致为准；不再声称与旧A像素差小于0.5%。

**历史 final3 记录（2026-10-28；并非当前实现的最终测量）：** `check_layout.py` 25项、`check_input.py` 5项、glass 测试套件 130项通过。`check_veil.py --frames 180` 双DPR通过像素、布局、缓存与paint性能断言；静止淡入禁止过滤器调用，列表paint禁止重建。`grab_all.py`已刷新`/workspace/gluspot/grok/final3/narrow3_overview.png`及原始截图，目视确认大字、完整头部和无直线接缝。

| 离屏Qt实绘（ms） | DPR1 | DPR2 |
| --- | ---: | ---: |
| 歌词淡入paint p95 / p99 | 1.56 / 2.59 | 3.13 / 4.16 |
| 列表淡入paint p95 / p99 | 1.14 / 1.39 | 2.18 / 2.39 |
| 列表advancing-clock稳态paint p95 | 4.60 | 6.01 |
| 列表advancing-clock淡入paint p95 | 0.83 | 1.99 |
| 列表淡入tick＋paint p95 | 1.51 | 2.66 |
| 歌词滚动＋淡入paint p95 | 4.21 | 8.14 |

实际滚动缓存命中率95.65%；六张缓存DPR1/2分别1,823,040／2,730,240字节。33ms节拍列表淡入CPU为7.10%／13.84%，歌词为9.55%／17.61%；DPR2歌词仍触发原脚本的15% CPU警告，未宣称全部PRD性能目标达成。上述为Linux offscreen的真实Qt paint耗时，报告在final3的`veil_acceptance_dpr1.json`、`veil_acceptance_dpr2.json`；原生Windows PC与物理屏幕流畅度未验证。

## 真实来源窄窗实验（Windows PC）

在配置好项目 Python 环境的 Windows PC 上，从仓库根目录运行：

```powershell
E:\gpt-island\.venv\Scripts\python.exe local/main.py --narrow-live [--netease-relaunch] [--grab OUT]
```

方括号表示可选参数，实际输入时不要包含方括号。仅源码入口通过 `IslandStackVisual` 连接真实 `WindowsMediaProvider`，默认使用外部 Windows 系统媒体控制（SMTC）来源；`--netease-relaunch` 明确启用现有网易云捕获／重启路径，可能重启播放器。截图需外部播放器实际推进播放并提供来源歌词。`--grab OUT` 在收到符合条件的真实播放快照及来源歌词后保存空闲与指针移动状态的原生 Qt 窗口截图及 `live-evidence.json`；如 40 秒内没有符合条件的播放快照，退出并不生成截图。使用私有临时歌词、封面缓存目录和内存设置，不创建 `SettingsManager`，不读取或保存账户设置；版本和生产应用的默认入口不变。

`E:\gpt-island\.venv\Scripts\python.exe _demos/narrow3/check_live.py` 用 `EmptyIslandDataProvider` 和显式 `TrackData` 夹具，通过真实 `IslandStackVisual._apply_visual_data` 检查来源歌词、逐词时间、位置与来源切换、队列、控制和空闲隐藏。这些是合成夹具，不是实际播放或真实截图。当前没有 PC 连接，真实来源截图、End／自动切歌、播放器交互、PC 测试和桌面表现仍待验证。既有 `check_input.py`、`check_layout.py` 继续覆盖演示的输入及布局。

本轮最终 180 帧暂停夹具基准通过双 DPR 的 CPU≤15% 硬断言：DPR1 歌词／列表为 5.19%／5.02%，DPR2 为 12.27%／10.07%；DPR2 原始 veil-only 60Hz 压力仍为 20.38%／19.38%。详细 paint 数值、内存成本和验证边界见 [capture-narrow4.md](../../docs/capture-narrow4.md)。这些是 Linux offscreen 合成夹具结果，不代表真实播放或原生 PC 表现。

真实 CLI 默认只连接外部系统媒体来源，不获得应用自有播放队列或私人 DJ；显式注入已有 provider 的 owner 类可以使用其自有队列能力。本演示没有替换常规应用。`check_live.py` 还覆盖窗口计时器及关闭、来源逐词点击和偏移／来源围栏、换行歌词与译文高度、收藏控件状态和列表滚动的 veil 缓存键。

2026-10-29 补充：暂停来源通过真实 provider 边界的 48 行合成夹具，在 DPR2 的三次淡入 CPU 为 14.49%／8.84%／8.82%；20 项 live 检查通过（2.731s）。它们不代表正在播放的原生 PC 验收，真实歌词／截图和活动源 CPU 仍待 PC 验证。
