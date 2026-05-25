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
    "max_sources_per_keyword": 2,
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
    docs: list[LocalDoc] = []
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
        docs.append(
            LocalDoc(
                path=path,
                title=first_heading(text, path.stem),
                text=text,
                weight=field_value(text, "Weight"),
                status=field_value(text, "Status"),
            )
        )
    return docs


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


def token_set(value: str) -> set[str]:
    ascii_tokens = {token.lower() for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-/]{1,}", value)}
    cjk_tokens = {chunk for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", value)}
    return ascii_tokens | cjk_tokens


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
    for result in parser.results:
        url = result.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        cleaned.append(result)
        if len(cleaned) >= limit:
            break
    if not cleaned:
        return [], "已尝试联网搜索，但没有解析到可用结果。"
    return cleaned, ""


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
        return f"今天的信息共同指向“AI 相关机会的识别与筛选”：{keywords} 不是孤立名词，而是在提醒你把升学、工具和商业场景放进同一张机会雷达里。"
    if has_tool:
        return f"今天的主线是“让工具服务长期认知工作流”：{keywords} 更像是在验证点子发芽系统如何减少摩擦，而不是追逐新功能本身。"
    return f"今天的输入共同指向：{keywords}。关键判断不是收集更多资料，而是识别它们是否能连接到你的长期兴趣、项目或行动机会。"


def judgment_for(record: InputRecord, sources: list[dict[str, str]], related: list[LocalDoc]) -> str:
    if record.weight in {"4", "5"}:
        return "用户给了高权重，值得优先追踪；但仍需要一个可验证的小行动，避免直接升为核心主题。"
    if related and sources:
        return "已有本地连接且能找到外部资料，建议升温观察。"
    if related:
        return "和旧知识有连接，但外部证据还薄，先保留为候选。"
    if sources:
        return "外部资料可查，但和个人长期主题的连接还弱，先低成本观察。"
    return "目前证据不足，先不加重维护负担。"


def next_step_for(record: InputRecord) -> str:
    if "链接" in record.context or "http" in record.context:
        return "回看原链接，摘出 1 句真正触动你的信息。"
    if "学院" in record.keyword:
        return "补充学校官网或项目介绍链接，确认它和 AI 方向的真实关系。"
    if "私域" in record.keyword:
        return "补一个具体案例：这个场景靠什么关系、内容或活动形成复购。"
    return "补一个来源标题、链接或一句原话，让它从印象变成可追踪线索。"


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
    title = tex_escape(source.get("title", "未命名来源"))
    url = source.get("url", "")
    if url:
        return rf"\item \href{{{url}}}{{{title}}}"
    return tex_item(title)


def build_report(
    date_text: str,
    records: list[InputRecord],
    docs: list[LocalDoc],
    searches: dict[str, tuple[list[dict[str, str]], str]],
    image: dict[str, str] | None,
    config: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
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
    for keyword, (results, note) in searches.items():
        if note:
            sources_json["search_notes"].append({"keyword": keyword, "note": note})
        for result in results:
            row = {"keyword": keyword, **result, "accessed_at": datetime.now().date().isoformat()}
            sources_json["text_sources"].append(row)
    if image:
        sources_json["image_sources"].append(image)

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
                        f"{doc.title}：权重 {doc.weight or '未标注'}，状态 {doc.status or '未标注'}，今日只做轻量复盘。"
                        for doc in high_weight_docs(docs)[:4]
                    ]
                    or ["当前没有标注为高权重的节点。"]
                ),
                r"\subsection*{沉睡点子回顾}",
                tex_items(
                    [
                        f"{seed.title}：保留为沉睡点子，等待新的输入或行动场景重新激活。"
                        for seed in dormant_seeds(docs, int(config.get("max_idea_seeds_per_report", 3)))
                    ]
                    or ["当前没有可回顾的沉睡点子。"]
                ),
                r"\subsection*{watchlist 新进展检查}",
                tex_items(
                    [
                        f"{keyword}："
                        + (
                            "已联网检查，候选来源包括 "
                            + "；".join(result["title"] for result in results[:2])
                            if results
                            else note or "当前环境无法联网，未能核实外部新进展。"
                        )
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
            source_summary = "；".join(
                f"{result.get('title', '来源')}：{result.get('snippet', '')[:90]}" for result in results[:2]
            )
            if not source_summary:
                source_summary = note or "当前没有可用外部资料。"
            relation_summary = (
                "；".join(f"{doc.title}（{relative_path(doc.path)}）" for doc in connected)
                if connected
                else "暂未发现强相关旧节点。"
            )
            lines.extend(
                [
                    r"\subsection*{" + tex_escape(record.keyword) + "}",
                    tex_items(
                        [
                            f"它是什么：一个来自今日输入的观察对象，原始上下文是“{record.context or '未填写'}”。",
                            f"今天查到了什么：{source_summary}",
                            f"和我有什么关系：{relation_summary}",
                            f"今日判断：{judgment_for(record, results, connected)}",
                            f"下一步：{next_step_for(record)}",
                        ]
                    ),
                ]
            )
    else:
        high_docs = high_weight_docs(docs)
        if high_docs:
            for doc in high_docs[:4]:
                results, note = searches.get(doc.title, ([], ""))
                source_summary = "；".join(result.get("title", "") for result in results[:2]) or note
                lines.extend(
                    [
                        r"\subsection*{" + tex_escape(doc.title) + "}",
                        tex_items(
                            [
                                f"今日状态：权重 {doc.weight or '未标注'}，状态 {doc.status or '未标注'}。",
                                f"复盘判断：仍可保留，但需要更多真实输入来验证它是否继续重要。",
                                f"外部检查：{source_summary or '当前没有可用外部资料。'}",
                            ]
                        ),
                    ]
                )
        else:
            lines.append("没有可复盘的高权重节点。")

    lines.append(r"\section*{与旧知识的连接}")
    connection_items: list[str] = []
    for record in records:
        for doc in related_docs(record, docs, limit=2):
            connection_items.append(
                f"{record.keyword} ↔ {doc.title}：相关文件 {relative_path(doc.path)}；连接价值是判断它是否能进入长期追踪，而不是只停留在当天印象。"
            )
    if not connection_items and not records:
        for doc in high_weight_docs(docs)[:3]:
            connection_items.append(f"{doc.title}：高权重节点，适合在无新输入日检查是否仍有行动价值。")
    lines.append(tex_items(connection_items or ["未发现足够明确的旧知识连接；本次不强行连接。"]))

    lines.append(r"\section*{今日发芽点子}")
    idea_items: list[str] = []
    if records:
        for record in records[: int(config.get("max_idea_seeds_per_report", 3))]:
            maturity = 45
            if related_docs(record, docs):
                maturity += 15
            if searches.get(record.keyword, ([], ""))[0]:
                maturity += 10
            idea_items.append(
                f"{record.keyword} 的小种子：来源组合为今日输入 + 外部资料 + 本地旧知识；值得关注是因为它可能转化为一个可追踪问题；成熟度 {min(maturity, 85)}/100；最小下一步：{next_step_for(record)}"
            )
    else:
        for seed in dormant_seeds(docs, int(config.get("max_idea_seeds_per_report", 3))):
            idea_items.append(
                f"{seed.title}：沉睡点子回顾；成熟度 40/100；最小下一步是确认它是否仍能服务当前系统。"
            )
    lines.append(tex_items(idea_items or ["今天没有足够材料提炼发芽点子。"]))

    lines.append(r"\section*{权重变化}")
    weight_items: list[str] = []
    if records:
        for record in records:
            suggested = "3" if related_docs(record, docs) else "2"
            if record.weight in {"4", "5"}:
                suggested = record.weight
            weight_items.append(
                f"{record.keyword}：当前权重 {record.weight or '未填写'}，建议观察权重 {suggested}；理由是本地连接和外部证据仍需继续确认。"
            )
    else:
        for doc in high_weight_docs(docs)[:4]:
            weight_items.append(f"{doc.title}：保持权重 {doc.weight or '未标注'}；无新输入日不自动升降权。")
    lines.append(tex_items(weight_items or ["无权重变化建议。"]))

    lines.append(r"\section*{外部新进展}")
    external_items: list[str] = []
    for keyword, (results, note) in searches.items():
        if results:
            external_items.append(f"{keyword}：已联网检查，重点来源包括 " + "；".join(result["title"] for result in results[:2]))
        elif note:
            external_items.append(f"{keyword}：{note}")
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
    return "\n".join(lines) + "\n", sources_json


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

    queries: list[str]
    if records:
        queries = [record.keyword for record in records]
    else:
        high_docs = high_weight_docs(docs)
        queries = []
        for query in [*(doc.title for doc in high_docs[:4]), *read_tracking_topics()]:
            if query and query not in queries:
                queries.append(query)
        queries = queries[:6]
        if not queries and TRACKING_PATH.exists():
            queries = ["点子发芽 新知孵化 工作流"]

    searches: dict[str, tuple[list[dict[str, str]], str]] = {}
    for query in queries:
        searches[query] = search_web(
            query,
            int(config.get("max_sources_per_keyword", 2)),
            bool(config.get("enable_web_search", True)),
        )

    report_dir = REPORT_ROOT / args.date
    report_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = report_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    image = None
    if bool(config.get("enable_images", True)):
        image = generate_radar_image(report_dir, records, docs)

    tex, sources_json = build_report(args.date, records, docs, searches, image, config)
    (report_dir / "report.tex").write_text(tex, encoding="utf-8")
    (report_dir / "sources.json").write_text(
        json.dumps(sources_json, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.no_compile:
        print(f"Wrote {report_dir / 'report.tex'}")
        return 0

    ok, message = compile_pdf(report_dir, config)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
