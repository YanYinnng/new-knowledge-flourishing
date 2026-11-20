from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from context_builder import append_jsonl as append_structured_jsonl
from context_builder import build_secretary_context, field_value, markdown_keyword_records, read_jsonl, stable_id, weight_target
from report_artifacts import parse_report_date, report_fingerprint
from report_quality import (
    QUALITY_FAILURE_EXIT,
    clean_source_text,
    normalize_space,
    quality_check_simple,
    valid_cards,
)


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


def compact_text(value: Any, limit: int = 220) -> str:
    text = normalize_space(str(value or ""))
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


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
    return [
        InputRecord(item["keyword"], item["supplemental_info"] or "未填写", normalize_weight(item["weight"]))
        for item in markdown_keyword_records(INBOX_DIR / f"{date_text}.md")
    ]


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
    return keyword


def review_search_query(topic: str) -> str:
    return f"{topic} 最新 进展 背景 应用"


def is_irrelevant_review_result(result: dict[str, str], topic: str) -> bool:
    text = f"{result.get('title', '')} {result.get('snippet', '')} {result.get('url', '')}".lower()
    stock_noise = ["股票", "a股", "沪指", "深成指", "创业板", "涨", "跌", "收盘", "选股", "打板", "公告"]
    if ("复盘" in topic or "每日" in topic) and any(word in text for word in stock_noise):
        return True
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


def concept_sentence(record: InputRecord) -> str:
    keyword = record.keyword
    context = record.context or "未填写"
    if "codex" in keyword.lower() or "goal" in keyword.lower():
        return "它是一类把一次性对话延长成可检查目标的工具能力。对你有用的地方在于：它能记住目标、拆出后续步骤，并在长项目里减少反复交代背景的成本。"
    if "学院" in keyword:
        return "它是一条升学或能力路径线索。名称是入口，真正值得看的是课程、项目、人和选择空间，能不能补上你现在的学习或经历缺口。"
    return f"它是今天从“{context}”里冒出来的观察对象。先看清它属于什么领域、有哪些可靠来源，再决定要不要留下来继续追。"


def next_step_for(record: InputRecord) -> str:
    if "链接" in record.context or "http" in record.context:
        return "今晚回看原链接，摘出 1 句真正触动你的信息，并写下它为什么和你有关。"
    if "学院" in record.keyword:
        return "补齐官网链接、申请对象和一个你能联系的人，再决定它是否进入 watchlist。"
    if "codex" in record.keyword.lower() or "goal" in record.keyword.lower():
        return "记录一次 /goal 或 automation 真正省下的步骤，再决定要不要写入工具工作流节点。"
    return "补一个来源标题、链接或一句原话，再写下它能连接到哪个长期主题。"


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
    return brief


def brief_reference_sources(brief: dict[str, Any], sources_json: dict[str, Any]) -> list[dict[str, str]]:
    sources = sources_json.get("text_sources", [])
    references = brief.get("reference_sources")
    if not isinstance(references, list) or not references:
        return sources
    requested = {str(source.get("id", "")) for source in references if isinstance(source, dict)}
    selected = [source for source in sources if source.get("id") in requested]
    return selected or sources


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
        payload.pop("kind", None)
        kind = default_kind
        identity = text
        if kind == "weight_change_candidate":
            target_path = weight_target(ROOT, str(payload.get("path") or ""))
            suggested_weight = str(payload.get("suggested_weight") or payload.get("new_weight") or "").strip()
            if target_path is None or suggested_weight not in {"1", "2", "3", "4", "5"}:
                continue
            current_weight = field_value(target_path.read_text(encoding="utf-8"), "Weight")
            if current_weight == suggested_weight:
                continue
            payload.update(path=relative_path(target_path), current_weight=current_weight, suggested_weight=suggested_weight, new_weight=suggested_weight)
            identity = f'{payload["path"]}:{suggested_weight}'
        candidate_id = stable_id(date_text, kind, identity, prefix="candidate")
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
        for row in read_jsonl(queue_path)
        if row.get("id")
    }
    candidates: list[dict[str, Any]] = []
    candidates.extend(normalize_candidate_items(brief.get("memory_candidates"), "memory_candidate", date_text)[:1])
    candidates.extend(normalize_candidate_items(brief.get("weight_change_candidates"), "weight_change_candidate", date_text)[:1])
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
    raw_themes = review.get("themes")
    themes = [str(item) for item in raw_themes if str(item).strip()] if isinstance(raw_themes, list) else []
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
    old_links = brief.get("old_knowledge_links")
    idea_seeds = brief.get("idea_seeds")
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
    lines.append(tex_text_items(old_links, limit=3) or tex_items(["今日没有足够强的旧知识链接。"]))
    lines.append(r"\section*{今日发芽点子}")
    lines.append(tex_text_items(idea_seeds, limit=2) or tex_items(["今日暂无值得保留的新点子。"]))
    lines.append(r"\section*{参考搜索内容}")
    if references:
        lines.append(r"\begin{itemize}[leftmargin=2em]")
        for source in references:
            source_id = source.get("id", "")
            tier = source.get("quality_tier", "未分层")
            title_text = source.get("title", "未命名来源")
            url = str(source.get("url") or "")
            if url.startswith(("https://", "http://")):
                url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%")
            else:
                url = ""
            label = tex_escape(f"[{source_id}] {tier}：{title_text}")
            lines.append(rf"\item \href{{{url}}}{{{label}}}" if url else r"\item " + label)
        lines.append(r"\end{itemize}")
    else:
        lines.append(tex_items(["当前没有可列出的参考搜索内容。"]))
    free_note_tex = tex_free_note_review(free_note_review)
    if free_note_tex:
        lines.append(r"\section*{随心记复盘}")
        lines.append(free_note_tex)
    memory_candidate_tex = tex_text_items(brief.get("memory_candidates"), limit=1)
    if memory_candidate_tex:
        lines.append(r"\section*{个人记忆候选}")
        lines.append(memory_candidate_tex)
    weight_candidates = normalize_candidate_items(brief.get("weight_change_candidates"), "weight_change_candidate", date_text)
    weight_candidate_tex = tex_text_items([item["payload"] for item in weight_candidates], limit=1)
    if weight_candidate_tex:
        lines.append(r"\section*{权重调整候选}")
        lines.append(weight_candidate_tex)
    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


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


def write_report_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_saved_report(
    date_text: str,
    report_dir: Path,
    config: dict[str, Any],
    report_context: dict[str, Any],
    sources_json: dict[str, Any],
    brief: dict[str, Any],
    no_compile: bool,
) -> int:
    quality_path = report_dir / "quality_check.json"
    write_report_json(quality_path, {"passed": False, "pdf_ready": False, "issues": []})
    if not isinstance(brief, dict) or not valid_cards(brief.get("knowledge_cards")):
        issue = "report_brief.knowledge_cards must be an array of keyword cards."
        write_report_json(quality_path, {"passed": False, "pdf_ready": False, "issues": [issue]})
        print(issue, file=sys.stderr)
        return QUALITY_FAILURE_EXIT

    inputs = report_context.get("inputs", brief.get("inputs") or [])
    brief = {**brief, "inputs": inputs}
    local = report_context.get("local_knowledge", {})
    tex = render_report_from_brief(date_text, brief, sources_json)
    (report_dir / "report.tex").write_text(tex, encoding="utf-8")
    quality = quality_check_simple(
        tex,
        [row["keyword"] for row in inputs],
        sources_json,
        brief,
        local.get("scanned_paths", []),
        len(report_context.get("free_notes", [])),
        any(next(base.rglob("*.md"), None) is not None for base in [*KNOWLEDGE_DIRS, *SEED_DIRS]),
    )
    quality["pdf_ready"] = False
    write_report_json(quality_path, quality)
    if not quality["passed"]:
        print("Report quality check failed. report.tex was kept. Issues:")
        for issue in quality["issues"]:
            print(f"- {issue}")
        return QUALITY_FAILURE_EXIT
    if no_compile:
        print(f"Wrote {report_dir / 'report.tex'}")
        return 0
    fingerprint = report_fingerprint(report_dir)
    ok, message = compile_pdf(report_dir, config)
    print(message)
    if not ok:
        return 1
    sync_review_queue_from_brief(date_text, brief)
    quality["pdf_ready"] = True
    quality["report_fingerprint"] = fingerprint
    write_report_json(quality_path, quality)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an Idea Sprout cognitive radar LaTeX/PDF report.")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--mode", choices=["auto", "input", "review"], default="")
    parser.add_argument("--no-web", action="store_true")
    parser.add_argument("--no-images", action="store_true")
    parser.add_argument("--no-compile", action="store_true")
    stage = parser.add_mutually_exclusive_group()
    stage.add_argument("--collect-only", action="store_true", help="Only write report_context.json and sources.json.")
    stage.add_argument("--render-only", action="store_true", help="Render report.tex/PDF from an existing report_brief.json.")
    args = parser.parse_args()
    try:
        parse_report_date(args.date)
    except ValueError as exc:
        parser.error(str(exc))

    config = read_config()
    if args.no_web:
        config["enable_web_search"] = False
    if args.no_images:
        config["enable_images"] = False

    report_dir = REPORT_ROOT / args.date
    if args.render_only:
        context_path = report_dir / "report_context.json"
        sources_path = report_dir / "sources.json"
        brief_path = report_dir / "report_brief.json"
        try:
            report_context, sources_json, report_brief = [
                json.loads(path.read_text(encoding="utf-8")) for path in (context_path, sources_path, brief_path)
            ]
        except (OSError, ValueError) as exc:
            print(f"Cannot render saved report: {exc}", file=sys.stderr)
            return 1
        return render_saved_report(args.date, report_dir, config, report_context, sources_json, report_brief, args.no_compile)

    report_dir.mkdir(parents=True, exist_ok=True)
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
    write_report_json(report_dir / "sources.json", sources_json)
    write_report_json(report_dir / "report_context.json", report_context)

    if args.collect_only:
        print(f"Wrote {report_dir / 'report_context.json'}")
        return 0

    brief_path = report_dir / "report_brief.json"
    if brief_path.exists():
        report_brief = json.loads(brief_path.read_text(encoding="utf-8"))
        if not isinstance(report_brief, dict) or not valid_cards(report_brief.get("knowledge_cards")):
            report_brief = fallback_brief_from_context(report_context)
            write_report_json(brief_path, report_brief)
    else:
        report_brief = fallback_brief_from_context(report_context)
        write_report_json(brief_path, report_brief)
    return render_saved_report(args.date, report_dir, config, report_context, sources_json, report_brief, args.no_compile)


if __name__ == "__main__":
    raise SystemExit(main())
