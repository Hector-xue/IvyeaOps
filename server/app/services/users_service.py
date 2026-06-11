"""Registered-user accounts (email + password) for the multi-user mode.

The single admin (env / hub_settings password) is NOT stored here — it stays a
special account. This table only holds *registered* users, who default to
status='pending' until an admin approves them.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional

import bcrypt

from app.core.config import settings

_DB = settings.data_dir / "users.sqlite3"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

VALID_STATUS = ("pending", "active", "suspended")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB), isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# Versioned migrations for this DB (see app/core/db_migrations). The baseline
# schema below (+ the legacy position/permissions ALTERs) is "version 0";
# append future breaking changes here.
_MIGRATIONS: tuple = ()


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'user',
                status        TEXT NOT NULL DEFAULT 'pending',
                created_at    INTEGER NOT NULL,
                approved_at   INTEGER,
                position      TEXT NOT NULL DEFAULT '',
                permissions   TEXT NOT NULL DEFAULT '[]'
            )
        """)
        # Migration for databases created before module authorization existed.
        for col, default in (("position", "''"), ("permissions", "'[]'")):
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}")
            except sqlite3.OperationalError:
                pass  # column already exists
        from app.core.db_migrations import apply_migrations
        apply_migrations(conn, _MIGRATIONS)


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d.pop("password_hash", None)
    # permissions is stored as a JSON array string; expose it as a list.
    raw = d.get("permissions")
    if isinstance(raw, str):
        try:
            d["permissions"] = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            d["permissions"] = []
    elif raw is None:
        d["permissions"] = []
    d.setdefault("position", "")
    return d


def valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip().lower()))


def create_user(email: str, password: str) -> Dict[str, Any]:
    email = email.strip().lower()
    if not valid_email(email):
        raise ValueError("邮箱格式不正确")
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    now = int(time.time() * 1000)
    with _connect() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone()
        if exists:
            raise ValueError("该邮箱已注册")
        cur = conn.execute(
            "INSERT INTO users (email,password_hash,role,status,created_at) VALUES (?,?,?,?,?)",
            (email, _hash(password), "user", "pending", now),
        )
        uid = cur.lastrowid
        return {"id": uid, "email": email, "role": "user", "status": "pending"}


def verify_login(email: str, password: str) -> Dict[str, Any]:
    """Return user dict on success. Raises ValueError with a user-facing reason
    (bad credentials / pending / suspended)."""
    email = email.strip().lower()
    with _connect() as conn:
        r = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not r:
        raise ValueError("账号或密码错误")
    try:
        ok = bcrypt.checkpw(password.encode("utf-8"), r["password_hash"].encode("utf-8"))
    except ValueError:
        ok = False
    if not ok:
        raise ValueError("账号或密码错误")
    if r["status"] == "pending":
        raise ValueError("账号待管理员审批")
    if r["status"] == "suspended":
        raise ValueError("账号已被停用")
    return _row(r)


def get_by_id(uid: int) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        r = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return _row(r) if r else None


def list_users() -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    return [_row(r) for r in rows]


def set_status(uid: int, status: str) -> None:
    if status not in VALID_STATUS:
        raise ValueError("invalid status")
    now = int(time.time() * 1000)
    with _connect() as conn:
        if status == "active":
            conn.execute("UPDATE users SET status=?, approved_at=? WHERE id=?", (status, now, uid))
        else:
            conn.execute("UPDATE users SET status=? WHERE id=?", (status, uid))


def reset_password(uid: int, new_password: str) -> None:
    if len(new_password) < 8:
        raise ValueError("密码至少 8 位")
    with _connect() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (_hash(new_password), uid))


def delete_user(uid: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (uid,))


def set_permissions(uid: int, position: str, permissions: List[str]) -> Dict[str, Any]:
    """Set a user's position label and granted module list. Only known grantable
    keys are stored (sanitized by the caller / permissions catalog)."""
    pos = (position or "").strip()
    perms_json = json.dumps(list(permissions or []), ensure_ascii=False)
    with _connect() as conn:
        cur = conn.execute("UPDATE users SET position=?, permissions=? WHERE id=?", (pos, perms_json, uid))
        if cur.rowcount == 0:
            raise ValueError("用户不存在")
        r = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return _row(r)
