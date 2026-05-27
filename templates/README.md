# templates

这个目录保留当前工作流还需要的结构模板。

- `daily-input.md`：网页或 `scripts/new-today.ps1` 创建当天 `inbox/YYYY-MM-DD.md` 时使用。
- `report-brief.json`：nightly / catch-up automation 写 `report_brief.json` 时必须遵守的结构合约。
- `knowledge-node.md`：人工新建或整理 `knowledge/*.md` 时参考；同步脚本生成的新节点也保持同一结构。
- `idea-seed.md`：人工新建或整理 `synthesis/idea_seeds/*.md` 时参考；同步脚本生成的新种子也保持同一结构。

已移除旧的 Markdown 日报模板和来源模板；当前日报输出使用 `synthesis/daily_reports/YYYY-MM-DD/` 下的 `report_brief.json`、`report.tex`、`report.pdf`、`sources.json`。
