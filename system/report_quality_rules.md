# 点子发芽日报质量规则

这份日报不是资料汇总，也不是搜索结果排版。脚本只负责收集材料和渲染 PDF；真正的报告正文必须由 Codex automation 读取 `report_context.json` 后写入 `report_brief.json`。

## 根本问题

之前报告质量差，是因为 Python 脚本用硬编码模板直接写正文：

- 同一个标签会重复很多次，例如多条“今天查到了什么”和“和我有什么关系”。
- “简介”只有一句判断，读者无法真正理解关键词。
- 搜索结果、来源分层和个人判断混在一起，正文像搜索材料拼贴。
- 报告章节太多，阅读负担过重。

## 固定报告结构

PDF 只允许这些主章节：

1. 今日总结
2. 今日输入
3. 今日新知
4. 与旧知识的链接
5. 今日发芽点子
6. 参考搜索内容

每个关键词在“今日新知”下只允许三个小节：

1. 简介
2. 最近有什么相关新闻
3. 最小下一步

禁止出现旧标签：`它是什么`、`今天查到了什么`、`和我有什么关系`、`今日判断`。

## Automation 写作流程

1. 运行 `python scripts/generate-radar-report.py --date YYYY-MM-DD --collect-only`。
2. 读取 `synthesis/daily_reports/YYYY-MM-DD/report_context.json`。
3. 对每个关键词按下面三个问题写作：
   - `A 是什么？`
   - `A 最近有什么新闻/新进展？`
   - `结合用户输入语境 B，有什么补充判断？`
4. 把答案整理成 `report_brief.json`，必须符合 `templates/report-brief.json`。
5. 运行 `python scripts/generate-radar-report.py --date YYYY-MM-DD --render-only`。
6. 用 `pdftotext` 或 `report.tex` 抽查结果；如果仍像搜索堆砌，修改 `report_brief.json` 后再次 render。

## 内容质量标准

- `summary`：1-2 段，说明今天信息共同指向什么。
- `intro`：每个关键词 2-4 句；既解释概念，也结合用户上下文。
- `recent_news`：1 段或最多 2 条；不能粘贴搜索标题或摘要。
- `next_step`：只能有 1 个动作，必须可执行。
- `old_knowledge_links`：最多 3 条，只写真相关，不强行凑。
- `idea_seeds`：最多 2 条；没有好点子就写“今日暂无值得保留的新点子”。
- `reference_sources`：只列来源标题、等级和 URL，不放网页摘要。

## 质量检查必须拦截

- 旧标签出现在 PDF 中。
- 每个关键词没有恰好一个“简介 / 最近有什么相关新闻 / 最小下一步”。
- 简介少于 2 句。
- 正文直接复用搜索标题或长摘要。
- 启用联网搜索却没有 `sources.json` 来源或失败说明。
