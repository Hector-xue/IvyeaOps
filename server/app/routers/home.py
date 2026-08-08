"""Home dashboard router — competitor / own-ASIN watchlist + snapshots + alerts.

Live version: each ``/pulse`` fetches the ASIN now, stores a snapshot, and
returns the change vs the previous stored snapshot. The watchlist and snapshot
tables live server-side specifically so a future scheduled poller can reuse
them (read the watchlist, write snapshots) with zero client changes.
"""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import require_user
from app.services import asin_pulse_service, category_service, market_traffic_service, sellersprite_service
from app.services.asin_pulse_service import SNAPSHOT_METRICS

router = APIRouter()

_SNAPSHOT_MAX_PER_ASIN = 200
_INITED: set = set()
_DATA_SOURCES = {"sorftime", "sellersprite"}


def _data_source(value: str | None) -> str:
    source = (value or "sorftime").strip().lower()
    if source not in _DATA_SOURCES:
        raise HTTPException(400, f"unsupported data_source: {source}")
    return source


def _source_key(source: str, value: str) -> str:
    """Preserve legacy Sorftime keys; namespace all other provider caches."""
    return value if source == "sorftime" else f"{source}:{value}"


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _db_path() -> str:
    from app.core.security import user_data_dir
    return str(user_data_dir() / "home_monitor.sqlite3")


def _connect() -> sqlite3.Connection:
    path = _db_path()
    conn = sqlite3.connect(path, isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    if path not in _INITED:
        _ensure_schema(conn)
        _INITED.add(path)
    return conn


def _init_db() -> None:
    # Initialize the admin (shared) DB at startup; per-user DBs are created
    # lazily on first connect.
    _connect().close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    if True:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS home_watch (
                id          TEXT PRIMARY KEY,
                asin        TEXT NOT NULL,
                marketplace TEXT NOT NULL,
                data_source TEXT NOT NULL DEFAULT 'sorftime',
                kind        TEXT NOT NULL DEFAULT 'competitor',
                label       TEXT NOT NULL DEFAULT '',
                ts          INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS home_snapshot (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                asin        TEXT NOT NULL,
                marketplace TEXT NOT NULL,
                data_source TEXT NOT NULL DEFAULT 'sorftime',
                ts          INTEGER NOT NULL,
                metrics     TEXT NOT NULL DEFAULT '{}'
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshot_asin "
            "ON home_snapshot(asin, marketplace, ts DESC)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS home_category_snapshot (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id     TEXT NOT NULL,
                marketplace TEXT NOT NULL,
                data_source TEXT NOT NULL DEFAULT 'sorftime',
                ts          INTEGER NOT NULL,
                ranks       TEXT NOT NULL DEFAULT '{}'
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cat_snapshot "
            "ON home_category_snapshot(node_id, marketplace, ts DESC)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS home_market_watch (
                id          TEXT PRIMARY KEY,
                query       TEXT NOT NULL,
                marketplace TEXT NOT NULL,
                data_source TEXT NOT NULL DEFAULT 'sorftime',
                label       TEXT NOT NULL DEFAULT '',
                ts          INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS home_market_snapshot (
                query_key     TEXT NOT NULL,
                marketplace   TEXT NOT NULL,
                data_source   TEXT NOT NULL DEFAULT 'sorftime',
                day           TEXT NOT NULL,
                ts            INTEGER NOT NULL,
                search_volume REAL,
                total_sales   REAL,
                avg_price     REAL,
                PRIMARY KEY (query_key, marketplace, day)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS home_category_result (
                query_key   TEXT NOT NULL,
                marketplace TEXT NOT NULL,
                data_source TEXT NOT NULL DEFAULT 'sorftime',
                ts          INTEGER NOT NULL,
                data        TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (query_key, marketplace)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS home_market_node (
                mid           TEXT PRIMARY KEY,
                data_source   TEXT NOT NULL DEFAULT 'sorftime',
                node_id       TEXT NOT NULL,
                category_name TEXT NOT NULL DEFAULT '',
                ts            INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS home_keyword (
                id          TEXT PRIMARY KEY,
                keyword     TEXT NOT NULL,
                marketplace TEXT NOT NULL,
                data_source TEXT NOT NULL DEFAULT 'sorftime',
                label       TEXT NOT NULL DEFAULT '',
                ts          INTEGER NOT NULL,
                data        TEXT NOT NULL DEFAULT '',
                data_ts     INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS home_keyword_extends (
                id          TEXT PRIMARY KEY,
                keyword     TEXT NOT NULL,
                marketplace TEXT NOT NULL,
                data_source TEXT NOT NULL DEFAULT 'sorftime',
                ts          INTEGER NOT NULL,
                data        TEXT NOT NULL DEFAULT '[]'
            )
        """)
        # Existing installations predate source selection.  Preserve all rows
        # as Sorftime and namespace new SellerSprite IDs/cache keys.
        for table in (
            "home_watch", "home_snapshot", "home_category_snapshot",
            "home_market_watch", "home_market_snapshot", "home_category_result",
            "home_market_node", "home_keyword", "home_keyword_extends",
        ):
            _ensure_column(conn, table, "data_source", "TEXT NOT NULL DEFAULT 'sorftime'")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshot_source_asin "
            "ON home_snapshot(data_source, asin, marketplace, ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cat_snapshot_source "
            "ON home_category_snapshot(data_source, node_id, marketplace, ts DESC)"
        )


def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _day_of(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")


# ── Watchlist CRUD ────────────────────────────────────────────────────────────

class WatchIn(BaseModel):
    asin: str
    marketplace: str = "US"
    data_source: str = "sorftime"
    kind: str = "competitor"          # "competitor" | "own"
    label: str = ""


@router.get("/watch")
def list_watch(data_source: str = "sorftime", _user: str = Depends(require_user)) -> List[dict]:
    source = _data_source(data_source)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id,asin,marketplace,data_source,kind,label,ts FROM home_watch "
            "WHERE data_source=? ORDER BY ts ASC",
            (source,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/watch")
def add_watch(item: WatchIn, _user: str = Depends(require_user)) -> dict:
    asin = item.asin.strip().upper()
    source = _data_source(item.data_source)
    if not asin:
        raise HTTPException(400, "asin cannot be empty")
    if item.kind not in ("competitor", "own"):
        raise HTTPException(400, "kind must be competitor or own")
    wid = _source_key(source, f"{item.kind}:{item.marketplace}:{asin}")
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO home_watch (id,asin,marketplace,data_source,kind,label,ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (wid, asin, item.marketplace, source, item.kind, item.label.strip(), int(time.time() * 1000)),
        )
    return {"id": wid}


@router.delete("/watch/{wid}")
def delete_watch(wid: str, _user: str = Depends(require_user)) -> dict:
    with _connect() as conn:
        conn.execute("DELETE FROM home_watch WHERE id=?", (wid,))
    return {"ok": True}


# ── Snapshots + delta ─────────────────────────────────────────────────────────

def _latest_metrics(
    conn: sqlite3.Connection, asin: str, marketplace: str, data_source: str = "sorftime",
) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT metrics FROM home_snapshot WHERE asin=? AND marketplace=? AND data_source=? "
        "ORDER BY ts DESC LIMIT 1",
        (asin, marketplace, data_source),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["metrics"])
    except Exception:
        return None


def _compute_delta(curr: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-metric numeric delta (curr - prev). Missing/None on either side → None."""
    out: Dict[str, Any] = {}
    if not prev:
        return out
    for m in SNAPSHOT_METRICS:
        c, p = curr.get(m), prev.get(m)
        if isinstance(c, (int, float)) and isinstance(p, (int, float)):
            out[m] = round(c - p, 4)
    return out


def _trim_snapshots(
    conn: sqlite3.Connection, asin: str, marketplace: str, data_source: str = "sorftime",
) -> None:
    conn.execute(
        "DELETE FROM home_snapshot WHERE asin=? AND marketplace=? AND data_source=? AND id NOT IN "
        "(SELECT id FROM home_snapshot WHERE asin=? AND marketplace=? AND data_source=? "
        "ORDER BY ts DESC LIMIT ?)",
        (asin, marketplace, data_source, asin, marketplace, data_source, _SNAPSHOT_MAX_PER_ASIN),
    )


class PulseReq(BaseModel):
    asin: str
    marketplace: str = "US"
    data_source: str = "sorftime"


@router.post("/pulse")
async def pulse(req: PulseReq, _user: str = Depends(require_user)) -> dict:
    asin = req.asin.strip().upper()
    source = _data_source(req.data_source)
    if not asin:
        raise HTTPException(400, "asin cannot be empty")

    pulse_data = (
        await sellersprite_service.home_asin_pulse(asin, req.marketplace)
        if source == "sellersprite"
        else await asin_pulse_service.fetch_asin_pulse(asin, req.marketplace)
    )
    pulse_data["data_source"] = source

    # Persist a snapshot only when the core fetch succeeded.
    metrics = asin_pulse_service.snapshot_payload(pulse_data)

    delta: Dict[str, Any] = {}
    prev_ts: Optional[int] = None
    if not pulse_data.get("error"):
        with _connect() as conn:
            prev = _latest_metrics(conn, asin, req.marketplace, source)
            delta = _compute_delta(metrics, prev)
            now = int(time.time() * 1000)
            conn.execute(
                "INSERT INTO home_snapshot (asin,marketplace,data_source,ts,metrics) VALUES (?,?,?,?,?)",
                (asin, req.marketplace, source, now, json.dumps(metrics, ensure_ascii=False)),
            )
            _trim_snapshots(conn, asin, req.marketplace, source)
            prow = conn.execute(
                "SELECT ts FROM home_snapshot WHERE asin=? AND marketplace=? AND data_source=? "
                "ORDER BY ts DESC LIMIT 1 OFFSET 1",
                (asin, req.marketplace, source),
            ).fetchone()
            prev_ts = prow["ts"] if prow else None

    return {"current": pulse_data, "delta": delta, "prev_ts": prev_ts}


@router.get("/watch-snapshots")
def watch_snapshots(data_source: str = "sorftime", _user: str = Depends(require_user)) -> List[dict]:
    """Latest stored snapshot per watched ASIN — no provider call. Powers the
    cache-first card render so opening a tab costs zero API quota."""
    source = _data_source(data_source)
    out: List[dict] = []
    with _connect() as conn:
        watch = conn.execute(
            "SELECT id,asin,marketplace,data_source,kind,label FROM home_watch "
            "WHERE data_source=? ORDER BY ts ASC",
            (source,),
        ).fetchall()
        for w in watch:
            row = conn.execute(
                "SELECT ts,metrics FROM home_snapshot WHERE asin=? AND marketplace=? AND data_source=? "
                "ORDER BY ts DESC LIMIT 1",
                (w["asin"], w["marketplace"], source),
            ).fetchone()
            metrics = {}
            ts = None
            if row:
                try:
                    metrics = json.loads(row["metrics"])
                except Exception:
                    metrics = {}
                ts = row["ts"]
            out.append({
                "id": w["id"], "asin": w["asin"], "marketplace": w["marketplace"],
                "data_source": source, "kind": w["kind"], "label": w["label"],
                "ts": ts, "metrics": metrics,
            })
    return out


# ── Alerts (recent significant changes across the watchlist) ──────────────────

# Minimum absolute change for a metric to count as an alert-worthy move.
_ALERT_THRESHOLD = {
    "price": 0.01,
    "bsr": 1,
    "est_sales": 1,
    "review_count": 1,
    "rating": 0.1,
    "inventory": 1,
}


@router.get("/alerts")
def alerts(data_source: str = "sorftime", _user: str = Depends(require_user)) -> List[dict]:
    """For each watched ASIN, diff its two most recent snapshots and surface
    metrics that moved beyond their threshold. Sparse until ≥2 snapshots
    exist per ASIN — that's expected in the live (no background poller) mode."""
    source = _data_source(data_source)
    out: List[dict] = []
    with _connect() as conn:
        watch = conn.execute(
            "SELECT asin,marketplace,kind,label FROM home_watch WHERE data_source=?",
            (source,),
        ).fetchall()
        for w in watch:
            rows = conn.execute(
                "SELECT ts,metrics FROM home_snapshot WHERE asin=? AND marketplace=? AND data_source=? "
                "ORDER BY ts DESC LIMIT 2",
                (w["asin"], w["marketplace"], source),
            ).fetchall()
            if len(rows) < 2:
                continue
            try:
                curr = json.loads(rows[0]["metrics"])
                prev = json.loads(rows[1]["metrics"])
            except Exception:
                continue
            for m in SNAPSHOT_METRICS:
                c, p = curr.get(m), prev.get(m)
                if not (isinstance(c, (int, float)) and isinstance(p, (int, float))):
                    continue
                diff = c - p
                if abs(diff) < _ALERT_THRESHOLD.get(m, 0):
                    continue
                out.append({
                    "asin": w["asin"],
                    "marketplace": w["marketplace"],
                    "kind": w["kind"],
                    "label": w["label"],
                    "metric": m,
                    "from": p,
                    "to": c,
                    "diff": round(diff, 4),
                    "ts": rows[0]["ts"],
                })
            # Coupon / deal appearing or disappearing.
            for flag in ("coupon", "deal"):
                cf, pf = bool(curr.get(flag)), bool(prev.get(flag))
                if cf != pf:
                    out.append({
                        "asin": w["asin"], "marketplace": w["marketplace"],
                        "kind": w["kind"], "label": w["label"],
                        "metric": flag, "from": pf, "to": cf, "diff": None,
                        "ts": rows[0]["ts"],
                    })
    out.sort(key=lambda a: a["ts"], reverse=True)
    return out[:30]


# ── Category dashboard ────────────────────────────────────────────────────────

_MOVER_THRESHOLD = 5          # min rank positions changed to count as a mover
_CAT_SNAPSHOT_MAX = 60


class CategoryReq(BaseModel):
    query: str
    marketplace: str = "US"
    data_source: str = "sorftime"
    mode: str = "category"        # "category" | "keyword"


def _category_movers(
    conn: sqlite3.Connection, node_id: str, marketplace: str, curr: Dict[str, int],
    data_source: str = "sorftime",
) -> Dict[str, Any]:
    """Diff current {asin: rank} against the last stored snapshot."""
    row = conn.execute(
        "SELECT ranks FROM home_category_snapshot WHERE node_id=? AND marketplace=? AND data_source=? "
        "ORDER BY ts DESC LIMIT 1",
        (node_id, marketplace, data_source),
    ).fetchone()
    if not row:
        return {"new_entrants": [], "movers": [], "has_baseline": False}
    try:
        prev: Dict[str, int] = json.loads(row["ranks"])
    except Exception:
        return {"new_entrants": [], "movers": [], "has_baseline": False}

    new_entrants = [a for a in curr if a not in prev][:15]
    movers: List[Dict[str, Any]] = []
    for asin, rank in curr.items():
        if asin in prev:
            diff = rank - prev[asin]          # negative = climbed (lower rank #)
            if abs(diff) >= _MOVER_THRESHOLD:
                movers.append({"asin": asin, "from": prev[asin], "to": rank, "diff": diff})
    movers.sort(key=lambda m: abs(m["diff"]), reverse=True)
    return {"new_entrants": new_entrants, "movers": movers[:15], "has_baseline": True}


@router.post("/category")
async def category(req: CategoryReq, _user: str = Depends(require_user)) -> dict:
    if not req.query.strip():
        raise HTTPException(400, "query cannot be empty")

    source = _data_source(req.data_source)
    mode = req.mode if req.mode in ("category", "keyword") else "category"
    data = (
        await sellersprite_service.home_category(req.query, req.marketplace, mode)
        if source == "sellersprite"
        else await category_service.fetch_category(req.query, req.marketplace, mode)
    )
    data["data_source"] = source

    # Rank movers only make sense for a stable category node (category mode).
    changes: Dict[str, Any] = {"new_entrants": [], "movers": [], "has_baseline": False}
    if mode == "category" and not data.get("error") and data.get("top") and data.get("node_id"):
        node_id = data["node_id"]
        curr = category_service.rank_map(data["top"])
        with _connect() as conn:
            changes = _category_movers(conn, node_id, req.marketplace, curr, source)
            conn.execute(
                "INSERT INTO home_category_snapshot (node_id,marketplace,data_source,ts,ranks) "
                "VALUES (?,?,?,?,?)",
                (node_id, req.marketplace, source, int(time.time() * 1000), json.dumps(curr, ensure_ascii=False)),
            )
            conn.execute(
                "DELETE FROM home_category_snapshot WHERE node_id=? AND marketplace=? AND data_source=? AND id NOT IN "
                "(SELECT id FROM home_category_snapshot WHERE node_id=? AND marketplace=? AND data_source=? "
                "ORDER BY ts DESC LIMIT ?)",
                (node_id, req.marketplace, source, node_id, req.marketplace, source, _CAT_SNAPSHOT_MAX),
            )

    result = {**data, "changes": changes}
    # Cache per (mode, query) so reopening shows it without re-analyzing.
    if not data.get("error"):
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO home_category_result "
                "(query_key,marketplace,data_source,ts,data) VALUES (?,?,?,?,?)",
                (_source_key(source, f"{mode}:{req.query.strip().lower()}"), req.marketplace,
                 source, int(time.time() * 1000), json.dumps(result, ensure_ascii=False)),
            )
    return result


@router.get("/category-result")
def category_result(
    query: str, marketplace: str = "US", mode: str = "category",
    data_source: str = "sorftime", _user: str = Depends(require_user),
) -> dict:
    """Last cached category analysis — no provider call."""
    source = _data_source(data_source)
    with _connect() as conn:
        row = conn.execute(
            "SELECT ts,data FROM home_category_result WHERE query_key=? AND marketplace=? AND data_source=?",
            (_source_key(source, f"{mode}:{query.strip().lower()}"), marketplace, source),
        ).fetchone()
    if not row:
        return {"cached": None, "ts": None}
    try:
        return {"cached": json.loads(row["data"]), "ts": row["ts"]}
    except Exception:
        return {"cached": None, "ts": None}


# ── Market (大盘) traffic: watchlist + daily series + recording ───────────────

class MarketWatchIn(BaseModel):
    query: str
    marketplace: str = "US"
    data_source: str = "sorftime"
    label: str = ""


@router.get("/market-watch")
def list_market_watch(data_source: str = "sorftime", _user: str = Depends(require_user)) -> List[dict]:
    source = _data_source(data_source)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id,query,marketplace,data_source,label,ts FROM home_market_watch "
            "WHERE data_source=? ORDER BY ts ASC",
            (source,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/market-watch")
async def add_market_watch(item: MarketWatchIn, _user: str = Depends(require_user)) -> dict:
    query = item.query.strip()
    source = _data_source(item.data_source)
    if not query:
        raise HTTPException(400, "query cannot be empty")
    mid = _source_key(source, f"{item.marketplace}:{query.lower()}")
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO home_market_watch "
            "(id,query,marketplace,data_source,label,ts) VALUES (?,?,?,?,?,?)",
            (mid, query, item.marketplace, source, item.label.strip(), int(time.time() * 1000)),
        )
    # Record an initial point immediately so the chart isn't empty on day one.
    try:
        await _record_market_one(query, item.marketplace, source)
    except Exception:
        pass
    return {"id": mid}


@router.delete("/market-watch/{mid}")
def delete_market_watch(mid: str, _user: str = Depends(require_user)) -> dict:
    with _connect() as conn:
        conn.execute("DELETE FROM home_market_watch WHERE id=?", (mid,))
    return {"ok": True}


def _asin_daily_series(
    conn: sqlite3.Connection, asins: List[str], marketplace: str, data_source: str = "sorftime",
) -> List[dict]:
    """Daily series = average est_sales across the given ASINs, one point per
    day (using each ASIN's last snapshot that day)."""
    if not asins:
        return []
    placeholders = ",".join("?" * len(asins))
    rows = conn.execute(
        f"SELECT asin,ts,metrics FROM home_snapshot "
        f"WHERE marketplace=? AND data_source=? AND asin IN ({placeholders}) ORDER BY ts ASC",
        (marketplace, data_source, *asins),
    ).fetchall()
    # day -> asin -> est_sales (last wins because rows are ts-ascending)
    by_day: Dict[str, Dict[str, float]] = {}
    for r in rows:
        try:
            m = json.loads(r["metrics"])
        except Exception:
            continue
        es = m.get("est_sales")
        if not isinstance(es, (int, float)):
            continue
        by_day.setdefault(_day_of(r["ts"]), {})[r["asin"]] = float(es)
    out: List[dict] = []
    for day in sorted(by_day):
        vals = list(by_day[day].values())
        if vals:
            out.append({"day": day, "value": round(sum(vals) / len(vals), 1)})
    return out


@router.get("/market-series")
def market_series(
    query: str, marketplace: str = "US", data_source: str = "sorftime",
    _user: str = Depends(require_user),
) -> dict:
    source = _data_source(data_source)
    qk = _source_key(source, query.strip().lower())
    with _connect() as conn:
        mrows = conn.execute(
            "SELECT day,search_volume,total_sales,avg_price FROM home_market_snapshot "
            "WHERE query_key=? AND marketplace=? AND data_source=? ORDER BY day ASC",
            (qk, marketplace, source),
        ).fetchall()
        own_asins = [r["asin"] for r in conn.execute(
            "SELECT asin FROM home_watch WHERE kind='own' AND marketplace=? AND data_source=?",
            (marketplace, source),
        ).fetchall()]
        comp_asins = [r["asin"] for r in conn.execute(
            "SELECT asin FROM home_watch WHERE kind='competitor' AND marketplace=? AND data_source=?",
            (marketplace, source),
        ).fetchall()]
        own = _asin_daily_series(conn, own_asins, marketplace, source)
        competitor = _asin_daily_series(conn, comp_asins, marketplace, source)
    return {
        "query": query,
        "marketplace": marketplace,
        "data_source": source,
        "market": [dict(r) for r in mrows],
        "own": own,
        "competitor": competitor,
    }


# ── Recording (used by the daily scheduler + manual / external trigger) ───────

async def _record_market_one(query: str, marketplace: str, data_source: str = "sorftime") -> bool:
    source = _data_source(data_source)
    m = (
        await sellersprite_service.home_market_metrics(query, marketplace)
        if source == "sellersprite"
        else await market_traffic_service.fetch_market_metrics(query, marketplace)
    )
    if m.get("search_volume") is None and m.get("total_sales") is None and m.get("avg_price") is None:
        return False
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO home_market_snapshot "
            "(query_key,marketplace,data_source,day,ts,search_volume,total_sales,avg_price) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (_source_key(source, query.strip().lower()), marketplace, source, _today_key(), int(time.time() * 1000),
             m.get("search_volume"), m.get("total_sales"), m.get("avg_price")),
        )
    return True


async def _record_asin_one(asin: str, marketplace: str, data_source: str = "sorftime") -> bool:
    source = _data_source(data_source)
    pulse_data = (
        await sellersprite_service.home_asin_pulse(asin, marketplace)
        if source == "sellersprite"
        else await asin_pulse_service.fetch_asin_pulse(asin, marketplace)
    )
    if pulse_data.get("error"):
        return False
    metrics = asin_pulse_service.snapshot_payload(pulse_data)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO home_snapshot (asin,marketplace,data_source,ts,metrics) VALUES (?,?,?,?,?)",
            (asin, marketplace, source, int(time.time() * 1000), json.dumps(metrics, ensure_ascii=False)),
        )
        _trim_snapshots(conn, asin, marketplace, source)
    return True


async def run_due_recordings(data_source: str | None = None) -> dict:
    """Record today's point for any market baseline / watched ASIN that doesn't
    have one yet. Best-effort: individual failures are swallowed. Returns a
    small summary for logging / the manual-trigger response."""
    day = _today_key()
    recorded_market = 0
    recorded_asin = 0

    selected_source = _data_source(data_source) if data_source is not None else None
    with _connect() as conn:
        if selected_source:
            baselines = [(r["query"], r["marketplace"], r["data_source"]) for r in conn.execute(
                "SELECT query,marketplace,data_source FROM home_market_watch WHERE data_source=?",
                (selected_source,),
            ).fetchall()]
            watched = [(r["asin"], r["marketplace"], r["data_source"]) for r in conn.execute(
                "SELECT asin,marketplace,data_source FROM home_watch WHERE data_source=?",
                (selected_source,),
            ).fetchall()]
        else:
            baselines = [(r["query"], r["marketplace"], r["data_source"]) for r in
                         conn.execute("SELECT query,marketplace,data_source FROM home_market_watch").fetchall()]
            watched = [(r["asin"], r["marketplace"], r["data_source"]) for r in
                       conn.execute("SELECT asin,marketplace,data_source FROM home_watch").fetchall()]

    for query, mkt, source in baselines:
        with _connect() as conn:
            done = conn.execute(
                "SELECT 1 FROM home_market_snapshot WHERE query_key=? AND marketplace=? "
                "AND data_source=? AND day=?",
                (_source_key(source, query.strip().lower()), mkt, source, day),
            ).fetchone()
        if done:
            continue
        try:
            if await _record_market_one(query, mkt, source):
                recorded_market += 1
        except Exception:
            pass

    for asin, mkt, source in watched:
        with _connect() as conn:
            row = conn.execute(
                "SELECT ts FROM home_snapshot WHERE asin=? AND marketplace=? AND data_source=? "
                "ORDER BY ts DESC LIMIT 1",
                (asin, mkt, source),
            ).fetchone()
        if row and _day_of(row["ts"]) == day:
            continue
        try:
            if await _record_asin_one(asin, mkt, source):
                recorded_asin += 1
        except Exception:
            pass

    return {"day": day, "recorded_market": recorded_market, "recorded_asin": recorded_asin}


@router.post("/market-record")
async def market_record(data_source: str = "sorftime", _user: str = Depends(require_user)) -> dict:
    """Manual 'record now' — also suitable for an external cron / systemd timer."""
    return await run_due_recordings(data_source)


# ── Historical backfill via trend tools (monthly points) ──────────────────────

def _ts_for_day(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").timestamp() * 1000)


async def backfill_history(
    query: str, marketplace: str, data_source: str = "sorftime",
) -> dict:
    """Seed monthly history for one baseline: keyword_trend → market search
    volume; product_trend → est_sales for each watched ASIN (for attribution).
    Idempotent: replaces market points by (key,day) and re-inserts ASIN history
    after clearing pre-today snapshots."""
    source = _data_source(data_source)
    qk = _source_key(source, query.strip().lower())
    today = _today_key()

    if source == "sellersprite":
        sv_series, sv_err = await sellersprite_service.home_keyword_trend_series(query, marketplace)
    else:
        sv_series, sv_err = await market_traffic_service.fetch_keyword_trend_series(query, marketplace)
    market_points = 0
    with _connect() as conn:
        for day, sv in sv_series:
            if day == today:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO home_market_snapshot "
                "(query_key,marketplace,data_source,day,ts,search_volume,total_sales,avg_price) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (qk, marketplace, source, day, _ts_for_day(day), sv, None, None),
            )
            market_points += 1

    with _connect() as conn:
        watched = [r["asin"] for r in conn.execute(
            "SELECT asin FROM home_watch WHERE marketplace=? AND data_source=?",
            (marketplace, source),
        ).fetchall()]

    start_today = _ts_for_day(today)
    asin_points = 0
    asin_errors = 0
    for asin in watched:
        if source == "sellersprite":
            series, err = await sellersprite_service.home_product_trend_series(asin, marketplace)
        else:
            series, err = await market_traffic_service.fetch_product_trend_series(asin, marketplace)
        if err or not series:
            asin_errors += 1
            continue
        with _connect() as conn:
            conn.execute(
                "DELETE FROM home_snapshot WHERE asin=? AND marketplace=? AND data_source=? AND ts < ?",
                (asin, marketplace, source, start_today),
            )
            for day, sales in series:
                if day == today:
                    continue
                conn.execute(
                    "INSERT INTO home_snapshot (asin,marketplace,data_source,ts,metrics) VALUES (?,?,?,?,?)",
                    (asin, marketplace, source, _ts_for_day(day),
                     json.dumps({"est_sales": sales}, ensure_ascii=False)),
                )
                asin_points += 1

    return {"market_points": market_points, "asin_points": asin_points,
            "asin_errors": asin_errors, "sv_error": sv_err}


class BackfillReq(BaseModel):
    query: str
    marketplace: str = "US"
    data_source: str = "sorftime"


def _hist_products(report: Any) -> List[dict]:
    """Top-100 rows of a category_report_from_history answer."""
    from app.services.sorftime_service import record
    root = record(report)
    lst = root.get("top100_products") or root.get("Top100产品") or []
    return [p for p in lst if isinstance(p, dict)]


def _hist_day_metrics(report: Any) -> tuple:
    """From a single-day category_report_from_history → (total_sales, avg_price)."""
    from app.services.sorftime_service import record
    root = record(report)
    rep = root.get("category_stats_report") or root.get("类目统计报告") or {}
    total = _kx_num(
        _kx_pick(rep, "top100_monthly_sales_volume", "top100产品月销量")
    ) if isinstance(rep, dict) else None
    prices = [_kx_num(_kx_pick(p, "price", "价格")) for p in _hist_products(report)]
    prices = [x for x in prices if x]
    avg = round(sum(prices) / len(prices), 2) if prices else None
    return total, avg


class DailyBackfillReq(BaseModel):
    query: str
    marketplace: str = "US"
    data_source: str = "sorftime"
    category: str = ""          # ASIN / nodeId / category name to resolve the node
    days: int = 31


@router.post("/market-daily-backfill")
async def market_daily_backfill(req: DailyBackfillReq, _user: str = Depends(require_user)) -> dict:
    """Backfill the last N days (≤31) of *daily* category total-sales / avg-price
    via category_report_from_history (per-day snapshots). Requires resolving the
    category node from `category` (ASIN reverse-lookup recommended). Search volume
    has no daily equivalent on Sorftime, so only sales/price get daily points."""
    source = _data_source(req.data_source)
    if source == "sellersprite":
        return {
            "error": "卖家精灵当前提供月度趋势，不提供近 31 天类目日历史；可使用“导入历史”回填月度数据",
            "filled": 0, "asin_daily": 0, "node_id": "", "category_name": None,
            "days": 0, "data_source": source,
        }

    from datetime import date, timedelta
    from app.services import category_service as CS
    from app.services.sorftime_service import _make_client, _safe_call

    qk = _source_key(source, req.query.strip().lower())
    days = max(1, min(req.days, 31))
    async with _make_client() as client:
        node, name, source, rerr = await CS._resolve_node(client, (req.category or req.query), req.marketplace)
        if not node:
            return {"error": rerr or "无法解析类目节点（请提供该品类真实 ASIN 或 nodeId）",
                    "filled": 0, "node_id": "", "category_name": name}
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO home_market_node "
                "(mid,data_source,node_id,category_name,ts) VALUES (?,?,?,?,?)",
                (_source_key(source, f"{req.marketplace}:{req.query.strip().lower()}"), source,
                 node, name or "", int(time.time() * 1000)),
            )
            # Watched ASINs (own + competitor) in this marketplace — we'll fill
            # their daily sales from the same per-day Top100 (no extra calls).
            watched = {r["asin"] for r in conn.execute(
                "SELECT asin FROM home_watch WHERE marketplace=? AND data_source=?",
                (req.marketplace, source),
            ).fetchall()}
        filled = 0
        asin_daily = 0
        today = date.today()
        for i in range(days):
            d = (today - timedelta(days=i)).isoformat()
            _, rep, e = await _safe_call(client, "category_report_from_history",
                                         {"node_id": node, "start_date": d, "end_date": d, "amz_site": req.marketplace}, 1)
            if e or not isinstance(rep, dict):
                continue
            total, avg = _hist_day_metrics(rep)
            ts_d = _ts_for_day(d)
            with _connect() as conn:
                if total is not None or avg is not None:
                    row = conn.execute(
                        "SELECT search_volume FROM home_market_snapshot WHERE query_key=? AND marketplace=? "
                        "AND data_source=? AND day=?",
                        (qk, req.marketplace, source, d)).fetchone()
                    sv = row["search_volume"] if row else None
                    conn.execute(
                        "INSERT OR REPLACE INTO home_market_snapshot "
                        "(query_key,marketplace,data_source,day,ts,search_volume,total_sales,avg_price) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (qk, req.marketplace, source, d, int(time.time() * 1000), sv, total, avg))
                    filled += 1
                # Per-ASIN daily sales for any watched ASIN present in the Top100.
                if watched:
                    for p in _hist_products(rep):
                        a = p.get("ASIN") or p.get("asin")
                        if a in watched:
                            es = _kx_num(_kx_pick(p, "monthly_sales_volume", "月销量"))
                            if es is not None:
                                conn.execute(
                                    "DELETE FROM home_snapshot WHERE asin=? AND marketplace=? AND data_source=? "
                                    "AND ts>=? AND ts<?",
                                    (a, req.marketplace, source, ts_d, ts_d + 86400000))
                                conn.execute(
                                    "INSERT INTO home_snapshot (asin,marketplace,data_source,ts,metrics) "
                                    "VALUES (?,?,?,?,?)",
                                    (a, req.marketplace, source, ts_d,
                                     json.dumps({"est_sales": es}, ensure_ascii=False)))
                                asin_daily += 1
    return {"error": None, "filled": filled, "asin_daily": asin_daily,
            "node_id": node, "category_name": name, "days": days, "data_source": source}


@router.post("/market-backfill")
async def market_backfill(req: BackfillReq, _user: str = Depends(require_user)) -> dict:
    if not req.query.strip():
        raise HTTPException(400, "query cannot be empty")
    return await backfill_history(req.query, req.marketplace, req.data_source)


# ── Keyword watchlist (server-side, synced across devices, cache-first) ───────

class KeywordIn(BaseModel):
    keyword: str
    marketplace: str = "US"
    data_source: str = "sorftime"
    label: str = ""


def _kw_id(keyword: str, marketplace: str, data_source: str = "sorftime") -> str:
    return _source_key(data_source, f"{marketplace}:{keyword.strip().lower()}")


@router.get("/keywords")
def list_keywords(data_source: str = "sorftime", _user: str = Depends(require_user)) -> List[dict]:
    source = _data_source(data_source)
    out: List[dict] = []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id,keyword,marketplace,data_source,label,ts,data,data_ts FROM home_keyword "
            "WHERE data_source=? ORDER BY ts ASC",
            (source,),
        ).fetchall()
    for r in rows:
        data = None
        if r["data"]:
            try:
                data = json.loads(r["data"])
            except Exception:
                data = None
        out.append({
            "id": r["id"], "keyword": r["keyword"], "marketplace": r["marketplace"],
            "data_source": source, "label": r["label"], "ts": r["ts"],
            "data": data, "data_ts": r["data_ts"],
        })
    return out


@router.post("/keyword")
def add_keyword(item: KeywordIn, _user: str = Depends(require_user)) -> dict:
    kw = item.keyword.strip()
    source = _data_source(item.data_source)
    if not kw:
        raise HTTPException(400, "keyword cannot be empty")
    kid = _kw_id(kw, item.marketplace, source)
    with _connect() as conn:
        # Don't clobber existing cached data on re-add.
        exists = conn.execute("SELECT 1 FROM home_keyword WHERE id=?", (kid,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO home_keyword (id,keyword,marketplace,data_source,label,ts,data,data_ts) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (kid, kw, item.marketplace, source, item.label.strip(), int(time.time() * 1000), "", None),
            )
    return {"id": kid}


@router.delete("/keyword/{kid}")
def delete_keyword(kid: str, _user: str = Depends(require_user)) -> dict:
    with _connect() as conn:
        conn.execute("DELETE FROM home_keyword WHERE id=?", (kid,))
    return {"ok": True}


class KeywordPulseReq(BaseModel):
    keyword: str
    marketplace: str = "US"
    data_source: str = "sorftime"


@router.post("/keyword-pulse")
async def keyword_pulse(req: KeywordPulseReq, _user: str = Depends(require_user)) -> dict:
    """Live keyword_detail + keyword_trend, cached server-side so subsequent
    tab opens read from cache (GET /keywords) without spending API quota."""
    kw = req.keyword.strip()
    source = _data_source(req.data_source)
    if not kw:
        raise HTTPException(400, "keyword cannot be empty")

    if source == "sellersprite":
        result = await sellersprite_service.home_keyword_pulse(kw, req.marketplace)
        detail = result.get("detail")
        detail_err = result.get("detail_error")
    else:
        from app.services.sorftime_service import _make_client, _safe_call
        async with _make_client() as client:
            detail_task = _safe_call(client, "keyword_detail",
                                     {"keyword": kw, "keyword_support_site": req.marketplace}, 1)
            trend_task = _safe_call(client, "keyword_trend",
                                    {"keyword": kw, "keyword_support_site": req.marketplace}, 2)
            (_, detail, detail_err), (_, trend, trend_err) = await asyncio.gather(detail_task, trend_task)
        result = {
            "keyword": kw, "marketplace": req.marketplace, "data_source": source,
            "detail": detail, "detail_error": detail_err,
            "trend": trend, "trend_error": trend_err,
        }
    # Cache only when we actually got the core detail.
    if detail and not detail_err:
        with _connect() as conn:
            kid = _kw_id(kw, req.marketplace, source)
            now = int(time.time() * 1000)
            exists = conn.execute("SELECT 1 FROM home_keyword WHERE id=?", (kid,)).fetchone()
            if exists:
                conn.execute("UPDATE home_keyword SET data=?, data_ts=? WHERE id=?",
                             (json.dumps(result, ensure_ascii=False), now, kid))
            else:
                conn.execute(
                    "INSERT INTO home_keyword (id,keyword,marketplace,data_source,label,ts,data,data_ts) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (kid, kw, req.marketplace, source, "", now,
                     json.dumps(result, ensure_ascii=False), now),
                )
    return result


# ── Expanded keywords (拓展词): related keywords + opportunity score ──────────

def _kx_num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).replace(",", "").replace("$", "").replace("%", "").strip()
        return float(s) if s else None
    except Exception:
        return None


def _kx_pick(d: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, "", []):
            return d[k]
    return None


def _score_extends(items: List[dict], base: str) -> None:
    """Opportunity score 0-100 = 0.7·demand(log) + 0.3·CPC intent (relative to set).
    Marks `related` = shares a token with the base keyword."""
    vols = [i["monthly_search"] for i in items if isinstance(i.get("monthly_search"), (int, float))]
    cpcs = [i["cpc"] for i in items if isinstance(i.get("cpc"), (int, float))]
    maxv = max(vols) if vols else 1.0
    maxc = max(cpcs) if cpcs else 1.0
    base_tokens = {t for t in base.lower().split() if t}
    for i in items:
        v = i.get("monthly_search") or 0
        c = i.get("cpc") or 0
        voln = (math.log10(v + 1) / math.log10(maxv + 1)) if maxv > 1 else 0.0
        cpcn = (c / maxc) if maxc else 0.0
        i["score"] = round(100 * (0.7 * voln + 0.3 * cpcn))
        i["related"] = bool(base_tokens & {t for t in str(i.get("keyword", "")).lower().split() if t})


@router.post("/keyword-extends")
async def keyword_extends(req: KeywordPulseReq, _user: str = Depends(require_user)) -> dict:
    """Fetch related/extended keywords (keyword_extends) + compute opportunity
    score. Cached server-side so reopening costs no quota."""
    kw = req.keyword.strip()
    source = _data_source(req.data_source)
    if not kw:
        raise HTTPException(400, "keyword cannot be empty")
    if source == "sellersprite":
        items, err = await sellersprite_service.home_keyword_extends(kw, req.marketplace)
    else:
        from app.services.sorftime_service import _make_client, _safe_call, rows
        async with _make_client() as client:
            _, res, err = await _safe_call(client, "keyword_extends",
                                           {"keyword": kw, "keyword_support_site": req.marketplace}, 1)
        items = []
        if not err:
            for x in rows(res):
                k = _kx_pick(x, "关键词", "keyword")
                if not k or str(k).strip().lower() == kw.lower():
                    continue
                items.append({
                    "keyword": k,
                    "monthly_search": _kx_num(_kx_pick(x, "月搜索量", "monthly_search_volume",
                                                       "monthlySearches")),
                    "cpc": _kx_num(_kx_pick(x, "cpc推荐竞价", "推荐cpc竞价",
                                            "recommended_cpc_bid", "cpc")),
                    "seasonality": _kx_pick(x, "季节性", "seasonality"),
                    "evidence_sales": None,
                })
    if err or not items:
        return {
            "keyword": kw, "marketplace": req.marketplace, "data_source": source,
            "error": err or "无拓展词", "items": [], "ts": None,
        }
    _score_extends(items, kw)
    items.sort(key=lambda i: i.get("score", 0), reverse=True)

    now = int(time.time() * 1000)
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO home_keyword_extends "
            "(id,keyword,marketplace,data_source,ts,data) VALUES (?,?,?,?,?,?)",
            (_kw_id(kw, req.marketplace, source), kw, req.marketplace, source, now,
             json.dumps(items, ensure_ascii=False)),
        )
    return {
        "keyword": kw, "marketplace": req.marketplace, "data_source": source,
        "error": None, "items": items, "ts": now,
    }


@router.get("/keyword-extends")
def keyword_extends_cached(
    keyword: str, marketplace: str = "US", data_source: str = "sorftime",
    _user: str = Depends(require_user),
) -> dict:
    """Cached extended keywords — no provider call."""
    source = _data_source(data_source)
    with _connect() as conn:
        row = conn.execute(
            "SELECT ts,data FROM home_keyword_extends WHERE id=? AND data_source=?",
            (_kw_id(keyword, marketplace, source), source),
        ).fetchone()
    if not row:
        return {"keyword": keyword, "marketplace": marketplace, "items": [], "ts": None}
    try:
        return {"keyword": keyword, "marketplace": marketplace, "items": json.loads(row["data"]), "ts": row["ts"]}
    except Exception:
        return {"keyword": keyword, "marketplace": marketplace, "items": [], "ts": None}


@router.post("/keyword-extends-sales")
async def keyword_extends_sales(req: KeywordPulseReq, _user: str = Depends(require_user)) -> dict:
    """Deep order-evidence: for the top extended keywords (by score) lacking it,
    pull keyword_search_results and record the median monthly sales of the top
    products — an evidence that the keyword actually drives orders. Up to 8
    extra Sorftime calls."""
    kw = req.keyword.strip()
    source = _data_source(req.data_source)
    kid = _kw_id(kw, req.marketplace, source)
    with _connect() as conn:
        row = conn.execute(
            "SELECT data FROM home_keyword_extends WHERE id=? AND data_source=?", (kid, source),
        ).fetchone()
    if not row:
        raise HTTPException(400, "先拉取拓展词")
    try:
        items: List[dict] = json.loads(row["data"])
    except Exception:
        items = []

    targets = [i for i in sorted(items, key=lambda x: x.get("score", 0), reverse=True)
               if i.get("evidence_sales") is None][:8]

    if source == "sellersprite":
        for it in targets:
            evidence = await sellersprite_service.home_keyword_purchase_evidence(
                str(it.get("keyword") or ""), req.marketplace,
            )
            if evidence is not None:
                it["evidence_sales"] = round(evidence)
    else:
        from app.services.sorftime_service import _make_client, _safe_call, rows
        async with _make_client() as client:
            for it in targets:
                try:
                    _, sr, err = await _safe_call(
                        client, "keyword_search_results",
                        {"keyword": it["keyword"], "keyword_support_site": req.marketplace}, 1,
                    )
                    if not err:
                        sales = sorted(
                            [s for s in (
                                _kx_num(_kx_pick(p, "本产品月销量", "monthly_sales_volume"))
                                for p in rows(sr)
                            ) if s],
                            reverse=True,
                        )[:10]
                        if sales:
                            mid = (
                                sales[len(sales) // 2]
                                if len(sales) % 2
                                else (sales[len(sales) // 2 - 1] + sales[len(sales) // 2]) / 2
                            )
                            it["evidence_sales"] = round(mid)
                except Exception:
                    pass

    now = int(time.time() * 1000)
    with _connect() as conn:
        conn.execute("UPDATE home_keyword_extends SET data=?, ts=? WHERE id=?",
                     (json.dumps(items, ensure_ascii=False), now, kid))
    return {
        "keyword": kw, "marketplace": req.marketplace, "data_source": source,
        "error": None, "items": items, "ts": now,
    }
