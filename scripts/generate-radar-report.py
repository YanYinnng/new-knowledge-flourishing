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
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "system" / "report_config.json"
INBOX_DIR = ROOT / "inbox"
REPORT_ROOT = ROOT / "synthesis" / "daily_reports"
KNOWLEDGE_DIRS = [ROOT / "knowledge", ROOT / "library" / "nodes"]
SEED_DIRS = [ROOT / "synthesis" / "idea_seeds", ROOT / "library" / "seeds"]
TRACKING_PATH = ROOT / "tracking" / "topics.md"


DEFAULT_CONFIG = {
    "enable_web_search": True,
    "enable_images": True,
    "enable_ai_generated_images": False,
    "default_report_mode": "auto",
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


def parse_inbox(date_text: str) -> list[InputRecord]:
    path = INBOX_DIR / f"{date_text}.md"
    if not path.exists():
        return []
    records: list[InputRecord] = []
    current: InputRecord | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        keyword_match = re.match(r"^-+\s*关键词[：:]\s*(.+)$", line)
        if keyword_match:
            current = InputRecord(keyword=keyword_match.group(1).strip(), context="未填写", weight="未填写")
            records.append(current)
            continue
        if current:
            context_match = re.match(r"^-+\s*上下文[：:]\s*(.+)$", line)
            weight_match = re.match(r"^-+\s*(?:权重|可选权重)[：:]\s*(.+)$", line)
            if context_match:
                current.context = context_match.group(1).strip()
            elif weight_match:
                current.weight = weight_match.group(1).strip()
    return records


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


def search_web(query: str, limit: int, enabled: bool) -> tuple[list[dict[str, str]], str]:
    if not enabled:
        return [], "联网搜索已在 system/report_config.json 中关闭。"
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) IdeaSprout/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return [], f"当前环境无法联网，未能核实外部新进展：{exc}"
    parser = DuckDuckGoParser()
    parser.feed(body)
    seen: set[str] = set()
    cleaned: list[dict[str, str]] = []
    parse_limit = max(limit * 4, limit)
    for result in parser.results:
        url = result.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        cleaned.append(result)
        if len(cleaned) >= parse_limit:
            break
    if not cleaned:
        return [], "已尝试联网搜索，但没有解析到可用结果。"
    return rank_sources(cleaned, query, limit), ""


def infer_mainline(records: list[InputRecord], docs: list[LocalDoc]) -> str:
    if not records:
        return "今天没有新的网页输入，主线转为复盘已有高权重主题：哪些仍值得追踪，哪些只是系统搭建期留下的惯性。"
    keywords = "、".join(record.keyword for record in records[:4])
    has_ai = any("AI" in record.keyword.upper() or "AI" in record.context.upper() for record in records)
    has_tool = any("codex" in record.keyword.lower() or "自动" in record.keyword for record in records)
    has_opportunity = any(
        word in record.keyword or word in record.context
        for record in records
        for word in ["学院", "私域", "创业", "升学"]
    )
    if has_ai and has_opportunity:
        return f"今天的信息共同指向“AI 相关机会的识别与筛选”：{keywords} 不是孤立词条，而是在提醒你把升学路径、AI 工具和商业场景放进同一张机会雷达里。它们之间的连接还不算强，但弱连接本身有价值：都可能成为申请表达、实习探索、人脉请教或创业观察的素材入口。"
    if has_tool:
        return f"今天的主线是“让工具服务长期认知工作流”：{keywords} 更像是在验证点子发芽系统如何减少摩擦，而不是追逐新功能本身。其他关键词如果暂时分散，也可以作为工具工作流能否承接多领域线索的压力测试。"
    return f"今天的输入共同指向：{keywords}。关键词之间如果关系弱，弱点主要在于它们还没有落到同一个行动场景；今天的判断重点是找出哪个最能连接到你的长期兴趣、项目或机会。"


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
        return "它是把一次性对话延长成可检查目标的一类工具能力；对你来说，重点不是新功能本身，而是它能否托住长期项目。"
    if "上海创智学院" in keyword:
        return "它更像一个 AI 人才培养与产学研连接入口，而不是普通学校名词；你需要判断它是否能成为 AI+X、实习、人脉和创新创业的信息节点。"
    if "深圳科创学院" in keyword:
        return "它是围绕硬科技创业和工程人才培养的机构线索；和你听到的升学路径分享放在一起看，价值在于判断它是否提供 AI+硬件/创业方向的真实机会。"
    if "学院" in keyword:
        return "它是一个升学或能力路径线索；真正要判断的是它能否连接到你的 AI 学习、项目经历、导师/同伴网络和申请表达。"
    if "私域" in keyword:
        return "它不是一个营销热词，而是品牌或个人能反复触达一群人的关系资产；放到雪茄吧这种高客单场景里，关键是会员、活动和复购机制。"
    return f"它是今天从“{context}”里冒出来的观察对象；真正价值取决于它能不能和你的长期主题、行动机会或表达素材发生连接。"


def synthesize_findings(record: InputRecord, results: list[dict[str, str]], note: str) -> list[str]:
    credible = credible_sources(results)
    low = low_quality_sources(results)
    refs = source_refs(credible or results)
    findings: list[str] = []

    if not results:
        return [note or "当前环境无法联网，未能核实外部进展；本条只能先基于本地上下文做低置信判断。"]

    if credible:
        top_tiers = "、".join(sorted({source["quality_tier"] for source in credible}))
        findings.append(f"本次优先采用{top_tiers}，低质量博客/百科只作为线索；核心判断来自可追溯来源 {refs}。")
    elif low:
        findings.append(f"搜索结果主要是普通网页、机构营销页、博客或论坛线索，不能直接当作结论；今天只把它当成发现问题的入口 {source_refs(low)}。")

    keyword = record.keyword
    combined_text = " ".join(f"{item.get('title', '')} {item.get('snippet', '')}" for item in results)
    if "codex" in keyword.lower() or "goal" in keyword.lower():
        findings.extend(
            [
                "可核实的信息更像是“长期任务管理能力”的使用说明，而不是一个独立知识主题；它的价值在于减少重复交代背景。",
                "普通博客可以提示使用路径，但是否值得纳入你的系统，取决于它能否让自动化、补跑和日报质量变得更可控。",
                "今天不应把它升格为核心主题；更适合把它作为点子发芽项目的工具链观察项。",
            ]
        )
    elif "上海创智学院" in keyword:
        findings.extend(
            [
                "官方/机构资料显示它围绕人工智能人才培养与产学研生态展开，和“AI+X”方向的连接强于普通学校资讯。",
                "它对你更像机会雷达节点：可能关联课程/训练营、科研项目、企业合作、实习入口和 AI 创业网络。",
                "现在的信息还不足以判断申请价值，但足够进入 watchlist：下一步应核实培养方式、适合对象、申请门槛和可接触的人。",
            ]
        )
    elif "深圳科创学院" in keyword:
        findings.extend(
            [
                "公开资料更强调硬科技创业、工程训练和孵化网络；这和单纯 AI 学术路径不同，更偏“技术+产品+创业”。",
                "如果你关注未来升学路径，它的价值不只在学校名称，而在能否提供项目制经历、导师网络和真实创业案例。",
                "今天适合把它和上海创智学院放在同一组“AI/硬科技升学与机会路径”里比较，而不是拆成孤立节点。",
            ]
        )
    elif "学院" in keyword:
        findings.extend(
            [
                "这类机构线索要优先核实官网和培养方案，而不是被百科词条带着走。",
                "对你的价值主要取决于是否能带来项目经历、导师网络、实习/科研机会或申请叙事素材。",
                "今天先进入低成本 watchlist，等补齐官网、申请条件和学长原话后再决定是否升权。",
            ]
        )
    elif "私域" in keyword:
        findings.extend(
            [
                "可核实资料普遍把私域指向“可重复、低成本触达”的关系资产；但这个定义本身不够行动化。",
                "雪茄吧场景更值得关注：高客单、强信任、线下社交和活动复购，可能比泛泛的流量概念更接近真实商业机制。",
                "今天不要把它当成营销术语收藏；更适合转化成一个案例问题：这种店如何让顾客从一次消费变成社群成员。",
            ]
        )
    else:
        if combined_text:
            findings.extend(
                [
                    "搜索结果能确认它不是完全孤立的词，但当前证据还不足以支持高权重判断。",
                    "更有价值的做法是把它放回你的上下文：它从哪里来、为什么被你注意、能不能带来下一步行动。",
                    "今天先把它作为观察线索，等出现第二次触发或更高质量来源后再决定是否升级。",
                ]
            )

    if len(findings) < 3:
        findings.append("当前资料密度不足，先保留为低成本观察，不为它新建复杂结构。")
    return findings[:5]


def local_connection_analysis(record: InputRecord, docs: list[LocalDoc], topics: list[str]) -> list[str]:
    connected = related_docs(record, docs)
    items: list[str] = []
    if connected:
        for doc in connected[:2]:
            kind = "知识节点" if "knowledge" in str(doc.path) or "nodes" in str(doc.path) else "点子/追踪文件"
            items.append(
                f"本地{kind}《{doc.title}》可连接：它说明这个关键词不是单日噪音，而是可能影响已有主题“{doc.title}”的使用或判断。连接价值是帮助你决定是否进入长期追踪。"
            )

    keyword = record.keyword
    context = record.context
    if "codex" in keyword.lower() or "goal" in keyword.lower():
        items.append(
            "AI 工具/自动化连接：你正在用 Codex automation、补跑任务和 PDF 报告生成器维护点子发芽；这个关键词能直接反馈系统本身是否省心。"
        )
        items.append(
            "项目连接：它可以成为 README 或复盘素材，记录“如何把长期目标交给工具执行但仍保留人工判断”。"
        )
    if "上海创智学院" in keyword:
        items.append(
            "科研/学习连接：它和 AI+X、科研训练、项目制学习有关，适合放进“AI 升学路径”观察，而不是单纯记学校名。"
        )
        items.append(
            "人脉/机会连接：来源是学长分享，说明下一步最有价值的不是再搜十篇网页，而是追问学长具体项目、申请门槛、实习/导师资源和创新创业生态。"
        )
        items.append(
            "申请/表达素材连接：如果未来写申请或个人陈述，它可作为“我如何识别 AI 生态机会”的素材，但前提是补齐真实经历和选择理由。"
        )
    elif "深圳科创学院" in keyword:
        items.append(
            "创新创业连接：它更偏硬科技创业和工程实践，适合和上海创智学院并列比较“AI+工程/创业”的路径差异。"
        )
        items.append(
            "机会连接：学长分享是低成本入口，下一步应该把人脉线索变成具体问题清单，而不是只保存机构名。"
        )
    elif "学院" in keyword:
        items.append(
            "科研/学习连接：它可能是升学路径节点；当前是弱连接，理由是本地库还没有你的申请目标、时间线和筛选标准。建议低成本观察，而不是进入高权重节点。"
        )
    if "私域" in keyword:
        items.append(
            "商业/创业连接：雪茄吧是高客单线下场景，私域在这里可能对应会员关系、活动组织、复购和口碑传播，适合转化为案例观察。"
        )
        items.append(
            "表达素材连接：如果你之后写商业观察或创业案例，‘雪茄吧如何做私域’比‘私域是什么’更有个人辨识度。"
        )

    if not items:
        topic_hint = "、".join(topics[:3]) if topics else "现有长期主题"
        items.append(
            f"当前是弱连接，理由是它尚未明确落到 {topic_hint} 的某个行动场景；建议低成本观察，等它第二次出现或补充来源后再考虑建节点。"
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
        return "低成本观察：外部资料可查，但和个人长期主题的连接还需要补强。"
    return "暂时放下：当前证据不足，先不增加维护负担。"


def next_step_for(record: InputRecord) -> str:
    if "链接" in record.context or "http" in record.context:
        return "今晚回看原链接，摘出 1 句真正触动你的信息，并写下它为什么和你有关。"
    if "上海创智学院" in record.keyword:
        return "向学长追问 3 个问题：适合什么背景、有没有项目/实习入口、AI+X 方向具体怎么落地。"
    if "深圳科创学院" in record.keyword:
        return "打开官网或项目介绍，记录 1 个你能参与或模仿的硬科技/AI 项目训练形式。"
    if "学院" in record.keyword:
        return "补齐官网链接、申请对象和一个你能联系的人，判断它是否值得进入 watchlist。"
    if "私域" in record.keyword:
        return "把雪茄吧案例写成 3 行：客单价/复购方式/老板如何触达老客。"
    if "codex" in record.keyword.lower() or "goal" in record.keyword.lower():
        return "记录一次 /goal 或 automation 真正省下的步骤，判断它是否值得写入工具工作流节点。"
    return "补一个来源标题、链接或一句原话，再写下它能连接到哪个长期主题。"


def review_local_judgment(doc: LocalDoc) -> str:
    if "新知孵化工作流" in doc.title:
        return (
            "它仍是当前唯一真正的主线：网页输入、PDF 日报、邮件发送和 Git 同步都在围绕它减摩擦。"
            "今天不需要扩功能，重点是让报告质量稳定到你愿意每天读。"
        )
    return "它可以保留为观察节点，但今天没有新输入支撑升权；先看它是否还能导向一个具体动作。"


def review_personal_connection(doc: LocalDoc) -> str:
    if "新知孵化工作流" in doc.title:
        return (
            "这直接关系到你以后是否只通过网页输入关键词、上下文和权重，就能获得一份有判断的晚间报告。"
            "如果报告仍像搜索拼贴，这个系统就没有省心；如果报告能指出主线和下一步，它才值得继续用。"
        )
    return "它和你的关系暂时是弱连接：还没有新的触发词把它拉回行动场景，所以只适合低成本复盘。"


def review_next_step(doc: LocalDoc) -> str:
    if "新知孵化工作流" in doc.title:
        return "明天只做一次真实输入测试：从网页提交 3 个关键词，然后检查日报是否能给出主线、旧知识连接和一个可执行小动作。"
    return "明天如果它再次出现，再补一条具体上下文；否则不主动扩展。"


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
            f"外部资料只能提供 AI/个人知识管理趋势背景 {refs}；今天真正要看的不是行业热闹，"
            "而是这个系统能否把网页输入、晚间报告和明日小推进稳定串起来。"
        )
    if "本地 Markdown" in keyword:
        return (
            f"可参考的外部线索 {refs} 指向的是轻量、可迁移、可手动审查；"
            "这支持继续用 Markdown/JSON 做底座，而不是过早上数据库或知识图谱。"
        )
    if "晚间复盘" in keyword or "每日" in keyword:
        return (
            f"外部资料 {refs} 只作为方法背景；本项目的复盘重点不是新闻式汇总，"
            "而是把当天输入压缩成一个判断、一个连接和一个明天能做的小动作。"
        )
    if credible:
        return (
            f"已找到较高质量来源 {source_refs(credible)}，但无新输入日只做轻量核实："
            "它仍可作为 watchlist 背景，不因此自动升权。"
        )
    return (
        f"只找到普通线索 {source_refs(results)}，不足以支持新进展判断；"
        "建议保留观察，不写入核心结论。"
    )


def external_progress_summary(keyword: str, results: list[dict[str, str]], note: str) -> str:
    if not results:
        return f"{keyword}：{note or '当前环境无法联网，未能核实外部进展。'}"
    credible = credible_sources(results)
    refs = source_refs(credible or results)
    if "新知孵化工作流" in keyword:
        return f"{keyword}：外部背景 {refs} 说明 AI 工具正在降低个人知识整理成本；今天不把它写成行业新闻，只用于校准本地工作流是否省心。"
    if "本地 Markdown" in keyword:
        return f"{keyword}：外部线索 {refs} 支持“本地、可读、可迁移”的方向；当前判断是继续保持轻量文件结构。"
    if "晚间复盘" in keyword or "每日" in keyword:
        return f"{keyword}：外部线索 {refs} 只提供复盘方法背景；本项目的重点是每天产出可执行的小判断，而不是追逐泛资讯。"
    if credible:
        tiers = "、".join(sorted({item["quality_tier"] for item in credible}))
        return f"{keyword}：已用{tiers}核实背景 {source_refs(credible)}；本次只保留和当天输入或 watchlist 强相关的判断。"
    return f"{keyword}：搜索结果主要是普通博客/百科/论坛 {source_refs(results)}，只作为线索，不作为外部新进展。"


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
        "reader_promise": "先给个人判断，再给证据；来源只做支撑，不把搜索结果堆成正文。",
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
                    "context": record.context,
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
            f"{record.keyword}；上下文：{record.context or '未填写'}；权重：{record.weight or '未填写'}"
            for record in records
        ]
        lines.append(tex_items(rows))
    else:
        lines.append("今日没有新的网页输入，进入无新输入复盘模式。")
        lines.extend(
            [
                r"\section*{无新输入复盘模式}",
                r"\subsection*{今日状态}",
                tex_escape("没有新关键词输入，报告目标改为检查已有主题是否仍有追踪价值，而不是生成空报告。"),
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
                        [f"它是什么：{concept_sentence(record)}"]
                        + [f"今天查到了什么：{finding}" for finding in synthesize_findings(record, results, note)]
                        + [
                            "和我有什么关系：" + item
                            for item in local_connection_analysis(record, docs, tracking_topics)
                        ]
                        + [
                            f"今日判断：{judgment_for(record, results, connected)}",
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
                                f"复盘判断：{review_local_judgment(doc)}",
                                f"和我有什么关系：{review_personal_connection(doc)}",
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
                f"{doc.title}：{review_personal_connection(doc)}连接价值是把“系统是否好用”变成明天可以验证的一次输入测试。"
            )
    lines.append(
        tex_items(
            connection_items
            or ["当前是弱连接，理由是输入还没有落到明确项目、机会、人脉或学习路径；建议低成本观察，而不是进入高权重节点。"]
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
    lines.append(tex_items(idea_items or ["今日暂无明确发芽点子，理由是输入之间还没有形成足够清晰的行动组合。"]))

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
    args = parser.parse_args()

    config = read_config()
    if args.no_web:
        config["enable_web_search"] = False
    if args.no_images:
        config["enable_images"] = False

    records = parse_inbox(args.date)
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

    image = None
    if bool(config.get("enable_images", True)):
        image = generate_radar_image(report_dir, records, docs)

    tex, sources_json, report_brief = build_report(args.date, records, docs, searches, image, config)
    (report_dir / "report.tex").write_text(tex, encoding="utf-8")
    (report_dir / "sources.json").write_text(
        json.dumps(sources_json, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (report_dir / "report_brief.json").write_text(
        json.dumps(report_brief, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    quality = quality_check(tex, records, sources_json, report_brief)
    (report_dir / "quality_check.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not quality["passed"]:
        print("Report quality check failed. report.tex was kept. Issues:")
        for issue in quality["issues"]:
            print(f"- {issue}")
        return 1

    if args.no_compile:
        print(f"Wrote {report_dir / 'report.tex'}")
        return 0

    ok, message = compile_pdf(report_dir, config)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
