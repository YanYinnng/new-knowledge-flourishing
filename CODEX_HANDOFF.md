# Codex Handoff

Last updated: 2026-06-01, Asia/Shanghai.

## 2026-06-01 Secretary Layer Update

The project now has the first "personal secretary" skeleton while preserving the existing daily radar report flow.

- Added `scripts/context_builder.py` as the shared context-building layer for daily reports, future secretary replies, weekly reviews, task suggestions, and review queues.
- Added long-term memory files:
  - `memory/profile.md`
  - `memory/preferences.jsonl`
  - `memory/themes.md`
- Added task event storage:
  - `tasks/tasks.jsonl`
- Added confirmation queue storage:
  - `review_queue/YYYY-MM-DD.jsonl`
- Web UI now supports manual task capture, calendar capture, pending review decisions, and task status actions.
- New raw inbox kinds:
  - `task_capture`
  - `calendar_capture`
- AI-generated candidates should use:
  - `memory_candidate`
  - `task_candidate`
  - `knowledge_candidate`
  - `idea_seed_candidate`
- `generate-radar-report.py` now includes `secretary_context` in `report_context.json`, renders optional secretary sections when present in `report_brief.json`, and syncs candidate fields into `review_queue/` instead of writing confirmed memory/tasks directly.
- `system/architecture.md` documents the current data flow.

Validation after this update:

```powershell
python -m py_compile app.py scripts\generate-radar-report.py scripts\sync-knowledge-from-report.py scripts\send-daily-report.py scripts\context_builder.py
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\validate-structure.ps1
git diff --check
python scripts\generate-radar-report.py --date 2026-05-26 --collect-only --no-web
python scripts\sync-knowledge-from-report.py --date 2026-05-26 --dry-run
```

All passed on 2026-06-01. `git diff --check` only printed CRLF warnings.

This file is the fastest entry point for a new Codex conversation. Read this first, then run `git status --short` before editing.

## Project In One Breath

`点子发芽` is a personal local-first knowledge incubation MVP. The user writes daily keywords and optional free-form "随心记" notes in a local web page; nightly automation turns the keyword inputs into a PDF cognitive radar report, appending a separate free-note review when present; passed reports are then synced back into candidate knowledge nodes and raw idea seeds.

Hard constraints:

- No database, vector store, knowledge graph engine, web framework, or OpenAI API.
- Store state in Markdown, JSON, LaTeX/PDF, and a few PowerShell/Python scripts.
- Daily raw input now has a structured JSONL layer: `inbox/YYYY-MM-DD.jsonl`.
- `free_note` / "随心记" entries must not affect keyword summaries, search, old-knowledge links, idea seeds, or knowledge sync; they only feed the final "随心记复盘" section.
- Future writes go to `knowledge/` and `synthesis/idea_seeds/`.
- `library/nodes/` and `library/seeds/` are read-only legacy compatibility.
- Do not auto-promote knowledge to weight 4/5; user confirmation is required.

## Current Working Tree

The repo is intentionally dirty. Do not reset or revert broad changes.

Notable current changes:

- Web UI now has a top input console with keyword input and "随心记", plus a bottom shared-reader workspace: the left side is "阅读索引" with fixed groups for reports, knowledge nodes, and idea seeds; the right side is one shared reader. Each left-side group scrolls its own item list independently.
- `scripts/sync-knowledge-from-report.py` was added to sync passed reports into `knowledge/` and `synthesis/idea_seeds/`.
- `automation/nightly-codex-prompt.md` and `automation/catch-up-codex-prompt.md` now require `knowledge_sync.json`.
- `templates/source.md` and `templates/daily-report.md` were removed as obsolete.
- `templates/README.md` and `tracking/README.md` explain the remaining useful directories.
- Old flat Markdown report entries `synthesis/daily_reports/2026-05-24.md` and `synthesis/daily_reports/2026-05-25.md` are deleted from the working tree; structured report folders remain.
- Modified tracked files currently include `CODEX_HANDOFF.md`, `README.md`, `app.py`, report scripts, automation prompt/rules/templates, and web files.
- `PLAN.md` and `cleanup_report.md` are deleted in the working tree.
- New/untracked generated content currently includes `knowledge/node-7c0378ad.md`, `synthesis/daily_reports/2026-05-27/`, `synthesis/idea_seeds/seed-e2879fd0.md`, and `synthesis/idea_seeds/seed-ed5e2f08.md`.

Always preserve user work. If a changed file looks unrelated to your task, leave it alone.

## Core Files

- `app.py`: zero-dependency local web server, login/session handling, inbox append, overview API, safe file reads.
- `web/index.html`, `web/static/app.js`, `web/static/styles.css`: frontend for the input console and shared-reader reading workspace.
- `scripts/generate-radar-report.py`: report collect/render pipeline, local knowledge scan, web search, LaTeX/PDF render, quality checks.
- `scripts/sync-knowledge-from-report.py`: reads `report_brief.json` and `report_context.json`, writes candidate nodes/seeds, records `knowledge_sync.json`.
- `automation/nightly-codex-prompt.md`: canonical nightly Codex automation instructions.
- `automation/catch-up-codex-prompt.md`: catch-up version for missed dates.
- `system/report_quality_rules.md`: report writing and quality rules.
- `templates/report-brief.json`: required shape for Codex-authored report brief.
- `tracking/topics.md`: long-term topics scanned by report generation.

## Data Layout

- `inbox/YYYY-MM-DD.md`: human-readable daily web inputs.
- `inbox/YYYY-MM-DD.jsonl`: structured raw input entries (`keyword_batch`, `free_note`) used by new report generation when present; old Markdown-only dates remain compatible.
- `synthesis/daily_reports/YYYY-MM-DD/`: structured daily report output:
  - `report_context.json`
  - `report_brief.json`
  - `quality_check.json`
  - `knowledge_sync.json`
  - `report.tex`
  - `report.pdf`
  - `sources.json`
- `knowledge/*.md`: current primary knowledge nodes.
- `synthesis/idea_seeds/*.md`: current primary idea seeds.
- `library/nodes/`, `library/seeds/`: legacy read-only compatibility.
- `reports/daily/`: legacy report directory; not the current main output.

## Current Known Content

- 2026-05-25 report: topic `量子计算与AI的结合`; quality passed; synced into `knowledge/node-0011b3bd.md` and two idea seeds.
- 2026-05-26 report: topic `多智能体协作`; quality passed; synced into `knowledge/node-78eeff5b.md`.
- 2026-05-27 report: topic `中美创客大赛`; `quality_check.json` passed with no issues; `knowledge_sync.json` created:
  - `knowledge/node-7c0378ad.md`
  - `synthesis/idea_seeds/seed-ed5e2f08.md`
  - `synthesis/idea_seeds/seed-e2879fd0.md`
- A sync parsing bug was fixed on 2026-05-27: strings starting with `点子种子：` are now parsed by stripping that prefix. The bad generated `点子种子：点子种子` seed was removed and replaced by:
  - `synthesis/idea_seeds/seed-72a2ef24.md`
  - `synthesis/idea_seeds/seed-b123728a.md`

## Main Workflows

Start local web app:

```powershell
$env:IDEA_SPROUT_AUTO_GIT_SYNC="0"; python app.py
```

Use another port if 3000 is occupied:

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

Validation commands:

```powershell
python -m py_compile app.py scripts/generate-radar-report.py scripts/sync-knowledge-from-report.py scripts/send-daily-report.py
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate-structure.ps1
git diff --check
```

For PowerShell reads involving Chinese text, prefer:

```powershell
chcp 65001 > $null; [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
```

## Latest Health Check

Verified on 2026-05-27:

- Python compile passed for `app.py`, `generate-radar-report.py`, `sync-knowledge-from-report.py`, `send-daily-report.py`.
- `scripts/validate-structure.ps1` passed.
- `git diff --check` passed; only CRLF warnings were printed.
- `python scripts/sync-knowledge-from-report.py --date 2026-05-26 --dry-run` is now idempotent: one knowledge node and two idea seeds are `already_synced`.
- Temporary API smoke test on `http://127.0.0.1:3002` passed:
  - login succeeded
  - `/api/overview` returned reports, knowledge nodes, and seeds; exact counts may change as generated content is added
  - path traversal request `../README.md` returned 404

## Watch Points

- The worktree has many uncommitted generated files and documentation edits. Ask before committing or deleting anything not directly related.
- Web input auto-commits and pushes by default. During tests, set `IDEA_SPROUT_AUTO_GIT_SYNC=0`.
- `config/local_auth.json` and `config/email_auth.json` are local secrets and ignored; never print or commit their real contents.
- Report generation may use web search and LaTeX. If network or TeX is missing, collect/render may behave differently.
- `quality_check.json` validates structure but does not guarantee writing quality; Codex still needs to read `report_context.json` and write a thoughtful `report_brief.json`.
- `cleanup_report.md` is currently deleted in the working tree; do not rely on it unless restored. `git status --short` is the source of truth for current file state.
- `README.md` still contains a stale sentence saying the three lists each have their own reader; update it in a separate docs cleanup pass or when touching README next.

## Suggested First Steps For New Codex

1. Read this file.
2. Run `git status --short`.
3. If working on reports, read `system/report_quality_rules.md` and the target date's `report_context.json`.
4. If working on UI/API, inspect `app.py`, `web/index.html`, and `web/static/app.js`.
5. If changing sync behavior, run `sync-knowledge-from-report.py --date 2026-05-26 --dry-run` afterward to verify idempotence.
6. End with `py_compile`, `validate-structure.ps1`, and `git diff --check` when feasible.
