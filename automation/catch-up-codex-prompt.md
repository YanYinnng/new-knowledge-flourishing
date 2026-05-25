# 点子发芽漏跑报告补生成提示词

你正在补跑一个错过的报告日期：`{{DATE}}`。

请读取 `system/report_quality_rules.md` 和 `automation/nightly-codex-prompt.md`，但本次日期固定为 `{{DATE}}`，不要使用当前日期替代它。

目标输出：

```text
synthesis/daily_reports/{{DATE}}/
  report_context.json
  report_brief.json
  report.tex
  report.pdf
  sources.json
  quality_check.json
```

执行：

```powershell
python scripts/generate-radar-report.py --date {{DATE}} --collect-only
```

然后你作为大模型写作者读取 `report_context.json`，写出符合 `templates/report-brief.json` 的 `report_brief.json`。写好后运行：

```powershell
python scripts/generate-radar-report.py --date {{DATE}} --render-only
```

检查 `quality_check.json` 和 PDF 文本。如果报告仍像搜索堆砌、百科词条或新闻摘要，修改 `report_brief.json` 后重新 render。

不要发送邮件，不要等待 23:00，不要创建或修改 automation，不要提交 Git。补跑脚本会在 PDF 存在后调用邮件脚本。
