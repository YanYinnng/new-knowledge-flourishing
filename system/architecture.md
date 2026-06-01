# 点子发芽信息架构

## 数据层

- `inbox/YYYY-MM-DD.md`：人类可读的每日输入日志。
- `inbox/YYYY-MM-DD.jsonl`：结构化原始输入底座，按行追加，字段为 `schema_version`、`id`、`date`、`created_at`、`source`、`kind`、`payload`。
- `memory/`：长期秘书记忆，只写入确认后的内容。
- `tasks/tasks.jsonl`：任务事件流，通过创建、更新、完成、取消、延期事件重建当前状态。
- `review_queue/YYYY-MM-DD.jsonl`：AI 候选和人工确认结果。
- `knowledge/` 与 `synthesis/idea_seeds/`：当前知识节点和点子种子主库。

## 输入类型

- `keyword_batch`：日报关键词主体。
- `free_note`：随心记，只用于独立复盘和候选生成。
- `task_capture`：手动捕捉任务，并同步写入任务事件流。
- `calendar_capture`：手动捕捉日程，暂不连接外部日历。
- `link_capture`、`file_capture`：后续扩展预留。

## 上下文构建

`scripts/context_builder.py` 是统一上下文读取层。日报、秘书回复、周报和任务建议应优先通过它读取：

- 当日原始输入统计。
- 长期记忆摘要。
- 当前打开任务和临近截止任务。
- 待确认队列。

## 确认原则

报告和随心记可以生成候选，但候选只进入 `review_queue/`。接受后才写入 `memory/`、`tasks/tasks.jsonl` 或后续知识库；拒绝也作为事件保留。
