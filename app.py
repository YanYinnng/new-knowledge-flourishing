from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import socket
import subprocess
import time
import urllib.parse
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scripts.context_builder import (
    append_jsonl as append_structured_jsonl,
    pending_review_items,
    read_review_queue,
    rebuild_tasks,
    stable_id,
)


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
CONFIG_DIR = ROOT / "config"
AUTH_CONFIG = CONFIG_DIR / "local_auth.json"
AUTH_EXAMPLE = CONFIG_DIR / "local_auth.example.json"
COOKIE_NAME = "idea_sprout_session"
SESSION_SECONDS = 30 * 24 * 60 * 60
MAX_BODY_BYTES = 128 * 1024
GIT_TIMEOUT_SECONDS = 60

PRIMARY_DIRS = {
    "inbox": ROOT / "inbox",
    "knowledge": ROOT / "knowledge",
    "daily_reports": ROOT / "synthesis" / "daily_reports",
    "idea_seeds": ROOT / "synthesis" / "idea_seeds",
    "system": ROOT / "system",
    "memory": ROOT / "memory",
    "tasks": ROOT / "tasks",
    "review_queue": ROOT / "review_queue",
}

MEMORY_PROFILE = PRIMARY_DIRS["memory"] / "profile.md"
MEMORY_THEMES = PRIMARY_DIRS["memory"] / "themes.md"
MEMORY_PREFERENCES = PRIMARY_DIRS["memory"] / "preferences.jsonl"
TASK_EVENTS = PRIMARY_DIRS["tasks"] / "tasks.jsonl"

LEGACY_DIRS = {
    "knowledge": ROOT / "library" / "nodes",
    "daily_reports": ROOT / "reports" / "daily",
    "idea_seeds": ROOT / "library" / "seeds",
}


def ensure_runtime_files() -> None:
    for path in PRIMARY_DIRS.values():
        path.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not MEMORY_PROFILE.exists():
        MEMORY_PROFILE.write_text(
            "# 个人画像\n\n"
            "这里只保存经过确认的长期画像。日报和随心记可以提出候选，但不会自动改写这里。\n",
            encoding="utf-8",
        )
    if not MEMORY_THEMES.exists():
        MEMORY_THEMES.write_text(
            "# 长期主题\n\n"
            "记录反复出现的长期主题、当前关注强度和最近证据。\n",
            encoding="utf-8",
        )
    if not MEMORY_PREFERENCES.exists():
        MEMORY_PREFERENCES.write_text("", encoding="utf-8")
    if not TASK_EVENTS.exists():
        TASK_EVENTS.write_text("", encoding="utf-8")
    if not AUTH_CONFIG.exists():
        config = {
            "password": "change-me",
            "session_secret": secrets.token_urlsafe(32),
            "note": "本地自用配置。请修改 password；也可以改用 password_sha256。",
        }
        AUTH_CONFIG.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def load_auth_config() -> dict:
    ensure_runtime_files()
    config = json.loads(AUTH_CONFIG.read_text(encoding="utf-8"))
    changed = False
    if not config.get("session_secret"):
        config["session_secret"] = secrets.token_urlsafe(32)
        changed = True
    if changed:
        AUTH_CONFIG.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return config


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("ascii"))


def sign_payload(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256)
    return b64url_encode(digest.digest())


def make_session_cookie() -> str:
    config = load_auth_config()
    now = int(time.time())
    payload = b64url_encode(
        json.dumps({"iat": now, "exp": now + SESSION_SECONDS}, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    signature = sign_payload(payload, config["session_secret"])
    token = f"{payload}.{signature}"
    return (
        f"{COOKIE_NAME}={token}; Max-Age={SESSION_SECONDS}; "
        "Path=/; HttpOnly; SameSite=Lax"
    )


def clear_session_cookie() -> str:
    return f"{COOKIE_NAME}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"


def parse_cookie(header: str | None) -> SimpleCookie:
    cookie = SimpleCookie()
    if header:
        cookie.load(header)
    return cookie


def is_valid_session(cookie_header: str | None) -> bool:
    cookie = parse_cookie(cookie_header)
    if COOKIE_NAME not in cookie:
        return False
    token = cookie[COOKIE_NAME].value
    if "." not in token:
        return False
    payload, signature = token.rsplit(".", 1)
    config = load_auth_config()
    expected = sign_payload(payload, config["session_secret"])
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        data = json.loads(b64url_decode(payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return False
    return int(data.get("exp", 0)) > int(time.time())


def password_matches(candidate: str) -> bool:
    config = load_auth_config()
    if "password_sha256" in config and config["password_sha256"]:
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        return hmac.compare_digest(digest, str(config["password_sha256"]))
    return hmac.compare_digest(candidate, str(config.get("password", "")))


def first_heading(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
    except OSError:
        pass
    return path.stem


def relative_path(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def list_markdown_files(paths: list[Path], limit: int | None = None) -> list[dict]:
    items: list[dict] = []
    seen_stems: set[str] = set()
    for base in paths:
        if not base.exists():
            continue
        for path in base.rglob("*.md"):
            if not path.is_file():
                continue
            stem_key = path.stem.lower()
            if stem_key in seen_stems:
                continue
            seen_stems.add(stem_key)
            stat = path.stat()
            items.append(
                {
                    "title": first_heading(path),
                    "path": relative_path(path),
                    "directory": relative_path(base),
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                }
            )
    items.sort(key=lambda item: item["modified"], reverse=True)
    if limit is not None:
        return items[:limit]
    return items


def list_report_files(limit: int | None = None) -> list[dict]:
    items: list[dict] = []
    structured_dates: set[str] = set()
    primary = PRIMARY_DIRS["daily_reports"]
    if primary.exists():
        for path in primary.iterdir():
            if path.is_dir():
                pdf = path / "report.pdf"
                tex = path / "report.tex"
                sources = path / "sources.json"
                if pdf.exists():
                    stat = pdf.stat()
                    items.append(
                        {
                            "title": f"个人认知雷达报告 {path.name}",
                            "path": relative_path(pdf),
                            "directory": relative_path(path),
                            "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                                "%Y-%m-%d %H:%M"
                            ),
                            "format": "pdf",
                            "status": "PDF",
                            "sources": relative_path(sources) if sources.exists() else "",
                            "date_key": path.name,
                        }
                    )
                    structured_dates.add(path.name)
                elif tex.exists():
                    stat = tex.stat()
                    items.append(
                        {
                            "title": f"个人认知雷达报告 {path.name}",
                            "path": relative_path(tex),
                            "directory": relative_path(path),
                            "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                                "%Y-%m-%d %H:%M"
                            ),
                            "format": "tex",
                            "status": "PDF 尚未生成或编译失败",
                            "sources": relative_path(sources) if sources.exists() else "",
                            "date_key": path.name,
                        }
                    )
                    structured_dates.add(path.name)
        for path in primary.glob("*.md"):
            if not path.is_file():
                continue
            if path.stem in structured_dates:
                continue
            stat = path.stat()
            items.append(
                {
                    "title": first_heading(path),
                    "path": relative_path(path),
                    "directory": relative_path(primary),
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                    "format": "markdown",
                    "status": "旧 Markdown",
                    "sources": "",
                    "date_key": path.stem,
                }
            )
    legacy = LEGACY_DIRS["daily_reports"]
    if legacy.exists():
        for path in legacy.rglob("*.md"):
            if not path.is_file():
                continue
            if path.stem in structured_dates:
                continue
            stat = path.stat()
            items.append(
                {
                    "title": first_heading(path),
                    "path": relative_path(path),
                    "directory": relative_path(legacy),
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                    "format": "markdown",
                    "status": "旧 Markdown",
                    "sources": "",
                    "date_key": path.stem,
                }
            )
    items.sort(key=lambda item: (item.get("date_key", ""), item["modified"]), reverse=True)
    for item in items:
        item.pop("date_key", None)
    if limit is not None:
        return items[:limit]
    return items


def allowed_file(path_text: str, kind: str) -> Path | None:
    allowed_dirs = {
        "report": [PRIMARY_DIRS["daily_reports"], LEGACY_DIRS["daily_reports"]],
        "knowledge": [PRIMARY_DIRS["knowledge"], LEGACY_DIRS["knowledge"]],
        "seed": [PRIMARY_DIRS["idea_seeds"], LEGACY_DIRS["idea_seeds"]],
    }
    suffixes = {
        "report": {".md", ".tex", ".pdf", ".json"},
        "knowledge": {".md"},
        "seed": {".md"},
    }
    if kind not in allowed_dirs:
        return None
    candidate = (ROOT / path_text).resolve()
    if candidate.suffix.lower() not in suffixes[kind] or not candidate.is_file():
        return None
    for base in allowed_dirs[kind]:
        try:
            candidate.relative_to(base.resolve())
            return candidate
        except ValueError:
            continue
    return None


def today_inbox_path() -> Path:
    return PRIMARY_DIRS["inbox"] / f"{datetime.now().strftime('%Y-%m-%d')}.md"


def today_inbox_jsonl_path() -> Path:
    return PRIMARY_DIRS["inbox"] / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"


def create_inbox_if_needed(path: Path) -> None:
    if path.exists():
        return
    today = datetime.now().strftime("%Y-%m-%d")
    template = ROOT / "templates" / "daily-input.md"
    if template.exists():
        content = template.read_text(encoding="utf-8").replace("YYYY-MM-DD", today)
    else:
        content = (
            f"# 每日输入 {today}\n\n"
            "> 本文件由网页端自动维护。日常输入只通过网页提交：关键词、补充信息、权重、随心记。\n\n"
            "## 网页输入记录\n"
        )
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def run_git(args: list[str], timeout: int = GIT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def short_command_output(process: subprocess.CompletedProcess, limit: int = 500) -> str:
    output = "\n".join(
        part.strip()
        for part in [process.stdout, process.stderr]
        if part and part.strip()
    )
    if len(output) > limit:
        return output[:limit].rstrip() + "..."
    return output


def auto_git_sync(paths: Path | list[Path]) -> dict:
    disabled = os.environ.get("IDEA_SPROUT_AUTO_GIT_SYNC", "").lower() in {
        "0",
        "false",
        "no",
        "off",
    }
    path_list = [paths] if isinstance(paths, Path) else list(paths)
    relatives = [relative_path(path) for path in path_list]
    if disabled:
        return {
            "enabled": False,
            "committed": False,
            "pushed": False,
            "message": "Auto git sync is disabled by IDEA_SPROUT_AUTO_GIT_SYNC.",
        }

    try:
        inside = run_git(["rev-parse", "--is-inside-work-tree"])
        if inside.returncode != 0:
            return {
                "enabled": False,
                "committed": False,
                "pushed": False,
                "message": "This folder is not a git repository.",
            }

        branch = run_git(["branch", "--show-current"])
        branch_name = branch.stdout.strip() or "main"
        remote = run_git(["remote", "get-url", "origin"])
        if remote.returncode != 0:
            return {
                "enabled": True,
                "committed": False,
                "pushed": False,
                "message": "No git remote named origin is configured.",
            }

        status = run_git(["status", "--porcelain", "--", *relatives])
        if status.returncode != 0:
            return {
                "enabled": True,
                "committed": False,
                "pushed": False,
                "message": short_command_output(status),
            }
        if not status.stdout.strip():
            return {
                "enabled": True,
                "committed": False,
                "pushed": False,
                "message": "No git changes detected for the inbox files.",
            }

        added = run_git(["add", "--", *relatives])
        if added.returncode != 0:
            return {
                "enabled": True,
                "committed": False,
                "pushed": False,
                "message": short_command_output(added),
            }

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        commit = run_git(["commit", "-m", f"Auto sync inbox {stamp}", "--", *relatives])
        if commit.returncode != 0:
            return {
                "enabled": True,
                "committed": False,
                "pushed": False,
                "message": short_command_output(commit),
            }

        push = run_git(["push", "origin", branch_name], timeout=120)
        if push.returncode != 0:
            return {
                "enabled": True,
                "committed": True,
                "pushed": False,
                "message": short_command_output(push),
            }

        commit_hash = run_git(["rev-parse", "--short", "HEAD"]).stdout.strip()
        return {
            "enabled": True,
            "committed": True,
            "pushed": True,
            "commit": commit_hash,
            "branch": branch_name,
            "message": "Committed and pushed to GitHub.",
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "enabled": True,
            "committed": False,
            "pushed": False,
            "message": str(exc),
        }


def normalize_weight(value: str) -> str:
    cleaned = str(value or "").strip()
    return cleaned if cleaned in {"1", "2", "3", "4", "5"} else "3"


def markdown_keyword_records(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("- 关键词：") or line.startswith("- 关键词:"):
            keyword = line.split("：", 1)[-1] if "：" in line else line.split(":", 1)[-1]
            current = {"keyword": keyword.strip(), "supplemental_info": "", "weight": "3"}
            if current["keyword"]:
                records.append(current)
            continue
        if current and (line.startswith("- 补充信息：") or line.startswith("- 补充信息:") or line.startswith("- 上下文：") or line.startswith("- 上下文:")):
            current["supplemental_info"] = line.split("：", 1)[-1] if "：" in line else line.split(":", 1)[-1]
            current["supplemental_info"] = current["supplemental_info"].strip()
            continue
        if current and (line.startswith("- 权重：") or line.startswith("- 权重:") or line.startswith("- 可选权重：") or line.startswith("- 可选权重:")):
            weight = line.split("：", 1)[-1] if "：" in line else line.split(":", 1)[-1]
            current["weight"] = normalize_weight(weight)
    return records


def raw_entry(kind: str, payload: dict, now: datetime | None = None, source: str = "web") -> dict:
    created_at = (now or datetime.now().astimezone()).astimezone()
    return {
        "schema_version": 1,
        "id": f"{created_at.strftime('%Y-%m-%dT%H-%M-%S')}-{secrets.token_hex(3)}",
        "date": created_at.strftime("%Y-%m-%d"),
        "created_at": created_at.isoformat(timespec="seconds"),
        "source": source,
        "kind": kind,
        "payload": payload,
    }


def append_jsonl_entry(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def bootstrap_jsonl_from_markdown(inbox_path: Path, jsonl_path: Path, now: datetime) -> None:
    if jsonl_path.exists():
        return
    for record in markdown_keyword_records(inbox_path):
        append_jsonl_entry(
            jsonl_path,
            raw_entry(
                "keyword_batch",
                {
                    "keywords": [record["keyword"]],
                    "supplemental_info": record.get("supplemental_info", ""),
                    "weight": normalize_weight(record.get("weight", "")),
                },
                now=now,
                source="web_legacy_md",
            ),
        )


def append_keywords(payload: dict) -> dict:
    raw_keywords = str(payload.get("keywords", ""))
    keywords = []
    for line in raw_keywords.splitlines():
        cleaned = line.strip().lstrip("-").strip()
        if cleaned:
            keywords.append(cleaned)
    supplemental_info = str(payload.get("supplemental_info", payload.get("context", ""))).strip()
    raw_weight = str(payload.get("weight", "")).strip()
    if raw_weight and raw_weight not in {"1", "2", "3", "4", "5"}:
        raise ValueError("权重只能是 1-5。")
    weight = normalize_weight(raw_weight)
    if not keywords:
        raise ValueError("请至少输入一个关键词。")

    inbox_path = today_inbox_path()
    jsonl_path = today_inbox_jsonl_path()
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    create_inbox_if_needed(inbox_path)

    now = datetime.now().astimezone()
    bootstrap_jsonl_from_markdown(inbox_path, jsonl_path, now)
    append_jsonl_entry(
        jsonl_path,
        raw_entry(
            "keyword_batch",
            {
                "keywords": keywords,
                "supplemental_info": supplemental_info,
                "weight": weight,
            },
            now=now,
        ),
    )

    stamp = now.strftime("%Y-%m-%d %H:%M")
    lines = ["", f"### 网页输入 {stamp}", ""]
    for keyword in keywords:
        lines.append(f"- 关键词：{keyword}")
        lines.append(f"  - 补充信息：{supplemental_info or '未填写'}")
        lines.append(f"  - 权重：{weight}")
    lines.append("")

    with inbox_path.open("a", encoding="utf-8") as file:
        file.write("\n".join(lines))

    sync = auto_git_sync([inbox_path, jsonl_path])
    return {
        "path": relative_path(inbox_path),
        "jsonl_path": relative_path(jsonl_path),
        "count": len(keywords),
        "sync": sync,
    }


def append_free_note(payload: dict) -> dict:
    text = str(payload.get("text", "")).strip()
    if not text:
        raise ValueError("请先写一点随心记内容。")
    if len(text) > 8000:
        raise ValueError("单条随心记最多 8000 字。")

    inbox_path = today_inbox_path()
    jsonl_path = today_inbox_jsonl_path()
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    create_inbox_if_needed(inbox_path)

    now = datetime.now().astimezone()
    bootstrap_jsonl_from_markdown(inbox_path, jsonl_path, now)
    append_jsonl_entry(jsonl_path, raw_entry("free_note", {"text": text}, now=now))

    stamp = now.strftime("%Y-%m-%d %H:%M")
    lines = ["", f"### 随心记 {stamp}", "", text, ""]
    with inbox_path.open("a", encoding="utf-8") as file:
        file.write("\n".join(lines))

    sync = auto_git_sync([inbox_path, jsonl_path])
    return {
        "path": relative_path(inbox_path),
        "jsonl_path": relative_path(jsonl_path),
        "count": 1,
        "sync": sync,
    }


def append_task_capture(payload: dict) -> dict:
    title = str(payload.get("title", "")).strip()
    if not title:
        raise ValueError("请先写任务标题。")
    notes = str(payload.get("notes", "")).strip()
    due_date = str(payload.get("due_date", "")).strip()
    theme = str(payload.get("theme", "")).strip()

    inbox_path = today_inbox_path()
    jsonl_path = today_inbox_jsonl_path()
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    create_inbox_if_needed(inbox_path)

    now = datetime.now().astimezone()
    bootstrap_jsonl_from_markdown(inbox_path, jsonl_path, now)
    task_id = stable_id(title, due_date, now.isoformat(timespec="seconds"), prefix="task")
    entry = raw_entry(
        "task_capture",
        {
            "task_id": task_id,
            "title": title,
            "notes": notes,
            "due_date": due_date,
            "theme": theme,
        },
        now=now,
    )
    append_jsonl_entry(jsonl_path, entry)
    append_structured_jsonl(
        TASK_EVENTS,
        {
            "schema_version": 1,
            "id": stable_id(task_id, "created", now.isoformat(timespec="seconds"), prefix="task-event"),
            "created_at": entry["created_at"],
            "source": "web_manual_capture",
            "kind": "task_created",
            "payload": {
                "task_id": task_id,
                "title": title,
                "notes": notes,
                "due_date": due_date,
                "theme": theme,
                "status": "open",
                "source_entry_id": entry["id"],
            },
        },
    )

    stamp = now.strftime("%Y-%m-%d %H:%M")
    lines = ["", f"### 任务捕捉 {stamp}", "", f"- 任务：{title}"]
    if due_date:
        lines.append(f"  - 截止：{due_date}")
    if theme:
        lines.append(f"  - 关联主题：{theme}")
    if notes:
        lines.append(f"  - 备注：{notes}")
    lines.append("")
    with inbox_path.open("a", encoding="utf-8") as file:
        file.write("\n".join(lines))

    sync = auto_git_sync([inbox_path, jsonl_path, TASK_EVENTS])
    return {
        "path": relative_path(inbox_path),
        "jsonl_path": relative_path(jsonl_path),
        "task_id": task_id,
        "sync": sync,
    }


def append_calendar_capture(payload: dict) -> dict:
    title = str(payload.get("title", "")).strip()
    if not title:
        raise ValueError("请先写日程标题。")
    start_at = str(payload.get("start_at", "")).strip()
    end_at = str(payload.get("end_at", "")).strip()
    notes = str(payload.get("notes", "")).strip()

    inbox_path = today_inbox_path()
    jsonl_path = today_inbox_jsonl_path()
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    create_inbox_if_needed(inbox_path)

    now = datetime.now().astimezone()
    bootstrap_jsonl_from_markdown(inbox_path, jsonl_path, now)
    entry = raw_entry(
        "calendar_capture",
        {"title": title, "start_at": start_at, "end_at": end_at, "notes": notes},
        now=now,
    )
    append_jsonl_entry(jsonl_path, entry)

    stamp = now.strftime("%Y-%m-%d %H:%M")
    lines = ["", f"### 日程捕捉 {stamp}", "", f"- 日程：{title}"]
    if start_at:
        lines.append(f"  - 开始：{start_at}")
    if end_at:
        lines.append(f"  - 结束：{end_at}")
    if notes:
        lines.append(f"  - 备注：{notes}")
    lines.append("")
    with inbox_path.open("a", encoding="utf-8") as file:
        file.write("\n".join(lines))

    sync = auto_git_sync([inbox_path, jsonl_path])
    return {
        "path": relative_path(inbox_path),
        "jsonl_path": relative_path(jsonl_path),
        "entry_id": entry["id"],
        "sync": sync,
    }


def current_tasks() -> list[dict]:
    tasks = list(rebuild_tasks().values())
    tasks.sort(key=lambda item: (str(item.get("status") or ""), str(item.get("due_date") or "9999-99-99"), str(item.get("title") or "")))
    return tasks


def update_task_status(payload: dict) -> dict:
    task_id = str(payload.get("task_id", "")).strip()
    action = str(payload.get("action", "")).strip()
    if not task_id:
        raise ValueError("缺少 task_id。")
    if action not in {"complete", "cancel", "defer"}:
        raise ValueError("任务动作只能是 complete、cancel 或 defer。")
    tasks = rebuild_tasks()
    if task_id not in tasks:
        raise ValueError("任务不存在。")
    kind_by_action = {
        "complete": "task_completed",
        "cancel": "task_cancelled",
        "defer": "task_deferred",
    }
    event_payload = {"task_id": task_id}
    if action == "defer":
        event_payload["due_date"] = str(payload.get("due_date", "")).strip()
    now = datetime.now().astimezone()
    append_structured_jsonl(
        TASK_EVENTS,
        {
            "schema_version": 1,
            "id": stable_id(task_id, action, now.isoformat(timespec="seconds"), prefix="task-event"),
            "created_at": now.isoformat(timespec="seconds"),
            "source": "web_manual_update",
            "kind": kind_by_action[action],
            "payload": event_payload,
        },
    )
    sync = auto_git_sync(TASK_EVENTS)
    return {"task_id": task_id, "action": action, "sync": sync}


def review_queue_summary() -> dict:
    pending = pending_review_items()
    return {
        "pending": pending,
        "all": read_review_queue(),
    }


def find_review_candidate(candidate_id: str) -> dict | None:
    for item in pending_review_items():
        if str(item.get("id")) == candidate_id:
            return item
    return None


def append_review_decision(candidate: dict, decision: str, edited_payload: dict | None = None) -> dict:
    if decision not in {"accepted", "rejected"}:
        raise ValueError("确认动作只能是 accepted 或 rejected。")
    queue_path = ROOT / str(candidate.get("queue_path", ""))
    if not queue_path.exists():
        queue_path = PRIMARY_DIRS["review_queue"] / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    row = raw_entry(
        "review_decision",
        {
            "candidate_id": candidate.get("id"),
            "candidate_kind": candidate.get("kind"),
            "decision": decision,
            "edited_payload": edited_payload or {},
        },
        source="web_review",
    )
    append_jsonl_entry(queue_path, row)
    return row


def materialize_review_acceptance(candidate: dict, edited_payload: dict | None = None) -> list[str]:
    payload = edited_payload if edited_payload else candidate.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    kind = str(candidate.get("kind") or "")
    written: list[str] = []
    now = datetime.now().astimezone()
    if kind == "memory_candidate":
        append_structured_jsonl(
            MEMORY_PREFERENCES,
            {
                "schema_version": 1,
                "id": stable_id(str(candidate.get("id")), "memory", now.isoformat(timespec="seconds"), prefix="memory"),
                "created_at": now.isoformat(timespec="seconds"),
                "source": "review_queue",
                "kind": "memory_accepted",
                "payload": {
                    "candidate_id": candidate.get("id"),
                    "text": str(payload.get("text") or payload.get("summary") or "").strip(),
                    "target": str(payload.get("target") or "preferences"),
                    "source_report": payload.get("source_report", ""),
                },
            },
        )
        written.append(relative_path(MEMORY_PREFERENCES))
    elif kind == "task_candidate":
        title = str(payload.get("title") or payload.get("text") or "").strip()
        if title:
            task_id = stable_id(str(candidate.get("id")), title, prefix="task")
            append_structured_jsonl(
                TASK_EVENTS,
                {
                    "schema_version": 1,
                    "id": stable_id(task_id, "review-created", now.isoformat(timespec="seconds"), prefix="task-event"),
                    "created_at": now.isoformat(timespec="seconds"),
                    "source": "review_queue",
                    "kind": "task_created",
                    "payload": {
                        "task_id": task_id,
                        "title": title,
                        "notes": str(payload.get("notes") or ""),
                        "due_date": str(payload.get("due_date") or ""),
                        "theme": str(payload.get("theme") or ""),
                        "status": "open",
                        "source_candidate_id": candidate.get("id"),
                    },
                },
            )
            written.append(relative_path(TASK_EVENTS))
    return written


def primary_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            ip = probe.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        return None
    return None


def lan_ip_candidates() -> list[str]:
    candidates: list[str] = []
    primary = primary_lan_ip()
    if primary:
        candidates.append(primary)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in candidates:
                candidates.append(ip)
    except OSError:
        pass
    return candidates


def print_startup_urls(host: str, port: int) -> None:
    print("点子发芽网页已启动", flush=True)
    print(f"电脑本机访问: http://127.0.0.1:{port}", flush=True)
    if host in {"0.0.0.0", ""}:
        lan_ips = lan_ip_candidates()
        if lan_ips:
            print("手机访问链接（手机和电脑需在同一 Wi-Fi / 局域网）：", flush=True)
            for ip in lan_ips:
                print(f"  http://{ip}:{port}", flush=True)
        else:
            print("未能自动识别局域网 IP。可在 Windows 网络设置中查看本机 IPv4 地址。", flush=True)
        print("如果电脑正在使用 VPN，请确认 VPN 允许局域网 / LAN 访问。", flush=True)
        print("如果当前是校园网 / WPA2-Enterprise Wi-Fi，手机打不开时请优先尝试手机热点或电脑移动热点。", flush=True)
    else:
        print(f"当前仅监听: http://{host}:{port}", flush=True)
        print("如需手机访问，请使用 IDEA_SPROUT_HOST=0.0.0.0 启动。", flush=True)
    print(f"本地密码配置: {AUTH_CONFIG}", flush=True)


class IdeaSproutHandler(BaseHTTPRequestHandler):
    server_version = "IdeaSproutLocal/0.1"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            self.send_static_file(WEB_DIR / "index.html")
            return
        if path.startswith("/static/"):
            static_path = (WEB_DIR / "static" / path.removeprefix("/static/")).resolve()
            if WEB_DIR.resolve() in static_path.parents:
                self.send_static_file(static_path)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
            return
        if path == "/api/auth/status":
            self.send_json({"authenticated": self.authenticated()})
            return
        if path == "/api/overview":
            if not self.require_auth():
                return
            self.send_json(
                {
                    "reports": list_report_files(limit=8),
                    "knowledge": list_markdown_files(
                        [PRIMARY_DIRS["knowledge"], LEGACY_DIRS["knowledge"]]
                    ),
                    "seeds": list_markdown_files(
                        [PRIMARY_DIRS["idea_seeds"], LEGACY_DIRS["idea_seeds"]]
                    ),
                    "review_queue": review_queue_summary()["pending"],
                }
            )
            return
        if path == "/api/file":
            if not self.require_auth():
                return
            query = urllib.parse.parse_qs(parsed.query)
            kind = query.get("kind", [""])[0]
            file_path = query.get("path", [""])[0]
            target = allowed_file(file_path, kind)
            if not target:
                self.send_json({"error": "文件不存在或路径不允许。"}, HTTPStatus.NOT_FOUND)
                return
            if target.suffix.lower() == ".pdf":
                title = (
                    f"个人认知雷达报告 {target.parent.name}"
                    if kind == "report"
                    else target.stem
                )
                self.send_json(
                    {
                        "title": title,
                        "path": relative_path(target),
                        "format": "pdf",
                        "url": f"/api/raw?kind={urllib.parse.quote(kind)}&path={urllib.parse.quote(relative_path(target))}",
                    }
                )
                return
            self.send_json(
                {
                    "title": first_heading(target),
                    "path": relative_path(target),
                    "format": target.suffix.lower().lstrip("."),
                    "content": target.read_text(encoding="utf-8"),
                }
            )
            return
        if path == "/api/raw":
            if not self.require_auth():
                return
            query = urllib.parse.parse_qs(parsed.query)
            kind = query.get("kind", [""])[0]
            file_path = query.get("path", [""])[0]
            target = allowed_file(file_path, kind)
            if not target:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_static_file(target)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/login":
            payload = self.read_json_body()
            if payload is None:
                return
            if password_matches(str(payload.get("password", ""))):
                self.send_json(
                    {"authenticated": True, "expiresInDays": 30},
                    headers={"Set-Cookie": make_session_cookie()},
                )
            else:
                self.send_json({"error": "密码不正确。"}, HTTPStatus.UNAUTHORIZED)
            return
        if parsed.path == "/api/logout":
            self.send_json(
                {"authenticated": False},
                headers={"Set-Cookie": clear_session_cookie()},
            )
            return
        if parsed.path == "/api/keywords":
            if not self.require_auth():
                return
            payload = self.read_json_body()
            if payload is None:
                return
            try:
                result = append_keywords(payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json(result)
            return
        if parsed.path == "/api/free-notes":
            if not self.require_auth():
                return
            payload = self.read_json_body()
            if payload is None:
                return
            try:
                result = append_free_note(payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json(result)
            return
        if parsed.path == "/api/review":
            if not self.require_auth():
                return
            payload = self.read_json_body()
            if payload is None:
                return
            candidate_id = str(payload.get("candidate_id", "")).strip()
            action = str(payload.get("action", "")).strip()
            if action not in {"accept", "reject"}:
                self.send_json({"error": "确认动作只能是 accept 或 reject。"}, HTTPStatus.BAD_REQUEST)
                return
            candidate = find_review_candidate(candidate_id)
            if not candidate:
                self.send_json({"error": "待确认项不存在或已经处理。"}, HTTPStatus.NOT_FOUND)
                return
            edited_payload = payload.get("edited_payload") if isinstance(payload.get("edited_payload"), dict) else {}
            try:
                decision = "accepted" if action == "accept" else "rejected"
                written = materialize_review_acceptance(candidate, edited_payload) if decision == "accepted" else []
                append_review_decision(candidate, decision, edited_payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"candidate_id": candidate_id, "decision": decision, "written": written})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def authenticated(self) -> bool:
        return is_valid_session(self.headers.get("Cookie"))

    def require_auth(self) -> bool:
        if self.authenticated():
            return True
        self.send_json({"error": "需要登录。"}, HTTPStatus.UNAUTHORIZED)
        return False

    def read_json_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            self.send_json({"error": "请求内容过大。"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self.send_json({"error": "JSON 格式不正确。"}, HTTPStatus.BAD_REQUEST)
            return None

    def send_json(
        self,
        data: dict,
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def send_static_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type, _ = mimetypes.guess_type(path.name)
        raw = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: object) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {self.address_string()} {format % args}", flush=True)


def run() -> None:
    ensure_runtime_files()
    host = os.environ.get("IDEA_SPROUT_HOST", "0.0.0.0")
    port = int(os.environ.get("IDEA_SPROUT_PORT", "3000"))
    server = ThreadingHTTPServer((host, port), IdeaSproutHandler)
    print_startup_urls(host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务。")
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
