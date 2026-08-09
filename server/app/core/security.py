"""Session token and authentication dependencies.

Multi-user scheme:
- Admin: a single special account (env / hub_settings password). role='admin'.
- Registered users: rows in users_service, role='user', must be status='active'.
- On login we issue a signed cookie carrying {id, role}. ``require_user``
  decodes it, re-validates registered users against the DB (so suspend takes
  effect immediately), stashes the resolved user in a contextvar, and returns
  the identity string (email) for backward compatibility with existing routers.
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any, Dict, Optional

import bcrypt
from fastapi import Cookie, Depends, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import settings

_serializer = URLSafeTimedSerializer(settings.secret_key, salt="ivyea-ops.session")

ADMIN_ID = "admin"

# Request-scoped current user: {"id", "role", "email"}. Unset (None) outside a
# request (e.g. background tasks) — callers treat that as admin context.
current_user: ContextVar[Optional[Dict[str, Any]]] = ContextVar("current_user", default=None)


def _resolve_session_principal(session: str) -> Dict[str, Any]:
    """Decode one session cookie into a normalized principal dict.

    Returns ``{"id", "role", "email"}`` or raises the same HTTP errors used by
    the public auth dependencies. This avoids relying on ContextVar propagation
    across FastAPI's sync dependency / endpoint thread hops.
    """
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")

    data = _decode(session)
    if not data or "id" not in data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")

    uid = data["id"]
    token_role = str(data.get("r") or "").strip().lower()
    if uid == ADMIN_ID:
        return {"id": ADMIN_ID, "role": token_role or "admin", "email": settings.admin_user}

    from app.services import users_service

    u = users_service.get_by_id(int(uid)) if isinstance(uid, int) or str(uid).isdigit() else None
    if not u:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")
    if u.get("status") != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号未激活或已停用")
    return {
        "id": u["id"],
        "role": u.get("role", "user"),
        "email": u["email"],
        "permissions": u.get("permissions", []) or [],
        "position": u.get("position", ""),
    }


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def issue_session(user_id: Any, role: str) -> str:
    return _serializer.dumps({"id": user_id, "r": role, "t": int(time.time())})


def _decode(token: str) -> Optional[Dict[str, Any]]:
    try:
        data = _serializer.loads(token, max_age=settings.session_max_age_seconds)
    except (SignatureExpired, BadSignature):
        return None
    return data if isinstance(data, dict) else None


def verify_session(token: str) -> Optional[str]:
    """Backward-compatible identity check (used by WebSocket auth where the
    dependency system isn't applied). Returns the identity string (admin user
    or an active user's email), or None if invalid/inactive."""
    data = _decode(token) if token else None
    if not data or "id" not in data:
        return None
    uid = data["id"]
    if uid == ADMIN_ID:
        return settings.admin_user
    from app.services import users_service
    try:
        u = users_service.get_by_id(int(uid))
    except (ValueError, TypeError):
        return None
    if u and u.get("status") == "active":
        return u["email"]
    return None


def is_admin() -> bool:
    cu = current_user.get()
    return bool(cu and cu.get("role") == "admin")


def user_data_dir():
    """Per-user data directory for isolating user-facing storage.

    - admin / no request context (background tasks) → the shared data_dir
      (admin keeps the existing global DBs unchanged).
    - registered user → data_dir/users/{id}/ (created on demand).
    """
    cu = current_user.get()
    if cu and cu.get("role") != "admin" and cu.get("id") not in (None, ADMIN_ID):
        d = settings.data_dir / "users" / str(cu["id"])
        d.mkdir(parents=True, exist_ok=True)
        return d
    return settings.data_dir


def require_user(
    session: Optional[str] = Cookie(default=None, alias=settings.session_cookie_name),
) -> str:
    """FastAPI dependency: validate session, set current_user contextvar, return
    the identity string (email/admin user). 401 if unauthenticated/invalid,
    403 if the registered user is no longer active."""
    cu = _resolve_session_principal(session or "")
    current_user.set(cu)
    return cu["email"]


def require_user_info(
    session: Optional[str] = Cookie(default=None, alias=settings.session_cookie_name),
) -> Dict[str, Any]:
    """Like require_user, but returns the normalized principal dict directly."""
    cu = _resolve_session_principal(session or "")
    current_user.set(cu)
    return cu


def require_admin(
    _user: str = Depends(require_user),
    session: Optional[str] = Cookie(default=None, alias=settings.session_cookie_name),
) -> str:
    """Dependency for admin-only routes/routers.

    In production we resolve the principal from the session directly so sync
    dependency/thread hops cannot drop the admin role. In tests, many fixtures
    override ``require_user`` without issuing a cookie; when no session cookie is
    present we trust that upstream override and keep the legacy behavior.
    """
    if not session:
        return _user

    cu = _resolve_session_principal(session)
    current_user.set(cu)
    if cu.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return cu["email"]


def require_module(module_key: str):
    """Dependency factory gating a router/route behind a grantable module.

    Admin passes unconditionally; a registered user passes only if the module
    key is in their granted permissions. Used at the router level in main.py so
    granted users reach the module's API while others get 403."""

    def _dep(
        _user: str = Depends(require_user),
        session: Optional[str] = Cookie(default=None, alias=settings.session_cookie_name),
    ) -> str:
        # No cookie → trust an upstream require_user override (test fixtures),
        # matching require_admin's behavior.
        #
        # 这里必须走 Depends(require_user) 而不是直接 `return require_user(session)`：
        # 直接调用是普通的 Python 函数调用，**绕过 FastAPI 的 dependency_overrides**，
        # 于是上面这句注释描述的行为从来没有真的发生过 —— 测试里 override 了
        # require_user 也没用，请求照样 401。app/tests 里 61 项常年红着就是这个原因
        # （而它们不在 CI 里，所以没人知道）。
        # 生产行为不变：没有 cookie 时 require_user 自己就会抛 401。
        if not session:
            return _user
        cu = _resolve_session_principal(session)
        current_user.set(cu)
        if cu.get("role") == "admin":
            return cu["email"]
        if module_key in (cu.get("permissions") or []):
            return cu["email"]
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无该板块访问权限")

    return _dep
