# GluSpot 官网

React / Vite 单页官网，独立构建；下载入口跟随桌面程序最新公开 Windows 发行版。

## 本地开发与验证

```sh
npm ci
npm run dev -- --port 4173
npm run build
npm run test:sites
npm run test:story
npm run test:releases
npm run check:deploy
```

构建后的静态文件位于 `dist/client/`。线上使用 Cloudflare Pages 的 GitHub 集成自动构建；`worker/index.js` 与现有 Sites 构建接口保持兼容，`wrangler.jsonc` 保留为 Workers 部署方案。

## 内容与交互

- 纯色深底；播放器内部复用桌面程序生产 Qt 背景渲染器的静音 H.264 采样，60 fps、12 秒循环。采样帧率不代表浏览器或屏幕实测帧率。
- 三首经来源核对的展示曲目，封面保持正向、完整正方形。Chace 与 Alicks 展示生产 Qt 30 fps 歌词动画及中文译文，输出为透明 WebM 与 PNG/WebP 静帧；Airport Bar 不添加未核实歌词。网页不播放歌曲音频；歌词进度是依据用户截图估算的静音演示时间，不与实际音频对齐。
- 岛／窗口／全屏的画布内切换；产品窗口演示尺寸为 700 × 380，品牌标志停留 5 秒或在下滑时开始缩小并移至页首。四种播放布局通过播放器内模式按钮循环选择。
- Free、Top 的岛态使用生产 Qt 绘制代码及 `solid_album` 专辑纯色角色；Tasker、Finder 采用原生 `transparent` 无背景栏样式，不使用 `sea_level`。
- 独立滚动章节展示四种原生岛态，完整自动章节为 13.5 秒；岛、窗口、全屏演示章节为 10 秒。两章均按 3 秒停留和 0.5 秒过渡计时，可暂停、拖动进度，也可在章节贴合视口时用滚轮或单指触摸手势控制时间线。
- 首页底部横条与第一座岛共用同一个 DOM 元素，随滚动沿上抬后回落的弧线放大。两段章节均完成后才放行下滚；鼠标滚轮灵敏度为先前的 1/9，用现有 rAF 时钟进行 140 毫秒连续补间，键盘与触摸保留各自步幅。
- 岛态章节使用四张生产 painter 的固定截图。歌词文本来自用户提供的片段，但没有原始歌词时间戳；截图不代表源音乐时序，章节切换时间是网页演示编排。逐字高光短句同样使用明确编写的静音字符时钟，30 fps、240 帧、8 秒循环，不与歌曲音频对齐。
- 设置中的红绿灯、玻璃强度；设备加入／退出及音量演示。
- 桌面壁纸是模拟桌面，经过模糊和压暗。聆听房间以设备全灭场景为底图，叠加白色播放指示灯和低位声波；先前制作的四张彩色亮灯场景图保留作素材备份，不用于当前场景。
- 私人 DJ 的静音交互中，播放／暂停可触发下一首预览的播报态，约 6.5 秒后自动收束。五环动画使用生产 Qt 环形图标 helper 和封面原始取色；就绪状态使用 `#16c777`。应用另有加载蓝、失败红状态色，见 DJ provenance。
- 文案仅展示“在线音乐”和“系统音频抓取”；下载直接前往 GitHub 最新公开 Windows Release，包含测试版。页面加载时查询最近 100 条公开发行记录，按数字版本比较，跳过草稿、纯源码及安装版／便携版尚未上传完整的版本；两个入口与版本文字共享结果。查询失败或超过 5 秒时打开发行列表，不固定旧版本。当前公开版本为 6.418；旧演示素材的 6.417 provenance 保留原始来源记录。

自动章节在页面隐藏、展示区离开视口、设置打开或减少动态效果时停止推进。全屏是网页中的产品展示状态，不会自动接管浏览器或操作系统全屏。

## Cloudflare

官网地址为 [gluspot.top](https://gluspot.top/)，Cloudflare Pages 项目地址为 [gluspot-website.pages.dev](https://gluspot-website.pages.dev)。2026-10-23 已验证官网 HTTPS 访问、开场动画与新分镜页面；Cloudflare 显示自定义域名活动、SSL 已启用。Pages 连接 `LouisGluGlu/GluSpot` 仓库的 `main` 分支，根目录为 `website`，构建命令为 `npm run build`，输出目录为 `dist/client`；后续推送到 `main` 会触发自动构建与部署。

`gluspot.top` 通过 CNAME 指向 Pages 项目，域名注册保留在阿里云。已验证的源码版本与部署结果见 [部署记录](DEPLOYMENT.md)。

`wrangler.jsonc` 与 `npm run deploy` 保留为 Workers Static Assets 的备选方案。该命令使用 Wrangler 发布静态资源与轻量资源路由，并包含自定义域名配置；Pages 的 GitHub 自动部署不使用此命令。若改用 Workers Builds，根目录同样为 `website`，构建命令为 `npm run build`，部署命令为 `npm run deploy`。Wrangler 登录使用官方 OAuth 流程，凭据不属于仓库或网页构建输入。

## 素材与复现

素材来源、授权边界见 [素材声明](ASSET_CREDITS.md)。`tools/export_product_media.py` 可在桌面程序的 Python 环境中重建岛态和背景；它使用本地固定展示数据，不登录账号、不播放音频、不抓取桌面。界面设计验收见 [design-qa.md](design-qa.md)。

房间插画、白色播放指示灯、岛态章节、逐字高光及私人 DJ 动画的来源与备份校验记录见素材声明和 `public/assets` 下的 provenance JSON 文件。岛态章节素材位于 `island-story/`，逐字高光位于 `highlight/`；相关时序、原生绘制来源及静帧／动画关系均记录在 provenance 中。房间叠层的音量联动属于网页演示行为。
