# 点子发芽漏跑报告补生成提示词

你正在补跑一个错过的报告日期：`{{DATE}}`。

请读取 `automation/nightly-codex-prompt.md` 作为内容规则，但本次以 `{{DATE}}` 为准，不要使用当前日期替代它。

目标输出：

```text
synthesis/daily_reports/{{DATE}}/
  report.tex
  report.pdf
  assets/
  sources.json
```

推荐先运行：

```powershell
python scripts/generate-radar-report.py --date {{DATE}}
```

如果需要修改 `report.tex`，修改后重新编译：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/compile-radar-report.ps1 -Date {{DATE}}
```

不要发送邮件，不要等待 23:00，不要创建或修改 automation，不要提交 Git。补跑脚本会在 PDF 存在后调用邮件脚本。
