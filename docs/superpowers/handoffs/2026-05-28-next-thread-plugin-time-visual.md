# 下一对话交接：插件 TIME 视觉参考稿

## 回滚判定

- 已检查当前仓库，未发现上一段中断对话提到的 `Solaris Obsidian`、`code.html`、`DESIGN.md`、HTML/WebView 映射等内容落盘到项目文件。
- 上一段中断对话看起来只读取了外部 HTML 参考稿并在对话内生成了预览，没有形成可安全归属的项目代码改动。
- 因此本次不做破坏式回滚，也不回退上一轮已经完成并测试通过的 TIME 插件功能实现，避免误伤已有工作。

## 当前项目边界

- 项目主程序入口：`local/main.py`
- 核心岛界面：`local/island_stack_visual.py`
- 当前岛 UI 是 Python / PySide6 手绘，不是 Web，不应直接引入 HTML 或 WebView。
- fake / real 数据边界保持在 `api/providers.py` 与相关 provider 层。
- 不要恢复旧 Pill UI，不要恢复已移除的 live-media source tree。

## 已完成基础

- TIME 插件面板已有三栏功能基础：左侧时钟，中间倒计时，右侧闹钟。
- 倒计时预设已能写入 `v2_timer_started_at` / `v2_timer_end_at`。
- 闹钟设置已在插件面板内联打开，时/分/频次/日期支持滚轮，备注支持输入并可保存为闹钟标签。
- TIME 的倒计时/闹钟状态已收回 TIME 面板，不再进入旧侧边副岛提醒队列。
- 最近验证记录：`python -m unittest discover -s tests` 通过 236 项。

## 下个对话待做

1. 读取外部 HTML 参考稿并做视觉拆解
   - 参考路径：`D:\Download\Chrome\stitch_python_desktop_dynamic_island (1)\stitch_python_desktop_dynamic_island`
   - 需要读取：`code.html`、截图资源、`DESIGN.md`（如存在）
   - 目标：提取结构、配色、尺寸、圆角、描边、动效与交互状态，不直接复制 HTML 技术栈。

2. 将参考稿翻译为 PySide 手绘规范
   - 目标文件：`local/island_stack_visual.py`
   - 重点区域：插件默认条、向下展开面板、TIME 三栏面板、闹钟设置面板。
   - 原则：只改绘制语言和交互细节，不污染音乐进度、歌词同步、网易云跳转链路。

3. 生成或更新视觉方案文档
   - 建议新建：`docs/superpowers/plans/2026-05-28-plugin-time-visual-reference.md`
   - 需要写清：
     - HTML 参考稿里的设计语言
     - 对应到 PySide 的绘制策略
     - 哪些内容采用，哪些内容不采用
     - 验证方式和截图检查点

4. 补充测试与可视验证
   - 行为测试：`tests/test_island_stack_visual_messages.py`
   - 重点保护：
     - TIME 面板仍注册倒计时/闹钟动作
     - 闹钟设置仍支持滚轮和备注输入
     - TIME 不重新进入旧侧边副岛提醒队列
   - 运行验证：
     - `python -m py_compile local\island_stack_visual.py`
     - `python -m unittest tests.test_island_stack_visual_messages`
     - `python -m unittest discover -s tests`

## 必读文档

- `README.md`
- `docs/PRD.md`
- `docs/ISSUES.md`
- `docs/PLUGIN_ISLAND_PRD.md`
- `docs/superpowers/plans/2026-05-27-time-plugin-reference-layout.md`
- `docs/superpowers/plans/2026-05-27-plugin-island-design.md`

## 建议使用的 skills

- `using-superpowers`
- `systematic-debugging`
- `test-driven-development`
- `impeccable`
- `awesome-design-md`
- `ui-ux-pro-max`
- `verification-before-completion`

## 下个对话建议开场语

请在 `D:\Program\gpt-island` 的 `规范design` 分支继续。先读 `AGENTS.md`、`README.md`、`docs/PRD.md`、`docs/ISSUES.md`，再读 `docs/superpowers/handoffs/2026-05-28-next-thread-plugin-time-visual.md`。上一段中断对话没有可识别落盘改动，不要做破坏式回滚。请按 superpower 流程读取外部 HTML 参考稿 `D:\Download\Chrome\stitch_python_desktop_dynamic_island (1)\stitch_python_desktop_dynamic_island`，只把它的视觉语言翻译到 PySide 手绘插件 TIME 面板中，不引入 WebView/HTML，不污染音乐进度、歌词同步、网易云跳转逻辑。先写方案文档并确认映射点，再按 TDD 实现和验证。
