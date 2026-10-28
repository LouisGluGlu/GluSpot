# 窄窗 v3 演示

本目录是独立实验，不是生产界面或发布版本。Windows 启动器：`窄窗v3-方案A-静态.bat`、`窄窗v3-方案B-悬停.bat`（使用启动器中配置的 Python 环境）。

A 静态空闲时隐藏全部控件和封面标题；歌词、列表以 20px 边距铺满窗口。歌词使用 31px 基线间距，当前行基线在 360×640 时为 211.2px；21px 粗体保证此行距下字形不重叠，当前行及相邻两行保持清晰。B 指针进入或触摸按下后叠加控件，字号、行距、视口与滚动位置均不改变。无指针／触摸 1200ms 后隐藏。控件和 veil 共用一个 300ms cubic ease-out，反向切换从当前透明度续接。

共用 `VeilOverlay` 在顶部 0–88px、底部 512–640px 叠加渐进模糊和黑色暗化；64–88px、512–544px 为羽化带，最大逻辑模糊半径16px、暗化alpha0.55；中部不受 veil 影响。透明内容的模糊带在计时器内预计算为半分辨率缓存（含 DPR），按文本布局、主题、DPR、31px取整滚动位置索引；最多六张并按 LRU 淘汰。不同半径的采样条带和静止羽化结果合并在同一张缓存图，滚动残差只移动内容纹理，羽化边界留在原处。绘制帧仅混合清晰／缓存模糊内容，再暗化底图，不在 paint 中重模糊。半分辨率仅用于 veil，生产 clear-glass／背景绘制链不变。

周边歌词静态栅格复用，当前行继续调用生产逐词阅读；玻璃控件仍由生产 renderer 绘制，但合成结果在计时器内准备，淡入淡出期间复用。输入反馈与主题改变立即失效，静止玻璃按10Hz刷新。列表选中玻璃也在淡入淡出期间复用。300ms显隐期间调用生产背景的 `advance_frame=False` 复用现有栅格，两态稳态恢复正常背景更新；歌词阅读不停止。应用原有33ms绘制节拍不变；性能脚本分别记录此节拍下300ms淡入CPU和60Hz压力数值。

顶部封面标题收进88px band。底部居中播放三键实际绘制和点击区域均44×44，下方40px高行依次为喜欢与加入歌单胶囊、循环、布局和私人 DJ；无 DJ 时循环和布局按钮在180ms内移向右侧。胶囊右侧将当前歌曲加入演示内存中的歌单。无进度条。DJ开启显示绿色环，说话时环旋转；状态仅保存在内存。

L / P 切歌词 / 列表，Tab 切换内容；N / W 切窄窗 / 宽窗；← / → 切歌；1–4 切封面；Space 暂停 / 继续；D 切换 DJ 是否可用，点击 DJ 开关，S 切换模拟说话；H 显隐提示；Esc 退出。循环按钮切换顺序、随机、单曲循环图标；布局按钮切换歌词 / 列表。使用临时 HOME、APPDATA、LOCALAPPDATA 和测试夹具，不播放音频。

宽版调用生产 `MusicSurfaceWindow.paintEvent`；窄版复用生产背景、玻璃、歌词栅格和逐字阅读绘制，播放三键调用生产 `_paint_transport_icon`，DJ 使用生产 `GlassControlRenderer` 的 `GLASS_DJ` 和 `paint_personal_dj_icon`，封面取色调用生产 provider。歌词时间是演示夹具，不代表实际歌曲。宽窄切换仍为硬切，真实桌面交互、材质和长时间表现尚未验证。

运行 `python -X utf8 _demos/narrow3/check_layout.py` 检查 A/B 布局和显隐；`python _demos/narrow3/check_input.py` 检查真实Qt进入、触摸生命周期、44px点击边界及宽窗委派。运行 `python -X utf8 _demos/narrow3/grab_all.py --out DIR` 生成 A/B 歌词与列表截图、生产宽窗参考及 `narrow3_overview.png`。B 截图固定在展开状态。`python _demos/narrow3/check_veil.py --out DIR --frames 180` 在独立 DPR1／2 进程验证清晰中部、零位移、模糊／暗化、缓存及 paint p95≤16.7ms／p99≤24ms，同时输出 A／B-top／B-bottom 截图与性能 JSON。Linux 使用 `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 HOME=/tmp/qtsandbox /workspace/gluspot/qtvenv/bin/python`。离屏耗时不代表原生显示帧率。

基线 `b1bf5ca` 审查：旧 A 虽然铺满，但实际行距45.675px、基线约231px，旧B还把视口从20–620收缩为190–414；列表也随显隐重排。旧 transport 仅图标44px，点击槽为100×60；封面越出顶部band。本轮按 PRD 修正这些问题，保留铺满。为了让31px行距的截图上下都有真实示例行，演示歌词重复扩展为48行，截图选中段53.6s。上述获授权的基线修正会改变旧 A 像素，因此与“旧A像素差<0.5%”要求存在明确冲突；验收以修正后A/B内容零几何变化和清晰中部像素一致为准。
