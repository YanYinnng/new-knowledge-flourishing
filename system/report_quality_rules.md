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

每个关键词在“今日新知”下只允许四个小节：

1. 简介
2. 最近有什么相关新闻
3. 与我相关
4. 最小下一步

禁止出现旧标签：`它是什么`、`今天查到了什么`、`和我有什么关系`、`今日判断`。

## Automation 写作流程

1. 运行 `python scripts/generate-radar-report.py --date YYYY-MM-DD --collect-only`。
2. 读取 `synthesis/daily_reports/YYYY-MM-DD/report_context.json`。
3. 对每个关键词按下面三个问题写作：
   - `A 是什么？`
   - `A 最近有什么新闻/新进展？`
   - `结合补充信息 B，A 与我的记录有什么具体联系？`
4. 把答案整理成 `report_brief.json`，必须符合 `templates/report-brief.json`。
5. 运行 `python scripts/generate-radar-report.py --date YYYY-MM-DD --render-only`。
6. 用 `pdftotext` 或 `report.tex` 抽查结果；如果仍像搜索堆砌，修改 `report_brief.json` 后再次 render。
7. `quality_check.json` 通过后，运行 `python scripts/sync-knowledge-from-report.py --date YYYY-MM-DD`，把报告沉淀成候选知识节点和 raw 点子种子。

## 内容质量标准

- `summary`：1-2 段，说明今天信息共同指向什么。
- `intro`：每个关键词约 200-300 字；只解释关键词本身，不联系补充信息。
- `recent_news`：1 段或最多 2 条；不能粘贴搜索标题或摘要。
- `relevance`：单独分析关键词和补充信息的联系，可以写它为什么触发注意、连接到什么学习/项目/机会/人脉/商业观察。
- `next_step`：只能有 1 个动作，必须可执行。
- `old_knowledge_links`：最多 3 条，只写真相关，不强行凑。
- `idea_seeds`：最多 2 条；没有好点子就写“今日暂无值得保留的新点子”。
- `reference_sources`：只列来源标题、等级和 URL，不放网页摘要。

## 渲染质量检查必须拦截

- 旧标签出现在 PDF 中。
- 每个关键词没有恰好一个“简介 / 最近有什么相关新闻 / 与我相关 / 最小下一步”。
- 简介明显短于 200 字。
- 正文直接复用搜索标题或长摘要。
- 启用联网搜索却没有 `sources.json` 来源或失败说明。
- `report_context.json.local_knowledge.scanned_paths` 为空，但本地 `knowledge/`、`synthesis/idea_seeds/`、`library/nodes/` 或 `library/seeds/` 实际上有内容。

## 同步流程必须检查

- 缺少 `knowledge_sync.json`，或同步结果没有 `created` / `updated` / `skipped` 任何记录。

## 新知库沉淀规则

- 报告生成必须读取旧知识：`report_context.json.local_knowledge.scanned_paths` 不能为空，除非 `knowledge/`、`synthesis/idea_seeds/`、`library/nodes/`、`library/seeds/` 确实都是空目录。
- 日报通过质量检查后，必须运行 `python scripts/sync-knowledge-from-report.py --date YYYY-MM-DD`，并生成 `knowledge_sync.json`。
- 同步脚本只写 `knowledge/` 和 `synthesis/idea_seeds/`；`library/nodes/` 和 `library/seeds/` 只读兼容，不再写入。
- 新知识节点默认 `Status: candidate`，权重最高只写到 `3`；已有人工设置的 `4/5` 不被自动降低或覆盖。
- 新点子种子默认 `Status: raw`。
- 新写入内容只使用“补充信息”这个字段名；历史“上下文”只允许作为旧输入兼容读取。
