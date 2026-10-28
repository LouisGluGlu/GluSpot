# 窄窗 v3 演示

本目录是独立实验，不是生产界面或发布版本。Windows 启动器：`窄窗v3-方案A-静态.bat`、`窄窗v3-方案B-悬停.bat`（使用启动器中配置的 Python 环境）。

A 静态空闲时隐藏全部控件和封面标题，歌词或列表以 20 px 边距铺满窗口；歌词约 31.5 px 粗体左对齐（随宽度线性缩放），当前行位于约 33% 高度，上下柔和渐隐，后续行一直铺到窗口下三分之一；列表同样占满高度并隐藏小标题。B 鼠标移动后显示控件和封面标题，内容缩回中间区域，离开窗口后收起。显隐和内容几何在约 340 ms 内以 smoothstep 同步过渡，A↔B 字号与位置连续变化，不会跳变。底部为居中的播放三键，下方同一行依次为喜欢与加入歌单胶囊、循环、布局模式和私人 DJ；无 DJ 时循环和布局按钮在约 180 ms 内移向右侧。胶囊右侧将当前歌曲加入演示内存中的歌单。没有进度条。DJ 开启显示绿色环，说话时环旋转。演示状态仅保存在内存。

L / P 切歌词 / 列表，Tab 切换内容；N / W 切窄窗 / 宽窗；← / → 切歌；1–4 切封面；Space 暂停 / 继续；D 切换 DJ 是否可用，点击 DJ 开关，S 切换模拟说话；H 显隐提示；Esc 退出。循环按钮切换顺序、随机、单曲循环图标；布局按钮切换歌词 / 列表。使用临时 HOME、APPDATA、LOCALAPPDATA 和测试夹具，不播放音频。

宽版调用生产 `MusicSurfaceWindow.paintEvent`；窄版复用生产背景、玻璃、歌词栅格和逐字阅读绘制，播放三键调用生产 `_paint_transport_icon`，DJ 使用生产 `GlassControlRenderer` 的 `GLASS_DJ` 和 `paint_personal_dj_icon`，封面取色调用生产 provider。歌词时间是演示夹具，不代表实际歌曲。宽窄切换仍为硬切，真实桌面交互、材质和长时间表现尚未验证。

运行 `python -X utf8 _demos/narrow3/check_layout.py` 检查 A/B 布局和显隐；运行 `python -X utf8 _demos/narrow3/grab_all.py --out DIR` 生成 A/B 歌词与列表截图、生产宽窗参考及 `narrow3_overview.png`。B 截图固定在展开状态。Linux 无显示环境可设置 `QT_QPA_PLATFORM=offscreen`。`--bench` 提供合成绘制耗时，不能代表原生显示帧率。
