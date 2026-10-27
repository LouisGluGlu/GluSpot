# GluSpot 窄窗 v2 实验

本目录使用假数据，不连接播放服务、不访问用户数据、不发声。A 把播放控制放入底部玻璃岛；B 把播放控制并入头部；C 保留宽窗四角的控制关系。

运行 `demo_island.py`、`demo_header.py` 或 `demo_corners.py`，Windows 可用对应批处理。`--grab DIR` 保存宽窗、窄窗、列表、收起头部和四色截图；`--seconds N --bench N` 分别报告首帧、稳态、宽窄动画和模拟按压期间的绘制时间。`check_layout.py` 检查三种尺寸的控件、内容安全区和时间文字矩形；A 的绘制内容有意延伸到岛后，安全区不含这一部分。不检查按压变形或过渡中的矩形。

键位：L 歌词，P 列表，Tab 切换，N/W 窄/宽，左右方向键调宽，1–4 切换配色，空格暂停，H 提示，Esc 退出。列表滚轮滚动，点击歌曲切换；播放、喜欢、DJ 可点击。

复用：`GlassControlRenderer`、`ControlFeedback`、`AlbumFlowBackgroundRenderer` 和封面调色板；transport 通过轻量适配器调用 `MusicSurfaceWindow._paint_transport_icon`，翻译调用 `_translation_symbol_path`，播放模式调用 `paint_music_source_icon`；其余图标使用项目音频输出、内容布局、窗口模式、音乐来源和私人 DJ 图标函数。玻璃图块限制每帧生成一块，过渡时缩放已有图块；A 的岛按约 0.1 秒采样背后内容；内容从岛上方 24px 渐隐到约 35%，列表末尾可滚到岛上方。transport 使用项目场景黑白墨色选择。抓图等待过渡、头部和玻璃刷新完成，再绘制两帧。

复刻：假封面、歌词布局和逐字填亮、模糊歌词、进度光轨、窗口过渡和头部收起。背景在项目渲染器结果上叠加取色径向渐变。当前行使用自动换行，最多显示两行；歌词逐词运动及完整歌词排版并非播放器的实现。图块使用缩小的采样图生成以控制绘制成本，切色时短暂复用旧色图块；按压的触点光使用演示层绘制。布局检查和基准只覆盖 offscreen 测量，尚未在 Windows 真机验证。
