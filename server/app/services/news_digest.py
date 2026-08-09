"""On-demand Amazon + AI news digest generator.

Open-source replacement for the old Hermes ``ai-amazon-daily-digest`` cron: it
fetches a (configurable) set of RSS feeds, then uses the standard AI fallback
chain (Hermes → 全局兜底 → Codex → Claude) to summarise / classify / translate
each item into the NewsItem schema, stored as ``news/YYYY-MM-DD.json``.

No external project or cron required — works out of the box; users can override
the feed list via the ``news_feeds`` setting (one ``url|source|category`` per line).
"""
from __future__ import annotations

import asyncio
import logging
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import feedparser
import httpx

from app.core import hub_settings
from app.core.config import settings

logger = logging.getLogger("ivyea.services.news_digest")

_NEWS_DIR = settings.data_dir / "news"

# (rss_url, source_label, category)  — category ∈ {ai_industry, amazon_seller}
# Every feed here was probed live 2026-07 (Anthropic / Marketplace Pulse /
# Microsoft AI / Meta AI / Tinuiti etc. are 404/410/empty and stay out).
_DEFAULT_FEEDS: list[tuple[str, str, str]] = [
    # —— AI 大厂官方 ——
    ("https://openai.com/blog/rss.xml",                                  "OpenAI",          "ai_industry"),
    ("https://blog.google/technology/ai/rss/",                           "Google AI",       "ai_industry"),
    ("https://deepmind.google/blog/rss.xml",                             "DeepMind",        "ai_industry"),
    ("https://blogs.nvidia.com/feed/",                                   "NVIDIA",          "ai_industry"),
    ("https://aws.amazon.com/blogs/machine-learning/feed/",              "AWS ML",          "ai_industry"),
    ("https://huggingface.co/blog/feed.xml",                             "HuggingFace",     "ai_industry"),
    # —— AI 媒体 / 博客 ——
    ("https://venturebeat.com/category/ai/feed/",                        "VentureBeat AI",  "ai_industry"),
    ("https://techcrunch.com/category/artificial-intelligence/feed/",    "TechCrunch AI",   "ai_industry"),
    ("https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "The Verge AI",   "ai_industry"),
    ("https://www.technologyreview.com/topic/artificial-intelligence/feed", "MIT TR AI",    "ai_industry"),
    ("https://arstechnica.com/ai/feed/",                                 "Ars Technica AI", "ai_industry"),
    ("https://www.wired.com/feed/tag/ai/latest/rss",                     "Wired AI",        "ai_industry"),
    ("https://the-decoder.com/feed/",                                    "The Decoder",     "ai_industry"),
    ("https://www.artificialintelligence-news.com/feed/",                "AI News",         "ai_industry"),
    ("https://simonwillison.net/atom/everything/",                       "Simon Willison",  "ai_industry"),
    ("https://www.qbitai.com/feed",                                      "量子位",           "ai_industry"),
    # —— 亚马逊卖家 / 电商 ——
    ("https://www.ecommercebytes.com/feed/",                             "EcommerceBytes",  "amazon_seller"),
    ("https://www.junglescout.com/blog/feed/",                           "Jungle Scout",    "amazon_seller"),
    ("https://channelx.world/feed/",                                     "ChannelX",        "amazon_seller"),
    ("https://www.retaildive.com/feeds/news/",                           "Retail Dive",     "amazon_seller"),
    ("https://www.sellerlabs.com/blog/feed/",                            "Seller Labs",     "amazon_seller"),
    ("https://www.modernretail.co/feed/",                                "Modern Retail",   "amazon_seller"),
    ("https://www.digitalcommerce360.com/feed/",                         "DigitalCommerce360", "amazon_seller"),
    ("https://www.ennews.com/rss",                                       "亿恩网",           "amazon_seller"),
]

# Batch sizing: total items sent to the model per day, and per-LLM-call chunk
# (25 items in one call already produced truncated/broken JSON — keep chunks small).
_MAX_ITEMS = 60
_CHUNK_SIZE = 15

# Some feeds (VentureBeat) reject the default python UA or sit behind redirects
# feedparser won't follow — fetch bytes ourselves with a browser UA + timeout.
_FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
}

# Source labels that count as 大厂官方 (pinned section) even without AI judgement.
_OFFICIAL_HINTS = ("openai", "anthropic", "amazon", "google", "meta", "microsoft", "aws")

# Module state for the single background generation.
_state: dict[str, Any] = {"generating": False, "last_error": ""}
_bg_task: Optional["asyncio.Task[Any]"] = None


def _news_dir() -> Path:
    _NEWS_DIR.mkdir(parents=True, exist_ok=True)
    return _NEWS_DIR


def _feeds() -> list[tuple[str, str, str]]:
    """Configured feeds (``news_feeds`` setting) or the curated defaults.

    User format, one per line: ``url | source | category`` (category optional)."""
    raw = str(hub_settings.get("news_feeds") or "").strip()
    if not raw:
        return _DEFAULT_FEEDS
    out: list[tuple[str, str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        url = parts[0]
        src = parts[1] if len(parts) > 1 and parts[1] else url
        cat = parts[2] if len(parts) > 2 and parts[2] in ("ai_industry", "amazon_seller") else "ai_industry"
        if url:
            out.append((url, src, cat))
    return out or _DEFAULT_FEEDS


def _fetch_one_feed(url: str, src: str, cat: str, max_per_feed: int) -> list[dict[str, Any]]:
    try:
        resp = httpx.get(url, headers=_FETCH_HEADERS, timeout=15, follow_redirects=True)
        parsed = feedparser.parse(resp.content)
    except Exception:
        return []
    items: list[dict[str, Any]] = []
    for e in (getattr(parsed, "entries", None) or [])[:max_per_feed]:
        title = (getattr(e, "title", "") or "").strip()
        link = (getattr(e, "link", "") or "").strip()
        if not title or not link:
            continue
        summary = re.sub(r"<[^>]+>", "", getattr(e, "summary", "") or "")[:500].strip()
        published = getattr(e, "published", "") or getattr(e, "updated", "")
        items.append({
            "title": title, "url": link, "source": src, "category": cat,
            "raw_summary": summary, "published_at": str(published)[:40],
        })
    return items


def _fetch_raw_items(max_per_feed: int = 8) -> list[dict[str, Any]]:
    """Blocking RSS fetch (run via asyncio.to_thread). Best-effort per feed.

    Feeds are fetched in parallel — with 20+ sources a serial sweep at 15s
    timeout each could take minutes.
    """
    import concurrent.futures as cf

    feeds = _feeds()
    items: list[dict[str, Any]] = []
    with cf.ThreadPoolExecutor(max_workers=min(8, max(1, len(feeds)))) as ex:
        futs = [ex.submit(_fetch_one_feed, url, src, cat, max_per_feed) for url, src, cat in feeds]
        for fut in futs:  # keep feed-list order deterministic
            try:
                items.extend(fut.result())
            except Exception:
                continue
    return items


def _build_prompt(pairs: list[tuple[int, dict[str, Any]]]) -> str:
    """``pairs`` are (global_index, item) so chunked calls keep stable indices."""
    compact = [
        {"i": idx, "title": it["title"], "source": it["source"],
         "category": it["category"], "summary": it["raw_summary"]}
        for idx, it in pairs
    ]
    return (
        "你是亚马逊跨境电商 + AI 行业的资讯主编。下面是今天抓取的若干条英文/中文资讯"
        "（含序号 i / 标题 / 来源 / 分类 / 摘要）。请为每一条生成中文要点，输出一个 JSON 数组，"
        "每个元素形如：\n"
        '{"i": 序号, "summary_zh": "40-80字客观中文摘要", '
        '"reason_zh": "20-50字推荐理由，直接说这条对亚马逊卖家/AI从业者为什么重要、该做什么", '
        '"importance": 0到5的整数（对亚马逊卖家或AI从业者的重要度，拉开区分度，别都给3）, '
        '"is_official": true/false（是否OpenAI/Anthropic/Amazon/Google/Meta/Microsoft等大厂官方消息）, '
        '"tags": ["中文标签1","中文标签2"]}\n'
        "要求：只输出 JSON 数组，不要任何解释、引用标注或代码块标记；保持 i 与输入一致；"
        "不确定 importance 时给 3；字符串内如需引号一律用中文引号「」或“”，绝不能出现未转义的英文双引号。\n\n输入：\n"
        + json.dumps(compact, ensure_ascii=False)
    )


# Citation markers some providers append when knowledge-base retrieval leaks
# into the answer (e.g. " [K3]" / "【K1】") — strip them from display text.
_CITE_RE = re.compile(r"\s*[\[【]K?\d+(?:[-–][\[【]?K?\d+[\]】]?)?[\]】]")


def _clean_text(s: str) -> str:
    return _CITE_RE.sub("", s or "").strip()


def _repair_inner_quotes(t: str) -> str:
    """Escape unescaped ASCII double quotes *inside* JSON string values.

    Models sometimes quote terms with ASCII quotes（如 "反向联邦制"）inside a
    string, which breaks strict parsing. Walk the text tracking string state; a
    quote only terminates a string when the next non-space char is a JSON
    delimiter (``, : } ]``), otherwise escape it.
    """
    out: list[str] = []
    in_str = False
    i, n = 0, len(t)
    while i < n:
        c = t[i]
        if not in_str:
            if c == '"':
                in_str = True
            out.append(c)
        elif c == "\\":
            out.append(c)
            if i + 1 < n:
                out.append(t[i + 1])
                i += 1
        elif c == '"':
            j = i + 1
            while j < n and t[j] in " \t\r\n":
                j += 1
            if j >= n or t[j] in ",:}]":
                in_str = False
                out.append(c)
            else:
                out.append('\\"')
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _scan_for_array(t: str) -> Optional[list]:
    dec = json.JSONDecoder()
    pos = t.find("[")
    while pos >= 0:
        try:
            v, _end = dec.raw_decode(t, pos)
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return v
        except Exception:
            logger.debug("v 失败（旁路，已忽略）", exc_info=True)
        pos = t.find("[", pos + 1)
    return None


def _extract_json_array(text: str) -> Optional[list]:
    """Return the first valid JSON array of objects found anywhere in ``text``.

    Providers may wrap the array in prose, emit it twice, append citation notes
    with stray brackets, embed unescaped ASCII quotes, or truncate the tail —
    so degrade gracefully: direct scan → inner-quote repair → per-object salvage.
    """
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()

    v = _scan_for_array(t)
    if v is not None:
        return v

    repaired = _repair_inner_quotes(t)
    v = _scan_for_array(repaired)
    if v is not None:
        return v

    # Last resort（截断的数组永远闭合不了）：salvage every standalone object
    # that carries an "i" index and stitch them back into a list.
    dec = json.JSONDecoder()
    objs: list[dict] = []
    pos = repaired.find("{")
    while pos >= 0:
        try:
            o, end = dec.raw_decode(repaired, pos)
            if isinstance(o, dict) and "i" in o:
                objs.append(o)
                pos = repaired.find("{", end)
                continue
        except Exception:
            logger.debug("o 失败（旁路，已忽略）", exc_info=True)
        pos = repaired.find("{", pos + 1)
    return objs or None


def _is_official(source: str, ai_flag: Any) -> bool:
    if isinstance(ai_flag, bool):
        return ai_flag
    s = (source or "").lower()
    return any(h in s for h in _OFFICIAL_HINTS)


def _round_robin_by_source(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reorder items so sources alternate (each feed is newest-first already)."""
    by_src: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_src.setdefault(it["source"], []).append(it)
    qs = list(by_src.values())
    out: list[dict[str, Any]] = []
    while qs:
        for q in list(qs):
            out.append(q.pop(0))
            if not q:
                qs.remove(q)
    return out


def _stats(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(items),
        "ai_industry": sum(1 for i in items if i.get("category") == "ai_industry"),
        "amazon_seller": sum(1 for i in items if i.get("category") == "amazon_seller"),
        "official": sum(1 for i in items if i.get("is_official")),
    }


async def generate_digest(target_date: Optional[str] = None) -> dict[str, Any]:
    """Fetch feeds, synthesise via the standard chain, and write the day file."""
    from app.services import ai_synthesis_service

    d = target_date or datetime.now().strftime("%Y-%m-%d")
    raw = await asyncio.to_thread(_fetch_raw_items)

    # De-dup by URL, then cap the batch sent to the model. Interleave categories
    # (a straight slice in feed order starved amazon_seller down to 1-2 items)
    # and, inside each category, round-robin across sources — otherwise the
    # first few feeds monopolise the cap and 2/3 of the sources never surface.
    seen: set[str] = set()
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for it in raw:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        by_cat.setdefault(it["category"], []).append(it)
    inputs: list[dict[str, Any]] = []
    queues = [
        _round_robin_by_source(by_cat[c])
        for c in ("ai_industry", "amazon_seller")
        if by_cat.get(c)
    ]
    while len(inputs) < _MAX_ITEMS and queues:
        for q in list(queues):
            if len(inputs) >= _MAX_ITEMS:
                break
            inputs.append(q.pop(0))
            if not q:
                queues.remove(q)

    out_items: list[dict[str, Any]] = []
    note: Optional[str] = None

    if not inputs:
        note = "未抓取到任何资讯——请检查服务器网络是否能访问 RSS 源，或在「系统配置」用 news_feeds 自定义可用的 RSS。"
    else:
        # Summarise in chunks with stable global indices: one big call gets
        # truncated / broken JSON, and one bad chunk shouldn't sink the rest.
        # agent_retrieval=False keeps knowledge-base citations out of the
        # strict-JSON answers; each chunk retries once before giving up.
        enriched: list = []
        failed_chunks = 0
        chain_error: Optional[str] = None
        pairs = list(enumerate(inputs))
        for start in range(0, len(pairs), _CHUNK_SIZE):
            chunk = pairs[start:start + _CHUNK_SIZE]
            chunk_arr: Optional[list] = None
            try:
                for _attempt in range(2):
                    _prov, text = await ai_synthesis_service.run_text_chain(
                        _build_prompt(chunk), agent_retrieval=False
                    )
                    chunk_arr = _extract_json_array(text)
                    if chunk_arr is not None:
                        break
            except Exception as e:  # noqa: BLE001
                chain_error = str(e)
                break  # whole chain down — no point burning the remaining chunks
            if chunk_arr is None:
                failed_chunks += 1
            else:
                enriched.extend(chunk_arr)

        if chain_error is not None:
            note = f"AI 汇总失败（{chain_error}），已展示原始资讯标题。可在「系统配置 → 全局兜底大模型」配置一个可用模型后重试。"
        elif failed_chunks and not enriched:
            note = "AI 返回的内容未能解析为 JSON（已展示英文原文）。请点「立即刷新」重试一次。"
        elif failed_chunks:
            note = f"有 {failed_chunks} 批资讯 AI 汇总失败（这部分显示英文原文），可点「立即刷新」重试。"

        by_idx = {int(x["i"]): x for x in enriched if isinstance(x, dict) and "i" in x}
        for idx, it in enumerate(inputs):
            ai = by_idx.get(idx, {})
            summary_zh = _clean_text(str(ai.get("summary_zh") or "")) or it["raw_summary"] or it["title"]
            reason_zh = _clean_text(str(ai.get("reason_zh") or ""))
            try:
                importance = int(ai.get("importance", 3))
            except (TypeError, ValueError):
                importance = 3
            importance = max(0, min(5, importance))
            tags = [_clean_text(str(t)) for t in (ai.get("tags") or []) if str(t).strip()][:4]
            out_items.append({
                "title": it["title"], "source": it["source"], "url": it["url"],
                "summary_zh": summary_zh, "reason_zh": reason_zh, "category": it["category"],
                "importance": importance, "is_official": _is_official(it["source"], ai.get("is_official")),
                "published_at": it["published_at"], "tags": tags,
            })

    day = {
        "date": d,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "items": out_items,
        "stats": _stats(out_items),
        "notes": note,
    }
    (_news_dir() / f"{d}.json").write_text(json.dumps(day, ensure_ascii=False, indent=2), encoding="utf-8")
    return day


def is_generating() -> bool:
    return bool(_state["generating"])


def start_generation() -> str:
    """Kick off a single background digest generation. Returns a status message."""
    global _bg_task
    if _state["generating"]:
        return "正在生成今日资讯，请稍候 2-4 分钟后刷新查看…"

    async def _run() -> None:
        _state["generating"] = True
        _state["last_error"] = ""
        try:
            await generate_digest()
        except Exception as e:  # noqa: BLE001
            _state["last_error"] = str(e)
        finally:
            _state["generating"] = False

    _bg_task = asyncio.create_task(_run())
    return "已开始生成今日资讯（24 个信源抓取 + AI 分批汇总），约 2-4 分钟后自动出现"
