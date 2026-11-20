from __future__ import annotations

import html
import re
import unicodedata
from collections import Counter
from datetime import datetime
from typing import Any


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def clean_source_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", html.unescape(value or ""))
    without_controls = "".join(char for char in normalized if unicodedata.category(char)[0] != "C")
    return normalize_space(without_controls.replace("�", ""))


def sentence_count(text: str) -> int:
    return len([part for part in re.split(r"[。！？!?；;]\s*", text) if part.strip()])


NEW_BANNED_REPORT_PHRASES = [
    "它是什么",
    "今天查到了什么",
    "和我有什么关系",
    "今日判断",
    "今日主线",
    "权重变化",
    "外部新进展",
    "明日一问",
    "来源说明",
    "暂未发现强相关旧节点",
    "继续关注",
    "深入研究",
    "据资料显示",
]


VOICE_BANNED_META_PHRASES = [
    "本地旧节点显示",
    "可连接",
    "它说明这个关键词",
    "连接价值是",
    "当前是弱连接，理由是",
    "低成本观察",
    "脚本 fallback",
    "由 Codex automation",
]

VOICE_STYLE_LIMITS = {
    "不是/而是": 1,
    "只是": 2,
    "更像": 2,
    "判断": 3,
}

RECENT_NEWS_LABEL = "最近有什么相关的事情"
MIN_RECENT_NEWS_CHARS_WITH_SOURCES = 220
QUALITY_FAILURE_EXIT = 3
CARD_FIELDS = ("intro", "recent_news", "relevance", "next_step")


def rendered_body_text(tex: str) -> str:
    main, _, references_and_followups = tex.partition(r"\section*{参考搜索内容}")
    _, followups, tail = references_and_followups.partition(r"\section*{")
    before_input, _, input_and_cards = main.partition(r"\section*{今日输入}")
    _, cards, remainder = input_and_cards.partition(r"\section*{今日新知}")
    return before_input + cards + remainder + followups + tail


def source_text_reused_in_new_body(tex: str, sources_json: dict[str, Any]) -> list[str]:
    body = rendered_body_text(tex)
    reused: list[str] = []
    for source in sources_json.get("text_sources", []):
        keyword = clean_source_text(str(source.get("keyword", "")))
        title = clean_source_text(str(source.get("title", "")))
        snippet = clean_source_text(str(source.get("snippet", "")))
        if title and len(title) >= 14 and title != keyword and title in body:
            reused.append(f"标题被直接放入正文：{title[:40]}")
        if snippet and len(snippet) >= 40 and snippet[:42] in body:
            reused.append(f"摘要片段被直接放入正文：{snippet[:40]}")
    return reused[:5]


def candidate_public_texts(value: Any) -> list[str]:
    if not value:
        return []
    raw_items = value if isinstance(value, list) else [value]
    texts: list[str] = []
    for item in raw_items:
        if isinstance(item, dict):
            text = item.get("text") or item.get("title") or item.get("summary") or item.get("notes") or item.get("reason")
            if text:
                texts.append(str(text))
        elif str(item).strip():
            texts.append(str(item))
    return texts


def public_brief_text(brief: dict[str, Any]) -> str:
    chunks: list[str] = [str(brief.get("summary") or "")]
    for card in brief.get("knowledge_cards", []):
        if not isinstance(card, dict):
            continue
        for field in ["intro", "recent_news", "relevance", "next_step"]:
            chunks.append(str(card.get(field) or ""))
    for field in ["old_knowledge_links", "idea_seeds"]:
        value = brief.get(field)
        if isinstance(value, list):
            chunks.extend(str(item) for item in value if str(item).strip())
        elif value:
            chunks.append(str(value))
    review = brief.get("free_note_review")
    if isinstance(review, dict):
        themes = review.get("themes")
        if isinstance(themes, list):
            chunks.extend(str(item) for item in themes if str(item).strip())
        for field in ["discussion", "evaluation", "question_for_tomorrow"]:
            chunks.append(str(review.get(field) or ""))
    for field in ["memory_candidates", "weight_change_candidates"]:
        chunks.extend(candidate_public_texts(brief.get(field)))
    return "\n".join(chunk for chunk in chunks if chunk.strip())


def style_phrase_count(label: str, text: str) -> int:
    if label == "不是/而是":
        pattern = "(?<!\u662f)\u4e0d\u662f[^\u3002\uff01\uff1f\uff1b\n]{0,120}?\u800c\u662f"
        return len(re.findall(pattern, text))
    return text.count(label)


def voice_smell_check(tex: str, brief: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    visible_body = rendered_body_text(tex)
    brief_text = public_brief_text(brief)
    for phrase in VOICE_BANNED_META_PHRASES:
        if phrase in visible_body:
            issues.append(f"PDF 正文出现语言元话语：{phrase}")
        elif phrase in brief_text:
            issues.append(f"report_brief 公开字段出现语言元话语：{phrase}")
    for label, limit in VOICE_STYLE_LIMITS.items():
        count = style_phrase_count(label, visible_body)
        if count > limit:
            issues.append(f"PDF 正文中“{label}”出现 {count} 次，超过上限 {limit}。")
    return issues


def normalize_compact_for_quality(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def keyword_has_sources(keyword: str, sources_json: dict[str, Any]) -> bool:
    return any(str(source.get("keyword") or "") == keyword for source in sources_json.get("text_sources", []))


def recent_news_is_source_only(text: str) -> bool:
    compact = normalize_compact_for_quality(text)
    refs = re.findall(r"\[S\d+\]", compact)
    text_without_refs = re.sub(r"\[S\d+\]", "", compact)
    if len(refs) >= 2 and len(text_without_refs) < 140:
        return True
    source_words = ["检索到", "搜到", "搜索到", "找到了", "查到了", "参考来源", "相关来源", "材料显示", "公开资料", "来源编号", "搜索材料"]
    if any(word in compact for word in source_words) and len(text_without_refs) < 160:
        return True
    return False


def has_enough_recent_news_units(text: str) -> bool:
    if sentence_count(text) >= 3:
        return True
    bullet_lines = [
        line
        for line in str(text or "").splitlines()
        if re.match(r"^\s*(?:[-*•]|\d+[.、)]|[一二三四五六七八九十]+[、.])\s*\S+", line)
    ]
    return len(bullet_lines) >= 2


def recent_news_quality_issues(card: dict[str, Any], sources_json: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    keyword = str(card.get("keyword") or "未知关键词")
    recent_news = str(card.get("recent_news") or "")
    compact = normalize_compact_for_quality(recent_news)
    has_sources = keyword_has_sources(keyword, sources_json) or bool(re.search(r"\[S\d+\]", recent_news))
    no_news = "未查到" in compact and "新进展" in compact
    if not compact or not has_sources or no_news:
        return issues
    if len(compact) < MIN_RECENT_NEWS_CHARS_WITH_SOURCES:
        issues.append(
            f"{keyword} 的“{RECENT_NEWS_LABEL}”太短；有搜索来源时应写 2-4 个具体线索，正文至少约 {MIN_RECENT_NEWS_CHARS_WITH_SOURCES} 字。"
        )
    if not has_enough_recent_news_units(recent_news):
        issues.append(f"{keyword} 的“{RECENT_NEWS_LABEL}”信息密度不足；至少写 3 句，或 2 条以上具体线索，说明发生了什么、和关键词有什么关系。")
    if recent_news_is_source_only(recent_news):
        issues.append(f"{keyword} 的“{RECENT_NEWS_LABEL}”像来源清单；请写出材料共同说明的进展、活动或趋势。")
    return issues


def valid_cards(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(card, dict)
        and isinstance(card.get("keyword"), str)
        and card["keyword"].strip()
        for card in value
    )


def quality_check_simple(
    tex: str,
    input_keywords: list[str],
    sources_json: dict[str, Any],
    brief: dict[str, Any],
    context_scanned_paths: list[str] | None = None,
    free_note_count: int = 0,
    has_local_knowledge: bool = False,
) -> dict[str, Any]:
    issues: list[str] = []
    field_issues: dict[str, list[str]] = {}
    brief_text = public_brief_text(brief)

    def flag(path: str, message: str) -> None:
        issues.append(message)
        field_issues.setdefault(path, []).append(message)

    for section in ["今日总结", "今日输入", "今日新知", "与旧知识的链接", "今日发芽点子", "参考搜索内容"]:
        if section not in tex:
            issues.append(f"缺少章节：{section}")
    for phrase in NEW_BANNED_REPORT_PHRASES:
        if phrase in brief_text:
            issues.append(f"出现旧结构或空泛表达：{phrase}")
    issues.extend(source_text_reused_in_new_body(tex, sources_json))
    issues.extend(voice_smell_check(tex, brief))
    cards = brief.get("knowledge_cards", [])
    if input_keywords and Counter(card["keyword"] for card in cards) != Counter(input_keywords):
        issues.append("knowledge_cards 的关键词及次数必须与今日输入一致。")
    for index, card in enumerate(cards):
        path = f"knowledge_cards[{index}]"
        keyword = str(card.get("keyword") or "未知关键词")
        intro = str(card.get("intro") or "")
        recent_news = str(card.get("recent_news") or "")
        relevance = str(card.get("relevance") or "")
        next_step = str(card.get("next_step") or "")
        if not isinstance(card.get("intro"), str) or len(re.sub(r"\s+", "", intro)) < 180:
            flag(f"{path}.intro", f"{keyword} 的简介过短，应接近 200-300 字。")
        if not isinstance(card.get("recent_news"), str) or not recent_news.strip():
            flag(f"{path}.recent_news", f"{keyword} 缺少“{RECENT_NEWS_LABEL}”。")
        else:
            for issue in recent_news_quality_issues(card, sources_json):
                flag(f"{path}.recent_news", issue)
        if not isinstance(card.get("relevance"), str) or not relevance.strip():
            flag(f"{path}.relevance", f"{keyword} 缺少“与我相关”。")
        if not isinstance(card.get("next_step"), str) or not next_step.strip():
            flag(f"{path}.next_step", f"{keyword} 缺少“最小下一步”。")
    expected = len(cards)
    if tex.count(r"\paragraph{简介}") != expected:
        issues.append("每个关键词必须恰好有一个“简介”。")
    if tex.count(rf"\paragraph{{{RECENT_NEWS_LABEL}}}") != expected:
        issues.append(f"每个关键词必须恰好有一个“{RECENT_NEWS_LABEL}”。")
    if tex.count(r"\paragraph{与我相关}") != expected:
        issues.append("每个关键词必须恰好有一个“与我相关”。")
    if tex.count(r"\paragraph{最小下一步}") != expected:
        issues.append("每个关键词必须恰好有一个“最小下一步”。")
    if not isinstance(brief.get("summary"), str) or not brief["summary"].strip():
        flag("summary", "report_brief 缺少今日总结。")
    if free_note_count:
        review = brief.get("free_note_review")
        if "随心记复盘" not in tex:
            issues.append("当天有随心记，但 PDF 缺少“随心记复盘”。")
        if not isinstance(review, dict):
            issues.append("当天有随心记，但 report_brief 缺少 free_note_review。")
        else:
            for field in ["discussion", "evaluation", "question_for_tomorrow"]:
                if not isinstance(review.get(field), str) or not review[field].strip():
                    flag(f"free_note_review.{field}", f"free_note_review 缺少 {field}。")
    elif "随心记复盘" in tex:
        issues.append("当天没有随心记，但 PDF 出现了“随心记复盘”。")
    if (input_keywords or cards) and sources_json["config"].get("enable_web_search") and not sources_json["text_sources"] and not sources_json["search_notes"]:
        issues.append("启用联网搜索时，sources.json 没有文字来源或失败说明。")
    if has_local_knowledge and not context_scanned_paths:
        issues.append("report_context.json.local_knowledge.scanned_paths 为空，但本地知识库目录已有内容。")
    for source in sources_json["text_sources"]:
        if not source.get("quality_tier") or not source.get("id"):
            issues.append("sources.json 中有来源缺少分层或 id。")
            break
    return {
        "passed": not issues,
        "issues": issues,
        "field_issues": field_issues,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
