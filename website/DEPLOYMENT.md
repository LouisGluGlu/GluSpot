# 部署记录

- 当前产品版本：6.418；网站包版本：6.418.0。
- 下载规则：通过公开 GitHub Releases API 选择最高数字版本，包含测试版，要求安装版和便携版均已上传完成；链接与版本文字同步。API 不可用时转到发行列表，不固定旧版。
- 本轮下载修复验证（2026-10-23）：正式构建、23 项网站测试（5 项发行选择、14 项时间线、4 项路由／打包）及 Wrangler dry-run 通过。公开 API 实测选出 `v6.418`；本地正式构建在浏览器中确认两个下载入口均为 `v6.418`，版本文字同步显示 `6.418`。未改动桌面程序及既有发行附件。
- 目标仓库：LouisGluGlu/GluSpot，`main`，目录 `website/`。
- 当前托管：Cloudflare Pages，项目 `gluspot-website`，通过现有 GitHub 集成自动构建与部署。
- 构建配置：生产分支 `main`；根目录 `website`；构建命令 `npm run build`；输出目录 `dist/client`。
- 官网地址：[gluspot.top](https://gluspot.top/)；Pages 项目地址：[gluspot-website.pages.dev](https://gluspot-website.pages.dev)。
- 此前分镜部署的源码提交：`e6402ea1f09f5bc3a7e95b08600ff7eb6ea27815`；当时 Cloudflare 生产部署来源与该提交一致，桌面程序与发行版本为 6.417。
- 发布状态：2026-10-23，Cloudflare Pages 部署成功，官网通过 HTTPS 正常加载开场动画与新分镜页面；线上 JS/CSS 文件名与该版本本地构建一致。后续推送到 `main` 会触发新的 Pages 构建与部署。
- 自定义域名：Cloudflare 显示 `gluspot.top` 为活动状态、SSL 已启用；根域 CNAME 指向 `gluspot-website.pages.dev`，域名注册保留在阿里云。
- 备选部署：保留 `wrangler.jsonc` 与 `npm run deploy` 的 Cloudflare Workers Static Assets 方案；当前 Pages 自动部署使用上述构建配置。

此文件不记录账户邮箱、账户 ID、Token、OAuth 链接或个人电脑路径。
