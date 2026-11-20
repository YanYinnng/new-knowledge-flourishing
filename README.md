# 点子发芽 · 公开测试版

点子发芽把白天记下的关键词、补充信息和随心记整理成晚间 PDF。你可以用本地网页录入、阅读日报和知识节点，也可以接受、编辑或拒绝有明确作用的记忆与权重建议。

**先看看 [示例日报 PDF](examples/daily-report.pdf)。** 这份示例使用[虚构数据](examples/daily-report.json)，由项目自带的渲染器生成。

## 开始使用

使用 Python 3.10 或更新版本。输出 PDF 需要 TeX Live 或 MiKTeX，确保 `latexmk` 或 `xelatex` 在 PATH 中。Windows 定时任务使用 PowerShell。

在项目根目录运行：

```powershell
python app.py
```

打开 [本机网页](http://127.0.0.1:3000)，首次启动用 `change-me` 登录。修改 `config/local_auth.json` 中的密码。服务默认监听本机，可用 `IDEA_SPROUT_PORT` 修改端口。

## 生成和阅读日报

1. 每行输入一个关键词。补充信息可写触发它的具体场景和你想搞清的问题，默认权重为 3。
2. 单独记录随心记，用于报告末尾的复盘和记忆建议。
3. 配置 DeepSeek 后，运行下面的命令。把示例日期换成目标日期。

```powershell
Copy-Item config/deepseek_auth.example.json config/deepseek_auth.json
# Set api_key in config/deepseek_auth.json first.
python scripts/run-nightly-deepseek-report.py --date 2026-11-20
```

也可以通过 `DEEPSEEK_API_KEY` 提供密钥。生成脚本先收集上下文和来源，再调用 DeepSeek 写稿、生成 TeX、检查正文并编译 PDF。能定位到字段的质量问题只修对应段落。PDF 就绪后，候选进入待确认队列，知识卡与点子进入本地候选库。

当天的产物在 `synthesis/daily_reports/YYYY-MM-DD/`。从网页打开成品，或者在目录里核对 `report_context.json`、`sources.json`、`report_brief.json` 和 `quality_check.json`。

接受记忆会把它写入 `memory/preferences.jsonl`，之后的日报可以读到这条记录。接受权重调整会修改候选指向的现有 Markdown 节点。每天最多发布一条记忆建议和一条有有效目标的权重建议。接受和拒绝都会留下记录。

## 可选定时与邮件

```powershell
.\scripts\install-nightly-deepseek-report.ps1
Copy-Item config/email_auth.example.json config/email_auth.json
# Set your SMTP account, authorization code and recipient first.
.\scripts\install-nightly-mailer.ps1
```

生成任务默认在本地 22:50 运行。邮件任务在 23:00 等待当天已通过质量检查的 PDF，默认最多等 30 分钟。发送前可用 `python scripts/send-daily-report.py --date 2026-11-20 --dry-run` 预览。对应的 `uninstall-*.ps1` 可以移除定时任务。

## 文件在哪里

| 路径 | 用途 |
| --- | --- |
| `app.py`、`web/` | 网页服务和界面 |
| `scripts/` | 上下文构建、生成、校验、知识同步和邮件 |
| `config/` | 登录、DeepSeek 和 SMTP 的配置示例 |
| `system/`、`templates/` | 报告规则、搜索配置和数据模板 |
| `inbox/`、`memory/`、`review_queue/` | 运行时生成的输入、记忆和人工决定 |
| `knowledge/`、`synthesis/` | 本地候选知识、点子和日报 |
| `tests/` | 离线回归测试 |

原始输入按天追加到 JSONL，同时维护可阅读的 Markdown。实际配置和运行数据受 Git 忽略规则保护。联网搜索会将检索词发给搜索服务，DeepSeek 写作请求包含当日上下文和来源，因此可能包含随心记、记忆和补充信息。

## 验证和排障

```powershell
python -X utf8 -m unittest discover -s tests -v
python -m compileall -q app.py scripts tests
node --check web/static/app.js
```

回归测试使用本地样例和模拟模型响应。Windows 上的离线烟测覆盖录入、真实 PDF 编译、候选入队、知识同步和邮件就绪判定。实时搜索、DeepSeek API 和实际邮件投递取决于本地配置和服务可用性。定时任务需要在目标 Windows 设备上验证。

手动排障时，可先用 `python scripts/generate-radar-report.py --date YYYY-MM-DD --collect-only --no-web` 生成离线上下文。有合法 brief 后，用同一脚本的 `--render-only` 重新编译。失败详情见当日的 `quality_check.json`、`deepseek_run.json` 和 `system/deepseek-nightly.log`。

## 许可

本公开版本的代码和文档以 [MIT License](LICENSE) 发布。

版本变更见 [CHANGELOG.md](CHANGELOG.md)。
