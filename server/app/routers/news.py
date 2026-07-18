"""News digest endpoints.

Daily AI industry + Amazon seller news with LLM-generated Chinese summaries.
Data is produced by the Hermes skill ``ai-amazon-daily-digest`` and stored as
one JSON file per day under ``$IVYEA_OPS_DATA_DIR/news/YYYY-MM-DD.json``.

Retention: only the ``_KEEP_DAYS`` most recent days are kept. Anything older is
purged by ``_cleanup_old()`` which runs on every ``/refresh`` call and on startup.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import require_user

router = APIRouter()

# ---------------------------------------------------------------------------
# Paths & models
# ---------------------------------------------------------------------------

_NEWS_DIR = settings.data_dir / "news"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_KEEP_DAYS = 7  # a week of digests for the date switcher


def _news_dir() -> Path:
    _NEWS_DIR.mkdir(parents=True, exist_ok=True)
    return _NEWS_DIR


class NewsItem(BaseModel):
    title: str
    source: str
    url: str
    summary_zh: str
    reason_zh: str = ""  # 推荐理由：为什么这条对卖家/AI从业者重要
    category: str  # "ai_industry" | "amazon_seller"
    importance: int = Field(3, ge=0, le=5)
    is_official: bool = False  # 大厂官方新闻 → 置顶区
    published_at: str | None = None
    tags: list[str] = []


class NewsDay(BaseModel):
    date: str
    generated_at: str
    items: list[NewsItem]
    stats: dict[str, int] = {}
    notes: str | None = None


class DatesResponse(BaseModel):
    dates: list[str]
    latest: str | None
    total: int


class RefreshResponse(BaseModel):
    triggered: bool
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_date(s: str) -> str:
    if not _DATE_RE.match(s):
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid date: {e}") from e
    return s


def _load_day(d: str) -> NewsDay | None:
    fp = _news_dir() / f"{d}.json"
    if not fp.is_file():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(
            status_code=500, detail=f"failed to load {d}.json: {e}"
        ) from e
    raw_items: list[dict[str, Any]] = list(data.get("items") or [])
    items: list[NewsItem] = []
    for it in raw_items:
        try:
            items.append(
                NewsItem(
                    title=str(it.get("title", "")).strip(),
                    source=str(it.get("source", "")).strip(),
                    url=str(it.get("url", "")).strip(),
                    summary_zh=str(it.get("summary_zh", "")).strip(),
                    reason_zh=str(it.get("reason_zh", "") or "").strip(),
                    category=str(it.get("category", "ai_industry")),
                    importance=int(it.get("importance", 3)),
                    is_official=bool(it.get("is_official", False)),
                    published_at=it.get("published_at"),
                    tags=list(it.get("tags") or []),
                )
            )
        except (TypeError, ValueError):
            continue
    rec_date = str(data.get("date") or d)
    return NewsDay(
        date=rec_date,
        generated_at=str(
            data.get("generated_at") or datetime.now().isoformat(timespec="seconds")
        ),
        items=items,
        stats=dict(data.get("stats") or {}),
        notes=data.get("notes"),
    )


def _list_date_files() -> list[str]:
    """Return up to _KEEP_DAYS recent dates. Older files also visible if present."""
    out: list[str] = []
    for p in _news_dir().glob("*.json"):
        stem = p.stem
        if _DATE_RE.match(stem):
            out.append(stem)
    out.sort(reverse=True)
    return out


def _cleanup_old(keep_days: int = _KEEP_DAYS) -> int:
    """Delete digest files older than keep_days. Returns count removed."""
    dates = _list_date_files()
    keep = set(dates[:keep_days])
    removed = 0
    for p in _news_dir().glob("*.json"):
        if p.stem in keep:
            continue
        if not _DATE_RE.match(p.stem):
            continue
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/dates", response_model=DatesResponse)
def list_dates(_user: str = Depends(require_user)) -> DatesResponse:
    """List dates for which a digest exists (newest first, capped at KEEP_DAYS)."""
    dates = _list_date_files()[:_KEEP_DAYS]
    return DatesResponse(
        dates=dates,
        latest=dates[0] if dates else None,
        total=len(dates),
    )


@router.get("/list", response_model=NewsDay)
def list_news(
    date: str | None = Query(
        None,
        description="YYYY-MM-DD; defaults to the most recent digest available",
    ),
    category: str | None = Query(
        None,
        description="Optional filter: ai_industry | amazon_seller",
    ),
    _user: str = Depends(require_user),
) -> NewsDay:
    """Return the digest for a given date (default: latest)."""
    if date:
        d = _validate_date(date)
    else:
        dates = _list_date_files()
        if not dates:
            today = datetime.now().strftime("%Y-%m-%d")
            return NewsDay(
                date=today,
                generated_at=datetime.now().isoformat(timespec="seconds"),
                items=[],
                stats={},
                notes="尚未生成任何 digest。点击「立即刷新」即可现场抓取 RSS 并用 AI 汇总今日资讯。",
            )
        d = dates[0]

    day = _load_day(d)
    if day is None:
        raise HTTPException(status_code=404, detail=f"no digest for {d}")

    if category:
        day = day.model_copy(
            update={"items": [i for i in day.items if i.category == category]}
        )
    # Stable ordering inside each group is handled by the frontend.
    # Here we just ensure official items come first, importance desc, then published_at desc.
    day.items.sort(
        key=lambda i: (
            0 if i.is_official else 1,
            -i.importance,
            -(len(i.published_at or "")),
            i.title,
        )
    )
    return day


@router.post("/refresh", response_model=RefreshResponse)
async def trigger_refresh(_user: str = Depends(require_user)) -> RefreshResponse:
    """Generate today's digest on demand (RSS fetch + standard AI chain).

    Runs in the background so the request returns immediately; the frontend
    polls ``/list``. Replaces the old hermes ``ai-amazon-daily-digest`` cron, so
    it works out of the box with no external job or hermes install.
    """
    from app.services import news_digest

    removed = _cleanup_old()
    msg = news_digest.start_generation()
    if removed:
        msg = f"{msg}（已清理 {removed} 个过期 digest）"
    return RefreshResponse(triggered=True, message=msg)
