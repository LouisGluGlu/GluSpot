# 窄窗 v3 演示

本目录是独立实验，不是生产界面或发布版本。Windows 启动器：`窄窗v3-方案A-静态.bat`、`窄窗v3-方案B-悬停.bat`（使用启动器中配置的 Python 环境）。

- A「静态」始终只显示返回、内容切换、封面标题和歌词或列表。
- B「悬停」在鼠标进入窗口并移动后展开四个顶栏次级按钮及底部上一首、暂停/播放、下一首和喜欢胶囊；离开后收起。没有进度线和时间。

L / P 分别切歌词 / 列表，Tab 切换内容；N / W 切窄窗 / 宽窗；← / → 切歌；1–4 切封面；Space 暂停 / 继续；H 显隐提示；Esc 退出。列表点击及上一首 / 下一首会切换演示曲目；喜欢仅保存在内存。次级按钮和喜欢胶囊右侧的添加图标仅供展示。使用临时 HOME、APPDATA、LOCALAPPDATA 和测试夹具，不播放音频。

宽版调用生产 `MusicSurfaceWindow.paintEvent`；窄版复用生产背景、玻璃、歌词栅格和逐字阅读绘制，播放三键调用生产 `_paint_transport_icon`，封面取色调用生产 provider。歌词时间是演示夹具，不代表实际歌曲。宽窄切换仍为硬切，真实桌面交互、材质和长时间表现尚未验证。

运行 `python -X utf8 _demos/narrow3/check_layout.py` 检查 A/B 布局和显隐；运行 `python -X utf8 _demos/narrow3/grab_all.py --out DIR` 生成 A/B 歌词与列表截图、生产宽窗参考及 `narrow3_overview.png`。B 截图固定在展开状态。Linux 无显示环境可设置 `QT_QPA_PLATFORM=offscreen`。`--bench` 提供合成绘制耗时，不能代表原生显示帧率。
