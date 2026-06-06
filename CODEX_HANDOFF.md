# Codex Handoff

Last updated: 2026-06-05, Asia/Shanghai.

Read this first in a new Codex conversation, then run `git status --short`. The working tree is intentionally dirty; do not reset, checkout, or delete broad changes unless the user explicitly asks.

## Current Project Shape

`点子发芽` is a local-first personal secretary and knowledge incubation system. The active user workflow is:

1. User writes keywords and free notes in the local web UI.
2. `scripts/generate-radar-report.py --collect-only` builds `report_context.json` and `sources.json`.
3. Codex automation reads the context/rules and writes `report_brief.json`.
4. `--render-only` renders `report.tex` / `report.pdf`, runs quality checks, and syncs review candidates into `review_queue/`.
5. `scripts/sync-knowledge-from-report.py` writes passed report content into `knowledge/` and `synthesis/idea_seeds/`.

Hard constraints:

- No formal database, vector store, graph engine, web framework, or OpenAI API.
- State is Markdown, JSONL, JSON, LaTeX/PDF, and scripts.
- Raw input is append-only: `inbox/YYYY-MM-DD.jsonl`.
- `free_note` must not affect keyword summaries, search, old-knowledge links, idea seeds, or knowledge sync. It only feeds the final free-note review and possible review candidates.
- AI candidates are not confirmed facts until accepted in the web UI.
- Do not auto-promote knowledge or seeds by directly editing `Weight:` from automation. Use `weight_change_candidates`.

## Active UI/API

Frontend currently exposes:

- Keyword input.
- Free note input.
- Pending review.
- Reading index for reports, knowledge nodes, and idea seeds.
- Shared reader for PDF/Markdown/TeX.

Current public API routes:

- `POST /api/keywords`
- `POST /api/free-notes`
- `POST /api/review`
- `GET /api/overview`
- `GET /api/file`
- `GET /api/raw`

Task capture, calendar capture, and the current-tasks panel were intentionally removed from the UI. `app.py` still contains some helper functions and `tasks/tasks.jsonl` remains part of the architecture, but task/calendar capture routes are not exposed. Only accepting a `task_candidate` currently writes a task event.

## Important Recent Changes

### Secretary Context Layer

- `scripts/context_builder.py` is the shared context builder.
- `generate-radar-report.py` writes `secretary_context` into `report_context.json`.
- `secretary_context` includes:
  - raw input counts and non-keyword captures
  - `memory/profile.md`
  - `memory/themes.md`
  - recent `memory/preferences.jsonl`
  - open or due-soon tasks rebuilt from `tasks/tasks.jsonl`
  - pending review items from `review_queue/`

### Review Queue

- Candidate fields in `report_brief.json`:
  - `memory_candidates`
  - `task_candidates`
  - `knowledge_candidates`
  - `idea_seed_candidates`
  - `weight_change_candidates`
- `sync_review_queue_from_brief()` runs during report render and appends candidates into `review_queue/YYYY-MM-DD.jsonl`.
- If the brief does not contain candidate fields, pending review will be empty. The system does not fabricate candidates.
- Accepting a candidate appends a `review_decision`.
- Rejecting only records the decision.
- Accepting `memory_candidate` writes `memory/preferences.jsonl`.
- Accepting `task_candidate` writes `tasks/tasks.jsonl`.
- Accepting `weight_change_candidate` writes `tracking/weight_decisions.jsonl`; if the candidate has a safe Markdown path under `knowledge/` or `synthesis/idea_seeds/`, it also updates that file's `Weight:`.

### Report Voice Governance

- New file: `system/report_voice_rules.md`.
- Prompts now require reading both `system/report_quality_rules.md` and `system/report_voice_rules.md`.
- `report_context.writing_contract.voice_contract` summarizes the rules.
- `quality_check_simple()` calls `voice_smell_check()`.
- The check blocks visible report text that contains banned meta phrases such as `本地旧节点显示`, `可连接`, `脚本 fallback`, or excessive AI-style sentence patterns.

### Recent News Density

- `recent_news` is now treated as “最近有什么相关的事情”.
- It must describe 2-4 concrete recent signals: activities, projects, papers, products, discussions, or policy moves. It cannot just list `[S1] [S2] [S3]`.
- `recent_news_quality_issues()` checks brief content against available sources and fails too-short or source-only text.

### Multi-Source Search

- `system/report_config.json` now uses `web_search_provider: multi_source_opencli`.
- Search channels:
  - `opencli:weixin`
  - `opencli:bilibili`
  - `opencli:zhihu`
  - `opencli:weibo`
  - `opencli:baidu-scholar`
  - `opencli:wanfang`
  - `opencli:cnki`
  - `opencli:google-scholar`
  - `web:bing`
  - `web:baidu`
  - `web:duckduckgo_html`
- Search result cards include `search_channel`, `search_endpoint`, and `search_query`.
- `opencli doctor` previously worked in this environment. Individual sites may still fail or time out; direct web fallback should keep report collection usable.

### Docs

- `README.md` was refreshed on 2026-06-05 to reflect the current architecture, active UI, AI-visible context, search behavior, voice rules, and health check.
- `system/architecture.md` was refreshed on 2026-06-05 as a shorter architecture snapshot.

## Data Layout

- `inbox/YYYY-MM-DD.md`: human-readable daily input log.
- `inbox/YYYY-MM-DD.jsonl`: structured raw input events.
- `memory/profile.md`: stable personal profile.
- `memory/preferences.jsonl`: accepted preference/memory events.
- `memory/themes.md`: recurring long-term themes.
- `tasks/tasks.jsonl`: task event stream; currently mostly dormant except accepted `task_candidate`.
- `review_queue/YYYY-MM-DD.jsonl`: AI candidates and human decisions.
- `tracking/topics.md`: tracked long-term topics.
- `tracking/weight_decisions.jsonl`: accepted weight-change events; created on first accepted weight candidate.
- `knowledge/*.md`: current primary knowledge nodes.
- `synthesis/idea_seeds/*.md`: current primary idea seeds.
- `library/nodes/`, `library/seeds/`: legacy read-only compatibility.
- `synthesis/daily_reports/YYYY-MM-DD/`: report outputs.

Report directory files:

- `report_context.json`
- `sources.json`
- `report_brief.json`
- `report.tex`
- `report.pdf`
- `quality_check.json`
- `knowledge_sync.json`

## Core Files

- `app.py`: local web server, auth/session, file whitelist, input append, review decisions, auto Git sync.
- `web/index.html`, `web/static/app.js`, `web/static/styles.css`: frontend.
- `scripts/context_builder.py`: shared secretary context.
- `scripts/generate-radar-report.py`: collect/search/render/quality/review-queue pipeline.
- `scripts/sync-knowledge-from-report.py`: report-to-knowledge/seed sync.
- `automation/nightly-codex-prompt.md`: canonical nightly automation instructions.
- `automation/catch-up-codex-prompt.md`: missed-date automation instructions.
- `system/report_quality_rules.md`: report quality rules.
- `system/report_voice_rules.md`: report voice rules.
- `templates/report-brief.json`: required brief shape.

## Commands

Start local web app:

```powershell
$env:IDEA_SPROUT_AUTO_GIT_SYNC="0"; python app.py
```

Use another port:

```powershell
$env:IDEA_SPROUT_PORT="3001"; $env:IDEA_SPROUT_AUTO_GIT_SYNC="0"; python app.py
```

Nightly report flow:

```powershell
python scripts/generate-radar-report.py --date YYYY-MM-DD --collect-only
# Codex reads report_context.json and sources.json, then writes report_brief.json
python scripts/generate-radar-report.py --date YYYY-MM-DD --render-only
python scripts/sync-knowledge-from-report.py --date YYYY-MM-DD
```

Validation:

```powershell
python -m py_compile app.py scripts/generate-radar-report.py scripts/sync-knowledge-from-report.py scripts/send-daily-report.py scripts/context_builder.py
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate-structure.ps1
node --check web/static/app.js
git diff --check
```

Dry-run knowledge sync:

```powershell
python scripts/sync-knowledge-from-report.py --date 2026-05-26 --dry-run
```

For PowerShell reads involving Chinese text, prefer:

```powershell
chcp 65001 > $null; [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
```

## Latest Health Check

Verified on 2026-06-05:

- `python -m py_compile app.py scripts/generate-radar-report.py scripts/sync-knowledge-from-report.py scripts/send-daily-report.py scripts/context_builder.py` passed.
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate-structure.ps1` passed.
- `node --check web/static/app.js` passed.
- `git diff --check` only printed LF/CRLF warnings; no whitespace errors.
- `python scripts/sync-knowledge-from-report.py --date 2026-05-26 --dry-run` ran successfully. It reported one existing knowledge node would update and two idea seeds were already synced.

## Current Working Tree Notes

The repo has many user/generated modifications. At the time this handoff was updated, `git status --short` showed changes in:

- `README.md`
- `app.py`
- automation prompts
- several `knowledge/*.md` files
- `memory/preferences.jsonl`
- `scripts/generate-radar-report.py`
- `system/report_config.json`
- `system/report_quality_rules.md`
- `templates/report-brief.json`
- `web/static/app.js`
- untracked review queues, daily reports, idea seeds, knowledge files, and `system/report_voice_rules.md`

Always preserve user work. If a changed file is unrelated to the current task, leave it alone.

## Watch Points

- Web input auto-commits and pushes by default. During tests, set `IDEA_SPROUT_AUTO_GIT_SYNC=0`.
- `config/local_auth.json` and `config/email_auth.json` are local secrets and ignored; never print or commit real values.
- Report generation can use network search and LaTeX. Use `--no-web` or `--no-compile` when isolating failures.
- `free_note` isolation is important. Do not let free notes affect keyword cards, old-knowledge links, search, idea seeds, or sync.
- Pending review depends on candidate fields in `report_brief.json`. Empty pending review is expected if no candidates were generated or all candidates were rejected/accepted.
- Weight candidate paths must stay under `knowledge/` or `synthesis/idea_seeds/`; otherwise only record the decision.
- `quality_check.json` now checks style and news density, but it still cannot guarantee good writing by itself. Codex must write a thoughtful `report_brief.json`.

## Suggested First Steps For New Codex

1. Read `CODEX_HANDOFF.md`.
2. Read `README.md` if the task touches architecture or user workflow.
3. Run `git status --short`.
4. If working on reports, read `system/report_quality_rules.md`, `system/report_voice_rules.md`, `templates/report-brief.json`, and the target date's `report_context.json`.
5. If working on UI/API, inspect `app.py`, `web/index.html`, and `web/static/app.js`.
6. If changing sync behavior, run `python scripts/sync-knowledge-from-report.py --date 2026-05-26 --dry-run`.
7. End with `py_compile`, `validate-structure.ps1`, `node --check` when relevant, and `git diff --check`.
