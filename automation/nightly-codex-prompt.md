# 点子发芽 nightly Codex automation 提示词

你正在维护一个个人自用的新知孵化 MVP。请只使用本地 Markdown/JSON/LaTeX 文件、PowerShell/Python 脚本和 Codex 自带能力，不使用 OpenAI API，不引入数据库、向量库、知识图谱引擎或复杂依赖。

## 目标

每天 22:50 左右生成一份“个人认知雷达报告”，主输出为 LaTeX PDF：

```text
synthesis/daily_reports/YYYY-MM-DD/
  report.tex
  report.pdf
  assets/
  sources.json
```

邮件发送由 Windows 本地任务在 23:00 处理。不要在 Codex automation 里发送邮件、等待 23:00、调用 `Start-Process`、调用 `schtasks` 或启动后台 PowerShell。

## 配置

读取 `system/report_config.json`：

- `enable_web_search`：是否尽量联网搜索。
- `enable_images`：是否允许加入有助理解的图片。
- `enable_ai_generated_images`：是否允许 AI 生成示意图；如果使用，必须标注“AI 生成示意图”。
- `default_report_mode`：默认 `auto`。
- `max_idea_seeds_per_report`：日报最多提炼几个点子。
- `latex_engine`：默认 `xelatex`。

## 今日日期

使用当前本地日期，格式为 `YYYY-MM-DD`。

## 推荐执行流程

1. 运行 `python scripts/generate-radar-report.py --date YYYY-MM-DD` 生成初稿、联网来源、图片和 PDF。
2. 检查 `synthesis/daily_reports/YYYY-MM-DD/report.tex`，必要时手动改写为更有判断的报告。
3. 如果改了 `report.tex`，运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/compile-radar-report.ps1 -Date YYYY-MM-DD` 重新编译 PDF。
4. 确认 `report.pdf` 和 `sources.json` 存在。编译失败时保留 `report.tex`，并在任务结果里明确说明错误。

## 读取顺序

1. `inbox/YYYY-MM-DD.md`
2. `system/report_config.json`
3. `tracking/topics.md`
4. 按需读取 `knowledge/`、`synthesis/idea_seeds/`、`library/nodes/`、`library/sources/`、`library/seeds/`
5. 旧版 Markdown 日报可作为历史参考，但不要把旧格式当成最终输出。

## 联网搜索要求

凡涉及“今天查到了什么”“外部新进展”“近期趋势”“相关论文/产品/技术动态/新闻变化”，必须尽量联网搜索核实，不能只依赖本地知识库、旧记录或模型记忆。

- 如果 `enable_web_search` 为 true，优先使用可用的联网搜索能力或本地生成脚本的轻量搜索结果。
- 搜索来源必须写入 `sources.json`。
- 如果无法联网，在 `report.tex` 中明确写“当前环境无法联网，未能核实外部新进展”，不要编造。

## 图片要求

图片必须服务理解，不要装饰。

- 可使用联网资料中的官方图、论文图、产品图、流程图，或本地脚本生成的认知雷达示意图。
- 图片保存到 `assets/`，并在 PDF 中有图注。
- `sources.json` 必须记录图片来源或本地路径。
- AI 生成图必须标注“AI 生成示意图”；不要把 AI 图说成真实照片、实验结果或产品截图。
- 没有来源、没有帮助、纯装饰图片不要加入。

## 有新输入时

当 `inbox/YYYY-MM-DD.md` 存在，且“网页输入记录”里有有效内容时，只把每条网页记录中的三个字段作为用户输入依据：`关键词`、`上下文`、`权重`。

PDF 报告结构必须包含：

1. 今日主线：1-3 句话总结今天信息的共同方向，要有判断。
2. 今日输入：列出关键词、上下文、权重。
3. 今日新知：每个重点关键词包含“它是什么 / 今天查到了什么 / 和我有什么关系 / 今日判断 / 下一步”。其中“和我有什么关系”必须保留。
4. 与旧知识的连接：连接 `knowledge/`、`idea_seeds/`、watchlist、weights 等内容；说明新节点、旧节点、为什么相关、连接价值；不要强行连接。
5. 今日发芽点子：提炼 1-3 个可能发芽的点子，包括名称、来源组合、为什么值得关注、成熟度 0-100、最小下一步；没有就说明没有。
6. 权重变化：展示升温/降温节点，并解释原因。
7. 外部新进展：只写和当天关键词、高权重节点或 watchlist 强相关的新进展。
8. 明日一问：最后只给一个具体、有启发的问题。
9. 来源说明：列出重要资料来源。

## 无新输入时

如果当天没有新关键词，不要生成空报告。生成“无新输入复盘模式”，包括：

- 今日状态
- 高权重节点复盘
- 沉睡点子回顾
- watchlist 新进展检查；必须尽量联网搜索，无法联网则说明
- 明日一问

## 风格要求

- 不写成搜索摘要、百科词条、新闻汇总或公司日报。
- 优先回答：今天这些信息共同指向什么？它们和用户过去的知识、项目、兴趣、机会有什么关系？哪些值得继续追踪/深入/行动，哪些可以先放下？
- 保持短、有判断、可手动检查。
- 权重 4 或 5 必须等待用户明确确认，不自动升为核心主题。

生成 `report.tex`、`report.pdf`、`sources.json` 后停止。
