#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.client import IncompleteRead
from pathlib import Path
from typing import Any

from report_artifacts import parse_report_date, report_pdf_ready
from report_quality import QUALITY_FAILURE_EXIT, style_phrase_count, valid_cards


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "deepseek_auth.json"
REPORT_ROOT = ROOT / "synthesis" / "daily_reports"
SYSTEM_DIR = ROOT / "system"
LOCK_DIR = SYSTEM_DIR / "locks"
LOG_PATH = SYSTEM_DIR / "deepseek-nightly.log"

QUALITY_RULES = ROOT / "system" / "report_quality_rules.md"
VOICE_RULES = ROOT / "system" / "report_voice_rules.md"
BRIEF_TEMPLATE = ROOT / "templates" / "report-brief.json"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_MAX_TOKENS = 48000
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_COMMAND_TIMEOUT_SECONDS = 1800
DEFAULT_MAX_REPAIR_ATTEMPTS = 2


class RunnerError(RuntimeError):
    pass


class ConfigError(RunnerError):
    pass


class CommandError(RunnerError):
    def __init__(self, command: list[str], returncode: int) -> None:
        super().__init__(f"Command failed with exit code {returncode}: {' '.join(command)}")
        self.returncode = returncode


class DeepSeekAPIError(RunnerError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class DeepSeekConfig:
    api_key: str
    api_key_source: str
    base_url: str
    model: str
    thinking: str
    reasoning_effort: str
    max_tokens: int
    timeout_seconds: int
    command_timeout_seconds: int
    max_repair_attempts: int
    user_id: str


def now_stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def log(message: str) -> None:
    SYSTEM_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    LOG_PATH.write_text("", encoding="utf-8") if not LOG_PATH.exists() else None
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print(line, flush=True)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_config(require_key: bool = True) -> DeepSeekConfig:
    raw: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        raw = read_json(CONFIG_PATH)
    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    file_key = str(raw.get("api_key") or "").strip()
    if file_key and file_key not in {"REPLACE_ME", "YOUR_DEEPSEEK_API_KEY"}:
        api_key = file_key
        source = str(CONFIG_PATH)
    elif env_key:
        api_key = env_key
        source = "DEEPSEEK_API_KEY"
    elif require_key:
        raise ConfigError(
            "Missing DeepSeek API key. Copy config/deepseek_auth.example.json "
            "to config/deepseek_auth.json and fill api_key, or set DEEPSEEK_API_KEY."
        )
    else:
        api_key = ""
        source = "missing"

    return DeepSeekConfig(
        api_key=api_key,
        api_key_source=source,
        base_url=str(raw.get("base_url") or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        model=str(raw.get("model") or DEFAULT_MODEL),
        thinking=str(raw.get("thinking") or "enabled"),
        reasoning_effort=str(raw.get("reasoning_effort") or "high"),
        max_tokens=int(raw.get("max_tokens") or DEFAULT_MAX_TOKENS),
        timeout_seconds=int(raw.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS),
        command_timeout_seconds=int(raw.get("command_timeout_seconds") or DEFAULT_COMMAND_TIMEOUT_SECONDS),
        max_repair_attempts=max(0, int(raw.get("max_repair_attempts", DEFAULT_MAX_REPAIR_ATTEMPTS))),
        user_id=str(raw.get("user_id") or "idea-sprout-nightly"),
    )


def acquire_lock(date_text: str) -> Path:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOCK_DIR / f"deepseek-nightly-{date_text}.lock"
    if lock_path.exists():
        age = datetime.now() - datetime.fromtimestamp(lock_path.stat().st_mtime)
        if age < timedelta(hours=12):
            raise RunnerError(f"Another DeepSeek report run appears active: {lock_path}")
        log(f"Removing stale lock: {lock_path}")
        lock_path.unlink()
    try:
        with lock_path.open("x", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()}\nstarted_at={now_stamp()}\n")
    except FileExistsError as exc:
        raise RunnerError(f"Another DeepSeek report run appears active: {lock_path}") from exc
    return lock_path


def release_lock(lock_path: Path | None) -> None:
    if lock_path and lock_path.exists():
        lock_path.unlink()


def in_nightly_window() -> bool:
    return "22:45" <= datetime.now().strftime("%H:%M") <= "23:25"


def run_command(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    log("Running: " + " ".join(command))
    process = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    if process.stdout:
        for line in process.stdout.splitlines():
            log("stdout: " + line)
    if process.stderr:
        for line in process.stderr.splitlines():
            log("stderr: " + line)
    if process.returncode != 0:
        raise CommandError(command, process.returncode)
    return process


def report_complete(date_text: str) -> bool:
    report_dir = REPORT_ROOT / date_text
    required = [
        "report_context.json",
        "sources.json",
        "report_brief.json",
        "report.tex",
        "report.pdf",
        "quality_check.json",
        "knowledge_sync.json",
    ]
    if not all((report_dir / name).exists() for name in required):
        return False
    try:
        return report_pdf_ready(report_dir) and (
            read_json(report_dir / "knowledge_sync.json").get("report_fingerprint")
            == read_json(report_dir / "quality_check.json").get("report_fingerprint")
        )
    except (OSError, ValueError, AttributeError):
        return False


def clean_json_content(content: str) -> str:
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text:
        raise RunnerError("DeepSeek returned empty content.")
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        return text[first : last + 1]
    return text


def parse_brief(content: str) -> dict[str, Any]:
    try:
        brief = json.loads(clean_json_content(content))
    except json.JSONDecodeError as exc:
        raise RunnerError(f"DeepSeek output was not valid JSON: {exc}") from exc
    if not isinstance(brief, dict):
        raise RunnerError("DeepSeek output JSON must be an object.")
    if not valid_cards(brief.get("knowledge_cards")):
        raise RunnerError("DeepSeek output must include a knowledge_cards array of keyword cards.")
    return brief


def parse_json_object(content: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(clean_json_content(content))
    except json.JSONDecodeError as exc:
        raise RunnerError(f"DeepSeek {label} was not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RunnerError(f"DeepSeek {label} JSON must be an object.")
    return value


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def build_initial_messages(date_text: str, report_context: dict[str, Any], sources_json: dict[str, Any]) -> list[dict[str, str]]:
    system_prompt = (
        "你是点子发芽的私人秘书日报写作者。你必须只输出一个合法 JSON object，"
        "不要输出 Markdown、解释、代码块或额外文本。JSON 字段必须符合用户提供的模板。"
    )
    user_prompt = f"""
请为 {date_text} 生成 report_brief.json。

硬性要求：
- 输出必须是 JSON object，并且只输出 JSON。
- 严格遵守 report brief 模板中的字段。
- 写作时保留分析能力，但不要在正文展示推理过程。
- free_notes 只用于 free_note_review 和 memory_candidates。关键词主体依据其独立输入。
- 每天最多提出一条有长期价值的 memory_candidates；无合适观察则省略。
- 只有提供现有 Markdown 节点路径和充分理由时，才提出一条 weight_change_candidates。
- reference_sources 只能来自 sources.json 中的来源。

【report quality rules】
{read_text(QUALITY_RULES)}

【report voice rules】
{read_text(VOICE_RULES)}

【JSON template】
{read_text(BRIEF_TEMPLATE)}

【report_context.json】
{compact_json(report_context)}

【sources.json】
{compact_json(sources_json)}
""".strip()
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


CARD_REPAIR_FIELDS = {
    "intro": "简介",
    "recent_news": "最近有什么相关的事情",
    "relevance": "与我相关",
    "next_step": "最小下一步",
}

LIST_TEXT_FIELDS = {"old_knowledge_links", "idea_seeds"}
CANDIDATE_TEXT_FIELDS = {
    "memory_candidates": ["text"],
    "weight_change_candidates": ["target", "reason", "text"],
}


def iter_brief_text_fields(brief: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    fields.append({"path": "summary", "label": "今日总结", "value": brief.get("summary", "")})

    for index, card in enumerate(brief.get("knowledge_cards", [])):
        if not isinstance(card, dict):
            continue
        keyword = str(card.get("keyword") or f"关键词 {index + 1}")
        for field, label in CARD_REPAIR_FIELDS.items():
            fields.append(
                {
                    "path": f"knowledge_cards[{index}].{field}",
                    "keyword": keyword,
                    "label": label,
                    "value": card.get(field, ""),
                }
            )

    for field in LIST_TEXT_FIELDS:
        value = brief.get(field)
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, str):
                    fields.append({"path": f"{field}[{index}]", "label": field, "value": item})

    review = brief.get("free_note_review")
    if isinstance(review, dict):
        themes = review.get("themes")
        if isinstance(themes, list):
            for index, item in enumerate(themes):
                if isinstance(item, str):
                    fields.append({"path": f"free_note_review.themes[{index}]", "label": "随心记主题", "value": item})
        for field in ["discussion", "evaluation", "question_for_tomorrow"]:
            fields.append(
                {
                    "path": f"free_note_review.{field}",
                    "label": f"free_note_review.{field}",
                    "value": review.get(field, ""),
                }
            )

    for list_field, subfields in CANDIDATE_TEXT_FIELDS.items():
        value = brief.get(list_field)
        if not isinstance(value, list):
            continue
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                continue
            for subfield in subfields:
                if isinstance(item.get(subfield), str):
                    fields.append(
                        {
                            "path": f"{list_field}[{index}].{subfield}",
                            "label": f"{list_field}.{subfield}",
                            "value": item.get(subfield) or "",
                        }
                    )
    return fields


def field_by_path(fields: list[dict[str, Any]], path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    for field in fields:
        if field["path"] == path:
            return field
    return None


def add_repair_target(
    targets: list[dict[str, Any]],
    fields: list[dict[str, Any]],
    path: str | None,
    issue: str,
) -> None:
    field = field_by_path(fields, path)
    if not field:
        return
    for target in targets:
        if target["path"] == field["path"]:
            if issue not in target["issues"]:
                target["issues"].append(issue)
            return
    targets.append(
        {
            "path": field["path"],
            "label": field.get("label", field["path"]),
            "keyword": field.get("keyword", ""),
            "issues": [issue],
            "current_value": field.get("value", ""),
        }
    )


def add_targets_containing_text(
    targets: list[dict[str, Any]],
    fields: list[dict[str, Any]],
    needle: str,
    issue: str,
) -> None:
    needle = str(needle or "").strip()
    if not needle:
        return
    for field in fields:
        if needle in str(field.get("value") or ""):
            add_repair_target(targets, fields, str(field["path"]), issue)


def fields_matching_style_label(fields: list[dict[str, Any]], label: str) -> list[str]:
    return [field["path"] for field in fields if style_phrase_count(label, str(field["value"]))]


def repair_targets_from_issues(
    previous_brief: dict[str, Any], issues: list[str], field_issues: dict[str, list[str]] | None = None
) -> list[dict[str, Any]]:
    fields = iter_brief_text_fields(previous_brief)
    targets: list[dict[str, Any]] = []
    mapped_issues = set()
    for path, messages in (field_issues or {}).items():
        for message in messages:
            add_repair_target(targets, fields, path, message)
            mapped_issues.add(message)

    for issue in issues:
        if issue in mapped_issues:
            continue
        issue_text = str(issue)
        for prefix in ["出现旧结构或空泛表达：", "PDF 正文出现语言元话语：", "report_brief 公开字段出现语言元话语："]:
            if prefix in issue_text:
                add_targets_containing_text(targets, fields, issue_text.split(prefix, 1)[1].strip(), issue_text)

        for prefix in ["标题被直接放入正文：", "摘要片段被直接放入正文："]:
            if prefix in issue_text:
                add_targets_containing_text(targets, fields, issue_text.split(prefix, 1)[1].strip(), issue_text)

        style_match = re.search(r"PDF 正文中“(.+?)”出现", issue_text)
        if style_match:
            for path in fields_matching_style_label(fields, style_match.group(1)):
                add_repair_target(targets, fields, path, issue_text)

    return targets


def parse_path(path: str) -> list[str | int]:
    parts: list[str | int] = []
    for chunk in str(path or "").split("."):
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?", chunk)
        if not match:
            raise RunnerError(f"Invalid repair patch path: {path}")
        parts.append(match.group(1))
        if match.group(2) is not None:
            parts.append(int(match.group(2)))
    return parts


def set_path_value(value: Any, path: str, replacement: str) -> None:
    parts = parse_path(path)
    current = value
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = replacement


def apply_repair_patches(previous_brief: dict[str, Any], targets: list[dict[str, Any]], patches: list[dict[str, Any]]) -> dict[str, Any]:
    allowed_paths = {str(target["path"]) for target in targets}
    if not patches:
        raise RunnerError("DeepSeek repair patch did not include any patches.")
    updated = json.loads(json.dumps(previous_brief, ensure_ascii=False))
    seen: set[str] = set()
    for patch in patches:
        if not isinstance(patch, dict):
            raise RunnerError("DeepSeek repair patch item must be an object.")
        path = str(patch.get("path") or "")
        if path not in allowed_paths:
            raise RunnerError(f"DeepSeek repair tried to modify a non-target field: {path}")
        if path in seen:
            raise RunnerError(f"DeepSeek repair included a duplicate field: {path}")
        seen.add(path)
        replacement = patch.get("value")
        if not isinstance(replacement, str) or not replacement.strip():
            raise RunnerError(f"DeepSeek repair value for {path} must be a non-empty string.")
        set_path_value(updated, path, replacement)
    return updated


def build_targeted_repair_messages(
    date_text: str,
    report_context: dict[str, Any],
    sources_json: dict[str, Any],
    previous_brief: dict[str, Any],
    issues: list[str],
    targets: list[dict[str, Any]],
) -> list[dict[str, str]]:
    system_prompt = (
        "你是点子发芽的日报局部修稿器。你必须只输出合法 JSON object，"
        "不要输出 Markdown、解释、代码块或额外文本。"
    )
    user_prompt = f"""
{date_text} 的 report_brief.json 未通过质量检查。请只重写下方列出的失败字段，输出字段级补丁。

质量问题：
{compact_json(issues)}

允许修改的字段：
{compact_json(targets)}

输出格式：
{{
  "patches": [
    {{"path": "knowledge_cards[0].recent_news", "value": "替换后的完整字段文本"}}
  ]
}}

硬性要求：
- 只能输出 patches，不能输出完整 report_brief.json。
- path 必须逐字来自“允许修改的字段”，不能新增、删除或改动其他字段。
- 每个 value 必须是该字段替换后的完整文本，不要只给差异。
- 不要把 free_notes 混入关键词主体。
- recent_news 要写具体最近事情，不能只列来源编号。
- 避免 report_voice_rules.md 禁止的语言。

【report voice rules】
{read_text(VOICE_RULES)}

【JSON template】
{read_text(BRIEF_TEMPLATE)}

【previous report_brief.json】
{compact_json(previous_brief)}

【report_context.json】
{compact_json(report_context)}

【sources.json】
{compact_json(sources_json)}
""".strip()
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def call_deepseek(config: DeepSeekConfig, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
    endpoint = f"{config.base_url}/chat/completions"
    body: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "stream": False,
        "response_format": {"type": "json_object"},
        "max_tokens": config.max_tokens,
        "reasoning_effort": config.reasoning_effort,
        "thinking": {"type": config.thinking},
        "user_id": config.user_id,
    }
    raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=raw_body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        method="POST",
    )
    delays = [0, 5, 15]
    last_error: DeepSeekAPIError | None = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
                text = response.read().decode("utf-8", errors="replace").strip()
            payload = json.loads(text)
            choices = payload.get("choices") if isinstance(payload, dict) else None
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise DeepSeekAPIError("DeepSeek response contains no completion choices.")
            choice = choices[0]
            message = choice.get("message", {})
            if not isinstance(message, dict):
                raise DeepSeekAPIError("DeepSeek response has an invalid completion message.")
            content = str(message.get("content") or "")
            finish_reason = choice.get("finish_reason")
            if finish_reason and finish_reason not in {"stop", None}:
                raise DeepSeekAPIError(f"DeepSeek finish_reason={finish_reason}")
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            return content, usage
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = DeepSeekAPIError(f"DeepSeek HTTP {exc.code}: {detail[:800]}", exc.code)
            if exc.code not in {429, 500, 503}:
                break
            log(str(last_error))
        except (urllib.error.URLError, IncompleteRead, TimeoutError, ConnectionError) as exc:
            last_error = DeepSeekAPIError(f"DeepSeek request failed: {exc}")
            log(str(last_error))
        except json.JSONDecodeError as exc:
            last_error = DeepSeekAPIError(f"DeepSeek response was not JSON: {exc}")
            break
    raise last_error or DeepSeekAPIError("DeepSeek request failed.")


def generate_brief(
    date_text: str,
    config: DeepSeekConfig,
    report_context: dict[str, Any],
    sources_json: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    messages = build_initial_messages(date_text, report_context, sources_json)
    expected_keywords = Counter(row["keyword"] for row in report_context.get("inputs", []))
    parse_errors: list[str] = []
    usage: dict[str, Any] = {}
    for attempt in range(2):
        if parse_errors:
            messages.append(
                {
                    "role": "user",
                    "content": "上一轮输出未满足 JSON 结构或输入关键词要求。请按下面的问题修正并输出合法 JSON。\n"
                    + "\n".join(parse_errors[-2:]),
                }
            )
        content, usage = call_deepseek(config, messages)
        try:
            brief = parse_brief(content)
            if expected_keywords and Counter(card["keyword"] for card in brief["knowledge_cards"]) != expected_keywords:
                raise RunnerError("knowledge_cards must match the input keywords and their counts.")
            return brief, usage
        except RunnerError as exc:
            parse_errors.append(str(exc))
            log(str(exc))
    raise RunnerError("DeepSeek did not return valid report_brief JSON after retry: " + "; ".join(parse_errors))


def parse_repair_patch(content: str) -> list[dict[str, Any]]:
    data = parse_json_object(content, "repair patch")
    patches = data.get("patches")
    if not isinstance(patches, list):
        raise RunnerError("DeepSeek repair patch must contain a patches list.")
    return patches


def repair_brief_fields(
    date_text: str,
    config: DeepSeekConfig,
    report_context: dict[str, Any],
    sources_json: dict[str, Any],
    previous_brief: dict[str, Any],
    issues: list[str],
    field_issues: dict[str, list[str]],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    targets = repair_targets_from_issues(previous_brief, issues, field_issues)
    if not targets:
        raise RunnerError(
            "Quality failed, but the issues could not be mapped to specific report_brief fields. "
            "Refusing to rewrite the full report automatically. Issues: "
            + "; ".join(str(issue) for issue in issues)
        )

    messages = build_targeted_repair_messages(date_text, report_context, sources_json, previous_brief, issues, targets)
    parse_errors: list[str] = []
    usage: dict[str, Any] = {}
    allowed_paths = ", ".join(target["path"] for target in targets)
    for attempt in range(2):
        if parse_errors:
            messages.append(
                {
                    "role": "user",
                    "content": "上一轮补丁不是可用 JSON，或修改了未允许的字段。"
                    f"请只输出 patches，并且 path 只能来自：{allowed_paths}。\n"
                    + "\n".join(parse_errors[-2:]),
                }
            )
        content, usage = call_deepseek(config, messages)
        try:
            patches = parse_repair_patch(content)
            repaired = apply_repair_patches(previous_brief, targets, patches)
            return repaired, usage, targets, patches
        except RunnerError as exc:
            parse_errors.append(str(exc))
            log(str(exc))
    raise RunnerError("DeepSeek did not return a valid targeted repair patch after retry: " + "; ".join(parse_errors))


def read_quality_failure(report_dir: Path) -> tuple[list[str], dict[str, list[str]]]:
    path = report_dir / "quality_check.json"
    if not path.exists():
        return [], {}
    try:
        data = read_json(path)
    except Exception as exc:
        log(f"quality_check.json could not be read: {exc}")
        return [], {}
    if data.get("passed") is not False:
        return [], {}
    return [str(item) for item in data.get("issues", [])], data.get("field_issues", {})


def copy_mock_brief(mock_brief: Path, brief_path: Path) -> dict[str, Any]:
    brief = read_json(mock_brief)
    if not isinstance(brief.get("knowledge_cards"), list):
        raise RunnerError("Mock brief is missing knowledge_cards list.")
    if mock_brief.resolve() != brief_path.resolve():
        shutil.copyfile(mock_brief, brief_path)
    return brief


def write_run_meta(report_dir: Path, meta: dict[str, Any]) -> None:
    write_json(report_dir / "deepseek_run.json", meta)


def run(args: argparse.Namespace) -> int:
    date_text = args.date or datetime.now().strftime("%Y-%m-%d")
    report_dir = REPORT_ROOT / date_text
    meta: dict[str, Any] = {
        "date": date_text,
        "started_at": now_stamp(),
        "route": "deepseek_api",
        "status": "running",
        "attempts": [],
    }

    if args.dry_run:
        config = load_config(require_key=False)
        log(f"Dry run for {date_text}. DeepSeek key source: {config.api_key_source}.")
        log("Would run collect-only, write report_brief.json via DeepSeek or mock, render-only, then sync knowledge.")
        return 0

    if args.enforce_window and not in_nightly_window():
        log("Skipped: current local time is outside 22:45-23:25 nightly window.")
        return 0

    if report_complete(date_text) and not args.force:
        log(f"Skipped: report already complete for {date_text}. Use --force to regenerate.")
        return 0

    try:
        lock_path = acquire_lock(date_text)
    except (RunnerError, OSError) as exc:
        log(str(exc))
        return 1
    try:
        if report_complete(date_text) and not args.force:
            log(f"Skipped: report already complete for {date_text}.")
            return 0
        config = load_config(require_key=not args.mock_brief)
        meta.update(
            {
                "base_url": config.base_url,
                "model": config.model,
                "thinking": config.thinking,
                "reasoning_effort": config.reasoning_effort,
                "api_key_source": config.api_key_source,
            }
        )
        run_command(
            [sys.executable, "scripts/generate-radar-report.py", "--date", date_text, "--collect-only"],
            config.command_timeout_seconds,
        )
        report_context = read_json(report_dir / "report_context.json")
        sources_json = read_json(report_dir / "sources.json")
        brief_path = report_dir / "report_brief.json"

        if args.mock_brief:
            brief = copy_mock_brief(Path(args.mock_brief), brief_path)
            meta["attempts"].append({"kind": "mock_brief", "path": str(Path(args.mock_brief)), "at": now_stamp()})
        else:
            brief, usage = generate_brief(date_text, config, report_context, sources_json)
            write_json(brief_path, brief)
            meta["attempts"].append({"kind": "initial", "at": now_stamp(), "usage": usage})

        render_ok = False
        render_error = ""
        for repair_index in range(config.max_repair_attempts + 1):
            try:
                run_command(
                    [sys.executable, "scripts/generate-radar-report.py", "--date", date_text, "--render-only"],
                    config.command_timeout_seconds,
                )
                render_ok = True
                break
            except CommandError as exc:
                render_error = str(exc)
                issues, field_issues = read_quality_failure(report_dir) if exc.returncode == QUALITY_FAILURE_EXIT else ([], {})
                meta["attempts"].append(
                    {
                        "kind": "render_failed",
                        "at": now_stamp(),
                        "repair_index": repair_index,
                        "issues": issues,
                    }
                )
                if not issues or args.mock_brief or repair_index >= config.max_repair_attempts:
                    break
                log("Quality failed; asking DeepSeek for targeted field patches.")
                brief, usage, targets, patches = repair_brief_fields(
                    date_text,
                    config,
                    report_context,
                    sources_json,
                    brief,
                    issues,
                    field_issues,
                )
                write_json(brief_path, brief)
                meta["attempts"].append(
                    {
                        "kind": "targeted_repair",
                        "at": now_stamp(),
                        "usage": usage,
                        "target_paths": [target["path"] for target in targets],
                        "patched_paths": [str(patch.get("path") or "") for patch in patches if isinstance(patch, dict)],
                    }
                )

        if not render_ok:
            raise RunnerError(render_error or "render-only failed.")

        run_command(
            [sys.executable, "scripts/sync-knowledge-from-report.py", "--date", date_text],
            config.command_timeout_seconds,
        )
        meta["status"] = "passed"
        meta["finished_at"] = now_stamp()
        write_run_meta(report_dir, meta)
        log(f"DeepSeek nightly report finished for {date_text}.")
        return 0
    except Exception as exc:
        meta["status"] = "failed"
        meta["error"] = str(exc)
        meta["finished_at"] = now_stamp()
        write_run_meta(report_dir, meta)
        log(f"DeepSeek nightly report failed: {exc}")
        return 1
    finally:
        release_lock(lock_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Idea Sprout nightly report via DeepSeek API.")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--dry-run", action="store_true", help="Print the planned route without writing report files.")
    parser.add_argument("--mock-brief", default="", help="Use an existing report_brief.json instead of calling DeepSeek.")
    parser.add_argument("--force", action="store_true", help="Regenerate even if the report already appears complete.")
    parser.add_argument("--enforce-window", action="store_true", help="Skip unless local time is between 22:45 and 23:25.")
    args = parser.parse_args()
    try:
        parse_report_date(args.date)
    except ValueError as exc:
        parser.error(str(exc))
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
