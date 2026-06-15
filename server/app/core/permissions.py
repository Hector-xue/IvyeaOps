"""Module-level authorization catalog for the multi-user mode.

The admin account can access everything. Registered users get a baseline of
"open" modules (analytical, no code-exec) plus whatever higher-privilege
modules an admin explicitly grants them — chosen per user, usually via a
position preset and then fine-tuned.

This is the single source of truth: the frontend fetches the catalog/presets
from the API, and ``main.py`` gates the matching routers with
``require_module(<key>)``.
"""
from __future__ import annotations

from typing import Dict, List

# Modules an admin can grant. Keys match the frontend nav item keys and the
# router groups in main.py. "用户管理 / 系统配置" are intentionally NOT here —
# they stay admin-only and are never grantable.
MODULE_CATALOG: List[Dict[str, object]] = [
    {"key": "listing",   "label": "Listing 工作台", "sensitive": False},
    {"key": "image-translate", "label": "一键图片翻译", "sensitive": False},
    {"key": "tools",     "label": "分析工具",        "sensitive": False},
    {"key": "skill-hub", "label": "Skill 中心",      "sensitive": False},
    {"key": "agents",    "label": "智能体会话",      "sensitive": True},
    {"key": "brain",     "label": "GBrain 知识库",   "sensitive": True},
    {"key": "terminal",  "label": "服务器终端",      "sensitive": True},
    {"key": "servmon",   "label": "服务器监控",      "sensitive": True},
    {"key": "news",      "label": "资讯",            "sensitive": False},
]

GRANTABLE_KEYS = {m["key"] for m in MODULE_CATALOG}

# Modules every active registered user can always use (no grant needed). These
# are the analytical / AI modules whose routers are open in main.py.
BASE_MODULES: List[str] = ["market", "playbook", "assistant", "imagegen", "freight"]

# Position presets: applied as a starting point, then the admin can tweak the
# per-user module list. Only GRANTABLE_KEYS are meaningful here.
POSITION_PRESETS: Dict[str, List[str]] = {
    "运营专员": ["listing", "image-translate"],
    "运营主管": ["listing", "image-translate", "tools", "skill-hub", "news"],
    "设计":     ["listing", "image-translate", "skill-hub"],
    "技术助理": ["agents", "terminal", "servmon", "brain", "tools", "skill-hub"],
    "客服":     ["brain"],
}


def sanitize_permissions(perms) -> List[str]:
    """Keep only known grantable module keys, de-duplicated, order-stable."""
    if not isinstance(perms, (list, tuple)):
        return []
    seen: set = set()
    out: List[str] = []
    for p in perms:
        k = str(p).strip()
        if k in GRANTABLE_KEYS and k not in seen:
            seen.add(k)
            out.append(k)
    return out
