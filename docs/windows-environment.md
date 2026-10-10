# Windows 项目环境与跨文件夹修复

适用源码版本 `6.412`。每个 checkout / worktree 使用自己的 `.venv`，应用入口仍为 `local/main.py`。

## 截图错误的原因

旧 `setup-env.bat` 在同一个 CMD 括号块中先设置 `BASEPY`，再执行 `%BASEPY%`。CMD 在执行块之前展开变量，因此首次没有 `.venv` 时，Python 命令可能变成 `""`，随后误报需要安装 Python。本机已安装 Windows CPython 3.12.10；临时目录的真实 CMD 子进程复现了相同错误。这与 Python 未安装是两个不同问题。

新的批处理入口把环境选择、创建和检查委托给 `packaging/windows/setup-environment.ps1`，避免 CMD 的块内变量展开问题。脚本使用 Windows CPython 3.12 x64，并实际检查解释器身份；不接受本机 PATH 排在前面的 MSYS Python。可以用 `-PythonPath` 或 `GLUSPOT_PYTHON` 显式指定解释器。

## 使用

当前项目双击 `setup-env.bat` 或 `repair-env.bat`。`launch.bat` 在启动前检查本项目环境，缺失或失效时调用同一修复流程；`build-exe.bat` 先检查环境，再执行原有 native / PyInstaller 构建。

从任意工作目录指定另一个 GluSpot 项目文件夹，路径含空格时加引号：

```powershell
& 'C:\路径\GluSpot\repair-env.bat' -NoPause 'D:\项目副本\GluSpot 测试'
```

也可把一个或多个项目文件夹拖到 `repair-env.bat`，或在命令后依次传入多个路径。每个文件夹都必须有 `local/main.py` 和 `requirements.txt`；不是 GluSpot 项目的目录会被拒绝。

需要在 PowerShell 中传入多个项目或指定 Python 时，可直接调用核心脚本：

```powershell
& 'C:\路径\GluSpot\packaging\windows\setup-environment.ps1' `
  -ProjectRoot 'D:\项目一\GluSpot', 'D:\项目二\GluSpot 测试' `
  -PythonPath 'C:\Users\用户名\AppData\Local\Programs\Python\Python312\python.exe' `
  -NoPause
```

`-CheckOnly` 只检查，不安装、不修改环境。`launch.bat -CheckOnly -NoPause` 也不会启动应用；适合在复制文件夹后先诊断。`-NoPause` 用于自动化，保留退出码。

## 环境归属和恢复

脚本检查 `.venv` 的解释器版本、平台、架构、实际环境位置及依赖版本和导入状态。它按 UTF-8 读取 `pyvenv.cfg`，核对原始创建路径和基础解释器，并拒绝通过 `.pth` 引入其他项目的依赖目录。Windows 复制后的环境即使 `sys.prefix` 已指向新目录，工具入口仍可能引用旧路径；创建记录不匹配也会进入备份重建流程。不能把另一个 checkout 的虚拟环境路径当作本项目环境。复制项目时无需复制 `.venv`，到新路径运行修复脚本即可。

遇到坏掉或迁移后失效的本地环境，修复流程先将它改名为本项目内的 `.venv.backup-*`，再创建新的 `.venv`，保留旧内容。环境路径和祖先路径包含目录连接或符号链接时拒绝改动。环境、备份、运行日志和预览均被 Git 忽略。

安装失败会保留诊断及非零退出码，可修复网络或 Python 安装后重试。pip 默认读取超时为 60 秒、连接重试为 5 次，尊重用户已有的 `PIP_DEFAULT_TIMEOUT`、`PIP_RETRIES` 和代理配置。脚本不安装 Python，也不安装 native C++ 工具链。在线音乐的 Node 运行时仍按 [在线运行时说明](online-music-runtime.md) 单独准备。

只检查时使用隔离模式、禁用字节码写入，并在加载 site / `.pth` 前安装文件写入审计；检查不创建环境、日志、COM 包装缓存或运行应用。导入依赖不能通过时返回非零值，供修复流程处理。

退出码：`0` 就绪，`2` 项目路径无效，`3` 缺少兼容 Python，`4` 环境路径被重定向，`5` 创建失败，`6` pip 安装失败，`7` 环境或依赖检查失败。多项目调用会继续检查后续目录，并保留首次非零退出码。

## 验证记录

15 项 Windows 子进程集成测试全部通过（73.292 秒，无跳过）：非当前目录、中文/空格/`&`/`!` 路径、真实 CPython venv 创建与离线 pip 安装、有效环境保留、复制环境备份重建、解释器缺失、安装失败退出码、多目录继续处理、重定向拒绝、所有批处理入口的只读快照、导入和 `.pth` 写入阻断。依赖在这些测试中是明确标记的惰性测试模块；该结果验证脚本，不替代真实应用依赖验收。环境与品牌专测的另一次 25 项检查全部通过（73.027 秒）。旧脚本故障和 UTF-8 配置读取问题均经过真实子进程复现，证据留在 ignored `.runtime/environment-tests`。

当前 worktree 已通过核心脚本实际建立独立 `.venv` 并安装完整 `requirements.txt`：CPython 3.12.10 x64、PySide6 6.12.0、NumPy 2.5.3、Pillow 12.3.0。安装中遇到 PyPI 大文件读取超时，保留非零退出码后通过本机现有代理重试成功；本次将该环境 pip 更新为 26.2.1。没有把代理地址或其他 checkout 的解释器路径写入生产脚本。`pip check` 通过。

从 Downloads 目录调用本 worktree 的 `launch.bat -CheckOnly -NoPause`、`build-exe.bat -CheckOnly -NoPause` 与 `repair-env.bat -CheckOnly -NoPause <本项目路径>`，均返回 0。新环境下 49 项音乐库/播放器联动与 8 项真实 `LocalIslandApp` 构造、启动及关闭检查全部通过；它们使用合成账户、隔离设置和本机静音测试音频，没有操作用户账号或系统音量。原生入口、登录与音乐库截图已复核，七个 SVG 哈希与原件逐一相同。

当前独立环境运行 `.venv/Scripts/python.exe -X utf8 -X faulthandler -m unittest discover tests -v`，**3617 项全量测试全部通过**（181.125 秒，无跳过）。最终日志保存在 ignored `logs/svg-environment-full-tests.log`。Python 编译和 Git 差异检查通过；21 个源码/资产/测试/文档路径经独立审查，生成环境、日志、截图和凭据没有纳入提交。流程检查 100/100；图标替换与环境修复均不改变版本号，不代表重新打包了现有 EXE。
