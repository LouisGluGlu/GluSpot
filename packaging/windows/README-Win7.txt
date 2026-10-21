GluSpot 6.412 — Windows 7 兼容测试版
构建日期：2026-10-17

适用系统
Windows 7 SP1 64 位。此包不支持 32 位 Windows。
需要提供 AddDllDirectory 的系统更新（KB2533623 或其替代更新），
以及 Universal C Runtime（KB2999226 或其替代更新）。
安装程序在覆盖文件前检查这两项；便携包也需要相同的系统更新。
需要正常的显卡、声卡驱动及系统媒体组件；N/KN 版可能需要 Media Feature Pack。

使用方式
请完整解压并保留 GluSpot.exe 和 _internal 文件夹；不要单独复制 EXE。
首次启动只启用程序内播放。此版本不提供 SMTC 系统音频抓取、
系统媒体发布及相应的耳机上一首/下一首控制。
本机音频使用 Windows 默认输出；如需更换本机输出，请在 Windows 中选择。
配置、账号和缓存保存在程序目录下的 GluSpot 文件夹。
升级请使用安装程序并保留已有目录；卸载会清除该安装拥有的配置。

验证边界
本包采用独立的 Python 3.8、Qt 5 和 Node 13 运行时；它们已结束上游支持。
本次已在 Windows 11 上检查兼容运行时、静音本地播放和界面。
尚未取得真实 Windows 7 设备/虚拟机的安装、登录、音频及驱动验收结果。
HomePod/AirPlay、多设备同步、播放中默认声卡迁移及真实平台登录尚未在 Win7 验证。
这是未签名的内部兼容测试版，不是已完成 Win7 实机认证的正式发行版。

运行诊断
可在命令提示符中执行（先创建报告所在文件夹）：
GluSpot.exe --legacy-package-check "C:\Temp\GluSpot-Win7-report.json"
诊断使用临时数据、隐藏界面及静音本地 WAV，不登录账号或联系接收设备。
诊断结果仅说明实际运行该命令的系统，不能用新系统的结果代替 Win7 验收。

许可证及可替换组件
见 _internal\licenses\legacy-runtime、_internal\online_music 和
_internal\native\licenses 中的说明、许可及对应源码材料。
