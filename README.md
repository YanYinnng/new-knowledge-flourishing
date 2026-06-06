# 点子发芽：本地优先的个人秘书与知识孵化系统

`点子发芽` 是一个本地优先的个人信息系统。它从“每天写几个关键词，晚上生成一份知识报告”开始，正在升级成一个可确认、可追踪、逐渐理解你的个人秘书。

系统不依赖正式数据库、向量库、知识图谱引擎或 OpenAI API。事实主要保存在 Markdown、JSONL、JSON、LaTeX/PDF 和少量 Python/PowerShell 脚本里。你可以直接读文件、用 Git 备份，也可以通过网页完成日常输入、阅读和确认。

## 当前状态

截至 2026-06-05，当前可用能力是：

- 网页输入：关键词、补充信息、权重、随心记。
- 网页确认：待确认候选的接受、编辑后接受、拒绝。
- 网页阅读：日报 PDF/TeX、知识节点、点子种子。
- 日报生成：按关键词生成秘书简报，随心记独立复盘，不污染关键词主体。
- 搜索来源：优先用 opencli 平台/学术搜索，并用 Bing、百度、DuckDuckGo HTML 兜底。
- 语言治理：日报会读取 `system/report_voice_rules.md`，质量检查会拦截明显 AI 腔、元话语和过短的“最近有什么相关的事情”。
- 知识沉淀：质量通过后，日报可同步到 `knowledge/` 和 `synthesis/idea_seeds/`。

当前暂时下线的能力：

- 网页里的“任务捕捉”。
- 网页里的“日程捕捉”。
- 网页里的“当前任务”栏。

底层任务/日程结构仍保留，方便以后恢复；但当前前端和公开路由只暴露关键词、随心记、待确认、阅读器这些入口。

## 核心原则

1. 原始输入 append-only。每天输入写入 `inbox/YYYY-MM-DD.jsonl`，旧事件不覆盖。
2. Markdown 负责可读，JSONL 负责事实。人看 Markdown，脚本读 JSONL。
3. AI 只提出候选，不自动替你改长期事实。
4. 长期记忆、任务状态、知识节点分层存储。
5. 所有关键写入尽量可追溯：来源、时间、候选、确认结果都留在本地文件里。

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

调试时如果不想让网页提交后自动 Git commit / push：

```powershell
$env:IDEA_SPROUT_AUTO_GIT_SYNC="0"; python app.py
```

也可以用脚本启动：

```powershell
.\scripts\start-web.ps1
```

第一次启动会自动创建 `config/local_auth.json`。默认密码是 `change-me`，请尽快修改。

## 每天怎么用

白天主要输入两类东西：

1. 关键词：今天注意到的概念、人物、项目、问题、机会。每行一个关键词，可加补充信息和权重。
2. 随心记：突发想法、感受、判断、困惑。它只进入独立复盘和候选生成，不参与关键词日报主体。

好的关键词输入应该带一点上下文：

```text
关键词：AI 产品经理工作流
补充信息：今天看到一个团队把 PRD、原型和验收标准交给多智能体协作，想判断这是不是之后做项目管理工具的方向。
权重：4
```

随心记不需要写得正式。它适合保留还没变成关键词的个人状态、犹豫和直觉。真正有长期价值的内容会先进入待确认队列，等你接受后再沉淀。

晚上日报流程通常是：

```powershell
python scripts/generate-radar-report.py --date YYYY-MM-DD --collect-only
```

Codex automation 读取上下文后写入：

```text
synthesis/daily_reports/YYYY-MM-DD/report_brief.json
```

然后渲染：

```powershell
python scripts/generate-radar-report.py --date YYYY-MM-DD --render-only
```

质量通过后同步知识：

```powershell
python scripts/sync-knowledge-from-report.py --date YYYY-MM-DD
```

## 整体架构

系统分为六层。

### 1. 原始输入层

位置：

```text
inbox/YYYY-MM-DD.md
inbox/YYYY-MM-DD.jsonl
```

`inbox/YYYY-MM-DD.md` 是人类可读日志。网页每次提交后会追加一段 Markdown，方便直接查看当天写了什么。

`inbox/YYYY-MM-DD.jsonl` 是结构化事实来源。每行都是一个事件：

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

当前活跃输入：

- `keyword_batch`：关键词日报主体。
- `free_note`：随心记，只用于独立复盘和候选生成。

保留但当前网页不开放的输入：

- `task_capture`：任务捕捉，后续恢复任务模块时使用。
- `calendar_capture`：日程捕捉，后续恢复日程模块时使用。
- `link_capture`：链接/文章输入预留。
- `file_capture`：文件/长笔记输入预留。

### 2. 统一上下文层

位置：

```text
scripts/context_builder.py
```

这是秘书体系的统一读取入口。日报脚本通过它把 `secretary_context` 写入 `report_context.json`。目前它读取：

- 当天原始输入统计。
- 当天非关键词捕捉项，例如保留的任务、日程、链接、文件类型。
- 长期记忆：`memory/profile.md`、`memory/themes.md`、`memory/preferences.jsonl`。
- 任务事件流重建出的未完成或临近截止任务。
- 待确认队列。

未来做秘书问答、周报、任务建议、主动提醒时，都应该优先复用这层，避免每个脚本各读各的。

### 3. 长期记忆层

位置：

```text
memory/profile.md
memory/preferences.jsonl
memory/themes.md
```

`memory/profile.md` 保存稳定个人画像，例如长期目标、工作方式、项目方向、身份变化。

`memory/preferences.jsonl` 保存可追溯偏好事件，例如报告语气、交互偏好、希望系统怎样提醒你。它是事件流，因为偏好可能变化。

`memory/themes.md` 保存长期反复主题和当前关注强度。

日报和随心记可以产生 `memory_candidate`，但不会自动改写 `memory/`。只有你在网页“待确认”里接受后，才会写入长期记忆相关文件。

### 4. 确认队列层

位置：

```text
review_queue/YYYY-MM-DD.jsonl
```

AI 产物默认先进入确认队列。支持的候选类型：

- `memory_candidate`
- `task_candidate`
- `knowledge_candidate`
- `idea_seed_candidate`
- `weight_change_candidate`

待确认项出现的标准很明确：`report_brief.json` 里必须包含这些候选字段，且渲染阶段会执行 `sync_review_queue_from_brief()`。如果当天报告没有写候选字段，待确认区就不会凭空出现东西。

确认事件也写入队列：

- 接受：`review_decision`，`decision: accepted`
- 拒绝：`review_decision`，`decision: rejected`

接受后的行为：

- `memory_candidate`：写入 `memory/preferences.jsonl`。
- `task_candidate`：写入 `tasks/tasks.jsonl`，创建任务事件。
- `weight_change_candidate`：写入 `tracking/weight_decisions.jsonl`。如果候选里有安全路径，例如 `knowledge/...md` 或 `synthesis/idea_seeds/...md`，还会更新该 Markdown 文件里的 `Weight:`。
- `knowledge_candidate`、`idea_seed_candidate`：当前主要保留候选和确认能力，后续再接入更完整的编辑后写库流程。

这套机制的目的很简单：AI 可以主动观察，但不能越权替你改长期事实。

### 5. 任务与日程层

位置：

```text
tasks/tasks.jsonl
```

任务状态不放在 `inbox/` 里。`inbox/` 只保存原始捕捉；`tasks/tasks.jsonl` 保存任务生命周期事件：

- `task_created`
- `task_updated`
- `task_completed`
- `task_cancelled`
- `task_deferred`

每个任务有稳定 `task_id`，当前状态由事件流重建。

当前任务/日程网页入口已经下线；`app.py` 里仍保留部分辅助函数和任务事件流能力，但前端没有入口，公开 POST 路由也没有暴露任务/日程捕捉。现在只有接受 `task_candidate` 时才会写入任务事件流。

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

文件作用：

- `report_context.json`：写报告时的上下文，包括关键词、随心记、本地知识、搜索来源、秘书上下文、写作约束。
- `sources.json`：联网搜索结果和来源分层。
- `report_brief.json`：Codex automation 写出的结构化报告正文。
- `report.tex`：由脚本把 brief 渲染成 LaTeX。
- `report.pdf`：最终可读报告。
- `quality_check.json`：结构、来源、随心记隔离、语言风格、新闻密度等检查结果。
- `knowledge_sync.json`：知识同步脚本的结果记录。

## AI 写报告时看到什么

日报不是让 AI 随便读整个项目。标准流程里，AI 主要读取这些文件：

- `system/report_quality_rules.md`
- `system/report_voice_rules.md`
- `templates/report-brief.json`
- `synthesis/daily_reports/YYYY-MM-DD/report_context.json`
- `synthesis/daily_reports/YYYY-MM-DD/sources.json`

`report_context.json` 里最关键的字段：

- `inputs`：当天关键词、补充信息、权重。
- `free_notes`：当天随心记，只能用于最后的随心记复盘。
- `keyword_contexts`：每个关键词的搜索结果、本地知识匹配、写作问题。
- `local_knowledge`：追踪主题、高权重知识节点、点子种子摘要。
- `reference_sources`：可引用来源列表。
- `secretary_context`：个人画像、偏好、长期主题、任务状态、待确认队列。
- `writing_contract`：报告结构、写作限制、语言规范摘要。

所以，是的：当前个人画像、偏好、长期主题、任务状态、待确认项这些“秘书记忆”主要通过 `secretary_context` 传给报告 AI。报告 AI 还会看到关键词相关的本地知识匹配和搜索来源，但不会自动把整个 `memory/`、`knowledge/`、`tasks/` 全量塞进正文。

## 报告语言与搜索

日报语言由两层控制：

- 写作规范：`system/report_voice_rules.md`
- 自动检查：`voice_smell_check()` 和 `recent_news_quality_issues()`

当前规则要求：

- 正文像私人秘书晚间简报，不像公司周报、学术摘要或 AI 自我说明。
- 分析过程留在幕后，正文直接写结论。
- 禁止明显元话语，例如“本地旧节点显示”“可连接”“脚本 fallback”。
- 限制高频句式，例如“不是/而是”“只是”“更像”“判断”。
- “最近有什么相关的事情”必须具体说明近期出现的活动、项目、论文、产品、讨论或政策动向；有来源时不能只写“检索到 S1/S2/S3”。

搜索逻辑由 `scripts/generate-radar-report.py` 和 `system/report_config.json` 控制。当前配置：

- opencli：微信、B 站、知乎、微博、百度学术、万方、知网、Google Scholar。
- 直接网页兜底：Bing、百度、DuckDuckGo HTML。
- 每条来源会记录 `search_channel`、`search_endpoint`、`search_query`，方便回溯。

检查 opencli：

```powershell
opencli doctor
```

如果 opencli 某个站点失败，报告仍可用直接网页搜索兜底；但平台线索会减少。

## 网页里能做什么

前端文件：

```text
web/index.html
web/static/app.js
web/static/styles.css
```

后端：

```text
app.py
```

当前网页区域：

- 今日收集台：关键词输入、随心记。
- 秘书工作台：待确认。
- 阅读索引：最近日报、知识节点、点子种子。
- 阅读器：预览 PDF、Markdown、TeX 等允许读取的文件。

当前公开 API 主要包括：

- `POST /api/keywords`
- `POST /api/free-notes`
- `POST /api/review`
- `GET /api/overview`
- `GET /api/file`
- `GET /api/raw`

网页提交后会写入本地文件，并默认自动 Git 同步。如果你不想自动同步：

```powershell
$env:IDEA_SPROUT_AUTO_GIT_SYNC="0"; python app.py
```

## 重要文件和目录

### 根目录

- `README.md`：系统说明和使用手册。
- `PLAN.md`：秘书路线图。
- `CODEX_HANDOFF.md`：给下一次 Codex 接手时看的工作交接。
- `app.py`：零依赖本地 Web 服务，负责登录、API、文件读取、输入写入和自动 Git 同步。

### 输入和状态

- `inbox/`：每日原始输入。
- `memory/`：长期记忆层。
- `tasks/`：任务事件流，当前主要供待确认任务候选接受后写入。
- `review_queue/`：候选和确认结果。
- `tracking/topics.md`：长期追踪主题。
- `tracking/weight_decisions.jsonl`：确认后的权重调整记录，第一次接受权重候选时自动创建。

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

- `system/report_config.json`：日报配置，例如联网搜索、来源通道、LaTeX 引擎。
- `system/report_quality_rules.md`：日报质量规则。
- `system/report_voice_rules.md`：日报语言规范。
- `system/architecture.md`：当前数据架构摘要。
- `automation/nightly-codex-prompt.md`：夜间 Codex automation 主提示。
- `automation/catch-up-codex-prompt.md`：补跑日报提示。
- `templates/report-brief.json`：Codex 应写出的日报 brief 结构。
- `templates/daily-input.md`：每日 Markdown 输入模板。
- `templates/knowledge-node.md`：知识节点模板。
- `templates/idea-seed.md`：点子种子模板。

### 本地秘密文件

真实配置应留在本地，不要提交：

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

关闭联网搜索收集：

```powershell
python scripts/generate-radar-report.py --date 2026-06-01 --collect-only --no-web
```

渲染某天日报：

```powershell
python scripts/generate-radar-report.py --date 2026-06-01 --render-only
```

只生成 TeX，不编译 PDF：

```powershell
python scripts/generate-radar-report.py --date 2026-06-01 --render-only --no-compile
```

知识同步 dry-run：

```powershell
python scripts/sync-knowledge-from-report.py --date 2026-06-01 --dry-run
```

真正同步：

```powershell
python scripts/sync-knowledge-from-report.py --date 2026-06-01
```

结构检查：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate-structure.ps1
```

Python 编译检查：

```powershell
python -m py_compile app.py scripts/generate-radar-report.py scripts/sync-knowledge-from-report.py scripts/send-daily-report.py scripts/context_builder.py
```

前端 JS 语法检查：

```powershell
node --check web/static/app.js
```

Git 空白检查：

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
- 完整 CSRF 防护。
- 公网部署安全加固。

不要把它直接暴露到公网。如果以后要公网访问，需要补 HTTPS、正式 session 存储、密码哈希策略、CSRF、访问日志和更严格权限边界。

## 当前体检结论

2026-06-05 做过一次基础体检：

- `python -m py_compile ...` 通过。
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate-structure.ps1` 通过。
- `node --check web/static/app.js` 通过。
- `git diff --check` 只输出 LF/CRLF 换行提示，没有空白错误。
- `python scripts/sync-knowledge-from-report.py --date 2026-05-26 --dry-run` 可运行；结果显示旧报告会更新一个已有知识节点，两个点子种子已同步。

已知状态：

- 工作区有多处未提交修改和生成文件，处理前先看 `git status --short`。
- 任务/日程底层结构存在，但当前网页入口下线。
- `CODEX_HANDOFF.md` 是新对话接手时的第一入口。

## 未来扩展路线

### 第一阶段：稳住秘书骨架

已经具备：

- 多类型原始输入底座。
- 长期记忆目录。
- 确认队列。
- 权重调整候选。
- 统一上下文构建层。
- 日报尾部秘书模块。
- 日报语言质量检查。

接下来更值得补：

- 待确认项的编辑界面更细一点，不只编辑合成文本。
- 记忆候选写入 `profile.md` / `themes.md` 的更精细规则。
- 周报脚本。
- 更稳定的 source quality 评估。

任务编辑、日程捕捉可以晚一点恢复，除非你确认它已经重新变成高频需求。

### 第二阶段：周报和画像更新

建议新增：

```text
scripts/generate-weekly-review.py
synthesis/weekly_reports/YYYY-WW/
```

周报负责：

- 汇总本周反复主题。
- 生成长期画像候选。
- 检查兴趣漂移。
- 清理低价值点子种子。
- 只在确认后更新长期记忆。

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

如果“最近有什么相关的事情”太短：

1. 检查 `keyword_contexts[].search_results` 是否有来源。
2. 检查 `report_brief.json` 的 `knowledge_cards[].recent_news` 是否只是列来源编号，或既少于 3 句、又没有 2 条以上具体线索。
3. 重新写 brief 后运行 `--render-only`，质量检查会拦截过短、只列来源、没有具体事情的正文。

如果待确认队列没出现：

1. 检查 `report_brief.json` 是否包含 `memory_candidates`、`task_candidates`、`knowledge_candidates`、`idea_seed_candidates`、`weight_change_candidates`。
2. 重新运行 `generate-radar-report.py --render-only`。
3. 查看 `review_queue/YYYY-MM-DD.jsonl` 是否写入候选。

如果知识同步异常：

1. 先用 `--dry-run`。
2. 检查 `quality_check.json` 是否通过。
3. 检查 `report_brief.json` 的 `knowledge_cards` 和 `idea_seeds`。

## 给新 Codex 的入口

新开对话时，先让 Codex 读：

```text
CODEX_HANDOFF.md
README.md
system/architecture.md
```

然后运行：

```powershell
git status --short
```

这个项目的工作区经常包含未提交的生成文件。不要让新 Codex 一上来 reset、checkout 或删除未知文件。

## 一句话总结

`inbox/` 记录你真实输入了什么，`context_builder.py` 把长期状态组装成 `secretary_context`，日报负责理解和提出候选，`review_queue/` 让你确认，`memory/`、`tasks/`、`knowledge/` 只保存确认后或质量通过后的长期事实。这个系统变聪明的方式，是把“观察、建议、确认、沉淀”这条链路持续跑顺。
