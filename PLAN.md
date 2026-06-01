# 点子发芽秘书路线图

当前阶段把系统从“每日知识报告”升级为“可确认、可追踪、逐渐理解你的个人秘书”。

## 当前优先级

1. 稳住信息架构：`inbox/YYYY-MM-DD.jsonl` 继续作为 append-only 原始输入底座，所有新输入都用 `kind + payload`。
2. 建立长期记忆层：`memory/profile.md`、`memory/preferences.jsonl`、`memory/themes.md` 只接收确认后的内容。
3. 接入任务和日程：网页手动录入 `task_capture`、`calendar_capture`；任务状态写入 `tasks/tasks.jsonl` 事件流。
4. 建立确认队列：AI 只提出 `memory_candidate`、`task_candidate`、`knowledge_candidate`、`idea_seed_candidate`，接受或拒绝都写入 `review_queue/`。
5. 升级日报为秘书简报：保留关键词日报主体，在末尾按需加入随心记复盘、记忆候选、任务跟进、明日秘书提醒。

## 不变约束

- 本地优先，Git 备份，不引入正式数据库。
- 原始输入 append-only。
- AI 不自动改长期记忆、不自动创建真实任务、不自动升权知识节点。
- Markdown 保持可读日志，JSONL 作为结构化事实来源。
