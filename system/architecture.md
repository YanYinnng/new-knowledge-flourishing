# 点子发芽信息架构

最后更新：2026-06-05。

## 数据层

- `inbox/YYYY-MM-DD.md`：人类可读的每日输入日志。
- `inbox/YYYY-MM-DD.jsonl`：结构化原始输入底座，按行追加，字段为 `schema_version`、`id`、`date`、`created_at`、`source`、`kind`、`payload`。
- `memory/`：长期秘书记忆，只写入确认后的内容。
- `tasks/tasks.jsonl`：任务事件流，通过创建、更新、完成、取消、延期事件重建当前状态。
- `review_queue/YYYY-MM-DD.jsonl`：AI 候选和人工确认结果。
- `tracking/weight_decisions.jsonl`：已确认的权重调整事件。
- `knowledge/` 与 `synthesis/idea_seeds/`：当前知识节点和点子种子主库。

## 输入类型

当前网页开放：

- `keyword_batch`：日报关键词主体。
- `free_note`：随心记，只用于独立复盘和候选生成。

结构保留但当前网页不开放：

- `task_capture`：任务捕捉，后续恢复任务模块时使用。
- `calendar_capture`：日程捕捉，后续恢复日程模块时使用。
- `link_capture`、`file_capture`：后续扩展预留。

## 上下文构建

`scripts/context_builder.py` 是统一上下文读取层。日报、秘书回复、周报和任务建议应优先通过它读取：

- 当日原始输入统计。
- 长期记忆摘要。
- 当前打开任务和临近截止任务。
- 待确认队列。

日报会把这些内容写入 `report_context.json.secretary_context`。AI 写日报时通过这个字段看到个人画像、偏好、长期主题、任务状态和待确认项。

## 确认原则

报告和随心记可以生成候选，但候选只进入 `review_queue/`。接受后才写入 `memory/`、`tasks/tasks.jsonl`、`tracking/weight_decisions.jsonl` 或后续知识库；拒绝也作为事件保留。

`weight_change_candidate` 接受后会记录到 `tracking/weight_decisions.jsonl`。只有候选提供了安全路径，且路径位于 `knowledge/` 或 `synthesis/idea_seeds/` 下，才会更新对应 Markdown 文件的 `Weight:`。

## 报告生成

日报生成分三步：

1. `--collect-only`：生成 `report_context.json` 和 `sources.json`。
2. Codex automation：读取规则、上下文和来源，写 `report_brief.json`。
3. `--render-only`：渲染 TeX/PDF，写 `quality_check.json`，并把候选同步到 `review_queue/`。

质量检查会验证结构、来源、随心记隔离、语言风格和“最近有什么相关新闻”的信息密度。
