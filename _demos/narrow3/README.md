# 窄窗 v3 实验（未完成）

本目录是可运行的探索版本，**尚未达到 v3 规格，不能作为验收结果**。不要将其视为生产界面或发布版本。

- A「安静」：默认隐藏底部播放行，指针进入底部时显示。
- B「一行」：底部播放行常显。
- C「居中」：与 B 相同，歌词水平居中。

Windows 可运行对应 `.bat`。L、P、Tab 切歌词和列表；N、W 切窄窗和宽窗；左右键切歌；1–4 切封面；Space 暂停或继续；H 显隐提示；Esc 退出。程序使用临时 HOME、APPDATA、LOCALAPPDATA，数据由测试夹具提供，不播放音频。

## 实现与不足

宽版绘制调用 `MusicSurfaceWindow.paintEvent`。窄版复用 `_art_palette`、`_paint_background`、`_paint_surface_glass`、`_scene_control_ink`、`_surface_glass_backdrop`、`_paint_transport_icon`、`_paint_content_layout_icon`、`_surface_font`、`_lyric_raster`、`_blurred_lyric_raster`、`_lyric_group_blur_level`、`_future_lyric_color`，队列选中行调用生产 `GlassControlRenderer.paint`；封面色来自 `WindowsMediaProvider._colors_from_art`。窄版标题、队列、歌词位置和进度条是按目标几何重新排布，队列封面按行循环使用四张素材。列表选歌和左右键会切换演示封面、背景取色和曲目信息；喜欢状态仅保存在内存。

演示歌词使用逐字时间夹具和生产 `LyricReadingRenderer.paint` 绘制当前句，换句位置采用 420ms 缓动；时间是演示数据，不是实际歌曲的歌词时序。其他行仍使用生产栅格模糊。歌词和列表分别采用 32px / 28px 边缘渐隐。

顶栏和底栏使用独立 `motion.py` 透明度状态：顶栏停留 140ms 后逐项错开 20ms 淡入，离开后延迟淡出；A 底栏响应底部悬停。列表中顶栏和底栏顺序让路。玻璃反馈复用生产 `ControlFeedback`，队列选中胶囊也传递对应状态，但队列自身的交互反馈和全部边界仍未验证。音频输出等次级按钮仅绘制图标，不执行动作；没有真实设备面板、可拖动进度或网络音频。

**仍未完成**：宽窄窗口仍然硬切，缺少 420ms 单封面飞行和正文交叉淡化；没有过渡截图；键盘焦点保持可见、完整命中和玻璃反馈仍需完成；背景时钟未冻结，抓图等待透明度状态并重画两帧，但没有证明所有生产材质刷新完毕。`glass_count` 使用 renderer 实际记录角色，生产宽版也如实记录，其控件数不受窄版预算约束。图像比例裁切和真实桌面体验仍需验证。

`check_layout.py` 覆盖 36 静态组合、A 底栏悬停及模拟显隐预算；没有验证按压胀大、宽窄动画和文字墨迹。`--bench` 分别记录稳态、按压和显隐的真实绘制样本，首帧单列；`--bench` 会覆盖 `--seconds` 直到每段达到样本数。宽窄动画未实现，没有该段样本。结果是 offscreen 测量，不能代表原生窗口的显示帧率。320 宽只是 demo 检查点，生产原生窗口最小尺寸仍为 330×400。

运行 `python -X utf8 _demos/narrow3/grab_all.py --out DIR` 可生成三方案截图、生产夜海宽窗参考和 `narrow3_overview.png`。本实验未通过 v3 完整验收，不应作为发布依据。
