# 部署记录

- 产品版本：6.417；网站包版本：6.417.0。
- 目标仓库：LouisGluGlu/GluSpot，`main`，目录 `website/`。
- 当前托管：Cloudflare Pages，项目 `gluspot-website`，通过现有 GitHub 集成自动构建与部署。
- 构建配置：生产分支 `main`；根目录 `website`；构建命令 `npm run build`；输出目录 `dist/client`。
- 官网地址：[gluspot.top](https://gluspot.top/)；Pages 项目地址：[gluspot-website.pages.dev](https://gluspot-website.pages.dev)。
- 已验证部署的源码提交：`e6402ea1f09f5bc3a7e95b08600ff7eb6ea27815`；Cloudflare 生产部署来源与该提交一致，桌面程序与发行版本保持 6.417。
- 发布状态：2026-10-23，Cloudflare Pages 部署成功，官网通过 HTTPS 正常加载开场动画与新分镜页面；线上 JS/CSS 文件名与该版本本地构建一致。后续推送到 `main` 会触发新的 Pages 构建与部署。
- 自定义域名：Cloudflare 显示 `gluspot.top` 为活动状态、SSL 已启用；根域 CNAME 指向 `gluspot-website.pages.dev`，域名注册保留在阿里云。
- 备选部署：保留 `wrangler.jsonc` 与 `npm run deploy` 的 Cloudflare Workers Static Assets 方案；当前 Pages 自动部署使用上述构建配置。

此文件不记录账户邮箱、账户 ID、Token、OAuth 链接或个人电脑路径。
