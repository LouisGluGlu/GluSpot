# 窄窗 v3 实验（未完成）

本目录是可运行的探索版本，**尚未达到 v3 规格，不能作为验收结果**。不要将其视为生产界面或发布版本。

- A「安静」：默认隐藏底部播放行，指针进入底部时显示。
- B「一行」：底部播放行常显。
- C「居中」：与 B 相同，歌词水平居中。

Windows 可运行对应 `.bat`。L、P、Tab 切歌词和列表；N、W 切窄窗和宽窗；左右键切歌；1–4 切封面；Space 暂停或继续；H 显隐提示；Esc 退出。程序使用临时 HOME、APPDATA、LOCALAPPDATA，数据由测试夹具提供，不播放音频。

## 实现与不足

宽版绘制调用 `MusicSurfaceWindow.paintEvent`。窄版复用 `_art_palette`、`_paint_background`、`_paint_surface_glass`、`_scene_control_ink`、`_surface_glass_backdrop`、`_paint_transport_icon`、`_paint_content_layout_icon`、`_surface_font`、`_lyric_raster`、`_blurred_lyric_raster`、`_lyric_group_blur_level`、`_future_lyric_color`，队列选中行调用生产 `GlassControlRenderer.paint`；封面色来自 `WindowsMediaProvider._colors_from_art`。窄版标题、队列、歌词位置和进度条是按目标几何重新排布，队列暂时用同一张封面。列表选歌和左右键会切换演示封面、背景取色和曲目信息；喜欢状态仅保存在内存。

当前缺少规格要求的 420ms 封面飞行及正文交叉淡化、真实逐词进度、歌词平滑换句、控件显隐时序、完整命中及玻璃按压/悬停反馈、可拖动进度、图像比例裁切和完整的确定性截图状态机。音频输出等次级按钮仅绘制图标，不执行动作。宽窗动画调度在实验中被停止。歌词和列表分别采用 32px / 28px 边缘渐隐；玻璃反馈已部分接入生产 `ControlFeedback`，尚未完成全部控件和边界验证。截图中 `glass_count` 是按实验状态估算，不是对宽版真实控件计数。抓图使用固定等待时间，不能证明生产材质刷新已结束。

`check_layout.py` 仅检查静态矩形和玻璃预算，不覆盖文字墨迹、时间区、按压胀大、动态帧或 A 的底栏悬停。`--bench` 当前仅输出混合绘制样本，参数 N 限制统计样本数；没有分离切换和按压，不能用于规格性能验收。320 宽只是 demo 检查点，生产原生窗口最小尺寸仍为 330×400。

运行 `python -X utf8 _demos/narrow3/grab_all.py --out DIR` 可生成三方案截图、生产夜海宽窗参考和 `narrow3_overview.png`，并打印各步骤耗时。截图仍采用固定等待，不能作为动画或材质刷新完成的证据。
