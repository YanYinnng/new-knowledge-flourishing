from __future__ import annotations

import argparse
import html
import json
import math
import re
import shutil
import subprocess
import sys
import textwrap
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from context_builder import append_jsonl as append_structured_jsonl
from context_builder import build_secretary_context, stable_id


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "system" / "report_config.json"
VOICE_RULES_PATH = ROOT / "system" / "report_voice_rules.md"
INBOX_DIR = ROOT / "inbox"
REPORT_ROOT = ROOT / "synthesis" / "daily_reports"
KNOWLEDGE_DIRS = [ROOT / "knowledge", ROOT / "library" / "nodes"]
SEED_DIRS = [ROOT / "synthesis" / "idea_seeds", ROOT / "library" / "seeds"]
TRACKING_PATH = ROOT / "tracking" / "topics.md"
REVIEW_QUEUE_DIR = ROOT / "review_queue"


DEFAULT_CONFIG = {
    "enable_web_search": True,
    "enable_images": True,
    "enable_ai_generated_images": False,
    "default_report_mode": "auto",
    "default_input_weight": 3,
    "max_idea_seeds_per_report": 3,
    "latex_engine": "xelatex",
    "max_sources_per_keyword": 5,
    "web_search_provider": "duckduckgo_html",
}


@dataclass
class InputRecord:
    keyword: str
    context: str
    weight: str


@dataclass
class FreeNote:
    id: str
    text: str
    created_at: str
    source: str = ""


@dataclass
class DailyEntries:
    keyword_records: list[InputRecord]
    free_notes: list[FreeNote]
    entry_counts_by_kind: dict[str, int]
    source_format: str


@dataclass
class LocalDoc:
    path: Path
    title: str
    text: str
    weight: str = ""
    status: str = ""


class DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_link = False
        self._in_snippet = False
        self._current_link: dict[str, str] | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        klass = attr.get("class", "")
        if tag == "a" and "result__a" in klass:
            self._in_link = True
            self._current_link = {"title": "", "url": clean_result_url(attr.get("href", "")), "snippet": ""}
            self._buffer = []
        elif tag in {"a", "td"} and "result-link" in klass:
            self._in_link = True
            self._current_link = {"title": "", "url": clean_result_url(attr.get("href", "")), "snippet": ""}
            self._buffer = []
        elif "result__snippet" in klass:
            self._in_snippet = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_link or self._in_snippet:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._in_link and tag == "a":
            if self._current_link:
                self._current_link["title"] = normalize_space(" ".join(self._buffer))
                if self._current_link["title"] and self._current_link["url"]:
                    self.results.append(self._current_link)
            self._current_link = None
            self._in_link = False
            self._buffer = []
        elif self._in_snippet and tag in {"a", "td", "div"}:
            snippet = normalize_space(" ".join(self._buffer))
            if snippet and self.results:
                self.results[-1]["snippet"] = snippet
            self._in_snippet = False
            self._buffer = []


class GenericSearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current_href = ""
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr = {key: value or "" for key, value in attrs}
        href = clean_result_url(attr.get("href", ""))
        if href.startswith("http"):
            self._current_href = href
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._current_href:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._current_href:
            return
        title = normalize_space(" ".join(self._buffer))
        if title:
            self.results.append({"title": title, "url": self._current_href, "snippet": ""})
        self._current_href = ""
        self._buffer = []


def clean_result_url(url: str) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlparse(html.unescape(url))
    query = urllib.parse.parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return query["uddg"][0]
    if parsed.scheme in {"http", "https"}:
        return urllib.parse.urlunparse(parsed)
    return url


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def compact_text(value: Any, limit: int = 220) -> str:
    text = normalize_space(str(value or ""))
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def clean_source_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", html.unescape(value or ""))
    without_controls = "".join(char for char in normalized if unicodedata.category(char)[0] != "C")
    return normalize_space(without_controls.replace("�", ""))


def read_config() -> dict[str, Any]:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return DEFAULT_CONFIG.copy()
    loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config = DEFAULT_CONFIG.copy()
    config.update(loaded)
    return config


def read_report_voice_rules() -> str:
    if not VOICE_RULES_PATH.exists():
        return ""
    return VOICE_RULES_PATH.read_text(encoding="utf-8")


def normalize_weight(value: str, config: dict[str, Any] | None = None) -> str:
    cleaned = str(value or "").strip()
    if cleaned in {"1", "2", "3", "4", "5"}:
        return cleaned
    default = str((config or DEFAULT_CONFIG).get("default_input_weight", 3))
    return default if default in {"1", "2", "3", "4", "5"} else "3"


def parse_markdown_inbox(date_text: str) -> list[InputRecord]:
    path = INBOX_DIR / f"{date_text}.md"
    if not path.exists():
        return []
    records: list[InputRecord] = []
    current: InputRecord | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        keyword_match = re.match(r"^-+\s*关键词[：:]\s*(.+)$", line)
        if keyword_match:
            current = InputRecord(keyword=keyword_match.group(1).strip(), context="未填写", weight=normalize_weight(""))
            records.append(current)
            continue
        if current:
            context_match = re.match(r"^-+\s*(?:补充信息|上下文)[：:]\s*(.+)$", line)
            weight_match = re.match(r"^-+\s*(?:权重|可选权重)[：:]\s*(.+)$", line)
            if context_match:
                current.context = context_match.group(1).strip()
            elif weight_match:
                current.weight = normalize_weight(weight_match.group(1).strip())
    return records


def normalize_keyword_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value or "").splitlines()
    keywords: list[str] = []
    for item in raw_items:
        keyword = str(item or "").strip().lstrip("-").strip()
        if keyword:
            keywords.append(keyword)
    return keywords


def parse_jsonl_inbox(date_text: str) -> DailyEntries | None:
    path = INBOX_DIR / f"{date_text}.jsonl"
    if not path.exists():
        return None

    records: list[InputRecord] = []
    free_notes: list[FreeNote] = []
    counts: dict[str, int] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            counts["invalid_json"] = counts.get("invalid_json", 0) + 1
            continue
        if not isinstance(entry, dict):
            counts["invalid_entry"] = counts.get("invalid_entry", 0) + 1
            continue
        kind = str(entry.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        if kind == "keyword_batch":
            supplemental_info = str(payload.get("supplemental_info", payload.get("context", ""))).strip() or "未填写"
            weight = normalize_weight(str(payload.get("weight", "")))
            for keyword in normalize_keyword_list(payload.get("keywords", payload.get("keyword", ""))):
                records.append(InputRecord(keyword=keyword, context=supplemental_info, weight=weight))
        elif kind == "free_note":
            text = str(payload.get("text", "")).strip()
            if text:
                free_notes.append(
                    FreeNote(
                        id=str(entry.get("id") or f"{date_text}-line-{line_number}"),
                        text=text,
                        created_at=str(entry.get("created_at") or ""),
                        source=str(entry.get("source") or ""),
                    )
                )
    return DailyEntries(records, free_notes, counts, "jsonl")


def read_daily_entries(date_text: str) -> DailyEntries:
    jsonl_entries = parse_jsonl_inbox(date_text)
    if jsonl_entries is not None:
        return jsonl_entries
    records = parse_markdown_inbox(date_text)
    return DailyEntries(
        keyword_records=records,
        free_notes=[],
        entry_counts_by_kind={"legacy_markdown_keyword": len(records)} if records else {},
        source_format="markdown",
    )


def parse_inbox(date_text: str) -> list[InputRecord]:
    return read_daily_entries(date_text).keyword_records


def first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def read_local_docs() -> list[LocalDoc]:
    docs_by_key: dict[str, LocalDoc] = {}
    paths: list[Path] = []
    for base in [*KNOWLEDGE_DIRS, *SEED_DIRS]:
        if base.exists():
            paths.extend(path for path in base.rglob("*.md") if path.is_file())
    if TRACKING_PATH.exists():
        paths.append(TRACKING_PATH)

    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        doc = LocalDoc(
            path=path,
            title=first_heading(text, path.stem),
            text=text,
            weight=field_value(text, "Weight"),
            status=field_value(text, "Status"),
        )
        key = local_doc_key(doc)
        current = docs_by_key.get(key)
        if current is None or local_doc_priority(doc.path) < local_doc_priority(current.path):
            docs_by_key[key] = doc
    return list(docs_by_key.values())


def read_tracking_topics() -> list[str]:
    if not TRACKING_PATH.exists():
        return []
    topics: list[str] = []
    for raw in TRACKING_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        match = re.match(r"^-+\s*(.+)$", line)
        if not match:
            continue
        topic = match.group(1).split("：", 1)[0].strip()
        if topic and topic not in topics:
            topics.append(topic)
    return topics


def field_value(text: str, field: str) -> str:
    match = re.search(rf"^{re.escape(field)}:\s*(.+)$", text, flags=re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def local_doc_key(doc: LocalDoc) -> str:
    doc_id = field_value(doc.text, "ID")
    if doc_id:
        return f"id:{doc_id.lower()}"
    return f"title:{normalize_space(doc.title).lower()}"


def local_doc_priority(path: Path) -> int:
    relative = relative_path(path)
    if relative.startswith("knowledge/") or relative.startswith("synthesis/idea_seeds/"):
        return 0
    if relative == "tracking/topics.md":
        return 1
    return 2


def token_set(value: str) -> set[str]:
    ascii_tokens = {token.lower() for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-/]{1,}", value)}
    cjk_tokens = {chunk for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", value)}
    return ascii_tokens | cjk_tokens


def cjk_bigrams(value: str) -> set[str]:
    chunks = re.findall(r"[\u4e00-\u9fff]{2,}", value)
    grams: set[str] = set()
    for chunk in chunks:
        grams.update(chunk[index : index + 2] for index in range(max(0, len(chunk) - 1)))
    return grams


def related_docs(record: InputRecord, docs: list[LocalDoc], limit: int = 3) -> list[LocalDoc]:
    key_tokens = token_set(record.keyword) | token_set(record.context)
    scored: list[tuple[int, LocalDoc]] = []
    for doc in docs:
        haystack = f"{doc.title}\n{doc.text}"
        score = 0
        if record.keyword and record.keyword in haystack:
            score += 5
        score += len(key_tokens & token_set(haystack))
        if score:
            scored.append((score, doc))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [doc for _, doc in scored[:limit]]


LOW_QUALITY_DOMAINS = {
    "csdn.net",
    "blog.csdn.net",
    "zhihu.com",
    "zhuanlan.zhihu.com",
    "baike.baidu.com",
    "baijiahao.baidu.com",
    "cnblogs.com",
}

AUTHORITATIVE_MEDIA_DOMAINS = {
    "xinhuanet.com",
    "people.com.cn",
    "thepaper.cn",
    "caixin.com",
    "36kr.com",
}

ACADEMIC_DOMAINS = {
    "arxiv.org",
    "doi.org",
    "nature.com",
    "science.org",
    "acm.org",
    "ieee.org",
    "springer.com",
    "sciencedirect.com",
}


def domain_for(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def source_relevance_score(result: dict[str, str], keyword: str) -> int:
    title = clean_source_text(result.get("title", ""))
    snippet = clean_source_text(result.get("snippet", ""))
    url = result.get("url", "")
    haystack = f"{title} {snippet} {url}".lower()
    keyword_clean = clean_source_text(keyword)
    score = 0
    if keyword_clean and keyword_clean.lower() in haystack:
        score += 50
    ascii_tokens = {token for token in token_set(keyword_clean) if re.search(r"[a-z0-9]", token)}
    score += 8 * len(ascii_tokens & token_set(haystack))
    grams = cjk_bigrams(keyword_clean)
    score += 4 * len({gram for gram in grams if gram in haystack})
    return score


def source_profile(result: dict[str, str], keyword: str) -> dict[str, Any]:
    url = result.get("url", "")
    title = clean_source_text(result.get("title", ""))
    snippet = clean_source_text(result.get("snippet", ""))
    domain = domain_for(url)
    text = f"{title} {snippet} {url}".lower()
    relevance = source_relevance_score({**result, "title": title, "snippet": snippet}, keyword)

    tier = "普通线索"
    role = "可作为线索，不能直接作为核心结论。"
    score = 35

    if any(domain.endswith(item) for item in LOW_QUALITY_DOMAINS) or "百科" in title:
        tier = "低优先级线索"
        role = "只作为入口线索，不作为核心判断依据。"
        score = 20
    if any(domain.endswith(item) for item in AUTHORITATIVE_MEDIA_DOMAINS):
        tier = "权威媒体"
        role = "可用于判断近期动态，但仍需和官方资料互证。"
        score = 65
    if any(domain.endswith(item) for item in ACADEMIC_DOMAINS):
        tier = "论文/报告"
        role = "可作为技术或研究判断依据。"
        score = 75
    if domain.endswith((".edu", ".edu.cn", ".gov.cn", ".ac.cn")):
        tier = "官方/机构资料"
        role = "优先用于核实机构定位、项目方向和公开事实。"
        score = 90
    if ("官网" in title or "official" in text) and any(word in keyword for word in ["学院", "codex", "Codex", "OpenAI"]):
        tier = "官方/机构资料"
        role = "优先用于核实机构定位、项目方向和公开事实。"
        score = max(score, 85)
    elif "官网" in title:
        tier = "项目主页/机构主页"
        role = "只能代表该机构或产品自己的说法，不能替行业概念背书。"
        score = max(score, 45)
    if "深圳科创学院" in keyword and "innoxsz.com" in domain:
        tier = "官方/机构资料"
        role = "深圳科创学院官网，优先用于核实机构定位。"
        score = 95
    if "上海创智学院" in keyword and "sii.edu.cn" in domain:
        tier = "官方/机构资料"
        role = "上海创智学院官网，优先用于核实机构定位。"
        score = 95
    if "openai.com" in domain:
        tier = "官方/机构资料"
        role = "OpenAI 官方资料，优先用于核实 Codex 能力。"
        score = 95
    if "github.com" in domain:
        tier = "项目主页/机构主页"
        role = "可用于核实项目事实，结论仍需结合文档或官方说明。"
        score = max(score, 60)

    if relevance <= 4 and score >= 60 and keyword not in text:
        tier = "普通线索"
        role = "来源域名较强但和关键词相关性不足，只能作为旁证。"
        score = 40

    channel = str(result.get("search_channel") or "")
    if channel in {"weixin", "bilibili", "zhihu", "weibo"}:
        tier = "平台内容线索"
        role = f"来自 {channel} 的平台搜索结果，可用于发现相关讨论、活动或案例；需要在正文中说明与关键词的连接强弱。"
        score = max(score, 58)
    if channel in {"baidu-scholar", "wanfang", "cnki", "google-scholar", "baidu_scholar", "google_scholar"}:
        tier = "论文/学术线索"
        role = f"来自 {channel} 的学术搜索结果，可作为研究背景或技术趋势线索。"
        score = max(score, 72)
    if channel == "open_web":
        role = "来自开放网页搜索的相关线索，可用于补充近期动态；正文需要区分事实、观察和推测。"
        score = max(score, 45)

    return {
        **result,
        "title": title,
        "snippet": snippet,
        "domain": domain,
        "quality_tier": tier,
        "source_role": role,
        "authority_score": score,
        "relevance_score": relevance,
    }


def rank_sources(results: list[dict[str, str]], keyword: str, limit: int) -> list[dict[str, str]]:
    profiled = [source_profile(result, keyword) for result in results]
    profiled.sort(
        key=lambda item: (
            int(item.get("authority_score", 0)) + int(item.get("relevance_score", 0)),
            int(item.get("authority_score", 0)),
        ),
        reverse=True,
    )
    return profiled[:limit]


def input_search_query(keyword: str) -> str:
    lower = keyword.lower()
    if "codex" in lower and "goal" in lower:
        return "site:developers.openai.com/codex Codex /goal follow goal"
    if "深圳科创学院" in keyword:
        return "深圳科创学院 官网 科创训练营 创业 AI"
    if "上海创智学院" in keyword:
        return "上海创智学院 官网 AI 人才培养 产学研"
    if "私域" in keyword:
        return "私域 高客单 线下门店 会员 复购"
    return keyword


def review_search_query(topic: str) -> str:
    if "新知孵化" in topic:
        return "个人知识管理 AI workflow daily review knowledge management"
    if "本地 Markdown" in topic:
        return "本地 Markdown 知识库 个人知识管理 笔记 工具"
    if "晚间复盘" in topic or "每日" in topic:
        return "个人每日复盘 知识管理 日报 方法"
    return f"{topic} 个人知识管理 工作流"


def is_irrelevant_review_result(result: dict[str, str], topic: str) -> bool:
    text = f"{result.get('title', '')} {result.get('snippet', '')} {result.get('url', '')}".lower()
    stock_noise = ["股票", "a股", "沪指", "深成指", "创业板", "涨", "跌", "收盘", "选股", "打板", "公告"]
    if ("复盘" in topic or "每日" in topic) and any(word in text for word in stock_noise):
        return True
    if "本地 markdown" in topic.lower():
        return not any(word in text for word in ["markdown", "知识库", "笔记", "wiki", "本地"])
    if "新知孵化" in topic:
        return not any(word in text for word in ["知识", "管理", "工作流", "复盘", "ai", "workflow", "knowledge", "review"])
    return False


def filter_results_for_topic(results: list[dict[str, str]], topic: str) -> list[dict[str, str]]:
    filtered = [result for result in results if not is_irrelevant_review_result(result, topic)]
    return filtered or results


def search_endpoint(endpoint: str, query: str, limit: int) -> tuple[list[dict[str, str]], str]:
    if endpoint == "duckduckgo_html":
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        parser: HTMLParser = DuckDuckGoParser()
    elif endpoint == "bing":
        url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query})
        parser = GenericSearchParser()
    elif endpoint == "baidu":
        url = "https://www.baidu.com/s?" + urllib.parse.urlencode({"wd": query})
        parser = GenericSearchParser()
    else:
        return [], f"{endpoint} 不是已知搜索端点"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) IdeaSprout/0.1",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return [], f"{endpoint} 搜索失败：{exc}"

    parser.feed(body)
    raw_results = getattr(parser, "results", [])
    blocked_domains = {"bing.com", "microsoft.com", "baidu.com", "duckduckgo.com", "go.microsoft.com"}
    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    for result in raw_results:
        url = clean_result_url(str(result.get("url", "")))
        title = clean_source_text(str(result.get("title", "")))
        if not url or not title or url in seen:
            continue
        domain = domain_for(url)
        if any(domain == item or domain.endswith("." + item) for item in blocked_domains):
            continue
        seen.add(url)
        cleaned.append(
            {
                "title": title,
                "url": url,
                "snippet": clean_source_text(str(result.get("snippet", ""))),
                "search_endpoint": endpoint,
            }
        )
        if len(cleaned) >= max(limit * 3, limit):
            break
    if not cleaned:
        return [], f"{endpoint} 没有解析到可用结果"
    return cleaned, ""


def opencli_search_sites() -> list[tuple[str, list[str]]]:
    return [
        ("weixin", ["opencli", "weixin", "search"]),
        ("bilibili", ["opencli", "bilibili", "search"]),
        ("zhihu", ["opencli", "zhihu", "search"]),
        ("weibo", ["opencli", "weibo", "search"]),
        ("baidu-scholar", ["opencli", "baidu-scholar", "search"]),
        ("wanfang", ["opencli", "wanfang", "search"]),
        ("cnki", ["opencli", "cnki", "search"]),
        ("google-scholar", ["opencli", "google-scholar", "search"]),
    ]


def opencli_row_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ["summary", "excerpt", "content", "authors", "journal", "year", "publish_time", "author", "votes", "score", "cited"]:
        value = row.get(key)
        if value not in (None, ""):
            parts.append(f"{key}: {value}")
    return "；".join(parts)


def run_opencli_search(query: str, limit: int) -> tuple[list[dict[str, str]], str]:
    opencli_path = shutil.which("opencli")
    if not opencli_path:
        return [], "opencli 未在 PATH 中找到，已跳过 opencli 搜索。"
    results: list[dict[str, str]] = []
    notes: list[str] = []
    per_site_limit = max(2, min(4, limit))

    def run_site(site: str, command_prefix: list[str]) -> tuple[list[dict[str, str]], str]:
        site_results: list[dict[str, str]] = []
        command = [opencli_path, *command_prefix[1:], query, "--limit", str(per_site_limit), "-f", "json"]
        try:
            process = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=40,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [], f"{site}: {exc}"
        if process.returncode != 0:
            return [], f"{site}: exit {process.returncode} {(process.stderr or process.stdout)[:120]}"
        try:
            data = json.loads(process.stdout or "[]")
        except json.JSONDecodeError as exc:
            return [], f"{site}: JSON 解析失败 {exc}"
        rows = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = clean_source_text(str(row.get("title") or row.get("name") or row.get("question_title") or ""))
            url = clean_result_url(str(row.get("url") or row.get("link") or ""))
            snippet = clean_source_text(opencli_row_text(row))
            if not title and not snippet:
                continue
            site_results.append(
                {
                    "title": title or snippet[:80],
                    "url": url,
                    "snippet": snippet,
                    "search_channel": site,
                    "search_endpoint": "opencli",
                    "search_query": query,
                }
            )
        return site_results, ""

    sites = opencli_search_sites()
    with ThreadPoolExecutor(max_workers=min(4, len(sites))) as executor:
        future_to_site = {executor.submit(run_site, site, command_prefix): site for site, command_prefix in sites}
        for future in as_completed(future_to_site):
            site = future_to_site[future]
            try:
                site_results, note = future.result()
            except Exception as exc:
                notes.append(f"{site}: {exc}")
                continue
            results.extend(site_results)
            if note:
                notes.append(note)
    return results, "；".join(notes)


def site_search_queries(query: str) -> list[tuple[str, str]]:
    return [
        ("open_web", query),
        ("weixin", f"{query} site:mp.weixin.qq.com OR site:weixin.sogou.com"),
        ("bilibili", f"{query} site:bilibili.com"),
        ("zhihu", f"{query} site:zhihu.com"),
        ("weibo", f"{query} site:weibo.com"),
        ("baidu_scholar", f"{query} site:xueshu.baidu.com"),
        ("wanfang", f"{query} site:wanfangdata.com.cn"),
        ("cnki", f"{query} site:cnki.net OR site:cnki.com.cn"),
        ("google_scholar", f"{query} site:scholar.google.com"),
    ]


def search_web(query: str, limit: int, enabled: bool) -> tuple[list[dict[str, str]], str]:
    if not enabled:
        return [], "联网搜索已在 system/report_config.json 中关闭。"
    all_results: list[dict[str, str]] = []
    notes: list[str] = []
    seen: set[str] = set()

    opencli_results, opencli_note = run_opencli_search(query, limit)
    if opencli_note:
        notes.append(opencli_note)
    for result in opencli_results:
        key = result.get("url") or f"{result.get('search_channel')}:{result.get('title')}"
        if key and key not in seen:
            seen.add(key)
            all_results.append(result)

    endpoint_order = ["bing", "baidu", "duckduckgo_html"]
    per_channel_limit = max(2, min(4, limit))
    for channel, channel_query in site_search_queries(query):
        for endpoint in endpoint_order:
            results, note = search_endpoint(endpoint, channel_query, per_channel_limit)
            if note:
                notes.append(f"{channel}/{note}")
            if not results:
                continue
            for result in results:
                key = result.get("url") or f"{channel}:{result.get('title')}"
                if key and key not in seen:
                    seen.add(key)
                    all_results.append(
                        {
                            **result,
                            "search_channel": channel,
                            "search_query": channel_query,
                        }
                    )
            break
        if len(all_results) >= limit * 4:
            break

    if not all_results:
        return [], "多源搜索没有解析到可用结果；" + "；".join(notes[:8])
    ranked = rank_sources(all_results, query, max(limit, min(len(all_results), limit * 2)))
    return ranked[:limit], "；".join(notes[:8])


def infer_mainline(records: list[InputRecord], docs: list[LocalDoc]) -> str:
    if not records:
        return "今天没有新的网页输入，适合回看几个高权重主题：哪些仍在帮你做选择，哪些已经可以少花点心力。"
    keywords = "、".join(record.keyword for record in records[:4])
    has_ai = any("AI" in record.keyword.upper() or "AI" in record.context.upper() for record in records)
    has_tool = any("codex" in record.keyword.lower() or "自动" in record.keyword for record in records)
    has_opportunity = any(
        word in record.keyword or word in record.context
        for record in records
        for word in ["学院", "私域", "创业", "升学"]
    )
    if has_ai and has_opportunity:
        return f"今天的信息都在靠近“AI 相关机会怎么筛”：{keywords} 可以放进同一张机会清单里看，分别对应升学、工具、项目或商业场景。现在先抓住能问人、能试做、能补资料的入口。"
    if has_tool:
        return f"今天的主线是“让工具真的省心”：{keywords} 都可以拿来检查点子发芽系统有没有减少重复整理、补跑和复盘成本。"
    return f"今天的输入集中在：{keywords}。先从里面挑一个最贴近近期学习、项目或机会的小入口，明天补一条更具体的来源或原话。"


def source_refs(results: list[dict[str, str]], max_count: int = 3) -> str:
    refs = [f"[{result['id']}]" for result in results[:max_count] if result.get("id")]
    return " ".join(refs) if refs else ""


def credible_sources(results: list[dict[str, str]]) -> list[dict[str, str]]:
    return [result for result in results if int(result.get("authority_score", 0)) >= 55]


def low_quality_sources(results: list[dict[str, str]]) -> list[dict[str, str]]:
    return [result for result in results if int(result.get("authority_score", 0)) < 55]


def concept_sentence(record: InputRecord) -> str:
    keyword = record.keyword
    context = record.context or "未填写"
    if "codex" in keyword.lower() or "goal" in keyword.lower():
        return "它是一类把一次性对话延长成可检查目标的工具能力。对你有用的地方在于：它能记住目标、拆出后续步骤，并在长项目里减少反复交代背景的成本。"
    if "上海创智学院" in keyword:
        return "它可以看成一个 AI 人才培养与产学研资源入口。真正值得看的地方，是课程、导师、项目、企业合作和同伴网络能不能组合成一条可参与的 AI+X 路径。"
    if "深圳科创学院" in keyword:
        return "它围绕硬科技创业和工程人才培养展开。放到 AI+硬件、项目制训练和创业资源里看，它的价值取决于有没有真实项目、导师网络和可接触的训练机会。"
    if "学院" in keyword:
        return "它是一条升学或能力路径线索。名称是入口，真正值得看的是课程、项目、人和选择空间，能不能补上你现在的学习或经历缺口。"
    if "私域" in keyword:
        return "它指的是品牌或个人能反复触达一群人的关系资产。放到雪茄吧这种高客单场景里，关键会落到会员关系、活动组织、复购和老客口碑。"
    return f"它是今天从“{context}”里冒出来的观察对象。先看清它属于什么领域、有哪些可靠来源，再决定要不要留下来继续追。"


def synthesize_findings(record: InputRecord, results: list[dict[str, str]], note: str) -> list[str]:
    credible = credible_sources(results)
    low = low_quality_sources(results)
    refs = source_refs(credible or results)
    findings: list[str] = []

    if not results:
        return [note or "当前没有查到可核实的新进展；这条先按你的补充信息保留。"]

    if credible:
        top_tiers = "、".join(sorted({source["quality_tier"] for source in credible}))
        source_clause = f"可追溯来源：{refs}。" if refs else "后续渲染时需要保留来源编号。"
        findings.append(f"这次有{top_tiers}可用，可以写进近期动态；平台讨论或普通网页要说明和关键词的关系。{source_clause}")
    elif low:
        findings.append(f"这次只找到普通网页、博客或论坛线索 {source_refs(low)}。可以当作找入口的材料，别写成确定结论。")

    keyword = record.keyword
    combined_text = " ".join(f"{item.get('title', '')} {item.get('snippet', '')}" for item in results)
    if "codex" in keyword.lower() or "goal" in keyword.lower():
        findings.extend(
            [
                "可核实的信息主要指向长期目标管理和补跑能力，适合放进点子发芽的工具链观察里。",
                "真正要看的，是它能不能减少重复交代背景、补跑漏掉任务、检查日报质量这几类摩擦。",
                "今天先把它当作工作流改进线索，后续用一次真实任务来验证。"
            ]
        )
    elif "上海创智学院" in keyword:
        findings.extend(
            [
                "官方/机构资料显示它围绕人工智能人才培养与产学研生态展开，和“AI+X”方向的连接强于普通学校资讯。",
                "对你来说，它可以放进机会雷达：课程、训练营、科研项目、企业合作、实习入口和 AI 创业网络都值得查。",
                "现在的信息还不足以确认申请价值，但足够进入 watchlist：下一步应核实培养方式、适合对象、申请门槛和可接触的人。",
            ]
        )
    elif "深圳科创学院" in keyword:
        findings.extend(
            [
                "公开资料更强调硬科技创业、工程训练和孵化网络；这和单纯 AI 学术路径不同，更偏“技术+产品+创业”。",
                "如果你关注未来升学路径，它的价值不只在学校名称，而在能否提供项目制经历、导师网络和真实创业案例。",
                "今天可以把它和上海创智学院放在同一组里比较：一个看 AI 产学研，一个看硬科技创业和工程训练。",
            ]
        )
    elif "学院" in keyword:
        findings.extend(
            [
                "这类机构线索优先看官网、培养方案和真实学生反馈。",
                "对你的价值主要取决于是否能带来项目经历、导师网络、实习/科研机会或申请叙事素材。",
                "今天先放进 watchlist，补齐官网、申请条件和学长原话后再定权重。",
            ]
        )
    elif "私域" in keyword:
        findings.extend(
            [
                "可核实资料普遍把私域指向“可重复、低成本触达”的关系资产；但这个定义本身不够行动化。",
                "雪茄吧场景更值得关注：高客单、强信任、线下社交和活动复购，可能比泛泛的流量概念更接近真实商业机制。",
                "今天可以把它转成一个案例问题：这种店怎样让顾客从一次消费变成社群成员。",
            ]
        )
    else:
        if combined_text:
            findings.extend(
                [
                    "搜索结果说明它有公开讨论或相关材料，但现在还缺一条更贴近你自己的来源。",
                    "先把它放回你的补充信息里看：它从哪里来、为什么被你注意、能不能带来下一步行动。",
                    "等它再次出现，或者出现更可靠来源，再考虑提高权重。",
                ]
            )

    if len(findings) < 3:
        findings.append("当前资料密度还不够，先不急着新建复杂结构。")
    return findings[:5]


def local_connection_analysis(record: InputRecord, docs: list[LocalDoc], topics: list[str]) -> list[str]:
    connected = related_docs(record, docs)
    items: list[str] = []
    if connected:
        for doc in connected[:2]:
            items.append(
                f"这条可以和你之前记过的《{doc.title}》放在一起看：它会影响你后续怎么筛选资料、提问题或安排下一步。"
            )

    keyword = record.keyword
    context = record.context
    if "codex" in keyword.lower() or "goal" in keyword.lower():
        items.append(
            "AI 工具/自动化：你正在用 Codex、补跑任务和 PDF 报告维护点子发芽；这条能直接检验系统有没有省心。"
        )
        items.append(
            "项目素材：它适合写进 README 或复盘里，记录你怎样把长期目标交给工具托住。"
        )
    if "上海创智学院" in keyword:
        items.append(
            "科研/学习：它和 AI+X、科研训练、项目制学习有关，适合放进“AI 升学路径”清单。"
        )
        items.append(
            "人脉/机会：来源是学长分享，下一步最值钱的是追问具体项目、申请门槛、实习/导师资源和创新创业生态。"
        )
        items.append(
            "申请/表达素材：如果未来写申请或个人陈述，它可以成为“我如何识别 AI 生态机会”的一段素材，前提是补齐真实经历和选择理由。"
        )
    elif "深圳科创学院" in keyword:
        items.append(
            "创新创业：它偏硬科技创业和工程实践，适合和上海创智学院并列比较 AI+工程、AI+创业的路径差异。"
        )
        items.append(
            "机会入口：学长分享已经给了一个低成本入口，下一步把机构名改写成具体问题清单。"
        )
    elif "学院" in keyword:
        items.append(
            "科研/学习：它可能进入升学路径清单；先补申请对象、时间线和筛选标准，再决定权重。"
        )
    if "私域" in keyword:
        items.append(
            "商业/创业：雪茄吧是高客单线下场景，私域在这里可能对应会员关系、活动组织、复购和口碑传播，适合转成案例观察。"
        )
        items.append(
            "表达素材：如果之后写商业观察或创业案例，“雪茄吧如何做私域”会比泛泛解释私域更有辨识度。"
        )

    if not items:
        topic_hint = "、".join(topics[:3]) if topics else "现有长期主题"
        items.append(
            f"它还没有落到 {topic_hint} 里的具体场景。先等第二次出现，或者补一个链接、原话、人名，再决定要不要建节点。"
        )
    return items


def judgment_for(record: InputRecord, sources: list[dict[str, str]], related: list[LocalDoc]) -> str:
    if record.weight in {"4", "5"}:
        return "值得追踪：你给了高权重，但仍需要一个可验证的小行动，避免直接升为核心主题。"
    if "codex" in record.keyword.lower() or "goal" in record.keyword.lower():
        return "值得追踪：它直接影响点子发芽系统的自动化能力。"
    if "上海创智学院" in record.keyword or "深圳科创学院" in record.keyword:
        return "可进入 watchlist：它可能连接 AI+X、实习、人脉和创新创业机会，但需要官网信息和学长原话补证。"
    if "私域" in record.keyword:
        return "可转化为素材：目前不必升权，但可以沉淀为高客单线下生意的案例观察。"
    if related and credible_sources(sources):
        return "值得追踪：本地已有连接且有较高质量来源支持。"
    if credible_sources(sources):
        return "先轻量保留：外部资料可查，个人用途还需要补一条更具体的线索。"
    return "暂时放下：当前证据不足，先不增加维护负担。"


def next_step_for(record: InputRecord) -> str:
    if "链接" in record.context or "http" in record.context:
        return "今晚回看原链接，摘出 1 句真正触动你的信息，并写下它为什么和你有关。"
    if "上海创智学院" in record.keyword:
        return "向学长追问 3 个问题：适合什么背景、有没有项目/实习入口、AI+X 方向具体怎么落地。"
    if "深圳科创学院" in record.keyword:
        return "打开官网或项目介绍，记录 1 个你能参与或模仿的硬科技/AI 项目训练形式。"
    if "学院" in record.keyword:
        return "补齐官网链接、申请对象和一个你能联系的人，再决定它是否进入 watchlist。"
    if "私域" in record.keyword:
        return "把雪茄吧案例写成 3 行：客单价/复购方式/老板如何触达老客。"
    if "codex" in record.keyword.lower() or "goal" in record.keyword.lower():
        return "记录一次 /goal 或 automation 真正省下的步骤，再决定要不要写入工具工作流节点。"
    return "补一个来源标题、链接或一句原话，再写下它能连接到哪个长期主题。"


def review_local_judgment(doc: LocalDoc) -> str:
    if "新知孵化工作流" in doc.title:
        return (
            "它仍是当前唯一真正的主线：网页输入、PDF 日报、邮件发送和 Git 同步都在围绕它减摩擦。"
            "今天不需要扩功能，重点是让报告质量稳定到你愿意每天读。"
        )
    return "它可以继续保留。今天先看它还能不能导向一个具体动作。"


def review_personal_connection(doc: LocalDoc) -> str:
    if "新知孵化工作流" in doc.title:
        return (
            "这直接关系到你以后能不能只通过网页输入关键词、补充信息和权重，就拿到一份有用的晚间报告。"
            "如果报告仍像搜索拼贴，这个系统就没有省心；如果报告能指出主线和下一步，它才值得继续用。"
        )
    return "它今天还没有被新的触发词拉回行动场景，先轻轻带过。"


def review_next_step(doc: LocalDoc) -> str:
    if "新知孵化工作流" in doc.title:
        return "明天只做一次真实输入测试：从网页提交 3 个关键词，然后检查日报是否能给出主线、旧知识连接和一个可执行小动作。"
    return "明天如果它再次出现，再补一条具体补充信息；否则不主动扩展。"


def seed_review_item(seed: LocalDoc) -> str:
    if "每日小推进" in seed.title:
        return (
            f"{seed.title}：继续保留；为什么值得看：它能防止日报停在整理和总结，逼迫系统给出一个 10-20 分钟可完成的动作；"
            "成熟度 45/100；最小下一步：明天只执行日报里最小的一条动作，并记录是否真的做完。"
        )
    return f"{seed.title}：沉睡点子回顾；成熟度 40/100；最小下一步是确认它是否仍能服务当前系统。"


def synthesize_review_external_check(keyword: str, results: list[dict[str, str]], note: str) -> str:
    if not results:
        return note or "当前环境无法联网，未能核实外部进展。"
    credible = credible_sources(results)
    refs = source_refs(credible or results)
    if "新知孵化工作流" in keyword:
        return (
            f"外部资料 {refs} 可以补充 AI/个人知识管理的趋势背景；今天重点看系统能不能把网页输入、晚间报告和明日小推进稳定串起来。"
        )
    if "本地 Markdown" in keyword:
        return (
            f"可参考的外部线索 {refs} 指向的是轻量、可迁移、可手动审查；"
            "这支持继续用 Markdown/JSON 做底座，暂时不急着上数据库或知识图谱。"
        )
    if "晚间复盘" in keyword or "每日" in keyword:
        return (
            f"外部资料 {refs} 可以当作方法背景；本项目的复盘重点是把当天输入压缩成一个结论、一条旧记录和一个明天能做的小动作。"
        )
    if credible:
        return (
            f"已找到较高质量来源 {source_refs(credible)}，但无新输入日只做轻量核实："
            "它仍可作为 watchlist 背景，不因此自动升权。"
        )
    return (
        f"只找到普通线索 {source_refs(results)}，还撑不起新进展；"
        "先放在背景里，不写成核心结论。"
    )


def external_progress_summary(keyword: str, results: list[dict[str, str]], note: str) -> str:
    if not results:
        return f"{keyword}：{note or '当前环境无法联网，未能核实外部进展。'}"
    credible = credible_sources(results)
    refs = source_refs(credible or results)
    if "新知孵化工作流" in keyword:
        return f"{keyword}：外部背景 {refs} 说明 AI 工具正在降低个人知识整理成本；今天用它来校准本地工作流是否省心。"
    if "本地 Markdown" in keyword:
        return f"{keyword}：外部线索 {refs} 支持“本地、可读、可迁移”的方向；继续保持轻量文件结构。"
    if "晚间复盘" in keyword or "每日" in keyword:
        return f"{keyword}：外部线索 {refs} 可以补充复盘方法；本项目的重点是每天产出一个可执行的小结论。"
    if credible:
        tiers = "、".join(sorted({item["quality_tier"] for item in credible}))
        return f"{keyword}：已用{tiers}核实背景 {source_refs(credible)}；本次保留和当天输入或 watchlist 强相关的部分。"
    return f"{keyword}：搜索结果主要是普通博客/百科/论坛 {source_refs(results)}，先作为背景线索。"


def connection_items_for_records(records: list[InputRecord], docs: list[LocalDoc], topics: list[str]) -> list[str]:
    items: list[str] = []
    for record in records:
        analyses = local_connection_analysis(record, docs, topics)
        if analyses:
            items.append(f"{record.keyword}：{analyses[0]}")
            if len(analyses) > 1 and any(word in record.keyword for word in ["学院", "私域", "codex", "goal"]):
                items.append(f"{record.keyword}：{analyses[1]}")
    return items


def idea_seed_items(records: list[InputRecord], docs: list[LocalDoc], searches: dict[str, tuple[list[dict[str, str]], str]], limit: int) -> list[str]:
    candidates: list[tuple[int, str]] = []
    school_records = [record for record in records if "学院" in record.keyword]
    handled_keywords: set[str] = set()
    if len(school_records) >= 2:
        score = 60
        if any(credible_sources(searches.get(record.keyword, ([], ""))[0]) for record in school_records):
            score += 10
        names = "、".join(record.keyword for record in school_records)
        candidates.append(
            (
                min(score, 85),
                f"AI+X 升学/实习机会雷达；来源组合：今日输入“{names}” + 官方/机构资料 + 学长分享；"
                "为什么值得关注：它把升学、科研训练、实习入口、人脉请教和创新创业生态放到同一张机会地图里，适合后续做横向比较；"
                f"成熟度 {min(score, 85)}/100；最小下一步：向学长追问这几个学院分别适合什么背景、有没有项目/实习入口、AI+X 方向具体怎么落地。"
            )
        )
        handled_keywords.update(record.keyword for record in school_records)

    for record in records:
        if record.keyword in handled_keywords:
            continue
        score = 30
        if credible_sources(searches.get(record.keyword, ([], ""))[0]):
            score += 15
        if related_docs(record, docs):
            score += 15
        if any(word in record.keyword for word in ["codex", "goal", "学院", "私域"]):
            score += 15
        if score < 50:
            continue
        if "codex" in record.keyword.lower() or "goal" in record.keyword.lower():
            name = "把 Codex goal 当作个人项目推进器"
            why = "它能把长期目标、补跑机制和日报质量放进同一套可检查流程。"
        elif "学院" in record.keyword:
            name = "AI+X 升学/实习机会雷达"
            why = "深圳科创学院和上海创智学院可以合并成一个机会地图，用来筛选科研、实习、人脉和创新创业入口。"
        elif "私域" in record.keyword:
            name = "高客单线下店的私域机制观察"
            why = "雪茄吧场景足够具体，能把抽象营销词转化成会员、活动和复购问题。"
        else:
            name = f"{record.keyword} 的观察种子"
            why = "它和今日输入及本地主题有初步连接。"
        why = why.rstrip("。；; ")
        item = (
            f"{name}；来源组合：今日输入“{record.keyword}” + "
            f"{'外部较高质量来源' if credible_sources(searches.get(record.keyword, ([], ''))[0]) else '外部线索'} + 本地长期主题；"
            f"为什么值得关注：{why}；成熟度 {min(score, 85)}/100；最小下一步：{next_step_for(record)}"
        )
        candidates.append((score, item))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in candidates[:limit]]


def generate_radar_image(report_dir: Path, records: list[InputRecord], docs: list[LocalDoc]) -> dict[str, str] | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None
    if not records:
        labels = [doc.title[:14] for doc in high_weight_docs(docs)[:5]]
    else:
        labels = [record.keyword[:14] for record in records[:8]]
    if len(labels) < 2:
        return None
    assets_dir = report_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    image_path = assets_dir / "cognitive-radar.png"
    size = 1200
    center = size // 2
    radius = 390
    image = Image.new("RGB", (size, size), "#f8faf7")
    draw = ImageDraw.Draw(image)
    font_path = next(
        (Path(path) for path in [
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
        ] if Path(path).exists()),
        None,
    )
    font = ImageFont.truetype(str(font_path), 34) if font_path else ImageFont.load_default()
    small = ImageFont.truetype(str(font_path), 26) if font_path else ImageFont.load_default()
    colors = ["#0f766e", "#2563eb", "#b45309", "#7c3aed", "#be123c", "#15803d", "#0369a1", "#a16207"]
    draw.ellipse((center - radius, center - radius, center + radius, center + radius), outline="#cbd5c0", width=4)
    draw.ellipse((center - 180, center - 180, center + 180, center + 180), outline="#dfe7d8", width=3)
    draw.text((center - 130, center - 24), "今日认知雷达", fill="#1f2937", font=font)
    for index, label in enumerate(labels):
        angle = 2 * math.pi * index / len(labels) - math.pi / 2
        x = center + int(math.cos(angle) * radius)
        y = center + int(math.sin(angle) * radius)
        draw.line((center, center, x, y), fill="#b7c7bd", width=3)
        color = colors[index % len(colors)]
        draw.ellipse((x - 16, y - 16, x + 16, y + 16), fill=color)
        text_x = x + (20 if x >= center else -260)
        text_y = y - 20
        draw.text((text_x, text_y), label, fill="#111827", font=small)
    image.save(image_path)
    return {
        "path": relative_path(image_path),
        "tex_path": "assets/cognitive-radar.png",
        "caption": "今日认知雷达图：根据当天输入或复盘节点生成，用于辅助理解信息之间的注意力分布。",
        "source": "本地脚本根据当日报告数据生成；不是外部截图或真实实验结果。",
        "type": "generated_diagram",
    }


def high_weight_docs(docs: list[LocalDoc]) -> list[LocalDoc]:
    def score(doc: LocalDoc) -> int:
        try:
            return int(doc.weight)
        except ValueError:
            return 0
    return sorted([doc for doc in docs if score(doc) >= 3], key=score, reverse=True)


def dormant_seeds(docs: list[LocalDoc], limit: int) -> list[LocalDoc]:
    seeds = [doc for doc in docs if "idea_seeds" in str(doc.path) or "seeds" in str(doc.path)]
    return seeds[:limit]


def relative_path(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def tex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def tex_item(text: str) -> str:
    return f"\\item {tex_escape(text)}"


def tex_items(items: list[str]) -> str:
    if not items:
        return "\\begin{itemize}[leftmargin=2em]\n\\item 无。\n\\end{itemize}"
    return "\\begin{itemize}[leftmargin=2em]\n" + "\n".join(tex_item(item) for item in items) + "\n\\end{itemize}"


def source_line(source: dict[str, str]) -> str:
    source_id = source.get("id", "")
    tier = source.get("quality_tier", "未分层")
    title = tex_escape(f"[{source_id}] {tier}：{source.get('title', '未命名来源')}")
    url = source.get("url", "")
    if url:
        return rf"\item \href{{{url}}}{{{title}}}"
    return tex_item(title)


def source_cards(results: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "id": result.get("id", ""),
            "title": result.get("title", ""),
            "url": result.get("url", ""),
            "domain": result.get("domain", ""),
            "quality_tier": result.get("quality_tier", ""),
            "source_role": result.get("source_role", ""),
            "relevance_score": result.get("relevance_score", 0),
            "search_channel": result.get("search_channel", ""),
            "search_endpoint": result.get("search_endpoint", ""),
            "search_query": result.get("search_query", ""),
        }
        for result in results
    ]


def source_tier_counts(sources_json: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in sources_json.get("text_sources", []):
        tier = str(source.get("quality_tier") or "未分层")
        counts[tier] = counts.get(tier, 0) + 1
    return counts


def build_report_brief(
    date_text: str,
    records: list[InputRecord],
    docs: list[LocalDoc],
    searches: dict[str, tuple[list[dict[str, str]], str]],
    sources_json: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "date": date_text,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "input" if records else "review",
        "reader_promise": "先说今天能用的结论，再给必要证据；来源做支撑，不把搜索结果堆成正文。",
        "mainline": infer_mainline(records, docs),
        "source_policy": {
            "priority": ["官方/机构资料", "论文/报告", "权威媒体", "项目主页/机构主页", "普通线索", "低优先级线索"],
            "tier_counts": source_tier_counts(sources_json),
            "low_quality_rule": "低质量来源只能作为入口线索，不能作为核心结论。",
        },
        "local_knowledge_scan": {
            "scanned_paths": sorted(relative_path(doc.path) for doc in docs),
            "tracking_topics": read_tracking_topics(),
        },
        "quality_risks": [],
    }

    if records:
        keyword_cards: list[dict[str, Any]] = []
        for record in records:
            results, note = searches.get(record.keyword, ([], ""))
            connected = related_docs(record, docs)
            keyword_cards.append(
                {
                    "keyword": record.keyword,
                    "supplemental_info": record.context,
                    "weight": record.weight,
                    "what_it_is": concept_sentence(record),
                    "findings": synthesize_findings(record, results, note),
                    "personal_relevance": local_connection_analysis(record, docs, brief["local_knowledge_scan"]["tracking_topics"]),
                    "local_matches": [
                        {"title": doc.title, "path": relative_path(doc.path), "weight": doc.weight, "status": doc.status}
                        for doc in connected
                    ],
                    "judgment": judgment_for(record, results, connected),
                    "next_step": next_step_for(record),
                    "sources": source_cards(results),
                }
            )
            if not credible_sources(results):
                brief["quality_risks"].append(f"{record.keyword} 缺少高质量来源，正文只能低置信观察。")
        brief["keyword_cards"] = keyword_cards
        brief["old_knowledge_connections"] = connection_items_for_records(
            records,
            docs,
            brief["local_knowledge_scan"]["tracking_topics"],
        )
        brief["idea_seeds"] = idea_seed_items(records, docs, searches, int(config.get("max_idea_seeds_per_report", 3)))
    else:
        review_cards: list[dict[str, Any]] = []
        for doc in high_weight_docs(docs)[:4]:
            results, note = searches.get(doc.title, ([], ""))
            review_cards.append(
                {
                    "title": doc.title,
                    "path": relative_path(doc.path),
                    "weight": doc.weight,
                    "status": doc.status,
                    "review_judgment": review_local_judgment(doc),
                    "personal_relevance": review_personal_connection(doc),
                    "external_check": synthesize_review_external_check(doc.title, results, note),
                    "next_step": review_next_step(doc),
                    "sources": source_cards(results),
                }
            )
        brief["review_cards"] = review_cards
        brief["watchlist_checks"] = [
            external_progress_summary(keyword, results, note)
            for keyword, (results, note) in searches.items()
        ]
        brief["idea_seeds"] = [
            seed_review_item(seed)
            for seed in dormant_seeds(docs, int(config.get("max_idea_seeds_per_report", 3)))
        ]
        if not review_cards:
            brief["quality_risks"].append("无新输入日没有可复盘的高权重节点。")

    if not sources_json.get("text_sources") and config.get("enable_web_search"):
        brief["quality_risks"].append("启用联网搜索但没有可用外部来源。")
    return brief


def build_report(
    date_text: str,
    records: list[InputRecord],
    docs: list[LocalDoc],
    searches: dict[str, tuple[list[dict[str, str]], str]],
    image: dict[str, str] | None,
    config: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    is_review = not records
    title = f"个人认知雷达报告 {date_text}"
    sources_json: dict[str, Any] = {
        "date": date_text,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "enable_web_search": bool(config.get("enable_web_search")),
            "enable_images": bool(config.get("enable_images")),
            "enable_ai_generated_images": bool(config.get("enable_ai_generated_images")),
            "latex_engine": config.get("latex_engine", "xelatex"),
        },
        "text_sources": [],
        "image_sources": [],
        "search_notes": [],
    }
    enriched_searches: dict[str, tuple[list[dict[str, str]], str]] = {}
    source_counter = 1
    for keyword, (results, note) in searches.items():
        enriched_results: list[dict[str, str]] = []
        if note:
            sources_json["search_notes"].append({"keyword": keyword, "note": note})
        for result in results:
            enriched = {**result, "id": f"S{source_counter}"}
            source_counter += 1
            enriched_results.append(enriched)
            row = {
                "keyword": keyword,
                **enriched,
                "used_for": "外部核实、趋势判断或背景线索；报告正文只使用综合判断，不直接粘贴搜索片段。",
                "accessed_at": datetime.now().date().isoformat(),
            }
            sources_json["text_sources"].append(row)
        enriched_searches[keyword] = (enriched_results, note)
    searches = enriched_searches
    if image:
        sources_json["image_sources"].append(image)
    tracking_topics = read_tracking_topics()
    report_brief = build_report_brief(date_text, records, docs, searches, sources_json, config)

    lines: list[str] = [
        r"\documentclass[UTF8,zihao=-4]{ctexart}",
        r"\usepackage[a4paper,margin=2.2cm]{geometry}",
        r"\usepackage{graphicx}",
        r"\usepackage{xcolor}",
        r"\usepackage{hyperref}",
        r"\usepackage{enumitem}",
        r"\usepackage{longtable}",
        r"\hypersetup{colorlinks=true,linkcolor=teal,urlcolor=teal}",
        r"\setlist{itemsep=0.25em,topsep=0.35em}",
        r"\title{\bfseries " + tex_escape(title) + "}",
        r"\author{点子发芽}",
        r"\date{" + tex_escape(date_text) + "}",
        r"\begin{document}",
        r"\maketitle",
        r"\section*{今日主线}",
        tex_escape(infer_mainline(records, docs)),
    ]

    if image:
        lines.extend(
            [
                r"\begin{figure}[htbp]",
                r"\centering",
                r"\includegraphics[width=0.82\linewidth]{" + image.get("tex_path", "assets/cognitive-radar.png") + "}",
                r"\caption{" + tex_escape(image["caption"]) + "}",
                r"\end{figure}",
            ]
        )

    lines.append(r"\section*{今日输入}")
    if records:
        rows = [
            f"{record.keyword}；补充信息：{record.context or '未填写'}；权重：{record.weight or '未填写'}"
            for record in records
        ]
        lines.append(tex_items(rows))
    else:
        lines.append("今日没有新的网页输入，进入无新输入复盘模式。")
        lines.extend(
            [
                r"\section*{无新输入复盘模式}",
                r"\subsection*{今日状态}",
                tex_escape("今天没有新关键词输入，适合回看已有主题：哪些还值得放在手边，哪些可以先放轻一点。"),
                r"\subsection*{高权重节点复盘}",
                tex_items(
                    [
                        f"{doc.title}：权重 {doc.weight or '未标注'}，状态 {doc.status or '未标注'}；今日检查它是否仍能让系统更省心。"
                        for doc in high_weight_docs(docs)[:4]
                    ]
                    or ["当前没有标注为高权重的节点。"]
                ),
                r"\subsection*{沉睡点子回顾}",
                tex_items(
                    [
                        seed_review_item(seed)
                        for seed in dormant_seeds(docs, int(config.get("max_idea_seeds_per_report", 3)))
                    ]
                    or ["当前没有可回顾的沉睡点子。"]
                ),
                r"\subsection*{watchlist 新进展检查}",
                tex_items(
                    [
                        external_progress_summary(keyword, results, note)
                        for keyword, (results, note) in searches.items()
                    ]
                    or ["当前没有 watchlist 查询项。"]
                ),
            ]
        )

    lines.append(r"\section*{今日新知}")
    if records:
        for record in records:
            results, note = searches.get(record.keyword, ([], ""))
            connected = related_docs(record, docs)
            lines.extend(
                [
                    r"\subsection*{" + tex_escape(record.keyword) + "}",
                    tex_items(
                        [f"简介：{concept_sentence(record)}"]
                        + [f"近期线索：{finding}" for finding in synthesize_findings(record, results, note)]
                        + [
                            "与我相关：" + item
                            for item in local_connection_analysis(record, docs, tracking_topics)
                        ]
                        + [
                            f"今天先这样看：{judgment_for(record, results, connected)}",
                            f"最小下一步：{next_step_for(record)}",
                        ]
                    ),
                ]
            )
    else:
        high_docs = high_weight_docs(docs)
        if high_docs:
            for doc in high_docs[:4]:
                results, note = searches.get(doc.title, ([], ""))
                external_summary = synthesize_review_external_check(doc.title, results, note)
                lines.extend(
                    [
                        r"\subsection*{" + tex_escape(doc.title) + "}",
                        tex_items(
                            [
                                f"今日状态：权重 {doc.weight or '未标注'}，状态 {doc.status or '未标注'}。",
                                f"今天先这样看：{review_local_judgment(doc)}",
                                f"与我相关：{review_personal_connection(doc)}",
                                f"外部检查：{external_summary}",
                                f"最小下一步：{review_next_step(doc)}",
                            ]
                        ),
                    ]
                )
        else:
            lines.append("没有可复盘的高权重节点。")

    lines.append(r"\section*{与旧知识的连接}")
    connection_items: list[str] = connection_items_for_records(records, docs, tracking_topics)
    if not connection_items and not records:
        for doc in high_weight_docs(docs)[:3]:
            connection_items.append(
                f"{doc.title}：{review_personal_connection(doc)}明天可以用一次真实输入测试它是否省心。"
            )
    lines.append(
        tex_items(
            connection_items
            or ["今天还没有足够明确的旧记录可放在一起看。先补一个项目、机会、人名或来源，再考虑建节点。"]
        )
    )

    lines.append(r"\section*{今日发芽点子}")
    idea_items: list[str]
    if records:
        idea_items = idea_seed_items(records, docs, searches, int(config.get("max_idea_seeds_per_report", 3)))
    else:
        idea_items = []
        for seed in dormant_seeds(docs, int(config.get("max_idea_seeds_per_report", 3))):
            idea_items.append(seed_review_item(seed))
    lines.append(tex_items(idea_items or ["今日暂无明确发芽点子。输入之间还没有形成足够清晰的行动组合。"]))

    lines.append(r"\section*{权重变化}")
    weight_items: list[str] = []
    if records:
        for record in records:
            suggested = "3" if related_docs(record, docs) else "2"
            if record.weight in {"4", "5"}:
                suggested = record.weight
            weight_items.append(
                f"{record.keyword}：当前权重 {record.weight or '未填写'}，建议观察权重 {suggested}；理由是{judgment_for(record, searches.get(record.keyword, ([], ''))[0], related_docs(record, docs))}"
            )
    else:
        for doc in high_weight_docs(docs)[:4]:
            weight_items.append(f"{doc.title}：保持权重 {doc.weight or '未标注'}；无新输入日不自动升降权。")
    lines.append(tex_items(weight_items or ["无权重变化建议。"]))

    lines.append(r"\section*{外部新进展}")
    external_items: list[str] = []
    for keyword, (results, note) in searches.items():
        external_items.append(external_progress_summary(keyword, results, note))
    lines.append(tex_items(external_items or ["当前没有执行联网搜索，未能核实外部新进展。"]))

    lines.append(r"\section*{明日一问}")
    if records:
        question = f"今天这些输入里，哪一个最可能在 7 天后仍然值得你主动追问，为什么？"
    else:
        question = "如果明天只能恢复一个长期主题的推进，你会选择哪个节点，并用什么证据证明它还值得？"
    lines.append(tex_escape(question))

    lines.append(r"\section*{来源说明}")
    if sources_json["text_sources"]:
        lines.append(r"\begin{itemize}[leftmargin=2em]")
        for source in sources_json["text_sources"]:
            lines.append(source_line(source))
        lines.append(r"\end{itemize}")
    else:
        lines.append(tex_items(["当前环境无法联网或未启用联网搜索，未能核实外部新进展。"]))
    if image:
        lines.append(tex_items([f"图片：{image['caption']}；来源：{image['source']}"]))
    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n", sources_json, report_brief


BANNED_REPORT_PHRASES = [
    "暂未发现强相关旧节点",
    "候选来源包括",
    "重点来源包括",
    "继续关注",
    "深入研究",
    "据资料显示",
    "一个来自今日输入的观察对象",
    "股票市场复盘",
    "A股三大指数",
    "打板网",
    "选股通",
]


def report_body_before_sources(tex: str) -> str:
    marker = r"\section*{来源说明}"
    return tex.split(marker, 1)[0]


def source_text_reused_in_body(tex: str, sources_json: dict[str, Any]) -> list[str]:
    body = report_body_before_sources(tex)
    reused: list[str] = []
    for source in sources_json.get("text_sources", []):
        keyword = clean_source_text(str(source.get("keyword", "")))
        title = clean_source_text(str(source.get("title", "")))
        snippet = clean_source_text(str(source.get("snippet", "")))
        if title and len(title) >= 14 and title != keyword and title in body:
            reused.append(f"标题被直接放入正文：{title[:40]}")
        if snippet and len(snippet) >= 40:
            probe = snippet[:42]
            if probe in body:
                reused.append(f"摘要片段被直接放入正文：{probe[:40]}")
    return reused[:5]


def quality_check(
    tex: str,
    records: list[InputRecord],
    sources_json: dict[str, Any],
    report_brief: dict[str, Any],
) -> dict[str, Any]:
    issues: list[str] = []
    required_sections = [
        "今日主线",
        "今日输入",
        "今日新知",
        "与旧知识的连接",
        "今日发芽点子",
        "权重变化",
        "外部新进展",
        "明日一问",
        "来源说明",
    ]
    for section in required_sections:
        if section not in tex:
            issues.append(f"缺少章节：{section}")

    for phrase in BANNED_REPORT_PHRASES:
        if phrase in tex:
            issues.append(f"出现坏味道短语：{phrase}")

    for reused in source_text_reused_in_body(tex, sources_json):
        issues.append(reused)

    subsections = re.findall(r"\\subsection\*\{([^}]+)\}", tex)
    duplicate_subsections = sorted({title for title in subsections if subsections.count(title) > 1})
    if duplicate_subsections:
        issues.append(f"出现重复小节：{', '.join(duplicate_subsections[:3])}")

    if records:
        cards = report_brief.get("keyword_cards", [])
        if len(cards) != len(records):
            issues.append("report_brief 中关键词卡片数量与输入不一致。")
        for record in records:
            if record.keyword not in tex:
                issues.append(f"缺少关键词卡片：{record.keyword}")
        for card in cards:
            if not card.get("findings") or len(card.get("findings", [])) < 2:
                issues.append(f"report_brief 中 {card.get('keyword', '未知关键词')} 缺少综合发现。")
            if not card.get("personal_relevance"):
                issues.append(f"report_brief 中 {card.get('keyword', '未知关键词')} 缺少个人关联。")
            if not card.get("judgment") or not card.get("next_step"):
                issues.append(f"report_brief 中 {card.get('keyword', '未知关键词')} 缺少判断或下一步。")
        expected_count = len(records)
        if tex.count("它是什么：") < expected_count:
            issues.append("并非每个关键词都有“它是什么”。")
        if tex.count("今天查到了什么：") < expected_count:
            issues.append("并非每个关键词都有“今天查到了什么”。")
        if tex.count("和我有什么关系：") < expected_count:
            issues.append("并非每个关键词都有“和我有什么关系”。")
        if tex.count("今日判断：") < expected_count:
            issues.append("并非每个关键词都有“今日判断”。")
        if tex.count("最小下一步：") < expected_count:
            issues.append("并非每个关键词都有具体“最小下一步”。")
    elif "无新输入复盘模式" not in tex:
        issues.append("无新输入日没有进入复盘模式。")
    else:
        review_cards = report_brief.get("review_cards", [])
        if not review_cards:
            issues.append("无新输入日 report_brief 没有复盘卡片。")
        for card in review_cards:
            if not card.get("personal_relevance") or not card.get("next_step"):
                issues.append(f"复盘卡片 {card.get('title', '未知节点')} 缺少个人关联或下一步。")

    if sources_json["config"].get("enable_web_search") and not sources_json["text_sources"] and not sources_json["search_notes"]:
        issues.append("启用联网搜索时，sources.json 没有文字来源或失败说明。")

    scanned_paths = report_brief.get("local_knowledge_scan", {}).get("scanned_paths", [])
    if not scanned_paths:
        issues.append("report_brief 没有记录本地旧知识扫描范围。")
    if not report_brief.get("reader_promise"):
        issues.append("report_brief 缺少读者承诺。")

    for source in sources_json["text_sources"]:
        if not source.get("quality_tier") or not source.get("id"):
            issues.append("sources.json 中有来源缺少分层或 id。")
            break

    return {
        "passed": not issues,
        "issues": issues,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def input_record_row(record: InputRecord) -> dict[str, str]:
    return {"keyword": record.keyword, "supplemental_info": record.context, "weight": record.weight}


def free_note_row(note: FreeNote) -> dict[str, str]:
    return {
        "id": note.id,
        "text": note.text,
        "created_at": note.created_at,
        "source": note.source,
    }


def local_doc_card(doc: LocalDoc, max_chars: int = 420) -> dict[str, str]:
    text = normalize_space(re.sub(r"^# .+$", "", doc.text, flags=re.MULTILINE))
    return {
        "title": doc.title,
        "path": relative_path(doc.path),
        "weight": doc.weight,
        "status": doc.status,
        "excerpt": text[:max_chars],
    }


def enrich_searches_for_report(
    searches: dict[str, tuple[list[dict[str, str]], str]],
    config: dict[str, Any],
) -> tuple[dict[str, tuple[list[dict[str, str]], str]], dict[str, Any]]:
    sources_json: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "enable_web_search": bool(config.get("enable_web_search")),
            "latex_engine": config.get("latex_engine", "xelatex"),
        },
        "text_sources": [],
        "search_notes": [],
    }
    enriched_searches: dict[str, tuple[list[dict[str, str]], str]] = {}
    source_counter = 1
    for keyword, (results, note) in searches.items():
        enriched_results: list[dict[str, str]] = []
        if note:
            sources_json["search_notes"].append({"keyword": keyword, "note": note})
        for result in results:
            enriched = {**result, "id": f"S{source_counter}"}
            source_counter += 1
            enriched_results.append(enriched)
            sources_json["text_sources"].append(
                {
                    "keyword": keyword,
                    **enriched,
                    "used_for": "外部核实和写作参考；正文必须由大模型综合改写，不直接粘贴搜索片段。",
                    "accessed_at": datetime.now().date().isoformat(),
                }
            )
        enriched_searches[keyword] = (enriched_results, note)
    return enriched_searches, sources_json


def build_report_context(
    date_text: str,
    records: list[InputRecord],
    free_notes: list[FreeNote],
    entry_counts_by_kind: dict[str, int],
    input_source_format: str,
    docs: list[LocalDoc],
    searches: dict[str, tuple[list[dict[str, str]], str]],
    sources_json: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    secretary_context = build_secretary_context(date_text)
    keyword_contexts: list[dict[str, Any]] = []
    for record in records:
        results, note = searches.get(record.keyword, ([], ""))
        keyword_contexts.append(
            {
                "keyword": record.keyword,
                "supplemental_info": record.context,
                "weight": record.weight,
                "questions_for_model": [
                    f"{record.keyword} 是什么？请用 200-300 字解释清楚，只专注关键词本身。",
                    f"{record.keyword} 最近发生了什么相关事情？请结合来源写 2-4 个具体线索，没有可靠新进展就直接说明。",
                    f"结合补充信息“{record.context}”，这个关键词与我的记录有什么具体联系？",
                ],
                "search_note": note,
                "search_results": source_cards(results),
                "local_matches": [local_doc_card(doc) for doc in related_docs(record, docs)],
            }
        )

    return {
        "date": date_text,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "input" if records else "review",
        "report_structure": [
            "今日总结",
            "今日输入",
            "今日新知",
            "与旧知识的链接",
            "今日发芽点子",
            "参考搜索内容",
            "随心记复盘（仅在当天有随心记时追加到最后）",
        ],
        "writing_contract": {
            "model_role": "你是报告作者。请综合材料写 report_brief.json，不要把搜索结果直接拼进正文。",
            "keyword_card_shape": ["简介", "最近有什么相关的事情", "与我相关", "最小下一步"],
            "hard_limits": [
                "每个关键词只能有一个简介、一个最近有什么相关的事情、一个与我相关、一个最小下一步。",
                "简介约 200-300 字，只解释关键词本身，不要联系补充信息。",
                "与我相关直接说明它会影响用户怎么选、怎么问、怎么做，少写抽象的“联系”。",
                "最近相关事情写成 2-4 个具体线索：有搜索材料时说明谁在做什么、出现了什么活动/项目/论文/讨论、和关键词有什么关系，不能只列来源编号；最小下一步只能 1 个动作。",
                "禁止使用旧标签：它是什么、今天查到了什么、和我有什么关系、今日判断。",
                "随心记只用于最后的随心记复盘，不得影响今日总结、今日输入、今日新知、旧知识链接和今日发芽点子。",
            ],
            "voice_contract": {
                "rules_path": relative_path(VOICE_RULES_PATH),
                "target_voice": "私人秘书式晚间简报：自然、具体、克制、有温度。",
                "principle": "分析过程留在幕后，正文只写读者愿意看到的结果。",
                "banned_meta_phrases": [
                    "本地旧节点显示",
                    "可连接",
                    "它说明这个关键词",
                    "连接价值是",
                    "当前是弱连接，理由是",
                    "低成本观察",
                    "脚本 fallback",
                    "由 Codex automation",
                ],
                "style_limits": {"不是/而是": 1, "只是": 2, "更像": 2, "判断": 3},
                "rules_excerpt": compact_text(read_report_voice_rules(), 1200),
            },
            "free_note_review_shape": {
                "themes": "1-4 个短主题，概括随心记里反复出现的关注点。",
                "discussion": "温和讨论这些想法或感受背后的关注点、动机或张力。",
                "evaluation": "给一个轻量结论：先当作心情、长期信号、待观察线索，还是以后可能变成关键词或点子。",
                "question_for_tomorrow": "一个不催促、但能继续理解自己的问题。",
            },
        },
        "input_source": {
            "format": input_source_format,
            "entry_counts_by_kind": entry_counts_by_kind,
        },
        "secretary_context": secretary_context,
        "inputs": [input_record_row(record) for record in records],
        "free_notes": [free_note_row(note) for note in free_notes],
        "keyword_contexts": keyword_contexts,
        "local_knowledge": {
            "tracking_topics": read_tracking_topics(),
            "scanned_paths": sorted(relative_path(doc.path) for doc in docs),
            "high_weight_nodes": [local_doc_card(doc) for doc in high_weight_docs(docs)[:4]],
            "idea_seeds": [local_doc_card(seed) for seed in dormant_seeds(docs, int(config.get("max_idea_seeds_per_report", 3)))],
        },
        "source_policy": {
            "priority": ["官方/机构资料", "论文/报告", "权威媒体", "项目主页/机构主页", "普通线索", "低优先级线索"],
            "tier_counts": source_tier_counts(sources_json),
            "low_quality_rule": "低质量来源只能作为入口线索，不能作为核心结论。",
        },
        "reference_sources": [
            {
                "id": source.get("id", ""),
                "keyword": source.get("keyword", ""),
                "title": source.get("title", ""),
                "quality_tier": source.get("quality_tier", ""),
                "url": source.get("url", ""),
            }
            for source in sources_json.get("text_sources", [])
        ],
    }


def sentence_count(text: str) -> int:
    return len([part for part in re.split(r"[。！？!?；;]\s*", text) if part.strip()])


def fallback_free_note_review(free_notes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not free_notes:
        return None
    snippets = [normalize_space(note.get("text", "")) for note in free_notes if normalize_space(note.get("text", ""))]
    if not snippets:
        return None
    preview = "；".join(compact_text(text, 80) for text in snippets[:3])
    return {
        "themes": ["随手记录的想法和感受", "可能值得继续观察的个人信号"],
        "discussion": f"今天的随心记先放在个人状态里看。里面有这些片段：{preview}。它们帮你保留当下真实冒出来的注意力，暂时不用急着变成任务。",
        "evaluation": "可以先当作一组待观察线索。有些可能是当天心情，有些过几天还会冒出来；等同类想法多出现几次，再决定是否转成关键词或点子种子。",
        "question_for_tomorrow": "明天回看时，哪一句随心记仍然让你觉得有点在意？",
    }


def fallback_recent_news_summary(keyword: str, search_results: list[dict[str, Any]]) -> str:
    refs = " ".join(f"[{source.get('id')}]" for source in search_results[:3] if source.get("id"))
    if not refs:
        return "未查到可靠近期新进展；本条先按你的补充信息保留。"
    channels = sorted({str(source.get("search_channel") or source.get("quality_tier") or "公开来源") for source in search_results[:5]})
    channel_text = "、".join(channel for channel in channels if channel) or "公开来源"
    combined_text = " ".join(
        clean_source_text(f"{source.get('title', '')} {source.get('snippet', '')}")
        for source in search_results[:6]
    )
    signals: list[str] = []
    signal_rules = [
        ("活动/线下局", ["活动", "线下", "Social", "Night", "沙龙", "meetup"]),
        ("报名或招募", ["报名", "招募", "志愿者", "选手", "倒计时"]),
        ("社区周刊/回顾", ["周刊", "回顾", "社区", "近期活动"]),
        ("创业或项目讨论", ["创业", "创投", "项目", "路演", "硬件玩家"]),
        ("学术或研究线索", ["论文", "研究", "学术", "期刊", "报告"]),
    ]
    lowered = combined_text.lower()
    for label, words in signal_rules:
        if any(word.lower() in lowered for word in words):
            signals.append(label)
    dates = sorted(set(re.findall(r"20\d{2}[-年./]\d{1,2}(?:[-月./]\d{1,2})?", combined_text)))[:3]
    signal_text = "、".join(signals[:3]) if signals else "公开讨论和相关资料"
    date_text = f"材料里的时间线索集中在 {'、'.join(dates)} 附近。" if dates else "材料没有给出很清楚的近期日期，先按这次检索到的近线索处理。"
    source_count = min(len(search_results), 6)
    return (
        f"这次检索到 {source_count} 条可用线索，主要来自 {channel_text}。"
        f"它们指向几件相关事情：{keyword} 最近出现在{signal_text}里，说明这个词已经有可观察的现实场景；"
        f"{date_text}"
        "这些线索能帮你区分它是一次活动热度、一个项目入口，还是正在形成的研究或产品方向。"
        f"从你的使用角度看，重点是判断它现在更靠近活动、人群、项目机会、创业讨论还是研究资料。"
        f"目前证据强度还不完全一致，后续需要补官网、主办方、论文原文或项目主页来确认关键事实 {refs}。"
    )


def fallback_brief_from_context(context: dict[str, Any]) -> dict[str, Any]:
    cards: list[dict[str, str]] = []
    for item in context.get("keyword_contexts", []):
        record = InputRecord(
            keyword=str(item.get("keyword", "")),
            context=str(item.get("supplemental_info", item.get("context", "未填写"))),
            weight=str(item.get("weight", "未填写")),
        )
        intro = concept_sentence(record)
        if len(intro) < 180:
            intro = (
                f"{intro} 回看这类词时，先弄清它的基本含义、所属领域、常见使用场景和可靠来源。"
                "再把它放回今天的补充信息里，看它是否能带来一个问题、一个人、一个项目或一次小行动。"
                "如果暂时还做不到，就保留一条简短记录，不急着建成完整知识节点。"
            )
        recent_news = fallback_recent_news_summary(record.keyword, item.get("search_results", []))
        cards.append(
            {
                "keyword": record.keyword,
                "intro": intro,
                "recent_news": recent_news,
                "relevance": f"你记录它的原因是：{record.context or '未填写'}。明天可以先看它更贴近学习、项目、机会、人脉还是商业观察，再决定要不要继续追。",
                "next_step": next_step_for(record),
            }
        )

    if not cards:
        for node in context.get("local_knowledge", {}).get("high_weight_nodes", [])[:3]:
            title = str(node.get("title", "未命名节点"))
            cards.append(
                {
                    "keyword": title,
                    "intro": (
                        f"{title} 是当前本地知识库里的高权重节点。今天没有新的关键词输入，可以把它当作一条长期线索回看："
                        "它最近还在帮你解释学习、项目、机会选择或系统搭建需求吗？如果还在，就给它安排一次小测试；"
                        "如果已经变轻，就让它安静留在库里，等新的记录再把它带回来。"
                    ),
                    "recent_news": "无新输入日只做轻量外部检查；如果没有明确新证据，不自动扩写为新闻摘要。",
                    "relevance": "今天没有新的补充信息，因此只检查它是否仍然服务当前工作流，不强行建立新关联。",
                    "next_step": "明天通过网页提交 3 个真实关键词，再检查日报是否能给出清晰总结。",
                }
            )

    brief = {
        "date": context.get("date", ""),
        "mode": context.get("mode", "input"),
        "summary": "今天先生成一份保底简报：把现有输入整理成简介、近期线索、与你的关系和一个小动作。后续如果有更完整的材料，再把正文润得更贴近你的阅读习惯。",
        "inputs": context.get("inputs", []),
        "knowledge_cards": cards,
        "old_knowledge_links": [],
        "idea_seeds": [],
        "reference_sources": context.get("reference_sources", []),
    }
    free_note_review = fallback_free_note_review(context.get("free_notes", []))
    if free_note_review:
        brief["free_note_review"] = free_note_review
    secretary = context.get("secretary_context", {})
    open_tasks = secretary.get("tasks", {}).get("open_or_due_soon", []) if isinstance(secretary, dict) else []
    pending_review = secretary.get("review_queue", {}).get("pending_items", []) if isinstance(secretary, dict) else []
    if open_tasks:
        brief["task_followups"] = [
            f"{task.get('title', task.get('task_id', '未命名任务'))}；截止：{task.get('due_date') or '未设定'}"
            for task in open_tasks[:5]
        ]
    if pending_review:
        brief["secretary_reminders"] = [
            f"有 {len(pending_review)} 条待确认项，优先处理记忆候选和任务候选。"
        ]
    return brief


def brief_reference_sources(brief: dict[str, Any], sources_json: dict[str, Any]) -> list[dict[str, str]]:
    references = brief.get("reference_sources")
    if isinstance(references, list) and references:
        return [source for source in references if isinstance(source, dict)]
    return [
        {
            "id": source.get("id", ""),
            "title": source.get("title", ""),
            "quality_tier": source.get("quality_tier", ""),
            "url": source.get("url", ""),
        }
        for source in sources_json.get("text_sources", [])
    ]


def normalize_candidate_items(value: Any, default_kind: str, date_text: str) -> list[dict[str, Any]]:
    if not value:
        return []
    raw_items = value if isinstance(value, list) else [value]
    items: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if isinstance(raw_item, dict):
            payload = raw_item.copy()
            text = str(payload.get("text") or payload.get("title") or payload.get("summary") or "").strip()
            if not text and default_kind == "weight_change_candidate":
                target = payload.get("target") or payload.get("keyword") or payload.get("node_title") or payload.get("path") or "未命名对象"
                current_weight = payload.get("current_weight") or payload.get("from_weight") or "?"
                suggested_weight = payload.get("suggested_weight") or payload.get("new_weight") or payload.get("to_weight") or "?"
                reason = payload.get("reason") or payload.get("evidence") or ""
                text = f"{target}: 建议权重 {current_weight} -> {suggested_weight}" + (f"；理由：{reason}" if reason else "")
        else:
            text = str(raw_item).strip()
            payload = {"text": text}
        if not text:
            continue
        kind = str(payload.pop("kind", default_kind))
        candidate_id = stable_id(date_text, kind, text, prefix="candidate")
        items.append(
            {
                "schema_version": 1,
                "id": candidate_id,
                "date": date_text,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "source": "daily_report_brief",
                "kind": kind,
                "payload": {
                    **payload,
                    "text": text,
                    "source_report": f"synthesis/daily_reports/{date_text}/report_brief.json",
                },
            }
        )
    return items


def sync_review_queue_from_brief(date_text: str, brief: dict[str, Any]) -> None:
    queue_path = REVIEW_QUEUE_DIR / f"{date_text}.jsonl"
    existing_ids = {
        str(row.get("id"))
        for row in (json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines() if line.strip())
        if isinstance(row, dict)
    } if queue_path.exists() else set()
    candidates: list[dict[str, Any]] = []
    candidates.extend(normalize_candidate_items(brief.get("memory_candidates"), "memory_candidate", date_text))
    candidates.extend(normalize_candidate_items(brief.get("task_candidates"), "task_candidate", date_text))
    candidates.extend(normalize_candidate_items(brief.get("knowledge_candidates"), "knowledge_candidate", date_text))
    candidates.extend(normalize_candidate_items(brief.get("idea_seed_candidates"), "idea_seed_candidate", date_text))
    candidates.extend(normalize_candidate_items(brief.get("weight_change_candidates"), "weight_change_candidate", date_text))
    for candidate in candidates:
        if candidate["id"] not in existing_ids:
            append_structured_jsonl(queue_path, candidate)
            existing_ids.add(candidate["id"])


def tex_labeled_paragraph(label: str, text: str) -> str:
    return "\\paragraph{" + tex_escape(label) + "} " + tex_escape(text or "未填写。")


def tex_input_table(inputs: list[dict[str, Any]]) -> str:
    if not inputs:
        return tex_escape("今日没有新的网页输入。")
    lines = [
        r"\begin{longtable}{|p{0.22\linewidth}|p{0.58\linewidth}|p{0.10\linewidth}|}",
        r"\hline",
        r"\textbf{关键词} & \textbf{补充信息} & \textbf{权重} \\",
        r"\hline",
    ]
    for item in inputs:
        keyword = tex_escape(str(item.get("keyword", "")))
        supplemental_info = tex_escape(str(item.get("supplemental_info", item.get("context", "未填写")) or "未填写"))
        weight = tex_escape(str(item.get("weight", "3") or "3"))
        lines.append(f"{keyword} & {supplemental_info} & {weight} \\\\")
        lines.append(r"\hline")
    lines.append(r"\end{longtable}")
    return "\n".join(lines)


def tex_free_note_review(review: Any) -> str:
    if not isinstance(review, dict):
        return ""
    lines: list[str] = []
    themes = [str(item) for item in review.get("themes", []) if str(item).strip()]
    if themes:
        lines.append(r"\paragraph{主题}")
        lines.append(tex_items(themes[:4]))
    discussion = str(review.get("discussion") or "").strip()
    if discussion:
        lines.append(tex_labeled_paragraph("讨论", discussion))
    evaluation = str(review.get("evaluation") or "").strip()
    if evaluation:
        lines.append(tex_labeled_paragraph("评价", evaluation))
    question = str(review.get("question_for_tomorrow") or "").strip()
    if question:
        lines.append(tex_labeled_paragraph("留给明天的问题", question))
    return "\n".join(lines)


def tex_text_items(value: Any, limit: int = 5) -> str:
    if not value:
        return ""
    if isinstance(value, dict):
        items = []
        for key, item_value in value.items():
            if isinstance(item_value, list):
                for child in item_value:
                    if str(child).strip():
                        items.append(f"{key}: {child}")
            elif str(item_value).strip():
                items.append(f"{key}: {item_value}")
    elif isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("title") or item.get("summary") or json.dumps(item, ensure_ascii=False)
            else:
                text = item
            if str(text).strip():
                items.append(str(text).strip())
    else:
        items = [str(value).strip()]
    items = [item for item in items if item][:limit]
    return tex_items(items) if items else ""


def render_report_from_brief(date_text: str, brief: dict[str, Any], sources_json: dict[str, Any]) -> str:
    title = f"点子发芽日报 {date_text}"
    inputs = brief.get("inputs") or []
    cards = brief.get("knowledge_cards") or []
    old_links = brief.get("old_knowledge_links") or []
    idea_seeds = brief.get("idea_seeds") or []
    free_note_review = brief.get("free_note_review")
    references = brief_reference_sources(brief, sources_json)
    lines: list[str] = [
        r"\documentclass[UTF8,zihao=-4]{ctexart}",
        r"\usepackage[a4paper,margin=2.2cm]{geometry}",
        r"\usepackage{xcolor}",
        r"\usepackage{hyperref}",
        r"\usepackage{enumitem}",
        r"\usepackage{longtable}",
        r"\usepackage{array}",
        r"\hypersetup{colorlinks=true,linkcolor=teal,urlcolor=teal}",
        r"\setlist{itemsep=0.25em,topsep=0.35em}",
        r"\title{\bfseries " + tex_escape(title) + "}",
        r"\author{点子发芽}",
        r"\date{" + tex_escape(date_text) + "}",
        r"\begin{document}",
        r"\maketitle",
        r"\section*{今日总结}",
        tex_escape(str(brief.get("summary") or "今天没有可用总结。")),
        r"\section*{今日输入}",
    ]
    lines.append(tex_input_table(inputs))

    lines.append(r"\section*{今日新知}")
    if cards:
        for card in cards:
            lines.append(r"\subsection*{" + tex_escape(str(card.get("keyword") or "未命名关键词")) + "}")
            lines.append(tex_labeled_paragraph("简介", str(card.get("intro") or "")))
            lines.append(tex_labeled_paragraph("最近有什么相关的事情", str(card.get("recent_news") or "")))
            lines.append(tex_labeled_paragraph("与我相关", str(card.get("relevance") or "")))
            lines.append(tex_labeled_paragraph("最小下一步", str(card.get("next_step") or "")))
    else:
        lines.append(tex_escape("今日没有可写入的新知卡片。"))

    lines.append(r"\section*{与旧知识的链接}")
    lines.append(tex_items([str(item) for item in old_links][:3] or ["今日没有足够强的旧知识链接。"]))
    lines.append(r"\section*{今日发芽点子}")
    lines.append(tex_items([str(item) for item in idea_seeds][:2] or ["今日暂无值得保留的新点子。"]))
    lines.append(r"\section*{参考搜索内容}")
    if references:
        lines.append(r"\begin{itemize}[leftmargin=2em]")
        for source in references:
            source_id = source.get("id", "")
            tier = source.get("quality_tier", "未分层")
            title_text = source.get("title", "未命名来源")
            url = source.get("url", "")
            label = tex_escape(f"[{source_id}] {tier}：{title_text}")
            lines.append(rf"\item \href{{{url}}}{{{label}}}" if url else r"\item " + label)
        lines.append(r"\end{itemize}")
    else:
        lines.append(tex_items(["当前没有可列出的参考搜索内容。"]))
    free_note_tex = tex_free_note_review(free_note_review)
    if free_note_tex:
        lines.append(r"\section*{随心记复盘}")
        lines.append(free_note_tex)
    memory_candidate_tex = tex_text_items(brief.get("memory_candidates"), limit=4)
    if memory_candidate_tex:
        lines.append(r"\section*{个人记忆候选}")
        lines.append(memory_candidate_tex)
    weight_candidate_tex = tex_text_items(brief.get("weight_change_candidates"), limit=4)
    if weight_candidate_tex:
        lines.append(r"\section*{权重调整候选}")
        lines.append(weight_candidate_tex)
    task_followup_tex = tex_text_items(brief.get("task_followups"), limit=6)
    if task_followup_tex:
        lines.append(r"\section*{任务与跟进}")
        lines.append(task_followup_tex)
    secretary_reminder_tex = tex_text_items(brief.get("secretary_reminders"), limit=3)
    if secretary_reminder_tex:
        lines.append(r"\section*{明日秘书提醒}")
        lines.append(secretary_reminder_tex)
    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


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


def report_body_before_reference(tex: str) -> str:
    return tex.split(r"\section*{参考搜索内容}", 1)[0]


def source_text_reused_in_new_body(tex: str, sources_json: dict[str, Any]) -> list[str]:
    body = report_body_before_reference(tex)
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
    for field in ["old_knowledge_links", "idea_seeds", "task_followups", "secretary_reminders"]:
        value = brief.get(field)
        if isinstance(value, list):
            chunks.extend(str(item) for item in value if str(item).strip())
        elif value:
            chunks.append(str(value))
    review = brief.get("free_note_review")
    if isinstance(review, dict):
        chunks.extend(str(item) for item in review.get("themes", []) if str(item).strip())
        for field in ["discussion", "evaluation", "question_for_tomorrow"]:
            chunks.append(str(review.get(field) or ""))
    for field in ["memory_candidates", "task_candidates", "knowledge_candidates", "weight_change_candidates", "idea_seed_candidates"]:
        chunks.extend(candidate_public_texts(brief.get(field)))
    return "\n".join(chunk for chunk in chunks if chunk.strip())


def style_phrase_count(label: str, text: str) -> int:
    if label == "不是/而是":
        return min(text.count("不是"), text.count("而是"))
    return text.count(label)


def voice_smell_check(tex: str, brief: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    visible_body = report_body_before_reference(tex)
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


def recent_news_quality_issues(brief: dict[str, Any], sources_json: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for card in brief.get("knowledge_cards", []):
        if not isinstance(card, dict):
            continue
        keyword = str(card.get("keyword") or "未知关键词")
        recent_news = str(card.get("recent_news") or "")
        compact = normalize_compact_for_quality(recent_news)
        has_sources = keyword_has_sources(keyword, sources_json) or bool(re.search(r"\[S\d+\]", recent_news))
        no_news = "未查到" in compact and "新进展" in compact
        if not has_sources or no_news:
            continue
        if len(compact) < MIN_RECENT_NEWS_CHARS_WITH_SOURCES:
            issues.append(
                f"{keyword} 的“{RECENT_NEWS_LABEL}”太短；有搜索来源时应写 2-4 个具体线索，正文至少约 {MIN_RECENT_NEWS_CHARS_WITH_SOURCES} 字。"
            )
        if not has_enough_recent_news_units(recent_news):
            issues.append(f"{keyword} 的“{RECENT_NEWS_LABEL}”信息密度不足；至少写 3 句，或 2 条以上具体线索，说明发生了什么、和关键词有什么关系。")
        if recent_news_is_source_only(recent_news):
            issues.append(f"{keyword} 的“{RECENT_NEWS_LABEL}”像来源清单；请写出材料共同说明的进展、活动或趋势。")
    return issues[:6]


def local_knowledge_dirs_have_content() -> bool:
    for base in [*KNOWLEDGE_DIRS, *SEED_DIRS]:
        if base.exists() and any(path.is_file() for path in base.rglob("*.md")):
            return True
    return False


def quality_check_simple(
    tex: str,
    records: list[InputRecord],
    sources_json: dict[str, Any],
    brief: dict[str, Any],
    context_scanned_paths: list[str] | None = None,
    free_note_count: int = 0,
) -> dict[str, Any]:
    issues: list[str] = []
    for section in ["今日总结", "今日输入", "今日新知", "与旧知识的链接", "今日发芽点子", "参考搜索内容"]:
        if section not in tex:
            issues.append(f"缺少章节：{section}")
    for phrase in NEW_BANNED_REPORT_PHRASES:
        if phrase in tex:
            issues.append(f"出现旧结构或空泛表达：{phrase}")
    issues.extend(source_text_reused_in_new_body(tex, sources_json))
    issues.extend(voice_smell_check(tex, brief))
    issues.extend(recent_news_quality_issues(brief, sources_json))

    cards = brief.get("knowledge_cards", [])
    if records and len(cards) != len(records):
        issues.append("knowledge_cards 数量必须与今日输入关键词数量一致。")
    if not cards:
        issues.append("report_brief 缺少 knowledge_cards。")
    for card in cards:
        keyword = str(card.get("keyword") or "未知关键词")
        intro = str(card.get("intro") or "")
        recent_news = str(card.get("recent_news") or "")
        relevance = str(card.get("relevance") or "")
        next_step = str(card.get("next_step") or "")
        if len(re.sub(r"\s+", "", intro)) < 180:
            issues.append(f"{keyword} 的简介过短，应接近 200-300 字。")
        if not recent_news:
            issues.append(f"{keyword} 缺少“{RECENT_NEWS_LABEL}”。")
        if not relevance:
            issues.append(f"{keyword} 缺少“与我相关”。")
        if not next_step:
            issues.append(f"{keyword} 缺少“最小下一步”。")
    expected = len(cards)
    if tex.count(r"\paragraph{简介}") != expected:
        issues.append("每个关键词必须恰好有一个“简介”。")
    if tex.count(rf"\paragraph{{{RECENT_NEWS_LABEL}}}") != expected:
        issues.append(f"每个关键词必须恰好有一个“{RECENT_NEWS_LABEL}”。")
    if tex.count(r"\paragraph{与我相关}") != expected:
        issues.append("每个关键词必须恰好有一个“与我相关”。")
    if tex.count(r"\paragraph{最小下一步}") != expected:
        issues.append("每个关键词必须恰好有一个“最小下一步”。")
    if not str(brief.get("summary") or "").strip():
        issues.append("report_brief 缺少今日总结。")
    if free_note_count:
        review = brief.get("free_note_review")
        if "随心记复盘" not in tex:
            issues.append("当天有随心记，但 PDF 缺少“随心记复盘”。")
        if not isinstance(review, dict):
            issues.append("当天有随心记，但 report_brief 缺少 free_note_review。")
        else:
            for field in ["discussion", "evaluation", "question_for_tomorrow"]:
                if not str(review.get(field) or "").strip():
                    issues.append(f"free_note_review 缺少 {field}。")
    elif "随心记复盘" in tex:
        issues.append("当天没有随心记，但 PDF 出现了“随心记复盘”。")
    if sources_json["config"].get("enable_web_search") and not sources_json["text_sources"] and not sources_json["search_notes"]:
        issues.append("启用联网搜索时，sources.json 没有文字来源或失败说明。")
    if local_knowledge_dirs_have_content() and not context_scanned_paths:
        issues.append("report_context.json.local_knowledge.scanned_paths 为空，但本地知识库目录已有内容。")
    for source in sources_json["text_sources"]:
        if not source.get("quality_tier") or not source.get("id"):
            issues.append("sources.json 中有来源缺少分层或 id。")
            break
    return {"passed": not issues, "issues": issues, "checked_at": datetime.now().isoformat(timespec="seconds")}


def compile_pdf(report_dir: Path, config: dict[str, Any]) -> tuple[bool, str]:
    engine = str(config.get("latex_engine", "xelatex"))
    latexmk = shutil.which("latexmk")
    if latexmk:
        if engine == "lualatex":
            cmd = [latexmk, "-lualatex", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", "report.tex"]
        else:
            cmd = [latexmk, "-xelatex", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", "report.tex"]
    else:
        engine_path = shutil.which(engine)
        if not engine_path:
            return False, f"未找到 {engine} 或 latexmk。请安装 TeX Live/MiKTeX，并确保命令在 PATH 中。"
        cmd = [engine_path, "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", "report.tex"]
    try:
        process = subprocess.run(
            cmd,
            cwd=report_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, "LaTeX 编译超时。report.tex 已保留。"
    (report_dir / "compile.out.log").write_text(process.stdout or "", encoding="utf-8")
    (report_dir / "compile.err.log").write_text(process.stderr or "", encoding="utf-8")
    if process.returncode != 0:
        tail = "\n".join((process.stdout + "\n" + process.stderr).splitlines()[-30:])
        return False, "LaTeX 编译失败，report.tex 已保留。日志尾部：\n" + tail
    pdf = report_dir / "report.pdf"
    if not pdf.exists():
        return False, "LaTeX 命令结束但没有生成 report.pdf。"
    return True, f"PDF generated: {pdf}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an Idea Sprout cognitive radar LaTeX/PDF report.")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--mode", choices=["auto", "input", "review"], default="")
    parser.add_argument("--no-web", action="store_true")
    parser.add_argument("--no-images", action="store_true")
    parser.add_argument("--no-compile", action="store_true")
    parser.add_argument("--collect-only", action="store_true", help="Only write report_context.json and sources.json.")
    parser.add_argument("--render-only", action="store_true", help="Render report.tex/PDF from an existing report_brief.json.")
    args = parser.parse_args()

    config = read_config()
    if args.no_web:
        config["enable_web_search"] = False
    if args.no_images:
        config["enable_images"] = False

    daily_entries = read_daily_entries(args.date)
    records = daily_entries.keyword_records
    free_notes = daily_entries.free_notes
    docs = read_local_docs()
    mode = args.mode or str(config.get("default_report_mode", "auto"))
    if mode == "review":
        records = []

    query_pairs: list[tuple[str, str]]
    if records:
        query_pairs = [(record.keyword, input_search_query(record.keyword)) for record in records]
    else:
        high_docs = high_weight_docs(docs)
        review_topics: list[str] = []
        for query in [*(doc.title for doc in high_docs[:4]), *read_tracking_topics()]:
            if query and query not in review_topics:
                review_topics.append(query)
        review_topics = review_topics[:6]
        if not review_topics and TRACKING_PATH.exists():
            review_topics = ["新知孵化工作流"]
        query_pairs = [(topic, review_search_query(topic)) for topic in review_topics]

    searches: dict[str, tuple[list[dict[str, str]], str]] = {}
    for topic, query in query_pairs:
        results, note = search_web(
            query,
            int(config.get("max_sources_per_keyword", 2)),
            bool(config.get("enable_web_search", True)),
        )
        if not records and results:
            results = filter_results_for_topic(results, topic)
            results = rank_sources(results, query, int(config.get("max_sources_per_keyword", 2)))
        searches[topic] = (results, note)

    report_dir = REPORT_ROOT / args.date
    report_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = report_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    if args.render_only:
        sources_path = report_dir / "sources.json"
        brief_path = report_dir / "report_brief.json"
        if not sources_path.exists():
            print(f"Missing {sources_path}. Run --collect-only first.", file=sys.stderr)
            return 1
        if not brief_path.exists():
            print(f"Missing {brief_path}. Automation must write report_brief.json before --render-only.", file=sys.stderr)
            return 1
        sources_json = json.loads(sources_path.read_text(encoding="utf-8"))
        report_brief = json.loads(brief_path.read_text(encoding="utf-8"))
        context_path = report_dir / "report_context.json"
        report_context = json.loads(context_path.read_text(encoding="utf-8")) if context_path.exists() else {}
        scanned_paths = report_context.get("local_knowledge", {}).get("scanned_paths", [])
        free_note_count = len(report_context.get("free_notes", []))
        tex = render_report_from_brief(args.date, report_brief, sources_json)
        (report_dir / "report.tex").write_text(tex, encoding="utf-8")
        quality = quality_check_simple(tex, records, sources_json, report_brief, scanned_paths, free_note_count)
        (report_dir / "quality_check.json").write_text(
            json.dumps(quality, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if not quality["passed"]:
            print("Report quality check failed. report.tex was kept. Issues:")
            for issue in quality["issues"]:
                print(f"- {issue}")
            return 1
        sync_review_queue_from_brief(args.date, report_brief)
        if args.no_compile:
            print(f"Wrote {report_dir / 'report.tex'}")
            return 0
        ok, message = compile_pdf(report_dir, config)
        print(message)
        return 0 if ok else 1

    searches, sources_json = enrich_searches_for_report(searches, config)
    sources_json["date"] = args.date
    report_context = build_report_context(
        args.date,
        records,
        free_notes,
        daily_entries.entry_counts_by_kind,
        daily_entries.source_format,
        docs,
        searches,
        sources_json,
        config,
    )
    (report_dir / "sources.json").write_text(
        json.dumps(sources_json, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (report_dir / "report_context.json").write_text(
        json.dumps(report_context, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.collect_only:
        print(f"Wrote {report_dir / 'report_context.json'}")
        return 0

    brief_path = report_dir / "report_brief.json"
    if brief_path.exists():
        report_brief = json.loads(brief_path.read_text(encoding="utf-8"))
        if not isinstance(report_brief.get("knowledge_cards"), list):
            report_brief = fallback_brief_from_context(report_context)
            brief_path.write_text(json.dumps(report_brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        report_brief = fallback_brief_from_context(report_context)
        brief_path.write_text(json.dumps(report_brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    tex = render_report_from_brief(args.date, report_brief, sources_json)
    (report_dir / "report.tex").write_text(tex, encoding="utf-8")
    scanned_paths = report_context.get("local_knowledge", {}).get("scanned_paths", [])
    quality = quality_check_simple(tex, records, sources_json, report_brief, scanned_paths, len(free_notes))
    (report_dir / "quality_check.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not quality["passed"]:
        print("Report quality check failed. report.tex was kept. Issues:")
        for issue in quality["issues"]:
            print(f"- {issue}")
        return 1
    sync_review_queue_from_brief(args.date, report_brief)

    if args.no_compile:
        print(f"Wrote {report_dir / 'report.tex'}")
        return 0

    ok, message = compile_pdf(report_dir, config)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
