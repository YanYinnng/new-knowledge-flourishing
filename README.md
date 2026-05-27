# 点子发芽 MVP

这是一个个人自用的新知孵化工作流。日常输入只通过网页完成，系统用本地 Markdown 文件、少量 PowerShell 脚本和 Codex automation 提示词支撑每天的轻量输入、晚间整理和旧内容复盘。

第一版的原则很简单：白天在网页里写关键词、一点补充信息和可选权重；晚上看一份 PDF 版“个人认知雷达报告”；最后由你决定什么保留、什么合并、什么升权或降权。

## 每天怎么用

日常使用只走网页入口。Markdown 文件是本地存储和备份，不再作为主要输入界面。

### 网页入口

本项目新增了一个零依赖的本地 Python Web 服务。它只服务本机浏览器，不需要数据库，也不使用 OpenAI API。

启动：

```powershell
python app.py
```

也可以用脚本启动，效果相同，会明确面向手机访问：

```powershell
.\scripts\start-web.ps1
```

电脑本机访问：

```text
http://127.0.0.1:3000
```

手机访问：

1. 确保手机和电脑在同一个 Wi-Fi 或局域网里。
2. 启动服务后，看终端里打印的“手机访问链接”。
3. 在手机浏览器里打开类似下面的地址：

```text
http://电脑局域网IP:3000
```

例如本机当前 WLAN 地址可能显示为：

```text
http://10.180.77.192:3000
```

注意：手机上不能打开 `http://127.0.0.1:3000`，因为手机里的 `127.0.0.1` 指的是手机自己，不是电脑。

如果 3000 端口被占用，可以临时换端口：

```powershell
$env:IDEA_SPROUT_PORT="3001"; python app.py
```

如果只想让电脑本机能访问，不开放给同一局域网的手机：

```powershell
$env:IDEA_SPROUT_HOST="127.0.0.1"; python app.py
```

如果手机打不开，但电脑本机能打开，通常是 Windows 防火墙拦截了 Python 或 3000 端口。允许 Python 通过当前专用网络，或在防火墙里放行 3000 端口后再试。

如果电脑正在开 VPN，也可能出现手机打不开的情况。优先检查 VPN 客户端里是否有“允许局域网访问”“Allow LAN”“Bypass local network”之类的开关；如果有，打开它。仍然打不开时，可以临时暂停 VPN 再试。除非手机也接入同一个 VPN，否则不要优先使用 VPN 分配的 IP，优先使用 `WLAN` 或 `Ethernet` 对应的 IPv4 地址。

如果你连接的是校园网、公司网或 `WPA2-Enterprise` Wi-Fi，例如 `SJTU`，即使手机和电脑连着同一个 Wi-Fi，也可能因为网络启用了“客户端隔离”而无法互相访问。这种情况下网页服务本身已经启动成功，防火墙也可能没问题，但手机请求到不了电脑。

校园网下更稳的做法：

1. 让电脑连接手机热点，然后重新运行 `.\scripts\start-web.ps1`，用脚本新打印的手机访问链接。
2. 或者让电脑开启 Windows 移动热点，手机连接电脑热点，再访问脚本打印的地址。
3. 如果必须使用校园网，只能看学校网络是否允许同网设备互访；项目代码无法绕过校园网的客户端隔离。

排查脚本：

```powershell
.\scripts\diagnose-phone-access.ps1
```

如果诊断显示服务监听正常、本机访问正常，但手机打不开，优先怀疑校园网客户端隔离。若你确认不是校园网隔离，而是 Windows 防火墙，可以用管理员 PowerShell 运行：

```powershell
.\scripts\allow-phone-firewall.ps1
```

安装依赖：不需要额外安装依赖，只需要本机有 Python 3。

### 设置或修改本地密码

真实密码配置文件是 `config/local_auth.json`。第一次启动 `python app.py` 时，如果该文件不存在，服务会自动创建一个本地配置文件，默认密码是 `change-me`。

建议第一次启动后立刻编辑：

```json
{
  "password": "你的本地密码",
  "session_secret": "一段较长的随机字符串"
}
```

也可以先复制示例文件：

```powershell
Copy-Item .\config\local_auth.example.json .\config\local_auth.json
```

然后修改其中的 `password` 和 `session_secret`。`config/local_auth.json` 已写入 `.gitignore`，不要把真实密码提交进 Git。

### 登录状态

输入正确密码后，服务会写入一个带签名的 `httpOnly` cookie：`idea_sprout_session`。cookie 和服务端签名内容都会记录过期时间，默认 30 天。

30 天后需要重新登录，是为了让本地门禁不会永久有效。点击网页右上角“退出登录”会清除 cookie，再次访问会回到密码页。

### 网页里能做什么

- 在“网页输入”里一次输入多个关键词，每行一个。
- 为这批关键词写一点补充信息，比如在哪里听到它，或它引发了什么思考。
- 可选填写权重 1-5。
- 提交后内容会按“网页输入记录”格式追加到当天 `inbox/YYYY-MM-DD.md`，不会覆盖旧内容。
- 提交后会自动把当天 `inbox/YYYY-MM-DD.md` 做一次 Git commit，并推送到 `origin` 当前分支。
- 在“最近日报”里优先打开 `synthesis/daily_reports/YYYY-MM-DD/report.pdf`；如果只有 `report.tex`，页面会提示 PDF 尚未生成或编译失败；旧 Markdown 日报仍可兼容查看。
- 在“知识节点”里查看 `knowledge/` 中的知识卡片；页面也会兼容读取旧目录 `library/nodes/`。
- 在“点子种子”里查看 `synthesis/idea_seeds/` 中的点子；页面也会兼容读取旧目录 `library/seeds/`。
- 三栏各自有自己的阅读区：点击“最近日报”只更新日报栏下方的 PDF 预览；点击“知识节点”只更新知识栏下方的 Markdown；点击“点子种子”只更新种子栏下方的 Markdown。

列表读取规则：

- “最近日报”按日报日期排序，优先显示 `synthesis/daily_reports/YYYY-MM-DD/report.pdf`，其次是同目录 `report.tex`。如果同一天已经有结构化 PDF/TEX，旧的 `synthesis/daily_reports/YYYY-MM-DD.md` 不再重复显示在列表里。
- “知识节点”优先显示 `knowledge/`；“点子种子”优先显示 `synthesis/idea_seeds/`。`library/nodes/` 和 `library/seeds/` 仍保留为旧目录兼容来源，但同名文件不会在网页列表中重复出现。
- 点击列表条目时，后端只允许读取白名单目录中的报告、知识节点或点子种子文件；路径不存在或越界时，网页会显示友好的错误提示。

### 自动 Git 同步

网页每次成功提交关键词后，会自动同步这次写入：

1. 只暂存当天 `inbox/YYYY-MM-DD.md`。
2. 自动创建一条提交，格式类似 `Auto sync inbox 2026-05-24 19:30`。
3. 推送到 `origin` 的当前分支。

如果 GitHub 凭据失效、网络断开或远程仓库不可用，本地 Markdown 仍会先写入成功，网页会提示 Git 同步失败原因。你可以之后手动运行：

```powershell
git status
git push
```

真实密码文件 `config/local_auth.json` 和运行日志已在 `.gitignore` 中，自动同步不会主动添加这些文件。

如果临时不想自动提交和推送，可以这样启动：

```powershell
$env:IDEA_SPROUT_AUTO_GIT_SYNC="0"; python app.py
```

### 网页门禁的安全限制

这是本地自用的轻量门禁，不是正式产品级用户系统。它没有注册、多用户、找回密码、邮箱验证或短信验证。

当前方案适合自己电脑和同一局域网内的个人设备访问。手机访问时，局域网内其他设备也可能看到这个服务入口，所以请务必修改默认密码。不要把它直接暴露到公网；如果未来要公网访问，需要升级认证方式，例如 HTTPS、正式 session 存储、密码哈希策略、CSRF 防护、访问日志和更严格的权限边界。

1. 白天打开网页，在“网页输入”里写 3-5 个触发词、链接、人物、问题或粗糙想法。
2. 为这批关键词补一点补充信息，例如从哪里看到、为什么注意、它引发了什么思考。
3. 可选填写权重 1-5；不确定就留空。
4. 晚上查看 `synthesis/daily_reports/YYYY-MM-DD/report.pdf`。报告应该帮助你判断哪些内容值得留下、哪些只是噪音、哪些和旧内容有关。

## 晚上会发生什么

Codex automation 应提前于 23:00 Asia/Shanghai 运行。它不是 23:00 才开始整理，而是预留时间先生成 PDF 报告。

当前目标：每天 22:50 开始整理，先由脚本收集 `report_context.json` 和 `sources.json`，再由 Codex automation 写 `report_brief.json`，最后渲染 `report.tex` 和 `report.pdf`。邮件发送不再放在 Codex automation 里等待或调度，而是交给 Windows 本地计划任务在 23:00 执行。

22:50 视为当日报告的截稿和生成时间。23:00 的发信任务只负责发送当天已经生成的 `report.pdf`，不因为 `inbox/YYYY-MM-DD.md` 的修改时间晚于 `report.pdf` 就默认拒发。22:50 之后追加的输入不会阻塞当晚邮件；如果确实要纳入当晚报告，需要手动重新生成一次当天报告。

如果电脑在 22:50-23:00 之间关机或没有登录，Windows 登录补跑任务会在下一次开机登录后检查漏掉的报告。它会先补生成缺失的 `synthesis/daily_reports/YYYY-MM-DD/report.pdf`，再立刻发送邮件，不会等到当天晚上 23:00。它只处理安装补跑任务之后的日期，避免把旧报告突然重复补发。

automation 只把网页写入的 `关键词`、`补充信息`、`权重` 三类信息当作当天输入依据。权重未填写时默认按 `3` 处理。

如果当天有输入，它会：

- 读取“网页输入记录”中的关键词、补充信息和权重。
- 尽量联网搜索，资料来源写入 `sources.json`，写作材料写入 `report_context.json`。
- 由 Codex automation 综合材料写 `report_brief.json`，而不是让脚本硬编码正文。
- 每个关键词只写“简介 / 最近有什么相关新闻 / 与我相关 / 最小下一步”。
- 在“与旧知识的链接”和“今日发芽点子”中只保留真正相关的内容。
- 报告质量通过后，把新知识和点子沉淀成 `knowledge/` 与 `synthesis/idea_seeds/` 里的候选条目。

如果当天没有有效输入，它会：

- 读取 `tracking/topics.md`。
- 复盘高权重或久未更新的节点。
- 尽量联网检查 watchlist 新进展，无法联网则在报告中说明。
- 生成一份“无新输入复盘模式”报告，最后只给一个“明日一问”。

automation 的完整提示词在 `automation/nightly-codex-prompt.md`。如果需要手动创建 automation，就把该文件内容作为任务提示词，工作目录设为本仓库根目录，时间设为每天 22:50 Asia/Shanghai。这个 automation 不负责发邮件，不等待 23:00。

## PDF 认知雷达报告

每日报告的主输出目录为：

```text
synthesis/daily_reports/YYYY-MM-DD/
├─ report_context.json
├─ report_brief.json
├─ quality_check.json
├─ knowledge_sync.json
├─ report.tex
├─ report.pdf
└─ sources.json
```

收集某天报告材料：

```powershell
python .\scripts\generate-radar-report.py --date 2026-05-24 --collect-only
```

写好 `report_brief.json` 后渲染 PDF：

```powershell
python .\scripts\generate-radar-report.py --date 2026-05-24 --render-only
```

兼容旧的单命令生成某天报告。如果没有 `report_brief.json`，脚本会生成一个低配 fallback brief，质量低于 Codex automation 写作版本：

```powershell
python .\scripts\generate-radar-report.py --date 2026-05-24
```

只生成 `report.tex`，不编译 PDF：

```powershell
python .\scripts\generate-radar-report.py --date 2026-05-24 --render-only --no-compile
```

重新编译某天的 `report.tex`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\compile-radar-report.ps1 -Date 2026-05-24
```

LaTeX 工具要求：优先使用 TeX Live 或 MiKTeX，并确保 `xelatex` 和最好还有 `latexmk` 在 PATH 中。本机当前已检测到 TeX Live 2024 的 `xelatex` 和 `latexmk`。如果编译失败，`report.tex` 会保留，脚本会输出清楚的错误信息，详细日志在当天报告目录。

报告配置在 `system/report_config.json`：

```json
{
  "enable_web_search": true,
  "enable_images": true,
  "enable_ai_generated_images": false,
  "default_report_mode": "auto",
  "default_input_weight": 3,
  "max_idea_seeds_per_report": 3,
  "latex_engine": "xelatex"
}
```

临时关闭联网搜索：

```powershell
python .\scripts\generate-radar-report.py --date 2026-05-24 --no-web
```

联网搜索结果会写入 `sources.json`。如果当前环境无法联网，`report_context.json` 会保留失败说明，Codex automation 需要在正文里明确降低置信度，不要编造新闻。

### 报告质量规则

这份 PDF 不是搜索结果合集。质量规则写在 `system/report_quality_rules.md`，核心要求是：

- PDF 只保留“今日总结 / 今日输入 / 今日新知 / 与旧知识的链接 / 今日发芽点子 / 参考搜索内容”。
- 每个关键词只允许“简介 / 最近有什么相关新闻 / 与我相关 / 最小下一步”四个小节。
- `简介` 约 200-300 字，只专注解释关键词本身，不联系补充信息。
- `最近有什么相关新闻` 最多 1 段或 2 条；没有可靠新进展就明确说明。
- `与我相关` 单独分析关键词和补充信息之间的联系。
- 禁止旧标签：`它是什么`、`今天查到了什么`、`和我有什么关系`、`今日判断`。
- `quality_check.json` 会在编译前拦截旧标签、重复小节、过短简介和搜索结果直贴。

### 日报如何沉淀为新知库

当前主库是：

- 知识节点：`knowledge/*.md`
- 点子种子：`synthesis/idea_seeds/*.md`

旧目录 `library/nodes/` 和 `library/seeds/` 只作为历史兼容读取，不再自动写入，也不做双向同步。

每晚流程是：

1. `generate-radar-report.py --collect-only` 读取当天 inbox，也读取已有 `knowledge/`、`synthesis/idea_seeds/` 和旧 `library/` 内容，写入 `report_context.json`。
2. Codex automation 根据 `report_context.json` 写 `report_brief.json`。
3. `generate-radar-report.py --render-only` 渲染 PDF，并生成 `quality_check.json`。
4. 质量检查通过后，运行：

```powershell
python .\scripts\sync-knowledge-from-report.py --date 2026-05-25
```

同步脚本会把 `knowledge_cards[]` 写成或更新 `knowledge/` 中的候选知识节点，把 `idea_seeds[]` 写成或更新 `synthesis/idea_seeds/` 中的 raw 点子种子，并生成：

```text
synthesis/daily_reports/YYYY-MM-DD/knowledge_sync.json
```

它不会自动把节点升到 4/5，也不会替你做最终判断。你之后可以人工确认、升权、合并或归档。

## 邮件报告

PDF 报告邮件通过本地 SMTP 发送到：

```text
13583286559@163.com
```

真实邮箱授权配置放在 `config/email_auth.json`，该文件已被 `.gitignore` 忽略，不会上传 GitHub。先复制示例：

```powershell
Copy-Item .\config\email_auth.example.json .\config\email_auth.json
```

然后编辑 `config/email_auth.json`：

```json
{
  "smtp_host": "smtp.163.com",
  "smtp_port": 465,
  "use_ssl": true,
  "username": "13583286559@163.com",
  "password": "这里填 163 邮箱 SMTP 授权码，不是网页登录密码",
  "from_email": "13583286559@163.com",
  "to_emails": ["13583286559@163.com"]
}
```

163 邮箱通常需要在网页版邮箱设置中开启 SMTP/POP3/IMAP，并生成“授权码”。不要把邮箱网页登录密码填进这里。

手动测试发送：

```powershell
python .\scripts\send-daily-report.py --date 2026-05-24 --dry-run
```

去掉 `--dry-run` 会真正发送邮件；如果当天 PDF 存在，会优先把 PDF 作为附件发送：

```powershell
python .\scripts\send-daily-report.py --date 2026-05-24
```

发送成功后，脚本会写入 `system/email_sent/YYYY-MM-DD.sent` 作为本地标记。后续本地发信或补跑任务看到这个标记，会跳过同一天的重复发送；如果你确实要手动重发，可以加 `--force`。

安装 23:00 本地发信任务：

```powershell
.\scripts\install-nightly-mailer.ps1
```

这个任务会在每天 23:00 运行 `scripts/send-today-report.ps1`。如果当天 `report.pdf` 还没生成，它会最多等 30 分钟，但这个等待发生在本地 PowerShell 里，不消耗 Codex 用量。

安装开机登录补跑任务：

```powershell
.\scripts\install-startup-catchup.ps1
```

这个任务会在 Windows 登录时运行 `scripts/catch-up-daily-report.ps1`。默认只回看最近 2 天，并且不会处理安装日期之前的日报。手动试运行但不真正生成或发送：

```powershell
.\scripts\catch-up-daily-report.ps1 -DryRun
```

如果以后想移除补跑任务：

```powershell
.\scripts\uninstall-startup-catchup.ps1
```

如果以后想移除 23:00 本地发信任务：

```powershell
.\scripts\uninstall-nightly-mailer.ps1
```

## 如何确认

日报里的建议不是最终决定。第一版网页先覆盖日常输入和查看；如果需要确认节点更新、关系或权重，可以让 Codex 根据日报建议继续处理，或者在后续版本把这些确认动作也搬进网页。

本地文件仍然会保留为可检查的 Markdown：

- 当前知识节点主库：`knowledge/*.md`
- 当前点子种子主库：`synthesis/idea_seeds/*.md`
- 日报来源记录：`synthesis/daily_reports/YYYY-MM-DD/sources.json`
- 历史兼容知识节点：`library/nodes/*.md`
- 历史兼容点子种子：`library/seeds/*.md`
- 长期追踪主题：`tracking/topics.md`

权重 4 或 5 必须由你手动确认。不要让 automation 自动把一个节点升成核心主题。

## 权重 1-5

- 1：低价值存档，见过即可。
- 2：轻度关注，有意思但暂不投入。
- 3：稳定关注，和长期兴趣有关。
- 4：高优先级，近期反复出现或有行动价值。
- 5：核心主题，值得持续追踪，并可能转成项目、文章、研究或产品。

网页输入未填写权重时默认使用 3，表示“稳定关注但还不升为高优先级”。权重 4 或 5 仍需要你手动确认。同一主题 7 天内多次出现，或关联到多个 active 节点，可以建议 +1；30 天无更新且无行动，可以建议 -1。

## Helper 脚本

创建当天输入文件。不建议作为日常入口；网页提交时会自动创建当天文件：

```powershell
.\scripts\new-today.ps1
```

检查目录和关键文件是否完整：

```powershell
.\scripts\validate-structure.ps1
```

生成/编译 PDF 报告：

```powershell
python .\scripts\generate-radar-report.py --date 2026-05-24
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\compile-radar-report.ps1 -Date 2026-05-24
```

把某天已经生成的日报沉淀到候选知识节点和点子种子：

```powershell
python .\scripts\sync-knowledge-from-report.py --date 2026-05-24
```

脚本只辅助，不是主要使用入口。检查失败时，按输出的缺失项修复。

## 第一版不做

- 不使用 OpenAI API。
- 不引入数据库、向量库或知识图谱引擎。
- 不做复杂或正式产品级 Web UI、移动端、多用户、注册登录体系或商业化功能。
- 不做复杂爬虫、自动订阅系统或复杂标签体系。
- 不自动替你做最终判断。
- 不把日报写成资讯简报。

## 文件位置

- `inbox/`：每日原始输入。
- `knowledge/`：当前主知识节点库，网页优先读取，日报沉淀脚本会写入这里。
- `synthesis/daily_reports/`：网页入口优先读取的每日 PDF 认知雷达报告。
- `synthesis/idea_seeds/`：当前主点子种子库，网页优先读取，日报沉淀脚本会写入这里。
- `system/`：预留给本地系统说明和后续轻量配置。
- `library/nodes/`：历史兼容知识节点，只读保留。
- `library/sources/`：历史兼容来源目录；当前日报来源写在 `synthesis/daily_reports/YYYY-MM-DD/sources.json`。
- `library/seeds/`：历史兼容点子种子，只读保留。
- `reports/daily/`：历史兼容日报目录。
- `tracking/topics.md`：长期追踪主题。
- `tracking/README.md`：长期追踪主题的维护说明。
- `templates/`：当前工作流仍使用的结构模板。
- `templates/README.md`：各模板用途和已移除旧模板说明。
- `automation/`：Codex automation 提示词。
- `scripts/`：本地辅助脚本。
