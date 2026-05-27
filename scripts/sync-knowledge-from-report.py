from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORT_ROOT = ROOT / "synthesis" / "daily_reports"
KNOWLEDGE_DIR = ROOT / "knowledge"
IDEA_SEED_DIR = ROOT / "synthesis" / "idea_seeds"

DEFAULT_WEIGHT = "3"
SOURCE_TEMPLATE = "inbox/{date}.md, synthesis/daily_reports/{date}/report.pdf"
PLACEHOLDER_SEED_PATTERNS = [
    "今日暂无值得保留的新点子",
    "暂无值得保留",
    "没有值得保留",
    "无值得保留",
]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {relative_path(path)}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{relative_path(path)} must be a JSON object.")
    return data


def relative_path(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def compact_text(value: Any, limit: int = 220) -> str:
    text = normalize_space(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def normalize_weight(value: Any) -> str:
    cleaned = str(value or "").strip()
    if cleaned in {"1", "2", "3", "4", "5"}:
        return cleaned
    return DEFAULT_WEIGHT


def candidate_weight(value: Any) -> str:
    return str(min(int(normalize_weight(value)), 3))


def slugify(value: str, prefix: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char if ord(char) < 128 else " " for char in normalized)
    tokens = re.findall(r"[A-Za-z0-9]+", ascii_text)
    ascii_slug = "-".join(tokens).lower().strip("-")
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", value))
    ascii_count = len(re.findall(r"[A-Za-z0-9]", value))
    if ascii_slug and ascii_count >= cjk_count:
        return ascii_slug[:70].strip("-")
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{digest}"


def first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def field_value(text: str, field: str) -> str:
    match = re.search(rf"^{re.escape(field)}:\s*(.*)$", text, flags=re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def replace_field(text: str, field: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(field)}:\s*.*$", flags=re.MULTILINE | re.IGNORECASE)
    replacement = f"{field}: {value}"
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    lines = text.splitlines()
    insert_at = 1 if lines and lines[0].startswith("# ") else 0
    lines.insert(insert_at, replacement)
    return "\n".join(lines).rstrip() + "\n"


def merge_sources(text: str, sources: str) -> str:
    existing = field_value(text, "Sources")
    merged: list[str] = []
    for item in [*existing.split(","), *sources.split(",")]:
        cleaned = item.strip()
        if cleaned and cleaned not in merged:
            merged.append(cleaned)
    return replace_field(text, "Sources", ", ".join(merged))


def section_bounds(text: str, heading: str) -> tuple[int, int, int] | None:
    match = re.search(rf"(?m)^##\s+{re.escape(heading)}\s*$", text)
    if not match:
        return None
    body_start = match.end()
    next_match = re.search(r"(?m)^##\s+", text[body_start:])
    body_end = body_start + next_match.start() if next_match else len(text)
    return match.start(), body_start, body_end


def append_to_section_once(text: str, heading: str, entry: str, unique_key: str) -> tuple[str, bool]:
    bounds = section_bounds(text, heading)
    if not bounds:
        block = f"\n## {heading}\n\n{entry.rstrip()}\n"
        return text.rstrip() + "\n" + block, True
    _, body_start, body_end = bounds
    body = text[body_start:body_end]
    if unique_key in body:
        return text, False
    insertion = "\n" + entry.rstrip() + "\n"
    before = text[:body_end].rstrip()
    after = text[body_end:].lstrip("\n")
    if after:
        return before + insertion + "\n" + after, True
    return before + insertion, True


def markdown_files(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return sorted(path for path in base.rglob("*.md") if path.is_file())


def aliases_contain(text: str, keyword: str) -> bool:
    aliases = field_value(text, "Aliases")
    if not aliases:
        return False
    parts = [item.strip() for item in re.split(r"[,，、]", aliases) if item.strip()]
    return keyword in parts


def find_existing_knowledge(keyword: str, slug: str) -> Path | None:
    for path in markdown_files(KNOWLEDGE_DIR):
        text = path.read_text(encoding="utf-8")
        if field_value(text, "ID").lower() == slug.lower():
            return path
    for path in markdown_files(KNOWLEDGE_DIR):
        text = path.read_text(encoding="utf-8")
        if first_heading(text) == keyword:
            return path
    for path in markdown_files(KNOWLEDGE_DIR):
        text = path.read_text(encoding="utf-8")
        if aliases_contain(text, keyword):
            return path
    return None


def input_lookup(brief: dict[str, Any], context: dict[str, Any]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for source in [brief.get("inputs", []), context.get("inputs", []), context.get("keyword_contexts", [])]:
        if not isinstance(source, list):
            continue
        for item in source:
            if not isinstance(item, dict):
                continue
            keyword = normalize_space(item.get("keyword"))
            if not keyword:
                continue
            lookup[keyword] = {
                "supplemental_info": normalize_space(
                    item.get("supplemental_info", item.get("context", "未填写"))
                )
                or "未填写",
                "weight": normalize_weight(item.get("weight")),
            }
    return lookup


def source_line(date_text: str) -> str:
    return SOURCE_TEMPLATE.format(date=date_text)


def new_knowledge_node(
    *,
    date_text: str,
    slug: str,
    keyword: str,
    weight: str,
    intro: str,
    recent_news: str,
    relevance: str,
    next_step: str,
) -> str:
    one_liner = compact_text(intro, 140) or f"{keyword} 是从 {date_text} 日报沉淀出的候选知识节点。"
    return (
        f"# {keyword}\n\n"
        f"ID: {slug}\n"
        "Type: concept\n"
        f"Weight: {candidate_weight(weight)}\n"
        "Status: candidate\n"
        f"Last seen: {date_text}\n"
        "Aliases:\n"
        "Related:\n"
        f"Sources: {source_line(date_text)}\n\n"
        "## 一句话定义\n\n"
        f"{one_liner}\n\n"
        "## 为什么对我重要\n\n"
        f"{relevance or '待人工确认这个节点和个人目标的关系。'}\n\n"
        "## 当前理解\n\n"
        f"{intro or '待补充。'}\n\n"
        "## 近期动态\n\n"
        f"- {date_text}：{recent_news or '日报没有提供可靠近期动态。'}\n\n"
        "## 下一步\n\n"
        f"- {date_text}：{next_step or '下次遇到相关输入时再补充判断。'}\n\n"
        "## 更新记录\n\n"
        f"- {date_text}：由日报自动沉淀为 candidate 节点，未自动升权。\n"
    )


def build_knowledge_update(original: str, date_text: str, card: dict[str, Any]) -> tuple[str, str]:
    updated = replace_field(original, "Last seen", date_text)
    updated = merge_sources(updated, source_line(date_text))
    changed_sections = []

    recent = compact_text(card.get("recent_news"), 260)
    next_step = compact_text(card.get("next_step"), 180)
    intro = compact_text(card.get("intro"), 200)
    keyword = normalize_space(card.get("keyword"))

    updated, changed = append_to_section_once(
        updated,
        "近期动态",
        f"- {date_text}：{recent or '日报没有提供可靠近期动态。'}",
        date_text,
    )
    if changed:
        changed_sections.append("近期动态")

    updated, changed = append_to_section_once(
        updated,
        "下一步",
        f"- {date_text}：{next_step or '下次遇到相关输入时再补充判断。'}",
        date_text,
    )
    if changed:
        changed_sections.append("下一步")

    update_note = f"- {date_text}：根据日报更新「{keyword}」；摘要：{intro or '见当日报告。'}"
    updated, changed = append_to_section_once(updated, "更新记录", update_note, date_text)
    if changed:
        changed_sections.append("更新记录")

    return updated.rstrip() + "\n", ", ".join(changed_sections) or "metadata"


def update_knowledge_node(path: Path, date_text: str, card: dict[str, Any]) -> tuple[bool, str]:
    original = path.read_text(encoding="utf-8")
    updated, reason = build_knowledge_update(original, date_text, card)
    if updated.rstrip() == original.rstrip():
        return False, "already_synced"
    path.write_text(updated, encoding="utf-8")
    return True, reason


def sync_knowledge_cards(
    brief: dict[str, Any],
    context: dict[str, Any],
    date_text: str,
    dry_run: bool,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    created: list[dict[str, str]] = []
    updated: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    inputs = input_lookup(brief, context)
    cards = brief.get("knowledge_cards", [])
    if not isinstance(cards, list):
        return created, updated, [{"kind": "knowledge", "reason": "knowledge_cards_not_list"}]

    for card in cards:
        if not isinstance(card, dict):
            skipped.append({"kind": "knowledge", "reason": "invalid_card"})
            continue
        keyword = normalize_space(card.get("keyword"))
        if not keyword:
            skipped.append({"kind": "knowledge", "reason": "missing_keyword"})
            continue
        slug = slugify(keyword, "node")
        input_item = inputs.get(keyword, {})
        path = find_existing_knowledge(keyword, slug)
        record_base = {"kind": "knowledge", "keyword": keyword, "slug": slug}
        if path:
            rel = relative_path(path)
            if dry_run:
                original = path.read_text(encoding="utf-8")
                preview, _ = build_knowledge_update(original, date_text, card)
                target = updated if preview.rstrip() != original.rstrip() else skipped
                target.append(
                    {
                        **record_base,
                        "path": rel,
                        "action": "would_update" if target is updated else "already_synced",
                    }
                )
                continue
            changed, reason = update_knowledge_node(path, date_text, card)
            target = updated if changed else skipped
            target.append({**record_base, "path": rel, "action": "updated" if changed else reason})
            continue

        target_path = KNOWLEDGE_DIR / f"{slug}.md"
        node_text = new_knowledge_node(
            date_text=date_text,
            slug=slug,
            keyword=keyword,
            weight=input_item.get("weight", DEFAULT_WEIGHT),
            intro=normalize_space(card.get("intro")),
            recent_news=normalize_space(card.get("recent_news")),
            relevance=normalize_space(card.get("relevance")),
            next_step=normalize_space(card.get("next_step")),
        )
        rel = relative_path(target_path)
        if dry_run:
            created.append({**record_base, "path": rel, "action": "would_create"})
            continue
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        target_path.write_text(node_text, encoding="utf-8")
        created.append({**record_base, "path": rel, "action": "created"})
    return created, updated, skipped


def seed_parts(seed: Any) -> tuple[str, str]:
    if isinstance(seed, dict):
        title = normalize_space(seed.get("title") or seed.get("name") or seed.get("idea"))
        description = normalize_space(seed.get("description") or seed.get("summary") or seed.get("detail"))
        return title or compact_text(description, 32), description or title
    text = normalize_space(seed)
    for prefix in ["点子种子：", "点子种子:", "今日发芽点子：", "今日发芽点子:"]:
        if text.startswith(prefix):
            text = normalize_space(text.removeprefix(prefix))
            break
    for separator in ["：", ":"]:
        if separator in text:
            title, description = text.split(separator, 1)
            return normalize_space(title), normalize_space(description)
    title = normalize_space(re.split(r"[，,。；;]", text, maxsplit=1)[0])
    return compact_text(title or text, 48), text


def should_skip_seed(seed: Any) -> bool:
    text = normalize_space(seed)
    return not text or any(pattern in text for pattern in PLACEHOLDER_SEED_PATTERNS)


def find_existing_seed(title: str, slug: str) -> Path | None:
    for path in markdown_files(IDEA_SEED_DIR):
        text = path.read_text(encoding="utf-8")
        if field_value(text, "ID").lower() == slug.lower():
            return path
    for path in markdown_files(IDEA_SEED_DIR):
        text = path.read_text(encoding="utf-8")
        if first_heading(text) in {title, f"点子种子：{title}"}:
            return path
    return None


def new_seed_text(date_text: str, slug: str, title: str, description: str, related_nodes: list[str]) -> str:
    related = ", ".join(related_nodes)
    return (
        f"# 点子种子：{title}\n\n"
        f"ID: {slug}\n"
        "Status: raw\n"
        f"Created: {date_text}\n"
        f"Related nodes: {related}\n"
        f"Sources: {source_line(date_text)}\n\n"
        "## 触发\n\n"
        f"{date_text} 日报提出这个候选点子。\n\n"
        "## 点子描述\n\n"
        f"{description or title}\n\n"
        "## 为什么有意思\n\n"
        "它把当天的新知从“看过”推进到一个可继续观察或验证的小方向。\n\n"
        "## 可能的最小下一步\n\n"
        "下次遇到相关输入时，补充一个可验证案例或反例。\n\n"
        "## 风险或疑问\n\n"
        "当前仍是 raw 候选，尚未经过人工确认、合并或升权。\n\n"
        "## 更新记录\n\n"
        f"- {date_text}：由日报自动沉淀为 raw 点子种子。\n"
    )


def build_seed_update(original: str, date_text: str, title: str, description: str) -> tuple[str, str]:
    updated = merge_sources(original, source_line(date_text))
    entry = f"- {date_text}：日报再次触发「{title}」；补充：{compact_text(description, 220)}"
    updated, changed = append_to_section_once(updated, "更新记录", entry, date_text)
    return updated.rstrip() + "\n", "更新记录" if changed else "metadata"


def update_seed(path: Path, date_text: str, title: str, description: str) -> tuple[bool, str]:
    original = path.read_text(encoding="utf-8")
    updated, reason = build_seed_update(original, date_text, title, description)
    if updated.rstrip() == original.rstrip():
        return False, "already_synced"
    path.write_text(updated, encoding="utf-8")
    return True, reason


def sync_idea_seeds(
    brief: dict[str, Any],
    date_text: str,
    dry_run: bool,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    created: list[dict[str, str]] = []
    updated: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    seeds = brief.get("idea_seeds", [])
    if not isinstance(seeds, list):
        return created, updated, [{"kind": "seed", "reason": "idea_seeds_not_list"}]

    related_nodes = [
        slugify(normalize_space(card.get("keyword")), "node")
        for card in brief.get("knowledge_cards", [])
        if isinstance(card, dict) and normalize_space(card.get("keyword"))
    ]
    for seed in seeds:
        if should_skip_seed(seed):
            skipped.append({"kind": "seed", "reason": "placeholder", "title": normalize_space(seed)})
            continue
        title, description = seed_parts(seed)
        if not title:
            skipped.append({"kind": "seed", "reason": "missing_title"})
            continue
        slug = slugify(title, "seed")
        path = find_existing_seed(title, slug)
        record_base = {"kind": "seed", "title": title, "slug": slug}
        if path:
            rel = relative_path(path)
            if dry_run:
                original = path.read_text(encoding="utf-8")
                preview, _ = build_seed_update(original, date_text, title, description)
                target = updated if preview.rstrip() != original.rstrip() else skipped
                target.append(
                    {
                        **record_base,
                        "path": rel,
                        "action": "would_update" if target is updated else "already_synced",
                    }
                )
                continue
            changed, reason = update_seed(path, date_text, title, description)
            target = updated if changed else skipped
            target.append({**record_base, "path": rel, "action": "updated" if changed else reason})
            continue

        target_path = IDEA_SEED_DIR / f"{slug}.md"
        rel = relative_path(target_path)
        if dry_run:
            created.append({**record_base, "path": rel, "action": "would_create"})
            continue
        IDEA_SEED_DIR.mkdir(parents=True, exist_ok=True)
        target_path.write_text(new_seed_text(date_text, slug, title, description, related_nodes), encoding="utf-8")
        created.append({**record_base, "path": rel, "action": "created"})
    return created, updated, skipped


def sync(date_text: str, dry_run: bool) -> dict[str, Any]:
    report_dir = REPORT_ROOT / date_text
    brief = load_json(report_dir / "report_brief.json")
    context = load_json(report_dir / "report_context.json")

    created: list[dict[str, str]] = []
    updated: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    for bucket, values in zip(
        (created, updated, skipped),
        sync_knowledge_cards(brief, context, date_text, dry_run),
    ):
        bucket.extend(values)
    for bucket, values in zip(
        (created, updated, skipped),
        sync_idea_seeds(brief, date_text, dry_run),
    ):
        bucket.extend(values)

    result = {
        "date": date_text,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "library_policy": "read-only",
        "context_scanned_paths": context.get("local_knowledge", {}).get("scanned_paths", []),
        "created": created,
        "updated": updated,
        "skipped": skipped,
    }
    if not dry_run:
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "knowledge_sync.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync report knowledge cards into local Markdown knowledge base.")
    parser.add_argument("--date", required=True, help="Report date in YYYY-MM-DD format.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing files.")
    args = parser.parse_args()

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        print("--date must use YYYY-MM-DD.", file=sys.stderr)
        return 2
    try:
        result = sync(args.date, args.dry_run)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
