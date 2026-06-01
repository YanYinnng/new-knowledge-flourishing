from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
INBOX_DIR = ROOT / "inbox"
MEMORY_DIR = ROOT / "memory"
TASKS_DIR = ROOT / "tasks"
REVIEW_QUEUE_DIR = ROOT / "review_queue"
TASK_EVENTS = TASKS_DIR / "tasks.jsonl"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            rows.append({"kind": "invalid_json", "line_number": line_number})
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def stable_id(*parts: str, prefix: str = "item") -> str:
    raw = "\n".join(parts).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:12]
    return f"{prefix}-{digest}"


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_daily_raw_entries(date_text: str) -> list[dict[str, Any]]:
    return read_jsonl(INBOX_DIR / f"{date_text}.jsonl")


def entry_counts_by_kind(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        kind = str(entry.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def read_memory_context() -> dict[str, Any]:
    profile_path = MEMORY_DIR / "profile.md"
    themes_path = MEMORY_DIR / "themes.md"
    preferences_path = MEMORY_DIR / "preferences.jsonl"
    preferences = read_jsonl(preferences_path)
    return {
        "profile_path": relative_path(profile_path),
        "profile": profile_path.read_text(encoding="utf-8") if profile_path.exists() else "",
        "themes_path": relative_path(themes_path),
        "themes": themes_path.read_text(encoding="utf-8") if themes_path.exists() else "",
        "recent_preferences": preferences[-20:],
    }


def read_review_queue(date_text: str | None = None) -> list[dict[str, Any]]:
    paths = [REVIEW_QUEUE_DIR / f"{date_text}.jsonl"] if date_text else sorted(REVIEW_QUEUE_DIR.glob("*.jsonl"))
    candidates: dict[str, dict[str, Any]] = {}
    decisions: dict[str, dict[str, Any]] = {}
    for path in paths:
        for row in read_jsonl(path):
            kind = str(row.get("kind") or "")
            if kind.endswith("_candidate"):
                item = {**row, "queue_path": relative_path(path)}
                candidates[str(row.get("id") or "")] = item
            elif kind == "review_decision":
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                candidate_id = str(payload.get("candidate_id") or "")
                if candidate_id:
                    decisions[candidate_id] = {**row, "queue_path": relative_path(path)}
    queue: list[dict[str, Any]] = []
    for candidate_id, item in candidates.items():
        decision = decisions.get(candidate_id)
        if decision:
            item = {**item, "decision": decision}
        queue.append(item)
    queue.sort(key=lambda item: str(item.get("created_at") or ""))
    return queue


def pending_review_items(date_text: str | None = None) -> list[dict[str, Any]]:
    return [item for item in read_review_queue(date_text) if "decision" not in item]


def read_task_events() -> list[dict[str, Any]]:
    return read_jsonl(TASK_EVENTS)


def rebuild_tasks(events: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}
    for event in events if events is not None else read_task_events():
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        task_id = str(payload.get("task_id") or event.get("task_id") or "")
        if not task_id:
            continue
        current = tasks.get(task_id, {"task_id": task_id, "status": "open"})
        event_kind = str(event.get("kind") or "")
        if event_kind == "task_created":
            current.update(payload)
            current["status"] = str(payload.get("status") or "open")
        elif event_kind == "task_updated":
            current.update({key: value for key, value in payload.items() if key != "task_id"})
        elif event_kind == "task_completed":
            current["status"] = "completed"
            current["completed_at"] = event.get("created_at")
        elif event_kind == "task_cancelled":
            current["status"] = "cancelled"
            current["cancelled_at"] = event.get("created_at")
        elif event_kind == "task_deferred":
            current["status"] = "open"
            if payload.get("due_date"):
                current["due_date"] = payload["due_date"]
        current["last_event_at"] = event.get("created_at", current.get("last_event_at", ""))
        tasks[task_id] = current
    return tasks


def upcoming_tasks(date_text: str, horizon_days: int = 1) -> list[dict[str, Any]]:
    try:
        base = date.fromisoformat(date_text)
    except ValueError:
        base = date.today()
    horizon = base + timedelta(days=horizon_days)
    selected: list[dict[str, Any]] = []
    for task in rebuild_tasks().values():
        if task.get("status") not in {"open", "active"}:
            continue
        due_text = str(task.get("due_date") or "")
        if not due_text:
            selected.append(task)
            continue
        try:
            due = date.fromisoformat(due_text)
        except ValueError:
            selected.append(task)
            continue
        if due <= horizon:
            selected.append(task)
    selected.sort(key=lambda item: (str(item.get("due_date") or "9999-99-99"), str(item.get("title") or "")))
    return selected[:8]


def candidate_payload_text(candidate: dict[str, Any]) -> str:
    payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    for key in ("text", "title", "summary"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return str(candidate.get("id") or "")


def build_secretary_context(date_text: str) -> dict[str, Any]:
    raw_entries = read_daily_raw_entries(date_text)
    pending = pending_review_items()
    return {
        "date": date_text,
        "raw_input": {
            "entry_counts_by_kind": entry_counts_by_kind(raw_entries),
            "supported_kinds": [
                "keyword_batch",
                "free_note",
                "task_capture",
                "calendar_capture",
                "link_capture",
                "file_capture",
            ],
        },
        "memory": read_memory_context(),
        "tasks": {
            "events_path": relative_path(TASK_EVENTS),
            "open_or_due_soon": upcoming_tasks(date_text, horizon_days=1),
        },
        "review_queue": {
            "pending_count": len(pending),
            "pending_items": [
                {
                    "id": item.get("id", ""),
                    "kind": item.get("kind", ""),
                    "created_at": item.get("created_at", ""),
                    "text": candidate_payload_text(item),
                    "queue_path": item.get("queue_path", ""),
                }
                for item in pending[:20]
            ],
        },
    }


def relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()
