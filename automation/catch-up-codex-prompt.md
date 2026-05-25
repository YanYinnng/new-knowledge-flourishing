# 点子发芽漏跑日报补生成提示词

你正在补跑一个错过的日报日期：`{{DATE}}`。

请读取 `automation/nightly-codex-prompt.md` 作为内容规则，但本次以 `{{DATE}}` 为准，不要使用当前日期替代它。

## 本次任务

1. 读取 `inbox/{{DATE}}.md`。如果文件不存在或没有有效网页输入，就按 nightly 提示词里的“无新输入时”规则做轻量复盘。
2. 读取 `tracking/topics.md`，并按需读取 `knowledge/`、`synthesis/idea_seeds/`、`library/nodes/`、`library/sources/`、`library/seeds/` 中相关文件。
3. 生成或更新 `synthesis/daily_reports/{{DATE}}.md`。
4. 不要发送邮件。补跑脚本会在你退出后立即调用 `scripts/send-daily-report.py` 发送。
5. 不要等待 23:00。
6. 不要创建、修改或删除 Codex automation。
7. 不要提交 Git，也不要推送。

## 输出要求

日报仍使用 nightly 提示词里的结构，保持短、有判断、可手动检查。

完成后只简短说明写入了哪个日报文件，以及是否发现缺少输入或邮件发送之外的阻塞。
