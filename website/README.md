# GluSpot 官网

React / Vite 单页官网，与桌面程序的 6.417 源码、配置及发行包相互独立。

## 本地开发与验证

```sh
npm ci
npm run dev -- --port 4173
npm run build
npm run test:sites
npm run check:deploy
```

构建后的静态文件位于 `dist/client/`。`worker/index.js` 与现有 Sites 构建接口保持兼容；Cloudflare 配置见 `wrangler.jsonc`。

## 内容与交互

- 纯色深底；播放器内部复用桌面程序生产 Qt 背景渲染器的静音 H.264 采样，60 fps、12 秒循环。采样帧率不代表浏览器或屏幕实测帧率。
- 三首经来源核对的展示曲目，封面保持正向、完整正方形。Chace 与 Alicks 展示生产 Qt 30 fps 歌词动画及中文译文，输出为透明 WebM 与 PNG/WebP 静帧；Airport Bar 不添加未核实歌词。网页不播放歌曲音频；歌词进度是依据用户截图估算的静音演示时间，不与实际音频对齐。
- 岛／窗口／全屏的画布内切换；产品窗口演示尺寸为 700 × 380，品牌标志从居中开场缩小并移至页首。四种布局支持逐段自动进度、暂停与手动重选。
- Free、Top、Tasker、Finder 的岛态使用生产 Qt 绘制代码导出，背景采用 `solid_album` 专辑纯色角色，不使用 `sea_level`。
- 设置中的红绿灯、玻璃强度；设备加入／退出及音量演示。
- 桌面壁纸是模拟桌面，经过模糊和压暗。聆听房间以设备全灭场景为底图，叠加白色播放指示灯和低位声波；先前制作的四张彩色亮灯场景图保留作素材备份，不用于当前场景。
- 私人 DJ 的静音交互中，播放／暂停可触发下一首预览的播报态，约 6.5 秒后自动收束。五环动画使用生产 Qt 环形图标 helper 和封面原始取色；就绪状态使用 `#16c777`。应用另有加载蓝、失败红状态色，见 DJ provenance。
- 文案仅展示“在线音乐”和“系统音频抓取”；下载直接前往 GitHub 6.417 Release。

自动章节在页面隐藏、展示区离开视口、设置打开或减少动态效果时停止推进。全屏是网页中的产品展示状态，不会自动接管浏览器或操作系统全屏。

## Cloudflare

`npm run deploy` 发布静态资源与轻量资源路由，并绑定 `gluspot.top`。登录通过 Wrangler 官方 OAuth 流程完成；凭据不属于仓库或网页构建输入。

如启用 Cloudflare Workers Builds，连接本仓库的 `main`，根目录为 `website`，构建命令为 `npm run build`，部署命令为 `npm run deploy`。此处是配置说明，实际连接状态以部署记录为准。

## 素材与复现

素材来源、授权边界见 [素材声明](ASSET_CREDITS.md)。`tools/export_product_media.py` 可在桌面程序的 Python 环境中重建岛态和背景；它使用本地固定展示数据，不登录账号、不播放音频、不抓取桌面。界面设计验收见 [design-qa.md](design-qa.md)。

房间插画、白色播放指示灯及私人 DJ 动画的来源与备份校验记录见素材声明和 `public/assets` 下的 provenance JSON 文件。歌词动画、岛态、背景和 DJ 素材均有逐文件 provenance；房间叠层的音量联动属于网页演示行为。
