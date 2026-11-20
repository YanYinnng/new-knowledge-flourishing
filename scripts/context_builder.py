from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
INBOX_DIR = ROOT / "inbox"
MEMORY_DIR = ROOT / "memory"
REVIEW_QUEUE_DIR = ROOT / "review_queue"
REVIEW_KINDS = {"memory_candidate", "weight_change_candidate"}


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
        else:
            rows.append({"kind": "invalid_entry", "line_number": line_number})
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def markdown_keyword_records(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    records: list[dict[str, str]] = []
    current = None
    in_free_note = False
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if re.match(r"^#+\s", line):
            current = None
            if line.startswith("### 随心记"):
                in_free_note = True
            elif line.startswith("### 网页输入"):
                in_free_note = False
        if in_free_note:
            continue
        match = re.match(r"^-+\s*(关键词|补充信息|上下文|权重|可选权重)[：:]\s*(.*?)\s*$", line)
        if not match:
            continue
        label, value = match.groups()
        if label == "关键词":
            current = {"keyword": value, "supplemental_info": "", "weight": "3"} if value else None
            if current:
                records.append(current)
        elif current:
            field = "supplemental_info" if label in {"补充信息", "上下文"} else "weight"
            current[field] = value
    return records


def stable_id(*parts: str, prefix: str = "item") -> str:
    raw = "\n".join(parts).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:12]
    return f"{prefix}-{digest}"


def field_value(text: str, field: str) -> str:
    match = re.search(rf"^{re.escape(field)}:[ \t]*(.*)$", text, flags=re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else ""


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
            if kind in REVIEW_KINDS:
                candidate_id = str(row.get("id") or "")
                if candidate_id:
                    candidates[candidate_id] = {**row, "queue_path": relative_path(path)}
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
            "supported_kinds": ["keyword_batch", "free_note"],
        },
        "memory": read_memory_context(),
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


def weight_target(root: Path, path_text: str) -> Path | None:
    if not path_text:
        return None
    try:
        path = (root / path_text).resolve()
        bases = [root / "knowledge", root / "synthesis" / "idea_seeds"]
        if path.suffix.lower() != ".md" or not path.is_file():
            return None
        if not any(path.is_relative_to(base.resolve()) for base in bases):
            return None
        return path if field_value(path.read_text(encoding="utf-8"), "Weight") in {"1", "2", "3", "4", "5"} else None
    except (OSError, ValueError, RuntimeError):
        return None


def relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()
