"""定时任务：到点让 Agent 自己跑一轮，把结果留在历史里。

设计上的三条硬规矩
------------------
1. **无人值守一律只读。** 任务跑起来时没人在屏幕前，所以永远 ``plan_mode=True`` +
   ``approval="none"``：Agent 可以巡检、分析、把要改的东西列清楚，但不会真的动
   线上数据。想落地就自己去看结果、去对应板块执行。这条不做成开关 —— 一个"自动
   批准写操作"的定时任务正是前几期花力气消灭的东西。
2. **按创建者的身份跑。** 后台没有请求上下文时 ``ivyea_ops_tools._principal()``
   会回落成管理员；照这么跑，普通用户建的定时任务会拿到管理员权限去调板块工具。
   所以执行前把 principal 上下文设成任务的所有者。
3. **不给"追跑"。** 服务停了两天再起来，不该把这两天欠的几十次一口气补跑完。
   next_run 落在过去时只跑一次，然后按当前时间重算。

自己实现 cron 解析是因为环境里没有 croniter，也不值得为这一个功能拉依赖。
只支持标准 5 段（分 时 日 月 周）和 ``* n a,b a-b */n`` 这几种写法 —— 够用，
且每一条都有测试钉着。
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from app.core.config import settings
from app.core.db_migrations import apply_migrations

logger = logging.getLogger("ivyea.services.schedules")

TICK_SECONDS = 30.0
MAX_RUNS_KEPT = 50          # 每个任务保留多少条历史

_BASELINE_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS scheduled_tasks (
        id         TEXT PRIMARY KEY NOT NULL,
        name       TEXT NOT NULL DEFAULT '',
        cron       TEXT NOT NULL DEFAULT '',
        prompt     TEXT NOT NULL DEFAULT '',
        skill      TEXT NOT NULL DEFAULT '',
        workspace  TEXT NOT NULL DEFAULT '',
        enabled    INTEGER NOT NULL DEFAULT 1,
        principal  TEXT NOT NULL DEFAULT '',
        role       TEXT NOT NULL DEFAULT 'user',
        created    REAL NOT NULL DEFAULT 0,
        updated    REAL NOT NULL DEFAULT 0,
        last_run   REAL NOT NULL DEFAULT 0,
        next_run   REAL NOT NULL DEFAULT 0
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_sched_due ON scheduled_tasks(enabled, next_run);",
    """
    CREATE TABLE IF NOT EXISTS scheduled_runs (
        id         TEXT PRIMARY KEY NOT NULL,
        task_id    TEXT NOT NULL,
        trigger    TEXT NOT NULL DEFAULT 'scheduled',
        status     TEXT NOT NULL DEFAULT 'running',
        started    REAL NOT NULL DEFAULT 0,
        finished   REAL NOT NULL DEFAULT 0,
        session_id TEXT NOT NULL DEFAULT '',
        output     TEXT NOT NULL DEFAULT '',
        error      TEXT NOT NULL DEFAULT ''
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_sched_runs ON scheduled_runs(task_id, started DESC);",
)

_MIGRATIONS: tuple = ()


def _db_path() -> Path:
    return settings.data_dir / "schedules.sqlite3"


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        for ddl in _BASELINE_SCHEMA:
            conn.execute(ddl)
        apply_migrations(conn, _MIGRATIONS)


# ── cron ────────────────────────────────────────────────────────────────────

class CronError(ValueError):
    pass


_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))   # 分 时 日 月 周(0=周日)
_FIELD_NAMES = ("分钟", "小时", "日", "月", "星期")


def _parse_field(spec: str, low: int, high: int, label: str) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"{label}字段为空")
        step = 1
        if "/" in part:
            part, _, raw_step = part.partition("/")
            if not raw_step.isdigit() or int(raw_step) < 1:
                raise CronError(f"{label}的步长必须是正整数")
            step = int(raw_step)
            part = part or "*"
        if part == "*":
            start, end = low, high
        elif "-" in part.lstrip("-"):
            a, _, b = part.partition("-")
            if not (a.isdigit() and b.isdigit()):
                raise CronError(f"{label}区间只能是数字")
            start, end = int(a), int(b)
        elif part.isdigit():
            start = end = int(part)
        else:
            raise CronError(f"{label}字段无法识别：{part!r}")
        if start < low or end > high or start > end:
            raise CronError(f"{label}超出范围（{low}-{high}）")
        out.update(range(start, end + 1, step))
    if not out:
        raise CronError(f"{label}字段没有匹配到任何值")
    return out


def parse_cron(expr: str) -> list[set[int]]:
    """解析 5 段 cron，返回每段允许的取值集合。非法表达式抛 CronError。"""
    fields = str(expr or "").split()
    if len(fields) != 5:
        raise CronError("需要 5 段：分 时 日 月 星期（例：0 9 * * 1 表示每周一 9:00）")
    return [
        _parse_field(f, low, high, name)
        for f, (low, high), name in zip(fields, _FIELD_RANGES, _FIELD_NAMES)
    ]


def next_fire(expr: str, after: float | None = None) -> float:
    """算出 ``after`` 之后的下一个触发时刻（本地时区 epoch 秒）。

    从下一分钟开始逐分钟试，最多往前找 366 天 —— 够覆盖"每年某天"这种，
    也保证不会因为写了个永远不成立的表达式（如 2 月 30 日）而死循环。
    """
    minute, hour, dom, month, dow = parse_cron(expr)
    fields = str(expr).split()
    # cron 的老规矩：日和星期**都不是 `*` 时取"或"**（`0 0 1 * 1` = 每月 1 号 **或**
    # 每周一）；只有一个是 `*` 时按另一个走。这条反直觉但它是标准，别自作主张改。
    dom_star, dow_star = fields[2].strip() == "*", fields[4].strip() == "*"

    start = datetime.fromtimestamp(after if after is not None else time.time())
    # 对齐到下一整分钟，避免同一分钟内被重复触发
    cur = (start + timedelta(minutes=1)).replace(second=0, microsecond=0)
    limit = cur + timedelta(days=366)
    while cur <= limit:
        if cur.minute in minute and cur.hour in hour and cur.month in month:
            day_ok = cur.day in dom
            # Python 里周一=0、周日=6；cron 里周日=0
            week_ok = ((cur.weekday() + 1) % 7) in dow
            if dom_star and dow_star:
                day_match = True
            elif dom_star:
                day_match = week_ok
            elif dow_star:
                day_match = day_ok
            else:
                day_match = day_ok or week_ok
            if day_match:
                return cur.timestamp()
        cur += timedelta(minutes=1)
    raise CronError("这个表达式在一年内没有触发时刻，请检查日期组合")


def describe_cron(expr: str) -> str:
    """给人看的一句话说明。解析不了就原样回显，不编。"""
    try:
        nxt = next_fire(expr)
    except CronError:
        return expr
    return datetime.fromtimestamp(nxt).strftime("下次 %Y-%m-%d %H:%M")


# ── 任务 CRUD ───────────────────────────────────────────────────────────────

def _row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["enabled"] = bool(d.get("enabled"))
    return d


def list_tasks(principal: str, is_admin: bool) -> list[dict[str, Any]]:
    sql = "SELECT * FROM scheduled_tasks"
    args: list[Any] = []
    if not is_admin:
        sql += " WHERE principal = ?"
        args.append(principal or "")
    with _conn() as conn:
        return [_row(r) for r in conn.execute(sql + " ORDER BY created DESC", args).fetchall()]


def get_task(task_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
    return _row(r) if r else None


def create_task(*, name: str, cron: str, prompt: str, principal: str, role: str,
                skill: str = "", workspace: str = "", enabled: bool = True) -> dict[str, Any]:
    name = (name or "").strip()[:120]
    prompt = (prompt or "").strip()
    if not name:
        raise ValueError("任务名不能为空")
    if not prompt:
        raise ValueError("要让 Agent 做什么不能为空")
    next_run = next_fire(cron) if enabled else 0.0       # cron 非法会在这里抛
    now = time.time()
    task_id = uuid.uuid4().hex[:12]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO scheduled_tasks (id, name, cron, prompt, skill, workspace, enabled,"
            " principal, role, created, updated, last_run, next_run)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?)",
            (task_id, name, cron.strip(), prompt, skill.strip(), workspace.strip(),
             1 if enabled else 0, principal or "", role or "user", now, now, next_run),
        )
    return get_task(task_id) or {}


def update_task(task_id: str, **fields: Any) -> dict[str, Any] | None:
    task = get_task(task_id)
    if not task:
        return None
    merged = {**task, **{k: v for k, v in fields.items() if v is not None}}
    cron = str(merged.get("cron") or "")
    enabled = bool(merged.get("enabled"))
    # 改了 cron 或重新启用都要重算下次触发；停用则清零，免得留个过期时间误导人。
    next_run = next_fire(cron) if enabled else 0.0
    with _conn() as conn:
        conn.execute(
            "UPDATE scheduled_tasks SET name=?, cron=?, prompt=?, skill=?, workspace=?,"
            " enabled=?, updated=?, next_run=? WHERE id=?",
            (str(merged.get("name") or "")[:120], cron.strip(), str(merged.get("prompt") or ""),
             str(merged.get("skill") or ""), str(merged.get("workspace") or ""),
             1 if enabled else 0, time.time(), next_run, task_id),
        )
    return get_task(task_id)


def delete_task(task_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        conn.execute("DELETE FROM scheduled_runs WHERE task_id = ?", (task_id,))
        return bool(cur.rowcount)


# ── 运行历史 ────────────────────────────────────────────────────────────────

def list_runs(task_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_runs WHERE task_id = ? ORDER BY started DESC LIMIT ?",
            (task_id, max(1, min(int(limit), 100))),
        ).fetchall()
    return [dict(r) for r in rows]


def _start_run(task_id: str, trigger: str) -> str:
    run_id = uuid.uuid4().hex[:12]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO scheduled_runs (id, task_id, trigger, status, started) VALUES (?,?,?,?,?)",
            (run_id, task_id, trigger, "running", time.time()),
        )
    return run_id


def _finish_run(run_id: str, task_id: str, *, status: str, output: str = "",
                error: str = "", session_id: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE scheduled_runs SET status=?, finished=?, output=?, error=?, session_id=?"
            " WHERE id=?",
            (status, time.time(), output[:20000], error[:2000], session_id, run_id),
        )
        # 只留最近 N 条，别让历史无限长
        conn.execute(
            "DELETE FROM scheduled_runs WHERE task_id = ? AND id NOT IN "
            "(SELECT id FROM scheduled_runs WHERE task_id = ? ORDER BY started DESC LIMIT ?)",
            (task_id, task_id, MAX_RUNS_KEPT),
        )


# ── 执行 ────────────────────────────────────────────────────────────────────

def run_task_now(task: dict[str, Any], trigger: str = "manual") -> dict[str, Any]:
    """跑一次任务（阻塞）。返回本次 run 记录。

    以任务所有者的身份执行：后台没有请求上下文时 ivyea_ops_tools 会把 principal
    回落成管理员，照那么跑，普通用户建的定时任务会拿到管理员权限去调板块工具。
    """
    from app.core.security import current_user
    from app.services import ivyea_agent_service as svc

    run_id = _start_run(task["id"], trigger)
    token = current_user.set({
        "id": task.get("principal") or "",
        "email": task.get("principal") or "",
        "role": task.get("role") or "user",
        "permissions": [],
    })
    try:
        payload: dict[str, Any] = {
            "message": task["prompt"],
            # 无人值守：只读 + 不开远程审批。这两个是这条路的安全底线，不做成配置。
            "plan_mode": True,
            "approval": "none",
            "persist": True,
            "inject_retrieval": True,
            "auto_skill": not (task.get("skill") or ""),
            "ops_context": {"board": "schedules", "task": task.get("name") or ""},
        }
        if task.get("skill"):
            payload["skill"] = task["skill"]
        # 同样要把工作区**名字**换算成目录 —— 直接把名字当路径发下去，
        # agent 的文件工具会落到一个不存在的目录上。
        if task.get("workspace"):
            from app.services import console_sessions
            ws_dir = console_sessions.workspace_path(task["workspace"], task.get("principal") or "")
            if ws_dir:
                payload["workspace"] = ws_dir

        # 定时任务往往在半夜/清晨触发，那时 agent daemon 未必还活着。先确保它起来
        # （和任务台的 /chat/stream 一样走 ensure_available），否则到点只会留下一条
        # "Connection refused"，而不是应该跑出来的结果。
        status = svc.ensure_available()
        if not status.get("available"):
            _finish_run(run_id, task["id"], status="error",
                        error=f"IvyeaAgent 不可用：{status.get('error') or '服务未连接'}")
            return (list_runs(task["id"], limit=1) or [{"id": run_id, "status": "error"}])[0]

        res = svc.chat(payload)
        if not res.get("ok"):
            detail = str(res.get("detail") or res.get("error") or "执行失败")
            _finish_run(run_id, task["id"], status="error", error=detail)
        else:
            _finish_run(run_id, task["id"], status="done",
                        output=str(res.get("text") or ""),
                        session_id=str(res.get("session_id") or ""))
    except Exception as exc:  # noqa: BLE001 — 一个任务失败不该影响调度器和别的任务
        _finish_run(run_id, task["id"], status="error", error=str(exc))
    finally:
        current_user.reset(token)
        with _conn() as conn:
            conn.execute("UPDATE scheduled_tasks SET last_run = ? WHERE id = ?",
                         (time.time(), task["id"]))
    runs = list_runs(task["id"], limit=1)
    return runs[0] if runs else {"id": run_id, "status": "unknown"}


def due_tasks(now: float | None = None) -> list[dict[str, Any]]:
    now = now if now is not None else time.time()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE enabled = 1 AND next_run > 0 AND next_run <= ?",
            (now,),
        ).fetchall()
    return [_row(r) for r in rows]


def reschedule(task_id: str, cron: str) -> float:
    """按**当前时间**重算下次触发。

    刻意不从上一次应触发的时刻往后推：服务停了两天再起来，那样会把欠下的几十次
    一口气补跑完。宁可少跑一次，也不要突然打出一串任务。
    """
    try:
        nxt = next_fire(cron)
    except CronError:
        nxt = 0.0        # 表达式坏了就别再排了，等用户来修
    with _conn() as conn:
        conn.execute("UPDATE scheduled_tasks SET next_run = ? WHERE id = ?", (nxt, task_id))
    return nxt


async def scheduler_loop() -> None:
    """每 30 秒看一眼有没有到点的任务。单个任务失败只记进它自己的历史。"""
    while True:
        try:
            for task in due_tasks():
                # 先把下次时间排掉再执行：任务本身可能跑好几分钟，
                # 期间不该因为 next_run 还停在过去而被重复捞起来。
                reschedule(task["id"], task["cron"])
                logger.info("scheduled task firing: %s", task["name"])
                await asyncio.to_thread(run_task_now, task, "scheduled")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("scheduler error: %s", e)
        await asyncio.sleep(TICK_SECONDS)


def timezone_label() -> str:
    """调度用的时区，照实报。

    `next_fire` 走的是 `datetime.fromtimestamp()`（不带 tzinfo），也就是**服务器
    本地时区** —— 不是 UTC，也不是用户浏览器所在的时区。界面上必须写清楚，
    否则跨时区的人会按自己的钟去理解「每天 09:00」，然后发现报告在半夜到。
    """
    now = datetime.now().astimezone()
    offset = now.utcoffset() or timedelta(0)
    total = int(offset.total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    return f"{time.tzname[0]} (UTC{sign}{abs(total) // 60:02d}:{abs(total) % 60:02d})"
