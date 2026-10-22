# 素材与第三方声明

网站自有代码沿用仓库 MIT 许可；下列素材不因此变为 MIT 授权。

- GluSpot 标志、安装循环动画与应用图标：复用本仓库原素材及其原有来源声明。
- Chace — **The Rain Song**，专辑 **Belated Suffocation**：[官方发行页](https://chace.lnk.to/BelatedSuffocation)。封面为权利人作品，440 × 440 原图，未旋转或改绘。
- Alicks — **Ibuprofen**，专辑 **Unwritten**：[艺人 Bandcamp](https://alicks14.bandcamp.com/track/ibuprofen)。封面为权利人作品，1200 × 1200 原图，未旋转或改绘。
- goodboy noah — **Airport Bar (Prod. by Evil Needle)**：[艺人 SoundCloud 原始上传](https://soundcloud.com/goodboynoah/airport-bar-prod-by-evil-needle)，2015-07-30。1080 × 1080 原图；页面未列专辑信息，不与后来的 remix 混用。该曲演示仅展示曲目名，不伪造歌词。
- 两首歌的短歌词与中文译文取自用户提供的识别截图，仅作无音频界面展示；网页演示时间依据截图估算，不与实际音频对齐。Airport Bar 不添加未核实歌词；相关音乐及歌词版权归各自权利人。
- 岛态 PNG 由 GluSpot 6.417 生产 Qt 绘制代码和上述固定封面生成，使用 `solid_album` 专辑纯色角色，不使用 `sea_level`。静态外观由生产岛态 painter 导出。播放器背景由生产专辑流渲染器采样为静音 60 fps、12 秒 H.264 往返循环；采样帧率不代表浏览器或屏幕实测帧率。
- 模拟桌面壁纸与房间场景：由本次对话中的 OpenAI ImageGen 工具生成，不采集或呈现用户桌面；此前素材未记录模型或提示词。当前房间以设备全灭图为底，叠加白色播放指示灯与低位声波；先前生成的四张彩色亮灯场景图保留作素材备份，不用于当前合成。声波和播放指示灯随音量演示变化。插画用于展示球形、圆柱形 Wi-Fi 音箱、耳机和木质音箱，不构成设备兼容性或品牌合作承诺。`scene-provenance.json` 记录房间、壁纸及既有叠层素材；`playback-light-provenance.json` 记录白色播放指示灯的 PNG/WebP 文件、透明度、尺寸和 SHA-256。该指示灯由 OpenAI ImageGen 生成，提示词为 “Shallow neutral white playback indicator, genuinely transparent, no rainbow/Siri effect, no object/text”。
- 私人 DJ：六个动态 WebP 和对应静帧使用固定展示封面与静音夹具。播报态直接调用生产 `local/personal_dj_icon.py::paint_personal_dj_icon` 五环 helper，仅绘制环形图标；就绪态保留生产 Qt 控件中的绿色圆环。每个动态文件按 24 Hz 采样 60 次，循环总时长 2500 ms；相邻重复采样在编码前合并，因此 WebP 实际帧数为 45–58。环色来自当前展示封面的原始取色，就绪状态为 `#16c777`。加载蓝、失败红属于应用状态色；网页只演示播报和就绪。DJ 动态图、静帧和 provenance 均有逐文件记录；导出不涉及账号、网络、音频或桌面捕获。
- 图标库：Phosphor Icons，MIT；其余图标沿用仓库资产。
- 标题字体：Adobe Source Han Sans 的字形子集，重命名为 `GluSpotDisplaySubset`。保留 [SIL OFL 1.1](public/assets/font-LICENSE.txt)。正文使用系统字体栈。

封面公开来源 URL、尺寸与版权归属记录在 `public/assets/provenance*.json`；导出参数在 `public/assets/islands/provenance.json`。歌词时间、背景采样、私人 DJ 与播放指示灯的文件信息分别见 `public/assets/lyrics/provenance.json`、`background-provenance.json`、`dj/provenance.json` 和 `playback-light-provenance.json`。网页不包含音频文件、登录二维码或账号资料。
