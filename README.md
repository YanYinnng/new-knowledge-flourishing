# 点子发芽：本地优先的个人秘书与知识孵化系统

这是一个给个人使用的本地优先工作流。它从“每天写几个关键词，晚上生成一份知识报告”开始，正在逐步升级成一个“可确认、可追踪、逐渐理解你的个人秘书”。

它不依赖数据库、向量库、知识图谱引擎或 OpenAI API。系统状态主要保存在 Markdown、JSONL、JSON、LaTeX/PDF 和少量 Python/PowerShell 脚本里。你可以直接读文件、用 Git 备份，也可以在网页里完成日常输入和确认。

## 核心理念

1. 原始输入永远 append-only。每天的输入写入 `inbox/YYYY-MM-DD.jsonl`，旧内容不被覆盖。
2. Markdown 负责可读，JSONL 负责事实。人看 Markdown，脚本读 JSONL。
3. AI 只提出候选，不自动替你做最终决定。
4. 长期记忆、任务状态、知识节点分层存储，不把所有东西混进日报。
5. 所有关键写入都尽量可追溯：来源、时间、候选、确认结果都留在本地文件里。

## 快速开始

在项目根目录运行：

```powershell
python app.py
```

电脑本机访问：

```text
http://127.0.0.1:3000
```

如果 3000 端口被占用：

```powershell
$env:IDEA_SPROUT_PORT="3001"; python app.py
```

如果调试时不想让网页提交后自动 Git commit / push：

```powershell
$env:IDEA_SPROUT_AUTO_GIT_SYNC="0"; python app.py
```

也可以用脚本启动：

```powershell
.\scripts\start-web.ps1
```

第一次启动会自动创建 `config/local_auth.json`。默认密码是 `change-me`，请尽快改掉。

## 每天怎么用

白天打开网页，优先做两类轻量输入。

1. 关键词输入：写今天注意到的概念、人物、项目、问题或机会。每行一个关键词，可加补充信息和权重。
2. 随心记：写突发想法、感受、判断、困惑。它不会影响关键词日报主体，只会用于独立复盘和候选生成。

任务和日程入口当前暂时下线。底层事件流和上下文结构仍保留，方便以后重新启用。

晚上由日报流程整理：

```powershell
python scripts/generate-radar-report.py --date YYYY-MM-DD --collect-only
```

Codex automation 读取 `report_context.json` 和 `sources.json`，写入：

```text
synthesis/daily_reports/YYYY-MM-DD/report_brief.json
```

然后渲染 PDF：

```powershell
python scripts/generate-radar-report.py --date YYYY-MM-DD --render-only
```

质量通过后，同步知识候选：

```powershell
python scripts/sync-knowledge-from-report.py --date YYYY-MM-DD
```

## 秘书体系是怎么架构的

系统现在分为六层。

### 1. 原始输入层

位置：

```text
inbox/YYYY-MM-DD.md
inbox/YYYY-MM-DD.jsonl
```

`inbox/YYYY-MM-DD.md` 是人类可读日志。网页每次提交后会追加一段文本，方便直接查看当天写了什么。

`inbox/YYYY-MM-DD.jsonl` 是结构化事实来源。每行都是一个事件，基本形状是：

```json
{
  "schema_version": 1,
  "id": "2026-06-01T18-00-00-abc123",
  "date": "2026-06-01",
  "created_at": "2026-06-01T18:00:00+08:00",
  "source": "web",
  "kind": "keyword_batch",
  "payload": {}
}
```

当前支持的 `kind`：

- `keyword_batch`：关键词日报主体。
- `free_note`：随心记，只用于独立复盘和候选生成。
- `task_capture`：手动捕捉任务，并写入任务事件流。
- `calendar_capture`：手动捕捉日程。
- `link_capture`：后续链接/文章输入预留。
- `file_capture`：后续文件/长笔记输入预留。

设计原因：以后无论接入链接、文件、会议、邮件还是外部日历，都可以继续写入同一个 append-only 原始输入底座，不需要推翻现有日报流程。

### 2. 统一上下文层

位置：

```text
scripts/context_builder.py
```

这是秘书体系的“读数据入口”。它负责统一读取：

- 当天原始输入统计。
- 长期记忆。
- 已确认任务和临近截止任务。当前网页不开放任务录入，这部分主要为后续恢复任务模块预留。
- 待确认队列。

日报脚本已经通过它把 `secretary_context` 写入：

```text
synthesis/daily_reports/YYYY-MM-DD/report_context.json
```

未来做秘书问答、周报、任务建议、主动提醒时，都应该优先复用 `context_builder.py`，避免每个脚本各读各的，导致逻辑分叉。

### 3. 长期记忆层

位置：

```text
memory/profile.md
memory/preferences.jsonl
memory/themes.md
```

`memory/profile.md` 保存稳定个人画像，例如长期目标、工作方式、项目方向、长期身份变化。

`memory/preferences.jsonl` 保存可追溯偏好事件，例如你更喜欢命令行还是网页、报告语气偏好、提醒方式偏好。它是 JSONL，因为偏好可能随时间变化，事件流比覆盖式 JSON 更适合追踪。

`memory/themes.md` 保存长期反复主题和当前关注强度，例如某个领域最近是否频繁出现、是否正在从兴趣变成项目。

重要原则：日报和随心记可以产生 `memory_candidate`，但不会自动改写 `memory/`。只有你在网页“待确认”里接受后，才会写入长期记忆相关文件。

### 4. 任务与日程层

位置：

```text
tasks/tasks.jsonl
```

任务状态不放在 `inbox/` 里。`inbox/` 只保存原始捕捉，`tasks/tasks.jsonl` 保存任务生命周期事件。

典型事件：

- `task_created`
- `task_updated`
- `task_completed`
- `task_cancelled`
- `task_deferred`

每个任务有稳定的 `task_id`。当前任务状态由事件流重建，而不是靠某个唯一表格覆盖。这么做的好处是：你可以看到任务是怎么来的、什么时候延期、什么时候完成或取消。

任务/日程网页入口当前暂时下线。底层事件流、确认队列里的 `task_candidate`、以及上下文结构仍然保留，方便以后重新启用。

日程当前写入 `inbox/YYYY-MM-DD.jsonl` 的 `calendar_capture`。它暂时不进入复杂日历库，主要用于当天秘书简报和后续提醒。

### 5. 确认队列层

位置：

```text
review_queue/YYYY-MM-DD.jsonl
```

AI 产物默认先进入确认队列。支持的候选类型：

- `memory_candidate`
- `task_candidate`
- `knowledge_candidate`
- `idea_seed_candidate`

确认事件也写在队列里：

- 接受：`review_decision`，`decision: accepted`
- 拒绝：`review_decision`，`decision: rejected`

接受后才会真正写入目标位置，例如：

- 记忆候选接受后写入 `memory/preferences.jsonl`。
- 任务候选接受后写入 `tasks/tasks.jsonl`。
- 知识候选和点子候选目前先保留队列能力，后续可接入更完整的“编辑后接受并写库”流程。

这个机制让秘书可以“主动观察和建议”，但不会越权替你修改长期事实。

### 6. 产出层

主要输出位置：

```text
synthesis/daily_reports/YYYY-MM-DD/
```

一个完整日报目录通常包含：

```text
report_context.json
sources.json
report_brief.json
report.tex
report.pdf
quality_check.json
knowledge_sync.json
```

各文件作用：

- `report_context.json`：脚本收集出的写作上下文，包括关键词、随心记、本地知识、搜索来源、秘书上下文。
- `sources.json`：联网搜索结果和来源分层。
- `report_brief.json`：Codex automation 写出的结构化报告正文。
- `report.tex`：由脚本把 brief 渲染成 LaTeX。
- `report.pdf`：最终可读报告。
- `quality_check.json`：结构与质量检查结果。
- `knowledge_sync.json`：知识同步脚本的结果记录。

## 网页里能做什么

网页由三个文件构成：

```text
web/index.html
web/static/app.js
web/static/styles.css
```

后端在：

```text
app.py
```

当前网页区域：

- 今日收集台：关键词输入、随心记。
- 秘书工作台：待确认。任务捕捉、日程捕捉和当前任务栏当前暂时下线。
- 阅读索引：最近日报、知识节点、点子种子。
- 阅读器：预览 PDF、Markdown、TeX 等允许读取的文件。

网页提交后会写入本地文件，并默认自动 Git 同步。如果你不想自动同步，用：

```powershell
$env:IDEA_SPROUT_AUTO_GIT_SYNC="0"; python app.py
```

## 日报如何升级成秘书简报

当前日报主体仍保持原逻辑：

1. 今日总结。
2. 今日输入。
3. 今日新知。
4. 与旧知识的链接。
5. 今日发芽点子。
6. 参考搜索内容。
7. 随心记复盘，仅当当天有随心记时出现。

秘书模块作为可选尾部模块逐步加入：

- 个人记忆候选：AI 认为可能值得写入长期记忆的观察。
- 任务与跟进：基于已确认任务和当天任务/日程输入生成。
- 明日秘书提醒：明天最该关注的 1-3 件事。

空模块应该省略，不写套话。

## 如何更好地使用这个系统

### 白天输入要短，但要带一点上下文

关键词不要只写名词。最好加一句“为什么今天注意到它”。

好的输入：

```text
关键词：AI 产品经理工作流
补充信息：今天看到一个团队把 PRD、原型和验收标准都交给多智能体协作，想判断这是不是我之后做项目管理工具的方向。
权重：4
```

比只写“AI 产品经理”更有用。

### 随心记不要怕乱

随心记不是正式笔记。它适合记录：

- 一个突然冒出来的判断。
- 一段情绪。
- 一个还没想清楚的问题。
- 对某件事的犹豫。

系统会把它放在独立复盘里，不会污染关键词日报主体。真正有长期价值的内容会先变成候选，等你确认。

### 任务要写成可执行动作

不要写：

```text
研究比赛
```

更适合写：

```text
整理中美创客大赛的报名条件和截止日期
```

任务模块当前暂时下线。以后恢复时，任务应尽量写成可执行动作，并带上截止日期或完成标准。

### 每天看“待确认”

待确认区是秘书体系变聪明的关键。你接受什么、拒绝什么，决定了系统怎样理解你。

建议每天花 2-5 分钟做三件事：

- 接受明显正确的记忆候选。
- 编辑后接受表达不准但方向对的候选。
- 拒绝过度推断、情绪化、短期噪声。

### 每周做一次复盘

日报适合当天理解，周报更适合更新个人画像。后续建议加入周报脚本，集中处理：

- 本周反复主题。
- 任务拖延。
- 兴趣漂移。
- 值得写入长期画像的稳定变化。
- 值得清理或归档的点子种子。

## 重要文件和目录

### 根目录

- `README.md`：系统说明和使用手册。
- `PLAN.md`：当前秘书路线图。
- `CODEX_HANDOFF.md`：给下一次 Codex 接手时看的工作交接。
- `app.py`：零依赖本地 Web 服务，负责登录、API、文件读取、输入写入和自动 Git 同步。

### 输入和状态

- `inbox/`：每日原始输入。
- `memory/`：长期记忆层。
- `tasks/`：任务事件流。
- `review_queue/`：候选和确认结果。
- `tracking/topics.md`：长期追踪主题。

### 知识和点子

- `knowledge/`：当前主知识节点库。
- `synthesis/idea_seeds/`：当前主点子种子库。
- `library/nodes/`：历史兼容知识节点，只读。
- `library/seeds/`：历史兼容点子种子，只读。

### 报告和合成

- `synthesis/daily_reports/`：每日结构化报告输出。
- `reports/daily/`：历史兼容日报目录，不是当前主输出。

### 脚本

- `scripts/context_builder.py`：统一上下文读取层。
- `scripts/generate-radar-report.py`：日报收集、搜索、渲染、质量检查。
- `scripts/sync-knowledge-from-report.py`：把通过质量检查的日报沉淀到知识节点和点子种子。
- `scripts/send-daily-report.py`：发送日报邮件。
- `scripts/validate-structure.ps1`：检查关键目录和文件是否完整。
- `scripts/start-web.ps1`：启动网页服务。
- `scripts/compile-radar-report.ps1`：单独编译 LaTeX 报告。
- `scripts/install-nightly-mailer.ps1`：安装 23:00 本地发信任务。
- `scripts/install-startup-catchup.ps1`：安装开机补跑任务。

### 配置和规则

- `system/report_config.json`：日报配置，例如是否联网搜索、LaTeX 引擎等。
- `system/report_quality_rules.md`：日报质量规则。
- `system/architecture.md`：当前数据架构说明。
- `automation/nightly-codex-prompt.md`：夜间 Codex automation 的主提示词。
- `automation/catch-up-codex-prompt.md`：补跑日报时使用的提示词。
- `templates/report-brief.json`：Codex 应写出的日报 brief 结构。
- `templates/daily-input.md`：每日 Markdown 输入模板。
- `templates/knowledge-node.md`：知识节点模板。
- `templates/idea-seed.md`：点子种子模板。

### 本地秘密文件

这些文件应保留在本地，不要提交真实内容：

- `config/local_auth.json`
- `config/email_auth.json`

仓库里只保留示例：

- `config/local_auth.example.json`
- `config/email_auth.example.json`

## 常用命令

启动网页：

```powershell
python app.py
```

禁用自动 Git 同步后启动：

```powershell
$env:IDEA_SPROUT_AUTO_GIT_SYNC="0"; python app.py
```

收集某天日报上下文：

```powershell
python scripts/generate-radar-report.py --date 2026-06-01 --collect-only
```

渲染某天日报：

```powershell
python scripts/generate-radar-report.py --date 2026-06-01 --render-only
```

只生成 TeX，不编译 PDF：

```powershell
python scripts/generate-radar-report.py --date 2026-06-01 --render-only --no-compile
```

关闭联网搜索：

```powershell
python scripts/generate-radar-report.py --date 2026-06-01 --collect-only --no-web
```

同步知识，先 dry-run：

```powershell
python scripts/sync-knowledge-from-report.py --date 2026-06-01 --dry-run
```

真正同步：

```powershell
python scripts/sync-knowledge-from-report.py --date 2026-06-01
```

检查结构：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate-structure.ps1
```

Python 编译检查：

```powershell
python -m py_compile app.py scripts/generate-radar-report.py scripts/sync-knowledge-from-report.py scripts/send-daily-report.py scripts/context_builder.py
```

检查 Git 空白问题：

```powershell
git diff --check
```

## 邮件发送

PDF 报告可以通过本地 SMTP 发送。真实配置在：

```text
config/email_auth.json
```

先复制示例：

```powershell
Copy-Item .\config\email_auth.example.json .\config\email_auth.json
```

手动 dry-run：

```powershell
python scripts/send-daily-report.py --date 2026-06-01 --dry-run
```

真正发送：

```powershell
python scripts/send-daily-report.py --date 2026-06-01
```

安装每天 23:00 本地发信任务：

```powershell
.\scripts\install-nightly-mailer.ps1
```

安装开机补跑任务：

```powershell
.\scripts\install-startup-catchup.ps1
```

卸载：

```powershell
.\scripts\uninstall-nightly-mailer.ps1
.\scripts\uninstall-startup-catchup.ps1
```

## 手机访问

默认服务监听 `0.0.0.0`，同一局域网内手机可以访问电脑的局域网 IP。

注意：

- 手机上不能打开 `127.0.0.1:3000`，那指向手机自己。
- 电脑和手机需要在同一个 Wi-Fi 或局域网。
- 校园网、公司网、WPA2-Enterprise Wi-Fi 可能开启客户端隔离。
- VPN 可能阻止局域网访问。
- Windows 防火墙可能拦截 Python 或端口。

诊断：

```powershell
.\scripts\diagnose-phone-access.ps1
```

如果确认是防火墙问题，可以用管理员 PowerShell：

```powershell
.\scripts\allow-phone-firewall.ps1
```

如果只想电脑本机访问：

```powershell
$env:IDEA_SPROUT_HOST="127.0.0.1"; python app.py
```

## 安全边界

这是个人本地工具，不是正式多用户 Web 产品。

当前安全能力：

- 本地密码登录。
- httpOnly session cookie。
- 允许读取的文件路径有白名单限制。
- 真实密码和邮箱配置被 `.gitignore` 忽略。

当前不做：

- 多用户权限。
- 注册和找回密码。
- HTTPS。
- CSRF 完整防护。
- 公网部署安全加固。

不要把它直接暴露到公网。如果以后要公网访问，需要补 HTTPS、正式 session 存储、密码哈希策略、CSRF、访问日志和更严格权限边界。

## 未来扩展路线

### 第一阶段：稳住秘书骨架

已经具备：

- 多类型原始输入。
- 长期记忆目录。
- 任务事件流。
- 确认队列。
- 统一上下文构建层。
- 日报尾部秘书模块。

接下来应该补：

- 更好的待确认编辑界面。
- 任务编辑和批量清理。
- 记忆候选写入 `profile.md` / `themes.md` 的更精细规则。
- 周报脚本。

### 第二阶段：周报和画像更新

周报比日报更适合更新长期画像。建议新增：

```text
scripts/generate-weekly-review.py
synthesis/weekly_reports/YYYY-WW/
```

周报负责：

- 汇总本周反复主题。
- 生成长期画像候选。
- 检查任务拖延。
- 检查兴趣漂移。
- 清理低价值点子种子。

### 第三阶段：链接和文章

新增输入：

```json
{
  "kind": "link_capture",
  "payload": {
    "url": "https://example.com",
    "excerpt": "摘录",
    "why_saved": "为什么保存",
    "read_later": true
  }
}
```

建议先做：

- 手动保存 URL。
- 记录为什么保存。
- 生成阅读摘要。
- 只产生知识候选，不自动沉淀。

### 第四阶段：文件和长笔记

支持论文、PDF、课程笔记、项目文档。

原则：

- 不把大文件全文塞进日报。
- 保存文件索引、摘要、关键摘录和来源路径。
- 对长文生成候选，而不是自动写知识库。

### 第五阶段：对话、会议、邮件

这类信息最敏感，应该晚一点做。

建议顺序：

1. 手动导入会议纪要。
2. 摘要进入确认队列。
3. 确认后生成任务或记忆候选。
4. 最后再考虑连接飞书、邮箱、日历。

### 第六阶段：主动秘书

当记忆、任务、日程和知识库足够稳定后，可以做主动建议：

- 每日早晨建议。
- 明日准备事项。
- 长期目标冲突提醒。
- 重复拖延提醒。
- 任务拆解建议。

仍然保持“确认后执行”：创建任务、改任务状态、更新画像、沉淀知识都需要你确认。

## 开发和维护建议

改代码前先看：

```text
CODEX_HANDOFF.md
system/architecture.md
PLAN.md
```

改日报逻辑后至少跑：

```powershell
python -m py_compile app.py scripts/generate-radar-report.py scripts/sync-knowledge-from-report.py scripts/context_builder.py
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate-structure.ps1
git diff --check
```

改前端后至少跑：

```powershell
node --check web/static/app.js
python app.py
```

改知识同步后跑：

```powershell
python scripts/sync-knowledge-from-report.py --date 2026-05-26 --dry-run
```

改输入结构后确认：

- 旧 Markdown-only inbox 仍可读。
- `keyword_batch` 行为不变。
- `free_note` 不影响关键词主体。
- 新 kind 不影响旧日报生成。
- 未确认候选不会被当作事实。

## 当前不做什么

- 不引入正式数据库。
- 不引入向量库。
- 不引入知识图谱引擎。
- 不使用 OpenAI API。
- 不自动更新长期记忆。
- 不自动创建真实任务。
- 不自动把知识节点升权到 4/5。
- 不把所有内容混进日报。
- 不把敏感外部信息源过早自动接入。

## 故障排查

如果网页打不开：

1. 确认 `python app.py` 正在运行。
2. 换端口启动。
3. 本机先试 `http://127.0.0.1:3000`。
4. 手机访问时检查局域网、VPN、防火墙。

如果日报生成失败：

1. 先运行 `--collect-only` 看 `report_context.json` 是否生成。
2. 检查 `report_brief.json` 是否存在且 JSON 合法。
3. 运行 `--render-only --no-compile` 看 `report.tex` 是否生成。
4. 如果 PDF 编译失败，检查 LaTeX 日志。

如果知识同步异常：

1. 先用 `--dry-run`。
2. 检查 `quality_check.json` 是否通过。
3. 检查 `report_brief.json` 的 `knowledge_cards` 和 `idea_seeds`。

如果确认队列没出现：

1. 检查 `report_brief.json` 是否包含 `memory_candidates`、`task_candidates` 等字段。
2. 重新运行 `generate-radar-report.py --render-only`。
3. 查看 `review_queue/YYYY-MM-DD.jsonl` 是否写入候选。

## 一句话总结

`inbox/` 记录你今天真实输入了什么，`context_builder.py` 把这些输入和长期状态组装成上下文，日报负责理解和提出候选，`review_queue/` 让你确认，`memory/` 和 `tasks/` 只保存确认后的长期事实。这个系统真正变聪明的方式，不是一次性自动化所有东西，而是每天把“观察、建议、确认、沉淀”这条链路跑顺。
