# 搜索结果布局，两套可运行方案

产品代码这次只加快了搜索，没有改结果列表的排法。专辑和艺人要不要单独成组，是布局选择，所以放在这里，不直接进音乐库。

两套用同一批示例数据：歌曲、专辑、艺人、歌单。输入会高亮命中的字。这不是网易云的真实搜索，也不会写入账号或设置。

- 方案 A 分组列表：四类上下排列，每一类是一列结果。运行 `python _demos/search_t110/demo_sections.py`。Windows 可双击 `搜索-方案A-分组列表.bat`。
- 方案 B 横向货架：每一类单独一行，结果向右排开。运行 `python _demos/search_t110/demo_shelves.py`。Windows 可双击 `搜索-方案B-横向货架.bat`。

离屏检查：

```
QT_QPA_PLATFORM=offscreen python _demos/search_t110/check_layout.py
```

A 的四个标题从上到下。B 的四个标题分成四行，左边对齐。选定哪一种之后，再接到现有搜索结果上。
