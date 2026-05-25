# 点子发芽 nightly Codex automation 提示词

你正在维护一个个人自用的新知孵化 MVP。只使用本地 Markdown/JSON/LaTeX 文件、PowerShell/Python 脚本和 Codex 自带能力；不使用 OpenAI API，不引入数据库、向量库、知识图谱引擎或复杂依赖。

## 目标

每天 22:50 左右生成一份简洁的“点子发芽日报”，主输出目录：

```text
synthesis/daily_reports/YYYY-MM-DD/
  report_context.json
  report_brief.json
  report.tex
  report.pdf
  sources.json
  quality_check.json
```

邮件发送由 Windows 本地任务在 23:00 处理。不要在 Codex automation 里发送邮件、等待 23:00、调用 `Start-Process`、调用 `schtasks` 或启动后台 PowerShell。

## 报告结构

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

不要使用旧标签：`它是什么`、`今天查到了什么`、`和我有什么关系`、`今日判断`。

## 执行流程

1. 读取 `system/report_quality_rules.md`。
2. 使用当前本地日期 `YYYY-MM-DD`。
3. 运行：

```powershell
python scripts/generate-radar-report.py --date YYYY-MM-DD --collect-only
```

4. 读取 `synthesis/daily_reports/YYYY-MM-DD/report_context.json` 和 `sources.json`。
5. 你作为大模型写作者，生成 `synthesis/daily_reports/YYYY-MM-DD/report_brief.json`。必须符合 `templates/report-brief.json`。
6. 对每个关键词，用下面三个问题思考后再写入 brief：
   - `A 是什么？`
   - `A 最近有什么新闻/新进展？`
   - `结合用户输入语境 B，有什么补充判断？`
7. 运行：

```powershell
python scripts/generate-radar-report.py --date YYYY-MM-DD --render-only
```

8. 检查 `quality_check.json`，必须 `passed: true`。
9. 用 `pdftotext synthesis/daily_reports/YYYY-MM-DD/report.pdf -` 抽查 PDF 文本；如果没有 `pdftotext`，直接读 `report.tex`。
10. 如果 PDF 仍像搜索堆砌、百科词条、新闻摘要或公司日报，修改 `report_brief.json` 后重新运行 `--render-only`。

## 写作要求

- `summary`：1-2 段，说明今天输入共同指向什么。
- `intro`：每个关键词 2-4 句；既解释概念，也结合用户上下文。
- `recent_news`：1 段或最多 2 条；没有可靠新进展就明确说“未查到可靠近期新进展”。
- `next_step`：只给 1 个具体动作。
- `old_knowledge_links`：最多 3 条，只写真相关。
- `idea_seeds`：最多 2 条，宁缺毋滥。
- `reference_sources`：只列来源编号、标题、等级和 URL，不放网页摘要。
- 搜索来源必须写入 `sources.json`，正文引用来源编号即可，不要粘贴搜索结果标题或摘要。

## 无新输入

如果当天没有网页输入，不要写空报告。仍然先运行 `--collect-only`，然后根据 `report_context.json` 里的高权重节点和追踪主题写一份简短复盘。结构仍保持同一套 6 个主章节。

生成 `report.pdf`、`report_brief.json`、`quality_check.json`、`sources.json` 后停止。
