# 点子发芽项目整理报告

生成时间：2026-05-26

## 发现的问题

- 网页“最近日报”会同时列出结构化 PDF 日报和旧 Markdown 日报，同一天可能出现多个入口，容易点到旧文件。
- 网页“知识节点”和“点子种子”会同时列出新目录和旧兼容目录中的同名文件，容易误以为点击后展示错了内容。
- 前端已经显示“补充信息”，但 `inbox/2026-05-25.md` 中仍有一条新输入以“上下文”记录，和当前字段名不一致。
- 报告列表原先按文件修改时间排序，重新编译旧日报后会把旧日期排到最新日期前面。
- 前端点击 PDF 日报时直接打开新窗口，当前页面阅读区不更新，容易造成“点了但展示没变”的错觉。
- `/api/file` 返回错误时，前端缺少统一的友好错误展示。
- 网页三栏原先共用一个阅读区，点击知识节点或点子种子会占用“最近日报”下方区域。
- 报告生成后没有把新知识写回 `knowledge/` 和 `synthesis/idea_seeds/`，长期会让新知库停留在初始样例。
- 项目中存在编译中间产物和 Python 缓存，属于可清理生成物。

## 已修复的问题

- `app.py` 的日报列表现在按日报日期排序，并优先显示 `synthesis/daily_reports/YYYY-MM-DD/report.pdf` 或 `report.tex`。
- `app.py` 的日报列表会隐藏同一天的旧 Markdown 日报，避免 2026-05-24/2026-05-25 同日重复入口。
- `app.py` 的知识节点和点子种子列表按文件 stem 去重，优先显示 `knowledge/` 和 `synthesis/idea_seeds/`，旧 `library/` 目录只做兼容读取。
- `web/static/app.js` 点击 PDF 日报时会在当前阅读区显示对应 PDF 的打开链接和内嵌预览，不再只打开新窗口。
- `web/static/app.js` 读取文件失败时会在阅读区显示错误消息，而不是静默失败。
- `web/index.html` 和 `web/static/app.js` 已把阅读区拆成 `report-reader`、`knowledge-reader`、`seed-reader`，三栏点击互不抢占。
- 新增 `scripts/sync-knowledge-from-report.py`，可把通过质量检查的日报沉淀为候选知识节点和 raw 点子种子。
- `automation/nightly-codex-prompt.md` 和 `automation/catch-up-codex-prompt.md` 已接入 `knowledge_sync.json` 检查。
- `library/nodes/` 和 `library/seeds/` 明确改为历史只读兼容目录，后续自动写入只进入 `knowledge/` 和 `synthesis/idea_seeds/`。
- `inbox/2026-05-25.md` 已把新输入字段改为“补充信息”，并把未填写权重归一为 `3`。
- `scripts/generate-radar-report.py` 中旧的用户可见“上下文”措辞已替换为“补充信息”；只保留历史 inbox 兼容读取。
- `.gitignore` 增加 `report.synctex.gz`，避免 LaTeX 同步预览文件再次进入未跟踪状态。
- 已新增一次样例输入到 `inbox/2026-05-26.md`，确认新写入使用“补充信息”和默认权重 `3`。

## 清理或移动的文件

- 删除 `__pycache__/`：Python 编译缓存，可由运行时重新生成。
- 删除 `synthesis/daily_reports/**/report.aux`、`report.fdb_latexmk`、`report.fls`、`report.log`、`report.out`、`report.xdv`、`report.synctex.gz`：LaTeX 编译中间产物，可由 `compile-radar-report.ps1` 或 `generate-radar-report.py --render-only` 重新生成。
- 未移动文件。

## 保留并待人工确认的文件

- `library/nodes/personal-knowledge-incubation.md` 和 `library/seeds/daily-review-spark.md`：旧兼容目录中的知识节点/点子种子。网页优先显示新目录同名文件，旧文件暂不删除。
- `reports/daily/`：旧日报目录，目前作为兼容白名单保留。
- `system/*.log`、`reports/*.log`、`system/email_sent/*.sent`：运行日志和邮件发送标记，已由 `.gitignore` 管理，暂不删除。

## 已移除的旧日报入口

- `synthesis/daily_reports/2026-05-24.md`、`synthesis/daily_reports/2026-05-25.md`：旧 Markdown 日报入口已从当前工作树移除。结构化日报目录中的 `report.pdf`、`report.tex`、`report_brief.json`、`report_context.json`、`sources.json` 仍保留；如需找回旧 Markdown 文本，可从 Git 历史恢复。

## 模板与追踪目录维护

- `templates/daily-input.md`、`templates/report-brief.json`、`templates/knowledge-node.md`、`templates/idea-seed.md` 仍保留：分别对应每日输入创建、日报 brief 合约、知识节点结构和点子种子结构。
- 删除 `templates/source.md`：当前来源记录已进入每日日报目录的 `sources.json`，不再维护独立来源 Markdown 模板。
- 删除 `templates/daily-report.md`：当前日报流程输出 PDF/TEX/JSON，不再使用旧 Markdown 日报模板。
- 新增 `templates/README.md` 和 `tracking/README.md`，说明两个目录的当前用途。
- `tracking/topics.md` 仍由 `generate-radar-report.py` 读取，用于无新输入复盘和旧知识关联，不删除。

## 新增同步机制

- 网页三栏已改为独立阅读区：`report-reader`、`knowledge-reader`、`seed-reader` 分别显示日报、知识节点和点子种子。
- `report_context.json` 继续负责记录旧知识扫描结果，确保日报生成前读过 `knowledge/`、`synthesis/idea_seeds/` 和历史兼容目录。
- `report_brief.json` 通过质量检查后，`sync-knowledge-from-report.py` 会读取其中的 `knowledge_cards[]` 和 `idea_seeds[]`。
- 知识节点按 `ID`、标题、`Aliases` 匹配已有文件；匹配不到时在 `knowledge/` 新建 `Status: candidate` 节点。
- 点子种子按 slug/标题匹配已有文件；匹配不到时在 `synthesis/idea_seeds/` 新建 `Status: raw` 种子。
- 同一天重复运行不会重复追加同一天的更新记录；结果写入当天 `knowledge_sync.json`。

## 验证记录

- `python -m py_compile app.py scripts/generate-radar-report.py scripts/sync-knowledge-from-report.py`：通过。
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate-structure.ps1`：通过。
- `python scripts/sync-knowledge-from-report.py --date 2026-05-25 --dry-run`：通过，重跑时能识别同一天已同步内容并标记 `already_synced`。
- `python scripts/sync-knowledge-from-report.py --date 2026-05-25`：通过，生成/更新 `knowledge_sync.json`，写入 `library_policy: read-only` 和 `context_scanned_paths`，实际沉淀文件为 `knowledge/node-0011b3bd.md`、`synthesis/idea_seeds/seed-20845b10.md`、`synthesis/idea_seeds/seed-cf82098a.md`。
- `python scripts/generate-radar-report.py --date 2026-05-25 --collect-only`：通过，`report_context.json.local_knowledge.scanned_paths` 非空。
- `python scripts/generate-radar-report.py --date 2026-05-25 --render-only`：通过，`quality_check.json` 为 `passed: true`。
- `git status --short library`：无输出，确认同步没有写入 `library/`。
- `git diff --check`：通过，仅有 Git 行尾换行提示。
- 本地网页服务验证（3000 已占用，因此使用 `http://127.0.0.1:3001`）：
  - 登录接口 `/api/login`：通过。
  - `/api/overview`：返回 2 个日报、2 个知识节点、3 个点子种子。
  - 最近日报首项指向 `synthesis/daily_reports/2026-05-25/report.pdf`，旧 `2026-05-25.md` 未重复出现。
  - 知识节点可读取 `knowledge/node-0011b3bd.md`。
  - 点子种子可读取 `synthesis/idea_seeds/seed-20845b10.md`。
  - `/api/file` 点击上述三个条目均返回对应文件。
  - 路径越界请求 `../README.md` 返回 404。
  - 不存在文件请求返回 404 和错误信息。
- 浏览器点击验证：日报点击后 `report-reader` 出现 PDF 预览，知识节点点击后 `knowledge-reader` 显示 Markdown 且不覆盖日报，点子种子点击后 `seed-reader` 显示 Markdown 且不覆盖日报或知识节点；三个列表各保留 1 个独立选中态。
- `inbox/2026-05-26.md`：包含样例关键词“项目整理验证输入”，字段为“补充信息”，默认权重为 `3`，没有新增“上下文”字段。
- `parse_inbox("2026-05-26")`：能读取样例输入，并把权重解析为 `3`。

## 2026-05-27 大体检补充

- 新增 `CODEX_HANDOFF.md`，作为新对话 Codex 的首读交接文件，记录项目目的、当前脏工作树、核心文件、工作流、最新验证结果和风险点。
- `scripts/validate-structure.ps1` 已把 `CODEX_HANDOFF.md` 纳入必需文件。
- 修复 `scripts/sync-knowledge-from-report.py` 的点子种子标题解析：当 `idea_seeds[]` 项以 `点子种子：` 或 `今日发芽点子：` 开头时，先去掉这个通用前缀，再提取真实标题。
- 清理 2026-05-26 错误生成的 `点子种子：点子种子` 种子，重新沉淀为两个独立 raw 种子：`seed-72a2ef24.md` 和 `seed-b123728a.md`。
- `python -m py_compile app.py scripts/generate-radar-report.py scripts/sync-knowledge-from-report.py scripts/send-daily-report.py`：通过。
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate-structure.ps1`：通过。
- `git diff --check`：通过，仅有 Git 行尾换行提示。
- 临时端口 `3002` API 冒烟测试：登录成功，`/api/overview` 返回 3 个日报、3 个知识节点、5 个点子种子，路径越界请求返回 404。
