# 窄窗 v3 演示

本目录是独立实验，不是生产界面或发布版本。Windows 启动器：`窄窗v3-方案A-静态.bat`、`窄窗v3-方案B-悬停.bat`（使用启动器中配置的 Python 环境）。

A 静态空闲时隐藏底部控件；B 鼠标移动后显示控件，离开窗口后收起。底部依次为居中的大号播放三键、循环及布局模式按钮，以及左下收藏、右下私人 DJ；无 DJ 时循环和布局按钮在约 180 ms 内移至底行右侧。没有进度条。DJ 开启显示绿色环，说话时环旋转。演示状态仅保存在内存。

L / P 切歌词 / 列表，Tab 切换内容；N / W 切窄窗 / 宽窗；← / → 切歌；1–4 切封面；Space 暂停 / 继续；D 切换 DJ 是否可用，点击 DJ 开关，S 切换模拟说话；H 显隐提示；Esc 退出。循环按钮切换顺序、随机、单曲循环图标；布局按钮切换歌词 / 列表。使用临时 HOME、APPDATA、LOCALAPPDATA 和测试夹具，不播放音频。

宽版调用生产 `MusicSurfaceWindow.paintEvent`；窄版复用生产背景、玻璃、歌词栅格和逐字阅读绘制，播放三键调用生产 `_paint_transport_icon`，DJ 使用生产 `GlassControlRenderer` 的 `GLASS_DJ` 和 `paint_personal_dj_icon`，封面取色调用生产 provider。歌词时间是演示夹具，不代表实际歌曲。宽窄切换仍为硬切，真实桌面交互、材质和长时间表现尚未验证。

运行 `python -X utf8 _demos/narrow3/check_layout.py` 检查 A/B 布局和显隐；运行 `python -X utf8 _demos/narrow3/grab_all.py --out DIR` 生成 A/B 歌词与列表截图、生产宽窗参考及 `narrow3_overview.png`。B 截图固定在展开状态。Linux 无显示环境可设置 `QT_QPA_PLATFORM=offscreen`。`--bench` 提供合成绘制耗时，不能代表原生显示帧率。
